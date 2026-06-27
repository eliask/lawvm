"""Tests for ``core.overlay_default_replay_authorized_false_audit`` (D8).

Per :file:`notes_internal/audit_impl_D8.md` §6 — four synthetic cases:

* firing: an overlay-tagged node carrying ``replay_authorized=True`` with an
  empty promotion list yields exactly one ``OVERLAY.UNAUTHORIZED_PROMOTION``
  finding with ``blocking=True``;
* negative promotion-attached: the same node, paired with a matching
  :class:`ExecutionAuthorization`, yields zero findings (the promotion
  ladder paid for the authority);
* negative non-overlay-tagged node predicate: a node without overlay attrs is
  invisible to the audit;
* negative overlay-tagged but compliant-default: an overlay-tagged node
  WITHOUT ``replay_authorized=True`` (i.e. the §2.10 default) yields zero
  findings — the compliant shape.

Honest scope: the production wire into ``compile_timelines`` is staged as a
follow-up commit parallel to D7's wire-then-promote discipline; until the
wire lands, this audit lives off the production lane (its
``module-role baseline`` entry acknowledges it as ``test_only_live``).
"""

from __future__ import annotations

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import IRNode, IRNodeKind, IRStatute
from lawvm.core.overlay_default_replay_authorized_false_audit import (
    OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID,
    OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE,
    build_overlay_default_replay_authorized_false_report,
    iter_overlay_default_replay_authorized_false_violations,
)
from lawvm.core.phase_result import Finding, OBLIGATION_ROLE


def _statute_with_body(*children: IRNode) -> IRStatute:
    body = IRNode(kind=IRNodeKind.BODY, label="root", children=tuple(children))
    return IRStatute(
        statute_id="ukpga/2020/1-overlay-d8",
        title="D8 overlay-default test fixture",
        body=body,
    )


def _overlay_node(
    *,
    label: str = "overlay-target",
    replay_authorized: bool = True,
    overlay_kind: str = "test_overlay",
) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        attrs={
            "overlay_kind": overlay_kind,
            "replay_authorized": replay_authorized,
            "source_unit": "sch-overlay-1",
        },
    )


def _promotion_for(subject_id: str) -> ExecutionAuthorization:
    return ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="promoted",
        authorization_rule_id=subject_id,
        owner_phase="overlay_default_replay_authorized_false_audit_test",
        strict_disposition="block",
        required_proofs=("overlay_promotion_witness",),
        safe_default="skip_replay_until_promotion_explicit",
        detail={"subject_id": subject_id},
    )


# --------------------------------------------------------------------------- #
# Firing case — the load-bearing guard-liveness test.                          #
# --------------------------------------------------------------------------- #


def test_overlay_tagged_node_with_replay_authorized_and_no_promotion_fires() -> None:
    """An overlay-tagged node carrying replay_authorized=True WITHOUT a matching
    promotion event yields exactly one blocking ``OVERLAY.UNAUTHORIZED_PROMOTION``
    finding.

    Drives the audit directly with a single overlay-tagged node whose attrs
    claim ``replay_authorized=True`` and no ExecutionAuthorization promotion in
    its list. The audit MUST fire — this is the §2.10 deterministic-firewall
    breach surface (overlay plane silently mutating legal state without a
    typed promotion root).
    """
    statute = _statute_with_body(_overlay_node())
    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=()
        )
    )
    assert len(findings) == 1, (
        "an overlay-tagged node carrying replay_authorized=True WITHOUT a "
        "matching ExecutionAuthorization promotion MUST fire exactly one "
        f"finding; got {len(findings)}"
    )
    finding = findings[0]
    assert isinstance(finding, Finding)
    assert finding.kind == OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE
    assert finding.role == OBLIGATION_ROLE
    assert finding.blocking is True, (
        "OVERLAY.UNAUTHORIZED_PROMOTION is a strict barrier (default_enforcement"
        "=strict_fail); the finding MUST be blocking"
    )
    assert finding.stage == "compile-timelines"
    assert finding.source_statute == "ukpga/2020/1-overlay-d8"
    detail = finding.detail
    assert detail["rule_id"] == OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID
    assert detail["owner"] == "overlay_default_replay_authorized_false_audit"
    assert detail["node_label"] == "overlay-target"
    assert detail["node_kind"] == "section"
    assert detail["overlay_kind"] == "test_overlay"
    assert detail["claimed_replay_authorized"] is True
    assert "replay_authorized=True" in detail["reason"]
    assert "AGENTS.md §2.10" in detail["reason"]


