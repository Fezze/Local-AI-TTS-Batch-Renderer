from local_tts_renderer.providers import build_worker_provider_list, resolve_provider


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
