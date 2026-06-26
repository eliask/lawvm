"""EE structural-invariant smoke tests.

Pin EE's replayed tree structure against the shared
``lawvm.core.invariant_detectors`` for the jurisdiction-neutral detectors
(``duplicate_label``, ``text_duplication``, ``flattened_sublist_family``,
``sort_order``, ``mixed_hierarchy``). Drives a known-clean EE statute
(verified at zero open divergences against the RT consolidated oracle) through
the full ``replay_ee_to_pit`` production path and asserts the materialized
body has zero violations on each detector.

The ``illegal_edge`` and ``all_tree`` detectors are EXCLUDED because their
edge rules encode FI-centric nesting conventions (item-inside-subsection etc.)
that are legal in Estonian law. Wiring EE-specific edge rules is a separate
deferred work item (see ``ESTONIA_GUARD_LIVENESS_DISCIPLINE.md`` open work).

Pinning the jurisdiction-neutral detectors turns any future structural
regression in EE (a duplicate-label collision, an out-of-order sibling, a
flattened sublist, a duplicated text block) into a CI failure rather than a
silent observation. This is the EE analog of FI's structural-invariant
discipline, minus the FI-specific edge graph.

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


_NEUTRAL_DETECTORS = (
    "duplicate_label",
    "text_duplication",
    "flattened_sublist_family",
    "sort_order",
    "mixed_hierarchy",
)


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
    shared jurisdiction-neutral structural-tree detectors return zero
    violations.

    Pinned corpus: 130042020016 → 120092023003 at 2023-09-23
    (Sekretäri- ja kontoritöö erialade riiklikppekava, post ``bürooassistent``
    rename convergence from commit d22c44d0). Any future regression in EE
    tree construction that introduces a duplicate-label collision, a
    flattened sublist family, an out-of-order sibling, a duplicated text
    block, or a mixed hierarchy is caught here as a CI failure rather than
    a silent observation.

    The ``illegal_edge`` and ``all_tree`` detectors are excluded because they
    encode FI-centric nesting conventions legal in Estonian law; wiring
    EE-specific edge rules is deferred (see
    ``ESTONIA_GUARD_LIVENESS_DISCIPLINE.md`` open work).
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
        "the jurisdiction-neutral detectors (corpus pinned to "
        "130042020016 → 120092023003 at 2023-09-23, which tested zero "
        "open divergences). Either:\n"
        "  - the EE parser/apply introduced a structural regression — fix "
        "the regression; or\n"
        "  - the shared detector's edge graph lacks an EE-legal edge that "
        "neutral detector needs — extend the detector's "
        "jurisdiction-awareness.\n\n"
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
