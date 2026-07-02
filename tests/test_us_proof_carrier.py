"""US-federal EV-05 execution-authorization proof carrier (LawVM task).

Mirrors the proven Estonia recipe (``estonia/grafter.py`` ~9519) for the US
char-span apply lane. The genuine authority for a US state-mutating op is its
ORIGINATING amending instrument — the Public Law whose amendatory instruction
directed the change (``op.source.statute_id``, the ``"{congress}-{number}"`` key
the OLRC Table III vocabulary uses). ``_us_execution_authorization`` prefers a
proof minted onto the op's carrier, else mints one from that identity; an op with
NO amending-PL identity yields ``None`` (the honest unauthorized residue).

WHAT THIS GATE PROVES.
  1. ``_mint_us_execution_authorization`` mints a replay-authorized proof from a
     real ``op.source.statute_id`` and returns ``None`` for a blank/absent source.
  2. ``_us_execution_authorization`` reads an already-minted carried proof in
     preference to minting (carrier-first).
  3. Routed through ``apply_us_op``, the universal EV-05 observe gate is QUIET for
     an op whose authorizing PL is known (it resolves a rule_id) and FIRES the
     non-blocking ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` observation for an
     op with unknown authority.
  4. OBSERVATION-ISOLATION / BYTE-IDENTITY: the EV-05 witness lands on the seam's
     ``AppliedOp.observations`` lane, never on production ``findings``; the
     materialized section text is byte-identical to the resolver-free baseline.

AM-01 is NOT wired for US: US ops carry no closed Parsed-vs-Recovered provenance
signal (``target_resolution:`` is an open target-recovery vocabulary, not a
derivation-confidence binary), so no provenance resolver is added.
"""

from __future__ import annotations

from lawvm.core.apply_seam import (
    REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE,
    apply_op,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import (
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.phase_result import Finding
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.us_federal.apply_profile import (
    _mint_us_execution_authorization,
    _us_execution_authorization,
    apply_us_op,
    us_apply_profile,
    us_public_law_authorization_rule_id,
)
from lawvm.us_federal.source_tree import synthetic_usc_section


# ── builders ──────────────────────────────────────────────────────────────────


def _section():
    return synthetic_usc_section(
        title=10,
        section="2432",
        text=(
            "(a) Subsection A has no paragraphs. "
            "(b) Authority is granted. "
            "(1) The first paragraph mentions a 15-year window. "
            "(2) The second paragraph stands alone."
        ),
    )


def _op(
    *,
    statute_id="117-2",
    op_id="strike-15y",
    with_source=True,
    tags=(),
    execution_authorization=None,
):
    source = (
        OperationSource(statute_id=statute_id, enacted="2021-03-11")
        if with_source
        else None
    )
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(
                ("title", "10"),
                ("section", "2432"),
                ("subsection", "b"),
                ("paragraph", "1"),
            )
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="15-year", occurrence=1),
            replacement="20-year",
        ),
        source=source,
        provenance_tags=tuple(tags),
        execution_authorization=execution_authorization,
    )


# ── 1. rule-id helper ─────────────────────────────────────────────────────────


def test_us_public_law_rule_id_names_the_originating_pl():
    assert us_public_law_authorization_rule_id("117-2") == "us_public_law:117-2"
    # Older Statutes-at-Large chapter key flows through unchanged.
    assert us_public_law_authorization_rule_id("73-22") == "us_public_law:73-22"
    # A blank key mints no rule id — unknown authority, never fabricated.
    assert us_public_law_authorization_rule_id("") == ""
    assert us_public_law_authorization_rule_id("   ") == ""


# ── 2. mint from amending-PL identity (known) / refuse (unknown) ──────────────


def test_mint_authorization_from_known_amending_public_law():
    proof = _mint_us_execution_authorization(
        _op(statute_id="117-2", tags=("us_amendatory", "target_resolution:prose"))
    )
    assert proof is not None
    assert proof.replay_authorized is True
    assert proof.executable is True
    assert proof.authorization_status == "replay_authorized"
    assert proof.authorization_rule_id == "us_public_law:117-2"
    assert proof.owner_phase == "apply"
    # The detail is read-as-witness: it records the concrete instrument + the
    # target-resolution provenance, but drives no control flow.
    assert proof.detail["amending_public_law"] == "117-2"
    assert proof.detail["target_resolution"] == "prose"
    assert proof.detail["enacted"] == "2021-03-11"


