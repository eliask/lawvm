"""PrecedenceRegistry — typed loader for claim precedence config.

DEPRECATED: claim_precedence.yaml is superseded by EvidencePolicyRegistry
(v3 design §4, data/fi/v1/evidence_policy/*.json).  This module is retained
for one transition release.  New code should use evidence_policy.py directly.

Per §5 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2:
  Precedence is by claim layer + validator status + source-witness type.
  NOT by "human is more authoritative than source law".

When multiple competing claims target the same projection slot:
  - Apply precedence rule per layer.
  - Single winner → emit it.
  - Still ambiguous → emit AmbiguousClaimSet finding; strict-mode REJECTS the row.

Design: fail loud on missing or malformed file (AGENTS.md §1.10: no broad try/except).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import yaml  # operator-authored config boundary


# ---------------------------------------------------------------------------
# Typed registry model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerPrecedenceRule:
    """One layer's precedence rule from claim_precedence.yaml."""
    layer: str
    rule: str
    rationale: str


@dataclass(frozen=True, slots=True)
class PrecedenceRegistry:
    """Loaded operator-authored precedence rules for claim composition."""
    rules: Tuple[LayerPrecedenceRule, ...]
    source_path: str
    """Path to the YAML file this registry was loaded from."""


# ---------------------------------------------------------------------------
# Precedence ordering tables (derived from the YAML rules at load time)
# ---------------------------------------------------------------------------


# Extraction-layer precedence: validator_status ordering (lower index = higher precedence)
_EXTRACTION_VALIDATOR_ORDER: Tuple[str, ...] = (
    "entailment_verified",
    "span_verified",
    "migration_revalidated",
    "unvalidated",
)

# Substrate-layer precedence: source_witness_type ordering
_SUBSTRATE_WITNESS_ORDER: Tuple[str, ...] = (
    "finlex_corrigendum",
    "finlex_akn",
    "external_archival",
    "operator_filing",
    "llm_proposal",
)


# ---------------------------------------------------------------------------
# AmbiguousClaimSet finding
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AmbiguousClaimSet:
    """Emitted when multiple competing claims cannot be resolved by precedence.

    Per §5: strict-mode profiles REJECT the affected projection row when this
    fires. The row is NOT emitted; this finding is instead.
    """
    target_key: str
    """The projection slot (e.g. 'target_statute_id_str') that is ambiguous."""
    competing_claim_ids: Tuple[str, ...]
    competing_values: Tuple[str, ...]
    layer: str
    reason: str


# ---------------------------------------------------------------------------
# Precedence resolution
# ---------------------------------------------------------------------------


def resolve_precedence(
    claims_with_values: Sequence[Tuple[str, str, str, str]],
    layer: str,
    registry: PrecedenceRegistry,
) -> Tuple[Optional[str], Optional[AmbiguousClaimSet]]:
    """Resolve competing claims for a single projection slot by precedence rules.

    Args:
        claims_with_values: Sequence of (claim_id, value, validator_status, source_witness_type).
        layer: "extraction" | "substrate" | "correction" | "adjudication".
        registry: Loaded PrecedenceRegistry.

    Returns:
        (winning_claim_id, None) if a single winner is resolved.
        (None, AmbiguousClaimSet) if still ambiguous after applying rules.
        (None, None) if claims_with_values is empty.
    """
    if not claims_with_values:
        return None, None

    if len(claims_with_values) == 1:
        return claims_with_values[0][0], None

    # Collect unique values; if all claims agree, pick first (no real ambiguity)
    values = {v for _, v, _, _ in claims_with_values}
    if len(values) == 1:
        return claims_with_values[0][0], None

    # Apply layer-specific precedence ordering
    if layer == "extraction":
        def _rank(item: Tuple[str, str, str, str]) -> int:
            _, _, vs, _ = item
            try:
                return _EXTRACTION_VALIDATOR_ORDER.index(vs)
            except ValueError:
                return len(_EXTRACTION_VALIDATOR_ORDER)

        ranked = sorted(claims_with_values, key=_rank)
        best_rank = _rank(ranked[0])
        top_tier = [c for c in ranked if _rank(c) == best_rank]
        if len(top_tier) == 1:
            return top_tier[0][0], None
        # Still ambiguous at same validator rank
        claim_ids = tuple(c[0] for c in top_tier)
        competing_vals = tuple(c[1] for c in top_tier)
        return None, AmbiguousClaimSet(
            target_key="",
            competing_claim_ids=claim_ids,
            competing_values=competing_vals,
            layer=layer,
            reason=f"Multiple claims with equal validator_status={ranked[0][2]!r}",
        )

    elif layer == "substrate":
        def _swrank(item: Tuple[str, str, str, str]) -> int:
            _, _, _, sw = item
            try:
                return _SUBSTRATE_WITNESS_ORDER.index(sw)
            except ValueError:
                return len(_SUBSTRATE_WITNESS_ORDER)

        ranked = sorted(claims_with_values, key=_swrank)
        best_rank = _swrank(ranked[0])
        top_tier = [c for c in ranked if _swrank(c) == best_rank]
        if len(top_tier) == 1:
            return top_tier[0][0], None
        claim_ids = tuple(c[0] for c in top_tier)
        competing_vals = tuple(c[1] for c in top_tier)
        return None, AmbiguousClaimSet(
            target_key="",
            competing_claim_ids=claim_ids,
            competing_values=competing_vals,
            layer=layer,
            reason=f"Multiple claims with equal source_witness_type={ranked[0][3]!r}",
        )

    # Correction and adjudication: no auto-resolution (bulk forbidden per §5)
    claim_ids = tuple(c[0] for c in claims_with_values)
    competing_vals = tuple(c[1] for c in claims_with_values)
    return None, AmbiguousClaimSet(
        target_key="",
        competing_claim_ids=claim_ids,
        competing_values=competing_vals,
        layer=layer,
        reason=f"layer={layer!r} does not allow auto-resolution",
    )


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def load_precedence_registry(yaml_path: Path) -> PrecedenceRegistry:
    """Load and parse claim_precedence.yaml into a typed PrecedenceRegistry.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the file is malformed or missing required fields.
    """
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"claim_precedence.yaml not found at {yaml_path}. "
            "This file is required for claim composition. "
            "Create it under data/fi/v1/claim_precedence.yaml."
        )

    raw = yaml_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)

    if not isinstance(parsed, list):
        raise ValueError(
            f"claim_precedence.yaml must be a YAML list of rule objects, got {type(parsed).__name__!r}"
        )

    rules: List[LayerPrecedenceRule] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"Rule at index {i} must be a YAML dict, got {type(item).__name__!r}")
        required = ("layer", "rule", "rationale")
        missing = [k for k in required if k not in item]
        if missing:
            raise ValueError(f"Rule at index {i} missing required keys: {missing!r}")
        rules.append(LayerPrecedenceRule(
            layer=str(item["layer"]),
            rule=str(item["rule"]),
            rationale=str(item["rationale"]),
        ))

    return PrecedenceRegistry(
        rules=tuple(rules),
        source_path=str(yaml_path),
    )
