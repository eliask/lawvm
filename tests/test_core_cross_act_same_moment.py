"""Shared §1.7 cross-act same-moment detector — synthetic op-driven coverage.

Drives ``lawvm.core.cross_act_same_moment.detect_cross_act_same_moment_conflicts``
with synthetic ``LegalOperation`` ops across multiple frontend prefixes (EE,
NO) plus a forward-looking precedence-claim resolution case.

Mirrors the §2.9 test pyramid:

  * Synthetic positives: the EE shape (two REPLACEs, REPEAL vs REPLACE) tagged
    with the EE ``finder_kind_prefix`` produces findings byte-identical to EE's
    own detector (``ee_same_moment_cross_act_incompatible_payload_ambiguous``).
  * Synthetic parameterized positives: NO prefix produces the same shape with
    ``no_same_moment_cross_act_incompatible_payload_ambiguous``.
  * Negatives: same-act ops, TEXT_REPLACE fragment ops, two REPEALs of same
    target, undated ops, different effective dates, single op — all skip the
    finding per the default conservative compatibility predicate.
  * Cross-frontend prefix-distinctness: EE/NO/EU all produce distinct
    ``rule_id``s so the per-frontend audit-trail invariant holds.
  * Precedence claim resolution: a validated ``SameMomentPrecedenceClaim``
    flips the finding to non-blocking and ``resolution: "resolved_by_claim"``;
    an unvalidated claim (wrong winner-act) leaves the simple-blocking finding.

Per AGENTS.md §2.9 (no leak): synthetic markers used here (“ee-replace-A”,
“no-act-a/2025”) are pure test scaffolding and MUST NOT leak past the
detector's finding detail into ``LegalAddress`` or ``ProvisionTimeline``;
this suite's no-leak invariant is the structural assertion that the finding
``detail['affected_target']`` is the address-path string serialization of the
synthetic op target (no marker leakage into the address tuple itself).
"""
from __future__ import annotations

from lawvm.core.cross_act_same_moment import (
    BASIS_LATER_ENACTMENT,
    DEFAULT_UNPROVEN_RESOLUTION_LABEL,
    DetectedSameMomentConflict,
    SameMomentPrecedenceClaim,
    detect_cross_act_same_moment_conflicts,
    detected_same_moment_conflicts_from_ops,
    validate_same_moment_precedence_claim,
)
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import IRNodeKind, TextPatchKindEnum
from lawvm.replay_adjudication import CompileAdjudication


#─ Synthetic op builders───────────────────────────────────────────────────


def _replace_section_op(
    *,
    op_id: str,
    sequence: int,
    section_label: str,
    source_id: str,
    effective: str,
    replacement_text: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label=section_label,
            text=replacement_text,
        ),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _repeal_section_op(
    *,
    op_id: str,
    sequence: int,
    section_label: str,
    source_id: str,
    effective: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", section_label),)),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _text_replace_section_op(
    *,
    op_id: str,
    sequence: int,
    section_label: str,
    source_id: str,
    effective: str,
    match_text: str,
    replacement: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text=match_text),
            replacement=replacement,
        ),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _ee_prefix_two_replaces_same_moment() -> list[LegalOperation]:
    return [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A lydelse.",
        ),
        _replace_section_op(
            op_id="ee-replace-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2026-01-01",
            replacement_text="Act B lydelse.",
        ),
    ]


#─ POSITIVE (EE shape): two REPLACEs same target same effective date → finding


