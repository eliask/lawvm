from __future__ import annotations
from lawvm.core.ir import IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_to_text

import io
import tarfile
from types import SimpleNamespace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import ingest_consolidated, verify_consistency
from lawvm.core.timeline_consistency import ConsistencyDivergence
from lawvm.norway.sources import ingest_no_public_archives
from lawvm.norway.verify import (
    NO_VERIFY_COMPARE_OTHER_LAWS_CONTEXT_SUPPRESSED,
    _infer_no_source_signal,
    _normalize_no_compare_tree,
    _partition_primary_divergences,
    no_paths_related,
    irnode_to_no_comparison_text,
    normalize_no_comparison_text,
)
from lawvm.norway.verify import build_no_verify_partition, build_no_verify_scan, verify_no_against_current


_BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Testlov om data</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="LTI/lov/2025-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="LTI/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="§1" data-lovdata-URL="LTI/lov/2025-01-01-1/§1">
          <h3 class="legalArticleHeader">§ 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder testdata.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


_CURRENT_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Testlov om data</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="NL/lov/2025-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="NL/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="§1" data-lovdata-URL="NL/lov/2025-01-01-1/§1">
          <h3 class="legalArticleHeader">§ 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder oppdatert testdata.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


_CURRENT_DIVERGENT_XML = _CURRENT_XML.replace(
    b"oppdatert testdata",
    b"annen testdata",
)


def _amendment_xml() -> bytes:
    return """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">2025-02-10</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-change-part="lov/2025-01-01-1/§1">
        <article class="defaultP">Paragraf 1 skal lyde:</article>
        <article class="legalArticle" data-name="§1" data-lovdata-URL="LTI/lov/2025-01-01-1/§1">
          <h3 class="legalArticleHeader">§ 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder oppdatert testdata.</article>
        </article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _large_divergent_current_xml(section_count: int = 60) -> bytes:
    sections = []
    for idx in range(1, section_count + 1):
        sections.append(
            f"""
        <article class="legalArticle" data-name="§{idx}" data-lovdata-URL="NL/lov/2010-01-01-1/§{idx}">
          <h3 class="legalArticleHeader">§ {idx}. Tittel</h3>
          <article class="legalP">Gjeldende tekst {idx}.</article>
        </article>"""
        )
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Stor testlov</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="NL/lov/2010-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="NL/lov/2010-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        {''.join(sections)}
      </section>
    </main>
  </body>
</html>
"""
    return xml.encode("utf-8")


def _write_archive(path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def test_verify_no_against_current_accepts_exact_match(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_XML)],
    )

    result = verify_no_against_current(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.consistent is True
    assert result.divergence_count == 0


def test_verify_no_against_current_reports_divergence(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_DIVERGENT_XML)],
    )

    result = verify_no_against_current(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.consistent is False
    assert result.divergence_count == 1
    assert result.divergence_counts == {"MISMATCH": 1}


def test_verify_no_against_current_accepts_farchive_source_path(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_XML)],
    )
    db_path = tmp_path / "norway.farchive"
    ingest_no_public_archives(tmp_path, db_path)

    result = verify_no_against_current(
        "no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=db_path,
    )

    assert result.error is None
    assert result.consistent is True
    assert result.divergence_count == 0


def test_build_no_verify_scan_checks_executable_replayable_subset(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_XML)],
    )

    report = build_no_verify_scan(
        as_of="2025-02-15",
        data_dir=tmp_path,
        limit=5,
    )

    assert report["candidate_count"] == 1
    assert report["scanned_count"] == 1
    assert report["summary"] == {"consistent": 1, "divergent": 0, "error": 0}
    assert report["source_signal_counts"] == {}
    assert report["results"][0]["base_id"] == "no/lov/2025-01-01-1"

    filtered = build_no_verify_scan(
        as_of="2025-02-15",
        data_dir=tmp_path,
        limit=5,
        base_ids=["no/lov/2099-01-01-1"],
    )
    assert filtered["candidate_count"] == 0
    assert filtered["scanned_count"] == 0
    assert filtered["results"] == []


