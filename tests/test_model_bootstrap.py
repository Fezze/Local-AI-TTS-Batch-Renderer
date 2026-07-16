from __future__ import annotations

import errno
import hashlib
import importlib.util
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from local_tts_renderer import cli_runtime, model_bootstrap


def _load_doctor_module():
    path = Path("scripts/doctor.py").resolve()
    spec = importlib.util.spec_from_file_location("local_tts_test_doctor", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: bytes, *, content_length: int | None = None, delay: float = 0.0) -> None:
        self.payload = payload
        self.delay = delay
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        if self.delay:
            time.sleep(self.delay)
        midpoint = max(1, len(self.payload) // 2)
        yield self.payload[:midpoint]
        yield self.payload[midpoint:]


def _artifact(filename: str, url: str, payload: bytes) -> model_bootstrap.ModelArtifact:
    return model_bootstrap.ModelArtifact(
        filename=filename,
        url=url,
        sizes=frozenset({len(payload)}),
        sha256=frozenset({hashlib.sha256(payload).hexdigest()}),
    )


@pytest.fixture
def fake_artifacts(monkeypatch):
    payloads = {
        "kokoro-v1.0.onnx": b"offline-onnx-model",
        "voices-v1.0.bin": b"offline-voice-data",
    }
    artifacts = tuple(_artifact(filename, f"https://example.invalid/{filename}", payload) for filename, payload in payloads.items())
    monkeypatch.setattr(model_bootstrap, "MODEL_ARTIFACTS", artifacts)
    return artifacts, payloads


def test_ensure_model_files_downloads_validated_files_atomically(tmp_path, monkeypatch, fake_artifacts) -> None:
    artifacts, payloads = fake_artifacts
    requested: list[str] = []

    def fake_get(url: str, **kwargs):
        requested.append(url)
        payload = payloads[Path(url).name]
        assert not (tmp_path / Path(url).name).exists()
        return FakeResponse(payload, content_length=len(payload))

    monkeypatch.setattr(model_bootstrap.requests, "get", fake_get)

    model_path, voices_path = model_bootstrap.ensure_model_files(tmp_path)

    assert model_path.read_bytes() == payloads[model_path.name]
    assert voices_path.read_bytes() == payloads[voices_path.name]
    assert requested == [artifact.url for artifact in artifacts]
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob(".*.part"))


def test_failed_validation_preserves_existing_file_and_removes_temporary_file(tmp_path, monkeypatch, fake_artifacts) -> None:
    artifacts, payloads = fake_artifacts
    model_path = tmp_path / artifacts[0].filename
    model_path.write_bytes(b"previous-invalid-file")
    payload = payloads[model_path.name]
    monkeypatch.setattr(
        model_bootstrap.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(payload, content_length=len(payload) + 1),
    )

    with pytest.raises(model_bootstrap.ModelFileValidationError, match="incomplete download"):
        model_bootstrap.ensure_model_files(tmp_path)

    assert model_path.read_bytes() == b"previous-invalid-file"
    assert not list(tmp_path.glob(".*.part"))


def test_checksum_mismatch_never_publishes_download(tmp_path, monkeypatch, fake_artifacts) -> None:
    artifacts, payloads = fake_artifacts
    wrong_payload = b"x" * len(payloads[artifacts[0].filename])
    monkeypatch.setattr(
        model_bootstrap.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(wrong_payload, content_length=len(wrong_payload)),
    )

    with pytest.raises(model_bootstrap.ModelFileValidationError, match="checksum mismatch"):
        model_bootstrap.ensure_model_files(tmp_path)

    assert not (tmp_path / artifacts[0].filename).exists()
    assert not list(tmp_path.glob(".*.part"))


def test_existing_same_size_checksum_mismatch_is_replaced(tmp_path, monkeypatch, fake_artifacts) -> None:
    artifacts, payloads = fake_artifacts
    model_path = tmp_path / artifacts[0].filename
    expected_payload = payloads[model_path.name]
    model_path.write_bytes(b"x" * len(expected_payload))
    requested: list[str] = []

    def fake_get(url: str, **kwargs):
        requested.append(url)
        payload = payloads[Path(url).name]
        return FakeResponse(payload, content_length=len(payload))

    monkeypatch.setattr(model_bootstrap.requests, "get", fake_get)

    model_bootstrap.ensure_model_files(tmp_path)

    assert model_path.read_bytes() == expected_payload
    assert requested == [artifact.url for artifact in artifacts]


def test_parallel_bootstraps_download_each_artifact_once(tmp_path, monkeypatch, fake_artifacts) -> None:
    artifacts, payloads = fake_artifacts
    requested: list[str] = []
    request_guard = threading.Lock()

    def fake_get(url: str, **kwargs):
        with request_guard:
            requested.append(url)
        payload = payloads[Path(url).name]
        return FakeResponse(payload, content_length=len(payload), delay=0.05)

    monkeypatch.setattr(model_bootstrap.requests, "get", fake_get)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: model_bootstrap.ensure_model_files(tmp_path), range(4)))

    assert all(result == results[0] for result in results)
    assert requested == [artifact.url for artifact in artifacts]


