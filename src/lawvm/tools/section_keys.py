"""Shared section-address helpers for replay/oracle comparison tools.

Section numbers are not globally unique inside a Finnish statute. Some statutes
reuse the same section labels across chapters, so section-level comparison must
key provisions by their container path when available.
"""
from __future__ import annotations

import copy
from difflib import SequenceMatcher
import re
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, cast

from lxml import etree

from lawvm.core.timeline import _iter_nodes_with_address
from lawvm.roman import roman_to_arabic


_CONTAINER_KINDS = ("book", "part", "subpart", "title", "subtitle", "chapter")
_KIND_ORDER = {
    "book": 0,
    "part": 1,
    "subpart": 2,
    "title": 3,
    "subtitle": 4,
    "chapter": 5,
    "section": 6,
}


def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def norm_section_label(s: str) -> str:
    stripped = re.sub(r"[\s§.*]", "", s).lower()
    # Normalize Roman numerals to Arabic for consistent comparison
    roman_value = roman_to_arabic(stripped)
    if roman_value is not None:
        return str(roman_value)
    return stripped


def normalize_address_filter(address: str) -> str:
    parts = []
    for chunk in address.split("/"):
        if ":" not in chunk:
            continue
        kind, label = chunk.split(":", 1)
        kind = kind.strip().lower()
        if kind not in (*_CONTAINER_KINDS, "section"):
            continue
        norm = norm_section_label(label.strip())
        if not norm:
            continue
        parts.append(f"{kind}:{norm}")
        if kind == "section":
            break
    return "/".join(parts)


def section_key_from_path(path: Iterable[Tuple[str, str]]) -> str:
    parts = []
    for kind, label in path:
        if kind not in (*_CONTAINER_KINDS, "section"):
            continue
        norm = norm_section_label(label)
        if not norm:
            continue
        parts.append(f"{kind}:{norm}")
        if kind == "section":
            break
    return "/".join(parts)


def _normalize_container_label(kind: str, label: str) -> str:
    if kind == "chapter":
        label = re.sub(r"\s+luku\s*$", "", label, flags=re.IGNORECASE)
    elif kind == "part":
        label = re.sub(r"\s+osa\s*$", "", label, flags=re.IGNORECASE)
    return norm_section_label(label)


def section_key_from_target_dict(target: dict[str, Any]) -> str:
    if target.get("container") != "section":
        return ""
    parts = []
    for kind in _CONTAINER_KINDS:
        label = target.get(kind)
        if label:
            parts.append(f"{kind}:{norm_section_label(str(label))}")
    section = target.get("section")
    if not section:
        return ""
    parts.append(f"section:{norm_section_label(str(section))}")
    return "/".join(parts)


def section_key_from_compiled_scope_row(row: dict[str, Any]) -> str:
    """Return a section key from one flat compiled-op scope row."""
    if str(row.get("target_unit_kind") or "") != "section":
        return ""
    parts = []
    part = row.get("target_part")
    if part:
        parts.append(f"part:{_normalize_container_label('part', str(part))}")
    chapter = row.get("target_chapter")
    if chapter:
        parts.append(f"chapter:{_normalize_container_label('chapter', str(chapter))}")
    section = row.get("target_norm")
    if not section:
        return ""
    parts.append(f"section:{norm_section_label(str(section))}")
    return "/".join(parts)


def section_key_from_compile_failure(failure: Any) -> str:
    parts = []
    chapter = getattr(failure, "target_chapter", "") or ""
    if chapter:
        parts.append(f"chapter:{norm_section_label(str(chapter))}")
    section = getattr(failure, "target_section", "") or ""
    if not section:
        return ""
    parts.append(f"section:{norm_section_label(str(section))}")
    return "/".join(parts)


def section_key_from_target_ref(target: Any) -> str:
    if target is None:
        return ""
    path = getattr(target, "path", None)
    if path:
        return section_key_from_path(path)
    target_str = str(target)
    if "section:" not in target_str:
        return ""
    return normalize_address_filter(target_str)


