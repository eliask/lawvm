"""Estonia's concrete ``LabelAlgebra`` — superscript / division-suffix (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4. This encodes Estonia's ACTUAL label calculus as data for the
neutral ``core.label_algebra.LabelAlgebra`` seam type, so the calculus that
today lives IMPLICITLY in ``estonia/grafter.py`` (superscript inserts
``§10¹``, the ``_predecessor_rank`` successor logic, the
``default_label_sort_key`` ordering, the ``SlotIdentity`` normalized-label
collision key) becomes a first-class, conformance-tested profile object.

EE's label surface (VERIFIED against ``estonia/peg._normalize_num`` and
``core/tree_ops.default_label_sort_key``):

* A superscript section/division is written ``§71¹`` / ``§10¹`` and the parser
  normalizes it to the compound slot label ``71_1`` / ``10_1``
  (``_normalize_num``: ``'71¹'`` and ``'71 1'`` both → ``'71_1'``). The EE
  grafter operates on these ALREADY-normalized labels (``child.label`` is the
  ``N_M`` form); ``_predecessor_rank`` splits on ``'_'`` to recover the stem
  ``71`` and the superscript ordinal ``1``.
* A lettered division suffix is written ``14a`` (the stem-family ``14, 14a,
  14b`` §1 names). ``default_label_sort_key`` decomposes it to
  ``(14, 'a', 0)``.
* The authoritative sibling ORDER is ``default_label_sort_key`` —
  ``(number, letter_suffix, sub_number)`` — under which ``71`` < ``71_1`` <
  ``71_2`` < ``72`` and ``14`` < ``14a`` < ``14b``, i.e. a superscript /
  lettered insert sorts immediately after its stem, before the next stem
  (the interleaved-label order §1 requires).
* COLLISION is normalized-label identity (``SlotIdentity`` =
  (parent path, kind, normalized label), ``core/occupancy``):
  ``normalized_label_key`` of the normalized ``N_M`` label. Two labels collide
  iff those keys are equal.
* The admissible fresh SUCCESSOR for a superscript insert into a stem family is
  the next free superscript ``stem_(max_existing_superscript + 1)`` — an insert
  after ``§10`` (no superscript siblings) yields ``§10¹`` (``10_1``); after
  ``§10`` + ``§10¹`` yields ``§10²`` (``10_2``). This mirrors
  ``_predecessor_rank`` (it ranks same-stem predecessors by superscript ordinal
  to place the insert), read as its inverse: the fresh label is one past the
  greatest same-stem superscript present. A stem with NO superscript siblings
  gets ``_1`` (the first superscript).

SCOPE (declared-first, byte-identical). This is a DECLARED, conformance-tested
MIRROR of EE's real relabel/collision code, NOT yet routed into the grafter's
relabel path (``tests/test_label_algebra.py`` binds each declared behaviour to
EE's ACTUAL ``default_label_sort_key`` / ``normalized_label_key`` /
``_normalize_num`` code, so it FAILS if EE's label logic drifts). It covers the
superscript + lettered stem-family calculus — the ``§10¹`` / ``14a`` shapes.
Routing the grafter through it, and the old-format duplicate-division-suffix
REPAIR (``_ee_relabel_duplicate_division_suffix_before_insert``, a base-cleanup
recovery orthogonal to fresh-label admissibility), are follow-ups (see the
module note).

PLANE. This frontend module imports the neutral seam type from
``core.label_algebra`` (the kernel never imports a jurisdiction) plus EE's own
``peg`` / core label helpers — the same import direction the θ tables use.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from lawvm.core.label_algebra import LabelAlgebra, LabelComponent, ParsedLabel
from lawvm.core.tree_ops import default_label_sort_key, normalized_label_key
from lawvm.estonia.peg import _normalize_num

__all__ = [
    "EE_LABEL_ALGEBRA",
    "build_ee_label_algebra",
    "ee_parse_label",
    "ee_label_sort_key",
]


def _ee_stem_and_components(
    normalized: str, sort_key: tuple[object, ...]
) -> tuple[str, tuple[LabelComponent, ...]]:
    """Recover (stem, components) from EE's normalized label + its sort key.

    ``sort_key`` is ``(number, letter_suffix, sub_number)`` from
    ``default_label_sort_key``. A non-empty ``sub_number`` is a SUPERSCRIPT
    component (``71_1`` → sub 1); a non-empty ``letter_suffix`` is a LETTER
    component (``14a`` → 'a'). A bare stem has neither. The stem is the ``N``
    before the ``'_'`` (superscript form) else the leading digits (letter form)
    else the whole normalized key.
    """
    number, letter_suffix, sub_number = sort_key
    if "_" in normalized:
        stem = normalized.split("_", 1)[0]
    elif isinstance(letter_suffix, str) and letter_suffix:
        # Lettered form ``14a``: stem is the numeric prefix.
        stem = normalized[: len(normalized) - len(letter_suffix)]
    else:
        stem = normalized
    components: list[LabelComponent] = []
    if isinstance(sub_number, int) and sub_number > 0:
        components.append(("super", sub_number))
    if isinstance(letter_suffix, str) and letter_suffix:
        components.append(("letter", letter_suffix))
    return stem, tuple(components)


def ee_parse_label(label: str) -> ParsedLabel:
    """Parse an EE section/division label into the neutral ``ParsedLabel`` form.

    Normalizes the superscript surface (``§71¹`` / ``71 1`` → ``71_1``) via
    EE's own ``_normalize_num``, then decomposes with EE's authoritative
    ``default_label_sort_key`` (the sort key IS the order value) and the
    ``normalized_label_key`` identity key (the collision key). This binds the
    parse to EE's real code — the parse is a thin structuring over EE's own
    label primitives, not a re-implementation.
    """
    normalized = _normalize_num(label).strip()
    # ``_normalize_num`` may leave a leading ``§`` (``'§71¹'`` → ``'§71_1'``);
    # the numeric label the grafter stores/compares is section-symbol-free, so
    # strip a leading section symbol before decomposing (the label the tree
    # carries is ``71_1``, not ``§71_1``).
    if normalized.startswith("§"):
        normalized = normalized[1:].strip()
    sort_key = default_label_sort_key(normalized)
    collision_key = normalized_label_key(normalized)
    stem, components = _ee_stem_and_components(normalized, sort_key)
    return ParsedLabel(
        raw=label,
        stem=stem,
        components=components,
        sort_key=sort_key,
        collision_key=collision_key,
    )


def _ee_successor(
    existing: Sequence[ParsedLabel], anchor: Optional[object]
) -> ParsedLabel:
    """The admissible fresh EE superscript label for a stem-family insert.

    ``anchor`` names the stem the insert attaches to (a ``ParsedLabel`` or a raw
    stem string); absent it, the stem is inferred from the ``existing`` siblings
    (they must share one stem). Returns ``stem_(max_superscript + 1)`` — the next
    free superscript — mirroring ``_predecessor_rank``'s same-stem superscript
    ranking read as its inverse (fresh = one past the greatest present).

    Fails loud when the stem cannot be determined (no anchor and no existing
    siblings, or existing siblings spanning multiple stems) — an unbounded
    successor is not admissible, and silently guessing a stem is exactly the
    implicit-behaviour leak this seam closes.
    """
    if isinstance(anchor, ParsedLabel):
        stem = anchor.stem
    elif isinstance(anchor, str) and anchor:
        stem = ee_parse_label(anchor).stem
    else:
        stems = {sib.stem for sib in existing}
        if len(stems) != 1:
            raise ValueError(
                "EE successor needs a single anchor stem; got existing stems "
                f"{sorted(stems)!r} with no anchor"
            )
        stem = next(iter(stems))
    max_super = 0
    for sib in existing:
        if sib.stem != stem:
            continue
        for kind, value in sib.components:
            if kind == "super" and isinstance(value, int) and value > max_super:
                max_super = value
    fresh_super = max_super + 1
    return ee_parse_label(f"{stem}_{fresh_super}")


def build_ee_label_algebra() -> LabelAlgebra:
    """Construct EE's concrete label algebra (superscript / lettered stem family)."""
    return LabelAlgebra(
        jurisdiction="ee",
        parse_fn=ee_parse_label,
        successor_fn=_ee_successor,
    )


