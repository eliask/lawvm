"""EE structural-invariant smoke tests.

Pin EE's replayed tree structure against the shared
``lawvm.core.invariant_detectors`` for the full detector set
(``duplicate_label``, ``text_duplication``, ``flattened_sublist_family``,
``sort_order``, ``mixed_hierarchy``, ``illegal_edge``, ``all_tree``).
Drives a known-clean EE statute (verified at zero open divergences against
the RT consolidated oracle) through the full ``replay_ee_to_pit``
production path and asserts the materialized body has zero violations on
each detector.

A broad corpus sweep (30 EE replayable pairs, 2026-06-26) confirmed
``illegal_edge`` and ``all_tree`` are zero-violation on EE — no
jurisdiction-specific edge rules are needed. (The original baseline
smoke reported 25 ``illegal_edge`` violations on the curriculum statute;
that turned out to be a transient pre-fix tree state.) The full
detector set is now CI-enforced.

Pinning these detectors turns any future structural regression in EE (a
duplicate-label collision, an out-of-order sibling, a flattened sublist,
a duplicated text block, a mixed hierarchy, or an illegal parentchild
edge) into a CI failure rather than a silent observation.

Pinned corpus: 130042020016 → 120092023003 (Sekretäri- ja kontoritöö
erialade riiklikppekava) at 2023-09-23. Verified zero open divergences
against the RT consolidated oracle (commit d22c44d0).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lawvm.core.invariant_detectors import run_invariant_detector_messages
from lawvm.estonia.fetch import open_rt_archive
from lawvm.estonia.replay import replay_ee_to_pit, EEPitResult

if TYPE_CHECKING:
    pass


# All structural-tree detectors in the shared core/invariant_detectors
# bundle. As of 2026-06-26 every one of these produces zero violations
# over the EE pinned corpus (and a broader 30-pair sweep), proving the
# FI-centric edge graph in ``_NESTING_ORDER`` is in fact
# EE-compatible — no jurisdiction-specific edge rules needed.
_FULL_DETECTORS = (
    "duplicate_label",
    "text_duplication",
    "flattened_sublist_family",
    "sort_order",
    "mixed_hierarchy",
    "illegal_edge",
    "all_tree",
)

_NEUTRAL_DETECTORS = _FULL_DETECTORS


def _replay_curriculum() -> EEPitResult:
    archive = open_rt_archive(readonly=True)
    result = replay_ee_to_pit(
        "130042020016",
        "2023-09-23",
        archive=archive,
        oracle_id="120092023003",
    )
    assert result.error is None, f"replay_ee_to_pit errored: {result.error!r}"
    assert result.replayed is not None, "replay_ee_to_pit did not produce a tree"
    return result


def test_ee_curriculum_replay_is_clean_against_neutral_structural_detectors() -> None:
    """Drives the curriculum statute (verified 0 divergences against the
    RT consolidated oracle) through the full replay path and asserts the
    shared structural-tree detectors return zero violations.

    Pinned corpus: 130042020016 → 120092023003 at 2023-09-23
    (Sekretäri- ja kontoritöö erialade riiklikppekava, post ``bürooassistent``
    rename convergence from commit d22c44d0). Any future regression in EE
    tree construction that introduces a duplicate-label collision, a
    flattened sublist family, an out-of-order sibling, a duplicated text
    block, a mixed hierarchy, or an illegal parentchild edge is caught
    here as a CI failure rather than a silent observation.

    The 2026-06-26 30-pair broad corpus sweep showed every EE statute in
    the sample was structurally clean (zero violations per detector);
    ``illegal_edge`` and ``all_tree`` are now part of the pinned check
    (no EE-specific edge rules are needed for the curriculum corpus).
    """
    result = _replay_curriculum()
    tree = result.replayed
    assert tree is not None, "replay_ee_to_pit did not produce a tree"
    body = tree.body
    violations_by_detector = {
        detector: run_invariant_detector_messages(body, detector, target_path="")
        for detector in _NEUTRAL_DETECTORS
    }
    failures = [
        f"{detector}: {len(violations)} violation(s)\n  "
        + "\n  ".join(violations[:3])
        for detector, violations in violations_by_detector.items()
        if violations
    ]
    assert not failures, (
        "EE curriculum replay tree has structural-invariant violations on "
        "one or more detectors (corpus pinned to "
        "130042020016 → 120092023003 at 2023-09-23, which tested zero "
        "open divergences against the RT consolidated oracle on "
        "2026-06-26). Either:\n"
        "  - the EE parser/apply introduced a structural regression — fix "
        "the regression; or\n"
        "  - the shared detector's edge graph rejects an EE-legal nest "
        "that needs an EE-specific edge rule — extend the shared "
        "detector's _NESTING_ORDER with the EE-legal parentchild pair.\n\n"
        + "\n".join(failures)
    )


@pytest.mark.parametrize("detector", _NEUTRAL_DETECTORS)
def test_ee_curriculum_replay_each_detector_is_clean(detector: str) -> None:
    """Per-detector parametrized form of the aggregate smoke. Makes the
    per-detector failure attribution explicit so the source of a structural
    regression is named on failure."""
    result = _replay_curriculum()
    tree = result.replayed
    assert tree is not None, "replay_ee_to_pit did not produce a tree"
    body = tree.body
    violations = run_invariant_detector_messages(body, detector, target_path="")
    assert not violations, (
        f"Detector {detector!r} returned {len(violations)} violation(s) on "
        "the pinned EE curriculum tree (130042020016 → 120092023003 at "
        "2023-09-23). First 5:\n  "
        + "\n  ".join(violations[:5])
    )
