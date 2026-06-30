"""First New Zealand dry-run replay surface for direct corroborated repeal.

This surface is the promotion step between candidate preflight and actual
replay. It applies preflight-approved, exact-target repeal operations to an
immutable parsed *before* source tree, produces a candidate after-tree, and
compares the candidate after-tree against the archived on-or-after XML oracle.

It deliberately stays narrow and boring:

- It consumes only preflight-approved candidate operations whose status is
  ``candidate_emitted``, whose family is ``repeal``, and which preflight already
  considers replayable (not source-change-only, not target-recovery).
- The apply kernel is a single boring mutation: convert the exact target node to
  a repealed tombstone (preserving addressability), never delete-and-forget.
- It never enables actual replay, never mutates the archive, and never claims
  canonical corpus state. ``replay_claims`` stays ``False`` everywhere.

It refuses (typed refusal, not a crash) when a target was recovered rather than
exact, when payload evidence is source-change-only, when the before/after change
window is missing, or when any other precondition from preflight is unmet.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    agreement_surface_from_residuals,
)
from lawvm.core.comparison_normalization import (
    normalize_inline_comparison_text,
    normalized_inline_contains,
    normalized_inline_occurrence_count,
)
from lawvm.core.ir import LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import StructuralAction
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.nz_oracle_normalization import classify_oracle_divergence
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateRow,
    NZEffectCandidatePreflightReport,
    build_archived_work_effect_candidate_preflight,
)
from lawvm.new_zealand.source_tree import (
    NZSourceDocument,
    NZSourceNode,
    NZStructuralReplacement,
    extract_structural_insertion,
    extract_structural_replacement,
    parse_nz_source_document,
)
from lawvm.new_zealand.version_diff import (
    NZArchivedVersion,
    NZArchivedVersionChangeWindow,
    archived_xml_version_change_window,
)


# Rule ids: agreement / refusal vocabulary for the dry-run surface.
NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID = "nz_dry_run_repeal_tombstone_matches_oracle"
NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID = "nz_dry_run_surface_not_replay_authorized"

NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID = "nz_dry_run_refused_preflight_not_ready_for_dry_run"
NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID = "nz_dry_run_refused_no_replayable_repeal_candidate"
# Family-specific "no candidate row in this family's witness surface" refusals.
# Kept as distinct diagnostics (rather than the historical reuse of the repeal
# rule id for replace/insert) so the lane that produced the receipt is named, per
# the AGENTS §1.10 distinguishability contract — a diagnostic that names the wrong
# family's witness reader tells the wrong next-step and is indistinguishable from
# a genuine repeal-lane miss. These are family-level refusals: a missing witness
# row carries no per-op identity (only the work-id family-level receipt).
NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID = "nz_dry_run_refused_no_replayable_replace_candidate"
NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID = "nz_dry_run_refused_no_replayable_insert_candidate"
NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID = "nz_dry_run_refused_target_recovered_not_exact"
NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID = "nz_dry_run_refused_source_change_only_payload"
NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID = "nz_dry_run_refused_missing_before_after_version_window"
NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID = "nz_dry_run_refused_before_xml_unreadable"
NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID = "nz_dry_run_refused_on_or_after_xml_unreadable"
NZ_DRY_RUN_REFUSED_TARGET_NOT_IN_BEFORE_RULE_ID = "nz_dry_run_refused_target_not_present_in_before_tree"
NZ_DRY_RUN_REFUSED_TARGET_NOT_SUBSTANTIVE_RULE_ID = "nz_dry_run_refused_target_not_substantive_in_before_tree"
NZ_DRY_RUN_REFUSED_TARGET_AMBIGUOUS_RULE_ID = "nz_dry_run_refused_target_path_ambiguous_in_before_tree"
NZ_DRY_RUN_REFUSED_TARGET_PATH_UNMAPPABLE_RULE_ID = "nz_dry_run_refused_target_address_path_unmappable_to_source"

# Oracle residual rule ids.
NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID = "nz_dry_run_residual_target_missing_in_oracle"
NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID = "nz_dry_run_residual_target_not_tombstone_in_oracle"
# Removal-on-repeal (definition) oracle outcomes.
NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID = "nz_dry_run_repeal_removed_node_matches_oracle"
NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID = "nz_dry_run_residual_target_not_removed_in_oracle"

# --- Text-substitution (TEXT_REPLACE) apply/oracle vocabulary. ----------------
# Agreement: the on-or-after oracle node reflects the single-occurrence
# substitution the candidate after-node produced.
NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID = "nz_dry_run_text_replace_substitution_reflected_in_oracle"
# Residual: the oracle still carries an old_text occurrence the candidate
# after-node removed (substitution NOT reflected — divergence or wrong target).
NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID = "nz_dry_run_text_replace_residual_old_text_remains_in_oracle"
# Residual: neither the substitution's new_text is present nor is the old_text
# residue consistent (another window change overwrote it / target drift).
NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID = "nz_dry_run_text_replace_residual_new_text_absent_in_oracle"
# Residual: the exact target node is missing from the on-or-after oracle.
NZ_DRY_RUN_TEXT_RESIDUAL_TARGET_MISSING_RULE_ID = "nz_dry_run_text_replace_residual_target_missing_in_oracle"

# Text-substitution refusals (typed, no mutation performed).
NZ_DRY_RUN_REFUSED_TEXT_NO_TEXT_PATCH_RULE_ID = "nz_dry_run_refused_text_replace_missing_text_patch"
NZ_DRY_RUN_REFUSED_TEXT_SCOPE_NOT_SINGLE_OCCURRENCE_RULE_ID = (
    "nz_dry_run_refused_text_replace_scope_not_single_occurrence"
)
NZ_DRY_RUN_REFUSED_TEXT_OLD_TEXT_OCCURRENCE_MISMATCH_RULE_ID = (
    "nz_dry_run_refused_text_replace_old_text_occurrence_not_single_in_before_target"
)
NZ_DRY_RUN_REFUSED_TEXT_APPLY_NO_OP_RULE_ID = "nz_dry_run_refused_text_replace_apply_left_node_unchanged"

# --- Structural whole-provision REPLACE apply/oracle vocabulary. --------------
# Agreement: the on-or-after oracle node-subtree matches the candidate
# replacement subtree (normalized text/structure).
NZ_DRY_RUN_REPLACE_AGREES_RULE_ID = "nz_dry_run_structural_replace_subtree_matches_oracle"
# Residual: the oracle target node-subtree exists but differs from the candidate
# replacement (other window change / wrong content). Never counted as agreement.
NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID = "nz_dry_run_structural_replace_residual_replacement_mismatch_in_oracle"
# Residual: the exact target node is absent from the on-or-after oracle.
NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID = "nz_dry_run_structural_replace_residual_target_missing_in_oracle"

# Structural-replace refusals (typed, no mutation performed).
NZ_DRY_RUN_REFUSED_REPLACE_TARGET_NOT_CANDIDATE_RULE_ID = "nz_dry_run_refused_structural_replace_target_address_not_candidate"
NZ_DRY_RUN_REFUSED_REPLACE_NO_AMENDING_WORK_RULE_ID = "nz_dry_run_refused_structural_replace_amending_work_unresolved"
NZ_DRY_RUN_REFUSED_REPLACE_AMENDING_XML_UNREADABLE_RULE_ID = "nz_dry_run_refused_structural_replace_amending_act_xml_unreadable"
NZ_DRY_RUN_REFUSED_REPLACE_AMENDING_HREF_NOT_FOUND_RULE_ID = "nz_dry_run_refused_structural_replace_amending_provision_href_not_found"
NZ_DRY_RUN_REFUSED_REPLACE_PAYLOAD_NOT_EXTRACTABLE_RULE_ID = "nz_dry_run_refused_structural_replace_payload_not_cleanly_extractable"
NZ_DRY_RUN_REFUSED_REPLACE_APPLY_NO_OP_RULE_ID = "nz_dry_run_refused_structural_replace_apply_left_subtree_unchanged"

# Typed not-in-scope reasons for the selected_family_replace scope.
NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPLACE_FAMILY = "not_in_scope_non_structural_replace_family"
NZ_DRY_RUN_NOT_IN_SCOPE_REPLACE_TARGET_NOT_CANDIDATE = "not_in_scope_structural_replace_target_not_candidate"
NZ_DRY_RUN_NOT_IN_SCOPE_REPLACE_AMENDING_UNRESOLVED = "not_in_scope_structural_replace_amending_unresolved"
NZ_DRY_RUN_NOT_IN_SCOPE_REPLACE_PAYLOAD_NOT_EXTRACTABLE = "not_in_scope_structural_replace_payload_not_extractable"

# --- Structural whole-provision INSERT apply/oracle vocabulary. ---------------
# An ``inserted`` history note records a NEW provision (whole node) added next to
# an anchor sibling. Unlike repeal/replace, the kernel ADDS a node: in the before
# tree the inserted node is ABSENT and a derived anchor sibling is PRESENT; the
# candidate after-tree carries the new node immediately after (or before) the
# anchor; the on-or-after oracle should carry the new node at the expected
# position with matching content.
# Agreement: the on-or-after oracle carries the inserted node-subtree with content
# matching the candidate new-node subtree (normalized text/structure).
NZ_DRY_RUN_INSERT_AGREES_RULE_ID = "nz_dry_run_structural_insert_new_node_present_and_matches_oracle"
# Residual: the inserted node is absent from the oracle (insertion not reflected).
NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID = "nz_dry_run_structural_insert_residual_new_node_not_present_in_oracle"
# Residual: the inserted node is present in the oracle but its content differs
# from the candidate new-node payload. Never counted as agreement.
NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID = (
    "nz_dry_run_structural_insert_residual_new_node_content_mismatch_in_oracle"
)
# Residual: the inserted node is present in the oracle with matching content, but
# its immediately-preceding same-kind sibling in the oracle is NOT the anchor we
# derived (the new node landed in a different position than the derived anchor
# claims). This catches a derived anchor that is content-correct but
# position-wrong — e.g. a block insert where every member would otherwise anchor
# on the same single predecessor. Never counted as agreement.
NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID = (
    "nz_dry_run_structural_insert_residual_new_node_position_mismatch_in_oracle"
)

# Structural-insert refusals (typed, no mutation performed).
NZ_DRY_RUN_REFUSED_INSERT_TARGET_NOT_CANDIDATE_RULE_ID = "nz_dry_run_refused_structural_insert_target_address_not_candidate"
NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID = "nz_dry_run_refused_structural_insert_anchor_not_derivable_from_inserted_label"
NZ_DRY_RUN_REFUSED_INSERT_NO_AMENDING_WORK_RULE_ID = "nz_dry_run_refused_structural_insert_amending_work_unresolved"
NZ_DRY_RUN_REFUSED_INSERT_AMENDING_XML_UNREADABLE_RULE_ID = "nz_dry_run_refused_structural_insert_amending_act_xml_unreadable"
NZ_DRY_RUN_REFUSED_INSERT_AMENDING_HREF_NOT_FOUND_RULE_ID = "nz_dry_run_refused_structural_insert_amending_provision_href_not_found"
NZ_DRY_RUN_REFUSED_INSERT_PAYLOAD_NOT_EXTRACTABLE_RULE_ID = "nz_dry_run_refused_structural_insert_payload_not_cleanly_extractable"
NZ_DRY_RUN_REFUSED_INSERT_TARGET_ALREADY_IN_BEFORE_RULE_ID = "nz_dry_run_refused_structural_insert_new_node_already_present_in_before_tree"
NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_IN_BEFORE_RULE_ID = "nz_dry_run_refused_structural_insert_anchor_not_present_in_before_tree"
NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_AMBIGUOUS_RULE_ID = "nz_dry_run_refused_structural_insert_anchor_path_ambiguous_in_before_tree"
# Nested insert (a new subsection/paragraph/definition WITHIN an existing
# provision): the inserted node's address has more than one segment, so the
# anchor + position are derived among the leaf's siblings under the resolved
# parent rather than at the top level.
NZ_DRY_RUN_REFUSED_INSERT_PARENT_NOT_IN_BEFORE_RULE_ID = "nz_dry_run_refused_structural_insert_nested_parent_not_present_in_before_tree"
NZ_DRY_RUN_REFUSED_INSERT_PARENT_AMBIGUOUS_RULE_ID = "nz_dry_run_refused_structural_insert_nested_parent_path_ambiguous_in_before_tree"
NZ_DRY_RUN_REFUSED_INSERT_NESTED_ANCHOR_NOT_DERIVABLE_RULE_ID = "nz_dry_run_refused_structural_insert_nested_anchor_not_derivable_from_sibling_group"

# Typed not-in-scope reasons for the selected_family_insert scope.
NZ_DRY_RUN_NOT_IN_SCOPE_NON_INSERT_FAMILY = "not_in_scope_non_structural_insert_family"
NZ_DRY_RUN_NOT_IN_SCOPE_INSERT_TARGET_NOT_CANDIDATE = "not_in_scope_structural_insert_target_not_candidate"

# History-note operation families that drive the structural-insert kernel.
# ``added`` behaves identically to ``inserted`` (a whole new provision added next
# to a sibling); both are covered by the insert kernel.
_NZ_INSERT_OPERATION_FAMILIES = frozenset({"inserted", "added"})

# History-note operation families that drive the structural-replace kernel: a
# whole-provision substitution is recorded as ``replaced`` or ``substituted``.
_NZ_REPLACE_OPERATION_FAMILIES = frozenset({"replaced", "substituted"})

# Dry-run scopes.
#
# ``complete_set`` is the original, strict behavior: refuse the whole work
# unless its full candidate set reached ``ready_for_dry_run_replay``. This is
# the default and its semantics must never change.
#
# ``selected_family_repeal`` is the partial-scope mode: it dry-runs the ready
# repeal operations in a work EVEN WHEN the work's full candidate set is
# incomplete. It relaxes only the WHOLE-WORK readiness gate; it never relaxes
# any per-operation exactness/corroboration check (those still refuse, typed).
# The report declares the partial scope explicitly and carries the count of
# operation witnesses NOT covered, typed by reason — never hidden.
#
# ``selected_family_text_replace`` is the same partial-scope mechanism applied
# to the single-occurrence text-substitution family: it dry-runs the ready
# TEXT_REPLACE operations in a work, applying old->new on the exact target node,
# and classifies whether the on-or-after oracle reflects the substitution. It
# relaxes only the WHOLE-WORK readiness gate; per-operation exactness/occurrence
# checks still refuse, typed.
# ``selected_family_replace`` is the same partial-scope mechanism applied to the
# WHOLE-PROVISION STRUCTURAL substitution family (history-note ``replaced`` /
# ``substituted``). It extracts the new provision body from the amending act's
# ``<amend>`` subtree (a typed structural payload, not inline text), replaces the
# exact target node's subtree with that payload, and classifies whether the
# on-or-after oracle node-subtree matches the candidate replacement (normalized).
# It relaxes only the WHOLE-WORK readiness gate; per-operation exactness checks
# (exact target, clean one-to-one payload extraction) still refuse, typed.
#
# ``selected_family_insert`` is the same partial-scope mechanism applied to the
# WHOLE-PROVISION STRUCTURAL insert family (history-note ``inserted`` / ``added``).
# It extracts the new provision body from the amending act's ``<amend>`` subtree
# (a typed structural payload, not inline text), derives an anchor sibling from
# the inserted node's suffix-letter label (e.g. ``18A`` -> after ``18``), inserts
# the new node next to that anchor, and classifies whether the on-or-after oracle
# carries the new node at the expected position with matching content. It relaxes
# only the WHOLE-WORK readiness gate; per-operation exactness checks (anchor
# derivable + unique in before, new node absent in before, clean one-node payload
# extraction) still refuse, typed.
NZ_DRY_RUN_SCOPE_COMPLETE_SET = "complete_set"
NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL = "selected_family_repeal"
NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE = "selected_family_text_replace"
NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE = "selected_family_replace"
NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT = "selected_family_insert"
_VALID_DRY_RUN_SCOPES = (
    NZ_DRY_RUN_SCOPE_COMPLETE_SET,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
)
# The selected-family scopes and the structural action they each select.
_SELECTED_FAMILY_SCOPES = (
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
)
_SCOPE_SELECTED_ACTION = {
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL: StructuralAction.REPEAL,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE: StructuralAction.TEXT_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE: StructuralAction.REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT: StructuralAction.INSERT,
}
_SCOPE_OPERATION_FAMILY = {
    NZ_DRY_RUN_SCOPE_COMPLETE_SET: "repeal",
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL: "repeal",
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE: "text_replace",
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE: "replace",
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT: "insert",
}

# Typed not-in-scope reasons for the selected_family_repeal scope. Each operation
# witness in the work that is not a dry-run-eligible repeal is carried under one
# of these reasons so the partial scope can never silently inflate coverage.
NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY = "not_in_scope_non_repeal_family"
NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_SOURCE_CHANGE_ONLY = "not_in_scope_repeal_source_change_only"
NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_TARGET_RECOVERY = "not_in_scope_repeal_target_recovery"
NZ_DRY_RUN_NOT_IN_SCOPE_CANDIDATE_OPERATION_MISSING = "not_in_scope_candidate_operation_missing"
NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS = "not_in_scope_blocked_operation_witness"
# Typed not-in-scope reasons for the selected_family_text_replace scope.
NZ_DRY_RUN_NOT_IN_SCOPE_NON_TEXT_REPLACE_FAMILY = "not_in_scope_non_text_replace_family"
# A text substitution whose selector is neither single-occurrence (occurrence 1)
# nor each-place (occurrence 0) — e.g. a specific occurrence >= 2 or a last-place
# selector. Such selectors are out of scope for this kernel.
NZ_DRY_RUN_NOT_IN_SCOPE_TEXT_REPLACE_UNSUPPORTED_SELECTOR = "not_in_scope_text_replace_unsupported_selector"

# NZ history-note family verb for a repeal operation witness. This is the
# ``operation_family`` value the readiness lowering assigns to a repeal (the
# candidate ``action`` is ``str(StructuralAction.REPEAL)`` only on emitted rows;
# blocked rows still carry this family), so it is the stable discriminator for
# the repeal-witness replay-coverage denominator.
_NZ_REPEAL_OPERATION_FAMILY = "repealed"

# A text-substitution operation witness is not discriminated by a single
# history-note verb (the family is ``amended`` plus other instruction verbs).
# It is identified by an emitted TEXT_REPLACE candidate action, or by a row
# blocked under the text-replace candidate's own blocking rule id.
_NZ_TEXT_REPLACE_BLOCKED_RULE_ID = "nz_text_replace_candidate_latest_oracle_witness_unavailable"

# Canonical tombstone marker for a repealed-but-addressable source node.
_REPEAL_TOMBSTONE_DELETION_STATUS = "repealed"

# Address-kind -> source-tree node-kind mapping. This is the inverse of the
# operation-surface source-segment mapping and is exact, not a guess.
_ADDRESS_KIND_TO_SOURCE_KIND = {
    "section": "prov",
    "subsection": "subprov",
    "paragraph": "label-para",
    "definition": "def-para",
    "part": "part",
    "schedule": "schedule",
}

# --- Auto-classified consolidation-error-candidate residual surface. ----------
#
# When a structural/text residual surfaces (oracle_match != "agrees"), the
# diverging candidate-vs-oracle node-text pairs are reconstructed and each pair
# is typed by ``classify_oracle_divergence`` (lawvm.new_zealand.
# nz_oracle_normalization). The per-node sub-families are folded into ONE
# target-level ``divergence_class``:
#
# - ``structural_nodeset``: the candidate and oracle subtree node SETS differ
#   (aligned by (relative_path, kind)); the divergence is topological, not a
#   per-node text difference.
# - ``editorial``: every diverging node is ``is_editorial`` (digit<->word, case,
#   trailing punctuation, BOM/zero-width, punctuation/whitespace). The official
#   consolidation's own normalization, not a content divergence.
# - ``substantive``: at least one diverging node survives every editorial fold;
#   a genuine content difference.
#
# This is an ADDITIONAL typed signal on residual proofs; it never changes
# ``oracle_match`` and never folds an editorial residual into "agrees" (the
# actual-replay path keeps refusing every non-"agrees" op).
NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET = "structural_nodeset"
NZ_DIVERGENCE_CLASS_EDITORIAL = "editorial"
NZ_DIVERGENCE_CLASS_SUBSTANTIVE = "substantive"

# A non-commensurable-whole-node residual: our single-amendment payload is being
# compared against a structural CONTAINER the oracle has independently further
# amended (a whole Part / subpart / crossheading, or a whole section the oracle
# kept amending). Such a residual is substantive by text but NOT a candidate
# oracle error — the comparison is between non-commensurable things (one op's
# payload vs a fully-consolidated multi-amendment container). It is typed out of
# the candidate set, conservatively (refuse-don't-guess): an uncertain container
# is typed non-commensurable rather than emitted as a false error candidate.
#
# Predicate (see ``_is_non_commensurable_whole_node``): the resolved target leaf
# kind is a structural container — ``part`` / ``subpart`` / ``crossheading`` /
# ``prov`` (a whole section contains its subsections/paragraphs) — OR the oracle
# subtree carries more than ``_NON_COMMENSURABLE_DESCENDANT_THRESHOLD``
# descendants (a backstop for any other kind whose subtree is far larger than a
# genuine single-amendment leaf).
#
# Threshold justification (from the credible-residual slice, NOT a magic number):
# the genuine substantive leaf residuals — the temporal/structural artifacts that
# SHOULD remain candidates — are deep sub-provision nodes with oracle descendant
# counts {0, 0, 11} (a label-para 0, a label-para 0, a subprovision 11). The
# non-commensurable container residuals have descendant counts
# {6, 18, 30, 37, 38, 239, 291, 292, 521, 715}. The container-kind gate already
# separates the two cleanly (every container is part/subpart/crossheading/prov;
# every genuine leaf is subprov/label-para). The descendant-count backstop guards
# only OTHER kinds, so it is set at 24 — strictly greater than 2x the observed
# genuine-leaf descendant ceiling (11), mirroring the classifier's own 2x
# structural ratio convention — so a non-container leaf must balloon well past any
# genuine single-amendment leaf before it is typed non-commensurable.
_NON_COMMENSURABLE_CONTAINER_KINDS = frozenset({"part", "subpart", "crossheading", "prov"})
_NON_COMMENSURABLE_DESCENDANT_THRESHOLD = 24

# Pervasiveness gate for an ALIGNED container residual. When the candidate and
# oracle subtrees align node-for-node (same label-keyed node set, INSERT family)
# and the divergence is confined to at most this many descendant leaves, the
# residual is a LOCALIZED substantive divergence (e.g. a single wrong
# cross-reference in one subsection of a freshly-inserted section) — a genuine
# candidate, NOT a non-commensurable whole-node comparison. When more than this
# many distinct descendant leaves diverge, the container was pervasively
# reworked (a later consolidation rewrote the body), so it stays
# non-commensurable. The root container node is never counted on its own: the
# source parser folds every descendant's text into the container's ``text``, so
# the root pair always "diverges" whenever any descendant does — counting it
# would double-count the localized leaf. A pure-leaf target (no descendants) is
# its own diverging unit and counts as one.
_NON_COMMENSURABLE_LOCALIZED_MAX = 2

# --- Temporal-window-fit proof. -----------------------------------------------
#
# The oracle is the earliest archived snapshot dated on-or-after the op's
# amendment date (``change_window.on_or_after``). But a snapshot composes EVERY
# amendment effective by its version date — so a substantive residual can be
# another amendment's change masquerading as an oracle error, not a genuine one.
# A consolidation-error candidate is only credible when we can prove the chosen
# snapshot reflects EXACTLY this op's amendment, no earlier and no later
# un-applied amendment intervening. When that cannot be proven the residual is
# typed OUT of the candidate set (refuse-don't-guess); the reason is recorded.
#
# Window-fit is UNPROVABLE when any of the following holds:
#
# - ``shared_window``: more than one distinct amending work has an effect date in
#   the op's version window ``(before.version_date, on_or_after.version_date]``.
#   The snapshot then composes several amendments and the single-op payload is
#   not the sole determinant of the snapshot's node — divergence is expected and
#   not an oracle error. (The amendment-date census is this work's own operation
#   witnesses; it is identity-only, never a content source.)
#
# - ``snapshot_predates_op``: the op mutated the before node (after != before),
#   yet the oracle target node is byte-identical to the before node — the op's
#   change is wholly absent from the chosen snapshot, so the snapshot predates
#   this op's effect. The "residual" is a window artifact, not a content error.
#
# - ``structural_drift``: for a PURE inline text substitution (which cannot add
#   or remove paragraphs), the oracle target subtree's structural node-set
#   diverges from the before snapshot's. A text op can never restructure the
#   node, so the extra/missing paragraphs were introduced by another amendment in
#   the snapshot — again a window artifact, not an oracle error.
#
# - ``composed_amend_provision``: for a structural replace, the amending
#   provision the payload was read from references the SAME target leaf in more
#   than one of its instruction steps — a "replaced by the following: …" step
#   followed by a further in-place substitution on the same definition/leaf. Our
#   extractor reads the single structured replacement step, so the payload is the
#   INTERMEDIATE state, not the provision's net effect; the oracle snapshot
#   reflects the net effect. We cannot prove the snapshot reflects exactly our
#   extracted step, so the residual is typed out. (Identity-only: the target's own
#   leaf label is matched against the provision's instruction text; no content is
#   sourced and the step semantics are never interpreted.)
#
# These reasons are mutually-non-exclusive; the first that holds (checked in the
# above order) is recorded. None of them ever changes ``oracle_match``: the
# actual-replay contract still refuses every non-"agrees" op. This is additive
# typed gating on the consolidation-error-candidate predicate only.
NZ_WINDOW_UNPROVABLE_SHARED_WINDOW = "shared_window"
NZ_WINDOW_UNPROVABLE_SNAPSHOT_PREDATES_OP = "snapshot_predates_op"
NZ_WINDOW_UNPROVABLE_STRUCTURAL_DRIFT = "structural_drift"
NZ_WINDOW_UNPROVABLE_COMPOSED_AMEND_PROVISION = "composed_amend_provision"

# Source-tree node kind whose repeal NZ effects by REMOVING the node from the
# consolidated text rather than leaving a repealed-but-addressable tombstone.
# When a definition (``def-para``) is repealed, the whole def-para disappears
# from the on-or-after XML; the agreeing oracle outcome is therefore an absent
# node, not a tombstone. (Ordinary provisions are tombstoned in place.)
_REMOVAL_ON_REPEAL_SOURCE_KIND = "def-para"


@dataclass(frozen=True)
class NZNodeDivergence:
    """One diverging candidate-vs-oracle node-text pair on a residual proof.

    Retained ONLY for consolidation-error candidates (the auditability packet
    exactly where a human needs it). ``relative_path`` is the node's path made
    relative to the target subtree root (label-stripped, kind-only segments) so
    it lines up with :func:`_normalized_subtree_signature`; ``kind`` is the
    source-tree node kind; ``candidate_text`` / ``oracle_text`` are the
    comparison-normalized texts that diverged; ``sub_family`` is the
    :class:`NZDivergenceSubFamily` value the per-node classifier assigned;
    ``is_editorial`` is its editorial flag.
    """

    relative_path: str
    kind: str
    candidate_text: str
    oracle_text: str
    sub_family: str
    is_editorial: bool

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "kind": self.kind,
            "candidate_text": self.candidate_text,
            "oracle_text": self.oracle_text,
            "sub_family": self.sub_family,
            "is_editorial": self.is_editorial,
        }


@dataclass(frozen=True)
class NZTargetDivergence:
    """Target-level reconstruction + classification of one residual proof.

    Produced by :func:`_classify_oracle_target_divergence` from the candidate
    replacement/insert subtree and the resolved oracle subtree at the same path.
    ``divergence_class`` is one of ``structural_nodeset`` / ``editorial`` /
    ``substantive`` (or ``None`` when the oracle target is absent, so there is no
    subtree pair to align). ``non_commensurable_whole_node`` types a substantive
    container residual out of the candidate set. ``node_pairs`` carries the
    diverging node-text pairs (the auditability packet) — populated for every
    residual, but only RETAINED on the proof for candidates (see
    :meth:`NZMutationBoundaryProof.is_consolidation_error_candidate`).
    """

    divergence_class: str | None
    sub_families: tuple[str, ...]
    non_commensurable_whole_node: bool
    oracle_descendant_count: int
    node_pairs: tuple[NZNodeDivergence, ...]


@dataclass(frozen=True)
class NZMutationBoundaryProof:
    """Per-operation mutation-boundary audit product.

    This is the point of the surface: it records exactly what node was touched,
    what its digest was before and after, and proves that siblings and parent
    were left unchanged. It also carries the oracle match partition for the one
    mutated node.
    """

    op_id: str
    action: str
    target_address: str
    selected_source_path: tuple[str, ...]
    target_xml_id: str
    target_digest_before: str
    target_digest_after: str
    operation_payload: str
    occupancy_before: str
    occupancy_after: str
    parent_source_path: tuple[str, ...]
    parent_digest_before: str
    parent_digest_after: str
    unaffected_neighbor_paths: tuple[tuple[str, ...], ...]
    unaffected_neighbor_digests_before: tuple[str, ...]
    unaffected_neighbor_digests_after: tuple[str, ...]
    neighbors_unchanged: bool
    oracle_version_id: str
    oracle_target_present: bool
    oracle_target_occupancy: str
    oracle_match: str
    oracle_match_rule_id: str
    # Text-substitution mutation evidence (empty for repeal proofs). The
    # occurrence counts make the substitution boundary auditable: how many
    # normalized old_text occurrences existed in the before target, how many
    # remain in the candidate after-node, and the parity the oracle was asked
    # to reflect.
    text_old_text: str = ""
    text_new_text: str = ""
    text_old_occurrences_before: int = 0
    text_old_occurrences_after: int = 0
    text_oracle_old_occurrences: int = 0
    text_oracle_contains_new_text: bool = False
    # Whether the substitution applies at EVERY occurrence in the target node
    # ("in each place it occurs") rather than at the single occurrence. An
    # each-place proof legitimately records ``text_old_occurrences_before > 1``;
    # the apply kernel substitutes every occurrence. False for single-occurrence
    # substitutions (and for all non-text proofs).
    text_each_place: bool = False
    # Structural whole-provision replace evidence (empty for non-replace proofs).
    # The amending source the replacement payload was read from, the descendant
    # count of the extracted replacement subtree, and the digest of the candidate
    # replacement subtree vs the oracle subtree — making the structural mutation
    # boundary auditable.
    replace_amending_work_id: str = ""
    replace_amending_provision_href: str = ""
    replace_replacement_descendant_count: int = 0
    replace_candidate_subtree_digest: str = ""
    replace_oracle_subtree_digest: str = ""
    # Structural whole-provision INSERT evidence (empty for non-insert proofs).
    # The derived anchor sibling the new node is placed next to, the direction
    # (after/before), the new node's path/digest, the candidate new-node subtree
    # digest vs the oracle subtree digest, and the anchor's before/after digests
    # (the anchor is an UNCHANGED neighbour, proved by equal digests) — making the
    # insertion boundary auditable: the new node is added, neighbours unperturbed.
    insert_anchor_source_path: tuple[str, ...] = ()
    insert_direction: str = ""
    insert_new_node_source_path: tuple[str, ...] = ()
    insert_amending_work_id: str = ""
    insert_amending_provision_href: str = ""
    insert_new_node_descendant_count: int = 0
    insert_anchor_digest_before: str = ""
    insert_anchor_digest_after: str = ""
    insert_candidate_subtree_digest: str = ""
    insert_oracle_subtree_digest: str = ""
    # The co-inserted block-member labels the dry-run anchor-position arbiter
    # admitted as oracle-confirmed position: members of the same (parent, kind)
    # block this work also inserts and absent from the before tree. Carried so
    # actual replay's slice re-confirm can apply the SAME carveout the dry-run
    # verified under — never the proof-schema without it, which would falsely
    # reject a verified block-insert (see ``NZ_DRY_RUN_INSERT_RESIDUAL_...``).
    insert_co_inserted_block_labels: frozenset[str] = frozenset()
    # --- Auto-classified residual-divergence signal (residual proofs only). ----
    # ``divergence_class`` is the target-level fold of the per-node oracle-
    # divergence classifier: ``structural_nodeset`` / ``editorial`` /
    # ``substantive`` (None for an agreeing proof, or a residual whose oracle
    # target is absent so there is no subtree pair to align). It is an ADDITIONAL
    # typed signal; it never changes ``oracle_match``.
    divergence_class: str | None = None
    # The per-node divergence sub-families (NZDivergenceSubFamily values) over the
    # diverging nodes, sorted; empty when there was no aligned text divergence.
    divergence_sub_families: tuple[str, ...] = ()
    # True when this substantive residual compares a single-amendment payload
    # against a structural container the oracle independently further amended (a
    # whole Part/subpart/crossheading/section). Such residuals are non-
    # commensurable, NOT candidate oracle errors — typed OUT of the candidate set.
    non_commensurable_whole_node: bool = False
    # True when we cannot prove the chosen oracle snapshot reflects EXACTLY this
    # op's amendment and no other (see the module-level window-fit note). An
    # unprovable window means a substantive residual may be another amendment's
    # change masquerading as an oracle error, so it is typed OUT of the candidate
    # set. ``temporal_window_unprovable_reason`` carries the proof reason
    # (``shared_window`` / ``snapshot_predates_op`` / ``structural_drift``); it is
    # non-empty exactly when ``temporal_window_unprovable`` is True.
    temporal_window_unprovable: bool = False
    temporal_window_unprovable_reason: str = ""
    # Diverging candidate-vs-oracle node-text pairs, RETAINED only for
    # consolidation-error candidates (the auditability packet). Empty for non-
    # candidate residuals and agreements so every proof is not bloated.
    divergence_node_pairs: tuple[NZNodeDivergence, ...] = ()

    @property
    def is_consolidation_error_candidate(self) -> bool:
        """Whether this residual is a probable-oracle-error candidate.

        A candidate is a residual (``oracle_match != "agrees"``) whose mutation
        boundary held (``neighbors_unchanged``), whose divergence is genuinely
        ``substantive`` (survives every editorial fold and is not a topological
        node-set difference), which is NOT a non-commensurable whole-node
        comparison, and whose temporal window is PROVABLY contemporaneous with
        this op's amendment (``not temporal_window_unprovable`` — the chosen
        oracle snapshot reflects exactly this op's amendment, not a shared-window
        neighbour, a pre-effect snapshot, or another amendment's restructuring).
        Editorial / structural-nodeset / non-commensurable / window-unprovable
        residuals are all excluded — they are not candidate consolidation errors.
        This is a typed signal for human adjudication, never a replay
        authorization.
        """

        return (
            self.oracle_match != "agrees"
            and self.neighbors_unchanged
            and self.divergence_class == NZ_DIVERGENCE_CLASS_SUBSTANTIVE
            and not self.non_commensurable_whole_node
            and not self.temporal_window_unprovable
        )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "action": self.action,
            "target_address": self.target_address,
            "selected_source_path": list(self.selected_source_path),
            "target_xml_id": self.target_xml_id,
            "target_digest_before": self.target_digest_before,
            "target_digest_after": self.target_digest_after,
            "operation_payload": self.operation_payload,
            "occupancy_before": self.occupancy_before,
            "occupancy_after": self.occupancy_after,
            "parent_source_path": list(self.parent_source_path),
            "parent_digest_before": self.parent_digest_before,
            "parent_digest_after": self.parent_digest_after,
            "unaffected_neighbor_paths": [list(path) for path in self.unaffected_neighbor_paths],
            "unaffected_neighbor_digests_before": list(self.unaffected_neighbor_digests_before),
            "unaffected_neighbor_digests_after": list(self.unaffected_neighbor_digests_after),
            "neighbors_unchanged": self.neighbors_unchanged,
            "oracle_version_id": self.oracle_version_id,
            "oracle_target_present": self.oracle_target_present,
            "oracle_target_occupancy": self.oracle_target_occupancy,
            "oracle_match": self.oracle_match,
            "oracle_match_rule_id": self.oracle_match_rule_id,
            "text_old_text": self.text_old_text,
            "text_new_text": self.text_new_text,
            "text_old_occurrences_before": self.text_old_occurrences_before,
            "text_old_occurrences_after": self.text_old_occurrences_after,
            "text_oracle_old_occurrences": self.text_oracle_old_occurrences,
            "text_oracle_contains_new_text": self.text_oracle_contains_new_text,
            "text_each_place": self.text_each_place,
            "replace_amending_work_id": self.replace_amending_work_id,
            "replace_amending_provision_href": self.replace_amending_provision_href,
            "replace_replacement_descendant_count": self.replace_replacement_descendant_count,
            "replace_candidate_subtree_digest": self.replace_candidate_subtree_digest,
            "replace_oracle_subtree_digest": self.replace_oracle_subtree_digest,
            "insert_anchor_source_path": list(self.insert_anchor_source_path),
            "insert_direction": self.insert_direction,
            "insert_new_node_source_path": list(self.insert_new_node_source_path),
            "insert_amending_work_id": self.insert_amending_work_id,
            "insert_amending_provision_href": self.insert_amending_provision_href,
            "insert_new_node_descendant_count": self.insert_new_node_descendant_count,
            "insert_anchor_digest_before": self.insert_anchor_digest_before,
            "insert_anchor_digest_after": self.insert_anchor_digest_after,
            "insert_candidate_subtree_digest": self.insert_candidate_subtree_digest,
            "insert_oracle_subtree_digest": self.insert_oracle_subtree_digest,
            "insert_co_inserted_block_labels": sorted(self.insert_co_inserted_block_labels),
            "divergence_class": self.divergence_class,
            "divergence_sub_families": list(self.divergence_sub_families),
            "non_commensurable_whole_node": self.non_commensurable_whole_node,
            "temporal_window_unprovable": self.temporal_window_unprovable,
            "temporal_window_unprovable_reason": self.temporal_window_unprovable_reason,
            "is_consolidation_error_candidate": self.is_consolidation_error_candidate,
            "divergence_node_pairs": [pair.to_jsonable() for pair in self.divergence_node_pairs],
        }


@dataclass(frozen=True)
class NZDryRunRefusal:
    """A typed refusal for one operation (no mutation performed)."""

    op_id: str
    rule_id: str
    message: str
    target_address: str = ""
    amendment_date_iso: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "rule_id": self.rule_id,
            "message": self.message,
            "target_address": self.target_address,
            "amendment_date_iso": self.amendment_date_iso,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class NZDryRunScopeCompleteness:
    """Honest declaration of how much of a work this dry-run report covers.

    In ``complete_set`` scope the report only runs when the work's full
    candidate set is ready, so the scope is the whole work. In
    ``selected_family_repeal`` scope only the ready repeal operations are
    dry-run while the work's other operation witnesses are explicitly carried
    here as typed not-in-scope counts. The scope is partial whenever any
    operation witness is left uncovered; this surface never hides that.
    """

    scope: str
    family: str
    total_operation_witnesses: int
    in_scope_operation_witnesses: int
    not_in_scope_operation_witnesses: int
    not_in_scope_reason_counts: Mapping[str, int] = field(default_factory=dict)
    # Repeal-family witness census. ``total_repeal_operation_witnesses`` is the
    # denominator of the family replay-coverage loop metric: every operation
    # witness in the work whose family is repeal, whether dry-run-eligible,
    # not-in-scope (source-change-only / target-recovery), or still blocked.
    total_repeal_operation_witnesses: int = 0
    repeal_witnesses_in_scope: int = 0
    repeal_witnesses_not_in_scope_reason_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def is_partial(self) -> bool:
        return self.not_in_scope_operation_witnesses > 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "family": self.family,
            "is_partial": self.is_partial,
            "total_operation_witnesses": self.total_operation_witnesses,
            "in_scope_operation_witnesses": self.in_scope_operation_witnesses,
            "not_in_scope_operation_witnesses": self.not_in_scope_operation_witnesses,
            "not_in_scope_reason_counts": dict(sorted(self.not_in_scope_reason_counts.items())),
            "total_repeal_operation_witnesses": self.total_repeal_operation_witnesses,
            "repeal_witnesses_in_scope": self.repeal_witnesses_in_scope,
            "repeal_witnesses_not_in_scope_reason_counts": dict(
                sorted(self.repeal_witnesses_not_in_scope_reason_counts.items())
            ),
        }


@dataclass(frozen=True)
class NZDryRunReport:
    """Typed dry-run replay report for direct corroborated repeal.

    Dry-run agreement is reported separately from any actual-replay agreement;
    actual replay is never performed by this surface.
    """

    work_id: str
    operation_family: str
    proofs: tuple[NZMutationBoundaryProof, ...]
    refusals: tuple[NZDryRunRefusal, ...]
    preflight_status: str
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET
    scope_completeness: NZDryRunScopeCompleteness | None = None

    def matched_proofs(self) -> tuple[NZMutationBoundaryProof, ...]:
        return tuple(proof for proof in self.proofs if proof.oracle_match == "agrees")

    def residual_proofs(self) -> tuple[NZMutationBoundaryProof, ...]:
        return tuple(proof for proof in self.proofs if proof.oracle_match != "agrees")

    def consolidation_error_candidates(self) -> tuple[NZMutationBoundaryProof, ...]:
        """Residual proofs that are probable-oracle-error candidates.

        This is the corpus-wide entrypoint: a candidate is a residual whose
        mutation boundary held, whose divergence is genuinely substantive, and
        which is not a non-commensurable whole-node comparison
        (:meth:`NZMutationBoundaryProof.is_consolidation_error_candidate`). Each
        candidate retains its diverging candidate-vs-oracle node-text pairs for
        human adjudication. A candidate is NEVER a replay authorization.
        """

        return tuple(proof for proof in self.proofs if proof.is_consolidation_error_candidate)

    def summary(self) -> dict[str, Any]:
        matched = self.matched_proofs()
        residual = self.residual_proofs()
        return {
            "work_id": self.work_id,
            "operation_family": self.operation_family,
            "scope": self.scope,
            "scope_completeness": self.scope_completeness.to_jsonable() if self.scope_completeness else None,
            "preflight_status": self.preflight_status,
            "operations_dry_run": len(self.proofs),
            "operations_refused": len(self.refusals),
            "dry_run_oracle_agreements": len(matched),
            "dry_run_oracle_residuals": len(residual),
            "neighbors_unchanged_all": all(proof.neighbors_unchanged for proof in self.proofs),
            "refusal_rule_counts": _counts(refusal.rule_id for refusal in self.refusals),
            "oracle_match_counts": _counts(proof.oracle_match for proof in self.proofs),
            # Dry-run agreement only. Actual replay is never claimed here.
            "replay_claims": False,
            "actual_replay_agreements": 0,
            "dry_run_claims": True,
        }

    def agreement_surface(self) -> dict[str, Any]:
        """Project the oracle partition into a typed agreement surface.

        Reuses :mod:`lawvm.core.agreement_residual`. Dry-run agreements and
        residuals are classified there; this never authorizes replay.
        """

        if self.scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE:
            surface_name = "nz_dry_run_text_replace"
        elif self.scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE:
            surface_name = "nz_dry_run_structural_replace"
        elif self.scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT:
            surface_name = "nz_dry_run_structural_insert"
        else:
            surface_name = "nz_dry_run_repeal"
        residuals: list[AgreementResidual] = []
        for proof in self.proofs:
            if proof.oracle_match == "agrees":
                residuals.append(
                    AgreementResidual(
                        residual_id=f"{self.work_id}:{proof.op_id}:agrees",
                        jurisdiction="nz",
                        agreement_surface=surface_name,
                        family="agreement",
                        agreement_residual_status="agrees",
                        owner_phase="dry_run",
                        rule_id=proof.oracle_match_rule_id,
                        source_artifact_id=proof.op_id,
                        replay_count=1,
                        oracle_count=1,
                        safe_default="classify_dry_run_agreement_without_authorizing_replay",
                        forbidden_shortcuts=(
                            "dry_run_agreement_as_replay_authorization",
                            "oracle_tombstone_as_source_truth",
                        ),
                        detail={"target_address": proof.target_address},
                    )
                )
            else:
                residuals.append(
                    AgreementResidual(
                        residual_id=f"{self.work_id}:{proof.op_id}:residual",
                        jurisdiction="nz",
                        agreement_surface=surface_name,
                        family=_residual_family(proof.oracle_match),
                        agreement_residual_status="residual",
                        owner_phase="dry_run",
                        rule_id=proof.oracle_match_rule_id,
                        source_artifact_id=proof.op_id,
                        replay_count=1,
                        oracle_count=1 if proof.oracle_target_present else 0,
                        safe_default="keep_dry_run_residual_visible_without_authorizing_replay",
                        forbidden_shortcuts=(
                            "dry_run_residual_as_replay_bug",
                            "oracle_score_as_source_truth",
                        ),
                        detail={
                            "target_address": proof.target_address,
                            "oracle_target_occupancy": proof.oracle_target_occupancy,
                        },
                    )
                )
        surface = agreement_surface_from_residuals(
            tuple(residuals),
            jurisdiction="nz",
            agreement_surface=surface_name,
            materialization_id=f"nz_dry_run:{self.work_id}",
            comparison_target_id=f"nz_on_or_after_oracle:{self.work_id}",
            comparison_kind="dry_run_after_tree_vs_archived_on_or_after_xml",
            materialization_kind="proposed_future_branch",
            comparison_materialization_kind="official_consolidation_view",
            exact_ratio=(len(self.matched_proofs()) / len(self.proofs)) if self.proofs else None,
        )
        return surface.to_dict()

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "dry_run_repeal_replay",
            "truth_claim": "dry_run_after_tree_vs_archived_on_or_after_xml_not_actual_replay",
            "replay_claims": False,
            "dry_run_claims": True,
            "scope": self.scope,
            "scope_completeness": self.scope_completeness.to_jsonable() if self.scope_completeness else None,
            "actual_replay_blocking_rule_id": NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        payload["mutation_boundary_proofs"] = [proof.to_jsonable() for proof in self.proofs]
        payload["refusals"] = [refusal.to_jsonable() for refusal in self.refusals]
        payload["agreement_surface"] = self.agreement_surface()
        return payload


def build_archived_work_dry_run_repeal(
    db_path: Path,
    work_id: str,
    *,
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET,
) -> NZDryRunReport:
    """Build the dry-run repeal report for one archived NZ work.

    For the structural-replace scope the candidate source is the work's operation
    surface (history-note witnesses) plus structural payload extraction from the
    cited amending act, not the repeal/text-replace candidate preflight; that
    scope is routed to its own builder so the preflight is not built for it.
    """

    if scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE:
        return build_archived_work_dry_run_replace(db_path, work_id)
    if scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT:
        return build_archived_work_dry_run_insert(db_path, work_id)

    preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
    archive = open_farchive(db_path)
    try:
        return build_dry_run_repeal(archive, work_id=work_id, preflight=preflight, scope=scope)
    finally:
        archive.close()


def build_dry_run_repeal(
    archive: Any,
    *,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET,
) -> NZDryRunReport:
    """Build a dry-run report for one archived NZ work.

    The default ``complete_set`` and ``selected_family_repeal`` scopes apply the
    repeal kernel. The ``selected_family_text_replace`` scope applies the
    single-occurrence text-substitution kernel instead. (The function keeps its
    historical name; the scope selects the family.)
    """

    if scope not in _VALID_DRY_RUN_SCOPES:
        raise ValueError(f"unknown dry-run scope {scope!r}; expected one of {_VALID_DRY_RUN_SCOPES}")

    if scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE:
        # The structural-replace scope consumes the operation surface + amending
        # act XML, not the candidate preflight; it is built by its own archived
        # entrypoint and must not be routed through this preflight-driven path.
        raise ValueError(
            "selected_family_replace is built by build_archived_work_dry_run_replace "
            "(operation-surface driven), not build_dry_run_repeal (preflight driven)"
        )

    if scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT:
        # The structural-insert scope is operation-surface + amending-act driven,
        # like replace; it is built by its own archived entrypoint.
        raise ValueError(
            "selected_family_insert is built by build_archived_work_dry_run_insert "
            "(operation-surface driven), not build_dry_run_repeal (preflight driven)"
        )

    if scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE:
        return _build_dry_run_text_replace(archive, work_id=work_id, preflight=preflight, scope=scope)

    preflight_status = str(preflight.summary()["preflight_status"])
    proofs: list[NZMutationBoundaryProof] = []
    refusals: list[NZDryRunRefusal] = []

    # The selected_family_repeal scope relaxes ONLY the whole-work readiness
    # gate. The complete_set scope keeps the original strict refusal.
    if scope == NZ_DRY_RUN_SCOPE_COMPLETE_SET and preflight_status != "ready_for_dry_run_replay":
        # The whole candidate set is not dry-run ready. Refuse without mutating.
        return NZDryRunReport(
            work_id=work_id,
            operation_family="repeal",
            proofs=(),
            refusals=(
                NZDryRunRefusal(
                    op_id=work_id or "new_zealand",
                    rule_id=NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID,
                    message=(
                        "dry-run repeal refused because candidate preflight is not "
                        f"ready_for_dry_run_replay (status={preflight_status})"
                    ),
                    detail={"preflight_status": preflight_status},
                ),
            ),
            preflight_status=preflight_status,
            scope=scope,
        )

    repeal_rows = _replayable_repeal_rows(preflight)
    scope_completeness = (
        _selected_family_repeal_scope_completeness(preflight, repeal_rows)
        if scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL
        else None
    )
    if not repeal_rows:
        return NZDryRunReport(
            work_id=work_id,
            operation_family="repeal",
            proofs=(),
            refusals=(
                NZDryRunRefusal(
                    op_id=work_id or "new_zealand",
                    rule_id=NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
                    message="dry-run repeal refused because no replayable repeal candidate was found",
                ),
            ),
            preflight_status=preflight_status,
            scope=scope,
            scope_completeness=scope_completeness,
        )

    # Cache parsed source documents per XML locator so a work with multiple
    # repeals on the same change window does not reparse the same bytes.
    parsed_cache: dict[str, NZSourceDocument | None] = {}

    for row in repeal_rows:
        operation = row.operation
        assert operation is not None  # guaranteed by _replayable_repeal_rows
        outcome = _dry_run_one_repeal(archive, work_id, row, operation, parsed_cache)
        if isinstance(outcome, NZDryRunRefusal):
            refusals.append(outcome)
        else:
            proofs.append(outcome)

    return NZDryRunReport(
        work_id=work_id,
        operation_family="repeal",
        proofs=tuple(proofs),
        refusals=tuple(refusals),
        preflight_status=preflight_status,
        scope=scope,
        scope_completeness=scope_completeness,
    )


def _build_dry_run_text_replace(
    archive: Any,
    *,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
    scope: str,
) -> NZDryRunReport:
    """Dry-run the ready single-occurrence text-substitution operations of a work.

    Mirrors :func:`build_dry_run_repeal`'s selected-family discipline: it relaxes
    only the whole-work readiness gate, never any per-operation exactness or
    single-occurrence check.
    """

    preflight_status = str(preflight.summary()["preflight_status"])
    text_rows = _replayable_text_replace_rows(preflight)
    scope_completeness = _selected_family_text_replace_scope_completeness(preflight, text_rows)
    if not text_rows:
        return NZDryRunReport(
            work_id=work_id,
            operation_family="text_replace",
            proofs=(),
            refusals=(
                NZDryRunRefusal(
                    op_id=work_id or "new_zealand",
                    rule_id=NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
                    message="dry-run text-replace refused because no replayable text_replace candidate was found",
                ),
            ),
            preflight_status=preflight_status,
            scope=scope,
            scope_completeness=scope_completeness,
        )

    parsed_cache: dict[str, NZSourceDocument | None] = {}
    amendment_census = _amendment_date_census(preflight.candidate_report.rows)
    proofs: list[NZMutationBoundaryProof] = []
    refusals: list[NZDryRunRefusal] = []
    for row in text_rows:
        operation = row.operation
        assert operation is not None  # guaranteed by _replayable_text_replace_rows
        outcome = _dry_run_one_text_replace(
            archive, work_id, row, operation, parsed_cache, amendment_census
        )
        if isinstance(outcome, NZDryRunRefusal):
            refusals.append(outcome)
        else:
            proofs.append(outcome)

    return NZDryRunReport(
        work_id=work_id,
        operation_family="text_replace",
        proofs=tuple(proofs),
        refusals=tuple(refusals),
        preflight_status=preflight_status,
        scope=scope,
        scope_completeness=scope_completeness,
    )


def _selected_family_repeal_scope_completeness(
    preflight: NZEffectCandidatePreflightReport,
    in_scope_repeal_rows: tuple[NZCanonicalEffectCandidateRow, ...],
) -> NZDryRunScopeCompleteness:
    """Type every operation witness in the work as in- or not-in-scope.

    The selected family is the replayable repeal family. Every other operation
    witness in the work is carried under a typed not-in-scope reason so the
    partial scope can never silently inflate coverage. The total is over all
    operation-witness rows in the work (blocked rows included), because a
    blocked witness is still an operation the work owns that this scope does
    not cover.
    """

    from lawvm.new_zealand.effect_candidates import (
        _source_change_only_candidate,
        _target_recovery_candidate,
    )

    in_scope_row_ids = {row.row_id for row in in_scope_repeal_rows}
    reason_counts: dict[str, int] = {}
    repeal_reason_counts: dict[str, int] = {}
    in_scope = 0
    total = 0
    total_repeal = 0
    repeal_in_scope = 0
    for row in preflight.candidate_report.rows:
        total += 1
        is_repeal_witness = row.operation_family == _NZ_REPEAL_OPERATION_FAMILY
        if is_repeal_witness:
            total_repeal += 1
        if row.row_id in in_scope_row_ids:
            in_scope += 1
            if is_repeal_witness:
                repeal_in_scope += 1
            continue
        reason = _not_in_scope_reason(row, _source_change_only_candidate, _target_recovery_candidate)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if is_repeal_witness:
            repeal_reason_counts[reason] = repeal_reason_counts.get(reason, 0) + 1
    return NZDryRunScopeCompleteness(
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
        family="repeal",
        total_operation_witnesses=total,
        in_scope_operation_witnesses=in_scope,
        not_in_scope_operation_witnesses=total - in_scope,
        not_in_scope_reason_counts=dict(sorted(reason_counts.items())),
        total_repeal_operation_witnesses=total_repeal,
        repeal_witnesses_in_scope=repeal_in_scope,
        repeal_witnesses_not_in_scope_reason_counts=dict(sorted(repeal_reason_counts.items())),
    )


def _not_in_scope_reason(
    row: NZCanonicalEffectCandidateRow,
    source_change_only: Any,
    target_recovery: Any,
) -> str:
    if row.candidate_status != "candidate_emitted":
        return NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS
    if row.operation is None:
        return NZ_DRY_RUN_NOT_IN_SCOPE_CANDIDATE_OPERATION_MISSING
    if row.action != str(StructuralAction.REPEAL):
        return NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY
    if source_change_only(row):
        return NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_SOURCE_CHANGE_ONLY
    if target_recovery(row):
        return NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_TARGET_RECOVERY
    # A candidate_emitted, exact-target, corroborated repeal that is not in the
    # in-scope set would be a contradiction (the in-scope filter is exactly that
    # predicate). Fall back to a distinct named reason rather than silently
    # absorbing it, so any future filter drift surfaces loudly.
    return NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY


def _replayable_repeal_rows(
    preflight: NZEffectCandidatePreflightReport,
) -> tuple[NZCanonicalEffectCandidateRow, ...]:
    # Import the preflight's own replayability predicates so the dry-run surface
    # consumes exactly the operations preflight authorized (no broader set).
    from lawvm.new_zealand.effect_candidates import (
        _source_change_only_candidate,
        _target_recovery_candidate,
    )

    rows: list[NZCanonicalEffectCandidateRow] = []
    for row in preflight.candidate_report.rows:
        if row.candidate_status != "candidate_emitted":
            continue
        if row.operation is None:
            continue
        if row.action != str(StructuralAction.REPEAL):
            continue
        if _source_change_only_candidate(row) or _target_recovery_candidate(row):
            continue
        rows.append(row)
    return tuple(rows)


def _is_eligible_text_replace_operation(operation: LegalOperation) -> bool:
    """A text substitution the kernel can apply: single-occurrence OR each-place.

    Single-occurrence selects exactly occurrence 1; each-place selects
    occurrence 0 and substitutes at every matching occurrence in the target
    node. Any other selector (a specific occurrence >= 2, or a -1 last-place
    selector) is out of scope for this kernel and refuses, typed.
    """

    patch = operation.text_patch
    if patch is None or patch.replacement is None:
        return False
    return patch.selector.occurrence in (0, 1)


def _replayable_text_replace_rows(
    preflight: NZEffectCandidatePreflightReport,
) -> tuple[NZCanonicalEffectCandidateRow, ...]:
    """The emitted, exact-target, single-occurrence and each-place candidates.

    Mirrors :func:`_replayable_repeal_rows`: it consumes only what preflight
    emitted and never broadens the set. Both single-occurrence (occurrence 1)
    and each-place (occurrence 0) substitutions are in scope. Any other selector
    (a specific occurrence >= 2 or a last-place selector) is NOT in scope
    (handled as typed not-in-scope in the census and as a typed refusal if
    forced through the kernel).
    """

    rows: list[NZCanonicalEffectCandidateRow] = []
    for row in preflight.candidate_report.rows:
        if row.candidate_status != "candidate_emitted":
            continue
        if row.operation is None:
            continue
        if row.action != str(StructuralAction.TEXT_REPLACE):
            continue
        # Defence in depth: only exact-target text substitutions are eligible.
        if (
            row.latest_oracle_target_resolution_status
            and row.latest_oracle_target_resolution_status != "exact_source_path"
        ):
            continue
        if not _is_eligible_text_replace_operation(row.operation):
            continue
        rows.append(row)
    return tuple(rows)


def _is_text_replace_witness(row: NZCanonicalEffectCandidateRow) -> bool:
    """Whether an operation-witness row is a text-substitution witness.

    A text_replace witness either emitted a TEXT_REPLACE candidate or was
    blocked under the text-replace candidate's own blocking rule id. This is the
    stable discriminator for the text_replace replay-coverage denominator.
    """

    if row.candidate_status == "candidate_emitted":
        return row.action == str(StructuralAction.TEXT_REPLACE)
    return row.blocking_rule_id == _NZ_TEXT_REPLACE_BLOCKED_RULE_ID


def _selected_family_text_replace_scope_completeness(
    preflight: NZEffectCandidatePreflightReport,
    in_scope_text_rows: tuple[NZCanonicalEffectCandidateRow, ...],
) -> NZDryRunScopeCompleteness:
    """Type every operation witness in the work as in- or not-in-scope.

    The selected family is the single-occurrence text-substitution family. Every
    other operation witness is carried under a typed not-in-scope reason so the
    partial scope can never silently inflate coverage. The repeal-witness census
    fields are reused as the family-witness census (their names are generic in
    the corpus scoreboard), so the text_replace coverage denominator is every
    text_replace operation-witness, eligible or blocked.
    """

    in_scope_row_ids = {row.row_id for row in in_scope_text_rows}
    reason_counts: dict[str, int] = {}
    family_reason_counts: dict[str, int] = {}
    in_scope = 0
    total = 0
    total_family = 0
    family_in_scope = 0
    for row in preflight.candidate_report.rows:
        total += 1
        is_family_witness = _is_text_replace_witness(row)
        if is_family_witness:
            total_family += 1
        if row.row_id in in_scope_row_ids:
            in_scope += 1
            if is_family_witness:
                family_in_scope += 1
            continue
        reason = _text_replace_not_in_scope_reason(row)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if is_family_witness:
            family_reason_counts[reason] = family_reason_counts.get(reason, 0) + 1
    return NZDryRunScopeCompleteness(
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
        family="text_replace",
        total_operation_witnesses=total,
        in_scope_operation_witnesses=in_scope,
        not_in_scope_operation_witnesses=total - in_scope,
        not_in_scope_reason_counts=dict(sorted(reason_counts.items())),
        total_repeal_operation_witnesses=total_family,
        repeal_witnesses_in_scope=family_in_scope,
        repeal_witnesses_not_in_scope_reason_counts=dict(sorted(family_reason_counts.items())),
    )


def _text_replace_not_in_scope_reason(row: NZCanonicalEffectCandidateRow) -> str:
    if row.candidate_status != "candidate_emitted":
        return NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS
    if row.operation is None:
        return NZ_DRY_RUN_NOT_IN_SCOPE_CANDIDATE_OPERATION_MISSING
    if row.action != str(StructuralAction.TEXT_REPLACE):
        return NZ_DRY_RUN_NOT_IN_SCOPE_NON_TEXT_REPLACE_FAMILY
    if (
        row.latest_oracle_target_resolution_status
        and row.latest_oracle_target_resolution_status != "exact_source_path"
    ):
        return NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_TARGET_RECOVERY
    if not _is_eligible_text_replace_operation(row.operation):
        return NZ_DRY_RUN_NOT_IN_SCOPE_TEXT_REPLACE_UNSUPPORTED_SELECTOR
    # An emitted, exact, single-occurrence text substitution that is not in the
    # in-scope set would contradict the in-scope filter. Name it distinctly so
    # any future filter drift surfaces loudly rather than being absorbed.
    return NZ_DRY_RUN_NOT_IN_SCOPE_NON_TEXT_REPLACE_FAMILY


def _dry_run_one_repeal(
    archive: Any,
    work_id: str,
    row: NZCanonicalEffectCandidateRow,
    operation: LegalOperation,
    parsed_cache: dict[str, NZSourceDocument | None],
) -> NZMutationBoundaryProof | NZDryRunRefusal:
    op_id = operation.op_id
    target_address = str(operation.target)
    amendment_date_iso = row.amendment_date_iso

    # Defence in depth: even though preflight is ready, refuse any non-exact
    # target locally. Target-recovery / source-change-only must never mutate.
    if row.latest_oracle_target_resolution_status and row.latest_oracle_target_resolution_status != "exact_source_path":
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID,
            message="dry-run repeal refused because target was recovered rather than exact",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"target_resolution_status": row.latest_oracle_target_resolution_status},
        )
    if operation.witness_rule_id and "source_change" in str(operation.witness_rule_id):
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID,
            message="dry-run repeal refused because payload evidence is source-change-only",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )

    if not amendment_date_iso:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run repeal refused because the operation has no ISO amendment date for a version window",
            target_address=target_address,
        )

    change_window = archived_xml_version_change_window(
        archive,
        work_id=work_id,
        version_date=amendment_date_iso,
    )
    if change_window.before is None or change_window.on_or_after is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run repeal refused because the before/after archived XML version window is missing",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail=_change_window_detail(change_window),
        )

    before_doc = _parse_archived_version(archive, change_window.before, parsed_cache)
    if before_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
            message="dry-run repeal refused because the before XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"before_version_id": change_window.before.version_id},
        )
    oracle_doc = _parse_archived_version(archive, change_window.on_or_after, parsed_cache)
    if oracle_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
            message="dry-run repeal refused because the on-or-after XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"on_or_after_version_id": change_window.on_or_after.version_id},
        )

    source_path = _source_path_for_address(operation)
    if source_path is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_PATH_UNMAPPABLE_RULE_ID,
            message="dry-run repeal refused because the target address path is not mappable to a source-tree path",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )

    before_matches = _resolve_target_nodes(before_doc, source_path)
    if len(before_matches) == 0:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_NOT_IN_BEFORE_RULE_ID,
            message="dry-run repeal refused because the exact target is not present in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path)},
        )
    if len(before_matches) > 1:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_AMBIGUOUS_RULE_ID,
            message="dry-run repeal refused because the target source path is ambiguous in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path), "match_count": len(before_matches)},
        )

    before_target = before_matches[0]
    # The resolved node may carry a leading ``part:`` segment the address omitted;
    # everything downstream (proof, neighbours, oracle partition) uses the
    # resolved path so the surface reports the exact node it actually touched.
    resolved_path = before_target.path
    occupancy_before = _occupancy(before_target)
    if occupancy_before != "substantive":
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_NOT_SUBSTANTIVE_RULE_ID,
            message="dry-run repeal refused because the before target is not substantive (cannot tombstone)",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"occupancy_before": occupancy_before},
        )

    # --- The boring apply kernel: substantive -> tombstone, addressability kept.
    after_target = _tombstone_node(before_target)
    occupancy_after = _occupancy(after_target)

    # Mutation-boundary proof: digests + unaffected neighbours.
    parent_path = resolved_path[:-1]
    siblings_before = _sibling_nodes(before_doc, resolved_path)
    # In the candidate after-tree only the target changed; siblings are the same
    # immutable nodes from the before tree, so their digests are unchanged by
    # construction. We still record both sides to make the boundary explicit.
    neighbor_paths = tuple(node.path for node in siblings_before)
    neighbor_before = tuple(_node_digest(node) for node in siblings_before)
    neighbor_after = neighbor_before  # kernel only touched the target node
    parent_before_nodes = _nodes_at_path(before_doc, parent_path) if parent_path else ()
    parent_digest_before = _node_digest(parent_before_nodes[0]) if parent_before_nodes else ""
    parent_digest_after = parent_digest_before  # parent identity untouched

    oracle_match, oracle_rule_id, oracle_present, oracle_occupancy = _oracle_partition(
        oracle_doc, resolved_path, target_kind=_leaf_source_kind(resolved_path)
    )

    return NZMutationBoundaryProof(
        op_id=op_id,
        action=str(operation.action),
        target_address=target_address,
        selected_source_path=resolved_path,
        target_xml_id=before_target.xml_id,
        target_digest_before=_node_digest(before_target),
        target_digest_after=_node_digest(after_target),
        operation_payload=_operation_payload_text(operation),
        occupancy_before=occupancy_before,
        occupancy_after=occupancy_after,
        parent_source_path=parent_path,
        parent_digest_before=parent_digest_before,
        parent_digest_after=parent_digest_after,
        unaffected_neighbor_paths=neighbor_paths,
        unaffected_neighbor_digests_before=neighbor_before,
        unaffected_neighbor_digests_after=neighbor_after,
        neighbors_unchanged=(neighbor_before == neighbor_after and parent_digest_before == parent_digest_after),
        oracle_version_id=change_window.on_or_after.version_id,
        oracle_target_present=oracle_present,
        oracle_target_occupancy=oracle_occupancy,
        oracle_match=oracle_match,
        oracle_match_rule_id=oracle_rule_id,
    )


def _dry_run_one_text_replace(
    archive: Any,
    work_id: str,
    row: NZCanonicalEffectCandidateRow,
    operation: LegalOperation,
    parsed_cache: dict[str, NZSourceDocument | None],
    amendment_census: frozenset[tuple[str, str]] = frozenset(),
) -> NZMutationBoundaryProof | NZDryRunRefusal:
    op_id = operation.op_id
    target_address = str(operation.target)
    amendment_date_iso = row.amendment_date_iso

    patch = operation.text_patch
    if patch is None or patch.replacement is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TEXT_NO_TEXT_PATCH_RULE_ID,
            message="dry-run text-replace refused because the operation carries no text_patch replacement",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )
    # The kernel applies two selector shapes: single-occurrence (occurrence 1)
    # and each-place (occurrence 0, "in each place it occurs"). Any other
    # selector — a specific occurrence >= 2 or a last-place selector — is not
    # supported and refuses, typed. ``each_place`` drives the apply count and the
    # before-occurrence precondition below.
    if patch.selector.occurrence not in (0, 1):
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TEXT_SCOPE_NOT_SINGLE_OCCURRENCE_RULE_ID,
            message=(
                "dry-run text-replace refused because the selector is neither single-occurrence "
                f"nor each-place (selector.occurrence={patch.selector.occurrence}); not supported"
            ),
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selector_occurrence": patch.selector.occurrence},
        )
    each_place = patch.selector.occurrence == 0

    # Defence in depth: refuse any non-exact target locally (mirror repeal).
    if row.latest_oracle_target_resolution_status and row.latest_oracle_target_resolution_status != "exact_source_path":
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID,
            message="dry-run text-replace refused because target was recovered rather than exact",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"target_resolution_status": row.latest_oracle_target_resolution_status},
        )

    if not amendment_date_iso:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run text-replace refused because the operation has no ISO amendment date for a version window",
            target_address=target_address,
        )

    change_window = archived_xml_version_change_window(
        archive,
        work_id=work_id,
        version_date=amendment_date_iso,
    )
    if change_window.before is None or change_window.on_or_after is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run text-replace refused because the before/after archived XML version window is missing",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail=_change_window_detail(change_window),
        )

    before_doc = _parse_archived_version(archive, change_window.before, parsed_cache)
    if before_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
            message="dry-run text-replace refused because the before XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"before_version_id": change_window.before.version_id},
        )
    oracle_doc = _parse_archived_version(archive, change_window.on_or_after, parsed_cache)
    if oracle_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
            message="dry-run text-replace refused because the on-or-after XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"on_or_after_version_id": change_window.on_or_after.version_id},
        )

    source_path = _source_path_for_address(operation)
    if source_path is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_PATH_UNMAPPABLE_RULE_ID,
            message="dry-run text-replace refused because the target address path is not mappable to a source-tree path",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )

    before_matches = _resolve_target_nodes(before_doc, source_path)
    if len(before_matches) == 0:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_NOT_IN_BEFORE_RULE_ID,
            message="dry-run text-replace refused because the exact target is not present in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path)},
        )
    if len(before_matches) > 1:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_AMBIGUOUS_RULE_ID,
            message="dry-run text-replace refused because the target source path is ambiguous in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path), "match_count": len(before_matches)},
        )

    before_target = before_matches[0]
    resolved_path = before_target.path

    old_text = patch.selector.match_text
    new_text = patch.replacement
    # Occurrence precondition. For single-occurrence the old_text must occur
    # exactly once in the before target node text (normalized comparison): zero
    # or many is a typed refusal — the kernel never guesses which occurrence to
    # edit. For each-place the old_text must occur at least once (every
    # occurrence is substituted); only zero occurrences is a refusal. The kernel
    # never guesses: a well-defined match is required either way.
    old_occ_before = normalized_inline_occurrence_count(before_target.text, old_text)
    if each_place:
        precondition_ok = old_occ_before >= 1
        precondition_msg = (
            "dry-run text-replace refused because the each-place old_text does not occur "
            f"at least once in the before target node (normalized occurrences={old_occ_before})"
        )
    else:
        precondition_ok = old_occ_before == 1
        precondition_msg = (
            "dry-run text-replace refused because the old_text does not occur exactly once "
            f"in the before target node (normalized occurrences={old_occ_before})"
        )
    if not precondition_ok:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TEXT_OLD_TEXT_OCCURRENCE_MISMATCH_RULE_ID,
            message=precondition_msg,
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={
                "old_text": old_text,
                "old_occurrences_before": old_occ_before,
                "each_place": each_place,
            },
        )

    # --- The boring apply kernel: substitute old->new on the target node text,
    # keeping the node otherwise identical (addressable in place). For each-place
    # every literal occurrence is substituted; for single-occurrence only the
    # first.
    after_target = _substitute_node_text(
        before_target, old_text, new_text, count=-1 if each_place else 1
    )
    if after_target.text == before_target.text:
        # The normalized count said the old_text occurs but the literal text did
        # not change. Surface loudly rather than emitting a vacuous mutation proof.
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TEXT_APPLY_NO_OP_RULE_ID,
            message=(
                "dry-run text-replace refused because the apply left the node text unchanged "
                "(normalized occurrence found but literal old_text not present)"
            ),
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"old_text": old_text, "new_text": new_text},
        )
    old_occ_after = normalized_inline_occurrence_count(after_target.text, old_text)

    parent_path = resolved_path[:-1]
    siblings_before = _sibling_nodes(before_doc, resolved_path)
    neighbor_paths = tuple(node.path for node in siblings_before)
    neighbor_before = tuple(_node_digest(node) for node in siblings_before)
    neighbor_after = neighbor_before  # kernel only touched the target node text
    parent_before_nodes = _nodes_at_path(before_doc, parent_path) if parent_path else ()
    parent_digest_before = _node_digest(parent_before_nodes[0]) if parent_before_nodes else ""
    parent_digest_after = parent_digest_before  # parent identity untouched

    (
        oracle_match,
        oracle_rule_id,
        oracle_present,
        oracle_occupancy,
        oracle_old_occ,
        oracle_has_new,
    ) = _oracle_partition_text(
        oracle_doc,
        resolved_path,
        old_text=old_text,
        new_text=new_text,
        after_old_occurrences=old_occ_after,
    )
    text_neighbors_unchanged = (
        neighbor_before == neighbor_after and parent_digest_before == parent_digest_after
    )
    divergence_fields: dict[str, Any] = {}
    window_unprovable = False
    window_reason = ""
    if oracle_match != "agrees":
        divergence = _classify_oracle_text_divergence(
            oracle_doc,
            resolved_path,
            candidate_after_text=after_target.text,
        )
        divergence_fields = _divergence_proof_fields(
            oracle_match, text_neighbors_unchanged, divergence
        )
        # A pure inline text substitution touches only the target node's own text
        # and never its descendant structure. Prove window-fit across all three
        # modes: shared_window (census), snapshot_predates (oracle SUBTREE still
        # byte-identical to the before SUBTREE, so the op never landed), and
        # structural_drift (oracle subtree's node-set differs from the before
        # subtree — paragraphs only another amendment could have added/removed).
        before_descendants = _descendant_nodes(before_doc, resolved_path)
        before_subtree_digest = _subtree_digest(before_target, before_descendants)
        after_subtree_digest = _subtree_digest(after_target, before_descendants)
        before_structural_set = _structural_node_set(
            before_target, before_descendants, root_path=resolved_path
        )
        oracle_subtree_digest = ""
        oracle_structural_set: Counter[str] | None = None
        oracle_matches = _resolve_target_nodes(oracle_doc, resolved_path)
        if oracle_matches:
            oracle_root = oracle_matches[0]
            oracle_descendants = _descendant_nodes(oracle_doc, oracle_root.path)
            oracle_subtree_digest = _subtree_digest(oracle_root, oracle_descendants)
            oracle_structural_set = _structural_node_set(
                oracle_root, oracle_descendants, root_path=oracle_root.path
            )
        window_unprovable, window_reason = _prove_temporal_window_fit(
            amendment_census=amendment_census,
            change_window=change_window,
            oracle_present=oracle_present,
            target_digest_before=before_subtree_digest,
            target_digest_after=after_subtree_digest,
            oracle_target_digest=oracle_subtree_digest,
            before_structural_set=before_structural_set,
            oracle_structural_set=oracle_structural_set,
        )
        # The single text substitution we applied is the provision's net effect
        # on this node only when no OTHER instruction step in the same amending
        # provision also targets it. A second step (a further substitution, or a
        # later "add"/"insert" appending content — e.g. 2009/31 omits-and-
        # substitutes "section 357A" in s358(1) in one step and ADDS a trailing
        # sentence to s358(1) in the next) composes additional content the oracle
        # holds but our single-substitution payload does not. Typed out
        # (refuse-don't-guess), never reclassified as an oracle error.
        if not window_unprovable and oracle_present and row.amending_work_id and row.amending_provision_hrefs:
            amending_root = _amending_act_root(archive, row.amending_work_id, {})
            if amending_root is not None:
                amending_node = _amending_node_by_href(amending_root, row.amending_provision_hrefs[0])
                if amending_node is not None and _amend_provision_overlaps_target_in_other_step(
                    amending_node, resolved_path
                ):
                    window_unprovable = True
                    window_reason = NZ_WINDOW_UNPROVABLE_COMPOSED_AMEND_PROVISION

    return NZMutationBoundaryProof(
        op_id=op_id,
        action=str(operation.action),
        target_address=target_address,
        selected_source_path=resolved_path,
        target_xml_id=before_target.xml_id,
        target_digest_before=_node_digest(before_target),
        target_digest_after=_node_digest(after_target),
        operation_payload=_text_replace_payload_text(operation),
        occupancy_before=_occupancy(before_target),
        occupancy_after=_occupancy(after_target),
        parent_source_path=parent_path,
        parent_digest_before=parent_digest_before,
        parent_digest_after=parent_digest_after,
        unaffected_neighbor_paths=neighbor_paths,
        unaffected_neighbor_digests_before=neighbor_before,
        unaffected_neighbor_digests_after=neighbor_after,
        neighbors_unchanged=text_neighbors_unchanged,
        oracle_version_id=change_window.on_or_after.version_id,
        oracle_target_present=oracle_present,
        oracle_target_occupancy=oracle_occupancy,
        oracle_match=oracle_match,
        oracle_match_rule_id=oracle_rule_id,
        text_old_text=old_text,
        text_new_text=new_text,
        text_old_occurrences_before=old_occ_before,
        text_old_occurrences_after=old_occ_after,
        text_oracle_old_occurrences=oracle_old_occ,
        text_oracle_contains_new_text=oracle_has_new,
        text_each_place=each_place,
        temporal_window_unprovable=window_unprovable,
        temporal_window_unprovable_reason=window_reason,
        **divergence_fields,
    )


def _oracle_partition_text(
    oracle_doc: NZSourceDocument,
    source_path: tuple[str, ...],
    *,
    old_text: str,
    new_text: str,
    after_old_occurrences: int,
) -> tuple[str, str, bool, str, int, bool]:
    """Classify whether the on-or-after oracle reflects the substitution.

    The oracle node reflects ALL window changes, so an exact ``after-text ==
    oracle-text`` is too strict. The honest, layered classification compares the
    candidate after-node against the oracle node by normalized comparison:

    - ``agrees``: the oracle contains ``new_text`` AND the oracle's residual
      ``old_text`` count matches the candidate after-node's residual count.
      This correctly handles ``new_text`` that contains ``old_text`` as a
      substring (the substitution legitimately leaves ``old_text`` present once,
      and the oracle should carry the same residual count).
    - ``residual_old_text_remains``: the oracle carries MORE ``old_text`` than
      the candidate after-node would (the substitution was NOT reflected — a
      possible divergence or wrong target). A non-reflected substitution is
      never counted as agreement.
    - ``residual_new_text_absent``: the ``new_text`` is not present in the
      oracle (another window change overwrote it / target drift).
    - ``target_missing``: the exact target node is absent from the oracle.
    """

    oracle_matches = _resolve_target_nodes(oracle_doc, source_path)
    if not oracle_matches:
        return (
            "target_missing",
            NZ_DRY_RUN_TEXT_RESIDUAL_TARGET_MISSING_RULE_ID,
            False,
            "absent",
            0,
            False,
        )
    oracle_node = oracle_matches[0]
    oracle_occupancy = _occupancy(oracle_node)
    oracle_old_occ = normalized_inline_occurrence_count(oracle_node.text, old_text)
    oracle_has_new = normalized_inline_contains(oracle_node.text, new_text)
    if not new_text.strip():
        # Omit-only deletion: there is no new text to find. The oracle reflects
        # the deletion when the omitted span's residual count matches the
        # candidate after-node's residual count (which is the after-deletion
        # count, normally 0). MORE old_text in the oracle than we removed is a
        # non-reflected deletion (residual); the deletion agrees otherwise.
        if oracle_old_occ > after_old_occurrences:
            return (
                "residual_old_text_remains",
                NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
                True,
                oracle_occupancy,
                oracle_old_occ,
                False,
            )
        return (
            "agrees",
            NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID,
            True,
            oracle_occupancy,
            oracle_old_occ,
            True,
        )
    if oracle_old_occ > after_old_occurrences:
        # The oracle still carries an old_text occurrence the substitution
        # removed: the substitution is not reflected. Report it as a residual
        # even if new_text happens to be present elsewhere.
        return (
            "residual_old_text_remains",
            NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
            True,
            oracle_occupancy,
            oracle_old_occ,
            oracle_has_new,
        )
    if not oracle_has_new:
        return (
            "residual_new_text_absent",
            NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID,
            True,
            oracle_occupancy,
            oracle_old_occ,
            oracle_has_new,
        )
    return (
        "agrees",
        NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID,
        True,
        oracle_occupancy,
        oracle_old_occ,
        oracle_has_new,
    )


def _substitute_node_text(
    node: NZSourceNode, old_text: str, new_text: str, *, count: int = 1
) -> NZSourceNode:
    # Boring kernel: replace literal occurrences of old_text with new_text in the
    # node text, keeping the node otherwise identical (same
    # kind/path/xml_id/label/heading/occupancy). Never delete-and-forget.
    # ``count`` bounds how many occurrences are substituted: 1 for the single
    # leading occurrence, -1 for every occurrence (each-place). The count is
    # passed straight to ``str.replace``, whose -1 sentinel replaces all.
    return NZSourceNode(
        kind=node.kind,
        path=node.path,
        xml_id=node.xml_id,
        xml_path=node.xml_path,
        source_zone=node.source_zone,
        label=node.label,
        heading=node.heading,
        deletion_status=node.deletion_status,
        text=node.text.replace(old_text, new_text, count),
        history=node.history,
    )


def _text_replace_payload_text(operation: LegalOperation) -> str:
    patch = operation.text_patch
    old_text = patch.selector.match_text if patch is not None else ""
    new_text = patch.replacement if patch is not None and patch.replacement is not None else ""
    return (
        f"action={operation.action} witness_rule_id={operation.witness_rule_id or ''} "
        f"payload=text_replace old_len={len(old_text)} new_len={len(new_text)}"
    )


# --- Structural whole-provision REPLACE kernel. -------------------------------
#
# Candidate source: the work's operation surface (history-note ``replaced`` /
# ``substituted`` witnesses), each carrying a candidate target address, an ISO
# amendment date, and the cited amending act + provision href. The replacement
# payload is the amending act's ``<amend>`` subtree, extracted into an
# NZSourceNode subtree by :func:`extract_structural_replacement`.


def build_archived_work_dry_run_replace(db_path: Path, work_id: str) -> NZDryRunReport:
    """Build the structural whole-provision replace dry-run report for one work.

    Mirrors the repeal/text-replace selected-family discipline: it relaxes only
    the whole-work readiness gate (there is no whole-work preflight gate here),
    never any per-operation exactness check. Each ``replaced``/``substituted``
    witness with a candidate target and a cleanly-extractable one-to-one
    replacement payload is applied and oracle-checked; every other outcome is a
    typed refusal or typed not-in-scope, never a guess.
    """

    from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

    surface = build_archived_work_operation_surface(db_path, work_id)
    archive = open_farchive(db_path)
    try:
        return build_dry_run_replace(archive, work_id=work_id, surface=surface)
    finally:
        archive.close()


def build_dry_run_replace(
    archive: Any,
    *,
    work_id: str,
    surface: Any,
) -> NZDryRunReport:
    """Build the structural-replace report from an operation surface + archive."""

    replace_rows = _replace_witness_rows(surface)
    scope_completeness = _selected_family_replace_scope_completeness(surface, replace_rows)
    if not replace_rows:
        return NZDryRunReport(
            work_id=work_id,
            operation_family="replace",
            proofs=(),
            refusals=(
                NZDryRunRefusal(
                    op_id=work_id or "new_zealand",
                    rule_id=NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID,
                    message="dry-run structural-replace refused because no candidate replaced/substituted witness was found",
                ),
            ),
            preflight_status="operation_surface_witnesses",
            scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
            scope_completeness=scope_completeness,
        )

    parsed_cache: dict[str, NZSourceDocument | None] = {}
    amending_root_cache: dict[str, Any] = {}
    amendment_census = _amendment_date_census(surface.rows)
    proofs: list[NZMutationBoundaryProof] = []
    refusals: list[NZDryRunRefusal] = []
    for row in replace_rows:
        outcome = _dry_run_one_replace(
            archive, work_id, row, parsed_cache, amending_root_cache, amendment_census
        )
        if isinstance(outcome, NZDryRunRefusal):
            refusals.append(outcome)
        else:
            proofs.append(outcome)

    return NZDryRunReport(
        work_id=work_id,
        operation_family="replace",
        proofs=tuple(proofs),
        refusals=tuple(refusals),
        preflight_status="operation_surface_witnesses",
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
        scope_completeness=scope_completeness,
    )


def _replace_witness_rows(surface: Any) -> tuple[Any, ...]:
    """The candidate-target replaced/substituted witnesses eligible for replace.

    Eligibility here is the structural precondition every replace witness must
    meet to even attempt the kernel: a structural-substitution family and a
    candidate (exact) target address. Payload extractability and target presence
    in the before-tree are checked inside the kernel (typed refusal), never here,
    so a witness that fails them is still counted as attempted, never hidden.
    """

    rows: list[Any] = []
    for row in surface.rows:
        if row.operation_family not in _NZ_REPLACE_OPERATION_FAMILIES:
            continue
        if row.target_address_candidate.target_address_status != "candidate":
            continue
        rows.append(row)
    return tuple(rows)


def _selected_family_replace_scope_completeness(
    surface: Any,
    in_scope_rows: tuple[Any, ...],
) -> NZDryRunScopeCompleteness:
    """Type every operation witness in the work as in- or not-in-scope.

    The selected family is the candidate-target structural-replace family. Every
    other operation witness is carried under a typed not-in-scope reason so the
    partial scope can never silently inflate coverage. The repeal-witness census
    fields are reused as the family-witness census (their names are generic in
    the corpus scoreboard), so the replace coverage denominator is every
    structural-replace operation-witness (``replaced``/``substituted``),
    eligible or not.
    """

    in_scope_row_ids = {row.row_id for row in in_scope_rows}
    reason_counts: dict[str, int] = {}
    family_reason_counts: dict[str, int] = {}
    in_scope = 0
    total = 0
    total_family = 0
    family_in_scope = 0
    for row in surface.rows:
        total += 1
        is_family_witness = row.operation_family in _NZ_REPLACE_OPERATION_FAMILIES
        if is_family_witness:
            total_family += 1
        if row.row_id in in_scope_row_ids:
            in_scope += 1
            if is_family_witness:
                family_in_scope += 1
            continue
        reason = _replace_not_in_scope_reason(row)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if is_family_witness:
            family_reason_counts[reason] = family_reason_counts.get(reason, 0) + 1
    return NZDryRunScopeCompleteness(
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
        family="replace",
        total_operation_witnesses=total,
        in_scope_operation_witnesses=in_scope,
        not_in_scope_operation_witnesses=total - in_scope,
        not_in_scope_reason_counts=dict(sorted(reason_counts.items())),
        total_repeal_operation_witnesses=total_family,
        repeal_witnesses_in_scope=family_in_scope,
        repeal_witnesses_not_in_scope_reason_counts=dict(sorted(family_reason_counts.items())),
    )


def _replace_not_in_scope_reason(row: Any) -> str:
    if row.operation_family not in _NZ_REPLACE_OPERATION_FAMILIES:
        return NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPLACE_FAMILY
    if row.target_address_candidate.target_address_status != "candidate":
        return NZ_DRY_RUN_NOT_IN_SCOPE_REPLACE_TARGET_NOT_CANDIDATE
    # A candidate-target structural-replace witness not in the in-scope set would
    # contradict the in-scope filter. Name it distinctly so any future filter
    # drift surfaces loudly rather than being absorbed.
    return NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPLACE_FAMILY


def _dry_run_one_replace(
    archive: Any,
    work_id: str,
    row: Any,
    parsed_cache: dict[str, NZSourceDocument | None],
    amending_root_cache: dict[str, Any],
    amendment_census: frozenset[tuple[str, str]],
) -> NZMutationBoundaryProof | NZDryRunRefusal:
    op_id = f"nz:{work_id}:{row.row_id}:replace"
    target_address = row.target_address_candidate.address or row.amended_provision
    amendment_date_iso = row.amendment_date_iso

    if row.target_address_candidate.target_address_status != "candidate":
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_REPLACE_TARGET_NOT_CANDIDATE_RULE_ID,
            message="dry-run structural-replace refused because the target address is not an exact candidate",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"target_address_status": row.target_address_candidate.target_address_status},
        )

    if not row.amending_work_id or not row.amending_provision_hrefs:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_REPLACE_NO_AMENDING_WORK_RULE_ID,
            message="dry-run structural-replace refused because the amending work/provision href is unresolved",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={
                "amending_work_id": row.amending_work_id,
                "amending_provision_hrefs": list(row.amending_provision_hrefs),
            },
        )

    if not amendment_date_iso:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run structural-replace refused because the operation has no ISO amendment date for a version window",
            target_address=target_address,
        )

    # Resolve the structural target leaf (kind, label) from the candidate address
    # path so the payload extractor can match the amending act's <amend> child.
    source_path = _source_path_for_tree_path(row.target_address_candidate.path)
    if source_path is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_PATH_UNMAPPABLE_RULE_ID,
            message="dry-run structural-replace refused because the target address path is not mappable to a source-tree path",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )
    leaf_kind = _leaf_source_kind(source_path)
    leaf_label = _leaf_source_label(source_path)
    provision_label = _top_level_provision_label(source_path)

    # Extract the replacement payload from the amending act <amend> subtree.
    amending_root = _amending_act_root(archive, row.amending_work_id, amending_root_cache)
    if amending_root is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_REPLACE_AMENDING_XML_UNREADABLE_RULE_ID,
            message="dry-run structural-replace refused because the amending act XML is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"amending_work_id": row.amending_work_id},
        )
    href = row.amending_provision_hrefs[0]
    amending_node = _amending_node_by_href(amending_root, href)
    if amending_node is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_REPLACE_AMENDING_HREF_NOT_FOUND_RULE_ID,
            message="dry-run structural-replace refused because the amending-provision href was not found in the amending act XML",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"amending_work_id": row.amending_work_id, "amending_provision_href": href},
        )
    base_year, base_number = _base_work_year_number(work_id)
    replacement = extract_structural_replacement(
        amending_node,
        target_leaf_kind=leaf_kind,
        target_leaf_label=leaf_label,
        target_provision_label=provision_label,
        base_work_year=base_year,
        base_work_number=base_number,
        amending_act_root=amending_root,
    )
    if isinstance(replacement, str):
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_REPLACE_PAYLOAD_NOT_EXTRACTABLE_RULE_ID,
            message=(
                "dry-run structural-replace refused because the amending payload is not a clean "
                f"one-to-one replacement (reason={replacement})"
            ),
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={
                "extractor_blocker": replacement,
                "target_leaf_kind": leaf_kind,
                "target_leaf_label": leaf_label,
                "amending_work_id": row.amending_work_id,
                "amending_provision_href": href,
            },
        )

    # Resolve the before/after archived XML version window.
    change_window = archived_xml_version_change_window(
        archive,
        work_id=work_id,
        version_date=amendment_date_iso,
    )
    if change_window.before is None or change_window.on_or_after is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run structural-replace refused because the before/after archived XML version window is missing",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail=_change_window_detail(change_window),
        )
    before_doc = _parse_archived_version(archive, change_window.before, parsed_cache)
    if before_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
            message="dry-run structural-replace refused because the before XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"before_version_id": change_window.before.version_id},
        )
    oracle_doc = _parse_archived_version(archive, change_window.on_or_after, parsed_cache)
    if oracle_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
            message="dry-run structural-replace refused because the on-or-after XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"on_or_after_version_id": change_window.on_or_after.version_id},
        )

    before_matches = _resolve_target_nodes(before_doc, source_path)
    if len(before_matches) == 0:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_NOT_IN_BEFORE_RULE_ID,
            message="dry-run structural-replace refused because the exact target is not present in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path)},
        )
    if len(before_matches) > 1:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_AMBIGUOUS_RULE_ID,
            message="dry-run structural-replace refused because the target source path is ambiguous in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path), "match_count": len(before_matches)},
        )

    before_target = before_matches[0]
    resolved_path = before_target.path

    # The amend payload may encode an interchangeable lettered-paragraph leaf
    # under the alias kind (``subprov`` vs ``label-para``) — a source-encoding
    # artifact, not a semantic difference. Normalize the extracted root's kind to
    # the resolved live-body target's kind so the candidate subtree compares
    # against the oracle (which carries the body kind) without a spurious
    # kind-only mismatch. The label, text, and structure are unchanged.
    replacement_root = _align_replacement_root_kind(replacement.root, before_target.kind)

    # --- The boring apply kernel: re-root the extracted replacement subtree onto
    # the resolved target path and swap it in for the target's subtree.
    after_target = _rebase_replacement_root(replacement_root, resolved_path)
    if _node_digest(after_target) == _node_digest(before_target) and after_target.text == before_target.text:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_REPLACE_APPLY_NO_OP_RULE_ID,
            message="dry-run structural-replace refused because the apply left the target subtree unchanged",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )

    parent_path = resolved_path[:-1]
    siblings_before = _sibling_nodes(before_doc, resolved_path)
    neighbor_paths = tuple(node.path for node in siblings_before)
    neighbor_before = tuple(_node_digest(node) for node in siblings_before)
    neighbor_after = neighbor_before  # kernel only swapped the target subtree
    parent_before_nodes = _nodes_at_path(before_doc, parent_path) if parent_path else ()
    parent_digest_before = _node_digest(parent_before_nodes[0]) if parent_before_nodes else ""
    parent_digest_after = parent_digest_before

    candidate_subtree_digest = _subtree_digest(replacement_root, replacement.descendants)
    (
        oracle_match,
        oracle_rule_id,
        oracle_present,
        oracle_occupancy,
        oracle_subtree_digest,
    ) = _oracle_partition_replace(
        oracle_doc,
        resolved_path,
        candidate_root=replacement_root,
        candidate_descendants=replacement.descendants,
    )
    neighbors_unchanged = (
        neighbor_before == neighbor_after and parent_digest_before == parent_digest_after
    )
    divergence_fields: dict[str, Any] = {}
    window_unprovable = False
    window_reason = ""
    if oracle_match != "agrees":
        divergence = _classify_oracle_target_divergence(
            oracle_doc,
            resolved_path,
            candidate_root=replacement_root,
            candidate_descendants=replacement.descendants,
            # REPLACE keeps label-STRIPPED alignment: a renumber-on-replace
            # (candidate paragraph (c) where the oracle now reads (d)) must not
            # align the wrong sibling pair.
            preserve_labels=False,
        )
        divergence_fields = _divergence_proof_fields(oracle_match, neighbors_unchanged, divergence)
        # Whole-node replace: the snapshot-predates proof compares the oracle
        # SUBTREE against the before SUBTREE (a replace that has not landed leaves
        # the oracle subtree byte-identical to the before subtree). Structural
        # drift does not apply to a structural replace (the payload defines the
        # node's structure), so only shared_window + snapshot_predates are checked.
        before_subtree_digest = _subtree_digest(
            before_target, _descendant_nodes(before_doc, resolved_path)
        )
        window_unprovable, window_reason = _prove_temporal_window_fit(
            amendment_census=amendment_census,
            change_window=change_window,
            oracle_present=oracle_present,
            target_digest_before=before_subtree_digest,
            target_digest_after=candidate_subtree_digest,
            oracle_target_digest=oracle_subtree_digest,
        )
        # The structured replace step we extracted is the provision's net effect
        # only when no OTHER step in the same amending provision re-touches the
        # target (a further substitution on the same leaf, or an each-place
        # insert into an enclosing scope, or a later "add" step). When one does,
        # our payload is the intermediate state and the oracle reflects the
        # composed net effect — typed out (refuse-don't-guess).
        if (
            not window_unprovable
            and oracle_present
            and (
                _amend_provision_composes_target(amending_node, leaf_label)
                or _amend_provision_overlaps_target_in_other_step(amending_node, resolved_path)
            )
        ):
            window_unprovable = True
            window_reason = NZ_WINDOW_UNPROVABLE_COMPOSED_AMEND_PROVISION

    return NZMutationBoundaryProof(
        op_id=op_id,
        action=str(StructuralAction.REPLACE),
        target_address=target_address,
        selected_source_path=resolved_path,
        target_xml_id=before_target.xml_id,
        target_digest_before=_node_digest(before_target),
        target_digest_after=_node_digest(after_target),
        operation_payload=_replace_payload_text(replacement, row),
        occupancy_before=_occupancy(before_target),
        occupancy_after=_occupancy(after_target),
        parent_source_path=parent_path,
        parent_digest_before=parent_digest_before,
        parent_digest_after=parent_digest_after,
        unaffected_neighbor_paths=neighbor_paths,
        unaffected_neighbor_digests_before=neighbor_before,
        unaffected_neighbor_digests_after=neighbor_after,
        neighbors_unchanged=neighbors_unchanged,
        oracle_version_id=change_window.on_or_after.version_id,
        oracle_target_present=oracle_present,
        oracle_target_occupancy=oracle_occupancy,
        oracle_match=oracle_match,
        oracle_match_rule_id=oracle_rule_id,
        replace_amending_work_id=row.amending_work_id,
        replace_amending_provision_href=href,
        replace_replacement_descendant_count=len(replacement.descendants),
        replace_candidate_subtree_digest=candidate_subtree_digest,
        replace_oracle_subtree_digest=oracle_subtree_digest,
        temporal_window_unprovable=window_unprovable,
        temporal_window_unprovable_reason=window_reason,
        **divergence_fields,
    )


def _oracle_partition_replace(
    oracle_doc: NZSourceDocument,
    source_path: tuple[str, ...],
    *,
    candidate_root: NZSourceNode,
    candidate_descendants: tuple[NZSourceNode, ...],
) -> tuple[str, str, bool, str, str]:
    """Classify whether the on-or-after oracle subtree matches the replacement.

    The honest classification compares the candidate replacement node-subtree
    (root + descendants) against the oracle node-subtree at the resolved target
    path by NORMALIZED text/structure:

    - ``agrees``: the oracle target node-subtree matches the candidate
      replacement subtree (same normalized leaf labels/kinds and per-node text).
    - ``residual_replacement_mismatch``: the oracle target node-subtree exists
      but differs from the candidate replacement (another window change / wrong
      content). A mismatch is NEVER counted as agreement.
    - ``target_missing``: the exact target node is absent from the oracle.
    """

    oracle_matches = _resolve_target_nodes(oracle_doc, source_path)
    if not oracle_matches:
        return (
            "target_missing",
            NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID,
            False,
            "absent",
            "",
        )
    oracle_root = oracle_matches[0]
    oracle_occupancy = _occupancy(oracle_root)
    oracle_descendants = _descendant_nodes(oracle_doc, oracle_root.path)
    candidate_sig = _normalized_subtree_signature(candidate_root, candidate_descendants, root_path=candidate_root.path)
    oracle_sig = _normalized_subtree_signature(oracle_root, oracle_descendants, root_path=oracle_root.path)
    oracle_subtree_digest = _subtree_digest(oracle_root, oracle_descendants)
    if candidate_sig == oracle_sig:
        return (
            "agrees",
            NZ_DRY_RUN_REPLACE_AGREES_RULE_ID,
            True,
            oracle_occupancy,
            oracle_subtree_digest,
        )
    return (
        "residual_replacement_mismatch",
        NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID,
        True,
        oracle_occupancy,
        oracle_subtree_digest,
    )


# --- Structural whole-provision INSERT kernel. --------------------------------
#
# Mirrors the REPLACE kernel spine (resolve / extract payload / oracle-partition /
# mutation-boundary proof) but for a node ADD rather than a node swap:
#
# - new-node content is read from the amending act's <amend> subtree by
#   :func:`extract_structural_insertion` (the per-witness label selects the single
#   inserted node, so a one-to-many "insert the following sections" amend subtree
#   is fine);
# - the anchor + direction are DERIVED from the inserted node's suffix-letter
#   label (NZ convention: a section ``18A`` is inserted AFTER section ``18``;
#   ``18B`` after ``18A``). No explicit "after section N" prose is needed because
#   the label convention fixes the position exactly; a label without a clean
#   suffix-letter (or a non-single-segment target) is a typed refusal, never a
#   guessed position;
# - the before tree must NOT already carry the new node (it would be a no-op or a
#   wrong window) and MUST carry the derived anchor exactly once;
# - the candidate after-tree adds the new node next to the anchor among siblings.
#   The anchor and pre-existing siblings are PROVED unchanged (equal digests); the
#   new node's presence is the only delta — insertion must not perturb neighbours;
# - the oracle agrees iff the on-or-after XML carries the new node with content
#   matching the candidate new-node payload (normalized); absent -> residual not
#   present; present-but-different -> residual content mismatch. Never loosened.


def build_archived_work_dry_run_insert(db_path: Path, work_id: str) -> NZDryRunReport:
    """Build the structural whole-provision insert dry-run report for one work."""

    from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

    surface = build_archived_work_operation_surface(db_path, work_id)
    archive = open_farchive(db_path)
    try:
        return build_dry_run_insert(archive, work_id=work_id, surface=surface)
    finally:
        archive.close()


def build_dry_run_insert(
    archive: Any,
    *,
    work_id: str,
    surface: Any,
) -> NZDryRunReport:
    """Build the structural-insert report from an operation surface + archive."""

    insert_rows = _insert_witness_rows(surface)
    scope_completeness = _selected_family_insert_scope_completeness(surface, insert_rows)
    if not insert_rows:
        return NZDryRunReport(
            work_id=work_id,
            operation_family="insert",
            proofs=(),
            refusals=(
                NZDryRunRefusal(
                    op_id=work_id or "new_zealand",
                    rule_id=NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
                    message="dry-run structural-insert refused because no candidate inserted/added witness was found",
                ),
            ),
            preflight_status="operation_surface_witnesses",
            scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
            scope_completeness=scope_completeness,
        )

    parsed_cache: dict[str, NZSourceDocument | None] = {}
    amending_root_cache: dict[str, Any] = {}
    # Block-insert co-members: the set of same-kind sibling labels this work
    # inserts under the same parent. A whole new Part / a run of sequential new
    # sections all anchor (by the before-tree convention) on the single existing
    # predecessor, so in the oracle each member after the first is immediately
    # preceded by ANOTHER block member rather than by the derived before-tree
    # anchor. The position check uses this set to recognize a contiguous block
    # landing as oracle-confirmed (a co-member predecessor) rather than a
    # position residual — never to invent a payload (the oracle only informs
    # POSITION; co-membership comes from this work's own insert witnesses).
    block_member_labels = _insert_block_member_labels(insert_rows)
    amendment_census = _amendment_date_census(surface.rows)
    proofs: list[NZMutationBoundaryProof] = []
    refusals: list[NZDryRunRefusal] = []
    for row in insert_rows:
        outcome = _dry_run_one_insert(
            archive,
            work_id,
            row,
            parsed_cache,
            amending_root_cache,
            block_member_labels,
            amendment_census,
        )
        if isinstance(outcome, NZDryRunRefusal):
            refusals.append(outcome)
        else:
            proofs.append(outcome)

    return NZDryRunReport(
        work_id=work_id,
        operation_family="insert",
        proofs=tuple(proofs),
        refusals=tuple(refusals),
        preflight_status="operation_surface_witnesses",
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
        scope_completeness=scope_completeness,
    )


def _insert_witness_rows(surface: Any) -> tuple[Any, ...]:
    """The candidate-target inserted/added witnesses eligible for the kernel.

    Eligibility here is the structural precondition every insert witness must meet
    to even attempt the kernel: an insert family and a candidate (exact) target
    address (the inserted node's own address). Anchor derivability, payload
    extractability, and before-tree positioning are checked inside the kernel
    (typed refusal), never here, so a witness that fails them is still counted as
    attempted, never hidden.
    """

    rows: list[Any] = []
    for row in surface.rows:
        if row.operation_family not in _NZ_INSERT_OPERATION_FAMILIES:
            continue
        if row.target_address_candidate.target_address_status != "candidate":
            continue
        rows.append(row)
    return tuple(rows)


def _insert_block_member_labels(
    insert_rows: tuple[Any, ...],
) -> dict[tuple[tuple[str, ...], str], frozenset[str]]:
    """Co-member labels of every same-kind sibling group this work inserts.

    Keyed by ``(parent_source_path, leaf_kind)`` to the set of leaf labels the
    work inserts under that parent at that kind. A whole new Part inserts a run
    of sequential sections; each member's true predecessor (after the first) is
    another member of this very set, not the single existing before-tree anchor
    they all derive. The position check consults this set so a contiguous block
    landing the oracle confirms is not flagged a position residual. The set comes
    only from this work's own insert witnesses (candidate targets) — it never
    sources a payload, only the identity of co-inserted siblings.
    """

    groups: dict[tuple[tuple[str, ...], str], set[str]] = {}
    for row in insert_rows:
        source_path = _source_path_for_tree_path(row.target_address_candidate.path)
        if source_path is None:
            continue
        leaf_kind = _leaf_source_kind(source_path)
        leaf_label = _leaf_source_label(source_path)
        if not leaf_label:
            continue
        key = (source_path[:-1], leaf_kind)
        groups.setdefault(key, set()).add(leaf_label)
    return {key: frozenset(labels) for key, labels in groups.items()}


def _selected_family_insert_scope_completeness(
    surface: Any,
    in_scope_rows: tuple[Any, ...],
) -> NZDryRunScopeCompleteness:
    """Type every operation witness in the work as in- or not-in-scope (insert)."""

    in_scope_row_ids = {row.row_id for row in in_scope_rows}
    reason_counts: dict[str, int] = {}
    family_reason_counts: dict[str, int] = {}
    in_scope = 0
    total = 0
    total_family = 0
    family_in_scope = 0
    for row in surface.rows:
        total += 1
        is_family_witness = row.operation_family in _NZ_INSERT_OPERATION_FAMILIES
        if is_family_witness:
            total_family += 1
        if row.row_id in in_scope_row_ids:
            in_scope += 1
            if is_family_witness:
                family_in_scope += 1
            continue
        reason = _insert_not_in_scope_reason(row)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if is_family_witness:
            family_reason_counts[reason] = family_reason_counts.get(reason, 0) + 1
    return NZDryRunScopeCompleteness(
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
        family="insert",
        total_operation_witnesses=total,
        in_scope_operation_witnesses=in_scope,
        not_in_scope_operation_witnesses=total - in_scope,
        not_in_scope_reason_counts=dict(sorted(reason_counts.items())),
        total_repeal_operation_witnesses=total_family,
        repeal_witnesses_in_scope=family_in_scope,
        repeal_witnesses_not_in_scope_reason_counts=dict(sorted(family_reason_counts.items())),
    )


def _insert_not_in_scope_reason(row: Any) -> str:
    if row.operation_family not in _NZ_INSERT_OPERATION_FAMILIES:
        return NZ_DRY_RUN_NOT_IN_SCOPE_NON_INSERT_FAMILY
    if row.target_address_candidate.target_address_status != "candidate":
        return NZ_DRY_RUN_NOT_IN_SCOPE_INSERT_TARGET_NOT_CANDIDATE
    return NZ_DRY_RUN_NOT_IN_SCOPE_NON_INSERT_FAMILY


def _derive_insert_anchor(leaf_kind: str, leaf_label: str) -> tuple[str, str] | None:
    """Derive the anchor sibling label + direction from a suffix-letter label.

    NZ inserts a new provision next to an existing one using a suffix-letter
    label convention: ``18A`` is inserted AFTER ``18``; ``18B`` AFTER ``18A``;
    ``5A`` AFTER ``5``. The position is therefore fixed exactly by the label, with
    no need for explicit "after section N" prose. Returns ``(anchor_label,
    direction)`` for a clean single trailing letter on a numeric stem, else
    ``None`` (multi-letter suffixes, non-suffixed labels, and Roman-style labels
    are refused — never a guessed position).
    """

    match = re.fullmatch(r"([0-9]+)([A-Za-z])", leaf_label)
    if match is None:
        return None
    stem, suffix = match.group(1), match.group(2)
    lower = suffix.lower()
    if lower == "a":
        # 18A is inserted after the bare stem (18).
        return (stem, "after")
    if "a" <= lower <= "z":
        # 18B after 18A, 18C after 18B, ...
        return (stem + chr(ord(lower) - 1).upper() if suffix.isupper() else stem + chr(ord(lower) - 1), "after")
    return None


# A whole-provision leaf label is a bare-numeric ("7") or a multi-letter suffix
# ("14AB", "147ZA") when the single-trailing-letter convention does not apply.
# Both anchor on an EXISTING sibling and therefore must be validated against the
# before-tree's top-level sibling group; their derivation is deferred (the
# single-trailing-letter case stays a pure label derivation with no before-tree
# dependency).
_BARE_NUMERIC_LABEL_RE = re.compile(r"[0-9]+")
_MULTI_LETTER_SUFFIX_LABEL_RE = re.compile(r"[0-9]+[A-Za-z]{2,}")


def _is_before_tree_dependent_insert_label(leaf_label: str) -> bool:
    """Whether the top-level anchor for ``leaf_label`` needs the before-tree.

    True for a bare-numeric label (its predecessor is the greatest numerically
    smaller existing sibling) and for a multi-letter suffix label (its
    predecessor is the label with the final suffix letter removed, which must be
    present). False for everything else — including the single-trailing-letter
    convention (derived up front) and genuinely non-derivable labels (Roman
    numerals, empty), which are refused up front.
    """

    return bool(
        _BARE_NUMERIC_LABEL_RE.fullmatch(leaf_label)
        or _MULTI_LETTER_SUFFIX_LABEL_RE.fullmatch(leaf_label)
    )


def _derive_top_level_insert_anchor(
    leaf_label: str,
    sibling_labels: tuple[str, ...],
) -> tuple[str, str] | None:
    """Derive a whole-provision anchor that depends on the before-tree siblings.

    Two before-tree-dependent label shapes (the single-trailing-letter shape is
    handled by :func:`_derive_insert_anchor` without the before-tree):

    - **Bare-numeric leaf** (``7``): inserted AFTER the existing same-kind sibling
      with the greatest numeric label strictly less than ``7`` (e.g. ``6``).
      Comparison is numeric, not lexical (``7`` > ``6``, ``100`` > ``99``). Only
      the purely-numeric portion of a sibling label is comparable, so suffixed
      siblings (``6A``) are not predecessors of a bare numeric. If no smaller
      numeric sibling exists (``7`` would be first), refuse — never guess an
      append/renumber position.
    - **Multi-letter suffix leaf** (``14AB``, ``147ZA``): inserted AFTER the
      label with the final suffix letter removed (``14A``, ``147Z``), which must
      be present among the siblings; otherwise refuse.

    Returns ``(anchor_sibling_label, "after")`` or ``None`` (typed refusal in the
    caller). The derived anchor is matched against the actual sibling set, so an
    unresolvable position never produces a guess.
    """

    sibling_set = set(sibling_labels)

    if _MULTI_LETTER_SUFFIX_LABEL_RE.fullmatch(leaf_label):
        # The multi-letter predecessor is, in order of preference:
        #   1. the prior label in the suffix sequence (decrement the final
        #      letter: 14AC -> 14AB, 147ZB -> 147ZA) when present, then
        #   2. the label with the final suffix letter stripped (14AB -> 14A,
        #      147ZA -> 147Z, 18AA -> 18A) when present.
        # Both candidates are validated against the actual siblings; the oracle
        # position check is the final arbiter regardless. If neither is present,
        # refuse (no guessed position).
        last = leaf_label[-1]
        prior_in_sequence: str | None = None
        if last not in ("a", "A"):
            prior_in_sequence = leaf_label[:-1] + chr(ord(last) - 1)
        if prior_in_sequence is not None and prior_in_sequence in sibling_set:
            return (prior_in_sequence, "after")
        stripped = leaf_label[:-1]
        if stripped in sibling_set:
            return (stripped, "after")
        return None

    if _BARE_NUMERIC_LABEL_RE.fullmatch(leaf_label):
        target_value = int(leaf_label)
        # Candidate predecessors: purely-numeric siblings strictly smaller than N.
        numeric_siblings = [
            (int(label), label)
            for label in sibling_labels
            if _BARE_NUMERIC_LABEL_RE.fullmatch(label)
        ]
        smaller = [(value, label) for value, label in numeric_siblings if value < target_value]
        if not smaller:
            # N would be first (or no comparable numeric sibling): a renumber/
            # append we will not guess.
            return None
        # Greatest numeric predecessor wins (insert immediately after it).
        _, anchor_label = max(smaller, key=lambda item: item[0])
        return (anchor_label, "after")

    return None


def _derive_nested_insert_anchor(
    leaf_kind: str,
    leaf_label: str,
    sibling_labels: tuple[str, ...],
) -> tuple[str, str] | None:
    """Derive the anchor sibling label + direction for a NESTED inserted node.

    A nested insert places a new subsection / paragraph / definition among the
    leaf's siblings under an already-resolved parent. The position is derived
    from the label convention applied at the nested level, validated against the
    parent's existing sibling group:

    - **Suffix-letter leaf** (``3A``, ``ba``): same convention as the top-level
      anchor — ``3A`` after ``3``, ``ba`` after ``b``. The derived predecessor
      must exist among the siblings; otherwise refuse (no guessed position).
    - **Bare-numeric leaf** (``4``): inserted after its numeric predecessor
      (``3``) when that predecessor exists among the siblings. A bare-numeric
      leaf with no existing predecessor is a renumber/append we will not guess —
      refuse.
    - **Bare-alpha leaf** (``c``): inserted after its alpha predecessor (``b``)
      when that predecessor exists among the siblings; otherwise refuse.
    - **Definition** (``def-para``, alpha-ordered by term): inserted in
      case-insensitive alphabetical order among the existing definition terms.
      The anchor is the immediately-preceding term; if the new term sorts before
      every sibling it is inserted ``before`` the alphabetically-first sibling.
      An empty sibling group is not a derivable position — refuse.

    Returns ``(anchor_sibling_label, direction)`` or ``None`` (typed refusal in
    the caller). The returned anchor label is matched against the actual sibling
    set, so an unresolvable or ambiguous position never produces a guess.
    """

    sibling_set = set(sibling_labels)

    if leaf_kind == "def-para":
        # Definitions are alpha-ordered by term (case-insensitive). The label IS
        # the defined term. Find the alphabetically-immediately-preceding term.
        if not leaf_label or not sibling_labels:
            return None
        key = leaf_label.casefold()
        preceding = [s for s in sibling_labels if s.casefold() < key]
        if not preceding:
            # The new term sorts before every existing definition: insert before
            # the alphabetically-first sibling.
            first = min(sibling_labels, key=lambda s: s.casefold())
            return (first, "before")
        anchor = max(preceding, key=lambda s: s.casefold())
        return (anchor, "after")

    # Suffix-letter convention (3A after 3, ba after b) reuses the top-level
    # derivation, then requires the derived predecessor to actually be a sibling.
    suffix_anchor = _derive_insert_anchor(leaf_kind, leaf_label)
    if suffix_anchor is not None:
        anchor_label, direction = suffix_anchor
        if anchor_label in sibling_set:
            return (anchor_label, direction)
        return None

    # Bare-numeric leaf (4): after its numeric predecessor (3), if it exists.
    if re.fullmatch(r"[0-9]+", leaf_label):
        predecessor = str(int(leaf_label) - 1)
        if predecessor in sibling_set:
            return (predecessor, "after")
        return None

    # Bare-alpha leaf (c): after its single-letter alpha predecessor (b).
    if re.fullmatch(r"[a-z]", leaf_label):
        if leaf_label == "a":
            return None
        predecessor = chr(ord(leaf_label) - 1)
        if predecessor in sibling_set:
            return (predecessor, "after")
        return None
    if re.fullmatch(r"[A-Z]", leaf_label):
        if leaf_label == "A":
            return None
        predecessor = chr(ord(leaf_label) - 1)
        if predecessor in sibling_set:
            return (predecessor, "after")
        return None

    return None


def _dry_run_one_insert(
    archive: Any,
    work_id: str,
    row: Any,
    parsed_cache: dict[str, NZSourceDocument | None],
    amending_root_cache: dict[str, Any],
    block_member_labels: Mapping[tuple[tuple[str, ...], str], frozenset[str]] | None = None,
    amendment_census: frozenset[tuple[str, str]] = frozenset(),
) -> NZMutationBoundaryProof | NZDryRunRefusal:
    op_id = f"nz:{work_id}:{row.row_id}:insert"
    target_address = row.target_address_candidate.address or row.amended_provision
    amendment_date_iso = row.amendment_date_iso

    if row.target_address_candidate.target_address_status != "candidate":
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_TARGET_NOT_CANDIDATE_RULE_ID,
            message="dry-run structural-insert refused because the target address is not an exact candidate",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"target_address_status": row.target_address_candidate.target_address_status},
        )

    # The inserted node's own source path (where it will live). A single-segment
    # source path is a WHOLE provision/part/schedule — the suffix-letter anchor
    # convention applies at the top level. A multi-segment path is a NESTED insert
    # (a new subsection/paragraph/definition WITHIN an existing provision); its
    # anchor + position are derived among the leaf's siblings under the resolved
    # parent, which requires the before-tree, so nested anchor derivation is
    # deferred until after the before-tree is parsed (below).
    new_node_source_path = _source_path_for_tree_path(row.target_address_candidate.path)
    if new_node_source_path is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_PATH_UNMAPPABLE_RULE_ID,
            message="dry-run structural-insert refused because the inserted node address path is not mappable to a source-tree path",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )
    leaf_kind = _leaf_source_kind(new_node_source_path)
    leaf_label = _leaf_source_label(new_node_source_path)
    is_nested = len(new_node_source_path) > 1
    parent_source_path = new_node_source_path[:-1]

    top_level_anchor: tuple[str, str] | None = None
    # A bare-numeric ("7") or multi-letter suffix ("14AB") whole-provision label
    # anchors on an EXISTING sibling and so is derived against the before-tree's
    # top-level sibling group below; defer it. The single-trailing-letter
    # convention (18A -> 18) stays a pure up-front label derivation.
    top_level_anchor_deferred = (
        not is_nested and _is_before_tree_dependent_insert_label(leaf_label)
    )
    if not is_nested and not top_level_anchor_deferred:
        # Whole-provision insert with the single-trailing-letter convention:
        # derive the top-level anchor from the label up front (no before-tree
        # dependency). Genuinely non-derivable labels (Roman numerals, empty) are
        # refused here. Unchanged behavior.
        top_level_anchor = _derive_insert_anchor(leaf_kind, leaf_label)
        if top_level_anchor is None:
            return NZDryRunRefusal(
                op_id=op_id,
                rule_id=NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID,
                message="dry-run structural-insert refused because no anchor sibling is derivable from the inserted node's label",
                target_address=target_address,
                amendment_date_iso=amendment_date_iso,
                detail={"inserted_leaf_kind": leaf_kind, "inserted_leaf_label": leaf_label},
            )

    if not row.amending_work_id or not row.amending_provision_hrefs:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_NO_AMENDING_WORK_RULE_ID,
            message="dry-run structural-insert refused because the amending work/provision href is unresolved",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={
                "amending_work_id": row.amending_work_id,
                "amending_provision_hrefs": list(row.amending_provision_hrefs),
            },
        )

    if not amendment_date_iso:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run structural-insert refused because the operation has no ISO amendment date for a version window",
            target_address=target_address,
        )

    # Extract the new-node payload from the amending act <amend> subtree.
    amending_root = _amending_act_root(archive, row.amending_work_id, amending_root_cache)
    if amending_root is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_AMENDING_XML_UNREADABLE_RULE_ID,
            message="dry-run structural-insert refused because the amending act XML is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"amending_work_id": row.amending_work_id},
        )
    href = row.amending_provision_hrefs[0]
    amending_node = _amending_node_by_href(amending_root, href)
    if amending_node is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_AMENDING_HREF_NOT_FOUND_RULE_ID,
            message="dry-run structural-insert refused because the amending-provision href was not found in the amending act XML",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"amending_work_id": row.amending_work_id, "amending_provision_href": href},
        )
    # For a NESTED insert the witness's enclosing section disambiguates which of
    # the amending section's several <amend> subtrees carries the new node; for a
    # whole-provision insert the new node IS the section, so there is no enclosing
    # section to scope by (None falls back to leaf-only matching).
    insert_provision_label = _top_level_provision_label(parent_source_path) if is_nested else None
    base_year, base_number = _base_work_year_number(work_id)
    payload = extract_structural_insertion(
        amending_node,
        inserted_leaf_kind=leaf_kind,
        inserted_leaf_label=leaf_label,
        target_provision_label=insert_provision_label,
        base_work_year=base_year,
        base_work_number=base_number,
        amending_act_root=amending_root,
    )
    if isinstance(payload, str):
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_PAYLOAD_NOT_EXTRACTABLE_RULE_ID,
            message=(
                "dry-run structural-insert refused because the amending payload is not a clean "
                f"single new node (reason={payload})"
            ),
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={
                "extractor_blocker": payload,
                "inserted_leaf_kind": leaf_kind,
                "inserted_leaf_label": leaf_label,
                "amending_work_id": row.amending_work_id,
                "amending_provision_href": href,
            },
        )

    # Resolve the before/after archived XML version window.
    change_window = archived_xml_version_change_window(
        archive,
        work_id=work_id,
        version_date=amendment_date_iso,
    )
    if change_window.before is None or change_window.on_or_after is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run structural-insert refused because the before/after archived XML version window is missing",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail=_change_window_detail(change_window),
        )
    before_doc = _parse_archived_version(archive, change_window.before, parsed_cache)
    if before_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
            message="dry-run structural-insert refused because the before XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"before_version_id": change_window.before.version_id},
        )
    oracle_doc = _parse_archived_version(archive, change_window.on_or_after, parsed_cache)
    if oracle_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
            message="dry-run structural-insert refused because the on-or-after XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"on_or_after_version_id": change_window.on_or_after.version_id},
        )

    # The new node must NOT already be in the before tree (an insert ADDS it).
    existing = _resolve_target_nodes(before_doc, new_node_source_path)
    if len(existing) > 0:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_TARGET_ALREADY_IN_BEFORE_RULE_ID,
            message="dry-run structural-insert refused because the new node is already present in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"new_node_source_path": list(new_node_source_path)},
        )
    # Derive + resolve the anchor sibling. For a nested insert the anchor + the
    # direction are derived from the leaf's actual sibling group under the parent
    # (which must resolve to exactly one live-body node first); for a whole
    # provision the top-level anchor was already derived from the label.
    if is_nested:
        parent_matches = _resolve_target_nodes(before_doc, parent_source_path)
        if len(parent_matches) == 0:
            return NZDryRunRefusal(
                op_id=op_id,
                rule_id=NZ_DRY_RUN_REFUSED_INSERT_PARENT_NOT_IN_BEFORE_RULE_ID,
                message="dry-run structural-insert refused because the inserted node's parent is not present in the before tree",
                target_address=target_address,
                amendment_date_iso=amendment_date_iso,
                detail={"parent_source_path": list(parent_source_path)},
            )
        if len(parent_matches) > 1:
            return NZDryRunRefusal(
                op_id=op_id,
                rule_id=NZ_DRY_RUN_REFUSED_INSERT_PARENT_AMBIGUOUS_RULE_ID,
                message="dry-run structural-insert refused because the inserted node's parent path is ambiguous in the before tree",
                target_address=target_address,
                amendment_date_iso=amendment_date_iso,
                detail={"parent_source_path": list(parent_source_path), "match_count": len(parent_matches)},
            )
        resolved_parent_path = parent_matches[0].path
        sibling_nodes = _child_nodes_of_kind(before_doc, resolved_parent_path, leaf_kind)
        sibling_labels = tuple(node.label for node in sibling_nodes if node.label)
        nested_anchor = _derive_nested_insert_anchor(leaf_kind, leaf_label, sibling_labels)
        if nested_anchor is None:
            return NZDryRunRefusal(
                op_id=op_id,
                rule_id=NZ_DRY_RUN_REFUSED_INSERT_NESTED_ANCHOR_NOT_DERIVABLE_RULE_ID,
                message="dry-run structural-insert refused because no anchor sibling is derivable from the nested sibling group",
                target_address=target_address,
                amendment_date_iso=amendment_date_iso,
                detail={
                    "inserted_leaf_kind": leaf_kind,
                    "inserted_leaf_label": leaf_label,
                    "sibling_labels": list(sibling_labels),
                },
            )
        anchor_label, direction = nested_anchor
        anchor_source_path = (*resolved_parent_path, f"{leaf_kind}:{anchor_label}")
    elif top_level_anchor_deferred:
        # Whole-provision bare-numeric / multi-letter suffix insert: derive the
        # anchor from the before-tree's top-level same-kind sibling group. A
        # top-level provision is pathed either at the root (``prov:6``) or under
        # a single part (``part:1/prov:6``); collect both shapes.
        sibling_labels = _top_level_sibling_labels(before_doc, leaf_kind)
        top_level_anchor = _derive_top_level_insert_anchor(leaf_label, sibling_labels)
        if top_level_anchor is None:
            return NZDryRunRefusal(
                op_id=op_id,
                rule_id=NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_DERIVABLE_RULE_ID,
                message="dry-run structural-insert refused because no anchor sibling is derivable from the inserted node's label and the before-tree sibling group",
                target_address=target_address,
                amendment_date_iso=amendment_date_iso,
                detail={
                    "inserted_leaf_kind": leaf_kind,
                    "inserted_leaf_label": leaf_label,
                    "top_level_sibling_labels": list(sibling_labels),
                },
            )
        anchor_label, direction = top_level_anchor
        anchor_source_path = (f"{leaf_kind}:{anchor_label}",)
    else:
        assert top_level_anchor is not None  # derived above for the whole-provision path
        anchor_label, direction = top_level_anchor
        anchor_source_path = (f"{leaf_kind}:{anchor_label}",)

    # The derived anchor must resolve to exactly one live-body node.
    anchor_matches = _resolve_target_nodes(before_doc, anchor_source_path)
    if len(anchor_matches) == 0:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_NOT_IN_BEFORE_RULE_ID,
            message="dry-run structural-insert refused because the derived anchor sibling is not present in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"anchor_source_path": list(anchor_source_path)},
        )
    if len(anchor_matches) > 1:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_INSERT_ANCHOR_AMBIGUOUS_RULE_ID,
            message="dry-run structural-insert refused because the derived anchor source path is ambiguous in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"anchor_source_path": list(anchor_source_path), "match_count": len(anchor_matches)},
        )

    anchor_node = anchor_matches[0]
    # The new node lands among the anchor's siblings, on the anchor's path stem
    # (same parent), addressed by its own label. Re-root the extracted payload's
    # root onto that path so the candidate after-node is addressable in place.
    new_node_resolved_path = (*anchor_node.path[:-1], f"{leaf_kind}:{leaf_label}")
    after_new_node = _rebase_replacement_root(payload.root, new_node_resolved_path)

    # --- The boring apply kernel: ADD the new node next to the anchor. The anchor
    # and pre-existing siblings are unchanged (insert only adds a node + shifts
    # positions); we prove that by equal before/after digests of every existing
    # sibling. The new node's presence is the only delta.
    siblings_before = _sibling_nodes(before_doc, anchor_node.path) + (anchor_node,)
    neighbor_paths = tuple(node.path for node in siblings_before)
    neighbor_before = tuple(_node_digest(node) for node in siblings_before)
    # Insert does not mutate any pre-existing sibling's content, so the after
    # digests of the SAME nodes are identical. (Position/order shifts but content
    # digests do not — the proof is content-unchanged, the new node is additive.)
    neighbor_after = neighbor_before
    anchor_digest = _node_digest(anchor_node)

    parent_path = anchor_node.path[:-1]
    parent_before_nodes = _nodes_at_path(before_doc, parent_path) if parent_path else ()
    parent_digest_before = _node_digest(parent_before_nodes[0]) if parent_before_nodes else ""
    # The parent gains a child but the parent NODE's own content (its label/
    # heading/text) is unchanged by an insert of a child provision.
    parent_digest_after = parent_digest_before

    candidate_subtree_digest = _subtree_digest(payload.root, payload.descendants)
    # Co-inserted block siblings the oracle may legitimately place between the
    # derived anchor and the new node: this work's other insert witnesses in the
    # same (parent, kind) group that are NOT already present in the before tree.
    # A before-tree label is the genuine anchor (already covered by the derived
    # anchor / oracle adjacency); only the genuinely-new co-members extend the
    # accepted-position set, and only because the oracle confirms the contiguous
    # block landing. This never sources content — co-membership is identity-only.
    group_block_labels: frozenset[str] = frozenset()
    if block_member_labels is not None:
        group_key = (parent_source_path, leaf_kind)
        candidate_block = block_member_labels.get(group_key, frozenset())
        before_group_labels = _resolved_group_labels(before_doc, anchor_node.path, leaf_kind)
        group_block_labels = frozenset(candidate_block - before_group_labels)
    (
        oracle_match,
        oracle_rule_id,
        oracle_present,
        oracle_occupancy,
        oracle_subtree_digest,
    ) = _oracle_partition_insert(
        oracle_doc,
        new_node_source_path,
        candidate_root=payload.root,
        candidate_descendants=payload.descendants,
        derived_anchor_label=anchor_label,
        derived_anchor_kind=leaf_kind,
        derived_direction=direction,
        co_inserted_block_labels=group_block_labels,
    )
    insert_neighbors_unchanged = (
        neighbor_before == neighbor_after and parent_digest_before == parent_digest_after
    )
    divergence_fields: dict[str, Any] = {}
    window_unprovable = False
    window_reason = ""
    if oracle_match != "agrees":
        divergence = _classify_oracle_target_divergence(
            oracle_doc,
            new_node_source_path,
            candidate_root=payload.root,
            candidate_descendants=payload.descendants,
            # INSERT uses label-PRESERVING alignment: a freshly-inserted
            # provision's subsection labels are stable (new content, no
            # renumbering), so same-kind siblings align to their counterparts and
            # a per-leaf substantive divergence (e.g. a wrong cross-reference in
            # one subsection) is surfaced instead of collapsing the siblings.
            preserve_labels=True,
        )
        divergence_fields = _divergence_proof_fields(
            oracle_match, insert_neighbors_unchanged, divergence
        )
        # An inserted node has no before-state and no structural reference; only
        # the shared-window proof applies (more than one amendment in the window
        # means the snapshot composes several inserts, not just this one).
        window_unprovable, window_reason = _prove_temporal_window_fit(
            amendment_census=amendment_census,
            change_window=change_window,
            oracle_present=oracle_present,
        )

    return NZMutationBoundaryProof(
        op_id=op_id,
        action=str(StructuralAction.INSERT),
        target_address=target_address,
        selected_source_path=new_node_resolved_path,
        target_xml_id=after_new_node.xml_id,
        target_digest_before="",  # the new node did not exist before
        target_digest_after=_node_digest(after_new_node),
        operation_payload=_insert_payload_text(payload, row, anchor_label, direction),
        occupancy_before="absent",
        occupancy_after=_occupancy(after_new_node),
        parent_source_path=parent_path,
        parent_digest_before=parent_digest_before,
        parent_digest_after=parent_digest_after,
        unaffected_neighbor_paths=neighbor_paths,
        unaffected_neighbor_digests_before=neighbor_before,
        unaffected_neighbor_digests_after=neighbor_after,
        neighbors_unchanged=insert_neighbors_unchanged,
        oracle_version_id=change_window.on_or_after.version_id,
        oracle_target_present=oracle_present,
        oracle_target_occupancy=oracle_occupancy,
        oracle_match=oracle_match,
        oracle_match_rule_id=oracle_rule_id,
        insert_anchor_source_path=anchor_node.path,
        insert_direction=direction,
        insert_new_node_source_path=new_node_resolved_path,
        insert_amending_work_id=row.amending_work_id,
        insert_amending_provision_href=href,
        insert_new_node_descendant_count=len(payload.descendants),
        insert_anchor_digest_before=anchor_digest,
        insert_anchor_digest_after=anchor_digest,
        insert_candidate_subtree_digest=candidate_subtree_digest,
        insert_oracle_subtree_digest=oracle_subtree_digest,
        insert_co_inserted_block_labels=group_block_labels,
        temporal_window_unprovable=window_unprovable,
        temporal_window_unprovable_reason=window_reason,
        **divergence_fields,
    )


def _oracle_adjacent_sibling_label(
    oracle_doc: NZSourceDocument,
    node: NZSourceNode,
    leaf_kind: str,
    direction: str,
) -> str | None:
    """Label of the oracle's same-kind sibling immediately adjacent to ``node``.

    For ``direction == "after"`` this is the sibling that immediately PRECEDES
    ``node`` in document order under the same parent (the anchor the new node was
    inserted after); for ``direction == "before"`` it is the sibling that
    immediately FOLLOWS. Returns ``None`` when no such adjacent same-kind sibling
    exists (the new node is first/last among its kind) — that case cannot confirm
    or refute a derived anchor and is handled by the caller.
    """

    parent = node.path[:-1]
    order = {id(n): i for i, n in enumerate(oracle_doc.nodes)}
    node_index = order[id(node)]
    siblings = [
        n
        for n in oracle_doc.nodes
        if n.path != node.path
        and n.path[:-1] == parent
        and n.kind == leaf_kind
        and n.label
    ]
    if direction == "after":
        preceding = [n for n in siblings if order[id(n)] < node_index]
        if not preceding:
            return None
        return max(preceding, key=lambda n: order[id(n)]).label
    following = [n for n in siblings if order[id(n)] > node_index]
    if not following:
        return None
    return min(following, key=lambda n: order[id(n)]).label


def _oracle_partition_insert(
    oracle_doc: NZSourceDocument,
    new_node_source_path: tuple[str, ...],
    *,
    candidate_root: NZSourceNode,
    candidate_descendants: tuple[NZSourceNode, ...],
    derived_anchor_label: str,
    derived_anchor_kind: str,
    derived_direction: str,
    co_inserted_block_labels: frozenset[str] = frozenset(),
) -> tuple[str, str, bool, str, str]:
    """Classify whether the on-or-after oracle carries the inserted node.

    Honest classification:

    - ``agrees``: the oracle carries the new node at its address with a subtree
      whose normalized signature matches the candidate new-node payload AND the
      new node's adjacent same-kind sibling in the oracle (preceding for an
      ``after`` anchor, following for a ``before`` anchor) is the anchor we
      derived OR another co-inserted block member (a sibling this work also
      inserts, absent from the before tree). Both content and position must hold.
    - ``residual_insert_position_mismatch``: the new node is present with matching
      content but its adjacent oracle sibling is NEITHER the derived anchor NOR a
      co-inserted block member — the derived position is genuinely wrong (not a
      contiguous block landing the oracle confirms). NEVER agreement.
    - ``residual_insert_content_mismatch``: the new node is present in the oracle
      but its content differs from the candidate payload. NEVER agreement.
    - ``residual_insert_not_present``: the new node is absent from the oracle.

    The position check is the arbiter of the derived anchor: a content-correct but
    position-wrong derivation cannot masquerade as agreement. A block insert (a
    whole new Part / a run of sequential new sections) has every member derive the
    SAME single existing predecessor as its anchor, but in the oracle each member
    after the first is immediately preceded by ANOTHER new block member. Such a
    co-member predecessor is accepted as oracle-confirmed position (the oracle
    only informs POSITION; the co-member identity comes from this work's own
    insert witnesses, never from oracle content). When the new node is first/last
    among its kind in the oracle there is no adjacent sibling to check; the
    position is then taken as consistent (content match alone decides), since the
    derivation only ever claims an EXISTING-sibling anchor and an absent adjacent
    sibling cannot refute it.
    """

    oracle_matches = _resolve_target_nodes(oracle_doc, new_node_source_path)
    if not oracle_matches:
        return (
            "residual_insert_not_present",
            NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID,
            False,
            "absent",
            "",
        )
    oracle_root = oracle_matches[0]
    oracle_occupancy = _occupancy(oracle_root)
    oracle_descendants = _descendant_nodes(oracle_doc, oracle_root.path)
    candidate_sig = _normalized_subtree_signature(candidate_root, candidate_descendants, root_path=candidate_root.path)
    oracle_sig = _normalized_subtree_signature(oracle_root, oracle_descendants, root_path=oracle_root.path)
    oracle_subtree_digest = _subtree_digest(oracle_root, oracle_descendants)
    if candidate_sig != oracle_sig:
        return (
            "residual_insert_content_mismatch",
            NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID,
            True,
            oracle_occupancy,
            oracle_subtree_digest,
        )
    # Content matches. Validate the derived anchor against the oracle's actual
    # adjacent same-kind sibling. A mismatch is a position residual, never an
    # agreement — this keeps a derived guess from masquerading as confirmed.
    oracle_adjacent = _oracle_adjacent_sibling_label(
        oracle_doc, oracle_root, derived_anchor_kind, derived_direction
    )
    position_confirmed = (
        oracle_adjacent is None
        or oracle_adjacent == derived_anchor_label
        or oracle_adjacent in co_inserted_block_labels
    )
    if not position_confirmed:
        return (
            "residual_insert_position_mismatch",
            NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID,
            True,
            oracle_occupancy,
            oracle_subtree_digest,
        )
    return (
        "agrees",
        NZ_DRY_RUN_INSERT_AGREES_RULE_ID,
        True,
        oracle_occupancy,
        oracle_subtree_digest,
    )


def _insert_payload_text(payload: NZStructuralReplacement, row: Any, anchor_label: str, direction: str) -> str:
    return (
        f"action={StructuralAction.INSERT} "
        f"payload=structural_insert amending={row.amending_work_id} "
        f"new_node={payload.root.kind}:{payload.root.label} "
        f"anchor={direction}:{anchor_label} "
        f"descendants={len(payload.descendants)}"
    )


# --- Structural materialization (actual-replay reuse). ------------------------
#
# The dry-run REPLACE/INSERT kernels prove a per-op mutation boundary but keep
# only digests of the candidate after-subtree — they never assemble a full
# after-``NZSourceDocument`` because the dry-run only ever oracle-partitions at a
# path. The strict actual-replay surface needs that whole materialized after-tree
# (the actual replay OUTPUT). These additive helpers assemble it WITHOUT a second
# apply path: they re-extract the SAME payload the kernel extracted (keyed by the
# proof's recorded amending work/href + the resolved target leaf), align/rebase it
# the SAME way, and splice it into the before document's flat node tuple. The
# extraction, kind alignment, and re-rooting are the kernel's own primitives, so
# the materialized subtree is exactly the one the dry-run proof was produced from.


class NZStructuralMaterializationError(Exception):
    """A verified structural proof could not be re-materialized into an after-tree.

    Raised (never swallowed) when re-extracting the proof's payload from the
    amending act fails despite the op being dry-run-verified — e.g. the amending
    XML became unreadable between the dry-run and the materialization, or the
    resolved target/anchor is no longer present in the before tree. The caller
    fails closed on this; it is never a silent skip.
    """


def _reextract_structural_replacement_for_proof(
    archive: Any,
    proof: NZMutationBoundaryProof,
    before_target: NZSourceNode,
    amending_root_cache: dict[str, Any],
) -> NZStructuralReplacement:
    """Re-extract + align the replacement subtree a verified REPLACE proof used."""

    resolved_path = proof.selected_source_path
    leaf_kind = _leaf_source_kind(resolved_path)
    leaf_label = _leaf_source_label(resolved_path)
    provision_label = _top_level_provision_label(resolved_path)
    amending_root = _amending_act_root(archive, proof.replace_amending_work_id, amending_root_cache)
    if amending_root is None:
        raise NZStructuralMaterializationError(
            "amending act XML unreadable while re-materializing a verified replace proof"
        )
    amending_node = _amending_node_by_href(amending_root, proof.replace_amending_provision_href)
    if amending_node is None:
        raise NZStructuralMaterializationError(
            "amending-provision href not found while re-materializing a verified replace proof"
        )
    base_year, base_number = _base_work_year_number_from_proof(proof.op_id)
    replacement = extract_structural_replacement(
        amending_node,
        target_leaf_kind=leaf_kind,
        target_leaf_label=leaf_label,
        target_provision_label=provision_label,
        base_work_year=base_year,
        base_work_number=base_number,
        amending_act_root=amending_root,
    )
    if isinstance(replacement, str):
        raise NZStructuralMaterializationError(
            f"replacement payload not re-extractable while materializing (reason={replacement})"
        )
    aligned_root = _align_replacement_root_kind(replacement.root, before_target.kind)
    return NZStructuralReplacement(root=aligned_root, descendants=replacement.descendants)


def _reextract_structural_insertion_for_proof(
    archive: Any,
    proof: NZMutationBoundaryProof,
    amending_root_cache: dict[str, Any],
) -> NZStructuralReplacement:
    """Re-extract the new-node subtree a verified INSERT proof used."""

    new_node_path = proof.insert_new_node_source_path or proof.selected_source_path
    leaf_kind = _leaf_source_kind(new_node_path)
    leaf_label = _leaf_source_label(new_node_path)
    is_nested = len(new_node_path) > 1
    provision_label = _top_level_provision_label(new_node_path[:-1]) if is_nested else None
    amending_root = _amending_act_root(archive, proof.insert_amending_work_id, amending_root_cache)
    if amending_root is None:
        raise NZStructuralMaterializationError(
            "amending act XML unreadable while re-materializing a verified insert proof"
        )
    amending_node = _amending_node_by_href(amending_root, proof.insert_amending_provision_href)
    if amending_node is None:
        raise NZStructuralMaterializationError(
            "amending-provision href not found while re-materializing a verified insert proof"
        )
    base_year, base_number = _base_work_year_number_from_proof(proof.op_id)
    payload = extract_structural_insertion(
        amending_node,
        inserted_leaf_kind=leaf_kind,
        inserted_leaf_label=leaf_label,
        target_provision_label=provision_label,
        base_work_year=base_year,
        base_work_number=base_number,
        amending_act_root=amending_root,
    )
    if isinstance(payload, str):
        raise NZStructuralMaterializationError(
            f"insert payload not re-extractable while materializing (reason={payload})"
        )
    return payload


def apply_structural_replace_to_nodes(
    before_doc: NZSourceDocument,
    proof: NZMutationBoundaryProof,
    archive: Any,
    amending_root_cache: dict[str, Any],
) -> tuple[tuple[NZSourceNode, ...], NZSourceNode]:
    """Materialize one verified REPLACE: swap the target subtree for the payload.

    Returns the after node tuple (target root + descendants replaced by the
    re-rooted replacement subtree; every other node identical) and the new
    after-root node. The kind alignment + re-rooting are the kernel's own
    primitives; the splice is a flat-tuple swap of the target subtree. Raises
    :class:`NZStructuralMaterializationError` if the payload is no longer
    re-extractable (fail-closed in the caller), never a guess.
    """

    resolved_path = proof.selected_source_path
    target_matches = _resolve_target_nodes(before_doc, resolved_path)
    if len(target_matches) != 1:
        raise NZStructuralMaterializationError(
            "verified replace target is no longer uniquely present in the before tree"
        )
    before_target = target_matches[0]
    replacement = _reextract_structural_replacement_for_proof(
        archive, proof, before_target, amending_root_cache
    )
    after_root = _rebase_replacement_root(replacement.root, before_target.path)
    after_descendants = _rebase_subtree_descendants(
        replacement.root, replacement.descendants, before_target.path
    )
    old_subtree_paths = {before_target.path} | {
        node.path for node in _descendant_nodes(before_doc, before_target.path)
    }
    new_nodes: list[NZSourceNode] = []
    spliced = False
    for node in before_doc.nodes:
        if node.path == before_target.path:
            new_nodes.append(after_root)
            new_nodes.extend(after_descendants)
            spliced = True
            continue
        if node.path in old_subtree_paths:
            # A descendant of the replaced subtree: dropped (the replacement
            # subtree carries its own descendants). It was emitted above.
            continue
        new_nodes.append(node)
    if not spliced:  # defence in depth: target resolved above, must be present
        raise NZStructuralMaterializationError(
            "verified replace target vanished from the before tree during the splice"
        )
    return tuple(new_nodes), after_root


def apply_structural_insert_to_nodes(
    before_doc: NZSourceDocument,
    proof: NZMutationBoundaryProof,
    archive: Any,
    amending_root_cache: dict[str, Any],
) -> tuple[tuple[NZSourceNode, ...], NZSourceNode]:
    """Materialize one verified INSERT: add the new node + descendants by anchor.

    Returns the after node tuple (the new node + its descendants spliced next to
    the verified anchor, in the proof's recorded direction; every pre-existing
    node unchanged) and the new node. The new node lands among the anchor's
    siblings on the anchor's path stem, addressed by its own label — exactly the
    placement the dry-run proof was produced from. Raises
    :class:`NZStructuralMaterializationError` if the anchor or payload is no
    longer resolvable (fail-closed in the caller), never a guess.
    """

    anchor_matches = _nodes_at_path(before_doc, proof.insert_anchor_source_path)
    if len(anchor_matches) != 1:
        raise NZStructuralMaterializationError(
            "verified insert anchor is no longer uniquely present in the before tree"
        )
    anchor_node = anchor_matches[0]
    payload = _reextract_structural_insertion_for_proof(archive, proof, amending_root_cache)
    new_node_path = proof.insert_new_node_source_path or proof.selected_source_path
    if _resolve_target_nodes(before_doc, new_node_path):
        raise NZStructuralMaterializationError(
            "verified insert new node is already present in the before tree"
        )
    after_new_node = _rebase_replacement_root(payload.root, new_node_path)
    after_descendants = _rebase_subtree_descendants(payload.root, payload.descendants, new_node_path)
    inserted_block = (after_new_node, *after_descendants)

    # Splice the inserted block immediately AFTER the anchor's whole subtree (for
    # direction "after") or immediately BEFORE the anchor node (for "before"). The
    # anchor's own descendants must stay attached to it, so for "after" we insert
    # past the last node of the anchor's subtree.
    anchor_subtree_paths = {anchor_node.path} | {
        node.path for node in _descendant_nodes(before_doc, anchor_node.path)
    }
    new_nodes: list[NZSourceNode] = []
    inserted = False
    nodes = before_doc.nodes
    if proof.insert_direction == "before":
        for node in nodes:
            if node.path == anchor_node.path and not inserted:
                new_nodes.extend(inserted_block)
                inserted = True
            new_nodes.append(node)
    else:
        # "after": emit the anchor and its whole subtree, then the inserted block.
        index = 0
        while index < len(nodes):
            node = nodes[index]
            new_nodes.append(node)
            if node.path == anchor_node.path and not inserted:
                index += 1
                # Carry the anchor's descendants across before the inserted block.
                while index < len(nodes) and nodes[index].path in anchor_subtree_paths:
                    new_nodes.append(nodes[index])
                    index += 1
                new_nodes.extend(inserted_block)
                inserted = True
                continue
            index += 1
    if not inserted:  # defence in depth: anchor resolved above, must be present
        raise NZStructuralMaterializationError(
            "verified insert anchor vanished from the before tree during the splice"
        )
    return tuple(new_nodes), after_new_node


def _rebase_subtree_descendants(
    payload_root: NZSourceNode,
    descendants: tuple[NZSourceNode, ...],
    resolved_root_path: tuple[str, ...],
) -> tuple[NZSourceNode, ...]:
    """Re-root each payload descendant onto the resolved root path.

    The payload subtree carries placeholder ``amend/...`` paths; re-root every
    descendant by replacing its ``payload_root.path`` prefix with the resolved
    root path so the materialized subtree is addressable in place. Node content is
    the new payload, never the old (mirrors :func:`_rebase_replacement_root`).
    """

    root_depth = len(payload_root.path)
    rebased: list[NZSourceNode] = []
    for node in descendants:
        suffix = node.path[root_depth:]
        new_path = (*resolved_root_path, *suffix)
        rebased.append(
            NZSourceNode(
                kind=node.kind,
                path=new_path,
                xml_id=node.xml_id,
                xml_path=node.xml_path,
                source_zone=node.source_zone,
                label=node.label,
                heading=node.heading,
                deletion_status=node.deletion_status,
                text=node.text,
                history=node.history,
            )
        )
    return tuple(rebased)


def _base_work_year_number_from_proof(op_id: str) -> tuple[str, str]:
    """Parse the base work id out of a structural proof op id, then year/number.

    Structural proof op ids are ``nz:{work_id}:{row_id}:{family}``; the work id is
    the second colon-segment. Returns ``("", "")`` for any other shape (the
    extractor then falls back to inline-amend matching, never a guess).
    """

    parts = op_id.split(":")
    if len(parts) >= 2 and parts[0] == "nz":
        return _base_work_year_number(parts[1])
    return ("", "")


def _source_path_for_tree_path(tree_path: tuple[tuple[str, str], ...]) -> tuple[str, ...] | None:
    """Map a target-address candidate TreePath to a source-tree path.

    Mirrors :func:`_source_path_for_address` but consumes the operation-surface
    candidate ``(address_kind, label)`` steps directly.
    """

    segments: list[str] = []
    for step in tree_path:
        kind = step[0]
        label = step[1] if len(step) > 1 else ""
        source_kind = _ADDRESS_KIND_TO_SOURCE_KIND.get(kind)
        if source_kind is None or not label:
            return None
        segments.append(f"{source_kind}:{label}")
    if not segments:
        return None
    return tuple(segments)


def _leaf_source_label(source_path: tuple[str, ...]) -> str:
    if not source_path:
        return ""
    leaf = source_path[-1]
    for separator in (":", "@", "#"):
        if separator in leaf:
            return leaf.split(separator, 1)[1]
    return ""


_BASE_WORK_ID_RE = re.compile(r"^act_public_(?P<year>\d{4})_(?P<number>[0-9A-Za-z]+)$")


def _base_work_year_number(work_id: str) -> tuple[str, str]:
    """Parse a base ``act_public_{year}_{number}`` work id into (year, number).

    Returns ``("", "")`` for any other work-id shape. The number is normalized the
    same way :func:`parse_public_act_citation` normalizes a schedule-group heading
    citation (leading zeros stripped) so the two compare exactly when keying a
    schedule amendment group to the base act.
    """
    match = _BASE_WORK_ID_RE.match(work_id or "")
    if match is None:
        return ("", "")
    number = match.group("number")
    return (match.group("year"), number.lstrip("0") or "0")


def _top_level_provision_label(source_path: tuple[str, ...]) -> str | None:
    """The first ``prov:`` segment's label in a source path, if any.

    A sub-provision target ``part:3/prov:88/subprov:4`` is anchored in section
    "88"; this returns that section label so the structural-payload extractor can
    disambiguate which of the amending section's several ``<amend>`` subtrees (one
    per instruction) carries the operation. Returns ``None`` when the path has no
    ``prov:`` segment (e.g. a schedule-only target), in which case the extractor
    falls back to section-agnostic leaf matching.
    """

    for segment in source_path:
        kind, _, label = segment.partition(":")
        if kind == "prov" and label:
            return label
    return None


def _align_replacement_root_kind(replacement_root: NZSourceNode, target_kind: str) -> NZSourceNode:
    """Normalize a kind-aliased replacement root to the live-body target kind.

    The structural extractor matches an interchangeable lettered-paragraph leaf
    (``subprov`` vs ``label-para``) across the alias, so the amend payload may
    carry the node under the alias kind. That tag choice is a source-encoding
    artifact: the node being replaced is the target's kind. When the extracted
    root kind already equals the target kind this is a no-op; otherwise the kind
    is rewritten to the target kind (label/text/everything else unchanged) so the
    candidate subtree compares cleanly against the oracle's body-kind node.
    """

    if replacement_root.kind == target_kind:
        return replacement_root
    return NZSourceNode(
        kind=target_kind,
        path=replacement_root.path,
        xml_id=replacement_root.xml_id,
        xml_path=replacement_root.xml_path,
        source_zone=replacement_root.source_zone,
        label=replacement_root.label,
        heading=replacement_root.heading,
        deletion_status=replacement_root.deletion_status,
        text=replacement_root.text,
        history=replacement_root.history,
    )


def _rebase_replacement_root(replacement_root: NZSourceNode, resolved_path: tuple[str, ...]) -> NZSourceNode:
    """Re-root the extracted replacement node onto the resolved target path.

    The replacement payload carries a placeholder ``amend/...`` path; the apply
    kernel re-roots its root onto the live-body target path so the candidate
    after-node is addressable exactly where the target was. The node content
    (kind/label/heading/text/deletion-status) is the new payload, never the old.
    """

    return NZSourceNode(
        kind=replacement_root.kind,
        path=resolved_path,
        xml_id=replacement_root.xml_id,
        xml_path=replacement_root.xml_path,
        source_zone=replacement_root.source_zone,
        label=replacement_root.label,
        heading=replacement_root.heading,
        deletion_status=replacement_root.deletion_status,
        text=replacement_root.text,
        history=replacement_root.history,
    )


def _descendant_nodes(document: NZSourceDocument, root_path: tuple[str, ...]) -> tuple[NZSourceNode, ...]:
    """Nodes strictly under ``root_path`` in document order."""

    depth = len(root_path)
    return tuple(
        node
        for node in document.nodes
        if len(node.path) > depth and node.path[:depth] == root_path
    )


def _normalized_subtree_signature(
    root: NZSourceNode,
    descendants: tuple[NZSourceNode, ...],
    *,
    root_path: tuple[str, ...],
    preserve_labels: bool = False,
) -> tuple[tuple[str, str, str], ...]:
    """A path-relative, normalized signature of a node-subtree for comparison.

    Each node contributes ``(relative_path, kind, normalized_text)``. Paths are
    made relative to ``root_path`` so the candidate (rooted at its amend path)
    and the oracle (rooted at the live-body path) compare structurally. Text is
    comparison-normalized so incidental whitespace/markup differences do not
    create false mismatches. Heading is folded into the leaf text by the source
    parser's ``_legal_text`` already, so the per-node text captures it.

    By default (``preserve_labels=False``) the LABEL is dropped off each path
    step, leaving a kind-only relative path. This is required for the REPLACE
    family, where a renumber-on-replace inside the subtree (a candidate paragraph
    (c) landing where the oracle now reads (d)) must not defeat structural
    comparison — label-stripping is exactly what keeps a renumbered replace from
    aligning the wrong sibling pair.

    When ``preserve_labels=True`` the full label is kept on each step (so
    ``subprov:1`` and ``subprov:2`` are DISTINCT keys). This is for the INSERT
    family: a freshly-inserted provision's subsection labels are stable (it is new
    content, no renumbering), so label-preserving alignment lets two same-kind
    siblings be aligned to their counterparts and a per-leaf substantive
    divergence (e.g. a wrong cross-reference in one subsection) be surfaced
    instead of collapsing the siblings to one ambiguous key.
    """

    depth = len(root_path)

    def relative(path: tuple[str, ...]) -> str:
        rel = path[depth:]
        if preserve_labels:
            # Keep the label-bearing segment as-is so same-kind siblings get
            # distinct keys. Drop only the addressing-suffix decorations
            # (``@``/``#``) that are never part of the label identity.
            return "/".join(segment.split("@", 1)[0].split("#", 1)[0] for segment in rel)
        # Drop the label off each step so a renumber-on-replace inside the
        # subtree does not defeat structural comparison; kind is kept.
        return "/".join(segment.split(":", 1)[0].split("@", 1)[0].split("#", 1)[0] for segment in rel)

    entries: list[tuple[str, str, str]] = [("", root.kind, normalize_inline_comparison_text(root.text))]
    for node in descendants:
        entries.append((relative(node.path), node.kind, normalize_inline_comparison_text(node.text)))
    return tuple(sorted(entries))


def _subtree_digest(root: NZSourceNode, descendants: tuple[NZSourceNode, ...]) -> str:
    payload = "".join(
        _node_digest(node) for node in (root, *descendants)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _divergence_proof_fields(
    oracle_match: str,
    neighbors_unchanged: bool,
    divergence: NZTargetDivergence,
) -> dict[str, Any]:
    """Project an ``NZTargetDivergence`` into the proof's divergence fields.

    Applies the candidate-only retention rule: the diverging node-text pairs (the
    auditability packet) are retained ONLY when the proof is a consolidation-error
    candidate (residual + boundary held + substantive + commensurable), so a
    corpus-wide run does not bloat every editorial / non-commensurable residual
    with retained texts. The class / sub-families / non-commensurable flag are
    always carried (they are cheap typed signals).
    """

    is_candidate = (
        oracle_match != "agrees"
        and neighbors_unchanged
        and divergence.divergence_class == NZ_DIVERGENCE_CLASS_SUBSTANTIVE
        and not divergence.non_commensurable_whole_node
    )
    return {
        "divergence_class": divergence.divergence_class,
        "divergence_sub_families": divergence.sub_families,
        "non_commensurable_whole_node": divergence.non_commensurable_whole_node,
        "divergence_node_pairs": divergence.node_pairs if is_candidate else (),
    }


def _is_non_commensurable_whole_node(
    leaf_kind: str,
    oracle_descendant_count: int,
    diverging_leaf_count: int,
    *,
    allow_localized_container: bool = False,
) -> bool:
    """Whether a substantive residual is a non-commensurable whole-node compare.

    The oracle-descendant backstop fires for ANY kind whose subtree has ballooned
    past the threshold (a far-larger-than-single-amendment subtree), regardless of
    how the divergence is distributed.

    A structural CONTAINER (a whole Part / subpart / crossheading / section) is
    otherwise gated by ``allow_localized_container``:

    - When False (the REPLACE family, and any caller without label-aware
      alignment), a container is ALWAYS non-commensurable: a whole-provision
      payload compared against a container the oracle may have independently
      further amended is not a commensurable comparison, and without stable
      per-leaf alignment we cannot localize the divergence safely.
    - When True (the INSERT family, which aligns by stable labels), the container
      is non-commensurable only when the divergence is PERVASIVE — more than
      ``_NON_COMMENSURABLE_LOCALIZED_MAX`` distinct descendant leaves diverge.
      A LOCALIZED substantive divergence (at most that many diverging leaves) in
      a label-aligned container is a genuine candidate (e.g. a single wrong
      cross-reference in one subsection of a freshly-inserted section).

    Conservative by design: a pervasively-diverging or unlocalizable container is
    typed non-commensurable (kept OUT of the candidate set) rather than emitted as
    a false oracle-error candidate.
    """

    if oracle_descendant_count > _NON_COMMENSURABLE_DESCENDANT_THRESHOLD:
        return True
    if leaf_kind in _NON_COMMENSURABLE_CONTAINER_KINDS:
        if not allow_localized_container:
            return True
        return diverging_leaf_count > _NON_COMMENSURABLE_LOCALIZED_MAX
    return False


def _classify_oracle_target_divergence(
    oracle_doc: NZSourceDocument,
    source_path: tuple[str, ...],
    *,
    candidate_root: NZSourceNode,
    candidate_descendants: tuple[NZSourceNode, ...],
    preserve_labels: bool = False,
) -> NZTargetDivergence:
    """Reconstruct + classify the candidate-vs-oracle subtree divergence.

    This is the wiring of ``classify_oracle_divergence`` into the structural
    REPLACE / INSERT residual path: it resolves the oracle target node at the
    same source path, builds the candidate and oracle normalized subtree
    signatures, aligns the nodes by ``(relative_path, kind)``, and types each
    diverging node-text pair. The per-node sub-families fold into one
    target-level ``divergence_class``:

    - oracle target ABSENT -> ``divergence_class = None`` (no subtree pair to
      align; the residual is target-missing/not-present, already typed by the
      partition function and never a content candidate);
    - node SETS differ -> ``structural_nodeset`` (REPLACE), or, for the INSERT
      family (``preserve_labels=True``), align the COMMON label-keys and surface
      a LOCALIZED substantive divergence in a common node as a candidate while
      treating the added/removed-only nodes as a structural note (not a blocker);
    - all diverging nodes ``is_editorial`` -> ``editorial``;
    - otherwise -> ``substantive``.

    ``preserve_labels`` selects the alignment key (see
    :func:`_normalized_subtree_signature`): the INSERT family keys by the full
    label (stable for new content) so two same-kind siblings can be aligned to
    their counterparts and a per-leaf substantive divergence surfaced; the
    REPLACE family keeps label-stripped keys so a renumber-on-replace does not
    align the wrong sibling pair.

    For a ``substantive`` divergence the non-commensurable-whole-node gate is
    evaluated against the resolved oracle leaf kind, descendant count, and the
    PERVASIVENESS of the divergence (number of diverging descendant leaves). The
    diverging node-text pairs are returned in full (the caller retains them only
    for candidates). Pure functional reconstruction: no mutation, no I/O.
    """

    oracle_matches = _resolve_target_nodes(oracle_doc, source_path)
    if not oracle_matches:
        # No oracle subtree to compare against — the residual is a target-absent
        # outcome the partition function already typed. There is no node pair to
        # classify; leave divergence_class unset (never a content candidate).
        return NZTargetDivergence(
            divergence_class=None,
            sub_families=(),
            non_commensurable_whole_node=False,
            oracle_descendant_count=0,
            node_pairs=(),
        )
    oracle_root = oracle_matches[0]
    oracle_descendants = _descendant_nodes(oracle_doc, oracle_root.path)
    candidate_sig = _normalized_subtree_signature(
        candidate_root, candidate_descendants, root_path=candidate_root.path, preserve_labels=preserve_labels
    )
    oracle_sig = _normalized_subtree_signature(
        oracle_root, oracle_descendants, root_path=oracle_root.path, preserve_labels=preserve_labels
    )
    # The signature key is ``(relative_path, kind)``. With label-stripped keys
    # (REPLACE) sibling nodes of the same kind at the same depth share one key; a
    # dict keyed on that would silently collapse such siblings, hiding a
    # node-COUNT difference and aligning two unrelated siblings (candidate (c) vs
    # an oracle (d) added later). Compare the key MULTISET first.
    candidate_keys = Counter((rel, kind) for rel, kind, _text in candidate_sig)
    oracle_keys = Counter((rel, kind) for rel, kind, _text in oracle_sig)

    if candidate_keys != oracle_keys:
        # The node sets (counts included) differ. For the REPLACE family this is
        # topological (oracle added / dropped / re-kinded / renumbered nodes) and
        # is typed structural_nodeset, conservatively. For the INSERT family the
        # labels are stable, so a node-set difference means the inserted section
        # was independently FURTHER-AMENDED (the oracle added or removed nodes
        # after the insert): instead of a blanket bail, align the COMMON
        # label-keys present on both sides and surface a localized substantive
        # divergence in a common node as a candidate. The added/removed-only keys
        # are recorded as a structural note (the residual is not "clean") but do
        # not block a localized in-common substantive finding.
        if not preserve_labels:
            return NZTargetDivergence(
                divergence_class=NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET,
                sub_families=(),
                non_commensurable_whole_node=False,
                oracle_descendant_count=len(oracle_descendants),
                node_pairs=(),
            )
        # Any repeated key on either side is still ambiguous (cannot pair the
        # duplicated siblings); bail to structural_nodeset for those.
        if any(count > 1 for count in candidate_keys.values()) or any(
            count > 1 for count in oracle_keys.values()
        ):
            return NZTargetDivergence(
                divergence_class=NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET,
                sub_families=(),
                non_commensurable_whole_node=False,
                oracle_descendant_count=len(oracle_descendants),
                node_pairs=(),
            )
        common_keys = set(candidate_keys) & set(oracle_keys)
        added_or_removed = (set(candidate_keys) ^ set(oracle_keys))
        return _classify_aligned_keys(
            candidate_sig=candidate_sig,
            oracle_sig=oracle_sig,
            oracle_root_kind=oracle_root.kind,
            oracle_descendant_count=len(oracle_descendants),
            restrict_to_keys=common_keys,
            structural_note_keys=added_or_removed,
            allow_localized_container=preserve_labels,
        )

    # Per-node text alignment is only well-defined when every ``(relative_path,
    # kind)`` key is UNIQUE on each side: a duplicated key (two same-kind siblings
    # at the same label-stripped depth) cannot be aligned to its counterpart
    # without guessing which sibling pairs with which. When any key repeats, type
    # the residual as a node-set divergence (conservative: leaves the candidate
    # set) rather than align ambiguous siblings. Label-preserving keys (INSERT)
    # make same-kind siblings distinct, so this guard practically only fires for
    # label-stripped REPLACE comparisons.
    if any(count > 1 for count in candidate_keys.values()):
        return NZTargetDivergence(
            divergence_class=NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET,
            sub_families=(),
            non_commensurable_whole_node=False,
            oracle_descendant_count=len(oracle_descendants),
            node_pairs=(),
        )

    return _classify_aligned_keys(
        candidate_sig=candidate_sig,
        oracle_sig=oracle_sig,
        oracle_root_kind=oracle_root.kind,
        oracle_descendant_count=len(oracle_descendants),
        restrict_to_keys=None,
        structural_note_keys=frozenset(),
        allow_localized_container=preserve_labels,
    )


def _classify_aligned_keys(
    *,
    candidate_sig: tuple[tuple[str, str, str], ...],
    oracle_sig: tuple[tuple[str, str, str], ...],
    oracle_root_kind: str,
    oracle_descendant_count: int,
    restrict_to_keys: set[tuple[str, str]] | None,
    structural_note_keys: set[tuple[str, str]] | frozenset[tuple[str, str]],
    allow_localized_container: bool,
) -> NZTargetDivergence:
    """Per-node text alignment + folding over uniquely-keyed signatures.

    ``restrict_to_keys`` (when not None) limits the alignment to the COMMON keys
    present on both sides (the INSERT further-amended path); ``None`` aligns the
    full key set (node sets already equal). ``structural_note_keys`` are the
    added/removed-only keys recorded as a structural note: their presence is
    folded into ``sub_families`` (so the residual is visibly not a clean
    node-for-node match) but never blocks a localized in-common substantive
    finding. The non-commensurable gate is keyed on the count of diverging
    DESCENDANT leaves (root excluded — its text aggregates its descendants); a
    pure-leaf target with no descendant divergence counts as one diverging unit.

    Contamination guard for the further-amended path: a common node whose
    relative-path is an ANCESTOR (path prefix) of any added/removed-only
    structural-note key has its aggregated text contaminated by the structural
    change (the source parser folds descendant text into the ancestor), so its
    divergence is a structural artifact, not a reliable content-error signal.
    Such contaminated common nodes are SKIPPED. When the only common divergences
    are contaminated (no clean leaf content divergence survives), the residual is
    conservatively typed structural_nodeset (refuse-don't-guess) rather than
    emitted as a false candidate from a re-lettered / further-amended container.
    """

    candidate_index = {(rel, kind): text for rel, kind, text in candidate_sig}
    oracle_index = {(rel, kind): text for rel, kind, text in oracle_sig}
    keys = restrict_to_keys if restrict_to_keys is not None else set(candidate_index)

    # Relative paths of the added/removed-only nodes. A common node is
    # "contaminated" when it is the root ("") or a path-prefix ancestor of any of
    # these — its folded text reflects the structural change, not a content edit.
    note_rel_paths = tuple(rel for rel, _kind in structural_note_keys)

    def _is_contaminated_by_structural_note(rel: str) -> bool:
        if not note_rel_paths:
            return False
        if rel == "":
            # The root aggregates every descendant; any added/removed node
            # contaminates it.
            return True
        for note_rel in note_rel_paths:
            if note_rel == rel or note_rel.startswith(rel + "/"):
                return True
        return False

    node_pairs: list[NZNodeDivergence] = []
    sub_families: list[str] = []
    all_editorial = True
    diverging_descendant_leaves = 0
    for key in keys:
        candidate_text = candidate_index[key]
        oracle_text = oracle_index[key]
        if candidate_text == oracle_text:
            continue
        rel, kind = key
        if _is_contaminated_by_structural_note(rel):
            # Skip a common node whose text difference is explained by the
            # added/removed descendant (structural artifact, not a content error).
            continue
        divergence = classify_oracle_divergence(candidate_text, oracle_text)
        node_pairs.append(
            NZNodeDivergence(
                relative_path=rel,
                kind=kind,
                candidate_text=candidate_text,
                oracle_text=oracle_text,
                sub_family=divergence.sub_family.value,
                is_editorial=divergence.is_editorial,
            )
        )
        sub_families.append(divergence.sub_family.value)
        if not divergence.is_editorial:
            all_editorial = False
        # The root node (relative_path == "") aggregates every descendant's text,
        # so it always "diverges" whenever any descendant does. Count only
        # descendant leaves for the pervasiveness gate.
        if rel != "":
            diverging_descendant_leaves += 1

    has_structural_note = bool(structural_note_keys)
    if has_structural_note:
        # Record the structural difference as a typed sub-family so the residual
        # is never silently presented as a clean node-for-node match.
        sub_families.append("structural_nodeset_partial")

    if not node_pairs:
        # Either the signatures agree node-for-node after normalization (a
        # residual keyed on a signal the signature folds out) or the only
        # difference was added/removed-only nodes (a pure structural note with no
        # in-common content divergence). In the latter case the residual is
        # topological; type it structural_nodeset. Otherwise editorial.
        if has_structural_note:
            return NZTargetDivergence(
                divergence_class=NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET,
                sub_families=tuple(sorted(sub_families)),
                non_commensurable_whole_node=False,
                oracle_descendant_count=oracle_descendant_count,
                node_pairs=(),
            )
        return NZTargetDivergence(
            divergence_class=NZ_DIVERGENCE_CLASS_EDITORIAL,
            sub_families=(),
            non_commensurable_whole_node=False,
            oracle_descendant_count=oracle_descendant_count,
            node_pairs=(),
        )

    if all_editorial:
        return NZTargetDivergence(
            divergence_class=NZ_DIVERGENCE_CLASS_EDITORIAL,
            sub_families=tuple(sorted(sub_families)),
            non_commensurable_whole_node=False,
            oracle_descendant_count=oracle_descendant_count,
            node_pairs=tuple(node_pairs),
        )

    # A pure-leaf target (no descendant diverged, only the root pair) is its own
    # diverging unit: count it as one so the pervasiveness gate treats a single
    # diverging leaf consistently whether or not the node has descendants.
    diverging_leaf_count = diverging_descendant_leaves or 1
    non_commensurable = _is_non_commensurable_whole_node(
        oracle_root_kind,
        oracle_descendant_count,
        diverging_leaf_count,
        allow_localized_container=allow_localized_container,
    )
    return NZTargetDivergence(
        divergence_class=NZ_DIVERGENCE_CLASS_SUBSTANTIVE,
        sub_families=tuple(sorted(sub_families)),
        non_commensurable_whole_node=non_commensurable,
        oracle_descendant_count=oracle_descendant_count,
        node_pairs=tuple(node_pairs),
    )


def _classify_oracle_text_divergence(
    oracle_doc: NZSourceDocument,
    source_path: tuple[str, ...],
    *,
    candidate_after_text: str,
) -> NZTargetDivergence:
    """Classify a TEXT_REPLACE residual's single-node candidate-vs-oracle text.

    The text-substitution family mutates one node's text (no subtree), so the
    divergence is a single candidate-after-node-text vs oracle-node-text pair
    typed by ``classify_oracle_divergence``. A text node is always a commensurable
    leaf (the substitution touched exactly one node), so the non-commensurable
    gate never fires here. Oracle target absent -> ``divergence_class = None``.
    """

    oracle_matches = _resolve_target_nodes(oracle_doc, source_path)
    if not oracle_matches:
        return NZTargetDivergence(
            divergence_class=None,
            sub_families=(),
            non_commensurable_whole_node=False,
            oracle_descendant_count=0,
            node_pairs=(),
        )
    oracle_node = oracle_matches[0]
    candidate_text = normalize_inline_comparison_text(candidate_after_text)
    oracle_text = normalize_inline_comparison_text(oracle_node.text)
    if candidate_text == oracle_text:
        # Normalized texts agree even though the substitution-parity partition
        # reported a residual: no surviving content divergence.
        return NZTargetDivergence(
            divergence_class=NZ_DIVERGENCE_CLASS_EDITORIAL,
            sub_families=(),
            non_commensurable_whole_node=False,
            oracle_descendant_count=0,
            node_pairs=(),
        )
    divergence = classify_oracle_divergence(candidate_text, oracle_text)
    sub_family = divergence.sub_family.value
    node_pair = NZNodeDivergence(
        relative_path="",
        kind=oracle_node.kind,
        candidate_text=candidate_text,
        oracle_text=oracle_text,
        sub_family=sub_family,
        is_editorial=divergence.is_editorial,
    )
    divergence_class = (
        NZ_DIVERGENCE_CLASS_EDITORIAL
        if divergence.is_editorial
        else (
            NZ_DIVERGENCE_CLASS_STRUCTURAL_NODESET
            if sub_family == "structural"
            else NZ_DIVERGENCE_CLASS_SUBSTANTIVE
        )
    )
    return NZTargetDivergence(
        divergence_class=divergence_class,
        sub_families=(sub_family,),
        non_commensurable_whole_node=False,
        oracle_descendant_count=0,
        node_pairs=(node_pair,),
    )


def _amending_act_root(
    archive: Any,
    amending_work_id: str,
    cache: dict[str, Any],
    *,
    findings_out: Optional[List[Finding]] = None,
) -> Any:
    if amending_work_id in cache:
        return cache[amending_work_id]
    from lxml import etree

    from lawvm.core.named_swallow import log_emitter, swallow_call
    from lawvm.new_zealand.dependencies import latest_xml_locator_for_work

    def _resolve_amending_root() -> Any:
        _version_id, locator = latest_xml_locator_for_work(archive, amending_work_id)
        data = archive.get(locator) if locator else None
        if data is not None:
            return etree.fromstring(data)
        return None

    # ``latest_xml_locator_for_work`` and ``etree.fromstring`` may raise across
    # archive/IO/parse paths. Previously ``except Exception: root = None``
    # silently swallowed to None (AGENTS.md §1.10 silent-fallback). Now routed
    # through ``swallow_call`` so a typed Finding is constructed carrying the
    # offending ``amending_work_id`` as ``source_artifact`` and ``clause_text``
    # (the work-id, truncated 400). Sink dispatch mirrors corpus.py:122
    # named_swallow precedent: when ``findings_out`` is plumbed, the Finding
    # lands in that per-statute audit-trail list; when not, ``log_emitter``
    # keeps stderr WARNING visibility so the swallow is still observed at this
    # archive-boundary probe. On swallow returns None so the dry-run cache
    # stays miss-shaped and the consumer detects the missing amending work via
    # its existing None path.
    #
    # Structural gap (iter4 W2 STOP-and-report): no NZ production caller
    # threads ``findings_out`` because the upstream call-chain
    # (``_dry_run_one_X`` -> ``build_dry_run_X`` -> ``NZDryRunReport``,
    # and ``_extract_X_payload`` -> ``_apply_X_op`` -> ``_apply_transition``
    # -> ``build_chain_replay``) does not carry a ``list[Finding]`` ledger;
    # widening signatures across that 5+ site chain is out-of-scope for W2.
    # The swallow falls through to ``log_emitter`` stderr WARNING
    # (sanctioned IO/utility carve-out per ``core/named_swallow.py``) — never silent.
    emit = None if findings_out is not None else log_emitter()
    root: Any = swallow_call(
        _resolve_amending_root,
        rule_id="nz_dry_run_amending_act_root",
        default=None,
        jurisdiction="nz",
        source_artifact=amending_work_id,
        clause_text=f"amending_work_id={amending_work_id[:400]}",
        emit=emit,
        findings_out=findings_out,
    )
    cache[amending_work_id] = root
    return root


def _amending_node_by_href(amending_root: Any, href: str) -> Any:
    if not href:
        return None
    for element in amending_root.iter():
        if isinstance(element.tag, str) and element.attrib.get("id") == href:
            return element
    return None


def _amend_provision_composes_target(amending_node: Any, target_leaf_label: str) -> bool:
    """Whether the amending provision re-touches the target in a later step.

    NZ amending sections decompose into numbered instruction steps (``subprov``).
    The structural-replace extractor reads exactly ONE step — the structured
    "<…> is replaced by the following:" step carrying the ``<amend>`` quote. When
    a LATER step in the SAME provision performs a further substitution on the same
    target leaf (e.g. "(3) In the definition of <leaf>,— … is replaced by …"), the
    extracted step is the INTERMEDIATE state, not the provision's net effect, so a
    residual against the (net-effect) oracle snapshot is a composition artifact,
    not an oracle error.

    Detection is identity-only and conservative: a step COMPOSES the target when
    it (a) mentions the target leaf label and (b) is phrased as a replacement
    ("replaced"/"substituted"). At least TWO such steps (the structured replace
    plus a further substitution) must be present — a single step is the plain
    whole-replacement and never composes. No content is sourced and the step
    semantics are never interpreted; only the target's own label and a
    replacement keyword are matched. Returns False when the label is empty or the
    node is unreadable (fail-open to the existing classification, never a guess).
    """

    if not target_leaf_label:
        return False
    label = normalize_inline_comparison_text(target_leaf_label)
    if not label:
        return False

    steps: list[str] = []
    try:
        for subprov in amending_node.iter():
            tag = getattr(subprov, "tag", "")
            if not isinstance(tag, str) or not tag.endswith("subprov"):
                continue
            text = normalize_inline_comparison_text(
                " ".join(t for t in subprov.itertext() if t and t.strip())
            )
            steps.append(text)
    except (AttributeError, TypeError):
        return False

    replace_keywords = ("replaced", "substituted")
    composing = [
        text
        for text in steps
        if label in text and any(kw in text for kw in replace_keywords)
    ]
    return len(composing) >= 2


# A target-citation as it appears at the head of an amending instruction step:
# "section 6(1)(a)", "Section 358(1)", "section 6(1)(c) and (2)(a)". We parse the
# LEADING citation's section number plus its bracketed sub-components into a
# label-path tuple ("6", "1", "a"). Trailing "and (2)(a)" alternatives are NOT
# parsed — a step that lists several targets is matched on its first target only,
# which is conservative for overlap (the first target is the primary one and any
# overlap with it already gates; missing a secondary target can only UNDER-gate,
# never over-gate).
_INSTRUCTION_TARGET_SECTION_RE = re.compile(
    r"\bsections?\s+(\d+[A-Za-z]*)((?:\s*﻿?\([0-9A-Za-z]+\))*)",
    re.IGNORECASE,
)
_INSTRUCTION_TARGET_BRACKET_RE = re.compile(r"\(([0-9A-Za-z]+)\)")


def _instruction_target_label_path(citation_text: str) -> tuple[str, ...] | None:
    """Parse an instruction-step target citation into a label-path tuple.

    "section 6(1)(a)" -> ("6", "1", "a"); "Section 358(1)" -> ("358", "1");
    "section 6" -> ("6",). Returns ``None`` when no leading section citation
    parses (the step's target cannot be located, so it cannot be proven to
    overlap — conservative: it is not counted). Only the FIRST section citation in
    the text is read (the step's primary target).
    """

    match = _INSTRUCTION_TARGET_SECTION_RE.search(citation_text)
    if not match:
        return None
    section = match.group(1)
    brackets = tuple(_INSTRUCTION_TARGET_BRACKET_RE.findall(match.group(2) or ""))
    return (section, *brackets)


def _paths_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether two label-paths are equal or one is an ancestor of the other.

    Overlap means the two instructions touch the SAME node or one touches an
    enclosing scope of the other (an each-place insert into "section 6(1)" reaches
    every paragraph of 6(1), including "6(1)(a)"). Sibling/cousin paths
    ("6(1)(a)" vs "6(1)(c)") do NOT overlap.
    """

    shorter = min(len(a), len(b))
    return a[:shorter] == b[:shorter]


def _node_label_path(source_path: tuple[str, ...]) -> tuple[str, ...]:
    """The prov/subprov/paragraph label chain of a resolved source path.

    "part:1/prov:6/subprov:1/label-para:a" -> ("6", "1", "a"). Container segments
    without a label and non-provision segments (``part``/``schedule``) are
    dropped, so the result is comparable to a parsed instruction-target citation.
    """

    labels: list[str] = []
    for segment in source_path:
        kind, _, label = segment.partition(":")
        if kind in {"prov", "subprov", "label-para", "def-para"} and label:
            labels.append(normalize_inline_comparison_text(label))
    return tuple(labels)


def _amend_provision_overlaps_target_in_other_step(
    amending_node: Any,
    node_source_path: tuple[str, ...],
) -> bool:
    """Whether another instruction step in the provision composes this node.

    A single amending provision (one history-note href) decomposes into numbered
    instruction steps. We extract exactly ONE step as the op's payload, but when
    ANOTHER step in the same provision targets the SAME node or an ENCLOSING scope
    of it (a per-section each-place "after smoking, insert or vaping in section
    6(1)" reaching paragraph 6(1)(a); a later "add" step appending a sentence to
    section 358(1) after the omit/substitute step), the oracle snapshot reflects
    the COMPOSED net effect while our payload is the intermediate state. The
    residual is then a composition artifact, not an oracle error.

    Detection is by target-citation OVERLAP and is conservative: we parse each
    step's leading target citation into a label-path and count how many steps
    overlap the replayed node's label-path (equal / ancestor / descendant). The
    op's own step always overlaps, so MORE THAN ONE overlapping step means a
    second instruction composes the node. Verb-agnostic (replace / omit / add /
    insert all compose). No content is sourced; an unparseable citation is simply
    not counted (fail toward NOT gating, never a guess). Returns False when the
    node path has no provision labels or the node is unreadable.
    """

    node_labels = _node_label_path(node_source_path)
    if not node_labels:
        return False

    overlapping = 0
    try:
        for subprov in amending_node.iter():
            tag = getattr(subprov, "tag", "")
            if not isinstance(tag, str) or not tag.endswith("subprov"):
                continue
            text = normalize_inline_comparison_text(
                " ".join(t for t in subprov.itertext() if t and t.strip())
            )
            target = _instruction_target_label_path(text)
            if target is None:
                continue
            if _paths_overlap(target, node_labels):
                overlapping += 1
    except (AttributeError, TypeError):
        return False

    return overlapping >= 2


def _replace_payload_text(replacement: NZStructuralReplacement, row: Any) -> str:
    return (
        f"action={StructuralAction.REPLACE} "
        f"payload=structural_replace amending={row.amending_work_id} "
        f"replacement_root={replacement.root.kind}:{replacement.root.label} "
        f"descendants={len(replacement.descendants)}"
    )


def _residual_family(oracle_match: str) -> AgreementResidualFamily:
    """Map an oracle-match outcome to an :class:`AgreementResidual` family.

    The text-substitution residuals (old_text remains / new_text absent) are
    classified as ``oracle_editorial_pathology`` — the same shared residual
    family the repeal path uses for an oracle node that exists but does not
    reflect the expected change — so this surface never invents a core type.
    """

    if oracle_match in {"target_missing", "target_recovery_mismatch", "residual_insert_not_present"}:
        return "target_recovery_mismatch"
    return "oracle_editorial_pathology"


def _oracle_partition(
    oracle_doc: NZSourceDocument,
    source_path: tuple[str, ...],
    *,
    target_kind: str = "",
) -> tuple[str, str, bool, str]:
    oracle_matches = _resolve_target_nodes(oracle_doc, source_path)
    if target_kind == _REMOVAL_ON_REPEAL_SOURCE_KIND:
        # NZ removes a repealed definition (``def-para``) from the consolidated
        # text rather than tombstoning it. An absent node is therefore the
        # agreeing outcome; a still-present node is the residual.
        if not oracle_matches:
            return (
                "agrees",
                NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID,
                False,
                "absent",
            )
        oracle_occupancy = _occupancy(oracle_matches[0])
        return (
            "target_not_removed",
            NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID,
            True,
            oracle_occupancy,
        )
    if not oracle_matches:
        # NZ consolidations preserve repealed-but-addressable tombstones, so a
        # missing node is a residual, not an agreement.
        return ("target_missing", NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID, False, "absent")
    oracle_node = oracle_matches[0]
    oracle_occupancy = _occupancy(oracle_node)
    if oracle_occupancy == "tombstone":
        return (
            "agrees",
            NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
            True,
            oracle_occupancy,
        )
    return (
        "target_not_tombstone",
        NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
        True,
        oracle_occupancy,
    )


def _leaf_source_kind(source_path: tuple[str, ...]) -> str:
    if not source_path:
        return ""
    leaf = source_path[-1]
    for separator in (":", "@", "#"):
        if separator in leaf:
            return leaf.split(separator, 1)[0]
    return leaf


def _source_path_for_address(operation: LegalOperation) -> tuple[str, ...] | None:
    segments: list[str] = []
    for kind, label in operation.target.path:
        source_kind = _ADDRESS_KIND_TO_SOURCE_KIND.get(kind)
        if source_kind is None or not label:
            return None
        segments.append(f"{source_kind}:{label}")
    if not segments:
        return None
    return tuple(segments)


def _nodes_at_path(document: NZSourceDocument, path: tuple[str, ...]) -> tuple[NZSourceNode, ...]:
    return tuple(node for node in document.nodes if node.path == path)


# Source zones that are NOT the live consolidated text: end-of-document
# amendment skeletons and front/end history. A repeal target must resolve into
# the live body (or a schedule), never into a skeleton copy.
_NON_BODY_SOURCE_ZONES = frozenset({"end_skeleton", "front_history", "end_history"})


def _is_leading_part_segment(path_segment: str) -> bool:
    """A node-path first segment that represents an enclosing ``<part>`` wrapper.

    Two shapes the source-tree parser emits for the enclosing ``<part>`` of a
    provision-bearing body:

    * ``part:N`` -- the labeled shape (``<part><label>N</label>...``).
    * ``part@DLM_xml_id`` -- the unlabeled fallback shape emitted when a
      ``<part>`` element lacks a parseable ``<label>`` (the parser falls back
      to the part's ``xml_id`` to disambiguate the wrapper). The legacy
      ``act_public_1981_23_en_2007-09-03`` snapshot has ~199 nodes whose path
      carries such an ``@``-keyed leading segment.

    Accepting both as the leading-1-extra tolerance is a strict-superset additive
    widening that mirrors the historical ``part:N`` semantics (a ``<part>``
    wrapper present in the parsed body's path but absent in the address-derived
    source_path); it closes the deterministic gap on the
    ``amendment_skipped_target_absent`` bucket where a chain-replay op resolves
    the clean ``prov:N``-form path but the carried tree (built off the earliest
    archived snapshot) has the unlabeled-``part`` wrapper. See
    ``notes/IMPLEMENTATION_DIVERGENCE_LEDGER.md``'s
    ``amendment_skipped_target_absent -- classified 2026-06-22`` section.
    """
    if not path_segment:
        return False
    # ``part:N`` (label-keyed) shape: split(':',1)[0] yields 'part'.
    if path_segment.split(":", 1)[0] == "part":
        return True
    # ``part@DLM_xml_id`` (xml_id-keyed fallback) shape: starts with ``part@``.
    # The unlabeled-fallback segment carries no ':' so the prior narrow predicate
    # silently rejected it.
    return path_segment.startswith("part@")


def _resolve_target_nodes(
    document: NZSourceDocument,
    source_path: tuple[str, ...],
) -> tuple[NZSourceNode, ...]:
    """Resolve an address-derived source path to live-body node(s).

    History-note ``amended-provision`` references omit the enclosing ``part``
    (e.g. "Section 2(1)"), while the parsed body nests provisions under their
    part (``part:1/prov:2/subprov:1``). We therefore accept a node whose path
    equals the address path exactly OR equals it with one extra leading
    ``part`` wrapper segment (either the labeled ``part:N`` form OR the
    unlabeled ``part@DLM_xml_id`` fallback form emitted when the ``<part>``
    element lacks a parseable ``<label>`` -- see :func:`_is_leading_part_segment`),
    but only in the live body -- never an end-of-document skeleton copy, which
    would resolve substantively for a node that is in fact repealed in the
    body. The caller still requires exactly one match; an empty or ambiguous
    result is a typed refusal, never a coarse-parent fallback.
    """
    matches: list[NZSourceNode] = []
    for node in document.nodes:
        if node.source_zone in _NON_BODY_SOURCE_ZONES:
            continue
        if node.path == source_path:
            matches.append(node)
        elif (
            len(node.path) == len(source_path) + 1
            and _is_leading_part_segment(node.path[0])
            and node.path[1:] == source_path
        ):
            matches.append(node)
    return tuple(matches)


def _sibling_nodes(document: NZSourceDocument, path: tuple[str, ...]) -> tuple[NZSourceNode, ...]:
    if not path:
        return ()
    parent = path[:-1]
    return tuple(
        node
        for node in document.nodes
        if node.path != path and node.path[:-1] == parent and len(node.path) == len(path)
    )


def _child_nodes_of_kind(
    document: NZSourceDocument,
    parent_path: tuple[str, ...],
    kind: str,
) -> tuple[NZSourceNode, ...]:
    """Direct child nodes of ``parent_path`` whose ``kind`` matches, in document order."""

    depth = len(parent_path)
    return tuple(
        node
        for node in document.nodes
        if len(node.path) == depth + 1
        and node.path[:depth] == parent_path
        and node.kind == kind
    )


def _top_level_sibling_labels(document: NZSourceDocument, kind: str) -> tuple[str, ...]:
    """Labels of top-level same-kind provisions in the live body, document order.

    A whole-provision insert (``new_node_source_path`` has one segment) lands
    among the top-level provisions of its kind. Those provisions are pathed
    either at the root (``prov:6``) or under a single part (``part:1/prov:6``) —
    the same one-extra-leading-``part`` shape that :func:`_resolve_target_nodes`
    accepts for a single-segment address. Collect both shapes from the live body
    only (never an end-of-document skeleton copy), so the derived predecessor is
    validated against the real consolidated sibling group.
    """

    labels: list[str] = []
    for node in document.nodes:
        if node.source_zone in _NON_BODY_SOURCE_ZONES:
            continue
        if node.kind != kind or not node.label:
            continue
        path = node.path
        if len(path) == 1:
            labels.append(node.label)
        elif len(path) == 2 and path[0].split(":", 1)[0] == "part":
            labels.append(node.label)
    return tuple(labels)


def _resolved_group_labels(
    document: NZSourceDocument,
    anchor_path: tuple[str, ...],
    kind: str,
) -> frozenset[str]:
    """Same-kind sibling labels in the before tree under the anchor's parent.

    The anchor node was resolved to a concrete before-tree path; its same-kind
    siblings under the shared parent are the existing members of the group the
    new node joins. Used to subtract genuinely-pre-existing labels from the
    co-inserted-block set so only NEW co-members can extend the accepted-position
    set in the oracle position check.
    """

    parent = anchor_path[:-1]
    siblings = _child_nodes_of_kind(document, parent, kind)
    return frozenset(node.label for node in siblings if node.label)


def _occupancy(node: NZSourceNode) -> str:
    if node.deletion_status:
        return "tombstone"
    return "substantive"


def _tombstone_node(node: NZSourceNode) -> NZSourceNode:
    # Boring kernel: keep the node addressable (same kind/path/xml_id/label/
    # heading), mark it repealed. Do not delete-and-forget.
    return NZSourceNode(
        kind=node.kind,
        path=node.path,
        xml_id=node.xml_id,
        xml_path=node.xml_path,
        source_zone=node.source_zone,
        label=node.label,
        heading=node.heading,
        deletion_status=_REPEAL_TOMBSTONE_DELETION_STATUS,
        text=node.text,
        history=node.history,
    )


def _node_digest(node: NZSourceNode) -> str:
    payload = "".join(
        (
            node.kind,
            "/".join(node.path),
            node.xml_id,
            node.label,
            node.heading,
            node.deletion_status,
            node.text,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _operation_payload_text(operation: LegalOperation) -> str:
    return f"action={operation.action} witness_rule_id={operation.witness_rule_id or ''} payload=tombstone"


def _parse_archived_version(
    archive: Any,
    version: NZArchivedVersion,
    parsed_cache: dict[str, NZSourceDocument | None],
) -> NZSourceDocument | None:
    locator = version.xml_locator
    if locator in parsed_cache:
        return parsed_cache[locator]
    data = archive.get(locator) if locator else None
    document: NZSourceDocument | None
    if data is None:
        document = None
    else:
        document = parse_nz_source_document(data, xml_locator=locator, version_id=version.version_id)
    parsed_cache[locator] = document
    return document


def _change_window_detail(window: NZArchivedVersionChangeWindow) -> dict[str, Any]:
    return {
        "requested_version_date": window.requested_version_date,
        "before_version_id": window.before.version_id if window.before else "",
        "on_or_after_version_id": window.on_or_after.version_id if window.on_or_after else "",
    }


def _amendment_date_census(rows: Iterable[Any]) -> frozenset[tuple[str, str]]:
    """The work's ``(amendment_date_iso, amending_work_id)`` operation witnesses.

    This is the set of distinct dated amendment instructions the work carries, as
    declared by its own operation/effect-candidate witnesses. It is used only as
    an identity census to count how many distinct amending works share an op's
    version window; it never sources content. A witness without an ISO amendment
    date contributes nothing (it has no place on the timeline). The amending work
    id may be empty for some witnesses; an empty id is still a distinct census key
    so an unattributed amendment in the window is counted conservatively (it
    cannot prove sole authorship).
    """

    census: set[tuple[str, str]] = set()
    for row in rows:
        date = str(getattr(row, "amendment_date_iso", "") or "")
        if not date:
            continue
        amender = str(getattr(row, "amending_work_id", "") or "")
        census.add((date, amender))
    return frozenset(census)


def _distinct_amenders_in_window(
    census: frozenset[tuple[str, str]],
    *,
    before_date: str,
    on_or_after_date: str,
) -> int:
    """Count distinct amending works with an effect date in the op's window.

    The window is the half-open interval ``(before_date, on_or_after_date]`` —
    every amendment whose effect date is strictly after the before snapshot and
    on-or-before the chosen oracle snapshot. The snapshot composes them all, so a
    count greater than one means the snapshot is not the sole product of this op's
    amendment. Dates are compared as ISO strings (lexical == chronological).
    """

    return len(
        {
            amender
            for (date, amender) in census
            if before_date < date <= on_or_after_date
        }
    )


def _structural_node_set(
    root: NZSourceNode,
    descendants: tuple[NZSourceNode, ...],
    *,
    root_path: tuple[str, ...],
) -> Counter[str]:
    """A label-stripped, text-ignoring structural node-set of a subtree.

    Each node contributes its kind-only relative path (labels, ``@`` selectors,
    and auto-generated ``#`` ids dropped, mirroring
    :func:`_normalized_subtree_signature`). The result is a multiset (Counter) so
    a paragraph added or removed by another amendment changes the count. Text is
    deliberately ignored — this captures STRUCTURE only, which a pure inline text
    substitution can never change.
    """

    depth = len(root_path)

    def relative(path: tuple[str, ...]) -> str:
        rel = path[depth:]
        return "/".join(
            segment.split(":", 1)[0].split("@", 1)[0].split("#", 1)[0] for segment in rel
        )

    entries = [("", root.kind)]
    entries.extend((relative(node.path), node.kind) for node in descendants)
    return Counter(f"{rel}|{kind}" for rel, kind in entries)


def _prove_temporal_window_fit(
    *,
    amendment_census: frozenset[tuple[str, str]],
    change_window: NZArchivedVersionChangeWindow,
    oracle_present: bool,
    target_digest_before: str = "",
    target_digest_after: str = "",
    oracle_target_digest: str = "",
    before_structural_set: Counter[str] | None = None,
    oracle_structural_set: Counter[str] | None = None,
) -> tuple[bool, str]:
    """Prove (or refuse to prove) the snapshot reflects EXACTLY this op.

    Returns ``(unprovable, reason)``. ``unprovable`` is True when the chosen
    oracle snapshot cannot be proven contemporaneous with this op's amendment; the
    reason is one of the module-level ``NZ_WINDOW_UNPROVABLE_*`` codes. See the
    module window-fit note for the three failure modes. Conservative: any failure
    to prove sole authorship types the residual out of the candidate set.

    The proof is only meaningful for a residual whose oracle target is present
    (an absent oracle target is already handled by the partition function and is
    never a substantive content candidate). For an absent oracle target this
    returns provable (False) so the existing absent-target typing is unchanged.
    """

    before = change_window.before
    on_or_after = change_window.on_or_after
    before_date = before.version_date if before else ""
    on_or_after_date = on_or_after.version_date if on_or_after else ""

    # shared_window: more than one distinct amending work in the op's window.
    if before_date and on_or_after_date:
        if (
            _distinct_amenders_in_window(
                amendment_census,
                before_date=before_date,
                on_or_after_date=on_or_after_date,
            )
            > 1
        ):
            return True, NZ_WINDOW_UNPROVABLE_SHARED_WINDOW

    if not oracle_present:
        return False, ""

    # snapshot_predates_op: the op mutated the before node, yet the oracle node is
    # byte-identical to the before node — the op's effect is wholly absent from
    # the snapshot, so the snapshot predates this op's true effect date.
    if (
        target_digest_before
        and target_digest_after
        and target_digest_before != target_digest_after
        and oracle_target_digest
        and oracle_target_digest == target_digest_before
    ):
        return True, NZ_WINDOW_UNPROVABLE_SNAPSHOT_PREDATES_OP

    # structural_drift: a pure inline text substitution cannot add or remove
    # paragraphs, so any structural node-set difference between the before
    # snapshot and the oracle snapshot is another amendment's restructuring.
    if before_structural_set is not None and oracle_structural_set is not None:
        if before_structural_set != oracle_structural_set:
            return True, NZ_WINDOW_UNPROVABLE_STRUCTURAL_DRIFT

    return False, ""


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__none__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def scope_from_arg(value: str | None) -> str:
    """Normalize a CLI scope token (dash form) to the internal scope constant.

    ``None`` / empty -> the default ``complete_set`` so the existing behavior is
    preserved. An unknown token raises so it can never silently degrade to the
    relaxed scope.
    """

    if not value:
        return NZ_DRY_RUN_SCOPE_COMPLETE_SET
    normalized = value.replace("-", "_")
    if normalized not in _VALID_DRY_RUN_SCOPES:
        raise ValueError(f"unknown dry-run scope {value!r}; expected one of {_VALID_DRY_RUN_SCOPES}")
    return normalized


def main(args: Any) -> None:
    import json

    scope = scope_from_arg(getattr(args, "scope", None))
    report = build_archived_work_dry_run_repeal(Path(args.db), args.work_id, scope=scope)
    if args.json:
        print(json.dumps(report.to_jsonable(summary_only=args.summary_only), ensure_ascii=False, indent=2))
        return
    summary = report.summary()
    completeness = summary.get("scope_completeness")
    print(f"scope={summary['scope']}")
    if completeness:
        print(
            f"scope_completeness is_partial={completeness['is_partial']} "
            f"family={completeness['family']} "
            f"in_scope={completeness['in_scope_operation_witnesses']} "
            f"not_in_scope={completeness['not_in_scope_operation_witnesses']} "
            f"of_total={completeness['total_operation_witnesses']} "
            f"not_in_scope_reasons={completeness['not_in_scope_reason_counts']}"
        )
    print(
        f"work_id={summary['work_id']} preflight_status={summary['preflight_status']} "
        f"operations_dry_run={summary['operations_dry_run']} "
        f"operations_refused={summary['operations_refused']} "
        f"dry_run_oracle_agreements={summary['dry_run_oracle_agreements']} "
        f"dry_run_oracle_residuals={summary['dry_run_oracle_residuals']} "
        f"neighbors_unchanged_all={summary['neighbors_unchanged_all']}"
    )
    print(f"actual_replay_blocking_rule_id={NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID}")
    if summary["refusal_rule_counts"]:
        print(f"refusal_rule_counts={summary['refusal_rule_counts']}")
    if args.summary_only:
        return
    for proof in report.proofs:
        print(
            f"PROOF\t{proof.op_id}\t{proof.target_address}\t"
            f"{proof.occupancy_before}->{proof.occupancy_after}\t"
            f"oracle={proof.oracle_match}({proof.oracle_target_occupancy})\t"
            f"neighbors_unchanged={proof.neighbors_unchanged}"
        )
        print(
            f"\tdigest_before={proof.target_digest_before[:12]} "
            f"digest_after={proof.target_digest_after[:12]} "
            f"oracle_version={proof.oracle_version_id}"
        )
    for refusal in report.refusals:
        print(f"REFUSED\t{refusal.op_id}\t{refusal.rule_id}\t{refusal.target_address or '-'}")
