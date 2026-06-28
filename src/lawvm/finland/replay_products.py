"""Typed replay/materialization products for the Finnish frontend."""
from __future__ import annotations

from collections import Counter
import re
from dataclasses import dataclass, replace as dc_replace
from functools import lru_cache
from typing import TYPE_CHECKING, Callable, Literal, Optional, Sequence, cast

from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    lower_lifecycle_event_to_temporal_event,
    validate_effect_graph_closure,
    validate_effect_graph_unique_ids,
)
from lawvm.core.identity_ledger import IdentityLedger
from lawvm.core.provenance import MigrationEvent
from lawvm.core.ir import IRNode, IRStatute, LegalAddress
from lawvm.core.ir_helpers import irnode_content_hash, irnode_to_text
from lawvm.core.ir import LegalOperation
from lawvm.core.ir import ProvisionTimeline
from lawvm.core.ir import ProvisionVersion
from lawvm.core.invariant_profiles import TreeInvariantProfile
from lawvm.core.invariant_profiles import collect_tree_invariant_violations
from lawvm.core.invariant_profiles import project_tree_invariant_dicts
from lawvm.core.invariant_detectors import run_label_normalization_collision_detector
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.temporal import FIXED_DATE_KIND, ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.timeline_lineage import (
    MaterializationLineageBridgeClassification,
    assert_acyclic as _assert_lineage_acyclic,
    classify_materialization_lineage_bridge,
    choose_materialization_lineage_decision,
    rekey_timelines_with_migration_events as _core_rekey_timelines_with_migration_events,
)
from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.timeline_results import (
    MaterializationCoverage,
    MaterializationLineageDecision,
    MaterializationLineagePlan,
    TimelineIssue,
    Timelines,
)
from lawvm.core.timeline_addresses import _retarget_version_content
from lawvm.core.tree_ops import (
    TreeInvariantViolation,
    _kind_str,
    check_invariants,
    default_label_sort_key,
    find_provisions_parent as _find_provisions_parent,
    insert_sorted as _insert_sorted,
    remove_at as _remove_at,
    resolve as _tops_resolve,
    resort_children as _resort_children,
)
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.finland.apply_tree_closure import assert_tree_authority_closure
from lawvm.finland.apply_ir_ops import (
    _strip_redundant_paragraph_label_prefixes_ir,
    _strip_standalone_subsection_item_prefixes_ir,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.references.cited_version import (
    CitedVersionParseResidual,
    recognize_item_cited_version_clause,
)
from lawvm.finland.post_process import _canonicalize_section_shell_order
from lawvm.finland.tree_invariant_allowances import (
    is_terminal_fi_commencement_section_violation,
)
from lawvm.finland.temporal_rewrites import reconcile_temporal_event_expiry_with_op_sources

if TYPE_CHECKING:
    from lawvm.core.stage_result import AuthoritySurface, StageResult
    from lawvm.core.write_receipt import WriteReceipt
    from lawvm.finland.replay_fold_timeline_backfill import FoldTimelineBackfillRecord
    from lawvm.finland.timeline_version_dedupe import TimelineVersionDedupeRecord
    from lawvm.finland.statute import ReplayState, StatuteContext


_FI_LABEL_NORMALIZER_NAME = "fi_label_norm_v1"
_FI_SLOT_IDENTITY_NORMALIZER_NAME = "fi_slot_identity_norm_v1"
_FI_LINEAGE_MODE_REKEYED_WITH_MIGRATIONS = "rekeyed_with_migrations"
_FI_LINEAGE_MODE_REKEYED_ONLY = "rekeyed_only"
_FI_LINEAGE_MODE_RAW_WITH_MIGRATIONS = "raw_with_migrations"
_FI_LINEAGE_REASON_DEFAULT = "default_migration_projection"
_FI_LINEAGE_REASON_NATIVE_REBIRTH = "native_rebirth_after_renumber"
_FI_LINEAGE_REASON_LEAF_STABLE_SCOPE_RENUMBER = "leaf_stable_scope_renumber"
_FI_LINEAGE_REASON_DESTINATION_OCCUPANCY = "destination_occupancy_collision"
_FI_LINEAGE_REASON_SCOPE_CHANGING_FALLBACK = "scope_changing_migration_fallback"
_FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE = "fi_sourceless_base_merge_cleanup_v1"
_FI_LABEL_TRAILING_DECORATION_RE = re.compile(r"[^a-zA-Z0-9äöå]+$")
_TIMELINE_SECTION_MARK_SPACING_RE = re.compile(r"^(\d+[a-z]?)\s*§")
_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR = (
    "lawvm_materialize_as_absent_under_detached_horizon"
)
_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_TAG = (
    "lawvm:materialize_as_absent_under_detached_horizon"
)
# Witness rule id for the residual substring path in
# ``_restore_replay_fold_repeal_placeholders``: an editorial repeal notice is
# recognised by a case-insensitive "kumottu" scan of materialized text because
# no typed marker yet exists for "the official consolidation text itself
# declares this provision repealed" (the typed ``lawvm_repeal_placeholder`` attr
# only covers replay-minted placeholders, which are checked first). Per
# leak-ledger rank 15 / AGENTS §1.11–§1.12, the substring path is no longer
# silent: each firing emits a witnessed observation reachable from ReplayProducts.
FI_EDITORIAL_REPEAL_NOTICE_SUBSTRING_RULE_ID = "fi.replay.editorial_repeal_notice_substring"
_FI_REPLAY_FOLD_MIXED_HIERARCHY_PROFILE = TreeInvariantProfile(
    surface="replay_fold_tree",
    families=("mixed_hierarchy_child",),
    profile_id="fi_product_mixed_hierarchy",
)
_FI_MATERIALIZED_MIXED_HIERARCHY_PROFILE = TreeInvariantProfile(
    surface="materialized_tree",
    families=("mixed_hierarchy_child",),
    profile_id="fi_product_mixed_hierarchy",
)


FinlandLineageBridgeClassification = MaterializationLineageBridgeClassification


@dataclass(frozen=True)
class MaterializationSpec:
    """Typed description of how PIT materialization was derived."""

    as_of: str
    query_type: Literal["governing", "in_force"] = "governing"
    label_normalizer: str = _FI_LABEL_NORMALIZER_NAME
    bridge_classification: FinlandLineageBridgeClassification = FinlandLineageBridgeClassification()
    lineage_plan: MaterializationLineagePlan = MaterializationLineagePlan(
        mode=_FI_LINEAGE_MODE_REKEYED_WITH_MIGRATIONS
    )
    lineage_reason: Literal[
        "default_migration_projection",
        "native_rebirth_after_renumber",
        "leaf_stable_scope_renumber",
        "destination_occupancy_collision",
        "scope_changing_migration_fallback",
    ] = _FI_LINEAGE_REASON_DEFAULT

    @property
    def lineage_mode(self) -> Literal[
        "rekeyed_with_migrations",
        "rekeyed_only",
        "raw_with_migrations",
    ]:
        return self.lineage_plan.mode


@dataclass
class ReplayProducts:
    """Replay artifacts after folding and PIT materialization."""

    replay_fold_state: "ReplayState"
    materialized_state: "ReplayState"
    timelines: Optional[Timelines]
    temporal_events: tuple[TemporalEvent, ...] = ()
    migration_events: tuple[MigrationEvent, ...] = ()
    source_effects: tuple[EffectRef, ...] = ()
    effect_relations: tuple[EffectRelation, ...] = ()
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...] = ()
    materialization_spec: Optional[MaterializationSpec] = None
    source_adjudication: Optional[SourceAdjudication] = None
    fold_timeline_backfills: tuple["FoldTimelineBackfillRecord", ...] = ()
    timeline_version_dedupes: tuple["TimelineVersionDedupeRecord", ...] = ()
    editorial_repeal_notice_substring_witnesses: tuple[
        "EditorialRepealNoticeSubstringWitness", ...
    ] = ()
    dropped_cited_version_snapshots: tuple["CitedVersionSnapshotDrop", ...] = ()
    cited_version_parse_residuals: tuple["CitedVersionParseResidual", ...] = ()
    materialization_issues: tuple[TimelineIssue, ...] = ()
    materialization_coverage: Optional[MaterializationCoverage] = None
    # StageResult-endgame: the full typed materialization account (coverage +
    # residuals + findings), carried so the certificate dossier routes it into a
    # per-stage account subroot instead of discarding it. ``None`` only on the
    # full-products-skipped path.
    materialization_stage: Optional["StageResult[IRStatute]"] = None
    # StageResult-endgame WAIST #7: the per-replay apply/replay execution
    # authority aggregated over every landed write (replay_authorized = AND over
    # all landed writes; one unauthorized write un-authorizes the replay). This is
    # the firewall the certificate dossier branches on — an unauthorized replay
    # cannot produce an authoritative (clean) receipt/dossier. ``None`` until the
    # apply path's receipts/findings are known (set at ReplayResult assembly,
    # where the landed receipts + findings are accumulated); the certificate
    # re-derives it descriptively from ``bundle.result`` so the writer never
    # trusts an un-set carrier.
    apply_authority: Optional["AuthoritySurface"] = None
    # StageResult-endgame WAIST #3: the per-replay STRUCTURAL (IRNode /
    # LegalAddress) write-footprint account aggregated over every landed
    # WriteReceipt of the replay. Each landed write already carries the canonical
    # ``structural_stage_result(post_ir, receipt)`` per-op account (coverage =
    # declared footprint owned, unit="paths"; one blocking ``unowned_violation``
    # residual iff ``receipt.divergence_explained is False``). This field carries
    # the UNION fold over those per-op accounts so the certificate dossier routes
    # the structural stage into a per-stage account subroot instead of re-deriving
    # it. ``None`` until the landed receipts are known (set at ReplayResult
    # assembly beside ``apply_authority``). On the green corpus every container
    # write has ``divergence_explained=True`` → empty blocking residuals → clean
    # coverage. The apply-decline VERDICT stays on the existing #3 apply ``Finding``
    # channel (``apply_structure_ops``); this carrier is the additive checkable
    # account.
    structural_stage: Optional["StageResult[IRNode]"] = None
    # StageResult-endgame WAIST #6: the per-replay CANONICAL-OPERATION compile
    # account aggregated over every amendment's ``compile_amendment_ops`` decline
    # partition. Each amendment already builds one canonical-op ``StageResult``
    # (``build_canonical_op_stage``: coverage ``unit="candidate_ops"``, owned =
    # emitted ops, violation = declined ops; one blocking ``unowned_violation``
    # residual per decline — the same account that backs the typed-residual decline
    # single-channel). This field carries the UNION fold over those per-amendment
    # accounts (captured FAITHFULLY off the producer via the
    # ``canonical_op_stages_out`` sink, NOT re-derived from the stage-tagless union
    # findings) so the certificate dossier routes the canonical-op stage into a
    # per-stage account subroot. ``None`` until the per-amendment accounts are
    # known (set at ReplayResult assembly beside ``apply_authority`` /
    # ``structural_stage``). The decline VERDICT stays on the existing #6
    # residual/finding single-channel; this carrier is the additive checkable
    # account.
    canonical_op_stage: Optional["StageResult[None]"] = None

    def __post_init__(self) -> None:
        temporal_events = tuple(self.temporal_events)
        migration_events = tuple(self.migration_events)
        source_effects = tuple(self.source_effects)
        effect_relations = tuple(self.effect_relations)
        effect_lifecycle_events = tuple(self.effect_lifecycle_events)
        if not all(isinstance(event, TemporalEvent) for event in temporal_events):
            raise TypeError("ReplayProducts.temporal_events must contain TemporalEvent records")
        if not all(isinstance(event, MigrationEvent) for event in migration_events):
            raise TypeError("ReplayProducts.migration_events must contain MigrationEvent records")
        # LS-11: the sealed migration ledger must form a DAG. A cycle (an eId
        # migrating into its own ancestry) is a non-terminating-materialization /
        # repeated-PIT-hash-drift hazard, so fail loud at ledger build rather than
        # let the address resolvers silently truncate the walk at the visited guard.
        _assert_lineage_acyclic(migration_events)
        if not all(isinstance(effect, EffectRef) for effect in source_effects):
            raise TypeError("ReplayProducts.source_effects must contain EffectRef records")
        if not all(isinstance(relation, EffectRelation) for relation in effect_relations):
            raise TypeError("ReplayProducts.effect_relations must contain EffectRelation records")
        if not all(isinstance(event, EffectLifecycleEvent) for event in effect_lifecycle_events):
            raise TypeError(
                "ReplayProducts.effect_lifecycle_events must contain EffectLifecycleEvent records"
            )
        validate_effect_graph_unique_ids(
            subject="ReplayProducts",
            source_effects=source_effects,
            effect_relations=effect_relations,
            effect_lifecycle_events=effect_lifecycle_events,
        )
        validate_effect_graph_closure(
            subject="ReplayProducts",
            source_effects=source_effects,
            effect_relations=effect_relations,
            effect_lifecycle_events=effect_lifecycle_events,
        )
        self.temporal_events = temporal_events
        self.migration_events = migration_events
        self.source_effects = source_effects
        self.effect_relations = effect_relations
        self.effect_lifecycle_events = effect_lifecycle_events
        # FW-01 / OV-01 / OV-02 (wave-2 apply-authority whole-tree closure): over
        # the finished materialized replay tree, assert no surface-origin node
        # mints replay authority and no overlay-origin node is replay-authorized
        # without a complete typed promotion witness. No-op on the FI corpus (the
        # replay tree carries no surface/overlay provenance markers); fails loud
        # the day a provider/overlay node reaches the replay tree.
        materialized_ir = getattr(self.materialized_state, "ir", None)
        if isinstance(materialized_ir, IRNode):
            assert_tree_authority_closure(materialized_ir)

    @property
    def identity_ledger(self) -> IdentityLedger:
        """Frozen read-only lineage snapshot over replay migration events."""
        return IdentityLedger.from_events(self.migration_events)


