"""Coverage / anti-drift guard for the UK believed_spec catalog.

This is the cataloged-fraction progress metric turned into a guard. It enforces:

* every UK ``*_RULE_ID`` *string constant* declared in the ``uk_legislation``
  package has a non-empty ``believed_spec`` entry in ``_UK_RULE_SPECS``;
* every ``uk_manual_frontier_*`` literal classification id in that package is
  likewise cataloged;
* every catalog key maps back to a real discovered rule id (no dead entries);
* dynamically-constructed rule-id families (prefix + runtime suffix) are
  excluded *deliberately* via ``_UK_DYNAMIC_RULE_ID_PREFIXES`` rather than faked,
  and those prefixes are real (a constructing f-string exists in the package).

When a new UK rule id is added, this test fails until its hypothesis is cataloged
— that is the anti-drift contract.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import lawvm.uk_legislation as uk_pkg
from lawvm.tools.spec_ledger_uk_catalog import (
    _UK_DYNAMIC_RULE_ID_PREFIXES,
    _UK_DYNAMIC_RULE_ID_SUFFIXES,
    _UK_RULE_SPECS,
)

_UK_PKG_DIR = Path(uk_pkg.__file__).resolve().parent
_FRONTIER_LITERAL_RE = re.compile(r'"(uk_manual_frontier_[a-z0-9_]+)"')


def _module_rule_id_constants() -> set[str]:
    """String values of every module-level ``*_RULE_ID`` constant in the package."""
    values: set[str] = set()
    for mod_info in pkgutil.iter_modules(uk_pkg.__path__):
        module = importlib.import_module(f"lawvm.uk_legislation.{mod_info.name}")
        for attr, val in vars(module).items():
            if attr.endswith("_RULE_ID") and isinstance(val, str):
                values.add(val)
    return values


def _manual_frontier_literals() -> set[str]:
    """The ``uk_manual_frontier_*`` literal classification ids in the package."""
    found: set[str] = set()
    for path in _UK_PKG_DIR.glob("*.py"):
        found.update(_FRONTIER_LITERAL_RE.findall(path.read_text(encoding="utf-8")))
    return found


def _is_dynamic(rule_id: str, statically_discovered: set[str]) -> bool:
    if any(rule_id.startswith(p) for p in _UK_DYNAMIC_RULE_ID_PREFIXES):
        return True
    # ``<base>_unresolved`` variants are synthesized from a base at emission time;
    # treat as dynamic only when the id is not itself a statically-declared constant.
    if rule_id not in statically_discovered and any(
        rule_id.endswith(s) for s in _UK_DYNAMIC_RULE_ID_SUFFIXES
    ):
        return True
    return False


def _discovered() -> set[str]:
    return _module_rule_id_constants() | _manual_frontier_literals()


def test_discovery_finds_the_expected_rule_id_population() -> None:
    consts = _module_rule_id_constants()
    frontier = _manual_frontier_literals()
    # Guard the floor so a refactor that silently empties discovery is caught.
    assert len(consts) >= 270, f"too few *_RULE_ID constants discovered: {len(consts)}"
    assert len(frontier) >= 80, f"too few manual-frontier ids discovered: {len(frontier)}"


def test_every_discovered_static_rule_id_is_cataloged() -> None:
    discovered = _discovered()
    missing = sorted(
        rid
        for rid in discovered
        if rid not in _UK_RULE_SPECS and not _is_dynamic(rid, discovered)
    )
    assert not missing, (
        "UK rule ids discovered in uk_legislation/ but missing a believed_spec "
        f"entry in _UK_RULE_SPECS: {missing}"
    )


def test_every_cataloged_spec_is_non_empty() -> None:
    empty = sorted(k for k, v in _UK_RULE_SPECS.items() if not v.strip())
    assert not empty, f"believed_spec entries must be non-empty: {empty}"


def test_no_dead_catalog_entries() -> None:
    discovered = _discovered()
    dead = sorted(k for k in _UK_RULE_SPECS if k not in discovered)
    assert not dead, (
        "catalog keys with no matching discoverable rule-id constant/literal "
        f"(dead entries): {dead}"
    )


def test_dynamic_prefixes_are_real() -> None:
    """Each excluded dynamic prefix must correspond to an actual constructing site."""
    blob = "\n".join(
        path.read_text(encoding="utf-8") for path in _UK_PKG_DIR.glob("*.py")
    )
    for prefix in _UK_DYNAMIC_RULE_ID_PREFIXES:
        assert f'f"{prefix}' in blob or f"f'{prefix}" in blob, (
            f"dynamic prefix {prefix!r} is not actually constructed anywhere — "
            "remove it from _UK_DYNAMIC_RULE_ID_PREFIXES or fix the prefix"
        )
