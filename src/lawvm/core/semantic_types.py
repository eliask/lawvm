"""Typed semantic enums for closed-world vocabularies in the LawVM kernel.

These enums replace string-based closed-world semantics (action kinds, address
units, scope kinds, etc.) with proper typed values.  Each enum member's
``.value`` matches the existing string used by the current boundary code, so
core can treat the enum as the governed vocabulary while adapters still bridge
boundary strings at ingress when needed.

Enum design:
    - ``enum.value`` matches the existing string used at the boundary.
    - Enums are in ``core/`` because their vocabulary is shared across
      jurisdictions.  Jurisdiction-specific enums live in the frontend.
    - Use typed comparisons or ``.value`` explicitly; string equality is not a
      substitute for these core enums.
"""

from __future__ import annotations
from typing import Optional, Protocol
from typing_extensions import override

from dataclasses import dataclass
from enum import Enum


class _TextPatchKindCarrier(Protocol):
    """Structural view of a ``TextPatchSpec`` for the text-patch predicates."""

    kind: "TextPatchKindEnum"


class _TextActionOpCarrier(Protocol):
    """Structural view of a ``LegalOperation`` for the text-action helpers.

    A typed carrier (FW-09) that names exactly the two fields the §2.1 O6
    text-patch predicates read — the op's ``action`` and its optional
    ``text_patch`` — without importing ``core.ir`` (which would form a cycle).
    """

    @property
    def action(self) -> "StructuralAction | str": ...

    @property
    def text_patch(self) -> Optional[_TextPatchKindCarrier]: ...


# ---------------------------------------------------------------------------
# StructuralAction -- operation action kinds (structural layer)
# ---------------------------------------------------------------------------


class StructuralAction(Enum):
    """Structural and text operation action kinds.

    Core structural operations: replace, repeal, insert, renumber, meta.
    Text-level operations: a single ``text_patch`` action (§2.1 O6). The former
    ``text_replace`` / ``text_repeal`` duality was redundant — the sole
    discriminator is ``TextPatchSpec.kind`` (REPLACE/DELETE/APPEND), not the
    action name — so both collapsed into one ``TEXT_PATCH`` action. The legacy
    ``"text_replace"`` / ``"text_repeal"`` value strings remain accepted by
    ``structural_action_from_str`` as aliases → ``TEXT_PATCH`` so persisted
    action rows (NZ/UK) round-trip. ``HEADING_REPLACE`` is left untouched: it is
    facet-addressed, a different mechanism with no redundant duality.
    """

    REPLACE = "replace"
    REPEAL = "repeal"
    INSERT = "insert"
    RENUMBER = "renumber"
    # First-class relocation (§2.1 O5): relocate a subtree to a new parent,
    # possibly relabelling. Distinct from RENUMBER (same-parent sibling relabel):
    # a MOVE carries a ``destination`` address naming the new parent container.
    # No frontend lowers directly to this member today — FI realizes its MOVE
    # semantics through ``CanonicalIntent.Move`` at the intent layer — so it adds
    # no runtime dispatch path; it completes the neutral structural vocabulary and
    # is the guarded home for a ``LegalOperation.destination`` outside RENUMBER.
    MOVE = "move"
    HEADING_REPLACE = "heading_replace"
    META = "meta"
    # Single text-level patch action (§2.1 O6). Discriminated solely by
    # ``TextPatchSpec.kind``: REPLACE/APPEND (the former TEXT_REPLACE) or DELETE
    # (the former TEXT_REPEAL). Use ``is_text_patch_replace`` / ``is_text_patch_delete``
    # to recover the former action-level distinction faithfully.
    TEXT_PATCH = "text_patch"

    @override
    def __str__(self) -> str:
        return self.value


#: Legacy boundary action strings that predate the TEXT_PATCH collapse. They
#: are still emitted into persisted proof/effect rows (NZ/UK) minted before the
#: collapse, so the codec must keep parsing them → ``TEXT_PATCH``.
_LEGACY_TEXT_PATCH_ALIASES: frozenset[str] = frozenset({"text_replace", "text_repeal"})


