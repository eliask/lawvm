"""Typed carriers for the Open Law Library frontend."""

from __future__ import annotations
from typing_extensions import override

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from lawvm.core.ir import IRNode


class OpenLawAction(Enum):
    """Structured operation names emitted in the Open Law ``codify`` namespace."""

    REPLACE = "replace"
    REPLACE_OR_INSERT = "replace-or-insert"
    EXPIRE = "expire"
    UNSUPPORTED = "unsupported"

    @override
    def __str__(self) -> str:
        return self.value


class OpenLawAnnotationLane(Enum):
    """Per-jurisdiction policy for whether Open Law annotations are official law.

    Annotation status is jurisdiction-dependent. In some jurisdictions (e.g. DC)
    all annotations are part of the official code; in others they are
    non-authoritative publication metadata. This policy is not knowable from the
    XML alone, so it is supplied per jurisdiction rather than hard-assumed.

    - ``OFFICIAL_CODE``: annotations are authoritative legal text and participate
      in the legal-text snapshot comparison.
    - ``PUBLICATION_METADATA``: annotations are non-authoritative and are
      projected out of the legal-text comparison into a separate metadata lane.
    - unset (``None`` at the call site): conservative default. Annotations are
      treated as potentially authoritative (compared, not discarded) and a
      finding records that the jurisdiction policy is unset.
    """

    OFFICIAL_CODE = "official_code"
    PUBLICATION_METADATA = "publication_metadata"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class OpenLawOperation:
    """One parsed Open Law codification operation."""

    op_id: str
    sequence: int
    action: OpenLawAction
    doc: str
    path: Tuple[str, ...]
    source_id: str
    effective: str = ""
    expire_date: str = ""
    history: bool = True
    applicability: str = ""
    payload: IRNode | None = None
    raw_action: str = ""
    diagnostics: Tuple["OpenLawFinding", ...] = ()


@dataclass(frozen=True)
class OpenLawLifecycleTombstone:
    """A replayed ``codify:expire`` lifecycle result.

    Open Law expiry produces a jurisdiction-dependent tombstone rather than a
    single universal deletion semantics (regime contract §5.1). For Maryland the
    declared ``codify:expire`` targets are Register emergency/proposed-regulation
    identifiers (``regulations|emergency|<id>``) that are not nodes in the
    persistent COMAR chapter tree, so the tombstone is a standalone typed
    lifecycle marker: it records that the identified target became expired at
    ``expire_date`` in this observer-time slice. It is emitted, owned, and
    replayed — not left as an unexecuted lifecycle gap. It is never a silent
    deletion of unrelated tree state.

    ``jurisdiction`` names the tombstone regime that produced this marker so the
    result is self-describing; core does not interpret frontend-local values.
    """

    op_id: str
    doc: str
    open_law_path: Tuple[str, ...]
    expire_date: str
    history: bool
    jurisdiction: str = "maryland_register"


@dataclass(frozen=True)
class OpenLawFinding:
    """Audit observation emitted by the Open Law frontend.

    ``source_pathology`` marks a finding as a defect in the published source
    artifact (a failed codification instruction). Open Law publishers receive a
    compile-time error when a codification instruction fails, so a failed
    instruction surfacing in published data is a source bug, not a replay-side
    recovery target. The verifier records these; it never silently repairs them.
    """

    kind: str
    message: str
    op_id: str = ""
    path: Tuple[str, ...] = ()
    blocking: bool = False
    source_pathology: bool = False
