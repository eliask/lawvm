"""US char-span apply-seam boundary + coverage gate (LawVM task #86).

Design: ``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.4 ("Boundary is
metric-agnostic — IR-paths vs char-spans") + the US sections of §2.2/§3.5/§4. US
is a HYBRID — structured ``LegalOperation``s, a TEXT-level materializer — so it
joins ``core/apply_seam.apply_op`` at CHAR-SPAN granularity via the new
``core/char_span_metric.CHAR_SPAN_METRIC`` + the US text materializer
(``us_federal/apply_profile.py``).

WHAT THIS GATE PROVES.
  1. The char-span region metric (§3.4) is a correct ``RegionMetric``: declared
     span = the located target span, observed span = the minimal changed span,
     ``within`` = ⊆, ``disjoint_elsewhere`` = nothing-outside-changed.
  2. The mutation-boundary invariant — *the op changed only its declared region,
     nothing outside it* — HOLDS on REAL US ops routed through ``apply_op``: the
     edited char span ⊆ the located target span AND nothing else in the section
     changed. An op that edits outside its located target span is surfaced.
  3. The US oracle-changed-sections / claimed-sections coverage model is unit-set
     algebra that feeds ``core/coverage_totality.assert_coverage_totality`` as an
     ADDITIVE audit lane (observe-only; the US dry-run output is unchanged).

BYTE-IDENTITY. ``us_federal/apply_profile.py`` is an additive lane: it never
touches ``build_us_dry_run``'s composition loop, AGREE/RESIDUAL rows, refusals, or
materialized text. The materializer delegates VERBATIM to ``_materialize_one`` —
the text US produces is byte-identical.
"""

from __future__ import annotations

from lawvm.core.char_span_metric import (
    CHAR_SPAN_METRIC,
    CharSpanState,
    char_span_boundary_holds,
)
from lawvm.core.coverage import CoverageUnit
from lawvm.core.coverage_totality import (
    COVERAGE_UNIT_UNCLASSIFIED,
    assert_coverage_totality,
    target_touch_partition,
)
from lawvm.core.ir import (
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.us_federal.apply_profile import (
    US_CHAR_SPAN_BOUNDARY_FINDING,
    apply_us_op,
    us_apply_profile,
    us_coverage_claim_for_op,
)
from lawvm.us_federal.source_tree import synthetic_usc_section


# ── builders ──────────────────────────────────────────────────────────────────


def _section():
    """A section whose unique ``paragraph:1`` lives under ``subsection:b``.

    Mirrors the title-10 §2432 amendment family the dry-run suite uses. Subsection
    (a) has no paragraphs; (b) carries (1) (the 15-year window) and (2).
    """
    return synthetic_usc_section(
        title=10,
        section="2432",
        text=(
            "(a) Subsection A has no paragraphs. "
            "(b) Authority is granted. "
            "(1) The first paragraph mentions a 15-year window. "
            "(2) The second paragraph stands alone."
        ),
    )


def _text_replace_op(*, op_id, segments, match_text, replacement, occurrence=1):
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("title", "10"), ("section", "2432"), *segments)),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text=match_text, occurrence=occurrence),
            replacement=replacement,
        ),
    )


# ── 1. CHAR_SPAN_METRIC is a correct region metric (§3.4) ─────────────────────


def test_char_span_metric_declared_observed_within_disjoint():
    before = "Section 1. The quick brown fox jumps."
    after = "Section 1. The quick red fox jumps."

    # whole-section op: declared span is the whole blob.
    whole = CharSpanState(section_text=before, located_node_text=None)
    assert CHAR_SPAN_METRIC.declared_region(None, whole) == (0, len(before))

    # observed span: the minimal changed interval (prefix/suffix-trimmed).
    observed = CHAR_SPAN_METRIC.observed_region(before, after)
    assert before[observed[0] : observed[1]] == "brown"
    assert after[observed[0] : observed[0] + len("red")] == "red"

    # within ⊆ + disjoint-elsewhere both hold for an in-bounds edit.
    declared_node = CharSpanState(
        section_text=before, located_node_text="The quick brown fox jumps."
    )
    declared = CHAR_SPAN_METRIC.declared_region(None, declared_node)
    assert declared is not None
    assert CHAR_SPAN_METRIC.within(observed, declared)
    assert CHAR_SPAN_METRIC.disjoint_elsewhere(before, after, declared)


def test_char_span_metric_no_op_edit_is_within_any_declared_span():
    blob = "Section 1. Unchanged."
    observed = CHAR_SPAN_METRIC.observed_region(blob, blob)
    assert observed[0] == observed[1]  # empty span
    assert CHAR_SPAN_METRIC.within(observed, (0, len(blob)))


