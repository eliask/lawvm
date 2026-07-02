"""Unit tests for the temporal-holdout generalization experiment (#182).

Covers the split logic on synthetic anchor sets, the per-statute and
corpus-level generalization-gap computation on fixtures, holdout-era residual
projection into the shared AgreementResidual taxonomy, and determinism of the
report. No corpus builds — every test feeds hand-built
:class:`AnchorObservation` / :class:`StatuteAttribution` fixtures into the pure
split + gap seam.
"""
from __future__ import annotations

from lawvm.tools.fi_anchor_manifest import (
    AnchorObservation,
    StatuteAttribution,
    TouchObservation,
)
from lawvm.tools.temporal_holdout import (
    CorpusHoldout,
    compute_statute_holdout,
    split_anchors,
)


def _obs(
    version_tag: str,
    as_of: str | None,
    *,
    struct_sim: float = 1.0,
    penalized: set[str] | None = None,
    replay_text: dict[str, str] | None = None,
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
        oracle_suspect=None,
        status="OK" if as_of is not None else "UNPLACEABLE",
    )


# ---------------------------------------------------------------------------
# split_anchors
# ---------------------------------------------------------------------------


def test_split_partitions_by_cutoff_inclusive_on_training() -> None:
    anchors = [
        _obs("v1", "2000-01-01"),
        _obs("v2", "2005-01-01"),  # exactly < T
        _obs("v3", "2010-06-01"),  # exactly == T ⇒ training (inclusive)
        _obs("v4", "2010-06-02"),  # just after T ⇒ holdout
        _obs("v5", "2020-01-01"),
    ]
    split = split_anchors(anchors, "2010-06-01", sid="100")
    assert [a.version_tag for a in split.training] == ["v1", "v2", "v3"]
    assert [a.version_tag for a in split.holdout] == ["v4", "v5"]
    assert split.is_informative


def test_split_drops_unplaceable_and_errored_anchors() -> None:
    anchors = [
        _obs("v1", "2000-01-01"),
        _obs("v2", None),  # UNPLACEABLE, as_of None
        _obs("v3", "2020-01-01", struct_sim=-1.0),  # ORACLE_CONTENT_ABSENT
        _obs("v4", "2021-01-01"),
    ]
    split = split_anchors(anchors, "2010-01-01")
    # only v1 (train) and v4 (holdout) survive; v2/v3 are unobserved
    assert [a.version_tag for a in split.training] == ["v1"]
    assert [a.version_tag for a in split.holdout] == ["v4"]


def test_one_sided_statute_is_not_informative() -> None:
    all_before = split_anchors(
        [_obs("v1", "2000-01-01"), _obs("v2", "2001-01-01")], "2010-01-01"
    )
    assert all_before.n_training == 2 and all_before.n_holdout == 0
    assert not all_before.is_informative

    all_after = split_anchors(
        [_obs("v1", "2019-01-01"), _obs("v2", "2020-01-01")], "2010-01-01"
    )
    assert all_after.n_holdout == 2 and all_after.n_training == 0
    assert not all_after.is_informative


def test_iso_date_string_comparison_is_chronological() -> None:
    # zero-padded ISO dates compare lexically == chronologically
    split = split_anchors(
        [_obs("v1", "2009-12-31"), _obs("v2", "2010-01-01")], "2009-12-31"
    )
    assert [a.version_tag for a in split.training] == ["v1"]
    assert [a.version_tag for a in split.holdout] == ["v2"]


# ---------------------------------------------------------------------------
# per-statute generalization gap
# ---------------------------------------------------------------------------


def _attr(sid: str, anchors: list[AnchorObservation], observations=()) -> StatuteAttribution:
    return StatuteAttribution(
        sid=sid, anchors=tuple(anchors), observations=tuple(observations)
    )


def test_zero_gap_when_accuracy_holds() -> None:
    # spec generalizes: identical accuracy before and after T
    attr = _attr(
        "100",
        [
            _obs("v1", "2000-01-01", struct_sim=0.9),
            _obs("v2", "2005-01-01", struct_sim=0.9),
            _obs("v3", "2020-01-01", struct_sim=0.9),
        ],
    )
    result = compute_statute_holdout(attr, "2010-01-01")
    assert result.train_acc == 0.9
    assert result.holdout_acc == 0.9
    assert result.gap == 0.0
    assert result.is_informative


def test_positive_gap_is_overfitting_direction() -> None:
    attr = _attr(
        "100",
        [
            _obs("v1", "2000-01-01", struct_sim=1.0),
            _obs("v2", "2020-01-01", struct_sim=0.6),  # holdout worse ⇒ +gap
        ],
    )
    result = compute_statute_holdout(attr, "2010-01-01")
    assert result.train_acc == 1.0
    assert result.holdout_acc == 0.6
    assert result.gap is not None
    assert abs(result.gap - 0.4) < 1e-9


