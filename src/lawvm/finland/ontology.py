"""Finnish legal ontology for LawVM: the semantic constitution.

This module defines what counts as a Finnish legal unit, facet, and label
series.  It is the single source of truth for the Finland jurisdiction's
structural vocabulary.

It is NOT an XML schema, viewer schema, or parser convenience layer.
It answers: what units are legally real, which are citable, which are
amendable, how they are addressed, how labels work, where hierarchy ends,
what is a facet vs a unit, and what is law vs transport noise.

Normative source: PRO_FINLAND_ONTOLOGY_AND_PROFILE.md

Compatibility: the ``to_unit_registry()`` function produces the Finland
frontend's ``UnitRegistry`` shape.  The shared core keeps only the generic
registry machinery; Finland owns the concrete registry instance.

Usage
-----
    from lawvm.finland.ontology import (
        UNIT_ONTOLOGY, FACET_KINDS, HIERARCHY_ORDER,
        is_legal_unit, parent_kinds, can_carry_facet, is_amendable,
        to_unit_registry,
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, FrozenSet, Literal, Tuple

if TYPE_CHECKING:
    from lawvm.core.unit_registry import UnitRegistry


# ---------------------------------------------------------------------------
# Kind enumerations (string Literals, not enums, for JSON/dict compat)
# ---------------------------------------------------------------------------

FinlandUnitKind = Literal[
    "statute",
    "supplement",
    "part",
    "division",
    "chapter",
    "subdivision",
    "section",
    "subsection",
    "item",
    "subitem",
]

ALL_UNIT_KINDS: Tuple[str, ...] = (
    "statute",
    "supplement",
    "part",
    "division",
    "chapter",
    "subdivision",
    "section",
    "subsection",
    "item",
    "subitem",
)

FinlandFacetKind = Literal[
    "title",
    "heading",
    "crossheading",
    "intro",
    "wording",
    "table",
    "repeal_notice",
]

ALL_FACET_KINDS: Tuple[str, ...] = (
    "title",
    "heading",
    "crossheading",
    "intro",
    "wording",
    "table",
    "repeal_notice",
)

FinlandIdentityClass = Literal["stable_label", "implicit_ordinal", "facet"]

FinlandInsertionPolicy = Literal["suffix", "shift_ordinal", "inherit_host"]

FinlandLabelSeries = Literal[
    "insertable_arabic",
    "roman_ordinal",
    "alpha_sequence",
    "symbolic",
    "implicit_ordinal",
    "open",  # division/supplement: multiple series tolerated
]


# ---------------------------------------------------------------------------
# UnitOntologyEntry: one row of the ontology table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnitOntologyEntry:
    """Ontology specification for one Finnish legal unit kind."""

    kind: str
    fi_name: str
    identity_class: FinlandIdentityClass
    insertion_policy: FinlandInsertionPolicy
    repeal_compacts: bool  # always False for Finland
    label_series: Tuple[str, ...]  # allowed label series for this kind
    allowed_parents: Tuple[str, ...]
    allowed_facets: Tuple[str, ...]
    is_amendable: bool
    hierarchy_depth: int
    can_have_heading: bool = False
    can_have_intro: bool = False


# ---------------------------------------------------------------------------
# UNIT_ONTOLOGY: the canonical table
# ---------------------------------------------------------------------------

_UNIT_ENTRIES: Tuple[UnitOntologyEntry, ...] = (
    UnitOntologyEntry(
        kind="statute",
        fi_name="saados",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=(),
        allowed_parents=(),
        allowed_facets=("title", "wording"),
        is_amendable=True,
        hierarchy_depth=0,
    ),
    UnitOntologyEntry(
        kind="supplement",
        fi_name="liite",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("symbolic", "roman_ordinal", "insertable_arabic"),
        allowed_parents=("statute",),
        allowed_facets=("heading", "wording", "table"),
        is_amendable=True,
        hierarchy_depth=1,
        can_have_heading=True,
    ),
    UnitOntologyEntry(
        kind="part",
        fi_name="osa",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("roman_ordinal",),
        allowed_parents=("statute", "supplement"),
        allowed_facets=("heading", "wording"),
        is_amendable=True,
        hierarchy_depth=2,
        can_have_heading=True,
    ),
    UnitOntologyEntry(
        kind="division",
        fi_name="osasto",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("roman_ordinal", "insertable_arabic", "symbolic"),
        allowed_parents=("statute", "supplement", "part"),
        allowed_facets=("heading", "wording"),
        is_amendable=True,
        hierarchy_depth=3,
        can_have_heading=True,
    ),
    UnitOntologyEntry(
        kind="chapter",
        fi_name="luku",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("insertable_arabic",),
        allowed_parents=("statute", "supplement", "part", "division"),
        allowed_facets=("heading", "intro", "wording"),
        is_amendable=True,
        hierarchy_depth=4,
        can_have_heading=True,
        can_have_intro=True,
    ),
    UnitOntologyEntry(
        kind="subdivision",
        fi_name="jakso",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("insertable_arabic", "symbolic"),
        allowed_parents=("chapter", "supplement"),
        allowed_facets=("heading", "wording"),
        is_amendable=True,
        hierarchy_depth=5,
        can_have_heading=True,
    ),
    UnitOntologyEntry(
        kind="section",
        fi_name="pykala",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("insertable_arabic",),
        allowed_parents=(
            "statute", "supplement", "chapter", "subdivision",
            "part", "division",
        ),
        allowed_facets=("heading", "intro", "wording", "table"),
        is_amendable=True,
        hierarchy_depth=6,
        can_have_heading=True,
        can_have_intro=True,
    ),
    UnitOntologyEntry(
        kind="subsection",
        fi_name="momentti",
        identity_class="implicit_ordinal",
        insertion_policy="shift_ordinal",
        repeal_compacts=False,
        label_series=("insertable_arabic", "implicit_ordinal"),
        allowed_parents=("section",),
        allowed_facets=("intro", "wording", "table"),
        is_amendable=True,
        hierarchy_depth=7,
        can_have_intro=True,
    ),
    UnitOntologyEntry(
        kind="item",
        fi_name="kohta",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("insertable_arabic", "alpha_sequence"),
        allowed_parents=("section", "subsection"),
        allowed_facets=("intro", "wording", "table"),
        is_amendable=True,
        hierarchy_depth=8,
        can_have_intro=True,
    ),
    UnitOntologyEntry(
        kind="subitem",
        fi_name="alakohta",
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
        label_series=("alpha_sequence", "roman_ordinal"),
        allowed_parents=("item", "subitem"),
        allowed_facets=("intro", "wording"),
        is_amendable=True,
        hierarchy_depth=9,
        # Note: can_have_intro is False in the existing FINLAND_REGISTRY.
        # The Pro spec says intro is possible on list-bearing units, but
        # the existing pipeline does not currently model subitem intros.
        # Keep False for registry compatibility; the ontology's allowed_facets
        # already includes "intro" for future use.
        can_have_intro=False,
    ),
)

UNIT_ONTOLOGY: dict[str, UnitOntologyEntry] = {e.kind: e for e in _UNIT_ENTRIES}

# ---------------------------------------------------------------------------
# HIERARCHY_ORDER: canonical depth ordering
# ---------------------------------------------------------------------------

HIERARCHY_ORDER: Tuple[str, ...] = tuple(e.kind for e in _UNIT_ENTRIES)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def is_legal_unit(kind: str) -> bool:
    """Return True if *kind* is a legal hierarchy unit (not an analysis anchor)."""
    return kind in UNIT_ONTOLOGY


def parent_kinds(kind: str) -> Tuple[str, ...]:
    """Return the allowed parent unit kinds for *kind*.

    Returns an empty tuple for ``statute`` or unknown kinds.
    """
    entry = UNIT_ONTOLOGY.get(kind)
    return entry.allowed_parents if entry is not None else ()


def can_carry_facet(unit_kind: str, facet_kind: str) -> bool:
    """Return True if *unit_kind* may carry *facet_kind*."""
    entry = UNIT_ONTOLOGY.get(unit_kind)
    return entry is not None and facet_kind in entry.allowed_facets


def is_amendable(kind: str) -> bool:
    """Return True if *kind* is an amendment target."""
    entry = UNIT_ONTOLOGY.get(kind)
    return entry is not None and entry.is_amendable


def hierarchy_depth(kind: str) -> int:
    """Return the canonical hierarchy depth for *kind*, or -1 if unknown."""
    entry = UNIT_ONTOLOGY.get(kind)
    return entry.hierarchy_depth if entry is not None else -1


def allowed_label_series(kind: str) -> Tuple[str, ...]:
    """Return the label series allowed for *kind*."""
    entry = UNIT_ONTOLOGY.get(kind)
    return entry.label_series if entry is not None else ()


# ---------------------------------------------------------------------------
# to_unit_registry: bridge to lawvm.core.unit_registry
# ---------------------------------------------------------------------------

def to_unit_registry() -> "UnitRegistry":
    """Produce Finland's concrete ``UnitRegistry`` from the ontology.

    This maps the ontology entries to ``UnitSpec`` instances using the same
    unit_kind strings and identity/insertion/repeal semantics currently used
    by Finland intent validation.

    The mapping covers only unit kinds that appear in the existing registry
    (i.e. the subset that the core pipeline currently consumes).  Ontology
    kinds like ``statute``, ``division``, and ``subdivision`` are omitted
    because the existing registry does not include them and adding them would
    change the validation surface of current callers.
    """
    from lawvm.core.unit_registry import UnitRegistry, UnitSpec

    # Mapping from ontology kind -> registry kind where they differ
    _KIND_REMAP: dict[str, str] = {
        "supplement": "annex",
    }

    # Only emit kinds present in the current FINLAND_REGISTRY
    _EMIT_KINDS: FrozenSet[str] = frozenset({
        "part", "chapter", "section", "subsection",
        "item", "subitem", "supplement",
    })

    specs: list[UnitSpec] = []
    for entry in _UNIT_ENTRIES:
        if entry.kind not in _EMIT_KINDS:
            continue
        reg_kind = _KIND_REMAP.get(entry.kind, entry.kind)
        specs.append(UnitSpec(
            unit_kind=reg_kind,
            display_name=entry.fi_name,
            can_have_heading=entry.can_have_heading,
            can_have_intro=entry.can_have_intro,
            identity_class=entry.identity_class,
            insertion_policy=entry.insertion_policy,
            repeal_compacts=entry.repeal_compacts,
        ))

    # Add crossheading and row which are in the existing registry but not
    # in the ontology hierarchy (crossheading is modelled as a standalone
    # addressable unit; row is a table analysis anchor).
    specs.append(UnitSpec(
        unit_kind="crossheading",
        display_name="väliotsikko",
        can_have_heading=False,
        identity_class="stable_label",
        insertion_policy="suffix",
        repeal_compacts=False,
    ))
    specs.append(UnitSpec(
        unit_kind="row",
        display_name="rivi",
        identity_class="implicit_ordinal",
        insertion_policy="shift_ordinal",
        repeal_compacts=False,
    ))

    return UnitRegistry(
        unit_specs={s.unit_kind: s for s in specs},
        valid_facets=frozenset({"heading", "intro", "wording", "wrapUp"}),
        jurisdiction="FI",
    )
