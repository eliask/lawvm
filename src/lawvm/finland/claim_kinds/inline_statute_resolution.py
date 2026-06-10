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
       d. (corpus existence check) resolved_statute_id's canonical year/number
          exists in data/fi/v1/statutes.parquet as a known Finnish statute.
          EU regulation IDs like '1210/2010' may pass checks a-c but are NOT
          Finnish statutes; check d rejects them.
     If citation form is structurally unresolvable → UNVALIDATED + reason.

Canonicalization (pre-1980 legacy form):
  Finnish statutes use NNNN/YYYY (number/year). Pre-1980 statutes were
  frequently cited with 2-digit years: NNN/YY → canonical NNN/1YYY.
  2-digit years always expand to 19YY (post-2000 statutes always use 4-digit years).
  _canonicalize_finnish_statute_id() handles this expansion. Callers (propose-claims)
  normalize before validation so stored claims always carry canonical form.

Corpus-existence check (Bug C):
  Finnish statute IDs in data/fi/v1/statutes.parquet use YYYY/N (year/number)
  format (e.g. '2003/434' = Hallintolaki, statute 434 of year 2003).
  Claims carry resolved_statute_id in NNNN/YYYY (number/year) format
  (e.g. '434/2003'). The check converts before lookup.
  The known-ID set is loaded once per process via lru_cache.
  Disable per-instance via check_corpus_existence=False (for synthetic-corpus
  unit tests).

Design discipline (AGENTS.md §1.11, §1.13):
  - All patterns compiled at module scope.
  - Bounded quantifiers; no adjacent unbounded repeats.
  - Substring guard before regex scan.
  - Single-pass recognizer for citation pattern family (§1.13).
"""
from __future__ import annotations

import functools
import hashlib
import re
from typing import FrozenSet, Optional, Tuple

from lawvm.core.manual_claims.kind_registry import (
    ClaimKindSpec,
    ValidationResult,
    register_claim_kind,
)

# ---------------------------------------------------------------------------
# Corpus-existence check helpers (Bug C)
# ---------------------------------------------------------------------------

# Default path for statutes.parquet; tests override via _corpus_statute_ids_for_test.
_DEFAULT_STATUTES_PARQUET = "data/fi/v1/statutes.parquet"


@functools.lru_cache(maxsize=1)
def _load_statute_ids_from_parquet(parquet_path: str) -> FrozenSet[str]:
    """Load the set of known statute_ids from statutes.parquet.

    Cached after first load (one read per process). The parquet stores IDs in
    YYYY/N format (e.g. '2003/434'); claims carry NNNN/YYYY (e.g. '434/2003').
    The caller must invert before lookup (see _statute_exists_in_corpus).
    """
    import importlib.util
    from pathlib import Path

    path = Path(parquet_path)
    if not path.exists():
        return frozenset()

    has_pyarrow = importlib.util.find_spec("pyarrow") is not None
    if not has_pyarrow:
        return frozenset()

    import pyarrow.parquet as pq
    table = pq.read_table(str(path), columns=["statute_id"])
    ids = set()
    for batch in table.to_batches():
        col = batch.column("statute_id")
        for i in range(batch.num_rows):
            v = col[i].as_py()
            if v:
                ids.add(v)
    return frozenset(ids)


def _number_year_to_year_number(resolved_id: str) -> Optional[str]:
    """Convert claim's NNNN/YYYY format to corpus's YYYY/N format.

    Finnish statute IDs in claims: number/year (e.g. '434/2003').
    Finnish statute IDs in corpus: year/number (e.g. '2003/434').
    Returns None if the ID doesn't match NNNN/YYYY shape.
    """
    m = _STATUTE_ID_RE.match(resolved_id)
    if not m:
        return None
    number, year = m.group(1), m.group(2)
    return f"{year}/{number}"


def _statute_exists_in_corpus(
    resolved_statute_id: str,
    *,
    statutes_parquet: str = _DEFAULT_STATUTES_PARQUET,
) -> Optional[bool]:
    """Check if resolved_statute_id maps to a known Finnish statute.

    resolved_statute_id is in NNNN/YYYY (number/year) format.
    statutes.parquet stores in YYYY/N (year/number) format.
    Converts before lookup.

    Returns:
      True   — ID found in corpus.
      False  — ID not found in corpus (corpus is populated).
      None   — corpus parquet unavailable; check cannot be performed.
    """
    corpus_id = _number_year_to_year_number(resolved_statute_id)
    if corpus_id is None:
        return False
    known_ids = _load_statute_ids_from_parquet(statutes_parquet)
    if not known_ids:
        # Parquet absent or empty — cannot verify; return None (inconclusive)
        return None
    return corpus_id in known_ids

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

# Pattern 5: Finnish date-decorated statute form DD.M[M].YYYY/NNNN or (DD.M[M].YYYY/NNNN)
# e.g. "11.12.2014/1055" → year=2014, number=1055
#      "(29.1.1999/77)"  → year=1999, number=77
# The date part (day.month) precedes the statute year; the statute number follows the slash.
# Bounded: day \d{1,2}, month \d{1,2}, year \d{4}, statute number \d{1,4}.
# Substring guard: requires "." digit pattern (Finnish date separator).
_CITE_DATE_DECORATED_RE = re.compile(
    r"\b\d{1,2}\.\d{1,2}\.(\d{4})/(\d{1,4})\b"
)


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
    Also handles legacy 2-digit-year citations (e.g. '(71/23)' = statute 71 of 1923)
    and Finnish date-decorated forms (e.g. '11.12.2014/1055' or '(29.1.1999/77)').
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

    # Finnish date-decorated form DD.M[M].YYYY/NNNN (higher specificity than bare slash)
    # e.g. "11.12.2014/1055" → year=2014, number=1055
    #      "(29.1.1999/77)"  → year=1999, number=77
    # Must be tried before _CITE_SLASHONLY_RE to avoid year/number inversion:
    # _CITE_SLASHONLY_RE would match "1999/77" → (year=77, number=1999) which is wrong.
    m = _CITE_DATE_DECORATED_RE.search(citation_form)
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
    *,
    check_corpus_existence: bool = False,
    statutes_parquet: str = _DEFAULT_STATUTES_PARQUET,
) -> ValidationResult:
    """Entailment validator: check resolved_statute_id is entailed by citation_form.

    Concrete checks:
      a. citation_form substring appears in span_text.
      b. resolved_statute_id matches NNNN/YYYY shape.
      c. citation_form parses to a year/number compatible with resolved_statute_id.
      d. (when check_corpus_existence=True) resolved_statute_id's YYYY/N form
         exists in statutes.parquet.  EU regulation IDs like '1210/2010' pass
         checks a-c but are NOT Finnish statutes — check d rejects them.

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

    # Check d: corpus existence (rejects EU regs that pattern-match as Finnish IDs)
    # Returns None when corpus parquet is unavailable — skip check rather than reject.
    if check_corpus_existence:
        exists = _statute_exists_in_corpus(
            resolved_statute_id, statutes_parquet=statutes_parquet
        )
        if exists is False:
            corpus_id = _number_year_to_year_number(resolved_statute_id) or resolved_statute_id
            return ValidationResult(
                passed=False,
                validator_name="entailment_verified",
                reason=(
                    f"resolved_statute_id {resolved_statute_id!r} canonicalizes to "
                    f"{corpus_id!r} but not present in Finnish statute corpus"
                ),
                details="not_in_corpus",
            )
        # exists is True (found) or None (corpus unavailable — skip check)

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
    cited_span = claim.cited_source_span  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    cited_hash = claim.cited_source_hash  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    return _validate_span(
        claim_target=None,
        claim_value=None,
        source_bytes=source_bytes,
        cited_span=cited_span,
        cited_hash=cited_hash,
    )


