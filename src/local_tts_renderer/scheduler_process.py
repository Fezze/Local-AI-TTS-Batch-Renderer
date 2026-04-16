from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

from .scheduler_types import WorkerConfig


_ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
_ACTIVE_PROCESSES_LOCK = threading.Lock()
_PAUSE_SCHEDULING = threading.Event()


def register_process(worker_name: str, process: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES[worker_name] = process


def unregister_process(worker_name: str) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.pop(worker_name, None)


def terminate_process_tree(process: subprocess.Popen, force: bool = False) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        flags = ["/T"]
        if force:
            flags.append("/F")
        subprocess.run(
            ["taskkill", *flags, "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        if force:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def terminate_all_active_processes(force: bool = True) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES.values())
    for process in processes:
        terminate_process_tree(process, force=force)


def terminate_active_process(worker_name: str, force: bool = True) -> bool:
    with _ACTIVE_PROCESSES_LOCK:
        process = _ACTIVE_PROCESSES.get(worker_name)
    if process is None:
        return False
    terminate_process_tree(process, force=force)
    return True


def is_scheduler_paused() -> bool:
    return _PAUSE_SCHEDULING.is_set()


def _toggle_debug() -> bool:
    current = os.environ.get("LOCAL_TTS_DEBUG", "").strip().lower() in {"1", "true", "yes", "on", "debug"}
    new_value = not current
    if new_value:
        os.environ["LOCAL_TTS_DEBUG"] = "1"
    else:
        os.environ.pop("LOCAL_TTS_DEBUG", None)
    return new_value


def start_console_controls(
    scheduler_condition: threading.Condition,
    workers: list[WorkerConfig],
    enabled: bool,
) -> tuple[threading.Event, threading.Thread | None]:
    stop_event = threading.Event()
    if not enabled or os.name != "nt":
        return stop_event, None

    worker_shortcuts = {str(index): worker.name for index, worker in enumerate(workers, start=1)}
    shortcut_list = " ".join(f"{key}:{name}" for key, name in worker_shortcuts.items())
    print(
        f"[batch:controls] p=pause/resume r=restart-active {shortcut_list} (restart single worker)",
        flush=True,
    )

    def run_controls() -> None:
        import msvcrt

        try:
            while not stop_event.is_set():
                if not msvcrt.kbhit():
                    time.sleep(0.1)
                    continue
                key = msvcrt.getwch()
                if not key:
                    continue
                lowered = key.lower()
                if lowered == "p":
                    if _PAUSE_SCHEDULING.is_set():
                        _PAUSE_SCHEDULING.clear()
                        print("[batch:control] resumed", flush=True)
                    else:
                        _PAUSE_SCHEDULING.set()
                        print("[batch:control] paused (running jobs continue)", flush=True)
                    with scheduler_condition:
                        scheduler_condition.notify_all()
                    continue
                if lowered == "r":
                    terminate_all_active_processes(force=True)
                    print("[batch:control] restart requested for all active workers", flush=True)
                    continue
                if lowered == "d":
                    enabled = _toggle_debug()
                    print(f"[batch:control] debug {'enabled' if enabled else 'disabled'}", flush=True)
                    continue
                if lowered in worker_shortcuts:
                    worker_name = worker_shortcuts[lowered]
                    if terminate_active_process(worker_name, force=True):
                        print(f"[batch:control] restart requested for {worker_name}", flush=True)
                    else:
                        print(f"[batch:control] {worker_name} is idle", flush=True)
                    continue
                if lowered == "h":
                    print(
                        f"[batch:controls] p=pause/resume r=restart-active {shortcut_list}",
                        flush=True,
                    )
        finally:
            _PAUSE_SCHEDULING.clear()
            with scheduler_condition:
                scheduler_condition.notify_all()

    thread = threading.Thread(target=run_controls, name="batch-console-controls", daemon=True)
    thread.start()
    return stop_event, thread
