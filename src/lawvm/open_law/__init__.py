"""Open Law Library XML frontend.

This frontend treats Open Law XML as a cooperative structured source language.
It does not infer amendments from prose; it audits declared ``codify:*``
operations against Open Law XML trees and publication snapshots.
"""

from lawvm.open_law.audit import OpenLawReplayResult, OpenLawSnapshotAuditResult, audit_open_law_snapshot, replay_open_law_ops
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.models import OpenLawAction, OpenLawFinding, OpenLawOperation
from lawvm.open_law.xml import parse_open_law_xml

__all__ = [
    "OpenLawAction",
    "OpenLawFinding",
    "OpenLawOperation",
    "OpenLawReplayResult",
    "OpenLawSnapshotAuditResult",
    "audit_open_law_snapshot",
    "parse_open_law_codify_ops",
    "parse_open_law_xml",
    "replay_open_law_ops",
]
