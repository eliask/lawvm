"""Typed chapter-seed skip ownership records for Finland replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ChapterSeedSkip:
    """Chapter body already seeded from one amendment before replay."""

    chapter_label: str
    amendment_id: str


type LegacyChapterSeedSkip = tuple[str, str]
type ChapterSeedSkipInput = ChapterSeedSkip | LegacyChapterSeedSkip
type ChapterSeedSkipInputSet = set[ChapterSeedSkipInput] | None


def normalize_chapter_seed_skips(
    skips: Iterable[ChapterSeedSkipInput] | None,
) -> tuple[ChapterSeedSkip, ...]:
    normalized: list[ChapterSeedSkip] = []
    for skip in skips or ():
        if isinstance(skip, ChapterSeedSkip):
            normalized.append(skip)
            continue
        chapter_label, amendment_id = skip
        normalized.append(ChapterSeedSkip(chapter_label=chapter_label, amendment_id=amendment_id))
    return tuple(normalized)
