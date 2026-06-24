"""Typed constructor facades for FI ``AmendmentOp`` legacy ``target_*`` kwargs.

This is the sanctioned, typed *construction* entry point for amendment targets
(migration wave W3a). Instead of hand-writing the 8 loosely-typed legacy
``target_*`` keyword arguments at a call site, a producer builds the target by
calling a facade — ``fi_section_target(...)``, ``fi_chapter_target(...)``, or
``fi_part_target(...)`` — which:

1. constructs a typed :class:`lawvm.core.target_selector.TargetSelector` (with
   the correct ``relative_path`` :class:`AddressSegment` chain and a
   :class:`TargetScope` / :class:`ScopeStatus`), then
2. lowers it back to the legacy columns through
   :meth:`TargetSelectorCodecV1.to_legacy`, returning the ``target_*`` kwargs as
   a plain ``dict[str, ...]`` suitable for ``**kwargs`` splatting into
   ``AmendmentOp(...)`` or ``dataclasses.replace(op, ...)``.

The facade output is *byte-identical* to hand-writing the legacy kwargs for the
same logical shape — it is purely a typed front door over the codec's existing
lowering. It deliberately performs NO resolution-time transforms (label
canonicalisation, the legacy ``"3d"`` item/subitem compound split, etc.); those
are lowering decisions owned elsewhere. See
``lawvm.finland.target_selector_codec`` for the exact column mapping.

Scope semantics (mirrors the codec, which is the lossy boundary):
- When the caller names a ``part`` and/or ``chapter`` scope, the selector
  carries an ``EXPLICIT_SCOPE`` path and the legacy ``target_part`` /
  ``target_chapter`` columns are populated.
- When no scope is named, the selector carries ``UNSPECIFIED`` scope (the legacy
  encoding cannot distinguish explicit-root from unspecified — the W2 ledger
  finding), and both scope columns lower to ``None``.

Facets: pass the *raw* legacy special token (e.g. ``"otsikko"``,
``"otsikko_edella"``, ``"johd"``) as ``special_raw`` — that token is the
authoritative round-trip value the codec lowers to ``target_special``. The
coarse cross-jurisdiction :class:`FacetKind` is derived from it for the typed
selector but is never consulted on the way back to legacy.
"""

from __future__ import annotations

from typing import TypedDict, cast

from lawvm.core.target_scope import TargetUnitKind
from lawvm.core.target_selector import (
    AddressSegment,
    ScopeStatus,
    TargetScope,
    TargetSelector,
)
from lawvm.finland.target_selector_codec import (
    _SPECIAL_TOKEN_TO_FACET,
    TargetSelectorCodecV1,
)


class LegacyTargetKwargs(TypedDict):
    """The legacy ``target_*`` construction kwargs a facade returns.

    Keys match the ``AmendmentOp`` constructor parameter names exactly, so the
    dict is splattable: ``AmendmentOp(op_id=..., **fi_section_target(...))``.

    ``target_unit_kind`` is the narrow ``TargetUnitKind`` literal (not bare
    ``str``) so the splat matches ``AmendmentOp.__init__``'s typed parameter —
    each facade always emits exactly its own focus kind ("section"/"chapter"/
    "part"), so the narrowing is sound (the codec records it as ``str``; a single
    ``cast`` in ``_selector_to_kwargs`` re-narrows it with no runtime change).
    """

    target_unit_kind: TargetUnitKind
    target_section: str
    target_chapter: str | None
    target_part: str | None
    target_paragraph: int | None
    target_item: str | None
    target_subitem: str | None
    target_special: str | None


def _descendant_segments(
    *,
    subsection: int | None,
    item: str | None,
    subitem: str | None,
) -> list[AddressSegment]:
    """Build the ordered momentti/kohta/alakohta focus tail (codec ordering)."""
    segments: list[AddressSegment] = []
    if subsection is not None:
        segments.append(AddressSegment("subsection", str(subsection)))
    if item is not None:
        segments.append(AddressSegment("item", item))
    if subitem is not None:
        segments.append(AddressSegment("subitem", subitem))
    return segments


def _scope_for(segments: list[AddressSegment]) -> TargetScope:
    """An ``EXPLICIT_SCOPE`` over ``segments`` if any, else ``UNSPECIFIED``."""
    if segments:
        return TargetScope(
            scope_status=ScopeStatus.EXPLICIT_SCOPE, path=tuple(segments)
        )
    return TargetScope(scope_status=ScopeStatus.UNSPECIFIED)


def _selector_to_kwargs(selector: TargetSelector) -> LegacyTargetKwargs:
    """Lower a selector to the legacy ``target_*`` construction kwargs."""
    rec = TargetSelectorCodecV1.to_legacy(selector)
    return LegacyTargetKwargs(
        # The codec types ``target_unit_kind`` as bare ``str``; every facade
        # builds its focus segment from a fixed "section"/"chapter"/"part" kind,
        # so re-narrowing to ``TargetUnitKind`` is sound (type-only, no runtime
        # change) and lets the kwargs splat into ``AmendmentOp.__init__``.
        target_unit_kind=cast(TargetUnitKind, rec.target_unit_kind),
        target_section=rec.target_section,
        target_chapter=rec.target_chapter,
        target_part=rec.target_part,
        target_paragraph=rec.target_paragraph,
        target_item=rec.target_item,
        target_subitem=rec.target_subitem,
        target_special=rec.target_special,
    )