def test_char_span_metric_unlocatable_node_declares_none():
    blob = "Section 1. Body."
    state = CharSpanState(section_text=blob, located_node_text="ABSENT FRAGMENT")
    assert CHAR_SPAN_METRIC.declared_region(None, state) is None


def test_char_span_disjoint_elsewhere_handles_length_changing_edit():
    # A replacement that GROWS the region must not be read as an out-of-boundary
    # tail change: the suffix is matched by length against the after tail, not by
    # indexing ``after`` at the before-text offset ``end``.
    before = "head MIDDLE tail"
    after = "head MIDDLE-GROWN tail"
    declared = (5, 11)  # the "MIDDLE" region in ``before``
    assert before[declared[0] : declared[1]] == "MIDDLE"
    assert CHAR_SPAN_METRIC.disjoint_elsewhere(before, after, declared)
    # A shrinking edit, same invariant.
    after_shrunk = "head M tail"
    assert CHAR_SPAN_METRIC.disjoint_elsewhere(before, after_shrunk, declared)
    # An edit that ALSO mangles the tail (outside the declared span) is rejected.
    after_tail_changed = "head MIDDLE TAIL-MANGLED"
    assert not CHAR_SPAN_METRIC.disjoint_elsewhere(
        before, after_tail_changed, declared
    )


def test_char_span_boundary_holds_flags_edit_outside_declared_span():
    # The declared span is the FIRST sentence; the edit lands in the SECOND.
    before = "First sentence. Second sentence has a typo here."
    after = "First sentence. Second sentence has a fix here."
    state = CharSpanState(section_text=before, located_node_text="First sentence.")
    verdict = char_span_boundary_holds(before, after, state, op_id="escape")
    assert not verdict.within_boundary
    assert not verdict.unresolved_declared


# ── 2. boundary invariant on REAL US ops through apply_op ─────────────────────


def test_apply_us_op_boundary_holds_on_subsection_text_replace():
    section = _section()
    op = _text_replace_op(
        op_id="strike-15y",
        segments=(("subsection", "b"), ("paragraph", "1")),
        match_text="15-year",
        replacement="20-year",
    )
    res = apply_us_op(
        section.statutory_text,
        op,
        before_section=section,
        source_statute="10:2432",
    )
    # The materialized text is byte-identical to the direct materializer.
    assert res.applied.applied
    assert "20-year" in res.applied.new_state.section_text
    assert "15-year" not in res.applied.new_state.section_text
    # The boundary invariant HOLDS: edited span ⊆ located paragraph (1) span,
    # nothing outside it changed → no observation.
    assert res.boundary is not None
    assert res.boundary.within_boundary
    assert res.observations == ()
    # The observed edit really is inside the declared span.
    assert res.boundary.declared_span is not None
    d_start, d_end = res.boundary.declared_span
    o_start, o_end = res.boundary.observed_span
    assert d_start <= o_start and o_end <= d_end
    # Sibling prose survived (the disjoint-elsewhere half, witnessed in text).
    assert "(a) Subsection A has no paragraphs." in res.applied.new_state.section_text
    assert "(2) The second paragraph stands alone." in res.applied.new_state.section_text


def test_apply_us_op_byte_identical_to_direct_materializer():
    from lawvm.us_federal.dry_run import _materialize_one

    section = _section()
    op = _text_replace_op(
        op_id="strike-15y",
        segments=(("subsection", "b"), ("paragraph", "1")),
        match_text="15-year",
        replacement="20-year",
    )
    from lawvm.us_federal.dry_run import USDryRunRefusal

    direct = _materialize_one(op, section.statutory_text, before_section=section)
    assert not isinstance(direct, USDryRunRefusal)
    direct_text, signal, _disp = direct
    assert signal == ""
    from lawvm.us_federal.dry_run import _normalize_text

    res = apply_us_op(section.statutory_text, op, before_section=section)
    # The seam-routed text is exactly the normalized direct materialization.
    assert res.applied.new_state.section_text == _normalize_text(direct_text)


def test_apply_us_op_whole_section_op_declares_whole_blob():
    # A whole-section TEXT_REPLACE: declared span is the whole section.
    section = _section()
    op = _text_replace_op(
        op_id="strike-granted",
        segments=(),  # target == the section itself (no sub-section segments)
        match_text="granted",
        replacement="conferred",
    )
    res = apply_us_op(section.statutory_text, op, before_section=section)
    assert res.applied.applied
    assert res.boundary is not None
    assert res.boundary.declared_span == (0, len(section.statutory_text))
    assert res.boundary.within_boundary
    assert "conferred" in res.applied.new_state.section_text


