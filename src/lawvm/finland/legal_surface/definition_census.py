"""Differential census for the definition-entry construction (Pilot B).

The FIRST net-new construction-grammar island's census, built on the
family-agnostic engine (:mod:`lawvm.finland.legal_surface.family_census`)
generalized out of the Pilot-A citation census. It wires the definition family's
FOUR engine plug-points:

  1. segment-selector — :func:`_definition_segment_selector` yields, per statute,
     each ``definition_list`` block (the chapeau segment plus its forward span up
     to the next ``definition_list`` chapeau / a bounded window — the span the
     production enumerated-block oracle scans) AND each single-sentence inline
     definition (a prose/heading segment carrying a binding cue with no enclosing
     enumerated header).
  2. projection-fn   — :func:`_definition_projection`: the
     :class:`DefinitionParse` projection's binding-key set
     (:func:`projection_definition_keys`).
  3. oracle-fn       — :func:`_definition_oracle`: the PRODUCTION definition
     extractor (``recognize_defined_term_bindings``) over the SAME span, lifted to
     the same key form (so the comparison is honest — identical coordinate space,
     identical key derivation).
  4. miss-shape-fn   — :func:`_definition_miss_shape`: coarse structural class of
     a missed definition (act-bound / plain-definiens, single-word / multi-word
     definiendum, by scope) for ranking what blocks miss=0.

The census comparison is per BLOCK / per SENTENCE: the projection's binding set
vs the production oracle's binding set over the same char span, classified
match / superset / miss / decline. Honors ``LAWVM_PARSE_TOTALITY`` via the
:class:`DefinitionParse` ``assert_total_ownership`` postcondition.

Pure measure-only. Changes no production behavior; off the replay/apply path.
"""
from __future__ import annotations

from collections.abc import Iterator

from lawvm.finland.legal_surface.definition_parse import (
    DEFINITION_LANE_DECLINED,
    assert_total_ownership,
    definition_key,
    parse_definition_block,
    projection_definition_keys,
)
from lawvm.finland.legal_surface.family_census import (
    CensusUnit,
    FamilyCensusResult,
    format_family_census_report,
    run_family_census,
)

#: Family id passed to the generalized engine.
DEFINITION_FAMILY = "definition_entry"

#: Forward window (chars) a ``definition_list`` block may extend past its chapeau
#: when no next chapeau bounds it — mirrors the production binder's
#: ``_ENUM_BLOCK_WINDOW`` so the span the projection and oracle see is the span the
#: production recognizer would scan.
_BLOCK_WINDOW = 12000

#: The binding cues that put a segment IN SCOPE for the single-sentence sub-family
#: (a prose/heading segment carrying one of these, NOT under a definition header).
_CUES = ("tarkoitetaan", "tarkoittaa")


def _definition_oracle_keys_for_span(text: str) -> set[str]:
    """Run the PRODUCTION definition extractor over a span → census key set.

    Wraps ``recognize_defined_term_bindings`` (the SAME recognizer the production
    H2 definition lens uses) and lifts each :class:`DefinedTermBinding` to the
    shared :func:`definition_key` form. This is the differential-census ORACLE:
    the current production definition-extraction output for the span.
    """
    from lawvm.finland.references.defined_terms import (
        BINDING_TARKOITETAAN,
        recognize_defined_term_bindings,
    )

    keys: set[str] = set()
    for b in recognize_defined_term_bindings(text, source_file=""):
        # The definition family (definiendum entries) is the ``tarkoitetaan``
        # binding kind; the alias kinds (parenthetical / jäljempänä) are the
        # citation-alias family, not the definition-entry island under census.
        if b.binding_kind != BINDING_TARKOITETAAN:
            continue
        keys.add(definition_key(b.term, b.scope, b.target_ref))
    return keys


