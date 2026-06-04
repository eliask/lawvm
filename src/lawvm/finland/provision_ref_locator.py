"""Finnish ProvisionRef.serialized() → HierarchicalLocator adapter.

ProvisionRef.serialized() produces forms like:
  '1734/3-000'          → statute-level only (no section)
  '1734/3-000/12'       → statute '1734/3-000', section '12'
  '1734/3-000/2 a'      → statute '1734/3-000', section '2 a'
  '1734/3-000/12/3'     → statute '1734/3-000', section '12', subsection 3
  '1734/3-000/12/3/a'   → statute '1734/3-000', section '12', subsection 3, item 'a'

The statute_id itself may contain a slash: '1734/3-000' (year/number-suffix).
Modern statutes: '2003/434' (year=2003, number=434).

Separation rule: the statute_id occupies the first two slash-separated tokens
when the FIRST token is a 4-digit year (≥1600 and ≤2100).  Everything after
the statute_id are provision path segments: section, subsection, item.

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

    # provision_parts[0] = section_label (e.g. '12', '2 a', '198b')
    # provision_parts[1] = subsection_num (e.g. '3')
    # provision_parts[2] = item_label (e.g. 'a')
    section_label = provision_parts[0]

    segments: list[LocatorSegment] = [LocatorSegment(kind="section", label=section_label)]

    # Subsections map to "subsection" kind; items map to "paragraph" kind.
    # These are not used by FinnishAKNResolver's current eId translation but
    # are included for completeness and future use.
    if len(provision_parts) >= 2:
        segments.append(LocatorSegment(kind="subsection", label=provision_parts[1]))
    if len(provision_parts) >= 3:
        segments.append(LocatorSegment(kind="paragraph", label=provision_parts[2]))

    return (statute_id, HierarchicalLocator(segments=tuple(segments)))
