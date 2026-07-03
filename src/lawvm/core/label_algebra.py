"""The neutral ``LabelAlgebra`` seam type — the jurisdiction's label calculus (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4
("The label algebra") and §7 delta #4 ("LabelAlgebra as a typed profile object …
absorbing superscript-vs-letter insertion and stem families"). §4.2 names this
"the second clearest missing seam object after MOVE".

WHAT THIS IS. An INSERT's precondition (§2.1 O2) requires that ``label(a)`` be
*admissible fresh* under "the jurisdiction's label algebra", and that the
insertion POSITION come "from the label algebra's ordering". That algebra — what
counts as a fresh sibling name (an inserted FI section is ``14 a §``, an EE one
``§ 10¹``, a US one ``1181`` / ``106A``, a UK one ``4A`` / ``4ZA``), the
insertion position, collision detection, and stem-family grouping (``14, 14a,
14b``) — is legally load-bearing and differs per jurisdiction. Today it lives
IMPLICITLY in scattered per-frontend relabel/collision code (EE
``_ee_relabel_duplicate_division_suffix_before_insert``,
``estonia/grafter.py``). This module makes it a FIRST-CLASS, typed, neutral seam
object: a frontend supplies the concrete algebra as DATA (the component
functions), and the kernel gets the four operations §4.2 names:

    parse         : str                         -> ParsedLabel
    order         : (ParsedLabel, ParsedLabel)  -> int   (the authoritative
                                                          sibling order)
    successor_set : (Sequence[ParsedLabel], anchor) -> ParsedLabel
                    (the admissible fresh sibling — the "insert after 14 → 14a"
                    calculus)
    collides      : (ParsedLabel, Sequence[ParsedLabel]) -> bool

``ParsedLabel`` is the neutral STRUCTURED, comparable form: a stem (the base
number/token) plus an ordered tuple of ``LabelComponent`` suffixes (a
superscript ``10¹`` → ``[('super', 1)]``; a letter ``14a`` → ``[('letter',
'a')]``). Two labels COLLIDE iff their normalized identity keys are equal
(``SlotIdentity`` = (parent path, kind, normalized label), ``core/occupancy``);
the ``collision_key`` on ``ParsedLabel`` is that per-label normalized key, so
collision is neutral equality over the parsed form.

WHAT THIS IS NOT (this increment). This is PARALLEL-FIRST, the same discipline
θ (``core/totalization``) and CTSF (``core/ctsf_residual_report``) used: the
neutral type is DEFINED and ONE frontend (Estonia) is ENCODED + conformance-
tested, but the grafter relabel/collision path is NOT yet routed through it.
The existing per-frontend relabel code stays the source of truth; the declared
algebra is a conformance-tested MIRROR (``tests/test_label_algebra.py`` binds
``EE_LABEL_ALGEBRA`` to EE's ACTUAL parse/order/collision decisions, so it FAILS
if EE's label logic drifts from the declaration). Routing the grafter through
the algebra, and encoding the FI ``14 a §`` / US / UK label algebras, are the
explicit follow-ups.

PLANE & DISCIPLINE (AGENTS.md §0-§2). Jurisdiction-neutral: this core module
imports NO jurisdiction package — the per-frontend algebras live in the frontend
packages (``estonia/label_algebra.py``) and import this neutral type, mirroring
the spec-ledger's lazy self-registration registry (the kernel must not import
frontends) and the θ tables. Pure, typed, frozen, deterministic; fail-loud on
shape-invalid input (an algebra with no ``parse`` supplied raises at
construction).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Tuple

__all__ = [
    "LabelComponent",
    "ParsedLabel",
    "LabelAlgebra",
]


#: A single suffix component of a parsed label, past the stem. The ``kind`` is a
#: free-form tag the frontend chooses (``"super"`` for a superscript ``10¹``,
#: ``"letter"`` for a lettered ``14a``); the ``value`` is the component's
#: *ordinal* form — an ``int`` for a superscript (1 for ``¹``) or the letter
#: string for a lettered suffix — chosen so a plain tuple comparison over
#: components yields the authoritative order (``[]`` < ``[('super', 1)]`` <
#: ``[('letter', 'a')]`` is exactly what the frontend's ``order`` decides, not
#: this tuple — this datum only *carries* the decomposition; ``order`` on the
#: algebra is authoritative).
LabelComponent = Tuple[str, object]


@dataclass(frozen=True, slots=True)
class ParsedLabel:
    """A neutral, structured, comparable decomposition of a label string.

    ``raw`` is the original label; ``stem`` is the base number/token
    (``"71"`` for ``§71¹``, ``"14"`` for ``14a``); ``components`` is the ordered
    tuple of ``LabelComponent`` suffixes past the stem (empty for a bare stem).
    ``sort_key`` is the frontend's authoritative sort key for this label — the
    value ``order`` compares (so a frontend that already has a total ``label ->
    key`` function, like EE's ``default_label_sort_key``, threads it here and
    ``order`` is a pure tuple compare). ``collision_key`` is the per-label
    NORMALIZED identity key (the ``SlotIdentity`` label component,
    ``core/occupancy``): two labels collide iff their ``collision_key`` s are
    equal.

    Frozen and hashable so parsed labels can be set/dict keys (collision
    detection is a membership test over ``collision_key`` s).
    """

    raw: str
    stem: str
    components: Tuple[LabelComponent, ...]
    #: The frontend's authoritative sort key — a heterogeneous comparable tuple
    #: (EE's ``default_label_sort_key`` yields ``(int, str, int)``). Typed
    #: ``Any`` element so ``order`` can compare it (a homogeneous ``object``
    #: tuple is not orderable); the frontend guarantees a total order over the
    #: keys it produces (EE's is total by ``default_label_sort_key``).
    sort_key: Tuple[Any, ...]
    collision_key: str


@dataclass(frozen=True, slots=True)
class LabelAlgebra:
    """A frozen, jurisdiction-neutral label calculus (§4.2 item 4).

    The frontend supplies the concrete calculus as DATA — four pure component
    callables — and this type exposes the four §4.2 operations over them:

    * ``parse(label)``            → ``ParsedLabel`` (decompose stem + suffixes).
    * ``order(a, b)``             → ``-1 / 0 / 1`` (authoritative sibling order,
                                    a pure compare of ``a.sort_key`` vs
                                    ``b.sort_key``).
    * ``successor_set(existing, anchor)`` → ``ParsedLabel`` (the admissible fresh
                                    sibling given the existing siblings and an
                                    optional anchor — the "insert after 14 → 14a"
                                    calculus; delegated to ``successor_fn``).
    * ``collides(candidate, existing)``   → ``bool`` (collision detection via
                                    normalized ``collision_key`` equality).

    ``jurisdiction`` is a free-form tag (``"ee"``) so a registry / the
    spec-ledger can attribute the algebra without importing the frontend. The
    constructor fails loud if any component callable is missing (a label algebra
    with no ``parse`` is unrepresentable — the point of the seam).
    """

    jurisdiction: str
    parse_fn: Callable[[str], ParsedLabel]
    successor_fn: Callable[[Sequence[ParsedLabel], Optional[object]], ParsedLabel] = field(
        repr=False
    )

    def __post_init__(self) -> None:
        if not self.jurisdiction:
            raise ValueError("LabelAlgebra requires a non-empty jurisdiction tag")
        if self.parse_fn is None:  # pragma: no cover - defensive (typed non-None)
            raise ValueError("LabelAlgebra requires a parse function")
        if self.successor_fn is None:  # pragma: no cover - defensive
            raise ValueError("LabelAlgebra requires a successor function")

    # -- The four §4.2 operations ------------------------------------------

    def parse(self, label: str) -> ParsedLabel:
        """Decompose ``label`` into its neutral structured ``ParsedLabel`` form."""
        return self.parse_fn(label)

    def order(self, a: ParsedLabel, b: ParsedLabel) -> int:
        """The authoritative sibling order: ``-1`` if ``a`` < ``b``, ``1`` if
        ``a`` > ``b``, else ``0`` — a pure compare of the frontend sort keys.

        This is total and self-consistent by construction (it delegates to the
        Python tuple order over ``sort_key``), so it satisfies the order-relation
        laws (irreflexivity of ``<``, transitivity, totality) that the
        conformance test pins.
        """
        if a.sort_key < b.sort_key:
            return -1
        if a.sort_key > b.sort_key:
            return 1
        return 0

    def successor_set(
        self, existing: Sequence[ParsedLabel], anchor: Optional[object] = None
    ) -> ParsedLabel:
        """The admissible fresh sibling label given the ``existing`` siblings.

        Delegates to the frontend ``successor_fn`` (the "insert after 14 → 14a"
        calculus — for EE, the next free superscript ``stem_(max+1)``). ``anchor``
        is the optional sibling the insert is positioned relative to (§2.1 O2:
        anchor given ⇒ anchor determines position; anchor absent ⇒ position from
        the ordering).
        """
        return self.successor_fn(existing, anchor)

    def collides(
        self, candidate: ParsedLabel, existing: Sequence[ParsedLabel]
    ) -> bool:
        """Whether ``candidate`` collides with any of the ``existing`` siblings.

        Collision is normalized-identity equality (the ``SlotIdentity`` label
        component): ``candidate`` collides iff some existing sibling shares its
        ``collision_key``. Neutral — no jurisdiction logic; the frontend's
        ``parse`` already produced the normalized key.
        """
        candidate_key = candidate.collision_key
        return any(sibling.collision_key == candidate_key for sibling in existing)
