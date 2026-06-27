"""EE guard-liveness ratchet tests.

Mirrors the FI guard-liveness discipline (AGENTS.md §2.9) for the Estonia
frontend: every blocking ``CompileAdjudication`` rule_id is either
exercised by a fire-drill (production-path test through ``replay_ee_to_pit``)
or explicitly admitted as a debt row in ``EE_NO_FIRE_DRILL_YET`` with a
stated reason and last-reviewed date.

The worst failure class is a guard that exists but is unreachable from
production: it looks real, passes review, and creates false confidence.
This ratchet makes silent-guard additions a CI failure rather than a
deferred smell.

EE does NOT use FI's ``FINDING_REGISTRY`` finding-registry carrier; it emits
blocking adjudications through ``replay_adjudication.CompileAdjudication``
with ``blocking=True`` and a ``kind=rule_id`` string identifier. This test
suite is the EE analog of ``tests/test_fi_guard_liveness.py``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict

import pytest

from lawvm.core.ir import IRStatute, IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.estonia.guard_liveness import (
    EE_BLOCKING_RULE_IDS,
    EE_FIRE_DRILL_COVERAGE,
    EE_NO_FIRE_DRILL_CEILING,
    EE_NO_FIRE_DRILL_YET,
    enumerate_ee_blocking_rule_ids,
)
from lawvm.estonia.grafter import apply_ee_ops
from lawvm.replay_adjudication import CompileAdjudication

EE_SRC = Path(__file__).resolve().parent.parent / "src" / "lawvm" / "estonia"


def _body_with_section(section_label: str, subsection_label: str, text: str) -> IRNode:
    """Minimal IRNode body for synthetic fire-drills: a single chapter →
    section → subsection tree. Small enough that the unsupported-action
    dispatch is the only code path likely to fire on a drill op."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label=section_label,
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label=subsection_label, text=text),),
                    ),
                ),
            ),
        ),
    )


def _drill_statute(section_label: str = "1", subsection_label: str = "1") -> IRStatute:
    """A minimal IRStatute suitable for synthetic dispatch drills."""
    body = _body_with_section(section_label, subsection_label, "drill body.")
    return IRStatute(
        statute_id="ee/fire_drill",
        title="Fire Drill Base",
        body=body,
    )


def _unsupported_action_op(
    *,
    sequence: int = 1,
    target_path: tuple = (("section", "1"),),
) -> LegalOperation:
    """Construct a LegalOperation with an action ``apply_ee_ops`` does not
    dispatch (HEADING_REPLACE). The op carries a non-statute-title target so
    the statute-title branch does not absorb it; it falls through to the
    ``ee_replay_unsupported_action`` blocking emit at
    ``src/lawvm/estonia/grafter.py:10360``.
    """
    return LegalOperation(
        op_id="ee_fire_drill_unsupported_action",
        sequence=sequence,
        action=StructuralAction.HEADING_REPLACE,
        target=LegalAddress(path=target_path),
        source=OperationSource(
            statute_id="ee/fire_drill_amendment",
            title="Fire Drill Amendment",
            enacted="2025-01-01",
            effective="2025-01-01",
            raw_text="",
        ),
        payload=None,
    )


def _target_not_found_op(
    *,
    sequence: int = 1,
) -> LegalOperation:
    """Construct a LegalOperation whose REPLACE target path resolves to no
    node in the fixture body (section:999 — the body only has section:1).
    The op is dispatched (action=REPLACE is in the whitelist), then
    ``_ee_apply_op`` is a no-op on the unresolvable target, then
    ``target_resolved = _ee_resolve_full_path(pre_op_body, target_path) is
    None`` triggers the ``ee_replay_target_not_found`` blocking emit at
    ``src/lawvm/estonia/grafter.py:10427``.
    """
    return LegalOperation(
        op_id="ee_fire_drill_target_not_found",
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "999"),)),
        source=OperationSource(
            statute_id="ee/fire_drill_amendment",
            title="Fire Drill Amendment",
            enacted="2025-01-01",
            effective="2025-01-01",
            raw_text="",
        ),
        payload=IRNode(kind=IRNodeKind.SECTION, label="999", text="drilled replacement"),
    )


def _statute_title_noop_op(
    *,
    sequence: int = 1,
    statute_title: str,
) -> LegalOperation:
    """Construct a LegalOperation that REPLACEs the statute-title address
    with a payload whose text equals the current statute title.

    This triggers the ``ee_replay_statute_title_noop`` blocking emit at
    ``src/lawvm/estonia/grafter.py:10351``: the dispatcher reaches the
    ``is_statute_title_address`` branch, applies the title replacement,
    detects ``new_title == old_title``, and emits the blocking noop.
    """
    from lawvm.core.statute_facets import statute_title_address

    return LegalOperation(
        op_id="ee_fire_drill_statute_title_noop",
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=statute_title_address(),
        source=OperationSource(
            statute_id="ee/fire_drill_amendment",
            title="Fire Drill Amendment",
            enacted="2025-01-01",
            effective="2025-01-01",
            raw_text="",
        ),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=statute_title,
        ),
    )


