from __future__ import annotations

import re
from typing import Iterable

from .cli_models import Chunk


def iter_sections(text: str) -> Iterable[tuple[str | None, str]]:
    heading: str | None = None
    body: list[str] = []

    for line in text.splitlines():
        if not line.strip():
            if body and body[-1] != "":
                body.append("")
            continue

        if len(line) < 120 and re.match(r"^[A-Z0-9][A-Za-z0-9 ,:'\"()/-]{0,118}$", line):
            if body:
                yield heading, "\n".join(body).strip()
                body = []
            heading = line
            continue

        body.append(line)

    if body:
        yield heading, "\n".join(body).strip()


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", normalized)
    return [part.strip() for part in parts if part.strip()]


def split_text_for_retry(text: str) -> list[str]:
    sentences = split_sentences(text)
    if len(sentences) > 1:
        midpoint = max(1, len(sentences) // 2)
        return [" ".join(sentences[:midpoint]).strip(), " ".join(sentences[midpoint:]).strip()]

    words = text.split()
    if len(words) > 1:
        midpoint = max(1, len(words) // 2)
        return [" ".join(words[:midpoint]).strip(), " ".join(words[midpoint:]).strip()]

    midpoint = max(1, len(text) // 2)
    return [text[:midpoint].strip(), text[midpoint:].strip()]


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]


def flush_chunk_buffer(chunks: list[Chunk], chunk_index: int, heading: str | None, buffer: list[str]) -> int:
    text = " ".join(part.strip() for part in buffer if part.strip()).strip()
    if text:
        chunks.append(Chunk(index=chunk_index, heading=heading, text=text))
        return chunk_index + 1
    return chunk_index


def chunk_paragraph_text(chunks: list[Chunk], heading: str | None, paragraph: str, max_chars: int, chunk_index: int) -> int:
    sentences = split_sentences(paragraph)
    if not sentences:
        return chunk_index

    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence_len = len(sentence)
        if current and current_len + 1 + sentence_len > max_chars:
            chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, current)
            current = [sentence]
            current_len = sentence_len
            continue

        if sentence_len > max_chars:
            if current:
                chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, current)
                current = []
                current_len = 0
            words = sentence.split()
            overflow: list[str] = []
            overflow_len = 0
            for word in words:
                add_len = len(word) if not overflow else len(word) + 1
                if overflow and overflow_len + add_len > max_chars:
                    chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, overflow)
                    overflow = [word]
                    overflow_len = len(word)
                else:
                    overflow.append(word)
                    overflow_len += add_len
            if overflow:
                chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, overflow)
            continue

        current.append(sentence)
        current_len += sentence_len if len(current) == 1 else sentence_len + 1

    if current:
        chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, current)
    return chunk_index


def chunk_section(heading: str | None, text: str, max_chars: int, start_index: int) -> list[Chunk]:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []
    chunks: list[Chunk] = []
    chunk_index = start_index
    paragraph_buffer: list[str] = []
    paragraph_buffer_len = 0

    for paragraph in paragraphs:
        paragraph_len = len(paragraph)
        if paragraph_len > max_chars:
            if paragraph_buffer:
                chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, paragraph_buffer)
                paragraph_buffer = []
                paragraph_buffer_len = 0
            chunk_index = chunk_paragraph_text(chunks, heading, paragraph, max_chars, chunk_index)
            continue

        add_len = paragraph_len if not paragraph_buffer else paragraph_len + 2
        if paragraph_buffer and paragraph_buffer_len + add_len > max_chars:
            chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, paragraph_buffer)
            paragraph_buffer = [paragraph]
            paragraph_buffer_len = paragraph_len
            continue

        paragraph_buffer.append(paragraph)
        paragraph_buffer_len += add_len if len(paragraph_buffer) > 1 else paragraph_len

    if paragraph_buffer:
        flush_chunk_buffer(chunks, chunk_index, heading, paragraph_buffer)
    return chunks


def build_chunks(cleaned_text: str, max_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    chunk_index = 1
    for heading, section_text in iter_sections(cleaned_text):
        section_chunks = chunk_section(heading, section_text, max_chars=max_chars, start_index=chunk_index)
        chunks.extend(section_chunks)
        if section_chunks:
            chunk_index = section_chunks[-1].index + 1
    return chunks

