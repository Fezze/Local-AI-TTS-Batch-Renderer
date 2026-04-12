from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .cli_audio_utils import create_audio_with_retry, write_mp3_from_wav
from .cli_models import AudioMetadata, PartialRunComplete
from .cli_parsing import (
    Chapter,
    build_chapter_number_map,
    build_group_directory_map,
    build_group_directory_map_from_toc,
    extract_epub_metadata,
    load_chapters,
    load_chapters_from_cache,
    load_epub_toc_from_path,
    print_chapter_summary,
    print_output_structure_preview,
    print_toc_tree,
    sanitize_filename_component,
    slugify,
)
from .cli_render_flow import render_audio
from .cli_runtime import (
    configure_onnx_provider,
    configure_runtime_temp_dir,
    debug_trace,
    enable_windows_espeak_fallback,
    ensure_model_files,
    get_kokoro_class,
    get_onnxruntime,
    parse_args,
)
from .providers import parse_provider_priority


def expand_inputs(paths: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for item in paths:
        matches = [Path(p) for p in sorted(Path().glob(item))] if any(ch in item for ch in "*?[]") else [Path(item)]
        expanded.extend(matches)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in expanded:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def main() -> int:
    args = parse_args()
    if args.wav_to_mp3:
        wav_path = Path(args.wav_to_mp3).resolve()
        mp3_path = wav_path.with_suffix(".mp3")
        converted = write_mp3_from_wav(
            wav_path=wav_path,
            mp3_path=mp3_path,
            bitrate_kbps=args.mp3_bitrate,
            force=args.force,
        )
        print(json.dumps({"input_wav": str(wav_path), "output_mp3": str(converted), "bitrate_kbps": args.mp3_bitrate}), flush=True)
        return 0

    if not args.input or not args.output_dir:
        if not args.list_chapters:
            print("--input and --output-dir are required unless --wav-to-mp3 or --list-chapters is used.", file=sys.stderr)
            return 2
    if not args.input:
        print("--input is required.", file=sys.stderr)
        return 2

    inputs = expand_inputs(args.input)
    if not inputs:
        print("No input files found.", file=sys.stderr)
        return 2
    missing = [path for path in inputs if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing input: {path}", file=sys.stderr)
        return 2

    if args.list_chapters:
        for source_path in inputs:
            if source_path.suffix.lower() == ".epub":
                print(f"Source: {source_path}")
                print_toc_tree(load_epub_toc_from_path(source_path))
            else:
                chapters = [chapter for chapter in load_chapters(source_path) if chapter.text and chapter.text.strip()]
                print_chapter_summary(source_path, chapters)
            chapters = [chapter for chapter in load_chapters(source_path) if chapter.text and chapter.text.strip()]
            print_output_structure_preview(source_path, chapters)
        return 0

    output_dir = Path(args.output_dir).resolve()
    print(f"[run:init] inputs={len(inputs)} output_dir={output_dir} model_dir={Path(args.model_dir).resolve()}", flush=True)
    print(
        "[run:config] "
        f"voice={args.voice} speed={args.speed} max_chars={args.max_chars} "
        f"trim_mode={args.trim_mode} mp3_only={args.mp3_only} force={args.force}",
        flush=True,
    )
    runtime_temp_dir = configure_runtime_temp_dir(output_dir=output_dir, temp_dir=args.temp_dir)
    enable_windows_espeak_fallback()
    model_dir = Path(args.model_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path, voices_path = ensure_model_files(model_dir)
    provider_priority = parse_provider_priority(args.providers)
    provider = configure_onnx_provider(provider_priority=provider_priority)
    ort = get_onnxruntime()
    print(json.dumps({"onnx_provider_preference": provider, "available_providers": ort.get_available_providers(), "runtime_temp_dir": str(runtime_temp_dir)}), flush=True)
    KokoroClass = get_kokoro_class()
    print("[run:bootstrap] creating kokoro session...", flush=True)
    kokoro = KokoroClass(str(model_path), str(voices_path))
    print(json.dumps({"session_providers": kokoro.sess.get_providers()}), flush=True)
    if args.warmup_text and args.warmup_text.strip():
        print("[run:warmup] start", flush=True)
        warmup_start = time.time()
        try:
            create_audio_with_retry(
                kokoro=kokoro,
                text=args.warmup_text.strip(),
                voice=args.voice,
                speed=args.speed,
                lang=args.lang,
                trim_mode=args.trim_mode,
            )
            print(f"[run:warmup] done elapsed={time.time() - warmup_start:.2f}s", flush=True)
        except Exception as exc:
            print(f"[run:warmup] failed error={exc}", flush=True)

    for source_path in inputs:
        if args.chapter_cache and args.chapter_index is not None:
            cache_path = Path(args.chapter_cache).resolve()
            if cache_path.exists():
                chapters = load_chapters_from_cache(cache_path)
                debug_trace(f"load_chapters:cache_done path={cache_path} chapters={len(chapters)}")
            else:
                chapters = load_chapters(source_path)
        else:
            chapters = load_chapters(source_path)
        chapters = [chapter for chapter in chapters if chapter.text and chapter.text.strip()]
        if not chapters:
            print(f"Skipped {source_path}: no readable chapters after cleaning.", file=sys.stderr)
            continue

        audio_metadata = AudioMetadata(source_title=source_path.stem)
        if source_path.suffix.lower() == ".epub":
            audio_metadata = extract_epub_metadata(source_path)
        if source_path.suffix.lower() == ".epub":
            group_dir_map = build_group_directory_map_from_toc(
                load_epub_toc_from_path(source_path),
                {chapter.group for chapter in chapters if chapter.group},
            )
        else:
            group_dir_map = build_group_directory_map(chapters)

        output_root_base = output_dir / slugify(source_path.stem)
        if args.chapter_index is not None:
            if args.chapter_index < 1 or args.chapter_index > len(chapters):
                print(f"Invalid --chapter-index {args.chapter_index} for {source_path}.", file=sys.stderr)
                return 2
            original_chapter = chapters[args.chapter_index - 1]
            chapter_group = original_chapter.group
            chapter_position = args.chapter_index if source_path.suffix.lower() == ".epub" else build_chapter_number_map(chapters)[args.chapter_index]
            chapter_title = original_chapter.title
            output_name = args.output_name or f"{chapter_position:02d}-{sanitize_filename_component(chapter_title)}"
            chapter_subdir = Path(args.output_subdir) if args.output_subdir else Path()
            if not args.output_subdir and chapter_group:
                chapter_subdir = Path(slugify(source_path.stem)) / group_dir_map.get(chapter_group, Path(sanitize_filename_component(chapter_group)))
            elif not args.output_subdir:
                chapter_subdir = Path(slugify(source_path.stem))

            chapter_output_root = output_dir / chapter_subdir
            chapter_for_render = Chapter(title=chapter_title, text=original_chapter.text, group=None)
            try:
                manifest = render_audio(
                    kokoro=kokoro,
                    chapters=[chapter_for_render],
                    base_output_dir=output_dir,
                    output_root=chapter_output_root,
                    group_dir_map={},
                    voice=args.voice,
                    lang=args.lang,
                    trim_mode=args.trim_mode,
                    speed=args.speed,
                    max_chars=args.max_chars,
                    silence_ms=args.silence_ms,
                    max_part_minutes=args.max_part_minutes,
                    keep_chunks=args.keep_chunks,
                    mp3_only=args.mp3_only,
                    force=args.force,
                    audio_metadata=audio_metadata,
                    heartbeat_seconds=args.heartbeat_seconds,
                    final_stem_override=output_name,
                    max_parts_per_run=args.max_parts_per_run,
                )
            except PartialRunComplete:
                return 75
            print(json.dumps({"source": str(source_path), "chapter_index": args.chapter_index, "chapter_title": chapter_title, "output_parts": manifest["parts"], "chunks": manifest["chunk_count"], "voice": manifest["voice"]}), flush=True)
            continue

        output_root = output_root_base
        try:
            manifest = render_audio(
                kokoro=kokoro,
                chapters=chapters,
                base_output_dir=output_dir,
                output_root=output_root,
                group_dir_map=group_dir_map,
                voice=args.voice,
                lang=args.lang,
                trim_mode=args.trim_mode,
                speed=args.speed,
                max_chars=args.max_chars,
                silence_ms=args.silence_ms,
                max_part_minutes=args.max_part_minutes,
                keep_chunks=args.keep_chunks,
                mp3_only=args.mp3_only,
                force=args.force,
                audio_metadata=audio_metadata,
                heartbeat_seconds=args.heartbeat_seconds,
                max_parts_per_run=args.max_parts_per_run,
            )
        except PartialRunComplete:
            return 75
        print(json.dumps({"source": str(source_path), "output_parts": manifest["parts"], "chunks": manifest["chunk_count"], "voice": manifest["voice"]}), flush=True)
    return 0