def aggregate_structural_stage(
    *,
    materialized_ir: IRNode,
    write_receipts: tuple["WriteReceipt", ...],
) -> "StageResult[IRNode]":
    """Fold the per-op structural write-footprint accounts (WAIST #3).

    Each landed ``WriteReceipt`` carries the canonical per-op structural account
    via :func:`lawvm.core.tree_ops.structural_stage_result` (coverage = declared
    footprint owned, ``unit="paths"``; one blocking ``unowned_violation`` residual
    iff ``receipt.divergence_explained is False``). This aggregates those per-op
    accounts over every landed write of the replay into the single
    ``StageResult[IRNode]`` the certificate dossier routes:

      * ``value``     — ``materialized_ir`` (the replay's materialized structural
        product tree).
      * ``coverage``  — the SUM of the per-op footprint partitions: ``owned`` =
        sum of every landed write's declared-footprint path count;
        ``violation`` = number of landed writes whose BOUND→LANDED divergence is
        unexplained (each contributes exactly one blocking residual, matching the
        per-op account); ``unit="paths"``, ``total = owned + violation`` so
        ``is_partition()`` holds.
      * ``residuals`` — the union of the per-op blocking ``unowned_violation``
        residuals (one per landed write that BOUND a target and landed elsewhere
        with no named recovery rule — the exact #3 structural mutation-boundary
        condition). EMPTY on the green corpus (every bound container write
        explains its boundary).

    BOUNDARY SEMANTICS (the #3/#7 contract, NOT a heuristic): a receipt with
    ``bound_target_path is None`` carried no resolver binding at this granularity
    (the op-level apply write — ``apply_resolved_op._collect_op_write_receipt``).
    It has NO bound→landed divergence to explain and the apply path does NOT block
    it today; the #3 structural residual fires ONLY for a bound target that
    diverged with no named rule (see ``apply_replay_authorization`` /
    ``_receipt_boundary_authorized``). Such a receipt therefore contributes its
    landed footprint as ``owned`` and emits NO residual — treating it as an
    unexplained divergence would manufacture a violation a write that legitimately
    stands today never had (a mapping error, not a finding). ``divergence_explained``
    alone is NOT the test, because for a ``bound=None`` receipt it is vacuously
    False (``None != landed_primary_path``).
      * ``evidence``  — ``EMPTY_EVIDENCE``: the per-op source anchors live on the
        receipts; the aggregate carries the partition account, not a merged
        witness bundle (the witnesses ride ``apply_authority`` / receipts).
      * ``findings``  — ``()`` (the apply layer owns the structural-divergence
        ``Finding`` verdict; this carrier is the additive checkable account).
      * ``authority`` — ``NEUTRAL_AUTHORITY`` (Pro §8 firewall; execution
        authorization rides ``apply_authority`` (#7), not this account).
    """
    from lawvm.core.stage_result import (
        EMPTY_EVIDENCE,
        NEUTRAL_AUTHORITY,
        CoverageCertificate,
        Residual,
        StageResult,
    )
    from lawvm.core.tree_ops import structural_stage_result

    owned = 0
    residuals: list[Residual] = []
    for receipt in write_receipts:
        if receipt.bound_target_path is None:
            # No resolver binding at this granularity: no bound→landed divergence
            # to account. The landed footprint is owned; emit no residual (the #3
            # apply consumer / replay-authority gate stand such a write today).
            owned += len(receipt.declared_footprint)
            continue
        per_op = structural_stage_result(materialized_ir, receipt)
        owned += per_op.coverage.owned
        residuals.extend(per_op.residuals)

    violation = len(residuals)
    coverage = CoverageCertificate(
        unit="paths",
        total=owned + violation,
        owned=owned,
        violation=violation,
        totality_claimed=True,
    )
    return StageResult(
        value=materialized_ir,
        evidence=EMPTY_EVIDENCE,
        residuals=tuple(residuals),
        findings=(),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )


def aggregate_canonical_op_stage(
    canonical_op_stages: tuple["StageResult[object]", ...],
) -> "StageResult[None]":
    """Aggregate the per-amendment canonical-op StageResult accounts (WAIST #6).

    Each amendment's ``compile_amendment_ops`` builds one canonical-op
    ``StageResult`` (``build_canonical_op_stage``: coverage ``unit="candidate_ops"``,
    ``owned`` = #emitted resolved ops, ``violation`` = #rejected/declined candidate
    ops, one blocking ``unowned_violation`` residual per decline — the exact #6
    typed-residual partition that backs the decline single-channel). This folds
    those per-amendment accounts — captured FAITHFULLY off the producer via the
    ``canonical_op_stages_out`` sink, NOT re-derived from the stage-tagless union
    findings — into the single ``StageResult`` the certificate dossier routes:

      * ``coverage``  — the SUM of the per-amendment partitions: ``owned`` = sum of
        every amendment's emitted-op count; ``violation`` = sum of every
        amendment's declined-op count; ``unit="candidate_ops"``,
        ``total = owned + violation`` so ``is_partition()`` holds.
      * ``residuals`` — the union of every per-amendment blocking/typed canonical-op
        residual (the compile declines). EMPTY only when no amendment declined a
        candidate op; the strict-rejection declines that exist on some statutes are
        the genuine canonical-op residue and ride through here unchanged.
      * ``findings``  — ``()`` (the decline VERDICT rides the existing #6
        residual/finding single-channel via
        ``reconstruct_findings_from_canonical_op_stage``; this carrier is the
        additive checkable account, it does not replace the decline channel).
      * ``value``     — ``None`` (the per-amendment resolved-op lists are not a
        single replay-level value; the account carries the partition, not a merged
        op list).
      * ``evidence``  — ``EMPTY_EVIDENCE``; ``authority`` — ``NEUTRAL_AUTHORITY``
        (compile authority is not execution authority — that rides #7).
    """
    from lawvm.core.stage_result import (
        EMPTY_EVIDENCE,
        NEUTRAL_AUTHORITY,
        CoverageCertificate,
        Residual,
        StageResult,
    )

    owned = 0
    violation = 0
    residuals: list[Residual] = []
    for stage in canonical_op_stages:
        owned += stage.coverage.owned
        violation += stage.coverage.violation
        residuals.extend(stage.residuals)
    coverage = CoverageCertificate(
        unit="candidate_ops",
        total=owned + violation,
        owned=owned,
        violation=violation,
        totality_claimed=True,
    )
    return StageResult(
        value=None,
        evidence=EMPTY_EVIDENCE,
        residuals=tuple(residuals),
        findings=(),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )


def _assert_finland_timeline_safe_ops(lo_ops_out: list[LegalOperation]) -> None:
    """Reject Finland replay ops that still depend on core tombstone quirks.

    Finland should not rely on payload-less ``replace`` semantics in
    ``compile_timelines()``. If a replay path still emits that shape, the fix
    belongs upstream in Finland replay emission, not as a replay-products shim.
    """
    for op in lo_ops_out:
        if op.action is not StructuralAction.REPLACE:
            continue
        if op.payload is not None:
            continue
        if op.op_id.startswith("snapshot_"):
            continue
        raise RuntimeError(
            "FI_TIMELINE_PAYLOADLESS_REPLACE: Finland replay emitted "
            f"payload-less replace for {op.target} (op_id={op.op_id or '<missing-op-id>'}). "
            "Emit explicit repeal semantics or a real replacement payload before "
            "timeline compilation."
        )


def fi_label_norm(label: str) -> str:
    """Normalize Finnish legacy labels for timeline materialization."""
    return _FI_LABEL_TRAILING_DECORATION_RE.sub("", label).strip() or label


def fi_slot_identity_norm(label: str) -> str:
    """Normalize Finnish labels for sibling slot-collision diagnostics."""
    return _norm_num_token(label)


def _fi_label_collision_invariant_messages(tree: IRNode, *, surface: str) -> tuple[str, ...]:
    return tuple(
        f"{surface}:{result.message}"
        for result in run_label_normalization_collision_detector(
            tree,
            fi_slot_identity_norm,
            detector=_FI_SLOT_IDENTITY_NORMALIZER_NAME,
        )
    )


def _fi_root_num_text(kind: IRNodeKind, label: str) -> str | None:
    """Return Finnish-facing NUM child text for migrated roots."""
    kind_value = str(kind)
    if kind_value == IRNodeKind.SECTION.value:
        return f"{label} §"
    if kind_value == IRNodeKind.CHAPTER.value:
        return f"{label} luku"
    return None


@dataclass(frozen=True, slots=True)
class EditorialRepealNoticeSubstringWitness:
    """Evidence for one editorial-repeal-notice recognised by raw-text substring.

    No typed marker yet owns "the materialized/oracle consolidation text itself
    declares this provision repealed". When the typed
    ``lawvm_repeal_placeholder`` attr is absent but the ``kumottu`` substring
    fires, the placeholder-restoration guard keeps the substring decision and
    records this witness so the residual surface predicate is accounted for
    (leak-ledger rank 15; AGENTS §1.11–§1.12).
    """

    kind: str
    label: str
    clause_text: str
    witness_rule_id: str = FI_EDITORIAL_REPEAL_NOTICE_SUBSTRING_RULE_ID


def _content_is_repeal_placeholder(node: IRNode) -> bool:
    return node.attrs.get("lawvm_repeal_placeholder") == "1"


