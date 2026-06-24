"""Finland legacy ``AmendmentOp`` target codec ↔ core ``TargetSelector``.

This codec is the jurisdiction boundary between Finland's legacy 8-column
``target_*`` encoding (mirrored by :class:`AmendmentOpV1Record`) and the
cross-jurisdiction :class:`lawvm.core.target_selector.TargetSelector`.

It is purely *structural* and lossless: it maps the raw legacy columns to a
selector and back byte-identically. It deliberately does NOT apply any of the
resolution-time transforms that live in
``lawvm.finland.ops._synthesize_target_address`` (part-label canonicalization via
``_norm_num_token``, the legacy ``"3d"`` item/subitem compound split, or the
``otsikko``/``otsikko_edella`` → ``FacetKind.HEADING`` collapse). Those are
lowering decisions; the codec preserves what the legacy record actually held.

Mapping (mirrors ``_synthesize_target_address`` *structure*, see ops.py ~1806):

- ``target_unit_kind`` selects the focus segment kind:
    - ``"section"``: focus = ``section:<target_section>``; scope = part(+chapter)
      when those columns are present.
    - ``"chapter"``: focus = ``chapter:<target_section>``; scope = part when the
      ``target_part`` column is present.
    - ``"part"``: focus = ``part:<target_section>``. A part has no *structural*
      enclosing scope, but the legacy encoding carries a REDUNDANT ``target_part``
      column that mirrors ``target_section`` for a part op (``_lo_target_fields``
      sets both from ``pd["part"]``; ``_synthesize_target_address`` collapses them
      via ``part_label = target_part or target_norm``). When that redundant column
      is present we carry it as an EXPLICIT_SCOPE ``part`` segment equal to the
      focus so the legacy round-trip is byte-identical; the resolved address
      collapses the duplicate back to a single ``part:<x>``. This is the W2
      corpus-scale FINDING (surfaced by 1929/234 part III/V/I); without it a part
      op's ``target_part`` was silently dropped on round-trip.
- ``target_chapter`` / ``target_part``: when present, become the EXPLICIT_SCOPE
  path. When absent (``None`` or ``""``), the legacy encoding *cannot* tell
  "explicitly at root" from "scope unspecified" — there is no column for that
  distinction (``_synthesize_target_address`` simply omits the segments either
  way) — so we map to ``ScopeStatus.UNSPECIFIED`` (per the W0 ruling).
- ``target_paragraph`` (momentti) → ``subsection``; ``target_item`` (kohta) →
  ``item``; ``target_subitem`` (alakohta) → ``subitem``. Appended to the focus.
- ``target_special`` → ``FacetKind`` (``otsikko``/``otsikko_edella`` →
  ``HEADING``; ``johd`` → ``INTRO``) AND the raw token on ``special_raw`` so the
  ``otsikko`` vs ``otsikko_edella`` distinction round-trips (FacetKind alone is
  lossy for it — see the W2 FINDING in the codec module docstring tail).
"""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.semantic_types import FacetKind
from lawvm.core.target_selector import (
    AddressSegment,
    ScopeStatus,
    TargetScope,
    TargetSelector,
)

# Legacy ``target_special`` token -> coarse FacetKind. ``otsikko`` and
# ``otsikko_edella`` both map to HEADING (the collapse that makes FacetKind alone
# lossy); the raw token is preserved separately on the selector for round-trip.
_SPECIAL_TOKEN_TO_FACET: dict[str, FacetKind] = {
    "otsikko": FacetKind.HEADING,
    "otsikko_edella": FacetKind.HEADING,
    "johd": FacetKind.INTRO,
}


@dataclass(frozen=True, slots=True)
class AmendmentOpV1Record:
    """Mirrors the current ``AmendmentOp`` ``target_*`` columns EXACTLY.

    Field names, types, and defaults match the live ``AmendmentOp`` fields
    (``finland/ops.py`` ~653). This is the on-the-wire legacy shape the codec
    round-trips against.
    """

    target_unit_kind: str
    target_section: str
    target_chapter: str | None
    target_part: str | None
    target_paragraph: int | None
    target_item: str | None
    target_subitem: str | None
    target_special: str | None


