"""Elliptical / anaphoric INTERNAL-reference resolution against the statute tree.

The internal-reference recognizer (``references/internal_refs.py``) emits bare
momentti / kohta references whose target carries the precision the SURFACE names
but leaves the part the surface OMITS empty:

  * bare momentti ``Edellä 2 momentissa``  -> ``subsection_num=2``,
    ``section_label=""``    (the ENCLOSING section is omitted by drafting
    convention — "the 2nd momentti of the section being read");
  * bare kohta ``Edellä 1 kohdassa``       -> ``item_label="1"``,
    ``subsection_num=None``, ``section_label=""`` (BOTH the enclosing section AND
    the momentti are omitted — "the 1st kohta of the momentti that HAS kohta").

A target with an empty ``section_label`` cannot be located: the AKN section
resolver builds the eId prefix from the segments, and a ``subsection``/``item``
segment with no ``section`` anchor has no ``sec_N`` to hang on, so the locator
fails to resolve and the viewer falls back to the whole-statute ROOT — the wrong
target. Resolving the omitted part is LawVM's job, NOT the viewer's; the viewer
renders only the LawVM-resolved target.

This module is a PURE downstream projection (it edits no recognizer): given the
statute's materialized AKN tree and the recognizer's internal mentions, it uses
the two pieces of context resolution needs —

  (i)  the ENCLOSING section of the citing text — read DIRECTLY from the
       mention's ``source_provision_ref.section_label``, which the extractor
       threaded on from the real ``<section>`` ancestry of the citing ``<p>``
       (see ``ref_mention_extractor._enclosing_section_labels``); and
  (ii) the materialized child structure of that section (which moments exist,
       which moments carry kohta), keyed by that section label —

and rewrites the bare target onto a concrete section/momentti, OR tags it
``ambiguous`` / ``open`` when convention + structure do not uniquely resolve. It
NEVER silently picks (AGENTS.md §1.1 fail-loud).

The enclosing section is authoritative AKN ancestry, NOT a byte-offset remap:
old Finlex consolidations carry ``<section>`` elements with no ``eId`` (so an
eId-keyed byte extent map finds zero sections and every bare ref falls to OPEN);
the ancestry-threaded label resolves them via the section's own ``<num>`` surface
(``10 §.`` -> ``10``).

Two disambiguation bases (BOTH in play, per the operator):

  (a) drafting CONVENTION — a bare momentti names a momentti of the ENCLOSING
      section. Given the enclosing section, ``subsection_num=N`` resolves to that
      section's momentti N. Deterministic from context alone.
  (b) ENACTMENT/RESOLVE-TIME STRUCTURAL UNIQUENESS — a bare kohta names the kohta
      of the momentti that HAS kohta. Within a section, momentti 1 may carry many
      kohta while other moments carry NONE; the bare-kohta target is the
      momentti-with-kohta, and it is unique ONLY when exactly one momentti of the
      enclosing section carries kohta. We consult the ACTUAL materialized tree —
      we do not guess "first".

Resolution ladder (per mention):

  * RESOLVED  — convention + structure uniquely fix section (and, for bare kohta,
                the momentti): the target is rewritten with the concrete
                ``section_label`` (and ``subsection_num`` for bare kohta).
  * AMBIGUOUS — the enclosing section cannot be identified, or (bare kohta) more
                than one momentti of the enclosing section carries kohta: the
                candidates are listed, none picked.
  * OPEN      — no enclosing section at all (the citation sits outside any
                ``<section>``): tagged, target left unresolved.

The OUTPUT for the AMBIGUOUS / OPEN cases is a re-typed mention whose
``cite_confidence`` is downgraded (AMBIGUOUS / UNRESOLVED), so the H1 / anaphora
lenses transcribe the fail-loud verdict onto the graph unchanged. A RESOLVED
mention keeps EXACT confidence with the now-complete provision path.
"""
from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ReferenceMention,
)

# AKN namespace-agnostic local-name helpers.
_SECTION = "section"
_SUBSECTION = "subsection"
_PARAGRAPH = "paragraph"

