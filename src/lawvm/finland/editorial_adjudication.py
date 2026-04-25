"""Cross-check Finlex inline repeal stubs against ProvisionTimeline terminator evidence.

Gap 4 of notes/FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md §1.9.

When ``semantic_structure_from_oracle`` detects a Finlex inline repeal stub such
as ``"2 kohta on kumottu A:lla 25.11.2021/1030"``, it strips the stub and emits a
``FINLEX_INLINE_REPEAL_STUB`` observation dict.  That observation carries two
verifiable facts:

- the item slot that was repealed (``target_range``)
- the amendment that performed the repeal (``amendment_id``)

This module cross-checks those claimed facts against ``ProvisionTimeline`` by
looking up the terminator version for each claimed slot and comparing amendment
ids.  Three outcomes are possible:

``editorial_witness_confirmed``
    The stub's claimed amendment id matches the timeline's repeal terminator.
    This is positive secondary evidence: the Finlex editorial layer independently
    agrees with LawVM's compiled lineage.

``editorial_witness_disagrees``
    The amendment ids differ.  This is a real disagreement that deserves manual
    triage: either the stub is wrong (Finlex editorial error) or the timeline is
    wrong (replay bug or missing source data).

``editorial_witness_unresolved``
    No repeal terminator exists in the timeline for the claimed slot.  Either the
    replay engine does not know the slot was repealed (replay bug or missing
    source) or the stub is purely editorial noise with no corresponding replay op.

The cross-check runs after replay (so the compiled timelines are available) but
is independent of any particular display path.  It can be called from structural
review, bench, or any tool that has both oracle XML sections and a ``ReplayResult``.

Timeline lookup design
----------------------
Finland's item-level repeal is typically applied as a SECTION-level REPLACE that
deposits a whole new section content tree, with the repealed item slot carrying a
``lawvm_repeal_placeholder == "1"`` marker.  The ``ProvisionTimeline`` thus has an
entry at the section address carrying the amendment source, but the item slot
itself may NOT have its own timeline entry.

The terminator lookup therefore uses two strategies, tried in order:

1. **Direct slot lookup**: check whether the timeline contains an explicit entry
   for the item address with a repeal-placeholder version.

2. **Ancestor content drill-down**: walk the ancestor addresses (subsection →
   section → …) looking for a timeline version from ANY amendment, then inspect
   that version's content tree for the target slot by label.  Collect all
   amendments that contributed a version containing a repeal placeholder at that
   slot; the latest such amendment is the terminator.

If both strategies agree, the result is authoritative.  If only the ancestor
strategy fires, the result is still actionable but carries the ancestor resolution
caveat implicitly (the caller sees the amendment id either way).

Plumbing note
-------------
``semantic_structure_from_oracle`` emits observations for the immediate children
of the node it is called on.  When it is called on a section element, stubs that
live inside nested subsection elements are stripped but NOT reported to
``_observations_out`` because recursive calls do not thread the out-param.

This module therefore provides two entry points:

``cross_check_stub_observations(observations, timelines)``
    Takes a pre-collected list of stub observation dicts (typically from calling
    ``semantic_structure_from_oracle`` directly on a subsection node with an
    ``_observations_out`` list), plus the compiled timelines dict.

``collect_and_cross_check(oracle_xml_node, timelines, *, jurisdiction="fi")``
    Walks an oracle XML element tree, calls the jurisdiction stub detector on
    every descendant paragraph, collects all stubs, then cross-checks.  This is
    the higher-level convenience entry that tools can call with a section root
    without having to manage ``_observations_out`` plumbing.
"""
from __future__ import annotations

import re
from typing import Any, Optional, TYPE_CHECKING

from lawvm.core.ir import LegalAddress, ProvisionTimeline

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode


# ---------------------------------------------------------------------------
# eId parsing helpers
# ---------------------------------------------------------------------------

