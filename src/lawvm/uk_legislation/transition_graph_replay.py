"""UK replay adapter for transition-graph exports.

The Python engine is the only authority. ``run_uk_transition_graph_replay``
compiles the UK amendment program once from the archived enacted base + effects,
and ``materialize_uk_transition_graph_tree`` re-materialises the point-in-time
tree at each change-date by applying the date-filtered op subset to the enacted
base. Materialisation is **pure source replay** (no oracle/EID alignment): the
certified tree is exactly what the engine derives from the enacted text plus the
compiled amendment operations, so the viewer's self-verification against the
engine checkpoint stays internally consistent and never silently borrows EIDs
from the current legislation.gov.uk projection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.core.ir import IRNode, IRStatute
from lawvm.tools.export_transition_graph import ReplayBundle
from lawvm.tools.transition_graph_profile import TransitionGraphExportProfile

_REPO_ROOT = Path(__file__).resolve().parents[3]  # LawVM/
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_LEG_BASE = "https://www.legislation.gov.uk"
_TITLE_RE = re.compile(rb"<dc:title>([^<]+)</dc:title>")
_ENACTMENT_DATE_RE = re.compile(
    rb"<ukm:EnactmentDate\b[^>]*\bDate=\"([0-9]{4}-[0-9]{2}-[0-9]{2})\""
)


@dataclass(frozen=True)
class _UKTransitionGraphReplayState:
    """Captured once-compiled UK replay program, re-applied per change-date."""

    base_ir: IRStatute
    ops: tuple[Any, ...]
    enacted_date: str


def _op_effective(op: Any) -> str:
    src = getattr(op, "source", None)
    return (getattr(src, "effective", "") if src is not None else "") or ""


def _statute_root_node(statute: IRStatute) -> IRNode:
    """Wrap body sections + schedule supplements under one addressable root.

    ``covering_units``/``_iter_addressed_nodes`` tile ``root.children``; UK
    amendments target both the body and the schedules, so both must live under
    the exported root or schedule changes would silently vanish.
    """
    children = (*statute.body.children, *statute.supplements)
    return IRNode(kind=statute.body.kind, label=None, text="", children=children)


def _resolve_title(base_ir: IRStatute, enacted_bytes: bytes, statute_id: str) -> str:
    title = (base_ir.title or "").strip()
    if title:
        return title
    match = _TITLE_RE.search(enacted_bytes)
    if match:
        return match.group(1).decode("utf-8", "ignore").strip() or statute_id
    return statute_id


def _resolve_enacted_date(enacted_bytes: bytes, statute_id: str) -> str:
    match = _ENACTMENT_DATE_RE.search(enacted_bytes)
    if match:
        return match.group(1).decode("ascii")
    parts = statute_id.split("/")
    year = parts[-2] if len(parts) >= 3 else "1900"
    return f"{year}-01-01"


def run_uk_transition_graph_replay(
    statute_id: str,
    *,
    profile: TransitionGraphExportProfile,
) -> ReplayBundle:
    """Compile the UK amendment program once and capture the replay state."""
    from farchive import Farchive

    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.enacted_base_loader import load_enacted_base

    enacted_url = f"{_LEG_BASE}/{statute_id}/enacted/data.xml"
    with Farchive(_DEFAULT_DB, readonly=True) as archive:
        enacted_bytes = archive.get(enacted_url)
        if enacted_bytes is None:
            raise FileNotFoundError(
                f"enacted XML missing from archive for {enacted_url}; "
                f"run `lawvm uk-acquire {statute_id}` first"
            )
        # PDF-only Acts carry a NumberOfProvisions="0" stub whose XML body is
        # empty; the shared loader substitutes the PDF replay base for those,
        # and is byte-identical to parse_uk_statute_ir_bytes for real XML bodies.
        base_ir = load_enacted_base(
            statute_id,
            enacted_bytes,
            archive,
            version_label="enacted",
            source_path=enacted_url,
        ).base_ir
        pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
        )

    title = _resolve_title(base_ir, enacted_bytes, statute_id)
    enacted_date = _resolve_enacted_date(enacted_bytes, statute_id)
    effective_dates = {_op_effective(op) for op in ops if _op_effective(op)}
    change_dates = sorted({enacted_date, *effective_dates})

    state = _UKTransitionGraphReplayState(
        base_ir=base_ir,
        ops=tuple(ops),
        enacted_date=enacted_date,
    )
    return ReplayBundle(
        statute_id=profile.canonical_statute_id(statute_id),
        engine_id=statute_id,
        title=title,
        result=state,
        lo_ops=list(ops),
        timelines={},
        change_dates=change_dates,
        replay_findings=[],
        failed_ops=[],
        source_pathologies=[],
    )


def materialize_uk_transition_graph_tree(bundle: ReplayBundle, as_of: str) -> IRNode:
    """Materialise the UK statute as of ``as_of`` by pure source replay."""
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module

    state = bundle.result
    assert isinstance(state, _UKTransitionGraphReplayState)
    # Only ops with a RESOLVED effective date are placed on the timeline. An op
    # with no effective date cannot be honestly materialised at any point — and
    # the change-date axis (built from non-empty effective dates) already ignores
    # them, so including them here would mis-place an undated amendment at every
    # date (e.g. an undated whole-schedule text_repeal would flatten the
    # structured schedule from date 0 onward). Drop them rather than guess.
    ops_in_force = [op for op in state.ops if (eff := _op_effective(op)) and eff <= as_of]
    pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
    replayed = pipeline.apply_ops(
        state.base_ir,
        ops_in_force,
        eid_map=None,
        text_map=None,
        allow_oracle_alignment=False,
    )
    return _statute_root_node(replayed)
