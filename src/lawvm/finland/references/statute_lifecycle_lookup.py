"""Registry-sourced ``LifecycleLookup`` + a runnable dangling-citation scan.

This module makes the CHEAP statute-lifecycle broken-reference detector
(``broken_detection.detect_statute_lifecycle_broken``) runnable end-to-end over a
corpus slice WITHOUT any heavy point-in-time (``legal_pit``) replay.

Why this module exists (the wiring gap it closes)
-------------------------------------------------
``broken_detection`` is a pure detector: it takes an injected
``LifecycleLookup = (statute_id) -> StatuteLifecycle`` and never reads a corpus.
The statute-name registry (``references.registries.statute_name``) already carries
every act's in-force window as a pure function of the corpus XML:

  * ``valid_from`` = the enacted source's ``FRBRWork`` ``dateIssued`` (enactment),
  * ``valid_to``  = the consolidated oracle's ``finlex:repealedBy / ... /
    dateEntryIntoForce[@date]`` (the date the repealing act entered into force —
    the in-corpus supersession date), or ``None`` (open = still in force).

Both are read by plain XML extraction (``_extract_title_and_date`` /
``_extract_repeal_date``) — NO amendment replay. So a ``LifecycleLookup`` sourced
from the registry answers the statute-level dangling question ("did the cited ACT
still exist at the citing date?") for free, complementing the provision-level
detector whose materializer (``default_tree_as_of``) IS the heavy replay seam.

What remains gated on #187 (provision granularity)
--------------------------------------------------
The provision-level detector (``detect_broken`` + ``default_tree_as_of``) still
needs ``legal_pit`` replay of the TARGET tree to tell whether a specific cited
*section/momentti* was repealed vs. renumbered vs. never existed. A whole-corpus
replay sweep DEADLOCKS (project task #187), so that granularity stays out of this
runnable path deliberately. This module answers only the cheaper ACT-level
question, which needs no replay. See ``broken_detection.default_tree_as_of``'s
SEAM NOTE for the provision path.

Fail-loud (AGENTS.md §1.1)
--------------------------
An act with no corpus XML at all → ``StatuteLifecycle(known=False)`` → the
detector emits ``StatuteLifecycleUnverifiable``, NEVER a false BROKEN. An act with
a body but an open ``valid_to`` is "in force" (no recorded supersession), also not
a finding. The scan never guesses a window and never silently drops a statute.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from lawvm.finland.references.broken_detection import (
    BrokenReason,
    LifecycleLookup,
    StatuteLifecycle,
    StatuteLifecycleFinding,
    StatuteLifecycleUnverifiable,
    detect_statute_lifecycle_broken,
)

if TYPE_CHECKING:
    from lawvm.corpus_store import CorpusStore

__all__ = [
    "LifecycleCache",
    "oracle_lifecycle_lookup",
    "registry_artifact_lifecycle_lookup",
    "default_lifecycle_lookup",
    "DanglingCitationReport",
    "scan_dangling_citations",
]


# ---------------------------------------------------------------------------
# Registry-sourced LifecycleLookup (NO replay)
# ---------------------------------------------------------------------------
#
# Two sources, both pure functions of the corpus XML and BYTE-IDENTICAL in the
# window they yield for a given id (they call the same extraction helpers):
#
#   * ``oracle_lifecycle_lookup`` — reads one act's source + oracle by id on
#     demand, memoized. Reaches every id the store can serve, including
#     "orphan-oracle" repealed acts that exist only as a consolidated oracle
#     (no enacted source) — e.g. an old act whose ``repealedBy`` block is the
#     only in-corpus trace of it.
#   * ``registry_artifact_lifecycle_lookup`` — a one-shot in-memory load of the
#     pre-built ``statute_name_registry.jsonl`` artifact (every titled act's
#     window), so each lookup is a dict hit with zero per-act XML reads. The
#     artifact is built by enumerating titled acts, so it does NOT carry
#     orphan-oracle ids; those are delegated to a ``fallback`` (the oracle
#     lookup) to keep the ledger complete.
#
# ``default_lifecycle_lookup`` prefers the artifact (fast) and falls back to the
# oracle path for absent ids, or uses the oracle path alone when no artifact
# exists (fresh checkout). All three read the SAME windows, so the findings are
# identical; only the access cost differs.


class LifecycleCache:
    """Process-local memo of ``statute_id -> StatuteLifecycle`` (no replay).

    Each cited act's in-force window is read from the corpus XML at most once and
    reused across every citation into it. Unbounded but tiny: one small dataclass
    per distinct cited act, no large IR trees retained (contrast the heavy
    per-``(statute, as_of)`` PIT cache the replay path needs).
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
        from the consolidated oracle's ``finlex:repealedBy`` block — the SAME
        extraction the statute-name registry uses. When NEITHER source nor oracle
        is present, the act has no lifecycle on record → ``known=False`` (→
        unverifiable). An act with a body but no repeal date keeps an OPEN
        ``valid_to`` (= in force), which is correct and is NOT "unknown".
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
        # A store read failure for one act's source is not a verdict — it means
        # "no source on record here"; the oracle read below still runs and, if
        # both are absent, saw_any stays False → known=False (unverifiable), never
        # a guessed window.
        # lawvm-failloud: undetermined-not-broken; both-absent → known=False.
        except Exception:
            src = None
        if src:
            saw_any = True
            extracted = _extract_title_and_date(src)
            if extracted is not None:
                _title, valid_from = extracted

        try:
            oracle = self._store.read_oracle(statute_id)
        # A failed oracle read means "no repeal date on record here", not a
        # verdict; an act with neither source nor oracle yields known=False.
        # lawvm-failloud: undetermined-not-broken; both-absent → known=False.
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
    """Build a ``LifecycleLookup`` reading each act's window from the corpus XML.

    Archive-only (no replay): ``valid_from`` from the source XML, ``valid_to``
    from the consolidated oracle's repeal block. Memoized via ``LifecycleCache``
    (pass an explicit cache in tests; ``None`` makes a fresh per-call cache).
    Returns ``StatuteLifecycle(known=False)`` for an act with no corpus XML at all
    (→ ``StatuteLifecycleUnverifiable``), never a guessed window.
    """
    lc = cache if cache is not None else LifecycleCache(store)
    return lc.get