# ---------------------------------------------------------------------------
# StructuralAction string codec (jurisdiction-neutral)
# ---------------------------------------------------------------------------
#
# Frontends carry their lowering decisions as the boundary action strings
# (``"replace"`` / ``"repeal"`` / ``"insert"`` / ``"renumber"`` /
# ``"text_replace"`` / ``"text_repeal"`` / ``"heading_replace"`` / ``"meta"``)
# before constructing a ``LegalOperation``. These two helpers are the single
# jurisdiction-neutral codec for that boundary, replacing per-frontend
# duplicates (EE ``grafter._to_structural_action``, UK
# ``lowering_actions._to_structural_action``, NO ``grafter._no_action_value``).
#
# ``structural_action_from_str`` is fail-loud by default: an action string that
# does not name a member raises ``ValueError`` rather than silently collapsing
# to ``META`` (the previous per-frontend behaviour, an unowned mislabel
# channel). Each ``StructuralAction`` member's ``.value`` is, by enum design,
# exactly the boundary string, so the mapping is the enum constructor itself.


def structural_action_from_str(
    s: str | StructuralAction,
    *,
    on_unknown: str = "raise",
) -> StructuralAction:
    """Map a boundary action string to its ``StructuralAction`` member.

    ``s`` may already be a ``StructuralAction`` (returned unchanged) or one of
    the boundary action strings whose value names an enum member.

    ``on_unknown`` governs an unrecognised string:
        - ``"raise"`` (default): raise ``ValueError`` — fail-loud. An action
          string that names no member is a real, unhandled vocabulary case and
          must surface, not be silently mislabelled ``META``.
        - ``"meta"``: legacy permissive fallback returning ``StructuralAction.META``.
          Only for explicitly opted-in legacy call sites.
    """
    if isinstance(s, StructuralAction):
        return s
    # Backward-compat: the legacy ``text_replace`` / ``text_repeal`` value
    # strings predate the TEXT_PATCH collapse (§2.1 O6) but are still present in
    # persisted proof/effect rows (NZ/UK). They both name the single TEXT_PATCH
    # action — the kind discriminator lives on ``TextPatchSpec.kind``, not here —
    # so map them as aliases rather than failing to parse.
    if s in _LEGACY_TEXT_PATCH_ALIASES:
        return StructuralAction.TEXT_PATCH
    try:
        return StructuralAction(s)
    except ValueError:
        pass
    if on_unknown == "meta":
        return StructuralAction.META
    if on_unknown == "raise":
        raise ValueError(
            f"unknown structural action string {s!r}; expected one of "
            f"{sorted(member.value for member in StructuralAction)}"
        )
    raise ValueError(f"unknown on_unknown policy {on_unknown!r}; expected 'raise' or 'meta'")


def is_text_patch_delete(op: _TextActionOpCarrier) -> bool:
    """Whether ``op`` is a TEXT_PATCH whose kind is the former TEXT_REPEAL.

    Recovers the former ``action is TEXT_REPEAL`` distinction after the O6
    collapse: a TEXT_PATCH action carrying a DELETE-kind ``text_patch`` (a bounded
    word repeal). ``op`` is a typed ``_TextActionOpCarrier`` (``.action`` /
    ``.text_patch``) to avoid an import cycle with ``core.ir``. A TEXT_PATCH with
    no ``text_patch`` carrier (some frontends convey old/new via the payload) is
    NOT a delete — it defaults to the replace family, exactly as a bare
    pre-collapse TEXT_REPLACE did.
    """
    if op.action is not StructuralAction.TEXT_PATCH:
        return False
    patch = op.text_patch
    return patch is not None and patch.kind is TextPatchKindEnum.DELETE