def test_build_no_verify_scan_skips_empty_current_shell_candidates(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250101-002.xml", _BASE_XML.replace(b"2025-01-01-1", b"2025-01-01-2")),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
            ("lti/2025/nl-20250202-006.xml", _amendment_xml().replace(b"2025-01-01-1", b"2025-01-01-2")),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [
            ("nl/nl-20250101-001.xml", _CURRENT_XML),
            (
                "nl/nl-20250101-002.xml",
                b"<html><head><title>Lov om endringer i testloven</title></head><body><main></main></body></html>",
            ),
        ],
    )

    report = build_no_verify_scan(as_of="2025-02-15", data_dir=tmp_path, limit=5)

    assert report["candidate_count"] == 1
    assert [item["base_id"] for item in report["results"]] == ["no/lov/2025-01-01-1"]


def test_verify_no_against_current_flags_sparse_indexed_history_signal(tmp_path) -> None:
    base_xml = _BASE_XML.replace(b"2025-01-01-1", b"2010-01-01-1")
    amendment_xml = _amendment_xml().replace(b"2025-01-01-1", b"2010-01-01-1")
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2010/nl-20100101-001.xml", base_xml),
            ("lti/2025/nl-20250202-005.xml", amendment_xml),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20100101-001.xml", _large_divergent_current_xml())],
    )

    result = verify_no_against_current(
        "no/lov/2010-01-01-1",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.consistent is False
    assert result.indexed_amendment_count == 1
    assert result.applied_amendment_count == 1
    assert result.replay_op_count == 1
    assert result.divergence_count >= 50
    assert result.source_signal == "sparse_indexed_history"

    report = build_no_verify_scan(
        as_of="2025-02-15",
        data_dir=tmp_path,
        limit=5,
    )
    assert report["summary"] == {"consistent": 0, "divergent": 1, "error": 0}
    assert report["source_signal_counts"] == {"sparse_indexed_history": 1}
    assert report["results"][0]["source_signal"] == "sparse_indexed_history"


def test_infer_no_source_signal_flags_single_op_mid_sized_sparse_case() -> None:
    assert (
        _infer_no_source_signal(
            divergence_count=18,
            indexed_amendment_count=1,
            replay_op_count=1,
            base_year=2005,
        )
        == "sparse_indexed_history"
    )


def test_infer_no_source_signal_leaves_real_two_amendment_case_unclassified() -> None:
    assert (
        _infer_no_source_signal(
            divergence_count=14,
            indexed_amendment_count=2,
            replay_op_count=2,
            base_year=2004,
        )
        is None
    )


def test_no_paths_related_treats_last_item_anchor_as_touching_concrete_item() -> None:
    assert no_paths_related(
        (("section", "5"), ("subsection", "1"), ("item", "last")),
        (("section", "5"), ("subsection", "1"), ("item", "8")),
    )


def test_build_no_verify_partition_separates_untouched_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.norway.verify.build_no_verify_scan",
        lambda **_: {
            "data_dir": "data/norway.farchive",
            "as_of": "2026-03-29",
            "candidate_count": 2,
            "scanned_count": 2,
            "summary": {"consistent": 0, "divergent": 2, "error": 0},
            "source_signal_counts": {},
            "results": [
                {
                    "base_id": "no/lov/2024-01-12-1",
                    "current_title": "A",
                    "replay_status": "replayed",
                    "consistent": False,
                    "divergence_count": 3,
                    "divergence_counts": {"MISMATCH": 3},
                    "indexed_amendment_count": 3,
                    "applied_amendment_count": 3,
                    "replay_op_count": 10,
                    "source_signal": "",
                    "error": "",
                },
                {
                    "base_id": "no/lov/2020-12-18-156",
                    "current_title": "B",
                    "replay_status": "replayed",
                    "consistent": False,
                    "divergence_count": 2,
                    "divergence_counts": {"MISMATCH": 1, "OPS_MISSING": 1},
                    "indexed_amendment_count": 2,
                    "applied_amendment_count": 2,
                    "replay_op_count": 7,
                    "source_signal": "",
                    "error": "",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda base_id, **_: SimpleNamespace(base_id=base_id, divergences=[]),
    )
    monkeypatch.setattr(
        "lawvm.norway.verify.build_no_verify_coverage_summary",
        lambda *, verify_result, index, data_dir=None: (
            {
                "touched_path_count": 3,
                "touched_source_count": 2,
                "touched_op_count": 7,
                "touched_divergence_count": 3,
                "untouched_divergence_count": 0,
            }
            if verify_result.base_id == "no/lov/2024-01-12-1"
            else {
                "touched_path_count": 1,
                "touched_source_count": 2,
                "touched_op_count": 7,
                "touched_divergence_count": 0,
                "untouched_divergence_count": 2,
            }
        ),
    )
    monkeypatch.setattr(
        "lawvm.norway.verify._load_no_index",
        lambda **_: SimpleNamespace(),
    )

    report = build_no_verify_partition(as_of="2026-03-29", data_dir=None, limit=10)

    assert [item["base_id"] for item in report["partitions"]["replay_defect"]] == ["no/lov/2024-01-12-1"]
    assert [item["base_id"] for item in report["partitions"]["untouched_drift"]] == ["no/lov/2020-12-18-156"]


def test_normalize_no_comparison_text_removes_spacing_noise_only() -> None:
    assert normalize_no_comparison_text("§ 1-2 , kapittel 7 .") == "§ 1-2, kapittel 7."


def test_normalize_no_comparison_text_treats_repealed_shell_as_empty() -> None:
    assert normalize_no_comparison_text("§ 2-6. (Opphevet)") == ""
    assert normalize_no_comparison_text("(Opphevet)") == ""


def test_normalize_no_comparison_text_strips_other_laws_placeholder_dashes() -> None:
    assert (
        normalize_no_comparison_text(
            "I lov 13. mars 1981 nr. 6 om vern mot forurensning og om avfall gjøres følgende endringer: – – –"
        )
        == "I lov 13. mars 1981 nr. 6 om vern mot forurensning og om avfall gjøres følgende endringer:"
    )
    assert (
        normalize_no_comparison_text(
            "Fra det tidspunktet loven trer i kraft, gjøres følgende endringer i andre lover: – – –"
        )
        == "Fra det tidspunktet loven trer i kraft, gjøres følgende endringer i andre lover:"
    )
    assert (
        normalize_no_comparison_text(
            "Frå den tida lova tek til å gjelde, skal desse endringane gjerast i andre lover: – – –"
        )
        == "Frå den tida lova tek til å gjelde, skal desse endringane gjerast i andre lover:"
    )


def test_normalize_no_comparison_text_closes_numeric_hyphen_gap() -> None:
    assert normalize_no_comparison_text("CO 2 -ekvivalenter") == "CO 2-ekvivalenter"


def test_normalize_no_comparison_text_strips_space_after_open_paren() -> None:
    assert normalize_no_comparison_text("§ 7-2 ( sikkerhetslovens anvendelse)") == (
        "§ 7-2 (sikkerhetslovens anvendelse)"
    )


def test_normalize_no_comparison_text_strips_inline_footnote_marker() -> None:
    assert normalize_no_comparison_text("Loven gjelder fra den tid 1 Kongen bestemmer.") == (
        "Loven gjelder fra den tid Kongen bestemmer."
    )


def test_normalize_no_comparison_text_strips_trailing_footnote_marker() -> None:
    assert normalize_no_comparison_text("Loven trer i kraft fra den tid Kongen bestemmer. 1") == (
        "Loven trer i kraft fra den tid Kongen bestemmer."
    )


def test_irnode_to_no_comparison_text_ignores_direct_section_headings() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="2-1",
        children=(IRNode(kind=IRNodeKind.HEADING, text="Skatteplikt for øverste morselskap"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Operativ tekst."),),
    )

    assert irnode_to_no_comparison_text(section) == "Operativ tekst."
    assert irnode_to_text(section) == "Skatteplikt for øverste morselskap Operativ tekst."


def test_irnode_to_no_comparison_text_combines_subsection_text_with_nested_items() -> None:
    subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="5",
        text="forutsatt at",
        children=(IRNode(kind=IRNodeKind.ITEM, label="a", text="første vilkår"),
            IRNode(kind=IRNodeKind.ITEM, label="b", text="andre vilkår"),),
    )

    assert irnode_to_no_comparison_text(subsection) == "forutsatt at første vilkår andre vilkår"


def test_normalize_no_compare_tree_flattens_sentence_only_subsection() -> None:
    subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="2",
        children=(IRNode(kind=IRNodeKind.SENTENCE, label="1", text="Første punktum."),
            IRNode(kind=IRNodeKind.SENTENCE, label="2", text="Andre punktum."),),
    )

    normalized = _normalize_no_compare_tree(subsection)

    assert normalized.kind is IRNodeKind.SUBSECTION
    assert normalized.label == "2"
    assert normalized.text == "Første punktum. Andre punktum."
    assert normalized.children == ()


def test_normalize_no_compare_tree_flattens_sentence_prefix_but_keeps_items() -> None:
    subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="2",
        children=(IRNode(kind=IRNodeKind.SENTENCE, label="1", text="Lead sentence."),
            IRNode(kind=IRNodeKind.ITEM, label="a", text="første"),
            IRNode(kind=IRNodeKind.ITEM, label="b", text="andre"),),
    )

    normalized = _normalize_no_compare_tree(subsection)

    assert normalized.text == "Lead sentence."
    assert [(child.kind, child.label, child.text) for child in normalized.children] == [
        (IRNodeKind.ITEM, "a", "første"),
        (IRNodeKind.ITEM, "b", "andre"),
    ]


def test_normalize_no_compare_tree_trims_inline_nested_item_duplication() -> None:
    item = IRNode(
        kind=IRNodeKind.ITEM,
        label="b",
        text=(
            "eieren er en fysisk person som er skattemessig bosatt i samme jurisdiksjon som "
            "det øverste morselskapet, og har en direkte eierinteresse som gir rett til "
            "maksimalt 5 prosent av fortjenesten og eiendelene til det øverste morselskapet, eller"
        ),
        children=(IRNode(
                kind=IRNodeKind.ITEM,
                label="1",
                text="er skattemessig bosatt i samme jurisdiksjon som det øverste morselskapet, og",
            ),
            IRNode(
                kind=IRNodeKind.ITEM,
                label="2",
                text=(
                    "har en direkte eierinteresse som gir rett til maksimalt 5 prosent av "
                    "fortjenesten og eiendelene til det øverste morselskapet, eller"
                ),
            ),),
    )

    normalized = _normalize_no_compare_tree(item)

    assert normalized.text == "eieren er en fysisk person som"
    assert [(child.kind, child.label, child.text) for child in normalized.children] == [
        (
            IRNodeKind.ITEM,
            "1",
            "er skattemessig bosatt i samme jurisdiksjon som det øverste morselskapet, og",
        ),
        (
            IRNodeKind.ITEM,
            "2",
            "har en direkte eierinteresse som gir rett til maksimalt 5 prosent av fortjenesten og eiendelene til det øverste morselskapet, eller",
        ),
    ]


def test_normalize_no_compare_tree_preserves_current_definition_list_items() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(IRNode(kind=IRNodeKind.HEADING, text="Begreper"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="I denne lov forstås med:",
                children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="Betalingsmidler: Kontanter."),
                    IRNode(kind=IRNodeKind.ITEM, label="2", text="Valutaveksling: Kjøp og salg."),),
            ),),
    )

    normalized = _normalize_no_compare_tree(section)
    subsection = next(child for child in normalized.children if child.kind is IRNodeKind.SUBSECTION)
    assert subsection.text == "I denne lov forstås med:"
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.ITEM, "1", "Betalingsmidler: Kontanter."),
        (IRNodeKind.ITEM, "2", "Valutaveksling: Kjøp og salg."),
    ]


