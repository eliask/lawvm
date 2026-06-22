"""WAIST #6 carrier — ``ReplayProducts.canonical_op_stage``.

Gates the producer-side carrier that THREADS the per-amendment canonical-op
``StageResult`` account out of ``compile_amendment_ops`` (where it is already
built, via ``build_canonical_op_stage``) up to the replay assembly, where it is
aggregated onto ``ReplayProducts.canonical_op_stage``.

The carrier is ADDITIVE: it only COLLECTS the already-built per-amendment
accounts via the ``canonical_op_stages_out`` sink. The decline VERDICT still
rides the existing #6 single-channel
(``reconstruct_findings_from_canonical_op_stage``) unchanged — that is gated by
``tests/test_fi_canonical_op_stage.py``.

Cases:
  (a) the carrier is POPULATED on a green replay; ``coverage.is_partition()``
      holds; unit/empty-decline shape is the candidate-op partition.
  (b) FAITHFUL aggregation — the carrier is the fold over the EXACT per-amendment
      producer ``StageResult`` accounts captured off the
      ``canonical_op_stages_out`` sink, NOT a re-derivation from the
      stage-tagless union findings. Captured directly via the sink (the same
      list the replay buffer threads) and re-aggregated; the carrier must equal
      that independent fold field-for-field.
"""

from __future__ import annotations

from typing import cast

import pytest

from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_products import aggregate_canonical_op_stage
from lawvm.finland.replay_request import ReplayXmlRequest

_GREEN_PARENT = "1985/336"


@pytest.fixture(scope="module")
def green_replay():
    return replay_xml(
        request=ReplayXmlRequest(
            parent_id=_GREEN_PARENT,
            mode="official_consolidation",
            quiet=True,
            build_full_products=True,
        )
    )


# ---------------------------------------------------------------------------
# (a) the carrier is populated + the partition holds on the green corpus
# ---------------------------------------------------------------------------


def test_canonical_op_stage_carrier_populated_partition(green_replay) -> None:
    stage = green_replay.products.canonical_op_stage
    assert stage is not None, (
        "ReplayProducts.canonical_op_stage must be populated on a green replay — "
        "the per-amendment canonical-op accounts are threaded out via the sink"
    )

    # candidate-op partition: total = emitted (owned) + declined (violation).
    assert stage.coverage.unit == "candidate_ops"
    assert stage.coverage.is_partition()
    assert stage.coverage.total == stage.coverage.owned + stage.coverage.violation
    # the green replay emits canonical ops; owned is the emitted-op count.
    assert stage.coverage.owned > 0

    # one blocking residual per declined candidate op (the genuine canonical-op
    # residue). violation == number of residuals — the strict-rejection declines.
    assert len(stage.residuals) == stage.coverage.violation

    # additive carrier: the decline VERDICT rides the #6 single-channel, not the
    # carrier — so the carrier itself carries NO findings, neutral authority,
    # empty evidence, and no merged op value.
    assert stage.findings == ()
    assert stage.authority.is_neutral
    assert stage.evidence.is_empty
    assert stage.value is None


# ---------------------------------------------------------------------------
# (b) FAITHFUL aggregation — the carrier is the fold over the producer's OWN
#     per-amendment accounts captured off the sink, NOT a union-findings
#     re-derivation.
# ---------------------------------------------------------------------------


def test_canonical_op_stage_carrier_is_faithful_producer_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The carrier is the fold over the producer's OWN per-amendment stages.

    Wrap the PRODUCTION ``build_canonical_op_stage`` (the per-amendment
    canonical-op stage builder) to record every stage it produces during a real
    replay. Re-aggregating those recorded stages with
    ``aggregate_canonical_op_stage`` must reproduce the carrier the replay
    assembly built — proving the carrier collects the producer's typed
    per-amendment accounts (threaded out via the ``canonical_op_stages_out``
    sink), NOT a reconstruction from the stage-tagless union findings (which
    carry no stage tag and so cannot isolate the per-amendment canonical-op
    partition). A re-derivation would NOT, in general, match the producer fold.
    """
    import lawvm.finland.compile_amendment as compile_amendment
    from lawvm.core.stage_result import StageResult

    real_build = compile_amendment.build_canonical_op_stage
    produced: list["StageResult[object]"] = []

    def recording_build(resolved, findings):  # type: ignore[no-untyped-def]
        stage, carriers = real_build(resolved, findings)
        produced.append(cast("StageResult[object]", stage))
        return stage, carriers

    monkeypatch.setattr(compile_amendment, "build_canonical_op_stage", recording_build)

    replay = replay_xml(
        request=ReplayXmlRequest(
            parent_id=_GREEN_PARENT,
            mode="official_consolidation",
            quiet=True,
            build_full_products=True,
        )
    )

    assert produced, "compile_amendment_ops must build per-amendment canonical-op stages"

    independent = aggregate_canonical_op_stage(tuple(produced))
    carrier = replay.products.canonical_op_stage
    assert carrier is not None

    # The carrier IS the fold over the producer's own per-amendment accounts —
    # field-for-field equal to an independent re-aggregation of the SAME stages.
    assert independent.coverage.owned == carrier.coverage.owned
    assert independent.coverage.violation == carrier.coverage.violation
    assert independent.coverage.total == carrier.coverage.total
    assert independent.coverage.unit == carrier.coverage.unit
    assert independent.residuals == carrier.residuals
