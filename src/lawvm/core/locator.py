"""Universal hierarchical section locator + per-jurisdiction resolver Protocol.

The LOCATOR string format is universal across AKN-using jurisdictions:

    part:5/chapter:11/section:10
    chapter:11/section:3
    section:14

The kind names are AKN-standard tag names (`part`, `chapter`, `section`,
`subpart`, `title`, `article`, `paragraph`, ...). Labels are the visible
identifiers (`5`, `11`, `3a`, `I`, ...).

The TRANSLATION from a HierarchicalLocator to an element in an oracle XML
document is jurisdiction-specific:

  - Finland: eId encoding `part_N__chp_N__sec_N` with `__` separator,
    abbreviations chp/sec/part, version suffix `vYYYYNNNN`, two parallel
    section elements per amendment.
  - UK Bills, NZ, EE, etc.: each their own eId conventions and version
    handling.

Each jurisdiction registers a SectionResolver implementation. Callers
look it up by jurisdiction code.
"""
from __future__ import annotations
from typing_extensions import override

import re
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class LocatorSegment:
    kind: str   # AKN tag name: part | chapter | section | subpart | title | article | ...
    label: str  # 5 | 11 | 10 | 3a | I | ...


@dataclass(frozen=True, slots=True)
class HierarchicalLocator:
    segments: tuple[LocatorSegment, ...]

    @override
    def __str__(self) -> str:
        return "/".join(f"{s.kind}:{s.label}" for s in self.segments)

    @property
    def is_top_level_section(self) -> bool:
        return len(self.segments) == 1 and self.segments[0].kind == "section"


_KIND_TOKEN_RE = re.compile(r"^[a-z][a-z_]*$")
_LABEL_TOKEN_RE = re.compile(r"^[A-Za-z0-9.\-][A-Za-z0-9.\- ]*$")


def parse_locator_string(s: str) -> HierarchicalLocator | None:
    """`part:5/chapter:11/section:10` → HierarchicalLocator. None on malformed input.

    Validates that each segment is `<kind>:<label>` with kind in a closed
    set of lowercase tokens and label in a conservative alphanumeric set.
    The kind is NOT checked against any jurisdiction's vocabulary — that's
    the resolver's job. We only check shape here.
    """
    if not s or ":" not in s:
        return None
    segments: list[LocatorSegment] = []
    for raw_segment in s.split("/"):
        if ":" not in raw_segment:
            return None
        kind, label = raw_segment.split(":", 1)
        kind = kind.strip().lower()
        label = label.strip()
        # lawvm-regex: owning_parser kind/label shape gate on parse_locator_string's own locator-string input (this file is the canonical locator-string parser); not an IR/raw_text read
        if not _KIND_TOKEN_RE.match(kind) or not _LABEL_TOKEN_RE.match(label):
            return None
        segments.append(LocatorSegment(kind=kind, label=label))
    return HierarchicalLocator(segments=tuple(segments))


class SectionResolver(Protocol):
    """Per-jurisdiction resolution of a HierarchicalLocator (or raw string) to
    an element in a parsed oracle XML document.

    Implementations:
      lawvm.finland.section_resolver.FinnishAKNResolver
      (future) lawvm.uk_legislation.section_resolver
      (future) lawvm.new_zealand.section_resolver
      ...
    """

    def resolve(self, root: Any, locator: HierarchicalLocator) -> Any | None:
        """Resolve a parsed hierarchical locator. None when no element matches."""
        ...

    def resolve_raw(self, root: Any, raw_locator: str) -> Any | None:
        """Resolve a raw string for jurisdiction-specific fallback addressing
        (e.g. Finnish `'2 §'` num-text matching). None when no match."""
        ...


_RESOLVERS: dict[str, SectionResolver] = {}


def register_section_resolver(jurisdiction: str, resolver: SectionResolver) -> None:
    _RESOLVERS[jurisdiction] = resolver


def get_section_resolver(jurisdiction: str) -> SectionResolver:
    resolver = _RESOLVERS.get(jurisdiction)
    if resolver is None:
        raise KeyError(
            f"no section resolver registered for jurisdiction {jurisdiction!r}. "
            f"Registered: {sorted(_RESOLVERS)}. "
            f"Did the jurisdiction module forget to call register_section_resolver()?"
        )
    return resolver
