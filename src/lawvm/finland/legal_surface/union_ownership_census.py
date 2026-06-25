"""Cross-family UNION token-ownership census — the SourceSyntaxGraph "ruler".

This is L0 of the Layer-2 / SourceSyntaxGraph roadmap: the cross-family union
token-ownership census that every downstream Layer-2 lane is gated against. Build
the ruler before the thing it measures.

Where :mod:`lawvm.finland.legal_surface.family_census` runs ONE family parser
over a corpus and reports that family's PER-FAMILY total ownership (everything
not-this-family is benign/residual to it), this harness GENERALIZES to
CROSS-FAMILY: per provision it runs segmentation + ALL SIX family parsers
(citation / definition / temporal / modal / condition_exception / delegation)
over the segments and computes a single UNION partition of every body token:

  * **owned-by-construction** — the token's char span is claimed by at least one
    family parser as a NON-residual (typed) construction span (cue / addressee /
    object / definiendum / date / qualifier / grant span). The owning families are
    recorded (a token can be owned by more than one family — e.g. a modal cue that
    also sits inside a delegation grant).
  * **benign-prose** — owned by no family, and carries no cheap legal signal:
    whitespace, punctuation, ordinary prose words. Benign by construction.
  * **residual (explicit)** — owned by no family, but covered by a family's
    EXPLICIT typed residual whose reason is NOT the generic benign-prose reason
    (a known-but-uninterpreted structure the parser surfaced rather than dropped).
  * **silent-unowned** — owned by no family, not benign, and carrying a CHEAP
    LEGAL SIGNAL (``§`` / momentti / ``tulee voimaan`` / ``sovelletaan`` / a modal
    / ``tarkoitetaan`` / ``sen estämättä`` / ``HE`` / ``SopS`` / CELEX …). THE
    BLUEPRINT INVARIANT: this bucket MUST be ~0 or every member must be surfaced.
    A non-empty silent-unowned bucket is exactly where the grammar must grow.

The high-value steering number is the **cheap-legal-signal unowned-span metric**:
every cheap-signal span (single- or multi-token) that has NO owning typed
construction is recorded, generalized to a coarse SHAPE, and the shapes are ranked
by corpus frequency. That ranked table is the grammar-growth WORKLIST — the
SyntaxCoverage metric the L1 SourceSyntaxGraph forest will carry.

DISCIPLINE
==========
This is a MEASUREMENT harness. It READS the six existing family parsers and the
segmentation substrate; it MODIFIES no production behaviour, authorizes NO replay
/ apply, makes NO legal conclusion, and is OFF the replay/apply path (it imports
only the surface parsers + the corpus store + the tokenizer). Residuals are
self-evidencing: every surfaced unowned span carries its verbatim span text, not
an opaque count. Honors ``LAWVM_PARSE_TOTALITY`` only insofar as the family
parsers do; the union partition is computed directly from the parse residuals so
it does not depend on that env toggle.
"""
from __future__ import annotations

import os
import re
from lawvm.core.regex_safety import PrefilteredPattern, compile_classifier_regex
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from lawvm.core.legal_surface_tokens import ClauseIndex, Token, TokenTape

# The six family parse entry points. Each takes a span's EXACT text and returns a
# parse object carrying ``seg_start`` / ``seg_end`` / ``residuals`` (each residual
# a ``char_start`` / ``char_end`` / ``reason``) and a ``kind``. A ``declined`` parse
# owns nothing (one residual spanning the whole span with a decline reason); an
# owning parse leaves only ``benign_uninterpreted_prose`` residuals.
from lawvm.finland.legal_surface.condition_exception_parse import (
    parse_condition_exception_sentence,
)
from lawvm.finland.legal_surface.definition_parse import parse_definition_block
from lawvm.finland.legal_surface.delegation_parse import parse_delegation_sentence
from lawvm.finland.legal_surface.modal_parse import parse_modal_sentence
from lawvm.finland.legal_surface.sentence_parse import parse_citation_sentence
from lawvm.finland.legal_surface.temporal_parse import parse_temporal_sentence
from lawvm.finland.legal_surface.tokenize import build_token_tape

#: The benign-leftover residual reason every owning parse uses for the prose it
#: claims-but-does-not-interpret. A residual with THIS reason is benign; a residual
#: with any OTHER reason is either a decline (family owns nothing) or a typed
#: known-but-uninterpreted structure (the explicit-residual bucket).
_BENIGN_RESIDUAL_REASON = "benign_uninterpreted_prose"

#: The closed family roster. Order is report order. Each entry is
#: ``(family_id, parse_fn)``; the parse_fn signature is ``str -> parse object``.
FamilyParseFn = Callable[[str], object]
FAMILY_PARSERS: tuple[tuple[str, FamilyParseFn], ...] = (
    ("citation", parse_citation_sentence),
    ("definition", parse_definition_block),
    ("temporal", parse_temporal_sentence),
    ("modal", parse_modal_sentence),
    ("condition_exception", parse_condition_exception_sentence),
    ("delegation", parse_delegation_sentence),
)

