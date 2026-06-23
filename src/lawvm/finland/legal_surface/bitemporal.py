"""Corpus bitemporal broken-reference scan — the dangling-citation report.

This is the corpus-scale consumer of the pure bitemporal detector in
``lawvm.finland.references.broken_detection``. It realizes the idea of
"semantic links on top of deterministic text-state across versions": for every
resolved cross-statute citation in a statute, ask whether the cited target
provision is actually present in the *time-indexed text-state* of the target
statute — both as of the citation and as of now.

What a finding IS (surface-fact discipline)
-------------------------------------------
A finding here is strictly: *the cited target provision is absent (or has moved)
in the time-indexed text-state of the target statute as of the citation*. It is
NOT a legal conclusion ("the law is invalid", "the citation is wrong"). The text
state is deterministic point-in-time replay (``legal_pit``); the report only
states what the replayed tree does or does not contain at the cited address.

Pipeline, per statute
----------------------
1. Read the citing statute's body XML from the corpus (archive-only — the body
   scan itself does no replay).
2. ``extract_all_reference_mentions`` → resolved cross-statute mentions (the
   detector skips UNRESOLVED / OPEN / AMBIGUOUS / already-BROKEN and refs with
   no resolved target statute identity).
3. Run ``detect_broken`` with the default ``legal_pit``-backed adapters
   (``default_tree_as_of`` / ``default_provision_present``). The materialization
   IS heavy point-in-time replay of the TARGET statute — this is the slow part.
4. Aggregate ``BrokenReferenceFinding`` by reason (repealed_since /
   renumbered_since / never_existed) and ``BrokenCheckUnavailable`` separately.

Fail-loud (AGENTS.md §1.1)
--------------------------
A target whose tree cannot be materialized is reported as ``BrokenCheckUnavailable``
— NEVER silently dropped and NEVER called broken. Brokenness stays *undetermined*
for that reference. A citing statute whose own body scan raises is recorded in an
errored bucket by id, never silently skipped.

Citation-time anchor
--------------------
``detect_broken`` needs a citation start date to tell REPEALED/RENUMBERED apart
from "live target". The extractor leaves ``valid_at_interval`` open by default,
so this scan supplies a coarse, honest anchor when the mention carries none: a
citation cannot pre-date the citing statute, so we anchor to 1 January of the
citing statute's enactment year (parsed from its ``NUMBER/YEAR`` id). This is a
lower bound on "when the citation could have been written", deliberately
conservative; it never claims a more precise date than the id supports. When the
mention already carries a concrete ``valid_at`` start, that is used as-is.
"""
from __future__ import annotations

import collections
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from lawvm.core.ir import IRNode
from lawvm.finland.references.broken_detection import (
    BrokenCheckUnavailable,
    BrokenReason,
    BrokenReferenceFinding,
    LifecycleLookup,
    ProvisionPresent,
    StatuteLifecycle,
    StatuteLifecycleFinding,
    StatuteLifecycleUnverifiable,
    TreeAsOf,
    default_provision_present,
    detect_broken,
    detect_statute_lifecycle_broken,
)

if TYPE_CHECKING:
    from lawvm.core.reference_mention import ProvisionRef, ReferenceMention
    from lawvm.corpus_store import CorpusStore


__all__ = [
    "StatuteScanResult",
    "BrokenRefReport",
    "scan_broken_references",
    "citation_anchor_for_statute",
    "legal_pit_tree_as_of",
    "PitCache",
    "cached_tree_as_of",
    # Current-state (no-replay) default mode
    "CurrentStateFinding",
    "CurrentStateUnavailable",
    "CurrentStateSkipped",
    "CurrentStateScanResult",
    "CurrentStateReport",
    "BodyFor",
    "scan_one_statute_current_state",
    "scan_current_state",
    # Statute-lifecycle (registry/oracle-driven, no-replay) layer
    "LifecycleCache",
    "oracle_lifecycle_lookup",
    "lifecycle_lookup_from_artifact",
    "default_lifecycle_lookup",
]


# ---------------------------------------------------------------------------
# Process-local point-in-time materialization cache
# ---------------------------------------------------------------------------
#
# WHY this exists
# ---------------
# The broken-refs scan resolves dozens of cross-statute citations per citing
# statute, and every distinct TARGET provision triggers a full ``legal_pit``
# replay of the target statute *as of* a date. The same target statute is cited
# over and over — both within one citing statute and across the corpus — and the
# present-tree check (``as_of = today``) repeats the *identical* materialization
# for every reference into a given target. Without memoization each of those is a
# fresh heavy replay; with it, each (target_statute_id, as_of) tree is replayed
# at most ONCE per worker.
#
# WHERE it lives (process-local, not cross-process)
# -------------------------------------------------
# The runner (tools/bitemporal_refs.py) uses a ``ProcessPoolExecutor``: workers
# are independent OS processes with NO shared memory. A cross-process cache would
# need IPC/serialization of large IRNode trees — not worth it. So the cache is a
# MODULE-LEVEL singleton (``_PROCESS_PIT_CACHE``): each worker process gets its
# own, populated lazily as that worker chews through its chunk of statute ids.
# ``legal_pit_tree_as_of`` is rebuilt per statute by the runner, but it shares
# this one process-global cache, so reuse survives across statutes within a
# worker. (Within a single in-process ``scan_broken_references`` call the same
# global is shared across all statutes, too.)
#
# Memory / latency tradeoff (be honest)
# -------------------------------------
# A materialized IRNode tree can be large (a fully-amended code is a deep tree).
# An unbounded cache would, on a full-corpus run, retain one tree per distinct
# (statute, as_of) and could blow the WSL2 memory ceiling. So the cache is a
# bounded LRU (``OrderedDict``, default cap 512 entries): on overflow the
# least-recently-used entry is evicted. The cap trades worst-case memory for a
# small chance of re-replaying an evicted target later. With low cardinality of
# distinct as_of dates (today + a handful of citation-year anchors) the working
# set per worker chunk is typically well under the cap, so eviction is rare in
# practice. Misses (``None`` = "could not materialize") ARE cached too — a target
# that fails to replay is expensive to retry, so we remember the failure and
# never re-replay it (it still surfaces as ``BrokenCheckUnavailable`` every time,
# identically — caching the miss does not change WHICH findings are produced).
#
# Determinism: the cache only avoids recomputation. For a fixed underlying
# ``legal_pit`` replay (which is itself deterministic per (statute, as_of)), the
# cached value equals the value that would have been recomputed, so the scan
# produces byte-identical findings with or without the cache.

_PIT_CACHE_DEFAULT_CAP = 512


