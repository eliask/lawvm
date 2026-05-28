"""Frozen mapping and JSON-safe value helpers used by core IR types."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, SupportsIndex


class FrozenDict(dict[str, Any]):
    """A deepcopy-friendly immutable dict for kernel IR attrs/metadata."""

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("FrozenDict is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("FrozenDict is immutable")

    def clear(self) -> None:
        raise TypeError("FrozenDict is immutable")

    def pop(self, key: str, default: Any = None) -> Any:
        raise TypeError("FrozenDict is immutable")

    def popitem(self) -> tuple[str, Any]:
        raise TypeError("FrozenDict is immutable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        raise TypeError("FrozenDict is immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("FrozenDict is immutable")

    def __copy__(self) -> "FrozenDict":
        return FrozenDict(self)

    def __deepcopy__(self, memo: Dict[int, Any]) -> "FrozenDict":
        # FrozenDict is a boundary type: preserve the immutable wrapper and
        # deep-copy nested payloads so copied core objects remain safe.
        frozen = FrozenDict({deepcopy(k, memo): _freeze_value(deepcopy(v, memo)) for k, v in self.items()})
        memo[id(self)] = frozen
        return frozen

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[type["FrozenDict"], tuple[dict[str, Any]]]:
        return (FrozenDict, (dict(self),))


def _freeze_value(value: Any) -> Any:
    """Recursively freeze mutable container values used inside kernel payloads."""
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, dict):
        return FrozenDict({key: _freeze_value(inner) for key, inner in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(inner) for inner in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(inner) for inner in value)
    return value


def _jsonable_value(value: Any, *, path: str) -> Any:
    """Validate and convert a Python value into a JSON-safe value."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, inner in value.items():
            if not isinstance(key, str):
                raise TypeError(f"Non-string key at {path}: {key!r}")
            out[key] = _jsonable_value(inner, path=f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_jsonable_value(inner, path=f"{path}[{idx}]") for idx, inner in enumerate(value)]
    if isinstance(value, set | frozenset):
        normalized = [_jsonable_value(inner, path=f"{path}[]") for inner in value]
        return sorted(normalized, key=repr)
    raise TypeError(f"Value at {path} is not JSON-serializable: {type(value).__name__}")
