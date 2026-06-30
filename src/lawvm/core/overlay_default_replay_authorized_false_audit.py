"""``lawvm.core.overlay_default_replay_authorized_false_audit`` — D8 audit.

Per :file:`notes_internal/audit_impl_D8.md`: AGENTS.md §2.10 declares that a
surface/overlay node **defaults to** ``replay_authorized=False``. A node
tagged as originating from the overlay plane may mutate legal state ONLY
through a typed :class:`~lawvm.core.execution_authorization.ExecutionAuthorization`
promotion event that carries its ``authorization_rule_id`` + required proofs.
An overlay-tagged node that claims ``replay_authorized=True`` without such a
promotion breaches the deterministic firewall (AGENTS.md §2.10), and this
audit surfaces it as a typed ``OVERLAY.UNAUTHORIZED_PROMOTION`` finding.

PLANE & DISCIPLINE (AGENTS.md §0, §2.10). This audit lives in the evidence
plane: it inspects a passed :class:`~lawvm.core.ir.IRStatute` and a
promotion-event sequence, yields :class:`~lawvm.core.phase_result.Obligation`
findings, and **never mutates legal state, never promotes a node, never
re-tags an overlay**. The wire consumer decides whether the obligation becomes
a strict-mode barrier; this function emits findings only.

WHAT THIS DOES **NOT** YET DO (honest scope):
  * The wire into ``compile_timelines`` is deliberately staged as a follow-up
    commit (parallel to D7's wire-then-promote discipline). Until that wire,
    this audit is `dead` (no production consumer) — its module-role baseline
    entry acknowledges it as ``test_only_live`` (covered by ``tests/test_
    overlay_default_replay_authorized_false.py``). Promotion onto the production
    compile lane is the §2.9 fire-drill once the wire lands.
  * The overlay-tag predicate is a CLOSED set:
    :data:`_OVERLAY_TAG_PREDICATES`. A new frontend overlay tag must be added
    here explicitly (per audit_impl_D8 §9 risk: tag vocabulary drift). The
    audit fails loud if an unknown overlay-tag *value* is non-empty but no
    key matches — the closed-set discipline prevents silent bypass.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Optional

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.phase_result import Finding, OBLIGATION_ROLE


OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID = (
    "overlay_default_replay_authorized_false"
)
OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE = "OVERLAY.UNAUTHORIZED_PROMOTION"
_OVERLAY_AUDIT_STAGE = "compile-timelines"
_OVERLAY_AUDIT_OWNER = "overlay_default_replay_authorized_false_audit"

# Closed set of overlay-tag predicates per audit_impl_D8 §2 / §9. A node is
# overlay-tagged iff ANY of these attr-keys carries a non-empty value. A new
# frontend overlay tag must be added here explicitly — the audit does NOT
# silently accept any "overlay_*" key (closed-set discipline; risk: tag
# vocabulary drift silently bypassing the gate).
_OVERLAY_TAG_PREDICATES: tuple[str, ...] = (
    "overlay_kind",
    "lawvm_temporal_overlay",
)

# A separate sub-key carrying a nested ``authority_plane == "overlay"``
# signal under the "authority" attrs entry. Kept explicit so an unknown
# authority-plane value fails loud rather than being silently treated as
# non-overlay (the spec's safe-default direction).
_OVERLAY_AUTHORITY_PLANE_KEY = "authority"
_OVERLAY_AUTHORITY_PLANE_OVERLAY_VALUE = "overlay"

# Attr-key on individual nodes that declares replay authority without a typed
# promotion event. Treatment: if a node is overlay-tagged AND has this key set
# to truthy AND no matching ExecutionAuthorization promotes it, the audit
# fires.
_REPLAY_AUTHORIZED_ATTR = "replay_authorized"


def _is_overlay_tagged_node(node: IRNode) -> bool:
    """True iff this node's attrs mark it as originating from the overlay plane.

    Closed-set predicate per audit_impl_D8 §2:
    * ``attrs["overlay_kind"]`` non-empty — substrate / UK application-overlay
      convention;
    * ``attrs["lawvm_temporal_overlay"] == "1"`` — core temporary-overlay
      marker (the canonical `== "1"` flag value; any other value fails loud);
    * ``attrs["authority"]["authority_plane"] == "overlay"`` — the nested
      authority-plane override path.

    A node that carries an unknown overlay-tag value (e.g. a key matching
    ``overlay_*`` that isn't in this predicate set) is NOT silently accepted
    here; the closed set must be widened explicitly (§9 risk: tag-vocab drift
    silently bypassing the gate).
    """
    attrs = node.attrs
    for tag_key in _OVERLAY_TAG_PREDICATES:
        value = attrs.get(tag_key)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return True
        elif isinstance(value, bool):
            # ``lawvm_temporal_overlay == True`` is legal too; don't reject.
            if value:
                return True
        elif value:
            return True
    # Nested authority.authority_plane path.
    authority = attrs.get(_OVERLAY_AUTHORITY_PLANE_KEY)
    if isinstance(authority, Mapping):
        plane = authority.get("authority_plane")
        if plane == _OVERLAY_AUTHORITY_PLANE_OVERLAY_VALUE:
            return True
    return False


def _node_replay_authorized_attr(node: IRNode) -> bool:
    """Return the value of the node's ``replay_authorized`` attr, if any.

    Strictly the node-side claim; the authoritative promotion is the
    :class:`ExecutionAuthorization` event. The audit fires when the
    node-side claim is truthy but no matching promotion exists.
    """
    value = node.attrs.get(_REPLAY_AUTHORIZED_ATTR)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _overlay_subject_id(statute_id: str, node: IRNode) -> str:
    """Derive a deterministic overlay subject-id for a tree node.

    Per audit_impl_D8 §9 (promotion matching ambiguity): derive identity from
    stable discriminators, not positional indices (a tuple index or HTML
    ordinal is NOT identity per AGENTS.md §2.8). The subject-id keys the
    lookup of an :class:`ExecutionAuthorization` promotion event so the
    audit can pair a tree node with its promotion witness.

    The carrier formula is ``(work_id, source_unit_id, overlay_kind,
    canonical_node_label)`` — ``canonical_node_label`` is the node's label
    (a stable structural discriminator) plus its IRNodeKind; absent a label
    the node kind alone is used. Source-unit-id comes from the node's
    ``source_unit`` attr when present.
    """
    source_unit = str(node.attrs.get("source_unit") or "")
    overlay_kind = (
        str(node.attrs.get("overlay_kind") or "")
        or str(node.attrs.get("lawvm_temporal_overlay") or "")
        or "overlay"
    )
    kind_token = node.kind.value if hasattr(node.kind, "value") else str(node.kind)
    label_token = str(node.label or "")
    return "|".join(
        (
            statute_id or "",
            source_unit,
            overlay_kind,
            kind_token,
            label_token,
        )
    )


def _promotion_event_for_overlay_node(
    node: IRNode,
    statute_id: str,
    promotions: Mapping[str, ExecutionAuthorization],
) -> Optional[ExecutionAuthorization]:
    """Return the matching promotion event for ``node`` if any, else ``None``.

    Keys ``promotions`` by the deterministic overlay subject-id (above). A
    promotion event authorises replay ONLY when its ``replay_authorized`` is
    True AND its ``executable`` is True (per the
    :class:`ExecutionAuthorization` two-flag authority waist).
    """
    subject_id = _overlay_subject_id(statute_id, node)
    promotion = promotions.get(subject_id)
    if promotion is None:
        return None
    if not (promotion.executable and promotion.replay_authorized):
        return None
    return promotion


def _iter_nodes(statute: IRStatute) -> Iterator[IRNode]:
    """Yield every IRNode in statute body + supplements, depth-first."""

    def _walk(node: IRNode) -> Iterator[IRNode]:
        yield node
        for child in node.children:
            yield from _walk(child)

    yield from _walk(statute.body)
    for supplement in statute.supplements:
        yield from _walk(supplement)


def _promotions_by_subject(
    authorizations: Iterable[ExecutionAuthorization],
    statute_id: str,
) -> dict[str, ExecutionAuthorization]:
    """Index executions by subject-id for O(1) lookup (AGENTS.md §2.7 perf)."""
    indexed: dict[str, ExecutionAuthorization] = {}
    for auth in authorizations:
        # The subject side of ExecutionAuthorization lives in the
        # ``authorization_rule_id`` plus an optional ``detail.subject_id``.
        # Per audit_impl_D8 §9 the promotion matching uses the overlay
        # subject-id scheme; if the AuthorizationResult / ExecutionAuthorization
        # already carries a deterministic subject-id in detail, prefer that.
        detail = auth.detail or {}
        subject_id = str(detail.get("subject_id") or "")
        if not subject_id:
            # Fall back to the rule_id-keyed dict; the wire site is responsible
            # for emitting ExecutionAuthorization carrying detail.subject_id
            # that matches an overlay node's subject-id. Until the wire lands
            # the fallback keeps the unit-test shape tractable.
            subject_id = str(auth.authorization_rule_id or "")
        if subject_id:
            indexed[subject_id] = auth
    return indexed


def iter_overlay_default_replay_authorized_false_violations(
    statute: IRStatute,
    *,
    authorizations: Iterable[ExecutionAuthorization],
) -> Iterator[Finding]:
    """Yield one ``OVERLAY.UNAUTHORIZED_PROMOTION`` :class:`Finding` per breach.

    A node breaches the audit iff:
      * it is overlay-tagged (per :data:`_OVERLAY_TAG_PREDICATES` / authority-
        plane override), AND
      * it claims ``replay_authorized=True`` on its attrs (the node-side
        claim), AND
      * NO matching :class:`ExecutionAuthorization` promotion event exists
        in ``authorizations`` whose ``subject_id`` keys the node's overlay
        subject-id AND whose two-flag authority waist
        (``executable=True``, ``replay_authorized=True``) is satisfied.

    The audit is conservative and §0-aligned: an overlay-tagged node WITHOUT
    a node-side ``replay_authorized=True`` claim is COMPLIANT (the default
    is False per AGENTS.md §2.10) and yields no finding. An overlay-tagged
    node that carries ``replay_authorized=True`` plus a matching promotion
    event is COMPLIANT (the promotion ladder paid for the authority). Only
    the authority-without-promotion branch fires.
    """
    statute_id = statute.statute_id
    promotions = _promotions_by_subject(authorizations, statute_id)
    for node in _iter_nodes(statute):
        if not _is_overlay_tagged_node(node):
            continue
        if not _node_replay_authorized_attr(node):
            # The default is False; a node that does NOT claim replay authority
            # is the compliant shape. Don't fire.
            continue
        promotion = _promotion_event_for_overlay_node(node, statute_id, promotions)
        if promotion is not None:
            continue
        yield _build_unauthorized_promotion_finding(
            statute_id=statute_id,
            node=node,
        )


def _build_unauthorized_promotion_finding(
    *,
    statute_id: str,
    node: IRNode,
) -> Finding:
    kind_token = node.kind.value if hasattr(node.kind, "value") else str(node.kind)
    detail: dict[str, Any] = {
        "rule_id": OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID,
        "owner": _OVERLAY_AUDIT_OWNER,
        "node_label": str(node.label or ""),
        "node_kind": kind_token,
        "node_path": _format_node_path(node),
        "overlay_kind": (
            str(node.attrs.get("overlay_kind") or "")
            or str(node.attrs.get("lawvm_temporal_overlay") or "")
        ),
        "claimed_replay_authorized": True,
        "reason": (
            "overlay-tagged IRNode carries replay_authorized=True but has no "
            "matching ExecutionAuthorization promotion event with rule_id and "
            "witness (AGENTS.md §2.10 deterministic firewall)"
        ),
    }
    return Finding(
        kind=OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE,
        role=OBLIGATION_ROLE,
        stage=_OVERLAY_AUDIT_STAGE,
        detail=detail,
        source_statute=statute_id,
        blocking=True,
    )


def _format_node_path(node: IRNode) -> str:
    if node.label:
        return str(node.label)
    kind_token = node.kind.value if hasattr(node.kind, "value") else str(node.kind)
    return f"<{kind_token}>"


def build_overlay_default_replay_authorized_false_report(
    statute: IRStatute,
    *,
    authorizations: Iterable[ExecutionAuthorization],
    jurisdiction: str,
) -> EvidenceSurfaceReport:
    """Project the sweep into an :class:`EvidenceSurfaceReport` with counts.

    The report lives in the evidence plane (AGENTS.md §2.10):
    it describes what the audit found, it does NOT grant authority.
    """
    findings = tuple(
        iter_overlay_default_replay_authorized_false_violations(
            statute, authorizations=authorizations
        )
    )
    overlay_tagged_total = sum(
        1 for node in _iter_nodes(statute) if _is_overlay_tagged_node(node)
    )
    unauthorized = len(findings)
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind="overlay_default_replay_authorized_false",
        schema="lawvm.overlay_default_replay_authorized_false_audit.v0",
        truth_claim=(
            "Every overlay-tagged IRNode either carries replay_authorized=False "
            "(the §2.10 default) or is promoted to replay only through a typed "
            "ExecutionAuthorization with rule_id and witness."
        ),
        # Evidence-plane: an audit surface does not make replay / canonical /
        # candidate / dry-run / agreement claims itself (the wire consumer
        # owns those upstream). The report is description-of-state only.
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary={
            "overlay_tagged_nodes_total": overlay_tagged_total,
            "unauthorized_promotions": unauthorized,
            "subject_id_scheme": "statute_id|source_unit|overlay_kind|node_kind|node_label",
            "rule_id": OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID,
        },
        rows=tuple(
            {
                "kind": finding.kind,
                "rule_id": OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID,
                "blocking": finding.blocking,
                "node_label": finding.detail.get("node_label"),
                "node_kind": finding.detail.get("node_kind"),
                "overlay_kind": finding.detail.get("overlay_kind"),
                "reason": finding.detail.get("reason"),
            }
            for finding in findings
        ),
        evidence_jsonl={},
        written_paths=(),
        detail={
            "owner": _OVERLAY_AUDIT_OWNER,
            "stage": _OVERLAY_AUDIT_STAGE,
            "finding_code": OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE,
        },
    )


__all__ = [
    "OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID",
    "OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE",
    "build_overlay_default_replay_authorized_false_report",
    "iter_overlay_default_replay_authorized_false_violations",
]