def registry_artifact_lifecycle_lookup(
    artifact_path: str | Path,
    *,
    fallback: Optional[LifecycleLookup] = None,
) -> LifecycleLookup:
    """Build a ``LifecycleLookup`` from the pre-built statute-name registry artifact.

    The whole-corpus ``statute_name_registry.jsonl`` already carries every titled
    act's ``(valid_from, valid_to)`` window — extracted by the SAME helpers
    (``_extract_title_and_date`` / ``_extract_repeal_date``) the oracle path calls
    per act. So consulting the artifact yields the BYTE-IDENTICAL window with ZERO
    per-act XML reads: the artifact is loaded once into an in-memory
    ``id -> StatuteLifecycle`` table and every lookup is a dict hit.

    Completeness. The artifact enumerates TITLED acts (a source/title), so it does
    not carry orphan-oracle ids (a repealed act with only a consolidated oracle).
    An id ABSENT from the artifact is delegated to ``fallback`` (the per-act oracle
    lookup) when given, so no genuine repealed-target finding is silently lost;
    with no fallback an absent id is ``known=False`` (unverifiable) — never a false
    BROKEN and never silently judged in force.
    """
    from lawvm.finland.references.registries.statute_name import (
        load_statute_name_entries,
    )

    table: dict[str, StatuteLifecycle] = {}
    for entry in load_statute_name_entries(artifact_path):
        # First-write-wins so a stable window is bound per id even in the
        # (unexpected) event of a duplicate id row in the artifact.
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
        if fallback is not None:
            return fallback(statute_id)
        return _UNKNOWN

    return _lookup