def test_existing_valid_files_need_no_network(tmp_path, monkeypatch, fake_artifacts) -> None:
    _, payloads = fake_artifacts
    for filename, payload in payloads.items():
        (tmp_path / filename).write_bytes(payload)
    monkeypatch.setattr(
        model_bootstrap.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("network must not be used for valid model files"),
    )

    paths = cli_runtime.ensure_model_files(tmp_path)

    assert [path.name for path in paths] == list(payloads)


def test_permanent_lock_error_fails_without_polling(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        model_bootstrap,
        "_try_lock",
        lambda _handle: (_ for _ in ()).throw(OSError(errno.ENOTSUP, "unsupported")),
    )
    monkeypatch.setattr(
        model_bootstrap.time,
        "sleep",
        lambda _seconds: pytest.fail("permanent lock errors must not be retried"),
    )

    with pytest.raises(RuntimeError, match="lock failed"):
        model_bootstrap.ensure_model_files(tmp_path)


def test_directory_sync_is_best_effort(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        model_bootstrap.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("unsupported")),
    )

    model_bootstrap._sync_directory(tmp_path)


def test_doctor_validates_model_checksum_and_handles_bad_output_path(
    tmp_path,
    monkeypatch,
    fake_artifacts,
) -> None:
    artifacts, payloads = fake_artifacts
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor, "MODEL_ARTIFACTS", artifacts)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    for filename, payload in payloads.items():
        (model_dir / filename).write_bytes(payload)

    assert doctor.check_models(model_dir)[0] is True
    first = model_dir / artifacts[0].filename
    first.write_bytes(b"x" * len(payloads[first.name]))
    assert doctor.check_models(model_dir)[0] is False

    blocked_output = tmp_path / "not-a-directory"
    blocked_output.write_text("x", encoding="utf-8")
    assert doctor.check_paths(blocked_output, model_dir)[0] is False


def test_linux_start_wrappers_bootstrap_before_preflight_and_render(tmp_path) -> None:
    for wrapper, entrypoint in (("start.sh", "md_to_audio.py"), ("start-batch.sh", "run_tts_batch.py")):
        project = tmp_path / wrapper
        (project / "scripts").mkdir(parents=True)
        (project / ".venv" / "bin").mkdir(parents=True)
        (project / "scripts" / wrapper).write_text((Path("scripts") / wrapper).read_text(encoding="utf-8"), encoding="utf-8")
        fake_python = project / ".venv" / "bin" / "python"
        fake_python.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> calls.log\n', encoding="utf-8")
        fake_python.chmod(0o755)

        subprocess.run(
            ["bash", f"scripts/{wrapper}", "--input", "book.md", "--model-dir", "alt-models"],
            cwd=project,
            check=True,
        )

        assert (project / "calls.log").read_text(encoding="utf-8").splitlines() == [
            "./scripts/bootstrap_models.py --input book.md --model-dir alt-models",
            "./scripts/doctor.py --input book.md --model-dir alt-models",
            f"./{entrypoint} --input book.md --model-dir alt-models",
        ]


@pytest.mark.parametrize("model_free_flag", ["--help", "--list-chapters", "--wav-to-mp3=input.wav"])
def test_linux_single_start_skips_model_checks_for_model_free_commands(
    tmp_path,
    model_free_flag: str,
) -> None:
    project = tmp_path / model_free_flag.removeprefix("--").replace("=", "-")
    (project / "scripts").mkdir(parents=True)
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / "scripts" / "start.sh").write_text(
        (Path("scripts") / "start.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fake_python = project / ".venv" / "bin" / "python"
    fake_python.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> calls.log\n', encoding="utf-8")
    fake_python.chmod(0o755)

    subprocess.run(
        ["bash", "scripts/start.sh", "--input", "book.md", model_free_flag],
        cwd=project,
        check=True,
    )

    assert (project / "calls.log").read_text(encoding="utf-8").splitlines() == [
        f"./md_to_audio.py --input book.md {model_free_flag}",
    ]


def test_linux_batch_start_skips_model_checks_for_help(tmp_path) -> None:
    project = tmp_path / "batch-help"
    (project / "scripts").mkdir(parents=True)
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / "scripts" / "start-batch.sh").write_text(
        (Path("scripts") / "start-batch.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fake_python = project / ".venv" / "bin" / "python"
    fake_python.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> calls.log\n', encoding="utf-8")
    fake_python.chmod(0o755)

    subprocess.run(["bash", "scripts/start-batch.sh", "--help"], cwd=project, check=True)

    assert (project / "calls.log").read_text(encoding="utf-8").splitlines() == [
        "./run_tts_batch.py --help",
    ]


def test_windows_start_wrappers_bootstrap_before_preflight_and_render() -> None:
    for wrapper, entrypoint in (("start.ps1", "md_to_audio.py"), ("start-batch.ps1", "run_tts_batch.py")):
        script = (Path("scripts") / wrapper).read_text(encoding="utf-8-sig")
        bootstrap = script.index(".\\scripts\\bootstrap_models.py")
        doctor = script.index(".\\scripts\\doctor.py")
        render = script.index(f".\\{entrypoint}")
        assert bootstrap < doctor < render
        assert script.index("$LASTEXITCODE", bootstrap) < doctor
        assert "ValueFromRemainingArguments" not in script
        assert "$ForwardArgs = @($args)" in script
        assert "$ModelFreeCommand" in script
        assert '--help' in script
        if wrapper == "start.ps1":
            assert '--list-chapters' in script
            assert '--wav-to-mp3' in script
