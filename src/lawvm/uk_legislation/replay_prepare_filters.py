from __future__ import annotations

import re

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.addressing import _action_name, _addr_container, _addr_leaf_kind, _uk_kind_value
from lawvm.uk_legislation.provenance_notes import _schedule_list_entry_repeal_selector


def _looks_like_schedule_entry_repeal_text(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    if not re.search(r"\b(?:repeal\w*|omit\w*)\b", normalized):
        return False
    return bool(re.search(r"\b(?:entry|entries)\s+(?:for|relating\s+to|in\s+relation\s+to)\b", normalized))


def _is_unsafe_schedule_entry_repeal_op(op: LegalOperation) -> bool:
    if _action_name(op.action) != "repeal":
        return False
    if _schedule_list_entry_repeal_selector(op) is not None:
        return False
    if _addr_container(op.target) != "schedule":
        return False
    if _addr_leaf_kind(op.target) not in {"schedule", "part", "chapter", "division"}:
        return False
    payload = op.payload
    raw_text = op.source.raw_text if op.source is not None else ""
    payload_text = payload.text if payload is not None else ""
    if not _looks_like_schedule_entry_repeal_text(f"{raw_text} {payload_text}"):
        return False
    return payload is None or _uk_kind_value(payload.kind) == "schedule"


# Structural container kinds whose whole-node REPLACE deletes every child section
# at once. A LEGITIMATE whole-Part/Chapter *substitution* ("Part 4A substituted for
# s. 40-55") replaces the container's sections with the payload's sections, so it
# carries the replacement sections as structured CHILDREN. A same-kind REPLACE whose
# payload has ZERO children is therefore a MIS-COMPILE: applying it swaps a populated
# Part/Chapter for an empty shell, silently deleting every untargeted child section.
# (This shape arises when a cross-referencing instrument's descriptive prose — e.g.
# "Part IX ... is to be treated as applying" — is lowered onto a bare
# ``part:N``/``chapter:N`` target with a flat-text, childless part/chapter payload.)
# We deliberately restrict to REPLACE (not REPEAL): a bare whole-Part REPEAL is a
# legitimate amendment shape — the REPEAL action IS the amendment verb — and must
# still collapse the container. See task #209 (ukpga/2000/8: uksi/2001/2617 lowered
# descriptive Building-Societies-Order text onto FSMA ``part:9``/``part:26``, whose
# childless part payloads deleted ~sections 132-137 and all of Part XXVI).
_UK_WHOLE_CONTAINER_CLOBBER_KINDS = frozenset({"part", "chapter"})


def _is_untyped_whole_container_structural_op(op: LegalOperation) -> bool:
    """True iff *op* is a whole-Part/Chapter REPLACE with an empty-children payload.

    Fires only for the narrow mis-compile shape: a bare ``part:N``/``chapter:N``
    target (a container of sections), a ``replace`` action, and a payload of the
    SAME container kind that carries NO structural children. Applying such a replace
    swaps a populated container for an empty shell, deleting every untargeted child
    section — never a legitimate substitution (those carry replacement children).
    """
    if _action_name(op.action) != "replace":
        return False
    path = op.target.path
    if len(path) != 1:
        return False
    leaf = _addr_leaf_kind(op.target)
    if leaf not in _UK_WHOLE_CONTAINER_CLOBBER_KINDS:
        return False
    if (op.target.special or "") != "":
        return False
    payload = op.payload
    if payload is None:
        return False
    if _uk_kind_value(payload.kind) != leaf:
        return False
    return not payload.children
