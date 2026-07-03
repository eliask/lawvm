"""U.S. federal's concrete ``LabelAlgebra`` — numeric stem + letter suffix (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4. This encodes the U.S. Code SECTION-label calculus as data for the
neutral ``core.label_algebra.LabelAlgebra`` seam type, so the calculus that today
lives IMPLICITLY in the shared kernel label helpers the US frontend uses
(``core/tree_ops.default_label_sort_key`` for order, ``normalized_label_key`` for
the identity token) becomes a first-class, conformance-tested profile object — the
US analogue of ``estonia/label_algebra.EE_LABEL_ALGEBRA`` (superscript /
division-suffix) and ``finland/label_algebra.FI_LABEL_ALGEBRA`` (Arabic + letter
suffix).

US's section-label surface (VERIFIED against the SHARED kernel section primitives
the US frontend orders on):

* A U.S. Code section is written ``1181`` — an Arabic numeric stem. An inserted
  section is written ``106A`` — the same numeric stem with an optional trailing
  LETTER suffix (``106, 106A, 106B``; §1552 → ``1552``, added §1552a → ``1552A``).
  The US frontend carries these as bare section labels and orders them with the
  SHARED kernel key ``core.tree_ops.default_label_sort_key``, which decomposes
  ``106A`` → ``(106, 'a', 0)`` — the ``(number, letter_suffix, sub_number)`` triple
  (the ``sub_number`` slot is always ``0`` for US: the ``N_M`` compound-superscript
  form is an EE surface, not a US one).
* The identity token US collides on is the SHARED ``normalized_label_key``
  (``core.tree_ops._norm``: strips non-alphanumerics and lowercases, so ``'106A'``,
  ``'106 A'`` and ``'106a'`` all → ``'106a'``). Two labels collide iff those tokens
  are equal.
* The authoritative sibling ORDER is ``default_label_sort_key`` — under which
  ``106 < 106A < 106B < 107`` (a letter-suffixed inserted section sorts immediately
  after its numeric stem, before the next stem; the interleaved insert order).
* The admissible fresh SUCCESSOR for a letter-suffixed insert into a stem family is
  the next free trailing letter: an insert after ``106`` (bare stem) yields
  ``106A``; after ``106A`` yields ``106B``. This mirrors EE's superscript-successor
  shape (``stem_(max+1)``) transposed to letters: the fresh label is one letter past
  the greatest same-stem letter suffix present (or the FIRST letter ``a`` when the
  stem has no lettered sibling yet). This is SYNTHESIZED from the SHARED
  ``default_label_sort_key`` decomposition (US has NO standalone next-section-label
  helper — see the SCOPE note), exactly as EE synthesizes ``stem_(max+1)`` from its
  own sort-key components rather than calling a separate "successor" primitive.

SCOPE (declared-first, byte-identical). This is a DECLARED, conformance-tested
MIRROR of the US section-label code, NOT routed into any grafter / apply / replay
path (``tests/test_label_algebra_us.py`` binds each declared behaviour to the
SHARED ``default_label_sort_key`` / ``normalized_label_key`` code the US frontend
orders on, so it FAILS if that label logic drifts). It covers the numeric +
lettered stem-family calculus — the ``106A`` shape — which is the US inserted-
section surface the §4.2 seam names (``1181`` / ``106A``).

HONEST GAP (successor primitive). Unlike FI (``next_letter_label``) and EE
(``_predecessor_rank``), the US frontend has NO dedicated next-inserted-section-
label helper to bind to — a grep of ``src/lawvm/us_federal/`` finds none, only the
section-label SHAPE regexes (``amendatory._SECTION_BARE_LABEL_RE`` etc.). The US
successor here is therefore SYNTHESIZED from the shared sort-key decomposition (the
same primitive US orders on) plus a plain letter-increment — the minimal honest
construction, and structurally identical to EE's synthesized superscript successor.
Its letter-suffix handling matches ``default_label_sort_key``'s
``_LETTER_SUFFIX_SORT_LABEL_RE`` (``^(\\d+)([a-z]*)$``): a single-letter suffix
``a..z`` increments to the next letter; ``z`` has NO successor (fail-loud — US has
no admissible fresh single-letter label past ``z`` in the declared surface).

PLANE. This frontend module imports the neutral seam type from
``core.label_algebra`` (the kernel never imports a jurisdiction) plus the SHARED
core label helpers — the same import direction the θ tables and
``estonia/label_algebra.py`` / ``finland/label_algebra.py`` use.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from lawvm.core.label_algebra import LabelAlgebra, LabelComponent, ParsedLabel
from lawvm.core.tree_ops import default_label_sort_key, normalized_label_key

__all__ = [
    "US_LABEL_ALGEBRA",
    "build_us_label_algebra",
    "us_parse_label",
]


def _us_stem_and_components(
    sort_key: Tuple[int, str, int]
) -> tuple[str, tuple[LabelComponent, ...]]:
    """Recover (stem, components) from US's shared section sort key.

    ``sort_key`` is ``(number, letter_suffix, sub_number)`` from
    ``default_label_sort_key``. A non-empty ``letter_suffix`` is a LETTER component
    (``106A`` → 'a'); a bare stem has none. The stem is the string form of the
    number (``106``). US never carries the EE ``N_M`` superscript (``sub_number``)
    surface, so ``sub_number`` is expected ``0`` — but if a non-zero one ever
    appears it is faithfully surfaced as a ``super`` component rather than silently
    dropped (mirror EE's decomposition; the algebra never invents structure it did
    not receive from the real key). An unparseable label yields ``number == -1``
    (``default_label_sort_key``'s sentinel); its stem is that ``-1`` form and it
    carries no components — the algebra faithfully mirrors the shared key's "cannot
    parse" disposition rather than inventing structure.
    """
    number, letter_suffix, sub_number = sort_key
    stem = str(number)
    components: list[LabelComponent] = []
    if isinstance(sub_number, int) and sub_number > 0:
        components.append(("super", sub_number))
    if isinstance(letter_suffix, str) and letter_suffix:
        components.append(("letter", letter_suffix))
    return stem, tuple(components)


def us_parse_label(label: str) -> ParsedLabel:
    """Parse a US Code section label into the neutral ``ParsedLabel`` form.

    Threads the SHARED kernel section primitives the US frontend orders on:
    ``default_label_sort_key`` for the authoritative order value (the sort key IS
    the order value) and ``normalized_label_key`` for the collision key. The parse
    is a thin structuring over that real label code, not a re-implementation — so
    the algebra fails the conformance test the moment US's section-label ordering
    logic drifts.
    """
    sort_key = default_label_sort_key(label)
    collision_key = normalized_label_key(label)
    stem, components = _us_stem_and_components(sort_key)
    return ParsedLabel(
        raw=label,
        stem=stem,
        components=components,
        sort_key=sort_key,
        collision_key=collision_key,
    )


def _next_us_letter(letter: str) -> Optional[str]:
    """The next single trailing letter after ``letter`` (``'a'`` → ``'b'``).

    ``''`` (a bare numeric stem with no letter yet) → the FIRST letter ``'a'``.
    A single letter ``a..y`` increments; ``'z'`` returns ``None`` (US has no
    admissible fresh single-letter label past ``z`` in the declared surface).
    Matches ``default_label_sort_key``'s ``_LETTER_SUFFIX_SORT_LABEL_RE`` letter
    slot: a lowercase ``[a-z]`` run — here the US single-letter section-insert
    surface (``106A``, not ``106AA``); a multi-letter suffix is out of the declared
    US surface and yields ``None`` (fail-loud rather than a fabricated calculus).
    """
    if letter == "":
        return "a"
    if len(letter) != 1 or not ("a" <= letter <= "z"):
        return None
    if letter == "z":
        return None
    return chr(ord(letter) + 1)


def _us_successor(
    existing: Sequence[ParsedLabel], anchor: Optional[object]
) -> ParsedLabel:
    """The admissible fresh US letter-suffixed section label for a stem-family insert.

    ``anchor`` names the stem the insert attaches to (a ``ParsedLabel`` or a raw
    label string); absent it, the stem is inferred from the ``existing`` siblings
    (they must share one stem). Returns ``stem + next_letter`` — the next free
    trailing letter past the greatest same-stem letter present (or ``stem + 'a'``
    when the stem has no lettered sibling yet) — SYNTHESIZED from the shared
    ``default_label_sort_key`` decomposition (US has no standalone next-label
    helper), the letter transpose of EE's ``stem_(max+1)`` superscript successor.

    Fails loud when the stem cannot be determined (no anchor and no existing
    siblings, or existing siblings spanning multiple stems) and when the letter
    series is exhausted (a ``106Z`` anchor — no admissible fresh single-letter
    label past ``z``) — an unbounded / fabricated successor is not admissible, and
    silently guessing is exactly the implicit-behaviour leak this seam closes.
    """
    if isinstance(anchor, ParsedLabel):
        anchor_parsed: Optional[ParsedLabel] = anchor
        stem = anchor.stem
    elif isinstance(anchor, str) and anchor:
        anchor_parsed = us_parse_label(anchor)
        stem = anchor_parsed.stem
    else:
        anchor_parsed = None
        stems = {sib.stem for sib in existing}
        if len(stems) != 1:
            raise ValueError(
                "US successor needs a single anchor stem; got existing stems "
                f"{sorted(stems)!r} with no anchor"
            )
        stem = next(iter(stems))

    def _letter_of(parsed: ParsedLabel) -> str:
        for kind, value in parsed.components:
            if kind == "letter" and isinstance(value, str):
                return value
        return ""

    greatest_letter = ""
    # The greatest same-stem letter seen across the existing siblings AND the
    # anchor (an anchor that IS itself a lettered sibling — 106A → 106B — is the
    # position the insert follows; a bare-stem anchor 106 → 106A contributes '').
    candidates = list(existing)
    if anchor_parsed is not None:
        candidates.append(anchor_parsed)
    for sib in candidates:
        if sib.stem != stem:
            continue
        letter = _letter_of(sib)
        if letter > greatest_letter:
            greatest_letter = letter

    fresh_letter = _next_us_letter(greatest_letter)
    if fresh_letter is None:
        raise ValueError(
            f"US letter-suffix series exhausted at stem {stem!r} (greatest letter "
            f"{greatest_letter!r}): no admissible fresh single-letter label past 'z'"
        )
    return us_parse_label(f"{stem}{fresh_letter.upper()}")


def build_us_label_algebra() -> LabelAlgebra:
    """Construct US's concrete label algebra (numeric stem + lettered stem family)."""
    return LabelAlgebra(
        jurisdiction="us",
        parse_fn=us_parse_label,
        successor_fn=_us_successor,
    )


#: US's concrete label algebra (module-level singleton; the frontend datum).
US_LABEL_ALGEBRA: LabelAlgebra = build_us_label_algebra()
