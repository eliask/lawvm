"""U.K. legislation's concrete ``LabelAlgebra`` — numeric stem + letter insert (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4. This encodes the U.K. inserted-provision label calculus as data for
the neutral ``core.label_algebra.LabelAlgebra`` seam type, so the calculus that
today lives IMPLICITLY across ``uk_legislation/ordering.py`` (``_label_sort_key``),
``uk_legislation/canonicalize.py`` (``_clean_num``) and
``uk_legislation/source_parent_payloads.py`` (``_next_same_stem_alnum_label``)
becomes a first-class, conformance-tested profile object — the UK analogue of
``estonia/label_algebra.EE_LABEL_ALGEBRA`` and ``finland/label_algebra.FI_LABEL_ALGEBRA``.

UK's label surface (VERIFIED against the UK provision primitives at runtime):

* A UK provision is written ``4`` — a numeric stem. An inserted provision is
  written ``4A`` — the stem with a trailing LETTER insertion suffix (``4, 4A, 4B``),
  and a double-letter interstitial insert is written ``4ZA``. UK's identity /
  ordering primitive is ``ordering._label_sort_key``, which splits the
  ``_clean_num``-canonicalized label into alternating numeric / alphabetic runs:
  ``'4A'`` → ``((0, 4), (1, 'a'))``, ``'4ZA'`` → ``((0, 4), (1, 'za'))``,
  ``'4'`` → ``((0, 4),)`` (a numeric run is tagged ``0``, an alphabetic run ``1``).
* COLLISION is ``canonicalize._clean_num`` identity (strip / drop surrounding
  parens / NBSP→space / lowercase): ``'4A'``, ``'(4A)'`` and ``'4a'`` all →
  ``'4a'``. Two labels collide iff those tokens are equal.
* The authoritative sibling ORDER is ``_label_sort_key``. NOTE — this is a plain
  LEXICOGRAPHIC order over the alphabetic run, so the runtime order is
  ``4 < 4A < 4B < 4ZA`` (because ``'a' < 'b' < 'za'``), NOT a ``ZA < A``
  interstitial priority. The declared algebra binds to what UK's code ACTUALLY
  does; see the SCOPE note's honest-divergence paragraph.
* The admissible fresh SUCCESSOR for a letter-suffixed insert into a numeric stem
  family is ``source_parent_payloads._next_same_stem_alnum_label``: an insert after
  ``4`` (bare stem) yields ``4a``; after ``4a`` yields ``4b``; ``4z`` has NO
  successor (returns ``''`` — the single-letter series is exhausted). This is UK's
  real fresh-sibling calculus.

SCOPE (declared-first, byte-identical). This is a DECLARED, conformance-tested
MIRROR of UK's real label code, NOT routed into any grafter / apply / replay path
(``tests/test_label_algebra_uk.py`` binds each declared behaviour to UK's ACTUAL
``_label_sort_key`` / ``_clean_num`` / ``_next_same_stem_alnum_label`` code, so it
FAILS if that label logic drifts). It covers the numeric + lettered insert calculus
— the ``4A`` / ``4ZA`` shape.

HONEST DIVERGENCE (ZA < A). The #186 seam brief names the UK surface as ``4A`` /
``4ZA`` "with the ``ZA < A`` ordering". UK's REAL ``_label_sort_key`` does NOT
implement that interstitial priority: it is a plain natural (lexicographic) sort,
so ``4A < 4B < 4ZA`` at runtime. The `ZA < A` interstitial-insertion ordering (a
``4ZA`` slotting BEFORE ``4A``) is legal-drafting reality but is NOT encoded in
UK's ordering primitive today; this declared algebra faithfully mirrors the CODE,
not the brief — a declared-but-honest gap rather than a fabricated calculus. If UK
later teaches ``_label_sort_key`` the interstitial priority, the conformance test
(which pins ``order`` == the ``_label_sort_key`` compare) tracks the change
automatically.

PLANE. This frontend module imports the neutral seam type from
``core.label_algebra`` (the kernel never imports a jurisdiction) plus UK's own
ordering / canonicalize / source-payload helpers — the same import direction the θ
tables and ``estonia`` / ``finland`` label algebras use.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

from lawvm.core.label_algebra import LabelAlgebra, LabelComponent, ParsedLabel
from lawvm.uk_legislation.canonicalize import _clean_num
from lawvm.uk_legislation.ordering import _label_sort_key
from lawvm.uk_legislation.source_parent_payloads import _next_same_stem_alnum_label

__all__ = [
    "UK_LABEL_ALGEBRA",
    "build_uk_label_algebra",
    "uk_parse_label",
]


def _uk_stem_and_components(
    clean: str,
) -> tuple[str, tuple[LabelComponent, ...]]:
    """Recover (stem, components) from UK's ``_clean_num`` canonical label.

    A digits-then-lowercase-letters label decomposes to a numeric stem plus an
    optional LETTER component (``'4a'`` → stem ``4`` + letter ``'a'``; ``'4za'`` →
    stem ``4`` + letter ``'za'``, the double-letter interstitial suffix). A label
    that is not the ``digits(+letters)`` shape (a bare alphabetic label, an empty
    clean, a compound surface) carries the whole clean form as its stem and no
    components — the algebra faithfully mirrors "no numeric-stem decomposition"
    rather than inventing structure.

    Implemented as a PLAIN-STRING split (a leading digit run, then a trailing
    lowercase-letter run) rather than a compiled pattern — the same numeric-run /
    letter-run partition ``_label_sort_key`` performs, kept regex-free so it does
    not add to the regex/classifier surface (§telemetry ratchets).
    """
    digit_end = 0
    while digit_end < len(clean) and clean[digit_end].isdigit():
        digit_end += 1
    if digit_end == 0:
        # No leading digit run: not the numeric-stem shape.
        return clean, ()
    stem = clean[:digit_end]
    suffix = clean[digit_end:]
    if suffix and not all("a" <= ch <= "z" for ch in suffix):
        # Trailing run is not pure lowercase letters (a compound / spaced surface):
        # faithfully surface the whole clean form as the stem, no components.
        return clean, ()
    components: list[LabelComponent] = []
    if suffix:
        components.append(("letter", suffix))
    return stem, tuple(components)


def uk_parse_label(label: str) -> ParsedLabel:
    """Parse a UK provision label into the neutral ``ParsedLabel`` form.

    Threads UK's OWN provision primitives: ``ordering._label_sort_key`` for the
    authoritative order value (the sort key IS the order value) and
    ``canonicalize._clean_num`` for the collision key. The parse is a thin
    structuring over UK's real label code, not a re-implementation — so the algebra
    fails the conformance test the moment UK's provision-label logic drifts.
    """
    clean = _clean_num(label)
    sort_key: Tuple[Any, ...] = _label_sort_key(label)
    stem, components = _uk_stem_and_components(clean)
    return ParsedLabel(
        raw=label,
        stem=stem,
        components=components,
        sort_key=sort_key,
        collision_key=clean,
    )


def _uk_successor(
    existing: Sequence[ParsedLabel], anchor: Optional[object]
) -> ParsedLabel:
    """The admissible fresh UK letter-suffixed label for a numeric-stem insert.

    ``anchor`` names the label the insert attaches to (a ``ParsedLabel`` or a raw
    label string) — the greatest sibling in the stem family; absent it, the anchor
    is inferred from the ``existing`` siblings (the label with the greatest sort
    key). Returns ``_next_same_stem_alnum_label(anchor)`` — UK's real fresh-sibling
    calculus (``4`` → ``4a``; ``4a`` → ``4b``).

    Fails loud when the single-letter series is exhausted
    (``_next_same_stem_alnum_label`` returns ``''`` for a ``4z`` anchor — UK has no
    admissible fresh single-letter label past ``z`` in this calculus) and when no
    anchor can be determined (no anchor and no existing siblings) — an unbounded
    successor is not admissible, and silently guessing is exactly the
    implicit-behaviour leak this seam closes.
    """
    if isinstance(anchor, ParsedLabel):
        anchor_label = anchor.raw
    elif isinstance(anchor, str) and anchor:
        anchor_label = anchor
    else:
        if not existing:
            raise ValueError(
                "UK successor needs an anchor or a non-empty existing sibling set"
            )
        greatest = max(existing, key=lambda sib: sib.sort_key)
        anchor_label = greatest.raw
    fresh = _next_same_stem_alnum_label(_clean_num(anchor_label))
    if not fresh:
        raise ValueError(
            f"UK letter-suffix series exhausted at anchor {anchor_label!r}: no "
            "admissible fresh single-letter same-stem sibling"
        )
    return uk_parse_label(fresh)


def build_uk_label_algebra() -> LabelAlgebra:
    """Construct UK's concrete label algebra (numeric stem + lettered insert)."""
    return LabelAlgebra(
        jurisdiction="uk",
        parse_fn=uk_parse_label,
        successor_fn=_uk_successor,
    )


#: UK's concrete label algebra (module-level singleton; the frontend datum).
UK_LABEL_ALGEBRA: LabelAlgebra = build_uk_label_algebra()
