import zipfile

import numpy as np

from local_tts_renderer import cli_render_flow
from local_tts_renderer.document_helpers import build_group_directory_map
from local_tts_renderer.sources import load_source


def test_nested_ncx_parent_without_href_and_manifest_order(tmp_path, monkeypatch):
    source = tmp_path / 'book.epub'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr('OPS/book.opf', '''<package><manifest>
          <item id="toc" href="nav/toc.ncx" media-type="application/x-dtbncx+xml"/>
          <item id="a" href="text/a.xhtml" media-type="application/xhtml+xml"/>
          <item id="b" href="text/b.xhtml" media-type="application/xhtml+xml"/>
          </manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>''')
        archive.writestr('OPS/nav/toc.ncx', '''<ncx><navMap>
          <navPoint><navLabel><text>Part</text></navLabel>
            <navPoint><navLabel><text>Alpha</text></navLabel><content src="../text/a.xhtml"/></navPoint>
            <navPoint><navLabel><text>Beta</text></navLabel><content src="../text/b.xhtml"/></navPoint>
          </navPoint></navMap></ncx>''')
        for letter in ['a', 'b']:
            archive.writestr(f'OPS/text/{letter}.xhtml', f'<html><body><p>Text {letter}.</p></body></html>')
    document = load_source(source)
    assert [(ch.title, ch.text, ch.group) for ch in document.chapters] == [
        ('Beta', 'Text b.', 'Part'), ('Alpha', 'Text a.', 'Part')]
    assert [node.href for node in document.navigation[0].children] == ['OPS/text/a.xhtml', 'OPS/text/b.xhtml']
    monkeypatch.setattr(cli_render_flow, 'CREATE_AUDIO_WITH_RETRY', lambda **kw: ([np.zeros(2400, dtype=np.float32)], 24000))
    manifest = cli_render_flow.render_audio(
        kokoro=object(), chapters=document.chapters, base_output_dir=tmp_path / 'out',
        output_root=tmp_path / 'out' / 'book', group_dir_map=build_group_directory_map(document.chapters),
        voice='test', lang='en-us', trim_mode='off', speed=1.0, max_chars=100,
        silence_ms=0, max_part_minutes=10, keep_chunks=False, mp3_only=True,
        force=False, audio_metadata=None, heartbeat_seconds=0)
    assert [chunk['chapter'] for chunk in manifest['chunks']] == ['Beta', 'Alpha']
    assert [chunk['text'] for chunk in manifest['chunks']] == ['Text b.', 'Text a.']