class PitCache:
    """Bounded LRU cache over (target_statute_id, as_of_iso) -> Optional[IRNode].

    Caches BOTH successful materializations (an ``IRNode``) and failures
    (``None`` — "could not materialize"), so a target is replayed at most once
    per distinct as-of within the cache's lifetime/cap. Not thread-safe by
    design: the scan is process-parallel (one cache per worker process), never
    thread-parallel within a process.

    Tracks ``hits`` / ``misses`` (cache hit vs. cache miss — a cache miss is a
    fresh underlying materialization) and ``evictions`` for measurement.
    """

    __slots__ = ("_store", "_cap", "hits", "misses", "evictions")

    def __init__(self, cap: int = _PIT_CACHE_DEFAULT_CAP) -> None:
        # Value is a 1-tuple wrapper so a cached ``None`` (failed materialization)
        # is distinguishable from "key absent". OrderedDict gives O(1) LRU.
        self._store: OrderedDict[tuple[str, str], tuple[Optional[IRNode]]] = (
            OrderedDict()
        )
        self._cap = cap if cap > 0 else _PIT_CACHE_DEFAULT_CAP
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get_or_compute(
        self,
        statute_id: str,
        on: date,
        compute: Callable[[str, date], Optional[IRNode]],
    ) -> Optional[IRNode]:
        key = (statute_id, on.isoformat())
        cached = self._store.get(key)
        if cached is not None:
            self.hits += 1
            self._store.move_to_end(key)  # mark most-recently-used
            return cached[0]
        # Cache miss: materialize once, then memoize (hit or miss alike).
        self.misses += 1
        value = compute(statute_id, on)
        self._store[key] = (value,)
        if len(self._store) > self._cap:
            self._store.popitem(last=False)  # evict least-recently-used
            self.evictions += 1
        return value

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "lookups": total,
            "size": len(self._store),
            "hit_rate": (self.hits / total) if total else 0.0,
        }

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0
        self.evictions = 0


# The process-local singleton: one per worker process (ProcessPoolExecutor) or
# one for an in-process scan. Lazily reused so reuse survives across statutes.
_PROCESS_PIT_CACHE: Optional[PitCache] = None


def process_pit_cache() -> PitCache:
    """Return this process's shared PIT cache, creating it on first use."""
    global _PROCESS_PIT_CACHE
    if _PROCESS_PIT_CACHE is None:
        _PROCESS_PIT_CACHE = PitCache()
    return _PROCESS_PIT_CACHE


def cached_tree_as_of(inner: TreeAsOf, cache: Optional[PitCache] = None) -> TreeAsOf:
    """Wrap a ``TreeAsOf`` so each (statute_id, as_of) materializes at most once.

    ``cache`` defaults to the process-local singleton (so reuse survives across
    statutes within a worker). Pass an explicit ``PitCache`` in tests to assert
    hit/miss behavior without touching the process global.
    """
    pit = cache if cache is not None else process_pit_cache()

    def _cached(statute_id: str, on: date) -> Optional[IRNode]:
        return pit.get_or_compute(statute_id, on, inner)

    return _cached


# ---------------------------------------------------------------------------
# legal_pit-backed as-of materializer (the wired seam)
# ---------------------------------------------------------------------------
#
# broken_detection.default_tree_as_of is a documented SEAM: it reaches for
# ``materialized_state.tree`` / ``.body`` / ``.root``, but the real
# point-in-time IRNode lives on ``materialized_state.ir`` (``.tree`` is a
# last-resort lxml parse of the ORIGINAL base XML, not the amended state). We
# must not edit broken_detection.py, so the integration layer supplies its own
# correctly-wired ``TreeAsOf`` here. It still fails loud (returns None) on any
# materialization failure, so brokenness stays undetermined (-> Unavailable),
# never a false BROKEN.


def legal_pit_tree_as_of(
    store: "CorpusStore", *, cache: Optional[PitCache] = None
) -> TreeAsOf:
    """Build a ``TreeAsOf`` over the real ``legal_pit`` point-in-time replay.

    Returns the amended IR tree (``ReplayState.ir``) of one statute as of a
    date, or ``None`` (fail-loud) on any failure. This is the working
    counterpart to ``broken_detection.default_tree_as_of`` (which reaches for
    the wrong accessor and is left untouched per the no-edit boundary).

    The heavy replay is wrapped in the process-local ``PitCache`` so each
    (statute_id, as_of) tree materializes at most once per worker (see the
    PitCache docstring for the memory/latency tradeoff). Pass an explicit
    ``cache`` to scope memoization (tests / isolated runs); ``None`` uses the
    process-global singleton so reuse survives across statutes.
    """

    def _replay(statute_id: str, on: date) -> Optional[IRNode]:
        try:
            from lawvm.finland.replay_entrypoint import replay_xml
            from lawvm.finland.replay_request import ReplayXmlRequest

            request = ReplayXmlRequest(
                parent_id=statute_id,
                mode="legal_pit",
                as_of=on.isoformat(),
                corpus=store,
                quiet=True,
            )
            result = replay_xml(request=request)
        except Exception:
            return None
        state = getattr(getattr(result, "products", None), "materialized_state", None)
        ir = getattr(state, "ir", None)
        return ir if isinstance(ir, IRNode) else None

    return cached_tree_as_of(_replay, cache=cache)


# ---------------------------------------------------------------------------
# Citation-time anchor
# ---------------------------------------------------------------------------


def citation_anchor_for_statute(citing_statute_id: str) -> Optional[date]:
    """Coarse lower-bound citation date for a citing statute.

    A citation cannot have been written before the citing statute existed, so we
    anchor to 1 January of the statute's enactment year. Finnish statute ids are
    ``YEAR/NUMBER`` (e.g. ``1991/1725`` = the 1725th act of 1991), so the year is
    the FIRST segment — NOT the last (the trailing number is an in-year ordinal
    that can itself look like a year, e.g. ``1991/1725``). Returns ``None`` when
    the leading segment is not a plausible year (then the lifecycle / replay
    checks run without a temporal anchor for that statute — fail-soft into the
    unverifiable bucket, never a fabricated date).
    """
    head = citing_statute_id.split("/", 1)[0]
    if not head.isdigit():
        return None
    year = int(head)
    if year < 1700 or year > 2100:
        return None
    return date(year, 1, 1)


