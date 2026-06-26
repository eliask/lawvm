"""Core data types for the Finnish law amendment pipeline.

Extracted from grafter.py to break the circular-import cycle that would arise
when normalize.py and apply.py need AmendmentOp but are also imported by
grafter.py.

This module has NO imports from grafter.py.  It only depends on:
  - Python stdlib (dataclasses, typing, datetime)
  - lawvm.core.ir (IRNode, LegalOperation)
  - lawvm.finland.helpers (_expand_section_range)

grafter.py re-exports every public symbol from here for backward compatibility.
"""

from __future__ import annotations
from typing_extensions import override

import datetime as dt
import logging
import re
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from enum import StrEnum
from typing import TYPE_CHECKING, Dict, Iterable, List, Literal, Optional, Tuple, assert_never, cast

from lawvm.core.ir import IRNode, LegalAddress, OperationSource, TextPatchSpec
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.compile_result import StrictProfile
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import (
    FacetKind,
    IRNodeKind,
    StructuralAction,
    TextPatchKindEnum,
)
from lawvm.core.tree_ops import Path
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.target_selector import (
    AddressSegment,
    ScopeStatus,
    TargetScope,
    TargetSelector,
)
from lawvm.finland.helpers import _expand_section_range, _norm_num_token
from lawvm.finland.op_provenance import (
    OpProvenance,
    Recovered,
    RecognizerId,
    dominant_surface,
    dominant_tier,
    has_recognizer,
)
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.target_selector_codec import (
    AmendmentOpV1Record,
    TargetSelectorCodecV1,
)

if TYPE_CHECKING:
    from lawvm.core.canonical_intent import CanonicalIntent
    from lawvm.core.temporal import ActivationRule
    from lawvm.finland.payload_normalize import PayloadCompletenessWitness, SubsectionSlotAssignmentResult

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

class OpType(StrEnum):
    """Finland amendment operation kind.

    A ``StrEnum`` (not a bare ``Literal``) so that op-type comparisons are
    member-vs-member and survive renames as a *type* error rather than a silent
    string mismatch (Pro TargetSelector invariant). Members subclass ``str`` and
    their ``value`` equals the legacy wire string, so ``OpType.REPLACE ==
    "REPLACE"`` is ``True`` and serialization stays byte-identical.
    """

    REPLACE = "REPLACE"
    REPEAL = "REPEAL"
    INSERT = "INSERT"
    RENUMBER = "RENUMBER"

_SCOPE_PROVENANCE_TAGS = frozenset(
    {
        "grouped_chapter_scope",
        "grouped_part_scope",
        "chapter_scope_from_preamble",
        "chapter_scope_from_explicit_chunk",
        "chapter_scope_from_same_amendment_stem",
        "chapter_scope_carry_forward",
    }
)

_TARGET_GUESSING_PROVENANCE_TAGS = frozenset(
    {
        "hallucinated_chapter_reclassified_to_section",
        "chapter_section_moment_misparsed_as_item_ref",
        "item_ref_misparsed_as_moment_ref",
        "normalize_item_like_target",
    }
)


def _derive_op_provenance(
    *,
    fallback_provenance: bool,
    body_root_replace_fallback: bool,
    sec1_body_johto_fallback: bool,
    uncovered_body_recovery: bool,
    extraction_provenance_tags: Tuple[str, ...],
    target_guessing_provenance_tags: Tuple[str, ...],
    witness_rule_id: Optional[str],
) -> OpProvenance | None:
    """Derive the typed :class:`OpProvenance` from an op's recovery markers.

    ADDITIVE (Phase-2 step 2): the legacy ``*_fallback`` / ``*_recovery`` booleans
    and the ``*_provenance_tags`` string bags remain authoritative; this derives a
    typed mirror so later steps can rekey readers onto
    ``RecognizerId.X in op.provenance.recognizer_ids`` / ``isinstance(prov, Recovered)``.

    An op carries the SET of every load-bearing recovery recognizer that touched
    it. The result is :class:`Recovered` iff the op bears a recovery marker
    (``fallback_provenance`` OR any recognizer membership); otherwise ``None``
    (no recovery stamp — the op was not produced by a fallback recognizer). The
    :class:`Parsed` arm is reserved for grammar-rule provenance wired in a later
    phase; absence is represented as ``None`` here to stay additive.
    """
    ids: set[RecognizerId] = set()
    if sec1_body_johto_fallback:
        ids.add(RecognizerId.SEC1_BODY_JOHTO)
    if body_root_replace_fallback:
        ids.add(RecognizerId.BODY_ROOT_REPLACE)
    if uncovered_body_recovery:
        ids.add(RecognizerId.UNCOVERED_BODY)
    if "extraction_fallback_heuristic" in extraction_provenance_tags:
        ids.add(RecognizerId.EXTRACTION_FALLBACK_HEURISTIC)
    if "jolloin_moment_renumber_supplement" in extraction_provenance_tags:
        ids.add(RecognizerId.JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT)
    if "unique_item_label_subsection_fallback" in target_guessing_provenance_tags:
        ids.add(RecognizerId.UNIQUE_ITEM_LABEL_SUBSECTION_FALLBACK)
    if "normalize_item_like_target" in target_guessing_provenance_tags:
        ids.add(RecognizerId.NORMALIZE_ITEM_LIKE_TARGET)
    if "rebase_duplicate_target_shifted_replace" in target_guessing_provenance_tags:
        ids.add(RecognizerId.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE)
    if "rebase_replaced_renumber_source" in target_guessing_provenance_tags:
        ids.add(RecognizerId.REBASE_REPLACED_RENUMBER_SOURCE)
    if "chapter_scope_from_unique_live_section" in target_guessing_provenance_tags:
        ids.add(RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION)
    if witness_rule_id == "fi.jolloin_renumber":
        ids.add(RecognizerId.JOLLOIN_RENUMBER)
    elif witness_rule_id == "fi.repeal_vts_voimaantulo":
        ids.add(RecognizerId.REPEAL_VTS_VOIMAANTULO)

    if not ids and not fallback_provenance:
        return None

    recognizer_ids = frozenset(ids)
    return Recovered(
        surface=dominant_surface(recognizer_ids),
        recognizer_ids=recognizer_ids,
        tier=dominant_tier(recognizer_ids),
    )

class ScopeResolutionConfidence(StrEnum):
    """Confidence rail for a Finland chapter-scope resolution witness.

    A ``StrEnum`` (not a bare ``Literal``) so confidence comparisons are
    member-vs-member and survive renames as a type error rather than a silent
    string mismatch.
    """

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    REWRITTEN = "rewritten"


class ScopeResolutionSource(StrEnum):
    """Source rail for a Finland chapter-scope resolution witness."""

    PREAMBLE = "preamble"
    EXPLICIT_CHUNK = "explicit_chunk"
    CARRY_FORWARD = "carry_forward"
    GROUPED_PART = "grouped_part"
    GROUPED_CHAPTER = "grouped_chapter"
    EXPLICIT_SCOPE_REWRITE = "explicit_scope_rewrite"
    LIVE_STEM_HOST = "live_stem_host"


class SectionPathResolutionReason(StrEnum):
    """Reason rail for a Finland late apply-time section-path fallback."""

    LIVE_UNIQUE_GLOBAL_FALLBACK = "live_unique_global_fallback"
    LIVE_UNIQUE_SUBSTANTIVE_OVER_PLACEHOLDER = "live_unique_substantive_over_placeholder"
    FOLLOW_SAME_WAVE_MIGRATION = "follow_same_wave_migration"


@dataclass(frozen=True)
class ScopeConfidence:
    """Finland-local unified witness for chapter-scope resolution provenance."""

    tag: str
    source: ScopeResolutionSource
    confidence: ScopeResolutionConfidence
    resolved_chapter: str | None = None
    fallback_reason: SectionPathResolutionReason | None = None

    @property
    def is_explicit(self) -> bool:
        return self.confidence is ScopeResolutionConfidence.EXPLICIT

    @property
    def is_rewritten(self) -> bool:
        return self.confidence is ScopeResolutionConfidence.REWRITTEN


@dataclass(frozen=True)
class ScopeAuthorityParity:
    """Comparison between runtime and projection scope-authority rails."""

    runtime: ScopeConfidence | None
    projection: ScopeConfidence | None
    matches: bool
    mismatch_kind: str | None = None


@dataclass(frozen=True)
class SectionPathResolution:
    """Typed result for late apply-time section path resolution.

    ``rung_id`` names the fallback-ladder rung that produced (or rejected)
    the path, in the lawvm.core.resolver_binding vocabulary; it is binding
    provenance for the passive ResolverBinding rollout and never changes
    which path is returned. ``global_candidate_count`` is the number of
    same-label section candidates in the provision index when the taken
    branch consulted it (None otherwise).
    """

    path: Path | None
    reason_code: SectionPathResolutionReason | None = None
    rung_id: str | None = None
    global_candidate_count: int | None = None

    @property
    def used_live_unique_global_fallback(self) -> bool:
        return self.reason_code in {
            SectionPathResolutionReason.LIVE_UNIQUE_GLOBAL_FALLBACK,
            SectionPathResolutionReason.LIVE_UNIQUE_SUBSTANTIVE_OVER_PLACEHOLDER,
        }


@dataclass(frozen=True)
class ContainerPathResolution:
    """Typed result for apply-time chapter/part container target resolution.

    The container family (chapter/part targets) is the first family whose
    apply path CONSUMES its ResolverBinding instead of resolving ad hoc
    (apply contract §3 step 3). ``rung_id`` names the ladder rung in the
    lawvm.core.resolver_binding vocabulary. ``candidate_count`` is the
    number of same-kind/same-label container nodes visible across the whole
    work — recorded so a first-match bind over a duplicated label is visible
    in provenance instead of silent.
    """

    path: Path | None
    rung_id: str | None = None
    candidate_count: int | None = None


def scope_confidence_from_tags(
    tags: Iterable[str],
    *,
    resolved_chapter: str | None = None,
) -> ScopeConfidence | None:
    """Return the strongest Finland-local scope-confidence witness from tags."""
    normalized = tuple(str(tag).strip() for tag in tags if str(tag).strip())
    if not normalized:
        return None
    for tag in normalized:
        if tag in {
            "chapter_scope_stripped_subsection_insert",
            "chapter_scope_stripped_section_facet_insert",
            "chapter_scope_stripped_unique_section",
            "chapter_scope_stripped_duplicate_label_outside_stated_chapter",
            "mixed_scope_group_merge",
        }:
            return ScopeConfidence(
                tag=tag,
                source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
                confidence=ScopeResolutionConfidence.REWRITTEN,
                resolved_chapter=resolved_chapter,
            )
    if "chapter_scope_from_explicit_chunk" in normalized:
        return ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter=resolved_chapter,
        )
    if "chapter_scope_carry_forward" in normalized:
        return ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter=resolved_chapter,
        )
    if "chapter_scope_from_preamble" in normalized:
        return ScopeConfidence(
            tag="chapter_scope_from_preamble",
            source=ScopeResolutionSource.PREAMBLE,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter=resolved_chapter,
        )
    if "chapter_scope_from_same_amendment_stem" in normalized:
        return ScopeConfidence(
            tag="chapter_scope_from_same_amendment_stem",
            source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter=resolved_chapter,
        )
    if "grouped_part_scope" in normalized:
        return ScopeConfidence(
            tag="grouped_part_scope",
            source=ScopeResolutionSource.GROUPED_PART,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter=resolved_chapter,
        )
    if "grouped_chapter_scope" in normalized:
        return ScopeConfidence(
            tag="grouped_chapter_scope",
            source=ScopeResolutionSource.GROUPED_CHAPTER,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter=resolved_chapter,
        )
    return None


def scope_resolution_witness_from_tags(
    tags: Iterable[str],
    *,
    resolved_chapter: str | None = None,
) -> ScopeConfidence | None:
    """Backward-compatible alias during the scope-confidence migration."""
    return scope_confidence_from_tags(tags, resolved_chapter=resolved_chapter)


def runtime_scope_confidence(
    *,
    scope_provenance_tags: Iterable[str],
    resolved_chapter: str | None = None,
) -> ScopeConfidence | None:
    """Return the live replay/apply scope witness.

    Runtime Finland semantics intentionally remain tag-derived until the full
    stored-carrier scope-authority transfer is landed end to end.
    """
    return scope_confidence_from_tags(
        scope_provenance_tags,
        resolved_chapter=resolved_chapter,
    )


def runtime_scope_confidence_for_op(op: object) -> ScopeConfidence | None:
    """Return the governed live runtime/apply scope witness for an op shell.

    Stored Finland-local scope carriers are now runtime authority too. Raw tags
    remain a compatibility fallback only when the stored carrier is absent.
    """
    # Guard: _StructureApplyView and other lightweight views lack scope fields.
    if not isinstance(op, (AmendmentOp, ResolvedOp)):
        return None
    return projection_scope_confidence_for_op(op)