# --------------------------------------------------------------------------- #
# Negative: attaching a matching ExecutionAuthorization promotion suppresses. #
# --------------------------------------------------------------------------- #


def test_overlay_tagged_node_with_matching_promotion_emits_zero_findings() -> None:
    """A matching ExecutionAuthorization promotion pays for replay authority.

    The promotion ladder is the load-bearing promotion chain per AGENTS.md §0
    (``source witness → candidate claim → execution-authorization status →
    replay proof``). A node paired with a typed promotion whose two-flag
    authority waist (executable=True AND replay_authorized=True) holds is
    compliant and yields zero findings.
    """
    overlay = _overlay_node()
    statute = _statute_with_body(overlay)
    # The promotion's subject_id must match the overlay node's overlay-subject-id.
    # Per _overlay_subject_id: statute_id|source_unit|overlay_kind|node_kind|node_label
    expected_subject_id = (
        "ukpga/2020/1-overlay-d8|sch-overlay-1|test_overlay|section|overlay-target"
    )
    promotion = _promotion_for(expected_subject_id)

    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=(promotion,)
        )
    )
    assert findings == ()


# --------------------------------------------------------------------------- #
# Negative: non-overlay-tagged node is invisible to the audit.                 #
# --------------------------------------------------------------------------- #


def test_non_overlay_tagged_node_is_not_flagged() -> None:
    """A node without overlay attrs is not in the overlay plane — invisible.

    Predicate negative: the audit's overlay-tag predicate must not false-fire
    on ordinary structural nodes that merely have ``replay_authorized=True``
    set on their attrs (an env-owned truthy signal without an overlay_kind
    is a structural node, not an overlay node). Closed-set discipline.
    """
    plain = IRNode(
        kind=IRNodeKind.SECTION,
        label="plain-section",
        attrs={"replay_authorized": True, "source_unit": "sch-1"},
    )
    statute = _statute_with_body(plain)
    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=()
        )
    )
    assert findings == ()


# --------------------------------------------------------------------------- #
# Negative: overlay-tagged but compliant default (no node-side claim).        #
# --------------------------------------------------------------------------- #


def test_overlay_tagged_node_without_replay_authorized_claim_emits_zero_findings() -> None:
    """An overlay-tagged node with the §2.10 default (replay_authorized=False)
    is the compliant shape — never flagged.

    AGENTS.md §2.10: surface/overlay node DEFAULTS to replay_authorized=False.
    A node that does NOT claim replay authority is compliant by default; only
    a node that claims authority WITHOUT a promotion event breaches the
    firewall.
    """
    overlay_default = _overlay_node(replay_authorized=False)
    statute = _statute_with_body(overlay_default)
    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=()
        )
    )
    assert findings == ()


# --------------------------------------------------------------------------- #
# Discriminators — multiple-overlay-node sweep + report projection.            #
# --------------------------------------------------------------------------- #


