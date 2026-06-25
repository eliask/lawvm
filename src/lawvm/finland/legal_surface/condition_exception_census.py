"""Differential census for the condition / exception construction.

The FIFTH and final core construction-grammar island after the citation pilot
(:mod:`lawvm.finland.legal_surface.sentence_census`), the definition pilot
(:mod:`lawvm.finland.legal_surface.definition_census`), the temporal island
(:mod:`lawvm.finland.legal_surface.temporal_census`), and the modal island
(:mod:`lawvm.finland.legal_surface.modal_census`), built on the family-agnostic
engine (:mod:`lawvm.finland.legal_surface.family_census`). It wires the
condition/exception family's FOUR engine plug-points:

  1. segment-selector — :func:`_condexc_segment_selector` yields, per statute,
     each SENTENCE segment of the decoded body (from the SegmentationGraph
     substrate's ``build_clause_index`` ``sentences`` view) that carries a
     condition/exception cue (the in-scope family discriminator).
  2. projection-fn   — :func:`_condexc_projection`: the
     :class:`ConditionExceptionParse` projection's qualifier-key set
     (:func:`projection_condexc_keys`).
  3. oracle-fn       — :func:`_condexc_oracle`: the PRODUCTION H6 exception/
     condition lens (``references.exception_condition.recognize_exception_condition_cues``)
     over the SAME span, lifted to the same ``kind:cue`` key form (honest
     comparison — identical coordinate space, identical key derivation).
  4. miss-shape-fn   — :func:`_condexc_miss_shape`: coarse structural class of a
     missed qualifier (cue kind) for ranking what blocks miss=0.

WEAK ORACLE CAVEAT (read before trusting miss == 0)
===================================================
The production H6 lens OVER-generates: one cue-fact per closed-list cue with a
proximity ``scope_hint`` (no requirement that the cue heads a real adjunct
clause). Because the construction parse MIRRORS the production closed cue lists
and the production ``jos`` / ``kun`` clause-initial guard, the projection keys on
the same cues the oracle finds, so:

  * ``miss == 0`` means little — the oracle finds a cue-fact wherever the
    projection does. The real gates are (a) total-token-ownership / no-silent-drop
    (``LAWVM_PARSE_TOTALITY``); (b) the CHEAP-SIGNAL proxy
    (:func:`compute_cheap_signal_coverage`) — sentences with a condition/exception
    cue surface that the construction parse fails to turn into a qualifier; and
    (c) the ATTACHMENT-QUALITY spot-check (:func:`compute_attachment_quality`) —
    does the construction attach to a deontic core / correctly mark ambiguous,
    rather than the proximity window's over-attachment.
  * Expect supersets where the construction keys a cue the oracle's guard dropped
    (and misses where the oracle's ``jos`` / ``kun`` proximity guard differs from
    the construction's clause-initial guard) — both reported NEUTRALLY.

The census comparison is per SENTENCE: the projection's qualifier-key set vs the
production oracle's qualifier-key set over the same char span, classified
match / superset / miss / decline. Honors ``LAWVM_PARSE_TOTALITY`` via the
:class:`ConditionExceptionParse` ``assert_total_ownership`` postcondition.

Pure measure-only. Changes no production behavior; off the replay/apply path.
"""
from __future__ import annotations

import re
from lawvm.core.regex_safety import PrefilteredPattern, compile_classifier_regex
from collections.abc import Iterator
from dataclasses import dataclass

from lawvm.finland.legal_surface.condition_exception_parse import (
    ATTACH_AMBIGUOUS,
    ATTACH_CANDIDATE,
    ATTACH_RESOLVED,
    CONDEXC_LANE_DECLINED,
    assert_total_ownership,
    condexc_key,
    parse_condition_exception_sentence,
    projection_condexc_keys,
)
from lawvm.finland.legal_surface.family_census import (
    CensusUnit,
    FamilyCensusResult,
    format_family_census_report,
    run_family_census,
)

#: Family id passed to the generalized engine.
CONDEXC_FAMILY = "exception_condition"


def _condexc_oracle_keys_for_span(text: str) -> set[str]:
    """Run the PRODUCTION H6 exception/condition lens over a span → census key set.

    Wraps ``recognize_exception_condition_cues`` (the H6 surface lens) and, per
    recognised cue, lifts it to a ``{kind}:{cue}`` key matching the projection's
    :func:`condexc_key`: the cue kind (``condition`` / ``exception``, lower-cased
    from the production ``EXCEPTION`` / ``CONDITION``) and the normalised cue
    surface (casefolded). This is the differential-census ORACLE: the current
    production H6 output for the span. It is a WEAK (over-generating) oracle — see
    the module caveat — so the census miss bucket measures little.
    """
    from lawvm.finland.references.exception_condition import (
        recognize_exception_condition_cues,
    )

    keys: set[str] = set()
    for cue in recognize_exception_condition_cues(text):
        kind = cue.cue_kind.casefold()  # EXCEPTION -> exception / CONDITION -> condition
        keys.add(condexc_key(kind, cue.marker_text.casefold()))
    return keys