def extract_ir_sections(root: Any) -> Dict[str, Any]:
    body = root.body if hasattr(root, "body") else root
    sections: Dict[str, Any] = {}
    for address, node in _iter_nodes_with_address(body):
        if not address.path or address.path[-1][0] != "section":
            continue
        # Symmetric with oracle kumottu-stub exclusion: already-repealed placeholder
        # sections carry no live content and should not appear as EXTRA vs oracle.
        if getattr(node, "attrs", {}).get("lawvm_repeal_placeholder") == "1":
            continue
        key = section_key_from_path(address.path)
        if key and key not in sections:
            sections[key] = node
    return sections


_ORACLE_SECTION_STRIP_NAMES = {"noteAuthorial", "signatures", "conclusions", "attachments"}
_ORACLE_VERSION_SUFFIX_RE = re.compile(r"v\d{8}$")
_INLINE_PRIOR_WORDING_RE = re.compile(r"\bAiempi sanamuoto kuuluu\b", re.IGNORECASE)


def _oracle_eid_base(el: etree._Element) -> Optional[str]:
    eid = el.get("eId", "")
    if not eid:
        return None
    return _ORACLE_VERSION_SUFFIX_RE.sub("", eid.split("__")[-1])


def _element_clean_text(el: etree._Element) -> str:
    """Return cleaned alphanumeric-only text content of an element (for comparison)."""
    raw = etree.tostring(el, method="text", encoding="unicode")
    return re.sub(r"[^a-z0-9äöå]", "", raw.lower())


def _dedup_versioned_children(parent: etree._Element, child_tag: str) -> None:
    """Remove duplicate versioned children with the same eId base.

    Finlex consolidated XML sometimes embeds multiple versioned snapshots of the
    same provision (e.g. para_3v20140649 and para_3v20230499 both representing
    item 3). Keep only the first occurrence of each eId base.

    However, Finlex occasionally assigns the same positional slot (eId base) to
    two genuinely distinct provisions:

    Case 1 — different num text: e.g. para_6v20251385 (num="5 a)") and
    para_6v20141432 (num="6)") share the base "para_6". The dedup key
    incorporates the normalised num text to handle this.

    Case 2 — same (empty) num text but genuinely different content: e.g.
    subsec_1v20150795 (original subsection 1) and subsec_1v20240859 (a new
    subsection 2 added by a later amendment). Both are unnumbered and share the
    positional slot, but carry different text bodies.

    Pro Q1: omission is a payload-surface marker. Finlex encodes a new subsection
    with the positional slot of the first subsection when the amendment uses an
    omission marker (eliding prior content). These are genuinely different
    provisions and must both be retained for correct oracle comparison.

    Guard: if two same-key children have substantially different text content
    (< 90% similarity by clean char length heuristic), treat them as distinct
    provisions and preserve both. Only drop a candidate if its content is
    sufficiently similar to the already-seen element (true version duplicate).
    """
    seen: dict[str, etree._Element] = {}
    _FINLEX_ORIG_ATTR = "{http://data.finlex.fi/schema/finlex}originalVersion"
    for child in list(parent):
        if _tag(child) != child_tag:
            continue
        eid_base = _oracle_eid_base(child)
        if not eid_base:
            continue
        num_text = _num_text(child)
        # Qualify the dedup key with the normalised num so that two children
        # that share an eId slot but carry different item numbers (a Finlex
        # insertion artifact) are treated as distinct provisions.
        key = f"{eid_base}\x00{norm_section_label(num_text)}"
        if key in seen:
            existing = seen[key]
            existing_has_orig = bool(existing.get(_FINLEX_ORIG_ATTR))
            candidate_has_orig = bool(child.get(_FINLEX_ORIG_ATTR))
            # Guard: if the candidate has substantially different content from
            # the already-seen element, it is a genuinely new provision (e.g. a
            # new subsection introduced at the same positional slot by a later
            # amendment via omission-elision encoding). Preserve it.
            existing_text = _element_clean_text(existing)
            candidate_text = _element_clean_text(child)
            if existing_text and candidate_text:
                if existing_has_orig != candidate_has_orig:
                    similarity = SequenceMatcher(None, existing_text, candidate_text).ratio()
                    overlaps_as_prior_wording = (
                        existing_text in candidate_text
                        or candidate_text in existing_text
                        or similarity >= 0.55
                    )
                    if overlaps_as_prior_wording:
                        # Finlex can pair one live versioned child with one plain
                        # prior-wording shadow at the same positional slot. Keep the
                        # versioned child only when the texts materially overlap;
                        # otherwise preserve both as distinct live provisions.
                        if existing_has_orig:
                            parent.remove(child)
                            continue
                        existing_parent = existing.getparent()
                        if existing_parent is not None:
                            existing_parent.remove(existing)
                        seen[key] = child
                        continue
                # Finlex can reuse the same positional eId slot for a genuinely
                # different live child (for example an unnumbered subsection
                # that later replaces a different ordinal position in the
                # section). Length ratio alone is too coarse for that family:
                # two different provisions can have similar lengths.
                #
                # Preserve both when the texts are meaningfully dissimilar.
                # Keep the stricter originalVersion-originalVersion drop below
                # for prior-wording editorial shadows.
                shorter = min(len(existing_text), len(candidate_text))
                longer = max(len(existing_text), len(candidate_text))
                similarity = SequenceMatcher(None, existing_text, candidate_text).ratio()
                if shorter / longer < 0.5 or similarity < 0.75:
                    # The texts are substantially different.  Usually this means
                    # a genuinely new provision at the same positional slot, and
                    # we would preserve both.  However, Finlex VÄLIAIKAINEN
                    # display embeds two subsections with the same eId base: the
                    # current VÄLIAIKAINEN wording (originalVersion=@YYYYNNN) and
                    # the prior wording for editorial context (also with
                    # originalVersion but an older amendment number).  Both carry
                    # originalVersion, the texts differ substantially (the
                    # prior wording is a strict prefix of the VÄLIAIKAINEN wording),
                    # and the candidate is editorial display, not a new provision.
                    # Drop it when both elements bear an originalVersion attribute.
                    if not (existing.get(_FINLEX_ORIG_ATTR) and child.get(_FINLEX_ORIG_ATTR)):
                        # Genuinely distinct provision — preserve both.
                        continue
                    # Both have originalVersion: candidate is prior-wording display.
                    # Fall through to parent.remove(child).
            parent.remove(child)
            continue
        seen[key] = child


