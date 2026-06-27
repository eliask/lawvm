"""Typed carriers for Finnish ``jolloin`` consequence-renumber pairs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JolloinRenumberPair:
    """A typed renumber pair extracted from a ``jolloin`` consequence span."""

    source_label: str
    destination_label: str
    kind: str
    destination_chapter: str = ""
    destination_part: str = ""
