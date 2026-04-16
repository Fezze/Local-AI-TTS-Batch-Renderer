from __future__ import annotations

from .cli_entry import main
from .cli_runtime import parse_args

__all__ = ["main", "parse_args"]


if __name__ == "__main__":
    raise SystemExit(main())
