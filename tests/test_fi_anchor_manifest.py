"""Unit tests for the frozen anchor manifest + touch-relation engine (#183).

Covers the FABLE §3.3 attribution calculus with small synthetic anchor
fixtures (no corpus builds): spontaneous appearance / healing convict the
oracle, a persistent post-touch divergence localizes a candidate replay bug to
its window, standing-untouched divergences are oracle-side, and a per-anchor
commensurability-suspect anchor gates every divergence to temporal_mismatch.
Also covers the manifest content-address diff (cnf-drift vs editorial-only
artifact-drift — the #137 predict-then-compare gate).
"""
from __future__ import annotations

from lawvm.tools.fi_anchor_manifest import (
    AnchorObservation,
    StatuteAttribution,
    _cnf_hash_of_map,
    attribute_divergences,
    diff_manifest,
    observation_to_residual,
    touch_set,
)


def _obs(
    version_tag: str,
    as_of: str,
    *,
    penalized: set[str] | None = None,
    replay_text: dict[str, str] | None = None,
    oracle_suspect: str | None = None,
    struct_sim: float = 1.0,
) -> AnchorObservation:
    penalized = penalized or set()
    replay_text = replay_text or {}
    return AnchorObservation(
        version_tag=version_tag,
        amendment_id="amend-" + version_tag,
        as_of=as_of,
        struct_sim=struct_sim,
        n_sections=max(len(replay_text), 1),
        n_penalized=len(penalized),
        penalized_keys=frozenset(penalized),
        replay_text=dict(replay_text),
        oracle_suspect=oracle_suspect,
        status="OK",
    )


def _verdicts_by_key(obs_list) -> dict[str, str]:
    return {o.section_key: o.verdict for o in obs_list}


# ---------------------------------------------------------------------------
# touch relation
# ---------------------------------------------------------------------------


def test_touch_set_flags_only_changed_and_appearing_keys() -> None:
    prev = _obs("v1", "2000-01-01", replay_text={"1": "x", "2": "y", "3": "keep"})
    cur = _obs("v2", "2001-01-01", replay_text={"1": "x", "2": "CHANGED", "4": "new"})
    touched = touch_set(prev, cur)
    # 2 changed, 3 disappeared, 4 appeared; 1 stable.
    assert touched == frozenset({"2", "3", "4"})
    assert "1" not in touched


# ---------------------------------------------------------------------------
# §3.3 attribution: the four typed cases
# ---------------------------------------------------------------------------


def test_spontaneous_appearance_convicts_oracle() -> None:
    # Key "1" matches at k-1, replay wording is IDENTICAL across the window
    # (untouched), yet it is penalized (diverges) at k ⇒ oracle changed an
    # untouched unit ⇒ oracle_suspect (non-billable to replay).
    a = _obs("v1", "2000-01-01", replay_text={"1": "same", "2": "s2"})
    b = _obs(
        "v2",
        "2001-01-01",
        penalized={"1"},
        replay_text={"1": "same", "2": "s2"},
    )
    obs = attribute_divergences("100", [a, b])
    verdicts = _verdicts_by_key(obs)
    assert verdicts["1"] == "oracle_suspect_spontaneous_appearance"
    # attribution never names a touching amendment for an oracle conviction
    appearance = next(o for o in obs if o.section_key == "1")
    assert appearance.touching_amendments == ()


def test_spontaneous_healing_convicts_oracle() -> None:
    # Key "1" diverges at k, replay is UNTOUCHED into k+1, and matches at k+1 ⇒
    # anchor k was wrong at the unit ⇒ oracle_suspect.
    a = _obs("v1", "2000-01-01", replay_text={"1": "same"})
    b = _obs("v2", "2001-01-01", penalized={"1"}, replay_text={"1": "same"})
    c = _obs("v3", "2002-01-01", replay_text={"1": "same"})
    obs = attribute_divergences("100", [a, b, c])
    verdicts = _verdicts_by_key(obs)
    # b's divergence at "1" is matched at a (appearance) — but appearance fires
    # first because matched_prev holds and the unit is untouched. Both the
    # appearance and healing branches convict the oracle, so assert it is an
    # oracle_suspect verdict (never a replay bug).
    assert verdicts["1"].startswith("oracle_suspect")


