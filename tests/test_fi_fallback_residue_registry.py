"""CI guard for the FI johtolause fallback-residue registry.

Two layers:

* Registry-integrity tests (always run, corpus-free): the closed set partitions
  its reason space, baselines are internally consistent, ``classify_decline_reason``
  is total over registered reasons and ``None`` (never a guess) over unknowns.

* Corpus closure + count guard (archive-gated): runs the full-corpus audit and
  asserts (a) EVERY live generalized decline reason maps to a registered class —
  the closed-set guarantee; (b) the total declined count and per-class counts do
  not exceed the pinned baselines without a deliberate human bump — the
  no-silent-fallback-growth guarantee. Skips cleanly when the canonical corpus is
  not linked, but MUST run and pass when it is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.finland.johtolause.fallback_residue import (
    FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE,
    FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0,
    FI_JOHTOLAUSE_FALLBACK_RESIDUE_TOTAL_BASELINE,
    audit_corpus,
    classify_decline_reason,
    registered_class_ids,
    registered_reasons,
)


# ---------------------------------------------------------------------------
# Registry integrity (corpus-free, always run)
# ---------------------------------------------------------------------------
def test_reason_space_is_partitioned() -> None:
    """No generalized reason is claimed by two classes; index covers all reasons."""
    seen: dict[str, str] = {}
    for rc in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0:
        for reason in rc.reasons:
            assert reason not in seen, (
                f"reason {reason!r} claimed by both {seen[reason]!r} and "
                f"{rc.class_id!r}"
            )
            seen[reason] = rc.class_id
    assert set(seen) == set(registered_reasons())


def test_class_ids_are_unique() -> None:
    ids = registered_class_ids()
    assert len(ids) == len(set(ids))


def test_classify_is_total_over_registered_and_none_over_unknown() -> None:
    for reason in registered_reasons():
        cid = classify_decline_reason(reason)
        assert cid is not None
        assert cid in set(registered_class_ids())
    # An unknown reason must map to None — never silently bucketed.
    assert classify_decline_reason("a reason that does not exist") is None
    assert classify_decline_reason("") is None


def test_baselines_are_internally_consistent() -> None:
    by_class = {
        rc.class_id: rc.baseline_count
        for rc in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0
    }
    assert by_class == FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE
    assert (
        sum(FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE.values())
        == FI_JOHTOLAUSE_FALLBACK_RESIDUE_TOTAL_BASELINE
    )
    for rc in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0:
        assert rc.future_path in {"own", "adjudicate", "keep_legacy"}
        assert rc.summary.strip()
        assert rc.strict_disposition.strip()
        assert rc.reasons, f"{rc.class_id} has no reasons"


# ---------------------------------------------------------------------------
# Corpus closure + count guard (archive-gated)
# ---------------------------------------------------------------------------
def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


@pytest.fixture(scope="module")
def _audit():
    return audit_corpus()


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_corpus_closed_set_every_reason_registered(_audit) -> None:
    """Closed-set guarantee: no live decline reason is unregistered."""
    assert _audit.unregistered_reasons == [], (
        "UNREGISTERED fallback-residue reason(s) found — the closed set must cover "
        "every decline the parser surfaces to the fallback boundary. Register each "
        "in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0:\n  "
        + "\n  ".join(_audit.unregistered_reasons)
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_corpus_total_declined_does_not_exceed_baseline(_audit) -> None:
    """No silent growth in the fallback set beyond the pinned total baseline."""
    assert _audit.total_declined <= FI_JOHTOLAUSE_FALLBACK_RESIDUE_TOTAL_BASELINE, (
        f"fallback declined count {_audit.total_declined} exceeds pinned baseline "
        f"{FI_JOHTOLAUSE_FALLBACK_RESIDUE_TOTAL_BASELINE}. If this growth is "
        "intended, bump the per-class baseline_count(s) in "
        "FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0 deliberately."
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_corpus_per_class_counts_do_not_exceed_baseline(_audit) -> None:
    """Per-class no-silent-growth guard. A class over baseline fails loudly."""
    overruns = {
        cid: (n, FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE.get(cid, 0))
        for cid, n in _audit.class_counts.items()
        if n > FI_JOHTOLAUSE_FALLBACK_RESIDUE_BASELINE.get(cid, 0)
    }
    assert not overruns, (
        "per-class fallback counts exceed baseline (live, baseline): "
        + ", ".join(f"{cid}={live}>{base}" for cid, (live, base) in overruns.items())
        + ". Bump baseline_count deliberately if intended."
    )
