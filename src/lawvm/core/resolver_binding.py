"""Typed target-binding provenance for the semantic apply plane.

A ResolverBinding records HOW an apply-time target resolution was produced:
which rung of the fallback ladder bound it, how many candidates were in
play, and whether a fallback widened the lookup. It is pre-write authority
over WHERE — it never authorizes replay (that remains the execution
authorization's job) and it never substitutes for the landed-write receipt.

Contract: notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md §2. The normative
replay principle the binding serves: the address you bind is the address
you write, and the address you write is the address you declare.

This module is jurisdiction-neutral. Jurisdiction-specific resolution
ladders (e.g. the Finland scoped-section ladder) map their branches onto
the rung vocabulary below; rung ids are stable serialized values.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Mapping

from lawvm.core.tree_ops import TreePath

ResolverBindingStatus = Literal[
    "resolved",
    "ambiguous",
    "not_found",
    "blocked_by_policy",
]

# Rung vocabulary (contract §2.2). Every rung is a NAMED rule; a bare
# boolean "fallback_used" is forbidden as the load-bearing record. Rungs
# are ordered here from narrowest binding authority to widest.
RUNG_PATH_HINT_VALIDATED = "path_hint_validated"
RUNG_SCOPED_FIND = "scoped_find"
RUNG_MIGRATION_LEDGER_FOLLOW = "migration_ledger_follow"
RUNG_PLACEHOLDER_SHADOW_FALLBACK = "placeholder_shadow_fallback"
RUNG_UNIQUE_GLOBAL_FALLBACK = "unique_global_fallback"
RUNG_UNCOVERED_BODY_AMBIGUITY = "uncovered_body_ambiguity"

KNOWN_RUNG_IDS = frozenset(
    {
        RUNG_PATH_HINT_VALIDATED,
        RUNG_SCOPED_FIND,
        RUNG_MIGRATION_LEDGER_FOLLOW,
        RUNG_PLACEHOLDER_SHADOW_FALLBACK,
        RUNG_UNIQUE_GLOBAL_FALLBACK,
        RUNG_UNCOVERED_BODY_AMBIGUITY,
    }
)

# Rungs that widen lookup beyond the operation's declared scope. A binding
# produced by one of these is a fallback by definition and MUST carry the
# producing rule id in fallback_rule_id.
WIDENING_RUNG_IDS = frozenset(
    {
        RUNG_PLACEHOLDER_SHADOW_FALLBACK,
        RUNG_UNIQUE_GLOBAL_FALLBACK,
        RUNG_MIGRATION_LEDGER_FOLLOW,
    }
)


@dataclass(frozen=True, slots=True)
class ResolverBinding:
    """Provenance record for one apply-time target resolution.

    Passive in the current rollout step: bindings are produced alongside
    the legacy resolution helpers and compared against their outputs; they
    do not yet drive the write. ``candidate_count`` is the number of
    same-kind/label candidates visible to the widest index consulted
    (None when no index was consulted on the taken branch).
    """

    binding_id: str
    op_label: str
    target_text: str
    target_path: TreePath | None
    binding_status: ResolverBindingStatus
    policy_id: str
    rung_id: str | None
    candidate_count: int | None = None
    fallback_used: bool = False
    fallback_rule_id: str | None = None
    rejection_reasons: tuple[str, ...] = ()
    finding_refs: tuple[str, ...] = ()
    detail: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rung_id is not None and self.rung_id not in KNOWN_RUNG_IDS:
            raise ValueError(
                f"ResolverBinding rung_id {self.rung_id!r} is not a known rung; "
                f"register it in lawvm.core.resolver_binding before use"
            )
        if self.binding_status == "resolved" and self.target_path is None:
            raise ValueError("resolved binding requires a target_path")
        if self.binding_status != "resolved" and self.target_path is not None:
            raise ValueError(
                f"{self.binding_status} binding must not carry a target_path"
            )
        if self.rung_id in WIDENING_RUNG_IDS:
            if not self.fallback_used or not self.fallback_rule_id:
                raise ValueError(
                    f"widening rung {self.rung_id!r} requires fallback_used "
                    f"and a named fallback_rule_id"
                )
        if self.fallback_used and not self.fallback_rule_id:
            raise ValueError("fallback_used requires a named fallback_rule_id")


def binding_id_for(
    *,
    op_label: str,
    target_text: str,
    rung_id: str | None,
    target_path: TreePath | None,
) -> str:
    """Stable content-derived binding id."""
    path_text = (
        "/".join(f"{kind}:{label}" for kind, label in target_path)
        if target_path is not None
        else ""
    )
    payload = "\x00".join((op_label, target_text, rung_id or "", path_text))
    return "rb:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
