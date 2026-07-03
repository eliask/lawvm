"""Anti-drift guard for the Estonia believed_spec catalog.

The catalog (`src/lawvm/tools/spec_ledger_ee_catalog.py`) carries a one-line falsifiable
hypothesis per EE ``witness_rule_id``. This test pins the cataloged fraction at 100% of
the statically discoverable EE rule-id surface, in both directions:

* coverage: every ``"ee_…"`` rule-id literal in ``src/lawvm/estonia/`` has a non-empty
  catalog entry (no silent gaps when a new rule id is added);
* no dead entries: every catalog key is a real rule-id literal in the estonia source
  (no stale hypotheses for rules that were renamed/removed).

Discovery is by AST over the estonia frontend, so it tracks the code rather than a hand
maintained list. Two ``"ee_…"`` literals are documented non-rule exclusions (an archive
filename and the dynamic ``ee_snap_`` op-id prefix); see the catalog module docstring.
"""
from __future__ import annotations

import ast
from pathlib import Path

from lawvm.tools.spec_ledger_discovery import format_uncataloged, locate_rule_ids
from lawvm.tools.spec_ledger_ee_catalog import _EE_RULE_SPECS

_ESTONIA_DIR = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "estonia"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Documented non-rule ``ee_*`` literals excluded from the rule-id denominator.  Kept in
# sync with the catalog module docstring; if either changes, this test should be updated
# deliberately.
_NON_RULE_LITERALS = frozenset(
    {
        "ee_riigiteataja.farchive",  # default archive filename (fetch.py)
        "ee_fetch_curl_",            # tempfile prefix (fetch.py)
        "ee_snap_",                  # dynamic replay op_id prefix (grafter.py f-string)
        # __all__ function-name exports in replayability_frontier.py (not rule ids):
        "ee_replayability_frontier_for_corpus",
        "ee_replayability_states_to_report",
        # __all__ function-name export in label_algebra.py (#186 LabelAlgebra) — the
        # public EE label parse function, not a witness rule_id.
        "ee_parse_label",
        # Confirmed non-rule literals (a coverage tag, a ContextVar name, an
        # f-string label fragment) — NOT witness rule_ids. Pre-existing in the
        # source; added here when the parity-integration audit surfaced them.
        "ee_mentioned",              # coverage tag in a frozenset (coverage_audit.py)
        "ee_raw_source_ctx",         # ContextVar name (peg.py)
        "ee_produced_label=",        # f-string evidence fragment (coverage_audit.py)
        "ee_amending_act:",          # dynamic proof-carrier authorization id prefix
    }
)


def _is_rule_literal(value: str) -> bool:
    """An ``ee_*`` string literal that is a rule id (not a filename / dynamic op-id)."""
    if not value.startswith("ee_"):
        return False
    if value in _NON_RULE_LITERALS:
        return False
    if "." in value:  # filenames / extensions are never rule ids
        return False
    return True


def _discover_ee_rule_ids() -> set[str]:
    """Every static ``ee_*`` rule-id literal across the estonia frontend, via AST."""
    found: set[str] = set()
    for path in sorted(_ESTONIA_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_rule_literal(node.value)
            ):
                found.add(node.value)
    return found


def test_estonia_dir_present() -> None:
    # Guard against the discovery silently finding nothing (wrong path / empty glob),
    # which would make the coverage assertion vacuously pass.
    assert _ESTONIA_DIR.is_dir(), _ESTONIA_DIR
    assert list(_ESTONIA_DIR.glob("*.py")), "no estonia modules discovered"


def test_every_discovered_rule_id_is_cataloged() -> None:
    """Coverage / anti-drift: no EE rule id may lack a believed_spec hypothesis."""
    discovered = _discover_ee_rule_ids()
    assert discovered, "AST discovery found no EE rule-id literals"
    uncataloged = sorted(discovered - set(_EE_RULE_SPECS))
    locations = locate_rule_ids(_ESTONIA_DIR, uncataloged, repo_root=_SRC_ROOT)
    assert not uncataloged, (
        f"{len(uncataloged)} EE witness rule id(s) have no believed_spec entry in "
        "_EE_RULE_SPECS (cataloged fraction < 100%) (id <- emit site):\n"
        f"{format_uncataloged(uncataloged, locations)}"
    )


def test_no_dead_catalog_entries() -> None:
    """Every catalog key must map to a real rule-id literal in src/lawvm/estonia/."""
    discovered = _discover_ee_rule_ids()
    dead = sorted(set(_EE_RULE_SPECS) - discovered)
    assert not dead, (
        f"{len(dead)} _EE_RULE_SPECS key(s) do not correspond to any EE rule-id literal "
        f"(stale/dead entries): {dead}"
    )


def test_all_hypotheses_non_empty() -> None:
    """A cataloged rule must carry a real one-line hypothesis, not a placeholder."""
    empty = sorted(k for k, v in _EE_RULE_SPECS.items() if not v or not v.strip())
    assert not empty, f"empty believed_spec hypotheses: {empty}"


def test_excluded_non_rule_literals_are_not_cataloged() -> None:
    # The documented non-rule literals must not sneak into the catalog as fake rules.
    leaked = sorted(_NON_RULE_LITERALS & set(_EE_RULE_SPECS))
    assert not leaked, f"non-rule literals cataloged as rules: {leaked}"
