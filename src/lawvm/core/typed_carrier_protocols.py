"""Typed Protocols for the surface->evidence->authority waist carriers (AGENTS.md §1.9).

Three small ``Protocol`` classes mirror the Wave 2 ``ScopeConfidence`` precedent
(``core/scope_confidence.py``): each is a typed boundary for a parameter that
previously accepted ``Any`` at the surface->evidence->authority waist.  Existing
typed carriers (``core.provenance_graph.ProvenanceAssertion``,
``core.evidence_kernel.AuthorizationResult``,
``replay_adjudication.CompileAdjudication``) structurally conform;
``Mapping[str, Any]`` is the §1.9 third-party-adapter exception explicitly
preserved in the union.  ``coerce_*`` helpers fail loud with a named
``TypeError`` subclass on any other shape (AGENTS.md §1.10 -- never
silent-fallback), replacing the previous ``isinstance(.., Mapping)`` vs
``getattr``-on-``Any`` dynamic-shape dispatch in
``frontier_work_item.frontier_work_item_claim_closure_report`` and
``adjudication_evidence._adjudication_input``.

Mirrors the ``coerce_scope_confidence`` precedent (AGENTS.md §1.10 fail-loud,
AGENTS.md §2.1 stable rule ID).  Unlike ``ScopeConfidence`` (a marker Protocol
with no required members, made ``@runtime_checkable`` so a bare ``str`` fails
``isinstance`` at the ``LegalOperation.__post_init__`` waist), these Protocols
list the specific fields core reads at the boundary -- a missing member on a
non-``Mapping`` carrier is the typed rebuttal to the historical ``Any``
parameter justifying silently-defensive ``getattr(..., None)`` defaults.  They
are NOT marked ``@runtime_checkable``: ``runtime_checkable`` Protocols with
data-attribute members interfere with ty's narrowing of
``Protocol | Mapping[str, Any]`` unions after an ``isinstance(x, Mapping)``
hint (the dispatched ``Mapping.get`` overload surfaces a spurious
``key: Never`` variant from the Protocol branch's ``object`` fallback).  The
``coerce_*`` helpers fan out on ``Mapping`` membership plus an explicit
reject-list of common smuggle types (``str``/``None``/``int``/``tuple``/etc.);
any other typed instance is presumed to conform structurally and is left
for the downstream reader (which raises ``AttributeError`` on a genuinely
missing field).
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol


# Common smuggle types rejected at the boundary. ``bool`` is intentionally
# absent: a typed carrier never has ``bool`` as its top-level shape, but the
# smuggle list is a deny-list, not an allow-list, and we only reject shapes we
# know are mistakes. Anything not in this list and not a ``Mapping`` is
# presumed to be a typed carrier and left to per-attribute access (which
# fails loud with ``AttributeError`` on a missing field).
_SMUGGLE_TYPES: tuple[type, ...] = (
    str,
    bytes,
    bytearray,
    int,
    float,
    complex,
    type(None),
    tuple,
    list,
    set,
    frozenset,
)


# §1.10 fail-loud carrier diagnostics MUST embed the offending value bounded
# to ~400 chars (mirrors ``core/named_swallow._truncate_clause_text``). A
# smuggled list / dict / deep-immutable could legitimately carry MBs of repr —
# unbounded ``value!r`` in the diagnostic message would (a) blow CI log noise
# past actionable triage bounds, (b) allocate a multi-MB string per failed
# boundary coercion, and (c) re-introduce the silent-failure cost shape §1.10
# forbids (a "fail loud" that nobody can read is just as opaque as ``pass``).
_BOUND_REPR_MAX_CHARS = 400


def _truncate_repr(value: object, max_len: int = _BOUND_REPR_MAX_CHARS) -> str:
    """Truncate ``repr(value)`` to ``max_len`` chars with a marker when longer.

    Mirrors ``core/named_swallow._truncate_clause_text`` for the carrier
    boundary diagnostics (``UnregisteredClaimAssertion`` /
    ``UnregisteredAuthorizationResult`` / ``UnregisteredAdjudicationCarrier``),
    so the §1.10-distinct named diagnostic embeds an offending ~400-char
    snippet rather than unbounded ``value!r``. Always preserves the type-name
    prefix (already separate in the f-string); only the embedded repr payload
    is bounded.
    """
    text = repr(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…[truncated]"


# --------------------------------------------------------------------------- #
# ClaimAssertion carrier
# --------------------------------------------------------------------------- #


class ClaimAssertion(Protocol):
    """Typed claim-assertion carrier at the core boundary.

    Conforms: ``core.provenance_graph.ProvenanceAssertion`` and any
    third-party mapping adapter (``Mapping[str, Any]`` per §1.9 exception).
    Core reads the listed fields via ``_claim_assertion_mapping`` in
    ``frontier_work_item`` -- if any field is missing on a typed carrier, the
    carrier is mis-shaped and the downstream ``AttributeError`` is the
    fail-loud receipt (no permissive ``getattr(..., None)`` fallback).

    ``Mapping[str, Any]`` is the explicit union member at the boundary
    parameter site; the ``coerce_assertion`` helper accepts both shapes.
    Not marked ``@runtime_checkable`` (see module docstring).
    """

    assertion_id: str
    jurisdiction: str
    kind: str
    scope: Mapping[str, Any]
    target: Mapping[str, Any]
    value: Mapping[str, Any]


class UnregisteredClaimAssertion(TypeError):
    """A claim-assertion carrier at the boundary is neither typed nor ``Mapping``.

    Raised at the ``frontier_work_item_claim_closure_report`` boundary instead
    of silently accepting a bare ``str``/``None``/``int``/etc. alongside a
    frontend's typed ``ProvenanceAssertion`` carrier.  The fix is always
    "construct a ``ProvenanceAssertion`` (or any typed dataclass conforming to
    ``ClaimAssertion``) or pass a ``Mapping[str, Any]`` adapter instead of a
    free-form value" -- keeping the boundary waist typed (AGENTS.md §1.9) and
    the §1.10 fail-loud contract intact.
    """

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"claim assertion must be a typed ClaimAssertion instance or a "
            f"Mapping[str, Any] adapter; got {type(value).__name__}: "
            f"{_truncate_repr(value)}"
        )


def coerce_assertion(
    value: "ClaimAssertion | Mapping[str, Any]",
) -> "ClaimAssertion | Mapping[str, Any]":
    """Coerce a claim-assertion carrier, failing loud on common smuggle types.

    Used at the ``frontier_work_item_claim_closure_report`` boundary (the
    surface->evidence waist) so a bare ``str``/``None``/``int``/etc. cannot
    ride alongside a frontend's typed ``ProvenanceAssertion`` carrier.  A
    ``Mapping[str, Any]`` is the §1.9 third-party-adapter exception: passed
    through unchanged.  Any typed instance that structurally conforms to the
    marker Protocol is presumed conformant; core does NOT inspect its
    jurisdiction-local fields (AGENTS.md §2.3).  Common smuggle shapes
    (``str``/``None``/``int``/``tuple``/etc.) raise
    ``UnregisteredClaimAssertion`` (AGENTS.md §1.10).  Anything else is left
    to the downstream reader, which raises ``AttributeError`` on a genuinely
    missing field -- the typed rebuttal to silently-defensive
    ``getattr(..., None)`` defaults.
    """
    if isinstance(value, Mapping):
        return value
    if isinstance(value, _SMUGGLE_TYPES):
        raise UnregisteredClaimAssertion(value)
    return value


# --------------------------------------------------------------------------- #
# ExecutionAuthorizationResult carrier
# --------------------------------------------------------------------------- #


class ExecutionAuthorizationResult(Protocol):
    """Typed authorization-result carrier at the core boundary.

    Conforms: ``core.evidence_kernel.AuthorizationResult`` and any third-party
    mapping adapter.  ``subject`` is required at the top level; nested
    ``subject.artifact_id`` access is validated by the downstream consumer
    (``_authorization_result_mapping`` raises ``AttributeError`` on a missing
    ``artifact_id`` rather than silently degrading).  Not marked
    ``@runtime_checkable`` (see module docstring).
    """

    subject: Any  # consumer reads `.artifact_id`; nested attribute validated by AttributeError
    policy_id: str
    profile_name: str
    authorized: bool
    satisfied_clauses: tuple[str, ...]
    unsatisfied_clauses: tuple[str, ...]
    forbidden_present: tuple[str, ...]
    evidence_bundle_hash: str


class UnregisteredAuthorizationResult(TypeError):
    """An authorization-result carrier at the boundary is neither typed nor ``Mapping``.

    Raised at the ``frontier_work_item_claim_closure_report`` boundary instead
    of silently accepting a bare ``str``/``None``/``dict``-shaped
    authorization miss alongside a frontend's typed
    ``AuthorizationResult`` carrier.  The fix is always "construct an
    ``AuthorizationResult`` (or conforming typed dataclass) or pass a
    ``Mapping[str, Any]`` adapter instead of a free-form value" (AGENTS.md §1.9
    + §1.10).
    """

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"authorization_result must be a typed ExecutionAuthorizationResult "
            f"instance or a Mapping[str, Any] adapter; got {type(value).__name__}: "
            f"{_truncate_repr(value)}"
        )


def coerce_authorization_result(
    value: "ExecutionAuthorizationResult | Mapping[str, Any]",
) -> "ExecutionAuthorizationResult | Mapping[str, Any]":
    """Coerce an authorization-result carrier, failing loud on common smuggle types.

    Mirrors ``coerce_assertion`` for the ``authorization_result`` parameter
    of ``frontier_work_item_claim_closure_report``.  A ``Mapping[str, Any]``
    adapter is passed through (§1.9 third-party exception); common smuggle
    shapes (``str``/``None``/``int``/``tuple``/etc.) raise
    ``UnregisteredAuthorizationResult`` (AGENTS.md §1.10); anything else is
    presumed to be a typed carrier and is passed through to the downstream
    reader (which raises ``AttributeError`` on a missing field).
    """
    if isinstance(value, Mapping):
        return value
    if isinstance(value, _SMUGGLE_TYPES):
        raise UnregisteredAuthorizationResult(value)
    return value


# --------------------------------------------------------------------------- #
# CompileAdjudicationProtocol carrier
# --------------------------------------------------------------------------- #


class CompileAdjudicationProtocol(Protocol):
    """Typed adjudication carrier at the core boundary.

    Conforms: ``replay_adjudication.CompileAdjudication`` and any third-party
    mapping adapter.  The seven fields (``kind``, ``detail``, ``op_id``,
    ``source_statute``, ``message``, ``blocking``, ``phase``) are exactly the
    fields ``adjudication_evidence._adjudication_input`` reads.
    ``blocking`` is a ``bool`` (enforcement-significant); ``phase`` is a
    ``str`` (provenance-significant).  A typed carrier missing any of these
    surfaces the mis-shape at the typed waist via ``AttributeError`` when the
    downstream reader accesses the missing field, instead of silently
    returning ``getattr(.., None)`` for it (AGENTS.md §1.9).  Not marked
    ``@runtime_checkable`` (see module docstring).
    """

    kind: str
    detail: Mapping[str, Any]
    op_id: str
    source_statute: str
    message: str
    blocking: bool
    phase: str


class UnregisteredAdjudicationCarrier(TypeError):
    """An adjudication carrier at the boundary is neither typed nor ``Mapping``.

    Raised at the ``adjudication_evidence._adjudication_input`` / \
    ``adjudication_diagnostic_detail`` / \
    ``adjudication_finding_evidence_rows`` boundary instead of silently
    accepting a bare ``str``/``None``/``int`` alongside a frontend's typed
    ``CompileAdjudication``.  The fix is always "construct a
    ``CompileAdjudication`` (or conforming typed dataclass) or pass a
    ``Mapping[str, Any]`` adapter instead of a free-form value" (AGENTS.md §1.9
    + §1.10).
    """

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"adjudication must be a typed CompileAdjudicationProtocol instance "
            f"or a Mapping[str, Any] adapter; got {type(value).__name__}: "
            f"{_truncate_repr(value)}"
        )


def coerce_adjudication(
    value: "CompileAdjudicationProtocol | Mapping[str, Any]",
) -> "CompileAdjudicationProtocol | Mapping[str, Any]":
    """Coerce an adjudication carrier, failing loud on common smuggle types.

    Mirrors ``coerce_assertion`` for the ``adjudication`` parameter of
    ``adjudication_evidence._adjudication_input`` /
    ``adjudication_diagnostic_detail``.  A ``Mapping[str, Any]`` adapter is
    passed through (§1.9 third-party exception); common smuggle shapes
    (``str``/``None``/``int``/``tuple``/etc.) raise
    ``UnregisteredAdjudicationCarrier`` (AGENTS.md §1.10); anything else is
    presumed to be a typed carrier and is passed through to the downstream
    reader (which raises ``AttributeError`` on a missing field).
    """
    if isinstance(value, Mapping):
        return value
    if isinstance(value, _SMUGGLE_TYPES):
        raise UnregisteredAdjudicationCarrier(value)
    return value


__all__ = [
    "ClaimAssertion",
    "UnregisteredClaimAssertion",
    "coerce_assertion",
    "ExecutionAuthorizationResult",
    "UnregisteredAuthorizationResult",
    "coerce_authorization_result",
    "CompileAdjudicationProtocol",
    "UnregisteredAdjudicationCarrier",
    "coerce_adjudication",
]

