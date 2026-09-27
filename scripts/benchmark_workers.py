"""Real six-task benchmark; run with PYTHONPATH=src and the project interpreter."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

from local_tts_renderer import scheduler_args
from local_tts_renderer.scheduler_jobs import build_jobs
from local_tts_renderer.scheduler_runtime import run_worker
from local_tts_renderer.scheduler_types import WorkerConfig, WorkerStatus
from local_tts_renderer.scheduler_process import (
    _ACTIVE_PROCESSES, _ACTIVE_PROCESSES_LOCK, terminate_all_active_processes, wait_for_active_processes,
)


def memory_sample():
    with _ACTIVE_PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES.values())
    ram = 0
    for process in processes:
        try:
            status = Path(f'/proc/{process.pid}/status').read_text()
            ram += int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:'))) * 1024
        except (OSError, StopIteration):
            pass
    query = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=5, check=True)
    pids = {str(p.pid) for p in processes}
    vram = sum(int(parts[1].strip()) * 1024**2 for line in query.stdout.splitlines()
               if len(parts := line.split(',')) == 2 and parts[0].strip() in pids and parts[1].strip().isdigit())
    return ram, vram


def run(source, root, mode, task_repeats=1):
    sys.argv = ['batch', '--input', str(source), '--out', str(root), '--worker-mode', mode,
                '--job-max-chars', '2000', '--max-retries', '0', '--no-console-controls']
    args = scheduler_args.parse_args()
    jobs, _, caches = build_jobs([source], root, False, job_max_chars=2000,
                                max_chars=args.max_chars, max_phoneme_chars=args.max_phoneme_chars)
    jobs = [j for j in jobs if j.estimated_chars >= 1500][:6]
    if len(jobs) != 6:
        raise RuntimeError('Source needs six segments of at least 1500 characters')
    workers = [WorkerConfig('gpu-1', 'CUDAExecutionProvider'), WorkerConfig('gpu-2', 'CUDAExecutionProvider'),
               WorkerConfig('cpu-1', 'CPUExecutionProvider')]
    # Fixed assignment gives both modes exactly the same work on each provider.
    pending = [[replace(j, preferred_provider=w.provider,
                        output_name=j.output_name if task_repeats == 1 else f'{j.output_name}-repeat-{repeat:02d}')
                for repeat in range(task_repeats) for j in jobs[i*2:i*2+2]] for i, w in enumerate(workers)]
    jobs = [j for assigned in pending for j in assigned]
    statuses = {w.name: WorkerStatus() for w in workers}
    counters = dict(active=0, done=0, failed=0, completed_chunks=0)
    condition, bootstrap = threading.Condition(), threading.Lock()
    args._scheduler_stop = threading.Event()
    started = time.monotonic()
    threads = [threading.Thread(target=run_worker, args=(w, pending[i], args, root/'runner.jsonl',
               Path(sys.executable), Path('unused'), len(jobs), sum(j.estimated_chunks for j in jobs), statuses,
               counters, condition, time.time(), {w.name: root/'temp'/w.name}, caches, bootstrap))
               for i, w in enumerate(workers)]
    try:
        for thread in threads:
            thread.start()
        peak_ram = peak_vram = 0
        while any(t.is_alive() for t in threads):
            ram, vram = memory_sample()
            peak_ram, peak_vram = max(peak_ram, ram), max(peak_vram, vram)
            time.sleep(1)
    except BaseException:
        with condition:
            args._scheduler_stop.set()
            condition.notify_all()
        terminate_all_active_processes(force=False)
        if not wait_for_active_processes(10):
            terminate_all_active_processes(force=True)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=15)
        raise
    elapsed = time.monotonic() - started
    if counters['done'] != len(jobs) or counters['failed'] or any(pending):
        raise RuntimeError(f'Benchmark failed: {counters}')
    texts, durations = [], []
    observed_providers = []
    session_metrics, task_metrics = [], []
    for log in root.rglob("*.runner.log"):
        for line in log.read_text().splitlines():
            if line.startswith("[run:worker-ready] "):
                line = line.split(" ", 1)[1]
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if 'session_providers' in entry:
                observed_providers.append(entry['session_providers'][0])
            if entry.get("event") in ("ready", "session_metrics"):
                session_metrics.append(entry)
            elif entry.get("event") in ("result", "document_metrics"):
                task_metrics.append(entry)
    for job in jobs:
        payload = json.loads((root/job.output_subdir/(job.output_name+'.json')).read_text())
        texts.append([c['text'] for c in payload['chunks']])
        durations.append(sum(p['duration_seconds'] for p in payload['parts']))
        for part in payload['parts']:
            subprocess.run(['ffmpeg', '-v', 'error', '-i', part['mp3_path'], '-f', 'null', '-'], check=True)
    expected_sessions = 6 if mode == 'subprocess' else 3
    expected_gpu = 4 if mode == 'subprocess' else 2
    if len(observed_providers) != expected_sessions or observed_providers.count('CUDAExecutionProvider') != expected_gpu:
        raise RuntimeError(f'Wrong session count/providers: {observed_providers}')
    result = dict(mode=mode, seconds=elapsed, audio_seconds=sum(durations), rtf=elapsed/sum(durations),
                  peak_ram_bytes=peak_ram, peak_vram_bytes=peak_vram, texts=texts, durations=durations,
                  session_metrics=session_metrics, task_metrics=task_metrics, session_providers=observed_providers)
    (root/'measurement.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--soak-repeats', type=int, default=0, help='Optional repeated pairs per persistent worker for memory stability checks.')
    args = parser.parse_args()
    if args.soak_repeats < 0:
        parser.error("--soak-repeats must be nonnegative")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for repeat in range(args.repeats):
        for mode in (['subprocess', 'persistent'] if repeat % 2 == 0 else ['persistent', 'subprocess']):
            result = run(args.input.resolve(), args.output/f'{repeat+1}-{mode}', mode)
            if results and result['texts'] != results[0]['texts']:
                raise RuntimeError('Different manifest text between benchmark runs')
            results.append(result)
            (args.output/'results.json').write_text(json.dumps(results, indent=2))
    if args.soak_repeats > 0:
        run(args.input.resolve(), args.output/'memory-soak', 'persistent', args.soak_repeats)
    medians = {mode: statistics.median(r['seconds'] for r in results if r['mode'] == mode)
               for mode in ('subprocess', 'persistent')}
    print(json.dumps(dict(medians=medians, speedup=medians['subprocess']/medians['persistent'])))


if __name__ == '__main__':
    main()