def test_spontaneous_healing_when_no_prior_match() -> None:
    # Key "1" diverges at the FIRST scored anchor (no prior), is untouched into
    # k+1, and heals at k+1. matched_prev is False, so the appearance branch is
    # skipped and the healing branch must fire ⇒ oracle_suspect_spontaneous_healing.
    a = _obs("v1", "2000-01-01", penalized={"1"}, replay_text={"1": "same"})
    b = _obs("v2", "2001-01-01", replay_text={"1": "same"})
    obs = attribute_divergences("100", [a, b])
    verdicts = _verdicts_by_key(obs)
    assert verdicts["1"] == "oracle_suspect_spontaneous_healing"


def test_persistent_post_touch_divergence_is_candidate_bug() -> None:
    # Key "1" is TOUCHED in the window (replay wording changes) and then stays
    # diverged ⇒ candidate replay bug localized to that window's amendment.
    a = _obs("v1", "2000-01-01", replay_text={"1": "before"})
    b = _obs(
        "v2", "2001-01-01", penalized={"1"}, replay_text={"1": "after-edit"}
    )
    c = _obs(
        "v3", "2002-01-01", penalized={"1"}, replay_text={"1": "after-edit"}
    )
    obs = attribute_divergences("100", [a, b, c])
    bug = next(o for o in obs if o.section_key == "1" and o.window.startswith("2000"))
    assert bug.verdict == "candidate_replay_bug_persistent_post_touch"
    # the bug is localized to the touching amendment
    assert bug.touching_amendments == ("amend-v2",)


def test_standing_untouched_divergence_is_oracle_side() -> None:
    # Key "1" diverges at the base anchor and replay NEVER changes it across the
    # whole life (single anchor, or identical wording) ⇒ standing untouched
    # divergence ⇒ oracle-side, not a bug.
    a = _obs("v1", "2000-01-01", penalized={"1"}, replay_text={"1": "stable"})
    b = _obs("v2", "2001-01-01", penalized={"1"}, replay_text={"1": "stable"})
    obs = attribute_divergences("100", [a, b])
    # At the base anchor (no prior, untouched-forever), the divergence is
    # standing-untouched. At the later anchor "1" matched_prev is False (was
    # penalized at a) and untouched, so appearance is skipped; it is standing.
    base = next(o for o in obs if o.window.startswith("-.."))
    assert base.verdict == "oracle_suspect_standing_untouched"
    assert base.touching_amendments == ()


# ---------------------------------------------------------------------------
# §5.4 per-anchor suspect gating
# ---------------------------------------------------------------------------


def test_per_anchor_commensurability_suspect_gates_all_divergences() -> None:
    # When the anchor itself is commensurability-suspect, EVERY divergence at it
    # is temporal_mismatch — the touch relation is not even consulted, so a unit
    # that would otherwise look like a candidate bug is gated out.
    a = _obs("100", "2000-01-01", replay_text={"1": "before"})
    b = _obs(
        "100",
        "2001-01-01",
        penalized={"1", "2"},
        replay_text={"1": "after", "2": "after"},
        oracle_suspect="2011/171 eff 2012-01-01 > cutoff 2011-06-01",
    )
    obs = attribute_divergences("100", [a, b])
    verdicts = _verdicts_by_key(obs)
    assert verdicts["1"] == "temporal_mismatch_commensurability"
    assert verdicts["2"] == "temporal_mismatch_commensurability"


def test_gate_clean_vs_candidate_bug_statute_property() -> None:
    a = _obs("v1", "2000-01-01", replay_text={"1": "before"})
    b = _obs(
        "v2", "2001-01-01", penalized={"1"}, replay_text={"1": "after"}
    )
    observations = attribute_divergences("100", [a, b])
    attr = StatuteAttribution(
        sid="100", anchors=(a, b), observations=tuple(observations)
    )
    # a touched-then-diverged unit is a candidate bug ⇒ NOT gated clean
    assert attr.candidate_bug_observations
    assert not attr.is_gated_clean
    assert attr.has_hidden_mid_life_divergence is False  # min == latest here

    # an all-oracle-convicted statute is gated clean
    a2 = _obs("v1", "2000-01-01", replay_text={"1": "same"})
    b2 = _obs("v2", "2001-01-01", penalized={"1"}, replay_text={"1": "same"})
    c2 = _obs("v3", "2002-01-01", replay_text={"1": "same"})
    obs2 = attribute_divergences("100", [a2, b2, c2])
    attr2 = StatuteAttribution(sid="100", anchors=(a2, b2, c2), observations=tuple(obs2))
    assert not attr2.candidate_bug_observations
    assert attr2.is_gated_clean


