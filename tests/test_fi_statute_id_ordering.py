"""Regression: säädös id ordering must never silently degrade to base-only.

A Finnish statute has two id orderings: the canonical säädös id ``num/year``
(e.g. ``"301/2004"``) and the engine-internal ``year/num`` (e.g. ``"2004/301"``)
that keys the corpus and the amendment index. Handing the engine the wrong
ordering historically resolved to an *empty* amendment set without error — a
silent degradation to base-only materialization (the "~3 trees across 94 change
dates" fingerprint).

These tests pin the invariant at the normalization boundary
(:mod:`lawvm.finland.statute_id`) and the replay-plan entry point
(:func:`lawvm.finland.replay_pipeline.prepare_replay_plan`):

- both orderings of a real amended statute resolve to the SAME engine id and
  the SAME (non-empty) amendment set;
- an id that does not resolve to any base raises the named
  ``FI_STATUTE_ID_UNRESOLVED`` diagnostic — never base-only silently;
- a statute that legitimately has zero amendments still builds a valid base
  plan without error (no over-correction).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from lawvm.corpus_store import CorpusStore
from lawvm.finland.replay_pipeline import prepare_replay_plan
from lawvm.finland.statute_id import (
    StatuteIdError,
    canonical_statute_id,
    engine_statute_id,
    looks_like_statute_id,
    split_year_num,
)


# Witness: a real amended FI statute. 301/2004 (canonical) == 2004/301 (engine).
_CANONICAL = "301/2004"
_ENGINE = "2004/301"


# ---------------------------------------------------------------------------
# Normalizer unit tests (no corpus needed)
# ---------------------------------------------------------------------------

def test_engine_statute_id_accepts_both_orderings() -> None:
    assert engine_statute_id(_CANONICAL) == _ENGINE
    assert engine_statute_id(_ENGINE) == _ENGINE
    # Idempotent and whitespace-tolerant.
    assert engine_statute_id("  301/2004 ") == _ENGINE


def test_canonical_statute_id_accepts_both_orderings() -> None:
    assert canonical_statute_id(_ENGINE) == _CANONICAL
    assert canonical_statute_id(_CANONICAL) == _CANONICAL


def test_split_year_num_disambiguates_by_plausible_year() -> None:
    assert split_year_num(_CANONICAL) == ("2004", "301")
    assert split_year_num(_ENGINE) == ("2004", "301")


def test_non_statute_ids_pass_through_unchanged() -> None:
    # Non-year/num shapes are NOT reordered: the normalizer never invents
    # structure it cannot prove.
    assert engine_statute_id("test/1") == "test/1"
    assert not looks_like_statute_id("test/1")
    assert split_year_num("nonsense") is None


def test_subnumbered_old_id_recognized_year_first_but_not_reordered() -> None:
    # Sub-numbered olders (e.g. "1889/39-001") are already engine year-first;
    # the year component is recognized so they are NOT swapped.
    assert looks_like_statute_id("1889/39-001")
    assert engine_statute_id("1889/39-001") == "1889/39-001"
    assert split_year_num("1889/39-001") == ("1889", "39-001")


def test_statute_id_error_is_value_error_subclass() -> None:
    # Named diagnostic type exists and is catchable as ValueError.
    assert issubclass(StatuteIdError, ValueError)


# ---------------------------------------------------------------------------
# prepare_replay_plan boundary tests (stub corpus mimicking the dual ordering)
# ---------------------------------------------------------------------------

def _dual_ordering_corpus() -> CorpusStore:
    """Corpus that resolves ONLY the engine ``year/num`` key (like Finlex)."""

    def read_source(sid: str) -> bytes | None:
        return b"<body/>" if sid == _ENGINE else None

    return cast(
        CorpusStore,
        SimpleNamespace(
            read_source=read_source,
            load_spine_base_ir=lambda _sid, _base_ir, _xml_bytes: None,
        ),
    )


def _engine_keyed_amendment_resolver():
    """Amendment resolver keyed by the engine id, like the real index."""
    records = [
        {
            "sequence": 1,
            "statute_id": "2006/588",
            "title": "Amendment",
            "effective_date": "2006-09-01",
            "issue_date": "2006-06-29",
            "sort_mode": "legal_pit",
            "included": True,
        }
    ]

    def resolve(sid: str, mode: str, corpus=None, residuals_out=None):
        # The real index is keyed ONLY in engine 'year/num' form: a wrong-form
        # id would get an empty set here. The plan must hand us the engine id.
        return (records if sid == _ENGINE else [], None, "")

    return resolve


def _build_plan(parent_id: str, *, resolver):
    return prepare_replay_plan(
        parent_id,
        mode="legal_pit",
        strict_profile=None,
        corpus=_dual_ordering_corpus(),
        stop_before="",
        label_postprocessor=lambda _sid, label: label,
        get_replay_profile=lambda _mode: SimpleNamespace(normalize_replay_text=False),
        resolve_applicable_amendment_records=resolver,
        get_consolidated_oracle_suspect=lambda _sid, corpus=None: None,
        extract_inline_corrections=lambda xml_bytes, _sid: ([], xml_bytes),
    )


def test_canonical_ordering_resolves_amendments_not_base_only() -> None:
    # The reported failure mode: wrong-form (canonical) id used to silently
    # yield zero amendments -> base-only. It must now resolve identically to
    # the engine form.
    resolver = _engine_keyed_amendment_resolver()
    plan = _build_plan(_CANONICAL, resolver=resolver)
    assert plan.parent_id == _ENGINE  # normalized at the boundary
    assert plan.amendment_ids == ["2006/588"]  # NOT empty -> not base-only


def test_engine_ordering_resolves_amendments() -> None:
    resolver = _engine_keyed_amendment_resolver()
    plan = _build_plan(_ENGINE, resolver=resolver)
    assert plan.parent_id == _ENGINE
    assert plan.amendment_ids == ["2006/588"]


def test_both_orderings_yield_identical_amendment_set() -> None:
    resolver = _engine_keyed_amendment_resolver()
    canonical_plan = _build_plan(_CANONICAL, resolver=resolver)
    engine_plan = _build_plan(_ENGINE, resolver=resolver)
    assert canonical_plan.amendment_ids == engine_plan.amendment_ids
    assert canonical_plan.amendment_ids  # non-empty witness


def test_unresolvable_id_raises_named_diagnostic_not_base() -> None:
    # An id that resolves no base in EITHER ordering must raise the named
    # diagnostic, never silently proceed with base-only materialization.
    resolver = _engine_keyed_amendment_resolver()
    with pytest.raises(RuntimeError, match="FI_STATUTE_ID_UNRESOLVED"):
        _build_plan("9999/123456", resolver=resolver)


def test_genuinely_unamended_statute_builds_base_plan_without_error() -> None:
    # Do NOT over-correct: a statute that legitimately has zero amendments must
    # still build a valid base plan without raising.
    def resolve_empty(sid: str, mode: str, corpus=None, residuals_out=None):
        return ([], None, "")

    plan = _build_plan(_ENGINE, resolver=resolve_empty)
    assert plan.parent_id == _ENGINE
    assert plan.amendment_ids == []  # legitimately empty, no error
