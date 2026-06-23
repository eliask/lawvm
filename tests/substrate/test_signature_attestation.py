"""Pins for ``lawvm.signature_attestation.v1`` (design §24; SIGNATURE_ATTESTATIONS_V0.md).

Schema-on-paper, NO crypto. The load-bearing invariant: an attestation is an
EVIDENCE-plane object computed over a subject ROOT; it MUST NOT enter that
subject's hash (countersigning never changes the signed root). Tests pin the
closed vocabularies (subject kinds / claim kinds / signer roles / profiles /
statuses), the "sign roots not rows" rule, the honest default status, and the
detached ``signature_attestation_root`` (empty = valid empty SetRoot).
"""

from __future__ import annotations

import pytest

from lawvm.substrate.signature_attestation import (
    AttestationSigner,
    AttestationSubject,
    SignatureAttestation,
    SignatureAttestationError,
    signature_attestation_root,
)


def _att(**kw) -> SignatureAttestation:
    return SignatureAttestation(
        subject=kw.pop(
            "subject",
            AttestationSubject("corpus_totality_root", "sha256:root"),
        ),
        claim_kind=kw.pop("claim_kind", "compiler_output"),
        signer=kw.pop("signer", AttestationSigner("lawvm.build", "LawVM Build", "compiler")),
        signature_profile=kw.pop("signature_profile", "lawvm.dsse.v1"),
        signed_at=kw.pop("signed_at", "2026-06-22T00:00:00Z"),
        signature_bytes_ref=kw.pop("signature_bytes_ref", "sha256:sigref"),
    )


def test_default_status_is_unverifiable() -> None:
    """v0 cannot verify → it must NOT claim ``active`` (honest default)."""
    assert _att().status == "unverifiable"


def test_subject_must_be_a_signable_root() -> None:
    """Sign roots, not rows (design §24) — a non-root subject kind is rejected."""
    with pytest.raises(SignatureAttestationError):
        AttestationSubject("selection_row", "sha256:x")


def test_unknown_claim_kind_rejected() -> None:
    with pytest.raises(SignatureAttestationError):
        _att(claim_kind="totally_official")


def test_unknown_signer_role_rejected() -> None:
    with pytest.raises(SignatureAttestationError):
        AttestationSigner("x", "X", "dictator")


def test_unknown_profile_rejected() -> None:
    with pytest.raises(SignatureAttestationError):
        _att(signature_profile="pgp.clearsign.v0")


def test_attestation_id_is_content_addressed_and_deterministic() -> None:
    assert _att().attestation_id == _att().attestation_id
    assert _att().attestation_id.startswith("sha256:")


def test_attestation_does_not_carry_its_own_id_in_body() -> None:
    """The id is never a member of the hashed body (evidence-plane identity rule)."""
    body = _att().to_canonical_dict()
    assert "attestation_id" not in body
    assert "signature_attestation_id" not in body


def test_eidas_and_cose_profiles_accepted() -> None:
    for profile in (
        "eidas.qualified_electronic_seal.asice.xades.v1",
        "lawvm.cose_sign1.v1",
    ):
        assert _att(signature_profile=profile).signature_profile == profile


def test_empty_signature_root_is_valid_empty_set_root() -> None:
    """The reserved ``signatures/`` layer omission is committed to (empty SetRoot)."""
    root = signature_attestation_root([])
    assert root.startswith("sha256:")


def test_signature_root_changes_with_membership() -> None:
    a = _att()
    b = _att(claim_kind="reviewed_finding_set")
    assert signature_attestation_root([a]) != signature_attestation_root([a, b])
