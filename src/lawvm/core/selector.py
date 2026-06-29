"""Shared human-stable provision selector grammar (``§a:b.c.d``).

The analyst-facing reading surface (``read`` / ``reconcile``) and the existing
engines (``provision-state``, ``oracle-text``, ...) historically disagreed on
how a provision is addressed:

  - ``oracle-text``  took the eId form ``chp_3__sec_1``
  - ``provision-state`` / ``timeline`` took ``chapter:3/section:1`` and
    subsection (Finlex: momentti) ``.../subsection:2``

Neither is human-stable. This module adds ONE canonical, human-stable surface
form that lowers to the existing ``chapter:/section:/subsection:/paragraph:``
locator string, so every command inherits it for free. The lowering is purely
additive: legacy forms (``chapter:3/section:1``, ``chp_3__sec_1``, ``1 §``) are
returned unchanged so existing callers keep working. The canonical vocabulary
is jurisdiction-neutral (subsection/paragraph); the Finlex aliases momentti
and kohta are noted only as a jurisdiction-local alias for the same levels,
not as canonical names.

Canonical form::

    §<chapter>:<section>[.<subsection>[.<paragraph>]]

Examples::

    §3:1     → chapter:3/section:1
    §3:1.2   → chapter:3/section:1/subsection:2
    §7       → section:7                              (flat / chapterless statute)
    §7.1.3   → section:7/subsection:1/paragraph:3

Semantic rule (load-bearing, from the Finlex XML grammar via the Finland
frontend): the subsection index is the **materialized in-force ordinal**, NOT
the raw ``subsec_N`` eId. The downstream resolver (``provision-state``) already
keys the subsection on the post-strip materialized sequence, so this module only
has to lower ``.N`` to ``subsection:N`` and let the resolver do the right thing.
Finlex's "momentti"/"kohta" wording is the same level pair surfaced as the
canonical subsection/paragraph here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A canonical §-selector: optional leading "§", then chapter:section or a bare
# section, then up to two dotted subsection/paragraph components.
# In the Finlex source grammar (Finland frontend) these levels surface as
# "momentti" (subsection) and "kohta" (paragraph); the canonical LawVM vocabulary
# here is jurisdiction-neutral and points to them only as a jurisdiction alias.
#   §3:1.2   §7   §7.1.3   3:1   §14 b   §3:1 a
# Labels may carry a trailing letter (e.g. "14 b", "3a"); the public LawVM
# locator form compacts that suffix ("14b") so it can be handed directly to
# `provision-state`.
_LABEL = r"[0-9]+(?:\s?[A-Za-z])?"
_SECTION_SELECTOR_RE = re.compile(
    rf"""
    ^\s*§?\s*
    (?:(?P<chapter>{_LABEL})\s*:\s*)?       # optional  <chapter>:
    (?P<section>{_LABEL})                    # required  <section>
    (?:\.\s*(?P<subsection>{_LABEL}))?       # optional  .<subsection>
    (?:\.\s*(?P<paragraph>{_LABEL}))?        # optional  .<paragraph>
    \s*$
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class ParsedSelector:
    """A parsed ``§`` selector, plus the lowered legacy locator string."""

    chapter: str | None
    section: str
    subsection: str | None  # Finlex alias: momentti
    paragraph: str | None  # Finlex alias: kohta
    locator: str  # e.g. "chapter:3/section:1/subsection:2"

    @property
    def is_section_scope(self) -> bool:
        """True when no subsection/paragraph is given (whole section)."""
        return self.subsection is None and self.paragraph is None


def _norm_label(raw: str) -> str:
    """Normalize a label's internal whitespace: '14 b' and '14b' -> '14b'."""
    m = re.fullmatch(r"\s*(\d+)\s*([A-Za-z])?\s*", raw)
    if m is None:
        return raw.strip()
    num, letter = m.group(1), m.group(2)
    return f"{num}{letter.lower()}" if letter else num


def parse_section_selector(s: str) -> ParsedSelector | None:
    """Parse a canonical ``§a:b.c.d`` selector. None if it is not that form.

    Returns None (rather than raising) when *s* does not look like a ``§``
    selector, so callers can fall back to the legacy locator forms.
    """
    if s is None:
        return None
    # lawvm-regex: owning_parser canonical §a:b.c.d selector grammar over this parser's own input; unparsed -> None (typed fallback), not a raw_text/IR read
    m = _SECTION_SELECTOR_RE.match(s)
    if m is None:
        return None
    chapter = _norm_label(m.group("chapter")) if m.group("chapter") else None
    section = _norm_label(m.group("section"))
    subsection = _norm_label(m.group("subsection")) if m.group("subsection") else None
    paragraph = _norm_label(m.group("paragraph")) if m.group("paragraph") else None

    parts: list[str] = []
    if chapter is not None:
        parts.append(f"chapter:{chapter}")
    parts.append(f"section:{section}")
    if subsection is not None:
        parts.append(f"subsection:{subsection}")
    if paragraph is not None:
        parts.append(f"paragraph:{paragraph}")
    locator = "/".join(parts)
    return ParsedSelector(
        chapter=chapter,
        section=section,
        subsection=subsection,
        paragraph=paragraph,
        locator=locator,
    )