def _statute_title_unsupported_action_op(
    *,
    sequence: int = 1,
) -> LegalOperation:
    """Construct a LegalOperation that targets the statute-title address
    with a non-replace action (REPEAL — ``repeal`` is in the whitelist but
    not at the statute-title address). The dispatcher hits the
    ``is_statute_title_address`` branch, falls into the ``action != replace``
    sub-branch, and emits ``ee_replay_unsupported_statute_title_action`` at
    ``src/lawvm/estonia/grafter.py:10338``.
    """
    from lawvm.core.statute_facets import statute_title_address

    return LegalOperation(
        op_id="ee_fire_drill_statute_title_unsupported",
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=statute_title_address(),
        source=OperationSource(
            statute_id="ee/fire_drill_amendment",
            title="Fire Drill Amendment",
            enacted="2025-01-01",
            effective="2025-01-01",
            raw_text="",
        ),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="impossible title-repeal"),
    )


# ---------------------------------------------------------------------------
# Partition: every blocking code is either drilled or debt-admitted
# ---------------------------------------------------------------------------


def test_ee_blocking_code_inventory_is_fully_partitioned() -> None:
    """Golden gate: BLOCKING == FIRE_DRILLS | NO_FIRE_DRILL_YET.

    A blocking code discovered in source that is neither drilled nor debt-
    admitted is a silent-guard failure. A debt-admitted code that no longer
    appears as a blocking emit in source is a stale debt row. Both are
    fatal here.
    """
    blocking = enumerate_ee_blocking_rule_ids()
    covered = EE_FIRE_DRILL_COVERAGE
    debt = set(EE_NO_FIRE_DRILL_YET)
    accounted = covered | debt
    unaccounted = blocking - accounted
    orphan_debt = debt - blocking
    orphan_drills = covered - blocking
    assert not unaccounted, (
        "Blocking EE rule_ids lack both a fire-drill and a NO_FIRE_DRILL_YET "
        "admission (silent-guard). Either write a drill or admit the debt:\n  "
        + "\n  ".join(sorted(unaccounted))
    )
    assert not orphan_debt, (
        "NO_FIRE_DRILL_YET lists blocking rule_ids that are no longer in "
        "EE_BLOCKING_RULE_IDS — the blocking emit was removed. Remove the "
        "stale debt row:\n  " + "\n  ".join(sorted(orphan_debt))
    )
    assert not orphan_drills, (
        "EE_FIRE_DRILL_COVERAGE lists rule_ids that are no longer blocking. "
        "Remove the stale drill:\n  " + "\n  ".join(sorted(orphan_drills))
    )


def test_ee_blocking_set_equals_fire_drills_union_allowlist() -> None:
    """Ratchet (Gate 1b): BLOCKING == FIRE_DRILLS | NO_FIRE_DRILL_YET exactly.

    Stronger than the partition gate: asserts the union is *exactly* the
    blocking set. A drill might target a code already debt-admitted (both
    entries for the same rule_id), which is fine — but the union must equal
    blocking. A new blocking code cannot silently enter either side without
    being accounted.
    """
    blocking = enumerate_ee_blocking_rule_ids()
    drills_or_allowlist = EE_FIRE_DRILL_COVERAGE | set(EE_NO_FIRE_DRILL_YET)
    assert blocking == drills_or_allowlist


# ---------------------------------------------------------------------------
# Debt ceiling: the allowlist may shrink but never silently grow
# ---------------------------------------------------------------------------


def test_ee_blocking_code_ceiling_never_grows() -> None:
    """The committed monotone-decreasing ceiling over NO_FIRE_DRILL_YET.

    The allowlist may shrink (a drill is built and the entry removed: pay
    down debt) but may never grow past the ceiling. To admit new debt you
    must first pay down existing debt; the allowlist cannot silently grow.
    """
    assert len(EE_NO_FIRE_DRILL_YET) <= EE_NO_FIRE_DRILL_CEILING


# ---------------------------------------------------------------------------
# Debt shape: each NO_FIRE_DRILL_YET row is well-formed
# ---------------------------------------------------------------------------


def test_ee_no_fire_drill_allowlist_entries_are_well_formed_debt() -> None:
    """Each NO_FIRE_DRILL_YET row is a (reason, last_reviewed_date) tuple
    where the reason is non-empty and the date is YYYY-MM-DD.
    """
    for code, entry in EE_NO_FIRE_DRILL_YET.items():
        assert isinstance(entry, tuple), (
            f"NO_FIRE_DRILL_YET[{code!r}] must be a (reason, last_reviewed) tuple, "
            f"got {type(entry).__name__!r}"
        )
        assert len(entry) == 2, (
            f"NO_FIRE_DRILL_YET[{code!r}] must be a (reason, last_reviewed) tuple of length 2"
        )
        reason, last_reviewed = entry
        assert isinstance(reason, str) and reason, (
            f"NO_FIRE_DRILL_YET[{code!r}] has an empty reason"
        )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", last_reviewed), (
            f"NO_FIRE_DRILL_YET[{code!r}] last_reviewed must be YYYY-MM-DD, "
            f"got {last_reviewed!r}"
        )


# ---------------------------------------------------------------------------
# No stale debt: every admit-row corresponds to a still-blocking emit in source
# ---------------------------------------------------------------------------


def test_ee_no_fire_drill_allowlist_lists_only_blocking_codes() -> None:
    """A debt-admit row that is no longer a blocking code is a stale debt row.

    When a blocking emit is removed (the parser stops emitting it, or the
    helper is repurposed as non-blocking), the corresponding debt row MUST
    also be removed. Otherwise the ratchet silently grows its effectively-
    empty allowlist, hiding the fact that real coverage was lost.
    """
    blocking = enumerate_ee_blocking_rule_ids()
    for code in EE_NO_FIRE_DRILL_YET:
        assert code in blocking, (
            f"NO_FIRE_DRILL_YET lists {code!r}, which is not in "
            "EE_BLOCKING_RULE_IDS — if the blocking emit was removed, remove "
            "this stale debt row."
        )


