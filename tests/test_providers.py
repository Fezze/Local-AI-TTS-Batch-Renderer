import sys

import json

from local_tts_renderer.providers import build_worker_provider_list, describe_provider_resolution, probe_available_providers, resolve_provider


def test_resolve_provider_prefers_requested_order() -> None:
    resolved = resolve_provider(
        available=["CPUExecutionProvider", "CUDAExecutionProvider"],
        requested=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    assert resolved.selected == "CUDAExecutionProvider"


def test_resolve_provider_falls_back_to_cpu() -> None:
    resolved = resolve_provider(
        available=["CPUExecutionProvider"],
        requested=["CUDAExecutionProvider"],
    )
    assert resolved.selected == "CPUExecutionProvider"


def test_build_worker_provider_list_gpu_and_cpu() -> None:
    providers = build_worker_provider_list(
        available=["CUDAExecutionProvider", "CPUExecutionProvider"],
        gpu_workers=2,
        cpu_workers=1,
    )
    assert providers == ["CUDAExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]


def test_probe_available_providers_falls_back_to_cpu(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    assert probe_available_providers() == ["CPUExecutionProvider"]


def test_describe_provider_resolution_serializes_decision() -> None:
    resolved = resolve_provider(
        available=["CUDAExecutionProvider", "CPUExecutionProvider"],
        requested=["DmlExecutionProvider", "CUDAExecutionProvider"],
    )
    payload = json.loads(describe_provider_resolution(resolved))
    assert payload["selected_provider"] == "CUDAExecutionProvider"
    assert payload["available_providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
