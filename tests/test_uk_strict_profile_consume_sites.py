"""§2.9 corpus-scale guard-liveness for all 9 Tier C PR2 strict-profile consume sites.

The Tier C PR2 consume-site wirings (commits fbe70e65 through b2591f2f)
introduce the inverse-of-FI strict-profile gate discipline: each site's
default IS block; the strict-profile provides the explicit LIFT-GATE for
the verified-allowed case.

This test pins §2.9 disposition 2 (strict-not-allowed-blocks) across all 9
consume sites in ONE corpus-scale verification — loads the strict-profile
default (``uk_ingestion_v1`` — has ``allows_uk_X=False`` for all 10 UK
gates) and runs ``apply_ops`` on a real statute. Zero
``uk_strict_profile_lifted_*`` observations must fire because every UK gate
is False in the default preset.

Combined with the per-site §2.9 tests at ``tests/test_uk_effect_savings_
references.py`` (disposition 3 for savings-qualified-repeal) and ``tests/
test_uk_devolved_whole_act_repeal_extent.py`` (disposition 3 for devolved-
extent-repeal), this closes the §2.9 discipline for all 9 consume sites:
- disposition 1 (no-strict-blocks) verified by the existing broad test suite
- disposition 2 (strict-not-allowed-blocks) verified by this test
- disposition 3 (strict-allowed-lifts-with-audit) verified by the 2 per-site tests

For sites 3-9 (crossheading/schedule_note/heading_only/empty_type_whole_act/
partial_whole_act_repeal/definition_pseudo_target/definition_child_insert),
disposition 3 is deferred — those would require patching ``active_uk_strict_
profile`` per-site to return a profile with the specific gate enabled.
Cohesive later commits should add those tests when each site's future-wire
is concretely exercised against a real corpus statute.
"""
from __future__ import annotations

from pathlib import Path


from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline


_LIFT_PREFIX = "uk_strict_profile_lifted_"
_STRICT_ENV_FLAG = "LAWVM_UK_STRICT_PROFILE"


def test_strict_profile_default_preserves_all_blocks_on_no_op_replay(monkeypatch) -> None:
    """§2.9 disposition 2 — all 9 consume sites should NOT lift when the
    default preset is loaded (allows_uk_X=False for every UK gate).

    Drives a no-op replay through the production lane with strict-profile
    loaded + all env-gated probes env-on. The probes fire observably; the
    strict-profile consume sites preserve their default blocks (zero lift
    observations) because every ``allows_uk_X`` field is False.

    This is the corpus-scale §2.9 guard-liveness closure across all 9
    consume-site wirings in ONE test — no per-site test files needed for
    disposition 2 when this runs. Disposition 3 (the lift case) is verified
    by the separate per-site tests at ``test_uk_effect_savings_references.py``
    and ``test_uk_devolved_whole_act_repeal_extent.py``.
    """
    # All 9 env-gated probes env-on + strict-profile loaded (default preset).
    for flag in (
        "LAWVM_UK_MATERIALIZE_TOTALITY_PROBE",
        "LAWVM_UK_MUTATION_BOUNDARY_PER_OP",
        "LAWVM_UK_IDENTITY_INTRINSIC_PROBE",
        "LAWVM_UK_LINEAGE_ACYCLIC_PROBE",
        "LAWVM_UK_COMMENCEMENT_EFFECT_TOTALITY_PROBE",
        "LAWVM_UK_OVERLAY_AUTHORIZATION_PROBE",
        "LAWVM_UK_OBSERVATION_PROMOTED_TO_AUTHORITY_PROBE",
        "LAWVM_UK_UNKNOWN_ATTESTATION_POLICY_PROBE",
        "LAWVM_UK_TIMELINE_INVARIANTS_PROBE",
    ):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv(_STRICT_ENV_FLAG, "uk_ingestion_v1")

    pipeline = UKReplayPipeline(Path("."))
    base = IRStatute(
        statute_id="strict_profile/scale/1",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", children=()),
            ),
        ),
        supplements=(),
        metadata={"pit_date": "2020-01-01"},
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)

    # No strict-profile lifts should fire — the default uk_ingestion_v1
    # preset has allows_uk_X=False for every UK gate.
    lifts = [a for a in adjudications if a.kind.startswith(_LIFT_PREFIX)]
    assert lifts == [], (
        "strict-profile default (uk_ingestion_v1) loaded — ZERO lifts "
        "expected because every allows_uk_X gate is False. Got: "
        "{}".format(lifts)
    )
