"""Differential census for the delegation/authority construction.

The sixth net-new construction-grammar island after the citation pilot
(:mod:`lawvm.finland.legal_surface.sentence_census`), the definition pilot
(:mod:`lawvm.finland.legal_surface.definition_census`), the temporal island
(:mod:`lawvm.finland.legal_surface.temporal_census`), the modal island
(:mod:`lawvm.finland.legal_surface.modal_census`), and the conditions/exceptions
island (:mod:`lawvm.finland.legal_surface.condition_exception_census`), built on
the family-agnostic engine (:mod:`lawvm.finland.legal_surface.family_census`). It
wires the delegation family's engine plug-points:

  1. segment-selector — :func:`_delegation_segment_selector` yields, per statute,
     each SENTENCE segment of the decoded body (from the SegmentationGraph
     substrate's ``build_clause_index`` ``sentences`` view) whose construction
     parse produced >=1 delegation core (the in-scope family discriminator — a
     delegation/authority grant cue).
  2. projection-fn   — :func:`_delegation_projection`: the
     :class:`DelegationParse` projection's grant-key set
     (:func:`projection_grant_keys`), keyed ``grant:{kind}:{instrument}``.
  3. oracle-prepare  — :func:`_build_delegation_oracle`: runs the PRODUCTION
     forward extractor (``delegation.extract_delegations``) over the whole statute
     XML ONCE, then buckets each :class:`DelegationEdge` to the body sentence whose
     normalized text contains the edge's normalized ``match_text``. (The extractor
     needs the markup; a per-sentence re-run on decoded text alone would not match
     the production scan-unit boundaries.)
  4. oracle-fn       — :func:`_delegation_oracle`: the bucketed production grant
     keys for the unit's sentence, lifted to the SAME ``grant:{kind}:{instrument}``
     key form (so the comparison is honest — identical coordinate space).
  5. miss-shape-fn   — :func:`_delegation_miss_shape`: coarse structural class of a
     missed grant (issuer kind + instrument) for ranking what blocks miss=0.

WEAK ORACLE CAVEAT (read before trusting miss == 0)
===================================================
The production forward extractor (``extract_delegations``) is a brittle
9-positive + 7-negative-regex module with lazy-gap ``{0,150}?`` windows and a
fixed issuer/verb/instrument ordering. It MISSES delegation grants whose surface
its windows do not cover (a wide modifier gap, a holder-after-verb shape, an
agency määräys with an unregistered issuer). The construction parse, by contrast,
recognizes the grant from the CUE alone (issuer underspecified when no registered
actor binds), so it SUPERSETS the oracle on genuine grants the regex windows
miss. Those supersets are reported NEUTRALLY as construction-recall-candidates,
NOT "production bugs" — and SOME may be construction overreach (a non-delegating
``asetuksella`` reference). The real recall gates are (a) total-token-ownership /
no silent drop (``LAWVM_PARSE_TOTALITY``), and (b) raw-XML adjudication of the
superset/miss frontier — not ``miss == 0`` against this weak oracle.

The census comparison is per SENTENCE: the projection's grant-key set vs the
production oracle's grant-key set over the same span, classified
match / superset / miss / decline. Honors ``LAWVM_PARSE_TOTALITY`` via the
:class:`DelegationParse` ``assert_total_ownership`` postcondition.

The ``nojalla`` authority-BASIS dimension (``extract_asetus_authority``) lives in
decree PREAMBLES, not statute body sentences, and its identity carries a parent
statute id the forward-grant projection does not. To keep the bucket comparison
HONEST (identical key coordinate space, no phantom dimension), the basis is NOT
mixed into the grant buckets; it is reported as a SEPARATE enrichment statistic
(:func:`compute_basis_coverage`) — how many construction cores carry a
references-recognized provision basis — and exercised directly in the unit tests.

Pure measure-only. Changes no production behavior; off the replay/apply path.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from lawvm.finland.legal_surface.delegation_parse import (
    DELEGATION_LANE_DECLINED,
    INSTRUMENT_ASETUS,
    INSTRUMENT_MAARAYS,
    KIND_AGENCY,
    KIND_ASETUS,
    assert_total_ownership,
    delegation_key,
    parse_delegation_sentence,
    projection_grant_keys,
)
from lawvm.finland.legal_surface.family_census import (
    CensusUnit,
    FamilyCensusResult,
    format_family_census_report,
    run_family_census,
)

#: Family id passed to the generalized engine.
DELEGATION_FAMILY = "delegation_authority"

_WS_RE = re.compile(r"\s+")


def _norm_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _instrument_for_delegation_type(delegation_type: str) -> str:
    """The lower instrument a production ``delegation_type`` issues.

    Mirrors the construction parse's instrument classification so the oracle key
    is in the SAME coordinate space as the projection: an ``AGENCY`` grant issues
    a ``määräys``; every asetus class (VN/MIN/PRES/generic) issues an ``asetus``.
    """
    if delegation_type == KIND_AGENCY:
        return INSTRUMENT_MAARAYS
    return INSTRUMENT_ASETUS


# ---------------------------------------------------------------------------
# Honest-comparison key normalization (the weak-oracle reconciliation layer)
# ---------------------------------------------------------------------------
#
# The production forward extractor is a fallible TEACHER, not ground truth (the
# differential-reconstruction discipline). Two of its regex-window pathologies
# diverge its grant KEY from the construction's correct key for the SAME physical
# grant — NOT because the construction missed a grant, but because the oracle
# resolved it less precisely or mis-classified it. To keep the per-sentence key
# comparison HONEST (identical coordinate space), both pathologies are normalized:
#
#   (1) ISSUER-RESOLUTION granularity. The construction reads the genitive issuer
#       the oracle's bounded ``{0,N}`` window truncated, so it emits a SPECIFIC
#       asetus class (VN / MIN / PRES) where the oracle emitted the generic
#       ``ASETUS`` for the same ``asetus`` instrument (and vice versa). The issuer
#       CLASS is exactly the oracle's UNRELIABLE axis — its match_text window
#       routinely clips the issuer NP — so the per-grant comparison is made on the
#       INSTRUMENT-level canonical key (every asetus class → one ``asetus`` grant),
#       collapsing VN / MIN / PRES / generic onto the canonical asetus key on BOTH
#       sides. This is the honest coordinate space: "did the construction find an
#       asetus grant here", not "did it guess the same issuer class the (clipped)
#       oracle window did". The issuer class stays available on the cores as
#       enrichment; it is simply not the comparison axis.
#
#   (2) OBJECT-MÄÄRÄYS oracle false-positive. The ``tarkempia säännöksiä JA
#       MÄÄRÄYKSIÄ … annetaan asetuksella`` drafting shape lists "määräyksiä" as
#       the regulated OBJECT of an ASETUS grant, not as an agency instrument. The
#       oracle's ``_classify_delegation_type`` keys on the substring ``määräyksi``
#       BEFORE the generic fallback, so it mis-keys the whole ASETUS grant as
#       ``AGENCY:määräys``. The tell is unambiguous: a genuine agency grant
#       (``voi antaa määräyksiä``) never carries ``asetuksella`` in its match_text,
#       whereas this false-positive always does. So an oracle ``AGENCY`` edge whose
#       match_text contains ``asetuksella`` is RE-KEYED to the generic
#       ``ASETUS:asetus`` it actually is, before bucketing — correcting the oracle's
#       object-confusion rather than forcing the construction to replicate it.
#
# Both are documented oracle-defect corrections, not magnitude scoring; the
# construction asserts nothing it did not parse.

_CANONICAL_ASETUS_KEY = delegation_key(KIND_ASETUS, INSTRUMENT_ASETUS)
_ASETUS_KIND_KEYS = frozenset(
    {
        delegation_key("VN_ASETUS", INSTRUMENT_ASETUS),
        delegation_key("MIN_ASETUS", INSTRUMENT_ASETUS),
        delegation_key("PRES_ASETUS", INSTRUMENT_ASETUS),
        delegation_key(KIND_ASETUS, INSTRUMENT_ASETUS),
    }
)


def _canonicalize_keys(keys: set[str]) -> set[str]:
    """Collapse every asetus-issuer-class key onto the canonical ``asetus`` key.

    The issuer class (VN / MIN / PRES / generic) is the oracle's unreliable axis;
    the comparison is at instrument granularity (one ``asetus`` grant per asetus
    instrument). Non-asetus keys (the agency ``määräys``) pass through unchanged.
    """
    return {(_CANONICAL_ASETUS_KEY if k in _ASETUS_KIND_KEYS else k) for k in keys}


# ---------------------------------------------------------------------------
# Whole-statute production oracle, bucketed to body sentences.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DelegationOracleContext:
    """Per-statute production grant keys bucketed to body sentence text.

    Attributes:
        by_sentence_norm: normalized-sentence-text -> set of
            ``grant:{kind}:{instrument}`` keys the production forward extractor
            emitted whose match_text falls inside that sentence.
    """

    by_sentence_norm: dict[str, set[str]]


def _build_delegation_oracle(statute_id: str, body: str) -> _DelegationOracleContext:
    """Run the production forward extractor over the statute XML, bucket to sentences.

    Reads the statute XML from the canonical corpus (the extractor scans Akoma
    Ntoso section/subsection units the decoded body text alone cannot reproduce),
    runs ``delegation.extract_delegations``, and buckets each emitted
    :class:`DelegationEdge` to the body sentence whose normalized text CONTAINS the
    edge's normalized ``match_text``. Each edge contributes a
    ``grant:{delegation_type}:{instrument}`` key.

    Fails closed to an empty oracle for any statute whose XML is unavailable or
    unparseable — the census then treats every sentence as oracle-empty for that
    statute (an honest under-count, never a fabrication).
    """
    from farchive import Farchive

    from lawvm.finland.delegation import extract_delegations
    from lawvm.finland.legal_surface.clause_segment import build_clause_index
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    try:
        store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
        xb = store.read_source(statute_id) or store.read_amendment(statute_id)
    except Exception:
        xb = None
    if not xb:
        return _DelegationOracleContext(by_sentence_norm={})

    try:
        edges = list(extract_delegations(xb, statute_id).accepted_items)
    except Exception:
        return _DelegationOracleContext(by_sentence_norm={})

    # Pre-segment the body the SAME way the selector does, so the normalized
    # sentence keys line up with the units the engine hands the oracle.
    try:
        index = build_clause_index(statute_id, body)
    except Exception:
        return _DelegationOracleContext(by_sentence_norm={})

    sent_norms: list[str] = [
        _norm_ws(body[s.char_start : s.char_end]) for s in index.sentences
    ]

    by_sentence: dict[str, set[str]] = {}
    for edge in edges:
        mt = _norm_ws(edge.match_text)
        if not mt:
            continue
        delegation_type = edge.delegation_type
        # OBJECT-MÄÄRÄYS oracle false-positive correction: an AGENCY edge whose
        # match_text carries ``asetuksella`` is a mis-keyed ASETUS grant (the
        # "määräyksiä" is the regulated object, not an agency instrument). Re-key it
        # to the generic ASETUS it actually is. A genuine agency grant never carries
        # ``asetuksella``, so this never reclassifies a real agency edge.
        if delegation_type == KIND_AGENCY and "asetuksella" in mt.lower():
            delegation_type = KIND_ASETUS
        key = delegation_key(
            delegation_type, _instrument_for_delegation_type(delegation_type)
        )
        # Collapse the issuer class onto the canonical asetus key (the comparison
        # is instrument-granular; the issuer class is the oracle's unreliable axis).
        key = next(iter(_canonicalize_keys({key})))
        for sn in sent_norms:
            if mt in sn:
                by_sentence.setdefault(sn, set()).add(key)
    return _DelegationOracleContext(by_sentence_norm=by_sentence)


def _delegation_oracle(unit: CensusUnit, ctx: object) -> set[str]:
    """The production grant keys bucketed to this unit's sentence."""
    if not isinstance(ctx, _DelegationOracleContext):
        return set()
    return set(ctx.by_sentence_norm.get(_norm_ws(unit.text), set()))


