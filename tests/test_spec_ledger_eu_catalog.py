"""Anti-drift guard for the European Union believed_spec catalog.

The catalog (``src/lawvm/tools/spec_ledger_eu_catalog.py``) carries a one-line
falsifiable hypothesis per EU ``witness_rule_id``. This test pins the cataloged
fraction at 100% of the statically discoverable EU rule-id surface, in both
directions:

* coverage: every ``"eu_…"`` rule-id literal in ``src/lawvm/eu/`` has a
  non-empty catalog entry (no silent gaps when a new rule id is added);
* no dead entries: every catalog key is a real rule-id literal in the EU source
  (no stale hypotheses for rules that were renamed/removed).

Discovery is by AST over the EU frontend, so it tracks the code rather than
a hand-maintained list. Non-rule ``eu_*`` literals (dynamic ``eu_cellar_`` lane
prefix, summary/metadata key strings like ``eu_doc_refs`` /
``eu_replay_applied_op_count`` / ``eu_replay_skipped_op_count``) are documented
as exclusions — see the catalog module docstring and ``_NON_RULE_LITERALS``
below.

This mirrors the EE/SE precedent (``tests/test_spec_ledger_ee_catalog.py``
and ``tests/test_spec_ledger_se_catalog.py``). Pre-iter4 W1 the EU frontend
had NO spec_ledger catalog at all — the AST-discovery guard did not cover EU,
a silent leak per the silent-failure review C1 finding (every other
jurisdiction's ``"xx_…"`` rule id was anti-drift-guarded except EU's).
"""
from __future__ import annotations

import ast
from pathlib import Path

from lawvm.tools.spec_ledger_discovery import format_uncataloged, locate_rule_ids
from lawvm.tools.spec_ledger_eu_catalog import _EU_RULE_SPECS

_EU_DIR = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "eu"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


# --- Dynamic construction prefixes that are NOT believed-spec hypotheses ----------------
# Currently EU has no dynamic per-op-instance ``op_id`` construction family in the
# manner of SE's ``se_official_renumber_{...}`` / EE's ``ee_snap_{...}`` (every
# EU rule_id call site passes a single literal string). The collection is kept
# as a documented-empty baseline so a future dynamic prefix family can be added
# here by name when it appears, mirroring the SE precedent at
# ``tests/test_spec_ledger_se_catalog.py::_DYNAMIC_OP_ID_PREFIXES``.
_DYNAMIC_PREFIXES: tuple[str, ...] = ()


# --- Summary / metadata key strings + f-string literal fragments that are NOT rule ids ---
# These are dict-key strings carrying replay statistics or summary counts, OR
# bare f-string literal-text fragments inside a JoinedStr (e.g. the
# ``"eu_cellar_"`` prefix inside ``f"eu_cellar_{notice_type}_{notice_format}"``
# in cellar.py:771); they happen to start with ``eu_`` but they are NOT
# rule-id hypotheses the compiler is testing against the authoritative EUR-Lex
# consolidation. They are excluded from the rule-id denominator.
_NON_RULE_LITERALS: frozenset[str] = frozenset(
    {
        # cellar.py::summary dict stat key (cellar.py: ``summary["eu_doc_refs"]``)
        "eu_doc_refs",
        # pipeline.py replay metadata keys (``metadata["eu_replay_applied_op_count"]``
        # / ``metadata["eu_replay_skipped_op_count"]``) — stat counters, not rule_ids
        "eu_replay_applied_op_count",
        "eu_replay_skipped_op_count",
        # cellar.py:771 — bare f-string literal-text fragment from
        # ``f"eu_cellar_{notice.notice_type}_{notice.notice_format}"``. This is the
        # dynamic per-notice acquisition-lane identifier prefix; it is NOT a
        # believed-spec hypothesis. The full rule_ids
        # ``eu_cellar_manifestation_option_skipped`` / ``eu_cellar_manifest_request_failed``
        # ARE cataloged (those are real rule_ids, NOT this prefix).
        "eu_cellar_",
        # pipeline.py — bare f-string literal-text fragment from the EV-05
        # ``authorization_rule_id=f"eu_amending_act:{celex}"`` (pipeline.py). This
        # is the per-instance authorization-proof id prefix pointing at the
        # concrete amending act; it is NOT a believed-spec hypothesis. The rule
        # *family* ``eu_amending_act_authorizes_apply`` IS cataloged.
        "eu_amending_act:",
        # eu_ordering.py __all__ function names (``def eu_temporal_key`` /
        # ``def eu_ordering_profile``) — the EU temporal sort key + ordering
        # profile factory, not rule-id hypotheses.
        "eu_temporal_key",
        "eu_ordering_profile",
    }
)


def _is_dynamic_prefix(value: str) -> bool:
    """True if ``value`` is a dynamic ``lane`` / ``op_id`` construction prefix."""
    return any(value.startswith(prefix) for prefix in _DYNAMIC_PREFIXES)


def _is_rule_literal(value: str) -> bool:
    """An ``eu_*`` string literal that is a rule id.

    Excludes summary/metadata key strings and dynamic ``lane`` construction
    prefixes — these are NOT believed-spec hypotheses.
    """
    if not value.startswith("eu_"):
        return False
    if value in _NON_RULE_LITERALS:
        return False
    if _is_dynamic_prefix(value):
        return False
    if "." in value:  # filenames / extensions are never rule ids
        return False
    return True


def _discover_eu_rule_ids() -> set[str]:
    """Every static ``eu_*`` rule-id literal across the EU frontend, via AST."""
    found: set[str] = set()
    for path in sorted(_EU_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_rule_literal(node.value)
            ):
                found.add(node.value)
    return found


def test_eu_dir_present() -> None:
    assert _EU_DIR.is_dir(), _EU_DIR
    assert list(_EU_DIR.glob("*.py")), "no EU modules discovered"


def test_every_discovered_rule_id_is_cataloged() -> None:
    """Coverage / anti-drift: no EU rule id may lack a believed_spec hypothesis."""
    discovered = _discover_eu_rule_ids()
    assert discovered, "AST discovery found no EU rule-id literals"
    uncataloged = sorted(discovered - set(_EU_RULE_SPECS))
    locations = locate_rule_ids(_EU_DIR, uncataloged, repo_root=_SRC_ROOT)
    assert not uncataloged, (
        f"{len(uncataloged)} EU witness rule id(s) have no believed_spec entry in "
        "_EU_RULE_SPECS (cataloged fraction < 100%) (id <- emit site):\n"
        f"{format_uncataloged(uncataloged, locations)}"
    )


def test_no_dead_catalog_entries() -> None:
    """Every catalog key must map to a real rule-id literal in src/lawvm/eu/."""
    discovered = _discover_eu_rule_ids()
    dead = sorted(set(_EU_RULE_SPECS) - discovered)
    assert not dead, (
        f"{len(dead)} _EU_RULE_SPECS key(s) do not correspond to any EU rule-id literal "
        f"(stale/dead entries): {dead}"
    )


def test_all_hypotheses_non_empty() -> None:
    """A cataloged rule must carry a real one-line hypothesis, not a placeholder."""
    empty = sorted(k for k, v in _EU_RULE_SPECS.items() if not v or not v.strip())
    assert not empty, f"empty believed_spec hypotheses: {empty}"


def test_excluded_non_rule_literals_are_not_cataloged() -> None:
    leaked = sorted(_NON_RULE_LITERALS & set(_EU_RULE_SPECS))
    assert not leaked, f"non-rule literals cataloged as rules: {leaked}"