def test_normalize_no_compare_tree_rebuilds_split_definition_pairs_as_items() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(IRNode(kind=IRNodeKind.HEADING, text="Begreper"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="I denne lov forstås med:"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Betalingsmidler:"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kontanter."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Valutaveksling:"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="Kjøp og salg."),),
    )

    normalized = _normalize_no_compare_tree(section)
    subsection = next(child for child in normalized.children if child.kind is IRNodeKind.SUBSECTION)
    assert subsection.text == "I denne lov forstås med:"
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.ITEM, "1", "Betalingsmidler: Kontanter."),
        (IRNodeKind.ITEM, "2", "Valutaveksling: Kjøp og salg."),
    ]


def test_normalize_no_compare_tree_blanks_repealed_shell_text() -> None:
    section = IRNode(kind=IRNodeKind.SECTION, label="2-6", text="§ 2-6. (Opphevet)")

    normalized = _normalize_no_compare_tree(section)

    assert normalized.text == ""


def test_normalize_no_compare_tree_collapses_other_laws_detail_section() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="22",
        children=(IRNode(kind=IRNodeKind.HEADING, text="(endringer i andre lover)"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="I lov 13. mars 1981 nr. 6 om vern mot forurensning og om avfall gjøres følgende endringer: – – –",
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="§ 11 nytt annet ledd skal lyde:"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kvotepliktig etter klimakvoteloven § 4 ..."),),
    )

    normalized = _normalize_no_compare_tree(section)

    assert [(child.kind, child.label, child.text) for child in normalized.children] == [
        (IRNodeKind.HEADING, None, "(endringer i andre lover)"),
        (
            IRNodeKind.SUBSECTION,
            "1",
            "I lov 13. mars 1981 nr. 6 om vern mot forurensning og om avfall gjøres følgende endringer:",
        ),
    ]


def test_normalize_no_compare_tree_records_other_laws_projection() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="22",
        children=(IRNode(kind=IRNodeKind.HEADING, text="Endringer i andre lover"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Fra den tid loven trer i kraft, gjøres følgende endringer i andre lover: – – –",
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="1. I lov 17. juli 1998 nr. 61 ..."),),
    )
    projections = []

    _normalize_no_compare_tree(
        section,
        projections_out=projections,
        surface="current",
        path=(("section", "22"),),
    )

    assert [projection.rule_id for projection in projections] == [NO_VERIFY_COMPARE_OTHER_LAWS_CONTEXT_SUPPRESSED]
    projection = projections[0]
    assert projection.surface == "current"
    assert projection.address == (("section", "22"),)
    assert projection.before_kind == "section"
    assert projection.before_label == "22"
    assert projection.before_child_count == 3
    assert projection.after_child_count == 2
    assert projection.to_dict()["family"] == "editorial_projection"


