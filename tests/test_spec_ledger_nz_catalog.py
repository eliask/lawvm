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
    "commencement.py",
    # Acquisition / dependency / payload-surface / closure / version-diff / cache /
    # agreement-projection lanes:
    "acquisition.py",
    "dependencies.py",
    "payload_surface.py",
    "closure.py",
    "version_diff.py",
    "corpus_cache.py",
    "agreement.py",
    "chain_replay.py",
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


def _discover_nz_rule_ids_in_scoping_src() -> set[str]:
    """Every static ``nz_*`` rule-id literal across the scoping SRC files, via AST.

    Sees bare f-string prefixes (``nz_X_``) only on runtime-constructed rule_ids
    — the full concatenated id is invisible to this scan. The test-suite scan
    ``_discover_nz_rule_ids_in_tests`` covers the runtime constructs.
    """
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


# Alias kept for the coverage-direction test's stable name.
_discover_nz_rule_ids = _discover_nz_rule_ids_in_scoping_src


def _discover_nz_rule_ids_in_tests() -> set[str]:
    """Every static ``nz_*`` rule-id literal across the NZ test files, via AST.

    Anchors the runtime f-string concatenation results (```f"nz_X_{status}"```)
    whose full id never appears as a literal in src/ — the test suite asserts
    against the full id, so it pins exactly which rule_ids fire at runtime.
    """
    tests_dir = Path(__file__).resolve().parents[1] / "tests"
    found: set[str] = set()
    for path in sorted(tests_dir.glob("test_new_zealand*.py")):
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
    """Every catalog key must map to a real rule-id literal somewhere — src OR tests."""
    discovered = _discover_nz_rule_ids_in_scoping_src() | _discover_nz_rule_ids_in_tests()
    dead = sorted(set(_NZ_RULE_SPECS) - discovered)
    assert not dead, (
        f"{len(dead)} _NZ_RULE_SPECS key(s) do not correspond to any NZ rule-id literal "
        f"in the scoping src files OR the NZ test files (stale/dead entries): {dead}"
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


def test_catalog_prose_agrees_with_production_adapter_on_overlap() -> None:
    """Parity gate (AGENTS §2.5): the production NZ spec-ledger adapter's
    ``NZRuleCatalogEntry`` set is the AUTHORITY for the 16 dry-oracle rule_ids it
    owns (those carry a paired *confidence tier* the broader catalog does not).
    This catalog must:
      - include every adapter rule_id (it is a strict superset of the adapter); and
      - use the ADAPTER's believed_spec prose VERBATIM for the overlap (no second
        authors' voice that could silently drift from the production-native one).

    A future change that adds an adapter-owned rule_id to ``_EXTRA_NZ_RULE_SPECS``
    with different prose fails this test — the runtime consolidation filter
    drops extras-with-the-same-id, so the adapter's prose always wins on overlap.
    """
    from lawvm.new_zealand.spec_ledger_adapter import NZ_RULE_SPECS as _ADAPTER
    from lawvm.tools.spec_ledger_nz_catalog import _EXTRA_NZ_RULE_SPECS as _EXTRAS

    # 1. Strict superset: every adapter rule_id is in the composed catalog.
    missing_from_catalog = sorted(set(_ADAPTER) - set(_NZ_RULE_SPECS))
    assert not missing_from_catalog, (
        f"adapter-cataloged rule_ids missing from the composed _NZ_RULE_SPECS: "
        f"{missing_from_catalog}"
    )

    # 2. Verbatim belief agreement on overlap.
    disagreements = sorted(
        [(k, _ADAPTER[k][:80], _NZ_RULE_SPECS[k][:80]) for k in _ADAPTER
         if _NZ_RULE_SPECS.get(k) != _ADAPTER[k]]
    )
    assert not disagreements, (
        f"nz_* rule_ids where the tools catalog DISAGREES with the production "
        f"adapter's believed_spec prose (the adapter is authoritative for the "
        f"dry-oracle subset): {disagreements}"
    )

    # 3. Extras must NOT contain any adapter-owned id — the consolidation filter
    # (module init) drops these so the adapter is the single source of truth.
    overlap = sorted(set(_EXTRAS) & set(_ADAPTER))
    assert not overlap, (
        f"_EXTRA_NZ_RULE_SPECS still contains adapter-owned rule_ids after the "
        f"runtime consolidation filter (AGENTS §2.5 single-source-per-family "
        f"regressed — these rule_ids must come only from the adapter): {overlap}"
    )


def test_every_nz_rule_id_used_in_nz_tests_is_cataloged_or_non_rule() -> None:
    """Second discovery surface: every ``nz_*`` rule_id literal the NZ test suite
    asserts against must be cataloged OR documented in ``NZ_NON_RULE_LITERALS``.

    Why a SEPARATE dimension from the src-scan: many witness rule_ids are
    constructed at runtime via f-string templates (``f"nz_X_{status}"``). The
    AST scan on src/ can only see the bare f-string prefix, not the full
    runtime-emitted id — but the test suite asserts against the full id, so it
    anchors exactly which rule_ids fire at runtime. This guard catches:

    * a rule_id in a test that no longer exists in src (renamed/deleted — dead
      reference, signal loss)
    * a runtime rule_id a future change emits without a paired believed_spec
      hypothesis (the src-scan misses it because the f-string prefix is the only
      literal in src; the test file would catch it as the new emission is added to
      an asserting test).

    ``NZ_NON_RULE_LITERALS`` covers the one intentional uncataloged-fixture
    (``nz_dry_run_some_future_uncataloged_rule``, the negative-test input for the
    legacy_unknown sentinel path — by-design MUST NOT be cataloged).
    """
    import ast as _ast
    tests_dir = Path(__file__).resolve().parents[1] / "tests"
    found: set[str] = set()
    for path in sorted(tests_dir.glob("test_new_zealand*.py")):
        tree = _ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _ast.walk(tree):
            if (
                isinstance(node, _ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("nz_")
            ):
                v = node.value
                if "." in v or ":" in v:
                    continue
                if v in NZ_NON_RULE_LITERALS:
                    continue
                found.add(v)
    uncataloged = sorted(found - set(_NZ_RULE_SPECS))
    assert not uncataloged, (
        f"{len(uncataloged)} nz_* rule_id literal(s) appear in NZ test files but are "
        f"NOT in _NZ_RULE_SPECS and NOT in NZ_NON_RULE_LITERALS — these are rule "
        f"ids the test suite asserts against (so they fire at runtime) but lack a "
        f"paired believed_spec hypothesis. Either catalog them or document them as "
        f"NON_RULE with a reason: {uncataloged}"
    )


def test_runtime_emitted_rule_ids_match_their_fstring_prefix() -> None:
    """Every runtime-concatenated rule_id (``f"nz_X_{status}"``) cataloged here
    must have its bare prefix fragment in ``NZ_NON_RULE_LITERALS`` — otherwise
    the AST scan on src/ would silently accept the prefix (which is the only
    thing that appears as a literal Constant in src) AND the full runtime-emitted
    id, leading to a duplicate-emission mask where the prefix is cataloged as
    the rule_id and the full id (an emission that fires only at runtime) is
    silently uncatalogued. Pinned by a guard (AGENTS §2.9 guard-liveness:
    the runtime f-string result lane was the gap the src-only scan missed).

    Each pair: the rule_id MUST start with the prefix (stripped of trailing
    underscore), the prefix MUST be NON_RULE, and the rule_id MUST be cataloged.
    """
    # Each pair: (f_string_prefix_NON_RULE, concrete_runtime_emitted_rule_id).
    # Sourced from the catalog's "Dynamic-emitted rule_ids" section.
    RUNTIME_BUILT_PAIRS = (
        ("nz_target_address_hint_", "nz_target_address_hint_missing"),
        ("nz_target_address_hint_", "nz_target_address_hint_unparsed"),
        ("nz_target_address_hint_", "nz_target_address_hint_compound_target_unparsed"),
        ("nz_lowering_readiness_", "nz_lowering_readiness_blocked_amending_work_resolved_unarchived"),
        ("nz_lowering_readiness_", "nz_lowering_readiness_blocked_non_structural_facet"),
        ("nz_lowering_readiness_", "nz_lowering_readiness_blocked_operation_missing"),
        ("nz_lowering_readiness_", "nz_lowering_readiness_blocked_operation_unclassified"),
        ("nz_lowering_readiness_", "nz_lowering_readiness_blocked_same_label_rebirth_duplicate"),
        ("nz_lowering_readiness_", "nz_lowering_readiness_blocked_target_hint_compound_target_unparsed"),
        ("nz_lowering_readiness_", "nz_lowering_readiness_blocked_target_hint_unparsed"),
        ("nz_operation_surface_", "nz_operation_surface_missing"),
        ("nz_operation_surface_", "nz_operation_surface_unclassified"),
        ("nz_source_change_text_", "nz_source_change_text_observed_single_replacement"),
        ("nz_source_change_text_", "nz_source_change_text_partial_text_change_observed"),
        ("nz_text_replace_witness_support_", "nz_text_replace_witness_support_latest_oracle_and_source_change_observed"),
        ("nz_text_replace_witness_support_", "nz_text_replace_witness_support_source_change_observed_target_mismatch"),
        ("nz_effect_readiness_", "nz_effect_readiness_payload_witness_not_available"),
        ("nz_effect_readiness_", "nz_effect_readiness_operation_not_payload_ready"),
        ("nz_instruction_latest_oracle_text_", "nz_instruction_latest_oracle_text_oracle_new_text_only"),
    )

    for prefix, rule_id in RUNTIME_BUILT_PAIRS:
        assert prefix in NZ_NON_RULE_LITERALS, (
            f"f-string prefix fragment {prefix!r} must be in NZ_NON_RULE_LITERALS or "
            f"the AST scan on src/ would silently catalog it as a rule id (the only "
            f"Ast-visible Constant on a runtime f-string concatenation). The full "
            f"runtime rule_id is {rule_id!r}."
        )
        assert rule_id in _NZ_RULE_SPECS, (
            f"runtime-emitted rule_id {rule_id!r} from f-string template prefix "
            f"{prefix!r} is not cataloged — the contract: a runtime f-string "
            f"emission path anchored by a NZ test must have a paired believed_spec "
            f"hypothesis. The src-scan cannot see it (only the prefix is an AST "
            f"Constant); the test-files-scan pins it. (AGENTS §2.5 / §2.9)"
        )
        # The runtime-concatenated rule_id starts with the prefix-as-fragment
        # (prefix with trailing underscore stripped + the status mapping).
        assert rule_id.startswith(prefix), (
            f"runtime rule_id {rule_id!r} does not start with prefix {prefix!r} "
            f"(naming convention drift between the NON_RULE prefix fragment and the "
            f"cataloged runtime rule_id)."
        )