def _content_is_editorial_repeal_notice(
    node: IRNode,
    *,
    witness_sink: Optional[list["EditorialRepealNoticeSubstringWitness"]] = None,
) -> bool:
    """Whether ``node`` already shows an editorial repeal notice.

    Typed-first: a replay-minted repeal placeholder (the typed
    ``lawvm_repeal_placeholder`` attr) is the authoritative marker and is
    consulted first. Only when no typed marker is present do we fall back to the
    residual ``kumottu`` substring scan of the rendered text — and that residual
    path is witnessed, never silent: when ``witness_sink`` is provided, each
    firing appends an ``EditorialRepealNoticeSubstringWitness`` so the
    surface-predicate decision is reachable from a public surface.
    """
    if _content_is_repeal_placeholder(node):
        return True
    text = irnode_to_text(node).casefold()
    if "kumottu" not in text:
        return False
    if witness_sink is not None:
        witness_sink.append(
            EditorialRepealNoticeSubstringWitness(
                kind=str(node.kind.value if hasattr(node.kind, "value") else node.kind),
                label=str(node.label or ""),
                clause_text=irnode_to_text(node)[:400],
            )
        )
    return True


def fi_product_tree_invariant_violations(
    tree: IRNode,
    profile: TreeInvariantProfile,
) -> tuple[TreeInvariantViolation, ...]:
    """Collect FI product tree invariants after FI source-shape allowances."""
    return tuple(
        violation
        for violation in collect_tree_invariant_violations(tree, profile)
        if not is_terminal_fi_commencement_section_violation(tree, violation)
    )


def fi_product_tree_invariant_messages(
    tree: IRNode,
    profile: TreeInvariantProfile,
) -> tuple[str, ...]:
    return tuple(
        f"{profile.surface}:{violation.message}"
        for violation in fi_product_tree_invariant_violations(tree, profile)
    )


def fi_product_tree_invariant_dicts(
    tree: IRNode,
    profile: TreeInvariantProfile,
) -> tuple[dict[str, object], ...]:
    return project_tree_invariant_dicts(
        fi_product_tree_invariant_violations(tree, profile),
        profile,
    )


def _fold_hcontainer_direct_sections(fold: IRNode) -> tuple[IRNode, ...]:
    """Return section nodes that live directly under the fold provisions wrapper."""
    provisions_node = _fold_provisions_node(fold)
    if provisions_node is None:
        return ()
    return tuple(
        child
        for child in provisions_node.children
        if child.kind is IRNodeKind.SECTION and child.label
    )


def _fold_provisions_node(fold: IRNode) -> IRNode | None:
    if (
        fold.kind is IRNodeKind.BODY
        and len(fold.children) == 1
        and fold.children[0].kind is IRNodeKind.HCONTAINER
        and fold.children[0].attrs.get("name") == "statuteProvisionsWrapper"
    ):
        return fold.children[0]

    provisions_parent = _find_provisions_parent(fold)
    if not provisions_parent:
        return None
    return _tops_resolve(fold, provisions_parent)


def _fold_provisions_has_hierarchical_roots(fold: IRNode) -> bool:
    provisions_node = _fold_provisions_node(fold)
    if provisions_node is None:
        return False
    return any(child.kind in {IRNodeKind.PART, IRNodeKind.CHAPTER} for child in provisions_node.children)


_FI_PROVISIONS_WRAPPER_NAME = "statuteProvisionsWrapper"
_FI_CHAPTER_SECTION_EID_RE = re.compile(r"^chp_(?P<chapter>[^_]+)__sec_")


def _ensure_body_hcontainer(ir: IRNode) -> tuple[IRNode, tuple[tuple[str, str], ...]]:
    """Return body IR with an hcontainer child and that container's path."""
    for child in ir.children:
        if child.kind is IRNodeKind.HCONTAINER:
            return ir, (("hcontainer", child.label or ""),)
    new_hcontainer = IRNode(kind=IRNodeKind.HCONTAINER, children=())
    return (
        IRNode(
            kind=ir.kind,
            label=ir.label,
            text=ir.text,
            attrs=dict(ir.attrs),
            children=ir.children + (new_hcontainer,),
        ),
        (("hcontainer", ""),),
    )


def _iter_sections(node: IRNode) -> tuple[IRNode, ...]:
    sections: list[IRNode] = []

    def _walk(current: IRNode) -> None:
        if current.kind is IRNodeKind.SECTION:
            sections.append(current)
        for child in current.children:
            _walk(child)

    _walk(node)
    return tuple(sections)


def _chapter_label_from_section_eid(node: IRNode) -> str:
    e_id = str(node.attrs.get("eId") or "")
    match = _FI_CHAPTER_SECTION_EID_RE.match(e_id)  # lawvm-regex: prefilter fixed-shape eId chapter/section locator parse (structural id, mints no legal state)
    if match is None:
        return ""
    return match.group("chapter").replace("_", " ")


def _is_materialized_provisions_wrapper_candidate(node: IRNode, replay_fold: IRNode) -> bool:
    if node.kind is not IRNodeKind.HCONTAINER:
        return False
    if node.attrs.get("name") == "attachments":
        return False
    if node.attrs.get("name") not in (None, "", _FI_PROVISIONS_WRAPPER_NAME):
        return False
    fold_labels = {
        section.label for section in _fold_hcontainer_direct_sections(replay_fold) if section.label
    }
    if not fold_labels:
        return False
    candidate_labels = {
        child.label for child in node.children if child.kind is IRNodeKind.SECTION and child.label
    }
    return bool(candidate_labels & fold_labels)


def project_materialized_provisions_wrapper(materialized: IRNode, replay_fold: IRNode) -> IRNode:
    """Project fold-owned provisions-wrapper children into materialized legal topology.

    Core PIT materialization preserves unlabeled hcontainer path shape but loses
    the Finland-local ``statuteProvisionsWrapper`` attribute.  For materialized
    products, that wrapper is only a source/editorial carrier: direct sections
    either belong directly under the body, or, when the materialized product has
    chapter shells and the section eId says ``chp_N__sec_X``, under that chapter.
    """
    if materialized.kind is not IRNodeKind.BODY:
        return materialized

    wrapper_index = next(
        (
            index
            for index, child in enumerate(materialized.children)
            if _is_materialized_provisions_wrapper_candidate(child, replay_fold)
        ),
        None,
    )
    if wrapper_index is None:
        return materialized
    wrapper = materialized.children[wrapper_index]

    has_hierarchical_roots = any(
        child.kind in {IRNodeKind.PART, IRNodeKind.CHAPTER}
        for index, child in enumerate(materialized.children)
        if index != wrapper_index
    )
    if not has_hierarchical_roots:
        rebuilt = tuple(
            grandchild
            for index, child in enumerate(materialized.children)
            for grandchild in ((child.children) if index == wrapper_index else (child,))
        )
        return dc_replace(materialized, children=rebuilt)

    chapter_indices = {
        child.label: index
        for index, child in enumerate(materialized.children)
        if child.kind is IRNodeKind.CHAPTER and child.label
    }
    if not chapter_indices:
        return materialized

    children = list(materialized.children)
    wrapper_children: list[IRNode] = []
    moved_by_chapter: dict[str, list[IRNode]] = {}
    for child in wrapper.children:
        if child.kind is not IRNodeKind.SECTION or not child.label:
            wrapper_children.append(child)
            continue
        chapter_label = _chapter_label_from_section_eid(child)
        if not chapter_label or chapter_label not in chapter_indices:
            wrapper_children.append(child)
            continue
        moved_by_chapter.setdefault(chapter_label, []).append(child)

    if not moved_by_chapter:
        return materialized

    for chapter_label, moved in moved_by_chapter.items():
        chapter_index = chapter_indices[chapter_label]
        chapter = children[chapter_index]
        existing_labels = {
            child.label
            for child in chapter.children
            if child.kind is IRNodeKind.SECTION and child.label
        }
        chapter_children = list(chapter.children)
        for moved_section in moved:
            if moved_section.label in existing_labels:
                continue
            target_key = default_label_sort_key(moved_section.label)
            insert_at = len(chapter_children)
            for index, existing in enumerate(chapter_children):
                if existing.kind is not IRNodeKind.SECTION or existing.label is None:
                    continue
                if default_label_sort_key(existing.label) > target_key:
                    insert_at = index
                    break
            chapter_children.insert(insert_at, moved_section)
            if moved_section.label is not None:
                existing_labels.add(moved_section.label)
        children[chapter_index] = dc_replace(chapter, children=tuple(chapter_children))

    if wrapper_children:
        children[wrapper_index] = dc_replace(wrapper, children=tuple(wrapper_children))
    else:
        del children[wrapper_index]
    return dc_replace(materialized, children=tuple(children))


def _split_operatives_from_attachments_wrapper(materialized: IRNode, replay_fold: IRNode) -> IRNode:
    """Move misplaced operative sections out of a direct attachments wrapper.

    Finland AKN often represents all top-level legal provisions inside an
    unlabeled ``hcontainer``.  In a malformed PIT product, core timeline
    materialization can restore fold-owned direct sections into the direct
    ``name="attachments"`` hcontainer because unlabeled hcontainer paths do not
    carry attrs.  Split only direct section children whose labels are witnessed
    by the replay fold's provisions wrapper; actual appendix children remain in
    ``attachments``.
    """
    if materialized.kind is not IRNodeKind.BODY:
        return materialized

    attachments_index = next(
        (
            index
            for index, child in enumerate(materialized.children)
            if child.kind is IRNodeKind.HCONTAINER and child.attrs.get("name") == "attachments"
        ),
        None,
    )
    if attachments_index is None:
        return materialized
    attachments = materialized.children[attachments_index]

    fold_labels = {section.label for section in _fold_hcontainer_direct_sections(replay_fold) if section.label}
    if not fold_labels:
        return materialized
    if _fold_provisions_has_hierarchical_roots(replay_fold):
        return materialized

    labels_outside_attachments = {
        node.label
        for index, sibling in enumerate(materialized.children)
        if index != attachments_index
        for node in _iter_sections(sibling)
        if node.label
    }

    moved: list[IRNode] = []
    kept: list[IRNode] = []
    for child in attachments.children:
        if (
            child.kind is IRNodeKind.SECTION
            and child.label in fold_labels
            and child.label not in labels_outside_attachments
        ):
            moved.append(child)
        else:
            kept.append(child)
    if not moved:
        return materialized

    provisions_index = next(
        (
            index
            for index, child in enumerate(materialized.children)
            if child.kind is IRNodeKind.HCONTAINER and child.attrs.get("name") == _FI_PROVISIONS_WRAPPER_NAME
        ),
        None,
    )
    if provisions_index is None:
        provisions = IRNode(
            kind=IRNodeKind.HCONTAINER,
            attrs={"name": _FI_PROVISIONS_WRAPPER_NAME},
            children=tuple(moved),
        )
    else:
        existing = materialized.children[provisions_index]
        existing_labels = {
            child.label for child in existing.children if child.kind is IRNodeKind.SECTION and child.label
        }
        provisions = dc_replace(
            existing,
            children=existing.children
            + tuple(child for child in moved if child.label not in existing_labels),
        )

    repaired_attachments = dc_replace(attachments, children=tuple(kept))
    rebuilt: list[IRNode] = []
    if provisions_index is None:
        for index, child in enumerate(materialized.children):
            if index == attachments_index:
                rebuilt.append(provisions)
                rebuilt.append(repaired_attachments)
            else:
                rebuilt.append(child)
    else:
        for index, child in enumerate(materialized.children):
            if index == provisions_index:
                rebuilt.append(provisions)
            elif index == attachments_index:
                rebuilt.append(repaired_attachments)
            else:
                rebuilt.append(child)
    return dc_replace(materialized, children=tuple(rebuilt))