def test_normalize_no_compare_tree_does_not_record_projection_for_plain_section() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Plain operative text."),),
    )
    projections = []

    _normalize_no_compare_tree(section, projections_out=projections, surface="current", path=(("section", "1"),))

    assert projections == []


def test_normalize_no_compare_tree_collapses_other_laws_detail_section_without_heading() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="11",
        children=(IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Med virkning fra den tid loven trer i kraft, gjøres følgende endringer i andre lover:",
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="§ 25 annet ledd tredje og fjerde punktum oppheves."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="2. I lov 24. mai 1929 nr. 4 ..."),),
    )

    normalized = _normalize_no_compare_tree(section)

    assert [(child.kind, child.label, child.text) for child in normalized.children] == [
        (
            IRNodeKind.SUBSECTION,
            "1",
            "Med virkning fra den tid loven trer i kraft, gjøres følgende endringer i andre lover:",
        ),
    ]


def test_normalize_no_compare_tree_collapses_nynorsk_other_laws_detail_section() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="22",
        children=(IRNode(kind=IRNodeKind.HEADING, text="Endringar i andre lover"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Frå den tida lova tek til å gjelde, skal desse endringane gjerast i andre lover: – – –",
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="1. I lov 17. juli 1998 nr. 61 ..."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="§ 2-5 overskrifta skal lyde:"),),
    )

    normalized = _normalize_no_compare_tree(section)

    assert [(child.kind, child.label, child.text) for child in normalized.children] == [
        (IRNodeKind.HEADING, None, "Endringar i andre lover"),
        (
            IRNodeKind.SUBSECTION,
            "1",
            "Frå den tida lova tek til å gjelde, skal desse endringane gjerast i andre lover:",
        ),
    ]


