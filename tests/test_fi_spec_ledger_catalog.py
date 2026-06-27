"""Anti-drift guard for the Finland believed_spec catalog.

Mirrors ``tests/test_spec_ledger_ee_catalog.py``: it pins the cataloged fraction of
the FI **parse-witness rule-id** surface at 100%, in both directions:

* coverage: every discovered FI parse-witness ``witness_rule_id`` has a non-empty
  believed_spec in ``_FI_RULE_SPECS`` (``finland/spec_ledger_adapter.py``) ∪ ``_FI_RULE_SPECS_SUPPLEMENT``
  (``spec_ledger_fi_catalog_supplement.py``) — no silent gap when a parse rule is added;
* no dead supplement: every ``_FI_RULE_SPECS_SUPPLEMENT`` key corresponds to a real
  discovered FI parse-witness rule id (no stale hypotheses).

Discovery scope — why it is narrower than EE's "every ``ee_*`` literal".  Finland's
frontend carries ~340 ``fi.*`` / ``fi_*`` string literals, but most are **not**
parse-witness rule ids: ``Finding``/observation ``rule_id`` kinds, certificate ids,
agency / court / ministry / committee codes (``fi.agency.*``, ``fi.court.*``,
``fi.ministry.*``, ``fi.committee.*``, ``fi.ev.*`` …), budget-line codes
(``fi.budget.*``), source-pathology/compare classes, and archive filenames
(``fi_government_proposal.farchive``).  The spec ledger's FI firings come from
``compiled_ops[].witness_rule_id`` (see ``fi_ledger_inputs`` in ``spec_ledger.py``),
so the catalogued denominator is the **parse-witness** surface, discovered by AST as
any ``fi.*`` / ``fi_*`` literal that reaches a witness context:

  * the first positional arg of ``_make_witness(...)`` (johtolause/surface_parse.py);
  * ``ParseRule(rule_id=...)`` registrations (johtolause/rule_registry.py);
  * a ``witness_rule_id=`` keyword on op/LO construction;
  * an assignment to a witness rule-id variable (``rid`` / ``inferred_rule_id``) or to
    a ``.witness_rule_id`` attribute (frontend_compile.py).

Module-level ``NAME = "fi…."`` rule-id constants are resolved per file so that
constant-referenced witnesses (e.g. ``FI_RECOVERY_UNCOVERED_BODY_RULE_ID``,
``FI_FALLBACK_EXTRACTION_RECOVERY_RULE_ID``) are still discovered.

The ``.farchive`` literal (an archive filename, not a rule id) is the sole documented
non-rule ``fi_*`` exclusion; it never reaches a witness context, so it is excluded by
construction and asserted absent from the catalog below.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from lawvm.finland.spec_ledger_adapter import _FI_RULE_SPECS
from lawvm.tools.spec_ledger_discovery import format_uncataloged, locate_rule_ids
from lawvm.tools.spec_ledger_fi_catalog_supplement import _FI_RULE_SPECS_SUPPLEMENT

_FINLAND_DIR = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "finland"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Documented non-rule ``fi_*`` literal exclusion: the government-proposal archive
# filename.  It is a ``fi_…`` literal but never a witness rule id; excluded by
# construction (it never reaches a witness context) and asserted catalog-absent below.
_NON_RULE_LITERALS = frozenset({"fi_government_proposal.farchive"})

# Local variable names that, when assigned a string literal, carry a *parse-witness*
# rule id (the surface_parse / frontend_compile convention).  Deliberately excludes a
# bare ``rule_id = "…"`` local — that name is the Finding / oracle-residual convention
# (e.g. ``AgreementResidual(rule_id=…)`` in proof_surfaces.py), not a parse witness, and
# would otherwise pull non-witness adjudication ids into the denominator.  ``ParseRule``
# registry registrations and ``witness_rule_id=`` keywords are captured separately via
# their call/keyword shape, so dropping the bare ``rule_id`` local loses no real witness.
_WITNESS_RULE_VARS = frozenset({"rid", "_rid", "inferred_rule_id"})


def _is_fi_rule_literal(value: str) -> bool:
    """A ``fi.`` / ``fi_`` string literal that may be a parse-witness rule id."""
    if not (value.startswith("fi.") or value.startswith("fi_")):
        return False
    if value in _NON_RULE_LITERALS:
        return False
    if value.endswith(".farchive"):  # archive filenames are never rule ids
        return False
    return True


def _module_rule_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "fi…."`` rule-id constants, for resolving Name witnesses."""
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if isinstance(value, str) and _is_fi_rule_literal(value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = value
    return consts


def _resolve_rule_literal(node: ast.expr, consts: dict[str, str]) -> str | None:
    """Resolve an AST expr to a fi rule literal (direct constant or module-const Name)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if _is_fi_rule_literal(node.value) else None
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def _file_parse_witness_rule_ids(tree: ast.Module) -> set[str]:
    """Parse-witness rule ids reachable in one module's witness contexts."""
    consts = _module_rule_constants(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if fname == "_make_witness" and node.args:
                rid = _resolve_rule_literal(node.args[0], consts)
                if rid:
                    found.add(rid)
            if fname == "ParseRule":
                for kw in node.keywords:
                    if kw.arg == "rule_id":
                        rid = _resolve_rule_literal(kw.value, consts)
                        if rid:
                            found.add(rid)
            for kw in node.keywords:
                if kw.arg == "witness_rule_id":
                    rid = _resolve_rule_literal(kw.value, consts)
                    if rid:
                        found.add(rid)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                is_witness_target = (
                    isinstance(target, ast.Name) and target.id in _WITNESS_RULE_VARS
                ) or (
                    isinstance(target, ast.Attribute) and target.attr == "witness_rule_id"
                )
                if is_witness_target:
                    rid = _resolve_rule_literal(node.value, consts)
                    if rid:
                        found.add(rid)
    return found


@lru_cache(maxsize=1)
def _discover_fi_parse_witness_rule_ids() -> set[str]:
    """Every static FI parse-witness rule id (literal or module-constant), via AST."""
    found: set[str] = set()
    for path in sorted(_FINLAND_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found |= _file_parse_witness_rule_ids(tree)
    return found


def _union_catalog() -> dict[str, str]:
    return {**_FI_RULE_SPECS, **_FI_RULE_SPECS_SUPPLEMENT}


def test_finland_dir_present() -> None:
    # Guard against discovery silently finding nothing (wrong path / empty glob),
    # which would make the coverage assertion vacuously pass.
    assert _FINLAND_DIR.is_dir(), _FINLAND_DIR
    assert list(_FINLAND_DIR.rglob("*.py")), "no finland modules discovered"


def test_every_discovered_parse_witness_rule_id_is_cataloged() -> None:
    """Coverage / anti-drift: no FI parse-witness rule id may lack a believed_spec."""
    discovered = _discover_fi_parse_witness_rule_ids()
    assert discovered, "AST discovery found no FI parse-witness rule-id literals"
    catalog = _union_catalog()
    uncataloged = sorted(discovered - set(catalog))
    locations = locate_rule_ids(
        _FINLAND_DIR, uncataloged, recursive=True, repo_root=_SRC_ROOT
    )
    assert not uncataloged, (
        f"{len(uncataloged)} FI parse-witness rule id(s) have no believed_spec entry in "
        "_FI_RULE_SPECS ∪ _FI_RULE_SPECS_SUPPLEMENT (cataloged fraction < 100%) "
        "(id <- emit site):\n"
        f"{format_uncataloged(uncataloged, locations)}"
    )


def test_no_dead_supplement_entries() -> None:
    """Every supplement key must map to a real discovered FI parse-witness rule id."""
    discovered = _discover_fi_parse_witness_rule_ids()
    dead = sorted(set(_FI_RULE_SPECS_SUPPLEMENT) - discovered)
    assert not dead, (
        f"{len(dead)} _FI_RULE_SPECS_SUPPLEMENT key(s) do not correspond to any FI "
        f"parse-witness rule-id literal (stale/dead entries): {dead}"
    )


def test_fallback_extraction_recovery_is_cataloged_in_supplement() -> None:
    # The fallback-extraction lane (frontend_compile.py Heuristic #29) is the entry
    # this stream added; pin it so a future supplement rewrite cannot silently drop it.
    rid = "fi.fallback_extraction_recovery"
    assert rid in _FI_RULE_SPECS_SUPPLEMENT
    assert _FI_RULE_SPECS_SUPPLEMENT[rid].strip()
    assert rid in _discover_fi_parse_witness_rule_ids()


def test_all_supplement_hypotheses_non_empty() -> None:
    """A cataloged rule must carry a real one-line hypothesis, not a placeholder."""
    empty = sorted(k for k, v in _FI_RULE_SPECS_SUPPLEMENT.items() if not v or not v.strip())
    assert not empty, f"empty believed_spec hypotheses: {empty}"


def test_supplement_does_not_shadow_base_catalog() -> None:
    # The supplement is folded into _FI_RULE_SPECS at the adapter; it must not silently
    # redefine a base hypothesis (that would be an unowned override).
    overlap = sorted(set(_FI_RULE_SPECS) & set(_FI_RULE_SPECS_SUPPLEMENT))
    assert not overlap, f"supplement keys shadow base _FI_RULE_SPECS: {overlap}"


def test_excluded_non_rule_literals_are_not_cataloged() -> None:
    # The documented non-rule literals must not sneak into either catalog as fake rules.
    catalog = _union_catalog()
    leaked = sorted(_NON_RULE_LITERALS & set(catalog))
    assert not leaked, f"non-rule literals cataloged as rules: {leaked}"
