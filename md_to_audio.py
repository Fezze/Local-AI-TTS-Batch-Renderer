from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_src_path() -> None:
    src_dir = Path(__file__).resolve().parent / "src"
    src_dir_str = str(src_dir)
    if src_dir.exists() and src_dir_str not in sys.path:
        sys.path.insert(0, src_dir_str)


_bootstrap_src_path()

from local_tts_renderer.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