def _all_section_paths(tree: IRNode, label: str) -> list[tuple[tuple[str, str], ...]]:
    """Return all section paths using the same root-relative format as ``find()``."""
    paths: list[tuple[tuple[str, str], ...]] = []

    def _walk(node: IRNode, prefix: tuple[tuple[str, str], ...]) -> None:
        for child in node.children:
            child_path = prefix + ((_kind_str(child.kind), child.label or ""),)
            if child.kind is IRNodeKind.SECTION and child.label == label:
                paths.append(child_path)
            _walk(child, child_path)

    _walk(tree, ())
    return paths


def _section_label_number(label: str) -> Optional[int]:
    digits: list[str] = []
    for char in label.strip():
        if not char.isdigit():
            break
        digits.append(char)
    if not digits:
        return None
    return int("".join(digits))


def _scoped_section_has_local_numeric_siblings(
    tree: IRNode,
    parent_path: tuple[tuple[str, str], ...],
    label: str,
) -> bool:
    target_number = _section_label_number(label)
    if target_number is None:
        return False
    parent_node = _tops_resolve(tree, parent_path)
    if parent_node is None:
        return False
    sibling_numbers = [
        number
        for child in parent_node.children
        if child.kind is IRNodeKind.SECTION
        and child.label != label
        for number in (_section_label_number(child.label or ""),)
        if number is not None
    ]
    if not sibling_numbers:
        return False
    return min(sibling_numbers) <= target_number <= max(sibling_numbers)


def _section_eid_confirms_chapter_path(
    section: IRNode,
    section_path: tuple[tuple[str, str], ...],
) -> bool:
    if len(section_path) < 2:
        return False
    parent_kind, parent_label = section_path[-2]
    if parent_kind != "chapter" or not parent_label:
        return False
    return _chapter_label_from_section_eid(section) == parent_label


def _has_repeated_scoped_section_label(
    tree: IRNode,
    section_paths: Sequence[tuple[tuple[str, str], ...]],
) -> bool:
    scoped_parent_paths = {
        section_path[:-1]
        for section_path in section_paths
        if section_path[:-1]
        and (parent := _tops_resolve(tree, section_path[:-1])) is not None
        and parent.kind is not IRNodeKind.HCONTAINER
    }
    return len(scoped_parent_paths) > 1


def _fold_has_section_at_materialized_path(
    replay_fold: IRNode,
    materialized: IRNode,
    section_path: tuple[tuple[str, str], ...],
    section_paths: Sequence[tuple[tuple[str, str], ...]],
) -> bool:
    """Return whether replay fold owns ``section_path`` through its provisions wrapper.

    Finland source statutes commonly wrap legal roots in
    ``statuteProvisionsWrapper``.  PIT materialization projects that wrapper
    away, so a scoped materialized path such as ``chapter:2/section:1`` may be
    present in the fold as ``hcontainer:/chapter:2/section:1``.  Reconciliation
    must treat that as a fold-owned scoped section, not as a misplaced direct
    wrapper section sharing the same bare label.  The fold can also contain a
    real direct section with the same label; in that collision family, keep a
    scoped materialized section only when its eId confirms the chapter path or
    the materialized product shows repeated scoped occurrences of that label.
    """
    if _tops_resolve(replay_fold, section_path) is not None:
        return True
    provisions_parent = _find_provisions_parent(replay_fold)
    if not provisions_parent:
        return False
    fold_section = _tops_resolve(replay_fold, provisions_parent + section_path)
    if fold_section is None:
        return False
    materialized_section = _tops_resolve(materialized, section_path)
    return (
        materialized_section is not None
        and _section_eid_confirms_chapter_path(materialized_section, section_path)
    ) or _has_repeated_scoped_section_label(materialized, section_paths)


def _reconcile_materialized_fold_hcontainer_sections(
    materialized: IRNode,
    replay_fold: IRNode,
) -> IRNode:
    """Restore fold-owned hcontainer-direct sections lost during PIT export.

    Timeline materialization can flatten the provisions wrapper and/or misplace
    orphan sections under inferred chapters.  When replay fold keeps a section
    as a direct child of the provisions hcontainer, export must preserve that
    editorial placement instead of hoisting it beside parts or rebinding it to
    a chapter container.
    """
    if materialized.kind is not replay_fold.kind:
        return materialized

    direct_fold_sections = _fold_hcontainer_direct_sections(replay_fold)
    if not direct_fold_sections:
        return materialized

    result = _split_operatives_from_attachments_wrapper(materialized, replay_fold)
    fold_has_hierarchical_roots = _fold_provisions_has_hierarchical_roots(replay_fold)
    synthesized_parent_paths: set[tuple[tuple[str, str], ...]] = set()
    if fold_has_hierarchical_roots:
        for fold_section in direct_fold_sections:
            label = fold_section.label or ""
            if not label:
                continue
            for section_path in _all_section_paths(result, label):
                parent_path = section_path[:-1]
                parent_node = _tops_resolve(result, parent_path) if parent_path else result
                if (
                    parent_node is not None
                    and parent_node.attrs.get("lawvm_synthesized_container") == "active_descendant"
                ):
                    synthesized_parent_paths.add(parent_path)
    allowed_synthesized_parent_paths = (
        synthesized_parent_paths if len(synthesized_parent_paths) == 1 else set()
    )
    for fold_section in direct_fold_sections:
        label = fold_section.label or ""
        section_paths = _all_section_paths(result, label)
        if not section_paths:
            continue

        hcontainer_paths: list[tuple[tuple[str, str], ...]] = []
        misplaced_paths: list[tuple[tuple[str, str], ...]] = []
        for section_path in section_paths:
            parent_path = section_path[:-1]
            parent_node = _tops_resolve(result, parent_path) if parent_path else result
            if parent_node is not None and parent_node.kind is IRNodeKind.HCONTAINER:
                hcontainer_paths.append(section_path)
            elif (
                parent_path in allowed_synthesized_parent_paths
                and parent_node is not None
                and parent_node.attrs.get("lawvm_synthesized_container") == "active_descendant"
            ):
                # Core PIT synthesis creates this ancestor because the active
                # timeline address requires it.  Treating the child as a
                # misplaced fold-wrapper section would destroy the materialized
                # legal address and move the text to the end of the body.
                continue
            elif (
                parent_path
                and parent_node is not None
                and parent_node.attrs.get("lawvm_synthesized_container") != "active_descendant"
                and (
                    _fold_has_section_at_materialized_path(
                        replay_fold, result, section_path, section_paths
                    )
                    or _scoped_section_has_local_numeric_siblings(result, parent_path, label)
                )
            ):
                # A fold-owned scoped section with the same numeric label is
                # not a misplaced fold-wrapper child.  Moving it would erase
                # its legal address (for example chapter:3a/section:4) and
                # corrupt PIT export whenever the statute also has a body-level
                # section:4.  A scoped path that is not present in replay fold
                # is still allowed when its numeric label belongs to the local
                # sibling run; this preserves real chapter sections after PIT
                # projection without keeping stray collisions such as 4a/59a.
                continue
            else:
                misplaced_paths.append(section_path)

        if not misplaced_paths:
            continue

        canonical_node = _tops_resolve(result, misplaced_paths[0])
        if canonical_node is None:
            continue

        for misplaced_path in reversed(misplaced_paths):
            result = _remove_at(result, misplaced_path)

        if hcontainer_paths:
            continue

        result, hcontainer_path = _ensure_body_hcontainer(result)
        result = _insert_sorted(result, hcontainer_path, canonical_node)

    return result


def _should_restore_repeal_placeholder(node: IRNode) -> bool:
    """Return whether a replay-only placeholder is visible in FI export.

    Repealed whole provisions stay absent in the materialized state. Subsection
    slots keep the existing dotted-text convention. Paragraph slots are restored
    only when apply marked a stale materialized item slot that was rebound by a
    named target-resolution recovery.
    """
    if not _content_is_repeal_placeholder(node):
        return False
    if node.kind is IRNodeKind.SUBSECTION:
        return True
    return node.kind is IRNodeKind.PARAGRAPH and node.attrs.get("lawvm_restore_materialized_stale_item_slot") == "1"


def _restore_replay_fold_repeal_placeholders(
    materialized: IRNode,
    replay_fold: IRNode,
    *,
    witness_sink: Optional[list["EditorialRepealNoticeSubstringWitness"]] = None,
) -> IRNode:
    """Carry replay-owned dotted-text placeholders through PIT export.

    Core materialization treats tombstones as absence. Finland's official
    consolidation export profile intentionally keeps repeal placeholders as
    visible dotted-text slots. This pass is Finland-local and copies only nodes
    that replay already marked as repeal placeholders.

    ``witness_sink`` collects ``EditorialRepealNoticeSubstringWitness`` rows for
    every node where the residual ``kumottu`` substring path (not the typed
    ``lawvm_repeal_placeholder`` attr) decided the node was already an editorial
    repeal notice, so that surface-predicate decision is no longer silent.
    """
    if materialized.kind is not replay_fold.kind or materialized.label != replay_fold.label:
        return materialized
    if _content_is_editorial_repeal_notice(materialized, witness_sink=witness_sink):
        return materialized
    if _should_restore_repeal_placeholder(replay_fold):
        return replay_fold
    if not replay_fold.children:
        return materialized

    replay_children = replay_fold.children
    if (
        materialized.kind is IRNodeKind.BODY
        and len(replay_children) == 1
        and replay_children[0].kind is IRNodeKind.HCONTAINER
        and replay_children[0].attrs.get("name") == "statuteProvisionsWrapper"
    ):
        replay_children = replay_children[0].children

    def _insert_missing_placeholder(children: list[IRNode], placeholder: IRNode) -> None:
        target_key = default_label_sort_key(placeholder.label)
        insert_at: int | None = None
        last_same_kind: int | None = None
        for index, child in enumerate(children):
            if child.kind is not placeholder.kind or child.label is None:
                continue
            last_same_kind = index
            if default_label_sort_key(child.label) > target_key:
                insert_at = index
                break
        if insert_at is None and last_same_kind is not None:
            insert_at = last_same_kind + 1
        if insert_at is None:
            children.append(placeholder)
            return
        children.insert(insert_at, placeholder)

    source_by_key: dict[tuple[IRNodeKind, str], IRNode] = {}
    for child in replay_children:
        if child.label is None:
            continue
        source_by_key.setdefault((child.kind, child.label), child)

    changed = False
    new_children: list[IRNode] = []
    existing_keys: set[tuple[IRNodeKind, str]] = set()
    for child in materialized.children:
        new_child = child
        if child.label is not None:
            key = (child.kind, child.label)
            existing_keys.add(key)
            source_child = source_by_key.get(key)
            if source_child is not None:
                new_child = _restore_replay_fold_repeal_placeholders(
                    child, source_child, witness_sink=witness_sink
                )
                changed = changed or new_child is not child
        new_children.append(new_child)

    for child in replay_children:
        if child.label is None or not _should_restore_repeal_placeholder(child):
            continue
        if child.kind is not IRNodeKind.SUBSECTION:
            continue
        key = (child.kind, child.label)
        if key in existing_keys:
            continue
        _insert_missing_placeholder(new_children, child)
        existing_keys.add(key)
        changed = True

    if not changed:
        return materialized
    return IRNode(
        kind=materialized.kind,
        label=materialized.label,
        text=materialized.text,
        attrs=dict(materialized.attrs),
        children=tuple(new_children),
    )


