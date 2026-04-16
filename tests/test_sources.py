from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path

from local_tts_renderer.sources import SourceLoadOptions, load_source, supported_suffixes


def _mk_tmp_dir() -> Path:
    path = Path.cwd() / ".test_tmp" / f"sources-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_source_registry_loads_markdown_document() -> None:
    tmp = _mk_tmp_dir()
    try:
        source = tmp / "book.md"
        source.write_text("# One\nA\n\n# Two\nB", encoding="utf-8")
        document = load_source(source, SourceLoadOptions(markdown_single_chapter=False))
        assert document.metadata.source_title == "book"
        assert [chapter.title for chapter in document.chapters] == ["One", "Two"]
        assert ".md" in supported_suffixes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_source_registry_loads_epub_document() -> None:
    tmp = _mk_tmp_dir()
    try:
        source = tmp / "book.epub"
        with zipfile.ZipFile(source, "w") as zf:
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
  <metadata><title>Book Title</title><language>en</language></metadata>
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
    <navPoint id="n1" playOrder="1"><navLabel><text>Chapter One</text></navLabel><content src="chapter1.xhtml"/></navPoint>
  </navMap>
</ncx>""",
            )
            zf.writestr(
                "OPS/chapter1.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>One</h1><p>Hello.</p></body></html>""",
            )
        document = load_source(source)
        assert document.metadata.source_title == "Book Title"
        assert [chapter.title for chapter in document.chapters] == ["Chapter One"]
        assert [node.title for node in document.navigation] == ["Chapter One"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
