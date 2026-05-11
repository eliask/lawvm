from __future__ import annotations

import io
import json
import tarfile

from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row
from lawvm.norway.index import NOAmendmentIndex, NOAmendmentIndexEntry, build_no_amendment_index, save_no_amendment_index
from lawvm.norway.replay import _effective_date_from_amendment, replay_no_to_pit
from lawvm.tools.replay_payloads import build_no_replay_payload


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


def _occupied_insert_amendment_xml(date_in_force: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-add-new-part="lov/2025-01-01-1/§2/nummer/1">
        <article class="defaultP">I loven skal ny nr. 1 tilfoyes.</article>
        <li data-li-identifier="1." data-name="1.">erstattet krav</li>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _replace_renumber_amendment_xml(date_in_force: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-change-part="lov/2025-01-01-1/§2/nummer/3">
        <article class="defaultP">§ 2 nr. 3 skal lyde:</article>
        <li data-li-identifier="3." data-name="3.">tredje krav</li>
      </article>
      <article class="change" data-move-part="lov/2025-01-01-1/§2/nummer/3;;lov/2025-01-01-1/§2/nummer/4">
        <article class="defaultP">Nåværende § 2 nr. 3 blir nytt nr. 4.</article>
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


def test_replay_no_to_pit_surfaces_action_family_adjudications(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _occupied_insert_amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert [(item.kind, item.detail["rule_id"]) for item in result.adjudications] == [
        ("no_replay_insert_occupied_target_replaced", "no_insert_occupied_target_replace")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudications_count"] == 1
    assert payload["adjudication_kind_counts"] == {
        "no_replay_insert_occupied_target_replaced": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "norway"
    assert evidence_row["rule_id"] == "no_insert_occupied_target_replace"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["blocking"] is True
    assert evidence_row["strict_disposition"] == "block"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_replay_no_to_pit_strict_action_family_rejects_recovery(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _occupied_insert_amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
        strict_action_family=True,
    )

    assert result.error is not None
    assert "action-family recovery" in result.error
    assert [(item.kind, item.detail["rule_id"]) for item in result.adjudications] == [
        ("no_replay_insert_occupied_target_replaced", "no_insert_occupied_target_replace")
    ]


def test_replay_no_to_pit_surfaces_parse_action_family_promotion(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _replace_renumber_amendment_xml("2025-02-10")),
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    kinds = [item.kind for item in result.adjudications]
    assert "no_parse_replace_promoted_to_insert_for_same_target_renumber" in kinds
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"]["no_parse_replace_promoted_to_insert_for_same_target_renumber"] == 1


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
    assert [(item.kind, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_future_effective_skipped", "temporal")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"] == {
        "no_replay_future_effective_skipped": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_future_effective_skipped"
    assert evidence_row["phase"] == "temporal"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert evidence_row["blocking"] is False
    assert evidence_row["strict_disposition"] == "record"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()

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
    assert [(item.kind, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_unknown_effective_skipped", "temporal")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"] == {
        "no_replay_unknown_effective_skipped": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_unknown_effective_skipped"
    assert evidence_row["phase"] == "temporal"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_replay_no_to_pit_surfaces_contingent_commencement_skip(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
        ],
    )
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2001-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="contingent",
                effective_date=None,
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            )
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-12-31",
        data_dir=tmp_path,
        index=index,
    )

    assert result.error is None
    assert result.amendments_skipped_contingent == ["no/lovtid/2025-02-02-5"]
    assert [(item.kind, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_contingent_commencement_skipped", "temporal")
    ]
    payload = build_no_replay_payload(result)
    assert payload["adjudication_kind_counts"] == {
        "no_replay_contingent_commencement_skipped": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_contingent_commencement_skipped"
    assert evidence_row["phase"] == "temporal"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_replay_no_to_pit_marks_missing_source_separately(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
        ],
    )
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2001-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="date",
                effective_date="2025-02-10",
                base_ids=("no/lov/2025-01-01-1",),
                n_ops=1,
            )
        ],
    )

    result = replay_no_to_pit(
        "no/lov/2025-01-01-1",
        as_of="2025-12-31",
        data_dir=tmp_path,
        index=index,
    )

    assert result.error is None
    assert result.amendments_applied == []
    assert result.amendments_skipped_unknown_effective == []
    assert result.amendments_skipped_missing_source == ["no/lovtid/2025-02-02-5"]
    assert [(item.kind, item.source_statute, item.detail["phase"]) for item in result.adjudications] == [
        ("no_replay_missing_amendment_source", "no/lovtid/2025-02-02-5", "acquisition")
    ]
    payload = build_no_replay_payload(result)
    assert payload["amendment_counts"]["unknown_effective"] == 0
    assert payload["amendment_counts"]["missing_source"] == 1
    assert payload["skipped_amendments"]["missing_source"] == ["no/lovtid/2025-02-02-5"]
    assert payload["adjudication_kind_counts"] == {
        "no_replay_missing_amendment_source": 1
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["rule_id"] == "no_replay_missing_amendment_source"
    assert evidence_row["phase"] == "acquisition"
    assert evidence_row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


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
