"""Anti-drift guard for the Sweden believed_spec catalog.

The catalog (``src/lawvm/tools/spec_ledger_se_catalog.py``) carries a one-line
falsifiable hypothesis per SE ``witness_rule_id``. This test pins the cataloged
fraction at 100% of the statically discoverable SE rule-id surface, in both
directions:

* coverage: every ``"se_…"`` rule-id literal in ``src/lawvm/sweden/`` has a
  non-empty catalog entry (no silent gaps when a new rule id is added);
* no dead entries: every catalog key is a real rule-id literal in the sweden
  source (no stale hypotheses for rules that were renamed/removed).

Discovery is by AST over the sweden frontend, so it tracks the code rather than
a hand-maintained list. Non-rule ``se_*`` literals (function names exported in
``__init__.py``, locator strings ending in ``_locator``/``_url``, dynamic
``op_id`` construction prefixes) are documented as exclusions — see the catalog
module docstring and ``_NON_RULE_PATTERNS`` below.
"""
from __future__ import annotations

import ast
from pathlib import Path

from lawvm.tools.spec_ledger_discovery import format_uncataloged, locate_rule_ids
from lawvm.tools.spec_ledger_se_catalog import _SE_RULE_SPECS

_SWEDEN_DIR = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "sweden"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


# --- Dynamic construction prefixes that are NOT believed-spec hypotheses ----------------
# These are f-string op_id identifiers constructed at compile time per-op instance
# (e.g. ``se_official_renumber_{sfs_id}_{src}->{dst}``). They identify a specific
# replay op instance, not a believed-spec hypothesis the compiler tests against the
# authoritative consolidation.
_DYNAMIC_OP_ID_PREFIXES: tuple[str, ...] = (
    "se_official_renumber_",
    "se_official_repeal_",
    "se_official_insert_heading_",
    "se_official_insert_appendix_",
    "se_official_text_replace_",
    "se_official_replace_",
    "se_official_insert_",
    "se_reverse_insert_",
    "se_reverse_renumber_",
    "se_reverse_heading_",
    "se_reverse_appendix_",
    "se_reverse_chain::",
    "se_official_act::",  # group_id prefix in LegalOperation construction
    "se_compare_",        # ComparisonNormalizationRule name field (rule_class, not rule_id)
)

# --- Function-name exports and string constants that are NOT rule ids --------------------
# These are ``__all__``-exported function names or archive-locator strings that
# happen to start with ``se_``. They are excluded from the rule-id denominator.
_NON_RULE_LITERALS: frozenset[str] = frozenset(
    {
        # function names exported from __init__.py
        "se_section_text_map",
        "se_heading_before_section_map",
        "se_appendix_text_map",
        "se_legal_operation_from_dict",
        "se_legal_operation_to_dict",
        "se_pdf_bytes_to_text",
        "se_grafter_pdf_",
        "se_source_record_to_dict",
        "se_source_bundle_to_dict",
        "se_sfs_id_from_doc_url",
        "se_official_doc_url",
        "se_rk_current_url",
        "se_official_act_text_to_dict",
        "se_pdf_text_locator",
        "se_pdf_cleanup_locator",
        # function names exported from se_agreement_residuals
        "se_replay_agreement_residuals",
        "se_replay_row_agreement_residual",
        # function names exported from se_coverage_universe
        "se_coverage_universe_entry",
        "se_coverage_universe_root",
        # function names exported from se_overwrite_event_ledger
        "se_store_with_overwrite_event",
        "se_overwrite_event_root",
        # domain/schema string constants (evidence-plane domain labels, not rule_id hypotheses)
        "se_coverage_scan_universe",
        "se_official_artifacts_overwrite_event",
        # agreement-surface name passthrough — the projector's _SE_AGREEMENT_SURFACE
        # constant names the surface, not a rule_id hypothesis
        "se_official_replay",
        # locator string constants (archive path conventions)
        "se_official_ops_locator",
        "se_official_payload_surface_locator",
        "se_official_pdf_locator",
        "se_official_doc_locator",
        "se_official_act_locator",
        "se_official_base_ir_locator",
        "se_official_clause_surface_locator",
        "se_official_elaboration_locator",
        "se_official_effects_plan_locator",
        "se_rk_current_json_locator",
        "se_source_record_locator",
        "se_current_ir_locator",
        "se_bundle_manifest_locator",
        "se_official_pdf_text_url",
        "se_official_pdf_cleaned_text_url",
        "se_backfill_official_checkpoint_locator",
        "se_backfill_official_status_locator",
        "se_backfill_official_history_locator",
        "se_backfill_official_completeness_locator",
        "se_backfill_official_gap_report_locator",
        "se_backfill_official_chunk_plan_locator",
        "se_official_ops_adjudications_locator",
        # truncation artifact
        "se_official_",
    }
)


