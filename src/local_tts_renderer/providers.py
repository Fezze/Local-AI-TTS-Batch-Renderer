from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

DEFAULT_PROVIDER_PRIORITY = [
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "ROCMExecutionProvider",
    "CPUExecutionProvider",
]

GPU_PROVIDER_NAMES = {
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "ROCMExecutionProvider",
    "TensorrtExecutionProvider",
}


@dataclass(frozen=True)
class ProviderResolution:
    selected: str
    available: list[str]
    requested: list[str]


def parse_provider_priority(raw: str | None, fallback: Iterable[str] | None = None) -> list[str]:
    if raw:
        parsed = [item.strip() for item in raw.split(",") if item.strip()]
        if parsed:
            return parsed
    return list(fallback or DEFAULT_PROVIDER_PRIORITY)


def resolve_provider(
    available: Iterable[str],
    requested: Iterable[str] | None = None,
    fallback: Iterable[str] | None = None,
) -> ProviderResolution:
    available_list = list(available)
    requested_list = list(requested or [])
    search_order = requested_list or list(fallback or DEFAULT_PROVIDER_PRIORITY)
    for provider in search_order:
        if provider in available_list:
            return ProviderResolution(selected=provider, available=available_list, requested=search_order)
    if "CPUExecutionProvider" in available_list:
        return ProviderResolution(selected="CPUExecutionProvider", available=available_list, requested=search_order)
    if not available_list:
        raise RuntimeError("No ONNX Runtime providers available")
    return ProviderResolution(selected=available_list[0], available=available_list, requested=search_order)


def build_worker_provider_list(
    available: Iterable[str],
    gpu_workers: int,
    cpu_workers: int,
    provider_priority: Iterable[str] | None = None,
) -> list[str]:
    available_list = list(available)
    priority = list(provider_priority or DEFAULT_PROVIDER_PRIORITY)
    gpu_provider = None
    for provider in priority:
        if provider in available_list and provider in GPU_PROVIDER_NAMES:
            gpu_provider = provider
            break

    worker_providers: list[str] = []
    if gpu_provider is not None:
        worker_providers.extend([gpu_provider] * max(gpu_workers, 0))
    worker_providers.extend(["CPUExecutionProvider"] * max(cpu_workers, 0))

    if not worker_providers:
        resolved = resolve_provider(available_list, fallback=priority)
        worker_providers.append(resolved.selected)

    return worker_providers
