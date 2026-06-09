"""Finnish AKN section resolver — implements core.locator.SectionResolver.

Finlex consolidated AKN encoding conventions (Finland-specific):

  - eId format: `part_N__chp_N__sec_N`
  - Separator: `__` between hierarchical segments
  - Abbreviations: chapter→chp, section→sec, part→part, subpart→subpart
  - Version suffix: `vYYYYNNNN` (Finlex amendment version), appended either
    to the trailing component (`...sec_3v20230049`) or to interior segments
  - Multiple-version disambiguation: Finlex emits BOTH unversioned and
    versioned section elements when a section has been amended. Latest
    numeric version suffix is the active text at the consolidated version.

Partial-match policy: `chapter:11/section:3` resolves to `part_5__chp_11__sec_3`
when the statute embeds chapters inside parts. Bare `section:N` is NOT
promoted to a suffix match — too loose.

Bare-label fallback: `2 §` matches by walking `<section>` elements and
comparing `<num>` text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lawvm.core.locator import (
    HierarchicalLocator,
    register_section_resolver,
)


_ABBREV = {
    "part": "part",
    "subpart": "subpart",
    "chapter": "chp",
    "section": "sec",
}

_EID_VERSION_TAIL_RE = re.compile(r"(?:v\d{1,10})?(?:__|$)")
_VERSION_NUM_RE = re.compile(r"v(\d{1,10})")


def _normalize_num_label(label: str) -> str:
    return re.sub(r"[\s§.*]", "", label).lower()


def _locator_to_eid_prefix(locator: HierarchicalLocator) -> str | None:
    parts: list[str] = []
    for seg in locator.segments:
        abbrev = _ABBREV.get(seg.kind)
        if abbrev is None:
            return None
        parts.append(f"{abbrev}_{seg.label}")
    return "__".join(parts)


@dataclass(frozen=True, slots=True)
class FinnishAKNResolver:
    """Resolve a HierarchicalLocator against Finlex consolidated AKN XML."""

    def resolve(self, root: Any, locator: HierarchicalLocator) -> Any | None:
        eid_prefix = _locator_to_eid_prefix(locator)
        if eid_prefix is None:
            return None

        exact = root.find(f'.//*[@eId="{eid_prefix}"]')
        if exact is not None:
            # If multiple version variants exist alongside the unversioned
            # eId, pick the highest version among them. Otherwise the bare
            # element is the answer.
            versioned = self._find_versioned_variants(root, eid_prefix)
            if versioned is not None:
                return versioned
            return exact

        versioned = self._find_versioned_variants(root, eid_prefix)
        if versioned is not None:
            return versioned

        # Partial / suffix match: 'chapter:11/section:3' resolves to
        # 'part_X__chp_11__sec_3' when chapters nest in parts.
        if not locator.is_top_level_section:
            el = self._find_suffix_match(root, eid_prefix)
            if el is not None:
                return el

        # Last resort for `section:N` style locators: num-text match against
        # the trailing label. Preserves the legacy behavior where
        # `section:198b` resolves to `<num>198 b §</num>` even though no eId
        # named `sec_198b` exists alongside the lettered form. The caller
        # took responsibility for the segment kind being `section`, so we
        # only do this for the trailing `section:<label>` segment.
        if locator.segments and locator.segments[-1].kind == "section":
            return self._find_by_num_text(root, locator.segments[-1].label)

        return None

    def resolve_raw(self, root: Any, raw_locator: str) -> Any | None:
        # Bare-label fallback: `2 §` matches by num text. This is
        # Finland-specific (the `§` glyph; num-text matching).
        return self._find_by_num_text(root, raw_locator)

    @staticmethod
    def _find_by_num_text(root: Any, label: str) -> Any | None:
        num_text = label
        if "§" not in num_text:
            num_text = num_text + " §"
        wanted = _normalize_num_label(num_text)
        for sec in root.findall(".//{*}section"):
            num_el = sec.find("{*}num")
            if num_el is None:
                num_el = sec.find("num")
            if num_el is not None and num_el.text and _normalize_num_label(num_el.text) == wanted:
                return sec
        return None

    @staticmethod
    def _find_versioned_variants(root: Any, eid_prefix: str) -> Any | None:
        candidates: list[tuple[int, Any]] = []
        for el in root.iter():
            eid = el.get("eId") or ""
            if not eid.startswith(eid_prefix):
                continue
            tail = eid[len(eid_prefix):]
            if not _EID_VERSION_TAIL_RE.fullmatch(tail):
                continue
            if tail == "" or tail == "__":
                # Unversioned variant — handled separately by caller.
                continue
            m = _VERSION_NUM_RE.search(tail)
            candidates.append((int(m.group(1)) if m else -1, el))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])
        return candidates[-1][1]

    @staticmethod
    def _find_suffix_match(root: Any, eid_prefix: str) -> Any | None:
        candidates: list[tuple[int, Any]] = []
        for el in root.iter():
            eid = el.get("eId") or ""
            if eid == eid_prefix:
                return el
            if not eid.endswith(eid_prefix):
                # Allow `__<eid_prefix>` OR `__<eid_prefix>vYYYYNNNN`
                idx = eid.find(eid_prefix)
                if idx < 0:
                    continue
                before = eid[:idx]
                after = eid[idx + len(eid_prefix):]
                if before and not before.endswith("__"):
                    continue
                if not _EID_VERSION_TAIL_RE.fullmatch(after):
                    continue
                tail = after
            else:
                before = eid[: -len(eid_prefix)]
                if not before.endswith("__"):
                    continue
                tail = ""
            m = _VERSION_NUM_RE.search(tail)
            candidates.append((int(m.group(1)) if m else -1, el))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])
        return candidates[-1][1]


def register_finnish_section_resolver() -> None:
    register_section_resolver("fi", FinnishAKNResolver())


register_finnish_section_resolver()