# ---------------------------------------------------------------------------
# BODY-TEXT reference recognizers — the second ownership source.
# ---------------------------------------------------------------------------
#
# The six FAMILY_PARSERS above own the deontic/temporal/definitional surface;
# the citation family (sentence_parse) owns ONLY the (NNN/YYYY)-anchored and
# <ref>-element-shaped citations. The vast majority of BODY-TEXT references —
# bare ``§`` cross-refs (``5 §:ssä``), chapter-qualified internal refs
# (``3 luvun 5 §``), bare ``kohta`` / ``momentti`` self-references, by-name
# cross-statute references (``jätelain 5 §``), treaty (``SopS NNN/YYYY``) and
# EU-instrument-by-nickname (``<nickname> N artikla``) references — are owned by
# the dedicated reference recognizers in :mod:`lawvm.finland.references`, NOT by
# any of the six families. Those references were therefore counted as
# SILENT-UNOWNED here (the ``section_mark`` / ``kohta`` / ``momentti`` worklist),
# even though the reference layer already extracts them.
#
# This roster folds those recognizers in as a SECOND ownership source. Each entry
# is ``(family_id, recognize_fn)`` where ``recognize_fn`` returns char spans of the
# body-text reference surfaces it owns (see :func:`reference_owned_spans`). The
# union (:func:`union_over_sentence`) marks those chars OWNED, so a body-text
# reference the reference layer extracts is no longer silent-unowned, and the L1
# forest carries the same spans as ``reference_np`` leaves (coverage stays
# consistent BY CONSTRUCTION because both consume this one union).
#
# SCOPE BOUNDARY (grammar7: annotation is WITNESS, not construction): ONLY
# body-text CONSTRUCTION recognizers are included. The ``<ref>``-annotation lane
# (``ref_element`` explicit_id markup), the ``preparatory`` footer lane, and the
# ``affected_document`` preamble lane are EDITORIAL ANNOTATION, not body-text
# grammar, and are deliberately EXCLUDED — they are never forest construction
# leaves. (The census operates on ``decode_body_text`` output — the ``<p>`` body
# text with all ``<ref>``/footer markup already stripped — so the explicit_id
# inside ``<ref>`` markup never appears here at all; that boundary is deferred by
# design.)
#: A body-text reference family id → the char spans it owns in a sentence.
ReferenceSpanFn = Callable[[str], list[tuple[int, int]]]


def _internal_ref_spans(text: str) -> list[tuple[int, int]]:
    """Char spans owned by the internal (same-statute) bare-§ recognizer.

    Runs :func:`recognize_internal_refs` and locates each mention's verbatim
    ``surface_text`` (the captured ``§`` / ``kohta`` / ``momentti`` surface, which
    carries ``source_span=None`` and is re-anchored downstream). Coordinated runs
    share one surface; :func:`_locate_surfaces` walks document order so each
    occurrence maps to a distinct span.
    """
    from lawvm.finland.references.internal_refs import recognize_internal_refs

    mentions = recognize_internal_refs(text, "")
    return _locate_surfaces(text, [m.surface_text or "" for m in mentions])


def _by_name_ref_spans(text: str) -> list[tuple[int, int]]:
    """Char spans owned by the by-name cross-statute recognizer.

    Runs :func:`recognize_by_name_refs` and locates each mention's verbatim
    ``surface_text`` (name head + optional ``§`` tail).
    """
    from lawvm.finland.references.by_name import recognize_by_name_refs

    mentions = recognize_by_name_refs(text)
    return _locate_surfaces(text, [m.surface_text or "" for m in mentions])


def _treaty_ref_spans(text: str) -> list[tuple[int, int]]:
    """Char spans owned by the treaty-series (``SopS NNN/YYYY``) recognizer.

    :func:`recognize_treaty_refs` carries a reliable char ``source_span``
    (``byte_offset`` is a char offset into ``text``); we use it directly.
    """
    from lawvm.finland.references.treaty import recognize_treaty_refs

    spans: list[tuple[int, int]] = []
    for m in recognize_treaty_refs(text):
        sp = m.source_span
        if sp is not None and sp.byte_len > 0:
            spans.append((sp.byte_offset, sp.byte_offset + sp.byte_len))
    return spans


def _eu_directive_ref_spans(text: str) -> list[tuple[int, int]]:
    """Char spans owned by the EU-instrument-by-nickname (``N artikla``) recognizer.

    :func:`recognize_eu_directive_refs` returns ``EuDirectiveRef`` wrappers whose
    ``mention.surface_text`` is a verbatim slice of ``text``; locate those.
    """
    from lawvm.finland.references.eu_directive import recognize_eu_directive_refs

    drefs = recognize_eu_directive_refs(text)
    return _locate_surfaces(text, [d.mention.surface_text or "" for d in drefs])


