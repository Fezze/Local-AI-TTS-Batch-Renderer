from __future__ import annotations


def tts_main(*args, **kwargs):
    from .cli import main

    return main(*args, **kwargs)


def batch_main(*args, **kwargs):
    from .scheduler import main

    return main(*args, **kwargs)


__all__ = ["tts_main", "batch_main"]