# ---------------------------------------------------------------------------
# Source-scan defense: curation matches the live emit sites
# ---------------------------------------------------------------------------
#
# The inventory ``EE_BLOCKING_RULE_IDS`` is hand-curated. As a defense against
# drift, this test statically scans ``src/lawvm/estonia/`` for the rule_id
# literals + named constants that flow into ``CompileAdjudication(..., blocking=True, ...)``
# emit sites (direct or via the four named helpers that hardcode blocking)
# and asserts each discovered blocking rule_id is in the inventory.
#
# Coverage of kind=variable parameter sites is partial: the direct-call scan
# finds ``CompileAdjudication(kind="literal", ..., blocking=True)`` emit sites
# and the named-constant sites (``_EE_REF_SLICE_OP_FILTER_RULE`` and the
# literal-assignment before ``CompileAdjudication(kind=rule_id, ...)``). The
# third-party ``_append_ee_replay_adjudication`` and ``_record_ee_parse_rejection``
# helpers carry ``kind`` as a parameter; their blocking rule_ids are resolved
# by walking their callers. Any blocking emit found in this scan that is not
# in EE_BLOCKING_RULE_IDS is a silent-guard addition and fails the test.

_NAMED_BLOCKING_HELPERS = frozenset(
    {
        "_record_old_format_ref_slice_drop",  # grafter.py — hardcodes blocking=True
        "_record_ee_old_format_unparsed_meta_rejection",  # target_resolution.py — hardcodes
    }
)
# Wrapping helpers where blocking is hardcoded True: rule_id is the kind= value
# (whether literal or module-level constant).
_HARD_BLOCKING_HELPERS = _NAMED_BLOCKING_HELPERS | {
    "_append_ee_replay_adjudication",  # blocking behavior keyed on kind= value
}
# Forwarding blockers: helpers that take ``blocking=`` as a parameter and
# forward it verbatim to the internal ``CompileAdjudication(blocking=...)``.
# For these, the blocking disposition comes from the CALL SITE (not the
# helper body): a call site that passes ``blocking=True`` and a resolved
# ``kind=`` is a blocking emit. A call site that omits ``blocking=`` falls
# through to the helper default (typically ``False``) and is non-blocking.
#
# Witness commit: ``00f778fc EE replay: fail-loud broad-except audit`` introduced
# ``_ee_orchestration_adjudication`` along with two new blocking rule_ids
# (``ee_oracle_parse_failed``, ``ee_consistency_check_failed``) that flow
# through it.
_FORWARDING_BLOCKING_HELPERS = frozenset(
    {
        "_ee_orchestration_adjudication",
    }
)


def _scan_ee_blocking_emit_sites() -> set[str]:
    """Walk EE src ASTs and collect rule_ids observed to flow into
    ``CompileAdjudication(..., blocking=True, ...)`` emit sites.

    Coverage:
    * direct ``CompileAdjudication(kind="literal"|"named_const",_blocking=True)``;
    * `` CompileAdjudication(kind=NAME, blocking=True)`` where ``NAME`` was
      bound at module scope to an ``ee_*`` literal;
    * the wrapping helpers ``_record_old_format_ref_slice_drop`` and
      ``_append_ee_replay_adjudication`` (both hardcode ``blocking=True``
      internally) — every kind= passed in is resolved as a blocking rule_id;
    * the loop-body variant where ``rule_id = "ee_*"`` is assigned inside a
      ``for`` body and then consumed by a sibling
      ``CompileAdjudication(kind=rule_id, ..., blocking=True)`` (the
      ``ee_source_local_global_text_replace_selector_exclusion_inferred``
      case).
    """
    discovered: set[str] = set()
    for src in sorted(EE_SRC.glob("*.py")):
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        # First pass: collect module-level constant assignments that map
        # ``NAME = "ee_*"``. Used to resolve ``kind=NAME`` emit sites.
        const_literals: dict[str, str] = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and node.value.value.startswith("ee_")
            ):
                const_literals[node.targets[0].id] = node.value.value
        # Walk every Call node. For direct CompileAdjudication emit sites
        # with blocking=True and a resolvable kind (literal or named const),
        # record the rule_id. For wrapping helper call sites whose name is in
        # _HARD_BLOCKING_HELPERS, resolve kind= the same way.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = ""
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            is_blocking_helper = name in _HARD_BLOCKING_HELPERS
            is_forwarding_helper = name in _FORWARDING_BLOCKING_HELPERS
            is_direct_ca = name == "CompileAdjudication"
            if not (is_blocking_helper or is_forwarding_helper or is_direct_ca):
                continue
            kind_val: str | None = None
            blocking_val: bool | None = None
            for kw in node.keywords:
                if kw.arg == "kind":
                    kind_node = kw.value
                    if isinstance(kind_node, ast.Constant) and isinstance(kind_node.value, str):
                        kind_val = kind_node.value
                    elif isinstance(kind_node, ast.Name) and kind_node.id in const_literals:
                        kind_val = const_literals[kind_node.id]
                if kw.arg == "blocking" and isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, bool
                ):
                    blocking_val = kw.value.value
            # Direct CompileAdjudication: only count when blocking is literally True.
            if is_direct_ca and blocking_val is True and kind_val:
                discovered.add(kind_val)
            # Hard-blocker helpers that hardcode blocking=True internally:
            # every kind passed in goes blocking.
            if is_blocking_helper and kind_val:
                discovered.add(kind_val)
            # Forwarding helpers (``_ee_orchestration_adjudication`` etc.) take
            # ``blocking=`` as a parameter and forward it verbatim to the
            # internal ``CompileAdjudication(blocking=...)``; the disposition
            # comes from the CALL SITE. Only count call sites that explicitly
            # pass ``blocking=True`` (the helper default is typically False).
            if is_forwarding_helper and blocking_val is True and kind_val:
                discovered.add(kind_val)
        # Loop-body variant: ``rule_id = "ee_*"`` assigned inside a ``for``
        # body, then a sibling ``CompileAdjudication(kind=rule_id, ..., blocking=True)``
        # call consumes it (the
        # ee_source_local_global_text_replace_selector_exclusion_inferred site).
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            assigned: dict[str, str] = {}
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Assign)
                    and len(sub.targets) == 1
                    and isinstance(sub.targets[0], ast.Name)
                    and isinstance(sub.value, ast.Constant)
                    and isinstance(sub.value.value, str)
                    and sub.value.value.startswith("ee_")
                ):
                    assigned[sub.targets[0].id] = sub.value.value
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                fn = sub.func
                nm = ""
                if isinstance(fn, ast.Attribute):
                    nm = fn.attr
                elif isinstance(fn, ast.Name):
                    nm = fn.id
                if nm != "CompileAdjudication":
                    continue
                blocking_ok = False
                for kw in sub.keywords:
                    if kw.arg == "blocking" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        blocking_ok = True
                if not blocking_ok:
                    continue
                for kw in sub.keywords:
                    if (
                        kw.arg == "kind"
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id in assigned
                    ):
                        discovered.add(assigned[kw.value.id])
                        break
    return discovered