def _anchor_mentions(
    mentions: list["ReferenceMention"],
    anchor: Optional[date],
) -> list["ReferenceMention"]:
    """Stamp a citation-start anchor onto mentions whose interval start is open.

    Only fills an ABSENT start; a concrete ``valid_at`` start the extractor
    already carries is left untouched. Returns new mentions (frozen dataclass).
    """
    if anchor is None:
        return mentions
    from dataclasses import replace

    out: list["ReferenceMention"] = []
    for m in mentions:
        start, end = m.valid_at_interval
        if start is None:
            out.append(replace(m, valid_at_interval=(anchor, end)))
        else:
            out.append(m)
    return out


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteScanResult:
    """Per-statute scan outcome (always returned — errors are recorded, not dropped).

    Attributes:
        sid: The citing statute id scanned.
        mentions_checked: How many resolved cross-statute mentions were handed
            to ``detect_broken`` for this statute.
        findings: Confirmed dangling-citation findings for this statute.
        unavailable: References whose brokenness could not be established
            (target tree could not be materialized) — fail-loud, not broken.
        error: Set when the citing statute's own body scan / extraction raised.
            The statute is then counted in the errored bucket, never silently
            skipped.
    """

    sid: str
    mentions_checked: int
    findings: tuple[BrokenReferenceFinding, ...]
    unavailable: tuple[BrokenCheckUnavailable, ...]
    error: Optional[str] = None


@dataclass
class BrokenRefReport:
    """Corpus-wide aggregate of the bitemporal broken-reference scan.

    Attributes:
        statutes_scanned: How many citing statutes were scanned.
        statutes_with_findings: How many scanned statutes had ≥1 finding.
        statutes_errored: ids whose own scan raised (with the error text).
        mentions_checked: Total resolved cross-statute mentions handed to the
            detector across the corpus.
        reason_counts: Findings tallied by ``BrokenReason`` value
            (repealed_since / renumbered_since / never_existed).
        unavailable_count: Total ``BrokenCheckUnavailable`` records (fail-loud
            undetermined checks).
        unavailable_by_kind: ``BrokenCheckUnavailable`` tallied by which
            materialization failed ("cited" / "current").
        per_statute: All ``StatuteScanResult`` rows (one per scanned statute).
    """

    statutes_scanned: int = 0
    statutes_with_findings: int = 0
    statutes_errored: list[tuple[str, str]] = field(default_factory=list)
    mentions_checked: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)
    unavailable_count: int = 0
    unavailable_by_kind: dict[str, int] = field(default_factory=dict)
    per_statute: list[StatuteScanResult] = field(default_factory=list)

    @property
    def total_findings(self) -> int:
        return sum(self.reason_counts.values())

    def top_statutes(self, n: int) -> list[StatuteScanResult]:
        """Statutes ranked by finding count (errored statutes excluded)."""
        scored = [r for r in self.per_statute if r.error is None and r.findings]
        return sorted(scored, key=lambda r: -len(r.findings))[:n]


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def _resolved_body(store: "CorpusStore", sid: str) -> Optional[bytes]:
    """Best available body XML for reference extraction (archive-only, no replay).

    Delegates to :func:`read_reference_body`: prefer the consolidated oracle (the
    same text the fi_refs projection scans), but fall back to the enacted source
    or amendment act when the oracle is absent OR a ``contentAbsent`` stub, so
    repealed/expired statutes still contribute their mentions.
    """
    from lawvm.finland.legal_surface.body_source import read_reference_body

    return read_reference_body(store, sid)


def scan_one_statute(
    sid: str,
    store: "CorpusStore",
    *,
    tree_as_of: TreeAsOf,
    provision_present: ProvisionPresent,
    current_as_of: Optional[date] = None,
) -> StatuteScanResult:
    """Scan one citing statute for dangling cross-statute citations.

    Reads the body, extracts mentions, anchors their citation start to the
    statute's enactment year (when open), and runs ``detect_broken`` with the
    injected materialization adapters. Always returns a ``StatuteScanResult`` —
    a body/extraction failure is recorded in ``error``, never raised away.
    """
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions

    try:
        xb = _resolved_body(store, sid)
    except Exception as exc:  # noqa: BLE001 — fail-loud into the errored bucket
        return StatuteScanResult(
            sid=sid,
            mentions_checked=0,
            findings=(),
            unavailable=(),
            error=f"read_body: {exc!r}",
        )
    if not xb:
        return StatuteScanResult(
            sid=sid, mentions_checked=0, findings=(), unavailable=()
        )

    try:
        extraction = extract_all_reference_mentions(xb, sid)
    except Exception as exc:  # noqa: BLE001 — fail-loud into the errored bucket
        return StatuteScanResult(
            sid=sid,
            mentions_checked=0,
            findings=(),
            unavailable=(),
            error=f"extract: {exc!r}",
        )

    anchor = citation_anchor_for_statute(sid)
    mentions = _anchor_mentions(list(extraction.mentions), anchor)

    # detect_broken itself filters to resolved cross-statute targets; we count
    # how many it will actually inspect for an honest "mentions_checked".
    checked = sum(1 for m in mentions if _will_check(m))

    results = detect_broken(
        mentions,
        tree_as_of=tree_as_of,
        provision_present=provision_present,
        current_as_of=current_as_of,
    )

    findings = tuple(r for r in results if isinstance(r, BrokenReferenceFinding))
    unavailable = tuple(r for r in results if isinstance(r, BrokenCheckUnavailable))
    return StatuteScanResult(
        sid=sid,
        mentions_checked=checked,
        findings=findings,
        unavailable=unavailable,
    )


def _will_check(mention: "ReferenceMention") -> bool:
    """Exact mirror of detect_broken's own gate.

    Counts a mention iff detect_broken will inspect it: a resolved target
    (confidence EXACT/APPROXIMATE/STATUTE_ONLY) with a truthy target statute
    identity. This deliberately does NOT exclude self-references or EU targets —
    the detector does not either, so ``mentions_checked`` matches the count of
    references actually run through materialization (some of which yield
    ``BrokenCheckUnavailable``, e.g. EU targets with no FI legal_pit tree).
    """
    from lawvm.core.reference_mention import CiteConfidence

    tgt = mention.target_provision_ref
    if tgt is None or not tgt.statute_id:
        return False
    return mention.cite_confidence not in (
        CiteConfidence.UNRESOLVED,
        CiteConfidence.OPEN,
        CiteConfidence.AMBIGUOUS,
        CiteConfidence.BROKEN,
    )


