"""Finnish ProvisionRef.serialized() → HierarchicalLocator adapter.

ProvisionRef.serialized() produces a self-describing, TYPED form
``statute_id[/chN]/section[/momentti][/kLABEL]`` (see
``core.reference_mention.ProvisionRef.serialized``):
  '1734/3-000'          → statute-level only (no section)
  '1734/3-000/12'       → statute '1734/3-000', section '12'
  '1734/3-000/2 a'      → statute '1734/3-000', section '2 a'
  '1734/3-000/12/3'     → statute '1734/3-000', section '12', subsection (momentti) 3
  '1734/3-000/12/3/ka'  → statute '1734/3-000', section '12', subsection 3, item (kohta) 'a'
  '1734/3-000/ch47/4'   → statute '1734/3-000', chapter (luku) '47', section '4'
  '1734/3-000/ch3'      → statute '1734/3-000', chapter '3' (no section)
  '1734/3-000/12/k3'    → statute '1734/3-000', section '12', item (kohta) '3', no momentti

The statute_id itself may contain a slash: '1734/3-000' (year/number-suffix).
Modern statutes: '2003/434' (year=2003, number=434).

Separation rule: the statute_id occupies the first two slash-separated tokens
when the FIRST token is a 4-digit year (≥1600 and ≤2100).  Everything after
the statute_id are TYPED provision path segments:
  * ``ch{N}``      — chapter (luku);
  * bare integer   — momentti (subsection) — the only bare non-section segment;
  * ``k{LABEL}``   — kohta (item);
  * anything else  — the section label.

This module is Finland-specific and must NOT be imported from core/.

AGENTS.md discipline:
  §12: Finnish-specific knowledge belongs in finland/, not core/.
  §1.11: module-scope regex compile.
  §1.13: simple deterministic parsing, no regex over legal text bodies.
"""
from __future__ import annotations

import re
from typing import Optional

from lawvm.core.locator import HierarchicalLocator, LocatorSegment


_YEAR_RE = re.compile(r"^\d{4}$")


def parse_provision_ref_serialized(serialized: str) -> tuple[str, Optional[HierarchicalLocator]]:
    """Parse a ProvisionRef.serialized() string into (statute_id, locator_or_None).

    The statute_id occupies the first two slash-separated tokens when the
    first token is a 4-digit year-like value (≥1600, ≤2100).  All remaining
    tokens form the provision path.

    Returns:
      (statute_id, None)           — statute-level only, no section locator.
      (statute_id, HierarchicalLocator)  — with section (and optionally more).

    If the input cannot be parsed (empty, single token with no year prefix),
    returns ('', None).
    """
    if not serialized:
        return ("", None)

    parts = serialized.split("/")

    # Determine where the statute_id ends and provision path begins.
    # A Finnish statute_id is always 2 slash-separated tokens:
    #   first = 4-digit year (1600–2100)  e.g. '2003', '1734', '1999'
    #   second = number (possibly with suffix) e.g. '434', '3-000', '1091'
    if len(parts) >= 2 and _YEAR_RE.match(parts[0]):
        year_val = int(parts[0])
        if 1600 <= year_val <= 2100:
            statute_id = f"{parts[0]}/{parts[1]}"
            provision_parts = parts[2:]
        else:
            # First token is 4-digit but not a plausible year — treat as opaque
            statute_id = serialized
            return (statute_id, None)
    else:
        # Single token or first token is not a 4-digit year
        statute_id = serialized
        return (statute_id, None)

    if not provision_parts:
        return (statute_id, None)

    # Parse the TYPED provision tail. Chapter (``chN``) leads when present;
    # momentti is the only bare-integer segment after the section; kohta is
    # ``k``-prefixed. (Mirrors ProvisionRef.serialized's emission order.)
    segments: list[LocatorSegment] = []
    idx = 0
    if provision_parts[idx].startswith("ch"):
        segments.append(
            LocatorSegment(kind="chapter", label=provision_parts[idx][len("ch") :])
        )
        idx += 1
    if idx < len(provision_parts):
        # section_label (e.g. '12', '2 a', '198b')
        segments.append(LocatorSegment(kind="section", label=provision_parts[idx]))
        idx += 1
    # Subsection (momentti) — bare integer, NOT a ``k``-prefixed kohta.
    if idx < len(provision_parts) and not provision_parts[idx].startswith("k"):
        segments.append(LocatorSegment(kind="subsection", label=provision_parts[idx]))
        idx += 1
    # Item (kohta) — ``k``-prefixed; maps to AKN "paragraph" kind.
    if idx < len(provision_parts) and provision_parts[idx].startswith("k"):
        segments.append(
            LocatorSegment(kind="paragraph", label=provision_parts[idx][len("k") :])
        )
        idx += 1

    if not segments:
        return (statute_id, None)

    return (statute_id, HierarchicalLocator(segments=tuple(segments)))
