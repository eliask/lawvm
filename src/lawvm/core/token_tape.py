"""Shared immutable token-tape projection contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True, slots=True)
class TokenLexeme:
    """One classified token in a source-preserving token tape."""

    text: str
    lemma: str
    category: str
    gram_case: str = ""
    semantic_code: str = ""
    char_start: int = -1
    char_end: int = -1
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "lemma", str(self.lemma or ""))
        object.__setattr__(self, "category", _required_string("TokenLexeme.category", self.category))
        object.__setattr__(self, "gram_case", str(self.gram_case or ""))
        object.__setattr__(self, "semantic_code", str(self.semantic_code or ""))
        _require_int("TokenLexeme.char_start", self.char_start)
        _require_int("TokenLexeme.char_end", self.char_end)
        if self.char_start >= 0 and self.char_end >= 0 and self.char_end < self.char_start:
            raise ValueError("TokenLexeme.char_end must be >= char_start when offsets are present")
        if not isinstance(self.detail, Mapping):
            raise ValueError("TokenLexeme.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "lemma": self.lemma,
            "category": self.category,
            "gram_case": self.gram_case,
            "semantic_code": self.semantic_code,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class TokenTape:
    """Immutable source-token tape shared by frontend proof surfaces."""

    source_text: str
    lexemes: tuple[TokenLexeme, ...]
    source_hash: str = ""
    tape_schema: str = "lawvm.token_tape.v1"

    def __post_init__(self) -> None:
        source_text = str(self.source_text or "")
        object.__setattr__(self, "source_text", source_text)
        source_hash = str(self.source_hash or "") or hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        object.__setattr__(self, "source_hash", source_hash)
        object.__setattr__(self, "tape_schema", _required_string("TokenTape.tape_schema", self.tape_schema))
        lexemes = tuple(self.lexemes)
        if not all(isinstance(item, TokenLexeme) for item in lexemes):
            raise ValueError("TokenTape.lexemes must contain TokenLexeme objects")
        object.__setattr__(self, "lexemes", lexemes)

    def __len__(self) -> int:
        return len(self.lexemes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_hash": self.source_hash,
            "source_length": len(self.source_text),
            "tape_schema": self.tape_schema,
            "lexeme_count": len(self.lexemes),
            "lexemes": [lexeme.to_dict() for lexeme in self.lexemes],
        }


@dataclass(frozen=True, slots=True)
class TokenAnnotation:
    """Source-preserving annotation over a token tape span."""

    annotation_id: str
    kind: str
    start: int
    end: int
    sentinel_kind: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "annotation_id",
            _required_string("TokenAnnotation.annotation_id", self.annotation_id),
        )
        object.__setattr__(self, "kind", _required_string("TokenAnnotation.kind", self.kind))
        _require_int("TokenAnnotation.start", self.start)
        _require_int("TokenAnnotation.end", self.end)
        if self.start < 0:
            raise ValueError("TokenAnnotation.start must be >= 0")
        if self.end < self.start:
            raise ValueError("TokenAnnotation.end must be >= start")
        object.__setattr__(self, "sentinel_kind", str(self.sentinel_kind or ""))
        if not isinstance(self.detail, Mapping):
            raise ValueError("TokenAnnotation.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_id": self.annotation_id,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "sentinel_kind": self.sentinel_kind,
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class AnnotatedTokenView:
    """A parser-visible view over an immutable token tape."""

    tape: TokenTape
    annotations: tuple[TokenAnnotation, ...] = ()
    visible_indices: tuple[int, ...] = ()
    view_schema: str = "lawvm.annotated_token_view.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.tape, TokenTape):
            raise ValueError("AnnotatedTokenView.tape must be a TokenTape")
        annotations = tuple(self.annotations)
        if not all(isinstance(item, TokenAnnotation) for item in annotations):
            raise ValueError("AnnotatedTokenView.annotations must contain TokenAnnotation objects")
        object.__setattr__(self, "annotations", annotations)
        visible_indices = tuple(self.visible_indices)
        for index in visible_indices:
            if not isinstance(index, int):
                raise ValueError("AnnotatedTokenView.visible_indices must contain integers")
            if index < 0 or index >= len(self.tape):
                raise ValueError("AnnotatedTokenView.visible_indices must reference tape lexemes")
        object.__setattr__(self, "visible_indices", visible_indices)
        object.__setattr__(
            self,
            "view_schema",
            _required_string("AnnotatedTokenView.view_schema", self.view_schema),
        )

    def __len__(self) -> int:
        return len(self.visible_indices)

    def to_dict(self) -> dict[str, Any]:
        return {
            "view_schema": self.view_schema,
            "source_hash": self.tape.source_hash,
            "visible_count": len(self.visible_indices),
            "annotation_count": len(self.annotations),
            "visible_indices": list(self.visible_indices),
            "annotations": [annotation.to_dict() for annotation in self.annotations],
        }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _require_int(field_name: str, value: Any) -> None:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