def scan_broken_references(
    statute_ids: list[str],
    store: "CorpusStore",
    *,
    tree_as_of: Optional[TreeAsOf] = None,
    provision_present: Optional[ProvisionPresent] = None,
    current_as_of: Optional[date] = None,
) -> BrokenRefReport:
    """Corpus bitemporal broken-reference scan over ``statute_ids``.

    For each citing statute, extracts resolved cross-statute mentions and runs
    the pure bitemporal detector with ``legal_pit``-backed materialization
    (default) of the TARGET statute trees. Aggregates findings by reason and
    counts ``BrokenCheckUnavailable`` separately (fail-loud — undetermined,
    never broken).

    Args:
        statute_ids: Citing statutes to scan (the report iterates them in order).
        store: Corpus store the body reads AND the default replay materializer
            read from.
        tree_as_of / provision_present: Injected materialization adapters. When
            omitted, the real ``legal_pit``-backed materializer
            (``legal_pit_tree_as_of``) + ``default_provision_present`` over
            ``store`` are used (heavy point-in-time replay of each target
            statute). Tests inject synthetic adapters and never touch real
            replay.
        current_as_of: Date taken as "now" for the still-present check; defaults
            to ``date.today()`` inside the detector.

    Returns:
        A ``BrokenRefReport``. Mentions are never mutated in place; statutes are
        never silently dropped.
    """
    if tree_as_of is None:
        tree_as_of = legal_pit_tree_as_of(store)
    if provision_present is None:
        provision_present = default_provision_present

    report = BrokenRefReport()
    reason_ct: collections.Counter[str] = collections.Counter()
    unavailable_kind_ct: collections.Counter[str] = collections.Counter()

    for sid in statute_ids:
        result = scan_one_statute(
            sid,
            store,
            tree_as_of=tree_as_of,
            provision_present=provision_present,
            current_as_of=current_as_of,
        )
        report.per_statute.append(result)
        report.statutes_scanned += 1
        if result.error is not None:
            report.statutes_errored.append((result.sid, result.error))
            continue
        report.mentions_checked += result.mentions_checked
        if result.findings:
            report.statutes_with_findings += 1
        for finding in result.findings:
            reason_ct[finding.reason.value] += 1
        for unavail in result.unavailable:
            unavailable_kind_ct[unavail.unavailable_for] += 1
            report.unavailable_count += 1

    # Stable reason ordering (closed enum) so the report is deterministic.
    report.reason_counts = {
        r.value: reason_ct.get(r.value, 0)
        for r in BrokenReason
        if reason_ct.get(r.value, 0)
    }
    report.unavailable_by_kind = dict(sorted(unavailable_kind_ct.items()))
    return report


# ===========================================================================
# Statute-lifecycle layer (registry/oracle-driven, NO replay)
# ===========================================================================
#
# The cheap statute-level bitemporal signal: each target act's in-force window
# (valid_from = enactment, valid_to = the in-corpus oracle repeal/supersession
# date). Read straight from the corpus XML (source for valid_from, consolidated
# oracle's ``finlex:repealedBy`` for valid_to) — the exact extraction the
# statute-name registry uses — with NO point-in-time replay. A target id is read
# at most once per process via ``LifecycleCache``.
#
# This answers BROKEN kind #1 ("target statute repealed-since / not-yet-in-force"
# at the citing date) corpus-wide for free, complementing the provision-level
# checks. Unknown lifecycle (no XML / unparseable) → ``known=False`` → the
# detector emits ``StatuteLifecycleUnverifiable``, never a false BROKEN.


class LifecycleCache:
    """Process-local memo of ``statute_id -> StatuteLifecycle``.

    Each target act's lifecycle window is read from the corpus once and reused
    across every citation into it (and across citing statutes within a worker /
    in-process scan). Unbounded but tiny: one small dataclass per distinct cited
    act (a few thousand at most), no large IR trees retained.
    """

    __slots__ = ("_store", "_cache", "hits", "misses")

    def __init__(self, store: "CorpusStore") -> None:
        self._store = store
        self._cache: dict[str, StatuteLifecycle] = {}
        self.hits = 0
        self.misses = 0

    def get(self, statute_id: str) -> StatuteLifecycle:
        cached = self._cache.get(statute_id)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        lifecycle = self._read(statute_id)
        self._cache[statute_id] = lifecycle
        return lifecycle

    def _read(self, statute_id: str) -> StatuteLifecycle:
        """Read one act's lifecycle window from the corpus (fail-loud on unknown).

        ``valid_from`` from the source/amendment XML's ``dateIssued``; ``valid_to``
        from the consolidated oracle's ``finlex:repealedBy`` block (the same
        extraction the registry uses). When NEITHER source nor oracle is present
        the act has no lifecycle on record → ``known=False`` (→ unverifiable). An
        act with a body but no repeal date keeps an OPEN ``valid_to`` (= in force),
        which is correct and NOT "unknown".
        """
        from lawvm.finland.references.registries.statute_name import (
            _extract_repeal_date,
            _extract_title_and_date,
        )

        valid_from: Optional[date] = None
        valid_to: Optional[date] = None
        saw_any = False

        try:
            src = self._store.read_source(statute_id) or self._store.read_amendment(
                statute_id
            )
        except Exception:
            src = None
        if src:
            saw_any = True
            extracted = _extract_title_and_date(src)
            if extracted is not None:
                _title, valid_from = extracted

        try:
            oracle = self._store.read_oracle(statute_id)
        except Exception:
            oracle = None
        if oracle:
            saw_any = True
            valid_to = _extract_repeal_date(oracle)

        if not saw_any:
            return StatuteLifecycle(valid_from=None, valid_to=None, known=False)
        return StatuteLifecycle(valid_from=valid_from, valid_to=valid_to, known=True)


def oracle_lifecycle_lookup(
    store: "CorpusStore", *, cache: Optional[LifecycleCache] = None
) -> LifecycleLookup:
    """Build a ``LifecycleLookup`` reading each act's in-force window from the corpus.

    Archive-only (no replay): ``valid_from`` from the source XML, ``valid_to``
    from the consolidated oracle's repeal block. Memoized via ``LifecycleCache``
    (pass an explicit cache in tests; ``None`` makes a fresh per-call cache).
    Returns ``StatuteLifecycle(known=False)`` for an act with no corpus XML at all
    (→ ``StatuteLifecycleUnverifiable``), never a guessed window.
    """
    lc = cache if cache is not None else LifecycleCache(store)
    return lc.get


