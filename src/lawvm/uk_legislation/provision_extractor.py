"""Provision reference parsing and XML extraction for UK legislation sources."""
from __future__ import annotations

import re
from lxml import etree as ET
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, Optional

from lawvm.uk_legislation.xml_helpers import _tag, _text_content


# lxml _Element objects do not support weak references; use a plain dict.
# Eviction is explicit via the compile-loop lifecycle (evict_source_root_caches).
_INSTRUCTION_TEXT_CACHE: dict[ET._Element, str] = {}
_NON_ALNUM_RE = re.compile(r"[^0-9a-zA-Z]")


class UKExtractionContext(NamedTuple):
    parent_map: dict[ET._Element, ET._Element]
    exact_id_map: dict[str, ET._Element]
    sequence_map: dict[tuple[str, ...], ET._Element]


# ---------------------------------------------------------------------------
# §1.11 Hot-path: extraction context cache, built once per source root
#
# Sensor I's cProfile of ukpga/1970/9 showed _build_extraction_context called
# 113 times × 50ms each = 5.72s cumtime.  The function is a pure function of
# root identity: it walks the tree building parent_map, exact_id_map, and
# sequence_map.  The tree is immutable during compilation, so the result can be
# cached for the lifetime of the root ET._Element.
#
# Cache: plain dict keyed on root lxml _Element — lxml elements are not
# weakly-referenceable, so WeakKeyDictionary cannot be used.  Memory safety
# is maintained by explicit eviction via evict_source_root_caches() in the
# compile loop (source_context.py).  The cached UKExtractionContext holds
# _Element objects (via parent_map values) that can include root itself;
# explicit eviction breaks this cycle at compile-loop boundaries.  64 entries
# is enough for the largest UK statutes (ukpga/1970/9 has ~113 distinct
# affecting-act roots but within any single compile call far fewer are
# simultaneously active).
# ---------------------------------------------------------------------------

# lxml _Element objects do not support weak references; use a plain dict.
# Eviction is explicit via evict_source_root_caches() in the compile loop.
_EXTRACTION_CONTEXT_CACHE: dict[ET._Element, UKExtractionContext] = {}


def _instruction_text_before_amendment_container(el: ET._Element) -> str:
    """Collect lead-in text before the first amendment payload container."""
    cached = _INSTRUCTION_TEXT_CACHE.get(el)
    if cached is not None:
        return cached
    parts: list[str] = []
    stopped = False

    def _walk(node: ET._Element) -> None:
        nonlocal stopped
        if stopped:
            return
        if node is not el and _tag(node) in ("BlockAmendment", "InlineAmendment"):
            stopped = True
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            _walk(child)
            if stopped:
                return
            if child.tail:
                parts.append(child.tail)

    _walk(el)
    text = " ".join(" ".join(parts).split())
    _INSTRUCTION_TEXT_CACHE[el] = text
    return text


def _norm_prov_ref(ref: str) -> str:
    """Normalise a provision reference for comparison."""
    return _NON_ALNUM_RE.sub("", ref).lower()


_NUM_ALPHA_RE = re.compile(r"(\d+)([a-z]+)", flags=re.I)
_REF_SPLIT_RE = re.compile(r"[\s.()]+")
_SEQUENCE_KIND_TOKENS = {
    "schedule",
    "part",
    "chapter",
    "section",
    "paragraph",
    "subparagraph",
    "p1",
    "p2",
    "p3",
    "pblock",
    "wrapper",
    "article",
    "rule",
    "regulation",
}
_ROMAN_NUMERAL_LABELS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}
_PROVISION_KIND_SYNONYMS = {
    "schedule": ("schedule", "sched", "schedules"),
    "paragraph": ("p3", "p2", "p1", "paragraph", "para", "p", "listitem"),
    "section": ("section", "p1", "p1group"),
    "regulation": ("regulation", "p1", "p1group"),
    "part": ("part",),
    "chapter": ("pblock", "chapter"),
}


