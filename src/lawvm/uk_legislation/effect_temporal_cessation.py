"""UK temporal cessation effect-family predicates.

These helpers classify source clauses whose effect-feed verb says a provision
``ceases to have effect`` but whose source text qualifies that cessation as a
temporal/application state rather than an unconditional textual repeal.
"""

from __future__ import annotations

from collections.abc import Sequence

from lawvm.core.ir import LegalOperation


UK_TEMPORAL_CEASES_TO_HAVE_EFFECT_REPLAY_EXCLUDED_RULE_ID = (
    "uk_effect_temporal_ceases_to_have_effect_replay_excluded"
)
UK_TEMPORAL_CEASES_TO_HAVE_EFFECT_REPLAY_EXCLUDED_REASON = (
    "UK ceases-to-have-effect source text is temporally qualified, so replay "
    "must not treat it as an unconditional structural repeal."
)

_QUALIFIED_CESSATION_CONTEXT_MARKERS = (
    "subject to article",
    "subject to paragraph",
    "subject to regulation",
    "subject to section",
)
_TEMPORAL_CESSATION_ENDPOINT_MARKERS = (
    "at the end of that day",
    "at the end of the day",
)


def is_uk_ceases_to_have_effect_type(effect_type: str) -> bool:
    """Return whether a UK effects-feed type is the cessation family."""
    return " ".join(str(effect_type or "").strip().lower().split()).startswith(
        "ceases to have effect"
    )


def is_temporally_qualified_ceases_to_have_effect_text(source_text: str) -> bool:
    """Return whether source text describes a qualified temporal cessation.

    The predicate is intentionally narrow. It catches clauses like "subject to
    article 3 ... cease to have effect at the end of that day", where another
    provision owns savings/continuation semantics and unconditional deletion
    would destroy live legal state.
    """
    normalized = " ".join(str(source_text or "").lower().split())
    if not normalized:
        return False
    if "cease to have effect" not in normalized and "ceases to have effect" not in normalized:
        return False
    has_context_qualifier = any(
        marker in normalized for marker in _QUALIFIED_CESSATION_CONTEXT_MARKERS
    )
    has_temporal_endpoint = any(
        marker in normalized for marker in _TEMPORAL_CESSATION_ENDPOINT_MARKERS
    )
    return has_context_qualifier and has_temporal_endpoint


def temporal_ceases_to_have_effect_exclusion_rule(
    *,
    effect_type: str,
    source_text: str,
) -> str:
    """Return the replay-exclusion rule id for temporal cessation, if any."""
    if not is_uk_ceases_to_have_effect_type(effect_type):
        return ""
    if not is_temporally_qualified_ceases_to_have_effect_text(source_text):
        return ""
    return UK_TEMPORAL_CEASES_TO_HAVE_EFFECT_REPLAY_EXCLUDED_RULE_ID


def _operation_source_text(op: LegalOperation) -> str:
    if op.source is not None and op.source.raw_text:
        return op.source.raw_text
    if op.payload is None:
        return ""
    rewrite_witness = op.payload.attrs.get("rewrite_witness")
    if not isinstance(rewrite_witness, dict):
        return ""
    extraction_witness = rewrite_witness.get("extraction_witness")
    if isinstance(extraction_witness, dict):
        extracted_text = str(extraction_witness.get("extracted_text") or "")
        if extracted_text:
            return extracted_text
    source = rewrite_witness.get("source")
    if isinstance(source, dict):
        return str(source.get("raw_text") or "")
    return ""


def temporal_ceases_to_have_effect_exclusion_rule_for_ops(
    *,
    effect_type: str,
    compiled_ops: Sequence[LegalOperation],
) -> str:
    """Return the replay-exclusion rule id for temporal cessation ops, if any."""
    source_text = " ".join(
        source_text
        for op in compiled_ops
        for source_text in (_operation_source_text(op),)
        if source_text
    )
    return temporal_ceases_to_have_effect_exclusion_rule(
        effect_type=effect_type,
        source_text=source_text,
    )