def normalize_scope_confidence(
    scope_confidence: ScopeConfidence | None,
    *,
    resolved_chapter: str | None = None,
) -> ScopeConfidence | None:
    """Normalize a stored Finland-local scope witness against the current target."""
    if scope_confidence is None:
        return None
    if scope_confidence.resolved_chapter != resolved_chapter:
        return dc_replace(scope_confidence, resolved_chapter=resolved_chapter)
    return scope_confidence


def projection_scope_confidence(
    *,
    scope_confidence: ScopeConfidence | None,
    scope_provenance_tags: Iterable[str],
    resolved_chapter: str | None = None,
) -> ScopeConfidence | None:
    """Return the projection/transport scope witness.

    Stored Finland-local scope carriers are primary on projection/evidence
    surfaces. Raw tags remain a compatibility fallback only.
    """
    stored = normalize_scope_confidence(
        scope_confidence,
        resolved_chapter=resolved_chapter,
    )
    if stored is not None:
        return stored
    return runtime_scope_confidence(
        scope_provenance_tags=scope_provenance_tags,
        resolved_chapter=resolved_chapter,
    )


def projection_scope_confidence_for_op(
    op: "AmendmentOp | ResolvedOp",
) -> ScopeConfidence | None:
    """Return the projection/transport scope witness for an op shell."""
    if isinstance(op, AmendmentOp):
        return projection_scope_confidence(
            scope_confidence=op.scope_confidence,
            scope_provenance_tags=op.scope_provenance_tags,
            resolved_chapter=op.target_cols.target_chapter,
        )
    return projection_scope_confidence(
        scope_confidence=op.scope_confidence,
        scope_provenance_tags=op.scope_provenance_tags,
        resolved_chapter=op.resolved_target_scope_chapter_label,
    )


def scope_authority_parity_for_op(
    op: "AmendmentOp | ResolvedOp",
) -> ScopeAuthorityParity:
    """Return runtime-vs-projection scope parity for one op shell."""
    runtime = runtime_scope_confidence_for_op(op)
    projection = projection_scope_confidence_for_op(op)
    if runtime is None and projection is None:
        return ScopeAuthorityParity(runtime=None, projection=None, matches=True, mismatch_kind=None)
    if runtime is None:
        return ScopeAuthorityParity(
            runtime=None,
            projection=projection,
            matches=False,
            mismatch_kind="runtime_missing_projection_present",
        )
    if projection is None:
        return ScopeAuthorityParity(
            runtime=runtime,
            projection=None,
            matches=False,
            mismatch_kind="projection_missing_runtime_present",
        )
    matches = runtime == projection
    return ScopeAuthorityParity(
        runtime=runtime,
        projection=projection,
        matches=matches,
        mismatch_kind=None if matches else "runtime_projection_disagree",
    )


def lo_scope_confidence(lo: _LegalOperation) -> ScopeConfidence | None:
    """Return the Finland-local scope witness stored on one LegalOperation."""
    chapter = _lo_path_dict(lo).get("chapter")
    stored = cast(ScopeConfidence | None, lo.scope_confidence)
    return normalize_scope_confidence(stored, resolved_chapter=chapter)


def lo_with_scope_confidence(
    lo: _LegalOperation,
    scope_confidence: ScopeConfidence | None,
) -> _LegalOperation:
    """Attach the Finland-local scope witness to one LegalOperation."""
    return dc_replace(
        lo,
        scope_confidence=normalize_scope_confidence(
            scope_confidence,
            resolved_chapter=_lo_path_dict(lo).get("chapter"),
        ),
    )


def lo_with_move_clause_target_unit_kind(
    lo: _LegalOperation,
    move_clause_target_unit_kind: "TargetUnitKind | None",
) -> _LegalOperation:
    """Attach the Finland-local move-rider destination kind to one LegalOperation.

    Finnish johtolause move riders ("29 e §, joka samalla siirretään 5 b
    lukuun") resolve the DESTINATION scope onto the target address at parse
    time. The rider itself must survive to the apply layer as typed evidence:
    ``AmendmentOp.from_lo`` reads this carrier, and the occupancy policy uses
    it to evaluate a destination-scoped REPLACE against the move ORIGIN slot.
    Same Finland-local transport pattern as ``lo_with_scope_confidence``.
    """
    if move_clause_target_unit_kind is None:
        return lo
    return dc_replace(lo, move_clause_target_unit_kind=move_clause_target_unit_kind)


def lo_with_added_scope_tag(lo: _LegalOperation, tag: str) -> _LegalOperation:
    """Append one scope evidence tag and refresh the stored Finland witness."""
    provenance_tags = tuple(dict.fromkeys((*tuple(lo.provenance_tags), tag)))
    lo_new = dc_replace(lo, provenance_tags=provenance_tags)
    return lo_with_scope_confidence(
        lo_new,
        scope_confidence_from_tags(
            provenance_tags,
            resolved_chapter=_lo_path_dict(lo_new).get("chapter"),
        ),
    )

# ---------------------------------------------------------------------------
# LegalOperation path helpers
# ---------------------------------------------------------------------------

_PATH_KINDS = ("part", "chapter", "section", "subsection", "item", "subitem")


def _format_operation_description(
    *,
    action_type: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    target_chapter: str | None,
    target_paragraph: int | None,
    target_item: str | None,
    target_special: str | None,
) -> str:
    """Render the stable human-facing description for one Finland op."""
    kind_label = {
        "section": "§",
        "chapter": "luku",
        "part": "osa",
    }.get(target_unit_kind, "?")
    if target_unit_kind == "section" and target_chapter:
        description = f"{action_type} {target_chapter} luku {target_label} {kind_label}"
    else:
        description = f"{action_type} {target_label} {kind_label}"
    if (
        target_unit_kind == "chapter"
        and action_type == "REPLACE"
        and not target_special
        and target_paragraph is None
        and target_item is None
    ):
        description += " otsikko"
    if target_special:
        description += f" {target_special}"
    elif target_paragraph is not None:
        description += f" {target_paragraph} mom"
    if target_item:
        description += f" {target_item} kohta"
    return description


def _lo_target_record(lo: _LegalOperation) -> AmendmentOpV1Record:
    """Unpack a LegalOperation target path into the legacy 8-column record shape.

    This is the ``lo`` → target-columns derivation; ``AmendmentOp`` encodes the
    resulting record to its stored :class:`TargetSelector` via the codec, so the
    selector is the sole stored representation and the legacy columns are a
    projection (``AmendmentOp.target_cols``) rather than stored state.
    """
    pd = {k: v for k, v in lo.target.path}
    if "section" in pd:
        section = pd["section"]
        target_unit_kind: TargetUnitKind = "section"
        chapter = pd.get("chapter")  # qualifier when section is primary
    elif "chapter" in pd:
        section = pd["chapter"]
        target_unit_kind = "chapter"
        chapter = None
    elif "part" in pd:
        section = pd["part"]
        target_unit_kind = "part"
        chapter = None
    else:
        raise ValueError(
            "LegalOperation target path has no Finland-supported primary unit "
            f"(part/chapter/section): {tuple(lo.target.path)!r}"
        )
    sub_val = pd.get("subsection", "")
    special = lo.target.special
    if special == FacetKind.HEADING or str(special) == "heading":
        ts: str | None = "otsikko"
    elif str(special) == "otsikko_edella":
        ts = "otsikko_edella"
    elif special == FacetKind.INTRO or str(special) == "intro":
        ts = "johd"
    else:
        ts = None
    # kohta (item) and alakohta (subitem) are distinct hierarchy levels in the
    # canonical address. The legacy late-waist still carries a single combined
    # ``target_item`` slot that the apply layer splits on demand, so combine the
    # two segments here while exposing the alakohta separately.
    item_label = pd.get("item")
    subitem_label = pd.get("subitem")
    return AmendmentOpV1Record(
        target_section=section,
        target_unit_kind=target_unit_kind,
        target_chapter=chapter,
        target_part=pd.get("part"),
        target_paragraph=int(sub_val) if sub_val and sub_val.isdigit() else None,
        target_item=(f"{item_label}{subitem_label}" if item_label and subitem_label else item_label),
        target_subitem=subitem_label,
        target_special=ts,
    )


# Sentinel: ``AmendmentOp.__init__``'s ``target_selector`` parameter was not
# explicitly supplied. Distinct from ``None`` so a caller could (in principle)
# clear it, and so ``dataclasses.replace`` — which re-passes the stored selector
# — is distinguishable from a fresh column-built construction.
class _TargetSelectorUnset:
    __slots__ = ()


_TARGET_SELECTOR_UNSET: "_TargetSelectorUnset" = _TargetSelectorUnset()

# Default selector for a bare ``AmendmentOp()`` (section focus, empty label,
# unspecified scope) — matches the historical column defaults
# (``target_section=""``, ``target_unit_kind="section"``) byte-for-byte under the
# codec round-trip.
_DEFAULT_TARGET_SELECTOR: TargetSelector = TargetSelector(
    relative_path=(AddressSegment("section", ""),),
    scope=TargetScope(scope_status=ScopeStatus.UNSPECIFIED),
    special=None,
    special_raw=None,
)


def _selector_from_columns(
    *,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str | None,
    target_part: str | None,
    target_paragraph: int | None,
    target_item: str | None,
    target_subitem: str | None,
    target_special: str | None,
) -> TargetSelector:
    """Encode the legacy 8-column construction inputs to the stored selector."""
    return TargetSelectorCodecV1.from_legacy(
        AmendmentOpV1Record(
            target_unit_kind=target_unit_kind,
            target_section=target_section,
            target_chapter=target_chapter,
            target_part=target_part,
            target_paragraph=target_paragraph,
            target_item=target_item,
            target_subitem=target_subitem,
            target_special=target_special,
        )
    )


def _lo_path_dict(lo: _LegalOperation) -> Dict[str, str]:
    """Return {kind: label} from LegalOperation target path (flat dict)."""
    return {k: v for k, v in lo.target.path}


def _lo_with_path_update(lo: _LegalOperation, target: Optional[LegalAddress] = None, **updates) -> _LegalOperation:
    """Return new LO with target path fields updated.

    Pass value=None to remove a kind.
    """
    base_target = target if target is not None else lo.target
    fields: Dict[str, str] = {k: v for k, v in base_target.path}
    for k, v in updates.items():
        if v is None:
            fields.pop(k, None)
        else:
            fields[k] = str(v)
    new_path: Tuple[Tuple[str, str], ...] = tuple((k, fields[k]) for k in _PATH_KINDS if k in fields)
    new_target = dc_replace(base_target, path=new_path)
    return lo_with_scope_confidence(
        dc_replace(lo, target=new_target),
        lo_scope_confidence(lo),
    )


def temporary_signal_for_op(op: "AmendmentOp | ResolvedOp") -> bool:
    """Return the temporary replay signal for one Finland op.

    The live temporary authority is carried through the replay-side temporal
    event path; the op shell only needs the coarse `is_temporary` flag here.
    """
    return bool(getattr(op, "is_temporary", False))


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegalOperationConversionSkip:
    """Visible reason why one LegalOperation has no structural AmendmentOp."""

    finding_kind: str
    op_id: str
    action: str
    target_path: Tuple[Tuple[str, str], ...]
    reason_code: str
    message: str
    blocking: bool = False

    def as_detail(self) -> dict[str, object]:
        return {
            "op_id": self.op_id,
            "action": self.action,
            "target_path": self.target_path,
            "reason_code": self.reason_code,
            "message": self.message,
        }


def classify_legal_operation_conversion_skip(
    lo: _LegalOperation,
) -> LegalOperationConversionSkip | None:
    """Classify LegalOperation inputs that are intentionally not structural ops."""

    target_path = tuple(lo.target.path)
    target_kinds = frozenset(kind for kind, _ in target_path)
    action = str(lo.action.value)

    if target_kinds and target_kinds <= {"nimike", "appendix"}:
        return LegalOperationConversionSkip(
            finding_kind="ELAB.REJECTED_OPERATION",
            op_id=lo.op_id,
            action=action,
            target_path=target_path,
            reason_code="ELAB.UNSUPPORTED_TOP_LEVEL_TARGET",
            message=(
                "LegalOperation target uses an unsupported Finland top-level "
                "nimike/appendix structural lane; no AmendmentOp was emitted."
            ),
            blocking=True,
        )
    if not target_kinds and lo.text_patch is not None:
        return LegalOperationConversionSkip(
            finding_kind="ELAB.LAW_LEVEL_TEXT_PATCH_SEPARATE_LANE",
            op_id=lo.op_id,
            action=action,
            target_path=target_path,
            reason_code="ELAB.LAW_LEVEL_TEXT_PATCH_SEPARATE_LANE",
            message=(
                "Law-level text patch LegalOperation is handled outside "
                "structural AmendmentOp conversion; no AmendmentOp was emitted."
            ),
        )
    if not target_kinds:
        return LegalOperationConversionSkip(
            finding_kind="ELAB.REJECTED_OPERATION",
            op_id=lo.op_id,
            action=action,
            target_path=target_path,
            reason_code="ELAB.EMPTY_LEGAL_OPERATION_TARGET",
            message="LegalOperation had an empty target path; no AmendmentOp was emitted.",
            blocking=True,
        )
    if target_kinds.isdisjoint({"part", "chapter", "section"}):
        return LegalOperationConversionSkip(
            finding_kind="ELAB.REJECTED_OPERATION",
            op_id=lo.op_id,
            action=action,
            target_path=target_path,
            reason_code="ELAB.UNSUPPORTED_DESCENDANT_ONLY_TARGET",
            message=(
                "LegalOperation target has subsection/item scope without a "
                "part, chapter, or section anchor; no AmendmentOp was emitted."
            ),
            blocking=True,
        )

    return None


