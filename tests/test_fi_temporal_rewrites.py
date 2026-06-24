from __future__ import annotations

import datetime as dt

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import FacetKind, StructuralAction
from lawvm.finland.temporal_rewrites import (
    _rewrite_compiled_op_activation_rule_effective_for_chapters,
    _rewrite_lo_op_source_effective_for_chapters,
    _rewrite_lo_op_source_effective_for_address_suffixes,
)


def _op(*, target: LegalAddress) -> LegalOperation:
    return LegalOperation(
        op_id=f"op-{target}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=target,
        source=OperationSource(statute_id="2025/212", effective="2025-07-01"),
        group_id="finland-johto:2025/212",
    )


def test_commencement_address_suffix_rewrite_matches_heading_and_sparse_items() -> None:
    ops = [
        _op(target=LegalAddress(path=(("section", "4"), ("subsection", "1"), ("item", "2")))),
        _op(target=LegalAddress(path=(("section", "5"),), special=FacetKind.HEADING)),
        _op(target=LegalAddress(path=(("section", "5"), ("subsection", "1")))),
        _op(target=LegalAddress(path=(("section", "6"),))),
        _op(target=LegalAddress(path=(("section", "9"), ("subsection", "1")))),
    ]

    touched = _rewrite_lo_op_source_effective_for_address_suffixes(
        ops,
        "2025/212",
        dt.date(2026, 1, 1),
        address_suffixes=(
            LegalAddress(path=(("section", "4"), ("item", "2"))),
            LegalAddress(path=(("section", "5"),), special=FacetKind.HEADING),
            LegalAddress(path=(("section", "5"), ("subsection", "1"))),
            LegalAddress(path=(("section", "6"),)),
        ),
        new_group_id="finland-johto:2025/212:subsection_commencement",
    )

    assert touched == (
        LegalAddress(path=(("section", "4"), ("subsection", "1"), ("item", "2"))),
        LegalAddress(path=(("section", "5"),), special=FacetKind.HEADING),
        LegalAddress(path=(("section", "5"), ("subsection", "1"))),
        LegalAddress(path=(("section", "6"),)),
    )
    assert [op.source.effective if op.source else "" for op in ops] == [
        "2026-01-01",
        "2026-01-01",
        "2026-01-01",
        "2026-01-01",
        "2025-07-01",
    ]


def test_chapter_commencement_rewrite_matches_only_named_chapters() -> None:
    ops = [
        _op(target=LegalAddress(path=(("chapter", "7"), ("section", "2")))),
        _op(target=LegalAddress(path=(("chapter", "7a"), ("section", "9")))),
        _op(target=LegalAddress(path=(("chapter", "6a"), ("section", "1")))),
        _op(target=LegalAddress(path=(("chapter", "17"), ("section", "2")))),
    ]

    touched = _rewrite_lo_op_source_effective_for_chapters(
        ops,
        "2025/212",
        dt.date(2026, 11, 20),
        chapter_labels=frozenset({"7", "7a"}),
        new_group_id="finland-johto:2025/212:chapter_commencement",
    )

    assert touched == (
        LegalAddress(path=(("chapter", "7"), ("section", "2"))),
        LegalAddress(path=(("chapter", "7a"), ("section", "9"))),
    )
    assert [op.source.effective if op.source else "" for op in ops] == [
        "2026-11-20",
        "2026-11-20",
        "2025-07-01",
        "2025-07-01",
    ]
    assert [op.group_id for op in ops] == [
        "finland-johto:2025/212:chapter_commencement",
        "finland-johto:2025/212:chapter_commencement",
        "finland-johto:2025/212",
        "finland-johto:2025/212",
    ]


def test_chapter_commencement_rewrite_updates_compiled_activation_rules() -> None:
    compiled_ops: list[dict[str, object]] = [
        {"source_statute": "2025/212", "target_chapter": "7", "target_norm": "2"},
        {"source_statute": "2025/212", "target_chapter": "7a", "target_norm": "9"},
        {"source_statute": "2025/212", "target_chapter": "6a", "target_norm": "1"},
    ]

    updated = _rewrite_compiled_op_activation_rule_effective_for_chapters(
        compiled_ops,
        "2025/212",
        dt.date(2026, 11, 20),
        chapter_labels=frozenset({"7", "7a"}),
    )

    assert updated is True
    assert compiled_ops[0]["activation_rule"] == {
        "kind": "fixed_date",
        "effective_date": "2026-11-20",
        "condition_ref": "",
    }
    assert compiled_ops[1]["activation_rule"] == {
        "kind": "fixed_date",
        "effective_date": "2026-11-20",
        "condition_ref": "",
    }
    assert "activation_rule" not in compiled_ops[2]
