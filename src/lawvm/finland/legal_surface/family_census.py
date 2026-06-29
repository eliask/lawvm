"""Family-agnostic differential census engine for SourceSyntaxGraph islands.

This is the reusable scaffolding extracted from the Pilot-A citation-sentence
census (:mod:`lawvm.finland.legal_surface.sentence_census`). The 4-bucket
differential machinery — match / superset / miss / decline, ``LAWVM_PARSE_TOTALITY``
totality counting, ranked miss-shape breakdown, ``parser_lane`` provenance, and
segment iteration over the :class:`SegmentationGraph` substrate — is identical for
EVERY construction-grammar island. The only things that differ between families
are FOUR plug-points:

    1. ``segment_selector``  — yield the in-scope census UNITS of a statute (each
       a :class:`CensusUnit`: a text span + provenance). For the citation family a
       unit is one sentence segment carrying an ``(id)`` anchor; for the definition
       family a unit is a definitions block (chapeau + entries) or a single-
       sentence definition.
    2. ``projection_fn``     — the construction parse's projected KEY SET for a
       unit (what the new grammar found, as a set of comparable keys).
    3. ``oracle_fn``         — the PRODUCTION extractor's KEY SET for the same span
       (the differential oracle; what production finds today).
    4. ``key_fn`` (implicit) — both projection and oracle return already-keyed
       ``set[str]`` so the comparison is a pure set differential. (The family owns
       how it derives its keys; the engine only diffs.)

Plus two optional plug-points carried on the :class:`CensusUnit` the selector
yields: a ``declined`` flag (the construction parser refused this unit → the
``decline`` bucket, typed residue not a guessed parse) and a ``totality_ok``
predicate result (whether the unit's construction parse satisfied total token
ownership — the raw-tape no-silent-drop guard, checked only under
``LAWVM_PARSE_TOTALITY``).

The engine is PURE measure-only: it iterates the corpus, decodes bodies, builds
the :class:`SegmentationGraph`, hands each statute to the family's selector, and
classifies every yielded unit. It changes NO production behaviour and is off the
replay/apply path. Each family wires its four plug-points and gets the same
self-documenting scoreboard for free.
"""
from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

#: The four census buckets, in report order. Every in-scope unit of any family
#: lands in exactly one.
CENSUS_BUCKETS: tuple[str, ...] = ("match", "superset", "miss", "decline")


@dataclass(frozen=True)
class CensusUnit:
    """One in-scope census unit a family's ``segment_selector`` yields.

    A unit is the comparison granule: a contiguous text span of a statute body
    (the span the projection and the oracle both see, so the differential is
    honest — no byte/char remapping) plus the construction-parse provenance the
    engine needs to bucket it.

    Attributes:
        text:        The EXACT span text the projection and oracle both run over.
        parser_lane: Which lane produced the construction parse (the family's
                     closed provenance set), recorded on the row.
        declared_marker: A short surface tag describing the unit (e.g. the
                     declaration cue / definition cue), for the report only.
        declined:    True iff the construction parser DECLINED this unit (typed
                     residue, not a guessed parse) → the ``decline`` bucket.
        totality_ok: Whether the construction parse satisfied total-token-
                     ownership for this unit. Only meaningful under
                     ``LAWVM_PARSE_TOTALITY``; the family computes it (or leaves it
                     True when the check is skipped).
    """

    text: str
    parser_lane: str
    declared_marker: str = ""
    declined: bool = False
    totality_ok: bool = True


@dataclass(frozen=True)
class CensusRow:
    """The census verdict for one in-scope unit."""

    statute_id: str
    bucket: str
    projection_keys: tuple[str, ...]
    oracle_keys: tuple[str, ...]
    #: Oracle keys the projection lacks (the miss frontier for this unit).
    missing_keys: tuple[str, ...]
    #: Projection keys beyond the oracle (the superset surplus).
    extra_keys: tuple[str, ...]
    declared_marker: str
    parser_lane: str
    totality_ok: bool
    text: str


@dataclass(frozen=True)
class FamilyCensusResult:
    """Outcome of a family differential census run."""

    family: str
    statutes_scanned: int
    in_scope_units: int
    #: bucket id -> count. Keys are exactly :data:`CENSUS_BUCKETS`.
    buckets: dict[str, int]
    #: Number of in-scope units whose construction parse violated total-token-
    #: ownership (a SILENT DROP). Only populated under ``LAWVM_PARSE_TOTALITY``.
    totality_violations: int
    #: missing-key shape -> count (refines the ``miss`` bucket: the structures
    #: blocking miss=0, generalized to a coarse shape).
    miss_shape_counts: dict[str, int]
    miss_examples: tuple[CensusRow, ...] = field(default_factory=tuple)
    superset_examples: tuple[CensusRow, ...] = field(default_factory=tuple)
    decline_examples: tuple[CensusRow, ...] = field(default_factory=tuple)

    @property
    def partition_total(self) -> int:
        return sum(self.buckets.values())

    def is_partition(self) -> bool:
        """The four buckets sum to the in-scope unit total (no leak)."""
        return self.partition_total == self.in_scope_units


