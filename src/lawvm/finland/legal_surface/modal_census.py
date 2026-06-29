"""Differential census for the modal-predicate / actor_modal construction.

The next net-new construction-grammar island after the citation pilot
(:mod:`lawvm.finland.legal_surface.sentence_census`), the definition pilot
(:mod:`lawvm.finland.legal_surface.definition_census`), and the temporal island
(:mod:`lawvm.finland.legal_surface.temporal_census`), built on the
family-agnostic engine (:mod:`lawvm.finland.legal_surface.family_census`). It
wires the modal family's FOUR engine plug-points:

  1. segment-selector — :func:`_modal_segment_selector` yields, per statute, each
     SENTENCE segment of the decoded body (from the SegmentationGraph substrate's
     ``build_clause_index`` ``sentences`` view) that carries a modal-marker cue
     (the in-scope family discriminator) — obligation / permission / prohibition
     / power.
  2. projection-fn   — :func:`_modal_projection`: the :class:`ModalParse`
     projection's modal-key set (:func:`projection_modal_keys`).
  3. oracle-fn       — :func:`_modal_oracle`: the PRODUCTION actor/modal primitive
     (``references.actor_modal.recognize_actor_modal_frames``) over the SAME span,
     lifted to the same ``token:polarity:voice`` key form (so the comparison is
     honest — identical coordinate space, identical key derivation).
  4. miss-shape-fn   — :func:`_modal_miss_shape`: coarse structural class of a
     missed modal core (token + polarity + voice) for ranking what blocks miss=0.

WEAK ORACLE CAVEAT (read before trusting miss == 0)
===================================================
The production actor/modal lens only fires a frame when a KNOWN actor surface
(institutional registry + closed role-actor list) sits within 60 chars before
the modal marker. A real deontic core whose subject is an unregistered actor or
is impersonal (``säädetään``, sentence-initial ``On tehtävä …``) yields NO
production frame. So the production oracle UNDER-COVERS recall here. Consequently:

  * ``miss == 0`` means little — the oracle finds few cores, so the projection
    rarely lacks one. The real recall gates are (a) total-token-ownership / no
    silent drop (``LAWVM_PARSE_TOTALITY``), and (b) a CHEAP-SIGNAL modal proxy:
    spans carrying a modal cue that the construction parse fails to turn into a
    core (:func:`compute_cheap_signal_coverage`).
  * Expect MANY supersets — the construction frame finds modal cores production
    missed (actor-underspecified / impersonal). Those are reported NEUTRALLY as
    construction-recall-candidates, NOT "production bugs"; a spot-check (the
    superset examples) tells genuine cores from copula/false-positive overreach.

The census comparison is per SENTENCE: the projection's modal-key set vs the
production oracle's modal-key set over the same char span, classified
match / superset / miss / decline. Honors ``LAWVM_PARSE_TOTALITY`` via the
:class:`ModalParse` ``assert_total_ownership`` postcondition.

Pure measure-only. Changes no production behavior; off the replay/apply path.
"""
from __future__ import annotations

import re
from lawvm.core.regex_safety import PrefilteredPattern, compile_classifier_regex
from collections.abc import Iterator
from dataclasses import dataclass

from lawvm.finland.legal_surface.family_census import (
    CensusUnit,
    FamilyCensusResult,
    format_family_census_report,
    run_family_census,
)
from lawvm.finland.legal_surface.modal_parse import (
    MODAL_LANE_DECLINED,
    assert_total_ownership,
    parse_modal_sentence,
    projection_modal_keys,
)

#: Family id passed to the generalized engine.
MODAL_FAMILY = "actor_modal"

#: Map production polarity (``positive``) onto the family vocabulary
#: (``affirmative``); ``negative`` and voice (``active`` / ``passive``) are
#: shared verbatim. (Mirrors ``modal_parse._PROD_POLARITY``.)
_PROD_POLARITY = {"positive": "affirmative", "negative": "negative"}


