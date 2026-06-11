"""Finland-local amendment verb vocabulary.

This enum is a frontend bridge for Finnish amendment clause parsing and
tokenization. Shared core semantics should use jurisdiction-neutral enums.
"""

from __future__ import annotations
from typing_extensions import override

from enum import Enum


class SourceVerb(Enum):
    """Legacy Finland amendment-verb classification."""

    MUUTTAA = "muuttaa"
    KUMOTA = "kumota"
    LISATA = "lisata"
    SIIRTAA = "siirtaa"

    @override
    def __str__(self) -> str:
        return self.value
