"""The model-code derivation ladder — proof-graded edges per tier (§25.9 Step 3).

These pins exercise :mod:`lawvm.substrate.model_code_derivation` on small
SYNTHETIC fixtures (no live parquet): the ladder classifier produces a
matrix-legal edge per tier, a trivial-delta sibling pair earns
``verified_textual_derivation`` (delta_verified, replay reproduces), a genuinely
divergent pair falls back to ``kinship`` + a typed residual (NOT verified), an
ORC cite yields ``incorporates_by_reference``, and a corrupted delta is rejected
from the legal_state plane (the firewall bites). The live-LOCUS end-to-end pack
lives in the e2e harness, not here.
"""

from __future__ import annotations

from lawvm.substrate.model_code_derivation import (
    RESIDUAL_REPLAY_MISMATCH,
    Provision,
    apply_edit_script,
    build_verified_derivation_edge,
    compute_edit_script,
    derive_model_code_edges,
)
from lawvm.substrate.relation_edge import (
    AuthorityPlane,
    RelationKind,
    VerificationLevel,
    edge_authority_violation,
    recompute_edge_id,
)

CV = "us-oh-model-code:test"
BASELINE = "us-local:cities:oh/portsmouth"
SIBLING = "us-local:cities:oh/newlexington"


# --- fixtures -------------------------------------------------------------- #
# A baseline provision and a sibling that differ ONLY by the markdown header
# level (## vs ###) — the canonical verbatim-adoption shape (section body
# byte-identical). The derivation surface = header + body, so this IS a real,
# replay-checkable delta.
_BODY_337_17 = (
    "(a) No person shall operate a vehicle without due regard for the safety "
    "of persons or property. (ORC 4511.20)\n"
    "(b) Whoever violates this section is guilty of a minor misdemeanor."
)
_BASELINE_337_17 = Provision(
    address="337.17",
    header="## 337.17 RECKLESS OPERATION.",
    body=_BODY_337_17,
)
_SIBLING_337_17 = Provision(
    address="337.17",
    header="### 337.17 RECKLESS OPERATION.",  # only the markdown level differs
    body=_BODY_337_17,
)
# A genuinely divergent sibling (same address + title skeleton, different body).
_SIBLING_337_17_DIVERGENT = Provision(
    address="337.17",
    header="### 337.17 RECKLESS OPERATION.",
    body="(a) A wholly different local provision text that does not derive.",
)


def _edges_by_kind(edges, kind):
    return [e for e in edges if e["relation_kind"] == kind.value]


# --- delta + replay core --------------------------------------------------- #


def test_trivial_delta_replays_byte_for_byte() -> None:
    base = _BASELINE_337_17.derivation_text
    sib = _SIBLING_337_17.derivation_text
    assert base != sib  # the markdown level differs
    script = compute_edit_script(base, sib)
    assert apply_edit_script(base, script) == sib  # replay reproduces exactly
    assert script.edit_script_id
    assert script.baseline_text_hash != script.sibling_text_hash


def test_edit_script_is_deterministic() -> None:
    base = _BASELINE_337_17.derivation_text
    sib = _SIBLING_337_17.derivation_text
    a = compute_edit_script(base, sib)
    b = compute_edit_script(base, sib)
    assert a.edit_script_id == b.edit_script_id


# --- ladder: verified derivation tier -------------------------------------- #


def test_trivial_delta_pair_yields_verified_textual_derivation() -> None:
    res = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17],
        corpus_version=CV,
    )
    assert res.n_verified_textual_derivation == 1
    assert res.n_replay_mismatch == 0
    vtd = _edges_by_kind(res.edges, RelationKind.VERIFIED_TEXTUAL_DERIVATION)
    assert len(vtd) == 1
    edge = vtd[0]
    # The STRONG side of the matrix.
    assert edge["authority_plane"] == AuthorityPlane.LEGAL_STATE.value
    assert edge["verification_level"] == VerificationLevel.DELTA_VERIFIED.value
    assert edge["replay_authorized"] is True
    assert edge_authority_violation(edge) is None
    # The delta is carried so the claim is checkable.
    scope = edge["effective_scope"]
    assert scope["replay_reproduces_sibling"] is True
    assert scope["edit_script_id"]
    assert scope["edit_script_id"] in edge["evidence_refs"]
    # edge_id is the content hash of the body w/o itself.
    assert recompute_edge_id(edge) == edge["edge_id"]


