from __future__ import annotations

import sqlite3
import json
from pathlib import Path

from lxml import etree
import pytest

from lawvm.semantic.contracts import SEMANTIC_CONTRACT_VERSION
from lawvm.tools.structural_review import (
    is_oracle_content_absent,
)
from scripts.build_publication_db import (
    _exclude_from_publication_by_oracle,
    _attach_section_structures,
    _is_amendment_only_instrument,
    _is_amendment_only_instrument_with_reason,
    _johtolause_section_char_span,
    _normalize_display_diff_text,
    _publication_taxonomy,
    _configure_publication_db,
    _row_is_meaningful,
    _SCHEMA,
    _select_best_bundles,
    _resolve_section_row_key,
    _require_section_structure_payload,
    _section_diff_row_is_real,
    _oracle_bool_flag,
    _parse_finlex_page_meta,
    _populate_source_absent,
    _reclassify_error_family,
    _statute_sort_key,
    _serialize_oracle_section_node,
    _structure_support_projection,
    finlex_lainsaadanto_url,
)


def test_oracle_bool_flag_reads_explicit_finlex_boolean() -> None:
    oracle = b'<meta><proprietary><isInForce value="false"/></proprietary></meta>'
    assert _oracle_bool_flag(oracle, "isInForce") is False
    assert _oracle_bool_flag(oracle, "isRepealed") is None


def test_exclude_from_publication_by_oracle_when_not_in_force() -> None:
    oracle = b'<meta><proprietary><isInForce value="false"/></proprietary></meta>'
    assert _exclude_from_publication_by_oracle(oracle) is True


def test_exclude_from_publication_by_oracle_keeps_ambiguous_live_cases() -> None:
    oracle = (
        b'<meta><proprietary><isInForce value="true"/></proprietary></meta>'
        b'<body><hcontainer name="contentAbsent"/></body>'
    )
    assert _exclude_from_publication_by_oracle(oracle) is False


def test_finlex_lainsaadanto_url_preserves_historic_suffixes() -> None:
    assert (
        finlex_lainsaadanto_url("1902/31-174")
        == "https://www.finlex.fi/fi/lainsaadanto/1902/31-174"
    )


def test_statute_sort_key_orders_by_year_and_numeric_number() -> None:
    assert _statute_sort_key("1987/2") < _statute_sort_key("1987/17")
    assert _statute_sort_key("1987/17") < _statute_sort_key("1988/1")


def test_configure_publication_db_sets_browser_friendly_pragmas(tmp_path: Path) -> None:
    con = sqlite3.connect(str(tmp_path / "db.sqlite"))
    _configure_publication_db(con)
    page_size = con.execute("PRAGMA page_size").fetchone()[0]
    journal_mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    assert page_size == 32768
    assert str(journal_mode).upper() == "DELETE"
    con.close()


def test_parse_finlex_page_meta_extracts_title_and_status() -> None:
    html = (
        '<div class="styles_titleContainer__maCvk">'
        '<h2 lang="fi" class="styles_description__0Zy03 highlightable styles_h2__rq8uY">'
        'Laki eräiden lisärangaistusten poistamisesta'
        '</h2><span class="styles_inForce__qBsQ9">Ajantasainen</span></div>'
    ).encode("utf-8")
    title, status = _parse_finlex_page_meta(html)
    assert title == "Laki eräiden lisärangaistusten poistamisesta"
    assert status == "Ajantasainen"


