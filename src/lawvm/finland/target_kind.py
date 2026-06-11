"""Finland-local legacy target-kind codes.

This enum exists only as a Finland-local legacy compatibility shell around
Finland's historical `P/L/O/A` target vocabulary. Shared core should not own
it.
"""

from __future__ import annotations
from typing_extensions import override

from enum import Enum
from typing import Any


class _StringComparableMixin:
    """Local compatibility mixin for Finland's legacy target-kind compatibility shell."""

    value: Any

    @override
    def __eq__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self is other
        if isinstance(other, str):
            return self.value == other
        return NotImplemented

    @override
    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    @override
    def __str__(self) -> str:
        return self.value

    @override
    def __hash__(self) -> int:
        return hash(self.value)


class TargetKind(_StringComparableMixin, Enum):
    """Finland amendment-operation legacy target-kind compatibility codes."""

    AMENDMENT = "A"
    SECTION = "P"
    CHAPTER = "L"
    PART = "O"