def default_lifecycle_lookup(store: "CorpusStore") -> LifecycleLookup:
    """The lifecycle lookup a corpus dangling-citation scan should use.

    Prefers the pre-built whole-corpus registry artifact
    (``statute_name.default_artifact_path``) — a one-shot in-memory load that
    serves every titled act's window with no per-act XML read — and falls back to
    the per-act oracle lookup (memoized) for ids the artifact does not carry
    (orphan-oracle repealed acts), so the ledger loses NO genuine finding. When the
    artifact is absent entirely (fresh checkout) it uses the oracle path alone.
    Both paths read the SAME windows via the SAME extraction, so findings are
    identical; only the access cost differs. NO ``legal_pit`` replay on any path.
    """
    from lawvm.finland.references.registries.statute_name import (
        default_artifact_path,
    )

    oracle = oracle_lifecycle_lookup(store)
    artifact = default_artifact_path()
    if artifact.exists():
        return registry_artifact_lifecycle_lookup(artifact, fallback=oracle)
    return oracle


# ---------------------------------------------------------------------------
# Runnable corpus dangling-citation scan (statute-level, NO replay)
# ---------------------------------------------------------------------------
#
# CITING ANCHOR = NOW, deliberately.
# The body we extract mentions from is the CURRENT consolidated text-state: it
# accumulates every amendment up to today, so a reference in it may have been
# INSERTED by an amendment much later than the citer's original enactment.
# Anchoring the "citing date" to the enactment year would manufacture a huge
# not-yet-in-force false-positive class (an old act "citing" a future act because
# a later amendment added the reference). The honest, defensible question for a
# live consolidated text is: "does the law AS IT STANDS NOW cite an act that is
# not in force NOW?" — a live text still pointing at an act repealed years ago is
# the genuine dangling-citation finding. So the scan pins the citing anchor to
# today; the detector itself stays date-general.


@dataclass
class DanglingCitationReport:
    """Corpus-slice aggregate of the statute-level dangling-citation scan.

    A finding is a surface fact: a live consolidated statute text cites an ACT
    whose in-corpus repeal date is at/before the citing anchor (today). NOT a
    legal conclusion. Deterministic and fail-loud — an unknown lifecycle is
    counted as ``lifecycle_unverifiable``, never as a finding.

    Attributes:
        statutes_scanned: How many citing statutes were scanned.
        statutes_with_findings: How many had >=1 dangling-act finding.
        statutes_errored: ``(sid, error)`` for citers whose own scan raised
            (fail-loud — recorded, never silently dropped).
        mentions_checked: Total resolved cross-statute mentions inspected.
        reason_counts: Findings tallied by ``BrokenReason`` value
            (target_statute_repealed / target_statute_not_yet_in_force).
        unverifiable_count: ``StatuteLifecycleUnverifiable`` records (fail-loud
            undetermined — unknown lifecycle / no citing anchor). Never broken.
        findings: The full flat list of confirmed findings (one per dangling
            cite), for the caller to render / persist. Deterministically ordered.
    """

    statutes_scanned: int = 0
    statutes_with_findings: int = 0
    statutes_errored: list[tuple[str, str]] = field(default_factory=list)
    mentions_checked: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)
    unverifiable_count: int = 0
    findings: list[StatuteLifecycleFinding] = field(default_factory=list)

    @property
    def total_findings(self) -> int:
        return len(self.findings)


def _resolved_body(store: "CorpusStore", sid: str) -> Optional[bytes]:
    """Best available body XML for reference extraction (archive-only, no replay).

    Prefers the consolidated oracle (the in-force text), falling back to the
    enacted source / amendment act so repealed/expired citers still contribute
    their mentions.
    """
    from lawvm.finland.legal_surface.body_source import read_reference_body

    return read_reference_body(store, sid)  # type: ignore[arg-type]