def test_hidden_mid_life_divergence_detection() -> None:
    a = _obs("v1", "2000-01-01", struct_sim=1.0, replay_text={"1": "x"})
    b = _obs("v2", "2001-01-01", struct_sim=0.5, replay_text={"1": "y"})
    c = _obs("v3", "2002-01-01", struct_sim=1.0, replay_text={"1": "z"})
    attr = StatuteAttribution(sid="100", anchors=(a, b, c), observations=())
    assert attr.min_over_life == 0.5
    assert attr.latest_scored == 1.0
    assert attr.has_hidden_mid_life_divergence is True


# ---------------------------------------------------------------------------
# residual projection
# ---------------------------------------------------------------------------


def test_observation_to_residual_maps_families() -> None:
    a = _obs("v1", "2000-01-01", replay_text={"1": "same", "2": "s2"})
    b = _obs("v2", "2001-01-01", penalized={"1"}, replay_text={"1": "same", "2": "s2"})
    obs = attribute_divergences("100", [a, b])
    residual = observation_to_residual(obs[0])
    assert residual.family == "oracle_editorial_pathology"
    assert residual.agreement_residual_status == "blocked"
    assert residual.jurisdiction == "finland"
    assert residual.agreement_surface == "all_pit_anchor_touch"
    # the residual never authorizes replay or turns the oracle into source truth
    assert "touch_observation_as_replay_authorization" in residual.forbidden_shortcuts


# ---------------------------------------------------------------------------
# manifest content-address diff (§3.1.2 predict-then-compare gate)
# ---------------------------------------------------------------------------


def _manifest(sid: str, anchors: list[dict]) -> dict:
    return {"statutes": {sid: {"sid": sid, "anchors": anchors}}}


def _anchor(vt: str, *, artifact: str, cnf: str, amend: str = "a", as_of: str = "2000-01-01") -> dict:
    return {
        "version_tag": vt,
        "amendment_id": amend,
        "as_of": as_of,
        "artifact_hash": artifact,
        "cnf_hash": cnf,
    }


def test_diff_manifest_cnf_drift_is_the_137_failure_mode() -> None:
    frozen = _manifest("100", [_anchor("v1", artifact="AA", cnf="CC")])
    # normative content moved under a FIXED amendment pin — the silent baseline drift
    fresh = _manifest("100", [_anchor("v1", artifact="BB", cnf="DD")])
    delta = diff_manifest(frozen, fresh)
    kinds = {d.kind for d in delta.deltas}
    assert "cnf_drift" in kinds
    assert delta.cnf_drifts  # surfaced as a preregistered event
    assert not delta.is_empty


def test_diff_manifest_editorial_only_artifact_drift() -> None:
    frozen = _manifest("100", [_anchor("v1", artifact="AA", cnf="CC")])
    # raw bytes re-rendered but NORMATIVE content stable ⇒ editorial-only
    fresh = _manifest("100", [_anchor("v1", artifact="BB", cnf="CC")])
    delta = diff_manifest(frozen, fresh)
    kinds = {d.kind for d in delta.deltas}
    assert kinds == {"artifact_drift_editorial_only"}
    assert not delta.cnf_drifts


def test_diff_manifest_added_removed_and_stable() -> None:
    frozen = _manifest("100", [_anchor("v1", artifact="AA", cnf="CC")])
    fresh = _manifest(
        "100",
        [
            _anchor("v1", artifact="AA", cnf="CC"),
            _anchor("v2", artifact="EE", cnf="FF"),
        ],
    )
    delta = diff_manifest(frozen, fresh)
    kinds = {(d.version_tag, d.kind) for d in delta.deltas}
    assert ("v2", "anchor_added") in kinds
    # v1 unchanged ⇒ no delta emitted for it
    assert not any(d.version_tag == "v1" for d in delta.deltas)

    # dropping v2 back out ⇒ anchor_removed
    delta_removed = diff_manifest(fresh, frozen)
    assert any(
        d.version_tag == "v2" and d.kind == "anchor_removed"
        for d in delta_removed.deltas
    )


def test_diff_manifest_stable_is_empty() -> None:
    frozen = _manifest("100", [_anchor("v1", artifact="AA", cnf="CC")])
    delta = diff_manifest(frozen, dict(frozen))
    assert delta.is_empty


def test_cnf_hash_is_order_independent_and_deterministic() -> None:
    h1 = _cnf_hash_of_map({"1": "a", "2": "b"})
    h2 = _cnf_hash_of_map({"2": "b", "1": "a"})
    assert h1 == h2
    assert h1 != _cnf_hash_of_map({"1": "a", "2": "B"})