def test_populate_source_absent_skips_not_in_force_statutes(monkeypatch) -> None:
    class FakeCorpus:
        def oracle_path_index(self) -> dict[str, str]:
            return {
                "1999/1": "ignored",
                "2000/2": "ignored",
            }

        def read_oracle(self, sid: str) -> bytes:
            if sid == "1999/1":
                return b'<isInForce value="false"/><hcontainer name="contentAbsent"/>'
            return b'<isInForce value="true"/><hcontainer name="contentAbsent"/>'

    monkeypatch.setattr(
        "lawvm.finland.transparent_store.is_known_missing_source",
        lambda sid: True,
    )
    monkeypatch.setattr(
        "lawvm.finland.corpus.get_corpus",
        lambda: FakeCorpus(),
    )
    monkeypatch.setattr(
        "scripts.build_publication_db._bulk_fetch_html_page_meta",
        lambda sids, html_cache_path: {sid: (f"Title {sid}", "Ajantasainen") for sid in sids},
    )

    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE source_absent (
            statute_id TEXT PRIMARY KEY,
            year INTEGER,
            consolidated_url TEXT,
            page_title TEXT,
            page_status_label TEXT,
            content_absent INTEGER DEFAULT 1,
            repealed INTEGER DEFAULT 0
        );
        """
    )

    total_oracle, total_source_absent = _populate_source_absent(
        con,
        html_cache_path=Path(".tmp/test-cache.farchive"),
    )

    assert total_oracle == 1
    assert total_source_absent == 1
    rows = con.execute(
        "SELECT statute_id, page_title, page_status_label FROM source_absent"
    ).fetchall()
    assert rows == [("2000/2", "Title 2000/2", "Ajantasainen")]


def test_publication_taxonomy_maps_temporal_mismatch_to_temporal_category() -> None:
    taxonomy = _publication_taxonomy(
        {
            "error_family": "oracle_temporal_impossibility",
            "error_complexity": "mixed",
            "ready_for_clean_v1": 1,
        }
    )

    assert taxonomy["review_category"] == "temporal_mismatch"
    assert taxonomy["severity"] == "temporal"
    assert taxonomy["fixability"] == "ingestion_fixable"
    assert taxonomy["lawvm_status"] == "likely_finlex_issue"
    assert taxonomy["evidence_quality"] == "ready"


def test_publication_taxonomy_maps_legacy_structural_extra_to_structural_category() -> None:
    taxonomy = _publication_taxonomy(
        {
            "error_family": "replay_structural_diff",
            "error_complexity": "extra",
            "ready_for_clean_v1": 1,
        }
    )

    assert taxonomy["review_category"] == "structural_extra"
    assert taxonomy["severity"] == "structural"
    assert taxonomy["fixability"] == "lawvm_fixable"
    assert taxonomy["lawvm_status"] == "likely_lawvm_bug"


def test_select_best_bundles_accepts_legacy_section_result_bundles(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    legacy = cache_dir / "2019_1__a.json"
    newer = cache_dir / "2019_1__b.json"
    legacy.write_text(
        json.dumps(
            {
                "statute_id": "2019/1",
                "section_results": [{"section": "section:1", "diagnosis": "EXTRA"}],
            }
        ),
        encoding="utf-8",
    )
    newer.write_text(
        json.dumps({"statute_id": "2019/1"}),
        encoding="utf-8",
    )

    selected = _select_best_bundles(cache_dir, {"2019/1"})
    assert selected["2019_1"].name == "2019_1__a.json"


def test_section_diff_row_is_real_filters_editorial_only_rows() -> None:
    assert _section_diff_row_is_real({"structure_diff_kind": "extra"}) is True
    assert _section_diff_row_is_real({"structure_diff_kind": "editorial_only"}) is False
    assert _section_diff_row_is_real({"structure_diff_kind": "identical"}) is False


def test_require_section_structure_payload_rejects_flat_section_rows() -> None:
    with pytest.raises(RuntimeError, match="Section row without structured payload"):
        _require_section_structure_payload(
            "1995/1679",
            {
                "section": "chapter:1/section:1",
                "error_family": "replay_structural_diff",
                "oracle_structure": None,
                "replay_structure": None,
            },
        )


def test_attach_section_structures_recomputes_empty_cache(monkeypatch, tmp_path: Path) -> None:
    statute_errors = {
        "1995/1679": [
            {
                "section": "section:1",
                "error_family": "replay_structural_diff",
            }
        ]
    }
    statute_modes = {"1995/1679": "legal_pit"}
    section_cache_dir = tmp_path / "section-cache"
    section_cache_dir.mkdir()

    monkeypatch.setattr(
        "scripts.build_publication_db._try_read_section_cache",
        lambda statute_id, mode, section_cache_dir: {},
    )
    monkeypatch.setattr(
        "scripts.build_publication_db._compute_section_map_worker",
        lambda args: (
            args[0],
            {
                "chapter:1/section:1": {
                    "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
                    "oracle": {"kind": "section", "label": "1"},
                    "replay": {"kind": "section", "label": "1"},
                    "aligned": {"kind": "section", "label": "1"},
                    "semantic_diff": {
                        "kind": "structure_only",
                        "summary": "Rakenne eroaa.",
                        "structural": 1,
                        "label": 0,
                        "text": 0,
                        "events": [],
                    },
                }
            },
        ),
    )

    _attach_section_structures(
        statute_errors,
        statute_modes,
        section_cache_dir=section_cache_dir,
        workers=1,
    )

    row = statute_errors["1995/1679"][0]
    assert row["section"] == "chapter:1/section:1"
    assert row["section_display"] == "1 luku 1 §"
    assert row["section_sort_key"] == "05:0000000001:/06:0000000001:"
    assert row["structure_diff_kind"] == "structure_only"
    assert row["structure_diff_summary"] == "Rakenne eroaa."


def test_resolve_section_row_key_prefers_matching_text_when_ambiguous() -> None:
    section_map = {
        "chapter:6/section:2a": {
            "aligned": {
                "right": {
                    "text": "Tuomioistuimen on valvottava, että asia käsitellään jäsennellysti.",
                }
            },
            "oracle": {
                "text": "Tuomioistuimen on valvottava, että asia käsitellään jäsennellysti.",
            },
        },
        "chapter:26/section:2a": {
            "aligned": {
                "right": {
                    "text": "2 a § on kumottu L:lla 24.6.2010/650.",
                }
            },
            "oracle": {
                "text": "2 a § on kumottu L:lla 24.6.2010/650.",
            },
        },
    }
    row = {
        "section": "section:2a",
        "replay_text": "2 a § Tuomioistuimen on valvottava, että asia käsitellään jäsennellysti.",
        "oracle_text": "",
        "suspect_detail": "",
    }

    assert _resolve_section_row_key(section_map, row) == "chapter:6/section:2a"


def test_serialize_oracle_section_node_preserves_numbered_children() -> None:
    node = etree.fromstring(
        """
        <section xmlns="urn:akn">
          <num>13 §</num>
          <subsection>
            <num>3 mom.</num>
            <content>3 momentti on kumottu L:lla 30.12.2025/1492.</content>
            <paragraph>
              <num>a kohta</num>
              <content>Kohta A.</content>
            </paragraph>
          </subsection>
        </section>
        """
    )

    got = _serialize_oracle_section_node(node)
    assert got == {
        "kind": "section",
        "label": "13",
        "children": [
            {
                "kind": "subsection",
                "label": "3",
                "text": "3 momentti on kumottu L:lla 30.12.2025/1492.",
                "facets": {
                    "wording": {"text": "3 momentti on kumottu L:lla 30.12.2025/1492."},
                },
                "children": [
                    {
                        "kind": "item",
                        "label": "a",
                        "text": "Kohta A.",
                        "facets": {
                            "wording": {"text": "Kohta A."},
                        },
                    },
                ],
            },
        ],
    }


def test_serialize_oracle_section_node_avoids_content_p_duplication() -> None:
    node = etree.fromstring(
        """
        <section xmlns="urn:akn">
          <num>10 §</num>
          <subsection>
            <num>1 mom.</num>
            <content>
              <p>Ensimmäinen virke.</p>
            </content>
          </subsection>
        </section>
        """
    )

    got = _serialize_oracle_section_node(node)

    assert got == {
        "kind": "section",
        "label": "10",
        "children": [
            {
                "kind": "subsection",
                "label": "1",
                "text": "Ensimmäinen virke.",
                "facets": {
                    "wording": {"text": "Ensimmäinen virke."},
                },
            }
        ],
    }


def test_serialize_oracle_section_node_falls_back_to_ordinal_subsection_labels() -> None:
    node = etree.fromstring(
        """
        <section xmlns="urn:akn">
          <num>10 §</num>
          <subsection><content><p>Ensimmäinen momentti.</p></content></subsection>
          <subsection><content><p>Toinen momentti.</p></content></subsection>
        </section>
        """
    )

    got = _serialize_oracle_section_node(node)

    assert got == {
        "kind": "section",
        "label": "10",
        "children": [
            {
                "kind": "subsection",
                "label": "1",
                "label_basis": "ordinal_fallback",
                "text": "Ensimmäinen momentti.",
                "facets": {
                    "wording": {"text": "Ensimmäinen momentti."},
                },
            },
            {
                "kind": "subsection",
                "label": "2",
                "label_basis": "ordinal_fallback",
                "text": "Toinen momentti.",
                "facets": {
                    "wording": {"text": "Toinen momentti."},
                },
            },
        ],
    }


def test_normalize_display_diff_text_strips_editorial_repeal_boilerplate() -> None:
    raw = (
        "Edellä 1 momentissa tarkoitettu asia ratkaistaan lunastustoimituksessa. "
        "3 momentti on kumottu L:lla 5.2.1999/142."
    )

    assert _normalize_display_diff_text(raw) == (
        "Edellä 1 momentissa tarkoitettu asia ratkaistaan lunastustoimituksessa."
    )


def test_structure_support_projection_preserves_owned_semantic_diff_fields() -> None:
    projection = _structure_support_projection(
        {
            "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
            "oracle": {"kind": "section", "label": "10"},
            "replay": {"kind": "section", "label": "10"},
            "aligned": {
                "kind": "section",
                "label": "10",
                "match_basis": "exact_label",
                "left": {"kind": "section", "label": "10"},
                "right": {"kind": "section", "label": "10"},
            },
            "semantic_diff": {
                "kind": "label_and_text",
                "summary": "Sama rakenne, eri tunnus ja sanamuoto.",
                "structural": 0,
                "label": 1,
                "text": 1,
                "events": [
                    {
                        "kind": "wording_text_changed",
                        "semantic_path": ["section:10", "subsection:1"],
                        "semantic_path_parts": [
                            {"kind": "section", "label": "10"},
                            {"kind": "subsection", "label": "1"},
                        ],
                        "match_basis": "ordinal_fallback",
                        "unit_kind": "subsection",
                        "unit_label": "1",
                        "facet_kind": "wording",
                        "left_text": "A",
                        "right_text": "B",
                        "left_badge": "1 mom.",
                        "right_badge": "1 mom.",
                    }
                ],
            },
        }
    )

    assert projection["semantic_contract_version"] == SEMANTIC_CONTRACT_VERSION
    assert projection["oracle_structure"] == '{"kind": "section", "label": "10", "_normalized": true}'
    assert projection["replay_structure"] == '{"kind": "section", "label": "10", "_normalized": true}'
    assert projection["aligned_structure"] == (
        '{"kind": "section", "label": "10", "match_basis": "exact_label", '
        '"left": {"kind": "section", "label": "10"}, '
        '"right": {"kind": "section", "label": "10"}}'
    )
    assert projection["structure_diff_kind"] == "label_and_text"
    assert projection["structure_diff_summary"] == "Sama rakenne, eri tunnus ja sanamuoto."
    assert projection["structure_diff_structural"] == 0
    assert projection["structure_diff_label"] == 1
    assert projection["structure_diff_text"] == 1
    assert projection["structure_diff_events"] == (
        '[{"kind": "wording_text_changed", "semantic_path": ["section:10", "subsection:1"], '
        '"semantic_path_parts": [{"kind": "section", "label": "10"}, {"kind": "subsection", "label": "1"}], '
        '"match_basis": "ordinal_fallback", "unit_kind": "subsection", "unit_label": "1", '
        '"facet_kind": "wording", '
        '"left_text": "A", "right_text": "B", "left_badge": "1 mom.", "right_badge": "1 mom."}]'
    )


def test_is_oracle_content_absent_returns_true_for_marked_oracle() -> None:
    root = etree.fromstring(
        '<akomaNtoso xmlns="urn:akn">'
        '<act><body>'
        '<hcontainer name="contentAbsent"/>'
        '</body></act></akomaNtoso>'
    )
    assert is_oracle_content_absent(root) is True


def test_is_oracle_content_absent_returns_false_for_normal_oracle() -> None:
    root = etree.fromstring(
        '<akomaNtoso xmlns="urn:akn">'
        '<act><body>'
        '<section><num>1 §</num><subsection><content><p>Text.</p></content></subsection></section>'
        '</body></act></akomaNtoso>'
    )
    assert is_oracle_content_absent(root) is False


def test_is_oracle_content_absent_returns_false_for_none() -> None:
    assert is_oracle_content_absent(None) is False




def test_compute_live_returns_sections_without_db_reads(
    monkeypatch,
) -> None:
    """_compute_live computes sections from replay+oracle without reading the publication DB."""
    from lxml import etree as _etree

    from lawvm.tools.structural_review import _compute_live

    # Build a minimal oracle XML with one section (num "1 §", one subsection with text).
    oracle_xml = _etree.fromstring(
        """
        <akomaNtoso xmlns="urn:akn">
          <act>
            <body>
              <section eId="sec-1">
                <num>1 §</num>
                <subsection eId="sec-1__subsec-1">
                  <num>1 mom.</num>
                  <content><p>Oracle text here.</p></content>
                </subsection>
              </section>
            </body>
          </act>
        </akomaNtoso>
        """
    )

    # Mock corpus (not used beyond passing to replay_xml / get_ground_truth_tree).
    class _FakeCorpus:
        pass

    monkeypatch.setattr(
        "lawvm.finland.corpus.get_corpus",
        lambda: _FakeCorpus(),
    )
    # No replay available → replay_sections will be empty.
    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda *a, **kw: None,
    )
    # Oracle returns our minimal XML.
    monkeypatch.setattr(
        "lawvm.finland.corpus.get_ground_truth_tree",
        lambda sid, corpus=None, selector=None: oracle_xml,
    )

    result = _compute_live("2022/1")

    assert result is not None
    sections = result.get("sections", {})
    # "section:1" should be present (oracle-only → replay is missing → events).
    assert "section:1" in sections, f"expected section:1 in {list(sections)}"
    sd = sections["section:1"].get("semantic_diff", {})
    events = sd.get("events", [])
    assert events, "expected at least one diff event for oracle-only section"

    # oracle_diagnosis is not populated by _compute_live (no DB reads in live path).
    from lawvm.semantic.contracts import _ORACLE_ANNOTATABLE_KINDS

    annotatable = [e for e in events if e.get("kind") in _ORACLE_ANNOTATABLE_KINDS]
    assert annotatable, (
        f"expected at least one oracle-annotatable event, got kinds: "
        f"{[e.get('kind') for e in events]}"
    )
    for ev in annotatable:
        assert ev.get("oracle_diagnosis", "") == "", (
            f"event {ev.get('kind')} has oracle_diagnosis={ev.get('oracle_diagnosis')!r}, "
            "expected empty string (no DB reads in live path)"
        )


# ---------------------------------------------------------------------------
# _johtolause_section_char_span tests
# ---------------------------------------------------------------------------

class TestJohtolauseSectionCharSpan:

    def test_simple_section_span(self):
        """Basic: 'muutetaan 3 §' with section:3 returns span of '3 §'."""
        import re
        text = "muutetaan 3 § seuraavasti:"
        normed = re.sub(r"\s+", " ", text).strip()
        span = _johtolause_section_char_span(text, "section:3")
        assert span is not None, "Expected a span"
        covered = normed[span[0]:span[1]]
        assert "3" in covered
        assert "§" in covered

    def test_section_with_chapter_path(self):
        """Section path 'chapter:2/section:5' extracts number 5."""
        import re
        text = "muutetaan 5 §"
        normed = re.sub(r"\s+", " ", text).strip()
        span = _johtolause_section_char_span(text, "chapter:2/section:5")
        assert span is not None
        covered = normed[span[0]:span[1]]
        assert "5" in covered

    def test_section_number_not_in_text_returns_none(self):
        """Returns None when section number is not present in the johtolause."""
        span = _johtolause_section_char_span("muutetaan 3 §", "section:99")
        assert span is None

    def test_empty_johtolause_returns_none(self):
        assert _johtolause_section_char_span("", "section:3") is None

    def test_empty_section_path_returns_none(self):
        assert _johtolause_section_char_span("muutetaan 3 §", "") is None

    def test_no_section_part_in_path_returns_none(self):
        """A path with only 'chapter:' and no 'section:' returns None."""
        span = _johtolause_section_char_span("muutetaan 3 §", "chapter:3")
        assert span is None

    def test_section_with_letter_suffix(self):
        """Section '12a' matches token '12' + LETTER 'a'."""
        import re
        text = "muutetaan 12 a §"
        normed = re.sub(r"\s+", " ", text).strip()
        span = _johtolause_section_char_span(text, "section:12a")
        assert span is not None
        covered = normed[span[0]:span[1]]
        assert "12" in covered

    def test_span_positions_are_within_normalized_text(self):
        """Span bounds must be within the normalized text length."""
        import re
        text = "muutetaan 7 § seuraavasti:"
        normed = re.sub(r"\s+", " ", text).strip()
        span = _johtolause_section_char_span(text, "section:7")
        assert span is not None
        assert 0 <= span[0] < span[1] <= len(normed)


# ---------------------------------------------------------------------------
# _reclassify_error_family tests
# ---------------------------------------------------------------------------

import json as _json


class TestReclassifyErrorFamily:

    def test_leaves_already_classified_families_unchanged(self):
        """Families like cross_chapter_oracle_section_drift should not be reclassified."""
        row = {
            "error_family": "cross_chapter_oracle_section_drift",
            "section": "section:1",
            "structure_diff_structural": 5,
        }
        assert _reclassify_error_family(row) == "cross_chapter_oracle_section_drift"

    def test_pending_amendment_from_events(self):
        """Rows with oracle_pending_amendment_suspect event -> oracle_pending_amendment."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:1",
            "structure_diff_events": _json.dumps([
                {"kind": "oracle_pending_amendment_suspect"},
            ]),
        }
        assert _reclassify_error_family(row) == "oracle_pending_amendment"

    def test_editorial_convention_when_all_events_editorial(self):
        """All editorial events -> editorial_convention."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:1",
            "structure_diff_events": _json.dumps([
                {"kind": "editorial_repeal_notice"},
                {"kind": "empty_oracle_shell"},
            ]),
        }
        assert _reclassify_error_family(row) == "institutional_editorial_convention"

    def test_not_editorial_when_mixed_events(self):
        """Mix of editorial + non-editorial should NOT be classified as editorial."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:1",
            "structure_diff_events": _json.dumps([
                {"kind": "editorial_repeal_notice"},
                {"kind": "wording_text_changed"},
            ]),
            "structure_diff_structural": 0,
            "structure_diff_text": 1,
        }
        assert _reclassify_error_family(row) == "replay_wording_diff"

    def test_structural_diff_classification(self):
        """Rows with structural > 0 -> replay_structural_diff."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:1",
            "structure_diff_events": _json.dumps([
                {"kind": "subsection_missing_left"},
            ]),
            "structure_diff_structural": 2,
            "structure_diff_text": 1,
        }
        assert _reclassify_error_family(row) == "replay_structural_diff"

    def test_wording_diff_classification(self):
        """Rows with text > 0 but structural == 0 -> replay_wording_diff."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:1",
            "structure_diff_events": _json.dumps([
                {"kind": "wording_text_changed"},
            ]),
            "structure_diff_structural": 0,
            "structure_diff_text": 3,
        }
        assert _reclassify_error_family(row) == "replay_wording_diff"

    def test_fallback_to_oracle_section_stale(self):
        """Rows with no structure data fall back to oracle_section_stale."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:1",
        }
        assert _reclassify_error_family(row) == "oracle_section_stale"

    def test_pending_amendment_takes_priority_over_structural(self):
        """Pending amendment signal has higher priority than structural diff."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:1",
            "structure_diff_events": _json.dumps([
                {"kind": "oracle_pending_amendment_suspect"},
                {"kind": "wording_text_changed"},
            ]),
            "structure_diff_structural": 1,
            "structure_diff_text": 2,
        }
        assert _reclassify_error_family(row) == "oracle_pending_amendment"