def test_kinship_also_emitted_for_verbatim_pair() -> None:
    res = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17],
        corpus_version=CV,
    )
    # The pair shares a title skeleton (## vs ### stripped) → a kinship edge too.
    kin = _edges_by_kind(res.edges, RelationKind.KINSHIP)
    assert len(kin) == 1
    assert kin[0]["authority_plane"] == AuthorityPlane.OVERLAY.value
    assert kin[0]["verification_level"] == VerificationLevel.INDUCED_SIMILARITY.value
    assert kin[0]["replay_authorized"] is False
    assert edge_authority_violation(kin[0]) is None


# --- ladder: divergent pair falls back to kinship + residual --------------- #


def test_divergent_pair_is_kinship_plus_residual_not_verified() -> None:
    res = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17_DIVERGENT],
        corpus_version=CV,
    )
    # NO legal-state edge for a non-reproducible derivation.
    assert res.n_verified_textual_derivation == 0
    assert _edges_by_kind(res.edges, RelationKind.VERIFIED_TEXTUAL_DERIVATION) == []
    # A typed, self-evidencing residual instead.
    assert res.n_replay_mismatch == 1
    assert len(res.residuals) == 1
    residual = res.residuals[0]
    assert residual.kind == RESIDUAL_REPLAY_MISMATCH
    assert residual.address == "337.17"
    assert "337.17" in residual.detail
    # The kinship edge (shared skeleton) still owns the resemblance.
    assert _edges_by_kind(res.edges, RelationKind.KINSHIP)


# --- ladder: ORC cite → incorporates_by_reference -------------------------- #


def test_orc_cite_yields_incorporates_by_reference_edge() -> None:
    res = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17],
        corpus_version=CV,
    )
    inc = _edges_by_kind(res.edges, RelationKind.INCORPORATES_BY_REFERENCE)
    assert len(inc) == 1  # one (ORC 4511.20) cite in the body
    edge = inc[0]
    assert edge["authority_plane"] == AuthorityPlane.EVIDENCE.value
    assert edge["verification_level"] == VerificationLevel.SOURCE_ASSERTED.value
    assert edge["replay_authorized"] is False
    assert edge["target_set"] == ["us-oh-orc:section:4511.20"]
    assert edge_authority_violation(edge) is None


def test_orc_cite_is_not_a_verified_derivation() -> None:
    # The ORC axis must NEVER be a legal-state derivation (the city paraphrases
    # the ORC; it does not reproduce it).
    res = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17],
        corpus_version=CV,
    )
    for edge in _edges_by_kind(res.edges, RelationKind.INCORPORATES_BY_REFERENCE):
        assert edge["authority_plane"] != AuthorityPlane.LEGAL_STATE.value


# --- the firewall: a corrupted delta cannot be legal_state ----------------- #


def test_corrupted_delta_replay_is_not_byte_identical() -> None:
    base = _BASELINE_337_17.derivation_text
    sib = _SIBLING_337_17.derivation_text
    script = compute_edit_script(base, sib)
    # Corrupt the first copy op (drop its last char) → replay no longer matches.
    from lawvm.substrate.model_code_derivation import EditOp, EditScript

    corrupted_ops = []
    bumped = False
    for op in script.ops:
        if op.op == "copy" and not bumped and op.end - op.start > 1:
            corrupted_ops.append(EditOp(op="copy", start=op.start, end=op.end - 1))
            bumped = True
        else:
            corrupted_ops.append(op)
    corrupted = EditScript(
        ops=tuple(corrupted_ops),
        baseline_text_hash=script.baseline_text_hash,
        sibling_text_hash=script.sibling_text_hash,
        copy_coverage=script.copy_coverage,
        edit_script_id=script.edit_script_id,
    )
    assert apply_edit_script(base, corrupted) != sib