def _strip_inline_prior_wording_sibling(note: etree._Element) -> None:
    """Drop a same-slot sibling explicitly marked as prior wording by Finlex."""
    if note.get("name") != "noteAuthorial":
        return
    note_text = etree.tostring(note, method="text", encoding="unicode")
    if not _INLINE_PRIOR_WORDING_RE.search(note_text):
        return
    previous = note.getprevious()
    candidate = note.getnext()
    if previous is None or candidate is None:
        return
    if _tag(previous) != _tag(candidate):
        return
    previous_base = _oracle_eid_base(previous)
    candidate_base = _oracle_eid_base(candidate)
    if previous_base is None or previous_base != candidate_base:
        return
    parent = candidate.getparent()
    if parent is not None:
        parent.remove(candidate)


def _normalize_oracle_section(sec: etree._Element) -> etree._Element:
    """Return a cleaned comparison-only clone of one oracle section.

    Finlex sometimes embeds inline noteAuthorial blocks and prior wording as
    versioned sibling subsections inside the current consolidated section.
    Comparison tools should see only the current materialized section.
    """
    # lxml elements are mutable/re-parented; comparison should work on a
    # detached clone so the source cache entry stays untouched.
    clone = copy.deepcopy(sec)

    for el in cast(list[etree._Element], clone.xpath('.//*[local-name()="hcontainer"]')):
        _strip_inline_prior_wording_sibling(el)
        if el.get("name") in _ORACLE_SECTION_STRIP_NAMES:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    _dedup_versioned_children(clone, "subsection")
    for sub in clone.findall("{*}subsection"):
        _dedup_versioned_children(sub, "paragraph")

    return clone