def is_text_patch_replace(op: _TextActionOpCarrier) -> bool:
    """Whether ``op`` is a TEXT_PATCH in the former TEXT_REPLACE (content-writing) family.

    Recovers the former ``action is TEXT_REPLACE`` distinction after the O6
    collapse: any TEXT_PATCH that is NOT a DELETE-kind repeal. This deliberately
    includes a TEXT_PATCH with no ``text_patch`` carrier (payload-carried EE text
    replaces) and REPLACE/APPEND-kind patches, matching the pre-collapse rule that
    a bare TEXT_REPLACE was the replace family.
    """
    return op.action is StructuralAction.TEXT_PATCH and not is_text_patch_delete(op)


def legacy_text_action_value(op: _TextActionOpCarrier) -> str:
    """Project an op's action to its PRE-collapse boundary value string.

    Frontends (EE/UK/NO) historically dispatched on the action value string
    ``"text_replace"`` / ``"text_repeal"``. After the §2.1 O6 collapse a text op's
    ``action.value`` is uniformly ``"text_patch"``, which those string comparisons
    no longer recognise. This helper re-derives the legacy value string from the
    op so the existing string-dispatch sites stay byte-identical: a DELETE-kind
    TEXT_PATCH → ``"text_repeal"``, any other TEXT_PATCH → ``"text_replace"``
    (matching the pre-collapse default where a bare TEXT_REPLACE was the replace
    family). Non-TEXT_PATCH actions return their ordinary ``.value``. ``op`` is a
    typed ``_TextActionOpCarrier`` to avoid an import cycle.
    """
    action = op.action
    if action is StructuralAction.TEXT_PATCH:
        return "text_repeal" if is_text_patch_delete(op) else "text_replace"
    return action.value if isinstance(action, StructuralAction) else str(action)


def structural_action_value(action: StructuralAction | str) -> str:
    """Return the boundary string value for a ``StructuralAction`` or string.

    Accepts either a ``StructuralAction`` (returns its ``.value``) or a string
    already at the boundary (returned unchanged). Jurisdiction-neutral inverse
    of ``structural_action_from_str`` for comparisons and serialization.
    """
    return action.value if isinstance(action, StructuralAction) else action


# ---------------------------------------------------------------------------
# LabelAction -- label/heading-level operations
# ---------------------------------------------------------------------------


class LabelAction(Enum):
    """Label-level and heading-level operation kinds.

    These are operations that modify labels or headings rather than
    structural content.
    """

    RENUMBER = "renumber"
    HEADING_REPLACE = "heading_replace"
    HEADING_INSERT = "heading_insert"


# ---------------------------------------------------------------------------
# PayloadSourceShape -- coarse amendment-body payload shapes
# ---------------------------------------------------------------------------


class PayloadSourceShape(Enum):
    """Coarse payload-shape vocabulary for amendment-body normalization."""

    WHOLE_SECTION = "whole_section"
    SPARSE_SUBSECTIONS = "sparse_subsections"
    SINGLE_SUBSECTION = "single_subsection"
    ITEMS_ONLY = "items_only"
    EMPTY = "empty"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# MetaClauseKind -- non-structural clause classifications
# ---------------------------------------------------------------------------


class MetaClauseKind(Enum):
    """Classification of meta/effect clauses.

    These are non-structural clause components that carry temporal or
    procedural semantics rather than structural amendments.

    The English values (commencement, expiry, transition, delegation) are
    used in the meta_parse surface pipeline and bridge code. Keeping the
    vocabulary neutral in core avoids carrying frontend boundary codes as
    first-class semantic vocabulary.
    """

    # English surface-pipeline vocabulary (meta_parse.py)
    COMMENCEMENT = "commencement"
    EXPIRY = "expiry"
    TRANSITION = "transition"
    DELEGATION = "delegation"
    OTHER = "other"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# TextPatchKind -- text-level patch operation kinds (typed enum)
# ---------------------------------------------------------------------------


class TextPatchKindEnum(Enum):
    """Text-level patch operation kinds."""

    REPLACE = "replace"
    DELETE = "delete"
    APPEND = "append"