# Pull the bare section label out of a ``…sec_<label>…`` AKN eId fragment.
# The label run is bounded and GREEDY (no lazy/optional overlap — §1.11): an
# optional trailing ``v<digits>`` version tag is split off in :func:`_sec_label_from_eid`
# rather than modeled as an adjacent optional group, which the regex risk linter
# (rightly) flags as overlapping-backtracking.
_SEC_EID_RE = re.compile(r"(?:^|__)sec_([A-Za-z0-9.-]{1,32})(?=__|$)")
# Trailing AKN version tag (``…v3``) appended to a section label segment.
_SEC_VERSION_SUFFIX_RE = re.compile(r"v\d+$")


class EllipticalStatus(Enum):
    """Resolution outcome for one elliptical internal reference."""

    RESOLVED = "resolved"
    """Convention + structure uniquely fix the omitted address part."""

    AMBIGUOUS = "ambiguous"
    """Enclosing section unknown, or >1 momentti carries kohta (bare kohta)."""

    OPEN = "open"
    """No enclosing section at all — the citation sits outside any <section>."""

    NOT_ELLIPTICAL = "not_elliptical"
    """The mention names its section already (or is not an internal bare ref)."""


@dataclass(frozen=True, slots=True)
class EllipticalResolution:
    """One internal mention's elliptical resolution against the statute tree.

    Attributes:
        mention:    The (possibly rewritten) mention. RESOLVED -> a NEW mention
                    whose target carries the concrete section (and momentti for
                    bare kohta); AMBIGUOUS / OPEN -> a NEW mention with the
                    cite_confidence downgraded; NOT_ELLIPTICAL -> the input
                    mention, unchanged.
        status:     The resolution outcome.
        enclosing_section_label: The section the citing text sits in, when found.
        candidate_subsections:   For an AMBIGUOUS bare kohta, the moments that
                    carry kohta (the tie); empty otherwise.
    """

    mention: ReferenceMention
    elliptical_status: EllipticalStatus
    enclosing_section_label: Optional[str] = None
    candidate_subsections: Tuple[int, ...] = ()