def lifecycle_lookup_from_artifact(
    artifact_path: str | Path,
    *,
    fallback: Optional[LifecycleLookup] = None,
) -> LifecycleLookup:
    """Build a ``LifecycleLookup`` from the pre-built statute-name registry artifact.

    The whole-corpus ``statute_name_registry.jsonl`` already carries every act's
    ``(valid_from, valid_to)`` window — extracted by the SAME functions
    (``_extract_title_and_date`` / ``_extract_repeal_date``) that
    ``LifecycleCache._read`` calls per act. So consulting the artifact yields the
    BYTE-IDENTICAL lifecycle that the per-act oracle path would compute for those
    ids, but with ZERO per-act XML reads: the artifact is loaded once into an
    in-memory ``id -> StatuteLifecycle`` table and every lookup is a dict hit. On
    a full-corpus scan this removes the dominant cost of the lifecycle layer (one
    source + one oracle read per distinct cited act).

    COMPLETENESS (the no-silent-loss requirement). The artifact is built by
    enumerating ``list_statute_ids()`` (acts that have a source/title), so it does
    NOT carry orphan ids that exist only as a consolidated oracle (a repealed act
    whose enacted source is not in the corpus but whose oracle still publishes a
    ``repealedBy`` block — e.g. ``1964/591``). The per-act oracle path reaches
    those by reading the oracle directly by id, so dropping it would silently lose
    genuine repealed-target findings. To stay a FULL täyslaskenta, an id ABSENT
    from the artifact is delegated to ``fallback`` (the per-act oracle lookup) when
    one is given; the fallback's own cache makes the long tail of absent ids cheap.
    With no fallback, an absent id is ``known=False`` (unverifiable) — never a
    false BROKEN and never silently judged in force.

    Fail-loud discipline (preserved EXACTLY). An act PRESENT in the artifact with
    an open ``valid_to`` is "in force" (no recorded supersession), exactly as the
    oracle path treats a missing ``repealedBy`` block. The artifact is a pure
    function of the corpus, so a finding produced over it is the same finding the
    oracle path would produce.
    """
    from lawvm.finland.references.registries.statute_name import (
        load_statute_name_entries,
    )

    table: dict[str, StatuteLifecycle] = {}
    for entry in load_statute_name_entries(artifact_path):
        # First-write-wins so a stable, deterministic window is bound per id even
        # in the (unexpected) event the artifact carries a duplicate id row.
        table.setdefault(
            entry.statute_id,
            StatuteLifecycle(
                valid_from=entry.valid_from,
                valid_to=entry.valid_to,
                known=True,
            ),
        )

    _UNKNOWN = StatuteLifecycle(valid_from=None, valid_to=None, known=False)

    def _lookup(statute_id: str) -> StatuteLifecycle:
        cached = table.get(statute_id)
        if cached is not None:
            return cached
        # Absent from the artifact: an orphan-oracle id (no source/title) may still
        # have an in-corpus repeal date. Delegate to the oracle fallback so the
        # ledger stays complete; fail-loud unverifiable when no fallback is wired.
        if fallback is not None:
            return fallback(statute_id)
        return _UNKNOWN

    return _lookup


def default_lifecycle_lookup(store: "CorpusStore") -> LifecycleLookup:
    """The lifecycle lookup the corpus scan should use: artifact + oracle fallback.

    Prefers the pre-built whole-corpus registry artifact
    (``statute_name.default_artifact_path``) — a one-shot in-memory load that
    serves every titled act's window with no per-act XML read (the full-corpus
    perf path) — and falls back to the per-act oracle lookup
    (``oracle_lifecycle_lookup``, memoized) for ids the artifact does not carry
    (orphan-oracle repealed acts), so the ledger loses NO genuine finding. When the
    artifact is absent entirely (fresh checkout) it uses the oracle path alone, so
    behavior is unchanged. Both paths read the SAME windows via the SAME
    extraction; only the access cost differs, so findings are identical.
    """
    from lawvm.finland.references.registries.statute_name import (
        default_artifact_path,
    )

    oracle = oracle_lifecycle_lookup(store)
    artifact = default_artifact_path()
    if artifact.exists():
        return lifecycle_lookup_from_artifact(artifact, fallback=oracle)
    return oracle


# ===========================================================================
# DEFAULT MODE: current-state detection (no replay)
# ===========================================================================
#
# WHY a second, cheaper mode is the DEFAULT
# -----------------------------------------
# The replay path above answers the temporal-provenance question ("did the
# target exist WHEN cited; was it repealed vs renumbered SINCE?"). Answering it
# requires a full point-in-time ``legal_pit`` replay of every target statute as
# of two dates — heavy, and the reason ``broken-refs`` was slow enough to need
# ``--limit`` sampling.
#
# But the *default* question a reader of the statute book actually has is much
# cheaper: "does the cited target provision EXIST in the target's CURRENT
# consolidated text-state?" The Finlex oracle already gives us each statute's
# current consolidated body for free (no replay). So the default scan is a
# structural presence check against that current body — exactly the access +
# parse + presence machinery the corpus type-mismatch lint already owns.
#
# REUSE, do not duplicate (no third tree walker)
# ----------------------------------------------
# ``legal_surface.corpus_lints`` already (a) reads the target's CURRENT body
# (``_read_body`` — oracle-preferred, archive-only), (b) parses it into a
# deterministic ``{section_label: _SectionStructure}`` map (``_parse_target_sections``),
# and (c) decides momentti/kohta absence + structural-type mismatch from both
# sides (``_check_citation``). We IMPORT and reuse all three here rather than
# writing a third walker. The ONLY thing corpus_lints deliberately does not do
# is treat a MISSING SECTION as a finding (it scopes a missing section out as a
# renumber-over-time concern owned by broken_detection). For the current-state
# "is the cited target present NOW?" question, a missing section IS the cheap
# analog of "broken", so this mode adds that one decision on top of the reused
# structure parser + deeper-level check.
#
# SURFACE-FACT DISCIPLINE (preserved)
# -----------------------------------
# A finding here is strictly "the cited provision is absent in the target's
# current consolidated text-state" (or a structural-type disagreement) — never a
# legal conclusion. A target whose current body is unavailable / unparseable is
# reported as ``CurrentStateUnavailable`` (fail-loud), NEVER called absent.


# Injected access to a statute's CURRENT consolidated body bytes (archive-only,
# no replay). Defaults to ``corpus_lints._read_body`` over the store; tests
# inject a fake so the default mode is testable without a corpus.
BodyFor = Callable[[str], Optional[bytes]]
"""``(statute_id) -> current consolidated body XML, or None if unavailable.``"""

# Injected SCOPE predicate: does this citer have a real consolidated text-state
# the broken-refs product can meaningfully scope its check to? Defaults to
# ``body_source.has_consolidated_text_state`` over the store; tests inject a fake.
InScope = Callable[[str], bool]
"""``(statute_id) -> whether the citer has a consolidated (in-force) text-state.``"""


@dataclass(frozen=True, slots=True)
class CurrentStateFinding:
    """A resolved citation whose target provision is absent in the CURRENT text-state.

    The cheap, no-replay analog of a ``BrokenReferenceFinding``: established
    purely from the target's current consolidated body, with no point-in-time
    materialization and therefore NO temporal classification (repealed vs
    renumbered). It only says the cited address is not present (or its claimed
    structural type disagrees) in the target's text-state as it stands now.

    Attributes:
        source: The citing provision.
        target: The resolved target provision found absent / type-mismatched.
        kind: The corpus-lint surface-fact kind
            (``reference.target_provision_absent`` /
            ``reference.structural_type_mismatch``).
        message: Self-evidencing diagnostic (embeds the cited path + the
            target's actual shape), reused verbatim from the corpus lint.
        rule_id: Stable rule identifier.
    """

    source: ProvisionRef
    target: ProvisionRef
    kind: str
    message: str
    rule_id: str = "fi.refs.current_state.absent"


