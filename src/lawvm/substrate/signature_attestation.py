"""``lawvm.signature_attestation.v1`` — the SEPARATE signing axis, schema-on-paper (design §24).

Totality and signing are **two different things; do not collapse them** (design
§24). A signature can sign a totality certificate, but a signature alone does not
prove totality. A signer signs a **claim over a root**, not "the system."

This module is the v0 **schema-on-paper** for that axis: the frozen
:class:`SignatureAttestation` type + the reserved ``signatures/`` layer + the
named subject / claim / signer / profile vocabularies. There is **NO PKI / no
crypto** here (design §24 "do NOT require signatures in v0; hash-rooted bundles
first, PKI later"). No signing logic, no verification of ``signature_bytes`` —
just the type and the reserved root so a real eIDAS seal / DSSE attestation lands
later WITHOUT a breaking redesign.

**The load-bearing invariant: an attestation lives in the EVIDENCE / PROOF plane
and MUST NOT enter any semantic object hash** (design §24). It is computed OVER an
``object_hash`` / root and is never a member of that object's identity — else
counter-signing a pack would change the pack's hash. Signatures are detached, in
their own ``signatures/`` layer; signable subjects are the Merkle ROOTS
(``corpus_totality_root``, ``work_universe_root``, ``state_selection_root``,
``pack_manifest_hash``, ``certificate_root``, …), never individual rows.

Multiple attestations are normal — each a DIFFERENT proposition: an ORK / OM
source seal over ``source_bundle_version_root``; a Finlex consolidation seal over
``state_selection_root`` (``claim_kind=official_consolidation``); a LawVM build
attestation over ``pack_manifest_hash`` + ``certificate_root``; an independent
checker attestation; an archive receipt. This lets a user distinguish *official
source universe complete* vs *LawVM derivation internally checkable* vs *keeper
has / hasn't adopted the consolidation*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, set_root

_SCHEMA_SIGNATURE_ATTESTATION = "lawvm.signature_attestation.v1"
_DOMAIN_SIGNATURE_ATTESTATION = "signature_attestation"
_DOMAIN_SIGNATURE_ATTESTATION_ROOT = "signature_attestation"

# The reserved detached layer name (design §24 "detached signatures in a
# ``signatures/`` layer"). Reserved EMPTY in v0 so omission is committed to.
SIGNATURES_LAYER = "signatures"


class SignatureAttestationError(ValueError):
    """A signature-attestation object violates a v0 schema invariant."""


# --------------------------------------------------------------------------- #
# Closed vocabularies (design §24).                                           #
# --------------------------------------------------------------------------- #

# What KIND of root a signature signs — the signable Merkle subjects (design §24
# "sign roots, not rows"). A signature over anything else is rejected at v0.
SUBJECT_KINDS: frozenset[str] = frozenset(
    {
        "corpus_totality_root",
        "work_universe_root",
        "source_manifestation_root",
        "source_bundle_version_root",
        "pack_manifest_hash",
        "content_leaf_root",
        "certified_transition_trace_root",
        "state_selection_root",
        "surface_overlay_root",
        "edge_index_root",
        "projection_root",
        "certificate_root",
    }
)

# The PROPOSITION a signature asserts over its subject (design §24).
CLAIM_KINDS: frozenset[str] = frozenset(
    {
        "official_source_bundle",
        "official_consolidation",
        "compiler_output",
        "reviewed_finding_set",
        "archival_receipt",
    }
)

# The ROLE of the signer (design §24).
SIGNER_ROLES: frozenset[str] = frozenset(
    {"source_keeper", "publisher", "compiler", "reviewer", "mirror"}
)

# The signature FORMAT profile — legal/public (eIDAS in ASiC) vs technical
# (COSE / DSSE, SLSA-style). All stored as the same attestation over a subject
# root (design §24 "two format layers, don't force one").
SIGNATURE_PROFILES: frozenset[str] = frozenset(
    {
        "eidas.qualified_electronic_seal.asice.xades.v1",
        "lawvm.dsse.v1",
        "lawvm.cose_sign1.v1",
    }
)

# The lifecycle status of an attestation (design §24).
ATTESTATION_STATUSES: frozenset[str] = frozenset(
    {"active", "superseded", "revoked", "expired", "unverifiable"}
)


# --------------------------------------------------------------------------- #
# Sub-blocks.                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AttestationSubject:
    """The signed subject — a ROOT, by kind + hash (design §24 "sign roots, not rows")."""

    subject_kind: str
    subject_hash: str

    def __post_init__(self) -> None:
        if self.subject_kind not in SUBJECT_KINDS:
            raise SignatureAttestationError(
                f"subject_kind must be a signable root {sorted(SUBJECT_KINDS)!r}, "
                f"got {self.subject_kind!r} — signatures sign roots, never rows (design §24)"
            )
        if not self.subject_hash:
            raise SignatureAttestationError("subject_hash must be a non-empty root hash string")

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {"subject_kind": self.subject_kind, "subject_hash": self.subject_hash}


@dataclass(frozen=True, slots=True)
class AttestationSigner:
    """Who signed + their role + the certificate that backs the key (design §24)."""

    signer_id: str
    signer_name: str
    signer_role: str
    certificate_ref: str = ""

    def __post_init__(self) -> None:
        if self.signer_role not in SIGNER_ROLES:
            raise SignatureAttestationError(
                f"signer_role must be one of {sorted(SIGNER_ROLES)!r}, got {self.signer_role!r}"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "signer_id": self.signer_id,
            "signer_name": nfc(self.signer_name),
            "signer_role": self.signer_role,
            "certificate_ref": self.certificate_ref,
        }


# --------------------------------------------------------------------------- #
# The attestation.                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SignatureAttestation:
    """``lawvm.signature_attestation.v1`` — schema-on-paper, NO crypto (design §24).

    A detached attestation: a ``signer`` asserts a ``claim_kind`` over a
    ``subject`` root, under a ``signature_profile``, with the actual signature
    bytes referenced (not inlined) via ``signature_bytes_ref``. This object lives
    in the ``signatures/`` evidence layer and MUST NOT enter any semantic object
    hash — its own ``attestation_id`` is the only hash, computed over the
    attestation body (so the attestation is itself content-addressed, but signing
    a pack never perturbs the pack's roots).

    v0 carries no verification logic: ``signature_bytes_ref`` /
    ``verification_material_refs`` / ``timestamp_refs`` are opaque locators a
    future verifier resolves. ``attestation_status`` defaults to ``unverifiable``
    because v0 cannot verify (honest — never claim ``active`` without checking).
    """

    subject: AttestationSubject
    claim_kind: str
    signer: AttestationSigner
    signature_profile: str
    signed_at: str
    signature_bytes_ref: str
    attestation_status: str = "unverifiable"
    timestamp_refs: tuple[str, ...] = ()
    verification_material_refs: tuple[str, ...] = ()
    payload: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        if self.claim_kind not in CLAIM_KINDS:
            raise SignatureAttestationError(
                f"claim_kind must be one of {sorted(CLAIM_KINDS)!r}, got {self.claim_kind!r}"
            )
        if self.signature_profile not in SIGNATURE_PROFILES:
            raise SignatureAttestationError(
                f"signature_profile must be one of {sorted(SIGNATURE_PROFILES)!r}, "
                f"got {self.signature_profile!r}"
            )
        if self.attestation_status not in ATTESTATION_STATUSES:
            raise SignatureAttestationError(
                f"attestation_status must be one of {sorted(ATTESTATION_STATUSES)!r}, "
                f"got {self.attestation_status!r}"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_SIGNATURE_ATTESTATION,
            "subject": self.subject.to_canonical_dict(),
            "claim_kind": self.claim_kind,
            "signer": self.signer.to_canonical_dict(),
            "signature_profile": self.signature_profile,
            "signed_at": self.signed_at,
            "signature_bytes_ref": self.signature_bytes_ref,
            "attestation_status": self.attestation_status,
            "timestamp_refs": list(self.timestamp_refs),
            "verification_material_refs": list(self.verification_material_refs),
            "payload": dict(self.payload) if self.payload is not None else None,
        }

    @property
    def attestation_id(self) -> str:
        """Content id of the attestation — its ONLY hash (never enters a subject hash)."""
        return leaf_hash(_DOMAIN_SIGNATURE_ATTESTATION, self.to_canonical_dict())


def signature_attestation_root(attestations: Sequence[SignatureAttestation]) -> str:
    """``SetRoot`` over attestation ids — the reserved ``signature_attestation_root``.

    Empty (the v0 case) is a valid empty ``SetRoot`` — the reserved
    ``signatures/`` layer omission is itself committed to. This root is what the
    manifest's reserved ``signature_attestation_root`` member holds once a pack
    actually carries detached signatures.
    """
    return set_root(
        _DOMAIN_SIGNATURE_ATTESTATION_ROOT, [a.attestation_id for a in attestations]
    )