#: EE's concrete label algebra (module-level singleton; the frontend datum).
EE_LABEL_ALGEBRA: LabelAlgebra = build_ee_label_algebra()


def ee_label_sort_key(label: Optional[str]) -> Tuple[int, str, int]:
    """EE's authoritative sibling-order key, dispatched THROUGH the algebra (#186).

    Returns ``EE_LABEL_ALGEBRA.parse(label).sort_key`` — the algebra's ``order``
    operation packaged as the ``label -> key`` callable the grafter's
    positioning sites (``tree_ops.insert_sorted`` / the sibling-merge sorts)
    require. Routing those sites here makes the ``LabelAlgebra`` seam LOAD-BEARING
    for EE's insertion ordering (§2.1 O2: "the insertion POSITION comes from the
    label algebra's ordering") instead of them calling ``default_label_sort_key``
    directly.

    Signature MATCHES ``tree_ops.default_label_sort_key`` (``Optional[str] ->
    (int, str, int)``) so it is a drop-in ``sort_key_fn`` for ``insert_sorted``.
    A ``None`` label routes through the algebra's ``None``-normalization exactly
    as the primitive does (both yield the ``(-1, '', 0)`` sentinel key).

    BYTE-IDENTICAL to ``tree_ops.default_label_sort_key`` on the labels the
    grafter sorts: the algebra's parse is BUILT from EE's own
    ``default_label_sort_key`` (it applies ``_normalize_num`` first, which is a
    no-op on the already-normalized ``N`` / ``N_M`` / ``Na`` tree labels — and on
    range / whitespace / section-symbol surfaces alike, since
    ``default_label_sort_key`` itself re-normalizes via ``_norm``). The
    conformance test (``tests/test_label_algebra.py``) pins this equality, so a
    drift fails there. The EE parse always yields ``default_label_sort_key``'s
    ``(int, str, int)`` shape, so the return type is that concrete triple.
    """
    number, letter_suffix, sub_number = EE_LABEL_ALGEBRA.parse(label or "").sort_key
    return (number, letter_suffix, sub_number)
