"""UK per-EID divergence enrichment — finer ``source_pathology_label`` signal.

These tests pin the additive ``source_pathology_label`` enrichment on the UK
per-EID divergence surface in ``lawvm.tools.uk_oracle_check``:

* the new label is populated from a synthetic effect-diagnostic input, derived
  from the covering effect's source-pathology / manual-frontier / compare-shape
  class (``source_adjudication`` vocab);
* the existing ``diagnosis`` field is UNCHANGED (the ledger adapter keys on it);
* totality — every per-EID row carries a label that is never silently "" when a
  covering diagnostic row exists (loud ``unclassified`` sentinel instead);
* determinism — the same synthetic input yields byte-identical row tuples.

The synthetic ``UKDivergenceState`` is injected by monkeypatching the shared
read-only core ``_compute_uk_divergence_state`` so no archive / replay is run.
"""
from __future__ import annotations

import lawvm.tools.uk_oracle_check as oc
from lawvm.tools.uk_oracle_check import (
    UKDivergenceRow,
    UKDivergenceState,
    _UNCLASSIFIED_SOURCE_PATHOLOGY_LABEL,
    _source_pathology_label_for_cover,
    uk_divergence_rows_for_statute,
)
from lawvm.uk_legislation.source_adjudication import (
    UK_EFFECT_COMPARE_SHAPE_CLASSES,
    UK_EFFECT_SOURCE_PATHOLOGY_CLASSES,
)


# ── derivation helper (pure) ────────────────────────────────────────────────


def test_label_from_recognized_source_pathology_class() -> None:
    pathology = sorted(UK_EFFECT_SOURCE_PATHOLOGY_CLASSES)[0]
    cover = {"rule_id": "uk_effect_source_pathology_classified", "source_pathology": pathology}
    assert _source_pathology_label_for_cover(cover) == pathology


def test_label_from_recognized_compare_shape_class() -> None:
    shape = sorted(UK_EFFECT_COMPARE_SHAPE_CLASSES)[0]
    cover = {"source_pathology": shape}
    assert _source_pathology_label_for_cover(cover) == shape


def test_label_from_manual_compile_rule_id() -> None:
    cover = {
        "rule_id": "uk_manual_compile_frontier_classified",
        "manual_compile_rule_id": "uk_manual_frontier_appropriate_place_candidate",
    }
    assert (
        _source_pathology_label_for_cover(cover)
        == "uk_manual_frontier_appropriate_place_candidate"
    )


def test_label_from_manual_frontier_rule_id_fallback() -> None:
    cover = {"rule_id": "uk_manual_frontier_crossheading_candidate"}
    assert (
        _source_pathology_label_for_cover(cover)
        == "uk_manual_frontier_crossheading_candidate"
    )


def test_unrecognized_source_pathology_surfaced_verbatim_not_dropped() -> None:
    cover = {"rule_id": "some_other_rule", "source_pathology": "a_new_unmodelled_class"}
    # A finer class we do not yet model must stay visible, not collapse to sentinel.
    assert _source_pathology_label_for_cover(cover) == "a_new_unmodelled_class"


def test_covered_but_no_finer_class_is_loud_sentinel() -> None:
    cover = {"rule_id": "uk_replay_existing_target_gap"}
    assert (
        _source_pathology_label_for_cover(cover)
        == _UNCLASSIFIED_SOURCE_PATHOLOGY_LABEL
    )
    assert _UNCLASSIFIED_SOURCE_PATHOLOGY_LABEL == "unclassified"


def test_no_cover_is_empty_string() -> None:
    # "" is reserved for "no covering diagnostic row at all" — matches the other
    # covering-derived fields (blame/phase/rule_id), distinct from "unclassified".
    assert _source_pathology_label_for_cover(None) == ""


# ── per-EID surface integration (synthetic state, no archive) ───────────────


def _synthetic_state() -> UKDivergenceState:
    """A deterministic synthetic compile+classify product with covering rows."""
    pathology = sorted(UK_EFFECT_SOURCE_PATHOLOGY_CLASSES)[0]
    return UKDivergenceState(
        buckets={
            "deterministic_gap": ["section-1", "section-2"],
            "manual_frontier": ["section-3"],
            "oracle_suspect": ["section-4"],
            "text_diff": [],
        },
        lowering_rejections=[
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "affected_provisions": "section-1",
                "affecting_act_id": "ukpga/2001/1",
                "owner_phase": "compile_source_pathology",
                "source_pathology": pathology,
            },
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "affected_provisions": "section-3",
                "affecting_act_id": "ukpga/2002/2",
                "owner_phase": "compile_manual_frontier",
                "manual_compile_rule_id": "uk_manual_frontier_appropriate_place_candidate",
            },
        ],
        effect_diagnostics=[
            {
                "rule_id": "uk_replay_existing_target_gap",
                "affected_provisions": "section-2",
                "affecting_act_id": "ukpga/2003/3",
                "owner_phase": "replay",
            },
        ],
    )


def _rows(monkeypatch) -> list[UKDivergenceRow]:
    monkeypatch.setattr(
        oc, "_compute_uk_divergence_state", lambda statute_id, **kw: _synthetic_state()
    )
    return uk_divergence_rows_for_statute("ukpga/2001/1")


def test_new_field_populated_from_synthetic_diagnostics(monkeypatch) -> None:
    rows = {r.eid: r for r in _rows(monkeypatch)}
    pathology = sorted(UK_EFFECT_SOURCE_PATHOLOGY_CLASSES)[0]

    # section-1: recognized source-pathology class
    assert rows["section-1"].source_pathology_label == pathology
    # section-3: manual-frontier classification id
    assert (
        rows["section-3"].source_pathology_label
        == "uk_manual_frontier_appropriate_place_candidate"
    )
    # section-2: covered by a rejection with no finer class -> loud sentinel
    assert rows["section-2"].source_pathology_label == _UNCLASSIFIED_SOURCE_PATHOLOGY_LABEL
    # section-4: no covering diagnostic row at all -> "" (like blame/phase)
    assert rows["section-4"].source_pathology_label == ""
    assert rows["section-4"].blame_source == ""


def test_existing_diagnosis_field_unchanged(monkeypatch) -> None:
    rows = {r.eid: r for r in _rows(monkeypatch)}
    # The coarse bucket diagnosis (ledger adapter key) is untouched by enrichment.
    assert rows["section-1"].diagnosis == "deterministic_gap"
    assert rows["section-2"].diagnosis == "deterministic_gap"
    assert rows["section-3"].diagnosis == "manual_frontier"
    assert rows["section-4"].diagnosis == "oracle_suspect"


def test_totality_every_row_has_a_label(monkeypatch) -> None:
    for r in _rows(monkeypatch):
        # A covered row never carries a silently-blank finer label; an uncovered
        # row carries "" (the documented no-cover case). Either way the attribute
        # exists on every row and is a str.
        assert isinstance(r.source_pathology_label, str)
        if r.eid in {"section-1", "section-2", "section-3"}:
            assert r.source_pathology_label != ""


def test_determinism_identical_runs(monkeypatch) -> None:
    first = _rows(monkeypatch)
    second = _rows(monkeypatch)
    assert first == second
    # Stable ordering: buckets in classifier key order, EIDs sorted per bucket.
    assert [r.eid for r in first] == ["section-1", "section-2", "section-3", "section-4"]