def test_ee_blocking_rule_ids_match_source_scan() -> None:
    """Defense: every blocking rule_id statically discoverable in source must
    be in ``EE_BLOCKING_RULE_IDS``, and vice versa.

    Catches two classes of drift:
    * a new blocking code literal/constant lands in source without being
      added to the inventory (silent-guard addition),
    * the inventory lists a blocking code that no longer appears in source
      (stale inventory).
    """
    discovered = _scan_ee_blocking_emit_sites()
    inventory = enumerate_ee_blocking_rule_ids()
    unbilled = discovered - inventory
    stale = inventory - discovered
    assert not unbilled, (
        "Source scan found blocking emit sites whose rule_ids are NOT in "
        "EE_BLOCKING_RULE_IDS — add them to the inventory, then either "
        "write a fire-drill or admit the debt:\n  "
        + "\n  ".join(sorted(unbilled))
    )
    assert not stale, (
        "EE_BLOCKING_RULE_IDS lists rule_ids not found in source scan — if "
        "the emit site was refactored, update the inventory and the spec:\n  "
        + "\n  ".join(sorted(stale))
    )


# ---------------------------------------------------------------------------
# Inventory shape invariants
# ---------------------------------------------------------------------------


def test_ee_blocking_inventory_is_a_frozenset() -> None:
    """The inventory is an immutable frozen set so accidental mutation cannot
    silently grow or shrink it. (A list would allow ``.append`` to bypass
    the ratchet.)
    """
    assert isinstance(EE_BLOCKING_RULE_IDS, frozenset)
    assert isinstance(EE_FIRE_DRILL_COVERAGE, frozenset)


def test_ee_inventory_nonempty() -> None:
    """Sanity: the EE frontend does emit blocking adjudications; an empty
    inventory would mean either the inventory was accidentally wiped or the
    parser stopped emitting blocking codes (which would itself be a
    regression worth flagging)."""
    assert EE_BLOCKING_RULE_IDS, (
        "EE_BLOCKING_RULE_IDS is empty — either no blocking adjudications "
        "are emitted (regression) or the inventory was wiped."
    )


@pytest.mark.parametrize("rule_id", sorted(EE_BLOCKING_RULE_IDS))
def test_ee_blocking_rule_id_in_catalog(rule_id: str) -> None:
    """Every blocking rule_id is registered as a believed-spec hypothesis in
    the EE spec catalog. The catalog test enforces coverage of every ``ee_*``
    literal; this layer pins that the blocking subset is cataloged too."""
    from lawvm.tools.spec_ledger_ee_catalog import _EE_RULE_SPECS

    assert rule_id in _EE_RULE_SPECS, (
        f"Blocking rule_id {rule_id!r} is not in the EE believed-spec "
        "catalog (_EE_RULE_SPECS). Add a falsifiable hypothesis entry."
    )


# ---------------------------------------------------------------------------
# Fire-drills: production-path tests driving each EE_FIRE_DRILL_COVERAGE code
# ---------------------------------------------------------------------------


