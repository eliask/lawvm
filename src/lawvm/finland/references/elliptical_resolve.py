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
statute's materialized AKN tree and the recognizer's internal mentions (which
carry a ``source_span`` byte offset into the SAME ``xml_bytes`` the tree was
parsed from), it re-derives the two pieces of context resolution needs —

  (i)  the ENCLOSING section of the citing text (the AKN ``<section>`` ancestor
       of the byte span), and
  (ii) the materialized child structure of that section (which moments exist,
       which moments carry kohta) —

and rewrites the bare target onto a concrete section/momentti, OR tags it
``ambiguous`` / ``open`` when convention + structure do not uniquely resolve. It
NEVER silently picks (AGENTS.md §1.1 fail-loud).

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
_SEC_EID_RE = re.compile(r"(?:^|__)sec_([A-Za-z0-9.-]+?)(?:v\d+)?(?=__|$)")


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
    status: EllipticalStatus
    enclosing_section_label: Optional[str] = None
    candidate_subsections: Tuple[int, ...] = ()


def _localname(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _sec_label_from_eid(eid: str) -> Optional[str]:
    """Extract the bare section label (``5``, ``198b``) from a ``…sec_…`` eId."""
    m = _SEC_EID_RE.search(eid)
    return m.group(1) if m is not None else None


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


@dataclass(frozen=True, slots=True)
class _SectionStructure:
    """Materialized child structure of one AKN <section>, by byte extent.

    Attributes:
        section_label:        The bare section label (``5``).
        byte_start/byte_end:  The section's byte extent in ``xml_bytes`` (used to
                              find which section a citation's byte span sits in).
        subsec_nums:          Every momentti number present, ascending.
        subsecs_with_kohta:   The momentti numbers that carry >=1 kohta
                              (<paragraph>), ascending.
    """

    section_label: str
    byte_start: int
    byte_end: int
    subsec_nums: Tuple[int, ...]
    subsecs_with_kohta: Tuple[int, ...]


def _subsec_num_from_eid(eid: str) -> Optional[int]:
    m = re.search(r"(?:^|__)subsec_(\d+)(?:v\d+)?(?=__|$)", eid)
    return int(m.group(1)) if m is not None else None


def build_section_structures(xml_bytes: bytes) -> List[_SectionStructure]:
    """Materialize each <section>'s child structure + byte extent from xml_bytes.

    Uses :class:`ET.iterparse` over the SAME bytes the recognizer was anchored to
    so the byte extents are directly comparable to a mention's ``source_span``.
    For each section we record which moments exist and which carry kohta — the
    structural-uniqueness oracle for bare-kohta resolution. Sections without an
    eId-derivable label are skipped (no label to resolve to).
    """
    out: List[_SectionStructure] = []
    # We parse the whole tree, then locate each section's byte extent by its eId
    # surface in xml_bytes: the ``eId="…"`` attribute string appears verbatim,
    # exactly once, per element, so its first occurrence is that section's open
    # tag — directly comparable to a mention's ``source_span`` byte offset.
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out

    for sec in root.iter():
        if _localname(sec.tag) != _SECTION:
            continue
        eid = sec.get("eId") or ""
        label = _sec_label_from_eid(eid)
        if label is None:
            continue
        # Byte extent: locate the section's eId attribute occurrence in xml_bytes.
        # Each eId is unique, so the first occurrence is THIS section's open tag;
        # the extent runs from that open tag to the next section's eId (or EOF).
        byte_start = _eid_byte_offset(xml_bytes, eid)
        if byte_start < 0:
            continue
        subsec_nums: List[int] = []
        subsecs_with_kohta: List[int] = []
        for sub in sec:
            if _localname(sub.tag) != _SUBSECTION:
                continue
            sub_eid = sub.get("eId") or ""
            num = _subsec_num_from_eid(sub_eid)
            if num is None:
                continue
            subsec_nums.append(num)
            if any(_localname(c.tag) == _PARAGRAPH for c in sub):
                subsecs_with_kohta.append(num)
        out.append(
            _SectionStructure(
                section_label=label,
                byte_start=byte_start,
                byte_end=-1,  # filled below once all starts are known
                subsec_nums=tuple(sorted(set(subsec_nums))),
                subsecs_with_kohta=tuple(sorted(set(subsecs_with_kohta))),
            )
        )

    # Fill byte_end = the next section's byte_start (sections are document-ordered
    # by their open-tag offset); the last runs to EOF.
    out.sort(key=lambda s: s.byte_start)
    filled: List[_SectionStructure] = []
    for i, s in enumerate(out):
        end = out[i + 1].byte_start if i + 1 < len(out) else len(xml_bytes)
        filled.append(dataclasses.replace(s, byte_end=end))
    return filled


def _eid_byte_offset(xml_bytes: bytes, eid: str) -> int:
    """First byte offset of the ``eId="<eid>"`` attribute occurrence in xml_bytes."""
    needle = b'eId="' + eid.encode("utf-8") + b'"'
    return xml_bytes.find(needle)


def _enclosing_section(
    structures: List[_SectionStructure], byte_offset: Optional[int]
) -> Optional[_SectionStructure]:
    """The section whose byte extent contains ``byte_offset`` (None if outside)."""
    if byte_offset is None:
        return None
    for s in structures:
        if s.byte_start <= byte_offset < s.byte_end:
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

    See module docstring for the resolution ladder. The mention's
    ``source_span.byte_offset`` (into the SAME ``xml_bytes`` ``structures`` were
    built from) locates the enclosing section; the section's materialized child
    structure decides convention vs structural-uniqueness vs ambiguity.
    """
    if not _is_internal_bare_ref(mention):
        return EllipticalResolution(mention, EllipticalStatus.NOT_ELLIPTICAL)

    tgt = mention.target_provision_ref
    assert tgt is not None
    byte_offset = (
        mention.source_span.byte_offset if mention.source_span is not None else None
    )
    enclosing = _enclosing_section(structures, byte_offset)

    if enclosing is None:
        # No enclosing section in scope -> OPEN (cannot anchor; never guess).
        return EllipticalResolution(
            _downgrade(mention, CiteConfidence.OPEN),
            EllipticalStatus.OPEN,
        )

    # ── bare momentti (subsection named, section omitted) ──────────────────
    if tgt.subsection_num is not None and tgt.item_label is None:
        # CONVENTION: the bare momentti is a momentti of the enclosing section.
        # If the section actually carries that momentti, resolve; if the section
        # has moments but not THIS one, it is still convention-resolvable to the
        # enclosing section (the momentti number is the surface's; we trust it),
        # so we attach the enclosing section regardless. (We do not invent a
        # momentti the surface did not name.)
        return EllipticalResolution(
            _rewrite_target(
                mention,
                section_label=enclosing.section_label,
                subsection_num=tgt.subsection_num,
            ),
            EllipticalStatus.RESOLVED,
            enclosing_section_label=enclosing.section_label,
        )

    # ── bare kohta (item named, momentti AND section omitted) ──────────────
    if tgt.item_label is not None and tgt.subsection_num is None:
        carriers = enclosing.subsecs_with_kohta
        if len(carriers) == 1:
            # STRUCTURAL UNIQUENESS: exactly one momentti carries kohta -> that
            # momentti is the bare-kohta target's momentti.
            return EllipticalResolution(
                _rewrite_target(
                    mention,
                    section_label=enclosing.section_label,
                    subsection_num=carriers[0],
                ),
                EllipticalStatus.RESOLVED,
                enclosing_section_label=enclosing.section_label,
            )
        # 0 or >1 moments carry kohta -> NOT uniquely resolvable -> AMBIGUOUS
        # (list candidates, never pick).
        return EllipticalResolution(
            _downgrade(mention, CiteConfidence.AMBIGUOUS),
            EllipticalStatus.AMBIGUOUS,
            enclosing_section_label=enclosing.section_label,
            candidate_subsections=carriers,
        )

    # bare kohta that ALSO names its momentti is not elliptical here (the momentti
    # anchors it); attach only the enclosing section by convention.
    if tgt.item_label is not None and tgt.subsection_num is not None:
        return EllipticalResolution(
            _rewrite_target(
                mention,
                section_label=enclosing.section_label,
                subsection_num=tgt.subsection_num,
            ),
            EllipticalStatus.RESOLVED,
            enclosing_section_label=enclosing.section_label,
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