def _condexc_segment_selector(sid: str, body: str) -> Iterator[CensusUnit]:
    """Yield the in-scope condition/exception sentence units of one statute.

    Segments the body into sentences via the SegmentationGraph substrate
    (``build_clause_index``) and yields one :class:`CensusUnit` per sentence whose
    construction parse produced >=1 qualifier (the in-scope family discriminator —
    a condition/exception cue that survives the ``jos`` / ``kun`` clause-initial
    guard). A sentence whose construction parse declined (a cue that did NOT yield
    a qualifier, e.g. a mid-clause ``jos`` failing the guard, or no cue at all) is
    NOT yielded — like the modal family's copula skip, an out-of-family sentence is
    not a construction decline.
    """
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    index = build_clause_index(sid, body)
    for sent in index.sentences:
        seg_text = body[sent.char_start : sent.char_end]
        cp = parse_condition_exception_sentence(seg_text)
        if cp.parser_lane == CONDEXC_LANE_DECLINED:
            # No qualifier parsed → out of family (not a construction decline).
            continue
        totality_ok = True
        try:
            assert_total_ownership(cp)
        except AssertionError:
            totality_ok = False
        kind = cp.qualifiers[0].kind if cp.qualifiers else "-"
        yield CensusUnit(
            text=seg_text,
            parser_lane=cp.parser_lane,
            declared_marker=f"sentence:{kind}",
            declined=cp.parser_lane == CONDEXC_LANE_DECLINED,
            totality_ok=totality_ok,
        )


def _condexc_projection(unit: CensusUnit, sid: str) -> set[str]:
    return projection_condexc_keys(parse_condition_exception_sentence(unit.text))


def _condexc_oracle(unit: CensusUnit, _ctx: object = None) -> set[str]:
    # The condition/exception family's oracle is span-local (it runs the production
    # H6 recognizer over the unit text), so it ignores the per-statute oracle
    # context the engine threads through.
    return _condexc_oracle_keys_for_span(unit.text)


def _condexc_miss_shape(missing_keys: set[str], declared_marker: str) -> str:
    """Coarse structural class of a missed qualifier (what blocks miss=0).

    A qualifier key is ``<kind>:<cue>``. The shape names the missed kind(s)
    (condition / exception) — which kind of qualifier the projection lacked that
    the (weak) oracle found.
    """
    kinds = sorted({k.split(":", 1)[0] for k in missing_keys if ":" in k})
    return "+".join(kinds) if kinds else "nokind"


# ---------------------------------------------------------------------------
# Cheap-signal proxy (the REAL recall gate for this weak-oracle family)
# ---------------------------------------------------------------------------

#: Cheap surface signals that a sentence almost-certainly carries a
#: condition/exception qualifier, used to estimate recall INDEPENDENTLY of the
#: construction parser and the (over-generating) production oracle. A sentence
#: matching one of these but yielding NO construction qualifier is a candidate
#: recall miss. These overlap the closed cue lists but are spelled as cheap
#: regexes so they are a genuine independent signal.
_CHEAP_SIGNALS: tuple[re.Pattern[str] | PrefilteredPattern, ...] = (
    compile_classifier_regex(r"\bjos\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[jos]"),
    compile_classifier_regex(r"\bjollei\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[jollei]"),
    compile_classifier_regex(r"\bellei\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[ellei]"),
    compile_classifier_regex(r"\bkun\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[kun]"),
    compile_classifier_regex(r"\bmikäli\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[mikali]"),
    compile_classifier_regex(r"\bei\s+kuitenkaan\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[ei_kuitenkaan]"),
    compile_classifier_regex(r"\bsen\s+estämättä\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[sen_estamatta]"),
    compile_classifier_regex(r"\bpaitsi\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[paitsi]"),
    compile_classifier_regex(r"\blukuun\s+ottamatta\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[lukuun_ottamatta]"),
    compile_classifier_regex(r"\bpoiketen\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[poiketen]"),
    compile_classifier_regex(r"\bedellyttäen\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[edellyttaen]"),
    compile_classifier_regex(r"\bsiltä\s+osin\b", re.IGNORECASE, classifier_id="fi.legal_surface.condition_exception_census.cheap_signals[silta_osin]"),
)