def test_mint_returns_none_for_unknown_authority():
    # No source at all → unknown authority → no fabricated proof.
    assert _mint_us_execution_authorization(_op(with_source=False)) is None
    # A source with a blank statute_id is equally unknown.
    assert _mint_us_execution_authorization(_op(statute_id="")) is None


# ── 3. resolver: carrier-first, then mint ─────────────────────────────────────


def test_resolver_prefers_an_already_carried_proof():
    carried = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="replay_authorized",
        authorization_rule_id="us_public_law:CARRIED-99",
        owner_phase="apply",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        safe_default="execute_only_after_amending_public_law_identity_is_known",
        required_proofs=(),
        forbidden_shortcuts=("x",),
        detail={},
    )
    op = _op(statute_id="117-2", execution_authorization=carried)
    resolved = _us_execution_authorization(op)
    # The carried proof wins over a freshly-minted one (different rule id proves it).
    assert resolved is carried
    assert resolved.authorization_rule_id == "us_public_law:CARRIED-99"


def test_resolver_mints_when_no_proof_carried():
    resolved = _us_execution_authorization(_op(statute_id="118-5"))
    assert resolved is not None
    assert resolved.authorization_rule_id == "us_public_law:118-5"


def test_resolver_none_for_unknown_authority():
    assert _us_execution_authorization(_op(with_source=False)) is None


# ── 4. EV-05 observe gate through apply_us_op: quiet / fires ──────────────────


def _ev05_observations(observations: tuple[Finding, ...]) -> tuple[Finding, ...]:
    return tuple(
        o
        for o in observations
        if o.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    )


def test_profile_wires_the_us_authorization_resolver():
    profile = us_apply_profile()
    assert profile.authorization_resolver is _us_execution_authorization


def test_ev05_gate_quiet_when_amending_pl_known():
    section = _section()
    res = apply_us_op(
        section.statutory_text,
        _op(statute_id="117-2"),
        before_section=section,
        source_statute="10:2432",
    )
    assert res.applied.applied
    # The op resolves a rule_id → the universal EV-05 observe gate emits nothing.
    assert _ev05_observations(res.applied.observations) == ()


def test_ev05_gate_fires_on_unknown_authority():
    section = _section()
    res = apply_us_op(
        section.statutory_text,
        _op(with_source=False),
        before_section=section,
        source_statute="10:2432",
    )
    assert res.applied.applied
    fired = _ev05_observations(res.applied.observations)
    # No amending-PL identity → the firewall-hole witness fires (non-blocking).
    assert len(fired) == 1
    assert fired[0].role == "observation"
    assert fired[0].blocking is False


# ── 5. observation-isolation / byte-identity ──────────────────────────────────


def test_ev05_witness_never_lands_in_production_findings():
    section = _section()
    res = apply_us_op(
        section.statutory_text,
        _op(with_source=False),
        before_section=section,
        source_statute="10:2432",
    )
    # The EV-05 witness lives ONLY on the observations lane, never findings.
    assert _ev05_observations(res.applied.observations)
    assert all(
        getattr(f, "kind", None) != REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for f in res.applied.findings
    )


def test_resolver_wiring_is_materialized_text_byte_identical():
    # Routing the SAME op through a resolver-free profile vs the wired profile
    # yields byte-identical materialized text: EV-05 is an additive observe lane.
    from lawvm.us_federal.apply_profile import (
        CharSpanState,
        _located_node_text_for,
        _us_materializer_factory,
    )
    from lawvm.core.apply_seam import ApplyProfile
    from lawvm.core.char_span_metric import CHAR_SPAN_METRIC

    section = _section()
    op = _op(statute_id="117-2")
    located = _located_node_text_for(op, section)
    state = CharSpanState(section_text=section.statutory_text, located_node_text=located)

    baseline_profile: ApplyProfile[CharSpanState] = ApplyProfile(
        jurisdiction="us",
        materializer=_us_materializer_factory(section),
        region_metric=CHAR_SPAN_METRIC,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        # no authorization_resolver — the firewall-hole baseline.
    )
    baseline = apply_op(
        state, op, provenance=None, profile=baseline_profile, source_statute="10:2432"
    )
    wired = apply_us_op(
        section.statutory_text, op, before_section=section, source_statute="10:2432"
    )
    assert baseline.new_state.section_text == wired.applied.new_state.section_text
    # The baseline (no resolver) DOES fire EV-05; the wired profile is quiet.
    assert _ev05_observations(baseline.observations)
    assert _ev05_observations(wired.applied.observations) == ()
