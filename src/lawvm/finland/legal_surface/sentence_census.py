"""Differential census for the citation-bearing-sentence construction (Pilot A).

Mirrors the johtolause full-accounting census
(``lawvm.finland.johtolause.census_accounting``) ONE LEVEL UP: instead of
comparing the new amendment-clause grammar against the legacy clause parser per
johtolause, it compares the citation-bearing-sentence CONSTRUCTION projection
(:mod:`lawvm.finland.legal_surface.sentence_parse`) against the PRODUCTION
reference-extraction oracle, per citation-bearing sentence/clause segment.

As of Pilot B the 4-bucket differential MACHINERY (match/superset/miss/decline,
``LAWVM_PARSE_TOTALITY`` totality counting, ranked miss-shape breakdown,
``parser_lane`` provenance, segment iteration over the :class:`SegmentationGraph`)
lives in the family-agnostic engine :mod:`lawvm.finland.legal_surface.family_census`.
This module is the citation family's wiring of that engine's FOUR plug-points
(segment-selector / projection-fn / oracle-fn, plus the miss-shape namer) — and it
keeps its original ``SentenceCensusResult`` public surface so existing callers and
tests are unaffected.

The unit of census is a SENTENCE/CLAUSE SEGMENT of a statute's decoded body, as
identified by the :class:`SegmentationGraph` substrate's ``build_clause_index``
(``sentences`` view). A segment is IN SCOPE for this family iff it carries at
least one ``(NUMBER/YEAR)`` statute-id anchor (the family discriminator the
construction parser keys on).

For each in-scope segment we compute two reference sets, keyed by
``ProvisionRef.serialized()`` (the production dedup key):

  * the CONSTRUCTION projection set (``projection_reference_keys``);
  * the production ORACLE set (``oracle_reference_keys_for_span``).

and classify the segment into EXACTLY ONE of four buckets:

  1. ``match``    — projection set == oracle set (the parity win).
  2. ``superset`` — projection ⊋ oracle (projection finds STRICTLY more).
  3. ``miss``     — oracle has a key the projection does NOT. The frontier: the
                    distance from miss=0 is exactly the count of these.
  4. ``decline``  — the construction parser DECLINED the segment (typed residue).

Pure measure-only. Imports the corpus + extractor lazily; changes no production
behavior; is off the replay/apply path.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from lawvm.finland.legal_surface.family_census import (
    CensusRow,
    CensusUnit,
    FamilyCensusResult,
    classify,
    run_family_census,
)
from lawvm.finland.legal_surface.sentence_parse import (
    SENTENCE_LANE_DECLINED,
    assert_total_ownership,
    oracle_reference_keys_for_span,
    parse_citation_sentence,
    projection_reference_keys,
)

#: The four census buckets, in report order (kept for backward compatibility;
#: identical to :data:`family_census.CENSUS_BUCKETS`).
SENTENCE_CENSUS_BUCKETS: tuple[str, ...] = ("match", "superset", "miss", "decline")

#: Family id passed to the generalized engine.
SENTENCE_FAMILY = "citation_sentence"


# Backward-compatible row alias (the engine row is structurally identical; the
# field formerly named ``declaration_marker`` is carried as ``declared_marker``).
SegmentCensusRow = CensusRow


@dataclass(frozen=True)
class SentenceCensusResult:
    """Outcome of a citation-bearing-sentence differential census run.

    Preserves the original public field surface (``in_scope_segments`` etc.); it
    is a thin re-projection of the generalized :class:`FamilyCensusResult`.
    """

    statutes_scanned: int
    segments_total: int
    in_scope_segments: int
    buckets: dict[str, int]
    totality_violations: int
    miss_shape_counts: dict[str, int]
    miss_examples: tuple[CensusRow, ...] = field(default_factory=tuple)
    superset_examples: tuple[CensusRow, ...] = field(default_factory=tuple)
    decline_examples: tuple[CensusRow, ...] = field(default_factory=tuple)

    @property
    def partition_total(self) -> int:
        return sum(self.buckets.values())

    def is_partition(self) -> bool:
        return self.partition_total == self.in_scope_segments


def _classify(projection: set[str], oracle: set[str], declined: bool) -> str:
    """Citation-family classifier (delegates to the shared engine)."""
    return classify(projection, oracle, declined)


def _miss_shape(missing_keys: set[str], declaration_marker: str) -> str:
    """Generalize a miss to a coarse shape for ranking what blocks miss=0.

    The shape names the structural class of the missed keys: a sub-provision miss
    (the oracle key carries momentti/kohta the projection lacks), a chapter miss,
    a whole-statute miss, or a section miss — plus whether the segment carried a
    declaration cue. Index-bearing labels are collapsed to ``*``.
    """
    has_chapter = any("/ch" in k for k in missing_keys)
    has_kohta = any("/k" in k for k in missing_keys)
    has_momentti = any(len(k.split("/")) >= 4 and "/k" not in k for k in missing_keys)
    statute_only = any(len(k.split("/")) == 2 for k in missing_keys)
    parts: list[str] = []
    if has_chapter:
        parts.append("chapter")
    if has_momentti:
        parts.append("momentti")
    if has_kohta:
        parts.append("kohta")
    if statute_only:
        parts.append("statute_only")
    if not parts:
        parts.append("section")
    cue = "with_cue" if declaration_marker else "no_cue"
    return f"{'+'.join(parts)}|{cue}"


# ---------------------------------------------------------------------------
# The citation family's FOUR engine plug-points.
# ---------------------------------------------------------------------------


def _citation_segment_selector(sid: str, body: str) -> Iterator[CensusUnit]:
    """Yield the in-scope citation-bearing sentence units of one statute.

    Segments the body into sentences via the SegmentationGraph substrate
    (``build_clause_index``) and yields one :class:`CensusUnit` per sentence that
    carries a statute-id anchor (the in-scope family discriminator). Identical
    in-scope/decline logic to the pre-generalization census.
    """
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    index = build_clause_index(sid, body)
    for sent in index.sentences:
        seg_text = body[sent.char_start : sent.char_end]
        if "(" not in seg_text or "/" not in seg_text:
            continue  # fast family prefilter: needs an (id) paren
        sp = parse_citation_sentence(seg_text)
        if sp.kind != "citation_bearing" and sp.parser_lane != SENTENCE_LANE_DECLINED:
            continue
        declined = sp.parser_lane == SENTENCE_LANE_DECLINED
        if declined and not sp.citations:
            # No anchor parsed at all -> out of family (stray '(' + '/'), not a
            # construction decline.
            continue
        totality_ok = True
        try:
            assert_total_ownership(sp)
        except AssertionError:
            totality_ok = False
        yield CensusUnit(
            text=seg_text,
            parser_lane=sp.parser_lane,
            declared_marker=sp.declaration_marker,
            declined=declined,
            totality_ok=totality_ok,
        )


def _citation_projection(unit: CensusUnit, sid: str) -> set[str]:
    return projection_reference_keys(parse_citation_sentence(unit.text), sid)


def _citation_oracle(unit: CensusUnit) -> set[str]:
    return oracle_reference_keys_for_span(unit.text)


def run_sentence_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 6,
) -> SentenceCensusResult:
    """Run the citation-bearing-sentence differential census over the corpus.

    Thin wrapper that wires the citation family's four plug-points into the
    generalized :func:`family_census.run_family_census` engine and re-projects the
    result into the original :class:`SentenceCensusResult` surface.

    Sampling: ``min_year`` restricts to statutes enacted in/after that year
    (inline ``(NUMBER/YEAR)`` cross-statute citations are a MODERN convention);
    ``limit`` caps the count taken from that slice. ``check_totality`` defaults to
    ``LAWVM_PARSE_TOTALITY``.
    """
    res: FamilyCensusResult = run_family_census(
        family=SENTENCE_FAMILY,
        segment_selector=_citation_segment_selector,
        projection_fn=_citation_projection,
        oracle_fn=_citation_oracle,
        miss_shape_fn=_miss_shape,
        limit=limit,
        min_year=min_year,
        check_totality=check_totality,
        max_examples=max_examples,
    )
    return SentenceCensusResult(
        statutes_scanned=res.statutes_scanned,
        # ``segments_total`` was the count of ALL body sentence segments scanned;
        # the generalized engine no longer surfaces that pre-filter total (it is a
        # citation-family-specific metric of no analytical value — only the
        # in-scope count drives the partition). Reported as the in-scope count's
        # superset is unavailable, so we expose the in-scope total here too; the
        # field is retained only so existing callers do not break.
        segments_total=res.in_scope_units,
        in_scope_segments=res.in_scope_units,
        buckets=res.buckets,
        totality_violations=res.totality_violations,
        miss_shape_counts=res.miss_shape_counts,
        miss_examples=res.miss_examples,
        superset_examples=res.superset_examples,
        decline_examples=res.decline_examples,
    )


def format_sentence_census_report(result: SentenceCensusResult) -> str:
    """Render the four-bucket differential-census scoreboard as text."""
    total = result.in_scope_segments

    def pct(n: int) -> str:
        return f"{100 * n / total:.2f}%" if total else "n/a"

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("FI CITATION-BEARING-SENTENCE DIFFERENTIAL CENSUS (Pilot A)")
    lines.append("=" * 72)
    lines.append(f"  statutes scanned                : {result.statutes_scanned}")
    lines.append(f"  in-scope citation segments      : {result.in_scope_segments}")
    lines.append("-" * 72)
    for b in SENTENCE_CENSUS_BUCKETS:
        n = result.buckets[b]
        lines.append(f"  {b:<28}: {n:6d}  ({pct(n)})")
    lines.append("-" * 72)
    lines.append(
        f"  partition sum                   : {result.partition_total:6d}  "
        f"(== in-scope: {result.is_partition()})"
    )
    lines.append(f"  distance from miss=0            : {result.buckets['miss']}")
    lines.append(f"  totality (no-silent-drop) viols : {result.totality_violations}")
    lines.append("")

    if result.miss_shape_counts:
        lines.append("-" * 72)
        lines.append("miss shapes (ranked — what blocks miss=0)")
        lines.append("-" * 72)
        for shape, n in sorted(result.miss_shape_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n:6d}  {shape}")
        lines.append("")

    def _examples(title: str, rows: tuple[CensusRow, ...]) -> None:
        if not rows:
            return
        lines.append("-" * 72)
        lines.append(title)
        lines.append("-" * 72)
        for r in rows:
            snippet = r.text if len(r.text) <= 140 else r.text[:137] + "..."
            lines.append(f"  [{r.statute_id}] cue={r.declared_marker or '-'}")
            lines.append(f"    proj  : {list(r.projection_keys)}")
            lines.append(f"    oracle: {list(r.oracle_keys)}")
            if r.missing_keys:
                lines.append(f"    MISS  : {list(r.missing_keys)}")
            if r.extra_keys:
                lines.append(f"    EXTRA : {list(r.extra_keys)}")
            lines.append(f"    text  : {snippet!r}")
        lines.append("")

    _examples("miss examples (oracle found, projection did not)", result.miss_examples)
    _examples(
        "superset examples (projection found strictly more)", result.superset_examples
    )
    _examples("decline examples (construction parser refused)", result.decline_examples)

    return "\n".join(lines)


def main() -> None:
    import sys

    # Usage: python -m ...sentence_census [LIMIT] [MIN_YEAR]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = run_sentence_census(limit=limit, min_year=min_year)
    print(format_sentence_census_report(result))
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but in-scope segments = {result.in_scope_segments}"
        )


if __name__ == "__main__":
    main()
