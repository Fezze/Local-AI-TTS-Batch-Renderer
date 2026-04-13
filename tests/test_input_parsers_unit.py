from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path

from local_tts_renderer import input_parsers as ip


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"input-parsers-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_clean_markdown_and_split_chapters() -> None:
    raw = "# Part One\nHello [link](https://x). `code`\n\n## not chapter\ntext\n\n# Part Two\nWorld"
    chapters = ip.split_markdown_chapters(raw, fallback_title="Fallback")
    assert len(chapters) == 2
    assert chapters[0].title == "Part One"
    assert "link" in chapters[0].text
    assert "`" not in chapters[0].text


def test_split_markdown_single_chapter_and_limit() -> None:
    text = "# One\nA\n\n# Two\nB " + ("c" * 120)
    single = ip.split_markdown_chapters(text, fallback_title="Fallback", single_chapter=True)
    assert len(single) == 1
    assert single[0].title == "Fallback"

    limited = ip.split_markdown_chapters(text, fallback_title="Fallback", single_chapter=True, max_chapter_chars=80)
    assert len(limited) >= 2
    assert limited[0].title.startswith("Fallback")


def test_group_path_helpers_and_directory_map() -> None:
    chapters = [
        ip.Chapter(title="A", text="x", group="Book / Section"),
        ip.Chapter(title="B", text="x", group="Book / Section"),
        ip.Chapter(title="C", text="x", group="Book / Appendix"),
    ]
    mapping = ip.build_group_directory_map(chapters)
    assert "Book / Section" in mapping
    assert mapping["Book / Section"].parts[0].startswith("01-")
    assert ip.split_group_path("Book / Section") == ["Book", "Section"]
    assert ip.join_group_path(["Book", "Section"]) == "Book / Section"


def test_build_toc_lookup_and_group_map_from_toc() -> None:
    nodes = [
        ip.TocNode(
            title="Book",
            href="OPS/book.xhtml",
            children=[
                ip.TocNode(title="Chapter 1", href="OPS/ch1.xhtml", children=None),
                ip.TocNode(title="Chapter 2", href="OPS/ch2.xhtml", children=None),
            ],
        )
    ]
    lookup = ip.build_toc_lookup(nodes)
    assert "OPS/ch1.xhtml" in lookup
    selected = {"Book", "Book / Chapter 1", "Book / Chapter 2"}
    mapping = ip.build_group_directory_map_from_toc(nodes, selected)
    assert "Book" in mapping
    assert mapping["Book"].parts[0].startswith("01-")


def test_load_chapters_markdown_and_epub() -> None:
    tmp = _mk_tmp_dir()
    try:
        md_path = tmp / "sample.md"
        md_path.write_text("# Intro\nHello\n\n# Next\nWorld", encoding="utf-8")
        md_chapters = ip.load_chapters(md_path)
        assert len(md_chapters) == 2

        epub_path = tmp / "sample.epub"
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
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/></spine>
</package>""",
            )
            zf.writestr(
                "OPS/toc.ncx",
                """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1" playOrder="1">
      <navLabel><text>Chapter One</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>""",
            )
            zf.writestr(
                "OPS/chapter1.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Neutral Chapter</h1><p>Hello text.</p></body></html>""",
            )
        toc = ip.load_epub_toc_from_path(epub_path)
        assert len(toc) == 1
        epub_chapters = ip.load_chapters(epub_path)
        assert len(epub_chapters) == 1
        assert "Hello" in epub_chapters[0].text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
