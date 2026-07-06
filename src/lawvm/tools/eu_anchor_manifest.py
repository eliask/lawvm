"""eu_anchor_manifest.py — the European Union frozen content-addressed anchor +
replay-attribution engine (#183/#204 → #221, FOURTH jurisdiction).

This is the EU analogue of :mod:`lawvm.tools.uk_anchor_manifest` (United Kingdom),
:mod:`lawvm.tools.ee_anchor_manifest` (Estonia), and
:mod:`lawvm.tools.fi_anchor_manifest` (Finland), extending the drift-robust #183
CTSF metric (``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4) to a fourth
jurisdiction. It is ADDITIVE: it never mutates the EU corpus, the EU replay
pipeline, or the existing ``eu_bench_unit_result`` scoring path; the EU frontend
stays byte-identical.

#221 UPDATE (read the "#221 — the EU ORACLE-TOUCH surface" section below): the
premise of the next paragraph — no stored consolidation, no dated DAG — was TRUE
at #204 and is preserved here as the historical record of why the conserved-apply
lane exists; #221 closed both gaps for the frozen corpus bases (75 published
sector-0 consolidations stored offline + a frozen dated closure table) and the
gate's PRIMARY EU surface is now the FI-style oracle-touch attribution
(:func:`attribute_base_consolidations`). The conserved-apply window scoring below
remains as the documented FALLBACK for the one base with zero published
consolidations (``32017R1576``).

WHAT AN EU "ANCHOR" WAS AT #204 — and why EU then did not fit the FI/EE/UK
ORACLE-TOUCH MODEL (historical, load-bearing for the fallback lane). Finland
enumerates the published *consolidation snapshots* of one statute over its life;
Estonia enumerates the Riigi Teataja *terviktekst* chain per ``grupi_id``; the UK
models each act as an enacted→current replay window scored against the single
revised in-force oracle. All three have, in the Farchive, a materialized ORACLE
(a published consolidated text) to score the native replay AGAINST. The EU Cellar
corpus, as acquired by #204, did NOT:

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


# ---------------------------------------------------------------------------
# #221 — the EU ORACLE-TOUCH surface (published sector-0 consolidations).
#
# The header above documented WHY EU could not run the FI/EE/UK oracle-touch
# model: the Farchive stored no sector-0 consolidation and no dated amendment
# DAG. #221 closed BOTH gaps for the frozen corpus bases:
#
#   * ``scripts/acquire_eu_consolidations.py`` stored the 75 PUBLISHED dated
#     sector-0 consolidations of 8 of the 9 frozen bases under
#     ``cellar://celex/{base}/{YYYYMMDD}/eng/fmx4`` — real EUR-Lex consolidated
#     FMX4 bytes, readable offline.
#   * the dated amendment DAG of those bases (``eu_amendment_graph`` over the
#     live CDM SPARQL endpoint) is FROZEN below as a content-pinned edge table
#     (:data:`REAL_ANCHOR_EU_AMENDMENT_CLOSURE`) — the gate never enumerates
#     live; a DAG refresh is a deliberate, reviewed constant change.
#
# An EU oracle anchor is one stored consolidation ``(base, as_of)``. The native
# PIT body is reconstructed by the MULTI-AMENDER closure replay: graft the base,
# lower EVERY closure amender effective by ``as_of``, order the combined op set
# legally (``order_eu_ops`` over the threaded dates), and run the conserved
# apply fold. The per-article diff against the stored consolidation
# (``eu_consolidation_oracle`` / ``compare_replay_to_consolidation``) feeds
# Finland's NEUTRAL attribution calculus (``attribute_divergences``) — the same
# typed touch-relation verdicts FI/EE/UK/NZ emit, projected through the same
# ``_VERDICT_TO_FAMILY``. The consolidation stays an EDITORIAL witness ("no
# legal value" — the eu_oracle_divergence honesty regime): divergences are
# TYPED, never repaired toward.
#
# CLOSURE INCLUSION RULE (documented, load-bearing): an amender is effective by
# ``as_of`` iff its EARLIEST machine-readable date (entry-into-force or
# date-of-application) is <= as_of. EUR-Lex suffixes a consolidation with "the
# date of entry into force or of application" of the last incorporated act, and
# BOTH orders occur on this corpus (32009R0754's amenders apply retroactively
# before their entry into force; 32010R1093's 32014R0806 enters into force
# before it applies) — the earliest-date rule is the one consistent with the
# Office's observed incorporation practice. A residual inclusion mismatch
# surfaces as a typed divergence (usually spontaneous-healing/appearance, an
# oracle-commensurability artifact), never as a repair.
#
# HONEST GAP TYPING (the partial-closure discipline): an anchor whose closure
# window is NOT fully replayable — an amender effective by as_of with no stored
# bytes (acquisition gap), an amender with unlowered non-boilerplate
# instructions (lowering capability gap), or a typed apply-fold op skip — is
# marked commensurability-suspect (``AnchorObservation.oracle_suspect``), so
# Finland's calculus types ALL its divergences ``temporal_mismatch`` (non-
# billable): replay KNOWS it under-applied, so neither replay nor the oracle
# can be convicted at that anchor. Each distinct gap cause is ALSO emitted once
# as a typed, non-billable EU observation so the residual set carries the gap
# explicitly (never buried). Corrigenda (undated, unstored ``…R(NN)`` edges)
# are a standing exclusion from the closure: a corrigendum-caused text change
# surfaces as an untouched-unit divergence the calculus types oracle-side
# (editorial) — exactly what a corrigendum is. Billable convictions
# (``candidate_replay_bug`` / ``untyped``) can therefore ONLY arise at
# fully-covered anchors — the conservative, honest regime.
#
# #221 BACKLOG (the typed frontier, precise — every item is VISIBLE in the
# frozen baseline as a cnf_unsupported / temporal_mismatch row, never buried):
#
#   1. MULTI-POINT OMNIBUS INSTRUCTION LOWERING — DONE (Increment 4 in
#      ``fmx4_amendment_grammar``): "Regulation (EU) No X is amended as
#      follows: (1) …; (2) …" NP sub-instructions are iterated (with nest
#      contexts, per-NP foreign-target guard, grafter-commensurable payload
#      extraction), directive-amender substantive articles are typed
#      ``non_amending_provision``, and annex-lane/act-metadata instructions
#      are typed OFF-SURFACE (``eu_closure_off_surface_gap``, article-only
#      compare surface untouched). The flip itself CONVICTED and root-fixed a
#      standing mis-lowering: the whole-article REPLACE rule's free ``.*?``
#      gap swallowed "In Article 2 of Regulation 923/2012, point 104 is
#      replaced …" and nuked 32012R0923 Article 2 to the point payload
#      (billed at 32012R0923@20150630 the moment the anchor first scored).
#   1b. SUB-ARTICLE APPLY RESOLUTION (the NEW dominant suspicion cause, the
#      ``eu_replay_typed_op_skip`` rows): the EU grafter lifts NO label onto
#      paragraph/item nodes (``NO.PARAG`` is not parsed; points are flattened
#      into ALINEA text), so every lowered sub-article REPLACE/REPEAL targets
#      an unresolvable coordinate and typed-skips (507 skips → 32010R1093
#      alone). Fixing this lives in ``eu/grafter.py`` + the apply seam — a
#      separate lane from this manifest.
#   2. AMENDERS UNACQUIRABLE AS FMX4 (``eu_closure_amender_unstored`` +
#      the ``envelope_no_enacting_terms``/``annex_root_no_number`` lowering
#      gaps): 32016R0646 / 32017R1221 (→ 32008R0692) and 32016R1185
#      (→ 32012R0923) have no stored eng fmx4; ~17 more amenders are stored
#      as a ~2KB metadata-only DOC envelope (32011R0566, 32014R0806,
#      32015L2366, 32019R2175, 32010R0279 …) or as their own ANNEX body in
#      lieu of the act (32014L0059) — the acquisition lane stored the wrong
#      manifestation item (needs item-shape investigation). These are now the
#      SECOND dominant anchor-suspicion cause.
#   3. CORRIGENDA (``…R(NN)`` edges, undated + unstored) are excluded from the
#      closure by construction; their text effects type as
#      ``oracle_editorial_pathology`` via the touch relation (correct for an
#      editorial-lane instrument, but acquiring + dating them would let replay
#      reproduce e.g. 32008R0402R(01)'s "A.TR.1"→"A.TR." fix).
#   4. INCLUSION-RULE REFINEMENT: the earliest-date rule can include an
#      amender one consolidation early when the Office incorporated it at its
#      LATER date; the mismatch surfaces as non-billable spontaneous-healing /
#      appearance rows at fully-covered anchors (none observed at freeze).
#   5. EMBEDDED ANNEX-INSTRUCTION SEQUENCES (``eu_closure_off_surface_gap``):
#      "Annex I … is amended in accordance with the Annex to this Regulation"
#      ships its amendments INSIDE the amender's own annex; executing them
#      needs an annex-body sub-grammar (plus stored base annexes to apply to —
#      the graft currently materialises zero ``supplements``).
# ---------------------------------------------------------------------------

#: Anchor could not be scored against its stored consolidation (consolidation
#: bytes unparseable / apply-fold unreachable) — a typed, non-billable PIT gap.
VERDICT_ORACLE_ANCHOR_UNSCORABLE = "eu_oracle_anchor_unscorable"
#: A closure amender effective by as_of has no stored FMX4 — acquisition gap.
VERDICT_CLOSURE_AMENDER_UNSTORED = "eu_closure_amender_unstored"
#: A closure amender carries unlowered non-boilerplate instructions — a
#: lowering capability gap (grammar coverage), non-billable.
VERDICT_CLOSURE_LOWERING_GAP = "eu_closure_lowering_gap"
#: A closure amender carries an OFF-SURFACE gap: an ANNEX-LANE gap (embedded
#: annex-instruction indirection, annex-internal point edits, a separate-
#: manifestation annex payload) or an act-METADATA gap (act-title replace).
#: Typed + visible, but it does NOT commensurability-poison the base's
#: anchors: the EU anchor compare surface is ARTICLE-only (per-article diff
#: against the consolidation), and an annex-/title-scoped instruction cannot
#: change any article unit, so article scoring stays honest with the gap open.
VERDICT_CLOSURE_OFF_SURFACE_GAP = "eu_closure_off_surface_gap"

#: Lowering-diagnostic families that are NOT closure gaps: a foreign-target
#: instruction amends a different instrument; a non-amending provision is the
#: amending act's own substantive/final law (it cannot touch the base). Neither
#: can make replay-vs-oracle article units incommensurable.
_NON_GAP_DIAG_FAMILIES = frozenset({"foreign_target", "non_amending_provision"})
#: Lowering-diagnostic families that are OFF-SURFACE gaps (typed via
#: :data:`VERDICT_CLOSURE_OFF_SURFACE_GAP`, excluded from anchor suspicion).
_OFF_SURFACE_DIAG_FAMILIES = frozenset(
    {"annex_payload_gap", "annex_extraction_gap", "act_metadata_gap"}
)

_VERDICT_TO_FAMILY.update(
    {
        VERDICT_ORACLE_ANCHOR_UNSCORABLE: "temporal_mismatch",
        VERDICT_CLOSURE_AMENDER_UNSTORED: "temporal_mismatch",
        VERDICT_CLOSURE_LOWERING_GAP: "cnf_unsupported",
        VERDICT_CLOSURE_OFF_SURFACE_GAP: "cnf_unsupported",
    }
)
_VERDICT_TO_RESIDUAL_FAMILY.update(
    {
        VERDICT_ORACLE_ANCHOR_UNSCORABLE: "temporal_mismatch",
        VERDICT_CLOSURE_AMENDER_UNSTORED: "temporal_mismatch",
        VERDICT_CLOSURE_LOWERING_GAP: "accepted_non_executable_frontier",
        VERDICT_CLOSURE_OFF_SURFACE_GAP: "accepted_non_executable_frontier",
    }
)
_VERDICT_TO_STATUS.update(
    {
        VERDICT_ORACLE_ANCHOR_UNSCORABLE: "blocked",
        VERDICT_CLOSURE_AMENDER_UNSTORED: "blocked",
        VERDICT_CLOSURE_LOWERING_GAP: "blocked",
        VERDICT_CLOSURE_OFF_SURFACE_GAP: "blocked",
    }
)


@dataclass(frozen=True)
class EUAmendmentEdgeRef:
    """One frozen, dated CDM amendment edge (amender → the closure's base).

    A content-pinned snapshot of ``eu_amendment_graph.AmendmentEdge`` (queried
    live once, frozen here so the gate is offline + deterministic). Dates are
    ISO or ``""`` (the act exposed no machine-readable date — honest gap; such
    an edge is never included in a dated closure).
    """

    celex: str
    relation_kind: str  # "amends" | "corrects"
    entry_into_force: str = ""
    date_of_application: str = ""

    @property
    def earliest_date(self) -> str:
        dates = [d for d in (self.entry_into_force, self.date_of_application) if d]
        return min(dates) if dates else ""

    @property
    def ordering_date(self) -> str:
        """The legal-chronological ordering key (date-of-application, else EIF)."""
        return self.date_of_application or self.entry_into_force

    def effective_by(self, as_of_iso: str) -> bool:
        """True iff this edge's amendment is incorporated at ``as_of_iso``.

        The earliest-date inclusion rule (see the section header): EUR-Lex
        incorporates an amending act once its first legal date (entry into
        force or application, whichever comes first) has arrived.
        """
        earliest = self.earliest_date
        return bool(earliest) and earliest <= as_of_iso


#: The frozen corpus bases with PUBLISHED sector-0 consolidations stored in the
#: Farchive (75 dated snapshots over these 8). The ninth frozen base,
#: ``32017R1576``, has ZERO published consolidations (verified live) — it stays
#: on the conserved-apply fallback lane in ``ctsf_gate``.
REAL_ANCHOR_EU_ORACLE_BASES: tuple[str, ...] = (
    "32008R0402",
    "32008R0692",
    "32009R0754",
    "32009R1284",
    "32010R1093",
    "32012R0923",
    "32019R0787",
    "32022R2309",
)

#: The frozen dated amendment DAG per oracle base — a content-pinned snapshot
#: of the live CDM graph (``eu_amendment_graph.build_amendment_graph``, queried
#: 2026-07-05). Sorted per base by (ordering_date, celex) as the live module
#: returns them. A refresh is a deliberate constant change, reviewed like any
#: frozen-corpus move (#137 discipline) — never a silent live re-enumeration.
REAL_ANCHOR_EU_AMENDMENT_CLOSURE: dict[str, tuple[EUAmendmentEdgeRef, ...]] = {
    "32008R0402": (
        EUAmendmentEdgeRef("32008R0402R(01)", "corrects", "", ""),
        EUAmendmentEdgeRef("32013R0519", "amends", "2013-07-01", "2013-07-01"),
    ),
    "32008R0692": (
        EUAmendmentEdgeRef("32008R0692R(01)", "corrects", "", ""),
        EUAmendmentEdgeRef("32008R0692R(02)", "corrects", "", ""),
        EUAmendmentEdgeRef("32008R0692R(03)", "corrects", "", ""),
        EUAmendmentEdgeRef("32011R0566", "amends", "2011-06-19", "2011-06-19"),
        EUAmendmentEdgeRef("32012R0459", "amends", "2012-06-04", "2012-06-04"),
        EUAmendmentEdgeRef("32012R0630", "amends", "2012-08-02", "2012-08-02"),
        EUAmendmentEdgeRef("32013R0171", "amends", "2013-03-19", "2013-03-19"),
        EUAmendmentEdgeRef("32013R0195", "amends", "2013-03-28", "2013-07-01"),
        EUAmendmentEdgeRef("32013R0143", "amends", "2013-01-01", "2014-01-01"),
        EUAmendmentEdgeRef("32014R0136", "amends", "2014-03-05", "2014-03-05"),
        EUAmendmentEdgeRef("32015R0045", "amends", "2015-02-04", "2015-02-04"),
        EUAmendmentEdgeRef("32016R0427", "amends", "2016-01-01", "2016-04-20"),
        EUAmendmentEdgeRef("32016R0646", "amends", "2016-05-16", "2016-05-16"),
        EUAmendmentEdgeRef("32017R1151", "amends", "2017-07-27", "2017-07-27"),
        EUAmendmentEdgeRef("32018R1832", "amends", "2018-12-17", "2019-01-01"),
        EUAmendmentEdgeRef("32017R1221", "amends", "2017-07-27", "2019-09-01"),
    ),
    "32009R0754": (
        EUAmendmentEdgeRef("32009R0754R(01)", "corrects", "", ""),
        EUAmendmentEdgeRef("32010R0053", "amends", "2010-01-01", "2010-01-27"),
        EUAmendmentEdgeRef("32010R0712", "amends", "2010-01-01", "2010-08-11"),
        EUAmendmentEdgeRef("32011R0057", "amends", "2011-01-01", "2011-02-01"),
        EUAmendmentEdgeRef("32011R1106", "amends", "2011-01-01", "2011-11-05"),
        EUAmendmentEdgeRef("32012R1040", "amends", "2012-01-01", "2012-11-10"),
        EUAmendmentEdgeRef("32013R1182", "amends", "2013-01-01", "2013-11-23"),
        EUAmendmentEdgeRef("32014R0732", "amends", "2014-01-01", "2014-07-05"),
    ),
    "32009R1284": (
        EUAmendmentEdgeRef("32009R1284R(01)", "corrects", "", ""),
        EUAmendmentEdgeRef("32010R0279", "amends", "2010-04-02", "2010-04-02"),
        EUAmendmentEdgeRef("32011R0269", "amends", "2011-03-22", "2011-03-22"),
        EUAmendmentEdgeRef("32011R1295", "amends", "2011-12-14", "2011-12-14"),
        EUAmendmentEdgeRef("32013R0049", "amends", "2013-01-24", "2013-01-24"),
        EUAmendmentEdgeRef("32013R0517", "amends", "2013-07-01", "2013-07-01"),
        EUAmendmentEdgeRef("32014R0380", "amends", "2014-04-16", "2014-04-16"),
        EUAmendmentEdgeRef("32018R1604", "amends", "2018-10-27", "2018-10-27"),
        EUAmendmentEdgeRef("32019R1163", "amends", "2019-07-09", "2019-07-09"),
        EUAmendmentEdgeRef("32019R1778", "amends", "2019-10-26", "2019-10-26"),
        EUAmendmentEdgeRef("32021R1301", "amends", "2021-08-07", "2021-08-07"),
        EUAmendmentEdgeRef("32022R0595", "amends", "2022-04-13", "2022-04-13"),
        EUAmendmentEdgeRef("32022R2042", "amends", "2022-10-26", "2022-10-26"),
        EUAmendmentEdgeRef("32023R2694", "amends", "2023-11-29", "2023-11-29"),
        EUAmendmentEdgeRef("32024R2465", "amends", "2024-09-13", "2024-09-13"),
    ),
    "32010R1093": (
        EUAmendmentEdgeRef("32010R1093R(01)", "corrects", "", ""),
        EUAmendmentEdgeRef("32010R1093R(02)", "corrects", "", ""),
        EUAmendmentEdgeRef("32013R1022", "amends", "2013-10-30", "2013-10-30"),
        EUAmendmentEdgeRef("32014L0017", "amends", "2014-03-20", "2014-03-20"),
        EUAmendmentEdgeRef("32014R0258", "amends", "2014-01-01", "2014-04-09"),
        EUAmendmentEdgeRef("32014L0059", "amends", "2014-07-02", "2015-01-01"),
        EUAmendmentEdgeRef("32014R0806", "amends", "2014-08-19", "2016-01-01"),
        EUAmendmentEdgeRef("32015L2366", "amends", "2016-01-12", "2016-01-12"),
        EUAmendmentEdgeRef("32018R1717", "amends", "2018-11-16", "2019-03-30"),
        EUAmendmentEdgeRef("32019R2033", "amends", "2019-12-25", "2021-06-26"),
        EUAmendmentEdgeRef("32019R2175", "amends", "2019-12-30", "2022-01-01"),
        EUAmendmentEdgeRef("32023R1114", "amends", "2023-06-29", "2024-12-30"),
        EUAmendmentEdgeRef("32025R2088", "amends", "2025-11-10", "2025-11-10"),
        EUAmendmentEdgeRef("32024R1620", "amends", "2024-06-26", "2025-12-31"),
    ),
    "32012R0923": (
        EUAmendmentEdgeRef("32012R0923R(01)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(02)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(03)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(04)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(05)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(06)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(07)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(08)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(09)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(10)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(11)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(12)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(13)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(14)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(15)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(16)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(17)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(18)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(19)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(20)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(21)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(22)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(23)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(24)", "amends", "", ""),
        EUAmendmentEdgeRef("32012R0923R(25)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(26)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(27)", "corrects", "", ""),
        EUAmendmentEdgeRef("32012R0923R(28)", "corrects", "", ""),
        EUAmendmentEdgeRef("32015R0340", "amends", "2015-03-26", "2015-06-30"),
        EUAmendmentEdgeRef("32017R0835", "amends", "2017-06-06", "2017-06-06"),
        EUAmendmentEdgeRef("32016R1185", "amends", "2016-08-10", "2017-10-12"),
        EUAmendmentEdgeRef("32020R0886", "amends", "2020-07-19", "2020-07-19"),
        EUAmendmentEdgeRef("32020R0469", "amends", "2020-04-23", "2022-01-27"),
        EUAmendmentEdgeRef("32021R0666", "amends", "2021-05-13", "2023-01-26"),
        EUAmendmentEdgeRef("32023R1772", "amends", "2023-10-05", "2023-10-05"),
        EUAmendmentEdgeRef("32024R0379", "amends", "2024-02-15", "2024-02-15"),
        EUAmendmentEdgeRef("32024R0404", "amends", "2024-05-01", "2025-05-01"),
        EUAmendmentEdgeRef("32024R1111", "amends", "2024-06-12", "2025-05-01"),
    ),
    "32019R0787": (
        EUAmendmentEdgeRef("32019R0787R(01)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(02)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(03)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(04)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(05)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(06)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(07)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(08)", "corrects", "", ""),
        EUAmendmentEdgeRef("32019R0787R(09)", "corrects", "", ""),
        EUAmendmentEdgeRef("32021R1096", "amends", "2021-05-25", "2021-07-09"),
        EUAmendmentEdgeRef("32021R1334", "amends", "2021-05-25", "2021-08-15"),
        EUAmendmentEdgeRef("32021R1335", "amends", "2021-05-25", "2021-08-15"),
        EUAmendmentEdgeRef("32021R1465", "amends", "2021-05-25", "2021-09-16"),
        EUAmendmentEdgeRef("32022R1303", "amends", "2022-08-15", "2022-08-15"),
        EUAmendmentEdgeRef("32024R1143", "amends", "2024-05-13", "2025-01-01"),
    ),
    "32022R2309": (
        EUAmendmentEdgeRef("32023R0331", "amends", "2023-02-16", "2023-02-16"),
        EUAmendmentEdgeRef("32023R1569", "amends", "2023-08-01", "2023-08-01"),
        EUAmendmentEdgeRef("32023R2573", "amends", "2023-11-15", "2023-11-15"),
        EUAmendmentEdgeRef("32024R0291", "amends", "2024-01-15", "2024-01-15"),
        EUAmendmentEdgeRef("32024R1803", "amends", "2024-06-26", "2024-06-26"),
        EUAmendmentEdgeRef("32024R2465", "amends", "2024-09-13", "2024-09-13"),
        EUAmendmentEdgeRef("32024R2755", "amends", "2024-10-24", "2024-10-24"),
        EUAmendmentEdgeRef("32024R3138", "amends", "2024-12-16", "2024-12-16"),
        EUAmendmentEdgeRef("32025R0608", "amends", "2025-03-26", "2025-03-26"),
        EUAmendmentEdgeRef("32025R1433", "amends", "2025-07-15", "2025-07-15"),
        EUAmendmentEdgeRef("32025R1576", "amends", "2025-07-29", "2025-07-29"),
        EUAmendmentEdgeRef("32025R2443", "amends", "2025-12-01", "2025-12-01"),
        EUAmendmentEdgeRef("32025R2567", "amends", "2025-12-15", "2025-12-15"),
    ),}


def _dated_locator(base_celex: str, date8: str, lang: str) -> str:
    return f"cellar://celex/{base_celex}/{date8}/{lang}/fmx4"


def enumerate_stored_consolidation_dates(archive: Any, base_celex: str) -> tuple[str, ...]:
    """The SORTED ``YYYYMMDD`` dates of stored sector-0 consolidations of a base.

    Reads the Farchive locator index (offline): every dated locator
    ``cellar://celex/{base}/{YYYYMMDD}/…/fmx4`` is one published consolidation
    snapshot the #221 acquisition stored. The ``enacted`` locator is the base
    act itself, not an anchor.
    """
    prefix = f"cellar://celex/{base_celex}/"
    dates: set[str] = set()
    for locator in archive.locators(prefix + "%"):
        rest = locator[len(prefix):]
        date8 = rest.split("/", 1)[0]
        if len(date8) == 8 and date8.isdigit():
            dates.add(date8)
    return tuple(sorted(dates))


def _graft_dated(archive: Any, base_celex: str, date8: str):
    """Parse a STORED dated consolidation into an ``IRStatute`` offline, or None."""
    from lawvm.eu.grafter import parse_eu_regulation_ir

    data = None
    for lang in ("eng", "fin"):
        data = archive.get(_dated_locator(base_celex, date8, lang))
        if data:
            break
    if not data:
        return None
    with tempfile.NamedTemporaryFile(suffix=".xml") as tf:
        tf.write(data)
        tf.flush()
        try:
            return parse_eu_regulation_ir(Path(tf.name), celex=f"0{base_celex[1:]}-{date8}")
        # A consolidation-parse failure is "this anchor is unscorable" (a typed,
        # non-billable PIT gap the caller records) — not an error to raise.
        # lawvm-failloud: graft-availability probe; failure is the answer.
        except Exception:  # noqa: BLE001
            return None


#: Final-provision boilerplate ("This Regulation shall enter into force …";
#: "shall be binding in its entirety and directly applicable …") is NOT an
#: amendment instruction: an uncovered diagnostic over it is not a coverage gap.
def _is_final_provision_excerpt(excerpt: str) -> bool:
    low = excerpt.lower()
    return "enter into force" in low or "binding in its entirety" in low


def _typography_commensurable_equal(replay_text: str, oracle_text: str) -> bool:
    """True iff two article renderings agree modulo TYPOGRAPHY only.

    The EU commensurable compare surface (mirrors UK's normalized-eId choice of
    a per-key surface). TWO typography dimensions are elided, both SYMMETRIC
    (applied to BOTH sides — never moving either side toward the other's
    wording, so a commensurability choice, not an oracle repair):

    * WHITESPACE — FMX4 whitespace is typography, not content: the grafter
      renders inline point markers ``by:(a)the`` while a replay-materialized
      QUOT payload space-joins them ``by: (a) the``, and OJ signs drift between
      ``–7 °C`` and ``– 7 °C`` across renderings of the SAME words.
    * LIST PUNCTUATION — parentheses, semicolons and full stops. The Office's
      consolidation re-renders list-marker/separator typography of amendment
      payloads to the base act's own house style: 32010R0053 adds points to
      32009R0754 Article 1 with source markers ``c)`` / ``d)`` and the
      published consolidation renders ``(c)`` / ``(d)`` and reflows the
      previous point's terminal ``.`` into ``;`` (verified against the raw
      amender bytes at 32009R0754@20100101 — the same 1-D editorial-artifact
      class Finland's oracle doctrine elides, never consumes). WORD content is
      never elided: ``certificate A.TR.1.`` vs ``certificate A.TR.`` stays
      divergent on the ``1``.
    """
    def _surface(t: str) -> str:
        return "".join(t.translate(_LIST_TYPOGRAPHY_ELISION).split())

    return _surface(replay_text) == _surface(oracle_text)


#: The elided list-punctuation characters (see _typography_commensurable_equal).
_LIST_TYPOGRAPHY_ELISION = str.maketrans({"(": " ", ")": " ", ";": " ", ".": " "})


@dataclass(frozen=True)
class EUOracleAttribution:
    """The typed oracle-touch attribution of ONE base's consolidation chain.

    ``observations`` are Finland's neutral :class:`TouchObservation` verdicts
    (projected via the FI ``_VERDICT_TO_FAMILY``); ``eu_observations`` are the
    EU-native typed extras (apply raise / conservation violation — billable;
    acquisition/lowering/op-skip gaps — non-billable), projected via the EU
    :data:`_VERDICT_TO_FAMILY`.
    """

    sid: str  # the base CELEX
    anchors: tuple[Any, ...]  # fi_anchor_manifest.AnchorObservation
    observations: tuple[Any, ...]  # fi_anchor_manifest.TouchObservation
    eu_observations: tuple[EUReplayObservation, ...]
    # VOCAB-02: namespaced (not bare ``status``) — the attribution-computability
    # resolution of this base's whole consolidation chain.
    resolution_status: str = "OK"

    def family_counts(self) -> dict[str, int]:
        """Project ALL observations into their CTSF residual families."""
        from lawvm.tools.fi_anchor_manifest import (
            _VERDICT_TO_FAMILY as _FI_VERDICT_TO_FAMILY,
        )

        families: dict[str, int] = {}
        for obs in self.observations:
            fam = str(_FI_VERDICT_TO_FAMILY[obs.verdict])
            families[fam] = families.get(fam, 0) + 1
        for eobs in self.eu_observations:
            fam = _VERDICT_TO_FAMILY[eobs.verdict]
            families[fam] = families.get(fam, 0) + 1
        return {fam: n for fam, n in sorted(families.items()) if n}


def attribute_base_consolidations(base_celex: str, *, archive: Any) -> EUOracleAttribution:
    """Score one base's stored consolidation chain via multi-amender PIT closure.

    For each stored ``(base, as_of)`` consolidation (ascending): rebuild the
    native PIT body (graft base → lower every closure amender effective by
    as_of → order the combined op set → conserved apply fold), diff it
    per-article against the stored consolidation, and build one
    ``AnchorObservation``. Finland's neutral ``attribute_divergences`` then
    runs the touch relation over the whole chronological chain. Gap-limited
    anchors carry an ``oracle_suspect`` witness (→ ``temporal_mismatch``);
    apply raises / conservation violations are typed BILLABLE EU observations.
    Deterministic given the frozen Farchive bytes + frozen closure table.
    """
    from lawvm.eu.eu_oracle_divergence import (
        _articles,
        compare_replay_to_consolidation,
    )
    from lawvm.eu.eu_ordering import order_eu_ops
    from lawvm.eu.fmx4_amendment_grammar import lower_amending_act
    from lawvm.eu.pipeline import apply_eu_ops_conserved
    from lawvm.tools.fi_anchor_manifest import (
        AnchorObservation,
        attribute_divergences,
    )

    dates = enumerate_stored_consolidation_dates(archive, base_celex)
    if not dates:
        return EUOracleAttribution(
            sid=base_celex,
            anchors=(),
            observations=(),
            eu_observations=(),
            resolution_status="ERROR:no-stored-consolidations",
        )
    base_ir = _graft(archive, base_celex)
    if base_ir is None:
        return EUOracleAttribution(
            sid=base_celex,
            anchors=(),
            observations=(),
            eu_observations=(),
            resolution_status="ERROR:base-not-graftable",
        )

    edges = REAL_ANCHOR_EU_AMENDMENT_CLOSURE.get(base_celex, ())
    amend_edges = [e for e in edges if e.relation_kind == "amends"]

    # Lower each stored amender ONCE (ops are frozen dataclasses; the apply fold
    # never mutates its inputs, so the lowered op set is reusable per anchor).
    lowered_cache: dict[str, Any] = {}

    def _lowered(edge: EUAmendmentEdgeRef):
        if edge.celex not in lowered_cache:
            data = _fetch_fmx4_bytes(archive, edge.celex)
            if not data:
                lowered_cache[edge.celex] = None
            else:
                lowered_cache[edge.celex] = lower_amending_act(
                    data,
                    edge.celex,
                    base_celex=base_celex,
                    effective=edge.date_of_application or edge.entry_into_force,
                    enacted=edge.entry_into_force,
                )
        return lowered_cache[edge.celex]

    anchors: list[AnchorObservation] = []
    eu_observations: list[EUReplayObservation] = []
    seen_gap_keys: set[tuple[str, str]] = set()

    def _emit_once(verdict: str, key: str, window: str, amenders: tuple[str, ...], evidence: str) -> None:
        dedup = (verdict, key)
        if dedup in seen_gap_keys:
            return
        seen_gap_keys.add(dedup)
        eu_observations.append(
            EUReplayObservation(
                sid=base_celex,
                section_key=key,
                verdict=verdict,
                window=window,
                touching_amendments=amenders,
                evidence=evidence[:300],
            )
        )

    prev_iso = ""
    for date8 in dates:
        iso = f"{date8[:4]}-{date8[4:6]}-{date8[6:]}"
        closure = [e for e in amend_edges if e.effective_by(iso)]
        window_amenders = tuple(
            e.celex for e in closure if not prev_iso or not e.effective_by(prev_iso)
        )
        witnesses: list[str] = []
        ops: list[Any] = []
        for edge in closure:
            low = _lowered(edge)
            if low is None:
                witnesses.append(
                    f"amender {edge.celex} effective by {iso} has no stored FMX4"
                )
                _emit_once(
                    VERDICT_CLOSURE_AMENDER_UNSTORED,
                    edge.celex,
                    f"..{iso}",
                    (edge.celex,),
                    "closure amender bytes absent from the Farchive (acquisition gap)",
                )
                continue
            gap_diags = [
                d
                for d in low.diagnostics
                if d.family not in _NON_GAP_DIAG_FAMILIES
                and d.family not in _OFF_SURFACE_DIAG_FAMILIES
                and not _is_final_provision_excerpt(d.source_excerpt)
            ]
            if gap_diags:
                witnesses.append(
                    f"amender {edge.celex}: {len(gap_diags)} unlowered instruction(s)"
                )
                _emit_once(
                    VERDICT_CLOSURE_LOWERING_GAP,
                    edge.celex,
                    f"..{iso}",
                    (edge.celex,),
                    "; ".join(f"{d.rule_id}: {d.source_excerpt[:80]}" for d in gap_diags[:3]),
                )
            # Off-surface gaps (annex lane / act metadata) are TYPED (never
            # buried) but do not poison the article-only compare surface (see
            # VERDICT_CLOSURE_OFF_SURFACE_GAP).
            annex_gap_diags = [
                d for d in low.diagnostics if d.family in _OFF_SURFACE_DIAG_FAMILIES
            ]
            if annex_gap_diags:
                _emit_once(
                    VERDICT_CLOSURE_OFF_SURFACE_GAP,
                    edge.celex,
                    f"..{iso}",
                    (edge.celex,),
                    "; ".join(
                        f"{d.rule_id}: {d.source_excerpt[:80]}"
                        for d in annex_gap_diags[:3]
                    ),
                )
            ops.extend(low.ops)

        version_tag = f"0{base_celex[1:]}-{date8}"
        ordered = order_eu_ops(list(ops))
        try:
            result = apply_eu_ops_conserved(base_ir, list(ordered.ops))
        # An apply RAISE is the headline BILLABLE replay crash — the raise IS
        # the typed observation, and the anchor is unscored (excluded from the
        # touch chain), never a swallowed error.
        # lawvm-failloud: the raise IS the typed observation.
        except Exception as exc:  # noqa: BLE001
            eu_observations.append(
                EUReplayObservation(
                    sid=base_celex,
                    section_key="<apply-fold>",
                    verdict=VERDICT_APPLY_RAISE,
                    window=f"{prev_iso or '-'}..{iso}",
                    touching_amendments=window_amenders,
                    evidence=f"{type(exc).__name__}: {str(exc)[:200]}",
                )
            )
            anchors.append(
                AnchorObservation(
                    version_tag=version_tag,
                    amendment_id=",".join(window_amenders),
                    as_of=iso,
                    struct_sim=-1.0,
                    n_sections=0,
                    n_penalized=0,
                    penalized_keys=frozenset(),
                    replay_text={},
                    oracle_suspect=None,
                    status="APPLY_RAISE",
                )
            )
            prev_iso = iso
            continue

        applied = len(result.applied_ops)
        skipped = len(result.skipped_items)
        if applied + skipped != len(ordered.ops):
            eu_observations.append(
                EUReplayObservation(
                    sid=base_celex,
                    section_key="<conservation>",
                    verdict=VERDICT_CONSERVATION_VIOLATION,
                    window=f"{prev_iso or '-'}..{iso}",
                    touching_amendments=window_amenders,
                    evidence=(
                        f"applied={applied} + skipped={skipped} != total={len(ordered.ops)}"
                    ),
                )
            )
        for rejected in result.skipped_items:
            reason_code = getattr(rejected, "reason_code", "") or "eu_replay_op_skip"
            op = getattr(rejected, "item", None)
            op_id = getattr(op, "op_id", "") or str(reason_code)
            # An ANNEX-rooted op skip cannot poison the ARTICLE-only compare
            # surface (the same lane argument as VERDICT_CLOSURE_OFF_SURFACE_GAP):
            # typed + visible, but not an anchor-suspicion witness.
            annex_lane = (
                op is not None
                and getattr(op, "target", None) is not None
                and op.target.root_kind() == "supplements"
            )
            if not annex_lane:
                witnesses.append(f"op {op_id} typed-skipped ({reason_code})")
            _emit_once(
                VERDICT_TYPED_OP_SKIP,
                str(op_id),
                f"..{iso}",
                window_amenders,
                str(getattr(rejected, "reason", ""))[:200],
            )

        cons_ir = _graft_dated(archive, base_celex, date8)
        if cons_ir is None:
            _emit_once(
                VERDICT_ORACLE_ANCHOR_UNSCORABLE,
                version_tag,
                f"{prev_iso or '-'}..{iso}",
                window_amenders,
                "stored consolidation bytes did not graft to an IRStatute",
            )
            anchors.append(
                AnchorObservation(
                    version_tag=version_tag,
                    amendment_id=",".join(window_amenders),
                    as_of=iso,
                    struct_sim=-1.0,
                    n_sections=0,
                    n_penalized=0,
                    penalized_keys=frozenset(),
                    replay_text={},
                    oracle_suspect=None,
                    status="ERROR:consolidation-not-graftable",
                )
            )
            prev_iso = iso
            continue

        comparison = compare_replay_to_consolidation(
            result.statute, cons_ir, as_of=iso, base_celex=base_celex
        )
        # Penalized = per-article divergence on the EU commensurable compare
        # surface: a text_divergence whose sides agree modulo whitespace is
        # typography, not a divergence (see _typography_commensurable_equal).
        penalized = frozenset(
            d.article_label
            for d in comparison.divergences
            if not d.agrees
            and not (
                d.kind == "text_divergence"
                and _typography_commensurable_equal(d.replay_text, d.oracle_text)
            )
        )
        n_compared = comparison.article_count
        struct_sim = 1.0 if not n_compared else 1.0 - len(penalized) / n_compared
        anchors.append(
            AnchorObservation(
                version_tag=version_tag,
                amendment_id=",".join(window_amenders),
                as_of=iso,
                struct_sim=struct_sim,
                n_sections=n_compared,
                n_penalized=len(penalized),
                penalized_keys=penalized,
                replay_text=_articles(result.statute),
                oracle_suspect=("; ".join(witnesses) or None),
                status="OK",
            )
        )
        prev_iso = iso

    observations = attribute_divergences(base_celex, anchors)
    return EUOracleAttribution(
        sid=base_celex,
        anchors=tuple(anchors),
        observations=tuple(observations),
        eu_observations=tuple(eu_observations),
    )