#: The body-text reference recognizer roster. Order is report order. Each owns a
#: DISJOINT slice of the reference catalogue (internal / by-name / treaty / EU);
#: overlaps with the citation family are recorded as multi-family ownership, never
#: dropped. The ``ref_`` family-id prefix keeps them distinct from the six
#: construction families in the per-family ownership report.
REFERENCE_RECOGNIZERS: tuple[tuple[str, ReferenceSpanFn], ...] = (
    ("ref_internal", _internal_ref_spans),
    ("ref_by_name", _by_name_ref_spans),
    ("ref_treaty", _treaty_ref_spans),
    ("ref_eu", _eu_directive_ref_spans),
)


def _locate_surfaces(text: str, surfaces: list[str]) -> list[tuple[int, int]]:
    """Locate each verbatim surface in ``text`` as a char span, in document order.

    Mirrors the production re-anchoring convention
    (:func:`…ref_mention_extractor.extract_surface_grammar_mentions._relocate`):
    a per-surface cursor advances one occurrence per call, so several mentions
    that share the SAME surface (a coordinated run enumerated per member) map to
    distinct successive occurrences rather than all collapsing onto the first.
    A surface that does not occur verbatim (rare — only the by-name reassembled
    ``name + " " + tail`` form) is skipped (fail-loud by absence; never a
    fabricated span).
    """
    cursor: dict[str, int] = {}
    spans: list[tuple[int, int]] = []
    for surface in surfaces:
        if not surface:
            continue
        pos = text.find(surface, cursor.get(surface, 0))
        if pos < 0:
            continue
        cursor[surface] = pos + 1
        spans.append((pos, pos + len(surface)))
    return spans


def reference_owned_spans(sentence_text: str) -> dict[str, list[tuple[int, int]]]:
    """Run the body-text reference recognizers over one sentence; owned char spans.

    Returns ``{family_id: [(start, end), …]}`` for each
    :data:`REFERENCE_RECOGNIZERS` entry that owns >=1 span. This is the SECOND
    ownership source the union folds in (the first is :data:`FAMILY_PARSERS`).
    Faithful ASSEMBLY: it runs the existing recognizers and reports the char spans
    of the surfaces they own — it re-implements no recognizer grammar.
    """
    out: dict[str, list[tuple[int, int]]] = {}
    for family_id, span_fn in REFERENCE_RECOGNIZERS:
        spans = span_fn(sentence_text)
        if spans:
            out[family_id] = spans
    return out