@dataclass(init=False)
class AmendmentOp:
    """Compiled amendment operation for the Finland replay engine.

    Architectural observations:
    - `AmendmentOp` is a Finland-local public compatibility shell, not the
      long-term canonical execution contract.
    - It remains useful as the adapter between frontend lowering/fallback
      machinery and the later `ResolvedOp` / `CanonicalIntent` seam.
    - New executable meaning should not be invented here if a typed field on
      `ResolvedOp`, typed elaboration output, or `CanonicalIntent` can own it.

    TODO:
    - keep shrinking string-hint and legacy-field ownership
    - keep promoting execution authority to `ResolvedOp.intent`
    - avoid generalizing this type into a cross-jurisdiction semantic waist

    Plain data — target fields derived from lo when lo is present.
    """

    op_id: str = ""
    op_type: OpType = OpType.REPLACE
    # W6 Phase C: the 8 loosely-typed legacy ``target_*`` columns are GONE as
    # stored state. The op's target is held once, as the typed cross-jurisdiction
    # :class:`TargetSelector`; the legacy 8-column shape is a lossless codec
    # projection exposed read-only via :attr:`target_cols`. Construction still
    # accepts the legacy ``target_*`` kwargs (or an ``lo`` carrier); both are
    # encoded to this single stored selector in ``__init__``.
    target_selector: TargetSelector = _DEFAULT_TARGET_SELECTOR
    named_row_targets: Tuple[str, ...] = ()
    numbered_table_targets: Tuple[str, ...] = ()
    body_root_replace_fallback: bool = False
    fallback_provenance: bool = False
    sec1_body_johto_fallback: bool = False
    move_clause_target_unit_kind: Optional[TargetUnitKind] = None
    uncovered_body_recovery: bool = False
    voimaantulo_repeal: bool = False
    extraction_provenance_tags: Tuple[str, ...] = ()
    target_guessing_provenance_tags: Tuple[str, ...] = ()
    scope_provenance_tags: Tuple[str, ...] = ()
    scope_confidence: ScopeConfidence | None = None
    source_statute: str = ""
    source_issue_date: Optional[dt.date] = None
    source_title: str = ""
    target_version_statute_id: Optional[str] = None
    post_repeal_item_shift_label: Optional[str] = None
    body_chapter_move_from: Optional[str] = None
    lo: Optional[_LegalOperation] = None
    # Temporal classification — set when johtolause contains "väliaikaisesti"
    # or when the amending act carries an explicit validity interval.
    is_temporary: bool = False
    has_exact_bound_payload: bool = False
    temporal_activation: Optional["ActivationRule"] = None
    preserve_explicit_heading_facet: bool = False
    # Parse-witness provenance (diagnostic only — zero replay semantics)
    witness_rule_id: Optional[str] = None
    if TYPE_CHECKING:
        target_kind: TargetKind

    @property
    def provenance(self) -> OpProvenance | None:
        """Typed op-provenance consolidation target (Phase-2).

        Computed on access from the authoritative recovery markers (the
        ``*_fallback`` / ``*_recovery`` booleans, the ``*_provenance_tags``
        membership, and the branched ``witness_rule_id``). A property — never a
        stored field — because those markers are mutated post-construction at the
        frontend (e.g. ``op.body_root_replace_fallback = True``), so a stored
        mirror would go stale; deriving on access keeps the typed view exactly in
        lockstep with the flags. ADDITIVE: the legacy flags/tags stay
        authoritative.
        """
        return _derive_op_provenance(
            fallback_provenance=self.fallback_provenance,
            body_root_replace_fallback=self.body_root_replace_fallback,
            sec1_body_johto_fallback=self.sec1_body_johto_fallback,
            uncovered_body_recovery=self.uncovered_body_recovery,
            extraction_provenance_tags=self.extraction_provenance_tags,
            target_guessing_provenance_tags=self.target_guessing_provenance_tags,
            witness_rule_id=self.witness_rule_id,
        )

    @property
    def resolved_scope_confidence(self) -> ScopeConfidence | None:
        return runtime_scope_confidence_for_op(self)

    @property
    def scope_authority_parity(self) -> ScopeAuthorityParity:
        return scope_authority_parity_for_op(self)

    def __init__(
        self,
        op_id: str = "",
        op_type: OpType = OpType.REPLACE,
        target_section: str = "",
        target_unit_kind: TargetUnitKind | None = None,
        target_kind: TargetKind | None = None,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
        target_paragraph: Optional[int] = None,
        target_item: Optional[str] = None,
        target_subitem: Optional[str] = None,
        target_special: Optional[str] = None,
        target_selector: "TargetSelector | _TargetSelectorUnset" = _TARGET_SELECTOR_UNSET,
        named_row_targets: Tuple[str, ...] = (),
        numbered_table_targets: Tuple[str, ...] = (),
        body_root_replace_fallback: bool = False,
        fallback_provenance: bool = False,
        sec1_body_johto_fallback: bool = False,
        move_clause_target_unit_kind: TargetUnitKind | None = None,
        uncovered_body_recovery: bool = False,
        voimaantulo_repeal: bool = False,
        extraction_provenance_tags: Tuple[str, ...] = (),
        target_guessing_provenance_tags: Tuple[str, ...] = (),
        scope_provenance_tags: Tuple[str, ...] = (),
        scope_confidence: ScopeConfidence | None = None,
        source_statute: str = "",
        source_issue_date: Optional[dt.date] = None,
        source_title: str = "",
        target_version_statute_id: Optional[str] = None,
        post_repeal_item_shift_label: Optional[str] = None,
        body_chapter_move_from: Optional[str] = None,
        lo: Optional[_LegalOperation] = None,
        is_temporary: bool = False,
        has_exact_bound_payload: bool = False,
        temporal_activation: Optional["ActivationRule"] = None,
        preserve_explicit_heading_facet: bool = False,
        witness_rule_id: Optional[str] = None,
    ) -> None:
        explicit_selector = (
            target_selector
            if not isinstance(target_selector, _TargetSelectorUnset)
            else None
        )

        if target_kind is not None:
            if not isinstance(target_kind, TargetKind):
                raise TypeError(f"AmendmentOp target_kind seed must be TargetKind, got {type(target_kind).__name__}")
            seeded_target_unit_kind = unit_kind_for_legacy_target_kind(target_kind)
            if target_unit_kind is not None and target_unit_kind != seeded_target_unit_kind:
                raise ValueError(
                    "AmendmentOp target_kind seed disagrees with explicit target_unit_kind: "
                    f"{target_kind!r} vs {target_unit_kind!r}"
                )
            target_unit_kind = seeded_target_unit_kind
        elif target_unit_kind is None and lo is None and explicit_selector is None:
            raise ValueError(
                "AmendmentOp direct construction requires explicit target_unit_kind "
                "unless lo, an explicit target_selector, or a TargetKind target_kind "
                "seed is provided"
            )
        elif target_unit_kind is None and lo is not None:
            target_unit_kind = _lo_target_record(lo).target_unit_kind

        # Resolve the SINGLE stored representation of this op's target — the typed
        # selector. Precedence (W6 Phase C):
        #   1. ``lo`` present → derive from the lo target path (lo is fully
        #      authoritative for the target columns, matching the pre-W6 behavior
        #      where the lo-sourced setattr loop overwrote all 8 columns).
        #   2. else an explicit ``target_selector`` (e.g. from ``replace_target``
        #      or ``dataclasses.replace`` re-passing the stored field) → use it.
        #   3. else encode the legacy 8-column construction kwargs.
        if lo is not None:
            self.target_selector = TargetSelectorCodecV1.from_legacy(_lo_target_record(lo))
        elif explicit_selector is not None:
            self.target_selector = explicit_selector
        else:
            if target_unit_kind is None:
                raise ValueError("AmendmentOp target_unit_kind could not be resolved")
            self.target_selector = _selector_from_columns(
                target_unit_kind=target_unit_kind,
                target_section=target_section,
                target_chapter=target_chapter,
                target_part=target_part,
                target_paragraph=target_paragraph,
                target_item=target_item,
                target_subitem=target_subitem,
                target_special=target_special,
            )

        # The final chapter, sourced from the stored selector, drives the
        # scope-confidence normalization below (the pre-W6 code read the
        # post-overwrite ``self.target_chapter``).
        final_chapter = self.target_cols.target_chapter

        self.op_id = op_id
        self.op_type = op_type
        self.named_row_targets = named_row_targets
        self.numbered_table_targets = numbered_table_targets
        self.body_root_replace_fallback = body_root_replace_fallback
        self.fallback_provenance = fallback_provenance
        self.sec1_body_johto_fallback = sec1_body_johto_fallback
        self.move_clause_target_unit_kind = move_clause_target_unit_kind
        self.uncovered_body_recovery = uncovered_body_recovery
        self.voimaantulo_repeal = voimaantulo_repeal
        self.extraction_provenance_tags = extraction_provenance_tags
        self.target_guessing_provenance_tags = target_guessing_provenance_tags
        self.scope_provenance_tags = scope_provenance_tags
        self.scope_confidence = normalize_scope_confidence(
            scope_confidence,
            resolved_chapter=final_chapter,
        )
        self.source_statute = source_statute
        self.source_issue_date = source_issue_date
        self.source_title = source_title
        self.target_version_statute_id = target_version_statute_id
        self.post_repeal_item_shift_label = post_repeal_item_shift_label
        self.body_chapter_move_from = body_chapter_move_from
        self.lo = lo
        self.is_temporary = is_temporary
        self.has_exact_bound_payload = has_exact_bound_payload
        self.temporal_activation = temporal_activation
        self.preserve_explicit_heading_facet = preserve_explicit_heading_facet
        self.witness_rule_id = witness_rule_id

        derived_move_clause_target_unit_kind: TargetUnitKind | None = (
            cast(TargetUnitKind | None, self.lo.move_clause_target_unit_kind)
            if self.lo is not None
            else None
        )
        if (
            move_clause_target_unit_kind is not None
            and derived_move_clause_target_unit_kind is not None
            and move_clause_target_unit_kind != derived_move_clause_target_unit_kind
        ):
            raise ValueError(
                "AmendmentOp move_clause_target_unit_kind seed disagrees with lo carrier: "
                f"{move_clause_target_unit_kind!r} vs {derived_move_clause_target_unit_kind!r}"
            )
        self.move_clause_target_unit_kind = move_clause_target_unit_kind or derived_move_clause_target_unit_kind

        # W6 Phase C: the lo-sourced target columns are no longer stored — the
        # stored ``target_selector`` was already derived from the lo target path
        # above (lo authoritative). Only the lo-sourced PROVENANCE side-channels
        # (scope tags / scope confidence / target-guessing tags) remain to fold.
        if self.lo is not None:
            lo_provenance_tags = self.lo.provenance_tags
            lo_scope_tags = tuple(note for note in lo_provenance_tags if note in _SCOPE_PROVENANCE_TAGS)
            lo_target_guessing_tags = tuple(
                note for note in lo_provenance_tags if note in _TARGET_GUESSING_PROVENANCE_TAGS
            )
            lo_scope_conf = lo_scope_confidence(self.lo)
            if lo_scope_tags and not self.scope_provenance_tags:
                object.__setattr__(
                    self,
                    "scope_provenance_tags",
                    lo_scope_tags,
                )
            if lo_scope_conf is not None and self.scope_confidence is None:
                object.__setattr__(
                    self,
                    "scope_confidence",
                    normalize_scope_confidence(
                        lo_scope_conf,
                        resolved_chapter=final_chapter,
                    ),
                )
            if lo_target_guessing_tags and not self.target_guessing_provenance_tags:
                object.__setattr__(
                    self,
                    "target_guessing_provenance_tags",
                    lo_target_guessing_tags,
                )
        if self.scope_confidence is None:
            object.__setattr__(
                self,
                "scope_confidence",
                scope_confidence_from_tags(
                    self.scope_provenance_tags,
                    resolved_chapter=final_chapter,
                ),
            )

    def description(self) -> str:
        cols = self.target_cols
        return _format_operation_description(
            action_type=self.op_type,
            target_unit_kind=cols.target_unit_kind,
            target_label=cols.target_section,
            target_chapter=cols.target_chapter,
            target_paragraph=cols.target_paragraph,
            target_item=cols.target_item,
            target_special=cols.target_special,
        )

    @property
    def target_kind(self) -> TargetKind:
        """Return the Finland legacy target enum as a compatibility projection."""
        return legacy_target_kind_for_unit_kind(self.target_cols.target_unit_kind)

    @property
    def target_cols(self) -> AmendmentOpV1Record:
        """The 8 legacy ``target_*`` columns projected from the stored selector.

        W6 Phase C: the loosely-typed ``target_*`` columns are no longer stored —
        the typed :attr:`target_selector` is the sole stored representation. This
        accessor re-projects it back to the legacy 8-tuple via
        :meth:`TargetSelectorCodecV1.to_legacy`. The codec is lossless
        (TARGET-03) and the corpus parity probe
        (``scripts/w6_target_column_accessor_parity.py``) confirmed this
        reproduces every former stored column byte-exactly on all compiled ops
        across the full pinned corpus. Read sites use ``op.target_cols.target_<col>``.

        Returns an :class:`AmendmentOpV1Record` (frozen): ``target_unit_kind``,
        ``target_section``, ``target_chapter``, ``target_part``,
        ``target_paragraph``, ``target_item``, ``target_subitem``,
        ``target_special``.
        """
        return TargetSelectorCodecV1.to_legacy(self.target_selector)

    @classmethod
    def from_lo(cls, lo: _LegalOperation, idx: int) -> List[AmendmentOp]:
        """Create AmendmentOp(s) from a LegalOperation.

        Returns [] only for inputs classified by
        classify_legal_operation_conversion_skip().
        Section ranges (e.g. '12―14') are expanded into one op each.
        Target fields are derived from lo.target.
        """
        # Every StructuralAction member is mapped EXPLICITLY (the map is exhaustive
        # over StructuralAction) so that a future enum addition fails loud here
        # rather than silently defaulting to a section-body REPLACE. HEADING_REPLACE
        # compiles to the REPLACE op family on purpose: its heading scope is carried
        # by the typed target facet (FacetKind.HEADING / target_special), not by the
        # op-type axis. META and the text-level actions (TEXT_REPLACE / TEXT_REPEAL)
        # also canonicalize to the REPLACE op family at this conversion: their meta /
        # text-patch payload rides on the LegalOperation, and the downstream
        # law-level-text-patch separate-lane diversion keys off that payload + target
        # (not the op-type axis), so REPLACE is their established op_type here. An
        # unmapped action can therefore only be a newly added StructuralAction member;
        # fail loud with a self-evidencing diagnostic instead of defaulting silently.
        _ACTION_MAP: Dict[StructuralAction, OpType] = {
            StructuralAction.REPLACE: OpType.REPLACE,
            StructuralAction.REPEAL: OpType.REPEAL,
            StructuralAction.INSERT: OpType.INSERT,
            StructuralAction.RENUMBER: OpType.RENUMBER,
            StructuralAction.HEADING_REPLACE: OpType.REPLACE,
            StructuralAction.META: OpType.REPLACE,
            StructuralAction.TEXT_REPLACE: OpType.REPLACE,
            StructuralAction.TEXT_REPEAL: OpType.REPLACE,
        }
        op_type: OpType | None = _ACTION_MAP.get(lo.action)
        if op_type is None:
            raise ValueError(
                "AmendmentOp.from_lo received a StructuralAction with no op-type "
                "mapping: "
                f"action={lo.action!r} op_id={lo.op_id!r}. _ACTION_MAP must be "
                "exhaustive over StructuralAction — add the new member explicitly."
            )
        base_id = lo.op_id or f"op_{idx}"
        move_clause_target_unit_kind = cast(TargetUnitKind | None, lo.move_clause_target_unit_kind)
        all_ops: List[AmendmentOp] = []

        target = lo.target
        path_dict = {k: v for k, v in target.path}
        skip = classify_legal_operation_conversion_skip(lo)
        if skip is not None:
            logging.getLogger("lawvm.finland.ops").debug(
                "Skipping LegalOperation conversion for %s: %s",
                lo.op_id,
                skip.reason_code,
            )
            return []

        # Determine primary target kind for range expansion
        for kind_key, unit_kind in [
            ("section", "section"),
            ("chapter", "chapter"),
            ("part", "part"),
        ]:
            if kind_key in path_dict:
                raw_section, path_key = path_dict[kind_key], kind_key
                target_unit_kind = unit_kind
                break
        else:
            logging.getLogger("lawvm.finland.ops").debug(
                "Skipping LegalOperation conversion for %s: no primary target unit",
                lo.op_id,
            )
            return []

        sections = _expand_section_range(raw_section) if target_unit_kind == "section" else [raw_section]
        for s_idx, sec in enumerate(sections):
            op_suffix = f"_{s_idx}" if len(sections) > 1 else ""
            all_ops.append(
                cls(
                    op_id=f"{base_id}{op_suffix}",
                    op_type=op_type,
                    move_clause_target_unit_kind=move_clause_target_unit_kind,
                    lo=_lo_with_path_update(lo, target=target, **{path_key: sec}),
                    witness_rule_id=lo.witness_rule_id,
                )
            )

        return all_ops

    @classmethod
    def extract_law_level_text_patches(
        cls,
        lo_ops: List[_LegalOperation],
    ) -> List["LawLevelTextPatch"]:
        """Extract law-level text patches from LegalOperations.

        Law-level text patches have empty target paths and carry a text_patch
        field.  These represent global "sana X korvataan sanalla Y" amendments
        that apply across the entire statute, not to specific sections.

        Returns a list of LawLevelTextPatch objects.  The matching LOs are
        left in lo_ops (they are harmless in compile_timelines).
        """
        patches: List["LawLevelTextPatch"] = []
        for lo in lo_ops:
            if lo.target.path:
                continue
            patch = lo.text_patch
            if patch is None:
                continue
            source_statute = lo.source.statute_id if lo.source else ""
            effective = (lo.source.effective if lo.source else "") or ""
            patches.append(
                LawLevelTextPatch(
                    op_id=lo.op_id,
                    patch=patch,
                    source_amendment=source_statute,
                    effective=effective,
                )
            )
        return patches


