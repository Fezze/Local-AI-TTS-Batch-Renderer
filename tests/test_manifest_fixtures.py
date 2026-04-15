from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from pathlib import Path

import numpy as np

from local_tts_renderer import cli_core, cli_render_flow, input_parsers as ip
from local_tts_renderer.cli_models import AudioMetadata


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"manifest-fixtures-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fake_audio(**kwargs):  # type: ignore[no-untyped-def]
    return [np.zeros(24000, dtype=np.float32)], 24000


def _canonical_manifest(manifest: dict) -> dict:
    return {
        "source": manifest["source"],
        "voice": manifest["voice"],
        "lang": manifest["lang"],
        "speed": manifest["speed"],
        "sample_rate": manifest["sample_rate"],
        "chapter_count": manifest["chapter_count"],
        "chunk_count": manifest["chunk_count"],
        "max_part_minutes": manifest["max_part_minutes"],
        "parts": [
            {
                "part": part["part"],
                "duration_seconds": part["duration_seconds"],
                "group": part["group"],
                "chapter_titles": part["chapter_titles"],
                "start_chunk": part["start_chunk"],
                "end_chunk": part["end_chunk"],
            }
            for part in manifest["parts"]
        ],
        "chunks": [
            {
                "index": chunk["index"],
                "heading": chunk["heading"],
                "chapter": chunk["chapter"],
                "chars": chunk["chars"],
                "text": chunk["text"],
            }
            for chunk in manifest["chunks"]
        ],
    }


def test_render_audio_manifest_order_from_markdown(monkeypatch) -> None:
    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", _fake_audio)

    tmp = _mk_tmp_dir()
    try:
        md_path = tmp / "book.md"
        md_path.write_text("# One\nA\n\n# Two\nB\n\n# Three\nC", encoding="utf-8")
        chapters = ip.load_chapters(md_path)

        output_root = tmp / "out"
        manifest = cli_core.render_audio(
            kokoro=object(),
            chapters=chapters,
            base_output_dir=tmp,
            output_root=output_root,
            group_dir_map={},
            voice="voice_a",
            lang="en-us",
            trim_mode="off",
            speed=1.0,
            max_chars=80,
            silence_ms=0,
            max_part_minutes=10.0,
            keep_chunks=False,
            mp3_only=True,
            force=True,
            audio_metadata=AudioMetadata(source_title="Book"),
            heartbeat_seconds=0.0,
        )

        saved = json.loads((output_root.with_suffix(".json")).read_text(encoding="utf-8"))
        expected = json.loads((Path(__file__).parent / "snapshots" / "manifest_order_md.json").read_text(encoding="utf-8"))
        assert _canonical_manifest(manifest) == expected
        assert _canonical_manifest(saved) == expected
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_render_audio_manifest_order_from_epub(monkeypatch) -> None:
    monkeypatch.setattr(cli_render_flow, "CREATE_AUDIO_WITH_RETRY", _fake_audio)

    tmp = _mk_tmp_dir()
    try:
        epub_path = tmp / "book.epub"
        with zipfile.ZipFile(epub_path, "w") as zf:
            zf.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
            )
            zf.writestr(
                "OPS/content.opf",
                """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>""",
            )
            zf.writestr(
                "OPS/toc.ncx",
                """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1" playOrder="1"><navLabel><text>Part One</text></navLabel><content src="chapter1.xhtml"/></navPoint>
    <navPoint id="n2" playOrder="2"><navLabel><text>Part Two</text></navLabel><content src="chapter2.xhtml"/></navPoint>
  </navMap>
</ncx>""",
            )
            zf.writestr(
                "OPS/chapter1.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>One</h1><p>A</p></body></html>""",
            )
            zf.writestr(
                "OPS/chapter2.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Two</h1><p>B</p></body></html>""",
            )

        chapters = ip.load_chapters(epub_path)
        output_root = tmp / "out"
        manifest = cli_core.render_audio(
            kokoro=object(),
            chapters=chapters,
            base_output_dir=tmp,
            output_root=output_root,
            group_dir_map=ip.build_group_directory_map_from_toc(ip.load_epub_toc_from_path(epub_path), {chapter.group for chapter in chapters if chapter.group}),
            voice="voice_a",
            lang="en-us",
            trim_mode="off",
            speed=1.0,
            max_chars=80,
            silence_ms=0,
            max_part_minutes=10.0,
            keep_chunks=False,
            mp3_only=True,
            force=True,
            audio_metadata=AudioMetadata(source_title="Book"),
            heartbeat_seconds=0.0,
        )

        saved = json.loads((output_root.with_suffix(".json")).read_text(encoding="utf-8"))
        expected = json.loads((Path(__file__).parent / "snapshots" / "manifest_order_epub.json").read_text(encoding="utf-8"))
        assert _canonical_manifest(manifest) == expected
        assert _canonical_manifest(saved) == expected
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
