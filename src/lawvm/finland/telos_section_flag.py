"""Telos-section flag — typed structural classifier for Finnish purpose/objective clauses.

Promotes the 'is §1 a telos section?' question to a typed claim per
NEXT_FEATURES_ROADMAP.md feature #5 and TELOS_SECTION_FLAG.md.

Design discipline (AGENTS.md §1.9, §1.11, §1.13):

  High-precision only. No false positives.
  Ambiguous candidates stay unflagged and emit BorderlineTelosCandidate.
  Missing a telos section is acceptable. Falsely flagging is not.

Phase: Elaborate (AGENTS.md §6 phase 6) — post-PIT projection.

Extraction rule (tight; ALL conditions must hold):
  1. Section is §1 of its parent statute (label normalizes to "1").
  2. Heading is one of the canonical set (exact match, case-insensitive strip),
     OR heading is "Soveltamisala" and body begins with telos-phrasing.
  3. Body text is non-empty.

For "Soveltamisala" without telos body-phrasing: emit BorderlineTelosCandidate,
do NOT flag.

For §1 with no heading match: do NOT flag, no observation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.ir_helpers import irnode_to_text


# ---------------------------------------------------------------------------
# Module-scope constants (AGENTS.md §1.11 — compile patterns at module scope)
# ---------------------------------------------------------------------------

# Canonical headings that unconditionally trigger the flag (case-insensitive
# exact match after whitespace strip). "Soveltamisala" is the borderline case
# handled separately below.
_CANONICAL_TELOS_HEADINGS: frozenset[str] = frozenset(
    {
        "lain tarkoitus",
        "tarkoitus",
        "tarkoitus ja soveltamisala",
        "soveltamisala ja tarkoitus",
        "lain tavoite",
        "tavoite",
    }
)

# Borderline heading: only flag if body begins with telos-phrasing.
_BORDERLINE_HEADING: str = "soveltamisala"

# Body-phrasing prefixes that confirm a "Soveltamisala" section is also a
# telos section. Check is done on normalized (lowercased, stripped) body.
# These are tight Finnish-drafting-convention prefixes.
_TELOS_BODY_PREFIXES: tuple[str, ...] = (
    "tämän lain tarkoituksena on",
    "tällä lailla",
    "tämän lain tavoitteena on",
)

# Maximum body-text snippet length for purpose_text_snippet column.
_SNIPPET_MAX_CHARS: int = 300

# Normalized label for §1 — always "1" after stripping "§", spaces, dots.
_SECTION_ONE_NORMALIZED: str = "1"


# ---------------------------------------------------------------------------
# Typed observation: BorderlineTelosCandidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BorderlineTelosCandidate:
    """Observation emitted when §1 heading is 'Soveltamisala' but body does
    not begin with canonical telos-phrasing.

    The section is NOT flagged is_purpose_section. This record documents the
    ambiguity for downstream consumers.

    rule_id: stable rule identifier for finding registration.
    statute_id: statute being classified.
    section_label: raw label string of the §1 candidate.
    heading_text: heading text as found.
    body_snippet: first 300 chars of body text (empty if body was empty).
    reason: human-readable reason why the section was not flagged.
    blocking: always False — this is a structural observation, not a gate.
    """

    rule_id: str
    statute_id: str
    section_label: str
    heading_text: str
    body_snippet: str
    reason: str
    blocking: bool = False


# ---------------------------------------------------------------------------
# Extraction result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TelosExtractionResult:
    """Result of classifying one section node.

    is_purpose_section: True iff ALL tight-rule conditions hold.
    purpose_text_snippet: first 300 chars of body text when flagged; None otherwise.
    borderline_candidate: emitted when heading is 'Soveltamisala' but body
        does not confirm; None otherwise.
    """

    is_purpose_section: bool
    purpose_text_snippet: Optional[str]
    borderline_candidate: Optional[BorderlineTelosCandidate]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_heading(text: str) -> str:
    """Lower-case and strip whitespace from a heading string."""
    return text.strip().lower()


def _extract_heading_from_section(section_node: IRNode) -> str:
    """Return the heading text from a section IRNode, or '' if absent.

    Searches direct children for IRNodeKind.HEADING; returns the first one's
    text (via irnode_to_text for robustness). Does not recurse into
    subsections — headings are always direct children of their section.
    """
    for child in section_node.children:
        if child.kind is IRNodeKind.HEADING:
            return irnode_to_text(child).strip()
    return ""


def _extract_body_text_from_section(section_node: IRNode) -> str:
    """Return the non-heading body text of a section IRNode.

    Concatenates text from all direct children that are NOT headings or num
    nodes. Uses irnode_to_text for each qualifying child. Returns '' if the
    section is structurally empty (no subsections / content children).
    """
    _skip_kinds = frozenset({IRNodeKind.HEADING, IRNodeKind.NUM})
    parts: list[str] = []
    for child in section_node.children:
        if child.kind in _skip_kinds:
            continue
        text = irnode_to_text(child).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _normalize_section_label(label: Optional[str]) -> str:
    """Return a normalized section label string.

    Strips "§", spaces, dots, and trailing noise from a raw section label.
    Returns '' for None or empty input.

    Examples:
        "1 §" -> "1"
        "1." -> "1"
        "1" -> "1"
        "2" -> "2"
        None -> ""
    """
    if not label:
        return ""
    # Strip whitespace, §, and trailing punctuation.
    stripped = label.strip().rstrip("§").strip().rstrip(".").strip()
    return stripped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_telos_section(
    section_node: IRNode,
    section_label: Optional[str],
    statute_id: str,
) -> TelosExtractionResult:
    """Classify one section node as a telos section.

    Parameters
    ----------
    section_node:
        The IRNode for the section. Must be kind == IRNodeKind.SECTION (not
        enforced here — caller is responsible).
    section_label:
        The raw section label (e.g. "1", "1 §", "2"). May be None.
    statute_id:
        The statute identifier, used in BorderlineTelosCandidate emission.

    Returns
    -------
    TelosExtractionResult:
        is_purpose_section=True only when ALL tight-rule conditions hold.
    """
    # -----------------------------------------------------------------
    # Condition 1: section must be §1.
    # -----------------------------------------------------------------
    norm_label = _normalize_section_label(section_label)
    if norm_label != _SECTION_ONE_NORMALIZED:
        return TelosExtractionResult(
            is_purpose_section=False,
            purpose_text_snippet=None,
            borderline_candidate=None,
        )

    # -----------------------------------------------------------------
    # Condition 2: heading check.
    # -----------------------------------------------------------------
    heading_raw = _extract_heading_from_section(section_node)
    heading_norm = _normalize_heading(heading_raw)

    # -----------------------------------------------------------------
    # Condition 3: body must be non-empty.
    # Body extraction (needed for both the body check and snippet).
    # -----------------------------------------------------------------
    body_text = _extract_body_text_from_section(section_node)
    body_non_empty = bool(body_text.strip())

    # Case A: canonical heading — flag if body non-empty.
    if heading_norm in _CANONICAL_TELOS_HEADINGS:
        if body_non_empty:
            snippet = body_text[: _SNIPPET_MAX_CHARS]
            return TelosExtractionResult(
                is_purpose_section=True,
                purpose_text_snippet=snippet,
                borderline_candidate=None,
            )
        # Canonical heading but empty body — do not flag (body condition fails).
        return TelosExtractionResult(
            is_purpose_section=False,
            purpose_text_snippet=None,
            borderline_candidate=None,
        )

    # Case B: borderline heading "Soveltamisala".
    if heading_norm == _BORDERLINE_HEADING:
        # Check body-phrasing prefix.
        body_prefix = body_text.strip().lower()
        for prefix in _TELOS_BODY_PREFIXES:
            if body_prefix.startswith(prefix):
                if body_non_empty:
                    snippet = body_text[: _SNIPPET_MAX_CHARS]
                    return TelosExtractionResult(
                        is_purpose_section=True,
                        purpose_text_snippet=snippet,
                        borderline_candidate=None,
                    )

        # Body does not begin with telos-phrasing → emit BorderlineTelosCandidate.
        body_snippet = body_text[: _SNIPPET_MAX_CHARS]
        candidate = BorderlineTelosCandidate(
            rule_id="TELOS.BORDERLINE_SOVELTAMISALA_NO_TELOS_BODY",
            statute_id=statute_id,
            section_label=section_label or "",
            heading_text=heading_raw,
            body_snippet=body_snippet,
            reason=(
                "Heading is 'Soveltamisala' but body text does not begin with "
                "canonical telos-phrasing. Section is NOT flagged as purpose section."
            ),
            blocking=False,
        )
        return TelosExtractionResult(
            is_purpose_section=False,
            purpose_text_snippet=None,
            borderline_candidate=candidate,
        )

    # Case C: §1 exists but heading does not match any telos pattern.
    return TelosExtractionResult(
        is_purpose_section=False,
        purpose_text_snippet=None,
        borderline_candidate=None,
    )
