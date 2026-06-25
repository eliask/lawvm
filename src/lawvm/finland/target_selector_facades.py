"""Typed constructor facades for the FI ``AmendmentOp`` target selector.

This is the sanctioned, typed *construction* entry point for amendment targets.
Instead of hand-writing a :class:`TargetSelector` (or, historically, the 8
loosely-typed legacy ``target_*`` keyword arguments) at a call site, a producer
builds the target by calling a facade — ``fi_section_target(...)``,
``fi_chapter_target(...)``, or ``fi_part_target(...)`` — which constructs a typed
:class:`lawvm.core.target_selector.TargetSelector` (with the correct
``relative_path`` :class:`AddressSegment` chain and a :class:`TargetScope` /
:class:`ScopeStatus`) and returns it wrapped as the single ``target_selector``
construction kwarg (:class:`TargetSelectorKwarg`), splattable into
``AmendmentOp(op_id=..., **fi_section_target(...))`` or
``dataclasses.replace(op, **replace_target(op, ...))``.

W6 Phase C: ``AmendmentOp`` stores that typed selector directly — the legacy
8-column ``target_*`` construction kwargs are gone; the codec
(:mod:`lawvm.finland.target_selector_codec`) is consulted only to overlay a
partial re-target in :func:`replace_target`. The facades perform NO
resolution-time transforms (label canonicalisation, the legacy ``"3d"``
item/subitem compound split, etc.); those are lowering decisions owned elsewhere.

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

from typing import TYPE_CHECKING, Final, TypedDict, cast

from lawvm.core.target_selector import (
    AddressSegment,
    ScopeStatus,
    TargetScope,
    TargetSelector,
)
from lawvm.finland.target_selector_codec import (
    _SPECIAL_TOKEN_TO_FACET,
    AmendmentOpV1Record,
    TargetSelectorCodecV1,
)

if TYPE_CHECKING:
    from lawvm.finland.ops import AmendmentOp


class _Unset:
    """Sentinel: a ``replace_target`` field the caller did not change.

    Distinct from ``None`` (which is a meaningful "clear this column" value), so
    ``replace_target(op, target_special=None)`` can clear the facet while
    ``replace_target(op, target_item="3")`` leaves ``target_special`` untouched.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "<UNSET>"


_UNSET: Final = _Unset()


class TargetSelectorKwarg(TypedDict):
    """The single ``target_selector`` construction kwarg a facade returns.

    A typed single-key dict so it splats with full type precision into the
    ``AmendmentOp(op_id=..., **fi_section_target(...))`` /
    ``dataclasses.replace(op, **replace_target(op, ...))`` call sites: the key
    matches ``AmendmentOp.__init__``'s ``target_selector`` parameter exactly, so
    a plain ``dict[str, TargetSelector]`` (whose splat ``ty`` cannot key-resolve)
    is avoided.

    W6 Phase C: ``AmendmentOp`` stores the typed :class:`TargetSelector`
    directly; the legacy 8-column construction kwargs are gone.
    """

    target_selector: TargetSelector


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


def _selector_kwarg(selector: TargetSelector) -> TargetSelectorKwarg:
    """Wrap a selector as the single ``target_selector`` construction kwarg.

    W6 Phase C: ``AmendmentOp`` stores the typed selector directly, so a typed
    facade emits ``target_selector=<selector>`` (splattable into
    ``AmendmentOp(op_id=…, **fi_section_target(…))``) rather than the legacy
    8-column kwargs.
    """
    return TargetSelectorKwarg(target_selector=selector)


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
) -> TargetSelectorKwarg:
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
    return _selector_kwarg(selector)