@dataclass(frozen=True)
class LawLevelTextPatch:
    """Text replacement that applies to the entire statute.

    Represents Finnish "sana X korvataan sanalla Y" amendments where the
    replacement applies across all sections, not to a specific target.
    """

    op_id: str
    patch: TextPatchSpec
    source_amendment: str  # e.g. "2025/572"
    effective: str  # effective date


def _apply_law_level_text_patches(
    ir: IRNode,
    patches: List[LawLevelTextPatch],
) -> IRNode:
    """Apply law-level text replacements to the entire IR tree.

    Walks all text nodes in the tree and applies each patch's
    match_text -> replacement substitution.
    """
    for patch_record in patches:
        ir = _apply_single_text_patch(ir, patch_record.patch)
    return ir


def _apply_single_text_patch(node: IRNode, patch: TextPatchSpec) -> IRNode:
    """Apply one text patch to all text nodes in the tree recursively.

    IRNode is frozen, so this rebuilds the tree where changes occur.
    Preserves object identity when no change is needed.
    """
    # Apply to this node's text
    new_text = node.text
    if new_text and patch.selector.match_text in new_text:
        if patch.kind == TextPatchKindEnum.REPLACE:
            replacement = patch.replacement or ""
            if patch.selector.occurrence == 0:
                new_text = new_text.replace(patch.selector.match_text, replacement)
            else:
                # Replace only the Nth occurrence
                parts = new_text.split(patch.selector.match_text)
                if len(parts) > patch.selector.occurrence:
                    before = patch.selector.match_text.join(parts[: patch.selector.occurrence])
                    after = patch.selector.match_text.join(parts[patch.selector.occurrence :])
                    new_text = before + replacement + after
        elif patch.kind == TextPatchKindEnum.DELETE:
            if patch.selector.occurrence == 0:
                new_text = new_text.replace(patch.selector.match_text, "")
            else:
                parts = new_text.split(patch.selector.match_text)
                if len(parts) > patch.selector.occurrence:
                    before = patch.selector.match_text.join(parts[: patch.selector.occurrence])
                    after = patch.selector.match_text.join(parts[patch.selector.occurrence :])
                    new_text = before + after

    # Recurse into children
    new_children = tuple(_apply_single_text_patch(c, patch) for c in node.children)

    # Check if anything changed — preserve identity when possible
    text_changed = new_text is not node.text and new_text != node.text
    children_changed = new_children != node.children

    if not text_changed and not children_changed:
        return node  # no change, preserve identity

    return IRNode(
        kind=node.kind,
        label=node.label,
        text=new_text,
        attrs=node.attrs,
        children=new_children,
    )


