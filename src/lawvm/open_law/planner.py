"""Planning from Open Law locators to corpus XML files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from lawvm.open_law.models import OpenLawFinding, OpenLawOperation


@dataclass(frozen=True)
class OpenLawFilePlan:
    """Plan for auditing one operation against before/after XML files."""

    status: str
    xml_path: str = ""
    path_prefix: Tuple[str, ...] = ()
    finding: OpenLawFinding | None = None


def plan_maryland_comar_operation(op: OpenLawOperation) -> OpenLawFilePlan:
    """Map a Maryland COMAR codify path to a chapter-level XML file.

    This handles the current public COMAR layout where chapter files live at:

    ``us/md/exec/comar/<title>/<subtitle>/<chapter>.xml``

    and operations target descendants inside that chapter.
    """

    if op.doc != "Code of Maryland Regulations":
        return _planning_failure(
            op,
            "open_law_unplanned_document",
            f"Unsupported Open Law document for Maryland COMAR planner: {op.doc!r}.",
        )
    if len(op.path) == 2 and op.path[1] == "heading":
        title = op.path[0]
        return OpenLawFilePlan(
            status="planned",
            xml_path=f"us/md/exec/comar/{title}/index.xml",
            path_prefix=(),
        )
    if len(op.path) < 3:
        return _planning_failure(
            op,
            "open_law_unplanned_short_path",
            f"Open Law path is too short for chapter-file planning: {'|'.join(op.path)!r}.",
        )
    if len(op.path) == 3 and op.path[2] == "heading":
        title, subtitle = op.path[:2]
        return OpenLawFilePlan(
            status="planned",
            xml_path=f"us/md/exec/comar/{title}/{subtitle}/index.xml",
            path_prefix=(title,),
        )
    title, subtitle, chapter = op.path[:3]
    return OpenLawFilePlan(
        status="planned",
        xml_path=f"us/md/exec/comar/{title}/{subtitle}/{chapter}.xml",
        path_prefix=(title, subtitle),
    )


def _planning_failure(op: OpenLawOperation, kind: str, message: str) -> OpenLawFilePlan:
    return OpenLawFilePlan(
        status="failed",
        finding=OpenLawFinding(
            kind=kind,
            message=message,
            op_id=op.op_id,
            path=op.path,
            blocking=True,
        ),
    )
