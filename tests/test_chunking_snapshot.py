from local_tts_renderer.chunking import chunk_section


def test_chunking_snapshot_basic_markdown() -> None:
    text = "Paragraph one. " * 80
    chunks = chunk_section("Intro", text, max_chars=180, start_index=1)
    assert len(chunks) > 3
    assert chunks[0].index == 1
    assert chunks[-1].index == len(chunks)
    assert all(chunk.text.strip() for chunk in chunks)


def test_chunking_preserves_heading() -> None:
    chunks = chunk_section("Heading", "A short paragraph.", max_chars=120, start_index=5)
    assert len(chunks) == 1
    assert chunks[0].heading == "Heading"
    assert chunks[0].index == 5