@dataclass(frozen=True)
class CheapSignalCoverage:
    """Cheap-signal condition/exception recall proxy for one census run.

    Attributes:
        sentences_with_signal: Sentences carrying any cheap condition/exception
                               signal.
        sentences_with_qualifier: Of those, sentences whose construction parse
                               produced >=1 qualifier.
        candidate_misses:      ``sentences_with_signal - sentences_with_qualifier``
                               — cheap-signal-bearing sentences with NO qualifier
                               (the candidate recall frontier; mostly the
                               ``jos`` / ``kun`` mid-clause guard skips).
        miss_examples:         A few example candidate-miss sentence snippets.
    """

    sentences_with_signal: int
    sentences_with_qualifier: int
    candidate_misses: int
    miss_examples: tuple[str, ...] = ()

    @property
    def coverage(self) -> float:
        if not self.sentences_with_signal:
            return 1.0
        return self.sentences_with_qualifier / self.sentences_with_signal


def _has_cheap_signal(text: str) -> bool:
    return any(p.search(text) is not None for p in _CHEAP_SIGNALS)


# ---------------------------------------------------------------------------
# Attachment-quality spot-check (the construction-grammar value over proximity)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachmentQuality:
    """Distribution of qualifier attachment statuses over a census run.

    The construction's value over the production proximity ``scope_hint`` is that
    it attaches each qualifier to a deontic core (modal island) with an explicit
    confidence: ``resolved`` (exactly one core → unambiguous), ``ambiguous``
    (several cores → flagged, never silently picked), ``candidate`` (no core →
    target not yet typed). High ``resolved`` share + a real ``ambiguous`` share
    (instead of silent over-attachment) is the quality verdict.

    Attributes:
        qualifiers_total:   All qualifiers parsed across the slice.
        resolved:           Qualifiers attached to exactly one deontic core.
        ambiguous:          Qualifiers flagged ambiguous (multiple candidate cores).
        candidate:          Qualifiers with no deontic core to attach to.
        examples:           A few (status, cue, snippet) example tuples.
    """

    qualifiers_total: int
    resolved: int
    ambiguous: int
    candidate: int
    examples: tuple[tuple[str, str, str], ...] = ()

    @property
    def resolved_share(self) -> float:
        if not self.qualifiers_total:
            return 0.0
        return self.resolved / self.qualifiers_total


def _iter_corpus_sentences(
    *, limit: int, min_year: int
) -> Iterator[tuple[str, str]]:
    """Yield ``(statute_id, sentence_text)`` over the corpus slice.

    Shared iteration for the two oracle-independent proxies (cheap-signal +
    attachment-quality), mirroring the census engine's sampling: ``min_year`` /
    ``limit`` restrict the slice exactly as :func:`run_family_census` does.
    """
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.legal_surface.clause_segment import build_clause_index
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]
    if limit:
        ids = ids[:limit]

    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            body = decode_body_text(xb)
        except Exception:
            continue
        if not body:
            continue
        try:
            index = build_clause_index(sid, body)
        except Exception:
            continue
        for sent in index.sentences:
            yield sid, body[sent.char_start : sent.char_end]


def compute_cheap_signal_coverage(
    *, limit: int = 0, min_year: int = 0, max_examples: int = 8
) -> CheapSignalCoverage:
    """Compute the cheap-signal condition/exception recall proxy over the slice.

    Iterates the SAME corpus slice the census uses; for each sentence carrying a
    cheap condition/exception signal records whether the construction parse
    produced >=1 qualifier. The ratio is an oracle-INDEPENDENT recall estimate;
    the candidate misses (cheap signal but no qualifier) are the recall frontier —
    expected to be dominated by the deliberate ``jos`` / ``kun`` mid-clause guard.
    """
    with_signal = 0
    with_qualifier = 0
    examples: list[str] = []
    for sid, seg_text in _iter_corpus_sentences(limit=limit, min_year=min_year):
        if not _has_cheap_signal(seg_text):
            continue
        with_signal += 1
        cp = parse_condition_exception_sentence(seg_text)
        if cp.qualifiers:
            with_qualifier += 1
        elif len(examples) < max_examples:
            snippet = seg_text if len(seg_text) <= 160 else seg_text[:157] + "..."
            examples.append(f"[{sid}] {snippet!r}")

    return CheapSignalCoverage(
        sentences_with_signal=with_signal,
        sentences_with_qualifier=with_qualifier,
        candidate_misses=with_signal - with_qualifier,
        miss_examples=tuple(examples),
    )