# ---------------------------------------------------------------------------
# The delegation family's projection + selector + miss-shape plug-points.
# ---------------------------------------------------------------------------


def _delegation_segment_selector(sid: str, body: str) -> Iterator[CensusUnit]:
    """Yield the in-scope delegation sentence units of one statute.

    Segments the body into sentences via the SegmentationGraph substrate
    (``build_clause_index``) and yields one :class:`CensusUnit` per sentence whose
    construction parse produced >=1 delegation core (the in-scope family
    discriminator — a delegation/authority grant cue). A sentence whose
    construction parse declined (a delegation-looking surface that did NOT yield a
    core, or no cue at all) is NOT yielded — like the citation family's
    stray-anchor skip, a non-delegating sentence is not a construction decline; it
    is simply out of family.
    """
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    index = build_clause_index(sid, body)
    for sent in index.sentences:
        seg_text = body[sent.char_start : sent.char_end]
        if "asetukse" not in seg_text.lower() and "määräyks" not in seg_text.lower():
            continue  # fast family prefilter
        dp = parse_delegation_sentence(seg_text)
        if dp.parser_lane == DELEGATION_LANE_DECLINED:
            # No delegation core parsed → out of family (not a construction decline).
            continue
        totality_ok = True
        try:
            assert_total_ownership(dp)
        except AssertionError:
            totality_ok = False
        kind = dp.cores[0].kind if dp.cores else "-"
        yield CensusUnit(
            text=seg_text,
            parser_lane=dp.parser_lane,
            declared_marker=f"sentence:{kind}",
            declined=dp.parser_lane == DELEGATION_LANE_DECLINED,
            totality_ok=totality_ok,
        )