# ---------------------------------------------------------------------------
# StructureKind -- typed structural vocabulary for core
# ---------------------------------------------------------------------------


class StructureKind(Enum):
    """Typed structural kinds used by core projections.

    This is the governed structural vocabulary available to shared core
    projections and validation. It does not own storage; ``LegalAddress.path``
    remains the open storage vocabulary. These values are the closed-world
    semantic vocabulary shared by core, not an exhaustive parser ingress
    schema for every historical frontend spelling.
    """

    DOCUMENT = "document"
    TITLE = "title"
    PART = "part"
    DIVISION = "division"
    SUBDIVISION = "subdivision"
    CHAPTER = "chapter"
    SUBCHAPTER = "subchapter"
    SECTION = "section"
    SUBSECTION = "subsection"
    ITEM = "item"
    SUBITEM = "subitem"
    ROW = "row"
    CELL = "cell"
    APPENDIX = "appendix"
    ANNEX_PART = "annex_part"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# FacetKind -- node facets (sub-parts of a node)
# ---------------------------------------------------------------------------


class FacetKind(Enum):
    """Facet kinds for sub-parts of a legal node.

    These distinguish between targeting the whole node vs. specific
    sub-parts like headings, introductions, tables, etc. ``NONE`` and
    ``WHOLE_ACT`` are sentinels used at other boundaries; shared-core
    ``FacetTarget`` requires a concrete node facet such as ``HEADING`` or
    ``INTRO``.
    """

    NONE = ""
    BODY = "body"
    HEADING = "heading"
    INTRO = "intro"
    TABLE = "table"
    TABLE_HEADER = "table_header"
    TABLE_BODY = "table_body"
    REPEAL_NOTICE = "repeal_notice"
    EDITORIAL_NOTICE = "editorial_notice"
    FOOTNOTE = "footnote"
    WHOLE_ACT = "whole_act"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# SpanKind -- governed sub-section anchoring vocabulary
# ---------------------------------------------------------------------------


class SpanKind(Enum):
    """Closed-world anchoring kinds for span-level references.

    These are the only span categories the shared core anchoring layer emits.
    Callers must not invent ad hoc span-kind strings at this boundary.
    """

    SUBSECTION = "subsection"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    ITEM = "item"
    HEADING = "heading"
    INTRO = "intro"
    SUBPARAGRAPH = "subparagraph"

    @override
    def __str__(self) -> str:
        return self.value




# ---------------------------------------------------------------------------
# LabelForm -- label format classification
# ---------------------------------------------------------------------------


class LabelForm(Enum):
    """Label format classification for structure units.

    Describes the format/style of labels used for structure units.
    """

    NONE = "none"
    ARABIC = "arabic"
    ARABIC_SUFFIX = "arabic_suffix"
    ROMAN = "roman"
    LETTER = "letter"
    COMPOUND_LETTER = "compound_letter"
    PAREN_ARABIC = "paren_arabic"
    PAREN_LETTER = "paren_letter"
    PAREN_COMPOUND_LETTER = "paren_compound_letter"
    PAREN_ROMAN = "paren_roman"
    TARIFF_CODE = "tariff_code"
    STARRED = "starred"
    FREE_TEXT = "free_text"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# Label -- typed label with normalized form
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Label:
    """A typed label with raw and normalized forms.

    Attributes:
        raw: The original label text as it appears in source.
        normalized: The normalized/canonical form of the label.
        form: The label format classification.
        value: The numeric/letter value for sorting/comparison.
        base: The base label (for suffixes like "1a" where base is "1").
        suffix: The suffix part (for "1a" where suffix is "a").
    """

    raw: str
    normalized: str
    form: LabelForm
    value: str = ""
    base: str = ""
    suffix: str = ""


# ---------------------------------------------------------------------------
# IRNodeKind -- IR node kind enum
# ---------------------------------------------------------------------------