def _make_entailment_validator(*, check_corpus_existence: bool = True):
    """Factory: return an entailment_validator callable with configurable corpus check.

    check_corpus_existence=True (default): rejects resolved_statute_ids not
        present in data/fi/v1/statutes.parquet.  Catches EU regulation IDs
        like '1210/2010' that pattern-match as Finnish statute IDs.
    check_corpus_existence=False: disables the corpus lookup.  Use in unit
        tests that work with synthetic corpora where statutes.parquet is absent.
    """
    def _validator(claim: object, source_bytes: bytes) -> ValidationResult:
        """Entailment validator compatible with ClaimKindSpec.entailment_validator."""
        cited_span = claim.cited_source_span  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        start, end = cited_span

        if start < 0 or end > len(source_bytes) or start >= end:
            return ValidationResult(
                passed=False,
                validator_name="entailment_verified",
                reason=f"span ({start}, {end}) out of range — cannot extract span text",
                details=None,
            )

        span_text = source_bytes[start:end].decode("utf-8", errors="replace")

        value_dict = dict(claim.value)  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
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
            check_corpus_existence=check_corpus_existence,
        )

    return _validator


# Default validator — corpus-existence check ON.
validate_entailment = _make_entailment_validator(check_corpus_existence=True)
"""Entailment validator for ClaimKindSpec registration.

Canonicalizes resolved_statute_id and checks corpus existence by default.
Callers that need to disable the corpus check (e.g. synthetic-corpus tests)
should use _make_entailment_validator(check_corpus_existence=False) to build
a local validator and pass it to a ClaimKindSpec directly.
"""


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
    span_validator=validate_span,  # ty:ignore[invalid-argument-type]
    entailment_validator=validate_entailment,  # ty:ignore[invalid-argument-type]
)

register_claim_kind(_SPEC)


def get_spec() -> ClaimKindSpec:
    """Return the ClaimKindSpec for fi.v1.INLINE_STATUTE_RESOLUTION."""
    return _SPEC