@dataclass(frozen=True, slots=True)
class CurrentStateUnavailable:
    """The current-state presence of a target could not be established (fail-loud).

    Emitted instead of a (false) absence finding when the target's current body
    is unavailable or cannot be parsed deterministically. Brokenness stays
    *undetermined* for that reference — never called absent.

    Attributes:
        source: The citing provision.
        target: The resolved target provision we could not check.
        reason: Human-readable diagnostic.
        rule_id: Stable rule identifier.
    """

    source: ProvisionRef
    target: ProvisionRef
    reason: str
    rule_id: str = "fi.refs.current_state.unavailable"


@dataclass(frozen=True, slots=True)
class CurrentStateSkipped:
    """A citing statute skipped because it has no consolidated text-state to scope to.

    The broken-refs product checks citations *in the law as it stands* — against
    a consolidated/in-force text-state where an internal "N §" self-reference is
    meaningful. A citer with no consolidated oracle (only an enacted-source /
    amendment-act payload) is OUT OF SCOPE: its internal refs are
    amended-law-relative, so checking them against its own body manufactured the
    characterized false-positive class (amendment-act self-refs). Such citers are
    SKIPPED explicitly (this record, surfaced as a count) — never silently
    dropped, never checked.

    Attributes:
        sid: The skipped citing statute id.
        reason: Why it is out of scope (no consolidated text-state).
        rule_id: Stable rule identifier.
    """

    sid: str
    reason: str
    rule_id: str = "fi.refs.current_state.out_of_scope"


@dataclass(frozen=True)
class CurrentStateScanResult:
    """Per-statute current-state scan outcome (always returned — errors recorded).

    Mirrors ``StatuteScanResult`` for the no-replay default mode.

    ``skipped`` is set (and the statute is NOT checked) when the citer has no
    consolidated text-state to scope the check to (amendment-act / source-only
    body) — fail-loud out-of-scope, surfaced in the report's ``skipped_count``,
    never silently dropped.
    """

    sid: str
    mentions_checked: int
    findings: tuple[CurrentStateFinding, ...]
    unavailable: tuple[CurrentStateUnavailable, ...]
    error: Optional[str] = None
    skipped: Optional[CurrentStateSkipped] = None
    self_refs_excluded: int = 0
    # Statute-lifecycle layer (registry/oracle-driven, no replay): the cited ACT
    # itself was repealed / not-yet-in-force at the citing date. Orthogonal to the
    # provision-presence findings above (a finding here can co-occur with one
    # there). ``lifecycle_unverifiable`` is the fail-loud bucket (unknown
    # lifecycle / no citing anchor) — never counted as broken.
    lifecycle_findings: tuple[StatuteLifecycleFinding, ...] = ()
    lifecycle_unverifiable: tuple[StatuteLifecycleUnverifiable, ...] = ()


@dataclass
class CurrentStateReport:
    """Corpus-wide aggregate of the no-replay current-state scan.

    Attributes:
        statutes_scanned: How many citing statutes were scanned.
        statutes_with_findings: How many scanned statutes had ≥1 finding.
        statutes_errored: ids whose own scan raised (with the error text).
        mentions_checked: Total resolved cross-statute mentions inspected.
        kind_counts: Findings tallied by surface-fact ``kind``.
        unavailable_count: Total ``CurrentStateUnavailable`` records (fail-loud).
        skipped_count: Citers skipped as out-of-scope (no consolidated
            text-state — amendment-act / source-only body). Fail-loud, surfaced,
            never silently dropped.
        self_refs_excluded: Internal self-references excluded from the check (the
            product scopes to CROSS-statute citations; an internal "N §" self-ref
            checked against the citer's own parsed body is a parser-quirk-prone
            second false-positive class). Surfaced, never silently dropped.
        per_statute: All ``CurrentStateScanResult`` rows.
    """

    statutes_scanned: int = 0
    statutes_with_findings: int = 0
    statutes_errored: list[tuple[str, str]] = field(default_factory=list)
    mentions_checked: int = 0
    kind_counts: dict[str, int] = field(default_factory=dict)
    unavailable_count: int = 0
    skipped_count: int = 0
    self_refs_excluded: int = 0
    # Statute-lifecycle layer (no replay): the cited ACT was repealed /
    # not-yet-in-force at the citing date. Tallied by ``BrokenReason`` value
    # (target_statute_repealed / target_statute_not_yet_in_force).
    lifecycle_reason_counts: dict[str, int] = field(default_factory=dict)
    lifecycle_unverifiable_count: int = 0
    per_statute: list[CurrentStateScanResult] = field(default_factory=list)

    @property
    def total_findings(self) -> int:
        return sum(self.kind_counts.values())

    @property
    def total_lifecycle_findings(self) -> int:
        return sum(self.lifecycle_reason_counts.values())

    def top_statutes(self, n: int) -> list[CurrentStateScanResult]:
        """Statutes ranked by total finding count (errored statutes excluded).

        Counts BOTH provision-absence findings and statute-lifecycle findings so
        the worst-offenders worklist reflects the whole dangling-ref ledger.
        """
        scored = [
            r
            for r in self.per_statute
            if r.error is None and (r.findings or r.lifecycle_findings)
        ]
        return sorted(
            scored, key=lambda r: -(len(r.findings) + len(r.lifecycle_findings))
        )[:n]


def _provision_ref_to_address_path(ref: "ProvisionRef"):
    """Build a corpus_lints ``_AddressPath`` from a target ``ProvisionRef``.

    Returns ``None`` (skip — tag-don't-guess) when the ref carries no section
    label or carries a momentti/kohta ordinal this mode cannot resolve as a
    1-based integer (matching the lint's own ``_parse_address_tail`` discipline).
    The corpus lint parses an integer-shaped ``section/subsection/item`` tail; we
    construct the same shape directly from the typed ref instead of re-stringing
    it, so the deeper-level check is byte-for-byte the lint's check.
    """
    from lawvm.finland.legal_surface.corpus_lints import _AddressPath

    if not ref.section_label:
        return None
    subsection: Optional[int] = None
    item: Optional[int] = None
    depth = 1
    if ref.subsection_num is not None:
        if ref.subsection_num < 1:
            return None
        subsection = ref.subsection_num
        depth = 2
        if ref.item_label:
            # The lint resolves only integer-shaped kohta ordinals.
            if not ref.item_label.isdigit():
                return None
            item = int(ref.item_label)
            if item < 1:
                return None
            depth = 3
    return _AddressPath(
        section=ref.section_label,
        subsection=subsection,
        item=item,
        depth=depth,
    )


