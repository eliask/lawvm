"""Differential census for the citation-bearing-sentence construction (Pilot A).

Mirrors the johtolause full-accounting census
(``lawvm.finland.johtolause.census_accounting``) ONE LEVEL UP: instead of
comparing the new amendment-clause grammar against the legacy clause parser per
johtolause, it compares the citation-bearing-sentence CONSTRUCTION projection
(:mod:`lawvm.finland.legal_surface.sentence_parse`) against the PRODUCTION
reference-extraction oracle, per citation-bearing sentence/clause segment.

The unit of census is a SENTENCE/CLAUSE SEGMENT of a statute's decoded body, as
identified by the :class:`SegmentationGraph` substrate's ``build_clause_index``
(``sentences`` view). A segment is IN SCOPE for this family iff it carries at
least one ``(NUMBER/YEAR)`` statute-id anchor (the family discriminator the
construction parser keys on).

For each in-scope segment we compute two reference sets, keyed by
``ProvisionRef.serialized()`` (the production dedup key):

  * the CONSTRUCTION projection set (``projection_reference_keys``);
  * the production ORACLE set (``oracle_reference_keys_for_span``).

and classify the segment into EXACTLY ONE of four buckets — the same four the
johtolause census uses, lifted to sets:

  1. ``match``    — projection set == oracle set (the parity win).
  2. ``superset`` — projection ⊋ oracle (projection finds STRICTLY more; a
                    candidate adjudicated win — the construction recovered a
                    reference the production oracle missed in this span).
  3. ``miss``     — oracle has a key the projection does NOT (oracle found
                    something the construction missed). The frontier: the
                    distance from miss=0 is exactly the count of these.
  4. ``decline``  — the construction parser DECLINED the segment
                    (``parser_lane == DECLINED``) — typed residue, not a guessed
                    parse. (An in-scope segment never declines in v0, since the
                    family discriminator IS an ``(id)`` anchor; a decline here
                    would be a real construction-parser refusal.)

A segment that is BOTH a superset and a miss (each set has a key the other
lacks — symmetric difference both ways) is classified as ``miss`` (the
conservative bucket; any oracle key the projection lacks is a frontier item).

The census also honors ``LAWVM_PARSE_TOTALITY``: when set, every in-scope
segment's :func:`assert_total_ownership` postcondition is checked, and any
violation is counted as a ``totality_violation`` (a SILENT DROP in the
construction parse — the raw-tape no-silent-drop guarantee one level up). This
mirrors the johtolause totality predicate's raw-tape coverage check.

Pure measure-only. Imports the corpus + extractor lazily; changes no production
behavior; is off the replay/apply path.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from lawvm.finland.legal_surface.sentence_parse import (
    SENTENCE_LANE_DECLINED,
    assert_total_ownership,
    oracle_reference_keys_for_span,
    parse_citation_sentence,
    projection_reference_keys,
)

#: The four census buckets, in report order. Every in-scope citation-bearing
#: segment lands in exactly one.
SENTENCE_CENSUS_BUCKETS: tuple[str, ...] = ("match", "superset", "miss", "decline")


@dataclass(frozen=True)
class SegmentCensusRow:
    """The census verdict for one in-scope citation-bearing segment."""

    statute_id: str
    bucket: str
    projection_keys: tuple[str, ...]
    oracle_keys: tuple[str, ...]
    #: Oracle keys the projection lacks (the miss frontier for this segment).
    missing_keys: tuple[str, ...]
    #: Projection keys beyond the oracle (the superset surplus).
    extra_keys: tuple[str, ...]
    declaration_marker: str
    parser_lane: str
    totality_ok: bool
    text: str


@dataclass(frozen=True)
class SentenceCensusResult:
    """Outcome of a citation-bearing-sentence differential census run."""

    statutes_scanned: int
    segments_total: int
    in_scope_segments: int
    #: bucket id -> count. Keys are exactly :data:`SENTENCE_CENSUS_BUCKETS`.
    buckets: dict[str, int]
    #: Number of in-scope segments whose construction parse violated
    #: total-token-ownership (a SILENT DROP). Only populated under
    #: ``LAWVM_PARSE_TOTALITY``; 0 otherwise (check skipped).
    totality_violations: int
    #: missing-key shape -> count (refines the ``miss`` bucket: the structures
    #: blocking miss=0, generalized to a coarse shape).
    miss_shape_counts: dict[str, int]
    #: A few example miss rows (for the report).
    miss_examples: tuple[SegmentCensusRow, ...] = field(default_factory=tuple)
    #: A few example superset rows (for the report).
    superset_examples: tuple[SegmentCensusRow, ...] = field(default_factory=tuple)
    #: A few example decline rows (for the report).
    decline_examples: tuple[SegmentCensusRow, ...] = field(default_factory=tuple)

    @property
    def partition_total(self) -> int:
        return sum(self.buckets.values())

    def is_partition(self) -> bool:
        """The four buckets sum to the in-scope segment total (no leak)."""
        return self.partition_total == self.in_scope_segments


def _classify(projection: set[str], oracle: set[str], declined: bool) -> str:
    if declined:
        return "decline"
    missing = oracle - projection
    if missing:
        return "miss"
    if projection - oracle:
        return "superset"
    return "match"


def _miss_shape(missing_keys: set[str], declaration_marker: str) -> str:
    """Generalize a miss to a coarse shape for ranking what blocks miss=0.

    The shape names the structural class of the missed keys: a sub-provision miss
    (the oracle key carries momentti/kohta the projection lacks), a chapter miss,
    a whole-statute miss, or a section miss — plus whether the segment carried a
    declaration cue. Index-bearing labels are collapsed to ``*``.
    """
    has_chapter = any("/ch" in k for k in missing_keys)
    has_kohta = any("/k" in k for k in missing_keys)
    # a momentti is a bare integer segment after the section; approximate by a
    # 3+-segment key (statute/.../section/momentti...).
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


def run_sentence_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 6,
) -> SentenceCensusResult:
    """Run the citation-bearing-sentence differential census over the corpus.

    Iterates the canonical Finlex corpus (or a sampled slice), decodes each
    statute body, segments it into sentences via the SegmentationGraph substrate
    (``build_clause_index``), and for every sentence that carries a statute-id
    anchor (the in-scope family) compares the construction projection against the
    production reference-extraction oracle.

    Sampling: ``min_year`` restricts to statutes enacted in/after that year
    (inline ``(NUMBER/YEAR)`` cross-statute citations are a MODERN convention —
    pre-2000 statutes carry almost none, so a bare prefix ``limit`` over the
    full corpus samples mostly citation-free 1700s–1900s acts). ``limit`` then
    caps the count taken from that (year-filtered) slice. With both 0 the whole
    corpus is scanned.

    ``check_totality`` defaults to whether ``LAWVM_PARSE_TOTALITY`` is set; when
    True, every in-scope segment's total-token-ownership postcondition is checked
    and violations counted (the raw-tape no-silent-drop guard one level up).

    Requires the canonical corpus (``LAWVM_CANONICAL_DATA_ROOT`` /
    ``LAWVM_FARCHIVE_DB``). Imports the corpus + extractor lazily.
    """
    import os

    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.legal_surface.clause_segment import build_clause_index
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    if check_totality is None:
        check_totality = bool(os.environ.get("LAWVM_PARSE_TOTALITY"))

    store = TransparentCorpusStore(Farchive(_archive_path()))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]
    if limit:
        ids = ids[:limit]

    counts: Counter[str] = Counter()
    miss_shape_counts: Counter[str] = Counter()
    miss_examples: list[SegmentCensusRow] = []
    superset_examples: list[SegmentCensusRow] = []
    decline_examples: list[SegmentCensusRow] = []
    statutes_scanned = 0
    segments_total = 0
    in_scope_segments = 0
    totality_violations = 0

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
        statutes_scanned += 1

        try:
            index = build_clause_index(sid, body)
        except Exception:
            continue

        for sent in index.sentences:
            seg_text = body[sent.char_start : sent.char_end]
            segments_total += 1
            if "(" not in seg_text or "/" not in seg_text:
                continue  # fast family prefilter: needs an (id) paren
            sp = parse_citation_sentence(seg_text)
            if sp.kind != "citation_bearing" and sp.parser_lane != SENTENCE_LANE_DECLINED:
                continue
            # In scope iff a citation construction was found OR the parser
            # explicitly declined a segment that looked citation-bearing. A
            # declined segment with no (id) anchor is simply out of family.
            declined = sp.parser_lane == SENTENCE_LANE_DECLINED
            if declined and not sp.citations:
                # No anchor parsed at all -> out of family, not a construction
                # decline. (The prefilter let it through on a stray '(' + '/'.)
                continue
            in_scope_segments += 1

            totality_ok = True
            if check_totality:
                try:
                    assert_total_ownership(sp)
                except AssertionError:
                    totality_ok = False
                    totality_violations += 1

            projection = projection_reference_keys(sp, sid)
            oracle = oracle_reference_keys_for_span(seg_text)
            bucket = _classify(projection, oracle, declined)
            counts[bucket] += 1

            missing = oracle - projection
            extra = projection - oracle
            row = SegmentCensusRow(
                statute_id=sid,
                bucket=bucket,
                projection_keys=tuple(sorted(projection)),
                oracle_keys=tuple(sorted(oracle)),
                missing_keys=tuple(sorted(missing)),
                extra_keys=tuple(sorted(extra)),
                declaration_marker=sp.declaration_marker,
                parser_lane=sp.parser_lane,
                totality_ok=totality_ok,
                text=seg_text,
            )
            if bucket == "miss":
                miss_shape_counts[_miss_shape(missing, sp.declaration_marker)] += 1
                if len(miss_examples) < max_examples:
                    miss_examples.append(row)
            elif bucket == "superset" and len(superset_examples) < max_examples:
                superset_examples.append(row)
            elif bucket == "decline" and len(decline_examples) < max_examples:
                decline_examples.append(row)

    buckets = {b: counts.get(b, 0) for b in SENTENCE_CENSUS_BUCKETS}
    return SentenceCensusResult(
        statutes_scanned=statutes_scanned,
        segments_total=segments_total,
        in_scope_segments=in_scope_segments,
        buckets=buckets,
        totality_violations=totality_violations,
        miss_shape_counts=dict(miss_shape_counts),
        miss_examples=tuple(miss_examples),
        superset_examples=tuple(superset_examples),
        decline_examples=tuple(decline_examples),
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
    lines.append(f"  body segments total             : {result.segments_total}")
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
        for shape, n in sorted(
            result.miss_shape_counts.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {n:6d}  {shape}")
        lines.append("")

    def _examples(title: str, rows: tuple[SegmentCensusRow, ...]) -> None:
        if not rows:
            return
        lines.append("-" * 72)
        lines.append(title)
        lines.append("-" * 72)
        for r in rows:
            snippet = r.text if len(r.text) <= 140 else r.text[:137] + "..."
            lines.append(f"  [{r.statute_id}] cue={r.declaration_marker or '-'}")
            lines.append(f"    proj  : {list(r.projection_keys)}")
            lines.append(f"    oracle: {list(r.oracle_keys)}")
            if r.missing_keys:
                lines.append(f"    MISS  : {list(r.missing_keys)}")
            if r.extra_keys:
                lines.append(f"    EXTRA : {list(r.extra_keys)}")
            lines.append(f"    text  : {snippet!r}")
        lines.append("")

    _examples("miss examples (oracle found, projection did not)", result.miss_examples)
    _examples("superset examples (projection found strictly more)", result.superset_examples)
    _examples("decline examples (construction parser refused)", result.decline_examples)

    return "\n".join(lines)


def main() -> None:
    import sys

    # Usage: python -m ...sentence_census [LIMIT] [MIN_YEAR]
    # e.g. `... 400 2015` = first 400 statutes enacted in/after 2015.
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
