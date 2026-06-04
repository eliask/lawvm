"""fi.v1.INLINE_STATUTE_RESOLUTION — extraction-layer claim kind.

Resolves plain-text Finnish statute citation phrases to canonical statute_ids
where the <ref> markup extractor failed to type the citation.

Schema (§3.2 + §16 Slice 2 of design memo v2.2):
  target = {statute_id: str, section_locator: SourceLocator, mention_span: (int, int)}
  value  = {resolved_statute_id: str, citation_form: str}

Two deterministic validators:
  1. span_verified — cited_source_span actually exists at cited_source_hash.
     Recomputes SHA-256 from current source bytes; rejects on hash mismatch.
  2. entailment_verified — resolved_statute_id is entailed by citation_form
     within cited span. Concrete checks:
       a. citation_form substring appears in cited_source_span content.
       b. resolved_statute_id matches NNNN/YYYY shape (after canonicalization).
       c. citation_form parses to a compatible year/number reference via
          known Finnish citation patterns.
     If citation form is structurally unresolvable → UNVALIDATED + reason.

Canonicalization (pre-1980 legacy form):
  Finnish statutes use NNNN/YYYY (number/year). Pre-1980 statutes were
  frequently cited with 2-digit years: NNN/YY → canonical NNN/1YYY.
  2-digit years always expand to 19YY (post-2000 statutes always use 4-digit years).
  _canonicalize_finnish_statute_id() handles this expansion. Callers (propose-claims)
  normalize before validation so stored claims always carry canonical form.

Design discipline (AGENTS.md §1.11, §1.13):
  - All patterns compiled at module scope.
  - Bounded quantifiers; no adjacent unbounded repeats.
  - Substring guard before regex scan.
  - Single-pass recognizer for citation pattern family (§1.13).
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional, Tuple

from lawvm.core.manual_claims.kind_registry import (
    ClaimKindSpec,
    ValidationResult,
    register_claim_kind,
)

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# ---------------------------------------------------------------------------

# Finnish statute IDs are NNNN/YYYY (statute_number/year).
# e.g. 1234/2020 = statute 1234 of year 2020.
# The canonical form has YEAR on the right side as 4 digits.

# Matches canonical Finnish statute ID: NNNN/YYYY → group1=number, group2=year
_STATUTE_ID_RE = re.compile(r"^(\d{1,4})/(\d{4})$")

# Matches legacy 2-digit-year form: NNN/YY → group1=number, group2=2-digit-year
# Only matches when year is exactly 2 digits (not 3 or 4).
_LEGACY_STATUTE_ID_RE = re.compile(r"^(\d{1,4})/(\d{2})$")

# Finnish citation patterns (bounded quantifiers, no adjacent unbounded repeats)
# All patterns return (year, statute_number) after normalization.

# Pattern 1: (1234/2020) or (71/23) → number=group1, year=group2 (4-digit or 2-digit)
_CITE_PAREN_RE = re.compile(r"\((\d{1,4})/(\d{2,4})\)")

# Pattern 2: lain 1234/2020 or lain 71/23 → number=group1, year=group2
_CITE_BARE_RE = re.compile(r"\blain\s+(\d{1,4})/(\d{2,4})\b")

# Pattern 3: "vuoden YYYY lain N:o NNNN" → group1=year, group2=number
_CITE_VUODEN_RE = re.compile(
    r"\bvuoden\s+(\d{4})\s+la(?:in|kia)\s+N:o\s+(\d{1,4})\b"
)

# Pattern 4: bare NNNN/YYYY or NNN/YY in text → number=group1, year=group2
_CITE_SLASHONLY_RE = re.compile(r"\b(\d{1,4})/(\d{2,4})\b")


def _expand_year(raw_year: int) -> int:
    """Expand a 2-digit year to 4 digits using the 19xx convention.

    Pre-1980 Finnish statutes used 2-digit years. Post-2000 statutes always
    use 4-digit years, so there is no ambiguity: 2-digit always means 19YY.
    """
    if raw_year < 100:
        return 1900 + raw_year
    return raw_year


def _canonicalize_finnish_statute_id(raw: str) -> Optional[str]:
    """Expand a legacy 2-digit-year Finnish statute ID to canonical form.

    Accepted input forms:
      NNNN/YYYY  — already canonical; returned unchanged.
      NNN/YY     — legacy pre-1980 form; returns NNN/19YY.
      NN/YY      — ditto.

    Returns None if the input does not parse as any Finnish statute citation.
    """
    m = _STATUTE_ID_RE.match(raw)
    if m:
        return raw  # already canonical

    m = _LEGACY_STATUTE_ID_RE.match(raw)
    if m:
        number = m.group(1)
        year_4 = 1900 + int(m.group(2))
        return f"{number}/{year_4}"

    return None


def _extract_year_number(citation_form: str) -> Optional[Tuple[int, int]]:
    """Try to extract (year, statute_number) from a citation form string.

    Finnish statute IDs are NNNN/YYYY (e.g. '1234/2020' = statute 1234 of 2020).
    Also handles legacy 2-digit-year citations (e.g. '(71/23)' = statute 71 of 1923).
    Returns (year, statute_number) or None if no known pattern matches.
    Tries patterns in specificity order; returns first match.
    """
    # Substring guard: must contain a digit sequence of length 2+
    if not re.search(r"\d{2}", citation_form):
        return None

    # vuoden YYYY lain N:o NNNN → group1=year (4-digit), group2=number
    m = _CITE_VUODEN_RE.search(citation_form)
    if m:
        return int(m.group(1)), int(m.group(2))

    # (NNNN/YYYY) or (NNN/YY) → group1=number, group2=year
    m = _CITE_PAREN_RE.search(citation_form)
    if m:
        return _expand_year(int(m.group(2))), int(m.group(1))

    # lain NNNN/YYYY or lain NNN/YY → group1=number, group2=year
    m = _CITE_BARE_RE.search(citation_form)
    if m:
        return _expand_year(int(m.group(2))), int(m.group(1))

    # bare NNNN/YYYY or NNN/YY → group1=number, group2=year
    m = _CITE_SLASHONLY_RE.search(citation_form)
    if m:
        return _expand_year(int(m.group(2))), int(m.group(1))

    return None


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _validate_span(
    claim_target: object,
    claim_value: object,
    source_bytes: bytes,
    cited_span: Tuple[int, int],
    cited_hash: str,
) -> ValidationResult:
    """Span validator: verify that cited_source_span exists at cited_source_hash.

    source_bytes: the current bytes of the cited source artifact.
    cited_span: (start, end) byte offsets.
    cited_hash: the SHA-256 hash claimed for the bytes at cited_span.

    Returns SPAN_VERIFIED if the recomputed hash matches.
    Returns UNVALIDATED with reason if not.
    """
    start, end = cited_span
    if start < 0 or end > len(source_bytes) or start >= end:
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason=f"span ({start}, {end}) out of range for source of length {len(source_bytes)}",
            details=None,
        )
    span_bytes = source_bytes[start:end]
    actual_hash = hashlib.sha256(span_bytes).hexdigest()
    if actual_hash != cited_hash:
        return ValidationResult(
            passed=False,
            validator_name="span_verified",
            reason=(
                f"hash mismatch at span ({start}, {end}): "
                f"claimed {cited_hash!r}, actual {actual_hash!r}"
            ),
            details=None,
        )
    return ValidationResult(
        passed=True,
        validator_name="span_verified",
        reason="ok",
        details=None,
    )


def _validate_entailment(
    resolved_statute_id: str,
    citation_form: str,
    span_text: str,
) -> ValidationResult:
    """Entailment validator: check resolved_statute_id is entailed by citation_form.

    Concrete checks:
      a. citation_form substring appears in span_text.
      b. resolved_statute_id matches YYYY/NNNN shape.
      c. citation_form parses to a year/number compatible with resolved_statute_id.
    If citation_form is structurally unresolvable → UNVALIDATED (not failure).

    Returns a ValidationResult with validator_name='entailment_verified'.
    """
    # Check a: citation_form appears in span_text
    if citation_form not in span_text:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=f"citation_form {citation_form!r} not found in cited span text",
            details=None,
        )

    # Check b: resolved_statute_id has canonical shape
    id_match = _STATUTE_ID_RE.match(resolved_statute_id)
    if not id_match:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=(
                f"resolved_statute_id {resolved_statute_id!r} does not match "
                "expected NNNN/YYYY shape"
            ),
            details=None,
        )

    # _STATUTE_ID_RE captures NNNN/YYYY → group1=statute_number, group2=year
    resolved_number = int(id_match.group(1))
    resolved_year = int(id_match.group(2))

    # Check c: citation_form parses to compatible year/number
    # _extract_year_number returns (year, statute_number)
    extracted = _extract_year_number(citation_form)
    if extracted is None:
        # Structurally unresolvable → UNVALIDATED (not a hard failure)
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=(
                f"citation_form {citation_form!r} does not match any known "
                "Finnish citation pattern — cannot verify entailment structurally"
            ),
            details="unresolvable_citation_form",
        )

    cite_year, cite_number = extracted
    if cite_year != resolved_year or cite_number != resolved_number:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=(
                f"citation_form parses to {cite_year}/{cite_number} "
                f"but resolved_statute_id is {resolved_statute_id!r}"
            ),
            details=None,
        )

    return ValidationResult(
        passed=True,
        validator_name="entailment_verified",
        reason="ok",
        details=None,
    )


# ---------------------------------------------------------------------------
# Public validator API (matching ClaimKindSpec protocol)
# ---------------------------------------------------------------------------


def validate_span(claim: object, source_bytes: bytes) -> ValidationResult:
    """Span validator compatible with ClaimKindSpec.span_validator signature.

    claim: ManualCompilationClaim (typed as object to avoid circular import).
    source_bytes: bytes of the cited source artifact.
    """
    cited_span = claim.cited_source_span  # type: ignore[attr-defined]
    cited_hash = claim.cited_source_hash  # type: ignore[attr-defined]
    return _validate_span(
        claim_target=None,
        claim_value=None,
        source_bytes=source_bytes,
        cited_span=cited_span,
        cited_hash=cited_hash,
    )


def validate_entailment(claim: object, source_bytes: bytes) -> ValidationResult:
    """Entailment validator compatible with ClaimKindSpec.entailment_validator.

    claim: ManualCompilationClaim (typed as object to avoid circular import).
    source_bytes: bytes of the cited source artifact (for span text extraction).

    Canonicalizes resolved_statute_id before the shape check so that legacy
    2-digit-year forms (e.g. '361/72') are accepted and compared correctly.
    The canonical form is used for entailment checking; callers that need the
    canonical form stored in the claim should normalize before calling
    (see cmd_propose_claims.py normalize_fi_statute_resolution_value).
    """
    cited_span = claim.cited_source_span  # type: ignore[attr-defined]
    start, end = cited_span

    if start < 0 or end > len(source_bytes) or start >= end:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=f"span ({start}, {end}) out of range — cannot extract span text",
            details=None,
        )

    span_text = source_bytes[start:end].decode("utf-8", errors="replace")

    # Extract value fields from frozen tuple-of-pairs representation
    value_dict = dict(claim.value)  # type: ignore[attr-defined]
    raw_statute_id = value_dict.get("resolved_statute_id", "")
    citation_form = value_dict.get("citation_form", "")

    canonical = _canonicalize_finnish_statute_id(raw_statute_id)
    if canonical is None:
        return ValidationResult(
            passed=False,
            validator_name="entailment_verified",
            reason=(
                f"resolved_statute_id {raw_statute_id!r} does not parse as "
                "Finnish statute citation"
            ),
            details=None,
        )
    if canonical != raw_statute_id:
        import logging
        logging.getLogger(__name__).debug(
            "canonicalized %s → %s", raw_statute_id, canonical
        )

    return _validate_entailment(
        resolved_statute_id=canonical,
        citation_form=citation_form,
        span_text=span_text,
    )


# ---------------------------------------------------------------------------
# Register with core registry
# ---------------------------------------------------------------------------

_SPEC = ClaimKindSpec(
    claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
    jurisdiction="fi",
    layer="extraction",
    description=(
        "Resolves a plain-text Finnish statute citation phrase to a canonical "
        "statute_id. Extraction layer: fills NULL slots only where the <ref> "
        "markup extractor found no match. Does not override deterministic verdicts."
    ),
    target_fields=("statute_id", "section_locator", "mention_span"),
    value_fields=("resolved_statute_id", "citation_form"),
    span_validator=validate_span,
    entailment_validator=validate_entailment,
)

register_claim_kind(_SPEC)


def get_spec() -> ClaimKindSpec:
    """Return the ClaimKindSpec for fi.v1.INLINE_STATUTE_RESOLUTION."""
    return _SPEC