def classify(projection: set[str], oracle: set[str], declined: bool) -> str:
    """Bucket a unit from its projection / oracle key sets + the decline flag.

    A unit that is BOTH a superset and a miss (each set has a key the other lacks)
    is classified ``miss`` (the conservative bucket; any oracle key the projection
    lacks is a frontier item).
    """
    if declined:
        return "decline"
    missing = oracle - projection
    if missing:
        return "miss"
    if projection - oracle:
        return "superset"
    return "match"


# Type aliases for the family plug-points.
SegmentSelector = Callable[[str, str], Iterator[CensusUnit]]
ProjectionFn = Callable[[CensusUnit, str], set[str]]
#: The oracle plug-point. Receives the unit AND a per-statute oracle context
#: (whatever ``oracle_prepare_fn`` returned for this statute, or ``None`` when no
#: prepare hook is wired). The context lets a family run a WHOLE-STATUTE oracle
#: once per statute and bucket its results to units, instead of re-running the
#: oracle per unit on the unit text alone (e.g. the citation family runs the full
#: production reference extractor over the statute XML, which the unit text cannot
#: reproduce, then buckets the mentions to segments by source-span overlap).
OracleFn = Callable[[CensusUnit, object], set[str]]
#: Optional per-statute oracle preparation. Called once per statute with
#: ``(statute_id, body)`` BEFORE its units are classified; its return value is
#: threaded to every ``oracle_fn`` call for that statute as the oracle context.
OraclePrepareFn = Callable[[str, str], object]
MissShapeFn = Callable[[set[str], str], str]


