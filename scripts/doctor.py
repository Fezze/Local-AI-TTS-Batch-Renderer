from __future__ import annotations

import argparse
import json
import os
import py_compile
import tempfile
from pathlib import Path


def check_python() -> tuple[bool, str]:
    major, minor = os.sys.version_info[:2]
    if major == 3 and minor >= 11:
        return True, f"python={major}.{minor}"
    return False, f"python={major}.{minor} (expected >=3.11)"


def check_paths(output_dir: Path, model_dir: Path) -> tuple[bool, list[str]]:
    messages: list[str] = []
    ok = True
    output_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    try:
        probe = output_dir / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        messages.append(f"output_writable={output_dir}")
    except Exception as exc:
        ok = False
        messages.append(f"output_not_writable={output_dir} error={exc!r}")
    messages.append(f"model_dir={model_dir}")
    return ok, messages


def check_models(model_dir: Path) -> tuple[bool, list[str]]:
    required = {
        "model.onnx": model_dir / "kokoro-v1.0.onnx",
        "voices.json": model_dir / "voices-v1.0.bin",
    }
    ok = True
    messages: list[str] = []
    for label, path in required.items():
        if path.exists() and path.stat().st_size > 0:
            messages.append(f"{label}=ok path={path}")
        else:
            ok = False
            messages.append(f"{label}=missing path={path}")
    return ok, messages


def check_onnx(provider_order: str | None) -> tuple[bool, list[str]]:
    try:
        import onnxruntime as ort
    except Exception as exc:
        return False, [f"onnxruntime_import=failed error={exc!r}"]
    available = ort.get_available_providers()
    preferred = [p.strip() for p in (provider_order or "").split(",") if p.strip()]
    if not preferred:
        preferred = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    selected = next((p for p in preferred if p in available), None)
    ok = selected is not None
    payload = {
        "available_providers": available,
        "preferred": preferred,
        "selected": selected,
    }
    return ok, [f"onnx={json.dumps(payload, ensure_ascii=False)}"]


def check_temp_dir() -> tuple[bool, str]:
    base = Path(tempfile.gettempdir())
    try:
        probe = base / "local-tts-doctor-probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, f"temp_writable={base}"
    except Exception as exc:
        return False, f"temp_not_writable={base} error={exc!r}"


def check_py_compile(root: Path) -> tuple[bool, list[str]]:
    targets = [
        root / "md_to_audio.py",
        root / "run_tts_batch.py",
    ]
    targets.extend(sorted((root / "src" / "local_tts_renderer").rglob("*.py")))
    failures: list[str] = []
    for path in targets:
        if not path.exists():
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path}: {exc.msg}")
    ok = not failures
    messages = failures if failures else [f"compiled={len(targets)}"]
    return ok, messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight checks for Local AI TTS Batch Renderer.")
    parser.add_argument("--output-dir", default="out")
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--providers", default=None, help="Comma-separated provider order.")
    args = parser.parse_args()

    checks: list[tuple[str, bool, list[str]]] = []
    py_ok, py_msg = check_python()
    checks.append(("python", py_ok, [py_msg]))

    path_ok, path_msgs = check_paths(Path(args.output_dir).resolve(), Path(args.model_dir).resolve())
    checks.append(("paths", path_ok, path_msgs))

    model_ok, model_msgs = check_models(Path(args.model_dir).resolve())
    checks.append(("models", model_ok, model_msgs))

    ort_ok, ort_msgs = check_onnx(args.providers)
    checks.append(("onnxruntime", ort_ok, ort_msgs))

    tmp_ok, tmp_msg = check_temp_dir()
    checks.append(("temp", tmp_ok, [tmp_msg]))

    compile_ok, compile_msgs = check_py_compile(Path(__file__).resolve().parents[1])
    checks.append(("py_compile", compile_ok, compile_msgs))

    overall_ok = all(item[1] for item in checks)
    for name, ok, msgs in checks:
        print(f"[doctor] {name}={'ok' if ok else 'fail'}")
        for msg in msgs:
            print(f"  - {msg}")

    print(f"[doctor] status={'ok' if overall_ok else 'fail'}")
    return 0 if overall_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
