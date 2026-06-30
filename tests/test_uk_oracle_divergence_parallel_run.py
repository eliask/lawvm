"""UK parallel-run equality gate for the Stream-G oracle-divergence kernel.

``uk_oracle_check._classify_divergences`` was re-expressed to call the
jurisdiction-neutral ``core.oracle_divergence.classify_divergences`` kernel. This
test pins the cutover: it runs a FROZEN copy of the legacy classifier
(``_legacy_classify_divergences``, the pre-extraction body verbatim) and the
current kernel-backed production ``_classify_divergences`` on the same inputs and
asserts byte-identical bucket assignments.

Two layers:

* **Synthetic** — a battery of inputs that drives every promotion path
  (only_oracle default deterministic_gap; manual-frontier promotion; deterministic-
  dominates tiebreak; only_replay oracle_suspect with and without the
  not-source-warranted witness; text_diff passthrough; the loose substring covering
  relation). These run everywhere, no corpus needed.

* **Real corpus** — when the UK farchive is present, the same equality is asserted
  on real statutes (including ones exercising deterministic_gap / oracle_suspect /
  text_diff buckets at scale), through the real compile+replay+classify path. This
  is the "identical bucket assignments on the real UK corpus" gate; it is skipped
  (not failed) when the archive is unavailable so the unit shard stays hermetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lawvm.tools.uk_oracle_check import (
    _REPEAL_NOT_WARRANTED_RULE_ID,
    _classify_divergences,
    _is_manual_frontier_rule,
)


def _legacy_classify_divergences(
    *,
    only_replay: set[str],
    only_oracle: set[str],
    text_diff: set[str],
    lowering_rejections: list[dict[str, Any]],
    effect_diagnostics: list[dict[str, Any]],
    effect_feed_parse_rejections: list[dict[str, Any]],
    authority_rejections: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Verbatim pre-extraction body of ``uk_oracle_check._classify_divergences``.

    Frozen reference for the parallel-run equality gate. Do NOT edit to track the
    kernel — its job is to prove the kernel reproduces the OLD behavior exactly.
    """
    manual_frontier_eids: set[str] = set()
    deterministic_gap_eids: set[str] = set()

    all_rejections = (
        lowering_rejections + effect_feed_parse_rejections + authority_rejections
    )
    for rejection in all_rejections:
        rule_id = str(rejection.get("rule_id") or "")
        ap = str(rejection.get("affected_provisions") or "")
        if _is_manual_frontier_rule(rule_id):
            if ap:
                manual_frontier_eids.add(ap)
        elif rule_id and rule_id != _REPEAL_NOT_WARRANTED_RULE_ID:
            if ap:
                deterministic_gap_eids.add(ap)

    repeal_not_warranted_affected: set[str] = set()
    for diag in effect_diagnostics:
        rule_id = str(diag.get("rule_id") or "")
        if rule_id == _REPEAL_NOT_WARRANTED_RULE_ID:
            ap = str(diag.get("affected_provisions") or "")
            if ap:
                repeal_not_warranted_affected.add(ap)

    result: dict[str, list[str]] = {
        "deterministic_gap": [],
        "manual_frontier": [],
        "oracle_suspect": [],
        "text_diff": [],
    }

    for eid in sorted(only_oracle):
        eid_lower = eid.lower()
        covered_by_mf = any(
            mf_ap and (mf_ap.lower() in eid_lower or eid_lower in mf_ap.lower())
            for mf_ap in manual_frontier_eids
        )
        covered_by_det = any(
            det_ap and (det_ap.lower() in eid_lower or eid_lower in det_ap.lower())
            for det_ap in deterministic_gap_eids
        )
        if covered_by_mf and not covered_by_det:
            result["manual_frontier"].append(eid)
        elif covered_by_det:
            result["deterministic_gap"].append(eid)
        else:
            result["deterministic_gap"].append(eid)

    for eid in sorted(only_replay):
        eid_lower = eid.lower()
        covered_by_rnw = any(
            ap and (ap.lower() in eid_lower or eid_lower in ap.lower())
            for ap in repeal_not_warranted_affected
        )
        if covered_by_rnw:
            result["oracle_suspect"].append(eid)
        else:
            result["oracle_suspect"].append(eid)

    for eid in sorted(text_diff):
        result["text_diff"].append(eid)

    return result


def _mf_rej(ap: str) -> dict[str, Any]:
    return {"rule_id": "uk_manual_frontier_commencement_effect_out_of_scope", "affected_provisions": ap}


def _det_rej(ap: str) -> dict[str, Any]:
    return {"rule_id": "uk_some_blocking_rejection", "affected_provisions": ap}


def _rnw_diag(ap: str) -> dict[str, Any]:
    return {"rule_id": _REPEAL_NOT_WARRANTED_RULE_ID, "affected_provisions": ap}