def _temporal_events_from_lo_ops(
    lo_ops: list[LegalOperation],
    *,
    target_statute: str,
    covered_commence_group_ids: frozenset[str] = frozenset(),
    covered_expiry_signatures: frozenset[tuple[str, str, str]] = frozenset(),
) -> tuple[TemporalEvent, ...]:
    """Project replay ops into explicit temporal authority for timeline mode.

    Finland replay still carries bounded fallback synthesis for replay-owned
    structural groups whose executable temporal authority has not yet been
    emitted earlier in the pipeline. Frontend-supplied temporal events remain
    authoritative; this shim only preserves existing replay behavior while the
    producer path finishes migrating fully onto explicit carriers.
    """
    events: list[TemporalEvent] = []
    seen_group_ids: set[str] = set()
    seen_expiry_keys: set[tuple[str, str, str]] = set()
    for op in lo_ops:
        group_id = str(op.group_id or "")
        if not group_id:
            continue
        source = op.source
        if source is None:
            continue
        effective_from = str(source.effective or "")
        if (
            effective_from
            and group_id not in seen_group_ids
            and group_id not in covered_commence_group_ids
        ):
            seen_group_ids.add(group_id)
            scope = TemporalScope(target_statute=target_statute)
            events.append(
                TemporalEvent(
                    event_id=f"fi-temporal:{group_id}:commence",
                    kind="commence",
                    scope=scope,
                    effective=effective_from,
                    source=source,
                    activation_rule=ActivationRule(
                        kind=FIXED_DATE_KIND,
                        effective_date=effective_from,
                        raw_text=str(source.raw_text or ""),
                    ),
                    group_id=group_id,
                )
            )
        expires = str(source.expires or "")
        if not expires:
            continue
        target_address = op.target
        target_key = str(target_address) if target_address is not None else ""
        expiry_key = (group_id, target_key, expires)
        if expiry_key in seen_expiry_keys:
            continue
        if expiry_key in covered_expiry_signatures:
            continue
        seen_expiry_keys.add(expiry_key)
        expire_scope = TemporalScope(
            target_statute=target_statute,
            exact_addresses=(target_address,) if target_address is not None else (),
        )
        events.append(
            TemporalEvent(
                event_id=f"fi-temporal:{group_id}:expire:{target_key or 'target'}",
                kind="expire",
                scope=expire_scope,
                expires=expires,
                source=source,
                group_id=group_id,
            )
        )
    return tuple(events)


def _merge_temporal_events(
    existing: tuple[TemporalEvent, ...],
    synthesized: tuple[TemporalEvent, ...],
) -> tuple[TemporalEvent, ...]:
    """Merge temporal events without dropping pre-existing executable carriers."""
    merged = list(existing)
    events_by_id = {event.event_id: event for event in existing}

    def _signature(event: TemporalEvent) -> tuple[object, ...]:
        if event.kind == "expire":
            exact_addresses = tuple(
                str(address)
                for address in event.scope.exact_addresses
            )
            return (
                event.kind,
                event.group_id,
                event.expires,
                exact_addresses,
            )
        return (
            event.kind,
            event.group_id,
        )

    seen = {_signature(event) for event in merged}
    for event in synthesized:
        previous = events_by_id.get(event.event_id)
        if previous is not None:
            if previous != event:
                raise ValueError(
                    "Finland replay temporal merge conflicting duplicate "
                    f"event_id: {event.event_id!r}"
                )
            continue
        signature = _signature(event)
        if signature in seen:
            continue
        merged.append(event)
        events_by_id[event.event_id] = event
        seen.add(signature)
    return tuple(merged)


def _lower_lifecycle_events_without_direct_temporal_duplicates(
    lifecycle_events: tuple[EffectLifecycleEvent, ...],
    direct_temporal_events: tuple[TemporalEvent, ...],
) -> tuple[TemporalEvent, ...]:
    """Lower lifecycle rows without replaying already-present direct carriers."""
    direct_event_ids = {event.event_id for event in direct_temporal_events}
    lowered: list[TemporalEvent] = []
    for lifecycle_event in lifecycle_events:
        embedded = lifecycle_event.temporal_event
        if embedded is not None and embedded.event_id in direct_event_ids:
            continue
        event = lower_lifecycle_event_to_temporal_event(lifecycle_event)
        if event is not None:
            lowered.append(event)
    return tuple(lowered)


def _cached_temporal_events_from_lo_ops(
    lo_ops: list[LegalOperation],
    *,
    target_statute: str,
    covered_commence_group_ids: frozenset[str],
    covered_expiry_signatures: frozenset[tuple[str, str, str]],
    cache: dict[object, object] | None,
    cache_key_seed: tuple[object, ...],
) -> tuple[TemporalEvent, ...]:
    """Return fallback temporal events, reusing per-export product cache."""
    cache_key = (
        "synthesized_temporal_events_from_lo_ops",
        *cache_key_seed,
        target_statute,
        covered_commence_group_ids,
        covered_expiry_signatures,
    )
    if cache is not None:
        cached = cache.get(cache_key)
        if isinstance(cached, tuple):
            return cast(tuple[TemporalEvent, ...], cached)
    events = _temporal_events_from_lo_ops(
        lo_ops,
        target_statute=target_statute,
        covered_commence_group_ids=covered_commence_group_ids,
        covered_expiry_signatures=covered_expiry_signatures,
    )
    if cache is not None:
        cache[cache_key] = events
    return events


def _base_enacted_date_for_products(
    *,
    ctx: "StatuteContext",
    statute_id: str,
    cache: dict[object, object] | None,
) -> str:
    """Return base enacted date for replay products without reparsing per PIT date."""
    cache_key = (
        "base_enacted_date_for_replay_products",
        statute_id,
        id(ctx.base_xml_bytes),
        len(ctx.base_xml_bytes),
    )
    if cache is not None:
        cached = cache.get(cache_key)
        if isinstance(cached, str):
            return cached
    import lxml.etree as _etree
    from lawvm.finland.metadata import _statute_issue_date as _fi_statute_issue_date

    base_tree = _etree.fromstring(ctx.base_xml_bytes)
    base_issue_date = _fi_statute_issue_date(base_tree)
    base_enacted_date = base_issue_date.isoformat() if base_issue_date is not None else ""
    if cache is not None:
        cache[cache_key] = base_enacted_date
    return base_enacted_date


def _normalize_repeal_op_sources(lo_ops: list[LegalOperation]) -> list[LegalOperation]:
    """Keep repeal placeholders/tombstones from inheriting a temporary expiry.

    Whole-section repeal semantics should remain visible after the repeal date.
    If we keep the source expiry on a tombstone-like op, PIT materialization can
    fall back to the pre-repeal permanent version once the temporary horizon
    passes. That revives text that should stay suppressed.

    This normalization is intentionally narrow: only explicit repeal ops and
    ops that already carry a repeal placeholder payload lose their source
    expiry. Other temporary amendments still keep their sunset behavior.
    """
    normalized: list[LegalOperation] = []
    for op in lo_ops:
        payload = getattr(op, "payload", None)
        is_repeal_placeholder = bool(
            payload is not None and getattr(payload, "attrs", {}).get("lawvm_repeal_placeholder") == "1"
        )
        if (
            op.source is not None
            and op.source.expires
            and (op.action is StructuralAction.REPEAL or is_repeal_placeholder)
        ):
            normalized_payload = op.payload
            if (
                normalized_payload is not None
                and is_repeal_placeholder
                and op.source.expires == op.source.effective
            ):
                normalized_payload = IRNode(
                    kind=normalized_payload.kind,
                    label=normalized_payload.label,
                    text=normalized_payload.text,
                    attrs={
                        **dict(normalized_payload.attrs),
                        _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR: "1",
                    },
                    children=normalized_payload.children,
                )
            normalized.append(
                dc_replace(
                    op,
                    payload=normalized_payload,
                    source=dc_replace(op.source, expires=""),
                )
            )
            continue
        normalized.append(op)
    return normalized


def _mark_detached_horizon_future_repeals(
    lo_ops: list[LegalOperation],
    *,
    as_of: str,
    expires_as_of: str,
) -> list[LegalOperation]:
    """Mark repeal ops that must project absence across a detached expiry horizon.

    Official-consolidation replay sometimes materializes at a future effective
    date to match an oracle-version repeal while keeping expiry checks at the
    oracle cutoff. Only those future repeal ops may cross that split; unrelated
    future repeals remain suppressed by core selection.
    """
    if not as_of or not expires_as_of or as_of <= expires_as_of:
        return lo_ops
    marked: list[LegalOperation] = []
    for op in lo_ops:
        source = op.source
        payload = op.payload
        is_repeal_placeholder = bool(
            payload is not None and payload.attrs.get("lawvm_repeal_placeholder") == "1"
        )
        target_kind = op.target.path[-1][0] if op.target.path else ""
        target_label = op.target.path[-1][1] if op.target.path else ""
        if (
            (op.action is StructuralAction.REPEAL or is_repeal_placeholder)
            and target_kind == "section"
            and target_label != "1"
            and source is not None
            and source.effective
            and expires_as_of < source.effective <= as_of
            and _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_TAG not in op.provenance_tags
        ):
            marked_payload = op.payload
            if is_repeal_placeholder and marked_payload is not None:
                marked_payload = IRNode(
                    kind=marked_payload.kind,
                    label=marked_payload.label,
                    text=marked_payload.text,
                    attrs={
                        **dict(marked_payload.attrs),
                        _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR: "1",
                    },
                    children=marked_payload.children,
                )
            marked.append(
                dc_replace(
                    op,
                    payload=marked_payload,
                    provenance_tags=(
                        *op.provenance_tags,
                        _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_TAG,
                    ),
                )
            )
            continue
        marked.append(op)
    return marked


# The item-scoped cited-version clause grammar (target window + item-word cue +
# ``sellaisena kuin`` cited-version cue + ``laissa/asetuksessa N/YYYY`` statute
# id) is owned by the references lane (``references.cited_version``). The replay
# layer no longer parses the amendment source language itself; it passes the
# text in and consumes a typed ``ItemCitedVersionClause`` result (the
# cited-statute-id sub-parse is routed to the references statute-id constructor).


def _payload_shape_counts(payload: IRNode) -> Counter[tuple[IRNodeKind, str]]:
    counts: Counter[tuple[IRNodeKind, str]] = Counter()
    stack = [payload]
    while stack:
        node = stack.pop()
        counts[(node.kind, node.label or "")] += 1
        stack.extend(reversed(node.children))
    return counts


def _cited_snapshot_materially_covers_current(
    *,
    current_payload: IRNode | None,
    cited_payload: IRNode | None,
) -> bool:
    """Return true when the cited snapshot is a broader same-target witness.

    Cited-version item clauses sometimes emit a stale ancestor snapshot from the
    later amending act.  Dropping that snapshot is only sound when the cited act's
    same-effective snapshot structurally covers the later one and contains
    materially more payload.  If the later snapshot adds content or has equivalent
    coverage, it must stay: it is the actual current amendment payload.

    Material coverage is decided by the typed IRNode payload shape counts only
    (§1.12 — the typed payload IS the owner). The prior criterion additionally
    rendered both payloads to text and required ``len(cited_text) >=
    len(current_text) + 80``; that re-derived semantic authority from a lossier
    representation, so whitespace, editorial comments, or comment-rich prose could
    bump the score without bumping typed shape (false positive) and conversely a
    broader snapshot with terse extra nodes could be missed (false negative). The
    typed criterion is:
    (a) ``cited_counts`` covers ``current_counts`` — every ``(kind, label)`` tuple
        in the current payload appears at least as many times in the cited one;
    (b) the cited payload carries strictly more shape instances than the current
        one, so the drop fires only when the cited snapshot is genuinely broader
        — not when prose merely happens to be longer.
    """
    if current_payload is None or cited_payload is None:
        return False
    current_counts = _payload_shape_counts(current_payload)
    cited_counts = _payload_shape_counts(cited_payload)
    if not all(cited_counts[key] >= count for key, count in current_counts.items()):
        return False
    return sum(cited_counts.values()) > sum(current_counts.values())


FI_CITED_VERSION_SNAPSHOT_DROP_RULE_ID = "fi.replay.cited_version_ancestor_snapshot_drop"


