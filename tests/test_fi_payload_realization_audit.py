from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.finland.ops import AmendmentOp, OpType, ResolvedOp
from lawvm.finland.payload_realization_audit import payload_realization_findings


def _resolved(payload_text: str, *, op_id: str = "op1", op_type: OpType = "REPLACE") -> ResolvedOp:
    return ResolvedOp(
        op=AmendmentOp(
            op_id=op_id,
            op_type=op_type,
            target_section="1",
            target_unit_kind="section",
        ),
        muutos_ir=IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            children=(IRNode(kind=IRNodeKind.CONTENT, text=payload_text),),
        ),
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="1",
        op_id=op_id,
        _op_type_seed=op_type,
    )


def _after(text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
            ),
        ),
    )


def test_payload_realization_audit_is_clean_when_payload_text_lands() -> None:
    findings = payload_realization_findings(
        resolved_ops=(_resolved("Substantive amendment payload appears here."),),
        after_ir=_after("The folded statute says substantive amendment payload appears here."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_reports_missing_payload_text() -> None:
    findings = payload_realization_findings(
        resolved_ops=(_resolved("Substantive amendment payload appears here."),),
        after_ir=_after("The folded statute still contains unrelated old text."),
        amendment_id="2000/1",
    )

    assert [finding.kind for finding in findings] == ["COVERAGE.PAYLOAD_REALIZATION_GAP"]
    assert findings[0].stage == "post_apply_payload_realization"
    assert findings[0].source_statute == "2000/1"
    assert findings[0].detail["unit_id"] == "op1"
    assert findings[0].detail["disposition"] == "source_payload_text_not_realized_in_post_fold_state"


def test_replay_filters_payload_gaps_realized_in_materialized_product() -> None:
    from lawvm.finland.compile import compile_fi_facade

    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        facade = compile_fi_facade(
            "1998/464",
            replay_mode="official_consolidation",
            compile_mode="quirks",
        )

    gaps = [finding for finding in facade.finding_ledger if finding.kind == "COVERAGE.PAYLOAD_REALIZATION_GAP"]

    assert gaps == []


def test_payload_realization_audit_scopes_child_target_to_matching_payload_subtree() -> None:
    carrier = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="7a",
                        text="Owned inserted item text appears here.",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="16",
                        text="Sibling replacement text belongs to another operation.",
                    ),
                ),
            ),
        ),
    )
    op = ResolvedOp(
        op=AmendmentOp(
            op_id="op1",
            op_type="INSERT",
            target_section="6",
            target_unit_kind="section",
        ),
        muutos_ir=carrier,
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="6",
        op_id="op1",
        _op_type_seed="INSERT",
        _target_address_override=LegalAddress(
            path=(("section", "6"), ("subsection", "1"), ("item", "7a"))
        ),
    )

    findings = payload_realization_findings(
        resolved_ops=(op,),
        after_ir=_after("The folded statute says owned inserted item text appears here."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_scopes_split_subitem_target_to_combined_payload_label() -> None:
    carrier = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text="Shared subsection intro belongs to the carrier, not this subitem.",
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="7e",
                text="Owned subitem text appears here.",
            ),
        ),
    )
    op = ResolvedOp(
        op=AmendmentOp(
            op_id="op1",
            op_type="INSERT",
            target_section="6",
            target_unit_kind="section",
        ),
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=carrier,
        target_norm="6",
        op_id="op1",
        _op_type_seed="INSERT",
        _target_address_override=LegalAddress(
            path=(("section", "6"), ("subsection", "1"), ("item", "7"), ("subitem", "e"))
        ),
    )

    findings = payload_realization_findings(
        resolved_ops=(op,),
        after_ir=_after("The folded statute says owned subitem text appears here."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_scopes_combined_item_target_to_split_payload_child() -> None:
    carrier = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text="Shared subsection intro belongs to the carrier, not this item child.",
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="6",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBPARAGRAPH,
                        label="c",
                        text="Owned split child text appears here.",
                    ),
                    IRNode(
                        kind=IRNodeKind.SUBPARAGRAPH,
                        label="d",
                        text="Sibling split child text belongs to another operation.",
                    ),
                ),
            ),
        ),
    )
    op = ResolvedOp(
        op=AmendmentOp(
            op_id="op1",
            op_type="REPLACE",
            target_section="1",
            target_unit_kind="section",
        ),
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=carrier,
        target_norm="1",
        op_id="op1",
        _op_type_seed="REPLACE",
        _target_address_override=LegalAddress(
            path=(("section", "1"), ("subsection", "1"), ("item", "6c"))
        ),
    )

    findings = payload_realization_findings(
        resolved_ops=(op,),
        after_ir=_after("The folded statute says owned split child text appears here."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_does_not_smuggle_carrier_text_to_unmatched_child_target() -> None:
    op = ResolvedOp(
        op=AmendmentOp(
            op_id="op1",
            op_type="INSERT",
            target_section="6",
            target_unit_kind="section",
        ),
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            text="Shared subsection intro must not be charged to a missing child target.",
        ),
        target_norm="6",
        op_id="op1",
        _op_type_seed="INSERT",
        _target_address_override=LegalAddress(
            path=(("section", "6"), ("subsection", "1"), ("item", "7"), ("subitem", "e"))
        ),
    )

    findings = payload_realization_findings(
        resolved_ops=(op,),
        after_ir=_after("The folded statute has unrelated text."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_scopes_heading_target_to_heading_facet() -> None:
    carrier = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Owned chapter heading"),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                text="Sibling section body belongs to a separate operation.",
            ),
        ),
    )
    op = ResolvedOp(
        op=AmendmentOp(
            op_id="op1",
            op_type="REPLACE",
            target_chapter="6",
            target_unit_kind="chapter",
        ),
        muutos_ir=carrier,
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="6",
        op_id="op1",
        _op_type_seed="REPLACE",
        _target_address_override=LegalAddress(
            path=(("chapter", "6"),),
            special=FacetKind.HEADING,
        ),
    )

    findings = payload_realization_findings(
        resolved_ops=(op,),
        after_ir=_after("The folded statute says owned chapter heading."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_does_not_require_body_text_for_missing_heading_facet() -> None:
    op = ResolvedOp(
        op=AmendmentOp(
            op_id="op1",
            op_type="REPLACE",
            target_chapter="6",
            target_unit_kind="chapter",
        ),
        muutos_ir=IRNode(
            kind=IRNodeKind.CHAPTER,
            label="6",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Unowned body text should not be audited as heading payload.",
                ),
            ),
        ),
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="6",
        op_id="op1",
        _op_type_seed="REPLACE",
        _target_address_override=LegalAddress(
            path=(("chapter", "6"),),
            special=FacetKind.HEADING,
        ),
    )

    findings = payload_realization_findings(
        resolved_ops=(op,),
        after_ir=_after("The folded statute has unrelated text."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_ignores_unclaimed_source_body_context() -> None:
    findings = payload_realization_findings(
        resolved_ops=(),
        after_ir=_after("The parent statute body is unchanged."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_ignores_non_realizing_actions() -> None:
    findings = payload_realization_findings(
        resolved_ops=(
            _resolved(
                "Repealed source text should disappear from the folded statute.",
                op_type="REPEAL",
            ),
            _resolved(
                "Renumbered source text is not a new realization payload.",
                op_type="RENUMBER",
            ),
        ),
        after_ir=_after("The folded statute no longer contains those source texts."),
        amendment_id="2000/1",
    )

    assert findings == ()