@dataclass
class ResolvedOp:
    """A fully-resolved amendment operation ready for application.

    Produced by compile_amendment_ops — carries all data needed by
    apply_op_ir plus group metadata for snapshot emission.

    Architectural observations:
    - This is the late Finland execution waist.
    - `ResolvedOp.intent` is the direction of travel for executable meaning.
    - `op: AmendmentOp` remains here as a compatibility wrapper during the
      migration, not as the intended final semantic authority.
    - New producers should treat typed intent as the default contract here;
      unbound `ResolvedOp` is a compatibility/debug shape, not the target API.

    The normal constructor path is ``ResolvedOp.from_amendment_op(...)``,
    which binds the late-waist fields explicitly. Direct ``ResolvedOp(...)``
    construction is transitional and should not rely on hidden repair from
    the public compatibility shell.
    """

    op: AmendmentOp
    muutos_ir: Optional[IRNode]
    cross_ir: Optional[IRNode]
    amend_sub_ir: Optional[IRNode]
    target_norm: str
    target_unit_kind: TargetUnitKind = "section"
    op_id: str = ""
    # Transitional late-waist compatibility inputs. These are explicit
    # override hooks, not ordinary public runtime authority.
    _op_type_seed: OpType = OpType.REPLACE
    _target_special_override: Optional[str] = None
    sec1_body_johto_fallback: bool = False
    move_clause_target_unit_kind: TargetUnitKind | None = None
    move_clause_target_chapter: Optional[str] = None
    move_clause_target_part: Optional[str] = None
    uncovered_body_recovery: bool = False
    post_repeal_item_shift_label: Optional[str] = None
    body_chapter_move_from: Optional[str] = None
    named_row_targets: tuple[str, ...] = ()
    numbered_table_targets: tuple[str, ...] = ()
    body_root_replace_fallback: bool = False
    fallback_provenance: bool = False
    voimaantulo_repeal: bool = False
    extraction_provenance_tags: tuple[str, ...] = ()
    target_guessing_provenance_tags: tuple[str, ...] = ()
    scope_provenance_tags: tuple[str, ...] = ()
    scope_confidence: ScopeConfidence | None = None
    is_temporary: bool = False
    temporal_activation: Optional["ActivationRule"] = None
    witness_rule_id: Optional[str] = None
    # Phase B override slots. Runtime code should consume the resolved_*
    # accessors below; these are explicit override hooks, not peer authority.
    _op_source_override: Optional[OperationSource] = None
    _target_address_override: Optional[LegalAddress] = None
    _destination_address_override: Optional[LegalAddress] = None
    _source_statute_override: Optional[str] = None
    _source_issue_date_override: Optional[dt.date] = None
    _source_title_override: Optional[str] = None
    target_version_statute_id: Optional[str] = None
    slot_assignment: "SubsectionSlotAssignmentResult | None" = None
    payload_completeness: "PayloadCompletenessWitness | None" = None
    # Canonical three-axis typed intent (Step 1 of canonical intent migration).
    # Optional during migration — None means "use legacy fields for dispatch".
    # See canonical_intent.py and PRO_RESPONSE_CANONICAL_OP_INTENT_TAXONOMY.md.
    intent: "CanonicalIntent | None" = None

    @property
    def resolved_scope_confidence(self) -> ScopeConfidence | None:
        return runtime_scope_confidence_for_op(self)

    @property
    def scope_authority_parity(self) -> ScopeAuthorityParity:
        return scope_authority_parity_for_op(self)

    @property
    def resolved_target_scope_view(self) -> "ResolvedTargetScopeView":
        address = self.resolved_target_address
        section_label: str | None = None
        chapter_label: str | None = None
        part_label: str | None = None
        subsection_label: str | None = None
        item_label: str | None = None
        subitem_label: str | None = None
        resolved_special: str | None = None
        if address is not None:
            for kind, label in address.path:
                if kind == "section":
                    section_label = label
                elif kind == "chapter":
                    chapter_label = label
                elif kind == "part":
                    part_label = label
                elif kind == "subsection":
                    subsection_label = label
                elif kind == "item":
                    item_label = label
                elif kind == "subitem":
                    subitem_label = label
            if address.special == FacetKind.HEADING:
                resolved_special = "otsikko"
            elif address.special == FacetKind.INTRO:
                resolved_special = "johd"

        if self.target_unit_kind == "chapter":
            target_norm = chapter_label or self.target_norm
            target_chapter = None
            target_part = part_label
        elif self.target_unit_kind == "part":
            target_norm = part_label or self.target_norm
            target_chapter = None
            target_part = part_label or self.target_norm
        else:
            target_norm = section_label or self.target_norm
            target_chapter = chapter_label
            target_part = part_label

        target_paragraph: int | None = None
        if subsection_label is not None and subsection_label.isdigit():
            target_paragraph = int(subsection_label)

        # The apply late-waist consumes a single combined kohta+alakohta slot;
        # combine the distinct item/subitem address segments here while keeping
        # the alakohta available separately for consumers that need the split.
        combined_item = f"{item_label}{subitem_label}" if item_label and subitem_label else item_label
        return ResolvedTargetScopeView(
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            target_paragraph=target_paragraph,
            target_item=combined_item,
            target_special=resolved_special,
            target_subitem=subitem_label,
        )

    @property
    def resolved_target_scope(self) -> "ResolvedTargetScopeView":
        """Return the effective late-waist target scope from resolved identity.

        Resolved target address is primary. Transitional late-waist fields are
        only construction-time seed input; direct ``ResolvedOp(...)`` callers
        should bind a target address explicitly instead of relying on hidden
        seed-backed structural scope.
        """
        return self.resolved_target_scope_view

    @classmethod
    def from_amendment_op(
        cls,
        op: AmendmentOp,
        *,
        muutos_ir: Optional[IRNode],
        cross_ir: Optional[IRNode],
        target_unit_kind: TargetUnitKind,
        target_norm: str,
        target_chapter: Optional[str],
        slot_assignment: "SubsectionSlotAssignmentResult | None" = None,
        payload_completeness: "PayloadCompletenessWitness | None" = None,
        op_source: Optional[OperationSource] = None,
        target_address: Optional[LegalAddress] = None,
        destination_address: Optional[LegalAddress] = None,
    ) -> "ResolvedOp":
        """Build the late-waist Finland op from one compatibility-shell op."""
        bound_amend_sub_ir = None
        if slot_assignment is not None:
            bound_amend_sub_ir = slot_assignment.resolve_apply_subsection_ir_for_binding(
                op.op_id,
                op,
                None,
            )
        resolved_target_address = (
            target_address
            if target_address is not None
            else (op.lo.target if op.lo is not None else None)
        )
        resolved_target_address = _augment_replay_address_with_op_descendant_scope(
            _canonicalize_replay_address(resolved_target_address),
            op,
        ) or _synthesize_target_address(
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=op.target_cols.target_part,
            target_paragraph=op.target_cols.target_paragraph,
            target_item=op.target_cols.target_item,
            target_subitem=op.target_cols.target_subitem,
            target_special=op.target_cols.target_special,
        )
        if (
            resolved_target_address is not None
            and resolved_target_address.special == FacetKind.HEADING
            and _section_payload_requires_root_replace(
                op=op,
                muutos_ir=muutos_ir,
                target_unit_kind=target_unit_kind,
                resolved_target_address=resolved_target_address,
            )
        ):
            resolved_target_address = LegalAddress(path=resolved_target_address.path, special=None)
        resolved_destination_address = (
            destination_address
            if destination_address is not None
            else ((op.lo.destination or op.lo.anchor) if op.lo is not None else None)
        )
        resolved_destination_address = _canonicalize_replay_address(resolved_destination_address)
        rop = cls(
            op=op,
            muutos_ir=muutos_ir,
            cross_ir=cross_ir,
            amend_sub_ir=bound_amend_sub_ir,
            op_id=op.op_id,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            _op_type_seed=op.op_type,
            _target_special_override=(
                None
                if _section_payload_requires_root_replace(
                    op=op,
                    muutos_ir=muutos_ir,
                    target_unit_kind=target_unit_kind,
                    resolved_target_address=resolved_target_address,
                )
                else _target_special_override_for_address(op.target_cols.target_special, resolved_target_address)
            ),
            sec1_body_johto_fallback=op.sec1_body_johto_fallback,
            move_clause_target_unit_kind=op.move_clause_target_unit_kind,
            move_clause_target_chapter=op.target_cols.target_chapter,
            move_clause_target_part=op.target_cols.target_part,
            uncovered_body_recovery=op.uncovered_body_recovery,
            post_repeal_item_shift_label=op.post_repeal_item_shift_label,
            body_chapter_move_from=op.body_chapter_move_from,
            named_row_targets=op.named_row_targets,
            numbered_table_targets=op.numbered_table_targets,
            body_root_replace_fallback=op.body_root_replace_fallback,
            fallback_provenance=op.fallback_provenance,
            voimaantulo_repeal=op.voimaantulo_repeal,
            extraction_provenance_tags=op.extraction_provenance_tags,
            target_guessing_provenance_tags=op.target_guessing_provenance_tags,
            scope_provenance_tags=op.scope_provenance_tags,
            scope_confidence=projection_scope_confidence(
                scope_confidence=op.scope_confidence,
                scope_provenance_tags=op.scope_provenance_tags,
                resolved_chapter=target_chapter,
            ),
            is_temporary=op.is_temporary,
            temporal_activation=op.temporal_activation,
            witness_rule_id=op.witness_rule_id,
            _op_source_override=op_source if op_source is not None else (op.lo.source if op.lo is not None else None),
            _target_address_override=resolved_target_address,
            _destination_address_override=resolved_destination_address,
            _source_statute_override=op.source_statute if op.source_statute else None,
            _source_issue_date_override=op.source_issue_date if op.source_issue_date is not None else None,
            _source_title_override=op.source_title if op.source_title else None,
            target_version_statute_id=op.target_version_statute_id,
            slot_assignment=slot_assignment,
            payload_completeness=payload_completeness,
        )
        rop.intent = _build_canonical_intent(rop)
        return rop

    def resolved_amend_sub_ir(self) -> Optional[IRNode]:
        return self.amend_sub_ir

    def has_assigned_subsection_payload(self) -> bool:
        return self.amend_sub_ir is not None

    def description(self) -> str:
        """Return the human-facing amendment description."""
        return _format_operation_description(
            action_type=self.resolved_action_type,
            target_unit_kind=self.target_unit_kind,
            target_label=self.resolved_target_label,
            target_chapter=self.resolved_target_scope_chapter_label,
            target_paragraph=self.effective_target_paragraph,
            target_item=self.effective_target_item_label,
            target_special=self.effective_target_special,
        )

    @property
    def resolved_op_source(self) -> OperationSource | None:
        return self._op_source_override

    @property
    def resolved_target_address(self) -> LegalAddress | None:
        return self._target_address_override

    def _resolved_target_path_label(self, kind: str) -> str | None:
        address = self.resolved_target_address
        if address is None:
            return None
        for part_kind, label in address.path:
            if part_kind == kind:
                return label
        return None

    def _resolved_target_path_label_last(self, kind: str) -> str | None:
        address = self.resolved_target_address
        if address is None:
            return None
        for part_kind, label in reversed(address.path):
            if part_kind == kind:
                return label
        return None

    @property
    def resolved_target_part_label(self) -> str | None:
        return self._resolved_target_path_label("part")

    @property
    def resolved_target_chapter_label(self) -> str | None:
        return self._resolved_target_path_label("chapter")

    @property
    def resolved_target_section_label(self) -> str | None:
        return self._resolved_target_path_label("section")

    @property
    def resolved_target_subsection_label(self) -> str | None:
        return self._resolved_target_path_label("subsection")

    @property
    def resolved_target_item_label(self) -> str | None:
        # kohta + alakohta are distinct address segments; the apply late-waist
        # consumes them as one combined slot, so recombine them here.
        return self.effective_target_item_label

    @property
    def resolved_target_subitem_label(self) -> str | None:
        return self._resolved_target_path_label("subitem")

    @property
    def resolved_target_special(self) -> str | None:
        address = self.resolved_target_address
        if address is None:
            return None
        if address.special == FacetKind.HEADING:
            return "otsikko"
        if address.special == FacetKind.INTRO:
            return "johd"
        return None

    @property
    def resolved_target_label(self) -> str:
        if self.target_unit_kind == "part":
            return self._resolved_target_path_label_last("part") or self.target_norm
        if self.target_unit_kind == "chapter":
            return self._resolved_target_path_label_last("chapter") or self.target_norm
        return self._resolved_target_path_label_last("section") or self.target_norm

    @property
    def resolved_target_scope_chapter_label(self) -> str | None:
        if self.target_unit_kind != "section":
            return None
        return self._resolved_target_path_label_last("chapter")

    @property
    def resolved_target_scope_part_label(self) -> str | None:
        if self.target_unit_kind not in {"section", "chapter"}:
            return None
        return self._resolved_target_path_label_last("part")

    @property
    def effective_target_item_label(self) -> str | None:
        address = self.resolved_target_address
        if address is None:
            return None
        item_label: str | None = None
        subitem_label: str | None = None
        for kind, label in reversed(address.path):
            if kind == "subitem" and subitem_label is None:
                subitem_label = label
            elif kind == "item" and item_label is None:
                item_label = label
        if item_label is None:
            return None
        # The apply late-waist consumes a single combined kohta+alakohta slot
        # (which it re-splits as needed), so recombine the distinct item/subitem
        # address segments here.
        if subitem_label:
            return f"{item_label}{subitem_label}"
        return item_label

    @property
    def effective_target_paragraph(self) -> int | None:
        address = self.resolved_target_address
        if address is None:
            return None
        for kind, label in reversed(address.path):
            if kind == "subsection":
                if label.isdigit():
                    return int(label)
                return None
        return None

    @property
    def effective_target_special(self) -> str | None:
        address = self.resolved_target_address
        resolved_special: str | None = None
        if address is not None:
            if address.special == FacetKind.HEADING:
                resolved_special = "otsikko"
            elif address.special == FacetKind.INTRO:
                resolved_special = "johd"
        target_special = self._target_special_override
        if resolved_special == "otsikko" and target_special == "otsikko_edella":
            return target_special
        return resolved_special or target_special

    @property
    def resolved_section_lookup_scope(self) -> tuple[str, str | None, str | None]:
        scope = self.resolved_section_lookup_scope_view
        return (scope.target_norm, scope.target_chapter, scope.target_part)

    @property
    def resolved_section_lookup_scope_view(self) -> "ResolvedSectionLookupScopeView":
        """Return the neutral section lookup scope for state/path resolution."""
        return ResolvedSectionLookupScopeView(
            target_norm=self.resolved_target_label,
            target_chapter=(
                self.resolved_target_scope_chapter_label
                if self.target_unit_kind == "section"
                else None
            ),
            target_part=(
                self.resolved_target_scope_part_label
                if self.target_unit_kind in {"section", "chapter"}
                else None
            ),
        )

    @property
    def resolved_destination_address(self) -> LegalAddress | None:
        return self._destination_address_override

    @property
    def resolved_source_statute(self) -> str:
        return self._source_statute_override or ""

    @property
    def resolved_source_issue_date(self) -> dt.date | None:
        return self._source_issue_date_override

    @property
    def resolved_source_title(self) -> str:
        return self._source_title_override or ""

    @property
    def resolved_action_type(self) -> OpType:
        """Return the effective late-waist action family for replay/apply."""
        return self._op_type_seed

    @property
    def is_replace_action(self) -> bool:
        return self.resolved_action_type == "REPLACE"

    @property
    def is_insert_action(self) -> bool:
        return self.resolved_action_type == "INSERT"

    @property
    def is_repeal_action(self) -> bool:
        return self.resolved_action_type == "REPEAL"

    @property
    def is_renumber_action(self) -> bool:
        return self.resolved_action_type == "RENUMBER"

    @property
    def replay_requires_apply_pass(self) -> bool:
        return self.muutos_ir is not None or self.is_repeal_action or self.is_renumber_action

    @property
    def provenance(self) -> OpProvenance | None:
        """Typed op-provenance, derived from THIS ResolvedOp's forwarded markers.

        Critically NOT delegated to ``self.op``: the late waist forwards its own
        copies of the recovery markers (``sec1_body_johto_fallback``,
        ``uncovered_body_recovery``, the ``*_provenance_tags`` bags, the
        ``witness_rule_id``) at ``from_amendment_op`` time, and the legacy readers
        consumed THOSE forwarded fields — not the inner ``op``'s, which can carry
        a different (later-mutated) tag set. Deriving from ``self`` keeps the
        typed view in exact lockstep with the fields the old readers used.
        """
        return _derive_op_provenance(
            fallback_provenance=self.fallback_provenance,
            body_root_replace_fallback=self.body_root_replace_fallback,
            sec1_body_johto_fallback=self.sec1_body_johto_fallback,
            uncovered_body_recovery=self.uncovered_body_recovery,
            extraction_provenance_tags=self.extraction_provenance_tags,
            target_guessing_provenance_tags=self.target_guessing_provenance_tags,
            witness_rule_id=self.witness_rule_id,
        )

    @property
    def uses_sec1_body_johto_fallback(self) -> bool:
        return has_recognizer(self.provenance, RecognizerId.SEC1_BODY_JOHTO)

    @property
    def uses_uncovered_body_recovery(self) -> bool:
        return has_recognizer(self.provenance, RecognizerId.UNCOVERED_BODY)

    @property
    def resolved_post_repeal_item_shift_label(self) -> str | None:
        return self.post_repeal_item_shift_label

    @property
    def resolved_group_key(self) -> tuple[TargetUnitKind, str, Optional[str], Optional[str]]:
        group = self.resolved_group_key_view
        return (group.unit_kind, group.target_norm, group.target_chapter, group.target_part)

    @property
    def resolved_group_key_view(self) -> "ResolvedGroupKeyView":
        """Return the structural replay-group key from resolved target identity.

        Prefer the resolved target address once the late waist has it, because
        that is the stronger identity carrier for qualifiers than the mirrored
        section/chapter/part fields. The structural unit itself still comes
        from ``target_unit_kind``, and group keys keep the neutral
        ``target_norm`` as the primary structural label so replay grouping does
        not silently reclassify or relabel ops to descendant-resolved labels.
        """
        address = self.resolved_target_address
        if address is not None and address.path:
            labels = {kind: label for kind, label in address.path}
            if self.target_unit_kind == "section" and "section" in labels:
                return ResolvedGroupKeyView(
                    unit_kind="section",
                    target_norm=self.target_norm,
                    target_chapter=labels.get("chapter"),
                    target_part=labels.get("part"),
                )
            if self.target_unit_kind == "chapter" and "chapter" in labels:
                return ResolvedGroupKeyView(
                    unit_kind="chapter",
                    target_norm=self.target_norm,
                    target_chapter=None,
                    target_part=labels.get("part"),
                )
            if self.target_unit_kind == "part" and "part" in labels:
                return ResolvedGroupKeyView(
                    unit_kind="part",
                    target_norm=self.target_norm,
                    target_chapter=None,
                    target_part=labels["part"],
                )
        scope = self.resolved_target_scope_view
        if self.target_unit_kind == "section":
            return ResolvedGroupKeyView(
                unit_kind="section",
                target_norm=scope.target_norm,
                target_chapter=scope.target_chapter,
                target_part=scope.target_part,
            )
        if self.target_unit_kind == "chapter":
            return ResolvedGroupKeyView(
                unit_kind="chapter",
                target_norm=scope.target_norm,
                target_chapter=None,
                target_part=scope.target_part,
            )
        return ResolvedGroupKeyView(
            unit_kind="part",
            target_norm=scope.target_norm,
            target_chapter=None,
            target_part=scope.target_part or scope.target_norm,
        )

    def targets_whole_unit(self, unit_kind: TargetUnitKind) -> bool:
        """Return True when this target points at the whole structural unit."""
        address = self.resolved_target_address
        if address is not None and address.path:
            return address.special is None and address.path[-1][0] == unit_kind
        if unit_kind == "section":
            return (
                self.target_unit_kind == "section"
                and self.effective_target_paragraph is None
                and self.effective_target_item_label is None
                and self.effective_target_special is None
            )
        if unit_kind in {"chapter", "part"}:
            return (
                self.target_unit_kind == unit_kind
                and self.effective_target_paragraph is None
                and self.effective_target_item_label is None
                and self.effective_target_special is None
            )
        return False

    def targets_subsection_only(self) -> bool:
        """Return True when this target addresses exactly one subsection."""
        address = self.resolved_target_address
        if address is not None and address.path:
            return address.special is None and address.path[-1][0] == "subsection"
        return (
            self.effective_target_paragraph is not None
            and self.effective_target_item_label is None
            and self.effective_target_special is None
        )

