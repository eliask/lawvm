"""Self-evidencing diagnostic totality sweep (registry row EV-07).

EV-07 — *self-evidencing diagnostic sweep*
==========================================
Every source-text-failure finding must embed the offending source snippet as a
TYPED field, not an opaque message (the ``feedback_diagnostics_self_evidencing``
convention: a diagnostic about unhandled source text carries the offending clause
text, so a reader audits the finding from the record alone). The per-diagnostic
convention already exists at the producer level — the forest's ``SyntaxResidual``
and ``SyntaxNode(kind="residual_span")`` ENFORCE a non-empty ``residual_text`` in
``__post_init__``, and the surface-graph / forest stage producers set
``Residual.text`` verbatim. EV-07 is the **totality** sweep: the standing
assertion that EVERY core :class:`~lawvm.core.stage_result.Residual` in the
source-text-failure FAMILY carries its snippet — the convention is never silently
widened to a snippet-less member.

THE SOURCE-TEXT-FAILURE FAMILY (precise scope)
==============================================
The core ``Residual.kind`` closed vocabulary is
``out_of_scope`` / ``typed_residual`` / ``unowned_violation`` /
``benign_uninterpreted``. A *source-text-failure* residual is one that points at a
span of OFFENDING SOURCE TEXT that failed to be fully owned:

  * ``unowned_violation`` — a silent, signal-bearing source span no construction
    family owned (the forest/surface no-silent-drop frontier). MUST carry its text.
  * ``typed_residual``    — a surfaced owned-residue source span (the tag-don't-guess
    frontier). MUST carry its text.

The OTHER two kinds are explicitly NOT source-text-failures and are OUT of EV-07's
totality (the honest scope statement):

  * ``out_of_scope``        — e.g. an amendment-selection cutoff/oracle drop or a
    missing-source-bytes pathology. There is NO offending clause text to embed
    (the bytes are absent / the candidate is filtered, not malformed); requiring a
    snippet would manufacture a field that cannot exist.
  * ``benign_uninterpreted`` — a no-signal (whitespace-class) span; it carries no
    actionable signal and is never a failure to self-evidence.

So EV-07 covers the ``{unowned_violation, typed_residual}`` source-text-failure
family; a member of that family with an empty ``text`` is
``EVID.DIAGNOSTIC_NOT_SELF_EVIDENCING`` — an opaque diagnostic about unhandled
source text. This is the core-``Residual``-level totality; at the FI forest level
the same property is enforced-by-construction in ``SyntaxNode.__post_init__``, so
on the green corpus the sweep is silent (every source-text-failure residual that
reaches the core account already carries verbatim text). The synthetic
snippet-less residual is the guard-liveness fire-drill.

OBSERVATION-ROLE
================
A snippet-less source-text-failure residual is a producer defect (an opaque
diagnostic), surfaced as an OBSERVATION so the totality contract has a live,
distinctly-named detector without blocking a corpus that is already self-evidencing
by construction.

The sweep is PURE: it reads already-produced :class:`Residual` records and returns
typed finding records; no production behavior changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from lawvm.core.stage_result import Residual

# ---------------------------------------------------------------------------
# Finding code (registered in core/observation_registry.py)
# ---------------------------------------------------------------------------

DIAGNOSTIC_NOT_SELF_EVIDENCING = "EVID.DIAGNOSTIC_NOT_SELF_EVIDENCING"

#: The closed source-text-failure residual family EV-07 asserts totality over. A
#: residual ``kind`` NOT in this set is NOT a source-text-failure (it carries no
#: offending clause text by design) and is, by construction, out of scope — a new
#: snippet-carrying kind must be consciously added here, never silently swept.
SOURCE_TEXT_FAILURE_KINDS = frozenset({"unowned_violation", "typed_residual"})


@dataclass(frozen=True, slots=True)
class DiagnosticSelfEvidencingFinding:
    """One EV-07 fact: a source-text-failure residual missing its offending snippet.

    Attributes:
        code:        ``EVID.DIAGNOSTIC_NOT_SELF_EVIDENCING``.
        kind:        The offending residual ``kind`` (∈ ``SOURCE_TEXT_FAILURE_KINDS``).
        reason:      The residual's ``reason`` (the opaque message that lacks a snippet).
        scope:       The residual's ``scope`` (statute/unit id, for the drift anchor).
        source_unit_id: The source unit the empty-text span was into.
        detail:      SELF-EVIDENCING message naming the kind + scope + the missing
                     snippet field, so the finding is auditable from the record alone.
    """

    code: str
    kind: str
    reason: str
    scope: str
    source_unit_id: str
    detail: str


def sweep_source_text_failure_self_evidencing(
    residuals: Sequence["Residual"],
) -> tuple[DiagnosticSelfEvidencingFinding, ...]:
    """Assert self-evidencing totality over a residual population (EV-07).

    Every residual whose ``kind`` is in :data:`SOURCE_TEXT_FAILURE_KINDS` (the
    source-text-failure family) MUST carry a non-empty ``text`` snippet (the
    verbatim offending source span). A source-text-failure residual with an empty
    ``text`` is an opaque diagnostic about unhandled source text, typed
    ``EVID.DIAGNOSTIC_NOT_SELF_EVIDENCING``.

    Args:
        residuals: The core :class:`Residual` records to sweep (e.g. the union a
            stage account committed, or one stage's ``StageResult.residuals``).

    Returns:
        A tuple of :class:`DiagnosticSelfEvidencingFinding`, sorted by
        ``(kind, scope, source_unit_id, reason)``. Empty when every
        source-text-failure residual carries its snippet (the by-construction norm).

    Discipline: the sweep NEVER fabricates a snippet. It reads the residual's OWN
    ``text`` and asserts presence; a snippet-less family member is the producer's
    own opaque diagnostic, surfaced.
    """
    findings: list[DiagnosticSelfEvidencingFinding] = []
    for residual in residuals:
        if residual.kind not in SOURCE_TEXT_FAILURE_KINDS:
            continue
        if str(residual.text or "").strip():
            continue
        findings.append(
            DiagnosticSelfEvidencingFinding(
                code=DIAGNOSTIC_NOT_SELF_EVIDENCING,
                kind=residual.kind,
                reason=residual.reason,
                scope=residual.scope,
                source_unit_id=residual.source_unit_id,
                detail=(
                    f"source-text-failure residual (kind={residual.kind!r}, "
                    f"scope={residual.scope!r}, reason={residual.reason!r}) carries "
                    f"NO verbatim offending snippet (empty text field): an opaque "
                    f"diagnostic about unhandled source text, not self-evidencing"
                ),
            )
        )
    findings.sort(key=lambda f: (f.kind, f.scope, f.source_unit_id, f.reason))
    return tuple(findings)


__all__ = [
    "DIAGNOSTIC_NOT_SELF_EVIDENCING",
    "SOURCE_TEXT_FAILURE_KINDS",
    "DiagnosticSelfEvidencingFinding",
    "sweep_source_text_failure_self_evidencing",
]