@lru_cache(maxsize=131072)
def _sequence_tokens_cached(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize ID/reference parts while preserving token boundaries."""
    seq_parts: list[str] = []
    for p in parts:
        p_low = p.lower()
        if p_low in _SEQUENCE_KIND_TOKENS:
            seq_parts.append(p_low)
        elif p_low in _ROMAN_NUMERAL_LABELS:
            seq_parts.append(_ROMAN_NUMERAL_LABELS[p_low])
        elif p_low.isascii() and p_low.isdigit():
            seq_parts.append(p_low)
        elif p_low.isascii() and p_low.isalpha():
            seq_parts.append(p_low)
        elif match := _NUM_ALPHA_RE.fullmatch(p_low):
            seq_parts.extend([match.group(1), match.group(2)])
    return tuple(seq_parts)


def _sequence_tokens(parts: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return _sequence_tokens_cached(tuple(parts))


@lru_cache(maxsize=131072)
def _get_id_sequence(eid: str) -> tuple[str, ...]:
    """Extract semantic components with boundary preservation."""
    return _sequence_tokens_cached(tuple(eid.replace("_", "-").split("-")))


@lru_cache(maxsize=131072)
def _get_ref_sequence_cached(path: tuple[tuple[Optional[str], str], ...]) -> tuple[str, ...]:
    parts: list[str] = []
    for kind, label in path:
        if kind:
            parts.append(kind)
        if label:
            parts.append(label)
    return _sequence_tokens_cached(tuple(parts))


def _get_ref_sequence(path: list[tuple[Optional[str], str]] | tuple[tuple[Optional[str], str], ...]) -> tuple[str, ...]:
    return _get_ref_sequence_cached(tuple(path))


def _build_extraction_context(
    root: ET._Element,
) -> UKExtractionContext:
    cached = _EXTRACTION_CONTEXT_CACHE.get(root)
    if cached is not None:
        return cached
    parent_map: dict[ET._Element, ET._Element] = {}
    exact_id_map: dict[str, ET._Element] = {}
    sequence_map: dict[tuple[str, ...], ET._Element] = {}
    for el in root.iter():
        el_id = el.get("id") or el.get("Id")
        if el_id:
            norm_el_id = _norm_prov_ref(el_id)
            if norm_el_id and norm_el_id not in exact_id_map:
                exact_id_map[norm_el_id] = el
            seq = _get_id_sequence(el_id)
            if seq and seq not in sequence_map:
                sequence_map[seq] = el
        for child in el:
            parent_map[child] = el
    ctx = UKExtractionContext(parent_map, exact_id_map, sequence_map)
    _EXTRACTION_CONTEXT_CACHE[root] = ctx
    return ctx


@lru_cache(maxsize=65536)
def _parse_ref(ref: str) -> tuple[tuple[Optional[str], str], ...]:
    """Parse 'Sch. 2 para. 2(2)' into [('schedule', '2'), ('paragraph', '2'), (None, '2')]."""
    r = ref
    r = re.sub(
        r"\b(Sch|paras?|ss?|s|Pt|Ch|arts?|regs?)\.(?=[0-9A-Za-z])",
        r"\1. ",
        r,
        flags=re.I,
    )
    r = re.sub(r"\bSch\.", "schedule", r, flags=re.I)
    r = re.sub(r"\bSch\b", "schedule", r, flags=re.I)
    r = re.sub(r"\bpara\.", "paragraph", r, flags=re.I)
    r = re.sub(r"\bparas\.", "paragraph", r, flags=re.I)
    r = re.sub(r"\bparas?\b", "paragraph", r, flags=re.I)
    r = re.sub(r"\bs\.", "section", r, flags=re.I)
    r = re.sub(r"\bss\.", "section", r, flags=re.I)
    r = re.sub(r"\bPt\.", "part", r, flags=re.I)
    r = re.sub(r"\bPt\b", "part", r, flags=re.I)
    r = re.sub(r"\bCh\.", "chapter", r, flags=re.I)
    r = re.sub(r"\barts\.", "article", r, flags=re.I)
    r = re.sub(r"\bart\.", "article", r, flags=re.I)
    r = re.sub(r"\bregs\.", "regulation", r, flags=re.I)
    r = re.sub(r"\breg\.", "regulation", r, flags=re.I)
    r = re.sub(r"\bannex\b", "schedule", r, flags=re.I)
    r = re.sub(r"\bpoints?\b", "paragraph", r, flags=re.I)

    r = re.sub(r"\bArticle\b", "article", r, flags=re.I)
    r = re.sub(r"\bRule\b", "rule", r, flags=re.I)

    raw_tokens = _REF_SPLIT_RE.split(r)
    raw_tokens = [t.lower() for t in raw_tokens if t]

    kinds = {"schedule", "paragraph", "section", "part", "chapter", "article", "rule", "regulation"}
    _stop = {
        "and",
        "or",
        "of",
        "cross",
        "heading",
        "crossheading",
        "cross-heading",
        "title",
        "sidenote",
        "word",
        "words",
    }
    res = []
    i = 0

    def _normalize_label_token(token: str) -> str:
        match = re.fullmatch(r"0+([0-9]+)([a-z]*)", token, flags=re.I)
        if match is None:
            return token
        return f"{int(match.group(1))}{match.group(2).lower()}"

    while i < len(raw_tokens):
        t = raw_tokens[i]
        if t in _stop:
            i += 1
        elif t in kinds and i + 1 < len(raw_tokens):
            if t == "schedule" and raw_tokens[i + 1] in kinds | _stop:
                res.append((t, ""))
                i += 1
                continue
            res.append((t, _normalize_label_token(raw_tokens[i + 1])))
            i += 2
        elif t in kinds:
            res.append((t, ""))
            i += 1
        else:
            res.append((None, _normalize_label_token(t)))
            i += 1
    return tuple(res)


def _normalized_provision_num(num: str) -> str:
    target_num = _NON_ALNUM_RE.sub("", num).lower()
    return _ROMAN_NUMERAL_LABELS.get(target_num, target_num)


def _node_raw_number_values(el: ET._Element) -> list[str]:
    found_raw_nums = []
    if el.get("Number"):
        found_raw_nums.append(el.get("Number"))
    for child in el:
        ctag = _tag(child).lower()
        if ctag in ("pnumber", "number", "num"):
            raw_text = child.text or ""
            for grandchild in child:
                if grandchild.tail:
                    raw_text += grandchild.tail
            if raw_text.strip():
                found_raw_nums.append(raw_text.strip())
            elif child.text is not None:
                found_raw_nums.append(child.text)
    return found_raw_nums


def _match_node_prepared(
    el: ET._Element,
    *,
    kind_synonyms: Optional[tuple[str, ...]],
    target_num: str,
) -> bool:
    if kind_synonyms is not None and _tag(el).lower() not in kind_synonyms:
        return False

    if not target_num:
        return True

    for raw in _node_raw_number_values(el):
        if _normalized_provision_num(raw) == target_num:
            return True
    return False


def _match_node(el: ET._Element, kind: Optional[str], num: str) -> bool:
    """Check if an element matches a provision kind and/or number."""
    tag = _tag(el).lower()
    if kind:
        synonyms = _PROVISION_KIND_SYNONYMS.get(kind, (kind,))
        if tag not in synonyms:
            return False

    if not num:
        return True

    target_num = _normalized_provision_num(num)
    for raw in _node_raw_number_values(el):
        if _normalized_provision_num(raw) == target_num:
            return True
    return False


def _find_provision_greedy(
    el: ET._Element, path: list[tuple[Optional[str], str]], depth: int = 0
) -> tuple[Optional[ET._Element], int]:
    """Recursively find a provision."""
    best_node = el if depth > 0 else None
    best_depth = depth
    if depth >= len(path):
        return el, depth
    target_kind, target_num = path[depth]
    for child in el:
        if _match_node(child, target_kind, target_num):
            res_node, res_depth = _find_provision_greedy(child, path, depth + 1)
            if res_depth > best_depth:
                best_node = res_node
                best_depth = res_depth
        else:
            res_node, res_depth = _find_provision_greedy(child, path, depth)
            if res_depth > best_depth:
                best_node = res_node
                best_depth = res_depth
    return best_node, best_depth


@lru_cache(maxsize=1024)
def _first_component_number_index(search_root: ET._Element) -> dict[str, tuple[ET._Element, ...]]:
    """Index provision-like elements by normalized visible number in document order."""
    by_num: dict[str, list[ET._Element]] = {}
    for el in search_root.iter():
        if el is search_root:
            continue
        seen_nums: set[str] = set()
        for raw in _node_raw_number_values(el):
            normalized = _normalized_provision_num(raw)
            if not normalized or normalized in seen_nums:
                continue
            seen_nums.add(normalized)
            by_num.setdefault(normalized, []).append(el)
    return {num: tuple(elements) for num, elements in by_num.items()}


@lru_cache(maxsize=32768)
def _first_component_matches(
    search_root: ET._Element,
    target_kind: str,
    target_num: str,
) -> tuple[ET._Element, ...]:
    """Return source nodes matching the first parsed provision component."""
    kind_synonyms = _PROVISION_KIND_SYNONYMS.get(target_kind, (target_kind,))
    normalized_target_num = _normalized_provision_num(target_num)
    return tuple(
        el
        for el in _first_component_number_index(search_root).get(normalized_target_num, ())
        if _match_node_prepared(
            el,
            kind_synonyms=kind_synonyms,
            target_num=normalized_target_num,
        )
    )


def _find_provision_from_search_root(
    search_root: ET._Element,
    path: list[tuple[Optional[str], str]],
) -> tuple[Optional[ET._Element], int]:
    if path:
        target_kind, target_num = path[0]
        if target_kind and target_num:
            matches = _first_component_matches(search_root, target_kind, target_num)
            if not matches:
                return None, 0
            best_node: Optional[ET._Element] = None
            best_depth = 0
            for match in matches:
                candidate_node, candidate_depth = _find_provision_greedy(match, path, 1)
                if candidate_depth > best_depth:
                    best_node = candidate_node
                    best_depth = candidate_depth
            return best_node, best_depth
    return _find_provision_greedy(search_root, path)


def _select_extracted_match(
    el: ET._Element,
    parent_map: Optional[dict[ET._Element, ET._Element]] = None,
) -> ET._Element:
    """Prefer structural amendment containers, not naked inline quote nodes."""
    if _tag(el) in ("BlockAmendment", "InlineAmendment"):
        return el

    if parent_map is not None:
        parent = parent_map.get(el)
        if parent is not None:
            local_text = _text_content(el).strip().lower()
            lead_in_text = _instruction_text_before_amendment_container(el).strip().lower()
            if re.search(r"\bfor\b.+\bsubstitute\b", local_text):
                for child in el.iter():
                    if child is not el and _tag(child) == "BlockAmendment":
                        return el
            if re.search(r"\b(?:insert|substitute)\s*[—-]?\s*$", lead_in_text):
                for child in el.iter():
                    if child is not el and _tag(child) in ("BlockAmendment", "InlineAmendment"):
                        return el
            if re.search(r"\b(?:insert|substitute)\s*[—-]?\s*$", local_text):
                siblings = list(parent)
                try:
                    idx = siblings.index(el)
                except ValueError:
                    idx = -1
                if idx >= 0:
                    for sibling in siblings[idx + 1 :]:
                        sibling_tag = _tag(sibling)
                        if sibling_tag in ("BlockAmendment", "InlineAmendment"):
                            return sibling
                        if sibling_tag in {
                            "P1",
                            "P2",
                            "P3",
                            "P4",
                            "P1group",
                            "Pblock",
                            "Section",
                            "Schedule",
                            "Part",
                            "Chapter",
                            "Article",
                            "Rule",
                            "Subsection",
                        }:
                            break

    for child in el.iter():
        if child is el:
            continue
        if _tag(child) == "BlockAmendment":
            lead_in_text = _instruction_text_before_amendment_container(el) or _instruction_text_before_amendment_container(child)
            if (
                re.search(r"\binsert\s+(?:before|after)\s+[“\"']", lead_in_text, re.I)
                or re.search(r"\bat\s+the\s+appropriate\s+place,?\s+in\s+alphabetical\s+order", lead_in_text, re.I)
            ):
                return el
        if _tag(child) == "BlockAmendment":
            return child

    return el


def extract_provision_element(
    affecting_act_xml: Path,
    provision_ref: str,
) -> Optional[ET._Element]:
    """Extract the provision element from an affecting act's XML."""
    if not affecting_act_xml.exists():
        return None
    try:
        root = ET.parse(affecting_act_xml).getroot()
    except ET.ParseError as exc:
        print(f"  WARN: XML parse error for {affecting_act_xml}: {exc}")
        return None
    parent_map, exact_id_map, sequence_map = _build_extraction_context(root)

    return _extract_provision_element_from_root(
        root,
        provision_ref,
        parent_map=parent_map,
        exact_id_map=exact_id_map,
        sequence_map=sequence_map,
    )


def _extract_provision_element_from_root(
    root: ET._Element,
    provision_ref: str,
    *,
    parent_map: Optional[dict[ET._Element, ET._Element]] = None,
    exact_id_map: Optional[dict[str, ET._Element]] = None,
    sequence_map: Optional[dict[tuple[str, ...], ET._Element]] = None,
) -> Optional[ET._Element]:
    if parent_map is None or exact_id_map is None or sequence_map is None:
        parent_map, exact_id_map, sequence_map = _build_extraction_context(root)

    norm_full = _norm_prov_ref(provision_ref)
    path = _parse_ref(provision_ref)
    if not path:
        return None
    target_sequence = _get_ref_sequence(path)

    exact_match = exact_id_map.get(norm_full)
    if exact_match is not None:
        return _select_extracted_match(exact_match, parent_map)
    if target_sequence:
        seq_match = sequence_map.get(target_sequence)
        if seq_match is not None:
            return _select_extracted_match(seq_match, parent_map)

    body = None
    for el in root.iter():
        if _tag(el).lower() == "body":
            body = el
            break
    search_root = body if body is not None else root
    target_node, depth_reached = _find_provision_from_search_root(search_root, list(path))
    if target_node is not None:
        rem_tokens = [tn for _tk, tn in path[depth_reached:] if tn]
        for child in target_node.iter():
            if _tag(child) in ("BlockAmendment", "InlineAmendment"):
                if rem_tokens:
                    inner_text = _text_content(child)
                    if all(t.lower() in inner_text.lower() for t in rem_tokens):
                        return child
                else:
                    return child
        return _select_extracted_match(target_node, parent_map)
    return None


def extract_provision_element_from_bytes(
    xml_bytes: bytes,
    provision_ref: str,
    *,
    root: Optional[ET._Element] = None,
    parent_map: Optional[dict[ET._Element, ET._Element]] = None,
    exact_id_map: Optional[dict[str, ET._Element]] = None,
    sequence_map: Optional[dict[tuple[str, ...], ET._Element]] = None,
) -> Optional[ET._Element]:
    """Extract a provision element from affecting act XML bytes.

    Archive-backed alternative to extract_provision_element() - accepts bytes
    directly so no temp files are needed. Delegates to the same matching logic
    once the root is parsed.
    """
    if root is None:
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            print(f"  WARN: XML parse error in extract_provision_element_from_bytes: {exc}")
            return None
    return _extract_provision_element_from_root(
        root,
        provision_ref,
        parent_map=parent_map,
        exact_id_map=exact_id_map,
        sequence_map=sequence_map,
    )
