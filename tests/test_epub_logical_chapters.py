from pathlib import Path
import zipfile

import pytest

from local_tts_renderer.sources import load_source


def make_epub(path, bodies, toc='', epub3=False, order=None):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        items = ''.join(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>' for i, name in enumerate(bodies))
        nav_item = '<item id="toc" href="nav/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>' if epub3 else '<item id="toc" href="nav/toc.ncx" media-type="application/x-dtbncx+xml"/>'
        spine = ''.join(f'<itemref idref="c{i}"/>' for i in (order if order is not None else range(len(bodies))))
        z.writestr('OPS/book.opf', f'<package><metadata><title>Book</title></metadata><manifest>{items}{nav_item if toc else ""}</manifest><spine>{spine}</spine></package>')
        if toc:
            z.writestr('OPS/nav/toc.xhtml' if epub3 else 'OPS/nav/toc.ncx', toc)
        for name, body in bodies.items():
            z.writestr('OPS/'+name, f'<html xmlns:epub="http://www.idpf.org/2007/ops"><body>{body}</body></html>')
    return load_source(path)


def canonical(doc):
    return [(c.title, ' '.join(c.text.split()), c.group) for c in doc.chapters]


@pytest.mark.parametrize('epub3', [False, True])
def test_toc_fragments_split_same_resource_and_keep_inline_text(tmp_path, epub3):
    if epub3:
        toc = '''<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="page-list"><ol><li><a href="../all.xhtml#b">wrong</a></li></ol></nav>
        <nav epub:type="toc"><ol><li><span>Part</span><ol><li><a href="../all.xhtml#a">Alpha</a></li><li><a href="../all.xhtml#b">Beta</a></li></ol></li></ol></nav></body></html>'''
    else:
        toc = '''<ncx><navMap><navPoint><navLabel><text>Part</text></navLabel>
        <navPoint><navLabel><text>Alpha</text></navLabel><content src="../all.xhtml#a"/></navPoint>
        <navPoint><navLabel><text>Beta</text></navLabel><content src="../all.xhtml#b"/></navPoint>
        </navPoint></navMap></ncx>'''
    doc = make_epub(tmp_path/'book.epub', {'all.xhtml':'<div id="a"><p>First <em>inline</em> sentence.</p></div><a id="b"/><p>Second sentence.</p>'}, toc, epub3)
    assert canonical(doc) == [('Alpha','First inline sentence.','Part'), ('Beta','Second sentence.','Part')]
    assert [node.href for node in doc.navigation[0].children] == ['OPS/all.xhtml#a','OPS/all.xhtml#b']


def test_chapters_independent_of_resource_boundaries(tmp_path):
    first='<h1>One</h1><p>First paragraph.</p>'
    continuation='<p>Second paragraph.</p>'
    second='<h1>Two</h1><p>Third paragraph.</p>'
    whole=make_epub(tmp_path/'whole.epub', {'all.xhtml':first+continuation+second})
    split=make_epub(tmp_path/'split.epub', {'one.xhtml':first,'continuation.xhtml':continuation,'two.xhtml':second})
    assert canonical(whole) == canonical(split) == [('One','One First paragraph. Second paragraph.',None),('Two','Two Third paragraph.',None)]
    assert len(split.chapters)==2


def test_unmarked_files_form_one_logical_section(tmp_path):
    doc=make_epub(tmp_path/'book.epub', {'a.xhtml':'<p>Alpha.</p>','b.xhtml':'<p>Beta.</p>'})
    assert canonical(doc)==[('Book','Alpha. Beta.',None)]
    assert doc.chapters[0].text=='Alpha.\n\nBeta.'


def test_missing_anchor_warns_but_preserves_all_text(tmp_path):
    toc='<ncx><navMap><navPoint><navLabel><text>Missing</text></navLabel><content src="../a.xhtml#missing"/></navPoint></navMap></ncx>'
    with pytest.warns(RuntimeWarning,match='target not found'):
        doc=make_epub(tmp_path/'book.epub',{'a.xhtml':'<h2>Real</h2><p>Body.</p>'},toc)
    assert canonical(doc)==[('Real','Real Body.',None)]


def test_spine_order_wins_over_navigation_order(tmp_path):
    toc='<ncx><navMap><navPoint><navLabel><text>A</text></navLabel><content src="../a.xhtml"/></navPoint><navPoint><navLabel><text>B</text></navLabel><content src="../b.xhtml"/></navPoint></navMap></ncx>'
    doc=make_epub(tmp_path/'book.epub',{'a.xhtml':'<p>Alpha.</p>','b.xhtml':'<p>Beta.</p>'},toc,order=[1,0])
    assert [c.title for c in doc.chapters]==['B','A']
