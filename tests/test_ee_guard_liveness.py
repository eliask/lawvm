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

import pytest

from lawvm.estonia.guard_liveness import (
    EE_BLOCKING_RULE_IDS,
    EE_FIRE_DRILL_COVERAGE,
    EE_NO_FIRE_DRILL_CEILING,
    EE_NO_FIRE_DRILL_YET,
    enumerate_ee_blocking_rule_ids,
)

EE_SRC = Path(__file__).resolve().parent.parent / "src" / "lawvm" / "estonia"


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