def _definition_segment_selector(sid: str, body: str) -> Iterator[CensusUnit]:
    """Yield the in-scope definition units of one statute.

    Two sub-shapes, both surfaced from the SegmentationGraph substrate:

      * enumerated BLOCK — each ``definition_list`` chapeau, with the unit span
        running from the chapeau start to the next ``definition_list`` chapeau (or
        a bounded window). This is the span the production enumerated-block oracle
        scans, so the differential is honest.
      * single SENTENCE — a prose/heading segment NOT inside any definition block
        that carries an inline binding cue (``X:llä tarkoitetaan Y``).

    A unit whose construction parse declined (cue present but no definiendum
    parsed) is still in scope and carries ``declined=True`` (typed residue).
    """
    from lawvm.finland.legal_surface.clause_segment import build_segmentation_graph

    g = build_segmentation_graph(sid, body)
    segs = g.segments
    chapeau_indices = [i for i, s in enumerate(segs) if s.role == "definition_list"]
    chapeau_set = set(chapeau_indices)

    # The char ranges already covered by an enumerated block (so a single-sentence
    # unit inside a block is not double-counted).
    block_ranges: list[tuple[int, int]] = []

    for k, ci in enumerate(chapeau_indices):
        chap = segs[ci]
        block_start = chap.char_start
        # Block ends at the NEXT definition_list chapeau, else a bounded window.
        next_starts = [
            segs[j].char_start for j in chapeau_indices[k + 1 :]
        ]
        block_end = min(
            block_start + _BLOCK_WINDOW,
            next_starts[0] if next_starts else len(body),
        )
        block_text = body[block_start:block_end]
        block_ranges.append((block_start, block_end))
        dp = parse_definition_block(block_text)
        totality_ok = True
        try:
            assert_total_ownership(dp)
        except AssertionError:
            totality_ok = False
        yield CensusUnit(
            text=block_text,
            parser_lane=dp.parser_lane,
            declared_marker=f"block:{dp.chapeau_cue or '-'}",
            declined=dp.parser_lane == DEFINITION_LANE_DECLINED,
            totality_ok=totality_ok,
        )

    def _in_a_block(start: int, end: int) -> bool:
        return any(bs <= start and end <= be for bs, be in block_ranges)

    # Single-sentence inline definitions: a non-chapeau segment carrying a binding
    # cue, not enclosed by an enumerated block, and not a header chapeau itself.
    for i, s in enumerate(segs):
        if i in chapeau_set or s.kind == "residual":
            continue
        seg_text = body[s.char_start : s.char_end]
        low = seg_text.casefold()
        if not any(c in low for c in _CUES):
            continue
        if _in_a_block(s.char_start, s.char_end):
            continue
        dp = parse_definition_block(seg_text)
        # Out-of-family (no definitional definiendum AND the cue was referential):
        # a declined single-sentence with NO entries and an empty oracle is not a
        # definition at all — skip it (the family discriminator is a real
        # definitional binding, mirroring the citation family's stray-anchor skip).
        if dp.parser_lane == DEFINITION_LANE_DECLINED and not dp.entries:
            if not _definition_oracle_keys_for_span(seg_text):
                continue
        totality_ok = True
        try:
            assert_total_ownership(dp)
        except AssertionError:
            totality_ok = False
        yield CensusUnit(
            text=seg_text,
            parser_lane=dp.parser_lane,
            declared_marker=f"sentence:{dp.chapeau_cue or '-'}",
            declined=dp.parser_lane == DEFINITION_LANE_DECLINED,
            totality_ok=totality_ok,
        )


def _definition_projection(unit: CensusUnit, sid: str) -> set[str]:
    return projection_definition_keys(parse_definition_block(unit.text))


def _definition_oracle(unit: CensusUnit, _ctx: object = None) -> set[str]:
    # The definition family's oracle is span-local (it runs the production
    # definition recognizer over the unit text), so it ignores the per-statute
    # oracle context the engine threads through.
    return _definition_oracle_keys_for_span(unit.text)


def _definition_miss_shape(missing_keys: set[str], declared_marker: str) -> str:
    """Coarse structural class of a missed definition (ranking what blocks miss=0).

    A definition key is ``<term>|<scope>[|<target>]``. The shape names: whether the
    missed definitions are act-bound (carry a target) or plain-definiens, whether
    the definiendum is single- or multi-word, and the dominant scope — plus the
    unit shape (block vs sentence).
    """
    act_bound = any(k.count("|") >= 2 for k in missing_keys)
    multi_word = any(" " in k.split("|", 1)[0] for k in missing_keys)
    scopes = {k.split("|")[1] for k in missing_keys if "|" in k}
    parts: list[str] = []
    parts.append("act_bound" if act_bound else "plain_definiens")
    parts.append("multiword_term" if multi_word else "singleword_term")
    parts.append("+".join(sorted(scopes)) if scopes else "noscope")
    unit_shape = declared_marker.split(":", 1)[0] if ":" in declared_marker else "?"
    return f"{'|'.join(parts)}|{unit_shape}"


def run_definition_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 8,
) -> FamilyCensusResult:
    """Run the definition-entry differential census over the corpus.

    Wires the definition family's four plug-points into the generalized engine.
    Sampling identical to the citation census (``min_year`` / ``limit``);
    ``check_totality`` defaults to ``LAWVM_PARSE_TOTALITY``.
    """
    return run_family_census(
        family=DEFINITION_FAMILY,
        segment_selector=_definition_segment_selector,
        projection_fn=_definition_projection,
        oracle_fn=_definition_oracle,
        miss_shape_fn=_definition_miss_shape,
        limit=limit,
        min_year=min_year,
        check_totality=check_totality,
        max_examples=max_examples,
    )


def main() -> None:
    import sys

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = run_definition_census(limit=limit, min_year=min_year)
    print(
        format_family_census_report(
            result, title="FI DEFINITION-ENTRY DIFFERENTIAL CENSUS (Pilot B)"
        )
    )
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but in-scope units = {result.in_scope_units}"
        )


if __name__ == "__main__":
    main()
