"""Private JSON Lines worker protocol; stdout is reserved for events."""
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from . import cli_entry
from .cli_render_flow import request_termination
from .scheduler_logging import PROGRESS_RE
from .worker_snapshot import SnapshotReader
from .worker_metrics import current_rss_bytes


class EventOutput:
    def __init__(self, emit):
        self.emit = emit
        self.local = threading.local()

    def write(self, text):
        pending = getattr(self.local, 'pending', '') + text
        lines = pending.split('\n')
        self.local.pending = lines.pop()
        for line in lines:
            if line.startswith('[run:render]') or PROGRESS_RE.match(line) or '"heartbeat"' in line:
                self.emit('progress', line=line)
            else:
                print(line, file=sys.stderr, flush=True)
        return len(text)

    def flush(self):
        sys.stderr.flush()


def serve(commands, output):
    runtime = None
    snapshots = SnapshotReader()
    active = False
    stopping = False
    job_id, attempt = None, 0
    lock = threading.Lock()

    def emit(event, **payload):
        with lock:
            output.write(json.dumps(dict(event=event, job_id=job_id, attempt=attempt, **payload)) + '\n')
            output.flush()

    def terminate(_signal, _frame):
        nonlocal stopping
        stopping = True
        if active:
            request_termination()
        else:
            raise SystemExit(0)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        for raw in commands:
            command = json.loads(raw)
            if command.get('command') == 'shutdown':
                break
            job_id, attempt = command['job_id'], command['attempt']
            started = time.monotonic()
            try:
                sys.argv = ['worker', *command['argv']]
                with redirect_stdout(EventOutput(emit)):
                    args = cli_entry.parse_args()
                    args._persistent_worker = True
                    if runtime is None:
                        runtime = cli_entry.initialize_session(args, Path(args.output_dir).resolve())
                        emit('ready', initialization_seconds=time.monotonic() - started,
                             providers=runtime[0].sess.get_providers(), pid=os.getpid())
                    else:
                        emit('ready', initialization_seconds=0.0, reused=True, pid=os.getpid(),
                             providers=runtime[0].sess.get_providers())
                    if stopping:
                        break
                    prepared = time.monotonic()
                    document = snapshots.load(command['snapshot'], args.input[0])
                    preparation_seconds = time.monotonic() - prepared
                    active = True
                    result = cli_entry.main(args, runtime=runtime, source_document=document)
                    active = False
                emit('result', returncode=result, preparation_seconds=preparation_seconds,
                     elapsed_seconds=time.monotonic() - started, rss_bytes=current_rss_bytes())
                if result not in (0, 75) or stopping:
                    break
            except BaseException as exc:
                code = 130 if isinstance(exc, KeyboardInterrupt) else 1
                emit('error', returncode=code, error=repr(exc))
                break
    finally:
        signal.signal(signal.SIGTERM, previous)


def main():
    # Native libraries may write fd 1 directly. Keep them off the protocol pipe.
    with os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1, encoding='utf-8') as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        serve(sys.stdin, protocol)


if __name__ == '__main__':
    main()