def _is_dynamic_op_id_prefix(value: str) -> bool:
    """True if ``value`` is a dynamic ``op_id`` construction prefix."""
    return any(value.startswith(prefix) for prefix in _DYNAMIC_OP_ID_PREFIXES)


def _is_rule_literal(value: str) -> bool:
    """A ``se_*`` string literal that is a rule id.

    Excludes function names, locator strings, and dynamic ``op_id`` construction
    prefixes — these are NOT believed-spec hypotheses.
    """
    if not value.startswith("se_"):
        return False
    if value in _NON_RULE_LITERALS:
        return False
    if _is_dynamic_op_id_prefix(value):
        return False
    if "." in value:  # filenames / extensions are never rule ids
        return False
    return True


def _discover_se_rule_ids() -> set[str]:
    """Every static ``se_*`` rule-id literal across the sweden frontend, via AST."""
    found: set[str] = set()
    for path in sorted(_SWEDEN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_rule_literal(node.value)
            ):
                found.add(node.value)
    return found


def test_sweden_dir_present() -> None:
    assert _SWEDEN_DIR.is_dir(), _SWEDEN_DIR
    assert list(_SWEDEN_DIR.glob("*.py")), "no sweden modules discovered"


def test_every_discovered_rule_id_is_cataloged() -> None:
    """Coverage / anti-drift: no SE rule id may lack a believed_spec hypothesis."""
    discovered = _discover_se_rule_ids()
    assert discovered, "AST discovery found no SE rule-id literals"
    uncataloged = sorted(discovered - set(_SE_RULE_SPECS))
    locations = locate_rule_ids(_SWEDEN_DIR, uncataloged, repo_root=_SRC_ROOT)
    assert not uncataloged, (
        f"{len(uncataloged)} SE witness rule id(s) have no believed_spec entry in "
        "_SE_RULE_SPECS (cataloged fraction < 100%) (id <- emit site):\n"
        f"{format_uncataloged(uncataloged, locations)}"
    )


def test_no_dead_catalog_entries() -> None:
    """Every catalog key must map to a real rule-id literal in src/lawvm/sweden/."""
    discovered = _discover_se_rule_ids()
    dead = sorted(set(_SE_RULE_SPECS) - discovered)
    assert not dead, (
        f"{len(dead)} _SE_RULE_SPECS key(s) do not correspond to any SE rule-id literal "
        f"(stale/dead entries): {dead}"
    )


def test_all_hypotheses_non_empty() -> None:
    """A cataloged rule must carry a real one-line hypothesis, not a placeholder."""
    empty = sorted(k for k, v in _SE_RULE_SPECS.items() if not v or not v.strip())
    assert not empty, f"empty believed_spec hypotheses: {empty}"


def test_excluded_non_rule_literals_are_not_cataloged() -> None:
    leaked = sorted(_NON_RULE_LITERALS & set(_SE_RULE_SPECS))
    assert not leaked, f"non-rule literals cataloged as rules: {leaked}"