# Maps AKN eId component prefixes to LawVM LegalAddress kind strings.
_AKN_KIND_MAP: dict[str, str] = {
    "chp": "chapter",
    "sec": "section",
    "subsec": "subsection",
    # "para" at item depth → handled as "paragraph" for address matching
    # (Finland timeline uses "paragraph" not "item" for kohta slots)
    "para": "paragraph",
}

# AKN component separator.
_AKN_SEP = "__"

# Matches a single AKN component: kind prefix + "_" + label (digits, letters).
_AKN_COMPONENT_RE = re.compile(r'^([a-z]+)_(.+)$')

# Label normalization: strip the versioned suffix (e.g. "2v20211030" → "2").
_VERSIONED_LABEL_RE = re.compile(r'v\d{8}$')


def _eid_parent_path(eid: str) -> tuple[tuple[str, str], ...]:
    """Parse an AKN eId and return the LegalAddress path for the PARENT node.

    For a stub paragraph eId like ``chp_1__sec_3__subsec_1__para_2v20211030``
    the *parent* path is ``(("chapter", "1"), ("section", "3"), ("subsection", "1"))``.

    Rules:
    - Split on ``__``.
    - Drop the last component (that is the stub paragraph itself).
    - For each remaining component, strip any trailing ``v\\d+`` from the label
      (Finlex versioned suffix).
    - Map prefix to LawVM kind using ``_AKN_KIND_MAP``; skip unknown prefixes.

    Returns an empty tuple if parsing fails.
    """
    if not eid:
        return ()
    raw_parts = eid.split(_AKN_SEP)
    # Drop the last component (the para itself).
    parent_parts = raw_parts[:-1]
    path: list[tuple[str, str]] = []
    for part in parent_parts:
        m = _AKN_COMPONENT_RE.match(part)
        if m is None:
            continue
        prefix, raw_label = m.group(1), m.group(2)
        # Strip versioned suffix from label: "2v20211030" → "2"
        label = _VERSIONED_LABEL_RE.sub("", raw_label)
        kind = _AKN_KIND_MAP.get(prefix)
        if kind is None:
            # Unknown prefix — skip; for robustness do not abort the whole parse.
            continue
        path.append((kind, label))
    return tuple(path)


def _slot_addresses_for_stub(
    obs: dict[str, Any],
) -> list[LegalAddress]:
    """Derive the LegalAddress(es) for the paragraph slot(s) claimed by a stub.

    ``obs`` must have keys ``eId`` and ``target_range`` (list[int]).

    The stub's eId encodes the parent hierarchy.  Each ordinal ``n`` in
    ``target_range`` produces one address:
        parent_path + ("paragraph", str(n))

    Finland timelines use ``paragraph`` as the kind for kohta/item-level slots
    (mirroring the ``IRNodeKind.PARAGRAPH`` used in the base IR).

    Returns an empty list if parsing fails or ``target_range`` is empty.
    """
    parent_path = _eid_parent_path(obs.get("eId", ""))
    if not parent_path:
        return []
    target_range: list[int] = obs.get("target_range") or []
    if not target_range:
        return []
    return [
        LegalAddress(path=parent_path + (("paragraph", str(ordinal)),))
        for ordinal in target_range
    ]


# For backward compatibility with unit tests that build "item" addresses directly.
# The cross-check internally resolves via paragraph; callers that have item
# addresses must use _find_repeal_terminator_for_slot directly.
def _item_addresses_for_stub(
    obs: dict[str, Any],
) -> list[LegalAddress]:
    """Deprecated alias: returns paragraph-kind addresses matching Finland timeline keys.

    Kept for unit tests; callers should use ``_slot_addresses_for_stub`` instead.
    """
    return _slot_addresses_for_stub(obs)


# ---------------------------------------------------------------------------
# Timeline lookup helpers
# ---------------------------------------------------------------------------

def _is_repeal_placeholder_node(node: "IRNode | None") -> bool:
    """Return True when *node* carries the repeal-placeholder marker."""
    if node is None:
        return False
    return node.attrs.get("lawvm_repeal_placeholder") == "1"


