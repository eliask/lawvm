"""Canonical forward-grant → ``DelegationEdge`` adapter (StatuteGraph source).

Lowers the canonical token-native forward-grant parser
(:func:`lawvm.finland.legal_surface.delegation_canonical.parse_delegation_grants`)
to the exact :class:`lawvm.finland.delegation.DelegationEdge` shape the production
StatuteGraph builder consumes (``statute_id`` / ``section`` / ``eid`` /
``delegation_type`` / ``match_text`` / ``quote``). It is the canonical-parser
replacement for the legacy regex forward extractor
:func:`lawvm.finland.delegation.extract_delegations` as the StatuteGraph
forward-grant source.

Edge-key stability (the load-bearing invariant of the flip)
-----------------------------------------------------------
A ``DelegationEdge`` is keyed in the graph on ``(statute_id, section,
delegation_type, eid, match_text, quote)`` (``core.graph._delegation_sort_key``).
The ``section`` / ``eid`` keys are PROVISION-ADDRESS facts read off the Akoma
Ntoso ``section`` / ``subsection`` markup, NOT off the clause text. To keep those
keys byte-identical across the flip, this adapter walks the SAME scan units the
regex extractor walks — section, then subsection when present, else section — and
runs the canonical parser on each unit's normalized text. The ``section`` /
``eid`` of each emitted edge is therefore the address of the scan unit the grant
was found in, exactly as the regex extractor assigned it.

The canonical parser is token-native and parses ONE unit's text at a time (the
same per-unit invocation the LSG ``delegated_instrument`` lens uses), so the
clause windows, instrument anchors, holder binding and over-recognition guards
are identical to the production LSG ``delegation_frame`` / ``delegated_instrument``
nodes. This adapter is the SINGLE place the canonical grants are lowered to the
StatuteGraph edge vocabulary.

``delegation_type`` mapping
---------------------------
The canonical ``DelegationGrant.kind`` is ALREADY in the production
``delegation_type`` vocabulary (``VN_ASETUS`` / ``MIN_ASETUS`` / ``PRES_ASETUS`` /
``AGENCY`` / ``ASETUS`` — see ``delegation_canonical.KIND_*`` and
``delegation._classify_delegation_type``), so ``delegation_type = grant.kind``
with no remap. The canonical issuer classifier (``_classify_kind``) mirrors the
regex classifier's precedence (valtioneuvoston→VN, ministeriön→MIN,
presidentin→PRES, määräys/ohje→AGENCY, else generic ASETUS).

``match_text`` / ``quote``
--------------------------
``match_text`` is the canonical grant's whole-frame (clause) surface — the
token-aligned construction span, the canonical analogue of the regex match span.
``quote`` is the unit text head (first 500 chars), identical to the regex
extractor's ``quote`` convention so the surrounding-context field is unchanged.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from lawvm.finland.delegation import (
    NS,
    DelegationDiagnostic,
    DelegationEdge,
    _elem_text_norm,
    _record_parse_failure,
    _section_num,
)
from lawvm.finland.legal_surface.delegation_canonical import (
    DelegationGrantScan,
    parse_delegation_grants,
)
from lawvm.core.quirks_disposition import QuirksDisposition


def _scan_units(
    root: ET.Element[str],
) -> List[tuple[ET.Element[str], str, str]]:
    """The (element, section_num, eid) scan units — IDENTICAL walk to the regex.

    Mirrors :func:`lawvm.finland.delegation.extract_delegations`: prefer the
    finest-grained subsection unit for precise addressing, fall back to the
    section, and to the bare body when no sections/articles are present. The
    ``section`` / ``eid`` this yields per unit ARE the regex extractor's edge keys,
    so an edge built from a unit here is key-comparable to the regex edge from the
    same unit.
    """
    units: List[tuple[ET.Element[str], str, str]] = []
    sections = root.findall(f".//{NS}section") + root.findall(f".//{NS}article")
    if not sections:
        body = root.find(f".//{NS}body")
        if body is not None:
            units.append((body, "", ""))
        return units
    for sec in sections:
        sec_num = _section_num(sec)
        sec_eid = sec.get("eId", "")
        subsections = sec.findall(f"{NS}subsection")
        if subsections:
            for ss in subsections:
                ss_eid = ss.get("eId", "") or sec_eid
                units.append((ss, sec_num, ss_eid))
        else:
            units.append((sec, sec_num, sec_eid))
    return units


def extract_delegations_canonical(
    xml_bytes: bytes,
    statute_id: str,
    *,
    diagnostics_out: Optional[list[DelegationDiagnostic]] = None,
) -> List[DelegationEdge]:
    """Canonical forward-grant extractor producing production ``DelegationEdge``s.

    Drop-in replacement for :func:`lawvm.finland.delegation.extract_delegations`
    as the StatuteGraph forward-grant source. Walks the same Akoma Ntoso scan
    units (so ``section`` / ``eid`` edge keys are stable), runs the canonical
    token-native parser on each unit's normalized text, and lowers each
    :class:`~lawvm.finland.legal_surface.delegation_canonical.DelegationGrant` to a
    :class:`~lawvm.finland.delegation.DelegationEdge`.

    Args:
        xml_bytes:  Raw consolidated statute XML (Akoma Ntoso / Finlex format).
        statute_id: Canonical statute ID, e.g. ``"2011/646"``.
        diagnostics_out: Optional sink for typed parse-failure diagnostics
            (mirrors the regex extractor's diagnostics contract).

    Returns:
        List of :class:`DelegationEdge`, one per recognized forward grant.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        _record_parse_failure(
            diagnostics_out,
            statute_id=statute_id,
            phase="delegation_extraction",
        )
        return []

    edges: List[DelegationEdge] = []
    for elem, sec_num, unit_eid in _scan_units(root):
        unit_text = _elem_text_norm(elem)
        if not unit_text:
            continue
        scan = parse_delegation_grants(unit_text)
        for grant in scan.grants:
            match_text = unit_text[grant.frame_start : grant.frame_end].strip()
            edges.append(
                DelegationEdge(
                    statute_id=statute_id,
                    section=sec_num,
                    eid=unit_eid,
                    # grant.kind is already the production delegation_type vocab.
                    delegation_type=grant.kind,
                    match_text=match_text,
                    quote=unit_text[:500],
                )
            )
        # No-silent-drop carry-through: the canonical parser's typed residuals
        # (grant-SHAPED clauses it SEES but declines to emit — self-/cross-
        # references, postposition complements, anaphors, AGENCY false-positive
        # shapes, …) hold totality INSIDE the parser but are invisible at this
        # production boundary unless lifted out. When a diagnostics sink is
        # provided, record each residual as a typed ``graph_edge_filter``
        # diagnostic carrying the verbatim offending clause text (self-evidencing,
        # mirroring the legacy regex extractor's declined-candidate diagnostics)
        # so the residual is observable/countable, never silently discarded. The
        # residual is NOT lowered to an edge — it remains a residual.
        _record_residuals(
            diagnostics_out,
            scan=scan,
            statute_id=statute_id,
            section=sec_num,
            eid=unit_eid,
            unit_text=unit_text,
        )
    return edges


def _record_residuals(
    diagnostics_out: Optional[list[DelegationDiagnostic]],
    *,
    scan: DelegationGrantScan,
    statute_id: str,
    section: str,
    eid: str,
    unit_text: str,
) -> None:
    """Surface canonical typed residuals as observable typed diagnostics.

    Each :class:`GrantResidual` becomes a non-blocking ``graph_edge_filter``
    :class:`DelegationDiagnostic` whose ``rule_id`` carries the closed residual
    class and whose ``match_text`` embeds the verbatim declined clause text. No
    edge is fabricated — the residual stays a residual, merely made countable.
    """
    if diagnostics_out is None:
        return
    for residual in scan.residuals:
        diagnostics_out.append(
            DelegationDiagnostic(
                rule_id=f"fi_delegation_canonical_residual_{residual.kind}",
                family="graph_edge_filter",
                phase="delegation_extraction",
                source_statute_id=statute_id,
                reason=(
                    "Canonical forward-grant parser declined a grant-shaped clause "
                    f"as typed residue ({residual.kind})."
                ),
                section=section,
                eid=eid,
                match_text=residual.surface_text,
                quote=unit_text[:500],
                blocking=False,
                strict_disposition="record",
                quirks_disposition=QuirksDisposition.RECORD,
            )
        )
