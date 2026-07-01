"""Coverage / anti-drift guard for the Norway believed_spec catalog.

This is the cataloged-fraction progress metric turned into a guard. It enforces:

* every statically discoverable ``"no_*"`` or ``"no_verify.*"`` rule-id literal in the
  ``norway`` package has a non-empty ``believed_spec`` entry in ``_NO_RULE_SPECS``;
* every catalog key maps back to a real discovered rule id (no dead entries);
* the documented non-rule exclusions are *not* cataloged as fake rules;
* no hypothesis is empty / placeholder.

Discovery is by AST over the norway frontend, so it tracks the code rather than a hand
maintained list. The three ``"no_..."`` literals that are NOT rule ids are explicitly
excluded in ``_NON_RULE_LITERALS`` (the bare replay-status enum, the statsrad
stopped-reason enum, and the ``no_replay_`` family-stratification prefix); see
``spec_ledger_no_catalog.py``'s docstring for the rationale.
"""
from __future__ import annotations

import ast
from pathlib import Path

from lawvm.tools.spec_ledger_discovery import format_uncataloged, locate_rule_ids
from lawvm.tools.spec_ledger_no_catalog import _NO_RULE_SPECS

_NORWAY_DIR = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "norway"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Documented non-rule ``no_*`` literals excluded from the rule-id denominator.  Kept
# in sync with the catalog module docstring; if either changes, this test should be
# updated deliberately.  See spec_ledger_no_catalog.py docstring for the rationale.
_NON_RULE_LITERALS = frozenset(
    {
        "no_amendments",  # replay-status enum (commencement.py _base_replay_status_from_statuses)
        "no_list_items",  # statsrad stopped_reason enum value (statsrad.py)
        "no_replay_",  # family-stratification prefix (grafter.py kind.startswith)
        "no_raw_source_ctx",  # ContextVar name (grafter.py), not a rule_id hypothesis
        "no_affecting_act:",  # EV-05 per-instance authorization_rule_id f-string prefix
        #                       (grafter.py f"no_affecting_act:{statute_id}"); the rule
        #                       FAMILY no_affecting_act_authorizes_apply IS cataloged.
    }
)


def _is_rule_literal(value: str) -> bool:
    """A ``no_*`` / ``no_verify.*`` string literal that is a rule id (not an enum/prefix)."""
    if not (value.startswith("no_") or value.startswith("no_verify.")):
        return False
    if value in _NON_RULE_LITERALS:
        return False
    # Filename-shaped literals (e.g. ``no_*.farchive``) are never rule ids.  The dotted
    # ``no_verify.*`` namespace IS allowed (real rule ids live there).
    if "." in value and not value.startswith("no_verify."):
        return False
    return True


def _discover_no_rule_ids() -> set[str]:
    """Every static ``no_*`` / ``no_verify.*`` rule-id literal across the norway frontend, via AST."""
    found: set[str] = set()
    for path in sorted(_NORWAY_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_rule_literal(node.value)
            ):
                found.add(node.value)
    return found


def test_norway_dir_present() -> None:
    # Guard against the discovery silently finding nothing (wrong path / empty glob),
    # which would make the coverage assertion vacuously pass.
    assert _NORWAY_DIR.is_dir(), _NORWAY_DIR
    assert list(_NORWAY_DIR.glob("*.py")), "no norway modules discovered"


def test_discovery_finds_the_expected_rule_id_population() -> None:
    discovered = _discover_no_rule_ids()
    # Guard the floor so a refactor that silently empties discovery is caught.
    assert len(discovered) >= 60, (
        f"too few NO rule-id literals discovered: {len(discovered)}"
    )


def test_every_discovered_rule_id_is_cataloged() -> None:
    """Coverage / anti-drift: no NO rule id may lack a believed_spec hypothesis."""
    discovered = _discover_no_rule_ids()
    assert discovered, "AST discovery found no NO rule-id literals"
    uncataloged = sorted(discovered - set(_NO_RULE_SPECS))
    locations = locate_rule_ids(_NORWAY_DIR, uncataloged, repo_root=_SRC_ROOT)
    assert not uncataloged, (
        f"{len(uncataloged)} NO witness rule id(s) have no believed_spec entry in "
        "_NO_RULE_SPECS (cataloged fraction < 100%) (id <- emit site):\n"
        f"{format_uncataloged(uncataloged, locations)}"
    )


def test_no_dead_catalog_entries() -> None:
    """Every catalog key must map to a real rule-id literal in src/lawvm/norway/."""
    discovered = _discover_no_rule_ids()
    dead = sorted(set(_NO_RULE_SPECS) - discovered)
    assert not dead, (
        f"{len(dead)} _NO_RULE_SPECS key(s) do not correspond to any NO rule-id literal "
        f"(stale/dead entries): {dead}"
    )


def test_all_hypotheses_non_empty() -> None:
    """A cataloged rule must carry a real one-line hypothesis, not a placeholder."""
    empty = sorted(k for k, v in _NO_RULE_SPECS.items() if not v or not v.strip())
    assert not empty, f"empty believed_spec hypotheses: {empty}"


def test_excluded_non_rule_literals_are_not_cataloged() -> None:
    # The documented non-rule literals must not sneak into the catalog as fake rules.
    leaked = sorted(_NON_RULE_LITERALS & set(_NO_RULE_SPECS))
    assert not leaked, f"non-rule literals cataloged as rules: {leaked}"