class IRNodeKind(Enum):
    """IR node kinds used by ``IRNode.kind``."""

    BODY = "body"
    CHAPTER = "chapter"
    PART = "part"
    SECTION = "section"
    SUBSECTION = "subsection"
    PARAGRAPH = "paragraph"
    SUBPARAGRAPH = "subparagraph"
    BLOCK = "block"
    HCONTAINER = "hcontainer"
    CONTENT = "content"
    INTRO = "intro"
    HEADING = "heading"
    NUM = "num"
    P = "p"
    I = "i"
    OMISSION = "omission"
    CROSS_HEADING = "crossHeading"
    WRAP_UP = "wrapUp"
    ITEM = "item"
    DIVISION = "division"
    SUBDIVISION = "subdivision"
    SENTENCE = "sentence"
    CROSSHEADING = "crossheading"
    APPENDIX = "appendix"
    SCHEDULE = "schedule"
    SCHEDULE_ENTRY = "schedule_entry"
    RECITAL = "recital"
    PREAMBLE = "preamble"
    P1GROUP = "p1group"
    PGROUP = "pgroup"
    FINAL = "final"
    TABLE = "table"
    ROW = "row"
    CELL = "cell"
    HEADER_CELL = "header_cell"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# BodyNodeRole -- omission-aware payload claim role (PRO_RESPONSE_5_1 §5)
# ---------------------------------------------------------------------------


class BodyNodeRole(Enum):
    """Role of a node in an amendment body section that contains omission markers.

    When ``hcontainer name="omission"`` is present among a section's children,
    the semantics of sibling nodes change:

    - ``CANDIDATE_PAYLOAD``: a non-omission sibling that MAY become enacted law
      only if claimed by a structural target from the clause/coverage lane.
    - ``CONTEXT_CARRIED``: a node that was included in the amendment body as
      editorial context (e.g. unchanged johdantokappale carried along with item
      changes per drafting-guide rules) and must NOT overwrite prior law.
    - ``OMITTED_CONTEXT``: the omission marker itself — represents omitted text.
    - ``UNMATCHED``: a node that is neither claimed nor clearly context.

    Used in omission-aware merge to determine whether pre-omission intro nodes
    should be replaced by master content (CONTEXT_CARRIED) or applied as new
    law (CANDIDATE_PAYLOAD).  Only CANDIDATE_PAYLOAD nodes that are also
    claimed by a clause target become enacted.
    """

    CANDIDATE_PAYLOAD = "candidate_payload"
    CONTEXT_CARRIED = "context_carried"
    OMITTED_CONTEXT = "omitted_context"
    UNMATCHED = "unmatched"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# StructuralStatus -- lifecycle status of an addressable structural node
# ---------------------------------------------------------------------------


class StructuralStatus(Enum):
    """Lifecycle status of an addressable structural node in the replay IR.

    Separates the *structural* question (is the address still present for
    anchor resolution?) from the *presentation* question (should the node
    appear in end-user output?).

    LIVE     -- normal enacted content.
    REPEALED -- node has been repealed; kept as an address anchor so later
                amendments (e.g. "insert after kohta 15") can still resolve.
                Substantive content is cleared; label is preserved.
    OMITTED  -- omission/context only, not enacted (hcontainer omission).
    RESERVED -- intentionally empty anchor (e.g. placeholder for expected
                future content).
    UNKNOWN  -- status cannot be determined from available evidence.
    """

    LIVE = "live"
    REPEALED = "repealed"
    OMITTED = "omitted"
    RESERVED = "reserved"
    UNKNOWN = "unknown"

    @override
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# SourceNormalization -- source pathology correction witnesses
# ---------------------------------------------------------------------------