def _fire_drills_target_registered_codes() -> Dict[str, str]:
    """Stable enumeration of the drilling test names in this module (and in
    other EE test files) that ``EE_FIRE_DRILL_COVERAGE`` claims cover a
    blocking rule_id.

    Each entry is a one-(code, drill_name) pair; the partition ratchet gates
    check (a) every code in EE_FIRE_DRILL_COVERAGE has a registered
    drill_name here, and (b) every name here targets a code in
    EE_FIRE_DRILL_COVERAGE. A drill that targets an unregistered code would
    silently unblock the inventory; a registered name targeting an
    unregistered code would be a dead string.

    The 26 `existing-tests-registered` entries were cross-referenced
    manually against the EE test corpus via the
    ``notes_internal/_cross_ref_drills.py`` AST scan (2026-06-26): each
    selected test drives a known-violator input through the production
    path (``apply_ee_ops`` / ``parse_ee_amendment_ops`` /
    ``replay_ee_to_pit`` / ``_ee_filter_cancelled_pending_refs`` etc.)
    AND asserts a CompileAdjudication carrying ``kind == "<rule_id>"`` is
    produced. Hardcoded-CompileAdjudication literal tests (carrier /
    taxonomy self-checks) were excluded as non-drills; their asserts
    check payload shape, not guard reachability.
    """
    return {
        # === Self-authored drills in this test file ===
        "ee_replay_unsupported_action": "test_ee_fire_drill_replay_unsupported_action_blocks",
        "ee_replay_target_not_found": "test_ee_fire_drill_replay_target_not_found_blocks",
        "ee_replay_statute_title_noop": "test_ee_fire_drill_replay_statute_title_noop_blocks",
        "ee_replay_unsupported_statute_title_action": "test_ee_fire_drill_replay_unsupported_statute_title_action_blocks",
        "ee_oracle_parse_failed": "test_ee_fire_drill_oracle_parse_failed_blocks",
        "ee_consistency_check_failed": "test_ee_fire_drill_consistency_check_failed_blocks",
        # === Existing production-path drills in tests/test_ee_apply_semantics.py ===
        "ee_ambiguous_single_occurrence_text_replace": (
            "test_exact_target_insert_after_with_repeated_source_surface_emits_ambiguity"
        ),
        "ee_flat_part_repeal_span": "test_repeal_flat_part_marker_removes_owned_section_run_until_next_part",
        "ee_implicit_division_sequence_relabel_after_high_jagu_insert": (
            "test_high_division_insert_relabels_unique_duplicate_division_suffix_with_adjudication"
        ),
        "ee_inline_item_replace_singleton_subsection": (
            "test_replace_section_item_recovers_inline_singleton_subsection_item"
        ),
        "ee_labelled_item_replacement_payload_selection": (
            "test_replace_item_selects_matching_label_from_multi_item_payload"
        ),
        "ee_overbroad_container_replace_blocked": "test_replace_blocks_child_payload_from_overwriting_part_container",
        "ee_plural_item_replace_range_omits_inserted_labels": (
            "test_plural_item_replace_range_removes_omitted_inserted_item_labels"
        ),
        "ee_plural_subsection_replace_extra_payload_label": (
            "test_replace_extra_plural_subsection_payload_label_inserts_absent_subsection"
        ),
        "ee_replay_meta_non_body_skipped": "test_apply_ee_ops_records_meta_as_non_body_skip_not_unsupported",
        "ee_replay_noop": "test_apply_ee_ops_records_unresolved_target_and_noop",
        "ee_replay_unparsed_operation_skipped": (
            "test_apply_ee_ops_records_unparsed_meta_as_coverage_skip_not_non_body"
        ),
        "ee_replay_unsupported_heading_target": (
            "test_ee_apply_unsupported_heading_target_records_adjudication_not_warning"
        ),
        "ee_section_item_replace_unique_descendant_item": (
            "test_apply_ee_ops_resolves_section_item_replace_to_unique_descendant_item"
        ),
        "ee_source_case_only_text_replace": "test_apply_ee_ops_records_case_only_source_text_recovery",
        "ee_subsection_table_only_replace_preserve_intro": "test_subsection_table_only_replace_preserves_existing_intro",
        "ee_text_replace_numbered_subsection_for_item_target_by_old_text": (
            "test_apply_ee_ops_retargets_section_item_text_replace_to_same_number_subsection_old_text"
        ),
        "ee_text_replace_unique_descendant_item_by_old_text": (
            "test_apply_ee_ops_retargets_section_item_text_replace_to_unique_descendant_old_text"
        ),
        # === Existing production-path drills in tests/test_ee_parser_normalization.py ===
        "ee_parse_old_format_unparsed_meta_rejected": (
            "test_old_format_lower_op_texts_records_rejected_unparsed_meta"
        ),
        "ee_ref_slice_operation_filtered": (
            "test_parse_old_format_ref_slice_drop_uses_ref_slice_filtered_adjudication"
        ),
        "ee_source_local_global_text_replace_selector_exclusion_inferred": (
            "test_parse_ee_amendment_ops_keeps_selector_exclusion_out_of_global_replay_scope"
        ),
        # === Existing production-path drills in tests/test_ee_replay_logic.py ===
        "ee_amendment_parse_failed": "test_replay_ee_to_pit_adjudicates_amendment_parse_failure",
        "ee_amendment_source_fetch_failed": "test_replay_ee_to_pit_adjudicates_amendment_fetch_failure",
        "ee_cancelled_pending_ref_metadata_parse_failed": (
            "test_filter_cancelled_pending_refs_records_metadata_parse_failure_and_retains_ref"
        ),
        "ee_cancelled_pending_ref_source_fetch_failed": (
            "test_filter_cancelled_pending_refs_records_source_fetch_failure_and_retains_ref"
        ),
        "ee_pending_source_act_commencement_source_fetch_failed": (
            "test_precompose_pending_source_act_commencement_records_fetch_failure"
        ),
        "ee_temporal_source_scan_failed": "test_replay_ee_to_pit_adjudicates_temporal_source_scan_failure",
    }