def run_family_census(
    *,
    family: str,
    segment_selector: SegmentSelector,
    projection_fn: ProjectionFn,
    oracle_fn: OracleFn,
    miss_shape_fn: MissShapeFn,
    oracle_prepare_fn: OraclePrepareFn | None = None,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 6,
) -> FamilyCensusResult:
    """Run a 4-bucket differential census for ONE construction-grammar family.

    Iterates the canonical Finlex corpus (or a sampled slice), decodes each
    statute body, and hands ``(statute_id, body)`` to the family's
    ``segment_selector`` to obtain the in-scope census units. For each unit it
    computes the projection key set (``projection_fn``) and the production oracle
    key set (``oracle_fn``) and classifies the unit into exactly one of the four
    buckets via :func:`classify`.

    The plug-points (the FOUR family-specific functions) are the only difference
    between families; everything else — sampling, decoding, segmentation, totality
    counting, miss-shape ranking, examples — is shared.

    Sampling: ``min_year`` restricts to statutes enacted in/after that year (a
    no-op family discriminator left to the selector); ``limit`` caps the count
    taken from that slice. With both 0 the whole corpus is scanned.

    ``check_totality`` defaults to whether ``LAWVM_PARSE_TOTALITY`` is set; when
    True, a unit's ``totality_ok`` being False counts a violation.

    Requires the canonical corpus (``LAWVM_CANONICAL_DATA_ROOT`` /
    ``LAWVM_FARCHIVE_DB``). Imports the corpus lazily.
    """
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    if check_totality is None:
        check_totality = bool(os.environ.get("LAWVM_PARSE_TOTALITY"))

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]
    if limit:
        ids = ids[:limit]

    counts: Counter[str] = Counter()
    miss_shape_counts: Counter[str] = Counter()
    miss_examples: list[CensusRow] = []
    superset_examples: list[CensusRow] = []
    decline_examples: list[CensusRow] = []
    statutes_scanned = 0
    in_scope_units = 0
    totality_violations = 0

    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            body = decode_body_text(xb)
        except Exception as exc:
            # Unexpected body-decode failure: previously ``continue`` silently
            # swallowed; now route through ``named_swallow`` so a typed Finding
            # is logged at WARNING with the statute id + family as ``clause_text``
            # (AGENTS.md §1.10 — never silent).
            #
            # log_emitter sanctioned (iter3 W2 §3.2): dev-tooling census run
            # (``run_family_census`` analysis loop) — no per-statute
            # findings_out accumulator in scope at this loop phase; per
            # ``core/named_swallow.py`` docstring's IO/utility-boundary
            # sanctioned use, the swallow stays on log_emitter (stderr WARNING).
            from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

            log_emitter()(
                build_named_swallow_finding(
                    rule_id="fi_family_census_decode_body_text",
                    exception=exc,
                    op_id=None,
                    clause_text=f"sid={sid} family={family}",
                    jurisdiction="fi",
                    source_artifact=sid,
                )
            )
            continue
        if not body:
            continue
        statutes_scanned += 1

        try:
            units = list(segment_selector(sid, body))
        except Exception as exc:
            # Unexpected segment-selector failure: previously ``continue``
            # silently swallowed; now route through ``named_swallow`` so a
            # typed Finding is logged at WARNING with the statute id + family
            # + body length as ``clause_text`` (AGENTS.md §1.10 — never silent).
            #
            # log_emitter sanctioned (iter3 W2 §3.2): same dev-tooling census
            # boundary as the body-decode swallow above — no per-statute
            # findings_out accumulator in scope; see the prior sanctioned-use note.
            from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

            log_emitter()(
                build_named_swallow_finding(
                    rule_id="fi_family_census_segment_selector",
                    exception=exc,
                    op_id=None,
                    clause_text=f"sid={sid} family={family} body_len={len(body)}",
                    jurisdiction="fi",
                    source_artifact=sid,
                )
            )
            continue

        # Per-statute oracle context: families whose oracle needs the WHOLE
        # statute (not just the unit text) build it once here. None when no hook.
        oracle_ctx: object = None
        if oracle_prepare_fn is not None and units:
            try:
                oracle_ctx = oracle_prepare_fn(sid, body)
            except Exception as exc:
                # Unexpected oracle-context failure: previously set
                # ``oracle_ctx = None`` silently swallowed; now route through
                # ``named_swallow`` so a typed Finding is logged at WARNING
                # with the statute id + family as ``clause_text``
                # (AGENTS.md §1.10 — never silent).
                #
                # log_emitter sanctioned (iter3 W2 §3.2): same dev-tooling
                # census boundary as the body-decode swallow above — no per-
                # statute findings_out accumulator in scope; see the prior
                # sanctioned-use note.
                from lawvm.core.named_swallow import build_named_swallow_finding, log_emitter

                log_emitter()(
                    build_named_swallow_finding(
                        rule_id="fi_family_census_oracle_prepare_fn",
                        exception=exc,
                        op_id=None,
                        clause_text=f"sid={sid} family={family}",
                        jurisdiction="fi",
                        source_artifact=sid,
                    )
                )
                oracle_ctx = None

        for unit in units:
            in_scope_units += 1
            if check_totality and not unit.totality_ok:
                totality_violations += 1

            projection = projection_fn(unit, sid)
            oracle = oracle_fn(unit, oracle_ctx)
            bucket = classify(projection, oracle, unit.declined)
            counts[bucket] += 1

            missing = oracle - projection
            extra = projection - oracle
            row = CensusRow(
                statute_id=sid,
                bucket=bucket,
                projection_keys=tuple(sorted(projection)),
                oracle_keys=tuple(sorted(oracle)),
                missing_keys=tuple(sorted(missing)),
                extra_keys=tuple(sorted(extra)),
                declared_marker=unit.declared_marker,
                parser_lane=unit.parser_lane,
                totality_ok=unit.totality_ok,
                text=unit.text,
            )
            if bucket == "miss":
                miss_shape_counts[miss_shape_fn(missing, unit.declared_marker)] += 1
                if len(miss_examples) < max_examples:
                    miss_examples.append(row)
            elif bucket == "superset" and len(superset_examples) < max_examples:
                superset_examples.append(row)
            elif bucket == "decline" and len(decline_examples) < max_examples:
                decline_examples.append(row)

    buckets = {b: counts.get(b, 0) for b in CENSUS_BUCKETS}
    return FamilyCensusResult(
        family=family,
        statutes_scanned=statutes_scanned,
        in_scope_units=in_scope_units,
        buckets=buckets,
        totality_violations=totality_violations,
        miss_shape_counts=dict(miss_shape_counts),
        miss_examples=tuple(miss_examples),
        superset_examples=tuple(superset_examples),
        decline_examples=tuple(decline_examples),
    )


def format_family_census_report(result: FamilyCensusResult, *, title: str) -> str:
    """Render the four-bucket differential-census scoreboard as text."""
    total = result.in_scope_units

    def pct(n: int) -> str:
        return f"{100 * n / total:.2f}%" if total else "n/a"

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(title)
    lines.append("=" * 72)
    lines.append(f"  statutes scanned                : {result.statutes_scanned}")
    lines.append(f"  in-scope units                  : {result.in_scope_units}")
    lines.append("-" * 72)
    for b in CENSUS_BUCKETS:
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

    def _examples(heading: str, rows: tuple[CensusRow, ...]) -> None:
        if not rows:
            return
        lines.append("-" * 72)
        lines.append(heading)
        lines.append("-" * 72)
        for r in rows:
            snippet = r.text if len(r.text) <= 160 else r.text[:157] + "..."
            lines.append(f"  [{r.statute_id}] marker={r.declared_marker or '-'}")
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
