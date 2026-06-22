"""Applicable-amendment selection for Finnish replay.

This module owns the replay-plan question "which amendment acts are in scope,
and in what order?"  It keeps oracle-version filtering, legal-PIT ordering, and
oracle-reflected source overrides out of the grafter orchestration layer.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import lxml.etree as etree

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.stage_result import PartitionResult
from lawvm.corpus_store import CorpusStore
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.corpus import (
    _get_corpus_store,
    _oracle_mode_sort_key,
    get_consolidated_meta,
    get_consolidated_oracle_reflected_source_vts_children,
)
from lawvm.finland.metadata import (
    _amendment_effective_date,
    _statute_id_sort_key,
    _statute_issue_date,
)


ReplaySelectionMode = Literal["official_consolidation", "legal_pit"]

# Conservation (Audit C): a candidate the cutoff/oracle-version filter excludes
# from the replay plan is rejected with this reason rather than silently dropped.
AMENDMENT_OUT_OF_SCOPE_REASON = (
    "amendment candidate is later than the selected cutoff / oracle version "
    "boundary, so it is out of scope for this replay plan"
)
AMENDMENT_OUT_OF_SCOPE_REASON_CODE = "fi_amendment_selection_out_of_scope"


@dataclass(frozen=True, slots=True)
class AmendmentSelectionCandidate:
    """One amendment act with the source facts needed for replay ordering."""

    amendment_id: str
    effective_date: dt.date | None
    issue_date: dt.date | None
    title: str

    @property
    def ordering_date(self) -> dt.date:
        return amendment_ordering_date(self.effective_date, self.issue_date)


@dataclass(frozen=True, slots=True)
class AmendmentSourcePathology:
    """A candidate amendment dropped from the replay plan for a source reason.

    Conservation record (AGENTS.md §1.8): the candidate-reading loop must not
    silently shorten the replay plan. When ``corpus.read_source`` returns no
    bytes for a child amendment the candidate cannot be ordered into the plan,
    but the drop is recorded here rather than via a bare ``continue`` — mirroring
    ``amendment_index``'s ``fi_amendment_index_source_vts_artifact_missing`` for
    the identical condition.
    """

    rule_id: str
    family: str
    phase: str
    reason: str
    amendment_id: str
    blocking: bool = False
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class ApplicableAmendmentSelection:
    """Resolved amendment set plus the oracle/cutoff witness used to select it.

    Conservation (Audit C): ``out_of_scope`` carries the candidates the cutoff /
    oracle-version filter excluded from the replay plan. They are no longer
    silently dropped inside ``_filter_candidates`` — the selection result is the
    production consumer that reads the filter partition's ``rejected`` lane and
    surfaces every excluded candidate here for inspection. ``residuals`` keeps the
    source-pathology drops (missing source bytes) as before.
    """

    records: tuple[dict[str, object], ...]
    cutoff_date: dt.date | None
    oracle_version_amendment_id: str | None
    residuals: tuple[AmendmentSourcePathology, ...] = ()
    out_of_scope: tuple[RejectedItem[AmendmentSelectionCandidate], ...] = ()


@lru_cache(maxsize=1)
def amendment_children_by_parent() -> dict[str, list[str]]:
    from lawvm.finland.amendment_index import get_amendment_children

    return get_amendment_children()


def amendment_ordering_date(
    effective_date: dt.date | None,
    issue_date: dt.date | None,
) -> dt.date:
    return effective_date or issue_date or dt.date.min


def select_applicable_amendments(
    parent_id: str,
    mode: ReplaySelectionMode,
    *,
    corpus: CorpusStore | None = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> ApplicableAmendmentSelection:
    """Select amendment acts applicable to one replay mode.

    ``official_consolidation`` follows the selected consolidated artifact's
    version convention. ``legal_pit`` uses the same artifact version as its
    source boundary, but orders by legal effectivity for replay.
    """

    corpus = corpus or _get_corpus_store()
    cutoff_date, oracle_version_amendment_id = get_consolidated_meta(
        parent_id,
        selector=selector or ConsolidatedArtifactSelector.latest_cached_editorial(),
    )
    candidates, residuals = _read_amendment_candidates(parent_id, corpus)
    partition, cutoff_date, selection_basis_by_amendment = _filter_candidates(
        parent_id=parent_id,
        mode=mode,
        candidates=candidates,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        corpus=corpus,
        selector=selector,
    )
    # Production consumer of the filter partition: the accepted lane drives the
    # replay plan; the rejected (out-of-scope) lane is surfaced on the result so
    # the exclusion is inspectable rather than silent.
    applicable = partition.accepted
    ordered = sorted(
        applicable,
        key=lambda candidate: (
            candidate.ordering_date,
            candidate.issue_date or dt.date.min,
            _statute_id_sort_key(candidate.amendment_id),
        ),
    )
    return ApplicableAmendmentSelection(
        records=tuple(
            _record_for_candidate(
                sequence=idx,
                candidate=candidate,
                mode=mode,
                selection_basis=selection_basis_by_amendment.get(candidate.amendment_id, ""),
            )
            for idx, candidate in enumerate(ordered, start=1)
        ),
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        residuals=residuals,
        out_of_scope=partition.rejected,
    )


def resolve_applicable_amendment_records(
    parent_id: str,
    mode: ReplaySelectionMode,
    corpus: CorpusStore | None = None,
    selector: ConsolidatedArtifactSelector | None = None,
    *,
    residuals_out: list[AmendmentSourcePathology] | None = None,
) -> tuple[list[dict[str, object]], dt.date | None, str | None]:
    """Backward-shaped tuple adapter for replay-plan callers.

    Conservation (AGENTS.md §1.8): the tuple shape is preserved for the many
    inspection/debug callers, but the source-pathology residuals computed by
    ``select_applicable_amendments`` would otherwise be discarded here — turning
    a missing amendment source into a silently shorter replay plan on the live
    path. When ``residuals_out`` is supplied the residuals are threaded onto it
    so the replay pipeline can surface them on the production residual ledger.
    """

    selection = select_applicable_amendments(
        parent_id,
        mode,
        corpus=corpus,
        selector=selector,
    )
    if residuals_out is not None:
        residuals_out.extend(selection.residuals)
    return list(selection.records), selection.cutoff_date, selection.oracle_version_amendment_id


def _read_amendment_candidates(
    parent_id: str,
    corpus: CorpusStore,
) -> tuple[tuple[AmendmentSelectionCandidate, ...], tuple[AmendmentSourcePathology, ...]]:
    candidates: list[AmendmentSelectionCandidate] = []
    residuals: list[AmendmentSourcePathology] = []
    for amendment_id in amendment_children_by_parent().get(parent_id, ()):
        xml_bytes = corpus.read_source(amendment_id)
        if xml_bytes is None:
            # Conservation (AGENTS.md §1.8): a missing source artifact would
            # otherwise silently shorten the replay plan. Record the drop the
            # same way amendment_index does for the identical condition.
            residuals.append(
                AmendmentSourcePathology(
                    rule_id="fi_amendment_selection_source_artifact_missing",
                    family="source_pathology",
                    phase="acquisition",
                    reason=(
                        "Finland amendment selection skipped a candidate "
                        "amendment because its source XML bytes were missing."
                    ),
                    amendment_id=amendment_id,
                )
            )
            continue
        amendment_tree = etree.fromstring(xml_bytes)
        title_el = amendment_tree.find(".//{*}docTitle")
        title = " ".join("".join(str(text) for text in title_el.itertext()).split()) if title_el is not None else ""
        candidates.append(
            AmendmentSelectionCandidate(
                amendment_id=amendment_id,
                effective_date=_amendment_effective_date(amendment_tree),
                issue_date=_statute_issue_date(amendment_tree),
                title=title,
            )
        )
    return tuple(candidates), tuple(residuals)


def _filter_candidates(
    *,
    parent_id: str,
    mode: ReplaySelectionMode,
    candidates: tuple[AmendmentSelectionCandidate, ...],
    cutoff_date: dt.date | None,
    oracle_version_amendment_id: str | None,
    corpus: CorpusStore,
    selector: ConsolidatedArtifactSelector | None,
) -> tuple[
    PartitionResult[AmendmentSelectionCandidate],
    dt.date | None,
    dict[str, str],
]:
    """Partition the candidate set into accepted (in-scope) vs rejected.

    Conservation (Audit C): the cutoff / oracle-version filter previously dropped
    out-of-scope candidates silently. It now returns a ``PartitionResult`` whose
    ``accepted`` lane is byte-identical to the old applicable set and whose
    ``rejected`` lane carries every excluded candidate with an ``out_of_scope``
    reason. The acceptance decision is unchanged — only the discarded material is
    now typed and inspectable.
    """
    if mode == "legal_pit":
        applicable, cutoff_date = _filter_legal_pit_candidates(
            candidates,
            cutoff_date=cutoff_date,
            oracle_version_amendment_id=oracle_version_amendment_id,
        )
    elif mode == "official_consolidation":
        applicable = _filter_official_consolidation_candidates(
            candidates,
            cutoff_date=cutoff_date,
            oracle_version_amendment_id=oracle_version_amendment_id,
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")

    applicable, cutoff_date, selection_basis_by_amendment = (
        _readmit_oracle_reflected_candidates(
            parent_id=parent_id,
            applicable=applicable,
            candidates=candidates,
            cutoff_date=cutoff_date,
            corpus=corpus,
            selector=selector,
        )
    )

    accepted_ids = {candidate.amendment_id for candidate in applicable}
    rejected = tuple(
        RejectedItem(
            item=candidate,
            reason=AMENDMENT_OUT_OF_SCOPE_REASON,
            reason_code=AMENDMENT_OUT_OF_SCOPE_REASON_CODE,
            blocking=False,
        )
        for candidate in candidates
        if candidate.amendment_id not in accepted_ids
    )
    partition: PartitionResult[AmendmentSelectionCandidate] = PartitionResult(
        FilterResult(accepted_items=tuple(applicable), rejected_items=rejected),
    )
    return partition, cutoff_date, selection_basis_by_amendment


def _filter_legal_pit_candidates(
    candidates: tuple[AmendmentSelectionCandidate, ...],
    *,
    cutoff_date: dt.date | None,
    oracle_version_amendment_id: str | None,
) -> tuple[tuple[AmendmentSelectionCandidate, ...], dt.date | None]:
    if oracle_version_amendment_id is not None:
        version_key = _oracle_mode_sort_key(oracle_version_amendment_id)
        applicable = tuple(
            candidate
            for candidate in candidates
            if _oracle_mode_sort_key(candidate.amendment_id) <= version_key
        )
        version_candidate = _candidate_by_id(candidates, oracle_version_amendment_id)
        if version_candidate is not None:
            cutoff_date = version_candidate.ordering_date
        return applicable, cutoff_date

    if cutoff_date is not None:
        return tuple(candidate for candidate in candidates if candidate.ordering_date <= cutoff_date), cutoff_date
    return candidates, cutoff_date


def _filter_official_consolidation_candidates(
    candidates: tuple[AmendmentSelectionCandidate, ...],
    *,
    cutoff_date: dt.date | None,
    oracle_version_amendment_id: str | None,
) -> tuple[AmendmentSelectionCandidate, ...]:
    if oracle_version_amendment_id is not None:
        version_key = _oracle_mode_sort_key(oracle_version_amendment_id)
        return tuple(
            candidate
            for candidate in candidates
            if _oracle_mode_sort_key(candidate.amendment_id) <= version_key
        )

    if cutoff_date is not None:
        return tuple(candidate for candidate in candidates if candidate.ordering_date <= cutoff_date)
    return candidates


def _readmit_oracle_reflected_candidates(
    *,
    parent_id: str,
    applicable: tuple[AmendmentSelectionCandidate, ...],
    candidates: tuple[AmendmentSelectionCandidate, ...],
    cutoff_date: dt.date | None,
    corpus: CorpusStore,
    selector: ConsolidatedArtifactSelector | None,
) -> tuple[tuple[AmendmentSelectionCandidate, ...], dt.date | None, dict[str, str]]:
    selection_basis_by_amendment: dict[str, str] = {}
    oracle_reflected = get_consolidated_oracle_reflected_source_vts_children(
        parent_id,
        corpus=corpus,
        selector=selector,
    )
    if not oracle_reflected:
        return applicable, cutoff_date, selection_basis_by_amendment

    applicable_ids = {candidate.amendment_id for candidate in applicable}
    override_items = tuple(
        candidate
        for candidate in candidates
        if candidate.amendment_id in oracle_reflected and candidate.amendment_id not in applicable_ids
    )
    for candidate in override_items:
        selection_basis_by_amendment[candidate.amendment_id] = "oracle_editorial_repeal_stub_override"
        if cutoff_date is None or candidate.ordering_date > cutoff_date:
            cutoff_date = candidate.ordering_date
    return applicable + override_items, cutoff_date, selection_basis_by_amendment


def _candidate_by_id(
    candidates: tuple[AmendmentSelectionCandidate, ...],
    amendment_id: str,
) -> AmendmentSelectionCandidate | None:
    for candidate in candidates:
        if candidate.amendment_id == amendment_id:
            return candidate
    return None


def _record_for_candidate(
    *,
    sequence: int,
    candidate: AmendmentSelectionCandidate,
    mode: ReplaySelectionMode,
    selection_basis: str,
) -> dict[str, object]:
    return {
        "sequence": sequence,
        "statute_id": candidate.amendment_id,
        "title": candidate.title,
        "effective_date": candidate.effective_date.isoformat() if candidate.effective_date else "",
        "issue_date": candidate.issue_date.isoformat() if candidate.issue_date else "",
        "sort_mode": mode,
        "included": True,
        "selection_basis": selection_basis,
    }