def _is_oracle_version_shadow_section(sec: etree._Element) -> bool:
    """Return True when a section is a Finlex originalVersion shadow copy.

    Finlex consolidated PIT XML can carry both the current section and one or
    more historical shadow sections wrapped in ``finlex:originalVersion`` /
    ``finlex:originalVersionLabel``. Those shadow sections are editorial history,
    not the current consolidated text, and should not count as separate oracle
    provisions in structural comparisons.
    """
    return bool(sec.get("{http://data.finlex.fi/schema/finlex}originalVersion") or sec.get("{http://data.finlex.fi/schema/finlex}originalVersionLabel"))


_KUMOTTU_NOTICE_RE = re.compile(
    r"\d+(?:\s+[a-z])?\s*§\s+on kumottu\b",
    re.IGNORECASE,
)

# Future-repeal overlays say "N § on kumottu ..., joka tulee voimaan DATUM"
# These are editorial notices for a not-yet-effective repeal and must NOT be
# filtered — the oracle still carries the prior wording for comparison.
_FUTURE_REPEAL_RE = re.compile(r"\btulee voimaan\b", re.IGNORECASE)
# Väliaikaisesti (temporary law expiry) tombstone: "N § oli voimassa väliaikaisesti DATES."
# Past-tense "oli" distinguishes expired tombstones from present-tense "on voimassa" notices.
_VALIAIKAISESTI_TOMBSTONE_RE = re.compile(
    r"\d+(?:\s+[a-z])?\s*§\s+oli voimassa väliaikaisesti\b",
    re.IGNORECASE,
)


def _extract_tombstone_content_text(sec: etree._Element) -> Optional[str]:
    """Extract the content text from a potential tombstone section, or None if structure doesn't match.

    A tombstone has exactly one non-``<num>`` child — either a bare
    ``<content>`` element or a single ``<subsection>`` containing only a
    ``<content>`` — with optional ``<p>`` children inside content.
    """
    non_num = [c for c in sec if _tag(c) != "num"]
    if len(non_num) != 1:
        return None
    child = non_num[0]
    child_tag = _tag(child)
    if child_tag == "content":
        candidate = child
    elif child_tag == "subsection":
        # Single-subsection wrapper: <section><num>N §</num><subsection><content>...</content></subsection></section>
        sub_children = list(child)
        if len(sub_children) != 1 or _tag(sub_children[0]) != "content":
            return None
        candidate = sub_children[0]
    else:
        return None
    return etree.tostring(candidate, method="text", encoding="unicode").strip()


def _is_kumottu_notice_section(sec: etree._Element) -> bool:
    """Return True when a section is a Finlex kumottu (repeal) tombstone.

    A kumottu tombstone has exactly one non-``<num>`` child — either a bare
    ``<content>`` element or a single ``<subsection>`` containing only a
    ``<content>`` — whose text matches the standard Finnish repeal notice
    pattern ``N § on kumottu A:lla ...``.

    Letter suffixes may be space-separated (``26 a §``).

    These stubs must be excluded from oracle comparison because a
    correctly-replayed statute also omits expired sections.  Including them
    would produce spurious ``unit_missing_right`` divergences against every
    statute that LawVM correctly expires.
    """
    content_text = _extract_tombstone_content_text(sec)
    if content_text is None:
        return False
    if not _KUMOTTU_NOTICE_RE.search(content_text):
        return False
    # Do not filter future-repeal overlay notices — they are not yet in force
    # and the oracle still carries prior wording for comparison purposes.
    return not _FUTURE_REPEAL_RE.search(content_text)


def _is_valiaikaisesti_tombstone_section(sec: etree._Element) -> bool:
    """Return True when a section is a Finlex väliaikaisesti (temporary law expiry) tombstone.

    Finlex consolidated XML embeds expiry notices for sections that were temporarily
    in force and have since expired:  ``N § oli voimassa väliaikaisesti DATES.``
    These tombstones represent the same expired state as LawVM's chapter-level or
    section-level expiry, which removes the section entirely from the replay.

    The past-tense "oli" distinguishes expired tombstones from present-tense
    "on voimassa väliaikaisesti" notices for still-active temporary provisions.
    """
    content_text = _extract_tombstone_content_text(sec)
    if content_text is None:
        return False
    return bool(_VALIAIKAISESTI_TOMBSTONE_RE.search(content_text))