def test_negative_gap_when_better_on_holdout() -> None:
    attr = _attr(
        "100",
        [
            _obs("v1", "2000-01-01", struct_sim=0.5),
            _obs("v2", "2020-01-01", struct_sim=1.0),  # holdout better ⇒ -gap
        ],
    )
    result = compute_statute_holdout(attr, "2010-01-01")
    assert result.gap is not None and result.gap < 0


def test_one_sided_statute_has_no_gap() -> None:
    attr = _attr(
        "100",
        [_obs("v1", "2000-01-01"), _obs("v2", "2005-01-01")],
    )
    result = compute_statute_holdout(attr, "2010-01-01")
    assert result.holdout_acc is None
    assert result.gap is None
    assert not result.is_informative


# ---------------------------------------------------------------------------
# holdout-era residual projection (AgreementResidual reuse)
# ---------------------------------------------------------------------------


def test_only_holdout_era_observations_project_to_residuals() -> None:
    # a divergence whose window ENDS in the training era must not be attributed
    # to the holdout; only the post-T window's divergence should.
    train_obs = TouchObservation(
        sid="100",
        section_key="1",
        verdict="oracle_suspect_spontaneous_appearance",
        window="2000-01-01..2005-01-01",  # ends before T
        touching_amendments=(),
        evidence="pre-cutoff divergence",
    )
    holdout_obs = TouchObservation(
        sid="100",
        section_key="2",
        verdict="candidate_replay_bug_persistent_post_touch",
        window="2010-01-01..2020-01-01",  # ends after T
        touching_amendments=("amend-v3",),
        evidence="post-cutoff divergence",
    )
    attr = _attr(
        "100",
        [
            _obs("v1", "2005-01-01"),
            _obs("v2", "2020-01-01"),
        ],
        observations=(train_obs, holdout_obs),
    )
    result = compute_statute_holdout(attr, "2010-01-01")
    assert len(result.holdout_residuals) == 1
    r = result.holdout_residuals[0]
    assert r.detail["section_key"] == "2"
    assert r.family == "replay_bug"
    assert r.agreement_surface == "all_pit_anchor_touch"
    # never authorizes replay / oracle-as-truth
    assert "touch_observation_as_replay_authorization" in r.forbidden_shortcuts


# ---------------------------------------------------------------------------
# corpus aggregate + determinism
# ---------------------------------------------------------------------------


def _corpus_fixture() -> CorpusHoldout:
    generalizes = compute_statute_holdout(
        _attr(
            "100",
            [
                _obs("v1", "2000-01-01", struct_sim=1.0),
                _obs("v2", "2020-01-01", struct_sim=1.0),
            ],
        ),
        "2010-01-01",
    )
    overfits = compute_statute_holdout(
        _attr(
            "200",
            [
                _obs("v1", "2000-01-01", struct_sim=1.0),
                _obs("v2", "2020-01-01", struct_sim=0.5),
            ],
        ),
        "2010-01-01",
    )
    one_sided = compute_statute_holdout(
        _attr("300", [_obs("v1", "2000-01-01", struct_sim=1.0)]),
        "2010-01-01",
    )
    return CorpusHoldout(
        cutoff="2010-01-01",
        statutes=(generalizes, overfits, one_sided),
        errors=(("400", "ERROR:no-archive"),),
    )


def test_corpus_aggregate_means_over_informative_only() -> None:
    report = _corpus_fixture()
    assert report.n_informative == 2  # 300 is one-sided, excluded
    # train mean over {1.0, 1.0} = 1.0
    assert report.mean_train_acc == 1.0
    # holdout mean over {1.0, 0.5} = 0.75
    assert report.mean_holdout_acc is not None
    assert abs(report.mean_holdout_acc - 0.75) < 1e-9
    # mean gap over {0.0, 0.5} = 0.25
    assert report.mean_gap is not None
    assert abs(report.mean_gap - 0.25) < 1e-9


def test_report_to_dict_is_deterministic_and_schema_tagged() -> None:
    r1 = _corpus_fixture().to_dict()
    r2 = _corpus_fixture().to_dict()
    assert r1 == r2  # identical fixtures ⇒ identical report
    assert r1["schema"] == "lawvm.temporal_holdout.v1"
    assert r1["framing"] == "retrospective_cutoff_holdout"
    assert r1["informative_statute_count"] == 2
    assert r1["errors"] == [{"sid": "400", "error": "ERROR:no-archive"}]


def test_empty_corpus_has_none_gap_and_no_informative() -> None:
    report = CorpusHoldout(cutoff="2010-01-01", statutes=(), errors=())
    assert report.n_informative == 0
    assert report.mean_gap is None
    assert report.mean_train_acc is None
