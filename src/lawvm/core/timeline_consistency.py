"""States-first ingestion and consistency comparison helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.timeline_addresses import _iter_statute_nodes_with_address
from lawvm.core.timeline_results import Timelines
from lawvm.core.timeline_selection import select_active_version_ex


def node_text_differs(a: IRNode, b: IRNode) -> bool:
    """True if two IRNodes have different own text or attrs (ignoring children)."""
    return a.text != b.text or a.attrs != b.attrs


def ingest_uk_snapshots(
    statute_id: str,
    snapshots: dict[str, IRStatute],
) -> Timelines:
    """Build timelines from a collection of dated IRStatute snapshots."""
    if not snapshots:
        return {}

    sorted_dates = sorted(snapshots.keys())
    timelines: Timelines = {}

    earliest_date = sorted_dates[0]
    for address, node in _iter_statute_nodes_with_address(snapshots[earliest_date]):
        tl = ProvisionTimeline(address=address)
        tl.versions.append(
            ProvisionVersion(
                effective=earliest_date,
                enacted=earliest_date,
                content=node,
                content_hash=irnode_content_hash(node),
            )
        )
        timelines[address] = tl

    for i in range(1, len(sorted_dates)):
        date = sorted_dates[i]
        prev_date = sorted_dates[i - 1]

        current_nodes: dict[LegalAddress, IRNode] = dict(_iter_statute_nodes_with_address(snapshots[date]))
        prev_nodes: dict[LegalAddress, IRNode] = dict(_iter_statute_nodes_with_address(snapshots[prev_date]))

        for address, node in current_nodes.items():
            prev = prev_nodes.get(address)
            if prev is None or node_text_differs(prev, node):
                if address not in timelines:
                    timelines[address] = ProvisionTimeline(address=address)
                timelines[address].versions.append(
                    ProvisionVersion(
                        effective=date,
                        enacted=date,
                        content=node,
                        content_hash=irnode_content_hash(node),
                    )
                )

        for address in prev_nodes:
            if address not in current_nodes:
                if address not in timelines:
                    timelines[address] = ProvisionTimeline(address=address)
                timelines[address].versions.append(
                    ProvisionVersion(
                        effective=date,
                        enacted=date,
                        content=None,
                    )
                )

    for tl in timelines.values():
        tl.versions.sort(key=lambda v: (v.effective, v.enacted))

    return timelines


def ingest_consolidated(
    statute: IRStatute,
    as_of: str,
) -> Timelines:
    """Build timelines from a single authoritative consolidated text."""
    timelines: Timelines = {}
    for address, node in _iter_statute_nodes_with_address(statute):
        tl = ProvisionTimeline(address=address)
        tl.versions.append(
            ProvisionVersion(
                effective=as_of,
                enacted=as_of,
                content=node,
                content_hash=irnode_content_hash(node),
            )
        )
        timelines[address] = tl
    return timelines


@dataclass
class ConsistencyDivergence:
    """A divergence between ops-first and consolidated-text timelines at a date."""

    address: LegalAddress
    divergence_type: str
    ops_text: Optional[str]
    consolidated_text: Optional[str]

    def __str__(self) -> str:
        addr_str = "/".join(f"{k}:{v}" for k, v in self.address.path)
        ops_preview = (self.ops_text or "")[:80].replace("\n", " ")
        con_preview = (self.consolidated_text or "")[:80].replace("\n", " ")
        return f"[{self.divergence_type}] {addr_str}\n  ops : {ops_preview!r}\n  con : {con_preview!r}"


def verify_consistency(
    ops_timelines: Timelines,
    consolidated_timelines: Timelines,
    as_of: str,
    irnode_to_text: Callable[[IRNode], str],
    text_normalizer: Optional[Callable[[str], str]] = None,
    missing_equals_empty: bool = False,
) -> list[ConsistencyDivergence]:
    """Compare timelines from two sources at a point in time."""
    all_addresses = set(ops_timelines) | set(consolidated_timelines)
    divergences: list[ConsistencyDivergence] = []

    for address in sorted(all_addresses, key=lambda a: a.path):
        ops_version: Optional[ProvisionVersion] = None
        if address in ops_timelines:
            ops_version = select_active_version_ex(ops_timelines[address], as_of).version

        con_version: Optional[ProvisionVersion] = None
        if address in consolidated_timelines:
            con_version = select_active_version_ex(consolidated_timelines[address], as_of).version

        ops_text: Optional[str] = None
        if ops_version is not None and ops_version.content is not None:
            ops_text = irnode_to_text(ops_version.content).strip()
            if text_normalizer is not None:
                ops_text = text_normalizer(ops_text)

        con_text: Optional[str] = None
        if con_version is not None and con_version.content is not None:
            con_text = irnode_to_text(con_version.content).strip()
            if text_normalizer is not None:
                con_text = text_normalizer(con_text)

        if ops_text == con_text:
            continue

        if missing_equals_empty and ((ops_text is None and con_text == "") or (ops_text == "" and con_text is None)):
            continue

        if ops_text is None and con_text is not None:
            dtype = "OPS_MISSING"
        elif ops_text is not None and con_text is None:
            dtype = "CONSOLIDATED_MISSING"
        else:
            dtype = "MISMATCH"

        divergences.append(
            ConsistencyDivergence(
                address=address,
                divergence_type=dtype,
                ops_text=ops_text,
                consolidated_text=con_text,
            )
        )

    return divergences
