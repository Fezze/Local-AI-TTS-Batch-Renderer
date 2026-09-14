from __future__ import annotations

from dataclasses import dataclass

from .scheduler_logging import PROGRESS_RE, parse_heartbeat_line


@dataclass
class ProgressWatchdog:
    timeout: float = 0.0
    last_progress_at: float | None = None
    completed: int = 0

    def observe(self, line: str, now: float) -> None:
        heartbeat = parse_heartbeat_line(line)
        match = PROGRESS_RE.match(line.strip())
        if line.startswith('[run:render] start') or heartbeat is not None or match:
            if self.last_progress_at is None:
                self.last_progress_at = now
        count = heartbeat.get('completed_chunks') if heartbeat else None
        if match:
            count = int(match.group(1))
        if type(count) is int and count > self.completed:
            self.completed = count
            self.last_progress_at = now

    def stalled_seconds(self, now: float) -> float | None:
        if self.timeout <= 0 or self.last_progress_at is None:
            return None
        elapsed = now - self.last_progress_at
        return elapsed if elapsed >= self.timeout else None