def _modal_oracle_keys_for_span(text: str) -> set[str]:
    """Run the PRODUCTION actor/modal primitive over a span → census key set.

    Wraps ``recognize_actor_modal_frames`` (the H4 surface lens the production
    pipeline uses) and, per recognised frame, lifts its :class:`SurfaceModality`
    to a ``{token}:{polarity}:{voice}`` key (the same form the
    :func:`modal_key` projection produces). This is the differential-census
    ORACLE: the current production actor/modal output for the span. It is a WEAK
    oracle — it only fires on frames with a known bound actor — so the census
    miss bucket measures little; see the cheap-signal proxy below.
    """
    from lawvm.finland.references.actor_modal import recognize_actor_modal_frames

    scan = recognize_actor_modal_frames(text)
    keys: set[str] = set()
    for frame in scan.frames:
        pol = _PROD_POLARITY.get(frame.modal.polarity, frame.modal.polarity)
        keys.add(f"{frame.modal.token}:{pol}:{frame.modal.voice}")
    return keys


def _modal_segment_selector(sid: str, body: str) -> Iterator[CensusUnit]:
    """Yield the in-scope modal sentence units of one statute.

    Segments the body into sentences via the SegmentationGraph substrate
    (``build_clause_index``) and yields one :class:`CensusUnit` per sentence whose
    construction parse produced >=1 modal core (the in-scope family discriminator
    — a modal-marker cue that survives the necessive gate). A sentence whose
    construction parse declined (a modal-looking cue that did NOT yield a core,
    e.g. a bare ``on`` copula failing the necessive gate, or no cue at all) is NOT
    yielded — like the citation family's stray-anchor skip, a non-modal sentence
    is not a construction decline; it is simply out of family.
    """
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    index = build_clause_index(sid, body)
    for sent in index.sentences:
        seg_text = body[sent.char_start : sent.char_end]
        mp = parse_modal_sentence(seg_text)
        if mp.parser_lane == MODAL_LANE_DECLINED:
            # No modal core parsed → out of family (not a construction decline).
            continue
        totality_ok = True
        try:
            assert_total_ownership(mp)
        except AssertionError:
            totality_ok = False
        kind = mp.cores[0].kind if mp.cores else "-"
        yield CensusUnit(
            text=seg_text,
            parser_lane=mp.parser_lane,
            declared_marker=f"sentence:{kind}",
            declined=mp.parser_lane == MODAL_LANE_DECLINED,
            totality_ok=totality_ok,
        )


def _modal_projection(unit: CensusUnit, sid: str) -> set[str]:
    return projection_modal_keys(parse_modal_sentence(unit.text))


def _modal_oracle(unit: CensusUnit, _ctx: object = None) -> set[str]:
    # The modal family's oracle is span-local (it runs the production actor/modal
    # recognizer over the unit text), so it ignores the per-statute oracle context
    # the engine threads through.
    return _modal_oracle_keys_for_span(unit.text)


def _modal_miss_shape(missing_keys: set[str], declared_marker: str) -> str:
    """Coarse structural class of a missed modal core (what blocks miss=0).

    A modal key is ``<token>:<polarity>:<voice>``. The shape names the missed
    voice + polarity (the construction-grammar dimensions): which voice/polarity
    of modal the projection lacked that the (weak) oracle found.
    """
    voices = sorted({k.split(":")[2] for k in missing_keys if k.count(":") == 2})
    pols = sorted({k.split(":")[1] for k in missing_keys if k.count(":") == 2})
    voice_part = "+".join(voices) if voices else "novoice"
    pol_part = "+".join(pols) if pols else "nopol"
    return f"{voice_part}|{pol_part}"


# ---------------------------------------------------------------------------
# Cheap-signal modal proxy (the REAL recall gate for this weak-oracle family)
# ---------------------------------------------------------------------------

