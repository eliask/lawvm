"""Typed request boundary for one Finland amendment processing step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional, Set

from lawvm.corpus_store import CorpusStore
from lawvm.core.compile_result import StrictProfile
from lawvm.core.provenance import MigrationEvent
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.chapter_seed_targets import ChapterSeedSkipInput
from lawvm.finland.statute import ReplayState, StatuteContext


@dataclass(frozen=True, slots=True)
class ProcessAmendmentRequest:
    """Semantic inputs for processing one amendment statute.

    Diagnostic and artifact destinations belong in ``ProcessAmendmentSinks``.
    """

    amendment_id: str
    state: ReplayState
    ctx: StatuteContext
    replay_mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation"
    parent_id: str = ""
    strict_profile: Optional[StrictProfile] = None
    chapter_seed_skip: Optional[Set[ChapterSeedSkipInput]] = None
    corpus: Optional[CorpusStore] = None
    future_repeals: Optional[Set[RepealTargetRef]] = None
    prior_migration_events: tuple[MigrationEvent, ...] = ()
    processed_amendment_titles: Optional[Dict[str, str]] = None
    amendment_edge_kind: str = "oracle_amendedBy"