def _check_current_presence(ref: "ProvisionRef", sections):
    """Decide current-state presence of ``ref`` against parsed target sections.

    Reuses the corpus lint's deterministic structure: a missing SECTION is the
    cheap analog of "broken" (the lint scopes this out as a temporal concern; in
    current-state mode it IS the finding). For a present section, the deeper
    momentti/kohta absence + structural-type-mismatch decision is delegated
    verbatim to the lint's ``_check_citation`` (no duplicated logic).

    Returns ``(kind, message)`` on a provable absence/mismatch, else ``None``.
    ``None`` for an address tail this mode cannot resolve (tag-don't-guess).
    """
    from lawvm.finland.helpers import _normalize_source_section_num
    from lawvm.finland.legal_surface.corpus_lints import (
        KIND_ABSENT,
        _check_citation,
    )

    path = _provision_ref_to_address_path(ref)
    if path is None:
        return None

    sec_label = _normalize_source_section_num(path.section)
    if sec_label not in sections:
        # The cited SECTION is absent from the target's current text-state. This
        # is the no-replay analog of a broken reference; corpus_lints scopes a
        # missing section out (it owns only single-time type mismatches), so the
        # decision is made here.
        return (
            KIND_ABSENT,
            (
                f"cited section {sec_label} § is absent from the target's "
                f"current consolidated text-state"
            ),
        )

    # Section present — defer momentti/kohta absence + type-mismatch to the lint.
    finding = _check_citation(path, sections)
    if finding is None:
        return None
    return (finding.kind, finding.message)


def _default_in_scope(store: "CorpusStore") -> InScope:
    """Default citer-scope predicate: a real consolidated text-state over the store.

    Delegates to :func:`body_source.has_consolidated_text_state`. Archive-only,
    no replay. ``True`` only when the citer has its own non-``contentAbsent``
    consolidated oracle — so amendment-act / source-only citers are out of scope.
    """
    from lawvm.finland.legal_surface.body_source import has_consolidated_text_state

    def _scope(statute_id: str) -> bool:
        return has_consolidated_text_state(store, statute_id)  # type: ignore[arg-type]

    return _scope


def _default_body_for(store: "CorpusStore") -> BodyFor:
    """Default current-body accessor: ``corpus_lints._read_body`` over the store.

    Archive-only (oracle-preferred), no replay. Returns ``None`` when the body
    is unavailable so the caller reports it as ``CurrentStateUnavailable``
    (fail-loud), never as absent.
    """
    from lawvm.finland.legal_surface.corpus_lints import _read_body

    def _body(statute_id: str) -> Optional[bytes]:
        return _read_body(store, statute_id)  # type: ignore[arg-type]

    return _body


def scan_one_statute_current_state(
    sid: str,
    store: "CorpusStore",
    *,
    body_for: Optional[BodyFor] = None,
    in_scope: Optional[InScope] = None,
    lifecycle_of: Optional[LifecycleLookup] = None,
) -> CurrentStateScanResult:
    """Scan one citing statute for citations absent in the target's CURRENT text-state.

    The no-replay default counterpart to ``scan_one_statute``. Reads the citing
    body and extracts resolved cross-statute mentions exactly as the replay path
    does, then for each resolved target checks presence against the TARGET's
    current consolidated body (reused corpus_lints access + structure parse +
    presence check). NO point-in-time replay. Always returns a result — a
    body/extraction failure is recorded in ``error``, never raised away.

    CITER SCOPE (false-positive guard). The product checks citations *in the law
    as it stands*. A citer with no consolidated text-state (an amendment act /
    source-only body) is OUT OF SCOPE — its internal "N §" refs are
    amended-law-relative, not self-refs into its own structure, so checking them
    manufactured the characterized amendment-act self-ref false positives. Such a
    citer is SKIPPED (``CurrentStateScanResult.skipped`` set, surfaced as a
    count), never silently dropped and never checked. ``in_scope`` defaults to
    ``has_consolidated_text_state`` over the store; tests inject a fake.
    """
    from lawvm.finland.legal_surface.corpus_lints import _parse_target_sections
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions

    if body_for is None:
        body_for = _default_body_for(store)
    if in_scope is None:
        in_scope = _default_in_scope(store)
    if lifecycle_of is None:
        lifecycle_of = default_lifecycle_lookup(store)

    # CITER SCOPE GATE — fail-loud out-of-scope, never silently dropped.
    try:
        citer_in_scope = in_scope(sid)
    except Exception as exc:  # noqa: BLE001 — scope read failure → record, don't crash
        return CurrentStateScanResult(
            sid=sid,
            mentions_checked=0,
            findings=(),
            unavailable=(),
            error=f"scope: {exc!r}",
        )
    if not citer_in_scope:
        return CurrentStateScanResult(
            sid=sid,
            mentions_checked=0,
            findings=(),
            unavailable=(),
            skipped=CurrentStateSkipped(
                sid=sid,
                reason=(
                    "citer has no consolidated text-state (amendment-act / "
                    "source-only body); its internal references are "
                    "amended-law-relative, out of scope for the current-state "
                    "broken-reference check"
                ),
            ),
        )

    try:
        xb = _resolved_body(store, sid)
    except Exception as exc:  # noqa: BLE001 — fail-loud into the errored bucket
        return CurrentStateScanResult(
            sid=sid,
            mentions_checked=0,
            findings=(),
            unavailable=(),
            error=f"read_body: {exc!r}",
        )
    if not xb:
        return CurrentStateScanResult(
            sid=sid, mentions_checked=0, findings=(), unavailable=()
        )

    try:
        extraction = extract_all_reference_mentions(xb, sid)
    except Exception as exc:  # noqa: BLE001 — fail-loud into the errored bucket
        return CurrentStateScanResult(
            sid=sid,
            mentions_checked=0,
            findings=(),
            unavailable=(),
            error=f"extract: {exc!r}",
        )

    # STATUTE-LIFECYCLE LAYER (registry/oracle-driven, no replay): flag citations
    # to an ACT that is not in force when measured against the CURRENT text-state.
    #
    # ANCHOR = NOW, deliberately (not the citer's enactment year). The body we
    # extracted from is the CURRENT consolidated text-state: it accumulates every
    # amendment up to today, so a reference in it may have been INSERTED by a much
    # later amendment than the citer's original enactment. Anchoring to the
    # enactment year therefore manufactures a huge false-positive class
    # ("not_yet_in_force": an act citing a future act, because a later amendment
    # added that reference) — exactly the FP discipline we must preserve. The
    # honest question for a consolidated text is "does the law AS IT STANDS NOW
    # point at an act that is not in force NOW?": a live consolidated body still
    # citing an act repealed years ago is the genuine dangling-citation finding
    # (target_statute_repealed). The detector itself stays date-general; this
    # corpus driver pins the anchor to now so the only findings it can surface are
    # cheap, defensible ones (no enactment-year amendment-accretion artifacts).
    now = date.today()
    anchored = _anchor_mentions(list(extraction.mentions), now)
    lifecycle_results = detect_statute_lifecycle_broken(
        anchored, lifecycle_of=lifecycle_of
    )
    lifecycle_findings = tuple(
        r for r in lifecycle_results if isinstance(r, StatuteLifecycleFinding)
    )
    lifecycle_unverifiable = tuple(
        r for r in lifecycle_results if isinstance(r, StatuteLifecycleUnverifiable)
    )

    # Cache parsed target bodies per target statute within this citing statute
    # (sentinel distinguishes "unavailable/unparseable" from "not yet parsed").
    _UNPARSED = object()
    parsed: dict[str, object] = {}

    findings: list[CurrentStateFinding] = []
    unavailable: list[CurrentStateUnavailable] = []
    checked = 0
    self_refs_excluded = 0
    for mention in extraction.mentions:
        if not _will_check(mention):
            continue
        target = mention.target_provision_ref
        assert target is not None and target.statute_id  # guarded by _will_check
        source = mention.source_provision_ref
        target_statute = target.statute_id

        # SELF-REF SCOPE GATE (second false-positive guard). The product checks
        # CROSS-statute citations (its stated unit). An internal "N §" self-ref
        # is resolved to SELF and would be checked against the citer's OWN parsed
        # body, where deterministic-parse quirks (merged range-headings like
        # "3 a–4 §", subsection-level resolution) manufacture spurious absences.
        # Internal-ref integrity is a distinct lint, not this cross-statute
        # dangling-citation product. Excluded here and surfaced as a count —
        # never silently dropped.
        if target_statute == sid:
            self_refs_excluded += 1
            continue

        checked += 1

        if target_statute not in parsed:
            body = body_for(target_statute)
            parsed[target_statute] = (
                _parse_target_sections(body) if body is not None else _UNPARSED
            )
        sections = parsed[target_statute]
        if sections is _UNPARSED or sections is None:
            unavailable.append(
                CurrentStateUnavailable(
                    source=source,
                    target=target,
                    reason=(
                        f"current consolidated body for target statute "
                        f"{target_statute!r} is unavailable or could not be "
                        "parsed deterministically; presence undetermined"
                    ),
                )
            )
            continue

        hit = _check_current_presence(target, sections)  # type: ignore[arg-type]
        if hit is None:
            continue
        kind, message = hit
        findings.append(
            CurrentStateFinding(
                source=source,
                target=target,
                kind=kind,
                message=(
                    f"{message}. Cited target {target.serialized()}"
                ),
            )
        )

    return CurrentStateScanResult(
        sid=sid,
        mentions_checked=checked,
        findings=tuple(findings),
        unavailable=tuple(unavailable),
        self_refs_excluded=self_refs_excluded,
        lifecycle_findings=lifecycle_findings,
        lifecycle_unverifiable=lifecycle_unverifiable,
    )