def extract_oracle_sections(
    root: etree._Element,
    *,
    exclude_kumottu_stubs: bool = True,
    exclude_valiaikaisesti_stubs: bool = True,
) -> Dict[str, etree._Element]:
    sections: Dict[str, etree._Element] = {}
    candidates: Dict[str, list[etree._Element]] = {}
    for sec in cast(list[etree._Element], root.xpath(".//*[local-name()='section']")):
        parts = []
        for anc in reversed(list(sec.iterancestors())):
            tag = _tag(anc)
            if tag not in _CONTAINER_KINDS:
                continue
            num = _num_text(anc)
            if not num:
                continue
            parts.append(f"{tag}:{_normalize_container_label(tag, num)}")
        sec_num = _num_text(sec)
        if not sec_num:
            continue
        # Exclude editorial tombstones from structural comparison — a correctly-
        # replayed statute also omits expired/repealed sections, so including
        # them creates spurious divergences.  Diagnostic callers may opt out:
        #   exclude_kumottu_stubs=False: keep repeal tombstones for EDITORIAL_CONVENTION
        #   exclude_valiaikaisesti_stubs=False: keep temporary-law expiry tombstones
        if exclude_kumottu_stubs and _is_kumottu_notice_section(sec):
            continue
        if exclude_valiaikaisesti_stubs and _is_valiaikaisesti_tombstone_section(sec):
            continue
        parts.append(f"section:{norm_section_label(sec_num)}")
        key = "/".join(parts)
        if not key:
            continue
        candidates.setdefault(key, []).append(sec)

    for key, secs in candidates.items():
        chosen = next((sec for sec in secs if not _is_oracle_version_shadow_section(sec)), secs[0])
        sections[key] = _normalize_oracle_section(chosen)
    return sections


def _clean_section_text(text: str) -> str:
    return re.sub(r"[^a-z0-9äöå]", "", text.lower())


def _section_text(node: Any) -> str:
    if isinstance(node, etree._Element):
        return etree.tostring(node, method="text", encoding="unicode").strip()
    from lawvm.core.ir_helpers import irnode_to_text
    return irnode_to_text(node)


