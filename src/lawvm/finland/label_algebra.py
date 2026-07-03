"""Finland's concrete ``LabelAlgebra`` — Arabic + letter-suffix sections (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4. This encodes Finland's ACTUAL section-label calculus as data for the
neutral ``core.label_algebra.LabelAlgebra`` seam type, so the calculus that today
lives IMPLICITLY across ``finland/helpers.py`` (``_section_sort_key`` /
``_norm_num_token``) and ``finland/uncovered_recovery_support.py``
(``next_letter_label``) becomes a first-class, conformance-tested profile object —
the FI analogue of ``estonia/label_algebra.EE_LABEL_ALGEBRA`` (superscript /
division-suffix).

FI's label surface (VERIFIED against the FI section primitives):

* A Finnish inserted section is written ``14 a §`` — an Arabic stem with an
  optional lowercase LETTER suffix (``14, 14 a, 14 b``, the §1.6 stem family). The
  identity token FI compares on is the SUFFIX-COMPACT, section-symbol-free form
  ``14a`` (``helpers._norm_num_token``: ``'14 a §.'``, ``'14a'`` and ``'14 a §'``
  all → ``'14a'``; it also folds roman ``V osa`` → ``5osa`` and ``§ 1.`` → ``1``).
* The authoritative sibling ORDER is ``helpers._section_sort_key`` —
  ``(number, letter_suffix)`` — under which ``14 < 14a < 14b < 15``, i.e. a
  letter-suffixed insert sorts immediately after its stem, before the next stem
  (the interleaved §1.6 order; this agrees byte-for-byte with the core
  ``default_label_sort_key`` the grafter's ``insert_sorted`` uses on these
  labels, ``(14, 'a', 0)`` — the FI-specific key just drops the always-``0`` EE
  sub-number slot and pre-normalizes roman/``luku``/``§`` surfaces).
* COLLISION is normalized-token identity: ``_norm_num_token`` of the label. Two
  labels collide iff those tokens are equal (``'14 a §.'`` and ``'14a'`` are the
  same slot).
* The admissible fresh SUCCESSOR for a letter-suffix insert into a stem family is
  the next free letter: ``next_letter_label`` — an insert after ``14 §`` (no
  suffix) yields ``14a``; after ``14a`` yields ``14b``; ``14z`` has NO successor
  (returns ``None`` — the letter series is exhausted). This is FI's real
  fresh-sibling calculus (``uncovered_recovery_support.next_letter_label``, also
  duplicated verbatim as ``group_ops._next_letter_label``).

SCOPE (byte-identical; PARTIALLY load-bearing). This is a conformance-tested
profile of FI's real section-label code (``tests/test_label_algebra_fi.py`` binds
each declared behaviour to FI's ACTUAL ``_section_sort_key`` / ``_norm_num_token``
/ ``next_letter_label`` code, so it FAILS if FI's label logic drifts). It covers
the Arabic + lettered stem-family calculus — the ``14 a §`` shape — which is the
FI section surface the §4.2 seam names.

ROUTING (load-bearing, #206 tail). FI's ONE section-family sibling-ORDERING site —
``finland/merge.py``'s container-section sibling-merge sort — now dispatches its
``label -> key`` through ``fi_label_sort_key`` (``FI_LABEL_ALGEBRA.order``), so the
algebra is the load-bearing source of that insert ordering rather than a parallel
mirror, mirroring how EE routes its sibling-merge sorts through
``ee_label_sort_key``. Byte-identical: ``fi_label_sort_key`` is BUILT from
``_section_sort_key`` (the conformance test pins the equality).

STILL DIRECT (documented follow-ups). Unlike EE — whose algebra is built ON the
core ``default_label_sort_key`` so re-routing every ``insert_sorted`` positioning
is byte-identical — FI's algebra is built on ``_section_sort_key``, which
DISAGREES with ``default_label_sort_key`` on non-Arabic-section surfaces (``1
luku`` → ``(1, '')`` vs ``(1, 'luku', 0)``; ``V osa`` → ``(5, '')`` vs
``(-1, 'vosa', 0)``; roman ``ii`` → ``(2, '')`` vs ``(-1, 'ii', 0)``). Routing
FI's generic ``insert_sorted`` (chapter / part / roman) sites — which pass the
core ``default_label_sort_key`` — through the FI section algebra would therefore
CHANGE replay ordering, so those stay direct. The ``successor_set`` fresh-letter
mint (``next_letter_label``) also stays direct: its call sites
(``uncovered_recovery_runner.py`` / ``group_ops.py``) treat a ``None`` (series
exhausted) result as a graceful skip, whereas the algebra's ``_fi_successor`` is
fail-loud on that same case — routing would convert a skip into an exception. The
typed leaf-label algebra (``finland/labels.py``'s ``InsertableArabic`` /
``AlphaSequence`` items / subitems, roman parts) also remains a follow-up.

PLANE. This frontend module imports the neutral seam type from
``core.label_algebra`` (the kernel never imports a jurisdiction) plus FI's own
section-label helpers — the same import direction the θ tables and
``estonia/label_algebra.py`` use.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from lawvm.core.label_algebra import LabelAlgebra, LabelComponent, ParsedLabel
from lawvm.finland.helpers import _norm_num_token, _section_sort_key
from lawvm.finland.uncovered_recovery_support import next_letter_label

__all__ = [
    "FI_LABEL_ALGEBRA",
    "build_fi_label_algebra",
    "fi_parse_label",
    "fi_label_sort_key",
]


def _fi_stem_and_components(
    sort_key: Tuple[int, str]
) -> tuple[str, tuple[LabelComponent, ...]]:
    """Recover (stem, components) from FI's section sort key.

    ``sort_key`` is ``(number, letter_suffix)`` from ``_section_sort_key``. A
    non-empty ``letter_suffix`` is a LETTER component (``14a`` → 'a'); a bare stem
    has none. The stem is the string form of the number (``14``). An unparseable
    label yields ``number == -1`` (``_section_sort_key``'s sentinel); its stem is
    that ``-1`` form and it carries no components — the algebra faithfully mirrors
    FI's own "cannot parse" disposition rather than inventing structure.
    """
    number, letter_suffix = sort_key
    stem = str(number)
    components: list[LabelComponent] = []
    if letter_suffix:
        components.append(("letter", letter_suffix))
    return stem, tuple(components)


def fi_parse_label(label: str) -> ParsedLabel:
    """Parse an FI section label into the neutral ``ParsedLabel`` form.

    Threads FI's OWN section primitives: ``_section_sort_key`` for the
    authoritative order value (the sort key IS the order value) and
    ``_norm_num_token`` for the collision key. The parse is a thin structuring
    over FI's real label code, not a re-implementation — so the algebra fails the
    conformance test the moment FI's section-label logic drifts.
    """
    sort_key = _section_sort_key(label)
    collision_key = _norm_num_token(label)
    stem, components = _fi_stem_and_components(sort_key)
    return ParsedLabel(
        raw=label,
        stem=stem,
        components=components,
        sort_key=sort_key,
        collision_key=collision_key,
    )


def _fi_successor(
    existing: Sequence[ParsedLabel], anchor: Optional[object]
) -> ParsedLabel:
    """The admissible fresh FI letter-suffixed label for a stem-family insert.

    ``anchor`` names the label the insert attaches to (a ``ParsedLabel`` or a raw
    label string) — the greatest sibling in the stem family; absent it, the anchor
    is inferred from the ``existing`` siblings (the label with the greatest sort
    key). Returns ``next_letter_label(anchor)`` — FI's real fresh-sibling calculus
    (``14 §`` → ``14a``; ``14a`` → ``14b``).

    Fails loud when the letter series is exhausted (``next_letter_label`` returns
    ``None`` for a ``14z`` anchor — FI has no admissible fresh label past ``z``)
    and when no anchor can be determined (no anchor and no existing siblings) — an
    unbounded successor is not admissible, and silently guessing is exactly the
    implicit-behaviour leak this seam closes.
    """
    if isinstance(anchor, ParsedLabel):
        anchor_label = anchor.raw
    elif isinstance(anchor, str) and anchor:
        anchor_label = anchor
    else:
        if not existing:
            raise ValueError(
                "FI successor needs an anchor or a non-empty existing sibling set"
            )
        greatest = max(existing, key=lambda sib: sib.sort_key)
        anchor_label = greatest.raw
    fresh = next_letter_label(anchor_label)
    if fresh is None:
        raise ValueError(
            f"FI letter-suffix series exhausted at anchor {anchor_label!r}: no "
            "admissible fresh sibling past 'z'"
        )
    return fi_parse_label(fresh)


def build_fi_label_algebra() -> LabelAlgebra:
    """Construct FI's concrete label algebra (Arabic + lettered stem family)."""
    return LabelAlgebra(
        jurisdiction="fi",
        parse_fn=fi_parse_label,
        successor_fn=_fi_successor,
    )


#: FI's concrete label algebra (module-level singleton; the frontend datum).
FI_LABEL_ALGEBRA: LabelAlgebra = build_fi_label_algebra()


def fi_label_sort_key(label: str) -> Tuple[int, str]:
    """FI's authoritative section sibling-order key, dispatched THROUGH the algebra.

    Returns ``FI_LABEL_ALGEBRA.parse(label).sort_key`` — the algebra's ``order``
    operation packaged as the ``label -> key`` callable FI's section-family
    positioning site (``finland/merge.py``'s container-section sibling-merge sort)
    requires. Routing that site here makes the ``LabelAlgebra`` seam LOAD-BEARING
    for FI's section-insert ORDERING (§2.1 O2: "the insertion POSITION comes from
    the label algebra's ordering") instead of it calling FI's raw
    ``helpers._section_sort_key`` primitive directly — the FI analogue of EE's
    ``estonia/label_algebra.ee_label_sort_key`` routing through
    ``EE_LABEL_ALGEBRA``.

    Signature MATCHES ``helpers._section_sort_key`` (``str -> (int, str)``) so it
    is a drop-in ``key=`` callable at that sort site.

    BYTE-IDENTICAL to ``helpers._section_sort_key`` on every label: the algebra's
    ``parse`` is BUILT from ``_section_sort_key`` (``fi_parse_label`` sets
    ``sort_key = _section_sort_key(label)``), and the conformance test
    (``tests/test_label_algebra_fi.py``) pins that equality, so a drift fails
    there. The FI parse always yields ``_section_sort_key``'s ``(int, str)``
    shape (``number``, ``letter_suffix``), so the return type is that concrete
    pair.

    NOTE (why this is section-scoped, not a ``default_label_sort_key`` drop-in):
    unlike EE — whose algebra is built ON the core ``default_label_sort_key`` and
    so re-routes every ``insert_sorted`` positioning byte-identically — FI's
    algebra is built on ``_section_sort_key``, which DISAGREES with
    ``default_label_sort_key`` on non-Arabic-section surfaces (``1 luku`` →
    ``(1, '')`` vs ``(1, 'luku', 0)``; ``V osa`` → ``(5, '')`` vs
    ``(-1, 'vosa', 0)``; roman ``ii`` → ``(2, '')`` vs ``(-1, 'ii', 0)``).
    Routing FI's generic ``insert_sorted`` (chapter/part/roman) sites through this
    key would therefore CHANGE replay ordering. So this key is routed ONLY at the
    one FI site that already keys on ``_section_sort_key`` (the section-family
    merge sort), where byte-identity holds by construction. The generic
    ``insert_sorted`` reroute — and the ``successor_set`` fresh-letter mint (whose
    ``next_letter_label`` ``None``-exhaustion is handled gracefully at its call
    sites, but is fail-loud in the algebra's ``_fi_successor``) — remain direct,
    documented follow-ups.
    """
    number, letter_suffix = FI_LABEL_ALGEBRA.parse(label).sort_key
    return (number, letter_suffix)