def _find_slot_in_content(content: "IRNode", target_label: str) -> "IRNode | None":
    """Walk *content* tree (breadth-first) to find the first node labeled *target_label*.

    Looks for ``paragraph``, ``item``, and ``subsection`` kind nodes by label.
    Returns the first matching node, or None.
    """
    from collections import deque

    if content is None:
        return None
    queue: deque["IRNode"] = deque(content.children)
    while queue:
        node = queue.popleft()
        node_kind = str(node.kind).split(".")[-1].lower()
        if node_kind in {"paragraph", "item", "subsection"} and node.label == target_label:
            return node
        # Recurse into subsections to find nested items.
        if node_kind in {"subsection", "section", "chapter"}:
            queue.extend(node.children)
    return None


def _find_repeal_terminator_for_slot(
    target_addr: LegalAddress,
    timelines: dict[LegalAddress, ProvisionTimeline],
) -> Optional[str]:
    """Find the amendment id that repealed the provision at *target_addr*.

    Uses two strategies:

    1. **Direct**: look for *target_addr* in *timelines*; if found, check whether
       any version carries a repeal placeholder.  Return the latest such source.

    2. **Ancestor drill-down**: walk ancestor addresses (removing the rightmost
       path component each time) and look for a timeline version that contains
       a repeal placeholder at the target slot by label.  The target label is
       the leaf component label of *target_addr*.

    Returns ``None`` when neither strategy finds a repeal terminator.
    """
    if not target_addr.path:
        return None

    target_label = target_addr.leaf_label()

    # --- Strategy 1: direct slot timeline ---
    direct_tl = timelines.get(target_addr)
    if direct_tl is not None:
        latest_repeal_src: Optional[str] = None
        for version in direct_tl.versions:
            if _is_repeal_placeholder_node(version.content):
                src = version.source.statute_id if version.source else None
                if src:
                    latest_repeal_src = src  # take latest (versions sorted ascending)
        if latest_repeal_src is not None:
            return latest_repeal_src

    # --- Strategy 2: ancestor content drill-down ---
    # Walk from immediate parent up to root.
    current = target_addr
    while current.parent() is not None:
        ancestor = current.parent()
        assert ancestor is not None  # for type checker
        ancestor_tl = timelines.get(ancestor)
        if ancestor_tl is not None:
            # Scan all versions of this ancestor's timeline for a repeal
            # placeholder at the target slot.  Take the latest amendment id
            # that has a placeholder there.
            latest_repeal_src = None
            for version in ancestor_tl.versions:
                content = version.content
                if content is None:
                    continue
                slot_node = _find_slot_in_content(content, target_label)
                if slot_node is not None and _is_repeal_placeholder_node(slot_node):
                    src = version.source.statute_id if version.source else None
                    if src:
                        latest_repeal_src = src
            if latest_repeal_src is not None:
                return latest_repeal_src
        current = ancestor  # type: ignore[assignment]

    return None


# ---------------------------------------------------------------------------
# Core cross-check logic
# ---------------------------------------------------------------------------