def reconcile_unique_unscoped_aliases(
    replay_sections: Dict[str, Any],
    oracle_sections: Dict[str, Any],
    *,
    text_getter: Optional[Callable[[Any], str]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Align safe scoped-vs-unscoped section aliases between replay and oracle.

    Finlex sometimes nests a section under deeper container paths in the
    consolidated oracle even when the base/source artifact keeps the same
    uniquely-numbered section at body level or with fewer container prefixes.
    Treat these as equivalent only when:
    - the unmatched replay/oracle section labels are unique on both sides
    - one key is a strict suffix of the other at container-path granularity
      (depth mismatch), OR
    - the keys have the same path depth and kind-sequence but differ in
      container label values (e.g. Arabic vs Roman numeral part indices:
      ``part:1/chapter:1/section:1`` vs ``part:i/chapter:1/section:1``).

    Real text differences should remain visible as compared provisions, not
    inflate into paired ``MISSING``/``EXTRA`` noise just because one side
    carries a chapter path and the other does not.
    """
    replay = dict(replay_sections)
    oracle = dict(oracle_sections)

    replay_only = set(replay) - set(oracle)
    oracle_only = set(oracle) - set(replay)
    if not replay_only or not oracle_only:
        return replay, oracle

    replay_by_leaf: Dict[str, list[str]] = {}
    oracle_by_leaf: Dict[str, list[str]] = {}
    for key in replay_only:
        replay_by_leaf.setdefault(leaf_section_label(key), []).append(key)
    for key in oracle_only:
        oracle_by_leaf.setdefault(leaf_section_label(key), []).append(key)

    for leaf in sorted(set(replay_by_leaf) & set(oracle_by_leaf), key=_label_sort_key):
        rkeys = replay_by_leaf[leaf]
        okeys = oracle_by_leaf[leaf]
        if len(rkeys) != 1 or len(okeys) != 1:
            continue
        rkey = rkeys[0]
        okey = okeys[0]
        if rkey == okey:
            continue
        rparts = rkey.split("/")
        oparts = okey.split("/")
        if len(rparts) < len(oparts) and oparts[-len(rparts):] == rparts:
            replay[okey] = replay.pop(rkey)
        elif len(oparts) < len(rparts) and rparts[-len(oparts):] == oparts:
            oracle[rkey] = oracle.pop(okey)
        elif len(rparts) == len(oparts) and _same_kind_sequence(rparts, oparts):
            # Same structural shape but differing container labels (e.g. Arabic vs
            # Roman numeral parts: part:1/chapter:1/section:1 vs
            # part:i/chapter:1/section:1).  The leaf is unique on both sides so
            # it is safe to remap the replay key to the oracle key.
            replay[okey] = replay.pop(rkey)

    return replay, oracle


def _same_kind_sequence(parts_a: list[str], parts_b: list[str]) -> bool:
    """Return True if two split key paths have the same sequence of kinds.

    Used to detect structurally identical paths that differ only in their
    container label values (e.g. Arabic vs Roman numeral part indices).
    """
    if len(parts_a) != len(parts_b):
        return False
    for a, b in zip(parts_a, parts_b):
        ka = a.split(":")[0] if ":" in a else a
        kb = b.split(":")[0] if ":" in b else b
        if ka != kb:
            return False
    return True


def leaf_section_label(key: str) -> str:
    leaf = key.rsplit("/", 1)[-1]
    if leaf.startswith("section:"):
        return leaf[len("section:") :]
    return norm_section_label(leaf)


def section_key_matches_filter(
    key: str,
    address_filter: Optional[Tuple[str, str]],
) -> bool:
    if address_filter is None:
        return True
    kind, value = address_filter
    if kind == "path":
        return key == value
    value_norm = norm_section_label(value)
    if kind == "section":
        return leaf_section_label(key) == value_norm
    return f"{kind}:{value_norm}" in key.split("/")


def _label_sort_key(label: str) -> Tuple[int, str]:
    m = re.match(r"^(\d+)([a-z]*)$", label)
    if m:
        return (int(m.group(1)), m.group(2))
    return (999999, label)


def section_key_sort_key(key: str):
    parts = []
    for chunk in key.split("/"):
        if ":" not in chunk:
            continue
        kind, label = chunk.split(":", 1)
        parts.append((_KIND_ORDER.get(kind, 99), _label_sort_key(label)))
    return tuple(parts) or ((999999, (999999, key)),)


def section_key_sort_text(key: str) -> str:
    """Return a lexicographically sortable serialization of ``section_key_sort_key``.

    SQLite cannot order by the Python tuple returned by :func:`section_key_sort_key`,
    so publication code stores this string form alongside each error row.
    """
    if not key:
        return "~"
    parts: list[str] = []
    for chunk in key.split("/"):
        if ":" not in chunk:
            continue
        kind, label = chunk.split(":", 1)
        kind_rank = _KIND_ORDER.get(kind, 99)
        m = re.match(r"^(\d+)([a-zäöå]*)$", label)
        if m:
            num = int(m.group(1))
            suffix = m.group(2)
            parts.append(f"{kind_rank:02d}:{num:010d}:{suffix}")
        else:
            parts.append(f"{kind_rank:02d}:9999999999:{label.lower()}")
    return "/".join(parts) if parts else "~"


def display_section_key(key: str, el: Optional[etree._Element] = None) -> str:
    if "/" not in key and isinstance(el, etree._Element):
        raw = _num_text(el) or leaf_section_label(key)
        if raw.endswith("§"):
            return raw
        return f"{raw} §" if not raw.startswith("§") else raw
    if "/" not in key:
        return f"{leaf_section_label(key)} §"

    labels = []
    for chunk in key.split("/"):
        if ":" not in chunk:
            continue
        kind, label = chunk.split(":", 1)
        if kind == "chapter":
            labels.append(f"{label} luku")
        elif kind == "section":
            labels.append(f"{label} §")
        else:
            labels.append(f"{kind}:{label}")
    return " / ".join(labels) if labels else key
