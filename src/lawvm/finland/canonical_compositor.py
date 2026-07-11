"""Level-2 canonical compositor: raw PDF-extracted text → CLEAN, reflowable text.

The whole project's invariant is EXACTNESS-NOT-SLOP. This module turns raw
PDF-extracted text — carrying typographic artifacts (soft/line-break hyphens,
errant mid-word spaces, wrap breaks, whitespace noise) — into a CLEAN, reflowable
canonical form suitable for arbitrary browsers/clients, WITHOUT ever silently
changing legal content. Every transform is LEDGERED: marked, reversible, and
auditable back to the raw extraction.

Why this exists
===============
For an XML-backed unit the trusted XML *is* the clean canonical form, and
``op_equivalence`` supplies the inert quotient bridging raw-PDF ↔ clean-XML. This
compositor DERIVES that same clean form for the PDF-ONLY stratum (appendix tables,
formula-prose blocks, scanned pages) where no XML exists — reusing the SAME
trusted deterministic machinery (``page_elements.dehyphenate`` +
``op_equivalence``'s inert folds) rather than forking it, then optionally admitting
an injected LLM cleanup ONLY when a deterministic safety gate proves it changed no
content.

The two lanes
=============
  * DETERMINISTIC (always applied, trusted): line-break dehyphenation +
    the inert whitespace/format quotient. These come from the already-trusted
    ``page_elements`` / ``op_equivalence`` inert-fold surface, so each is ledgered
    ``proposed_by="deterministic"`` and ``verified=True`` by construction.
  * LLM (optional, distrusted): an injected ``Callable[[str], str]`` proposes a
    cleaner form (e.g. joining a hard wrap). It is ACCEPTED and ledgered
    ``proposed_by="llm"`` ONLY if :func:`verify_content_preserving` proves the
    proposal is a pure join/remove of the current text's existing characters —
    i.e. it added or substituted NOTHING. An unverified proposal is DISCARDED and
    never applied; hallucination cannot reach the published clean form.

The module is PURE and HERMETIC: the LLM is an injected callable, so every path is
testable with no live backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Optional, Tuple

from lawvm.finland.op_equivalence import _canonicalize_text
from lawvm.ingest.page_elements import dehyphenate

# --------------------------------------------------------------------------- #
# Artifact alphabet for the content-preservation safety gate.                 #
# --------------------------------------------------------------------------- #
# The ONLY characters a content-preserving transform is permitted to drop when
# joining/removing: redundant whitespace and the (in)visible line-break hyphens.
# ``dehyphenate`` fuses U+00AD SOFT HYPHEN and the U+FFFE discretionary glyph that
# pypdfium2 emits at a line break; both are pure artifacts of hyphenated wrapping.
# A real content hyphen ("-", en/em dashes) is NOT here — so an LLM that removes or
# invents a substantive dash is caught as a content change, not waved through.
_SOFT_HYPHEN = "­"
_DISCRETIONARY_GLYPH = "￾"
_ARTIFACT_HYPHENS = frozenset({_SOFT_HYPHEN, _DISCRETIONARY_GLYPH})


def _content_skeleton(text: str) -> str:
    """Strip artifact chars, leaving the substantive character sequence.

    Removes all whitespace and the two artifact line-break hyphens. What remains is
    the substantive content: every letter, digit, punctuation mark, and real hyphen
    in document order. Two texts that differ ONLY by whitespace / artifact-hyphen
    joining share the same skeleton.
    """
    return "".join(ch for ch in text if not ch.isspace() and ch not in _ARTIFACT_HYPHENS)


class TransformKind(StrEnum):
    """The closed set of canonicalization transforms this compositor ledgers.

    Each names a content-preserving cleanup class. ``DEHYPHENATE_LINEBREAK`` and
    ``WHITESPACE_NORMALIZE`` are applied deterministically from the trusted inert
    quotient; ``DESPACE_MIDWORD`` and ``WRAP_JOIN`` name the reflow classes that
    are unsafe to apply blindly (they can fuse genuine word/line boundaries) and so
    are only ever admitted through the verified LLM lane; ``LLM_CLEANUP`` is the
    kind an accepted, content-verified LLM proposal is ledgered under.
    """

    DEHYPHENATE_LINEBREAK = "dehyphenate_linebreak"  # soft/discretionary hyphen line join fused
    DESPACE_MIDWORD = "despace_midword"  # errant space strictly between two letters removed
    WRAP_JOIN = "wrap_join"  # hard wrap break between two lines joined
    WHITESPACE_NORMALIZE = "whitespace_normalize"  # inert whitespace/format quotient folded
    LLM_CLEANUP = "llm_cleanup"  # verified LLM-proposed reflow (added/changed nothing)


@dataclass(frozen=True, slots=True)
class Transform:
    """One ledgered, reversible edit from raw toward the clean canonical form.

    ``span`` is ``(offset, length)`` into the transform's INPUT text (the text as it
    stood when this transform was applied); ``before`` is exactly that input region
    and ``after`` is what replaced it. Storing both regions verbatim makes the ledger
    reversible: undoing the transforms newest-first reconstructs the raw extraction
    (see :func:`reconstruct_raw`). ``proposed_by`` is the provenance of the edit —
    ``"deterministic"`` (trusted inert quotient) or ``"llm"`` (distrusted, only
    admitted after :func:`verify_content_preserving`); ``verified`` records that the
    content-preservation gate passed for this edit.
    """

    kind: TransformKind
    span: Tuple[int, int]
    before: str
    after: str
    proposed_by: str
    verified: bool

    def to_jsonable(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "span": list(self.span),
            "before": self.before,
            "after": self.after,
            "proposed_by": self.proposed_by,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class ContentPreservationVerdict:
    """Typed verdict of the deterministic content-preservation safety gate.

    ``preserving`` is True iff the candidate clean text is a pure join/remove of the
    raw text's existing characters — nothing added, nothing substituted. When it is
    False, ``added_or_changed`` carries the substantive characters the candidate
    introduced that could not be accounted for in the raw text's content skeleton
    (the hallucinated / mutated glyphs), for the audit trail.
    """

    preserving: bool
    added_or_changed: Tuple[str, ...]

    def to_jsonable(self) -> dict[str, object]:
        return {
            "preserving": self.preserving,
            "added_or_changed": list(self.added_or_changed),
        }


@dataclass(frozen=True, slots=True)
class CanonicalUnit:
    """A raw extraction paired with its derived clean form and the transform ledger.

    ``clean_text`` is the PUBLISHED content; ``raw_text`` + ``transforms`` are the
    retained EVIDENCE (reversible, auditable). ``provenance`` records how the clean
    form was reached — ``"pdf_derived"`` (this compositor's deterministic + verified
    lanes) or ``"xml_reference"`` (the trusted XML already IS the canonical form).
    """

    clean_text: str
    raw_text: str
    transforms: Tuple[Transform, ...]
    provenance: str

    def to_jsonable(self) -> dict[str, object]:
        return {
            "clean_text": self.clean_text,
            "raw_text": self.raw_text,
            "transforms": [transform.to_jsonable() for transform in self.transforms],
            "provenance": self.provenance,
        }


def verify_content_preserving(raw: str, clean: str) -> ContentPreservationVerdict:
    """THE SAFETY GATE: prove ``clean`` changed no content vs ``raw`` (fully deterministic).

    Rule: after removing only ARTIFACT characters (soft/discretionary line-break
    hyphens and redundant whitespace) from both, the substantive character SEQUENCE
    of ``clean`` must be a faithful in-order SUBSEQUENCE of ``raw``'s substantive
    sequence — with NOTHING added and NOTHING substituted. Equivalently, ``clean`` is
    a pure join/remove of characters that already existed in ``raw``.

    Why it catches hallucination / content change: a genuine cleanup only DELETES
    artifacts and JOINS existing characters, so every substantive char of ``clean``
    still appears, in order, in ``raw``. The moment a proposal INSERTS a word or
    SUBSTITUTES a glyph (``2500`` → ``2600``, an interpolated clause), that new/changed
    char has no in-order match left in ``raw``'s content skeleton and the walk stalls
    on it — it is reported in ``added_or_changed`` and the verdict is non-preserving.
    (The gate is deliberately additions/substitutions-strict; it does not by itself
    prove nothing was DROPPED — the deterministic lane never drops content, and a
    dropped-content proposal is a separate, conservatively-rejected concern.)
    """
    raw_skeleton = _content_skeleton(raw)
    clean_skeleton = _content_skeleton(clean)

    added_or_changed: list[str] = []
    raw_cursor = 0
    raw_len = len(raw_skeleton)
    for ch in clean_skeleton:
        # Advance through raw looking for this clean char in order (subsequence match).
        found_at = raw_skeleton.find(ch, raw_cursor)
        if found_at == -1:
            # No remaining in-order occurrence: this char was added or substituted in.
            added_or_changed.append(ch)
        else:
            raw_cursor = found_at + 1
        if raw_cursor > raw_len:  # pragma: no cover - defensive
            break

    preserving = not added_or_changed
    return ContentPreservationVerdict(
        preserving=preserving,
        added_or_changed=tuple(added_or_changed),
    )


def _diff_span(before: str, after: str) -> Tuple[int, str, str]:
    """Minimal changed envelope between ``before`` and ``after``.

    Returns ``(offset, before_region, after_region)`` where ``offset`` is the shared
    leading-prefix length and the two regions are the substrings that differ (the
    shared trailing suffix trimmed off both). ``before[:offset] == after[:offset]``
    and the two regions carry every difference, so replacing ``before_region`` with
    ``after_region`` at ``offset`` transforms ``before`` into ``after`` — and the
    inverse reconstructs ``before``.
    """
    prefix = 0
    max_prefix = min(len(before), len(after))
    while prefix < max_prefix and before[prefix] == after[prefix]:
        prefix += 1
    # Shared suffix, not overlapping the already-consumed prefix on either side.
    suffix = 0
    max_suffix = min(len(before), len(after)) - prefix
    while suffix < max_suffix and before[len(before) - 1 - suffix] == after[len(after) - 1 - suffix]:
        suffix += 1
    before_region = before[prefix : len(before) - suffix]
    after_region = after[prefix : len(after) - suffix]
    return prefix, before_region, after_region


def _deterministic_transform(
    kind: TransformKind, before: str, after: str
) -> Transform:
    """Ledger one whole-text deterministic stage as a single reversible Transform."""
    offset, before_region, after_region = _diff_span(before, after)
    return Transform(
        kind=kind,
        span=(offset, len(before_region)),
        before=before_region,
        after=after_region,
        proposed_by="deterministic",
        verified=True,
    )


def compose_canonical(
    raw_text: str,
    *,
    line_boundaries: Optional[Tuple[int, ...]] = None,
    llm_proposer: Optional[Callable[[str], str]] = None,
) -> CanonicalUnit:
    """Derive the clean canonical form of ``raw_text``, ledgering every transform.

    Deterministic lane first (trusted, always applied): line-break dehyphenation
    (``page_elements.dehyphenate``), then the inert whitespace/format quotient
    (``op_equivalence``'s folds). Each stage that materially changes the text is
    ledgered ``proposed_by="deterministic"``.

    THEN, only if ``llm_proposer`` is given, its proposed clean text is gated by
    :func:`verify_content_preserving` against the CURRENT (post-deterministic) text.
    A preserving proposal is accepted and ledgered as ``LLM_CLEANUP``
    (``proposed_by="llm"``, ``verified=True``); a non-preserving proposal is
    DISCARDED and never applied (nothing is ledgered for it).

    ``line_boundaries`` (raw character offsets of hard line breaks) is accepted for
    callers that track wrap structure; the deterministic whitespace quotient already
    folds line breaks to single spaces, so it is advisory context only.
    """
    del line_boundaries  # advisory; the whitespace quotient already folds line breaks.

    transforms: list[Transform] = []
    current = raw_text

    # Deterministic stage 1 — line-break dehyphenation (soft/discretionary hyphen joins).
    dehyphenated = dehyphenate(current)
    if dehyphenated != current:
        transforms.append(
            _deterministic_transform(TransformKind.DEHYPHENATE_LINEBREAK, current, dehyphenated)
        )
        current = dehyphenated

    # Deterministic stage 2 — the inert whitespace/format quotient (reused, not forked).
    # ``_canonicalize_text`` re-runs dehyphenate internally (now a no-op) and folds the
    # invisible/whitespace layer to the same canonical form the XML bridge uses.
    normalized, _folds = _canonicalize_text(current)
    if normalized != current:
        transforms.append(
            _deterministic_transform(TransformKind.WHITESPACE_NORMALIZE, current, normalized)
        )
        current = normalized

    # Distrusted LLM lane — admitted ONLY through the content-preservation gate.
    if llm_proposer is not None:
        proposed = llm_proposer(current)
        if proposed != current:
            verdict = verify_content_preserving(current, proposed)
            if verdict.preserving:
                offset, before_region, after_region = _diff_span(current, proposed)
                transforms.append(
                    Transform(
                        kind=TransformKind.LLM_CLEANUP,
                        span=(offset, len(before_region)),
                        before=before_region,
                        after=after_region,
                        proposed_by="llm",
                        verified=True,
                    )
                )
                current = proposed
            # else: non-preserving proposal discarded — never applied, never ledgered.

    return CanonicalUnit(
        clean_text=current,
        raw_text=raw_text,
        transforms=tuple(transforms),
        provenance="pdf_derived",
    )


def canonical_from_reference(xml_clean_text: str) -> CanonicalUnit:
    """The XML-backed no-op path: the trusted XML already IS the clean canonical form.

    Returns a :class:`CanonicalUnit` whose ``clean_text`` equals the reference text,
    with an EMPTY transform ledger and ``provenance="xml_reference"`` — no cleanup is
    derived because none is needed (the inert quotient bridging raw-PDF ↔ clean-XML
    lives in ``op_equivalence``; here the clean form is given, not derived).
    """
    return CanonicalUnit(
        clean_text=xml_clean_text,
        raw_text=xml_clean_text,
        transforms=(),
        provenance="xml_reference",
    )


def reconstruct_raw(unit: CanonicalUnit) -> str:
    """Reconstruct the raw extraction from ``clean_text`` by undoing the ledger.

    Walks the transforms newest-first, replacing each ``after`` region with its
    ``before`` region at the recorded offset. Because every transform stores its
    changed envelope verbatim, this exactly inverts the composition — the returned
    string equals ``unit.raw_text``. This is the ledger's reversibility/audit proof.
    """
    text = unit.clean_text
    for transform in reversed(unit.transforms):
        offset, _before_len = transform.span
        text = text[:offset] + transform.before + text[offset + len(transform.after) :]
    return text
