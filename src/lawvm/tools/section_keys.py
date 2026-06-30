"""Shared section-address helpers for replay/oracle comparison tools.

Section numbers are not globally unique inside a Finnish statute. Some statutes
reuse the same section labels across chapters, so section-level comparison must
key provisions by their container path when available.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, cast

from lxml import etree

from lawvm.core.timeline import _iter_nodes_with_address
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.oracle_versioned_children import (
    _sequence_ratio_at_least,
    dedup_versioned_children as _dedup_versioned_children,
    strip_prior_wording_sibling,
)


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
    return _norm_num_token(s.replace("*", ""))


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
        label = re.sub(r"\s+(?:osa|osasto)\s*$", "", label, flags=re.IGNORECASE)
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


def chapter_key_from_compiled_scope_row(row: dict[str, Any]) -> str:
    """Return a chapter scope key from one flat compiled-op row."""
    if str(row.get("target_unit_kind") or "") != "chapter":
        return ""
    part = row.get("target_part")
    chapter = row.get("target_norm") or row.get("target_chapter")
    if not chapter:
        return ""
    parts = []
    if part:
        parts.append(f"part:{_normalize_container_label('part', str(part))}")
    parts.append(f"chapter:{_normalize_container_label('chapter', str(chapter))}")
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
# Also treat bare <block name="noteAuthorial"> (and authorialNote elems) as
# editorial notes to strip in oracle section clones for comparison. Finlex
# oracle XML uses block (with outline="huomautus") in addition to hcontainer.
_ORACLE_NOTE_BLOCK_TAGS = {"hcontainer", "block"}
_INLINE_PRIOR_WORDING_RE = re.compile(r"\bAiempi sanamuoto kuuluu\b", re.IGNORECASE)
_SECTION_EID_VERSION_RE = re.compile(r"(?:^|__)sec_[^_]*?v(?P<version>\d{1,10})(?:__|$)")


def _strip_inline_prior_wording_sibling(note: etree._Element) -> None:
    """Drop a same-slot sibling explicitly marked as prior wording by Finlex."""
    strip_prior_wording_sibling(note)


def _normalize_oracle_section(sec: etree._Element) -> etree._Element:
    """Return a cleaned comparison-only clone of one oracle section.

    Finlex sometimes embeds inline noteAuthorial blocks and prior wording as
    versioned sibling subsections inside the current consolidated section.
    Comparison tools should see only the current materialized section.
    """
    # lxml elements are mutable/re-parented; comparison should work on a
    # detached clone so the source cache entry stays untouched.
    clone = copy.deepcopy(sec)

    # Strip noteAuthorial etc from hcontainer *and* block (Finlex oracle uses
    # <block name="noteAuthorial" outline="huomautus"> for authorial notes).
    for tag in _ORACLE_NOTE_BLOCK_TAGS:
        for el in cast(list[etree._Element], clone.xpath(f'.//*[local-name()="{tag}"]')):
            _strip_inline_prior_wording_sibling(el)
            if el.get("name") in _ORACLE_SECTION_STRIP_NAMES:
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)

    # Strip any inline <authorialNote> elements that may be present in the
    # oracle section content (editorial/corrigendum residue).
    for note in cast(list[etree._Element], clone.xpath('.//*[local-name()="authorialNote"]')):
        parent = note.getparent()
        if parent is not None:
            parent.remove(note)

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


def _oracle_section_eid_version(sec: etree._Element) -> int:
    eid = sec.get("eId", "")
    matches = list(_SECTION_EID_VERSION_RE.finditer(eid))
    if not matches:
        return -1
    return int(matches[-1].group("version"))


def _is_future_repeal_overlay_section(sec: etree._Element) -> bool:
    """Return True for Finlex future-repeal overlays that retain prior wording.

    These sections are not ordinary expired tombstones: the source explicitly
    says the repeal comes into force later and carries "Aiempi sanamuoto
    kuuluu" so the previous wording can still be displayed.  They therefore
    must remain visible to diagnostic callers, but they must not displace the
    same-address live wording candidate during current-section selection.
    """
    content_text = _extract_tombstone_content_text(sec)
    if content_text is None:
        return False
    return bool(
        _KUMOTTU_NOTICE_RE.search(content_text)
        and _FUTURE_REPEAL_RE.search(content_text)
        and _INLINE_PRIOR_WORDING_RE.search(content_text)
    )


def _choose_oracle_section_candidate(secs: list[etree._Element]) -> etree._Element:
    """Choose the current Finlex section among same-address candidates.

    Finlex consolidated XML may carry both an unversioned section slot and one
    or more section-level ``...sec_NvYYYYNNNN`` variants. For Finnish AKN, the
    registered section resolver treats the highest section-level versioned eId
    as the active text for that slot. Structural comparison must use the same
    rule; otherwise it can compare replay against stale unversioned shells while
    point lookups resolve to the correct current section.
    """
    versioned: list[tuple[int, int, etree._Element]] = []
    for index, sec in enumerate(secs):
        if _is_future_repeal_overlay_section(sec):
            continue
        version = _oracle_section_eid_version(sec)
        if version >= 0:
            versioned.append((version, index, sec))
    if versioned:
        return max(versioned, key=lambda item: (item[0], item[1]))[2]
    return next((sec for sec in secs if not _is_oracle_version_shadow_section(sec)), secs[0])


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


def _single_future_repeal_overlay_versions(root: etree._Element) -> set[int]:
    version_counts: dict[int, int] = {}
    for sec in cast(list[etree._Element], root.xpath(".//*[local-name()='section']")):
        if not _is_future_repeal_overlay_section(sec):
            continue
        version = _oracle_section_eid_version(sec)
        if version >= 0:
            version_counts[version] = version_counts.get(version, 0) + 1
    return {version for version, count in version_counts.items() if count == 1}


def _group_oracle_section_candidates(
    root: etree._Element,
    *,
    exclude_kumottu_stubs: bool = True,
    exclude_valiaikaisesti_stubs: bool = True,
) -> Dict[str, list[etree._Element]]:
    """Group oracle ``<section>`` elements by their container-path section key.

    Shared by :func:`extract_oracle_sections` (which picks one candidate per key)
    and :func:`extract_oracle_section_alternates` (which keeps the discarded
    same-key siblings as ``amb`` candidates), so both see the IDENTICAL key space.
    """
    candidates: Dict[str, list[etree._Element]] = {}
    single_future_repeal_overlay_versions = (
        _single_future_repeal_overlay_versions(root) if exclude_kumottu_stubs else set()
    )
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
        version = _oracle_section_eid_version(sec)
        if (
            version in single_future_repeal_overlay_versions
            and not _is_future_repeal_overlay_section(sec)
        ):
            continue
        parts.append(f"section:{norm_section_label(sec_num)}")
        key = "/".join(parts)
        if not key:
            continue
        candidates.setdefault(key, []).append(sec)
    return candidates


def extract_oracle_sections(
    root: etree._Element,
    *,
    exclude_kumottu_stubs: bool = True,
    exclude_valiaikaisesti_stubs: bool = True,
) -> Dict[str, etree._Element]:
    candidates = _group_oracle_section_candidates(
        root,
        exclude_kumottu_stubs=exclude_kumottu_stubs,
        exclude_valiaikaisesti_stubs=exclude_valiaikaisesti_stubs,
    )
    sections: Dict[str, etree._Element] = {}
    for key, secs in candidates.items():
        chosen = _choose_oracle_section_candidate(secs)
        sections[key] = _normalize_oracle_section(chosen)
    return sections


@dataclass(frozen=True)
class OracleAmbAlternate:
    """One non-chosen oracle version rendering of a section slot.

    ``version`` is the eId version int (``sec_NvYYYYNNNN`` -> ``YYYYNNNN``), or
    ``-1`` for an unversioned candidate. ``text`` is the normalized plain text of
    that alternate section (same ``_normalize_oracle_section`` the chosen
    candidate goes through), left UNCLEANED — the bench applies its own oracle
    text cleaner so the comparison is identical to the chosen-candidate path.
    """

    version: int
    text: str


@dataclass(frozen=True)
class OracleAmbCandidates:
    """The chosen version label plus the discarded same-slot alternates."""

    chosen_version: int
    alternates: Tuple[OracleAmbAlternate, ...]


# Bound on alternates retained per section key (fails safe to "penalized" beyond).
_MAX_AMB_ALTERNATES_PER_SECTION = 8


def extract_oracle_section_alternates(
    root: etree._Element,
    *,
    exclude_kumottu_stubs: bool = True,
    exclude_valiaikaisesti_stubs: bool = True,
) -> Dict[str, OracleAmbCandidates]:
    """Per section key, the oracle version renderings ``extract_oracle_sections``
    DISCARDED — the ``amb`` (nondeterministic) candidate set.

    v1 captures SECTION-level alternates: when a slot carries several
    ``<section>`` versions (e.g. ``sec_6v20260143`` + ``sec_6v20250029``),
    ``_choose_oracle_section_candidate`` keeps the highest and this returns the
    rest. The bench uses them to forgive a replay whose text matches a
    genuine-but-not-chosen oracle version (the oracle's version SELECTION is
    unreliable) — neutralizing the penalty and emitting a warning, never masking
    fabricated text (exact same-slot text equality only).

    TODO(child-level): subsection/paragraph version shadows that
    ``_dedup_versioned_children`` drops inside the chosen section are NOT yet
    surfaced here; such cases fail safe to "penalized". See
    ``tests/test_fi_oracle_amb_match.py`` xfail.
    """
    candidates = _group_oracle_section_candidates(
        root,
        exclude_kumottu_stubs=exclude_kumottu_stubs,
        exclude_valiaikaisesti_stubs=exclude_valiaikaisesti_stubs,
    )
    out: Dict[str, OracleAmbCandidates] = {}
    for key, secs in candidates.items():
        if len(secs) < 2:
            continue  # single candidate — no alternates to forgive against
        chosen = _choose_oracle_section_candidate(secs)
        seen_text: set[str] = set()
        alts: list[OracleAmbAlternate] = []
        for sec in secs:
            if sec is chosen:
                continue
            text = _section_text(_normalize_oracle_section(sec))
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            alts.append(OracleAmbAlternate(version=_oracle_section_eid_version(sec), text=text))
            if len(alts) >= _MAX_AMB_ALTERNATES_PER_SECTION:
                break
        if alts:
            out[key] = OracleAmbCandidates(
                chosen_version=_oracle_section_eid_version(chosen),
                alternates=tuple(alts),
            )
    return out


def oracle_amb_alternate_match(
    section_key: str,
    replay_text_clean: str,
    candidates: Optional[OracleAmbCandidates],
    oracle_clean: Callable[[str], str],
) -> Optional[str]:
    """``amb`` match: a witness string iff replay's text equals a NON-chosen
    oracle version of THIS EXACT slot, else None.

    ``replay_text_clean`` is the replay section text already run through the
    bench replay cleaner; ``oracle_clean`` is the bench oracle text cleaner,
    applied here to each alternate so the comparison is byte-identical to the
    chosen-candidate text comparison. Match is EXACT equality, never a similarity
    ratio — so it forgives only the oracle's version SELECTION, never fabricated
    or near-miss replay text. Same-slot by construction (``candidates`` were
    grouped under ``section_key``); it can never neutralize a cross-provision
    divergence.
    """
    if candidates is None or not replay_text_clean:
        return None
    for alt in candidates.alternates:
        if oracle_clean(alt.text) == replay_text_clean:
            return (
                f"oracle_version_selection_alternate_match key={section_key} "
                f"matched=@{alt.version} chosen=@{candidates.chosen_version}"
            )
    return None


def _clean_section_text(text: str) -> str:
    return re.sub(r"[^a-z0-9äöå]", "", text.lower())


def _section_text(node: Any) -> str:
    if isinstance(node, str):
        return node
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
    Treat these as equivalent only when the unmatched replay/oracle section
    labels are unique on both sides AND one of:
    - one key is a strict suffix of the other at container-path granularity
      (depth mismatch — one side simply omits a leading container prefix), OR
    - the keys have the same path depth and kind-sequence but differ in a
      container label value (e.g. a section nested under a different chapter/part
      on each side) AND the two nodes carry near-identical provision text.

    The text-similarity gate on the same-depth case is load-bearing: a
    whole-section repeal that leaves the oracle holding a bare
    ``N § on kumottu`` stub while a same-numbered LIVE section survives in another
    chapter must NOT be aliased.  The stub and the live body share no text, so
    the gate rejects the pairing — preventing the surviving section from being
    fused onto the repeal stub and manufacturing a spurious ``REPLAY_UNREPEALED``.
    Genuinely relocated provisions keep their body, so they still pair.

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
            # Same path depth and kind-sequence but a differing container label
            # (e.g. a section nested under a different chapter/part on each side).
            # Only alias when the two nodes carry essentially the same provision
            # body: a genuinely relocated section keeps its text, so pairing it
            # across the label difference removes spurious MISSING/EXTRA noise.
            #
            # The discriminator must reject the repeal-stub trap: a whole-section
            # repeal that leaves the oracle holding a bare "N § on kumottu" stub
            # while a same-numbered LIVE section survives in another chapter must
            # NOT be aliased, or the surviving section is fused onto the repeal
            # stub and manufactures a spurious REPLAY_UNREPEALED.  Repeal stubs are
            # tiny ("14 §" → 2 chars; "4 a § on kumottu L:lla …" → ~27 chars);
            # relocated provisions are substantial bodies.  Alias when the bodies
            # are near-identical (handles verbatim relocations of any length), or
            # when BOTH bodies are substantial and reasonably similar (handles a
            # relocation that was also amended) — never when either side is a
            # stub-sized fragment.
            getter = text_getter or _section_text
            r_text = _clean_section_text(getter(replay[rkey]))
            o_text = _clean_section_text(getter(oracle[okey]))
            if r_text and o_text:
                both_substantial = min(len(r_text), len(o_text)) >= 40
                threshold = 0.6 if both_substantial else 0.9
                if _sequence_ratio_at_least(r_text, o_text, threshold):
                    replay[okey] = replay.pop(rkey)

    return replay, oracle


def _same_kind_sequence(parts_a: list[str], parts_b: list[str]) -> bool:
    """Return True if two split key paths have the same sequence of kinds.

    Used to detect structurally identical paths that differ only in their
    container label values.
    """
    if len(parts_a) != len(parts_b):
        return False
    for a, b in zip(parts_a, parts_b, strict=True):
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