def test_two_distinct_acts_replace_same_target_same_effective_date_emits_ee_finding() -> None:
    """Two REPLACE ops on §5 from distinct acts at the same effective date.

    The shared detector with ``finder_kind_prefix="ee"`` produces a finding
    byte-identical in shape to EE's own detector
    (``ee_same_moment_cross_act_incompatible_payload_ambiguous``): blocking=True,
    resolution=sequence_order_unproven, op_id="" so the per-op conserved-wrapper
    partition is unaffected, conflicting_affecting_acts sorted tuple.
    """
    ops = _ee_prefix_two_replaces_same_moment()
    adjudications: list[CompileAdjudication] = []
    lowering_observations: list[dict[str, object]] = []

    findings = detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="ee",
        adjudications_out=adjudications,
        lowering_observations_out=lowering_observations,
    )

    assert len(findings) == 1, f"expected 1 finding; got {findings!r}"
    finding = findings[0]
    assert finding["rule_id"] == "ee_same_moment_cross_act_incompatible_payload_ambiguous"
    assert finding["phase"] == "apply"
    assert finding["family"] == "temporal_recovery"
    assert finding["blocking"] is True
    assert finding["strict_disposition"] == "block"
    detail = finding["detail"] if "detail" in finding else finding
    # Diagnostic envelope flattens detail fields into the top-level dict via
    # `diagnostic_detail` — the finding payload is the merged record.
    assert detail["affected_target"] == "(('section', '5'),)"
    assert detail["effective_date"] == "2026-01-01"
    assert detail["reason_code"] == "same_moment_cross_act_incompatible_payload"
    assert detail["resolution"] == DEFAULT_UNPROVEN_RESOLUTION_LABEL
    assert set(detail["conflicting_affecting_acts"]) == {
        "ee/act-a/2025",
        "ee/act-b/2025",
    }
    conflicting_op_ids = {op["op_id"] for op in detail["conflicting_ops"]}
    assert conflicting_op_ids == {"ee-replace-A", "ee-replace-B"}
    by_id = {op["op_id"]: op for op in detail["conflicting_ops"]}
    assert by_id["ee-replace-A"]["action"] == "replace"
    assert by_id["ee-replace-A"]["affecting_act_id"] == "ee/act-a/2025"
    assert by_id["ee-replace-A"]["sequence"] == 1
    assert by_id["ee-replace-B"]["action"] == "replace"
    assert by_id["ee-replace-B"]["affecting_act_id"] == "ee/act-b/2025"
    assert by_id["ee-replace-B"]["sequence"] == 2

    # Dual-surface emission: adjudications_out and lowering_observations_out
    # each receive a mirrored record. The adjudication carries an empty op_id
    # (Pattern A) so the per-op conserved-wrapper partition is unaffected.
    assert len(adjudications) == 1
    adj = adjudications[0]
    assert adj.kind == "ee_same_moment_cross_act_incompatible_payload_ambiguous"
    assert adj.blocking is True
    assert adj.op_id == ""
    assert adj.source_statute == ""
    assert adj.phase == "apply"
    assert adj.detail["rule_id"] == finding["rule_id"]

    assert len(lowering_observations) == 1
    assert lowering_observations[0]["rule_id"] == finding["rule_id"]


#─ POSITIVE (EE shape): REPEAL vs REPLACE same target same effective date