# ---------------------------------------------------------------------------
# Cheap legal signals — the spans whose presence almost-certainly marks a legal
# construction. A cheap-signal span owned by NO family is the grammar-growth
# frontier. Each pattern is a CHEAP independent regex (NOT the parser's own cue
# list), so it is a genuine independent signal. The signal NAME is the coarse
# shape used to rank the worklist. Patterns are anchored / quantifier-bounded so
# they cannot backtrack (regex-discipline: no catastrophic backtracking).
# ---------------------------------------------------------------------------
_CHEAP_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str] | PrefilteredPattern], ...] = (
    # Structural address signals.
    ("section_mark", compile_classifier_regex(r"§", classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[section_mark]")),
    ("momentti", compile_classifier_regex(r"\b\d{1,3}\s+mom(?:entti|\.)", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[momentti]")),
    ("kohta", compile_classifier_regex(r"\b\d{1,3}\s+(?:ala)?koh[dt]a[a-zä]*\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[kohta]")),
    # Temporal / applicability signals.
    ("tulee_voimaan", compile_classifier_regex(r"\btulee\s+voimaan\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[tulee_voimaan]")),
    ("voimaan", compile_classifier_regex(r"\bvoimaan\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[voimaan]")),
    ("sovelletaan", compile_classifier_regex(r"\bsovelle[t]aan\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[sovelletaan]")),
    # Modal / deontic signals.
    ("modal_on_velvollinen", compile_classifier_regex(r"\bon\s+velvollinen\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[modal_on_velvollinen]")),
    ("modal_tulee", compile_classifier_regex(r"\btulee\b(?!\s+voimaan)", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[modal_tulee]")),
    ("modal_voi", compile_classifier_regex(r"\bvoi(?:daan)?\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[modal_voi]")),
    ("modal_saa", re.compile(r"\b(?:ei\s+)?saa\b", re.IGNORECASE)),
    # Definition signal.
    ("tarkoitetaan", compile_classifier_regex(r"\btarkoitetaan\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[tarkoitetaan]")),
    # Exception signal.
    ("sen_estamatta", compile_classifier_regex(r"\bsen\s+estämättä\b", re.IGNORECASE, classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[sen_estamatta]")),
    # Preparatory-work / treaty / EU reference signals.
    ("he_ref", compile_classifier_regex(r"\bHE\s+\d{1,4}/\d{4}\b", classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[he_ref]")),
    ("sops_ref", compile_classifier_regex(r"\bSopS\b", classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[sops_ref]")),
    ("celex", compile_classifier_regex(r"\b3\d{4}[A-Z]\d{4}\b", classifier_id="fi.legal_surface.union_ownership_census.cheap_signal_patterns[celex]")),
)


@dataclass(frozen=True)
class CensusSkip:
    """One corpus provision the census could NOT classify (a counted skip).

    The bare ``except Exception: continue`` that this replaces understated the
    partition denominator silently: a statute whose body failed to decode or
    whose classification raised simply vanished, so the census reported coverage
    over a SMALLER corpus than it claimed to scan. Each skip is now a counted,
    self-evidencing residual: it carries the statute id, the phase that failed
    (``decode`` or ``classify``), and the verbatim exception text — never an
    opaque drop.

    Attributes:
        statute_id: The statute that could not be classified.
        phase:      Where it failed — ``"decode"`` (body decode raised / empty)
                    or ``"classify"`` (the union partition raised).
        reason:     The verbatim exception text (or ``"empty_body"`` for a
                    decode that produced no body), for adjudication.
    """

    statute_id: str
    phase: str
    reason: str


@dataclass(frozen=True)
class UnownedSignalSpan:
    """One cheap-legal-signal span owned by NO family (a worklist item).

    Self-evidencing: carries the verbatim ``text`` of the offending span (NOT an
    opaque count) plus the statute it came from, so the worklist is auditable.

    Attributes:
        statute_id: The statute the span came from.
        shape:      The coarse cheap-signal NAME (the worklist rank key).
        text:       The verbatim span text (self-evidencing residue).
        context:    A short verbatim window around the span, for adjudication.
    """

    statute_id: str
    shape: str
    text: str
    context: str


@dataclass(frozen=True)
class UnionOwnershipResult:
    """Outcome of a cross-family union token-ownership census run.

    Attributes:
        statutes_scanned: Provisions (statute bodies) decoded + segmented AND
                          successfully classified into the partition.
        statutes_skipped: Provisions that could NOT be classified (body decode
                          failed/empty, or classification raised). Counted, not
                          silently dropped — the denominator-honesty fix. Equals
                          ``len(skips)``.
        sentences:        Sentence segments fed to the family parsers.
        total_tokens:     Total NON-WHITESPACE body tokens classified (whitespace
                          is contiguous filler, always benign; it is excluded from
                          the partition denominator so the percentages describe
                          the SIGNAL-BEARING tokens).
        owned_tokens:     Tokens whose span is claimed by >=1 family construction.
        benign_tokens:    Unowned tokens with NO cheap legal signal (benign prose).
        residual_tokens:  Unowned tokens inside an EXPLICIT typed (non-benign)
                          residual span.
        silent_tokens:    Unowned, non-benign tokens carrying a cheap legal signal
                          (the BUG/worklist class — MUST be ~0 or surfaced).
        family_token_counts: family_id -> tokens that family owned (overlaps
                          allowed; sums may exceed owned_tokens).
        unowned_shape_counts: cheap-signal SHAPE -> count of UNOWNED spans of that
                          shape (the ranked grammar-growth worklist).
        unowned_examples: a few self-evidencing example unowned spans per shape.
        skips:            self-evidencing records of every provision skipped
                          (decode/classify failure) — counted, never silent.
    """

    statutes_scanned: int
    statutes_skipped: int
    sentences: int
    total_tokens: int
    owned_tokens: int
    benign_tokens: int
    residual_tokens: int
    silent_tokens: int
    family_token_counts: dict[str, int]
    unowned_shape_counts: dict[str, int]
    unowned_examples: tuple[UnownedSignalSpan, ...] = field(default_factory=tuple)
    skips: tuple[CensusSkip, ...] = field(default_factory=tuple)

    @property
    def partition_total(self) -> int:
        return (
            self.owned_tokens
            + self.benign_tokens
            + self.residual_tokens
            + self.silent_tokens
        )

    def is_partition(self) -> bool:
        """The four token buckets sum to the classified-token total (no leak)."""
        return self.partition_total == self.total_tokens


# ---------------------------------------------------------------------------
# Per-provision union ownership (the reusable core; corpus-independent so the
# focused test can exercise it on synthetic provisions directly).
# ---------------------------------------------------------------------------


def _family_owned_spans(parse_obj: object) -> list[tuple[int, int]]:
    """The body-LOCAL char spans one family parse claims as a typed construction.

    Owned = ``[seg_start, seg_end)`` minus the parse's residual spans (which are
    EITHER the whole span for a declined parse — owning nothing — OR the benign
    leftover of an owning parse). Returns spans in the parse's own local
    coordinate system (the caller offsets them to body coordinates).
    """
    seg_start = getattr(parse_obj, "seg_start", 0)
    seg_end = getattr(parse_obj, "seg_end", 0)
    residuals = getattr(parse_obj, "residuals", ())
    blocked = sorted(
        (r.char_start, r.char_end) for r in residuals if r.char_end > r.char_start
    )
    owned: list[tuple[int, int]] = []
    cursor = seg_start
    for r_start, r_end in blocked:
        r_start = max(r_start, seg_start)
        r_end = min(r_end, seg_end)
        if r_start > cursor:
            owned.append((cursor, r_start))
        cursor = max(cursor, r_end)
    if cursor < seg_end:
        owned.append((cursor, seg_end))
    return owned


def _typed_residual_spans(parse_obj: object) -> list[tuple[int, int]]:
    """Residual spans whose reason is a TYPED known-but-uninterpreted structure.

    A residual with the benign-prose reason is benign (not surfaced); a residual
    of a DECLINED parse is "family owns nothing here" (also not a typed residual —
    another family may own it). Only a residual with a NON-benign, NON-decline
    reason on an OWNING parse is an explicit typed residual. We approximate "owning
    parse" by ``kind != 'declined'``: a declined parse's whole-span residual is the
    family's abstention, not a typed-residue claim.
    """
    kind = getattr(parse_obj, "kind", "")
    if kind == "declined":
        return []
    spans: list[tuple[int, int]] = []
    for r in getattr(parse_obj, "residuals", ()):
        if r.reason != _BENIGN_RESIDUAL_REASON and r.char_end > r.char_start:
            spans.append((r.char_start, r.char_end))
    return spans


@dataclass(frozen=True)
class _SentenceUnion:
    """Per-sentence union ownership over the six families (sentence-local coords)."""

    #: char -> owning family ids (only chars some family claims).
    owners: dict[int, frozenset[str]]
    #: explicit typed-residual char ranges (sentence-local).
    typed_residual: list[tuple[int, int]]
    #: family_id -> parse-kind tag recovered from the family parse object.
    family_kinds: dict[str, str]


@dataclass(frozen=True, slots=True)
class SentenceUnionAnalysis:
    """One sentence and its already-computed ownership union."""

    char_start: int
    char_end: int
    text: str
    union: _SentenceUnion


@dataclass(frozen=True, slots=True)
class BodyUnionAnalysis:
    """Body-level union ownership analysis with reusable sentence unions."""

    bucket_counts: Counter[str]
    family_counts: Counter[str]
    unowned_shape_counts: Counter[str]
    unowned_examples: tuple[UnownedSignalSpan, ...]
    sentence_count: int
    sentence_unions: tuple[SentenceUnionAnalysis, ...]


def union_over_sentence(sentence_text: str) -> _SentenceUnion:
    """Union the six family parsers AND the body-text reference recognizers.

    Returns the per-char owning-family map and the explicit typed-residual ranges.
    TWO ownership sources are unioned: the six :data:`FAMILY_PARSERS`
    (deontic/temporal/definitional surface + (NNN/YYYY)-anchored citations) AND
    the :data:`REFERENCE_RECOGNIZERS` (the body-text bare-§ / kohta / momentti /
    by-name / treaty / EU references the citation family does not own). Where the
    two overlap on a char, BOTH owning families are recorded (multi-family
    ownership — no family dropped). Pure: depends only on those recognizers; no
    corpus, no I/O. This is the reusable core the corpus harness calls per
    sentence, the L1 forest assembler reuses, and the focused test exercises on
    synthetic provisions.
    """
    owners: dict[int, set[str]] = {}
    typed_residual: list[tuple[int, int]] = []
    family_kinds: dict[str, str] = {}
    for family_id, parse_fn in FAMILY_PARSERS:
        parse_obj = parse_fn(sentence_text)
        if family_id == "condition_exception":
            for q in getattr(parse_obj, "qualifiers", ()):
                if getattr(q, "kind", "") == "exception":
                    family_kinds[family_id] = "exception"
                    break
        for s, e in _family_owned_spans(parse_obj):
            for i in range(s, e):
                owners.setdefault(i, set()).add(family_id)
        typed_residual.extend(_typed_residual_spans(parse_obj))
    # Second ownership source: the body-text reference recognizers. Their owned
    # surfaces become owner chars under a ``ref_*`` family id, so a bare ``§`` /
    # kohta / momentti / by-name / treaty / EU reference is no longer
    # silent-unowned (and the L1 forest carries the same spans as reference_np).
    for family_id, spans in reference_owned_spans(sentence_text).items():
        for s, e in spans:
            for i in range(s, e):
                owners.setdefault(i, set()).add(family_id)
    return _SentenceUnion(
        owners={i: frozenset(fams) for i, fams in owners.items()},
        typed_residual=typed_residual,
        family_kinds=family_kinds,
    )


def _cheap_signal_spans(text: str) -> list[tuple[int, int, str]]:
    """All cheap-legal-signal spans in ``text`` as ``(start, end, shape)``.

    Each pattern is scanned independently; overlapping signals (e.g. ``voimaan``
    inside ``tulee voimaan``) are all recorded — the union owner check then
    decides which are unowned, and the longest/most-specific shape naturally
    dominates the worklist because it has its own rank row.
    """
    spans: list[tuple[int, int, str]] = []
    for shape, pat in _CHEAP_SIGNAL_PATTERNS:
        for m in pat.finditer(text):
            if m.end() > m.start():
                spans.append((m.start(), m.end(), shape))
    return spans


def _span_is_owned(start: int, end: int, owners: dict[int, frozenset[str]]) -> bool:
    """A cheap-signal span counts as owned iff ANY of its chars is owned.

    A signal whose surface is even partly claimed by a typed construction is on
    the grammar's radar; we only flag the span as unowned when NOT ONE char of it
    is claimed by any family — the conservative no-false-frontier rule.
    """
    return any(i in owners for i in range(start, end))


# ---------------------------------------------------------------------------
# Token classification over a body (the partition).
# ---------------------------------------------------------------------------


def _context_window(text: str, start: int, end: int, radius: int = 30) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


def classify_body(
    statute_id: str,
    body: str,
    *,
    max_examples_per_shape: int = 3,
) -> tuple[
    Counter[str],
    Counter[str],
    Counter[str],
    list[UnownedSignalSpan],
    int,
]:
    """Classify every non-whitespace token of one body into the union partition.

    Returns ``(bucket_counts, family_counts, unowned_shape_counts,
    unowned_examples, sentence_count)`` for this body, to be accumulated by the
    corpus driver. ``bucket_counts`` keys are
    ``owned`` / ``benign`` / ``residual`` / ``silent``.

    Builds the body token tape, segments the body into sentences, runs the union
    over each sentence (offsetting owned spans + cheap-signal spans to body
    coordinates), then walks the tape and buckets each non-whitespace token.
    """
    analysis = analyze_body_union(
        statute_id,
        body,
        max_examples_per_shape=max_examples_per_shape,
    )
    return (
        analysis.bucket_counts,
        analysis.family_counts,
        analysis.unowned_shape_counts,
        list(analysis.unowned_examples),
        analysis.sentence_count,
    )


def analyze_body_union(
    statute_id: str,
    body: str,
    *,
    max_examples_per_shape: int = 3,
    token_tape: TokenTape | None = None,
    clause_index: ClauseIndex | None = None,
) -> BodyUnionAnalysis:
    """Classify ``body`` and retain the per-sentence union results for reuse."""
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    tape = token_tape or build_token_tape(statute_id, body)
    index = clause_index or build_clause_index(statute_id, body, token_tape=tape)

    # Body-coordinate owner map + typed-residual ranges + cheap-signal spans.
    owners: dict[int, frozenset[str]] = {}
    typed_residual_chars: set[int] = set()
    cheap_spans: list[tuple[int, int, str]] = []
    sentence_count = 0
    sentence_unions: list[SentenceUnionAnalysis] = []
    for sent in index.sentences:
        sentence_count += 1
        seg_text = body[sent.char_start : sent.char_end]
        off = sent.char_start
        su = union_over_sentence(seg_text)
        sentence_unions.append(
            SentenceUnionAnalysis(
                char_start=sent.char_start,
                char_end=sent.char_end,
                text=seg_text,
                union=su,
            )
        )
        for i, fams in su.owners.items():
            owners[off + i] = fams
        for s, e in su.typed_residual:
            for i in range(s, e):
                typed_residual_chars.add(off + i)
        for s, e, shape in _cheap_signal_spans(seg_text):
            cheap_spans.append((off + s, off + e, shape))

    # A cheap-signal span is "owned" if any of its chars is owned by a family.
    unowned_signal_chars: dict[int, str] = {}
    unowned_shape_counts: Counter[str] = Counter()
    unowned_examples: list[UnownedSignalSpan] = []
    shape_example_count: Counter[str] = Counter()
    for s, e, shape in cheap_spans:
        if _span_is_owned(s, e, owners):
            continue
        unowned_shape_counts[shape] += 1
        for i in range(s, e):
            unowned_signal_chars[i] = shape
        if shape_example_count[shape] < max_examples_per_shape:
            shape_example_count[shape] += 1
            unowned_examples.append(
                UnownedSignalSpan(
                    statute_id=statute_id,
                    shape=shape,
                    text=body[s:e],
                    context=_context_window(body, s, e),
                )
            )

    bucket_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    for tok in tape.tokens:
        if tok.category == "whitespace":
            continue
        fams = _token_owner_families(tok, owners)
        if fams:
            bucket_counts["owned"] += 1
            for f in fams:
                family_counts[f] += 1
            continue
        # Unowned: decide silent (cheap signal) > residual (typed) > benign.
        if _token_overlaps(tok, unowned_signal_chars):
            bucket_counts["silent"] += 1
        elif _token_overlaps_set(tok, typed_residual_chars):
            bucket_counts["residual"] += 1
        else:
            bucket_counts["benign"] += 1

    return BodyUnionAnalysis(
        bucket_counts=bucket_counts,
        family_counts=family_counts,
        unowned_shape_counts=unowned_shape_counts,
        unowned_examples=tuple(unowned_examples),
        sentence_count=sentence_count,
        sentence_unions=tuple(sentence_unions),
    )


def _token_owner_families(
    tok: Token, owners: dict[int, frozenset[str]]
) -> frozenset[str]:
    """Union of owning families across the token's chars (empty if unowned).

    A token is owned if ANY of its chars is claimed by a family construction (a
    multi-char token whose head is claimed is owned — partial claim = on radar).
    """
    fams: set[str] = set()
    for i in range(tok.char_start, tok.char_end):
        f = owners.get(i)
        if f:
            fams |= f
    return frozenset(fams)


def _token_overlaps(tok: Token, char_shapes: dict[int, str]) -> bool:
    return any(i in char_shapes for i in range(tok.char_start, tok.char_end))


def _token_overlaps_set(tok: Token, chars: set[int]) -> bool:
    return any(i in chars for i in range(tok.char_start, tok.char_end))


# ---------------------------------------------------------------------------
# Corpus driver.
# ---------------------------------------------------------------------------


def _iter_corpus_ids(
    limit: int,
    min_year: int,
    skips_out: list[CensusSkip] | None = None,
) -> Iterator[tuple[str, str]]:
    """Yield ``(statute_id, body)`` over the corpus slice. Lazy corpus import.

    A provision whose source is absent or whose body fails to decode (or decodes
    empty) is NOT silently dropped: a counted :class:`CensusSkip` (phase
    ``"decode"``) is appended to ``skips_out`` so the corpus denominator stays
    honest. A statute with no source at all is skipped silently by design (it is
    not in the corpus universe being measured); only a present-but-undecodable /
    empty body is a counted skip.
    """
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]
    # Even, deterministic sampling across the slice (not just the first N, which
    # skews to the oldest statutes) when a limit caps a larger corpus.
    if limit and limit < len(ids):
        step = len(ids) / limit
        ids = [ids[int(i * step)] for i in range(limit)]
    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            body = decode_body_text(xb)
        except Exception as exc:
            if skips_out is not None:
                skips_out.append(
                    CensusSkip(statute_id=sid, phase="decode", reason=repr(exc))
                )
            continue
        if not body:
            if skips_out is not None:
                skips_out.append(
                    CensusSkip(statute_id=sid, phase="decode", reason="empty_body")
                )
            continue
        yield sid, body


def run_union_ownership_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    max_examples_per_shape: int = 5,
) -> UnionOwnershipResult:
    """Run the cross-family union token-ownership census over a corpus slice.

    Sampling mirrors the family censuses (``min_year`` / ``limit``), but ``limit``
    samples EVENLY across the slice (deterministic stride), not just the oldest N.
    Requires the canonical corpus (``LAWVM_CANONICAL_DATA_ROOT`` /
    ``LAWVM_FARCHIVE_DB``); imports it lazily so the module stays importable (and
    the focused test stays corpus-free).
    """
    bucket_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    unowned_shape_counts: Counter[str] = Counter()
    unowned_examples: list[UnownedSignalSpan] = []
    shape_example_count: Counter[str] = Counter()
    skips: list[CensusSkip] = []
    statutes_scanned = 0
    sentences = 0

    for sid, body in _iter_corpus_ids(limit, min_year, skips_out=skips):
        try:
            (
                bc,
                fc,
                usc,
                examples,
                sent_count,
            ) = classify_body(sid, body, max_examples_per_shape=max_examples_per_shape)
        except Exception as exc:
            # Do NOT silently drop a classification failure: count it as a
            # self-evidencing skip so the partition denominator stays honest.
            skips.append(
                CensusSkip(statute_id=sid, phase="classify", reason=repr(exc))
            )
            continue
        statutes_scanned += 1
        sentences += sent_count
        bucket_counts.update(bc)
        family_counts.update(fc)
        unowned_shape_counts.update(usc)
        for ex in examples:
            if shape_example_count[ex.shape] < max_examples_per_shape:
                shape_example_count[ex.shape] += 1
                unowned_examples.append(ex)

    owned = bucket_counts.get("owned", 0)
    benign = bucket_counts.get("benign", 0)
    residual = bucket_counts.get("residual", 0)
    silent = bucket_counts.get("silent", 0)
    total = owned + benign + residual + silent

    return UnionOwnershipResult(
        statutes_scanned=statutes_scanned,
        statutes_skipped=len(skips),
        sentences=sentences,
        total_tokens=total,
        owned_tokens=owned,
        benign_tokens=benign,
        residual_tokens=residual,
        silent_tokens=silent,
        family_token_counts=dict(family_counts),
        unowned_shape_counts=dict(unowned_shape_counts),
        unowned_examples=tuple(unowned_examples),
        skips=tuple(skips),
    )


def format_union_ownership_report(result: UnionOwnershipResult) -> str:
    """Render the cross-family union token-ownership scoreboard as text."""
    total = result.total_tokens

    def pct(n: int) -> str:
        return f"{100 * n / total:.2f}%" if total else "n/a"

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("FI CROSS-FAMILY UNION TOKEN-OWNERSHIP CENSUS (the SyntaxCoverage ruler)")
    lines.append("=" * 72)
    lines.append(f"  statutes scanned                : {result.statutes_scanned}")
    lines.append(f"  statutes skipped (decode/classify): {result.statutes_skipped}")
    lines.append(f"  sentences segmented             : {result.sentences}")
    lines.append(f"  signal-bearing tokens classified: {result.total_tokens}")
    lines.append("-" * 72)
    lines.append(f"  owned-by-construction           : {result.owned_tokens:7d}  ({pct(result.owned_tokens)})")
    lines.append(f"  benign-prose                    : {result.benign_tokens:7d}  ({pct(result.benign_tokens)})")
    lines.append(f"  residual (explicit typed)       : {result.residual_tokens:7d}  ({pct(result.residual_tokens)})")
    lines.append(f"  SILENT-UNOWNED (cheap signal)   : {result.silent_tokens:7d}  ({pct(result.silent_tokens)})")
    lines.append("-" * 72)
    lines.append(
        f"  partition sum                   : {result.partition_total:7d}  "
        f"(== classified: {result.is_partition()})"
    )
    lines.append("")

    if result.family_token_counts:
        lines.append("-" * 72)
        lines.append("per-family token ownership (overlaps allowed; sum >= owned)")
        lines.append("-" * 72)
        for fam, n in sorted(result.family_token_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {fam:<22}: {n:7d}")
        lines.append("")

    lines.append("-" * 72)
    lines.append("UNOWNED cheap-signal SHAPES (ranked — the grammar-growth worklist)")
    lines.append("-" * 72)
    if result.unowned_shape_counts:
        for shape, n in sorted(
            result.unowned_shape_counts.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {n:7d}  {shape}")
    else:
        lines.append("  (none — every cheap-signal span is owned by a typed construction)")
    lines.append("")

    if result.unowned_examples:
        lines.append("-" * 72)
        lines.append("unowned-signal examples (self-evidencing; verbatim span + context)")
        lines.append("-" * 72)
        for ex in result.unowned_examples:
            lines.append(f"  [{ex.statute_id}] shape={ex.shape}")
            lines.append(f"    span   : {ex.text!r}")
            lines.append(f"    context: {ex.context!r}")
        lines.append("")

    if result.skips:
        lines.append("-" * 72)
        lines.append(
            "SKIPPED provisions (counted, not silently dropped — denominator honest)"
        )
        lines.append("-" * 72)
        skip_phase_counts: Counter[str] = Counter(s.phase for s in result.skips)
        for phase, n in sorted(skip_phase_counts.items()):
            lines.append(f"  {phase:<10}: {n}")
        # A few self-evidencing examples (verbatim exception text).
        for sk in result.skips[:10]:
            lines.append(f"  [{sk.statute_id}] phase={sk.phase} reason={sk.reason!r}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    import sys

    # Usage: python -m lawvm.finland.legal_surface.union_ownership_census [LIMIT] [MIN_YEAR]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = run_union_ownership_census(limit=limit, min_year=min_year)
    print(format_union_ownership_report(result))
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but classified tokens = {result.total_tokens}"
        )
    # Honor LAWVM_PARSE_TOTALITY as a HARD gate when explicitly set: the
    # silent-unowned bucket is the no-silent-drop invariant for the union.
    if os.environ.get("LAWVM_PARSE_TOTALITY") and result.silent_tokens:
        raise SystemExit(
            f"SILENT-UNOWNED VIOLATION: {result.silent_tokens} cheap-signal tokens "
            "owned by no construction (see ranked worklist above)"
        )


if __name__ == "__main__":
    main()
