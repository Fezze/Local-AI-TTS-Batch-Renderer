from __future__ import annotations

import errno
import hashlib
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import requests

from .defaults import MODEL_URL, VOICES_URL

DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 120
LOCK_POLL_SECONDS = 0.1
LOCK_TIMEOUT_SECONDS = 30 * 60


class ModelFileValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelArtifact:
    filename: str
    url: str
    sizes: frozenset[int]
    sha256: frozenset[str]


MODEL_ARTIFACTS = (
    ModelArtifact(
        filename="kokoro-v1.0.onnx",
        url=MODEL_URL,
        sizes=frozenset({325_532_387}),
        sha256=frozenset({"7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"}),
    ),
    ModelArtifact(
        filename="voices-v1.0.bin",
        url=VOICES_URL,
        sizes=frozenset({26_124_436, 28_214_398}),
        sha256=frozenset(
            {
                "d19762d46cf0e6648cb28a7711df1637aad15818185d13f4ff840d57f2f6dfed",
                "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
            }
        ),
    ),
)


def _artifact_for(path: Path, url: str) -> ModelArtifact | None:
    return next((artifact for artifact in MODEL_ARTIFACTS if artifact.filename == path.name and artifact.url == url), None)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_model_file(
    path: Path,
    artifact: ModelArtifact | None = None,
    *,
    verify_checksum: bool = False,
) -> None:
    if not path.is_file():
        raise ModelFileValidationError(f"model artifact is missing: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ModelFileValidationError(f"model artifact is empty: {path}")
    if artifact is not None and artifact.sizes and size not in artifact.sizes:
        expected = ", ".join(str(item) for item in sorted(artifact.sizes))
        raise ModelFileValidationError(f"unexpected size for {path.name}: got {size}, expected one of {expected}")
    if verify_checksum and artifact is not None and artifact.sha256:
        if _file_sha256(path) not in artifact.sha256:
            raise ModelFileValidationError(f"checksum mismatch for {path.name}")


def _validate_download(
    path: Path,
    artifact: ModelArtifact | None,
    downloaded_size: int,
    content_length: int | None,
    digest: str,
) -> None:
    validate_model_file(path, artifact)
    if content_length is not None and downloaded_size != content_length:
        raise ModelFileValidationError(
            f"incomplete download for {path.name}: got {downloaded_size} bytes, expected {content_length}"
        )
    if artifact is not None and artifact.sha256 and digest not in artifact.sha256:
        raise ModelFileValidationError(f"checksum mismatch for {path.name}")


def _response_content_length(response: requests.Response) -> int | None:
    value = getattr(response, "headers", {}).get("Content-Length")
    if not value:
        return None
    try:
        length = int(value)
    except ValueError as exc:
        raise ModelFileValidationError(f"invalid Content-Length header: {value!r}") from exc
    if length < 0:
        raise ModelFileValidationError(f"invalid Content-Length header: {value!r}")
    return length


def _sync_directory(path: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        os.close(descriptor)


def ensure_file(path: Path, url: str) -> None:
    artifact = _artifact_for(path, url)
    try:
        validate_model_file(path, artifact, verify_checksum=True)
        return
    except ModelFileValidationError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    downloaded_size = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                response.raise_for_status()
                content_length = _response_content_length(response)
                for part in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if not part:
                        continue
                    handle.write(part)
                    digest.update(part)
                    downloaded_size += len(part)
            handle.flush()
            os.fsync(handle.fileno())
        _validate_download(temp_path, artifact, downloaded_size, content_length, digest.hexdigest())
        os.replace(temp_path, path)
        _sync_directory(path.parent)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _try_lock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _model_download_lock(model_dir: Path) -> Iterator[None]:
    model_dir.mkdir(parents=True, exist_ok=True)
    lock_path = model_dir / ".model-bootstrap.lock"
    with lock_path.open("a+b") as handle:
        if os.name == "nt" and lock_path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()

        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                _try_lock(handle)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise RuntimeError(f"model bootstrap lock failed: {lock_path}") from exc
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for model bootstrap lock: {lock_path}") from exc
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(handle)


def ensure_model_files(
    model_dir: Path,
    *,
    file_ensurer: Callable[[Path, str], None] | None = None,
) -> tuple[Path, Path]:
    model_dir = Path(model_dir)
    ensure = file_ensurer or ensure_file
    paths = tuple(model_dir / artifact.filename for artifact in MODEL_ARTIFACTS)
    with _model_download_lock(model_dir):
        for path, artifact in zip(paths, MODEL_ARTIFACTS, strict=True):
            ensure(path, artifact.url)
    return paths[0], paths[1]