# ---------------------------------------------------------------------------
# _is_amendment_only_instrument tests
# ---------------------------------------------------------------------------

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _akn_oracle(body_inner: str) -> bytes:
    """Build a minimal AKN oracle XML bytes with the given body content."""
    return (
        f'<akomaNtoso xmlns="{_AKN}">'
        f"<act><body>{body_inner}</body></act>"
        f"</akomaNtoso>"
    ).encode("utf-8")


class TestIsAmendmentOnlyInstrument:

    def test_returns_true_for_none_bytes(self) -> None:
        assert _is_amendment_only_instrument(None) is True

    def test_returns_true_for_empty_bytes(self) -> None:
        assert _is_amendment_only_instrument(b"") is True

    def test_returns_true_for_explicit_content_absent_marker(self) -> None:
        oracle = _akn_oracle('<hcontainer name="contentAbsent"/>')
        assert _is_amendment_only_instrument(oracle) is True

    def test_reason_for_content_absent_marker(self) -> None:
        oracle = _akn_oracle('<hcontainer name="contentAbsent"/>')
        is_amendment, reason = _is_amendment_only_instrument_with_reason(oracle)
        assert is_amendment is True
        assert reason == "content_absent_marker"

    def test_returns_true_for_body_with_no_sections(self) -> None:
        """Body has only an introductory hcontainer, no section elements."""
        oracle = _akn_oracle("<hcontainer><p>Preamble text.</p></hcontainer>")
        assert _is_amendment_only_instrument(oracle) is True

    def test_reason_for_no_sections_no_chapters(self) -> None:
        oracle = _akn_oracle("<hcontainer><p>Preamble text.</p></hcontainer>")
        is_amendment, reason = _is_amendment_only_instrument_with_reason(oracle)
        assert is_amendment is True
        assert reason == "no_sections_no_chapters"

    def test_returns_false_for_statute_with_substantive_section(self) -> None:
        """A statute with real section content must NOT be classified as amendment-only."""
        oracle = _akn_oracle(
            f"<section xmlns='{_AKN}'>"
            "<num>1 \xa7</num>"
            "<subsection><content><p>Laki koskee kaikkia Suomen kansalaisia.</p></content></subsection>"
            "</section>"
        )
        assert _is_amendment_only_instrument(oracle) is False

    def test_reason_is_none_for_substantive_statute(self) -> None:
        oracle = _akn_oracle(
            f"<section xmlns='{_AKN}'>"
            "<num>1 \xa7</num>"
            "<subsection><content><p>Substantiiviset teksti tässä.</p></content></subsection>"
            "</section>"
        )
        _, reason = _is_amendment_only_instrument_with_reason(oracle)
        assert reason is None

    def test_returns_true_when_all_sections_are_amendment_johtolause(self) -> None:
        """All sections contain only amendment johtolause prose → classified amendment-only."""
        oracle = _akn_oracle(
            f"<section xmlns='{_AKN}'>"
            "<num>1 \xa7</num>"
            "<subsection><content><p>muutetaan 3 § seuraavasti:</p></content></subsection>"
            "</section>"
        )
        assert _is_amendment_only_instrument(oracle) is True

    def test_reason_for_johtolause_only_sections(self) -> None:
        oracle = _akn_oracle(
            f"<section xmlns='{_AKN}'>"
            "<num>1 \xa7</num>"
            "<subsection><content><p>muutetaan 3 § seuraavasti:</p></content></subsection>"
            "</section>"
        )
        is_amendment, reason = _is_amendment_only_instrument_with_reason(oracle)
        assert is_amendment is True
        assert reason == "all_sections_are_johtolause"

    def test_returns_true_for_kumotaan_without_seuraavasti(self) -> None:
        """Repeal-only johtolause ('kumotaan N §') lacks 'seuraavasti' but is still amendment-only."""
        oracle = _akn_oracle(
            f"<section xmlns='{_AKN}'>"
            "<num>1 \xa7</num>"
            "<subsection><content><p>kumotaan 5 §</p></content></subsection>"
            "</section>"
        )
        assert _is_amendment_only_instrument(oracle) is True

    def test_mixed_sections_not_classified_amendment_only(self) -> None:
        """Conservative: one substantive section among johtolause sections → NOT excluded."""
        oracle = _akn_oracle(
            f"<section xmlns='{_AKN}'>"
            "<num>1 \xa7</num>"
            "<subsection><content><p>muutetaan 3 § seuraavasti:</p></content></subsection>"
            "</section>"
            f"<section xmlns='{_AKN}'>"
            "<num>2 \xa7</num>"
            "<subsection><content><p>Tämä laki on substantiivinen.</p></content></subsection>"
            "</section>"
        )
        assert _is_amendment_only_instrument(oracle) is False

    def test_returns_true_for_lisataan_johtolause(self) -> None:
        """'lisätään lakiin uusi §' is an amendment johtolause."""
        oracle = _akn_oracle(
            f"<section xmlns='{_AKN}'>"
            "<num>1 \xa7</num>"
            "<subsection><content><p>lisätään lakiin uusi 5 § seuraavasti:</p></content></subsection>"
            "</section>"
        )
        assert _is_amendment_only_instrument(oracle) is True

    def test_unparseable_xml_falls_back_to_byte_check(self) -> None:
        """Corrupt XML without contentAbsent byte → NOT classified as amendment-only."""
        assert _is_amendment_only_instrument(b"<not valid xml &") is False

    def test_unparseable_xml_with_content_absent_bytes(self) -> None:
        """Corrupt XML that contains 'contentAbsent' bytes → classified via byte check."""
        assert _is_amendment_only_instrument(b'<not valid xml contentAbsent="yes"') is True