def test_normalize_no_compare_tree_collapses_heading_plus_self_section_shell() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="41",
        text=(
            "I § 41 Endringer i tvangsfullbyrdelsesloven skal "
            "tvangsfullbyrdelsesloven § 2-15 tredje ledd passusen "
            "«§§ 7-23, 7-24 og 7-27» erstattes av passusen «§§ 7-23 og 7-27»."
        ),
        children=(IRNode(kind=IRNodeKind.HEADING, text="Endringer i tvangsfullbyrdelsesloven"),),
    )

    normalized = _normalize_no_compare_tree(section)

    assert normalized.text == ""
    assert [(child.kind, child.label, child.text) for child in normalized.children] == [
        (IRNodeKind.HEADING, None, "Endringer i tvangsfullbyrdelsesloven"),
    ]


def test_normalize_no_compare_tree_blanks_contingent_other_laws_placeholder_section() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="42",
        children=(IRNode(kind=IRNodeKind.HEADING, text="Endringer i andre lover"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Fra den tid Kongen fastsetter, gjøres følgende endringer i andre lover:",
            ),),
    )

    normalized = _normalize_no_compare_tree(section)

    assert normalized.text == ""
    assert normalized.children == ()


def test_normalize_no_compare_tree_blanks_self_section_other_laws_lead_shell() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="42",
        text="I § 42 nr. 44 om endringer i skattebetalingsloven skal nye endringer lyde:",
    )

    normalized = _normalize_no_compare_tree(section)

    assert normalized.text == ""
    assert normalized.children == ()