_SYNTHETIC_CASES: list[dict[str, Any]] = [
    # 1. plain default: only_oracle -> deterministic_gap, only_replay -> oracle_suspect
    dict(
        only_oracle={"section-1", "section-3"},
        only_replay={"section-9"},
        text_diff={"section-5"},
        lowering_rejections=[],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    ),
    # 2. manual-frontier promotion
    dict(
        only_oracle={"section-2"},
        only_replay=set(),
        text_diff=set(),
        lowering_rejections=[_mf_rej("section-2")],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    ),
    # 3. deterministic dominates manual-frontier for the same EID
    dict(
        only_oracle={"section-4"},
        only_replay=set(),
        text_diff=set(),
        lowering_rejections=[_mf_rej("section-4"), _det_rej("section-4")],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    ),
    # 4. deterministic rejection alone
    dict(
        only_oracle={"section-6"},
        only_replay=set(),
        text_diff=set(),
        lowering_rejections=[_det_rej("section-6")],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    ),
    # 5. only_replay covered by repeal-not-warranted (witness fires; still suspect)
    dict(
        only_oracle=set(),
        only_replay={"section-8"},
        text_diff=set(),
        lowering_rejections=[],
        effect_diagnostics=[_rnw_diag("section-8")],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    ),
    # 6. loose substring covering (ap is a fragment of the eid)
    dict(
        only_oracle={"section-10-subsection-2"},
        only_replay=set(),
        text_diff=set(),
        lowering_rejections=[_mf_rej("section-10")],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    ),
    # 7. evidence from effect_feed_parse + authority rejection channels
    dict(
        only_oracle={"section-12", "section-14"},
        only_replay=set(),
        text_diff=set(),
        lowering_rejections=[],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[_det_rej("section-12")],
        authority_rejections=[_mf_rej("section-14")],
    ),
    # 8. empty everything
    dict(
        only_oracle=set(),
        only_replay=set(),
        text_diff=set(),
        lowering_rejections=[],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    ),
]


@pytest.mark.parametrize("case", _SYNTHETIC_CASES)
def test_synthetic_parallel_run_equality(case: dict[str, Any]) -> None:
    legacy = _legacy_classify_divergences(**case)
    kernel = _classify_divergences(**case)
    assert kernel == legacy


# --- real-corpus gate -------------------------------------------------------

def _resolve_uk_db() -> Path | None:
    """Locate the UK farchive: repo-local first, then the canonical data root."""
    import os

    repo_root = Path(__file__).resolve().parents[1]
    candidates = [repo_root / "data" / "uk_legislation.farchive"]
    canonical = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if canonical:
        candidates.append(Path(canonical) / "data" / "uk_legislation.farchive")
    for cand in candidates:
        if cand.exists():
            return cand
    return None


_UK_DB = _resolve_uk_db()

_REAL_SIDS = [
    "asp/2000/1",      # deterministic_gap + text_diff
    "ukpga/1840/110",  # oracle_suspect
    "ukpga/1880/20",   # oracle_suspect (large)
    "ukpga/1845/20",   # deterministic_gap + text_diff (large)
    "nia/2007/2",      # deterministic_gap (large)
]


@pytest.mark.skipif(
    _UK_DB is None, reason="UK farchive unavailable; corpus gate is hermetic-skipped"
)
@pytest.mark.parametrize("sid", _REAL_SIDS)
def test_real_corpus_parallel_run_equality(
    sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real compile+replay+classify: kernel-backed production buckets == legacy.

    Drives ``_compute_uk_divergence_state`` (production, kernel-backed) and spies
    on its ``_classify_divergences`` call to capture the EXACT membership sets +
    evidence rows the real compile/replay path produced, then re-runs the FROZEN
    legacy classifier on those inputs and asserts identical bucket assignments —
    the real UK corpus parallel-run gate.
    """
    import lawvm.tools.uk_oracle_check as oc
    from lawvm.tools.uk_oracle_check import _compute_uk_divergence_state

    orig = oc._classify_divergences
    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> dict[str, list[str]]:
        captured.update(kwargs)
        return orig(**kwargs)

    monkeypatch.setattr(oc, "_classify_divergences", _spy)
    state = _compute_uk_divergence_state(sid, db_path=_UK_DB)
    monkeypatch.undo()

    assert not state.error, state.error
    assert captured, "production path did not call _classify_divergences"

    kernel_buckets = orig(**captured)
    legacy_buckets = _legacy_classify_divergences(**captured)
    # Byte-identical bucket assignments: kernel-backed == frozen legacy.
    assert kernel_buckets == legacy_buckets
    # And the spied production result matches (determinism over the same inputs).
    assert {k: sorted(v) for k, v in kernel_buckets.items()} == {
        k: sorted(v) for k, v in state.buckets.items()
    }