def test_corrupted_delta_is_rejected_from_legal_state_via_replay_gate() -> None:
    # The classifier gates the legal-state edge on replay==sibling. Simulate the
    # gate seeing a corrupted delta: it must NOT emit verified_textual_derivation.
    res = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17_DIVERGENT],  # replay can't reproduce
        corpus_version=CV,
    )
    assert res.n_verified_textual_derivation == 0


def test_legal_state_edge_with_tampered_id_fails_recompute() -> None:
    # A verified-derivation edge whose body is mutated post-hoc no longer hashes
    # to its declared edge_id (the L0.8 identity check the checker enforces).
    script = compute_edit_script(
        _BASELINE_337_17.derivation_text, _SIBLING_337_17.derivation_text
    )
    edge = build_verified_derivation_edge(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        address="337.17",
        script=script,
        corpus_version=CV,
    )
    assert recompute_edge_id(edge) == edge["edge_id"]
    tampered = dict(edge)
    tampered["source_ref"] = "provision:evil#337.17"
    assert recompute_edge_id(tampered) != tampered["edge_id"]


def test_copy_coverage_gate_rejects_low_overlap_as_non_derivation() -> None:
    # A full-replace pair (replay reproduces by construction, but COPIES ~nothing
    # of the baseline) must NOT be a derivation — the copy-coverage gate is what
    # makes the legal-state claim meaningful (replay alone always reproduces).
    from lawvm.substrate.model_code_derivation import (
        DERIVATION_COPY_COVERAGE_MIN,
        apply_edit_script,
        compute_edit_script,
    )

    base = "## 410.01 SPEED LIMITS.\nThe speed limit is twenty-five miles per hour."
    sib = "### 410.01 NOISE ORDINANCE.\nNo person shall make unreasonable noise."
    script = compute_edit_script(base, sib)
    assert apply_edit_script(base, script) == sib  # replay still reproduces
    assert script.copy_coverage < DERIVATION_COPY_COVERAGE_MIN  # but not verbatim


def test_provisions_from_locus_rows_keeps_only_exact_dotted_leaves() -> None:
    from lawvm.substrate.locus import LocusRow
    from lawvm.substrate.model_code_derivation import provisions_from_locus_rows

    def _row(i, header, content):
        return LocusRow(
            row_index=i,
            header=header,
            content=content,
            is_substantive=True,
            function=None,
            topic=None,
            scores={},
        )

    rows = [
        _row(0, "# 337.17 RECKLESS OPERATION.", "(a) body."),
        _row(1, "GENERAL PROVISIONS", "a title heading, no dotted address"),
        _row(2, "## 337.18 STREET RACING.", "(a) other body."),
    ]
    provs = provisions_from_locus_rows(rows)
    addresses = {p.address for p in provs}
    assert addresses == {"337.17", "337.18"}  # the title heading is dropped here


def test_all_tiers_emitted_never_only_successes() -> None:
    # Two siblings: one verbatim (verified), one divergent (residual). The result
    # carries BOTH the success edge AND the residual — never a silent drop.
    res_ok = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17],
        corpus_version=CV,
    )
    res_bad = derive_model_code_edges(
        baseline_work_id=BASELINE,
        sibling_work_id=SIBLING,
        baseline_provisions=[_BASELINE_337_17],
        sibling_provisions=[_SIBLING_337_17_DIVERGENT],
        corpus_version=CV,
    )
    assert res_ok.n_verified_textual_derivation == 1 and res_ok.n_replay_mismatch == 0
    assert res_bad.n_verified_textual_derivation == 0 and res_bad.n_replay_mismatch == 1
    # Every emitted edge is matrix-legal by construction.
    for res in (res_ok, res_bad):
        for edge in res.edges:
            assert edge_authority_violation(edge) is None
