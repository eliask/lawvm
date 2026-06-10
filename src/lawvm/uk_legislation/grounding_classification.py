"""UK oracle-grounding classification contract.

Every replay node that the oracle-alignment pass leaves *unmatched* (no oracle
EID assigned — an alignment event whose ``after_eid`` is ``None``) must carry
exactly one **grounding classification**. The classification is a typed
statement about *why* the node carries no oracle EID, derived from an explicit
rule over the alignment mechanism — never inferred ad hoc from match-method
strings downstream.

The four classifications:

``source_faithful_oracle_absent``
    We have positively proven the oracle lacks this node and that replay is
    source-faithful. This is a *strong* claim and is **never** the default:
    the grounding pass does not currently mint it, because suppression alone
    does not prove the oracle's absence. Reserved for callers that supply an
    explicit absence proof.

``parser_structure_desync``
    A structural desync between the parser's tree and the oracle's — the node
    exists in a shape the oracle does not model. Not currently minted by the
    grounding pass; reserved for explicit structural-desync evidence.

``non_commensurable``
    The node kind does not carry an own oracle EID by design — transparent
    wrapper kinds (p1group / pblock / crossheading) whose children own the
    EIDs. The absence of an EID here is a structural fact, not a grounding
    failure.

``unresolved``
    We could not ground the node and have no proof that the oracle lacks it.
    This is the **conservative default** for every suppression mechanism. It
    is a visible, numerator-excluded bucket — never folded into either the
    source-faithful or the oracle-suspect side.

The mapping is total over the suppression mechanisms the grounding pass emits
(``GROUNDING_SUPPRESSION_MECHANISMS``). Any mechanism not in the explicit
mapping classifies as ``unresolved`` (fail safe), and any after_eid=None event
with no usable mechanism is itself a contract violation surfaced by
``unclassified_suppression_events``.
"""
from __future__ import annotations

from typing import Any, Iterable

# ── Classification values ────────────────────────────────────────────────────
GROUNDING_SOURCE_FAITHFUL_ORACLE_ABSENT = "source_faithful_oracle_absent"
GROUNDING_PARSER_STRUCTURE_DESYNC = "parser_structure_desync"
GROUNDING_NON_COMMENSURABLE = "non_commensurable"
GROUNDING_UNRESOLVED = "unresolved"

GROUNDING_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        GROUNDING_SOURCE_FAITHFUL_ORACLE_ABSENT,
        GROUNDING_PARSER_STRUCTURE_DESYNC,
        GROUNDING_NON_COMMENSURABLE,
        GROUNDING_UNRESOLVED,
    }
)

# The conservative default. Suppression proves we did not assign an oracle EID;
# it never proves the oracle lacks the node. So an unmatched node defaults here,
# never to the source-faithful claim.
GROUNDING_DEFAULT_CLASSIFICATION = GROUNDING_UNRESOLVED

# ── Mechanism → classification rules ─────────────────────────────────────────
# The suppression / clear mechanisms emitted by ``ground_ids`` on after_eid=None
# events. Each maps to exactly one classification via an explicit rule. Only
# the structurally-justified ``transparent_wrapper_cleared`` earns a
# non-``unresolved`` classification; every other mechanism is a genuine failure
# to ground and stays ``unresolved``.
_GROUNDING_MECHANISM_CLASSIFICATION: dict[str, str] = {
    "transparent_wrapper_cleared": GROUNDING_NON_COMMENSURABLE,
    "local_fallback_suppressed": GROUNDING_UNRESOLVED,
    "local_fallback_unlabeled_blocked": GROUNDING_UNRESOLVED,
    "non_oracle_eid_cleared": GROUNDING_UNRESOLVED,
    "schedule_entry_public_eid_cleared": GROUNDING_UNRESOLVED,
}

# The full set of mechanisms the grounding pass uses on suppression events.
# Kept explicit so a newly-introduced mechanism that forgot to register a
# classification rule is caught by a test rather than silently defaulting.
GROUNDING_SUPPRESSION_MECHANISMS: frozenset[str] = frozenset(
    _GROUNDING_MECHANISM_CLASSIFICATION
)


def is_suppression_event(event: dict[str, Any]) -> bool:
    """Return True for an alignment event that left the node without an EID.

    A suppression event is any alignment event whose ``after_eid`` is None —
    the node carries no oracle EID after grounding and therefore needs a
    grounding classification.
    """

    return event.get("after_eid") is None


def classify_suppression_mechanism(match_method: Any) -> str:
    """Return the grounding classification for a suppression ``match_method``.

    Total function: a known mechanism maps via the explicit rule table; an
    unknown or missing mechanism falls back to the conservative default
    (``unresolved``) — never to a source-faithful claim. The fallback is a
    safety net; a genuinely missing mechanism on an after_eid=None event is a
    contract violation that the check surfaces separately (see
    ``unclassified_suppression_events``).
    """

    if not match_method:
        return GROUNDING_DEFAULT_CLASSIFICATION
    return _GROUNDING_MECHANISM_CLASSIFICATION.get(
        str(match_method), GROUNDING_DEFAULT_CLASSIFICATION
    )


def grounding_classification_for_event(event: dict[str, Any]) -> str | None:
    """Return the grounding classification for one alignment event.

    Returns ``None`` for events that assigned an oracle EID (after_eid is not
    None) — those are matched, not suppressed, and carry no classification.
    Suppression events (after_eid is None) always return one of the four
    classifications.
    """

    if not is_suppression_event(event):
        return None
    return classify_suppression_mechanism(event.get("match_method"))


def unclassified_suppression_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return suppression events that carry no usable grounding mechanism.

    Totality guard: every after_eid=None event must carry a ``match_method``
    that the explicit rule table recognises. An event with a missing
    ``match_method`` — or one whose mechanism is outside
    ``GROUNDING_SUPPRESSION_MECHANISMS`` — cannot be classified by an explicit
    rule and is a contract violation. ``uk_oracle_check`` blocks on a non-empty
    return.
    """

    offenders: list[dict[str, Any]] = []
    for event in events:
        if not is_suppression_event(event):
            continue
        method = event.get("match_method")
        if not method or str(method) not in GROUNDING_SUPPRESSION_MECHANISMS:
            offenders.append(event)
    return offenders