def test_verify_consistency_accepts_no_text_normalizer() -> None:
    addr = LegalAddress(path=(("section", "1"), ("item", "a")))
    ops_tl = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    content=IRNode(kind=IRNodeKind.ITEM, label="a", text="§ 1-2, kapittel 7."),
                )
            ],
        )
    }
    oracle = IRStatute(
        statute_id="no/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.ITEM, label="a", text="§ 1-2 , kapittel 7 ."),),
                ),),
        ),
    )
    con_tl = ingest_consolidated(oracle, as_of="0000-00-00")

    raw_divs = verify_consistency(
        ops_tl,
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_text,
    )
    assert len(raw_divs) == 2

    norm_divs = verify_consistency(
        ops_tl,
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_text,
        text_normalizer=normalize_no_comparison_text,
        missing_equals_empty=True,
    )
    assert len(norm_divs) == 1
    assert norm_divs[0].address.path == (("section", "1"),)


def test_verify_consistency_accepts_missing_equals_empty_for_blank_norway_items() -> None:
    oracle = IRStatute(
        statute_id="no/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.ITEM, label="a", text=""),),
                ),),
        ),
    )
    con_tl = ingest_consolidated(oracle, as_of="0000-00-00")

    raw_divs = verify_consistency(
        {},
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_text,
        text_normalizer=normalize_no_comparison_text,
    )
    assert len(raw_divs) == 2

    norm_divs = verify_consistency(
        {},
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_text,
        text_normalizer=normalize_no_comparison_text,
        missing_equals_empty=True,
    )
    assert norm_divs == []


def test_primary_divergences_suppresses_chapter_only_relocation_pairs() -> None:
    ops = IRStatute(
        statute_id="no/test",
        title="Replay",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(
                            kind=IRNodeKind.SECTION,
                            label="6",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Samme tekst."),),
                        ),),
                ),),
        ),
    )
    oracle = IRStatute(
        statute_id="no/test",
        title="Current",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(
                            kind=IRNodeKind.SECTION,
                            label="6",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Samme tekst."),),
                        ),),
                ),),
        ),
    )

    raw_divs = verify_consistency(
        ingest_consolidated(ops, as_of="0000-00-00"),
        ingest_consolidated(oracle, as_of="0000-00-00"),
        as_of="0000-00-00",
        irnode_to_text=irnode_to_no_comparison_text,
        text_normalizer=normalize_no_comparison_text,
        missing_equals_empty=True,
    )

    from lawvm.norway.verify import _primary_divergences

    assert len(raw_divs) >= 4
    assert _primary_divergences(raw_divs) == []
    partition = _partition_primary_divergences(raw_divs)
    filtered_rule_ids = {row.rule_id for row in partition.filtered}
    assert "no_verify.prefix_descendant_suppressed" in filtered_rule_ids
    assert "no_verify.chapter_relocation_pair" in filtered_rule_ids


