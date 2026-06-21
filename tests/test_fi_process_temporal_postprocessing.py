from __future__ import annotations

import datetime as dt

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.process_temporal_postprocessing import (
    _kumotaan_labels_by_effective_date,
    _rewrite_delayed_kumotaan_injected_ops,
)


def _repeal_op(section: str, effective: str) -> LegalOperation:
    return LegalOperation(
        op_id=f"repeal-{section}",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", section),)),
        source=OperationSource(statute_id="2025/212", effective=effective),
        group_id="finland-johto:2025/212",
    )


def test_kumotaan_lifecycle_groups_follow_rewritten_repeal_effective_dates() -> None:
    groups = _kumotaan_labels_by_effective_date(
        [
            _repeal_op("5a", "2026-01-01"),
            _repeal_op("9", "2025-07-01"),
        ],
        labels=["5a", "9"],
        amendment_id="2025/212",
        default_effective_date=dt.date(2025, 7, 1),
    )

    assert groups == {
        dt.date(2025, 7, 1): ["9"],
        dt.date(2026, 1, 1): ["5a"],
    }


def test_delayed_kumotaan_group_rewrites_late_injected_repeal_ops() -> None:
    op = _repeal_op("5a", "2025-07-01")
    ops = [op]

    _rewrite_delayed_kumotaan_injected_ops(
        ops,
        amendment_id="2025/212",
        default_effective_date=dt.date(2025, 7, 1),
        expiry_groups={dt.date(2026, 1, 1): ["5a"]},
        base_ir=None,
        group_id_prefix="finland-johto:2025/212:kumotaan_commencement",
        chapter_section_map=None,
    )

    assert ops[0].source is not None
    assert ops[0].source.effective == "2026-01-01"
    assert ops[0].group_id == "finland-johto:2025/212:kumotaan_commencement:2026-01-01"
