"""Tests for route_amendment — the citation routing pure function.

route_amendment decides whether an amendment act should be applied to a given
parent statute.  It is a pure function: string inputs → (bool, str) output, no
corpus access, no side effects.

Section layout
--------------
1. Unit tests with hand-crafted inputs — cover all four routing branches.
2. Gold-DB tests — replay real captured intermediates and verify consistency
   with the inline logic that was in place when the DB was populated.
   Skipped automatically when .cache/pipeline_gold.db is absent or empty.

Run fast (unit tests only):
    uv run pytest tests/test_citation_routing.py -x -q

Run including gold-DB tests (requires populated .cache/pipeline_gold.db):
    uv run pytest tests/test_citation_routing.py -x -q -m slow
"""
from __future__ import annotations

import os
import pytest

from lawvm.finland.grafter import (
    route_amendment,
    _normalize_johtolause_verbs,
)
from lawvm.finland.citation_routing import OP_KEYWORDS, extract_pending_amendment_target_id


# ---------------------------------------------------------------------------
# Section 1: Unit tests — hand-crafted inputs
# ---------------------------------------------------------------------------


class TestRouteAmendmentNoGuard:
    """Cases where the guard condition is not met — return apply=True immediately."""

    def test_empty_parent_id(self) -> None:
        result = route_amendment("muutetaan 3 §", "", "muutetaan 3 §", "", "2012/715")
        assert result == (True, "no_guard_needed")

    def test_empty_amendment_id(self) -> None:
        result = route_amendment("muutetaan 3 §", "", "muutetaan 3 §", "2009/953", "")
        assert result == (True, "no_guard_needed")

    def test_non_numeric_amendment_year(self) -> None:
        # amendment_id year part is not purely digits → skip guard
        result = route_amendment("muutetaan 3 §", "", "muutetaan 3 §", "2009/953", "abc/715")
        assert result == (True, "no_guard_needed")


