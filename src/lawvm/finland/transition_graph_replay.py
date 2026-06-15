"""Finnish replay adapter for transition-graph exports."""
from __future__ import annotations

from typing import Any

from lawvm.tools.export_transition_graph import (
    ReplayBundle,
    compute_change_dates,
)
from lawvm.tools.transition_graph_profile import TransitionGraphExportProfile


def run_fi_transition_graph_replay(
    statute_id_yearnum: str,
    *,
    profile: TransitionGraphExportProfile,
) -> ReplayBundle:
    """Run the Finnish engine once and capture L2 ops plus timelines."""
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml

    lo_ops: list[Any] = []
    replay_findings: list[Any] = []
    failed_ops: list[Any] = []
    source_pathologies: list[Any] = []
    far_result = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(
            parent_id=statute_id_yearnum,
            mode="legal_pit",
            as_of="9999-12-31",
            quiet=True,
        ),
        sinks=ReplayXmlSinks(
            lo_ops_out=lo_ops,
            findings_out=replay_findings,
            failed_ops_out=failed_ops,
            source_pathologies_out=source_pathologies,
        ),
    )
    timelines = far_result.timelines or {}
    change_dates = compute_change_dates(timelines, profile=profile)
    return ReplayBundle(
        statute_id=profile.canonical_statute_id(statute_id_yearnum),
        engine_id=statute_id_yearnum,
        title=far_result.title,
        result=far_result,
        lo_ops=lo_ops,
        timelines=timelines,
        change_dates=change_dates,
        replay_findings=replay_findings,
        failed_ops=failed_ops,
        source_pathologies=source_pathologies,
    )


def materialize_fi_transition_graph_tree(bundle: ReplayBundle, as_of: str):
    """Materialize the Finnish replay products at one point in time."""
    from lawvm.finland.replay_products import build_replay_products

    result = bundle.result
    products = build_replay_products(
        ctx=result.ctx,
        statute_id=bundle.engine_id,
        replay_fold_state=result.products.replay_fold_state,
        lo_ops_out=bundle.lo_ops,
        as_of=as_of,
        expires_as_of=as_of,
        synthesize_repeal_placeholders=True,
        temporal_events=result.products.temporal_events,
        migration_events=result.products.migration_events,
    )
    return products.materialized_state.ir
