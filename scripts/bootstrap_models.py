from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_tts_renderer.model_bootstrap import ensure_model_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and validate the Kokoro model files.")
    parser.add_argument("--model-dir", default="models")
    args, _ = parser.parse_known_args(argv)
    model_dir = Path(args.model_dir).resolve()
    try:
        model_path, voices_path = ensure_model_files(model_dir)
    except Exception as exc:
        print(f"[models] bootstrap=fail error={exc}", file=sys.stderr, flush=True)
        return 2
    print(f"[models] bootstrap=ok model={model_path} voices={voices_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