def test_repeal_versus_replace_same_moment_is_incompatible_ee() -> None:
    """REPEAL of §5 against REPLACE of §5 at the same effective date is
    incompatible (you cannot both delete a provision and amend it)."""
    ops = [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A replacement text.",
        ),
        _repeal_section_op(
            op_id="ee-repeal-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2026-01-01",
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert len(findings) == 1
    detail = findings[0]
    assert set(detail["conflicting_affecting_acts"]) == {
        "ee/act-a/2025",
        "ee/act-b/2025",
    }
    assert detail["blocking"] is True


#─ NEGATIVE: same-act ops on same target — no finding


def test_same_act_two_ops_no_cross_act_finding() -> None:
    """Two ops from the SAME act on §5 at the same effective date are not a
    cross-act §1.7 conflict — within-source ordering/scope is its own lane."""
    ops = [
        _replace_section_op(
            op_id="ee-replace-A1",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A first replacement.",
        ),
        _replace_section_op(
            op_id="ee-replace-A2",
            sequence=2,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A second replacement.",
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert findings == []


#─ NEGATIVE: TEXT_REPLACE fragment on same target — no finding


def test_text_replace_does_not_trigger_incompatible_payload_finding() -> None:
    """Two TEXT_REPLACE ops on §5 at the same date are fragment-level and not
    flagged as incompatible — mirrors the UK/EE detector's exclusion of
    word/fragment-level effects (they can legitimately coexist at the same
    instant)."""
    ops = [
        _text_replace_section_op(
            op_id="ee-textreplace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            match_text="Original",
            replacement="replaced fragment A",
        ),
        _text_replace_section_op(
            op_id="ee-textreplace-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2026-01-01",
            match_text="Original",
            replacement="replaced fragment B",
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert findings == []


#─ NEGATIVE: two REPEALs of same target — not incompatible


def test_two_repeals_same_target_are_not_incompatible() -> None:
    """Two REPEALs of §5 from distinct acts are redundant destructive effects
    with the same outcome — NOT order-determining. The detector excludes them
    to avoid manufacturing false ambiguity from coexistence."""
    ops = [
        _repeal_section_op(
            op_id="ee-repeal-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
        ),
        _repeal_section_op(
            op_id="ee-repeal-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2026-01-01",
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert findings == []


#─ NEGATIVE: undated ops — no finding


def test_undated_ops_do_not_trigger_finding() -> None:
    """Two REPLACE ops with no effective date provenance are not a
    same-EFFECTIVE-DATE collision — bucketing undated ops together would
    manufacture false ambiguity from the absence of a date."""
    ops = [
        LegalOperation(
            op_id="ee-replace-A",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="5", text="Act A lydelse."),
            source=OperationSource(statute_id="ee/act-a/2025", effective=""),
        ),
        LegalOperation(
            op_id="ee-replace-B",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="5", text="Act B lydelse."),
            source=OperationSource(statute_id="ee/act-b/2025", effective=""),
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert findings == []


#─ NEGATIVE: single op — no finding


def test_single_op_no_finding() -> None:
    """A single op on §5 — no cross-act conflict, no finding (§2.9 negative)."""
    ops = [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A lydelse.",
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert findings == []


#─ NEGATIVE: different effective dates — no finding


def test_different_effective_dates_no_finding() -> None:
    """Two REPLACE ops on §5 from distinct acts but DIFFERENT effective dates
    are not a same-EFFECTIVE-DATE collision."""
    ops = [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A lydelse.",
        ),
        _replace_section_op(
            op_id="ee-replace-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2027-01-01",
            replacement_text="Act B lydelse.",
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert findings == []


#─ POSITIVE: NO prefix produces NO-kind finding


def test_no_prefix_emits_no_kind_finding() -> None:
    """Parameterized for the NO frontend: the shared detector with
    ``finder_kind_prefix="no"`` produces the same shape as EE but stamped
    ``no_same_moment_cross_act_incompatible_payload_ambiguous`` — the
    cross-frontend harmonization invariant (an EE finding never collides
    with a NO finding in an audit trail)."""
    ops = [
        _replace_section_op(
            op_id="no-replace-A",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A tekst.",
        ),
        _replace_section_op(
            op_id="no-replace-B",
            sequence=2,
            section_label="5",
            source_id="no/act-b/2025",
            effective="2026-01-01",
            replacement_text="Act B tekst.",
        ),
    ]

    findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="no"
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["rule_id"] == "no_same_moment_cross_act_incompatible_payload_ambiguous"
    assert finding["blocking"] is True
    assert finding["family"] == "temporal_recovery"
    assert finding["phase"] == "apply"
    assert set(finding["conflicting_affecting_acts"]) == {
        "no/act-a/2025",
        "no/act-b/2025",
    }
    finding_conflicting_op_ids = {op["op_id"] for op in finding["conflicting_ops"]}
    assert finding_conflicting_op_ids == {"no-replace-A", "no-replace-B"}


#─ POSITIVE: cross-frontend prefix-distinctness


def test_each_frontend_prefix_produces_distinct_rule_ids() -> None:
    """Each frontend's ``finder_kind_prefix`` must produce a distinct
    ``rule_id``. An EE finding, NO finding, EU finding, SE finding, NZ finding,
    US finding must each land on its own audit-trail channel — collapsing
    them would make one frontend's evidence invisible against another's."""
    ops = _ee_prefix_two_replaces_same_moment()
    expected_rule_ids = {
        "ee_same_moment_cross_act_incompatible_payload_ambiguous",
        "no_same_moment_cross_act_incompatible_payload_ambiguous",
        "eu_same_moment_cross_act_incompatible_payload_ambiguous",
        "se_same_moment_cross_act_incompatible_payload_ambiguous",
        "nz_same_moment_cross_act_incompatible_payload_ambiguous",
        "us_same_moment_cross_act_incompatible_payload_ambiguous",
    }
    actual_rule_ids = {
        detect_cross_act_same_moment_conflicts(
            ops, finder_kind_prefix=prefix
        )[0]["rule_id"]
        for prefix in ("ee", "no", "eu", "se", "nz", "us")
    }
    assert actual_rule_ids == expected_rule_ids
    # Cross-frontend prefix-distinctness is a 1:1 mapping (count == prefixes).
    assert len(actual_rule_ids) == 6


#─ POSITIVE: empty prefix fails loud (§1.10)


def test_empty_finder_kind_prefix_fails_loud() -> None:
    """Per AGENTS.md §1.10: an empty finder_kind_prefix would silently
    produce shared finding kinds across frontends (``_same_moment_...``)
    defeating cross-frontend audit-trail separation. Fail loud."""
    ops = _ee_prefix_two_replaces_same_moment()
    try:
        detect_cross_act_same_moment_conflicts(ops, finder_kind_prefix="")
    except ValueError as exc:
        assert "finder_kind_prefix" in str(exc)
    else:
        raise AssertionError(
            "expected ValueError for empty finder_kind_prefix (AGENTS.md §1.10)"
        )


def test_malformed_finder_kind_prefix_fails_loud() -> None:
    """Per AGENTS.md §1.10: a malformed finder_kind_prefix (uppercase, leading
    digit) must fail loud, not silently produce a malformed finding kind."""
    ops = _ee_prefix_two_replaces_same_moment()
    for bad_prefix in ("EE", "1ee", "ee-no"):
        try:
            detect_cross_act_same_moment_conflicts(
                ops, finder_kind_prefix=bad_prefix
            )
        except ValueError:
            continue
        else:
            raise AssertionError(
                f"expected ValueError for malformed finder_kind_prefix={bad_prefix!r}"
            )


#─ POSITIVE: validated SameMomentPrecedenceClaim flips finding to non-blocking


def test_validated_precedence_claim_resolves_conflict_non_blocking() -> None:
    """A validated ``SameMomentPrecedenceClaim`` binding the detected conflict
    flips the finding to ``blocking=False`` and records
    ``resolution: "resolved_by_claim"`` with the claimed winner. Per the
    §1.7/§0 (preserve uncertainty) invariant: the conflict is now owned by a
    typed claim, not silently picked by op.sequence."""
    ops = _ee_prefix_two_replaces_same_moment()
    claim = SameMomentPrecedenceClaim(
        claim_id="ee-claim-1",
        claim_kind="same_moment_precedence",
        effective_date="2026-01-01",
        affected_target="(('section', '5'),)",
        conflicting_affecting_acts=("ee/act-a/2025", "ee/act-b/2025"),
        winner_affecting_act_id="ee/act-a/2025",
        basis=BASIS_LATER_ENACTMENT,
        basis_note="act-a enacted later than act-b",
    )

    findings = detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="ee",
        precedence_claims=(claim,),
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["blocking"] is False
    assert finding["strict_disposition"] == "record"
    assert finding["resolution"] == "resolved_by_claim"
    assert (
        finding["resolved_by_claim_winner_affecting_act_id"] == "ee/act-a/2025"
    )


def test_unvalidated_precedence_claim_leaves_finding_blocking() -> None:
    """A precedence claim that names an act NOT in the conflict (or otherwise
    fails validation) leaves the finding in its default blocking-unproven
    state — a bad candidate must remain rejectable (AGENTS.md §0)."""
    ops = _ee_prefix_two_replaces_same_moment()
    # Claim names an act that isn't even in the conflict — must be rejected.
    bad_claim = SameMomentPrecedenceClaim(
        claim_id="ee-claim-bad",
        claim_kind="same_moment_precedence",
        effective_date="2026-01-01",
        affected_target="(('section', '5'),)",
        conflicting_affecting_acts=("ee/act-a/2025", "ee/act-c/2025"),
        winner_affecting_act_id="ee/act-c/2025",
        basis=BASIS_LATER_ENACTMENT,
    )

    findings = detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="ee",
        precedence_claims=(bad_claim,),
    )
    assert len(findings) == 1
    finding = findings[0]
    # Unvalidated claim → finding stays blocking-unproven.
    assert finding["blocking"] is True
    assert finding["resolution"] == DEFAULT_UNPROVEN_RESOLUTION_LABEL
    assert "resolved_by_claim_winner_affecting_act_id" not in finding


def test_validate_same_moment_precedence_claim_rejects_unknown_act() -> None:
    """The validator rejects a claim that names a winner act outside the
    conflict (AGENTS.md §0/§1.10 — never invent a winner)."""
    ops = _ee_prefix_two_replaces_same_moment()
    detected = detected_same_moment_conflicts_from_ops(ops)
    assert len(detected) == 1
    assert DetectedSameMomentConflict is type(detected[0])

    bad_claim = SameMomentPrecedenceClaim(
        claim_id="ee-claim-bad",
        claim_kind="same_moment_precedence",
        effective_date="2026-01-01",
        affected_target="(('section', '5'),)",
        conflicting_affecting_acts=("ee/act-a/2025", "ee/act-b/2025"),
        # Winner is OUTSIDE the conflicting act set — must be rejected at the
        # basis-admissibility stage.
        winner_affecting_act_id="ee/act-c/2025",
        basis=BASIS_LATER_ENACTMENT,
    )
    validation = validate_same_moment_precedence_claim(
        bad_claim,
        detected_conflicts=detected,
        finder_kind_prefix="ee",
    )
    assert validation.validated is False
    assert (
        validation.rule_id
        == "ee_same_moment_precedence_claim_rejected_basis"
    )
    assert "winner" in validation.reason.lower()


def test_validate_same_moment_precedence_claim_accepts_well_formed_claim() -> None:
    """The validator accepts a well-formed, conflict-bound claim with a
    recognized basis — the precedence-rule registry surface that future waves
    will consume."""
    ops = _ee_prefix_two_replaces_same_moment()
    detected = detected_same_moment_conflicts_from_ops(ops)
    assert len(detected) == 1

    claim = SameMomentPrecedenceClaim(
        claim_id="ee-claim-1",
        claim_kind="same_moment_precedence",
        effective_date="2026-01-01",
        affected_target="(('section', '5'),)",
        conflicting_affecting_acts=("ee/act-a/2025", "ee/act-b/2025"),
        winner_affecting_act_id="ee/act-a/2025",
        basis=BASIS_LATER_ENACTMENT,
        basis_note="act-a enacted 2025-12-01; act-b enacted 2025-12-15",
    )
    validation = validate_same_moment_precedence_claim(
        claim,
        detected_conflicts=detected,
        finder_kind_prefix="ee",
    )
    assert validation.validated is True
    assert validation.rule_id == "ee_same_moment_precedence_claim_validated"


#─ POSITIVE: default-overridable incompatible_payload_predicate


def test_custom_incompatible_payload_predicate_extends_coverage() -> None:
    """A frontend can supply its own comparator if its action vocabulary
    needs a different shape. Here: a frontend that treats two INSERT ops on
    the same section as incompatible (hypothetical drafting idiom)."""

    def _inserts_incompatible(left: LegalOperation, right: LegalOperation) -> bool:
        return (
            left.action is StructuralAction.INSERT
            and right.action is StructuralAction.INSERT
        )

    ops = [
        LegalOperation(
            op_id="ee-insert-A",
            sequence=1,
            action=StructuralAction.INSERT,
            anchor=LegalAddress(path=(("section", "5"),)),
            target=LegalAddress(path=(("section", "5"), ("subsection", "1"),)),
            payload=IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Insert A",
            ),
            source=OperationSource(
                statute_id="ee/act-a/2025", effective="2026-01-01"
            ),
        ),
        LegalOperation(
            op_id="ee-insert-B",
            sequence=2,
            action=StructuralAction.INSERT,
            anchor=LegalAddress(path=(("section", "5"),)),
            target=LegalAddress(path=(("section", "5"), ("subsection", "1"),)),
            payload=IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Insert B",
            ),
            source=OperationSource(
                statute_id="ee/act-b/2025", effective="2026-01-01"
            ),
        ),
    ]

    # Default predicate: INSERTs are fragment-level/non-structural → no finding.
    default_findings = detect_cross_act_same_moment_conflicts(
        ops, finder_kind_prefix="ee"
    )
    assert default_findings == []

    # Custom predicate: treat double-INSERT at the same target as incompatible.
    custom_findings = detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="ee",
        incompatible_payload_predicate=_inserts_incompatible,
    )
    assert len(custom_findings) == 1
    assert custom_findings[0]["blocking"] is True


def test_custom_unproven_resolution_label_per_frontend() -> None:
    """The ``unproven_resolution_label`` parameter exists for UK's frontend-
    specific tiebreak label (``affecting_act_id_lexical_order_unproven``).
    UK uses lexical act-id ordering; the rest use op.sequence ordering.
    Both still emit ``blocking=True`` and the finding has the same shape —
    only the resolution string differs."""
    ops = _ee_prefix_two_replaces_same_moment()
    findings = detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="uk",
        unproven_resolution_label="affecting_act_id_lexical_order_unproven",
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["blocking"] is True
    assert (
        finding["resolution"] == "affecting_act_id_lexical_order_unproven"
    )
    assert finding["rule_id"] == "uk_same_moment_cross_act_incompatible_payload_ambiguous"
