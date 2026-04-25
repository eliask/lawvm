from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ClassifyResult:
    """Return type of _classify_statute.

    Not frozen because section_results items are mutated in-place during
    the classification pass (diagnosis field updated by heuristics).
    """

    sid: str
    error: Optional[str] = None
    title: str = ""
    mode: str = ""
    overall_score: float = 0.0
    section_score: float = 0.0
    section_results: List[Dict] = field(default_factory=list)
    source_pathologies: List[Dict] = field(default_factory=list)
    html_topology: Dict[str, Any] = field(default_factory=dict)
    contingent_effective_sources: List[str] = field(default_factory=list)
    # Private fields so callers can reuse already-computed objects
    replay_result: Any = field(default=None, repr=False)
    compiled_ops: List = field(default_factory=list, repr=False)
    # Cached per-statute data to avoid redundant calls in build_evidence_bundle
    oracle_version_amendment_id: str = field(default="", repr=False)
    oracle_sections: Any = field(default=None, repr=False)

    @property
    def has_error(self) -> bool:
        return self.error is not None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to plain dict for backward-compat with JSON serialization."""
        return {
            "sid": self.sid,
            "title": self.title,
            "mode": self.mode,
            "overall_score": self.overall_score,
            "section_score": self.section_score,
            "section_results": self.section_results,
            "source_pathologies": self.source_pathologies,
            "html_topology": self.html_topology,
            "contingent_effective_sources": self.contingent_effective_sources,
            "error": self.error,
        }
