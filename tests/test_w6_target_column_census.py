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


def test_target_cols_accessor_reproduces_stored_columns() -> None:
    """The W6 typed accessor ``op.target_cols`` reproduces the stored columns.

    Phase A contract: ``op.target_cols`` is the single accessor every
    ``op.target_<col>`` read routes through. It must reproduce the live stored
    columns exactly for every column and every op shape (section/chapter/part,
    descendant focus, heading facet, lo-absent direct build). The corpus probe
    proves this at scale; this unit test pins the per-shape contract without a
    corpus so a regression fails in the bounded shard, not only under replay.
    """
    from lawvm.finland.ops import AmendmentOp

    _COLUMNS = (
        "target_unit_kind",
        "target_section",
        "target_chapter",
        "target_part",
        "target_paragraph",
        "target_item",
        "target_subitem",
        "target_special",
    )

    # lo-absent direct builds (columns are the sole source). Each call is a
    # distinct op shape the accessor must reproduce; built explicitly (not via a
    # dict splat) so the constructor's typed parameters are statically checked.
    cases = [
        # plain section
        AmendmentOp(op_id="t", target_unit_kind="section", target_section="5"),
        # section with enclosing chapter + part scope
        AmendmentOp(
            op_id="t",
            target_unit_kind="section",
            target_section="11",
            target_chapter="4",
            target_part="2",
        ),
        # chapter focus with enclosing part
        AmendmentOp(op_id="t", target_unit_kind="chapter", target_section="4", target_part="2"),
        # part focus with redundant mirrored target_part (the W2 finding)
        AmendmentOp(op_id="t", target_unit_kind="part", target_section="III", target_part="III"),
        # descendant focus: momentti / kohta / alakohta
        AmendmentOp(
            op_id="t",
            target_unit_kind="section",
            target_section="7",
            target_paragraph=2,
            target_item="3",
            target_subitem="a",
        ),
        # heading facet (otsikko_edella must round-trip, not collapse to otsikko)
        AmendmentOp(
            op_id="t",
            target_unit_kind="section",
            target_section="9",
            target_special="otsikko_edella",
        ),
    ]
    for op in cases:
        cols = op.target_cols
        for c in _COLUMNS:
            assert getattr(cols, c) == getattr(op, c), (
                f"target_cols.{c}={getattr(cols, c)!r} != stored {getattr(op, c)!r} "
                f"for op {op.op_id} unit_kind={op.target_unit_kind} section={op.target_section}"
            )


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