class SourceNormalizationKind(Enum):
    """Classification of the kind of source correction applied.

    This enum is the shared cross-jurisdiction taxonomy host for source
    normalization event kinds.  The first seven values (EDITORIAL_STRIP through
    SUSPICIOUS_SHAPE) are jurisdiction-neutral and may be emitted by any
    frontend.

    EDITORIAL_STRIP  -- structural nodes that carry no legal text were removed
                        (e.g. image blocks, authorial notes).
    TAG_RECLASSIFY   -- an XML tag was assigned a semantically wrong element
                        type by the source and must be reassigned
                        (e.g. <subsection> that is really a kohta/paragraph).
    WHITESPACE       -- local, semantics-preserving whitespace / OCR artefact.
    NUMBERING_REPAIR -- a numbering anomaly was detected (gap or duplicate)
                        in sibling items; recorded as witness for downstream.
    DUPLICATE_DROP   -- a duplicate structural wrapper was collapsed.
    CROSS_HEADING_HOIST -- a preceding ``crossHeading`` sibling was attached
                           to the following structural node as its heading.
    SUSPICIOUS_SHAPE -- a suspicious source shape was detected and preserved
                        with an explicit typed witness rather than silently
                        normalized away.
    Frontend-specific normalization kinds should be carried as frontend-local
    string values through ``SourceNormalizationFact.kind`` rather than added to
    this shared enum host.
    """

    EDITORIAL_STRIP = "editorial_strip"
    TAG_RECLASSIFY = "tag_reclassify"
    WHITESPACE = "whitespace"
    NUMBERING_REPAIR = "numbering_repair"
    DUPLICATE_DROP = "duplicate_drop"
    CROSS_HEADING_HOIST = "cross_heading_hoist"
    SUSPICIOUS_SHAPE = "suspicious_shape"

    @override
    def __str__(self) -> str:
        return self.value


class SourceNormalizationBasis(Enum):
    """Why this correction is considered auto-correctable (per policy in Query 10).

    SCHEMA_INVALID          -- violates XML schema / jurisdiction profile.
    PROFILE_INVALID         -- violates the jurisdiction profile hierarchy rules.
    IMPOSSIBLE_NUMBERING    -- produces an impossible numbering ontology
                               (e.g. subsection with item-style "9)" num).
    EDITORIAL_CONTAMINATION -- obvious editorial markup in legal payload.
    MONOTONIC_LOCAL_REPAIR  -- local, semantics-preserving, high-confidence fix.
    """

    SCHEMA_INVALID = "schema_invalid"
    PROFILE_INVALID = "profile_invalid"
    IMPOSSIBLE_NUMBERING = "impossible_numbering"
    EDITORIAL_CONTAMINATION = "editorial_contamination"
    MONOTONIC_LOCAL_REPAIR = "monotonic_local_repair"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SourceNormalizationFact:
    """A witness record for one source correction applied during normalization.

    Immutable.  Emitted by ``normalize_source_ir`` for every correction.
    Downstream audit / adjudication layers consume these facts to surface
    source pathology information without hiding it inside the parse.

    Attributes:
        statute_id:   Statute being normalized (e.g. "2020/1262").
        kind:         What category of correction was applied. Shared kinds use
                      ``SourceNormalizationKind``; frontend-local kinds are
                      carried as stable strings.
        basis:        Why it was considered auto-correctable.
        before:       Human-readable description of the raw (pre-correction) state.
        after:        Human-readable description of the normalized state.
        explanation:  Free-text rationale.
        path:         Structural path to the corrected node (label trail),
                      e.g. ("section:53", "subsection:1").
        confidence:   Normalized [0.0, 1.0] confidence in the correction.
    """

    statute_id: str
    kind: SourceNormalizationKind | str
    basis: SourceNormalizationBasis
    before: str = ""
    after: str = ""
    explanation: str = ""
    path: tuple[str, ...] = ()
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.kind_value:
            raise ValueError("SourceNormalizationFact.kind must be non-empty")

    @property
    def kind_value(self) -> str:
        """Stable string projection for staged enum-to-value migration."""
        return str(self.kind) if isinstance(self.kind, SourceNormalizationKind) else self.kind

    @property
    def basis_value(self) -> str:
        """Stable string projection for reporting/serialization edges."""
        return str(self.basis)
