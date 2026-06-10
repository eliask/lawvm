"""Open Law Library XML frontend.

This frontend treats Open Law XML as a cooperative structured source language.
It does not infer amendments from prose; it audits declared ``codify:*``
operations against Open Law XML trees and publication snapshots.

The publisher-documented regime semantics this frontend encodes are recorded in
``notes/OPEN_LAW_REGIME.md``. In summary:

- ``law-xml`` is source code; ``law-xml-codified`` and ``law-html`` are compiled
  outputs, reproducible from source only when the publication metadata declares
  ``reproducible: true`` (otherwise comparison is observational).
- ``codify:*`` is a stable operation language: an unknown verb is a finding,
  never a silent skip.
- A failed codification instruction is source pathology (the publisher would
  have seen a compile-time error); the verifier records it and never recovers
  it replay-side.
- Annotation authority is jurisdiction-dependent (official code vs publication
  metadata); the lane is a per-jurisdiction flag, conservative when unset.
- Temporal contract (documented, not executed): the effective date is a
  property of the whole document while individual parts may become applicable
  earlier or later (legal-effect time); a publication branch is the law as known
  for a slice of observer time, and history changes create new branches.
"""

from lawvm.open_law.audit import OpenLawReplayResult, OpenLawSnapshotAuditResult, audit_open_law_snapshot, replay_open_law_ops
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.models import OpenLawAction, OpenLawAnnotationLane, OpenLawFinding, OpenLawOperation
from lawvm.open_law.xml import parse_open_law_xml

__all__ = [
    "OpenLawAction",
    "OpenLawAnnotationLane",
    "OpenLawFinding",
    "OpenLawOperation",
    "OpenLawReplayResult",
    "OpenLawSnapshotAuditResult",
    "audit_open_law_snapshot",
    "parse_open_law_codify_ops",
    "parse_open_law_xml",
    "replay_open_law_ops",
]