# Forms that are ALREADY valid locator/eId strings for the existing engines and
# must be passed through untouched.
_LEGACY_LOCATOR_RE = re.compile(r"^[a-z][a-z_]*:", re.IGNORECASE)
_LEGACY_EID_RE = re.compile(r"^(?:part_|chp_|sec_|chapter_).*", re.IGNORECASE)
_BARE_SECTION_LABEL_RE = re.compile(r"^\(?\s*\d+\s*[a-z]?\s*§\s*\)?$", re.IGNORECASE)


def _normalize_locator_segment(segment: str) -> str:
    if ":" not in segment:
        return segment
    kind, label = segment.split(":", 1)
    kind = kind.strip()
    label = label.strip()
    if kind.lower() == "section":
        label = _norm_label(label)
    return f"{kind}:{label}"


def _normalize_legacy_locator(locator: str) -> str:
    return "/".join(_normalize_locator_segment(part) for part in locator.split("/"))


def _bare_section_label_to_locator(value: str) -> str | None:
    if not _BARE_SECTION_LABEL_RE.fullmatch(value):
        return None
    label = value.strip()
    if label.startswith("(") and label.endswith(")"):
        label = label[1:-1].strip()
    label = re.sub(r"\s*§\s*$", "", label).strip()
    normalized = _norm_label(label)
    return f"section:{normalized}" if normalized else None


def to_locator_string(s: str) -> str:
    """Lower any accepted selector form to a legacy locator string.

    Accepts, in priority order:
      1. the canonical ``§a:b.c.d`` form  → lowered to ``chapter:/section:/...``
      2. an existing ``kind:label/...`` locator → normalized for compact section labels
      3. an eId like ``chp_3__sec_1`` → returned unchanged
      4. a bare ``N §`` label → lowered to ``section:N``

    Non-section legacy labels and eIds are returned unchanged, so this is safe
    to call unconditionally before handing a selector to any engine.
    """
    if not s:
        return s
    stripped = s.strip()
    # Already a structured locator (chapter:.., section:.., ...) — pass through.
    # lawvm-regex: owning_parser input-shape discrimination on this module's own selector/locator surface; not an IR/raw_text read
    if _LEGACY_LOCATOR_RE.match(stripped) and "§" not in stripped:
        return _normalize_legacy_locator(stripped)
    # eId form — pass through.
    if "__" in stripped or stripped.lower().startswith(("chp_", "sec_", "part_")):
        return stripped
    parsed = parse_section_selector(stripped)
    if parsed is not None:
        return parsed.locator
    bare_section = _bare_section_label_to_locator(stripped)
    if bare_section is not None:
        return bare_section
    # Anything else — pass through for resolver_raw to handle.
    return stripped


def section_scope_locator(s: str) -> str:
    """Lower a selector to its SECTION-scope locator (drop subsection/paragraph).

    Used where only section-granularity resolution is available (the oracle
    consolidated resolver segments whole <section> elements, not subsections).
    For a canonical ``§3:1.2`` this returns ``chapter:3/section:1``; for an
    already section-scoped or legacy form it returns ``to_locator_string(s)``
    unchanged.
    """
    parsed = parse_section_selector(s.strip()) if s else None
    if parsed is not None:
        parts: list[str] = []
        if parsed.chapter is not None:
            parts.append(f"chapter:{parsed.chapter}")
        parts.append(f"section:{parsed.section}")
        return "/".join(parts)
    # Legacy locator: drop trailing subsection:/paragraph: segments.
    lowered = to_locator_string(s)
    if "/" in lowered and ":" in lowered:
        kept = [
            seg for seg in lowered.split("/")
            if not seg.lower().startswith(("subsection:", "paragraph:", "point:", "item:"))
        ]
        return "/".join(kept) if kept else lowered
    return lowered


def has_subprovision(s: str) -> bool:
    """True if the selector addresses below the section (subsection/paragraph)."""
    parsed = parse_section_selector(s.strip()) if s else None
    if parsed is not None:
        return not parsed.is_section_scope
    lowered = to_locator_string(s)
    return any(
        seg.lower().startswith(("subsection:", "paragraph:", "point:", "item:"))
        for seg in lowered.split("/")
    )