def _op_target_subsection_label(op: "AmendmentOp") -> Optional[str]:
    """Return the AmendmentOp subsection coordinate as a bare structural label.

    ``AmendmentOp`` carries the subsection as an int paragraph; FailedOp stores
    the subsection as a bare label so governance consumers can match it against
    structural path labels without re-parsing the rendered description.
    """
    target_paragraph = op.target_cols.target_paragraph
    if target_paragraph is None:
        return None
    return str(target_paragraph)


@dataclass
class FailedOp:
    """A structured record of an operation that could not be applied."""

    amendment_id: str
    description: str
    reason: str
    target_section: str
    target_unit_kind: TargetUnitKind
    target_chapter: Optional[str] = None
    target_part: Optional[str] = None
    # Descendant coordinates of the failed target, carried as typed scope so
    # governance consumers never re-parse the rendered description. Both are
    # bare labels (e.g. subsection "2", item "3 a"), matching the structural
    # path labels rather than the human "N mom / M kohta" rendering.
    target_subsection: Optional[str] = None
    target_item: Optional[str] = None
    reason_code: str = ""
    target_statute_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.target_unit_kind not in {"section", "chapter", "part"}:
            raise ValueError(f"FailedOp.target_unit_kind must be explicit neutral scope, got {self.target_unit_kind!r}")

    @property
    def compat_target_kind_code(self) -> str:
        return compat_target_kind_code_for_scope(self.target_unit_kind)

    def scope_detail(self) -> dict[str, object]:
        return {
            "target_unit_kind": self.target_unit_kind,
            "target_section": self.target_section,
            "target_chapter": self.target_chapter,
            "target_part": self.target_part,
            "target_subsection": self.target_subsection,
            "target_item": self.target_item,
        }

    def as_detail(self) -> dict[str, object]:
        return {
            "amendment_id": self.amendment_id,
            "description": self.description,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "target_statute_id": self.target_statute_id,
            **self.scope_detail(),
        }

    @classmethod
    def from_scope(
        cls,
        *,
        amendment_id: str,
        description: str,
        reason: str,
        reason_code: str = "",
        target_section: str,
        target_unit_kind: TargetUnitKind,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
        target_subsection: Optional[str] = None,
        target_item: Optional[str] = None,
    ) -> "FailedOp":
        """Build a failed-op record from neutral structural scope."""
        return cls(
            amendment_id=amendment_id,
            description=description,
            reason=reason,
            reason_code=reason_code,
            target_section=target_section,
            target_chapter=target_chapter,
            target_part=target_part,
            target_subsection=target_subsection,
            target_item=target_item,
            target_unit_kind=target_unit_kind,
        )


@dataclass(frozen=True, slots=True)
class ResolvedTargetScopeView:
    """Structured resolved target scope for late-waist Finland consumers."""

    target_norm: str
    target_chapter: str | None
    target_part: str | None
    target_paragraph: int | None
    target_item: str | None
    target_special: str | None
    target_subitem: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedGroupKeyView:
    """Structured replay grouping key for resolved Finland operations."""

    unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: str | None
    target_part: str | None


@dataclass(frozen=True, slots=True)
class ResolvedSectionLookupScopeView:
    """Structured neutral section lookup scope for resolved Finland operations."""

    target_norm: str
    target_chapter: str | None
    target_part: str | None


@dataclass(frozen=True)
class ReplayProfile:
    """Immutable replay configuration for a single amendment pass."""

    mode: Literal["official_consolidation", "legal_pit"]
    synthesize_repeal_placeholders: bool
    replace_same_numbered_section_insert: bool
    replace_same_numbered_container_insert: bool
    normalize_replay_text: bool
    allows_context_dependent_anchor_resolution: bool = True


def get_replay_profile(
    mode: Literal["official_consolidation", "legal_pit"],
    strict_profile: StrictProfile | None = None,
) -> ReplayProfile:
    """Return the canonical ReplayProfile for a given mode."""
    allows_context_dependent_anchor_resolution = (
        True if strict_profile is None else strict_profile.allows_context_dependent_anchor_resolution
    )
    if mode == "official_consolidation":
        return ReplayProfile(
            mode=mode,
            synthesize_repeal_placeholders=True,
            replace_same_numbered_section_insert=True,
            replace_same_numbered_container_insert=True,
            normalize_replay_text=True,
            allows_context_dependent_anchor_resolution=allows_context_dependent_anchor_resolution,
        )
    if mode == "legal_pit":
        return ReplayProfile(
            mode=mode,
            synthesize_repeal_placeholders=False,
            replace_same_numbered_section_insert=False,
            replace_same_numbered_container_insert=False,
            normalize_replay_text=True,
            allows_context_dependent_anchor_resolution=allows_context_dependent_anchor_resolution,
        )
    raise ValueError(f"Unknown replay mode: {mode}")


# ---------------------------------------------------------------------------
# Canonical intent builder (Step 1 migration)
# ---------------------------------------------------------------------------

_log = logging.getLogger("lawvm.finland.ops")


def _synthesize_target_address(
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
    target_paragraph: int | None,
    target_item: str | None,
    target_subitem: str | None = None,
    target_special: str | None,
) -> Optional[LegalAddress]:
    """Build a target LegalAddress from explicit late-waist construction inputs.

    This is a construction-time helper for ResolvedOp producers. Typed intent
    lowering should consume ``rop.resolved_target_address`` directly rather than
    reconstructing identity for itself.
    """
    path_parts: List[Tuple[str, str]] = []

    def _canonical_part(label: str | None) -> str | None:
        if not label:
            return None
        return _norm_num_token(str(label))

    if target_unit_kind == "chapter":
        canonical_part = _canonical_part(target_part)
        if canonical_part:
            path_parts.append(("part", canonical_part))
        if not target_norm:
            return None
        path_parts.append(("chapter", str(target_norm)))
    elif target_unit_kind == "part":
        part_label = _canonical_part(target_part or target_norm)
        if not part_label:
            return None
        path_parts.append(("part", part_label))
    else:
        canonical_part = _canonical_part(target_part)
        if canonical_part:
            path_parts.append(("part", canonical_part))
        if target_chapter:
            path_parts.append(("chapter", str(target_chapter)))
        if not target_norm:
            return None
        path_parts.append(("section", str(target_norm)))

    if target_paragraph is not None:
        path_parts.append(("subsection", str(target_paragraph)))

    if target_item is not None:
        # kohta (item) and alakohta (subitem) are distinct hierarchy levels.
        # The legacy combined slot may still carry a ``<digit><letter>`` compound
        # (e.g. ``3d``); split it back into the two separate address segments so
        # the canonical address never collapses the alakohta into the kohta.
        item_text = str(target_item)
        compound = re.fullmatch(r"(\d+)([a-z])", item_text) if target_subitem is None else None
        if target_subitem is not None:
            if item_text.endswith(str(target_subitem)):
                item_text = item_text[: -len(str(target_subitem))]
            path_parts.append(("item", item_text))
            path_parts.append(("subitem", str(target_subitem)))
        elif compound is not None:
            path_parts.append(("item", compound.group(1)))
            path_parts.append(("subitem", compound.group(2)))
        else:
            path_parts.append(("item", item_text))

    special: Optional[FacetKind] = None
    if target_special in {"otsikko", "otsikko_edella"}:
        special = FacetKind.HEADING
    elif target_special == "johd":
        special = FacetKind.INTRO

    return LegalAddress(path=tuple(path_parts), special=special)


def _canonicalize_replay_address(address: LegalAddress | None) -> LegalAddress | None:
    """Normalize replay-side part labels to the live-tree identity form.

    Finland replay and materialization use normalized part labels in structural
    paths (for example ``III`` -> ``3`` and ``IIa`` -> ``2a``). Public parse
    surfaces may still expose source-shaped labels, but the late execution waist
    must canonicalize them before grouping or path resolution.
    """
    if address is None or not address.path:
        return address
    changed = False
    canonical_path: list[tuple[str, str]] = []
    for kind, label in address.path:
        if kind == "part":
            canonical_label = _norm_num_token(label)
            if canonical_label != label:
                changed = True
            canonical_path.append((kind, canonical_label))
            continue
        canonical_path.append((kind, label))
    if not changed:
        return address
    return LegalAddress(path=tuple(canonical_path), special=address.special)


def _augment_replay_address_with_op_descendant_scope(
    address: LegalAddress | None,
    op: AmendmentOp,
) -> LegalAddress | None:
    """Preserve AmendmentOp child scope when a legacy LO only names a section."""
    if address is None or not address.path:
        return address
    cols = op.target_cols
    if cols.target_unit_kind != "section":
        return address
    if any(kind in {"subsection", "item", "subitem"} for kind, _label in address.path):
        return address
    has_descendant = (
        cols.target_paragraph is not None
        or cols.target_item is not None
        or cols.target_subitem is not None
    )
    has_special = cols.target_special is not None
    if not has_descendant and not has_special:
        return address

    path_parts = list(address.path)
    if cols.target_paragraph is not None:
        path_parts.append(("subsection", str(cols.target_paragraph)))
    if cols.target_item is not None:
        item_text = str(cols.target_item)
        compound = re.fullmatch(r"(\d+)([a-z])", item_text) if cols.target_subitem is None else None
        if cols.target_subitem is not None:
            if item_text.endswith(str(cols.target_subitem)):
                item_text = item_text[: -len(str(cols.target_subitem))]
            path_parts.append(("item", item_text))
            path_parts.append(("subitem", str(cols.target_subitem)))
        elif compound is not None:
            path_parts.append(("item", compound.group(1)))
            path_parts.append(("subitem", compound.group(2)))
        else:
            path_parts.append(("item", item_text))

    special = address.special
    if special is None:
        if cols.target_special in {"otsikko", "otsikko_edella"}:
            special = FacetKind.HEADING
        elif cols.target_special == "johd":
            special = FacetKind.INTRO
    return LegalAddress(path=tuple(path_parts), special=special)


