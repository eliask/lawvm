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
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Optional

from lawvm.core.ir import IRNode
from lawvm.finland.references.broken_detection import (
    BrokenCheckUnavailable,
    BrokenReason,
    BrokenReferenceFinding,
    ProvisionPresent,
    TreeAsOf,
    default_provision_present,
    detect_broken,
)

if TYPE_CHECKING:
    from lawvm.core.reference_mention import ReferenceMention
    from lawvm.corpus_store import CorpusStore


__all__ = [
    "StatuteScanResult",
    "BrokenRefReport",
    "scan_broken_references",
    "citation_anchor_for_statute",
    "legal_pit_tree_as_of",
]


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


def legal_pit_tree_as_of(store: "CorpusStore") -> TreeAsOf:
    """Build a ``TreeAsOf`` over the real ``legal_pit`` point-in-time replay.

    Returns the amended IR tree (``ReplayState.ir``) of one statute as of a
    date, or ``None`` (fail-loud) on any failure. This is the working
    counterpart to ``broken_detection.default_tree_as_of`` (which reaches for
    the wrong accessor and is left untouched per the no-edit boundary).
    """

    def _tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
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

    return _tree_as_of


# ---------------------------------------------------------------------------
# Citation-time anchor
# ---------------------------------------------------------------------------


def citation_anchor_for_statute(citing_statute_id: str) -> Optional[date]:
    """Coarse lower-bound citation date for a citing statute.

    A citation cannot have been written before the citing statute existed, so we
    anchor to 1 January of the statute's enactment year, parsed from the
    ``NUMBER/YEAR`` id. Returns ``None`` when the id carries no parseable year
    (then ``detect_broken`` runs without a temporal anchor for that statute and
    can only surface NEVER_EXISTED, never REPEALED/RENUMBERED — fail-soft, not a
    fabricated date).
    """
    tail = citing_statute_id.rsplit("/", 1)[-1]
    if not tail.isdigit():
        return None
    year = int(tail)
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

    Prefer the consolidated oracle (the same text the fi_refs projection scans);
    fall back to the enacted source or amendment act XML so non-consolidated
    statutes still contribute mentions.
    """
    try:
        xb = store.read_oracle(sid)
    except Exception:
        xb = None
    if xb:
        return xb
    return store.read_source(sid) or store.read_amendment(sid)


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
    from lawvm.finland.ref_mention_extractor import extract_all_reference_mentions

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