def _localname(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _sec_label_from_eid(eid: str) -> Optional[str]:
    """Extract the bare section label (``5``, ``198b``) from a ``…sec_…`` eId.

    Strips a trailing AKN version tag (``sec_5v3`` -> ``5``) after the bounded
    greedy label match, so the section-label key is version-agnostic.
    """
    m = _SEC_EID_RE.search(eid)
    if m is None:
        return None
    return _SEC_VERSION_SUFFIX_RE.sub("", m.group(1))


def _is_internal_bare_ref(mention: ReferenceMention) -> bool:
    """True iff ``mention`` is an INTERNAL ref whose section is omitted.

    A bare momentti / bare kohta: INTERNAL cite_kind, a target that carries a
    subsection or item but NO section_label and NO ``__``-shaped AKN
    provision_path (a chapter-qualified path already names its scope).
    """
    if mention.cite_kind is not CiteKind.INTERNAL:
        return False
    tgt = mention.target_provision_ref
    if tgt is None:
        return False
    if tgt.section_label:
        return False
    if "__" in (tgt.provision_path or ""):
        return False
    return tgt.subsection_num is not None or tgt.item_label is not None


# Section label from a ``<section><num>…</num>`` surface, used when the section
# carries no eId (pre-eId Finlex consolidations): ``10 §.`` -> ``10``,
# ``115 a §`` -> ``115a``. Mirrors ``ref_mention_extractor._SECTION_NUM_LABEL_RE``
# so the threaded source-ref label and the structure-oracle label agree.
_SECTION_NUM_LABEL_RE = re.compile(r"(\d{1,6})\s*([a-zA-Z\xe4\xf6\xc4\xd6])?")


@dataclass(frozen=True, slots=True)
class _SectionStructure:
    """Materialized child structure of one AKN <section>, keyed by label.

    Attributes:
        section_label:        The bare section label (``5``, ``10``, ``115a``).
        subsec_nums:          Every momentti number present, ascending.
        subsecs_with_kohta:   The momentti numbers that carry >=1 kohta
                              (<paragraph>), ascending.

    The structure is the per-section oracle the bare-kohta rule consults; the
    enclosing section itself is identified by the mention's threaded
    ``source_provision_ref.section_label`` (real AKN ancestry), so no byte extent
    is needed here.
    """

    section_label: str
    subsec_nums: Tuple[int, ...]
    subsecs_with_kohta: Tuple[int, ...]


def _subsec_num_from_eid(eid: str) -> Optional[int]:
    m = re.search(r"(?:^|__)subsec_(\d+)(?:v\d+)?(?=__|$)", eid)
    return int(m.group(1)) if m is not None else None


def _section_label_of(sec: ET.Element[str]) -> Optional[str]:
    """Bare label of a ``<section>`` from its eId, else its ``<num>`` surface.

    Returns ``None`` when neither yields a label (the section then has no key to
    resolve against).
    """
    eid = sec.get("eId") or ""
    if eid:
        label = _sec_label_from_eid(eid)
        if label is not None:
            return label
    for child in sec:
        if _localname(child.tag) == "num":
            nm = _SECTION_NUM_LABEL_RE.match((child.text or "").strip())
            if nm is not None:
                return nm.group(1) + (nm.group(2) or "").lower()
            break
    return None


def build_section_structures(xml_bytes: bytes) -> List[_SectionStructure]:
    """Materialize each <section>'s child structure, keyed by section label.

    For each section we record which moments exist and which carry kohta — the
    structural-uniqueness oracle for bare-kohta resolution. The section label is
    eId-derived where present and falls back to the section's ``<num>`` surface
    (pre-eId consolidations), so old statutes whose sections carry no eId still
    populate the oracle. Momentti numbers come from the subsection eId where
    present, else from 1-based document position (old statutes whose subsections
    carry neither eId nor ``<num>``); kohta-carriers are the moments holding a
    ``<paragraph>``. Sections with no derivable label are skipped.
    """
    out: List[_SectionStructure] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out

    for sec in root.iter():
        if _localname(sec.tag) != _SECTION:
            continue
        label = _section_label_of(sec)
        if label is None:
            continue
        subsec_nums: List[int] = []
        subsecs_with_kohta: List[int] = []
        position = 0
        for sub in sec:
            if _localname(sub.tag) != _SUBSECTION:
                continue
            position += 1
            # Prefer the eId-derived momentti number; fall back to 1-based
            # document position when the subsection carries no eId (old
            # consolidations), so a bare ``N momentissa`` and the kohta-carrier
            # index live in the same ordinal space.
            num = _subsec_num_from_eid(sub.get("eId") or "")
            if num is None:
                num = position
            subsec_nums.append(num)
            if any(_localname(c.tag) == _PARAGRAPH for c in sub):
                subsecs_with_kohta.append(num)
        out.append(
            _SectionStructure(
                section_label=label,
                subsec_nums=tuple(sorted(set(subsec_nums))),
                subsecs_with_kohta=tuple(sorted(set(subsecs_with_kohta))),
            )
        )

    return out


def _enclosing_label(mention: ReferenceMention) -> str:
    """The enclosing-section label threaded onto the mention's source provenance.

    The extractor sets ``source_provision_ref.section_label`` to the real
    ``<section>`` ancestor of the citing ``<p>`` (see
    ``ref_mention_extractor._enclosing_section_labels``). An empty string means
    the citation sits outside any labeled section (OPEN downstream).
    """
    src = mention.source_provision_ref
    return src.section_label if src is not None else ""


def _structure_for_label(
    structures: List[_SectionStructure], label: str
) -> Optional[_SectionStructure]:
    """The materialized structure of the section with ``label`` (None if absent)."""
    if not label:
        return None
    for s in structures:
        if s.section_label == label:
            return s
    return None


def _rewrite_target(
    mention: ReferenceMention,
    *,
    section_label: str,
    subsection_num: Optional[int],
) -> ReferenceMention:
    """Return a NEW mention whose target carries the concrete section/momentti."""
    tgt = mention.target_provision_ref
    assert tgt is not None
    changes: dict[str, object] = {"section_label": section_label}
    if subsection_num is not None:
        changes["subsection_num"] = subsection_num
    new_target = dataclasses.replace(tgt, **changes)
    return dataclasses.replace(
        mention,
        target_provision_ref=new_target,
        cite_confidence=CiteConfidence.EXACT,
    )


def _downgrade(
    mention: ReferenceMention, confidence: CiteConfidence
) -> ReferenceMention:
    """Return a NEW mention with the cite_confidence downgraded (fail-loud)."""
    return dataclasses.replace(mention, cite_confidence=confidence)


def resolve_elliptical_mention(
    mention: ReferenceMention,
    structures: List[_SectionStructure],
) -> EllipticalResolution:
    """Resolve one internal mention's omitted section/momentti against the tree.

    See module docstring for the resolution ladder. The enclosing section is read
    DIRECTLY from the mention's ``source_provision_ref.section_label`` (threaded by
    the extractor from the citing ``<p>``'s real ``<section>`` ancestry); the
    section's materialized child structure decides convention vs
    structural-uniqueness vs ambiguity for the bare-kohta case.
    """
    if not _is_internal_bare_ref(mention):
        return EllipticalResolution(mention, EllipticalStatus.NOT_ELLIPTICAL)

    tgt = mention.target_provision_ref
    assert tgt is not None
    enclosing_label = _enclosing_label(mention)

    if not enclosing_label:
        # No enclosing section in scope -> OPEN (cannot anchor; never guess).
        return EllipticalResolution(
            _downgrade(mention, CiteConfidence.OPEN),
            EllipticalStatus.OPEN,
        )

    enclosing = _structure_for_label(structures, enclosing_label)

    # ── bare momentti (subsection named, section omitted) ──────────────────
    if tgt.subsection_num is not None and tgt.item_label is None:
        # CONVENTION: the bare momentti is a momentti of the enclosing section.
        # The momentti number is the surface's; we trust it and attach the
        # enclosing section regardless of whether the section's materialized
        # structure happens to enumerate that exact momentti number (old
        # consolidations carry no subsection numbering). We do not invent a
        # momentti the surface did not name.
        return EllipticalResolution(
            _rewrite_target(
                mention,
                section_label=enclosing_label,
                subsection_num=tgt.subsection_num,
            ),
            EllipticalStatus.RESOLVED,
            enclosing_section_label=enclosing_label,
        )

    # ── bare kohta (item named, momentti AND section omitted) ──────────────
    if tgt.item_label is not None and tgt.subsection_num is None:
        carriers = enclosing.subsecs_with_kohta if enclosing is not None else ()
        if len(carriers) == 1:
            # STRUCTURAL UNIQUENESS: exactly one momentti carries kohta -> that
            # momentti is the bare-kohta target's momentti.
            return EllipticalResolution(
                _rewrite_target(
                    mention,
                    section_label=enclosing_label,
                    subsection_num=carriers[0],
                ),
                EllipticalStatus.RESOLVED,
                enclosing_section_label=enclosing_label,
            )
        # 0 or >1 moments carry kohta -> NOT uniquely resolvable -> AMBIGUOUS
        # (list candidates, never pick).
        return EllipticalResolution(
            _downgrade(mention, CiteConfidence.AMBIGUOUS),
            EllipticalStatus.AMBIGUOUS,
            enclosing_section_label=enclosing_label,
            candidate_subsections=carriers,
        )

    # bare kohta that ALSO names its momentti is not elliptical here (the momentti
    # anchors it); attach only the enclosing section by convention.
    if tgt.item_label is not None and tgt.subsection_num is not None:
        return EllipticalResolution(
            _rewrite_target(
                mention,
                section_label=enclosing_label,
                subsection_num=tgt.subsection_num,
            ),
            EllipticalStatus.RESOLVED,
            enclosing_section_label=enclosing_label,
        )

    return EllipticalResolution(mention, EllipticalStatus.NOT_ELLIPTICAL)


def resolve_elliptical_mentions(
    mentions: List[ReferenceMention],
    xml_bytes: bytes,
) -> List[EllipticalResolution]:
    """Resolve every internal bare ref in ``mentions`` against the statute tree.

    Builds the per-section structural oracle ONCE from ``xml_bytes`` and applies
    :func:`resolve_elliptical_mention` per mention, in input order. Non-internal /
    already-anchored mentions pass through as ``NOT_ELLIPTICAL`` unchanged.
    """
    structures = build_section_structures(xml_bytes)
    return [resolve_elliptical_mention(m, structures) for m in mentions]


__all__ = [
    "EllipticalResolution",
    "EllipticalStatus",
    "build_section_structures",
    "resolve_elliptical_mention",
    "resolve_elliptical_mentions",
]