def test_ee_fire_drills_are_named_in_coverage_registry() -> None:
    """Every code in EE_FIRE_DRILL_COVERAGE must have a registered drill test
    name in ``_fire_drills_target_registered_codes``."""
    registry = _fire_drills_target_registered_codes()
    assert set(registry.keys()) == EE_FIRE_DRILL_COVERAGE, (
        "EE_FIRE_DRILL_COVERAGE and the registered-drills set disagree:\n"
        f"  in coverage only: {EE_FIRE_DRILL_COVERAGE - set(registry.keys())}\n"
        f"  in registry only: {set(registry.keys()) - EE_FIRE_DRILL_COVERAGE}"
    )


def test_ee_fire_drill_replay_unsupported_action_blocks() -> None:
    """Fire-drill for ``ee_replay_unsupported_action``:

    Drive a known-violating op (a ``LegalOperation`` whose ``action`` is
    outside the dispatcher's whitelist: replace, repeal, insert, renumber,
    text_replace — here: ``HEADING_REPLACE``) through the full production
    ``apply_ee_ops`` dispatch and assert the blocking adjudication
    ``ee_replay_unsupported_action`` fires with ``blocking=True`` and the
    expected message.

    Pre-drill: ``ee_replay_unsupported_action`` was a debt row in
    ``EE_NO_FIRE_DRILL_YET`` from the baseline at
    commit ``3528f2c4``. This drill removes it from the debt allowlist and
    moves it into ``EE_FIRE_DRILL_COVERAGE``, paying the debt down by one
    (and correspondingly decrementing ``EE_NO_FIRE_DRILL_CEILING``).

    Source emission: src/lawvm/estonia/grafter.py:10360 in ``apply_ee_ops``,
    via the hard-blocker helper ``_append_ee_replay_adjudication``. The
    production lane reaches this emit site via the ``action not in (=)``
    branch of the dispatcher, not via any synthetic hook.
    """
    statute = _drill_statute()
    op = _unsupported_action_op()
    adjudications: list[CompileAdjudication] = []
    updated = apply_ee_ops(statute, [op], adjudications_out=adjudications)
    # Tree should be unchanged by the skipped op (over-retention is the safe
    # wrong per AGENTS.md §0): the body still has one chapter, one section,
    # one subsection.
    assert updated.body is statute.body, (
        "Unsupported-action dispatch MUST leave the tree unchanged (over-retention); "
        "the drill op is a no-op at structure time. Saw a body mutation."
    )
    # Find the matching blocking adjudication.
    matches = [adj for adj in adjudications if adj.kind == "ee_replay_unsupported_action"]
    assert matches, (
        "Expected a blocking adjudication with kind "
        f"'ee_replay_unsupported_action' to fire for the HEADING_REPLACE op, "
        f"got: {[(a.kind, a.blocking) for a in adjudications]}"
    )
    emit = matches[0]
    assert emit.blocking is True, (
        f"ee_replay_unsupported_action must emit blocking=True; "
        f"got blocking={emit.blocking!r}"
    )
    assert emit.phase == "replay", (
        f"ee_replay_unsupported_action must declare phase='replay'; "
        f"got phase={emit.phase!r}"
    )
    assert emit.op_id == op.op_id, (
        f"adjudication op_id mismatch: emit={emit.op_id!r} expected={op.op_id!r}"
    )


def test_ee_fire_drill_replay_target_not_found_blocks() -> None:
    """Fire-drill for ``ee_replay_target_not_found``:

    Drive a ``REPLACE`` op whose target path (section:999) resolves to no
    node in the fixture body (the body only has section:1) through the full
    production ``apply_ee_ops`` dispatch and assert the blocking
    adjudication ``ee_replay_target_not_found`` fires with ``blocking=True``.

    Pre-drill: ``ee_replay_target_not_found`` was a debt row in the baseline
    at commit ``3528f2c4``. This drill (the second in the EE guard-liveness
    suite) moves it from ``EE_NO_FIRE_DRILL_YET`` to
    ``EE_FIRE_DRILL_COVERAGE`` — paying the debt down by one and decrementing
    the ceiling by one (31 -> 30).

    Source emission: src/lawvm/estonia/grafter.py:10427 in ``apply_ee_ops``,
    via the hard-blocker helper ``_append_ee_replay_adjudication``. The
    production lane reaches this emit site via the
    ``if not target_resolved:`` arm of the dispatcher, where
    ``target_resolved = _ee_resolve_full_path(pre_op_body, target_path) is not None``
    is False because the target section:999 is absent from the body.

    Discipline (AGENTS.md §0): an op whose stated target does not resolve
    MUST NOT mutate the body — uncertainty is preserved rather than guessed.
    The drill asserts the body is unchanged (over-retention is the safe
    wrong) AND that the blocking adjudication surfaces the unresolved target.
    """
    statute = _drill_statute()
    op = _target_not_found_op()
    adjudications: list[CompileAdjudication] = []
    updated = apply_ee_ops(statute, [op], adjudications_out=adjudications)
    # Body MUST be unchanged — a non-resolving target is a structural no-op.
    assert updated.body is statute.body, (
        "ee_replay_target_not_found dispatch MUST leave the tree unchanged "
        "(over-retention per AGENTS.md §0); saw a body mutation."
    )
    matches = [adj for adj in adjudications if adj.kind == "ee_replay_target_not_found"]
    assert matches, (
        "Expected a blocking adjudication with kind "
        "'ee_replay_target_not_found' to fire for the section:999 REPLACE op, "
        f"got: {[(a.kind, a.blocking) for a in adjudications]}"
    )
    emit = matches[0]
    assert emit.blocking is True, (
        f"ee_replay_target_not_found must emit blocking=True; "
        f"got blocking={emit.blocking!r}"
    )
    assert emit.phase == "replay", (
        f"ee_replay_target_not_found must declare phase='replay'; "
        f"got phase={emit.phase!r}"
    )
    assert emit.op_id == op.op_id, (
        f"adjudication op_id mismatch: emit={emit.op_id!r} expected={op.op_id!r}"
    )


