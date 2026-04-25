from __future__ import annotations

import io
import json
import tarfile
from argparse import Namespace
from pathlib import Path

from lawvm.tools.no_op_trace import main as no_op_trace_main


def _write_archive(archive_path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


_BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Trace test lov</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="NL/lov/2025-01-01-1">
      <section class="section" data-name="kap1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="§1">
          <h3 class="legalArticleHeader">§ 1. Første</h3>
          <article class="legalP">Første tekst.</article>
        </article>
        <article class="legalArticle" data-name="§2">
          <h3 class="legalArticleHeader">§ 2. Andre</h3>
          <article class="legalP">Andre tekst.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

_AMENDMENT_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-change-part="lov/2025-01-01-1/§2/ledd/1">
        <article class="defaultP">§ 2 første ledd skal lyde:</article>
        <article class="legalP">Oppdatert tekst.</article>
      </article>
      <article class="change" data-repeal-part="lov/2025-01-01-1/§1">
        <article class="defaultP">§ 1 oppheves.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def test_no_op_trace_json_filters_sources_and_ops(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _BASE_XML)],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250202-005.xml", _AMENDMENT_XML)],
    )

    args = Namespace(
        base_id="no/lov/2025-01-01-1",
        data_dir=str(tmp_path),
        index=None,
        path=["section:2"],
        limit=20,
        json=True,
    )

    no_op_trace_main(args)
    payload = json.loads(capsys.readouterr().out)

    assert payload["base_id"] == "no/lov/2025-01-01-1"
    assert payload["source_count"] == 1
    assert payload["matched_source_count"] == 1
    assert payload["op_count"] == 1
    assert payload["sources"][0]["matched_op_count"] == 1
    assert payload["sources"][0]["compiled_op_count"] == 2
    assert payload["ops"][0]["target_text"] == "section:2/subsection:1"


def test_no_op_trace_text_prints_bounded_summary(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _BASE_XML)],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250202-005.xml", _AMENDMENT_XML)],
    )

    args = Namespace(
        base_id="no/lov/2025-01-01-1",
        data_dir=str(tmp_path),
        index=None,
        path=[],
        limit=1,
        json=False,
    )

    no_op_trace_main(args)
    output = capsys.readouterr().out

    assert "Norway Op Trace" in output
    assert "sources/ops" in output
    assert "compiled=" in output
    assert "payload :" in output