@dataclass(frozen=True, slots=True)
class CitedVersionSnapshotDrop:
    """Witness for a timeline op dropped as a covered cited-version ancestor snapshot.

    The later amending act emitted a stale ancestor snapshot for an item-scoped
    cited-version clause; a same-effective snapshot from the cited act structurally
    covers it. Dropping the stale op is sound, but the drop is recorded here so it
    is never a silent omission from the replay op stream.
    """

    rule_id: str
    op_id: str
    source_statute: str
    effective: str
    target_path: tuple[tuple[str, str], ...]

    def finding_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "op_id": self.op_id,
            "source_statute": self.source_statute,
            "effective": self.effective,
            "target_path": tuple(f"{kind}:{label}" for kind, label in self.target_path),
        }


@dataclass(frozen=True, slots=True)
class _CitedVersionDropResult:
    """Drop filter result plus the unparsed-cue residuals it accounted for.

    ``filtered`` is the lossless op filter (accepted + typed-rejected). ``residuals``
    records every item-cited-version clause whose ``sellaisena kuin`` cue matched
    the target but yielded no parseable cited statute id, so an unparsed cue is
    surfaced rather than silently passed through.
    """

    filtered: FilterResult[LegalOperation]
    residuals: tuple[CitedVersionParseResidual, ...]


def _drop_cited_version_item_ancestor_snapshots(
    lo_ops: list[LegalOperation],
) -> _CitedVersionDropResult:
    """Filter ancestor snapshots from item-scoped cited-version clauses.

    Returns a lossless filter (accepted ops plus a rejected record per dropped op)
    so a covered ancestor snapshot is removed from the replay stream with a typed
    receipt rather than vanishing silently, plus the typed residuals for any
    cited-version cue whose statute id did not parse.

    The item-cited-version clause grammar (target window + item-word + cited-version
    cue + cited statute id) is owned by ``references.cited_version``; this function
    consumes its typed result instead of parsing the amendment source language.
    """
    cited_parent_snapshots: dict[
        tuple[str, str, tuple[tuple[str, str], ...]], LegalOperation
    ] = {}
    for op in lo_ops:
        source = op.source
        if source is None or op.action not in {StructuralAction.REPLACE, StructuralAction.INSERT}:
            continue
        if not op.target.path or op.target.path[-1][0] not in {"section", "subsection"}:
            continue
        cited_parent_snapshots[(source.statute_id, source.effective, tuple(op.target.path))] = op

    accepted: list[LegalOperation] = []
    rejected: list[RejectedItem[LegalOperation]] = []
    residuals: list[CitedVersionParseResidual] = []
    for op in lo_ops:
        source = op.source
        raw_text = source.raw_text if source is not None else ""
        if (
            source is None
            or op.action not in {StructuralAction.REPLACE, StructuralAction.INSERT}
            or ":n" not in raw_text
            or "kohta" not in raw_text.lower()
            or not op.target.path
            or op.target.path[-1][0] not in {"section", "subsection"}
        ):
            accepted.append(op)
            continue
        target_label = op.target.path[-1][1]
        if op.target.path[-1][0] == "subsection" and len(op.target.path) >= 2:
            target_label = op.target.path[-2][1]
        clause = recognize_item_cited_version_clause(raw_text, target_label)
        if not clause.matched:
            accepted.append(op)
            continue
        if clause.residual is not None:
            # Cited-version cue without a parseable statute id: keep the op (no
            # cited snapshot to drop against) and surface the unparsed cue.
            residuals.append(clause.residual)
            accepted.append(op)
            continue
        cited_snapshots = tuple(
            cited_parent_snapshots.get((cited_id, source.effective, tuple(op.target.path)))
            for cited_id in clause.cited_statute_ids
        )
        if not any(
            cited_snapshot is not None
            and _cited_snapshot_materially_covers_current(
                current_payload=op.payload,
                cited_payload=cited_snapshot.payload,
            )
            for cited_snapshot in cited_snapshots
        ):
            accepted.append(op)
            continue
        # Covered ancestor snapshot — dropped with a typed receipt, never silently.
        rejected.append(
            RejectedItem(
                item=op,
                reason=(
                    "cited-version item clause emitted a stale ancestor snapshot "
                    "structurally covered by the cited act's same-effective snapshot"
                ),
                reason_code=FI_CITED_VERSION_SNAPSHOT_DROP_RULE_ID,
                blocking=False,
            )
        )
    return _CitedVersionDropResult(
        filtered=FilterResult(
            accepted_items=tuple(accepted), rejected_items=tuple(rejected)
        ),
        residuals=tuple(residuals),
    )


def _cited_version_snapshot_drops(
    result: FilterResult[LegalOperation],
) -> tuple[CitedVersionSnapshotDrop, ...]:
    """Project dropped cited-version snapshots into typed replay-products evidence."""
    drops: list[CitedVersionSnapshotDrop] = []
    for rejected in result.rejected_items:
        op = rejected.item
        source = op.source
        drops.append(
            CitedVersionSnapshotDrop(
                rule_id=rejected.reason_code or FI_CITED_VERSION_SNAPSHOT_DROP_RULE_ID,
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                effective=source.effective if source is not None else "",
                target_path=tuple(op.target.path),
            )
        )
    return tuple(drops)


def _drop_explicitly_repealed_source_move_events(
    timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
) -> tuple[MigrationEvent, ...]:
    """Drop ``move`` events whose source slot is already repealed.

    A section relocated into a newly created sibling chapter (for example
    ``5 luku §41`` moved under a freshly inserted ``5 a luku`` by the same
    amendment) is expressed by replay as two explicit lowered ops: a repeal of
    the section at its old chapter address and an insert of the section at the
    new chapter address. That repeal terminates the old-address timeline in a
    tombstone, so materialization correctly drops the base content there.

    The same amendment also records a ``move`` migration event for lineage. If
    that move event is allowed to rekey timelines, it relocates the entire
    old-address bucket — tombstone included — onto the destination address,
    where it collides with the destination's own insert lineage and, fatally,
    leaves the old chapter slot with no tombstone. The base content then
    survives as an orphan copy in the old chapter.

    When the old-address timeline already carries a tombstone at or before the
    move's effective date, the source slot is not live legal state available to
    move. If the tombstone is authored by the same source statute as the move,
    the relocation is fully expressed by the explicit repeal/insert ops. If an
    earlier act authored the tombstone, the later source is reusing labels for a
    new chapter, not migrating the repealed old text. In both cases, keeping the
    move event for rekey is redundant and destructive. Drop it (lineage
    consumers still see the event elsewhere). Genuine cross-parent moves with a
    live source keep their event.
    """
    if not migration_events:
        return migration_events

    def _source_repealed_by(event: MigrationEvent) -> bool:
        if event.kind != "move":
            return False
        source_timeline = timelines.get(event.from_address)
        if source_timeline is None:
            return False
        move_source_statute = (
            event.source_statute if isinstance(event.source_statute, str) else ""
        )
        if not move_source_statute:
            return False
        for version in source_timeline.versions:
            if version.content is not None or version.source is None:
                continue
            if version.source.statute_id == move_source_statute:
                return True
            if event.effective and version.effective and version.effective <= event.effective:
                return True
        return False

    filtered = tuple(
        event for event in migration_events if not _source_repealed_by(event)
    )
    return filtered if len(filtered) != len(migration_events) else migration_events


@lru_cache(maxsize=65536)
def _renumber_source_prefix_may_match_cached(
    path: TreePath,
    renumber_source_paths: frozenset[TreePath],
) -> bool:
    from lawvm.finland.migration_ledger import normalize_address_path

    normalized_path = normalize_address_path(path)
    return any(
        normalized_path[:depth] in renumber_source_paths
        for depth in range(1, len(normalized_path) + 1)
    )


def _rekey_timelines_with_migration_events(
    timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> dict["LegalAddress", ProvisionTimeline]:
    """Project Finland timelines onto migrated addresses for the requested PIT.

    Finland replay emits historical snapshots at the address valid when the
    amendment was applied. For PIT materialization, later container renumber
    waves can move those snapshots onto a different current address. Shared
    core timelines do not yet consume migration events directly, so Finland
    rekeys its replay-owned timelines here before materialization.
    """
    from lawvm.core.timeline import _address_prefix_matches
    from lawvm.finland.migration_ledger import (
        current_address_with_prefix_migrations_from_event_signatures,
        current_address_with_prefix_migrations_from_events,
    )

    migration_events = _drop_explicitly_repealed_source_move_events(
        timelines, migration_events
    )
    renumber_source_paths = frozenset(
        event.from_address.path
        for event in migration_events
        if event.kind == "renumber"
        and event.from_address.path
        and event.from_address.special is None
    )
    has_special_renumber_source = any(
        event.kind == "renumber" and event.from_address.special is not None
        for event in migration_events
    )

    def _renumber_source_prefix_may_match(address: LegalAddress) -> bool:
        if has_special_renumber_source:
            return True
        return _renumber_source_prefix_may_match_cached(
            address.path,
            renumber_source_paths,
        )

    return _core_rekey_timelines_with_migration_events(
        timelines,
        migration_events,
        as_of_date=as_of,
        current_address_with_prefix_migrations_fn=current_address_with_prefix_migrations_from_events,
        current_address_with_prefix_migration_signatures_fn=(
            current_address_with_prefix_migrations_from_event_signatures
        ),
        address_prefix_matches=_address_prefix_matches,
        renumber_source_prefix_may_match_fn=_renumber_source_prefix_may_match,
        retarget_version_content_fn=lambda version, address: _retarget_version_content(
            version,
            address,
            root_num_text_fn=_fi_root_num_text,
        ),
        merge_bucket_cleanup_fn=_cleanup_sourceless_base_merge_conflicts,
    )


def _cleanup_sourceless_base_merge_conflicts(
    versions: list[ProvisionVersion],
) -> list[ProvisionVersion]:
    """Prune replay-bucket collisions between base snapshots and newer lineage.

    This is a temporary Finland-local cleanup policy. Some rekeyed buckets can
    contain a source-less base snapshot plus later lineage versions that are
    not semantically additive. Until core owns a better non-textual rule for
    that identity/materialization family, Finland keeps the base snapshot and
    only the later versions that clearly extend beyond the base wording span.

    The rule name is stable on purpose:
    `_FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE`.
    """
    if not any(existing_version.source is None for existing_version in versions):
        return versions

    def _title_prefix_len(node: IRNode | None) -> int:
        if node is None:
            return 0
        text = irnode_to_text(node)
        prefix = text.split(" Tässä", 1)[0]
        return len(prefix)

    base_title_lengths = [
        _title_prefix_len(existing_version.content)
        for existing_version in versions
        if existing_version.source is None and existing_version.content is not None
    ]
    if not base_title_lengths:
        return versions
    base_effective = max(
        existing_version.effective
        for existing_version in versions
        if existing_version.source is None
    )
    base_title_len = max(base_title_lengths)
    cleaned = [
        existing_version
        for existing_version in versions
        if existing_version.source is None
        or (
            existing_version.content is None
            and existing_version.effective > base_effective
        )
        or (
            existing_version.content is not None
            and (
                existing_version.effective > base_effective
                or _title_prefix_len(existing_version.content) > base_title_len
            )
        )
    ]
    return _dedupe_same_source_semantic_versions(cleaned)


def _timeline_version_semantic_text_key(node: IRNode | None) -> str:
    if node is None:
        return ""
    text = " ".join(irnode_to_text(node).split())
    return _TIMELINE_SECTION_MARK_SPACING_RE.sub(r"\1 §", text)


def _dedupe_same_source_semantic_versions(
    versions: list[ProvisionVersion],
) -> list[ProvisionVersion]:
    """Collapse same-source timeline duplicates created by lineage projection.

    A whole-container replacement can emit a child snapshot while a migration
    event for the same source/effective date retargets the old child lineage to
    that same address. If the resulting texts are semantically identical, keep
    one version so PIT selection has a single source-backed state transition.
    """
    deduped: list[ProvisionVersion] = []
    index_by_key: dict[tuple[object, ...], int] = {}
    for version in versions:
        source_id = version.source.statute_id if version.source is not None else ""
        if not source_id or version.content is None:
            deduped.append(version)
            continue
        key = (
            source_id,
            version.effective,
            version.enacted,
            version.expires,
            version.variant_kind,
            tuple(version.applicability),
            _timeline_version_semantic_text_key(version.content),
        )
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(deduped)
            deduped.append(version)
            continue
        deduped[existing_index] = version
    return deduped


def _classify_finland_lineage_bridge(
    raw_timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> FinlandLineageBridgeClassification:
    from lawvm.core.timeline import _address_prefix_matches

    return classify_materialization_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of_date=as_of,
        address_prefix_matches=_address_prefix_matches,
    )


def _select_pit_lineage_inputs(
    raw_timelines: dict["LegalAddress", ProvisionTimeline],
    rekeyed_timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
    bridge_classification: FinlandLineageBridgeClassification | None = None,
) -> MaterializationLineageDecision:
    """Choose the canonical PIT lineage inputs for Finland replay products.

    Native rebirth must outrank the scope-changing migration fallback. Once a
    same-label native provision is born on the renumber date, replay products
    need the rekeyed split lineage and must stop forwarding the migration
    events into PIT materialization for that case. Otherwise the old lineage
    and the reborn native lineage compete across two authority surfaces:
    Finland's rekey shim and core migration materialization.
    """
    classification = bridge_classification or _classify_finland_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    return choose_materialization_lineage_decision(
        raw_timelines=raw_timelines,
        rekeyed_timelines=rekeyed_timelines,
        migration_events=migration_events,
        native_rebirth_after_renumber=classification.native_rebirth_after_renumber,
        leaf_stable_scope_renumber=classification.leaf_stable_scope_renumber,
        noncolliding_scope_migrations=classification.noncolliding_scope_migrations,
        destination_occupancy_collision=classification.destination_occupancy_collision,
        scope_changing_migration_fallback=classification.active_scope_changing,
        default_reason=_FI_LINEAGE_REASON_DEFAULT,
        native_rebirth_reason=_FI_LINEAGE_REASON_NATIVE_REBIRTH,
        leaf_stable_reason=_FI_LINEAGE_REASON_LEAF_STABLE_SCOPE_RENUMBER,
        destination_occupancy_reason=_FI_LINEAGE_REASON_DESTINATION_OCCUPANCY,
        scope_changing_fallback_reason=_FI_LINEAGE_REASON_SCOPE_CHANGING_FALLBACK,
    )


def _day_after_iso_for_backfill(effective: str) -> str:
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(effective) + timedelta(days=1)).isoformat()
    except ValueError:
        return effective