def test_ee_fire_drill_replay_statute_title_noop_blocks() -> None:
    """Fire-drill for ``ee_replay_statute_title_noop``:

    Drive a REPLACE op targeting the statute-title address with payload
    text equal to the current statute title. The dispatcher reaches the
    ``is_statute_title_address`` branch, applies the title replacement,
    detects ``new_title == old_title``, and emits the blocking noop.

    Pre-drill: ``ee_replay_statute_title_noop`` was a debt row admitted
    in commit ``5e2e972c``. This drill removes it from the debt allowlist
    and moves it into ``EE_FIRE_DRILL_COVERAGE`` (decrementing the ceiling
    by one).

    Source emission: src/lawvm/estonia/grafter.py:10351 (``if not new_title
    or new_title == old_title:`` arm), via the hard-blocker helper
    ``_append_ee_replay_adjudication``.
    """
    statute = _drill_statute()  # title = "Fire Drill Base"
    op = _statute_title_noop_op(statute_title=statute.title)
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, [op], adjudications_out=adjudications)
    matches = [adj for adj in adjudications if adj.kind == "ee_replay_statute_title_noop"]
    assert matches, (
        "Expected a blocking adjudication with kind "
        "'ee_replay_statute_title_noop' to fire for a title-replace op whose "
        f"new text equals the current title; got: "
        f"{[(a.kind, a.blocking) for a in adjudications]}"
    )
    emit = matches[0]
    assert emit.blocking is True, (
        f"ee_replay_statute_title_noop must emit blocking=True; "
        f"got blocking={emit.blocking!r}"
    )
    assert emit.phase == "replay", (
        f"ee_replay_statute_title_noop must declare phase='replay'; "
        f"got phase={emit.phase!r}"
    )
    assert emit.op_id == op.op_id


def test_ee_fire_drill_replay_unsupported_statute_title_action_blocks() -> None:
    """Fire-drill for ``ee_replay_unsupported_statute_title_action``:

    Drive a REPEAL op targeting the statute-title address. The dispatcher
    hits the ``is_statute_title_address`` branch, falls into the
    ``action != replace`` sub-branch, and emits the blocking
    ``ee_replay_unsupported_statute_title_action``.

    Pre-drill: ``ee_replay_unsupported_statute_title_action`` was a debt
    row admitted in commit ``3528f2c4``. This drill removes it from the
    debt allowlist and moves it into ``EE_FIRE_DRILL_COVERAGE``.

    Source emission: src/lawvm/estonia/grafter.py:10338 (``if action !=
    replace or op.payload is None:`` arm), via the hard-blocker helper
    ``_append_ee_replay_adjudication``.

    Discipline (AGENTS.md §0): an unsupported action targeting the
    statute title MUST NOT mutate the body — uncertainty is preserved
    rather than guessed. The drill asserts the body is unchanged (over-
    retention is the safe wrong) AND that the blocking adjudication
    surfaces the unsupported action.
    """
    statute = _drill_statute()  # title = "Fire Drill Base"
    op = _statute_title_unsupported_action_op()
    adjudications: list[CompileAdjudication] = []
    updated = apply_ee_ops(statute, [op], adjudications_out=adjudications)
    # Body MUST be unchanged — a non-replace action against the statute
    # title is a no-op at structure time (over-retention per §0).
    assert updated.body is statute.body, (
        "ee_replay_unsupported_statute_title_action dispatch MUST leave the "
        "tree unchanged (over-retention per AGENTS.md §0); saw a body mutation."
    )
    matches = [
        adj for adj in adjudications
        if adj.kind == "ee_replay_unsupported_statute_title_action"
    ]
    assert matches, (
        "Expected a blocking adjudication with kind "
        "'ee_replay_unsupported_statute_title_action' to fire for a REPEAL op "
        f"targeting the statute-title address; got: "
        f"{[(a.kind, a.blocking) for a in adjudications]}"
    )
    emit = matches[0]
    assert emit.blocking is True, (
        f"ee_replay_unsupported_statute_title_action must emit blocking=True; "
        f"got blocking={emit.blocking!r}"
    )
    assert emit.phase == "replay", (
        f"ee_replay_unsupported_statute_title_action must declare phase='replay'; "
        f"got phase={emit.phase!r}"
    )
    assert emit.op_id == op.op_id


# ---------------------------------------------------------------------------
# Crash-path fire-drills: drive replay_ee_to_pit through the source-lane
# crash boundaries via monkeypatch (mirrors test_ee_replay_logic.py pattern).
# ---------------------------------------------------------------------------


