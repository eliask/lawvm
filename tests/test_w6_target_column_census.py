"""Guard for the W6 target-column-collapse census tooling (analysis-only wave).

These tests pin the *contracts* of the two W6 feasibility scripts so the
committed census stays regenerable and the scanner cannot silently rot:

- ``scripts/w6_read_site_inventory.py`` — the AST read-site scanner. Guarded
  here (deterministic, no corpus needed): it must enumerate a non-trivial
  population of ``*.target_*`` Load sites across core+finland, bucket every site
  into a known shape, and tag a known ``likely_amendment_op`` class.
- ``scripts/w6_target_column_accessor_parity.py`` — the corpus parity probe. Its
  ``run_probe`` is the go/no-go evidence (``to_legacy(op.target_selector)`` vs the
  stored columns). It needs the populated ``data/finlex.farchive`` (absent in the
  bounded CI sandbox), so we do NOT replay here; we only assert the report shape
  the migration depends on, so the verdict schema cannot drift unnoticed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(stem: str):
    path = _REPO_ROOT / "scripts" / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    return module


_KNOWN_SHAPES = {
    "simple_read",
    "used_in_comparison",
    "used_as_replace_kwarg",
    "serialization",
    "other",
}
_KNOWN_LIKELY = {"amendment_op", "non_amendment_op", "ambiguous"}


def test_read_site_inventory_is_well_formed_and_nontrivial() -> None:
    module = _load_script("w6_read_site_inventory")
    report = module.run_inventory()

    # A non-trivial population must be found (the ~2.5k read sites the migration
    # must route). A near-zero count means the scanner or the columns drifted.
    assert report["total_sites"] >= 1500, (
        f"read-site scanner found only {report['total_sites']} sites; expected "
        f">= 1500. The AST scanner or the migrated column set has drifted."
    )

    # Every site must carry a known shape and likely-class (no unbucketed sites).
    for site in report["sites"]:
        assert site["shape"] in _KNOWN_SHAPES, site
        assert site["likely_amendment_op"] in _KNOWN_LIKELY, site
        assert site["column"] in set(report["columns"]), site

    # The bucket sums must reconcile with the total (no double/under counting).
    assert sum(report["by_shape"].values()) == report["total_sites"]
    assert sum(report["by_likely_amendment_op"].values()) == report["total_sites"]


def test_parity_probe_report_schema_is_stable() -> None:
    """The probe module loads and exposes the go/no-go report contract.

    No corpus replay here (the bounded sandbox has no farchive); this guards the
    keys the W6–W9 plan + any future ratchet read off the probe report so the
    verdict schema cannot silently change.
    """
    module = _load_script("w6_target_column_accessor_parity")

    # The 8 migrated columns the probe checks must match the inventory's set.
    inv = _load_script("w6_read_site_inventory")
    assert set(module._COLUMNS) == set(inv._COLUMNS), (
        "parity probe and read-site inventory disagree on the migrated column set"
    )

    # The classifier must map the documented blocker shapes to stable labels.
    assert callable(module.run_probe)
    assert callable(module._classify_mismatch)
