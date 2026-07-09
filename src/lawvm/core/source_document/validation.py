"""Structural validity of an extraction candidate — pre-adjudication gates.

A candidate that is not even structurally valid — unanchorable, or claiming an
un-governed kind — is not a witness at all and never reaches the adjudicator.
These are HARD, deterministic, producer-neutral checks: they reject malformed
candidates but grant NO assurance. Assurance is a property of adjudication
(``adjudication.py``), not of passing a structural gate.

Corroboration / cross-witness agreement is deliberately NOT here: whether two
candidates actually agree is a semantic judgment the adjudicator makes (it has
the model / the human), never a hardcoded core heuristic (AGENTS.md §2.4 — the
false pdfplumber-vs-model witness comparison lived here and was removed).

Discipline (AGENTS.md §1.9): typed frozen carrier.
"""
from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.source_document.extraction import ExtractionAssertion
from lawvm.core.source_document.ir import SourceDocumentNodeKind

CHECK_ANCHORED = "structural.anchored"
CHECK_GOVERNED_KIND = "structural.governed_kind"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of one structural check over one candidate (pre-adjudication)."""

    passed: bool
    check_name: str
    reason: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.check_name:
            raise ValueError("ValidationResult.check_name must be non-empty")


def validate_anchored(candidate: ExtractionAssertion) -> ValidationResult:
    """HARD: the candidate is re-locatable (non-empty locator) and carries text.

    An unanchorable or empty candidate pins to no region a reviewer could check
    — it is a hallucination, not a witness, and is rejected before adjudication
    (the review §2.3; the LLM-guide verifiability principle: every output
    references a source region).
    """
    locator = candidate.anchor.locator.strip() if candidate.anchor else ""
    passed = bool(locator) and bool(candidate.text.strip())
    return ValidationResult(
        passed=passed,
        check_name=CHECK_ANCHORED,
        reason=(
            "anchored to a concrete region with non-empty text"
            if passed
            else "no locator or no text — unanchorable, rejected"
        ),
    )


def validate_governed_kind(candidate: ExtractionAssertion) -> ValidationResult:
    """HARD: ``fragment_kind`` is a governed ``SourceDocumentNodeKind`` value.

    An un-governed kind is rejected, never silently relabeled — a candidate does
    not get to invent structure the schema does not admit.
    """
    try:
        SourceDocumentNodeKind(candidate.fragment_kind)
        return ValidationResult(
            passed=True,
            check_name=CHECK_GOVERNED_KIND,
            reason=f"kind '{candidate.fragment_kind}' is governed",
        )
    except ValueError:
        return ValidationResult(
            passed=False,
            check_name=CHECK_GOVERNED_KIND,
            reason=f"kind '{candidate.fragment_kind}' is not a governed SourceDocumentNodeKind",
        )


def is_structurally_valid(candidate: ExtractionAssertion) -> bool:
    """True iff the candidate passes every HARD structural gate (adjudicator-eligible)."""
    return validate_anchored(candidate).passed and validate_governed_kind(candidate).passed