# ---------------------------------------------------------------------------
# _row_is_meaningful tests (Bug 2 safety-net helper)
# ---------------------------------------------------------------------------


class TestRowIsMeaningful:

    def test_structured_section_with_blame_is_meaningful(self) -> None:
        row = {
            "error_family": "oracle_section_stale",
            "section": "chapter:1/section:3",
            "blame_source": "2020/100",
            "johtolause_text": "",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is True

    def test_structured_section_with_johtolause_is_meaningful(self) -> None:
        row = {
            "error_family": "replay_wording_diff",
            "section": "section:5",
            "blame_source": "",
            "johtolause_text": "muutetaan 5 § seuraavasti:",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is True

    def test_structured_section_with_diff_text_is_meaningful(self) -> None:
        row = {
            "error_family": "replay_structural_diff",
            "section": "section:7",
            "blame_source": "",
            "johtolause_text": "",
            "oracle_text": "oracle text here",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is True

    def test_empty_section_in_section_card_family_is_not_meaningful(self) -> None:
        """A section-card-family row with empty section always renders as '?' card."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "",
            "blame_source": "2020/100",
            "johtolause_text": "some text",
            "oracle_text": "oracle text",
            "replay_text": "replay text",
        }
        assert _row_is_meaningful(row) is False

    def test_question_mark_section_is_not_meaningful(self) -> None:
        """section='?' has no ':' so it fails the section-path check."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "?",
            "blame_source": "2020/100",
            "johtolause_text": "text",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is False

    def test_structured_section_with_no_substance_is_not_meaningful(self) -> None:
        """section:N present but all substance fields empty → meaningless card."""
        row = {
            "error_family": "oracle_section_stale",
            "section": "section:3",
            "blame_source": "",
            "johtolause_text": "",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is False

    def test_institutional_editorial_convention_needs_section_and_substance(self) -> None:
        row = {
            "error_family": "institutional_editorial_convention",
            "section": "",
            "blame_source": "",
            "johtolause_text": "",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is False

    def test_corrigendum_row_always_meaningful(self) -> None:
        """corrigendum_applied has a dedicated render branch; always kept."""
        row = {
            "error_family": "corrigendum_applied",
            "section": "",
            "section_display": "Sivulla 1, johtolauseessa",
            "blame_source": "2019/50",
            "johtolause_text": "",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is True

    def test_cutoff_drift_always_meaningful(self) -> None:
        """oracle_cutoff_version_drift has a dedicated render branch; always kept."""
        row = {
            "error_family": "oracle_cutoff_version_drift",
            "section": "",
            "blame_source": "",
            "johtolause_text": "",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is True

    def test_topology_drift_always_meaningful(self) -> None:
        """xml_html_topology_drift has a dedicated render branch; always kept."""
        row = {
            "error_family": "xml_html_topology_drift",
            "section": "",
            "blame_source": "",
            "johtolause_text": "",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is True

    def test_cross_chapter_always_meaningful(self) -> None:
        """cross_chapter_oracle_section_drift has its own branch; always kept."""
        row = {
            "error_family": "cross_chapter_oracle_section_drift",
            "section": "chapter:3/section:5",
            "blame_source": "2019/100",
            "johtolause_text": "",
            "oracle_text": "",
            "replay_text": "",
        }
        assert _row_is_meaningful(row) is True


# ---------------------------------------------------------------------------
# Bug 1 integration: error_count matches rows actually inserted (not pre-filter)
# ---------------------------------------------------------------------------


def _make_minimal_schema_con() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the publication schema."""
    con = sqlite3.connect(":memory:")
    con.executescript(_SCHEMA)
    return con


class TestErrorCountPostFilter:

    def test_error_count_equals_inserted_row_count_after_filtering(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Bug 1: statutes.error_count must reflect what is actually in errors,
        not the pre-filter len(rows).

        We mock a bundle where two of the three rows would be skipped:
        - one because _require_section_structure_payload raises (no structure)
        - one because _row_is_meaningful rejects it (empty section + no substance)
        Only the third row is a clean section row with blame, johtolause, and structure.
        """
        import json as _json
        from scripts import build_publication_db as bpd

        # Build a minimal bundle cache with one statute that has three rows.
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        bundle = {
            "statute_id": "2020/1",
            "title": "Testilaki",
            "mode": "legal_pit",
            "artifact_summary": {"by_family": {}, "ready_total_artifact_count": 3},
            "verification_links": {"consolidated_url": "https://finlex.fi/fi/test"},
            # Three section rows:
            # row A — clean: has blame + johtolause
            # row B — empty section: no blame, no text → _row_is_meaningful rejects
            # row C — no structure: _require_section_structure_payload raises
            "section_results": [
                {
                    "section": "section:1",
                    "diagnosis": "ORACLE_STALE",
                    "blame_source": "2019/99",
                    "blame_title": "Muutoslaki",
                    "blame_source_johtolause": "muutetaan 1 § seuraavasti:",
                    "oracle_text": "vanha teksti",
                    "replay_text": "uusi teksti",
                },
                {
                    "section": "",
                    "diagnosis": "ORACLE_STALE",
                    "blame_source": "",
                    "blame_source_johtolause": "",
                    "oracle_text": "",
                    "replay_text": "",
                },
                {
                    "section": "section:2",
                    "diagnosis": "ORACLE_STALE",
                    "blame_source": "2019/98",
                    "blame_title": "Toinen muutoslaki",
                    "blame_source_johtolause": "muutetaan 2 § seuraavasti:",
                    "oracle_text": "vanha",
                    "replay_text": "uusi",
                },
            ],
            "proof_claims": [],
            "supporting_amendments": [],
        }
        bundle_path = cache_dir / "2020_1__test.json"
        bundle_path.write_text(_json.dumps(bundle), encoding="utf-8")

        # Monkeypatch the expensive parts of build() that we don't need here.
        monkeypatch.setattr(bpd, "_enumerate_finnish_oracle_statute_ids", lambda: {"2020/1"})
        monkeypatch.setattr(bpd, "_is_finnish_statute_id", lambda sid: True)
        monkeypatch.setattr(bpd, "_exclude_from_publication_by_oracle", lambda ob: False)
        monkeypatch.setattr(bpd, "_extract_cross_chapter_errors", lambda *a, **kw: {})
        monkeypatch.setattr(bpd, "_extract_corrigendum_errors", lambda *a, **kw: {})
        monkeypatch.setattr(bpd, "_attach_section_structures", lambda *a, **kw: None)
        monkeypatch.setattr(bpd, "_populate_source_absent", lambda *a, **kw: (0, 0))
        monkeypatch.setattr(bpd, "_parse_verified_finlex_divergences_yaml", lambda *a: {})

        # Provide a fake corpus for the exclusion pre-pass.
        class _FakeCorpus:
            def oracle_path_index(self):
                return {"2020/1": "ignored"}
            def read_oracle(self, sid):
                return b""

        monkeypatch.setattr(
            "lawvm.finland.corpus.get_corpus",
            lambda: _FakeCorpus(),
        )

        # row C (section:2) lacks oracle_structure/replay_structure — it will be
        # rejected by _require_section_structure_payload.  Patch it to raise for
        # section:2 only.
        original_require = bpd._require_section_structure_payload

        def _patched_require(statute_id, row):
            if row.get("section") == "section:2":
                raise RuntimeError("Section row without structured payload: test")
            original_require(statute_id, row)

        monkeypatch.setattr(bpd, "_require_section_structure_payload", _patched_require)

        # section:1 row needs oracle_structure to pass the require check.
        # Inject it into the row by patching _attach_section_structures to
        # add a minimal structure to the first row only.
        def _fake_attach(statute_errors, statute_modes, *, section_cache_dir, workers=0):
            for sid, rows in statute_errors.items():
                for row in rows:
                    if row.get("section") == "section:1":
                        row["oracle_structure"] = '{"kind": "section", "label": "1"}'
                        row["replay_structure"] = '{"kind": "section", "label": "1"}'
                        row["structure_diff_kind"] = "text_only"

        monkeypatch.setattr(bpd, "_attach_section_structures", _fake_attach)

        output_path = tmp_path / "out.db"
        section_cache_dir = tmp_path / "scache"
        section_cache_dir.mkdir()
        html_cache = tmp_path / "html.farchive"

        bpd.build(
            cache_dir,
            output_path,
            html_cache,
            section_cache_dir,
            workers=1,
        )

        con = sqlite3.connect(str(output_path))
        row = con.execute(
            "SELECT error_count FROM statutes WHERE statute_id=?", ("2020/1",)
        ).fetchone()
        assert row is not None, "statute 2020/1 not found in statutes table"
        error_count = row[0]

        actual_errors = con.execute(
            "SELECT COUNT(*) FROM errors WHERE statute_id=?", ("2020/1",)
        ).fetchone()[0]
        con.close()

        # The pre-filter count would have been 2 (section:1 and section:2 after
        # the empty-section row is skipped by the existing guard in Pass 1).
        # After post-filter: section:2 is rejected by _require_section_structure_payload,
        # leaving exactly 1 inserted row.
        assert actual_errors == 1, f"expected 1 error row, got {actual_errors}"
        assert error_count == actual_errors, (
            f"statutes.error_count={error_count} != errors table count={actual_errors}"
        )