def replace_target(
    op: "AmendmentOp",
    *,
    target_section: str | _Unset = _UNSET,
    target_chapter: str | None | _Unset = _UNSET,
    target_part: str | None | _Unset = _UNSET,
    target_paragraph: int | None | _Unset = _UNSET,
    target_item: str | None | _Unset = _UNSET,
    target_subitem: str | None | _Unset = _UNSET,
    target_special: str | None | _Unset = _UNSET,
) -> TargetSelectorKwarg:
    """Typed *partial* re-target of an existing ``op`` → ``{"target_selector": …}``.

    This is the sanctioned typed path for a ``dataclasses.replace(op,
    **replace_target(op, …))`` that changes one (or a few) target columns while
    deliberately preserving the rest. Instead of hand-patching individual legacy
    columns, it:

    1. projects the op's current target to the legacy 8-column shape
       (``op.target_cols`` = ``codec.to_legacy(op.target_selector)``),
    2. overlays only the columns the caller explicitly passed (``_UNSET`` leaves
       a column at its current value; passing ``None`` clears it), and
    3. re-encodes the overlaid record to a typed :class:`TargetSelector`,
       returned as the single ``target_selector`` kwarg.

    W6 Phase C: ``AmendmentOp`` no longer stores the 8 columns — the typed
    selector is the sole stored representation. The returned mapping splats as
    ``dataclasses.replace(op, target_selector=…)``: ``dataclasses.replace``
    re-passes the op's *current* stored selector as a field copy, and this
    explicit ``target_selector`` override supersedes it (the override wins over
    the auto-injected field). For a no-op call (no overrides) the result is the
    op's current selector, so the replace is the identity on the target.

    It fails loud (below) on the one shape the codec cannot round-trip: an
    OVERLAID empty-string chapter/part/special, which the codec would lower to
    ``None`` (silently changing the column) — the op's own stored selector can
    never carry such a column (it is dropped at selector-construction time), so
    the only way an empty string reaches here is an explicit override.

    AUTHORITY CAVEAT — do NOT use this on a ``dataclasses.replace`` that also
    carries (or retains) an ``lo``: ``AmendmentOp.__init__`` derives the stored
    selector from ``lo`` when ``lo`` is present, so the ``target_selector`` this
    returns would be silently discarded. Only convert sites where ``lo`` is
    provably ``None`` after the replace. lo-bearing target rewrites belong to the
    ``lo`` carrier, not this shadow path.
    """
    # The op's current columns via the stored selector projection. This is the
    # baseline the overrides overlay on.
    current = TargetSelectorCodecV1.to_legacy(op.target_selector)

    def _pick(
        override: object, fallback: str | int | None
    ) -> str | int | None:
        return fallback if isinstance(override, _Unset) else cast("str | int | None", override)

    # Overlay the explicit overrides on the projected current columns.
    overlaid = AmendmentOpV1Record(
        target_unit_kind=current.target_unit_kind,
        target_section=cast(str, _pick(target_section, current.target_section)),
        target_chapter=cast("str | None", _pick(target_chapter, current.target_chapter)),
        target_part=cast("str | None", _pick(target_part, current.target_part)),
        target_paragraph=cast("int | None", _pick(target_paragraph, current.target_paragraph)),
        target_item=cast("str | None", _pick(target_item, current.target_item)),
        target_subitem=cast("str | None", _pick(target_subitem, current.target_subitem)),
        target_special=cast("str | None", _pick(target_special, current.target_special)),
    )

    # Fail loud on the one non-round-tripping shape: the codec lowers an
    # empty-string chapter/part/special to None. The op's stored selector can
    # never carry such a column, so an empty string here can only be a
    # caller-supplied override — reject it rather than silently clearing.
    for name, value in (
        ("target_chapter", overlaid.target_chapter),
        ("target_part", overlaid.target_part),
        ("target_special", overlaid.target_special),
    ):
        if value == "":
            raise ValueError(
                f"replace_target: overlaid empty-string {name}={value!r}, which the "
                "codec round-trip lowers to None (not byte-identical). Pass None to "
                "clear the column explicitly, or a non-empty label."
            )

    return TargetSelectorKwarg(target_selector=TargetSelectorCodecV1.from_legacy(overlaid))


def fi_chapter_target(
    chapter: str,
    *,
    part: str | None = None,
    subsection: int | None = None,
    item: str | None = None,
    subitem: str | None = None,
    special_raw: str | None = None,
) -> TargetSelectorKwarg:
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
    return _selector_kwarg(selector)


def fi_part_target(
    part: str,
    *,
    redundant_part_scope: bool = False,
    subsection: int | None = None,
    item: str | None = None,
    subitem: str | None = None,
    special_raw: str | None = None,
) -> TargetSelectorKwarg:
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
    return _selector_kwarg(selector)
