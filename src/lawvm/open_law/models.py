"""Typed carriers for the Open Law Library frontend."""

from __future__ import annotations

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
    history: bool = True
    applicability: str = ""
    payload: IRNode | None = None
    raw_action: str = ""


@dataclass(frozen=True)
class OpenLawFinding:
    """Audit observation emitted by the Open Law frontend."""

    kind: str
    message: str
    op_id: str = ""
    path: Tuple[str, ...] = ()
    blocking: bool = False