def _delegation_projection(unit: CensusUnit, sid: str) -> set[str]:
    # Canonicalized to instrument granularity (the asetus issuer class is the
    # oracle's unreliable axis — see the honest-comparison normalization above).
    return _canonicalize_keys(projection_grant_keys(parse_delegation_sentence(unit.text)))


def _delegation_miss_shape(missing_keys: set[str], declared_marker: str) -> str:
    """Coarse structural class of a missed grant (what blocks miss=0).

    A grant key is ``grant:<kind>:<instrument>``. The shape names the missed issuer
    kind(s) + instrument(s): which grant the projection lacked that the (weak)
    oracle found.
    """
    kinds = sorted({k.split(":")[1] for k in missing_keys if k.count(":") == 2})
    instrs = sorted({k.split(":")[2] for k in missing_keys if k.count(":") == 2})
    kind_part = "+".join(kinds) if kinds else "nokind"
    instr_part = "+".join(instrs) if instrs else "noinstr"
    return f"{kind_part}|{instr_part}"


# ---------------------------------------------------------------------------
# Basis-coverage enrichment statistic (the ``nojalla`` provision-basis recall).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BasisCoverage:
    """Construction-parse ``nojalla`` provision-basis recall over a corpus slice.

    Attributes:
        cores_total:        delegation cores parsed.
        cores_with_basis:   of those, cores carrying a recognized provision basis
                            window (a ``… (NUM/YEAR) N §:n nojalla`` tail).
        basis_targets_total: total references-recognized provision target labels
                            across all basis windows (a coordinated ``ja … nojalla``
                            window contributes >1).
        examples:           a few example basis-bearing sentence snippets + targets.
    """

    cores_total: int
    cores_with_basis: int
    basis_targets_total: int
    examples: tuple[str, ...] = ()


