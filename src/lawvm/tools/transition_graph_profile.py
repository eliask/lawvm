"""Jurisdiction profile hooks for transition-graph viewer exports."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


SourceReferenceExtractor = Callable[[object, str], str]
StatuteIdCodec = Callable[[str], str]
SourceUrlBuilder = Callable[[str, str], str]
CorpusFactory = Callable[[], object | None]
CommencementDateResolver = Callable[[object], str]


def no_source_reference(_corpus: object, _engine_source_id: str) -> str:
    return ""


def no_source_url(_canonical_id: str, _engine_id: str) -> str:
    return ""


def no_corpus() -> object | None:
    return None


def no_commencement_date(_timelines: object) -> str:
    return ""


@dataclass(frozen=True, slots=True)
class TransitionGraphExportProfile:
    """Jurisdiction-owned metadata and source-link hooks for viewer exports."""

    jurisdiction: str
    lang: str
    canonical_statute_id: StatuteIdCodec
    engine_statute_id: StatuteIdCodec
    statute_url: SourceUrlBuilder = no_source_url
    amendment_url: SourceUrlBuilder = no_source_url
    source_reference: SourceReferenceExtractor = no_source_reference
    corpus: CorpusFactory = no_corpus
    commencement_date: CommencementDateResolver = no_commencement_date

    def __post_init__(self) -> None:
        if not self.jurisdiction:
            raise ValueError("TransitionGraphExportProfile.jurisdiction must be non-empty")
        if not self.lang:
            raise ValueError("TransitionGraphExportProfile.lang must be non-empty")