def cross_check_stub_observations(
    observations: list[dict[str, Any]],
    timelines: Optional[dict[LegalAddress, ProvisionTimeline]],
) -> list[dict[str, Any]]:
    """Cross-check a list of ``FINLEX_INLINE_REPEAL_STUB`` observations against timelines.

    Parameters
    ----------
    observations:
        List of observation dicts as emitted by
        ``semantic_structure_from_oracle`` into ``_observations_out``.
        Non-stub observations are silently ignored (passthrough).
    timelines:
        The compiled provision timelines from ``ReplayResult.timelines``.
        If ``None`` or empty, all stubs produce ``editorial_witness_unresolved``.

    Returns
    -------
    A list of evidence record dicts.  Each record has:

    ``kind``:
        One of ``"editorial_witness_confirmed"``,
        ``"editorial_witness_disagrees"``,
        ``"editorial_witness_unresolved"``.
    ``slot_address``:
        String representation of the LegalAddress of the claimed item slot.
    ``amendment_id``:
        Amendment id claimed by the stub (e.g. ``"2021/1030"``).
    ``timeline_terminator`` (disagrees/unresolved only):
        The amendment id that the timeline records as terminator, or ``None``
        when no repeal terminator is in the timeline.
    """
    evidence: list[dict[str, Any]] = []
    if timelines is None:
        timelines = {}

    for obs in observations:
        if obs.get("kind") != "FINLEX_INLINE_REPEAL_STUB":
            continue
        claimed_amendment = obs.get("amendment_id")
        if not claimed_amendment:
            # Cannot verify without a claimed id; treat as unresolved.
            for addr in _slot_addresses_for_stub(obs):
                evidence.append({
                    "kind": "editorial_witness_unresolved",
                    "slot_address": str(addr),
                    "amendment_id": None,
                    "timeline_terminator": None,
                })
            continue

        addresses = _slot_addresses_for_stub(obs)
        if not addresses:
            # Cannot derive address from eId — emit unresolved with no slot.
            evidence.append({
                "kind": "editorial_witness_unresolved",
                "slot_address": "",
                "amendment_id": claimed_amendment,
                "timeline_terminator": None,
            })
            continue

        for addr in addresses:
            terminator = _find_repeal_terminator_for_slot(addr, timelines)
            slot_str = str(addr)
            if terminator is None:
                evidence.append({
                    "kind": "editorial_witness_unresolved",
                    "slot_address": slot_str,
                    "amendment_id": claimed_amendment,
                    "timeline_terminator": None,
                })
            elif terminator == claimed_amendment:
                evidence.append({
                    "kind": "editorial_witness_confirmed",
                    "slot_address": slot_str,
                    "amendment_id": claimed_amendment,
                })
            else:
                evidence.append({
                    "kind": "editorial_witness_disagrees",
                    "severity": "REQUIRES_TRIAGE",
                    "slot_address": slot_str,
                    "amendment_id": claimed_amendment,
                    "timeline_terminator": terminator,
                })

    return evidence


# ---------------------------------------------------------------------------
# Convenience entry: walk oracle XML tree to collect + cross-check
# ---------------------------------------------------------------------------

def collect_and_cross_check(
    oracle_xml_node: Any,
    timelines: dict[LegalAddress, ProvisionTimeline],
    *,
    jurisdiction: str = "fi",
) -> list[dict[str, Any]]:
    """Walk *oracle_xml_node* for inline repeal stubs and cross-check against timelines.

    This is the high-level entry point for tools that have an oracle XML element
    (section or subsection root) and a ``ReplayResult``.  It handles the
    ``_observations_out`` plumbing internally.

    Parameters
    ----------
    oracle_xml_node:
        An ``lxml.etree._Element``.  The function walks ALL descendants, not
        just direct children, so callers can pass a section root and stubs
        inside subsections will still be found.
    timelines:
        The compiled provision timelines from ``ReplayResult.timelines``.
    jurisdiction:
        Jurisdiction key to use for the stub detector (default ``"fi"``).

    Returns
    -------
    A list of evidence record dicts (same shape as
    ``cross_check_stub_observations``).
    """
    from lxml import etree
    # Ensure the Finland stub detector is registered before dispatching.
    import lawvm.finland.inline_repeal_stub as _  # noqa: F401
    from lawvm.semantic.projection import _detect_inline_repeal_stub

    if not isinstance(oracle_xml_node, etree._Element):
        return []

    observations: list[dict[str, Any]] = []
    for descendant in oracle_xml_node.iter():
        obs = _detect_inline_repeal_stub(descendant, jurisdiction)
        if obs is not None:
            observations.append(obs)

    return cross_check_stub_observations(observations, timelines)