def compute_attachment_quality(
    *, limit: int = 0, min_year: int = 0, max_examples: int = 8
) -> AttachmentQuality:
    """Compute the qualifier attachment-status distribution over the slice.

    Iterates the SAME corpus slice, parses each sentence, and tallies the
    attachment status of every qualifier. This is the construction-grammar
    spot-check the production proximity ``scope_hint`` cannot offer: how often the
    qualifier attaches to exactly one deontic core (``resolved``), how often the
    construction correctly flags ambiguity instead of over-attaching
    (``ambiguous``), and how often there is no typed core to attach to
    (``candidate``).
    """
    total = resolved = ambiguous = candidate = 0
    examples: list[tuple[str, str, str]] = []
    for _sid, seg_text in _iter_corpus_sentences(limit=limit, min_year=min_year):
        cp = parse_condition_exception_sentence(seg_text)
        for q in cp.qualifiers:
            total += 1
            if q.attachment_status == ATTACH_RESOLVED:
                resolved += 1
            elif q.attachment_status == ATTACH_AMBIGUOUS:
                ambiguous += 1
            elif q.attachment_status == ATTACH_CANDIDATE:
                candidate += 1
            if q.attachment_status in (ATTACH_RESOLVED, ATTACH_AMBIGUOUS) and len(
                examples
            ) < max_examples:
                snippet = seg_text if len(seg_text) <= 140 else seg_text[:137] + "..."
                examples.append((q.attachment_status, q.cue, snippet))

    return AttachmentQuality(
        qualifiers_total=total,
        resolved=resolved,
        ambiguous=ambiguous,
        candidate=candidate,
        examples=tuple(examples),
    )


def format_cheap_signal_report(cov: CheapSignalCoverage) -> str:
    lines: list[str] = []
    lines.append("-" * 72)
    lines.append("cheap-signal condition/exception recall proxy (oracle-INDEPENDENT)")
    lines.append("-" * 72)
    lines.append(f"  sentences with cheap cond/exc signal : {cov.sentences_with_signal}")
    lines.append(f"  of those, produced a qualifier       : {cov.sentences_with_qualifier}")
    lines.append(f"  candidate recall misses              : {cov.candidate_misses}")
    lines.append(f"  cheap-signal coverage                : {100 * cov.coverage:.2f}%")
    if cov.miss_examples:
        lines.append("  candidate-miss examples (signal but no qualifier):")
        for ex in cov.miss_examples:
            lines.append(f"    {ex}")
    lines.append("")
    return "\n".join(lines)


def format_attachment_quality_report(aq: AttachmentQuality) -> str:
    def pct(n: int) -> str:
        return f"{100 * n / aq.qualifiers_total:.2f}%" if aq.qualifiers_total else "n/a"

    lines: list[str] = []
    lines.append("-" * 72)
    lines.append("attachment-quality spot-check (construction value over proximity)")
    lines.append("-" * 72)
    lines.append(f"  qualifiers parsed                    : {aq.qualifiers_total}")
    lines.append(f"  resolved (1 deontic core)            : {aq.resolved}  ({pct(aq.resolved)})")
    lines.append(f"  ambiguous (>1 core; flagged)         : {aq.ambiguous}  ({pct(aq.ambiguous)})")
    lines.append(f"  candidate (no core; tagged)          : {aq.candidate}  ({pct(aq.candidate)})")
    if aq.examples:
        lines.append("  attached examples (status / cue / sentence):")
        for status, cue, snippet in aq.examples:
            lines.append(f"    [{status}] {cue!r}: {snippet!r}")
    lines.append("")
    return "\n".join(lines)


def run_condexc_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 8,
) -> FamilyCensusResult:
    """Run the condition/exception differential census over the corpus.

    Wires the family's four plug-points into the generalized engine. Sampling
    identical to the other family censuses (``min_year`` / ``limit``);
    ``check_totality`` defaults to ``LAWVM_PARSE_TOTALITY``.
    """
    return run_family_census(
        family=CONDEXC_FAMILY,
        segment_selector=_condexc_segment_selector,
        projection_fn=_condexc_projection,
        oracle_fn=_condexc_oracle,
        miss_shape_fn=_condexc_miss_shape,
        limit=limit,
        min_year=min_year,
        check_totality=check_totality,
        max_examples=max_examples,
    )


def main() -> None:
    import sys

    # Usage: python -m ...condition_exception_census [LIMIT] [MIN_YEAR]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = run_condexc_census(limit=limit, min_year=min_year)
    print(
        format_family_census_report(
            result, title="FI CONDITION/EXCEPTION DIFFERENTIAL CENSUS"
        )
    )
    # Weak-oracle recall gate: cheap-signal coverage (oracle-independent).
    cov = compute_cheap_signal_coverage(limit=limit, min_year=min_year)
    print(format_cheap_signal_report(cov))
    # Construction value over proximity: attachment-quality spot-check.
    aq = compute_attachment_quality(limit=limit, min_year=min_year)
    print(format_attachment_quality_report(aq))
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but in-scope units = {result.in_scope_units}"
        )


if __name__ == "__main__":
    main()