def _active_temporary_expiry_for_backfill_insert(
    timelines: dict["LegalAddress", ProvisionTimeline],
    target: "LegalAddress",
    effective: str,
) -> str:
    """Mirror compile_timelines' temporary-expiry inheritance for backfill inserts."""
    from lawvm.core.timeline import select_active_version, select_background_version

    timeline = timelines.get(target)
    if timeline is not None:
        previous = select_active_version(timeline, effective)
        if previous is not None:
            if previous.expires and previous.expires > _day_after_iso_for_backfill(effective):
                background = select_background_version(
                    timeline,
                    effective,
                    query_type="governing",
                    territory=None,
                )
                if background is None:
                    return previous.expires
            return ""

    current = LegalAddress(path=target.path[:-1])
    while current.path:
        timeline = timelines.get(current)
        if timeline is not None:
            previous = select_active_version(timeline, effective)
            if previous is not None and previous.expires and previous.expires > effective:
                return previous.expires
        current = LegalAddress(path=current.path[:-1])
    return ""


def _with_fold_backfill_versions(
    raw_timelines: dict["LegalAddress", ProvisionTimeline],
    backfill_ops: tuple[LegalOperation, ...],
) -> dict["LegalAddress", ProvisionTimeline]:
    """Return timelines with replay-fold backfill insert ops projected directly.

    The fold-backfill operation family is deliberately narrow: exact-section
    INSERTs with an already-stamped payload and an OperationSource effective date.
    Recompiling the entire statute operation stream for every transition date
    just to append those versions is equivalent but very expensive.
    """
    if not backfill_ops:
        return raw_timelines

    timelines = dict(raw_timelines)
    copied: set[LegalAddress] = set()
    for op in backfill_ops:
        if op.payload is None or op.source is None:
            continue
        effective = op.source.effective
        if not effective:
            continue
        target = op.target
        timeline = timelines.get(target)
        if timeline is None:
            timeline = ProvisionTimeline(address=target)
            timelines[target] = timeline
            copied.add(target)
        elif target not in copied:
            timeline = ProvisionTimeline(address=timeline.address, versions=list(timeline.versions))
            timelines[target] = timeline
            copied.add(target)

        expires = op.source.expires or _active_temporary_expiry_for_backfill_insert(
            raw_timelines,
            target,
            effective,
        )
        appended_version = ProvisionVersion(
            effective=effective,
            enacted=op.source.enacted,
            expires=expires,
            variant_kind="temporary" if expires else "permanent",
            content=op.payload,
            source=op.source,
            applicability=list(op.applicability),
            content_hash=irnode_content_hash(op.payload),
        )
        timelines[target] = dc_replace(timeline, versions=(*timeline.versions, appended_version))

    for address in copied:
        tl = timelines[address]
        timelines[address] = dc_replace(
            tl,
            versions=tuple(sorted(tl.versions, key=lambda version: (version.effective, version.enacted))),
        )
    return timelines


