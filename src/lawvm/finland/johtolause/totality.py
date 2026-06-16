"""Raw-tape no-silent-drop totality predicate (warn-only telemetry).

A token-coverage totality check that, at end of parse, looks for every OPERATIVE
token span that is NOT covered by a produced node's witness AND NOT shielded by a
benign annotation/sentinel span, and flags it as a candidate DROP.

Design (mirrors the established whitelist-audit requirements):
  1. Operates on the RAW token tape (NOT the filtered/annotated stream), so an
     annotation that hides a real operative span cannot mask a drop. Parser
     witness spans index the filtered stream; we project produced-op coverage
     back to raw-tape coordinates through the filtered->raw position map.
  2. UNIT-AGNOSTIC operative label: a NUM [LETTER] immediately followed by a
     structural-noun cat (PYKALA/LUKU/MOMENTTI/KOHTA/ALAKOHTA/OSA/LIITE/NIMIKE/
     OTSIKKO) -- NOT a section-only notion.
  3. For each SKIP_CATS annotation span and the ""-sentinel qualifier spans /
     title-suffix CITATION spans / END_SENTINEL trailing content, apply benign
     guards: an operative label that is structurally PART OF a benign span is NOT
     a drop. An operative-continuation ``sekä|ja NUM <struct>`` that leaks PAST a
     benign span boundary and produces no op IS a drop.
  4. Reuses coverage_audit.covered_token_indices for the produced-op witness
     coverage, lifted to raw-tape coordinates.

This is a pure measure-only function. It has no effect on a parse and is not on
the default parse hot path: ``parse_clause`` only calls it under the env flag
``LAWVM_PARSE_TOTALITY`` (it roughly doubles per-parse cost). On a flagged drop
the caller emits a self-evidencing ``silent_drop`` residual; the predicate itself
never raises a parse-affecting error.

``predicate(text) -> (list[FlaggedDrop], n_ops)``. ``n_ops`` lets a corpus harness
separate true silent-DROPs (``n_ops > 0``: an op was produced but a sibling target
was lost) from whole-clause DECLINES (``n_ops == 0``: a different, already-loud
failure mode).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lawvm.finland.johtolause.coverage_audit import covered_token_indices
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.scan import (
    AnnotatedStream,
    annotate_end_sentinels,
    annotate_formal_title_suffix,
    annotate_jolloin,
    annotate_provenance,
    annotate_punct,
    annotate_qualifiers,
    annotate_reinstatement,
    annotate_statute_citations,
    annotate_statute_names,
    _remap_annotation,
)
from lawvm.finland.johtolause.sentinels import SKIP_CATS

# Unit-agnostic structural-noun cats that make a preceding NUM[LETTER] an
# operative label.
_STRUCT_NOUNS: frozenset[str] = frozenset(
    {"PYKALA", "LUKU", "MOMENTTI", "KOHTA", "ALAKOHTA", "OSA", "LIITE", "NIMIKE", "OTSIKKO"}
)
_CONT_CONJ: frozenset[str] = frozenset({"CONJ"})  # sekä / ja / tai lemma carried by CONJ
_SUBREF_STRUCTS: frozenset[str] = frozenset({"MOMENTTI", "KOHTA", "ALAKOHTA"})
_MOVE_VERB_PREFIXES: tuple[str, ...] = ("siirre", "siirty", "siirret")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class RawAnnSpan:
    """A scan annotation span lifted to raw-tape coordinates."""

    start: int  # raw-tape inclusive
    end: int    # raw-tape exclusive
    kind: str
    sentinel_cat: str  # "" for pure-removal qualifiers; SKIP_CATS member otherwise


@dataclass(frozen=True)
class OperativeLabel:
    """A NUM[LETTER]<struct> operative label run in raw-tape coordinates."""

    num_idx: int        # index of the leading NUM token
    struct_idx: int     # index of the structural-noun token
    end: int            # one-past struct (exclusive)
    label: str          # e.g. "69j", "3" (luku), normalized lowercase no-space
    struct_cat: str


@dataclass(frozen=True)
class FlaggedDrop:
    """A candidate silent drop: an uncovered, non-benign operative label."""

    label: OperativeLabel
    reason: str          # why flagged (e.g. "uncovered_operative")
    source_text: str


def _normalize(text: str) -> str:
    return _WS_RE.sub("", text).lower()


def _raw_annotations(tokens: list[Token]) -> list[RawAnnSpan]:
    """Reproduce scan.apply_annotations' annotation set in RAW-TAPE coordinates.

    Returns every scan Annotation (phase1 in raw coords + phase2 remapped to raw)
    so the predicate can ask, for any raw token index, which benign span covers it.
    """
    cite_anns = annotate_statute_citations(tokens)
    name_anns = annotate_statute_names(tokens, cite_anns)
    phase1 = cite_anns + name_anns
    view, view_to_raw = AnnotatedStream(
        tokens=tokens, annotations=phase1
    ).structural_view_with_map()

    phase2_v = (
        annotate_formal_title_suffix(view)
        + annotate_provenance(view)
        + annotate_reinstatement(view)
        + annotate_jolloin(view)
        + annotate_qualifiers(view)
        + annotate_end_sentinels(view)
        + annotate_punct(view)
    )
    phase2_raw = [_remap_annotation(a, view_to_raw) for a in phase2_v]

    return [
        RawAnnSpan(
            start=a.span.start, end=a.span.end, kind=a.kind, sentinel_cat=a.sentinel_cat
        )
        for a in (phase1 + phase2_raw)
    ]


def _filtered_to_raw_coverage(
    text: str, raw_tokens: list[Token]
) -> tuple[set[int], int, set[str]]:
    """Compute (raw covered indices, n_produced_ops, produced_op_labels) for ``text``.

    The parser's witness source_spans index into the FILTERED stream
    (apply_annotations output). We rebuild that filtered stream WITH its
    view->raw map, get the filtered covered set from coverage_audit, then
    project each covered filtered index back to its raw token range.
    """
    from lawvm.finland.johtolause import surface_parse as _sp
    from lawvm.finland.johtolause.api import parse_clause as _parse_clause
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    # Rebuild the filtered stream + a filtered->raw position map by re-running the
    # same phase pipeline (structural_view_with_map gives the final-view map).
    cite_anns = annotate_statute_citations(raw_tokens)
    name_anns = annotate_statute_names(raw_tokens, cite_anns)
    phase1 = cite_anns + name_anns
    view, view_to_raw = AnnotatedStream(
        tokens=raw_tokens, annotations=phase1
    ).structural_view_with_map()
    phase2_v = (
        annotate_formal_title_suffix(view)
        + annotate_provenance(view)
        + annotate_reinstatement(view)
        + annotate_jolloin(view)
        + annotate_qualifiers(view)
        + annotate_end_sentinels(view)
        + annotate_punct(view)
    )
    phase2_raw = [_remap_annotation(a, view_to_raw) for a in phase2_v]
    all_anns = phase1 + phase2_raw
    filtered_tokens, filtered_to_raw = AnnotatedStream(
        tokens=raw_tokens, annotations=all_anns
    ).structural_view_with_map()

    # Parse the filtered stream exactly as api.parse_clause does (old parser primary).
    _filtered, jolloin_pairs = apply_annotations_with_jolloin_pairs(raw_tokens)
    jolloin_arg = jolloin_pairs if jolloin_pairs else None
    clause = _sp.parse(filtered_tokens, jolloin_renumber_pairs=jolloin_arg)

    covered_filtered = covered_token_indices(clause)
    covered_raw: set[int] = set()
    for fi in covered_filtered:
        if 0 <= fi < len(filtered_to_raw):
            r0, r1 = filtered_to_raw[fi]
            covered_raw.update(range(r0, r1))

    # n_ops + produced op-label set from the full pipeline. The label set is the
    # witness-fidelity guard: a label that IS already a produced op-target but
    # whose RAW tokens are uncovered is a re-mention (provenance `niistä N §
    # sellaisina kuin ne ovat`, range-expansion witness gap), NOT a drop.
    _ops = _parse_clause(text).parsed_ops or []
    n_ops = len(_ops)
    op_labels = {_normalize(op.number or "") for op in _ops if op.number}
    return covered_raw, n_ops, op_labels


def _scan_operative_labels(tokens: list[Token]) -> list[OperativeLabel]:
    """Scan RAW tokens for NUM [LETTER] <struct-noun> operative labels (unit-agnostic)."""
    n = len(tokens)
    out: list[OperativeLabel] = []
    i = 0
    while i < n:
        if tokens[i].cat != "NUM":
            i += 1
            continue
        num_idx = i
        label_parts = [tokens[i].text]
        j = i + 1
        if j < n and tokens[j].cat == "LETTER":
            label_parts.append(tokens[j].text)
            j += 1
        if j < n and tokens[j].cat in _STRUCT_NOUNS:
            out.append(
                OperativeLabel(
                    num_idx=num_idx,
                    struct_idx=j,
                    end=j + 1,
                    label=_normalize("".join(label_parts)),
                    struct_cat=tokens[j].cat,
                )
            )
            i = j + 1
            continue
        i += 1
    return out


def _covering_ann(idx: int, anns: list[RawAnnSpan]) -> RawAnnSpan | None:
    for a in anns:
        if a.start <= idx < a.end:
            return a
    return None


def _governing_section_label(label: OperativeLabel, tokens: list[Token]) -> str | None:
    """For a MOMENTTI/KOHTA/ALAKOHTA sub-ref, find its governing ``N §`` label.

    Scans backward from the sub-ref's NUM to the nearest preceding PYKALA, then
    reads the NUM[LETTER] immediately before that PYKALA. Returns the normalized
    section label, or None if no governing section is found in the local window.
    """
    # Walk back to the nearest PYKALA before this sub-ref's number, bounded by a
    # VERB / END (clause boundary) so we don't bind across the whole clause.
    k = label.num_idx - 1
    while k >= 0 and tokens[k].cat not in ("PYKALA", "VERB", "END"):
        k -= 1
    if k < 0 or tokens[k].cat != "PYKALA":
        return None
    # NUM[LETTER] immediately before the PYKALA.
    j = k - 1
    if j >= 0 and tokens[j].cat == "LETTER":
        if j - 1 >= 0 and tokens[j - 1].cat == "NUM":
            return _normalize(tokens[j - 1].text + tokens[j].text)
        return None
    if j >= 0 and tokens[j].cat == "NUM":
        return _normalize(tokens[j].text)
    return None


def _luku_governs_covered_section(
    label: OperativeLabel, tokens: list[Token], op_labels: set[str]
) -> bool:
    """True when a LUKU is the chapter context of a following covered ``N §``.

    Pattern: ``<LUKU> [WORD?] NUM[LETTER] PYKALA`` where the NUM[LETTER] section
    label IS a produced op. Then the LUKU is container context, not a dropped
    operative target. Only fires when the immediately-following section is covered
    (so a genuinely dropped whole-chapter op stays flagged).
    """
    n = len(tokens)
    k = label.struct_idx + 1
    # Allow one filler word (`luvun`) already consumed; look for NUM[LETTER]PYKALA.
    while k < n and tokens[k].cat == "WORD":
        k += 1
    if k < n and tokens[k].cat == "NUM":
        num = tokens[k].text
        m = k + 1
        if m < n and tokens[m].cat == "LETTER":
            num = num + tokens[m].text
            m += 1
        if m < n and tokens[m].cat == "PYKALA":
            return _normalize(num) in op_labels
    return False


# Container-context guard tuning.
_CONTAINER_LOCATIVE_PREFIXES: tuple[str, ...] = (
    "luvu",   # luvun / luvulle / luvussa
    "lukuu",  # lukuun
    "luvuks",  # luvuksi
    "osan",
    "osaan",
    "osaks",  # osaksi
)
# Tokens that END the locative noun phrase: past these the following ``N §`` is a
# coordinated SIBLING target or a different container, not a section CONTAINED by
# this LUKU/OSA. A comma / coordinating conjunction (sekä/ja) / range dash breaks
# the genitive-locative chain.
_CONTAINER_BARRIER_CATS: frozenset[str] = frozenset(
    {"VERB", "END", "COMMA", "CONJ", "DASH"}
)
# Struct nouns that, when seen before the target PYKALA, mean this label is NOT a
# direct §-container (a nested NIMIKE/OTSIKKO/MOMENTTI/KOHTA target, or a second
# container of a different kind). A single nested ``N luvun`` hop IS allowed for an
# OSA (``II osan 5 luvun 6 §:ään``), handled explicitly below.
_CONTAINER_BLOCKING_STRUCTS: frozenset[str] = frozenset(
    {"OSA", "MOMENTTI", "KOHTA", "ALAKOHTA", "LIITE", "NIMIKE", "OTSIKKO"}
)
_CONTAINER_WINDOW = 10


def _container_governs_covered_section(
    label: OperativeLabel, tokens: list[Token], op_labels: set[str]
) -> bool:
    """True when a LUKU/OSA is the structural CONTAINER of a covered ``N §`` target.

    Generalizes :func:`_luku_governs_covered_section` to the locative-chain shapes
    the immediate-adjacency form misses: ``N luvun [sellaisena kuin se on laissa X]
    M §:ään``, ``N lukuun väliaikaisesti uusi M [a] § ... ja K §`` and the appendix
    part-container ``II osan [P luvun] M §:ään``. The LUKU/OSA is benign container
    context (not a dropped operative target) iff a covered section op is reached
    through a TIGHT locative chain: no ``COMMA``/``CONJ``/``DASH`` barrier (which
    would make the ``N §`` a coordinated sibling, e.g. ``7 luvun otsikko sekä
    50―55 §`` where ``7 luku`` is itself a heading target), no intervening nested
    target struct (``NIMIKE``/``OTSIKKO``/``KOHTA``/...), within a bounded window.
    One nested ``P luvun`` hop is allowed for an OSA part-container.

    Conservative by construction: a genuinely dropped whole-chapter/part op (no
    contained covered ``§``, or one separated by a coordinator) stays flagged.
    """
    n = len(tokens)
    if not tokens[label.struct_idx].text.lower().startswith(
        _CONTAINER_LOCATIVE_PREFIXES
    ):
        # Only a genitive/illative locative container (``luvun``/``osan``/...) binds
        # a following section; a bare nominative ``N luku`` is a standalone target.
        return False
    k = label.struct_idx + 1
    seen = 0
    luku_hops = 0
    while k < n and seen < _CONTAINER_WINDOW:
        cat = tokens[k].cat
        if cat in _CONTAINER_BARRIER_CATS:
            return False
        if cat == "NUM":
            num = tokens[k].text
            m = k + 1
            if m < n and tokens[m].cat == "LETTER":
                num = num + tokens[m].text
                m += 1
            if m < n and tokens[m].cat == "PYKALA":
                return _normalize(num) in op_labels
            # ``OSA ... P luvun ... §`` -> allow a single nested genitive luku hop.
            if (
                m < n
                and tokens[m].cat == "LUKU"
                and luku_hops == 0
                and tokens[m].text.lower().startswith(("luvu", "lukuu"))
            ):
                luku_hops += 1
                k = m + 1
                seen += 1
                continue
            # A bare number that is neither a section nor an allowed luku-hop (an
            # appendix ``kohta`` list under the part) -> not a §-container.
            return False
        if cat in _CONTAINER_BLOCKING_STRUCTS:
            return False
        # A LETTER here is the roman-numeral part label tail (``II``); skip it.
        k += 1
        seen += 1
    return False


def _is_move_destination(label: OperativeLabel, tokens: list[Token]) -> bool:
    """True when the label is the destination of a move/renumber.

    (`siirretään N lukuun` / `siirtyy N luvuksi`)
    """
    for k in range(max(0, label.num_idx - 3), label.num_idx):
        if tokens[k].text.lower().startswith(_MOVE_VERB_PREFIXES):
            return True
    return False


def _followed_by_nojalla(label: OperativeLabel, tokens: list[Token]) -> bool:
    """True when the operative label is the object of ``... §:n nojalla`` (legal basis)."""
    n = len(tokens)
    for k in range(label.struct_idx + 1, min(label.struct_idx + 3, n)):
        if tokens[k].text.lower().startswith("nojalla"):
            return True
    return False


def _trailing_continuation_after_span(
    label: OperativeLabel, tokens: list[Token], ann: RawAnnSpan
) -> bool:
    """Operative-continuation guard, span-EXIT form.

    Fires only when the operative label sits AT OR AFTER the END of a benign span
    and is introduced by a ``sekä|ja NUM <struct>`` continuation that LEAKS PAST
    the span boundary -- a coordinated operative target wrongly swallowed by an
    over-extended annotation. A coordinated number that lives ENTIRELY INSIDE the
    span (e.g. the ``5 ja 6 §`` of ``5 ja 6 §:n muuttamisesta annetun lain``) is
    name-internal and must stay benign: the leading CONJ is inside the span, so
    this returns False.
    """
    # Find the CONJ/COMMA chain immediately before the label.
    k = label.num_idx - 1
    conj_idx: int | None = None
    while k >= 0 and tokens[k].cat in ("COMMA", "CONJ", "DASH"):
        if tokens[k].cat in _CONT_CONJ:
            conj_idx = k
        k -= 1
    if conj_idx is None:
        return False
    # The continuation leaks past the span iff the CONJ that introduces this label
    # is at or after the span's end (the label was coordinated OUT of the benign
    # region, not part of its internal name).
    return conj_idx >= ann.end


def _label_text(text: str, tokens: list[Token], lab: OperativeLabel) -> str:
    cs = tokens[lab.num_idx].char_start
    ce = tokens[lab.struct_idx].char_end
    if cs >= 0 and ce >= 0:
        # Widen a little for context.
        lo = max(0, cs - 30)
        hi = min(len(text), ce + 30)
        return text[lo:hi]
    return lab.label


def predicate(text: str) -> tuple[list[FlaggedDrop], int]:
    """The raw-tape no-silent-drop totality predicate. Returns (flagged labels, n_ops).

    A label is FLAGGED (a candidate DROP) iff:
      * it is NOT covered by any produced op's witness (raw coords), AND
      * it is NOT shielded by a benign span:
          - inside a SKIP_CATS span (citation / statute_name / provenance /
            reinstatement) -> benign (title-internal / name-internal numerals),
            UNLESS a `sekä|ja` continuation leaks the label PAST the span end,
          - inside an END_SENTINEL_SPAN (post-`seuraavasti` tail) -> benign,
          - inside a ""-sentinel qualifier span (LANGQUAL/TEMPORAL/ALAKOHTA/
            participial/VALIOTSIKKO/JOLLOIN_MOVE) -> benign (modifies an
            already-covered target).

    n_ops is returned so a corpus harness can separate true silent-DROPs
    (n_ops > 0, an op was produced but a sibling target was lost) from whole-clause
    DECLINES (n_ops == 0 -> a different, already-loud failure mode).
    """
    raw_tokens = tokenize(text)
    anns = _raw_annotations(raw_tokens)
    covered, n_ops, op_labels = _filtered_to_raw_coverage(text, raw_tokens)
    labels = _scan_operative_labels(raw_tokens)

    flagged: list[FlaggedDrop] = []
    for lab in labels:
        if any(idx in covered for idx in range(lab.num_idx, lab.end)):
            continue
        # Witness-fidelity guard: the label IS a produced op-target, just re-named
        # in provenance (`niistä N § sellaisina kuin ne ovat`) or its witness span
        # is narrow (range-expansion). The op exists -> not a drop.
        if lab.label in op_labels:
            continue
        # nojalla-authority guard: `N §:n nojalla` names the ENABLING statute
        # section (legal basis), never an operative target.
        if _followed_by_nojalla(lab, raw_tokens):
            continue
        # Sub-reference guard: a MOMENTTI/KOHTA/ALAKOHTA whose governing `N §` IS a
        # produced op-target is a covered sub-ref (modifies an already-covered
        # target). When the governing § is NOT covered, the sub-ref rides a
        # genuinely dropped target and stays flagged.
        if lab.struct_cat in _SUBREF_STRUCTS:
            gov = _governing_section_label(lab, raw_tokens)
            if gov is not None and gov in op_labels:
                continue
        # Container-context guard: a LUKU/OSA that is the structural container of a
        # produced `N §` (chapter context `N luvun M §`, appendix part `II osan M §`,
        # `N lukuun uusi M §`) is container context, not a dropped operative target.
        # Reached through a tight locative chain only (a coordinated sibling `N §`
        # past a comma/conjunction does NOT shield, so a dropped whole-chapter/part
        # target stays flagged).
        if lab.struct_cat in ("LUKU", "OSA") and (
            _luku_governs_covered_section(lab, raw_tokens, op_labels)
            or _container_governs_covered_section(lab, raw_tokens, op_labels)
        ):
            continue
        # Move-destination guard: a LUKU preceded by a move verb is the DESTINATION
        # of a renumber/move, not a dropped operative target.
        if lab.struct_cat == "LUKU" and _is_move_destination(lab, raw_tokens):
            continue
        ann = _covering_ann(lab.struct_idx, anns) or _covering_ann(lab.num_idx, anns)
        if ann is not None:
            # END_SENTINEL trailing content: always benign.
            if ann.sentinel_cat == "END_SENTINEL_SPAN":
                continue
            # ""-sentinel qualifier, VALIOTSIKKO heading-placement, or JOLLOIN_MOVE
            # renumber span: benign (the renumber pair is captured natively and
            # attaches to the already-covered §-target the jolloin modifies).
            if ann.sentinel_cat in ("", "VALIOTSIKKO", "JOLLOIN_MOVE"):
                continue
            # SKIP_CATS span (citation/statute_name/provenance/reinstatement):
            # benign unless a continuation leaks the label past the span end.
            if ann.sentinel_cat in SKIP_CATS:
                if not _trailing_continuation_after_span(lab, raw_tokens, ann):
                    continue
                flagged.append(
                    FlaggedDrop(
                        label=lab,
                        reason="continuation_leaked_past_benign_span",
                        source_text=_label_text(text, raw_tokens, lab),
                    )
                )
                continue
        # Uncovered, not benign-shielded.
        flagged.append(
            FlaggedDrop(
                label=lab,
                reason="uncovered_operative",
                source_text=_label_text(text, raw_tokens, lab),
            )
        )
    return flagged, n_ops
