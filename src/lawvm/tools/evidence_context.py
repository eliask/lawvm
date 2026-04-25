"""Shared replay context for evidence tools.

Built once per statute in build_evidence_bundle, then threaded to all
sub-tools (_section_bisect_support, _classify_statute, etc.) to avoid
redundant replay/fetch calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class EvidenceContext:
    """Pre-computed shared objects for one statute's evidence bundle."""

    statute_id: str
    mode: str = "legal_pit"
    oracle_root: Any = None
    html_audit: Any = None
    oracle_version_amendment_id: str = ""
    # Populated after _classify_statute returns
    replay_result: Any = field(default=None, repr=False)
    compiled_ops: List = field(default_factory=list, repr=False)