def scan_dangling_citations(
    statute_ids: list[str],
    store: "CorpusStore",
    *,
    lifecycle_of: Optional[LifecycleLookup] = None,
    current_as_of: Optional[date] = None,
) -> DanglingCitationReport:
    """Scan ``statute_ids`` for citations to an ACT not in force at the citing date.

    The bounded, runnable statute-level dangling-citation path. For each citing
    statute it reads the body (archive-only), extracts resolved cross-statute
    mentions, anchors their citing date to ``current_as_of`` (default: today —
    see the module note on why NOW is the honest anchor for consolidated text),
    and runs ``detect_statute_lifecycle_broken`` with a registry-sourced
    ``LifecycleLookup``. NO ``legal_pit`` replay on any path.

    Args:
        statute_ids: Citing statutes to scan (iterated in order).
        store: Corpus store the body reads and the default lifecycle lookup read
            from.
        lifecycle_of: Injected ``LifecycleLookup``. When ``None``, the default
            registry-artifact-preferred, oracle-fallback lookup over ``store`` is
            built once and SHARED across every citer (so each distinct cited act's
            window is read at most once). Tests inject a synthetic lookup and never
            touch a corpus.
        current_as_of: The citing anchor. Defaults to ``date.today()``.

    Returns:
        A ``DanglingCitationReport``. Mentions are never mutated; statutes are
        never silently dropped (a scan failure is recorded in ``statutes_errored``,
        an unknown lifecycle in ``unverifiable_count``).
    """
    from dataclasses import replace

    from lawvm.finland.references.ref_mention_extractor import (
        extract_all_reference_mentions,
    )

    if lifecycle_of is None:
        lifecycle_of = default_lifecycle_lookup(store)
    anchor = current_as_of if current_as_of is not None else date.today()

    report = DanglingCitationReport()
    reason_ct: collections.Counter[str] = collections.Counter()

    for sid in statute_ids:
        report.statutes_scanned += 1
        try:
            xb = _resolved_body(store, sid)
        # Recorded into the report's statutes_errored bucket (a production-visible,
        # per-id error surface), never silently dropped and never a finding.
        # lawvm-failloud: recorded into statutes_errored, surfaced in the report.
        except Exception as exc:  # noqa: BLE001 — fail-loud into the errored bucket
            report.statutes_errored.append((sid, f"read_body: {exc!r}"))
            continue
        if not xb:
            continue
        try:
            extraction = extract_all_reference_mentions(xb, sid)
        # Recorded into statutes_errored (surfaced in the report), never silently
        # dropped and never counted as a finding.
        # lawvm-failloud: recorded into statutes_errored, surfaced in the report.
        except Exception as exc:  # noqa: BLE001 — fail-loud into the errored bucket
            report.statutes_errored.append((sid, f"extract: {exc!r}"))
            continue

        # Stamp the citing anchor onto every mention whose interval start is open
        # (a concrete extractor-supplied start is left untouched).
        anchored = [
            replace(m, valid_at_interval=(anchor, m.valid_at_interval[1]))
            if m.valid_at_interval[0] is None
            else m
            for m in extraction.mentions
        ]
        results = detect_statute_lifecycle_broken(anchored, lifecycle_of=lifecycle_of)

        stmt_findings = [
            r for r in results if isinstance(r, StatuteLifecycleFinding)
        ]
        unverifiable = [
            r for r in results if isinstance(r, StatuteLifecycleUnverifiable)
        ]
        # mentions_checked = the count the detector actually inspected (a resolved
        # cross-statute target it either found in force, dangling, or unverifiable).
        report.mentions_checked += len(stmt_findings) + len(unverifiable)
        report.unverifiable_count += len(unverifiable)
        if stmt_findings:
            report.statutes_with_findings += 1
            for f in stmt_findings:
                reason_ct[f.reason.value] += 1
            report.findings.extend(stmt_findings)

    # Stable ordering: by citer, then target act, then source/target address, so
    # the ledger is deterministic and diff-friendly.
    report.findings.sort(
        key=lambda f: (
            f.source.statute_id,
            f.target.statute_id,
            f.source.serialized(),
            f.target.serialized(),
        )
    )
    # Closed-enum reason ordering for a deterministic summary.
    report.reason_counts = {
        r.value: reason_ct[r.value] for r in BrokenReason if reason_ct.get(r.value)
    }
    return report
