from __future__ import annotations

from argparse import Namespace

from lawvm.tools import source_dump


class _DummyCorpus:
    def __init__(self, xml_bytes: bytes) -> None:
        self._xml_bytes = xml_bytes

    def read_source(self, sid: str) -> bytes | None:
        return self._xml_bytes if sid == "1995/1552" else None


def test_build_source_dump_returns_line_numbered_section_snippet(monkeypatch) -> None:
    xml = b"""<akn xmlns='urn:test'>
  <body>
    <chapter>
      <num>3 luku</num>
      <section>
        <num>12 \xc2\xa7</num>
        <heading>Test heading</heading>
        <paragraph><content>Alpha</content></paragraph>
      </section>
    </chapter>
  </body>
</akn>
"""

    monkeypatch.setattr(source_dump, "get_corpus_store", lambda: _DummyCorpus(xml))

    bundle = source_dump.build_source_dump("1995/1552", address="chapter:3/section:12")

    assert bundle["statute_id"] == "1995/1552"
    assert bundle["address"] == "chapter:3/section:12"
    assert bundle["selected_kind"] == "section"
    assert bundle["selected_label"] == "12"
    assert any(line.lstrip().startswith("1 | ") for line in source_dump._format_text(bundle).splitlines())
    assert "Test heading" in bundle["xml"]
    assert "Alpha" in bundle["xml"]


def test_source_dump_main_prints_line_numbered_xml(capsys, monkeypatch) -> None:
    xml = b"""<akn xmlns='urn:test'>
  <body>
    <section>
      <num>4 \xc2\xa7</num>
      <heading>Printed heading</heading>
    </section>
  </body>
</akn>
"""

    monkeypatch.setattr(source_dump, "get_corpus_store", lambda: _DummyCorpus(xml))

    source_dump.main(Namespace(statute_id="1995/1552", address="section:4", json=False))

    out = capsys.readouterr().out
    assert "Statute  : 1995/1552" in out
    assert "Address  : section:4" in out
    assert "Kind     : section" in out
    assert "1 | <section" in out
    assert "Printed heading" in out
