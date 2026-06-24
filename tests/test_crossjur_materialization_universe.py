"""Cross-jurisdiction generality of the per-unit materialization-totality lens.

This is the anti-overfitting evidence: the "no hidden universe" invariant
(:mod:`lawvm.core.materialization_universe`) was first built FI-namespaced
against the ``1929/234`` (rikoslaki) ``content=None`` masking witness. These
tests prove the SAME jurisdiction-neutral core — the SAME partition, the SAME
``UniverseSpec`` keystone — runs UNMODIFIED over a REAL Estonian Riigi Teataja
replay tree (39 real sections), and that the FINLAND facade
(:mod:`lawvm.finland.materialization_totality`) is exactly the core bound to the
FI universe domain.

The strong claim demonstrated:
    * the membership partition is ONE implementation over the SHARED ``IRNode``
      tree + SHARED provision-label index, not Finland-specific logic;
    * it fires a named ``SILENTLY_DROPPED_UNIT`` violation when a real Estonian
      section is dropped, exactly as on the FI witness.

Honesty boundary (see module docstring + notes/CROSS_JURISDICTION_GENERALITY.md):
    The ``1929/234`` SILENT-DROP *bug class* is a Finnish replay-apply pathology
    (``content=None`` snapshot supersede). Estonia's materialization replays from
    RT amendment ops, so the silent-drop *surface* differs; this test proves the
    INVARIANT runs and discriminates on a real EE tree (CLEAN when total, FIRES
    when a unit is dropped), NOT that the FI bug exists in Estonia.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.materialization_universe import (
    DEFAULT_UNIVERSE_DOMAIN,
    MaterializationTotalityCode,
    MaterializationTotalityVerdict,
    UnitDisposition,
    check_materialization_totality,
    unit_address_key,
    universe_from_tree,
)


# --------------------------------------------------------------------------- #
# Jurisdiction-neutral core (synthetic, deterministic — no corpus required).  #
# --------------------------------------------------------------------------- #


def _section(label: str, *, tombstone: bool = False) -> IRNode:
    attrs = {"lawvm_repeal_placeholder": "1"} if tombstone else {}
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=f"{label} body", attrs=attrs)


def _chapter(label: str, *sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def test_core_domain_distinguishes_two_jurisdiction_universes() -> None:
    """The same member set under DIFFERENT domains yields DIFFERENT roots.

    This is what lets two jurisdictions share the core without their universe
    roots colliding by accident: the domain is part of the committed value.
    """
    tree = _body(_chapter("1", _section("1"), _section("2")))
    uni_fi = universe_from_tree(
        tree, work_id="w", pit_date="t", domain="fi.materialization_universe.section.v0"
    )
    uni_ee = universe_from_tree(
        tree, work_id="w", pit_date="t", domain="ee.materialization_universe.section.v0"
    )
    # Same membership ...
    assert set(uni_fi.expected_units) == set(uni_ee.expected_units) == {"sec_1", "sec_2"}
    # ... but distinct, self-describing roots.
    assert uni_fi.universe_root != uni_ee.universe_root


def test_core_fires_named_silent_drop_independent_of_jurisdiction() -> None:
    """The core fires a named SILENTLY_DROPPED_UNIT under a neutral domain."""
    base = _body(_chapter("1", _section("1"), _section("2"), _section("3")))
    uni = universe_from_tree(base, work_id="neutral/1", pit_date="t")
    assert uni.domain == DEFAULT_UNIVERSE_DOMAIN
    masked = _body(_chapter("1", _section("1"), _section("3")))  # "2" vanishes
    res = check_materialization_totality(uni, masked)
    assert res.verdict is MaterializationTotalityVerdict.INCOMPLETE
    assert {s.address_key for s in res.shortfalls} == {"sec_2"}
    assert res.shortfalls[0].code is MaterializationTotalityCode.SILENTLY_DROPPED_UNIT


def test_fi_facade_is_the_core_bound_to_the_fi_domain() -> None:
    """The FI facade produces exactly the core's result at the FI domain.

    Proves the FI module is no longer a separate implementation that could drift
    from the neutral core — it IS the core, domain-bound.
    """
    from lawvm.finland import materialization_totality as fi_lens

    tree = _body(_chapter("1", _section("10"), _section("11")))
    uni_fi = fi_lens.universe_from_tree(tree, work_id="1929/234", pit_date="t")
    uni_core = universe_from_tree(
        tree,
        work_id="1929/234",
        pit_date="t",
        unit_kind="section",
        domain="fi.materialization_universe.section.v0",
    )
    assert uni_fi.universe_root == uni_core.universe_root
    assert uni_fi.domain == "fi.materialization_universe.section.v0"


# --------------------------------------------------------------------------- #
# REAL Estonian replay tree (archive-backed; skipped without the RT farchive). #
# --------------------------------------------------------------------------- #


def _ee_archive_or_skip():
    """Open the cached Riigi Teataja farchive or skip (keeps bare worktrees runnable)."""
    import lawvm.tools.ee_self_consistency as ee_sc
    from lawvm.estonia.fetch import open_rt_archive

    db = ee_sc._DEFAULT_DB
    if not Path(db).exists():
        pytest.skip(f"EE Riigi Teataja archive not present: {db}")
    try:
        return open_rt_archive(Path(db))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"EE archive not openable: {type(exc).__name__}")


# Ehitisregister põhimäärus — a clean-replaying RT act used by the EE
# self-consistency suite (tests/test_ee_self_consistency.py). 39 real sections.
_EE_BASE_ID = "119062012020"
_EE_ORACLE_ID = "128092014004"
_EE_AS_OF = "2014-10-01"


def _replay_ee_body():
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = _ee_archive_or_skip()
    try:
        res = replay_ee_to_pit(
            _EE_BASE_ID, _EE_AS_OF, archive=archive, oracle_id=_EE_ORACLE_ID
        )
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()
    if res.error or res.replayed is None:
        pytest.skip(f"EE replay did not materialize a tree: {res.error}")
    return res.replayed.body


def _drop_first_section(node: IRNode, dropped: list[str]) -> IRNode:
    """Rebuild a tree with the first encountered live section removed."""
    new_children: list[IRNode] = []
    for child in node.children:
        if not dropped and child.kind.value == "section" and not child.attrs.get(
            "lawvm_repeal_placeholder"
        ):
            dropped.append(child.label or "")
            continue
        new_children.append(_drop_first_section(child, dropped))
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=dict(node.attrs),
        children=tuple(new_children),
    )


def test_real_ee_replay_self_check_is_total() -> None:
    """The SAME core runs on a REAL Estonian replay tree: every section PRESENT.

    Derives the universe from the materialized EE tree and checks it against
    itself — the unmodified jurisdiction-neutral lens yields TOTAL with every
    real EE section classified PRESENT.
    """
    body = _replay_ee_body()
    uni = universe_from_tree(
        body,
        work_id=f"ee/{_EE_BASE_ID}",
        pit_date=_EE_AS_OF,
        domain="ee.materialization_universe.section.v0",
    )
    # The Ehitisregister act materializes a known multi-section universe.
    assert len(uni) >= 20, f"expected a real multi-section EE universe, got {len(uni)}"
    res = check_materialization_totality(uni, body)
    assert res.verdict is MaterializationTotalityVerdict.TOTAL
    assert res.violation_count == 0
    assert res.present_count == len(uni)
    assert all(
        d == UnitDisposition.PRESENT.value for d in res.dispositions.values()
    )


def test_real_ee_section_drop_fires_named_violation() -> None:
    """Drop ONE real Estonian section -> the lens fires a named silent-drop.

    The decisive cross-jurisdiction fact: the invariant DISCRIMINATES on real
    Estonian data exactly as on the FI 1929/234 witness — a section in the
    declared universe that vanishes from materialization with no tombstone / no
    typed reason is a NAMED ``SILENTLY_DROPPED_UNIT`` violation.
    """
    body = _replay_ee_body()
    uni = universe_from_tree(
        body,
        work_id=f"ee/{_EE_BASE_ID}",
        pit_date=_EE_AS_OF,
        domain="ee.materialization_universe.section.v0",
    )
    dropped: list[str] = []
    masked = _drop_first_section(body, dropped)
    assert dropped, "test setup: expected to drop one real EE section"
    dropped_key = unit_address_key(dropped[0])

    res = check_materialization_totality(uni, masked)
    assert res.verdict is MaterializationTotalityVerdict.INCOMPLETE
    assert res.violation_count == 1
    short = res.shortfalls[0]
    assert short.code is MaterializationTotalityCode.SILENTLY_DROPPED_UNIT
    assert short.address_key == dropped_key
    # Self-evidencing: the EE work id + "SILENT DROP" are named in the detail.
    assert "SILENT DROP" in short.detail
    assert _EE_BASE_ID in short.detail
    # The universe root the claim ranges over is carried + EE-domain-distinct.
    assert res.universe_root == uni.universe_root
