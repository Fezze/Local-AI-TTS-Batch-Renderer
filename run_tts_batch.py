from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_src_path() -> None:
    src_dir = Path(__file__).resolve().parent / "src"
    src_dir_str = str(src_dir)
    if src_dir.exists() and src_dir_str not in sys.path:
        sys.path.insert(0, src_dir_str)


def _load_main():
    try:
        from local_tts_renderer.scheduler import main as imported_main
    except ModuleNotFoundError as exc:
        missing = exc.name or "unknown"
        if missing.startswith("local_tts_renderer"):
            raise
        print(
            f"Missing Python dependency '{missing}'. Run ./scripts/setup.sh and rerun with ./.venv/bin/python {Path(__file__).name} ...",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return imported_main


_bootstrap_src_path()
main = _load_main()


if __name__ == "__main__":
    raise SystemExit(main())
