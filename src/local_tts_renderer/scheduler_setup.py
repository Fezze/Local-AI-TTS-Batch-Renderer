from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .document_helpers import slugify
from .providers import build_worker_provider_list, parse_provider_priority, probe_available_providers
from .scheduler_jobs import prepare_worker_temp_dirs
from .scheduler_logging import debug_log
from .scheduler_types import WorkerConfig


@dataclass(frozen=True)
class SchedulerRuntimeSetup:
    provider_priority: list[str]
    available_providers: list[str]
    worker_providers: list[str]
    workers: list[WorkerConfig]
    python_exe: Path
    script_path: Path
    runner_log: Path
    run_tmp_root: Path
    worker_temp_dirs: dict[str, Path]


def build_worker_configs(worker_providers: list[str]) -> list[WorkerConfig]:
    workers: list[WorkerConfig] = []
    gpu_index = 0
    cpu_index = 0
    for provider in worker_providers:
        if provider == "CPUExecutionProvider":
            cpu_index += 1
            workers.append(WorkerConfig(name=f"cpu-{cpu_index}", provider=provider))
        else:
            gpu_index += 1
            workers.append(WorkerConfig(name=f"gpu-{gpu_index}", provider=provider))
    return workers


def prepare_scheduler_runtime(args, inputs: list[Path], output_dir: Path) -> SchedulerRuntimeSetup:
    provider_priority = parse_provider_priority(args.providers)
    available_providers = probe_available_providers()
    print(
        f"[batch:providers] available_probe=runtime priority={provider_priority}",
        flush=True,
    )
    debug_log(args.debug, f"provider_probe_runtime_available={available_providers}")
    worker_providers = build_worker_provider_list(
        available=available_providers,
        gpu_workers=args.gpu_workers,
        cpu_workers=args.cpu_workers,
        provider_priority=provider_priority,
    )
    workers = build_worker_configs(worker_providers)
    run_tmp_root, worker_temp_dirs = prepare_worker_temp_dirs(workers)
    python_exe = Path(sys.executable).resolve()
    script_path = Path(__file__).resolve().parents[2] / "md_to_audio.py"
    runner_log = (output_dir / slugify(inputs[0].stem) / "runner.jsonl") if len(inputs) == 1 else (output_dir / "runner.jsonl")
    debug_log(args.debug, f"python_exe={python_exe} script_path={script_path}")
    return SchedulerRuntimeSetup(
        provider_priority=provider_priority,
        available_providers=available_providers,
        worker_providers=worker_providers,
        workers=workers,
        python_exe=python_exe,
        script_path=script_path,
        runner_log=runner_log,
        run_tmp_root=run_tmp_root,
        worker_temp_dirs=worker_temp_dirs,
    )


def log_scheduler_runtime(args, runtime: SchedulerRuntimeSetup) -> None:
    print(f"[batch:workers] {', '.join(f'{w.name}:{w.provider}' for w in runtime.workers)}", flush=True)
    print(f"[batch:runtime] runner_log={runtime.runner_log} tmp_root={runtime.run_tmp_root}", flush=True)
    debug_log(args.debug, f"provider_order_resolved={runtime.worker_providers}")


__all__ = [
    "SchedulerRuntimeSetup",
    "build_worker_configs",
    "log_scheduler_runtime",
    "prepare_scheduler_runtime",
]
