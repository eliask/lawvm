"""surface_model — Phase 3 surface clause data model for Finnish amendment clauses.

This module defines the typed surface node model that replaces the current
ParsedOp-centric parser output.  Surface nodes represent what the parser
*sees in the token stream* without flattening to ops or resolving context.

Architecture (from PRO_FI_PEG_VPRI_2026-04-07.md, Phase 3):

    tokens -> AnnotatedTape/View -> SurfaceClause -> ResolvedSurfaceClause -> ClauseAST

SurfaceClause is Finland-local and private: it holds Finnish phenomena like
backrefs, consequence tails, heading placements, renumber/move tails, and
meta clauses as explicit typed nodes before lowering.

The current clause_surface.py types (SurfaceTarget, SurfaceBackref,
SurfaceValiotsikkoRef, SurfaceVerbGroup, SurfaceClause) carry ParsedOps inside
SurfaceTarget and are already used in the Phase 4 resolver.  The types
defined here are the Phase 3 replacement: rich enough to represent all
parser phenomena without information loss and without early flattening to
ParsedOps.

Migration path:
    1. Parser rules start emitting SurfaceNode types from this module.
    2. lower_surface.py bridges SurfaceClause -> list[ParsedOp] while
       downstream consumers still expect ParsedOps.
    3. Once all rules emit surface nodes, clause_surface.py types become
       thin wrappers or are replaced entirely.

All types are frozen dataclasses using tuples for immutable sequences.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, Union

from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.semantic_types import FacetKind, MetaClauseKind
from lawvm.finland.source_verb import SourceVerb


# ---------------------------------------------------------------------------
# VerbKind — the four Finnish amendment verbs
# ---------------------------------------------------------------------------


class VerbKind(Enum):
    """Finnish amendment verb classification.

    Maps to the verb codes used throughout the pipeline:
        M = muuttaa (replace/amend)
        K = kumota (repeal)
        L = lisätä (insert/add)
        S = siirtää (move/renumber)
        META = meta-only clause (no amendment verb; commencement, expiry, etc.)
    """

    MUUTTAA = "M"  # replace/amend
    KUMOTA = "K"  # repeal
    LISATA = "L"  # insert/add
    SIIRTAA = "S"  # move/renumber
    META = "META"  # meta-only (commencement, expiry, transition, delegation)

    @classmethod
    def from_code(cls, code: str | SourceVerb) -> "VerbKind":
        """Create from single-letter verb code (M/K/L/S), full verb name, or SourceVerb enum."""
        # If already a SourceVerb enum member, map to VerbKind
        if isinstance(code, SourceVerb):
            code_to_verb_kind = {
                SourceVerb.MUUTTAA: VerbKind.MUUTTAA,
                SourceVerb.KUMOTA: VerbKind.KUMOTA,
                SourceVerb.LISATA: VerbKind.LISATA,
                SourceVerb.SIIRTAA: VerbKind.SIIRTAA,
            }
            return code_to_verb_kind.get(code, VerbKind.META)
        # If already a VerbKind enum member, return it
        if isinstance(code, VerbKind):
            return code
        # Handle string repr of SourceVerb enum members (e.g., "SourceVerb.MUUTTAA")
        if isinstance(code, str) and code.startswith("SourceVerb."):
            source_verb_name = code.split(".")[1]
            source_verb_map = {
                "MUUTTAA": VerbKind.MUUTTAA,
                "KUMOTA": VerbKind.KUMOTA,
                "LISATA": VerbKind.LISATA,
                "SIIRTAA": VerbKind.SIIRTAA,
            }
            if source_verb_name in source_verb_map:
                return source_verb_map[source_verb_name]
        # Try single-letter mapping
        if code in ("M", "K", "L", "S", "META"):
            code_to_member = {
                "M": VerbKind.MUUTTAA,
                "K": VerbKind.KUMOTA,
                "L": VerbKind.LISATA,
                "S": VerbKind.SIIRTAA,
                "META": VerbKind.META,
            }
            return code_to_member[code]
        # Try full name mapping (case-insensitive)
        name_to_code = {
            "MUUTTAAN": "M",
            "MUUTTA": "M",
            "MUUTTAA": "M",
            "KUMOTAAN": "K",
            "KUMOTA": "K",
            "LISATAAN": "L",
            "LISATA": "L",
            "SIIRRETTAAN": "S",
            "SIIRTAA": "S",
        }
        upper_code = code.upper()
        if upper_code in name_to_code:
            for member in cls:
                if member.value == name_to_code[upper_code]:
                    return member
        raise ValueError(f"Unknown verb code: {code!r}")


# ---------------------------------------------------------------------------
# TargetKind — the structural target types
# ---------------------------------------------------------------------------


class TargetKind(Enum):
    """Finnish structural target type classification.

    Maps to the kind codes used in ParsedOp:
        P = pykälä (section §)
        L = luku (chapter)
        O = osa (part)
        N = nimike (title)
        A = liite (appendix)
    """

    SECTION = "P"  # pykälä §
    CHAPTER = "L"  # luku
    PART = "O"  # osa
    NIMIKE = "N"  # nimike (title of the statute)
    APPENDIX = "A"  # liite

    @classmethod
    def from_code(cls, code: str) -> TargetKind:
        """Create from single-letter kind code (P/L/O/N/A)."""
        for member in cls:
            if member.value == code:
                return member
        raise ValueError(f"Unknown kind code: {code!r}")

    @classmethod
    def for_leaf_kind(cls, leaf_kind: str) -> TargetKind:
        """Return the structural target kind for one canonical leaf kind."""
        return {
            "section": cls.SECTION,
            "subsection": cls.SECTION,
            "item": cls.SECTION,
            "chapter": cls.CHAPTER,
            "part": cls.PART,
            "nimike": cls.NIMIKE,
            "appendix": cls.APPENDIX,
        }.get(leaf_kind, cls.SECTION)

    def leaf_kind(self) -> str:
        """Return the canonical leaf kind for this structural target kind."""
        return {
            TargetKind.SECTION: "section",
            TargetKind.CHAPTER: "chapter",
            TargetKind.PART: "part",
            TargetKind.NIMIKE: "nimike",
            TargetKind.APPENDIX: "appendix",
        }[self]



# ---------------------------------------------------------------------------
# SurfaceSubRef — sub-reference within a section
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SurfaceSubRef:
    """Parsed sub-reference: momentti, item, or special qualifier.

    Represents the sub-part of a section being targeted:
    - momentti=2 -> "2 momentti" (subsection 2)
    - item="a" -> "a kohta" (item a)
    - facet=FacetKind.HEADING -> heading of the section
    - facet=FacetKind.INTRO -> johdantokappale (introductory paragraph)
    """

    momentti: int = 0  # 0 = whole section
    item: str = ""  # kohta identifier
    facet: Optional[FacetKind] = None  # FacetKind.HEADING, INTRO, or NONE
    special: str = ""  # Legacy: "otsikko", "johd" for backward compatibility


# ---------------------------------------------------------------------------
# SurfaceWitness — source span provenance for surface nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SurfaceWitness:
    """Provenance record for a surface node.

    Records which token span in the filtered stream this node was parsed from.

    Attributes:
        rule_id:     Construction rule that produced this node.
        source_span: (start, end) token indices in the filtered stream,
                     half-open [start, end).
    """

    rule_id: str = ""
    source_span: Optional[Tuple[int, int]] = None


# ---------------------------------------------------------------------------
# Surface node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SurfaceTargetRef:
    """A reference to a structural target (section, chapter, part, etc.).

    This is the workhorse node type: "7 §", "3 luvun 12 §:n 2 momentti",
    "II osan 1 luvun otsikko", etc.

    Attributes:
        kind:       Target type (section, chapter, part, nimike, appendix).
        label:      Primary number/label of the target (e.g., "12", "3a", "II").
        chapter:    Chapter context if any (e.g., "3" for "3 luvun 12 §").
        part:       Part context if any (e.g., "II" for "II osan ...").
        sub_refs:   Sub-references within the target (momentti, kohta, special).
                    Empty tuple means whole target.
        notes:      Parser-attached metadata (e.g., ("renumber_clause",)).
        move_clause_target_unit_kind: Typed move-tail destination kind, if
                    already known.
        is_exception: True when this target was parsed as a "lukuun ottamatta"
                    exclusion from a broader structural range.  Typed field;
                    the string "exception" in notes is kept for backward compat
                    but this field is the authority.
        renumber_dest:         Destination label for renumber ops.
        renumber_dest_chapter: Destination chapter for renumber/move families.
        renumber_dest_part:    Destination part for move families.
        witness:    Source span provenance.
    """

    kind: TargetKind
    label: str
    chapter: str = ""
    part: str = ""
    sub_refs: Tuple[SurfaceSubRef, ...] = ()
    notes: Tuple[str, ...] = ()
    move_clause_target_unit_kind: Optional[TargetUnitKind] = None
    is_exception: bool = False
    renumber_dest: str = ""
    renumber_dest_chapter: str = ""
    renumber_dest_part: str = ""
    witness: Optional[SurfaceWitness] = None


# ---------------------------------------------------------------------------
# Semantic enums for Finland-specific closed-world vocabularies (Pro #16)
#
# These typed enums replace string-based closed-world semantics.  Each
# member's .value matches the existing string used by current consumers.
# Defined here (before SurfaceScopeBlock and SurfaceBackRef) so the types
# are available when the dataclass field annotations are evaluated at import.
# ---------------------------------------------------------------------------


class ScopeKind(Enum):
    """Scope block kinds used in SurfaceScopeBlock and ResolvedScopeBlock."""

    CHAPTER = "chapter"
    PART = "part"


class BackRefArity(Enum):
    """Back-reference arity used in SurfaceBackRef.referent_type."""

    SINGULAR = "singular"
    PLURAL = "plural"


@dataclass(frozen=True, slots=True)
class SurfaceScopeBlock:
    """A scoping block that establishes chapter/part context for enclosed targets.

    Represents structures like "3 luvun 5, 7 ja 9 §" where "3 luvun" is the
    scope and the section references are the enclosed targets.

    Attributes:
        scope_kind: What kind of scope: "chapter" or "part".
        scope_label: The scope label (e.g., "3" for "3 luvun").
        targets:     The enclosed nodes (typically SurfaceTargetRef).
        witness:     Source span provenance.

    Typed accessor (Pro #16):
        typed_scope_kind  — returns ScopeKind for the scope_kind field.
    """

    scope_kind: ScopeKind  # ScopeKind.CHAPTER or ScopeKind.PART
    scope_label: str
    targets: Tuple[SurfaceNode, ...]
    witness: Optional[SurfaceWitness] = None

    @property
    def typed_scope_kind(self) -> "ScopeKind":
        """Return the ScopeKind enum for the scope_kind field."""
        return ScopeKind(self.scope_kind)


@dataclass(frozen=True, slots=True)
class SurfaceInsertion:
    """An insertion pattern: "N §:ään uusi M momentti", "lakiin uusi 5 a §", etc.

    Represents the parser recognizing an insertion target — the thing being
    inserted and where it goes.

    Attributes:
        kind:        Target type of the inserted entity.
        label:       Label of the inserted entity (e.g., "5a" for "uusi 5 a §").
        chapter:     Chapter context for the insertion.
        part:        Part context for the insertion.
        sub_target:  Sub-reference if inserting a sub-part (momentti, kohta).
        witness:     Source span provenance.
    """

    kind: TargetKind
    label: str
    chapter: str = ""
    part: str = ""
    sub_target: Optional[SurfaceSubRef] = None
    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceBackRef:
    """An unresolved back-reference: "mainitun pykälän 2 momentti".

    The parser recognizes a BACKREF token with sub-references but does NOT
    resolve which preceding section(s) it refers to.  Resolution is deferred
    to a post-parse pass.

    Attributes:
        referent_type: "singular" or "plural" (mainitun vs mainittujen).
        sub_refs:      The sub-references to apply to the resolved sections.
        witness:       Source span provenance.

    Typed accessor (Pro #16):
        typed_arity  — returns BackRefArity for the referent_type field.
    """

    referent_type: BackRefArity  # BackRefArity.SINGULAR or BackRefArity.PLURAL
    sub_refs: Tuple[SurfaceSubRef, ...] = ()
    witness: Optional[SurfaceWitness] = None

    @property
    def typed_arity(self) -> "BackRefArity":
        """Return the BackRefArity enum for the referent_type field."""
        return BackRefArity(self.referent_type)


@dataclass(frozen=True, slots=True)
class SurfaceHeadingPlacement:
    """A heading placement: "N §:n edelle uusi väliotsikko".

    Records that a heading is being inserted before a specific section,
    optionally with heading text.

    Attributes:
        target_section: The section before which the heading is placed (e.g., "53").
        heading_text:   The heading text if available (usually empty at parse time).
        chapter:        Chapter context.
        part:           Part context.
        witness:        Source span provenance.
    """

    target_section: str
    heading_text: str = ""
    chapter: str = ""
    part: str = ""
    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceMoveTail:
    """A move consequence tail: ", jotka samalla siirretään 5 lukuun".

    Records a move destination attached to preceding target refs.  The move
    tail does not stand alone — it modifies the immediately preceding target
    batch.

    Attributes:
        destination_chapter: Destination chapter label (e.g., "5").
        destination_part:    Destination part label (e.g., "I").
        witness:             Source span provenance.
    """

    destination_chapter: str = ""
    destination_part: str = ""
    witness: Optional[SurfaceWitness] = None
    move_clause_target_unit_kind: Optional[TargetUnitKind] = None

    def __post_init__(self) -> None:
        if self.move_clause_target_unit_kind not in (None, "chapter", "part"):
            raise ValueError(
                "SurfaceMoveTail.move_clause_target_unit_kind must be chapter, part, or None"
            )
        if self.move_clause_target_unit_kind == "chapter" and not self.destination_chapter:
            raise ValueError("SurfaceMoveTail chapter move tails require destination_chapter")
        if self.move_clause_target_unit_kind == "part" and not self.destination_part:
            raise ValueError("SurfaceMoveTail part move tails require destination_part")
        if not self.destination_chapter and not self.destination_part:
            raise ValueError("SurfaceMoveTail requires a destination chapter or part")


@dataclass(frozen=True, slots=True)
class SurfaceRenumberTail:
    """A renumber tail: "§:n numero 52:ksi" or "luvun numero 8:ksi".

    Records that a structural entity's label is being changed.

    Attributes:
        new_label: The destination label after renumbering.
        witness:   Source span provenance.
    """

    new_label: str
    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceMetaClause:
    """A meta/effect clause: temporal markers, commencement clauses, etc.

    These are non-structural clause components that carry temporal or
    procedural semantics.

    Attributes:
        kind: Classification (commencement, expiry, transition, delegation,
              or Finnish equivalents voimaantulo, siirtyma, valtuutus, other).
        text:      The raw text of the meta clause.
        witness:   Source span provenance.
    """

    kind: MetaClauseKind
    text: str = ""
    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceTextAmend:
    """A textual amendment: word/phrase replacement in statutory text.

    Finnish text amendments specify old text to be replaced with new text
    within a target provision.

    Attributes:
        target:   The target ref being text-amended.
        old_text: The text being replaced.
        new_text: The replacement text.
        witness:  Source span provenance.
    """

    target: Optional[SurfaceTargetRef] = None
    old_text: str = ""
    new_text: str = ""
    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceTargetVersionBinding:
    """Explicit per-target cited-version selector from provenance text.

    Finland source clauses sometimes split one amendment target list across
    different cited version owners, e.g. ``23 § laissa 195/2015 sekä 24 c,
    30 b ja 34 a § laissa 575/2018``. This sidecar preserves that selector
    ownership without pretending it is part of the structural target list.
    """

    target_labels: Tuple[str, ...]
    cited_statute_id: str
    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceValiotsikkoRef:
    """An unresolved valiotsikko heading back-reference.

    "sen edellä oleva väliotsikko" — resolved by looking at preceding
    targets and emitting heading ops for them.

    Attributes:
        witness: Source span provenance.
    """

    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceCrossVerbMoveTail:
    """A cross-verb-group move retarget: "siirretään muutettu N § M lukuun".

    Emitted when a siirretään verb group finds a matching section reference
    that should patch a prior verb group's target.  Resolution deferred to
    the resolver which scans preceding verb groups for matching section labels.

    Attributes:
        source_section_label:  The section label to match (e.g., "85b").
        destination_chapter:   Destination chapter label.
        destination_part:      Destination part label.
        witness:               Source span provenance.
    """

    source_section_label: str
    destination_chapter: str = ""
    destination_part: str = ""
    witness: Optional[SurfaceWitness] = None
    move_clause_target_unit_kind: Optional[TargetUnitKind] = None

    def __post_init__(self) -> None:
        if self.move_clause_target_unit_kind not in (None, "chapter", "part"):
            raise ValueError(
                "SurfaceCrossVerbMoveTail.move_clause_target_unit_kind must be chapter, part, or None"
            )
        if self.move_clause_target_unit_kind == "chapter" and not self.destination_chapter:
            raise ValueError("SurfaceCrossVerbMoveTail chapter move tails require destination_chapter")
        if self.move_clause_target_unit_kind == "part" and not self.destination_part:
            raise ValueError("SurfaceCrossVerbMoveTail part move tails require destination_part")
        if not self.destination_chapter and not self.destination_part:
            raise ValueError("SurfaceCrossVerbMoveTail requires a destination chapter or part")


@dataclass(frozen=True, slots=True)
class SurfaceRelabelFromContext:
    """An unresolved relabel: "joka siirretään [N luvun] M §:ksi".

    The parser recognizes the relabel syntax but cannot determine the
    source section — that requires context from preceding verb groups.
    Resolution is deferred to the resolver.

    Attributes:
        destination_label:   The destination section label (e.g., "61").
        destination_chapter: Destination chapter if explicit (e.g., "7").
        witness:             Source span provenance.
    """

    destination_label: str
    destination_chapter: str = ""
    witness: Optional[SurfaceWitness] = None


@dataclass(frozen=True, slots=True)
class SurfaceDescendantCoordination:
    """Coordination structure where a base target has multiple descendant arms.

    Represents structures like "5 §:n 1 ja 3 momentti sekä 2 momentin
    johdantokappale" where the base section (5 §) has coordinated sub-refs
    that may involve different sub-ref types.

    Attributes:
        base:    The base target reference.
        arms:    Coordinated descendant sub-references.
        witness: Source span provenance.
    """

    base: SurfaceTargetRef
    arms: Tuple[SurfaceSubRef, ...]
    witness: Optional[SurfaceWitness] = None


# ---------------------------------------------------------------------------
# SurfaceNode union type
# ---------------------------------------------------------------------------

SurfaceNode = Union[
    SurfaceTargetRef,
    SurfaceScopeBlock,
    SurfaceInsertion,
    SurfaceBackRef,
    SurfaceHeadingPlacement,
    SurfaceMoveTail,
    SurfaceRenumberTail,
    SurfaceMetaClause,
    SurfaceTextAmend,
    SurfaceValiotsikkoRef,
    SurfaceDescendantCoordination,
    SurfaceCrossVerbMoveTail,
    SurfaceRelabelFromContext,
]


# ---------------------------------------------------------------------------
# Top-level clause types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SurfaceVerbGroup:
    """One verb's target list with its surface nodes.

    Attributes:
        verb:  The amendment verb for this group.
        nodes: Ordered surface nodes parsed for this verb.
    """

    verb: VerbKind
    nodes: Tuple[SurfaceNode, ...]


@dataclass(frozen=True, slots=True)
class SurfaceClause:
    """Complete surface parse result.

    Contains the ordered list of verb groups as they appear in the source
    johtolause.  Some nodes may be unresolved (SurfaceBackRef, SurfaceValiotsikkoRef).

    meta_clauses and text_amend_clauses are top-level fields — they are
    supplementary to the structural verb groups, NOT members of any verb group.
    Putting them in verb_groups.nodes is architecturally incorrect because meta
    and text-amend phenomena are not verb-scoped: they describe the whole clause
    (commencement date, expiry, word replacement), not one amendment verb's targets.

    Attributes:
        verb_groups:        Ordered structural amendment verb groups.
        meta_clauses:       Meta/effect clauses (commencement, expiry, transition,
                            delegation).  Empty tuple when none present.
        text_amend_clauses: Textual amendment clauses (word/phrase replacements).
                            Empty tuple when none present.
        target_version_bindings:
                            Finland-local per-target cited-version selectors
                            preserved from provenance text.
        source_text:        The original source text (for diagnostics).
        consumed_count:     Filtered tokens consumed by the parser.
    """

    verb_groups: Tuple[SurfaceVerbGroup, ...]
    meta_clauses: Tuple[SurfaceMetaClause, ...] = ()
    text_amend_clauses: Tuple[SurfaceTextAmend, ...] = ()
    target_version_bindings: Tuple[SurfaceTargetVersionBinding, ...] = ()
    source_text: str = ""
    consumed_count: int = 0  # filtered tokens consumed by the parser
