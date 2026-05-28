from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.source_path_index import duplicate_preserving_source_path_index


@dataclass(frozen=True)
class _SourceRow:
    path: tuple[str, ...]
    source_id: str = ""


def test_duplicate_preserving_source_path_index_keeps_unique_paths_plain() -> None:
    row = _SourceRow(path=("prov:1",), source_id="S1")

    assert duplicate_preserving_source_path_index(
        [row],
        path_of=lambda item: item.path,
        duplicate_id_of=lambda item: item.source_id,
    ) == {("prov:1",): row}


def test_duplicate_preserving_source_path_index_appends_duplicate_source_ids() -> None:
    first = _SourceRow(path=("prov:1",), source_id="S1")
    second = _SourceRow(path=("prov:1",), source_id="S1A")

    assert duplicate_preserving_source_path_index(
        [first, second],
        path_of=lambda item: item.path,
        duplicate_id_of=lambda item: item.source_id,
    ) == {
        ("prov:1", "source-duplicate:S1"): first,
        ("prov:1", "source-duplicate:S1A"): second,
    }


def test_duplicate_preserving_source_path_index_uses_ordinals_without_ids() -> None:
    first = _SourceRow(path=("prov:1",))
    second = _SourceRow(path=("prov:1",))

    assert tuple(
        duplicate_preserving_source_path_index(
            [first, second],
            path_of=lambda item: item.path,
            duplicate_id_of=lambda item: item.source_id,
        )
    ) == (
        ("prov:1", "source-duplicate:ordinal:1"),
        ("prov:1", "source-duplicate:ordinal:2"),
    )