def test_primary_divergence_partition_records_prefix_suppression() -> None:
    parent = ConsistencyDivergence(
        address=LegalAddress(path=(("section", "1"),)),
        divergence_type="MISMATCH",
        ops_text="Parent replay",
        consolidated_text="Parent current",
    )
    child = ConsistencyDivergence(
        address=LegalAddress(path=(("section", "1"), ("subsection", "1"))),
        divergence_type="MISMATCH",
        ops_text="Child replay",
        consolidated_text="Child current",
    )

    partition = _partition_primary_divergences([parent, child])

    assert partition.primary == (child,)
    assert len(partition.filtered) == 1
    assert partition.filtered[0].divergence == parent
    assert partition.filtered[0].rule_id == "no_verify.prefix_descendant_suppressed"


def test_verify_no_against_current_ignores_section_heading_only_drift(tmp_path) -> None:
    base_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Heading drift test</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="LTI/lov/2025-01-01-2">
      <article class="legalArticle" data-name="§1" data-lovdata-URL="LTI/lov/2025-01-01-2/§1">
        <article class="legalP">Gammel tekst.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">2025-02-10</dd>
    <article class="document-change" data-document="lov/2025-01-01-2">
      <article class="change" data-change-part="lov/2025-01-01-2/§1">
        <article class="defaultP">§ 1 skal lyde:</article>
        <article class="futureLegalArticle" data-name="§1">
          <span class="futureLegalArticleHeader">
            <span class="legalArticleValue">§ 1</span>.
            <span class="legalArticleTitle">Kort tittel</span>
          </span>
          <article class="legalP">Oppdatert operativ tekst.</article>
        </article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    current_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Heading drift test</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="NL/lov/2025-01-01-2">
      <article class="legalArticle" data-name="§1" data-lovdata-URL="NL/lov/2025-01-01-2/§1">
        <article class="legalP">Oppdatert operativ tekst.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-002.xml", base_xml),
            ("lti/2025/nl-20250202-006.xml", amendment_xml),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-002.xml", current_xml)],
    )

    result = verify_no_against_current(
        "no/lov/2025-01-01-2",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.consistent is True
    assert result.divergence_count == 0


def test_verify_no_against_current_ignores_sentence_only_segmentation_drift(tmp_path) -> None:
    base_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Sentence drift test</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="LTI/lov/2025-01-01-3">
      <article class="legalArticle" data-name="§1" data-lovdata-URL="LTI/lov/2025-01-01-3/§1">
        <article class="numberedLegalP" data-numerator="1">(1) Første punktum. Andre gamle punktum.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">2025-02-10</dd>
    <article class="document-change" data-document="lov/2025-01-01-3">
      <article class="change"
               data-add-new-part="lov/2025-01-01-3/§1/ledd/1/setning/2"
               data-move-part="lov/2025-01-01-3/§1/ledd/1/setning/2;;lov/2025-01-01-3/§1/ledd/1/setning/3">
        <article class="defaultP">§ 1 første ledd nytt annet punktum.</article>
        <article class="legalP">Andre nye punktum.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    current_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Sentence drift test</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="NL/lov/2025-01-01-3">
      <article class="legalArticle" data-name="§1" data-lovdata-URL="NL/lov/2025-01-01-3/§1">
        <article class="numberedLegalP" data-numerator="1">(1) Første punktum. Andre nye punktum. Andre gamle punktum.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-003.xml", base_xml),
            ("lti/2025/nl-20250202-007.xml", amendment_xml),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-003.xml", current_xml)],
    )

    result = verify_no_against_current(
        "no/lov/2025-01-01-3",
        as_of="2025-02-15",
        data_dir=tmp_path,
    )

    assert result.error is None
    assert result.consistent is True
    assert result.divergence_count == 0
