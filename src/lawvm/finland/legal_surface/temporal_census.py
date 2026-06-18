"""Differential census for the temporal/applicability construction.

The next net-new construction-grammar island after the citation pilot
(:mod:`lawvm.finland.legal_surface.sentence_census`) and the definition pilot
(:mod:`lawvm.finland.legal_surface.definition_census`), built on the
family-agnostic engine (:mod:`lawvm.finland.legal_surface.family_census`). It
wires the temporal family's FOUR engine plug-points:

  1. segment-selector — :func:`_temporal_segment_selector` yields, per statute,
     each SENTENCE segment of the decoded body (from the SegmentationGraph
     substrate's ``build_clause_index`` ``sentences`` view) that carries a
     temporal-operator cue (the in-scope family discriminator) — commencement,
     validity/expiry, application/transition, or delegation.
  2. projection-fn   — :func:`_temporal_projection`: the :class:`TemporalParse`
     projection's temporal-key set (:func:`projection_temporal_keys`).
  3. oracle-fn       — :func:`_temporal_oracle`: the PRODUCTION temporal primitive
     (``meta_parse.extract_meta_surface_clauses`` for the clause-role
     classification + ``temporal_lowering`` date extractors) over the SAME span,
     lifted to the same ``{kind}:{date}`` key form (so the comparison is honest —
     identical coordinate space, identical key derivation).
  4. miss-shape-fn   — :func:`_temporal_miss_shape`: coarse structural class of a
     missed temporal clause (role + dated/undated) for ranking what blocks miss=0.

The census comparison is per SENTENCE: the projection's temporal-key set vs the
production oracle's temporal-key set over the same char span, classified
match / superset / miss / decline. Honors ``LAWVM_PARSE_TOTALITY`` via the
:class:`TemporalParse` ``assert_total_ownership`` postcondition.

Pure measure-only. Changes no production behavior; off the replay/apply path.
"""
from __future__ import annotations

from collections.abc import Iterator

from lawvm.finland.legal_surface.family_census import (
    CensusUnit,
    FamilyCensusResult,
    format_family_census_report,
    run_family_census,
)
from lawvm.finland.legal_surface.temporal_parse import (
    TEMPORAL_LANE_DECLINED,
    assert_total_ownership,
    parse_temporal_sentence,
    projection_temporal_keys,
)

#: Family id passed to the generalized engine.
TEMPORAL_FAMILY = "temporal_applicability"


def _temporal_oracle_keys_for_span(text: str) -> set[str]:
    """Run the PRODUCTION temporal primitive over a span → census key set.

    Wraps ``meta_parse.extract_meta_surface_clauses`` (the SAME classifier the
    production surface pipeline uses) and, per classified clause, extracts the ISO
    date with the production date extractors. Each clause becomes a
    ``{MetaClauseKind.value}:{date}`` key (the same form the
    :func:`temporal_key` projection produces). This is the differential-census
    ORACLE: the current production temporal-extraction output for the span.
    """
    from lawvm.core.semantic_types import MetaClauseKind
    from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
    from lawvm.finland.temporal_lowering import (
        _extract_date_from_text,
        _extract_expiry_date_from_text,
    )

    keys: set[str] = set()
    for clause in extract_meta_surface_clauses(text):
        if clause.kind == MetaClauseKind.COMMENCEMENT:
            date = _extract_date_from_text(clause.text)
        elif clause.kind == MetaClauseKind.EXPIRY:
            date = _extract_expiry_date_from_text(clause.text)
        else:
            date = ""
        keys.add(f"{clause.kind.value}:{date}")
    return keys


def _temporal_segment_selector(sid: str, body: str) -> Iterator[CensusUnit]:
    """Yield the in-scope temporal/applicability sentence units of one statute.

    Segments the body into sentences via the SegmentationGraph substrate
    (``build_clause_index``) and yields one :class:`CensusUnit` per sentence that
    carries a temporal-operator cue (the in-scope family discriminator). A
    sentence whose construction parse declined (no cue → out of family) is NOT
    yielded — like the citation family's stray-anchor skip, a non-temporal
    sentence is not a construction decline.

    A sentence that DOES carry a cue is always in scope; the construction parser
    never declines a cue-bearing sentence (it always classifies it by the
    production cue precedence), so the ``decline`` bucket stays empty for this
    family — temporal residue is carried as benign in-clause residual, not as a
    declined unit.
    """
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    index = build_clause_index(sid, body)
    for sent in index.sentences:
        seg_text = body[sent.char_start : sent.char_end]
        tp = parse_temporal_sentence(seg_text)
        if tp.parser_lane == TEMPORAL_LANE_DECLINED:
            # No temporal cue → out of family (not a construction decline).
            continue
        totality_ok = True
        try:
            assert_total_ownership(tp)
        except AssertionError:
            totality_ok = False
        role = tp.clauses[0].role if tp.clauses else "-"
        yield CensusUnit(
            text=seg_text,
            parser_lane=tp.parser_lane,
            declared_marker=f"sentence:{role}",
            declined=tp.parser_lane == TEMPORAL_LANE_DECLINED,
            totality_ok=totality_ok,
        )


def _temporal_projection(unit: CensusUnit, sid: str) -> set[str]:
    return projection_temporal_keys(parse_temporal_sentence(unit.text))


def _temporal_oracle(unit: CensusUnit, _ctx: object = None) -> set[str]:
    # The temporal family's oracle is span-local (it runs the production temporal
    # classifier + date extractors over the unit text), so it ignores the
    # per-statute oracle context the engine threads through.
    return _temporal_oracle_keys_for_span(unit.text)


def _temporal_miss_shape(missing_keys: set[str], declared_marker: str) -> str:
    """Coarse structural class of a missed temporal clause (what blocks miss=0).

    A temporal key is ``<role>:<date>``. The shape names the missed roles and
    whether the miss is dated (the projection lacked the production-extracted
    date) or undated (the projection lacked the clause entirely / role mismatch).
    """
    roles = sorted({k.split(":", 1)[0] for k in missing_keys if ":" in k})
    dated = any(k.split(":", 1)[1] for k in missing_keys if ":" in k)
    role_part = "+".join(roles) if roles else "norole"
    date_part = "dated" if dated else "undated"
    return f"{role_part}|{date_part}"


def run_temporal_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 8,
) -> FamilyCensusResult:
    """Run the temporal/applicability differential census over the corpus.

    Wires the temporal family's four plug-points into the generalized engine.
    Sampling identical to the other family censuses (``min_year`` / ``limit``);
    ``check_totality`` defaults to ``LAWVM_PARSE_TOTALITY``.
    """
    return run_family_census(
        family=TEMPORAL_FAMILY,
        segment_selector=_temporal_segment_selector,
        projection_fn=_temporal_projection,
        oracle_fn=_temporal_oracle,
        miss_shape_fn=_temporal_miss_shape,
        limit=limit,
        min_year=min_year,
        check_totality=check_totality,
        max_examples=max_examples,
    )


def main() -> None:
    import sys

    # Usage: python -m ...temporal_census [LIMIT] [MIN_YEAR]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = run_temporal_census(limit=limit, min_year=min_year)
    print(
        format_family_census_report(
            result, title="FI TEMPORAL/APPLICABILITY DIFFERENTIAL CENSUS"
        )
    )
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but in-scope units = {result.in_scope_units}"
        )


if __name__ == "__main__":
    main()