def _rebind_resolved_target_address(
    rop: ResolvedOp,
    *,
    target_paragraph: int | None,
    target_item: str | None,
    target_special: str | None,
) -> ResolvedOp:
    """Return a ResolvedOp whose target override matches rewritten granularity.

    Dispatch helpers sometimes reinterpret a subsection-level late-waist target
    into item-level routing. Keep the resolved target address authoritative in
    those cases instead of dropping back to mirror-only target fields.
    """
    address = rop.resolved_target_address
    rebound_address: LegalAddress | None = None
    if address is not None:
        prefix: list[tuple[str, str]] = []
        for kind, label in address.path:
            if kind in {"subsection", "item", "subitem"}:
                break
            prefix.append((kind, label))
        if target_paragraph is not None:
            prefix.append(("subsection", str(target_paragraph)))
        if target_item is not None:
            # Re-expand a combined kohta+alakohta slot into separate segments so
            # the rebound address keeps the alakohta as its own level.
            item_text = str(target_item)
            compound = re.fullmatch(r"(\d+)([a-z])", item_text)
            if compound is not None:
                prefix.append(("item", compound.group(1)))
                prefix.append(("subitem", compound.group(2)))
            else:
                prefix.append(("item", item_text))
        special: FacetKind | None = None
        if target_special in {"otsikko", "otsikko_edella"}:
            special = FacetKind.HEADING
        elif target_special == "johd":
            special = FacetKind.INTRO
        rebound_address = LegalAddress(path=tuple(prefix), special=special)
    rebound = dc_replace(
        rop,
        _target_special_override=_target_special_override_for_address(target_special, rebound_address),
        _target_address_override=rebound_address,
        intent=None,
    )
    rebound.intent = _build_canonical_intent(rebound)
    return rebound


def _section_payload_requires_root_replace(
    *,
    op: AmendmentOp,
    muutos_ir: IRNode | None,
    target_unit_kind: TargetUnitKind,
    resolved_target_address: LegalAddress | None,
) -> bool:
    """Return True when a heading-scoped legacy op really replaces the whole section.

    Finland clauses like "22 a §:n otsikko ja 1 momentti" can collapse to one
    section-level replace whose payload carries both the heading and substantive
    subsection content. In that case, keeping the resolved target on the heading
    facet loses the body update during apply. Bind the op to the section root
    instead, while leaving pure heading-only payloads on the facet path.
    """
    if target_unit_kind != "section":
        return False
    if op.op_type != OpType.REPLACE:
        return False
    if resolved_target_address is None or resolved_target_address.special != FacetKind.HEADING:
        return False
    cols = op.target_cols
    if cols.target_paragraph is not None or cols.target_item is not None:
        return False
    if op.preserve_explicit_heading_facet:
        return False
    if muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return False
    return any(
        child.kind not in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.OMISSION}
        for child in muutos_ir.children
    )


def _determine_unit_kind(rop: ResolvedOp) -> str:
    """Determine the unit_kind string for a NodeTarget or FacetTarget host.

    Returns the finest-grained structural unit being targeted.
    """
    if rop.effective_target_item_label is not None:
        return "item"
    if rop.effective_target_paragraph is not None:
        return "subsection"
    if rop.target_unit_kind == "chapter":
        return "chapter"
    if rop.target_unit_kind == "part":
        return "part"
    return "section"


def _target_special_override_for_address(
    target_special: str | None,
    address: LegalAddress | None,
) -> str | None:
    """Return only the facet nuance not already expressed by resolved address.

    Resolved address owns ordinary heading/intro facet identity. The override
    survives only when the public compatibility shell carries a distinction
    that the address cannot encode directly, such as ``otsikko_edella`` or
    unknown legacy specials.
    """
    if not target_special:
        return None
    resolved_special: str | None = None
    if address is not None:
        if address.special == FacetKind.HEADING:
            resolved_special = "otsikko"
        elif address.special == FacetKind.INTRO:
            resolved_special = "johd"
    if resolved_special is None:
        return target_special
    if resolved_special == "otsikko" and target_special == "otsikko_edella":
        return target_special
    if target_special == resolved_special:
        return None
    return target_special


def _intent_payload_for_resolved_op(rop: ResolvedOp) -> IRNode | None:
    """Return the payload Finland should place on typed execution intent.

    Subsection/item operations may carry their authoritative payload only on the
    late-waist sparse-slot carrier, not on ``muutos_ir``. Core canonical-intent
    types now require non-None payloads for Replace/Insert, so Finland must
    project that payload from ``ResolvedOp`` instead of relying on older
    payloadless compatibility behavior.
    """
    if rop.effective_target_special in {"otsikko", "otsikko_edella", "johd"}:
        return rop.muutos_ir
    if rop.effective_target_item_label is not None or rop.effective_target_paragraph is not None:
        return rop.resolved_amend_sub_ir() or rop.muutos_ir
    return rop.muutos_ir


def _build_canonical_intent(rop: ResolvedOp) -> "CanonicalIntent | None":
    """Build a CanonicalIntent from ResolvedOp legacy fields.

    Pure function: maps legacy waist fields to the typed three-axis intent.
    Returns None for op types that cannot yet be mapped (graceful degradation
    during migration).

    This is Step 1 of the canonical intent migration — intent is advisory only.
    Apply dispatch will NOT read this field until Step 2.

    Validation: unit_kind and facet strings are checked against Finland's
    frontend-owned registry
    via validate_intent_target.  Unknown values emit a WARNING but do not block
    intent construction — validation is advisory at this migration phase.
    """
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        FacetTarget,
        Insert,
        InsertOrder,
        IntentKind,
        Move,
        NodeTarget,
        OccupancyPolicy,
        OccupancyClass,
        Relabel,
        Repeal,
        Replace,
        _IRNodeLike,
    )
    from lawvm.core.unit_registry import validate_intent_target
    from lawvm.finland.unit_registry import FINLAND_REGISTRY

    def _replace_policy() -> OccupancyPolicy:
        """REPLACE occupancy: a live section is the primary expectation, but a
        tombstone is permitted as the legitimate reenactment lane (allowed but
        non-primary).  REPLACE on ABSENT falls outside allowed_from and is
        recorded as an occupancy policy violation observation.
        """
        return OccupancyPolicy(
            primary_expected_from=frozenset({OccupancyClass.SUBSTANTIVE}),
            allowed_from=frozenset(
                {OccupancyClass.SUBSTANTIVE, OccupancyClass.TOMBSTONE}
            ),
            result=OccupancyClass.SUBSTANTIVE,
        )

    def _insert_policy() -> OccupancyPolicy:
        """INSERT occupancy: a fresh absent slot is the primary expectation;
        a tombstone/scaffold occupant is the legitimate reenact-insert lane
        (allowed but non-primary, recorded for triage).  INSERT onto a
        substantive occupant falls outside allowed_from and is recorded as an
        occupancy policy violation observation.
        """
        return OccupancyPolicy(
            primary_expected_from=frozenset({OccupancyClass.ABSENT}),
            allowed_from=frozenset(
                {
                    OccupancyClass.ABSENT,
                    OccupancyClass.TOMBSTONE,
                    OccupancyClass.SCAFFOLD,
                }
            ),
            result=OccupancyClass.SUBSTANTIVE,
        )

    try:
        # Target identity must already be mirrored onto the late waist.
        address = rop.resolved_target_address
        if address is None:
            return None

        op_type = rop.resolved_action_type
        payload = _intent_payload_for_resolved_op(rop)
        if op_type in {"REPLACE", "INSERT"} and payload is None:
            _log.debug(
                "Skipping typed intent build for %s: %s has no payload",
                rop.op_id or "<missing-op-id>",
                op_type,
            )
            return None

        # --- FacetTarget cases (heading / intro) ---
        target_special = rop.effective_target_special

        if target_special in ("otsikko", "otsikko_edella"):
            # Host is the address without the special marker
            host = LegalAddress(path=address.path, special=None)
            target = FacetTarget(host=host, facet=FacetKind.HEADING)
            validate_intent_target(target, FINLAND_REGISTRY)

            match op_type:
                case OpType.REPLACE:
                    assert payload is not None
                    return Replace(
                        kind=IntentKind.REPLACE,
                        target=target,
                        payload=cast(_IRNodeLike, payload),
                        contract=ExecutionContract(
                            occupancy=_replace_policy(),
                            coverage=CoverageMode.EXACT,
                        ),
                    )
                case OpType.REPEAL:
                    return Repeal(
                        kind=IntentKind.REPEAL,
                        target=NodeTarget(address=host),
                        contract=ExecutionContract(
                            occupancy=OccupancyPolicy.repeal_to_tombstone(),
                            coverage=CoverageMode.EXACT,
                        ),
                    )
                case OpType.INSERT:
                    # INSERT otsikko = add a heading to a section that had none.
                    # Modelled as a REPLACE so it works whether or not the heading
                    # already exists; tombstone headings are a legitimate reenact
                    # lane (allowed but non-primary).
                    if payload is None:
                        return None
                    return Replace(
                        kind=IntentKind.REPLACE,
                        target=target,
                        payload=cast(_IRNodeLike, payload),
                        contract=ExecutionContract(
                            occupancy=_replace_policy(),
                            coverage=CoverageMode.EXACT,
                        ),
                    )
                case OpType.RENUMBER:
                    # RENUMBER heading — uncommon, return None for graceful degradation
                    return None
                case _ as unreachable:
                    assert_never(unreachable)

        if target_special == "johd":
            host = LegalAddress(path=address.path, special=None)
            target = FacetTarget(host=host, facet=FacetKind.INTRO)
            validate_intent_target(target, FINLAND_REGISTRY)

            match op_type:
                case OpType.REPLACE:
                    assert payload is not None
                    return Replace(
                        kind=IntentKind.REPLACE,
                        target=target,
                        payload=cast(_IRNodeLike, payload),
                        contract=ExecutionContract(
                            occupancy=_replace_policy(),
                            coverage=CoverageMode.EXACT,
                        ),
                    )
                case OpType.REPEAL:
                    return Repeal(
                        kind=IntentKind.REPEAL,
                        target=NodeTarget(address=host),
                        contract=ExecutionContract(
                            occupancy=OccupancyPolicy.repeal_to_tombstone(),
                            coverage=CoverageMode.EXACT,
                        ),
                    )
                case OpType.INSERT | OpType.RENUMBER:
                    return None
                case _ as unreachable:
                    assert_never(unreachable)

        # --- NodeTarget cases ---
        node_addr = LegalAddress(path=address.path, special=None)
        node_target = NodeTarget(address=node_addr)
        validate_intent_target(node_target, FINLAND_REGISTRY)

        source_parent = node_target.address.parent()
        destination_parent = None
        resolved_destination_address = rop.resolved_destination_address
        if resolved_destination_address is not None:
            if resolved_destination_address.leaf_kind() in {"chapter", "part"}:
                destination_parent = resolved_destination_address
            else:
                destination_parent = resolved_destination_address.parent()
        elif rop.move_clause_target_unit_kind == "chapter" and rop.move_clause_target_chapter:
            destination_parent = LegalAddress(path=(("chapter", rop.move_clause_target_chapter),))
        elif rop.move_clause_target_unit_kind == "part" and rop.move_clause_target_part:
            destination_parent = LegalAddress(path=(("part", rop.move_clause_target_part),))
        if (
            rop.move_clause_target_unit_kind is not None
            and destination_parent is not None
            and source_parent is not None
            and destination_parent.path != source_parent.path
            # A Move intent carries no payload; a payload-bearing move rider
            # ("muutetaan X §, joka samalla siirretään Y lukuun") must stay a
            # destination-scoped Replace so the replacement text lands via the
            # section move+replace recovery instead of being silently dropped.
            and payload is None
        ):
            return Move(
                kind=IntentKind.MOVE,
                source=node_target,
                destination_parent=destination_parent,
                contract=ExecutionContract(
                    occupancy=OccupancyPolicy.fresh_insert(),
                ),
            )

        match op_type:
            case OpType.REPLACE:
                assert payload is not None
                return Replace(
                    kind=IntentKind.REPLACE,
                    target=node_target,
                    payload=cast(_IRNodeLike, payload),
                    contract=ExecutionContract(
                        occupancy=_replace_policy(),
                        coverage=CoverageMode.EXACT,
                    ),
                )
            case OpType.REPEAL:
                return Repeal(
                    kind=IntentKind.REPEAL,
                    target=node_target,
                    contract=ExecutionContract(
                        occupancy=OccupancyPolicy.repeal_to_tombstone(),
                        coverage=CoverageMode.EXACT,
                    ),
                )
            case OpType.INSERT:
                assert payload is not None
                return Insert(
                    kind=IntentKind.INSERT,
                    target=node_target,
                    payload=cast(_IRNodeLike, payload),
                    contract=ExecutionContract(
                        occupancy=_insert_policy(),
                        coverage=CoverageMode.EXACT,
                        insert_order=InsertOrder.SORTED_FAMILY,
                    ),
                )
            case OpType.RENUMBER:
                # Relabel needs both source and destination addresses on the late
                # execution waist. Missing destination is now a lowering bug or an
                # intentional graceful-degradation case for older tests.
                destination_address = rop.resolved_destination_address
                if destination_address is not None:
                    # The legacy destination often carries only the new leaf label
                    # (for example ``section:3``), while Relabel requires the full
                    # parent path to stay identical to the source address.
                    source_address = rop.resolved_target_address
                    source_path = source_address.path if source_address is not None else ()
                    if source_path:
                        dest_leaf_kind = source_path[-1][0]
                        dest_path = source_path[:-1] + ((dest_leaf_kind, destination_address.leaf_label()),)
                    else:
                        dest_path = destination_address.path
                    dest_target = NodeTarget(
                        address=LegalAddress(path=dest_path, special=None),
                    )
                    return Relabel(
                        kind=IntentKind.RELABEL,
                        source=node_target,
                        destination=dest_target,
                        contract=ExecutionContract(
                            occupancy=OccupancyPolicy.same_slot_replace(),
                            coverage=CoverageMode.EXACT,
                        ),
                    )
                # Cannot determine destination — graceful degradation
                return None
            case _ as unreachable:
                assert_never(unreachable)

    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        _log.debug(
            "Failed to build canonical intent for %s: %s",
            rop.op_id or "<missing-op-id>",
            rop.description(),
            exc_info=True,
        )
        return None