def test_sweep_over_multiple_overlay_nodes_emits_one_finding_per_breach_in_order() -> None:
    """A multi-node statute surfaces exactly the breaches, in DFS-body order.

    Mixing compliant-promoted + compliant-default + breach shapes in one body
    surfaces exactly the breaches. The audit does NOT reorder, dedupe, or
    collapse findings — §1.8 receipt accounting: every breached node is
    owned, none silently dropped.
    """
    breach_a = _overlay_node(label="breach-a")
    promoted_overlay = _overlay_node(label="promoted-overlay")
    default_overlay = _overlay_node(label="default-overlay", replay_authorized=False)
    breach_b = _overlay_node(label="breach-b")
    statute = IRStatute(
        statute_id="ukpga/2020/1-overlay-d8-multi",
        title="D8 overlay-default multi-node fixture",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label="root",
            children=(breach_a, promoted_overlay, default_overlay, breach_b),
        ),
    )
    promoted_subject = "ukpga/2020/1-overlay-d8-multi|sch-overlay-1|test_overlay|section|promoted-overlay"
    promotions = (_promotion_for(promoted_subject),)

    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=promotions
        )
    )
    assert [f.detail["node_label"] for f in findings] == ["breach-a", "breach-b"]
    assert all(
        f.kind == OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE for f in findings
    )
    assert all(f.blocking for f in findings)


def test_build_report_carries_summary_counts_and_rows() -> None:
    """The EvidenceSurfaceReport projection carries counts + typed rows.

    The report lives in the evidence plane (AGENTS.md §2.10):
    it describes the audit's state-of-the-world, makes NO replay/canonical/
    candidate/dry-run/agreement claims itself. ``schema`` names the audit
    version; ``summary`` carries the load-bearing counts a triager needs.
    """
    breach = _overlay_node()
    statute = _statute_with_body(breach)
    report = build_overlay_default_replay_authorized_false_report(
        statute,
        authorizations=(),
        jurisdiction="uk",
    )
    assert report.jurisdiction == "uk"
    assert report.report_kind == "overlay_default_replay_authorized_false"
    assert report.schema.startswith("lawvm.overlay_default_replay_authorized_false")
    assert report.replay_claims is False
    assert report.canonical_effect_claims is False
    assert report.candidate_effect_claims is False
    assert report.dry_run_claims is False
    assert report.agreement_claims is False
    # One overlay-tagged node total (the one breach) — counted via predicate.
    assert report.summary["overlay_tagged_nodes_total"] == 1
    assert report.summary["unauthorized_promotions"] == 1
    assert report.summary["rule_id"] == OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row["kind"] == OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE
    assert row["blocking"] is True
    assert row["node_label"] == "overlay-target"


# --------------------------------------------------------------------------- #
# Discriminators — the closed overlay-tag set is load-bearing.                #
# --------------------------------------------------------------------------- #


def test_lawvm_temporal_overlay_attr_marker_flags_overlay_node() -> None:
    """The canonical core ``lawvm_temporal_overlay`` marker is also overlay-tag.

    The closed predicate set includes ``lawvm_temporal_overlay`` (core temp-
    overlay marker). A node carrying this attr + ``replay_authorized=True``
    WITHOUT a promotion event fires the same way as the
    ``overlay_kind=...``UK/substrate convention path.
    """
    overlay_temporal = IRNode(
        kind=IRNodeKind.SECTION,
        label="temp-overlay-1",
        attrs={
            "lawvm_temporal_overlay": "1",
            "replay_authorized": True,
            "source_unit": "tmp-overlay",
        },
    )
    statute = _statute_with_body(overlay_temporal)
    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=()
        )
    )
    assert len(findings) == 1
    assert findings[0].detail["overlay_kind"] == "1"


def test_nested_authority_plane_overlay_marker_flags_overlay_node() -> None:
    """A node carrying `attrs["authority"]["authority_plane"] == "overlay"` is overlay-tagged.

    The nested authority-plane override path is the third predicate in the
    closed set. Its detection preserves the contract even when a frontend
    sets neither ``overlay_kind`` nor ``lawvm_temporal_overlay`` directly but
    accumulates the overlay signal under the ``authority`` mapping instead.
    """
    overlay_authority_nested = IRNode(
        kind=IRNodeKind.SECTION,
        label="authority-plane-overlay",
        attrs={
            "authority": {"authority_plane": "overlay"},
            "replay_authorized": True,
            "source_unit": "auth-overlay",
        },
    )
    statute = _statute_with_body(overlay_authority_nested)
    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=()
        )
    )
    assert len(findings) == 1
    assert findings[0].detail["node_label"] == "authority-plane-overlay"