def build_replay_products(
    *,
    ctx: "StatuteContext",
    statute_id: str,
    replay_fold_state: "ReplayState",
    lo_ops_out: Optional[list[LegalOperation]],
    source_adjudication: Optional[SourceAdjudication] = None,
    as_of: str = "9999-12-31",
    query_type: Literal["governing", "in_force"] = "governing",
    synthesize_repeal_placeholders: bool = False,
    repeal_placeholder_normalizer: Optional[Callable[[object], object]] = None,
    build_full_products: bool = True,
    temporal_events: tuple[TemporalEvent, ...] = (),
    strict_johto_temporal: bool = True,
    migration_events: tuple[MigrationEvent, ...] = (),
    source_effects: tuple[EffectRef, ...] = (),
    effect_relations: tuple[EffectRelation, ...] = (),
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...] = (),
    expires_as_of: str = "",
    fold_backfill_preview_raw_timelines: dict["LegalAddress", ProvisionTimeline] | None = None,
    fold_backfill_preview_cache: dict[object, object] | None = None,
) -> ReplayProducts:
    """Build typed PIT materialization artifacts from a replay fold state.

    Callers must perform explicit temporal lowering before calling this
    function. Use ``lawvm.core.effect_lowering.lower_effect_intents_to_temporal_events``
    to convert parse-layer ``EffectIntent`` objects into executable
    ``TemporalEvent`` instances and pass the result as ``temporal_events``.

    Finland replay/materialization prefers explicit ``TemporalEvent`` carriers,
    but replay products still preserve a bounded fallback synthesis from
    replay-owned structural ops until the producer path is fully migrated.
    """
    direct_temporal_events = tuple(temporal_events)
    resolved_temporal_events = _merge_temporal_events(
        direct_temporal_events,
        _lower_lifecycle_events_without_direct_temporal_duplicates(
            effect_lifecycle_events,
            direct_temporal_events,
        ),
    )
    if not build_full_products:
        return ReplayProducts(
            replay_fold_state=replay_fold_state,
            materialized_state=replay_fold_state,
            timelines=None,
            temporal_events=resolved_temporal_events,
            migration_events=migration_events,
            source_effects=source_effects,
            effect_relations=effect_relations,
            effect_lifecycle_events=effect_lifecycle_events,
            materialization_spec=None,
            source_adjudication=source_adjudication,
        )

    from lawvm.core.timeline import compile_timelines, materialize_pit_ex
    from lawvm.core.timeline_results import materialization_result_to_stage_account

    base_ir = IRStatute(
        statute_id=statute_id,
        title=ctx.title,
        body=ctx.base_ir,
    )
    original_lo_ops = lo_ops_out or []
    static_prepared: tuple[
        tuple[LegalOperation, ...],
        tuple[CitedVersionSnapshotDrop, ...],
        tuple[CitedVersionParseResidual, ...],
    ] | None = None
    if fold_backfill_preview_cache is not None:
        prepared_cache_key = (
            "static_prepared_replay_product_lo_ops",
            statute_id,
            id(original_lo_ops),
            len(original_lo_ops),
        )
        cached_prepared = fold_backfill_preview_cache.get(prepared_cache_key)
        if isinstance(cached_prepared, tuple) and len(cached_prepared) == 3:
            static_prepared = cast(
                tuple[
                    tuple[LegalOperation, ...],
                    tuple[CitedVersionSnapshotDrop, ...],
                    tuple[CitedVersionParseResidual, ...],
                ],
                cached_prepared,
            )
        else:
            prepared = _normalize_repeal_op_sources(list(original_lo_ops))
            drop_result = _drop_cited_version_item_ancestor_snapshots(prepared)
            static_prepared = (
                drop_result.filtered.accepted_items,
                _cited_version_snapshot_drops(drop_result.filtered),
                drop_result.residuals,
            )
            fold_backfill_preview_cache[prepared_cache_key] = static_prepared
    if static_prepared is not None:
        lo_ops = list(static_prepared[0])
        cited_version_snapshot_drops = static_prepared[1]
        cited_version_parse_residuals = static_prepared[2]
        lo_ops = _mark_detached_horizon_future_repeals(
            lo_ops,
            as_of=as_of,
            expires_as_of=expires_as_of,
        )
    else:
        lo_ops = list(original_lo_ops)
        lo_ops = _normalize_repeal_op_sources(lo_ops)
        drop_result = _drop_cited_version_item_ancestor_snapshots(lo_ops)
        cited_version_snapshot_drops = _cited_version_snapshot_drops(drop_result.filtered)
        cited_version_parse_residuals = drop_result.residuals
        lo_ops = list(drop_result.filtered.accepted_items)
        lo_ops = _mark_detached_horizon_future_repeals(
            lo_ops,
            as_of=as_of,
            expires_as_of=expires_as_of,
        )
    covered_commence_group_ids = frozenset(
        event.group_id
        for event in resolved_temporal_events
        if event.kind == "commence"
        and event.group_id
    )
    covered_expiry_signatures = frozenset(
        (
            str(event.group_id or ""),
            str(next(iter(event.scope.exact_addresses or ()), "") or ""),
            str(event.expires or ""),
        )
        for event in resolved_temporal_events
        if event.kind == "expire"
        and event.group_id
        and event.expires
    )
    static_temporal_event_cache_key = (
        statute_id,
        id(original_lo_ops),
        len(original_lo_ops),
    )
    synthesized_temporal_events = _cached_temporal_events_from_lo_ops(
        lo_ops,
        target_statute=base_ir.statute_id,
        covered_commence_group_ids=covered_commence_group_ids,
        covered_expiry_signatures=covered_expiry_signatures,
        cache=fold_backfill_preview_cache,
        cache_key_seed=static_temporal_event_cache_key,
    )
    if synthesized_temporal_events:
        resolved_temporal_events = _merge_temporal_events(
            resolved_temporal_events,
            synthesized_temporal_events,
        )
    reconciled_temporal_events = list(resolved_temporal_events)
    reconcile_temporal_event_expiry_with_op_sources(
        reconciled_temporal_events,
        lo_ops,
        target_statute=base_ir.statute_id,
    )
    resolved_temporal_events = tuple(reconciled_temporal_events)
    # Extract the base statute's issue date (FRBR dateIssued / signature date) so
    # that compile_timelines can set the correct `enacted` date on base provisions.
    # This fixes --query-type in_force for pre-enactment as_of dates: the
    # `eligible()` check (enacted <= as_of) correctly excludes base provisions
    # when as_of < statute issue date.  The `effective` date of base provisions
    # remains "0000-00-00" so --query-type governing is completely unaffected
    # (governing only checks v.effective, not v.enacted).
    _base_enacted_date = _base_enacted_date_for_products(
        ctx=ctx,
        statute_id=statute_id,
        cache=fold_backfill_preview_cache,
    )
    from lawvm.finland.replay_fold_timeline_backfill import append_fold_timeline_backfill_ops

    preview_raw_timelines = fold_backfill_preview_raw_timelines
    if preview_raw_timelines is None and fold_backfill_preview_cache is not None:
        cache_key = (
            "fold_backfill_preview_raw_timelines",
            statute_id,
            _base_enacted_date,
            len(lo_ops),
            len(resolved_temporal_events),
        )
        cached = fold_backfill_preview_cache.get(cache_key)
        if isinstance(cached, dict):
            preview_raw_timelines = cast(dict[LegalAddress, ProvisionTimeline], cached)
        else:
            preview_raw_timelines = compile_timelines(
                base_ir,
                lo_ops,
                base_enacted_date=_base_enacted_date,
                label_norm=fi_label_norm,
                temporal_events=resolved_temporal_events,
            )
            fold_backfill_preview_cache[cache_key] = preview_raw_timelines

    fold_timeline_backfills = append_fold_timeline_backfill_ops(
        lo_ops=lo_ops,
        replay_fold_ir=replay_fold_state.ir,
        base_ir=ctx.base_ir,
        base_statute_id=statute_id,
        base_title=ctx.title,
        migration_events=migration_events,
        as_of=as_of,
        temporal_events=resolved_temporal_events,
        base_enacted_date=_base_enacted_date,
        preview_raw_timelines=preview_raw_timelines,
        preview_rekeyed_timelines_cache=fold_backfill_preview_cache,
    )
    if fold_timeline_backfills.records:
        backfill_temporal_events = _cached_temporal_events_from_lo_ops(
            lo_ops,
            target_statute=base_ir.statute_id,
            covered_commence_group_ids=covered_commence_group_ids,
            covered_expiry_signatures=covered_expiry_signatures,
            cache=fold_backfill_preview_cache,
            cache_key_seed=static_temporal_event_cache_key,
        )
        if backfill_temporal_events:
            resolved_temporal_events = _merge_temporal_events(
                resolved_temporal_events,
                backfill_temporal_events,
            )
    _assert_finland_timeline_safe_ops(lo_ops)
    if fold_timeline_backfills.records:
        raw_timelines = _with_fold_backfill_versions(
            fold_timeline_backfills.raw_timelines,
            fold_timeline_backfills.backfill_ops,
        )
        timelines = _rekey_timelines_with_migration_events(
            raw_timelines,
            migration_events,
            as_of=as_of,
        )
    else:
        raw_timelines = fold_timeline_backfills.raw_timelines
        timelines = fold_timeline_backfills.rekeyed_timelines
    from lawvm.finland.timeline_version_dedupe import (
        SemanticTextKeyCache,
        dedupe_finland_timelines,
    )

    semantic_text_cache: SemanticTextKeyCache | None = None
    if fold_backfill_preview_cache is not None:
        cache_key = ("timeline_version_dedupe_semantic_text", statute_id)
        cached = fold_backfill_preview_cache.get(cache_key)
        if isinstance(cached, dict):
            semantic_text_cache = cast(SemanticTextKeyCache, cached)
        else:
            semantic_text_cache = {}
            fold_backfill_preview_cache[cache_key] = semantic_text_cache
    timelines, timeline_version_dedupes = dedupe_finland_timelines(
        timelines,
        semantic_text_cache=semantic_text_cache,
    )
    bridge_classification = _classify_finland_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        timelines,
        migration_events,
        as_of=as_of,
        bridge_classification=bridge_classification,
    )
    materialization_result = materialize_pit_ex(
        lineage_decision.timelines,
        as_of=as_of,
        base=base_ir,
        query_type=query_type,
        label_norm=fi_label_norm,
        expires_as_of=expires_as_of,
        lineage_plan=lineage_decision.lineage_plan,
    )
    # StageResult-endgame: surface the materialization coverage account as the
    # canonical typed carrier so the certificate dossier can ROUTE it (instead of
    # the plain path discarding everything but the statute). Built from the SAME
    # MaterializationResult — no re-materialization, byte-identical value path.
    materialization_stage = materialization_result_to_stage_account(materialization_result)
    if materialization_result.materialization_status == "degraded_missing_scope":
        # Preserve the historical materialize_pit() contract: missing PIT scope is
        # a hard error, not a silently degraded materialization. The explicit
        # degradation result is now carried into ReplayProducts instead of thrown
        # away on the normal path.
        raise ValueError(
            "materialize_pit requires explicit scope when PIT selection is degraded "
            f"by missing {materialization_result.required_dimensions!r}; use "
            "materialize_pit_ex() for an explicit degradation result."
        )
    pit = materialization_result.statute
    materialized_state = replay_fold_state.with_ir(pit.body)
    materialized_state = materialized_state.with_ir(
        _strip_redundant_paragraph_label_prefixes_ir(
            _strip_standalone_subsection_item_prefixes_ir(materialized_state.ir)
        )
    )
    editorial_repeal_notice_substring_witnesses: list[
        EditorialRepealNoticeSubstringWitness
    ] = []
    if synthesize_repeal_placeholders:
        materialized_state = materialized_state.with_ir(
            _restore_replay_fold_repeal_placeholders(
                materialized_state.ir,
                replay_fold_state.ir,
                witness_sink=editorial_repeal_notice_substring_witnesses,
            )
        )
    materialized_state = materialized_state.with_ir(
        _reconcile_materialized_fold_hcontainer_sections(
            materialized_state.ir,
            replay_fold_state.ir,
        )
    )
    # Sort labeled children back into canonical order.  PIT materialization can
    # produce out-of-order siblings (e.g. paragraphs within a subsection) for
    # the same reason the replay fold can — amendment ops insert at arbitrary
    # positions and materialize_pit preserves that order.
    materialized_state = materialized_state.with_ir(
        _canonicalize_section_shell_order(_resort_children(materialized_state.ir))
    )
    if synthesize_repeal_placeholders and repeal_placeholder_normalizer is not None:
        materialized_state = materialized_state.with_ir(
            cast(IRNode, repeal_placeholder_normalizer(materialized_state.ir))
        )

    return ReplayProducts(
        replay_fold_state=replay_fold_state,
        materialized_state=materialized_state,
        timelines=timelines,
        temporal_events=resolved_temporal_events,
        migration_events=migration_events,
        source_effects=source_effects,
        effect_relations=effect_relations,
        effect_lifecycle_events=effect_lifecycle_events,
        fold_timeline_backfills=fold_timeline_backfills.records,
        timeline_version_dedupes=timeline_version_dedupes,
        editorial_repeal_notice_substring_witnesses=tuple(
            editorial_repeal_notice_substring_witnesses
        ),
        dropped_cited_version_snapshots=cited_version_snapshot_drops,
        cited_version_parse_residuals=cited_version_parse_residuals,
        materialization_issues=materialization_result.issues,
        materialization_coverage=materialization_result.certificate,
        materialization_stage=materialization_stage,
        materialization_spec=MaterializationSpec(
            as_of=as_of,
            query_type=query_type,
            label_normalizer=_FI_LABEL_NORMALIZER_NAME,
            bridge_classification=bridge_classification,
            lineage_plan=lineage_decision.lineage_plan,
            lineage_reason=cast(
                Literal[
                    "default_migration_projection",
                    "native_rebirth_after_renumber",
                    "leaf_stable_scope_renumber",
                    "destination_occupancy_collision",
                    "scope_changing_migration_fallback",
                ],
                lineage_decision.reason,
            ),
        ),
        source_adjudication=source_adjudication,
    )


def validate_replay_products(
    ctx: "StatuteContext",
    products: ReplayProducts,
    *,
    deep_materialization_check: bool = False,
) -> list[str]:
    """Return replay/materialization product invariant violations."""
    violations: list[str] = []

    if products.timelines is None and products.materialization_spec is not None:
        violations.append("materialization_spec_without_timelines")
    if products.timelines is not None and products.materialization_spec is None:
        violations.append("timelines_without_materialization_spec")

    if products.replay_fold_state.ir.kind is not IRNodeKind.BODY:
        violations.append(f"replay_fold_not_body:{products.replay_fold_state.ir.kind}")
    if products.materialized_state.ir.kind is not IRNodeKind.BODY:
        violations.append(f"materialized_not_body:{products.materialized_state.ir.kind}")

    for violation in check_invariants(products.replay_fold_state.ir):
        violations.append(f"replay_fold_tree:{violation}")
    for violation in check_invariants(products.materialized_state.ir):
        violations.append(f"materialized_tree:{violation}")
    violations.extend(
        fi_product_tree_invariant_messages(
            products.replay_fold_state.ir,
            _FI_REPLAY_FOLD_MIXED_HIERARCHY_PROFILE,
        )
    )
    violations.extend(
        fi_product_tree_invariant_messages(
            products.materialized_state.ir,
            _FI_MATERIALIZED_MIXED_HIERARCHY_PROFILE,
        )
    )
    violations.extend(
        _fi_label_collision_invariant_messages(
            products.replay_fold_state.ir,
            surface="replay_fold_tree",
        )
    )
    violations.extend(
        _fi_label_collision_invariant_messages(
            products.materialized_state.ir,
            surface="materialized_tree",
        )
    )

    # Check for temporary_unresolved versions — these represent VÄLIAIKAINEN
    # amendments with no parseable expiry date and are a product-level degradation
    # signal worth surfacing to callers.
    if products.timelines is not None:
        for tl in products.timelines.values():
            for ver in tl.versions:
                if ver.variant_kind == "temporary_unresolved":
                    violations.append("temporal_unresolved_temporary_expiry")
                    break
            else:
                continue
            break

    if deep_materialization_check and products.timelines is not None:
        from lawvm.core.timeline import materialize_pit

        base_ir = IRStatute(
            statute_id=ctx.id,
            title=ctx.title,
            body=ctx.base_ir,
        )
        spec = products.materialization_spec
        if spec is None:
            violations.append("deep_materialization_check_without_spec")
        elif spec.lineage_plan.mode == _FI_LINEAGE_MODE_RAW_WITH_MIGRATIONS:
            # Finland exposes current-address timelines after replay-owned
            # migrations are projected. Re-materializing from those already-
            # rekeyed timelines would double-apply scope-changing move
            # semantics and drift from the canonical PIT path, which instead
            # materializes from raw lineage plus explicit migration events.
            pass
        else:
            remat = materialize_pit(
                products.timelines,
                as_of=spec.as_of,
                base=base_ir,
                query_type=spec.query_type,
                label_norm=fi_label_norm,
                lineage_plan=spec.lineage_plan,
            )
            remat = dc_replace(remat, body=_resort_children(remat.body))
            lhs = irnode_to_text(remat.body)
            rhs = irnode_to_text(products.materialized_state.ir)
            if lhs != rhs:
                violations.append("materialized_state_drift_from_timelines")

    return violations


__all__ = [
    "MaterializationSpec",
    "ReplayProducts",
    "build_replay_products",
    "validate_replay_products",
    "fi_label_norm",
    "fi_slot_identity_norm",
    "_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR",
]
