from __future__ import annotations

import io
import json
import tarfile

from lawvm.core.semantic_types import IRNodeKind
from lawvm.norway.index import build_no_amendment_index, save_no_amendment_index
from lawvm.norway.replay import _effective_date_from_amendment, replay_no_to_pit


_BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head>
    <title>Testlov om data</title>
  </head>
  <body>
    <main class="documentBody" data-lovdata-URL="LTI/lov/2025-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="LTI/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="§1" data-lovdata-URL="LTI/lov/2025-01-01-1/§1">
          <h3 class="legalArticleHeader">§ 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder testdata.</article>
        </article>
        <article class="legalArticle" data-name="§2" data-lovdata-URL="LTI/lov/2025-01-01-1/§2">
          <h3 class="legalArticleHeader">§ 2. Krav</h3>
          <article class="legalP" id="ledd1">
            Kravene er:
            <ol>
              <li data-li-identifier="1." data-name="1.">ett krav</li>
              <li data-li-identifier="2." data-name="2.">to krav</li>
            </ol>
          </article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


def _amendment_xml(date_in_force: str | None) -> bytes:
    if date_in_force is None:
        date_block = ""
    else:
        date_block = f"<dd class=\"dateInForce\">{date_in_force}</dd>"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    {date_block}
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change"
               data-change-part="lov/2025-01-01-1/§2/nummer/1"
               data-add-new-part="lov/2025-01-01-1/§2/nummer/3">
        <article class="defaultP">I loven skal nr. 1 endres og ny nr. 3 tilfoyes.</article>
        <li data-li-identifier="1." data-name="1.">oppdatert krav</li>
        <li data-li-identifier="3." data-name="3.">tredje krav</li>
      </article>
      <article class="change" data-repeal-part="lov/2025-01-01-1/§1">
        <article class="defaultP">Paragraf 1 oppheves.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _write_archive(
    archive_path,
    members: list[tuple[str, bytes]],
) -> None:
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def _chapter_sections(result):
    assert result.replayed is not None
    chapter = result.replayed.body.children[0]
    return chapter, [child for child in chapter.children if child.kind is IRNodeKind.SECTION]


def test_replay_no_to_pit_applies_effective_amendments(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.base_source_id == "no/LTI/lov/2025-01-01-1"
    assert result.amendments_scanned == ["no/lovtid/2025-02-02-5"]
    assert result.amendments_applied == ["no/lovtid/2025-02-02-5"]
    assert result.amendments_skipped_future == []
    assert result.amendments_skipped_unknown_effective == []
    assert result.n_ops == 3

    chapter, sections = _chapter_sections(result)
    assert chapter.kind is IRNodeKind.CHAPTER
    assert [section.label for section in sections] == ["2"]
    subsection = sections[0].children[1]
    assert subsection.text == "Kravene er:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "oppdatert krav"),
        ("2", "to krav"),
        ("3", "tredje krav"),
    ]


def test_replay_no_to_pit_skips_future_amendments(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-01",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.amendments_applied == []
    assert result.amendments_skipped_future == ["no/lovtid/2025-02-02-5"]
    assert result.n_ops == 0

    _chapter, sections = _chapter_sections(result)
    assert [section.label for section in sections] == ["1", "2"]
    subsection = sections[1].children[1]
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "ett krav"),
        ("2", "to krav"),
    ]


def test_replay_no_to_pit_marks_unknown_effective_dates(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml(None)),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-12-31",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.amendments_applied == []
    assert result.amendments_skipped_unknown_effective == ["no/lovtid/2025-02-02-5"]
    assert result.n_ops == 0


def test_effective_date_from_amendment_marks_contingent_force() -> None:
    xml = b"""<html><body><dd class=\"dateInForce\">Kongen bestemmer</dd></body></html>"""

    effective = _effective_date_from_amendment(xml, source_date="2025-02-02")

    assert effective.status == "contingent"
    assert effective.effective_date is None


def test_effective_date_from_amendment_uses_source_date_for_straks() -> None:
    xml = b"""<html><body><dd class=\"dateInForce\">Trer i kraft straks.</dd></body></html>"""

    effective = _effective_date_from_amendment(xml, source_date="2025-02-02")

    assert effective.status == "immediate"
    assert effective.effective_date == "2025-02-02"


def test_replay_no_to_pit_accepts_prebuilt_index(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )
    index = build_no_amendment_index(tmp_path)
    index_path = tmp_path / "no_index.json"
    save_no_amendment_index(index, index_path)

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        index_path=index_path,
    )

    assert result.error is None
    assert result.amendments_applied == ["no/lovtid/2025-02-02-5"]
    assert result.n_ops == 3


def test_replay_no_to_pit_accepts_commencement_override(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("Kongen bestemmer")),
        ],
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps({"no/lovtid/2025-02-02-5": "2025-02-10"}),
        encoding="utf-8",
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        commencement_path=commencement_path,
    )

    assert result.error is None
    assert result.amendments_applied == ["no/lovtid/2025-02-02-5"]
    assert result.amendments_skipped_contingent == []
    assert result.n_ops == 3