def intent_required_for_apply(rop: ResolvedOp) -> bool:
    """Return True when FI apply must have a typed intent for this ResolvedOp.

    Current rule:

    - REPLACE / INSERT / REPEAL / RENUMBER must all carry CanonicalIntent

    Any renumber producer that still reaches apply without typed intent is
    using a legacy compatibility path and should be fixed upstream instead of
    being tolerated here.
    """
    if rop.resolved_action_type in {"REPLACE", "INSERT", "REPEAL", "RENUMBER"}:
        return True
    return False


def typed_intent_action_mismatch(rop: ResolvedOp) -> str | None:
    """Return a blocking mismatch reason when intent.kind contradicts op_type.

    This is a stricter guard than `_assert_intent_compat`: once a typed intent
    reaches FI apply, the action family must agree with the legacy mirrored
    action on `ResolvedOp`. Unknown intent kinds remain non-blocking during the
    migration so exploratory/placeholder intent objects still route through the
    existing fallback path.
    """
    intent = rop.intent
    if intent is None:
        return None

    expected_kind = _ACTION_TO_INTENT_KIND.get(rop.resolved_action_type)
    if expected_kind is None:
        return None

    actual_kind = intent.kind if isinstance(intent.kind, str) else intent.kind.value
    if actual_kind not in set(_ACTION_TO_INTENT_KIND.values()):
        return None
    if actual_kind != expected_kind:
        # Carve-out: for facet-targeted operations (heading/intro), INSERT and
        # REPLACE are semantically equivalent — both set the facet content with
        # an upsert policy.  The Insert CanonicalIntent does not support
        # FacetTarget, so INSERT otsikko maps to Replace(FacetTarget).
        from lawvm.core.canonical_intent import FacetTarget, Replace
        if (
            rop.resolved_action_type == "INSERT"
            and actual_kind == "replace"
            and isinstance(intent, Replace)
            and isinstance(intent.target, FacetTarget)
        ):
            return None
        return (
            f"legacy op_type={rop.resolved_action_type!r} maps to intent.kind={expected_kind!r}, "
            f"but typed intent carries {actual_kind!r}"
        )
    return None


# ---------------------------------------------------------------------------
# Intent-compat cross-validation
# ---------------------------------------------------------------------------
# _assert_intent_compat verifies that the typed CanonicalIntent built by
# _build_canonical_intent is consistent with the late-waist ResolvedOp fields.
# Lives here (not in apply.py) so that the apply.py op_type ratchet ceiling
# is not affected: apply.py imports and calls this from ops.py.
# ---------------------------------------------------------------------------


class _IntentCompatStats:
    """Simple mutable counter for intent/op field mismatches detected by
    _assert_intent_compat.  Module-level singleton; never reset automatically.
    Inspect in debug sessions or tests to track migration health.
    """

    __slots__ = ("total", "action_family", "unit_kind", "facet")

    def __init__(self) -> None:
        self.total: int = 0
        self.action_family: int = 0
        self.unit_kind: int = 0
        self.facet: int = 0

    @override
    def __repr__(self) -> str:
        return (
            f"IntentCompatStats(total={self.total}, "
            f"action_family={self.action_family}, "
            f"unit_kind={self.unit_kind}, "
            f"facet={self.facet})"
        )


intent_compat_stats = _IntentCompatStats()

# Maps legacy op_type strings → expected IntentKind values (as strings).
_ACTION_TO_INTENT_KIND: Dict[str, str] = {
    "REPLACE": "replace",
    "INSERT": "insert",
    "REPEAL": "repeal",
    "RENUMBER": "relabel",
}

# Maps fine-grained NodeTarget unit_kind strings → coarse AmendmentOp target_kind codes.
# Subsection and item targets both sit under a section (target_kind TargetKind.SECTION).
_UNIT_KIND_TO_LEGACY_TARGET_KIND: Dict[str, TargetKind] = {
    "section": TargetKind.SECTION,
    "subsection": TargetKind.SECTION,
    "item": TargetKind.SECTION,
    "row": TargetKind.SECTION,
    "chapter": TargetKind.CHAPTER,
    "part": TargetKind.PART,
}


def legacy_target_kind_for_unit_kind(unit_kind: Literal["section", "chapter", "part"]) -> TargetKind:
    """Project neutral unit vocabulary into the Finland legacy target enum."""
    return _UNIT_KIND_TO_LEGACY_TARGET_KIND[unit_kind]


def unit_kind_for_legacy_target_kind(target_kind: TargetKind) -> TargetUnitKind:
    """Project the Finland legacy target enum into neutral unit vocabulary."""
    if target_kind == TargetKind.SECTION:
        return "section"
    if target_kind == TargetKind.CHAPTER:
        return "chapter"
    return "part"


def compat_target_kind_code_for_scope(
    target_unit_kind: TargetUnitKind,
) -> str:
    """Project neutral scope to a legacy compat code for presentation surfaces."""
    return legacy_target_kind_for_unit_kind(target_unit_kind).value

# Maps legacy target_special strings → expected FacetTarget.facet values.
_SPECIAL_TO_FACET: Dict[str, FacetKind] = {
    "otsikko": FacetKind.HEADING,
    "otsikko_edella": FacetKind.HEADING,
    "johd": FacetKind.INTRO,
}


def _assert_intent_compat(
    rop: ResolvedOp,
    intent: "CanonicalIntent",
    ctx_label: str,
    *,
    findings_out: Optional[List[Finding]] = None,
) -> None:
    """Cross-validate typed CanonicalIntent fields against late-waist fields.

    Non-blocking — logs WARNING on any discrepancy, never raises.
    Increments ``intent_compat_stats`` counters for debugging.

    Three checks are performed:

    1. Action-family check
       ``intent.kind`` (e.g. ``replace``) must correspond to the legacy action
       field via ``_ACTION_TO_INTENT_KIND``.

    2. Unit-kind check (NodeTarget only)
       When ``intent.target`` is a ``NodeTarget``, its ``unit_kind`` (e.g.
       ``section``) must correspond to ``rop.target_unit_kind``. Subsection/item
       targets both normalize under ``section`` — the check verifies neutral
       structural scope consistency.

    3. Facet check (FacetTarget only)
       When ``intent.target`` is a ``FacetTarget``, its ``facet`` (e.g.
       ``heading``) must correspond to ``rop.effective_target_special`` (e.g.
       ``otsikko``) via ``_SPECIAL_TO_FACET``.

    Called during the migration period while both late-waist mirrors and typed
    fields coexist.  Remove once migration is complete and the mirrors are the
    only remaining execution contract.
    """
    from lawvm.core.canonical_intent import FacetTarget, Insert, Move, NodeTarget, Relabel, Replace, Repeal, TextPatch

    def _record_mismatch(mismatch_kind: str, detail: dict[str, object]) -> None:
        if findings_out is None:
            return
        findings_out.append(
            Finding(
                kind="APPLY.INTENT_COMPAT_MISMATCH",
                role="observation",
                stage="apply",
                source_statute=rop.resolved_source_statute,
                detail={
                    "mismatch_kind": mismatch_kind,
                    "ctx_label": ctx_label,
                    "op_id": rop.op_id,
                    "strict_disposition": "record",
                    **detail,
                },
                blocking=False,
            )
        )

    legacy_action = rop.resolved_action_type

    # --- Check 1: action family ---
    expected_kind = _ACTION_TO_INTENT_KIND.get(legacy_action)
    # intent.kind is a StrEnum — compare to its string value.
    actual_kind = intent.kind if isinstance(intent.kind, str) else intent.kind.value
    facet_insert_replace_compat = (
        legacy_action == "INSERT"
        and actual_kind == "replace"
        and isinstance(intent, Replace)
        and isinstance(intent.target, FacetTarget)
    )
    if expected_kind is not None and actual_kind != expected_kind and not facet_insert_replace_compat:
        intent_compat_stats.action_family += 1
        intent_compat_stats.total += 1
        _log.warning(
            "INTENT_COMPAT_MISMATCH action_family: %s — legacy action %r maps to %r but intent.kind=%r",
            ctx_label,
            legacy_action,
            expected_kind,
            actual_kind,
        )
        _record_mismatch(
            "action_family",
            {
                "legacy_action": legacy_action,
                "expected_intent_kind": expected_kind,
                "actual_intent_kind": actual_kind,
            },
        )

    # --- Check 2/3: target ---
    if isinstance(intent, (Replace, Insert, Repeal, TextPatch)):
        target = intent.target
    elif isinstance(intent, Relabel):
        target = intent.source
    elif isinstance(intent, Move):
        target = intent.source
    else:
        target = None

    if isinstance(target, NodeTarget):
        unit_kind = target.address.leaf_kind()
        expected_target_kind = _UNIT_KIND_TO_LEGACY_TARGET_KIND.get(unit_kind)
        if expected_target_kind is not None and rop.target_unit_kind != unit_kind_for_legacy_target_kind(expected_target_kind):
            intent_compat_stats.unit_kind += 1
            intent_compat_stats.total += 1
            _log.warning(
                "INTENT_COMPAT_MISMATCH unit_kind: %s — "
                "intent.target.address.leaf_kind()=%r implies target_kind=%r but rop.target_unit_kind=%r",
                ctx_label,
                unit_kind,
                expected_target_kind,
                rop.target_unit_kind,
            )
            _record_mismatch(
                "unit_kind",
                {
                    "intent_leaf_kind": unit_kind,
                    "expected_legacy_target_kind": expected_target_kind.value,
                    "rop_target_unit_kind": rop.target_unit_kind,
                },
            )

    elif isinstance(target, FacetTarget):
        facet = target.facet
        target_special = rop.effective_target_special
        expected_facet = _SPECIAL_TO_FACET.get(target_special or "")
        if expected_facet is not None and facet != expected_facet:
            intent_compat_stats.facet += 1
            intent_compat_stats.total += 1
            _log.warning(
                "INTENT_COMPAT_MISMATCH facet: %s — target_special=%r maps to %r but intent.target.facet=%r",
                ctx_label,
                target_special,
                expected_facet,
                facet,
            )
            _record_mismatch(
                "facet",
                {
                    "target_special": target_special or "",
                    "expected_facet": expected_facet.value,
                    "actual_facet": facet.value,
                },
            )
        elif target_special is not None and expected_facet is None:
            # target_special is set but not in the known mapping — advisory only.
            intent_compat_stats.facet += 1
            intent_compat_stats.total += 1
            _log.warning(
                "INTENT_COMPAT_MISMATCH facet: %s — target_special=%r is unknown; intent.target.facet=%r",
                ctx_label,
                target_special,
                facet,
            )
            _record_mismatch(
                "facet",
                {
                    "target_special": target_special,
                    "expected_facet": "",
                    "actual_facet": facet.value,
                    "unknown_target_special": True,
                },
            )
