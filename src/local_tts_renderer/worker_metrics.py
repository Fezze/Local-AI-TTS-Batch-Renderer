"""Optional process-memory measurements without extra runtime dependencies."""
from pathlib import Path
import sys


def current_rss_bytes():
    if sys.platform.startswith('linux'):
        try:
            for line in Path('/proc/self/status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
    return None

