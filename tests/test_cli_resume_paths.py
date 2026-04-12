from pathlib import Path

from local_tts_renderer.cli import build_temp_part_base_name, compute_part_output_paths


def test_temp_part_base_name_is_unique_for_chapter_override() -> None:
    left = build_temp_part_base_name(part_index=2, final_stem_override="04-Chapter One - Tragedy")
    right = build_temp_part_base_name(part_index=2, final_stem_override="08-Chapter Five - Machinations")
    assert left != right


def test_resume_temp_paths_do_not_collapse_to_global_part_name() -> None:
    base_output_dir = Path(r"C:\tmp\out")
    chapter_dir = base_output_dir / "book" / "part-a"
    base_name = build_temp_part_base_name(part_index=2, final_stem_override="04-Chapter One - Tragedy")
    wav_path, mp3_path = compute_part_output_paths(
        output_root=chapter_dir,
        base_output_dir=base_output_dir,
        part_index=2,
        multi_part=False,
        base_name=base_name,
        group_name=None,
        final_stem_override="04-Chapter One - Tragedy",
    )
    assert wav_path.name.startswith("02-tmp-04")
    assert "-part-02.wav" in wav_path.name
    assert mp3_path.name.startswith("02-tmp-04")
    assert "-part-02.mp3" in mp3_path.name


def test_multipart_final_name_keeps_chapter_first_order() -> None:
    base_output_dir = Path(r"C:\tmp\out")
    chapter_dir = base_output_dir / "book" / "part-a"
    wav_path, mp3_path = compute_part_output_paths(
        output_root=chapter_dir,
        base_output_dir=base_output_dir,
        part_index=2,
        multi_part=False,
        base_name="04-Chapter One - Tragedy",
        group_name=None,
        final_stem_override="04-Chapter One - Tragedy",
    )
    assert wav_path.name == "04-02 - Chapter One - Tragedy.wav"
    assert mp3_path.name == "04-02 - Chapter One - Tragedy.mp3"


def test_multipart_final_name_keeps_chapter_first_order_with_sanitized_stem() -> None:
    base_output_dir = Path(r"C:\tmp\out")
    chapter_dir = base_output_dir / "book" / "part-a"
    wav_path, mp3_path = compute_part_output_paths(
        output_root=chapter_dir,
        base_output_dir=base_output_dir,
        part_index=2,
        multi_part=False,
        base_name="04 - Chapter One - Tragedy",
        group_name=None,
        final_stem_override="04-Chapter One - Tragedy",
    )
    assert wav_path.name == "04-02 - Chapter One - Tragedy.wav"
    assert mp3_path.name == "04-02 - Chapter One - Tragedy.mp3"


def test_first_part_gets_01_when_forced_for_known_multipart() -> None:
    base_output_dir = Path(r"C:\tmp\out")
    chapter_dir = base_output_dir / "book" / "part-a"
    wav_path, mp3_path = compute_part_output_paths(
        output_root=chapter_dir,
        base_output_dir=base_output_dir,
        part_index=1,
        multi_part=False,
        base_name="04 - Chapter One - Tragedy",
        group_name=None,
        final_stem_override="04-Chapter One - Tragedy",
        force_numbered_first_part=True,
    )
    assert wav_path.name == "04-01 - Chapter One - Tragedy.wav"
    assert mp3_path.name == "04-01 - Chapter One - Tragedy.mp3"
