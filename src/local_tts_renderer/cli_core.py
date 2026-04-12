from __future__ import annotations

from .cli_audio_utils import (
    build_output_paths,
    build_temp_part_base_name,
    compute_part_output_paths,
    create_audio_with_retry,
    cross_process_io_gate,
    extract_track_number,
    light_trim_audio,
    load_resume_state,
    remove_output_files,
    safe_remove_path,
    save_resume_state,
    write_mp3_from_audio,
    write_mp3_from_wav,
    write_mp3_tags,
)
from .cli_chunking_utils import (
    build_chunks,
    chunk_paragraph_text,
    chunk_section,
    flush_chunk_buffer,
    iter_sections,
    split_paragraphs,
    split_sentences,
    split_text_for_retry,
)
from .cli_entry import expand_inputs, main
from .cli_models import (
    AudioMetadata,
    Chunk,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LANG,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_PART_MINUTES,
    DEFAULT_SILENCE_MS,
    DEFAULT_SPEED,
    DEFAULT_TRIM_MODE,
    DEFAULT_VOICE,
    GROUP_PATH_SEPARATOR,
    MODEL_URL,
    PartialRunComplete,
    VOICES_URL,
)
from .cli_parsing import (
    build_chapter_number_map,
    build_group_directory_map,
    build_group_directory_map_from_toc,
    clean_markdown,
    clean_plain_text,
    extract_epub_metadata,
    get_group_leaf_title,
    join_group_path,
    load_chapters,
    load_chapters_from_cache,
    load_epub_toc_from_path,
    print_chapter_summary,
    print_output_structure_preview,
    print_toc_tree,
    sanitize_filename_component,
    slugify,
    split_group_path,
    split_markdown_chapters,
    strip_front_matter,
    summarize_chapters,
)
from .cli_render_flow import OutputPartWriter, render_audio, render_chunk_audio, save_safe_checkpoint
from .cli_runtime import (
    configure_onnx_provider,
    configure_runtime_temp_dir,
    debug_trace,
    enable_windows_espeak_fallback,
    ensure_file,
    ensure_model_files,
    get_kokoro_class,
    get_onnxruntime,
    is_debug_enabled,
    parse_args,
    start_progress_heartbeat,
)
from .input_parsers import Chapter, TocNode


if __name__ == "__main__":
    raise SystemExit(main())

