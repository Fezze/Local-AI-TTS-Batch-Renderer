from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

print("[batch:entry] run_tts_batch.py started", flush=True)
from local_tts_renderer.scheduler import main


if __name__ == "__main__":
    raise SystemExit(main())