def _patch_replay_for_crash_drill(
    monkeypatch: pytest.MonkeyPatch,
    *,
    oracle_is_base: bool = False,
    oracle_id: str = "oracle",
    oracle_xml: bytes | None = b"<oracle-xml/>",
    parse_ee_statute_fn=None,
    verify_consistency_fn=None,
) -> None:
    """Minimal ``replay_ee_to_pit`` monkeypatch — mirrors the
    ``_patch_minimal_ee_replay_pipeline`` pattern in
    ``tests/test_ee_replay_logic.py`` but with oracle-path support so the
    ``ee_oracle_parse_failed`` and ``ee_consistency_check_failed`` crash
    paths are reachable.
    """
    from types import SimpleNamespace

    from lawvm.estonia import replay as ee_replay

    base = IRStatute(
        statute_id="ee/base",
        title="Test",
        body=IRNode(kind=IRNodeKind.BODY),
    )
    oracle = IRStatute(
        statute_id=f"ee/{oracle_id}",
        title="Oracle",
        body=IRNode(kind=IRNodeKind.BODY),
    )

    pair_plan = SimpleNamespace(
        grupi_id="g1",
        oracle_id=oracle_id if not oracle_is_base else None,
        source_basis=SimpleNamespace(value="pairwise_terviktekst_delta"),
        comparison_class="commensurable_delta",
        source_adjudication=None,
        oracle_is_base=oracle_is_base,
        oracle_refs=[],
        amendments_to_apply=[],
        base_is_consolidated=True,
        base_refs=[],
    )

    if parse_ee_statute_fn is None:
        def parse_ee_statute_fn(xml, statute_id):
            return oracle if "oracle" in statute_id else base

    if verify_consistency_fn is None:
        def verify_consistency_fn(*a, **kw):
            return []

    monkeypatch.setattr(ee_replay, "parse_ee_statute", parse_ee_statute_fn)
    monkeypatch.setattr(ee_replay, "fetch_rt_xml", lambda akt_viide, archive: b"<base-xml/>")
    monkeypatch.setattr(
        ee_replay,
        "plan_ee_oracle_pair",
        lambda **kw: SimpleNamespace(plan=pair_plan, oracle_xml=oracle_xml),
    )
    monkeypatch.setattr(ee_replay, "_ee_filter_cancelled_pending_refs", lambda refs, **kw: refs)
    monkeypatch.setattr(
        ee_replay,
        "_ee_precompose_pending_source_act_commencements",
        lambda refs, **kw: (tuple(refs), ()),
    )
    monkeypatch.setattr(ee_replay, "parse_ee_amendment_ops", lambda *a, **kw: [])
    monkeypatch.setattr(ee_replay, "apply_ee_ops", lambda statute, ops, **kw: statute)
    monkeypatch.setattr(ee_replay, "compile_timelines", lambda base_ir, lo_ops_out, temporal_events=(): {})
    monkeypatch.setattr(ee_replay, "materialize_pit", lambda timelines, as_of, base: base)
    monkeypatch.setattr(ee_replay, "ingest_consolidated", lambda oracle, as_of: oracle)
    monkeypatch.setattr(ee_replay, "verify_consistency", verify_consistency_fn)


def test_ee_fire_drill_oracle_parse_failed_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fire-drill for ``ee_oracle_parse_failed``:

    Drive ``replay_ee_to_pit`` through the oracle-parse crash path.
    Monkeypatch ``parse_ee_statute`` to raise ValueError when parsing the
    oracle XML; the replay path catches the exception at
    ``src/lawvm/estonia/replay.py:1519`` and emits the blocking
    ``ee_oracle_parse_failed`` adjudication.
    """
    from lawvm.estonia.replay import replay_ee_to_pit

    def fake_parse(xml, statute_id: str):
        if "oracle" in statute_id:
            raise ValueError("malformed oracle XML")
        return IRStatute(
            statute_id=statute_id,
            title="Base",
            body=IRNode(kind=IRNodeKind.BODY),
        )

    _patch_replay_for_crash_drill(
        monkeypatch,
        oracle_is_base=False,
        oracle_id="oracle-crash",
        oracle_xml=b"<bad-oracle-xml>",
        parse_ee_statute_fn=fake_parse,
    )

    result = replay_ee_to_pit("base", "2025-01-01", archive=object())

    matches = [adj for adj in result.adjudications if adj.kind == "ee_oracle_parse_failed"]
    assert matches, (
        "Expected ee_oracle_parse_failed blocking adjudication to fire when "
        "parse_ee_statute raises on the oracle XML; got: "
        f"{[(a.kind, a.blocking) for a in result.adjudications]}"
    )
    emit = matches[0]
    assert emit.blocking is True
    assert emit.phase == "parse"
    assert emit.detail.get("rule_id") == "ee_oracle_parse_failed"
    assert emit.detail.get("blocking") is True


def test_ee_fire_drill_consistency_check_failed_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fire-drill for ``ee_consistency_check_failed``:

    Drive ``replay_ee_to_pit`` through the consistency-check crash path.
    Monkeypatch ``verify_consistency`` to raise RuntimeError; the replay
    path catches the exception at
    ``src/lawvm/estonia/replay.py:1911`` and emits the blocking
    ``ee_consistency_check_failed`` adjudication.
    """
    from lawvm.estonia.replay import replay_ee_to_pit

    def crash_verify(*args, **kwargs):
        raise RuntimeError("consistency check exploded")

    _patch_replay_for_crash_drill(
        monkeypatch,
        oracle_is_base=False,
        oracle_id="oracle-ok",
        oracle_xml=b"<good-oracle/>",
        verify_consistency_fn=crash_verify,
    )

    result = replay_ee_to_pit("base", "2025-01-01", archive=object())

    matches = [adj for adj in result.adjudications if adj.kind == "ee_consistency_check_failed"]
    assert matches, (
        "Expected ee_consistency_check_failed blocking adjudication to fire when "
        "verify_consistency raises; got: "
        f"{[(a.kind, a.blocking) for a in result.adjudications]}"
    )
    emit = matches[0]
    assert emit.blocking is True
    assert emit.phase == "compare"
    assert emit.detail.get("rule_id") == "ee_consistency_check_failed"
    assert emit.detail.get("blocking") is True
