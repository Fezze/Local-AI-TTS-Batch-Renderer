"""A persistent process exposes one finite output stream per render task."""
import json
import queue
import subprocess
import threading

from .scheduler_process import register_process, unregister_process, terminate_process_tree


class TaskProcess:
    def __init__(self, owner, job):
        self.owner = owner
        self.pid = owner.process.pid
        self.job_id = job.output_subdir + '/' + job.output_name
        self.attempt = job.attempt
        self.returncode = None
        self.ready = False
        self.lines = queue.Queue()
        self.stdout = self
        self.finished = threading.Event()

    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None:
                return
            yield line

    def finish(self, code):
        if not self.finished.is_set():
            self.returncode = code
            self.lines.put(None)
            self.finished.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.finished.wait(timeout):
            raise subprocess.TimeoutExpired('persistent task', timeout)
        return self.returncode


class PersistentClient:
    def __init__(self, name):
        self.name = name
        self.process = None
        self.task = None
        self.readers = []

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def start_task(self, command, job, **kwargs):
        if not self.alive:
            self.close()
            command = [*command[:3], 'local_tts_renderer.worker_server']
            kwargs['stderr'] = subprocess.PIPE
            kwargs['stdin'] = subprocess.PIPE
            self.process = subprocess.Popen(command, **kwargs)
            register_process(self.name, self.process)
            self.task = TaskProcess(self, job)
            self.readers = [threading.Thread(target=self._logs, daemon=True),
                            threading.Thread(target=self._events, daemon=True)]
            for reader in self.readers:
                reader.start()
        else:
            self.task = TaskProcess(self, job)
        payload = dict(command='render', job_id=self.task.job_id, attempt=job.attempt,
                       argv=self._argv, snapshot=job.document_snapshot)
        self.process.stdin.write(json.dumps(payload) + '\n')
        self.process.stdin.flush()
        return self.task

    def submit(self, command, job, **kwargs):
        self._argv = command[4:]
        return self.start_task(command, job, **kwargs)

    def _events(self):
        process = self.process
        try:
            for line in process.stdout:
                event = json.loads(line)
                task = self.task
                if (event.get('job_id'), event.get('attempt')) != (task.job_id, task.attempt):
                    raise ValueError('Mismatched worker event identity')
                kind = event['event']
                if kind == 'ready':
                    task.ready = True
                    task.lines.put('[run:worker-ready] ' + json.dumps(event) + '\n')
                elif kind == 'progress':
                    task.lines.put(event['line'] + '\n')
                elif kind in ('result', 'error'):
                    task.lines.put(json.dumps(event) + '\n')
                    task.finish(int(event['returncode']))
                else:
                    raise ValueError('Unknown worker event')
        except Exception as exc:
            if self.task:
                self.task.lines.put(f'[worker:protocol-error] {exc!r}\n')
            terminate_process_tree(process, force=True)
        finally:
            # Drain native crash diagnostics before ending the task stream;
            # the scheduler uses CUDA errors to choose the retry provider.
            self.readers[0].join(timeout=1.0)
            if self.task:
                self.task.finish(-9)

    def _logs(self):
        for line in self.process.stderr:
            task = self.task
            if task is not None and not task.finished.is_set():
                task.lines.put(line)

    def close(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                terminate_process_tree(process, force=True)
                process.wait(timeout=10)
        for reader in self.readers:
            reader.join(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        unregister_process(self.name)
        self.process = None
        self.readers = []


def persistent_lifecycle(function):
    """Keep the transport lifetime equal to its scheduler thread lifetime."""
    from functools import wraps

    @wraps(function)
    def wrapped(*positional, **keywords):
        args = keywords.get('args', positional[2] if len(positional) > 2 else None)
        worker = keywords.get('worker', positional[0] if positional else None)
        client = PersistentClient(worker.name) if getattr(args, 'worker_mode', 'subprocess') == 'persistent' else None
        try:
            return function(*positional, **keywords, persistent_client=client)
        finally:
            if client:
                client.close()
    return wrapped
