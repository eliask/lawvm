"""Whole-tree apply-authority closure sweeps (audit-registry wave 2).

The wave-1 EV-05/FW-01 per-op gate proves every op that landed a write resolved
an :class:`ExecutionAuthorization`. The OPEN residual the registry names is the
post-hoc WHOLE-TREE closure: over the finished materialized replay-fold tree,
assert that EVERY node which carries a surface-origin or overlay-origin
provenance marker either (a) carries a typed execution-authorization promotion
witness, or (b) is typed ``surface_only`` (non-replay-authoritative). A surface
or overlay node minting replay authority WITHOUT a promotion witness is the
firewall hole.

HONEST SCOPE (the load-bearing finding for FW-01/OV-01/OV-02): the FI replay-fold
IR tree carries NO surface-origin or overlay-origin provenance markers today —
every node in it descends from an execution-authorized apply op (the per-op
EV-05/FW-01 gate already polices that ingress), and the LegalSurfaceGraph
(where ``surface_only`` / ``replay_authorized`` actually live) is a SEPARATE plane
that never feeds the replay tree. So the closure's totality is trivially met on
the production corpus: ZERO surface/overlay-marked nodes exist, so ZERO mint
unauthorized replay authority (0-delta, every replay product builds clean).

The sweep is nonetheless production-wired at ``ReplayProducts.__post_init__`` (the
central seal every replay product passes through, beside the LS-11 lineage
acyclicity guard) so that the day a provider/overlay node DOES reach the replay
tree (the Pro extensibility overlay seam), it cannot silently mint replay
authority — the closure fails loud. The synthetic guard-liveness drill forges a
surface/overlay-marked node into a ``ReplayProducts`` build to exercise the live
sweep.

Markers (IR ``attrs`` keys) the sweep recognizes:

* ``lawvm_surface_only`` — a node tagged as surface-plane-originated. It MUST NOT
  be replay-authoritative; a surface_only node with ``lawvm_replay_authorized``
  truthy is the FW-01 violation.
* ``lawvm_overlay_origin`` — a node promoted from a provider/registry overlay. If
  it is ``lawvm_replay_authorized`` it MUST carry a typed promotion witness
  (``lawvm_overlay_promotion_event``) — else OV-01; and that promotion MUST cite
  provider_id+model_version OR registry_version+entry_id — else OV-02.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from lawvm.core.ir import IRNode

# IR attrs markers (see module docstring).
SURFACE_ONLY_ATTR = "lawvm_surface_only"
OVERLAY_ORIGIN_ATTR = "lawvm_overlay_origin"
REPLAY_AUTHORIZED_ATTR = "lawvm_replay_authorized"
OVERLAY_PROMOTION_EVENT_ATTR = "lawvm_overlay_promotion_event"
OVERLAY_PROMOTION_PROVIDER_ID_ATTR = "lawvm_overlay_provider_id"
OVERLAY_PROMOTION_MODEL_VERSION_ATTR = "lawvm_overlay_model_version"
OVERLAY_PROMOTION_REGISTRY_VERSION_ATTR = "lawvm_overlay_registry_version"
OVERLAY_PROMOTION_ENTRY_ID_ATTR = "lawvm_overlay_entry_id"


class SurfaceAuthorityClosureError(AssertionError):
    """A surface-origin node in the replay tree minted replay authority (FW-01).

    Self-evidencing: ``offending`` carries the node kind/label and the offending
    attrs so the failure names the exact node, not an opaque message.
    """

    def __init__(self, message: str, *, offending: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.offending = dict(offending)


class OverlayPromotionClosureError(AssertionError):
    """An overlay-origin node was replay-authorized without a complete promotion.

    OV-01: replay_authorized overlay node with no typed promotion event.
    OV-02: a promotion event that does not cite provider_id+model_version OR
    registry_version+entry_id.
    """

    def __init__(self, message: str, *, code: str, offending: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.offending = dict(offending)


# Registered finding codes (core/observation_registry.py). Referenced at the emit
# site here so the registry/producer-consistency gate finds a real producer; the
# blocking surface for these is the raised closure error, the registry entry pins
# their role/enforcement.
SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED_CODE = "FW.SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED"
OVERLAY_REPLAY_AUTHORIZED_WITHOUT_PROMOTION_CODE = "OVERLAY.REPLAY_AUTHORIZED_WITHOUT_PROMOTION"
OVERLAY_PROMOTION_WITNESS_INCOMPLETE_CODE = "OVERLAY.PROMOTION_WITNESS_INCOMPLETE"


def _iter_nodes(node: IRNode) -> Iterator[IRNode]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _truthy_attr(attrs: Mapping[str, Any], key: str) -> bool:
    value = attrs.get(key)
    return bool(value) and str(value).strip().lower() not in ("", "0", "false")


def assert_tree_authority_closure(materialized_ir: IRNode) -> None:
    """FW-01 / OV-01 / OV-02 whole-tree apply-authority closure sweep.

    Raises :class:`SurfaceAuthorityClosureError` on a surface-origin node minting
    replay authority (FW-01) and :class:`OverlayPromotionClosureError` on an
    overlay-origin node that is replay-authorized without a complete typed
    promotion witness (OV-01/OV-02). On the production FI corpus no node carries
    these markers, so the sweep is a no-op and every replay product builds clean.
    """
    for node in _iter_nodes(materialized_ir):
        attrs = node.attrs
        if not attrs:
            continue
        replay_authorized = _truthy_attr(attrs, REPLAY_AUTHORIZED_ATTR)

        # FW-01: a surface_only node must never be replay-authoritative.
        if _truthy_attr(attrs, SURFACE_ONLY_ATTR) and replay_authorized:
            offending = {
                "kind": str(node.kind),
                "label": node.label or "",
                "code": SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED_CODE,
                SURFACE_ONLY_ATTR: attrs.get(SURFACE_ONLY_ATTR),
                REPLAY_AUTHORIZED_ATTR: attrs.get(REPLAY_AUTHORIZED_ATTR),
            }
            raise SurfaceAuthorityClosureError(
                f"{SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED_CODE}: surface-origin "
                f"node {node.kind}:{node.label or ''} in the materialized replay "
                "tree minted replay authority with no typed ExecutionAuthorization "
                "promotion (FW-01 whole-tree closure).",
                offending=offending,
            )

        # OV-01 / OV-02: an overlay-origin node that is replay-authorized must
        # carry a complete typed promotion witness.
        if _truthy_attr(attrs, OVERLAY_ORIGIN_ATTR) and replay_authorized:
            _assert_overlay_promotion_complete(node, attrs)


def _assert_overlay_promotion_complete(node: IRNode, attrs: Mapping[str, Any]) -> None:
    """OV-01 + OV-02 closure for one replay-authorized overlay node."""
    base_offending = {
        "kind": str(node.kind),
        "label": node.label or "",
        OVERLAY_ORIGIN_ATTR: attrs.get(OVERLAY_ORIGIN_ATTR),
    }
    # OV-01: a typed promotion event must exist.
    if not _truthy_attr(attrs, OVERLAY_PROMOTION_EVENT_ATTR):
        raise OverlayPromotionClosureError(
            f"{OVERLAY_REPLAY_AUTHORIZED_WITHOUT_PROMOTION_CODE}: overlay-origin "
            f"node {node.kind}:{node.label or ''} is replay-authorized with no "
            "typed promotion event + witness (OV-01).",
            code=OVERLAY_REPLAY_AUTHORIZED_WITHOUT_PROMOTION_CODE,
            offending=base_offending,
        )
    # OV-02: the promotion must cite provider_id+model_version (LLM) OR
    # registry_version+entry_id (registry).
    cites_provider = _truthy_attr(
        attrs, OVERLAY_PROMOTION_PROVIDER_ID_ATTR
    ) and _truthy_attr(attrs, OVERLAY_PROMOTION_MODEL_VERSION_ATTR)
    cites_registry = _truthy_attr(
        attrs, OVERLAY_PROMOTION_REGISTRY_VERSION_ATTR
    ) and _truthy_attr(attrs, OVERLAY_PROMOTION_ENTRY_ID_ATTR)
    if cites_provider or cites_registry:
        return
    raise OverlayPromotionClosureError(
        f"{OVERLAY_PROMOTION_WITNESS_INCOMPLETE_CODE}: overlay-origin node "
        f"{node.kind}:{node.label or ''} carries a promotion event that does not "
        "cite provider_id+model_version OR registry_version+entry_id (OV-02).",
        code=OVERLAY_PROMOTION_WITNESS_INCOMPLETE_CODE,
        offending={
            **base_offending,
            OVERLAY_PROMOTION_PROVIDER_ID_ATTR: attrs.get(OVERLAY_PROMOTION_PROVIDER_ID_ATTR),
            OVERLAY_PROMOTION_MODEL_VERSION_ATTR: attrs.get(OVERLAY_PROMOTION_MODEL_VERSION_ATTR),
            OVERLAY_PROMOTION_REGISTRY_VERSION_ATTR: attrs.get(OVERLAY_PROMOTION_REGISTRY_VERSION_ATTR),
            OVERLAY_PROMOTION_ENTRY_ID_ATTR: attrs.get(OVERLAY_PROMOTION_ENTRY_ID_ATTR),
        },
    )
