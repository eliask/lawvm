"""Jurisdiction adapter registry for transition-graph exports."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lawvm.tools.transition_graph_interlinks import LawvmInterlinkExportProvider
from lawvm.tools.transition_graph_overlays import LawvmSurfaceOverlayExportProvider
from lawvm.tools.transition_graph_profile import TransitionGraphExportProfile

TransitionGraphReplayRunner = Callable[..., Any]
TransitionGraphTreeMaterializer = Callable[[Any, str], Any]


@dataclass(frozen=True, slots=True)
class TransitionGraphJurisdictionAdapter:
    profile: TransitionGraphExportProfile
    replay_runner: TransitionGraphReplayRunner
    tree_materializer: TransitionGraphTreeMaterializer
    interlink_provider: LawvmInterlinkExportProvider | None = None
    overlay_provider: LawvmSurfaceOverlayExportProvider | None = None


def transition_graph_adapter_for_jurisdiction(
    jurisdiction: str,
) -> TransitionGraphJurisdictionAdapter:
    code = str(jurisdiction or "").strip().lower()
    if code == "fi":
        from lawvm.finland.interlink_targets import (
            fi_transition_graph_interlink_provider,
            fi_transition_graph_overlay_provider,
        )
        from lawvm.finland.transition_graph_profile import finland_transition_graph_export_profile
        from lawvm.finland.transition_graph_replay import (
            materialize_fi_transition_graph_tree,
            run_fi_transition_graph_replay,
        )

        return TransitionGraphJurisdictionAdapter(
            profile=finland_transition_graph_export_profile(),
            replay_runner=run_fi_transition_graph_replay,
            tree_materializer=materialize_fi_transition_graph_tree,
            interlink_provider=fi_transition_graph_interlink_provider(),
            overlay_provider=fi_transition_graph_overlay_provider(),
        )
    if code == "uk":
        from lawvm.uk_legislation.transition_graph_profile import (
            uk_transition_graph_export_profile,
        )
        from lawvm.uk_legislation.transition_graph_replay import (
            materialize_uk_transition_graph_tree,
            run_uk_transition_graph_replay,
        )

        return TransitionGraphJurisdictionAdapter(
            profile=uk_transition_graph_export_profile(),
            replay_runner=run_uk_transition_graph_replay,
            tree_materializer=materialize_uk_transition_graph_tree,
            interlink_provider=None,
        )
    supported = ", ".join(supported_transition_graph_jurisdictions())
    raise ValueError(
        f"transition graph export is not implemented for jurisdiction {jurisdiction!r}; "
        f"supported: {supported}"
    )


def supported_transition_graph_jurisdictions() -> tuple[str, ...]:
    return ("fi", "uk")