def test_us_apply_profile_plugs_char_span_metric():
    profile = us_apply_profile()
    assert profile.jurisdiction == "us"
    assert profile.region_metric is CHAR_SPAN_METRIC
    # Receipts/coverage are handled by the dedicated US lanes, not the seam's IR
    # synthesis (which would assert an IRNode state).
    assert profile.emit_receipts is False
    assert profile.emit_coverage is False
    # The IR boundary gate is off for the char-span lane; the char audit is
    # explicit in apply_us_op.
    assert profile.boundary_mode == "off"


# ── 3. coverage totality ingests the US section-set model (additive) ──────────


def _section_unit(number: str, *, tags=frozenset()) -> CoverageUnit:
    return CoverageUnit(
        unit_id=f"section_{number}",
        kind="section",
        observed_label=number,
        parent_label=None,
        payload_ref=None,
        tags=tags,
    )


def test_coverage_totality_over_us_claimed_and_oracle_changed_sections():
    # The US model: oracle-changed sections are the source units; the ops that
    # landed are the claims. A section claimed by a landed op is covered; an
    # oracle-changed section with NO claim is the honest lowering gap.
    oracle_changed = [_section_unit("2432"), _section_unit("2500")]
    landed_ops = [
        _text_replace_op(
            op_id="op-2432",
            segments=(("subsection", "b"), ("paragraph", "1")),
            match_text="15-year",
            replacement="20-year",
        )
    ]
    claims = [
        c for c in (us_coverage_claim_for_op(op) for op in landed_ops) if c is not None
    ]
    assert {uid for c in claims for uid in c.covered_unit_ids} == {"section_2432"}

    # A STRICT classifier that refuses to place a section (returns None) surfaces
    # the uncovered oracle-changed section as a typed COVERAGE.UNIT_UNCLASSIFIED —
    # the honest "oracle changed it, we never claimed it" gap.
    observations, report = assert_coverage_totality(
        source_units=oracle_changed,
        ops=landed_ops,
        target_units=oracle_changed,
        ledger=claims,
        classify=lambda unit: None,
        source_statute="10:title",
    )
    assert len(observations) == 1
    obs = observations[0]
    assert obs.kind == COVERAGE_UNIT_UNCLASSIFIED
    assert obs.detail["unit_id"] == "section_2500"
    # The report partitions totally: the covered section is not a gap, the
    # uncovered one is recorded.
    gap_ids = {g.unit.unit_id for g in report.gaps}
    assert gap_ids == {"section_2500"}


def test_coverage_totality_target_touch_partition_splits_us_sections():
    targets = [_section_unit("2432"), _section_unit("2500")]
    op = _text_replace_op(
        op_id="op-2432",
        segments=(("subsection", "b"), ("paragraph", "1")),
        match_text="15-year",
        replacement="20-year",
    )
    claim = us_coverage_claim_for_op(op)
    assert claim is not None
    touched, untouched = target_touch_partition(targets, [claim])
    assert [u.unit_id for u in touched] == ["section_2432"]
    assert [u.unit_id for u in untouched] == ["section_2500"]


def test_coverage_totality_default_classifier_never_unclassified():
    # With the default classifier (FI tag logic, never None), an uncovered
    # operative section is a supplemental_candidate gap, NOT an unclassified
    # finding — additive coverage with zero spurious findings.
    oracle_changed = [_section_unit("2500")]
    observations, report = assert_coverage_totality(
        source_units=oracle_changed,
        ops=[],
        target_units=oracle_changed,
        ledger=[],
        source_statute="10:title",
    )
    assert observations == ()
    assert {g.disposition for g in report.gaps} == {"supplemental_candidate"}


# ── boundary observation disposition (observe vs block) ───────────────────────


def test_apply_us_op_observe_mode_emits_finding_on_escape(monkeypatch):
    # Force an out-of-boundary edit by pointing the located node text at a
    # fragment that does NOT contain the edit, then confirm observe-mode emits the
    # non-blocking US char-span boundary finding.
    from lawvm.us_federal import apply_profile as ap

    section = _section()
    op = _text_replace_op(
        op_id="strike-15y",
        segments=(("subsection", "b"), ("paragraph", "1")),
        match_text="15-year",
        replacement="20-year",
    )

    # Monkeypatch the locator to return a fragment that excludes the edit site:
    # subsection (a) prose, which the paragraph (1) edit is NOT inside.
    monkeypatch.setattr(
        ap,
        "_located_node_text_for",
        lambda op, before_section: "(a) Subsection A has no paragraphs.",
    )
    res = apply_us_op(
        section.statutory_text,
        op,
        before_section=section,
        boundary_mode="observe",
    )
    assert res.applied.applied
    assert res.boundary is not None
    assert not res.boundary.within_boundary
    assert len(res.observations) == 1
    assert res.observations[0].kind == US_CHAR_SPAN_BOUNDARY_FINDING