def scan_current_state(
    statute_ids: list[str],
    store: "CorpusStore",
    *,
    body_for: Optional[BodyFor] = None,
    in_scope: Optional[InScope] = None,
    lifecycle_of: Optional[LifecycleLookup] = None,
) -> CurrentStateReport:
    """Corpus current-state (no-replay) broken-reference scan over ``statute_ids``.

    The DEFAULT mode: for each citing statute, extract resolved cross-statute
    mentions and check (a) whether each cited target provision is present in the
    TARGET's current consolidated text-state and (b) whether the cited ACT itself
    was repealed / not-yet-in-force at the citing date (the statute-lifecycle
    layer, registry/oracle-driven). No point-in-time replay — cheap enough to run
    corpus-wide. Aggregates provision findings by surface-fact ``kind`` and
    lifecycle findings by ``BrokenReason``; counts the fail-loud undetermined
    buckets separately (never broken).

    Citers with no consolidated text-state (amendment-act / source-only bodies)
    are SKIPPED as out-of-scope and surfaced in ``skipped_count`` (the
    characterized false-positive guard) — never silently dropped. ``in_scope``
    defaults to a real-consolidated-text-state predicate over the store;
    ``lifecycle_of`` defaults to a corpus-XML lifecycle lookup shared across all
    citers (one read per distinct cited act).
    """
    from lawvm.finland.references.broken_detection import BrokenReason

    report = CurrentStateReport()
    kind_ct: collections.Counter[str] = collections.Counter()
    lifecycle_ct: collections.Counter[str] = collections.Counter()

    # Build the default scope predicate once (one oracle read per citer) rather
    # than per-statute inside scan_one_statute_current_state. Likewise share ONE
    # lifecycle lookup so each distinct cited act's window is read at most once
    # across the whole scan (not once per citing statute).
    if in_scope is None:
        in_scope = _default_in_scope(store)
    if lifecycle_of is None:
        lifecycle_of = default_lifecycle_lookup(store)

    for sid in statute_ids:
        result = scan_one_statute_current_state(
            sid, store, body_for=body_for, in_scope=in_scope, lifecycle_of=lifecycle_of
        )
        report.per_statute.append(result)
        report.statutes_scanned += 1
        if result.error is not None:
            report.statutes_errored.append((result.sid, result.error))
            continue
        if result.skipped is not None:
            report.skipped_count += 1
            continue
        report.self_refs_excluded += result.self_refs_excluded
        report.mentions_checked += result.mentions_checked
        if result.findings or result.lifecycle_findings:
            report.statutes_with_findings += 1
        for finding in result.findings:
            kind_ct[finding.kind] += 1
        report.unavailable_count += len(result.unavailable)
        for lf in result.lifecycle_findings:
            lifecycle_ct[lf.reason.value] += 1
        report.lifecycle_unverifiable_count += len(result.lifecycle_unverifiable)

    report.kind_counts = dict(sorted(kind_ct.items()))
    # Stable lifecycle-reason ordering (closed enum) for a deterministic report.
    report.lifecycle_reason_counts = {
        r.value: lifecycle_ct.get(r.value, 0)
        for r in BrokenReason
        if lifecycle_ct.get(r.value, 0)
    }
    return report