class TargetSelectorCodecV1:
    """Lossless structural codec between the legacy record and a selector."""

    @staticmethod
    def from_legacy(rec: AmendmentOpV1Record) -> TargetSelector:
        scope_segments: list[AddressSegment] = []
        relative_path: list[AddressSegment] = []

        if rec.target_unit_kind == "part":
            # A part is the focus; nothing *structurally* encloses it. The legacy
            # encoding nonetheless carries a redundant ``target_part`` column that
            # mirrors ``target_section`` for a part op (see the W2 FINDING in the
            # module docstring tail and ``_lo_target_fields`` / the
            # ``_synthesize_target_address`` ``part_label = target_part or
            # target_norm`` collapse). To keep the round-trip byte-identical we
            # carry the presence of that redundant column as an EXPLICIT_SCOPE
            # ``part`` segment equal to the focus; ``to_legal_address_if_complete``
            # collapses the duplicate so the resolved address stays ``part:<x>``.
            if rec.target_part:
                scope_segments.append(AddressSegment("part", rec.target_part))
            relative_path.append(AddressSegment("part", rec.target_section))
        elif rec.target_unit_kind == "chapter":
            # Optional enclosing part scope; chapter is the focus.
            if rec.target_part:
                scope_segments.append(AddressSegment("part", rec.target_part))
            relative_path.append(AddressSegment("chapter", rec.target_section))
        else:
            # section (and any other unit kind defaults to section focus).
            if rec.target_part:
                scope_segments.append(AddressSegment("part", rec.target_part))
            if rec.target_chapter:
                scope_segments.append(AddressSegment("chapter", rec.target_chapter))
            relative_path.append(AddressSegment("section", rec.target_section))

        # Descendant focus segments (momentti / kohta / alakohta).
        if rec.target_paragraph is not None:
            relative_path.append(AddressSegment("subsection", str(rec.target_paragraph)))
        if rec.target_item is not None:
            relative_path.append(AddressSegment("item", rec.target_item))
        if rec.target_subitem is not None:
            relative_path.append(AddressSegment("subitem", rec.target_subitem))

        if scope_segments:
            scope = TargetScope(
                scope_status=ScopeStatus.EXPLICIT_SCOPE,
                path=tuple(scope_segments),
            )
        else:
            # Legacy encoding cannot distinguish explicit-root from unspecified.
            scope = TargetScope(scope_status=ScopeStatus.UNSPECIFIED)

        special_raw = rec.target_special if rec.target_special else None
        special = _SPECIAL_TOKEN_TO_FACET.get(special_raw) if special_raw else None

        return TargetSelector(
            relative_path=tuple(relative_path),
            scope=scope,
            special=special,
            special_raw=special_raw,
        )

    @staticmethod
    def to_legacy(sel: TargetSelector) -> AmendmentOpV1Record:
        # Reconstruct scope columns from the EXPLICIT_SCOPE path (UNSPECIFIED →
        # both None, matching the lossy legacy encoding).
        target_part: str | None = None
        target_chapter: str | None = None
        for segment in sel.scope.path:
            if segment.kind == "part":
                target_part = segment.label
            elif segment.kind == "chapter":
                target_chapter = segment.label

        target_paragraph: int | None = None
        target_item: str | None = None
        target_subitem: str | None = None
        focus_kind: str | None = None
        focus_label: str = ""
        for segment in sel.relative_path:
            if segment.kind in ("section", "chapter", "part"):
                focus_kind = segment.kind
                focus_label = segment.label
            elif segment.kind == "subsection":
                target_paragraph = int(segment.label)
            elif segment.kind == "item":
                target_item = segment.label
            elif segment.kind == "subitem":
                target_subitem = segment.label

        if focus_kind is None:
            raise ValueError(
                "TargetSelector has no part/chapter/section focus segment; "
                f"cannot encode to legacy record: {sel.relative_path!r}"
            )

        # The raw special token is authoritative for round-trip; ``special``
        # (FacetKind) is the coarse projection and is not consulted here.
        target_special = sel.special_raw

        return AmendmentOpV1Record(
            target_unit_kind=focus_kind,
            target_section=focus_label,
            target_chapter=target_chapter,
            target_part=target_part,
            target_paragraph=target_paragraph,
            target_item=target_item,
            target_subitem=target_subitem,
            target_special=target_special,
        )