#: Cheap surface signals that a sentence almost-certainly carries a deontic
#: modal core, used to estimate recall INDEPENDENTLY of the construction parser
#: and the (weak) production oracle. A sentence matching one of these but
#: yielding NO construction core is a candidate recall miss. These overlap the
#: closed marker list but are spelled as cheap regexes (incl. the participle
#: forms a bare ``on`` governs) so they are a genuine independent signal.
_CHEAP_SIGNALS: tuple[re.Pattern[str] | PrefilteredPattern, ...] = (
    re.compile(r"\bon\b[^.;:\n]{0,40}?\w*t[aä]v[aä]\b", re.IGNORECASE),  # on … -ttava
    compile_classifier_regex(r"\btulee\b(?!\s+voimaan)", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[1]"),  # excl. ``tulee voimaan`` (temporal)
    compile_classifier_regex(r"\btäytyy\b", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[2]"),
    compile_classifier_regex(r"\bei\s+saa\b", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[3]"),
    compile_classifier_regex(r"\bsaa\b", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[4]"),
    compile_classifier_regex(r"\bvoidaan\b", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[5]"),
    compile_classifier_regex(r"\bvoi\b", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[6]"),
    compile_classifier_regex(r"\bon\s+velvollinen\b", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[7]"),
    compile_classifier_regex(r"\bon\s+oikeus\b", re.IGNORECASE, classifier_id="fi.legal_surface.modal_census.cheap_signals[8]"),
)


@dataclass(frozen=True)
class CheapSignalCoverage:
    """Cheap-signal modal recall proxy for one census run.

    Attributes:
        sentences_with_signal: Sentences carrying any cheap modal signal.
        sentences_with_core:   Of those, sentences whose construction parse
                               produced >=1 modal core.
        candidate_misses:      ``sentences_with_signal - sentences_with_core`` —
                               cheap-signal-bearing sentences with NO core (the
                               candidate recall frontier).
        miss_examples:         A few example candidate-miss sentence snippets.
    """

    sentences_with_signal: int
    sentences_with_core: int
    candidate_misses: int
    miss_examples: tuple[str, ...] = ()

    @property
    def coverage(self) -> float:
        if not self.sentences_with_signal:
            return 1.0
        return self.sentences_with_core / self.sentences_with_signal


def _has_cheap_signal(text: str) -> bool:
    return any(p.search(text) is not None for p in _CHEAP_SIGNALS)


def compute_cheap_signal_coverage(
    *, limit: int = 0, min_year: int = 0, max_examples: int = 8
) -> CheapSignalCoverage:
    """Compute the cheap-signal modal recall proxy over the corpus slice.

    Iterates the SAME corpus slice the census uses, segments each body into
    sentences, and for each sentence carrying a cheap modal signal records
    whether the construction parse produced >=1 core. The ratio is an
    oracle-INDEPENDENT recall estimate: high coverage means the construction
    grammar turns nearly every modal-signalling sentence into a core; the
    candidate misses are the recall frontier worth inspecting.
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

    with_signal = 0
    with_core = 0
    examples: list[str] = []
    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            body = decode_body_text(xb)
        except Exception as exc:
            # Unexpected body-decode failure: previously ``continue`` silently
            # swallowed; now route through ``named_swallow`` so a typed Finding
            # is logged at WARNING with the statute id as ``clause_text``
            # (AGENTS.md §1.10 — never silent).
            #
            # log_emitter sanctioned (iter3 W2 §3.2): dev-tooling census run
            # (modal census analysis loop) — no per-statute findings_out
            # accumulator in scope at this loop phase; per ``core/named_swallow.py``
            # docstring's IO/utility-boundary sanctioned use, the swallow stays on
            # log_emitter (stderr WARNING).
            from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

            log_emitter()(
                build_named_swallow_finding(
                    rule_id="fi_modal_census_decode_body_text",
                    exception=exc,
                    op_id=None,
                    clause_text=f"sid={sid}",
                    jurisdiction="fi",
                    source_artifact=sid,
                )
            )
            continue
        if not body:
            continue
        try:
            index = build_clause_index(sid, body)
        except Exception as exc:
            # Unexpected clause-segmentation failure: previously ``continue``
            # silently swallowed; now route through ``named_swallow`` so a
            # typed Finding is logged at WARNING with the statute id + body
            # length as ``clause_text`` (AGENTS.md §1.10 — never silent).
            #
            # log_emitter sanctioned (iter3 W2 §3.2): same dev-tooling census
            # boundary as the body-decode swallow above — no per-statute
            # findings_out accumulator in scope; see the prior sanctioned-use note.
            from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

            log_emitter()(
                build_named_swallow_finding(
                    rule_id="fi_modal_census_build_clause_index",
                    exception=exc,
                    op_id=None,
                    clause_text=f"sid={sid} body_len={len(body)}",
                    jurisdiction="fi",
                    source_artifact=sid,
                )
            )
            continue
        for sent in index.sentences:
            seg_text = body[sent.char_start : sent.char_end]
            if not _has_cheap_signal(seg_text):
                continue
            with_signal += 1
            mp = parse_modal_sentence(seg_text)
            if mp.cores:
                with_core += 1
            elif len(examples) < max_examples:
                snippet = seg_text if len(seg_text) <= 160 else seg_text[:157] + "..."
                examples.append(f"[{sid}] {snippet!r}")

    return CheapSignalCoverage(
        sentences_with_signal=with_signal,
        sentences_with_core=with_core,
        candidate_misses=with_signal - with_core,
        miss_examples=tuple(examples),
    )


def format_cheap_signal_report(cov: CheapSignalCoverage) -> str:
    lines: list[str] = []
    lines.append("-" * 72)
    lines.append("cheap-signal modal recall proxy (oracle-INDEPENDENT)")
    lines.append("-" * 72)
    lines.append(f"  sentences with cheap modal signal : {cov.sentences_with_signal}")
    lines.append(f"  of those, produced a modal core   : {cov.sentences_with_core}")
    lines.append(f"  candidate recall misses           : {cov.candidate_misses}")
    lines.append(f"  cheap-signal coverage             : {100 * cov.coverage:.2f}%")
    if cov.miss_examples:
        lines.append("  candidate-miss examples (signal but no core):")
        for ex in cov.miss_examples:
            lines.append(f"    {ex}")
    lines.append("")
    return "\n".join(lines)


def run_modal_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 8,
) -> FamilyCensusResult:
    """Run the modal-predicate / actor_modal differential census over the corpus.

    Wires the modal family's four plug-points into the generalized engine.
    Sampling identical to the other family censuses (``min_year`` / ``limit``);
    ``check_totality`` defaults to ``LAWVM_PARSE_TOTALITY``.
    """
    return run_family_census(
        family=MODAL_FAMILY,
        segment_selector=_modal_segment_selector,
        projection_fn=_modal_projection,
        oracle_fn=_modal_oracle,
        miss_shape_fn=_modal_miss_shape,
        limit=limit,
        min_year=min_year,
        check_totality=check_totality,
        max_examples=max_examples,
    )


def main() -> None:
    import sys

    # Usage: python -m ...modal_census [LIMIT] [MIN_YEAR]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = run_modal_census(limit=limit, min_year=min_year)
    print(format_family_census_report(result, title="FI MODAL-PREDICATE (actor_modal) DIFFERENTIAL CENSUS"))
    # The weak-oracle recall gate: cheap-signal modal coverage (oracle-independent).
    cov = compute_cheap_signal_coverage(limit=limit, min_year=min_year)
    print(format_cheap_signal_report(cov))
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but in-scope units = {result.in_scope_units}"
        )


if __name__ == "__main__":
    main()
