from __future__ import annotations

from typing import Optional

from lawvm.core.ir import IRStatute
from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor


def align_uk_replay_to_oracle(
    replayed_ir: IRStatute,
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    verbose: bool = False,
) -> IRStatute:
    if not eid_map:
        return replayed_ir
    executor = UKReplayExecutor(
        replayed_ir,
        eid_map=eid_map,
        text_map=text_map or {},
        verbose=verbose,
    )
    executor.ground_ids()
    return executor.statute.to_irstatute()
