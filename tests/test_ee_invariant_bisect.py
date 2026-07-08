"""Tests for ``lawvm -j ee invariant-bisect`` (build_ee_invariant_bisect_bundle).

Pins the EE-side bisect bundle shape and behavior against the curriculum
corpus pair, mirroring the FI ``test_invariant_bisect`` patterns.

The build_ee_invariant_bisect_bundle function drives ``replay_ee_to_pit``
cumulatively over the EE amendment chain (via plan_ee_oracle_pair →
parse_ee_amendment_ops → apply_ee_ops) and runs the shared
``core.invariant_detectors`` against each intermediate IRStatute body,
mirroring ``build_invariant_bisect_bundle`` for FI /
``build_uk_invariant_bisect_bundle`` for UK.
"""
from __future__ import annotations

import pytest

from lawvm.tools.invariant_bisect import build_ee_invariant_bisect_bundle


_CURRICULUM_BASE = "130042020016"
_CURRICULUM_ORACLE = "120092023003"
_CURRICULUM_AS_OF = "2023-09-23"


@pytest.fixture(scope="module")
def curriculum_bisect_bundle() -> dict:
    """Build the EE invariant bisect bundle over the curriculum statute.

    Module-scoped: the curriculum pair is known clean at zero open divergences
   (this bundle is a fixpoint), so the bisect bundle is expected to report zero
    violations. Multiple tests reuse the same bundle.
    """
    return build_ee_invariant_bisect_bundle(
        statute_id=_CURRICULUM_BASE,
        as_of=_CURRICULUM_AS_OF,
        target_path="",
        detector="all_tree",
        oracle_id=_CURRICULUM_ORACLE,
    )


def test_ee_bisect_bundle_has_correct_ejurisdiction_surface(curriculum_bisect_bundle: dict) -> None:
    """Bundle identifies itself as EE with the expected surface fields."""
    assert curriculum_bisect_bundle["jurisdiction"] == "ee"
    assert curriculum_bisect_bundle["statute_id"] == _CURRICULUM_BASE
    assert curriculum_bisect_bundle["as_of"] == _CURRICULUM_AS_OF
    assert curriculum_bisect_bundle["mode"] == "legal_pit"
    assert curriculum_bisect_bundle["detector"] == "all_tree"
    assert curriculum_bisect_bundle["target_path"] == "(all)"


def test_ee_bisect_bundle_scan_window_resolves_amendment_chain(curriculum_bisect_bundle: dict) -> None:
    """The scan_window reports both the window count and the total chain count
    of the planned EE amendment chain (cumulative count of
    ``pair_plan.amendments_to_apply``).
    """
    scan = curriculum_bisect_bundle["scan_window"]
    assert scan["after"] == ""
    assert scan["before"] == ""
    # The curriculum pair has exactly 1 amendment effective between base
    # (130042020016, eff 2020-05-03) and oracle (120092023003, eff 2023-09-23):
    # the bürooassistent rename (120092023001, eff 2023-09-23).
    assert scan["total_in_chain"] == 1
    assert scan["count"] == 1


def test_ee_bisect_curriculum_chain_is_clean(curriculum_bisect_bundle: dict) -> None:
    """The curriculum statute is structurally clean under the EE-bisect
    application: no initial violations, no per-step violations.
    """
    assert curriculum_bisect_bundle["initial_clean"] is True
    assert curriculum_bisect_bundle["initial_violations"] == []
    assert curriculum_bisect_bundle["failure_count"] == 0
    assert curriculum_bisect_bundle["first_bad_amendment"] == ""
    assert curriculum_bisect_bundle["monotone_failure"] is False
    assert curriculum_bisect_bundle["transient_failure"] is False
    assert curriculum_bisect_bundle["total_scanned"] == 1
    assert curriculum_bisect_bundle["first_bad_violations"] == []


def test_ee_bisect_steps_each_amendment_with_clean_flag(curriculum_bisect_bundle: dict) -> None:
    """Each step in the bundle reports the amendment source_id, a clean
    flag, a violation count, and the (subset of) violations — mirroring the
    FI ``build_invariant_bisect_bundle`` step shape.
    """
    steps = curriculum_bisect_bundle["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["source_id"] == "120092023001"
    assert step["clean"] is True
    assert step["violation_count"] == 0
    assert step["violations"] == []
    # No error key when the step completed cleanly:
    assert "error" not in step or step["error"] is None or step.get("error") == ""


def test_ee_bisect_after_window_records_first_clean_amendment_at_last_step(curriculum_bisect_bundle: dict) -> None:
    """When the bundle is fully clean, ``first_clean_amendment`` is the last
    scanned amendment (the chain-end bookkeeping from the FI analogue).
    """
    assert curriculum_bisect_bundle["first_clean_amendment"] == "120092023001"


def test_ee_bisect_unknown_after_amendment_raises_systemexit() -> None:
    """An ``--after`` amendment id that is not in the chain raises SystemExit
    (matches FI build_invariant_bisect_bundle behavior).
    """
    with pytest.raises(SystemExit):
        build_ee_invariant_bisect_bundle(
            statute_id=_CURRICULUM_BASE,
            as_of=_CURRICULUM_AS_OF,
            target_path="",
            detector="all_tree",
            after_mid="999999999999",
            oracle_id=_CURRICULUM_ORACLE,
        )


def test_ee_bisect_unknown_before_amendment_raises_systemexit() -> None:
    """An ``--before`` amendment id that is not in the chain raises SystemExit
    (matches FI build_invariant_bisect_bundle behavior).
    """
    with pytest.raises(SystemExit):
        build_ee_invariant_bisect_bundle(
            statute_id=_CURRICULUM_BASE,
            as_of=_CURRICULUM_AS_OF,
            target_path="",
            detector="all_tree",
            before_mid="999999999999",
            oracle_id=_CURRICULUM_ORACLE,
        )