def compute_basis_coverage(
    *, limit: int = 0, min_year: int = 0, max_examples: int = 8
) -> BasisCoverage:
    """Compute the construction-parse ``nojalla`` provision-basis coverage.

    Iterates the SAME corpus slice the census uses, segments each body into
    sentences, parses each delegation sentence, and counts how many cores carry a
    references-recognized provision basis. This is an enrichment statistic the
    forward-grant bucket comparison deliberately excludes (the basis identity
    carries a parent statute id the forward grant lacks), reported separately so
    the basis recall is visible without polluting the grant buckets.
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

    cores_total = 0
    cores_with_basis = 0
    basis_targets_total = 0
    examples: list[str] = []
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
            seg_text = body[sent.char_start : sent.char_end]
            if "nojalla" not in seg_text.lower():
                continue
            dp = parse_delegation_sentence(seg_text)
            for c in dp.cores:
                cores_total += 1
                if c.basis_start is not None and c.basis_targets:
                    cores_with_basis += 1
                    basis_targets_total += len(c.basis_targets)
                    if len(examples) < max_examples:
                        snippet = (
                            seg_text if len(seg_text) <= 160 else seg_text[:157] + "..."
                        )
                        examples.append(
                            f"[{sid}] targets={list(c.basis_targets)} {snippet!r}"
                        )

    return BasisCoverage(
        cores_total=cores_total,
        cores_with_basis=cores_with_basis,
        basis_targets_total=basis_targets_total,
        examples=tuple(examples),
    )


def format_basis_coverage_report(cov: BasisCoverage) -> str:
    lines: list[str] = []
    lines.append("-" * 72)
    lines.append("nojalla provision-basis coverage (construction enrichment)")
    lines.append("-" * 72)
    lines.append(f"  delegation cores in nojalla sentences : {cov.cores_total}")
    lines.append(f"  of those, carrying a recognized basis : {cov.cores_with_basis}")
    lines.append(f"  total recognized basis targets        : {cov.basis_targets_total}")
    if cov.examples:
        lines.append("  basis examples:")
        for ex in cov.examples:
            lines.append(f"    {ex}")
    lines.append("")
    return "\n".join(lines)


def run_delegation_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 8,
) -> FamilyCensusResult:
    """Run the delegation/authority differential census over the corpus.

    Wires the delegation family's plug-points into the generalized engine.
    Sampling identical to the other family censuses (``min_year`` / ``limit``);
    ``check_totality`` defaults to ``LAWVM_PARSE_TOTALITY``. The oracle is the
    production forward extractor run once per statute (``oracle_prepare_fn``) and
    bucketed to body sentences.
    """
    return run_family_census(
        family=DELEGATION_FAMILY,
        segment_selector=_delegation_segment_selector,
        projection_fn=_delegation_projection,
        oracle_fn=_delegation_oracle,
        miss_shape_fn=_delegation_miss_shape,
        oracle_prepare_fn=_build_delegation_oracle,
        limit=limit,
        min_year=min_year,
        check_totality=check_totality,
        max_examples=max_examples,
    )


def main() -> None:
    import sys

    # Usage: python -m ...delegation_census [LIMIT] [MIN_YEAR]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = run_delegation_census(limit=limit, min_year=min_year)
    print(
        format_family_census_report(
            result, title="FI DELEGATION/AUTHORITY DIFFERENTIAL CENSUS"
        )
    )
    # The nojalla provision-basis enrichment (reported separately from the grant
    # buckets — different identity coordinate space).
    cov = compute_basis_coverage(limit=limit, min_year=min_year)
    print(format_basis_coverage_report(cov))
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but in-scope units = {result.in_scope_units}"
        )


if __name__ == "__main__":
    main()
