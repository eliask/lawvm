"""Anti-drift guard for the NZ believed_spec catalog.

The catalog (``src/lawvm/tools/spec_ledger_nz_catalog.py``) carries a falsifiable
hypothesis per NZ ``witness_rule_id`` for the dry-run kernel + actual-replay promotion
layer. This test pins the cataloged fraction at 100% of the statically discoverable NZ
rule-id surface in both directions:

* coverage: every ``"nz_…"`` rule-id literal in the scoping files has a non-empty
  catalog entry (no silent gaps when a new rule id is added — the receipt that
  fires a new finding is never a blind spot);
* no dead entries: every catalog key is a real rule-id literal in the scoping
  files (no stale hypotheses for rules that were renamed/removed).

Discovery is by AST over ``dry_run.py`` + ``dry_run_oracle.py`` + ``actual_replay.py``
under ``src/lawvm/new_zealand/``, so it tracks the code rather than a hand-maintained
list. Rule-bearing literals ending in ``:`` are excluded by the ``:``-fragment filter
(some are agreement_surface / locator-fragment labels). The four bare family name
strings (``nz_dry_run_repeal``, ``nz_dry_run_structural_insert``, etc.) and the
whole-tree agreement-surface identity ``nz_dry_run_repeal_whole_tree`` are the
documented ``NZ_NON_RULE_LITERALS`` exclusions. See the catalog module docstring.
"""
from __future__ import annotations

import ast
from pathlib import Path

from lawvm.tools.spec_ledger_discovery import format_uncataloged, locate_rule_ids
from lawvm.tools.spec_ledger_nz_catalog import (
    NZ_NON_RULE_LITERALS,
    _NZ_RULE_SPECS,
)

_NZ_DIR = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "new_zealand"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
# The rule-bearing kernel this catalog covers. Per the catalog module docstring the
# denominator is a fixed file list so extending coverage later is a deliberate
# expansion rather than a silent widening — adding a rule-bearing file here is a
# paired change with authoring its RULE_SPECS entries.
_SCOPED_FILES = (
    # The four promotable families' kernel + actual-replay promotion layer:
    "dry_run.py",
    "dry_run_oracle.py",
    "actual_replay.py",
    # Instruction lowering / readiness / candidates / source-change witnesses:
    "instruction_workqueue.py",
    "effect_candidates.py",
    "effect_readiness.py",
    # Frontier lane + operation surface:
    "frontier_work_items.py",
    "operation_surface.py",
    # Bench mark / commencement lane:
    "benchmark.py",
)


def _is_rule_literal(value: str) -> bool:
    """An ``nz_*`` string literal that is a rule id (not a surface label / locator)."""
    if not value.startswith("nz_"):
        return False
    if value in NZ_NON_RULE_LITERALS:
        return False
    if "." in value:  # filename extension
        return False
    if ":" in value:  # agreement_surface / locator-fragment label
        return False
    return True


def _discover_nz_rule_ids() -> set[str]:
    """Every static ``nz_*`` rule-id literal across the scoping files, via AST."""
    found: set[str] = set()
    for name in _SCOPED_FILES:
        path = _NZ_DIR / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_rule_literal(node.value)
            ):
                found.add(node.value)
    return found


def test_scoping_files_present() -> None:
    # Guard against discovery silently finding nothing (wrong path / wrong file names),
    # which would make the coverage assertion vacuously pass.
    for name in _SCOPED_FILES:
        path = _NZ_DIR / name
        assert path.is_file(), path


def test_every_discovered_rule_id_is_cataloged() -> None:
    """Coverage / anti-drift: no NZ rule id may lack a believed_spec hypothesis."""
    discovered = _discover_nz_rule_ids()
    assert discovered, "AST discovery found no NZ rule-id literals in the scoping files"
    uncataloged = sorted(discovered - set(_NZ_RULE_SPECS))
    locations = locate_rule_ids(_NZ_DIR, uncataloged, repo_root=_SRC_ROOT)
    assert not uncataloged, (
        f"{len(uncataloged)} NZ witness rule id(s) have no believed_spec entry in "
        "_NZ_RULE_SPECS (cataloged fraction < 100%) (id <- emit site):\n"
        f"{format_uncataloged(uncataloged, locations)}"
    )


def test_no_dead_catalog_entries() -> None:
    """Every catalog key must map to a real rule-id literal in a scoping file."""
    discovered = _discover_nz_rule_ids()
    dead = sorted(set(_NZ_RULE_SPECS) - discovered)
    assert not dead, (
        f"{len(dead)} _NZ_RULE_SPECS key(s) do not correspond to any NZ rule-id literal "
        f"in the scoping files (stale/dead entries): {dead}"
    )


def test_all_hypotheses_non_empty() -> None:
    """A cataloged rule must carry a real one-line hypothesis, not a placeholder."""
    empty = sorted(k for k, v in _NZ_RULE_SPECS.items() if not v or not v.strip())
    assert not empty, f"empty believed_spec hypotheses: {empty}"


def test_excluded_non_rule_literals_are_not_cataloged() -> None:
    # The documented non-rule literals must not sneak into the catalog as fake rules.
    leaked = sorted(NZ_NON_RULE_LITERALS & set(_NZ_RULE_SPECS))
    assert not leaked, f"non-rule literals cataloged as rules: {leaked}"


def test_new_rule_id_introduced_unless_cataloged() -> None:
    # Companion guard-liveness test: a newly added NZ_*_RULE_ID constant in a scoping
    # file without a catalog entry fails the coverage test above. This test exists to
    # pin the lesson explicitly — a rule id introduced in this iteration's scope is
    # added deliberately, never silently (per AGENTS §1.10 / §2.9): the witness must
    # be paired with its believed_spec hypothesis at the same change.
    discovered = _discover_nz_rule_ids()
    assert "nz_dry_run_refused_no_replayable_insert_candidate" in discovered
    assert "nz_dry_run_refused_no_replayable_replace_candidate" in discovered
    assert "nz_actual_replay_refused_materialized_target_slice_diverges_from_oracle" in discovered
    # The newly added proof-schema field for the block-insert composite case
    # (insert_co_inserted_block_labels) is consumed by this very diagnostic — its
    # companion rule must be cataloged so the defence-in-depth firing is auditable:
    assert (
        "nz_actual_replay_refused_materialized_target_slice_diverges_from_oracle"
        in _NZ_RULE_SPECS
    )
