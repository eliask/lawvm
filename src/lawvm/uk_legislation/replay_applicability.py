"""UK replay applicability predicates."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
    uk_nonstructural_replay_candidate_family,
)
from lawvm.uk_legislation.effect_substitution_normalization import (
    UK_SUBSTITUTED_SOURCE_OWNED_INSERT_RULE_IDS,
)
from lawvm.uk_legislation.effect_temporal_cessation import (
    temporal_ceases_to_have_effect_exclusion_rule_for_ops,
)
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
)


UK_EFFECT_NONSTRUCTURAL_SUBSTITUTED_SERIES_ALL_INSERTS_ADMITTED_RULE_ID = (
    "uk_effect_nonstructural_substituted_series_all_inserts_admitted"
)


def nonstructural_replay_exclusion_rule(
    effect: UKEffectRecord,
    compiled_ops: Sequence[LegalOperation],
) -> str:
    """Return the named rule that excludes a nonstructural row from replay."""
    effect_type = (effect.effect_type or "").strip().lower()
    if not effect_type.startswith("ceases to have effect"):
        return ""
    return temporal_ceases_to_have_effect_exclusion_rule_for_ops(
        effect_type=effect.effect_type,
        compiled_ops=compiled_ops,
    )

def should_replay_nonstructural_ops(
    effect: UKEffectRecord,
    compiled_ops: Sequence[LegalOperation],
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
    lowering_observations_out: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Admit narrow false-negative nonstructural effect-feed rows into replay."""
    if not effect.is_applicable_for_replay(applicability_mode=applicability_mode):
        return False
    effect_type = (effect.effect_type or "").strip().lower()
    if effect_type.startswith("substituted for"):
        if effect_type in {"substituted for word", "substituted for words"}:
            return False
        if not compiled_ops:
            return False
        # §source-backed whole-series insert promotion: a nonstructural feed row
        # labelled "substituted for ss. X-Y" sometimes carries new letter-suffix
        # targets (e.g. 6A-6E) that do not exist in the base statute.  Lowering
        # promotes every target to an after-anchor insert rather than a replace
        # on a missing leaf.  Admit the whole series into replay.
        if all(
            _action_name(op.action) == "insert" and op.payload is not None
            for op in compiled_ops
        ):
            if lowering_observations_out is not None:
                _append_uk_effect_lowering_observation(
                    lowering_observations_out,
                    rule_id=UK_EFFECT_NONSTRUCTURAL_SUBSTITUTED_SERIES_ALL_INSERTS_ADMITTED_RULE_ID,
                    family="replay_applicability",
                    reason_code="nonstructural_substituted_series_all_inserts_admitted",
                    reason=(
                        "Nonstructural UK effect row labelled 'substituted for ...' "
                        "lowered entirely to source-backed after-anchor inserts; "
                        "admitting the whole series into replay rather than under-applying."
                    ),
                    effect=effect,
                    extracted_el=None,
                    extracted_text=None,
                    detail={
                        "compiled_op_count": len(compiled_ops),
                        "compiled_actions": [
                            _action_name(op.action) for op in compiled_ops
                        ],
                        "compiled_targets": [str(op.target) for op in compiled_ops],
                        "strict_disposition": "record",
                        "quirks_disposition": "apply",
                    },
                )
            return True
        head, *tail = compiled_ops
        if _action_name(head.action) != "replace" or head.payload is None:
            return False
        if all(_action_name(op.action) == "replace" and op.payload is not None for op in compiled_ops):
            return True
        if all(
            (
                _action_name(op.action) == "replace"
                and op.payload is not None
            )
            or (
                _action_name(op.action) == "insert"
                and op.payload is not None
                and str(op.witness_rule_id or "")
                in UK_SUBSTITUTED_SOURCE_OWNED_INSERT_RULE_IDS
            )
            or (_action_name(op.action) == "repeal" and op.target.path)
            for op in tail
        ):
            return True
        return all(_action_name(op.action) == "repeal" and op.target.path for op in tail)
    if effect_type.startswith("revoked"):
        return bool(compiled_ops) and all(_action_name(op.action) == "repeal" and op.target.path for op in compiled_ops)
    if effect_type.startswith("ceases to have effect"):
        if nonstructural_replay_exclusion_rule(effect, compiled_ops):
            return False
        return bool(compiled_ops) and all(_action_name(op.action) == "repeal" and op.target.path for op in compiled_ops)
    if effect_type == "added":
        return bool(compiled_ops) and all(
            _action_name(op.action) == "insert" and op.payload is not None
            for op in compiled_ops
        )
    # A "words before the table substitute" schedule paragraph amendment is
    # marked nonstructural by the feed, but the source supplies structural
    # paragraph replacements and sibling inserts. Allow it to replay when
    # lowering has emitted the owned structural operations.
    if effect_type.startswith("substituted") and any(
        str(op.witness_rule_id or "")
        == UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID
        for op in compiled_ops
    ):
        return bool(compiled_ops) and all(
            _action_name(op.action) in {"replace", "insert"}
            and op.payload is not None
            for op in compiled_ops
        )
    if effect_type == "amended":
        return bool(compiled_ops) and all(
            _action_name(op.action) in {"text_replace", "text_repeal"}
            for op in compiled_ops
        )
    return False


def nonstructural_replay_candidate_family(
    effect: UKEffectRecord,
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
) -> str:
    """Return the nonstructural effect row family that may still replay."""
    return uk_nonstructural_replay_candidate_family(
        effect,
        applicability_mode=applicability_mode,
    )
