"""Finland-local amendment verb vocabulary.

This enum is a frontend bridge for Finnish amendment clause parsing and
tokenization. Shared core semantics should use jurisdiction-neutral enums.
"""

from __future__ import annotations

from enum import Enum


class SourceVerb(Enum):
    """Legacy Finland amendment-verb classification."""

    MUUTTAA = "muuttaa"
    KUMOTA = "kumota"
    LISATA = "lisata"
    SIIRTAA = "siirtaa"

    def __str__(self) -> str:
        return self.value