class TestRouteAmendmentReferencesParent:
    """Cases where johtolause references the parent statute → apply."""

    def test_explicit_statute_ref_matches(self) -> None:
        # johto contains (280/66), parent is 1966/280
        johto_raw = (
            "kumotaan 20 päivänä toukokuuta 1966 annetun valtion eläkelain "
            "( 280/66 ) 8§:n 6 momentti"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1966/280", "1989/103"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_four_digit_year_ref_matches(self) -> None:
        # johto contains (1479/1993), parent is 1993/1479
        johto_raw = "muutetaan 30 päivänä joulukuuta 1993 annetun lain ( 1479/1993 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1993/1479", "2010/50"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_empty_citation_guard_johto_defaults_to_apply(self) -> None:
        # When no preamble text is available, routing defaults to apply.
        should_apply, reason = route_amendment(
            "", "", "muutetaan 3 §", "2009/953", "2012/715"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_present_tense_lisaa_counts_as_operative_keyword(self) -> None:
        """Finite present ``lisää`` must count as an operative keyword.

        Regression for Verohallinto clauses like ``Verohallinto lisää ... uuden
        4 a §:n``. The replay ingress pre-check uses ``OP_KEYWORDS`` before
        Phase 2; omitting ``lisää`` caused those amendments to be silently
        treated as non-operative and skipped.
        """
        assert "lisää" in OP_KEYWORDS


class TestRouteAmendmentNumCollisionSkip:
    """NUM-collision: amendment and parent share the same statute number
    but the johtolause cites a different statute."""

    def test_same_num_different_year(self) -> None:
        # amendment 1966/611, parent 1960/611 — same NUM=611
        # johto cites a completely different statute (72/56)
        johto_raw = (
            "muutetaan 20 päivänä heinäkuuta 1961 annetun merimieseläkelain "
            "( 72/56 ) 3 §"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1960/611", "1966/611"
        )
        assert should_apply is False
        assert reason == "num_collision_skip"

    def test_num_collision_numeric_comparison(self) -> None:
        # Both have num=500, different years, johto explicitly cites (999/2010)
        # which is a different statute — parenthesized citation required for detection.
        johto_raw = "muutetaan joulukuuta 2010 annetun lain ( 999/2010 ) 5 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "2005/500", "2010/500"
        )
        assert should_apply is False
        assert reason == "num_collision_skip"


class TestRouteAmendmentCitationMismatchSkip:
    """johtolause cites a different statute (not NUM-collision)."""

    def test_explicit_foreign_citation(self) -> None:
        # johto cites (280/66) but parent is 1966/611
        johto_raw = (
            "kumotaan 20 päivänä toukokuuta 1966 annetun valtion eläkelain "
            "( 280/66 ) 8§:n 6 momentti"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1966/611", "1989/103"
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_meta_repeal_pattern(self) -> None:
        # johto contains "kumotaan ... muuttamisesta ... annetun lain (NUM" pattern
        # → this is a meta-repeal targeting a prior amendment act, not the parent
        meta_johto = (
            "kumotaan eräiden lakien muuttamisesta annetun lain ( 123/2010 ) 3 §"
        )
        meta_norm = _normalize_johtolause_verbs(meta_johto)
        should_apply, reason = route_amendment(
            meta_norm, "", meta_johto, "2008/500", "2012/600"
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_pending_amendment_of_parent_title_is_distinct_skip_family(self) -> None:
        johto_raw = (
            "muutetaan valmiuslain muuttamisesta annetun lain ( 631/2022 ) "
            "88 ja 126 § seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "2011/1552",
            "2022/1188",
            source_title="Laki valmiuslain muuttamisesta annetun lain 88 ja 126 §:n muuttamisesta",
            parent_title="Valmiuslaki",
        )
        assert should_apply is False
        assert reason == "pending_amendment_of_parent_skip"
        assert extract_pending_amendment_target_id(
            johto_raw,
            "2022/1188",
            "Laki valmiuslain muuttamisesta annetun lain 88 ja 126 §:n muuttamisesta",
            "Valmiuslaki",
        ) == "2022/631"

    def test_pending_amendment_of_parent_title_handles_section_scoped_form(self) -> None:
        johto_raw = (
            "muutetaan valmiuslain 109 §:n muuttamisesta annetun lain "
            "( 1233/2020 ) 109 §:n 1 momentti seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "2011/1552",
            "2022/708",
            source_title="Laki valmiuslain 109 §:n muuttamisesta annetun lain muuttamisesta",
            parent_title="Valmiuslaki",
        )
        assert should_apply is False
        assert reason == "pending_amendment_of_parent_skip"
        assert extract_pending_amendment_target_id(
            johto_raw,
            "2022/708",
            "Laki valmiuslain 109 §:n muuttamisesta annetun lain muuttamisesta",
            "Valmiuslaki",
        ) == "2020/1233"

    def test_pending_amendment_of_parent_title_handles_annetun_lain_parent_titles(self) -> None:
        johto_raw = (
            "muutetaan yleisestä asumistuesta annetun lain muuttamisesta annetun lain "
            "( 1533/2016 ) 29 ja 41 § seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "2014/938",
            "2017/480",
            source_title="Laki yleisestä asumistuesta annetun lain muuttamisesta annetun lain 29 ja 41 §:n muuttamisesta",
            parent_title="Laki yleisestä asumistuesta",
        )
        assert should_apply is False
        assert reason == "pending_amendment_of_parent_skip"
        assert extract_pending_amendment_target_id(
            johto_raw,
            "2017/480",
            "Laki yleisestä asumistuesta annetun lain muuttamisesta annetun lain 29 ja 41 §:n muuttamisesta",
            "Laki yleisestä asumistuesta",
        ) == "2016/1533"


class TestRouteAmendmentSec1Fallback:
    """sec1 fallback: terse preamble with no op keywords, but sec1 cites parent."""

    def test_sec1_cites_parent_rescues_terse_preamble(self) -> None:
        # Preamble is terse (no citations, no op keywords) — mismatch from preamble alone.
        # sec1 does cite the parent → should apply.
        johto_terse = "Ympäristöministerin esittelystä säädetään:"
        johto_norm = _normalize_johtolause_verbs(johto_terse)
        sec1_text = "muutetaan rakennuslain ( 370/1958 ) 3 § seuraavasti:"
        sec1_norm = _normalize_johtolause_verbs(sec1_text)
        should_apply, reason = route_amendment(
            johto_norm, sec1_norm, johto_terse, "1958/370", "1993/949"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_sec1_not_used_when_preamble_has_op_keywords(self) -> None:
        # Preamble has op keywords but no matching citation — sec1 is NOT consulted
        # (to avoid omnibus-repealer bleed). Result: citation_mismatch_skip.
        johto_with_ops = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_with_ops)
        sec1_cites_parent = "muutetaan ( 370/1958 ) 5 §"
        sec1_norm = _normalize_johtolause_verbs(sec1_cites_parent)
        should_apply, reason = route_amendment(
            johto_norm, sec1_norm, johto_with_ops, "1958/370", "2021/400"
        )
        # Preamble cites 999/2020 (not 370/1958), has op keywords → sec1 NOT consulted
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_empty_sec1_no_rescue(self) -> None:
        # Preamble fails citation check, sec1 is empty → still skip
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1958/370", "2021/400"
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"


class TestRouteAmendmentTitleMismatch:
    """Title-based override: amendment title explicitly names a different statute."""

    def test_title_mismatch_overrides_citation_match(self) -> None:
        # johto references parent correctly, but title says it targets laki X (not asetus Y).
        # Use a concrete title pattern that _title_explicitly_targets_other_statute recognises.
        johto_raw = "muutetaan lain ( 500/2005 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        # source_title matches "laki <name> annetun lain muuttamisesta" but
        # names a different kind from parent (laki vs asetus)
        source_title = "laki luottolaitostoiminnasta annetun lain muuttamisesta"
        parent_title = "asetus luottolaitostoiminnasta"
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "2005/500", "2010/300",
            source_title=source_title,
            parent_title=parent_title,
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_no_title_provided_does_not_skip(self) -> None:
        # When source/parent titles are empty, title check is no-op → apply if refs match.
        johto_raw = "muutetaan lain ( 500/2005 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "2005/500", "2010/300",
            source_title="",
            parent_title="",
        )
        assert should_apply is True
        assert reason == "references_parent"


class TestRouteAmendmentReturnType:
    """Return type is always (bool, str) with a known reason string."""

    _KNOWN_REASONS = frozenset({
        "references_parent",
        "pending_amendment_of_parent_skip",
        "no_guard_needed",
        "num_collision_skip",
        "citation_mismatch_skip",
    })

    def test_apply_case_returns_bool_str(self) -> None:
        result = route_amendment("", "", "", "2009/953", "2012/715")
        assert isinstance(result, tuple) and len(result) == 2
        apply, reason = result
        assert isinstance(apply, bool)
        assert reason in self._KNOWN_REASONS

    def test_skip_case_returns_bool_str(self) -> None:
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        result = route_amendment(johto_norm, "", johto_raw, "1958/370", "2021/400")
        assert isinstance(result, tuple) and len(result) == 2
        apply, reason = result
        assert isinstance(apply, bool)
        assert reason in self._KNOWN_REASONS

    def test_skip_implies_false(self) -> None:
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        apply, reason = route_amendment(johto_norm, "", johto_raw, "1958/370", "2021/400")
        if "skip" in reason:
            assert apply is False

    def test_no_guard_implies_true(self) -> None:
        apply, reason = route_amendment("", "", "", "", "2012/715")
        assert reason == "no_guard_needed"
        assert apply is True


# ---------------------------------------------------------------------------
# Section 2: Gold-DB consistency tests
#
# These tests verify that route_amendment produces results consistent with
# the inline routing logic that was in place when the captures were recorded.
# They are skipped automatically when the gold DB is absent or empty.
# ---------------------------------------------------------------------------

_DB_PATH = ".cache/pipeline_gold.db"
_GOLD_AVAILABLE = False
_GOLD_CAPTURES: list = []

if os.path.exists(_DB_PATH):
    try:
        from lawvm.core.pipeline_capture import AmendmentCapture, CaptureStore
        _store = CaptureStore(_DB_PATH)
        _stats = _store.stats()
        if _stats["total_amendments"] > 0:
            for _sid in _store.statutes():
                _GOLD_CAPTURES.extend(_store.load(_sid))
            _GOLD_AVAILABLE = True
    except Exception:
        pass  # DB exists but unreadable — skip gracefully


def _gold_id(cap: "AmendmentCapture") -> str:
    return f"{cap.statute_id}/{cap.amendment_id}"


@pytest.mark.slow
@pytest.mark.skipif(not _GOLD_AVAILABLE, reason="pipeline_gold.db absent or empty")
@pytest.mark.parametrize("cap", _GOLD_CAPTURES, ids=_gold_id)
def test_route_amendment_matches_captured_citation_action(cap: "AmendmentCapture") -> None:
    """route_amendment reproduces the captured citation_action for every amendment.

    The gold DB was populated by the inline routing logic.  This test verifies
    that the extracted function is behaviourally identical.

    Mapping from legacy citation_action values to route_amendment reasons:
      "pass"                    → should_apply=True
      "skip_num_collision"      → reason="num_collision_skip"
      "skip_citation_mismatch"  → reason="citation_mismatch_skip"
      ""  (not captured)        → any True result is acceptable
    """
    from lawvm.finland.grafter import _normalize_johtolause_verbs as _nvn

    johto_norm = _nvn(cap.preamble_raw)
    should_apply, reason = route_amendment(
        citation_guard_johto=johto_norm,
        citation_guard_sec1="",   # gold DB does not store citation_guard_sec1
        johto=cap.preamble_normalized,
        parent_id=cap.statute_id,
        amendment_id=cap.amendment_id,
        source_title=cap.source_title,
        parent_title="",          # gold DB does not store parent title
    )

    captured = cap.citation_action
    if not captured or captured == "pass":
        assert should_apply is True, (
            f"{cap.statute_id}/{cap.amendment_id}: expected apply=True "
            f"(captured action={captured!r}), got reason={reason!r}"
        )
    elif captured == "skip_num_collision":
        assert reason == "num_collision_skip" and not should_apply, (
            f"{cap.statute_id}/{cap.amendment_id}: expected num_collision_skip, "
            f"got ({should_apply}, {reason!r})"
        )
    elif captured == "skip_citation_mismatch":
        assert "mismatch" in reason and not should_apply, (
            f"{cap.statute_id}/{cap.amendment_id}: expected citation_mismatch_skip, "
            f"got ({should_apply}, {reason!r})"
        )
