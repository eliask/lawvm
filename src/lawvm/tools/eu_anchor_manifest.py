"""eu_anchor_manifest.py — the European Union frozen content-addressed anchor +
replay-attribution engine (#183/#204, FOURTH jurisdiction).

This is the EU analogue of :mod:`lawvm.tools.uk_anchor_manifest` (United Kingdom),
:mod:`lawvm.tools.ee_anchor_manifest` (Estonia), and
:mod:`lawvm.tools.fi_anchor_manifest` (Finland), extending the drift-robust #183
CTSF metric (``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4) to a fourth
jurisdiction. It is ADDITIVE: it never mutates the EU corpus, the EU replay
pipeline, or the existing ``eu_bench_unit_result`` scoring path; the EU frontend
stays byte-identical.

WHAT AN EU "ANCHOR" IS — and WHY EU DOES NOT FIT THE FI/EE/UK ORACLE-TOUCH MODEL
(documented, load-bearing). Finland enumerates the published *consolidation
snapshots* of one statute over its life; Estonia enumerates the Riigi Teataja
*terviktekst* chain per ``grupi_id``; the UK models each act as an enacted→current
replay window scored against the single revised in-force oracle. All three have, in
the Farchive, a materialized ORACLE (a published consolidated text) to score the
native replay AGAINST. The EU Cellar corpus, as acquired by #204, does NOT:

    * NO sector-0 consolidation is stored. Every ``consolidation_date`` in
      ``eu_cellar.farchive`` is ``enacted`` (verified: 0 sector-0 CELEXes over
      ~8.8k distinct CELEXes). ``eu_consolidation_oracle.build_consolidation_oracle``
      needs an external ``fetch_consolidation`` byte lane that returns the
      ``0YYYY<L><N>-YYYYMMDD`` consolidated FMX4 — and those bytes are not in the
      archive. The only offline consolidation oracles LawVM has are committed test
      fixtures, not a corpus-scale byte source.
    * NO persisted dated amendment DAG. ``eu_amendment_graph.build_amendment_graph``
      is a LIVE SPARQL query; the dated ``AmendmentEdge`` set is rebuilt each run and
      never persisted. The offline proxy (a notice's flat, UNDATED
      ``corrigendum_celexes`` set) lacks the date-of-application ordering the
      oracle-touch calculus needs.

So the FI/EE/UK "score the native replay against a published consolidation and type
each per-unit divergence via the touch relation" model has NO oracle to run against
for EU today. Rather than fabricate an oracle (the exact ``authoritative oracle ≠
correct`` anti-pattern LawVM forbids — see the EU honesty regime in
``eu_oracle_divergence``: the consolidation is editorial, "no legal value", and the
comparator NEVER repairs toward it), the EU gate scores a DIFFERENT, genuinely
available, deterministic property of the offline replay: **apply-fold conservation**.

WHAT IS SCORED (the EU adaptation — DOCUMENTED, and why it is honest). For each EU
act in the frozen corpus we run the offline replay chain purely from Farchive-stored
bytes, network-free:

    graft(base_celex)                      # parse_eu_regulation_ir over stored FMX4
      → lower_amending_act(amender_bytes)  # the amending act's typed LegalOperations
      → order_eu_ops                       # legal-chronological ordering
      → apply_eu_ops_conserved(base, ops)  # the conserved apply fold

``apply_eu_ops_conserved`` is a CONSERVED fold (AGENTS.md §1.8): every input op is
partitioned into ``applied_ops`` (its binding landed in the materialized statute) or
``skipped_items`` (a typed ``RejectedItem`` with a ``reason_code`` / ``blocking``
disposition). The conservation invariant — ``|applied| + |skipped| == |ops|`` — is a
real, checkable property of the replay engine, and its violation (or an apply RAISE
mid-fold) is a genuine replay defect, exactly the ``replay_bug`` the honest metric
exists to convict. This needs ZERO network and ZERO oracle.

THE EU VERDICT MODEL (its own ``_VERDICT_TO_FAMILY``, kept honest). EU cannot reuse
Finland's verdict map verbatim because there is no oracle-divergence to type; the
verdicts describe the CONSERVATION property instead, and each projects onto a real
CTSF residual family:

    * ``eu_replay_apply_raise``             → ``replay_bug``  (BILLABLE): the conserved
      fold raised mid-apply — replay crashed reconstructing the body. A hard FAIL.
    * ``eu_replay_conservation_violation``  → ``unknown``    (BILLABLE): applied +
      skipped != total — an op left the partition unaccounted (the exact pathology the
      §1.8 conservation contract forbids). A hard FAIL.
    * ``eu_replay_typed_op_skip``           → ``cnf_unsupported`` (non-billable): an op
      was typed-skipped with a non-blocking ``reason_code`` (a standing replay
      capability gap — e.g. ``eu_replay_target_not_found`` / ``…_unsupported_action``).
      A WARN-lane telemetry move, never a red gate — mirrors FI/EE/UK's non-billable
      typed families.

A fully-applied act (every op landed, 0 skips, 0 raises) emits NO observation — it is
scored-clean, the honest 0-billable steady state the frozen corpus is curated to hold.

OBSERVATION RECORD. EU emits its own :class:`EUReplayObservation` (a small typed
per-observation record) rather than reusing Finland's ``TouchObservation``: the FI
record's ``verdict`` is a CLOSED ``Literal`` of oracle-divergence verdicts, which EU's
conserved-apply verdicts are not members of. The shared taxonomy
(:class:`~lawvm.core.agreement_residual.AgreementResidual`) is reused as-is for
cross-jurisdiction residual reporting. The gate's diff/baseline machinery
(``lawvm.core.ctsf_gate``) consumes the projected ``{family: count}`` set identically
to FI/EE/UK.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    AgreementResidualStatus,
)

MANIFEST_SCHEMA = "lawvm.eu_anchor_manifest.v1"

# The Farchive locator convention for a stored FMX4 item (eu_acquire.celex_locator):
# ``cellar://celex/{CELEX}/{consolidation_date}/{lang}/{fmt}``. Every act in this
# corpus is stored at ``enacted`` (there is no consolidated manifestation).
_ENACTED = "enacted"


# ---------------------------------------------------------------------------
# The EU verdict vocabulary + its projection onto the CTSF residual families.
# EU cannot reuse Finland's oracle-divergence verdicts (there is no oracle); these
# describe the conserved-apply property instead. Kept EXPLICIT + honest.
# ---------------------------------------------------------------------------

#: Apply fold raised mid-body-reconstruction → a genuine replay crash (BILLABLE).
VERDICT_APPLY_RAISE = "eu_replay_apply_raise"
#: applied + skipped != total ops — an op left the partition (BILLABLE).
VERDICT_CONSERVATION_VIOLATION = "eu_replay_conservation_violation"
#: A typed, non-blocking op skip — a standing replay capability gap (non-billable).
VERDICT_TYPED_OP_SKIP = "eu_replay_typed_op_skip"

#: EU verdict → CTSF residual family (``lawvm.core.ctsf_residual_report``'s
#: ``RESIDUAL_VERDICT_FAMILIES`` — the family set the gate diffs). The two BILLABLE
#: verdicts map onto the two FAIL families (``replay_bug`` / ``unknown``); the
#: typed-skip verdict maps onto the non-billable capability-gap family
#: (``cnf_unsupported``), the EU WARN lane. This is the map the GATE consumes.
_VERDICT_TO_FAMILY: dict[str, str] = {
    VERDICT_APPLY_RAISE: "replay_bug",
    VERDICT_CONSERVATION_VIOLATION: "unknown",
    VERDICT_TYPED_OP_SKIP: "cnf_unsupported",
}

#: EU verdict → shared ``AgreementResidual`` family vocabulary (a DIFFERENT, wider
#: taxonomy than the CTSF families above). Used only by
#: :func:`observation_to_residual` for cross-jurisdiction residual reporting; the
#: gate does NOT use this. A typed op-skip is an ``accepted_non_executable_frontier``
#: (a recorded, non-billable capability gap); the two billable verdicts keep their
#: ``replay_bug`` / ``unknown`` identity (both are valid AgreementResidual families).
_VERDICT_TO_RESIDUAL_FAMILY: dict[str, str] = {
    VERDICT_APPLY_RAISE: "replay_bug",
    VERDICT_CONSERVATION_VIOLATION: "unknown",
    VERDICT_TYPED_OP_SKIP: "accepted_non_executable_frontier",
}

#: EU verdict → AgreementResidual status (residual = billable-lane, blocked = typed).
_VERDICT_TO_STATUS: dict[str, str] = {
    VERDICT_APPLY_RAISE: "residual",
    VERDICT_CONSERVATION_VIOLATION: "residual",
    VERDICT_TYPED_OP_SKIP: "blocked",
}


@dataclass(frozen=True)
class EUReplayObservation:
    """One typed EU replay-conservation observation over a (base, amender) window.

    The EU analogue of Finland's ``TouchObservation`` — but EU's ``verdict`` vocabulary
    is its own (the conserved-apply verdicts above, NOT the oracle-divergence verdicts
    Finland types, whose ``Literal`` is closed), so it is a distinct record. Projects
    onto the CTSF residual families via :data:`_VERDICT_TO_FAMILY`.
    """

    sid: str
    section_key: str
    verdict: str
    window: str
    touching_amendments: tuple[str, ...]
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "section_key": self.section_key,
            "verdict": self.verdict,
            "window": self.window,
            "touching_amendments": list(self.touching_amendments),
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Farchive access (offline, network-free)
# ---------------------------------------------------------------------------


def _default_db() -> Path:
    """The EU Cellar Farchive path (``LAWVM_CANONICAL_DATA_ROOT``-aware).

    Resolves ``data/eu_cellar.farchive`` under the canonical data root when set
    (the same env var the sibling bench tools honour), else under the repo root —
    so a corpus-free checkout resolves to a path that simply does not exist and the
    gate SKIPS clean (``eu_anchor_corpus_available`` returns ``False``).
    """
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    base = Path(root) if root else Path(__file__).resolve().parents[3]
    return base / "data" / "eu_cellar.farchive"


def _fmx4_locator(celex: str, lang: str) -> str:
    return f"cellar://celex/{celex}/{_ENACTED}/{lang}/fmx4"


def _fetch_fmx4_bytes(archive: Any, celex: str) -> Optional[bytes]:
    """Stored FMX4 bytes for ``celex`` (prefer ``eng``, fall back to ``fin``)."""
    for lang in ("eng", "fin"):
        data = archive.get(_fmx4_locator(celex, lang))
        if data:
            return data
    return None


def _graft(archive: Any, celex: str):
    """Parse a stored FMX4 act into an ``IRStatute`` offline, or return ``None``.

    The grafter reads a PATH, so the stored bytes are written to a temp file (the
    same discipline ``eu_consolidation_oracle._default_parse_fmx4_bytes`` uses —
    acquired bytes are never persisted to the tree).
    """
    from lawvm.eu.grafter import parse_eu_regulation_ir

    data = _fetch_fmx4_bytes(archive, celex)
    if not data:
        return None
    with tempfile.NamedTemporaryFile(suffix=".xml") as tf:
        tf.write(data)
        tf.flush()
        try:
            return parse_eu_regulation_ir(Path(tf.name), celex=celex)
        # A grafter root-tag / parse failure is a "base not reconstructable" answer,
        # not an error to raise — the corpus is curated to graftable bases, and a
        # regression here surfaces as an ERROR-status attribution the gate reads.
        # lawvm-failloud: graft-availability probe; failure is the answer.
        except Exception:  # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# The (base, amender) replay window per corpus act.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EUChainRef:
    """One frozen EU replay window: an amender applied to its base, both stored.

    Content-addressed by the pair of CELEXes (the Farchive locators are a pure
    function of them). ``amender`` lowers to the amendment ops; ``base`` is the
    statute they apply to. Both must be ACT-rooted + graftable offline (the corpus
    is curated so).
    """

    amender: str
    base: str


@dataclass(frozen=True)
class EUAttribution:
    """The typed attribution of one EU replay window (mirrors StatuteAttribution)."""

    sid: str
    observations: tuple[EUReplayObservation, ...]
    applied: int = 0
    skipped: int = 0
    total_ops: int = 0
    status: str = "OK"

    @property
    def conserved(self) -> bool:
        return self.applied + self.skipped == self.total_ops

    @property
    def billable_observations(self) -> tuple[EUReplayObservation, ...]:
        return tuple(
            o
            for o in self.observations
            if _VERDICT_TO_FAMILY[o.verdict] in ("replay_bug", "unknown")
        )

    @property
    def is_gated_clean(self) -> bool:
        return not self.billable_observations


def attribute_chain(chain: EUChainRef, *, archive: Any) -> EUAttribution:
    """Score one (base, amender) window's offline replay-conservation → attribution.

    Runs the network-free replay chain (graft base → lower amender → order → conserved
    apply) and emits typed :class:`EUReplayObservation`s per the EU verdict model. A
    fully-applied window (every op landed, 0 skips) emits NO observation (scored
    clean). An apply RAISE or a conservation violation emits a BILLABLE observation; a
    typed non-blocking op-skip emits a non-billable ``cnf_unsupported`` observation.
    Deterministic given the frozen Farchive bytes.
    """
    from lawvm.eu.eu_ordering import order_eu_ops
    from lawvm.eu.fmx4_amendment_grammar import lower_amending_act
    from lawvm.eu.pipeline import apply_eu_ops_conserved

    sid = f"{chain.amender}->{chain.base}"

    base = _graft(archive, chain.base)
    if base is None:
        return EUAttribution(sid=sid, observations=(), status="ERROR:base-not-graftable")

    amender_bytes = _fetch_fmx4_bytes(archive, chain.amender)
    if not amender_bytes:
        return EUAttribution(sid=sid, observations=(), status="ERROR:amender-not-stored")

    lowered = lower_amending_act(amender_bytes, chain.amender, base_celex=chain.base)
    ops = list(lowered.ops)
    total = len(ops)
    if total == 0:
        return EUAttribution(sid=sid, observations=(), status="ERROR:amender-lowered-zero-ops")

    ordered = order_eu_ops(ops)
    window = "enacted..amended"
    try:
        result = apply_eu_ops_conserved(base, list(ordered.ops))
    # An apply RAISE is a genuine replay crash — the headline BILLABLE residual the
    # metric exists to catch. Type it, do NOT let it propagate (it is the finding).
    # lawvm-failloud: the raise IS the typed observation, not a swallowed error.
    except Exception as exc:  # noqa: BLE001
        obs = EUReplayObservation(
            sid=sid,
            section_key="<apply-fold>",
            verdict=VERDICT_APPLY_RAISE,
            window=window,
            touching_amendments=(chain.amender,),
            evidence=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        return EUAttribution(
            sid=sid, observations=(obs,), total_ops=total, status="APPLY_RAISE"
        )

    applied = len(result.applied_ops)
    skipped = len(result.skipped_items)
    observations: list[EUReplayObservation] = []

    if applied + skipped != total:
        observations.append(
            EUReplayObservation(
                sid=sid,
                section_key="<conservation>",
                verdict=VERDICT_CONSERVATION_VIOLATION,
                window=window,
                touching_amendments=(chain.amender,),
                evidence=(
                    f"applied={applied} + skipped={skipped} != total={total}"
                ),
            )
        )
    else:
        # Each typed non-blocking op-skip is a standing capability-gap observation
        # (non-billable). A BLOCKING skip would be a harder gap — but the corpus is
        # curated to fully-applied windows, so blocking skips are absent at freeze;
        # a new one surfaces as a cnf_unsupported move the gate WARNs on.
        for rejected in result.skipped_items:
            reason_code = getattr(rejected, "reason_code", "") or "eu_replay_op_skip"
            observations.append(
                EUReplayObservation(
                    sid=sid,
                    section_key=str(reason_code),
                    verdict=VERDICT_TYPED_OP_SKIP,
                    window=window,
                    touching_amendments=(chain.amender,),
                    evidence=str(getattr(rejected, "reason", ""))[:200],
                )
            )

    return EUAttribution(
        sid=sid,
        observations=tuple(observations),
        applied=applied,
        skipped=skipped,
        total_ops=total,
        status="OK",
    )


# ---------------------------------------------------------------------------
# AgreementResidual projection (reuse the shared taxonomy, EU-stamped)
# ---------------------------------------------------------------------------


def observation_to_residual(obs: EUReplayObservation) -> AgreementResidual:
    """Project one EU replay observation into the shared AgreementResidual taxonomy.

    Uses :data:`_VERDICT_TO_RESIDUAL_FAMILY` (the wider AgreementResidual vocabulary),
    NOT the CTSF-family map the gate consumes — a typed op-skip is an
    ``accepted_non_executable_frontier`` here, ``cnf_unsupported`` in the gate.
    """
    family = cast(AgreementResidualFamily, _VERDICT_TO_RESIDUAL_FAMILY[obs.verdict])
    status = cast(AgreementResidualStatus, _VERDICT_TO_STATUS[obs.verdict])
    return AgreementResidual(
        residual_id=f"eu:replay-conservation:{obs.sid}:{obs.section_key}:{obs.window}",
        jurisdiction="european_union",
        agreement_surface="eu_replay_conservation",
        family=family,
        agreement_residual_status=status,
        owner_phase="eu_bench.anchor.replay_conservation",
        rule_id=obs.verdict,
        source_artifact_id=obs.sid,
        safe_default="classify_without_rewriting_replay_or_oracle",
        forbidden_shortcuts=(
            "conservation_observation_as_replay_authorization",
            "apply_raise_as_source_truth",
        ),
        detail={
            "section_key": obs.section_key,
            "window": obs.window,
            "touching_amendments": list(obs.touching_amendments),
            "evidence": obs.evidence,
        },
    )
