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
class ApplicableAmendmentSelection:
    """Resolved amendment set plus the oracle/cutoff witness used to select it."""

    records: tuple[dict[str, object], ...]
    cutoff_date: dt.date | None
    oracle_version_amendment_id: str | None


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
    candidates = _read_amendment_candidates(parent_id, corpus)
    applicable, cutoff_date, selection_basis_by_amendment = _filter_candidates(
        parent_id=parent_id,
        mode=mode,
        candidates=candidates,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        corpus=corpus,
        selector=selector,
    )
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
    )


def resolve_applicable_amendment_records(
    parent_id: str,
    mode: ReplaySelectionMode,
    corpus: CorpusStore | None = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> tuple[list[dict[str, object]], dt.date | None, str | None]:
    """Backward-shaped tuple adapter for replay-plan callers."""

    selection = select_applicable_amendments(
        parent_id,
        mode,
        corpus=corpus,
        selector=selector,
    )
    return list(selection.records), selection.cutoff_date, selection.oracle_version_amendment_id


def _read_amendment_candidates(
    parent_id: str,
    corpus: CorpusStore,
) -> tuple[AmendmentSelectionCandidate, ...]:
    candidates: list[AmendmentSelectionCandidate] = []
    for amendment_id in amendment_children_by_parent().get(parent_id, ()):
        xml_bytes = corpus.read_source(amendment_id)
        if xml_bytes is None:
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
    return tuple(candidates)


def _filter_candidates(
    *,
    parent_id: str,
    mode: ReplaySelectionMode,
    candidates: tuple[AmendmentSelectionCandidate, ...],
    cutoff_date: dt.date | None,
    oracle_version_amendment_id: str | None,
    corpus: CorpusStore,
    selector: ConsolidatedArtifactSelector | None,
) -> tuple[tuple[AmendmentSelectionCandidate, ...], dt.date | None, dict[str, str]]:
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

    return _readmit_oracle_reflected_candidates(
        parent_id=parent_id,
        applicable=applicable,
        candidates=candidates,
        cutoff_date=cutoff_date,
        corpus=corpus,
        selector=selector,
    )


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
