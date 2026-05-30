"""Source-faithfulness sensor for UK whole-provision repeal operations.

A structural repeal that deletes a whole provision must be *warranted by the
source*: the affecting act's instruction must actually name that provision. When
a whole-section / whole-schedule repeal op's target label does not appear in the
source instruction text (``op.source.raw_text``), the op was synthesized by
inference — range/span expansion (e.g. the feed's ``s. 1-430F`` exploded into
section-by-section repeals the affecting act's Schedule never enumerates) or
over-broad target resolution — rather than issued by the source. That is the
over-generation / over-repeal risk: executing an instruction the source program
never issued (AGENTS.md §2.1).

This is the *sensor* phase: it emits an owned, non-blocking observation so the
population is visible and countable. A later strict gate can reject these once
the constructive resolver (enumerate the affecting act's repeal Schedule) lands;
at that point a warranted whole-provision repeal is one the schedule lists.

Scope note: only WHOLE-provision repeals (``section:N`` / ``schedule:N`` with no
descendant path) are checked. Sub-provision repeals (``omit words in s. 10(2)``)
are word-level and faithful by construction — the omit instruction names the
words, not the container — so they are out of scope here.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.semantic_types import StructuralAction

REPEAL_SOURCE_WARRANT_RULE_ID = "uk_repeal_target_not_source_warranted"

_WHOLE_PROVISION_TARGET_RE = re.compile(r"(section|schedule):([0-9]+[A-Za-z]*)\Z")


def _whole_provision_repeal_label(op: Any) -> Optional[tuple[str, str]]:
    """Return ``(kind, label)`` for a whole-section/schedule REPEAL op, else None."""
    if getattr(op, "action", None) is not StructuralAction.REPEAL:
        return None
    match = _WHOLE_PROVISION_TARGET_RE.fullmatch(str(getattr(op, "target", "")))
    if match is None:
        return None
    return match.group(1), match.group(2)


def repeal_op_target_in_source(op: Any) -> Optional[bool]:
    """Tri-state warrant for a repeal op.

    - ``True``  — whole-provision repeal whose target label appears in the source
      instruction text (warranted).
    - ``False`` — whole-provision repeal whose target label is absent from the
      source instruction text (unwarranted / inference-derived).
    - ``None``  — not a whole-provision repeal, so this check does not apply.
    """
    parsed = _whole_provision_repeal_label(op)
    if parsed is None:
        return None
    _kind, label = parsed
    raw_text = getattr(getattr(op, "source", None), "raw_text", "") or ""
    return re.search(rf"\b{re.escape(label)}\b", raw_text, flags=re.I) is not None


def collect_repeal_source_warrant_observations(ops: Any) -> list[dict[str, Any]]:
    """Emit a non-blocking observation per unwarranted whole-provision repeal op."""
    observations: list[dict[str, Any]] = []
    for op in ops:
        if repeal_op_target_in_source(op) is False:
            source = getattr(op, "source", None)
            observations.append(
                diagnostic_detail(
                    rule_id=REPEAL_SOURCE_WARRANT_RULE_ID,
                    family="source_pathology",
                    phase="lowering",
                    blocking=False,
                    reason=(
                        "A whole-provision repeal deletes a provision whose label does "
                        "not appear in the source instruction text, so the op was "
                        "synthesized by inference (range/span expansion or over-broad "
                        "target resolution) rather than issued by the affecting act — a "
                        "source-faithfulness / over-repeal risk."
                    ),
                    detail={
                        "target": str(getattr(op, "target", "")),
                        "op_id": str(getattr(op, "op_id", "")),
                        "affecting_act_id": str(getattr(source, "statute_id", "")),
                    },
                )
            )
    return observations