def _facet_for(special_raw: str | None) -> None:
    """Validate a raw special token is one the codec knows (else fail loud)."""
    if special_raw is None:
        return None
    if special_raw not in _SPECIAL_TOKEN_TO_FACET:
        raise ValueError(
            f"fi target facade: unknown special token {special_raw!r}; known "
            f"tokens are {sorted(_SPECIAL_TOKEN_TO_FACET)}. Extend the codec's "
            "_SPECIAL_TOKEN_TO_FACET mapping before using a new token."
        )
    return None


def fi_section_target(
    section: str,
    *,
    chapter: str | None = None,
    part: str | None = None,
    subsection: int | None = None,
    item: str | None = None,
    subitem: str | None = None,
    special_raw: str | None = None,
) -> LegacyTargetKwargs:
    """Typed ``section``-focus target → legacy ``target_*`` kwargs.

    ``chapter`` / ``part`` populate the enclosing ``EXPLICIT_SCOPE``;
    ``subsection`` (momentti) / ``item`` (kohta) / ``subitem`` (alakohta) extend
    the focus tail; ``special_raw`` is the raw facet token (e.g. ``"otsikko"``).
    """
    _facet_for(special_raw)
    scope_segments: list[AddressSegment] = []
    if part is not None:
        scope_segments.append(AddressSegment("part", part))
    if chapter is not None:
        scope_segments.append(AddressSegment("chapter", chapter))
    relative_path: list[AddressSegment] = [AddressSegment("section", section)]
    relative_path.extend(
        _descendant_segments(subsection=subsection, item=item, subitem=subitem)
    )
    selector = TargetSelector(
        relative_path=tuple(relative_path),
        scope=_scope_for(scope_segments),
        special=_SPECIAL_TOKEN_TO_FACET.get(special_raw) if special_raw else None,
        special_raw=special_raw,
    )
    return _selector_to_kwargs(selector)


def fi_chapter_target(
    chapter: str,
    *,
    part: str | None = None,
    subsection: int | None = None,
    item: str | None = None,
    subitem: str | None = None,
    special_raw: str | None = None,
) -> LegacyTargetKwargs:
    """Typed ``chapter``-focus target → legacy ``target_*`` kwargs.

    ``part`` populates the enclosing ``EXPLICIT_SCOPE``. The chapter label lowers
    to ``target_section`` with ``target_unit_kind="chapter"`` (the legacy
    encoding stores the focus label in ``target_section`` regardless of kind).
    """
    _facet_for(special_raw)
    scope_segments: list[AddressSegment] = []
    if part is not None:
        scope_segments.append(AddressSegment("part", part))
    relative_path: list[AddressSegment] = [AddressSegment("chapter", chapter)]
    relative_path.extend(
        _descendant_segments(subsection=subsection, item=item, subitem=subitem)
    )
    selector = TargetSelector(
        relative_path=tuple(relative_path),
        scope=_scope_for(scope_segments),
        special=_SPECIAL_TOKEN_TO_FACET.get(special_raw) if special_raw else None,
        special_raw=special_raw,
    )
    return _selector_to_kwargs(selector)


def fi_part_target(
    part: str,
    *,
    redundant_part_scope: bool = False,
    subsection: int | None = None,
    item: str | None = None,
    subitem: str | None = None,
    special_raw: str | None = None,
) -> LegacyTargetKwargs:
    """Typed ``part``-focus target → legacy ``target_*`` kwargs.

    A part has no structural enclosing scope. The legacy encoding nonetheless
    carries a REDUNDANT ``target_part`` column mirroring ``target_section`` for a
    part op (the W2 corpus finding from 1929/234 part III/V/I). Set
    ``redundant_part_scope=True`` to reproduce that legacy shape byte-identically
    (carried as an ``EXPLICIT_SCOPE`` part segment equal to the focus, which the
    resolver collapses back to a single ``part:<x>``). Leave it ``False`` for a
    bare part op with no ``target_part`` column.
    """
    _facet_for(special_raw)
    scope_segments: list[AddressSegment] = []
    if redundant_part_scope:
        scope_segments.append(AddressSegment("part", part))
    relative_path: list[AddressSegment] = [AddressSegment("part", part)]
    relative_path.extend(
        _descendant_segments(subsection=subsection, item=item, subitem=subitem)
    )
    selector = TargetSelector(
        relative_path=tuple(relative_path),
        scope=_scope_for(scope_segments),
        special=_SPECIAL_TOKEN_TO_FACET.get(special_raw) if special_raw else None,
        special_raw=special_raw,
    )
    return _selector_to_kwargs(selector)
