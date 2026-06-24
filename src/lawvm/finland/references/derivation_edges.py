"""Typed derivation-edge classification for Finnish works — four DISTINCT kinds.

The motivating honesty rule (Pro relation-edge decision, round-12):

    "deduplication is not authority; shared bytes do not prove shared legal
    origin."

When one piece of Finnish legal text relates to another, that relationship is
NOT a single thing. There are at least four *categorically different* ways two
provisions can be related, and the substrate relation-edge algebra
(``lawvm.legal_relation_edge.v0``) already types them on different authority
planes. They must NEVER be conflated:

* **textual** (:data:`DerivationKind.TEXTUAL`) — shared / copied WORDING. Two
  provisions reproduce the same bytes (after NFC normalization), reproducible by
  a deterministic edit-script replay. This is the ONLY kind a byte comparison
  can establish, and it establishes ONLY shared wording — it says *nothing*
  about which provision is the legal ancestor, whether one was modelled on the
  other, or whether either conforms to anything. Substrate edge:
  ``verified_textual_derivation`` on the ``legal_state`` plane
  (``delta_verified``) when the replay reproduces the target byte-for-byte.

* **model_code** (:data:`DerivationKind.MODEL_CODE`) — one provision MODELLED on
  another (a template/model-code kinship). This is a claim about LINEAGE, and
  lineage is *not* computable from bytes alone — two acts can share text by
  coincidence, by common upstream source, or by genuine modelling, and bytes
  cannot tell them apart. Therefore model-code kinship is, by default, a
  ``kinship`` edge on the ``overlay`` plane (``induced_similarity``,
  ``replay_authorized=false``): a *discovery-only resemblance*, explicitly
  typed-UNKNOWN as to legal lineage. We never upgrade shared bytes to a lineage
  claim without external authority.

* **conformance** (:data:`DerivationKind.CONFORMANCE`) — an act CONFORMS to / is
  the national transposition of an EU directive. Conformance is an external
  legal assessment we do not perform. The honest posture is a single
  ``conformance_assessment`` edge that records the ABSENCE of an assessment
  (``overlay`` plane, ``external_assessment``, ``status=open``) — paired with the
  act's own ``source_claimed_transposition`` evidence edge when it declares it.

* **citation** (:data:`DerivationKind.CITATION`) — the text POINTS AT a target
  (a cross-reference). Source-anchored evidence, never legal state: ``citation``
  on the ``surface`` plane.

The classifier here is a FINLAND-LAYER projection. It does NOT live in, and does
NOT modify, ``lawvm.substrate`` source — it only *imports* the substrate
relation-edge constructors (``build_relation_edge`` / ``edge_authority_violation``
/ the ``RelationKind`` etc. enums) read-only, exactly the way a jurisdiction
adapter is meant to feed the universal edge algebra. The substrate already has a
US-municipal model-code ladder (``substrate.model_code_derivation``); that ladder
is *not* wired into the Finnish export path and is hard-coded to Ohio-Revised-Code
conventions (``(ORC 4511.21)`` cites, Walter-Drane skeletons). This module is the
Finnish-layer counterpart, built from FI provision text + FI reference / EU
transposition extraction, with the SAME four-way non-conflation discipline.

HONESTY BOUNDARY (what is computed vs. not):

* ``textual``  — COMPUTED from bytes only (NFC + deterministic edit-script replay
  + a copy-coverage gate). A ``legal_state`` ``verified_textual_derivation`` edge
  is emitted ONLY when the replay reproduces the target byte-for-byte and the
  target is substantially the source's bytes. Otherwise a ``kinship`` resemblance
  edge + a typed residual — never a fabricated lineage claim.
* ``model_code`` — NOT decidable from bytes. Emitted as a ``kinship`` edge whose
  ``effective_scope`` carries ``lineage_decided=False`` and
  ``lineage_basis="bytes_only_not_lineage"``: a structurally-honest
  typed-UNKNOWN, never a guessed ancestry.
* ``conformance`` — NOT assessed here. The ``conformance_assessment`` edge is the
  ABSENCE marker (``status=open``); the directive binding is taken verbatim from
  the act's own declaration (``source_claimed_transposition``), with the binding
  status (resolved / ambiguous / statute_only) carried through unchanged.
* ``citation`` — COMPUTED structurally from the citing text's pointer; the target
  identity is registry-resolved or source-asserted, never invented.
"""

from __future__ import annotations

import difflib
import enum
from dataclasses import dataclass, field
from typing import Optional, Sequence

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.relation_edge import (
    AuthorityPlane,
    EdgeStatus,
    RelationKind,
    TargetSetSemantics,
    VerificationLevel,
    build_relation_edge,
    edge_authority_violation,
)
from lawvm.substrate.roots import leaf_hash

# --------------------------------------------------------------------------- #
# The four kinds — the non-conflation taxonomy.                               #
# --------------------------------------------------------------------------- #


class DerivationKind(enum.Enum):
    """The four categorically-distinct ways one FI provision relates to another.

    These are NOT interchangeable. A byte match establishes ``TEXTUAL`` only; it
    does not license ``MODEL_CODE`` (lineage), ``CONFORMANCE`` (EU transposition),
    or ``CITATION`` (a pointer). Each maps to a different substrate
    :class:`RelationKind` on a different authority plane.
    """

    TEXTUAL = "textual"
    """Shared / copied WORDING — byte-reproducible. The only byte-decidable kind.
    Says nothing about lineage, conformance, or citation."""

    MODEL_CODE = "model_code"
    """One provision MODELLED on another (template kinship). LINEAGE — not
    decidable from bytes; emitted as a typed-UNKNOWN resemblance."""

    CONFORMANCE = "conformance"
    """An act CONFORMS to / transposes an EU directive. External legal assessment
    we do NOT perform; emitted as an absence-of-assessment marker."""

    CITATION = "citation"
    """The text POINTS AT a target (a cross-reference). Source-anchored evidence,
    never legal state."""


# The fraction of the TARGET bytes that must come verbatim from the SOURCE for a
# textual match to count as a genuine copy (rather than a coincidental overlap of
# boilerplate). Mirrors substrate.model_code_derivation's coverage gate so the FI
# and US ladders share the same legal-state threshold.
TEXTUAL_COPY_COVERAGE_MIN = 0.80


# --------------------------------------------------------------------------- #
# Provenance + residual records.                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DerivationResidual:
    """A typed, self-evidencing residual — a relationship that did NOT earn its
    strong edge (e.g. a textual candidate whose replay was not byte-identical, so
    it is owned as a resemblance, never as a fabricated derivation)."""

    kind: str
    detail: str

    def to_json(self) -> dict[str, JsonValue]:
        return {"kind": self.kind, "detail": nfc(self.detail)}


@dataclass(slots=True)
class DerivationEdgeSet:
    """The DISTINCT typed edges produced for a set of FI relationships.

    Each list holds ``lawvm.legal_relation_edge.v0`` bodies of exactly one
    :class:`DerivationKind`. They are kept SEPARATE by construction so a consumer
    physically cannot read a textual match as a lineage / conformance / citation
    claim — the non-conflation is structural, not a convention.
    """

    textual: list[dict[str, JsonValue]] = field(default_factory=list)
    model_code: list[dict[str, JsonValue]] = field(default_factory=list)
    conformance: list[dict[str, JsonValue]] = field(default_factory=list)
    citation: list[dict[str, JsonValue]] = field(default_factory=list)
    residuals: list[DerivationResidual] = field(default_factory=list)

    def all_edges(self) -> list[dict[str, JsonValue]]:
        """All edges, kind-grouped order (textual, model_code, conformance, cite)."""
        return [*self.textual, *self.model_code, *self.conformance, *self.citation]

    def kind_of(self, edge: dict[str, JsonValue]) -> DerivationKind:
        """Return the :class:`DerivationKind` an emitted edge belongs to.

        Derived purely from the substrate ``relation_kind`` + authority posture —
        the same byte that a consumer reads — so the taxonomy round-trips."""
        rk = edge.get("relation_kind")
        plane = edge.get("authority_plane")
        if rk == RelationKind.VERIFIED_TEXTUAL_DERIVATION.value:
            return DerivationKind.TEXTUAL
        if rk == RelationKind.KINSHIP.value:
            return DerivationKind.MODEL_CODE
        if rk in (
            RelationKind.CONFORMANCE_ASSESSMENT.value,
            RelationKind.SOURCE_CLAIMED_TRANSPOSITION.value,
            RelationKind.TIMELINESS_FACT.value,
        ):
            return DerivationKind.CONFORMANCE
        if rk == RelationKind.CITATION.value:
            return DerivationKind.CITATION
        raise ValueError(
            f"edge relation_kind={rk!r} (plane={plane!r}) is not a "
            f"derivation-edge kind this classifier emits"
        )


# --------------------------------------------------------------------------- #
# Provision model + deterministic edit-script replay (the textual core).      #
# --------------------------------------------------------------------------- #


def _collapse_ws(text: str) -> str:
    """Collapse all runs of whitespace to a single space and strip the ends.

    Deterministic + bounded (``str.split`` over Unicode whitespace) — no regex,
    so this semantic-plane module carries no raw ``re.compile`` smell."""
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class FiProvision:
    """One addressable Finnish provision (e.g. ``2 §`` of an act).

    ``work_id`` is the act's stable engine id; ``address`` is the section/pykälä
    address (the join key with the related work); ``text`` is the provision body
    (raw, NFC applied on read). Bodies are compared as bytes — no semantic
    interpretation — because the textual kind is, by definition, byte-only.

    ``header`` is the provision's heading line (e.g. ``2 § Soveltamisala``). It
    drives ONLY the model-code resemblance key (:meth:`title_skeleton`) — two
    same-titled provisions *resemble* each other regardless of body. Defaults to
    the address so a header-less provision still has a stable skeleton.
    """

    work_id: str
    address: str
    text: str
    header: str = ""

    @property
    def normalized_text(self) -> str:
        """NFC-normalized provision text — the byte string a textual edge matches.

        NFC only: we do NOT collapse whitespace or case here, because a
        ``verified_textual_derivation`` reproduces the target BYTE-FOR-BYTE; any
        whitespace difference is a real, replay-checkable delta, not erased."""
        return nfc(self.text)

    def ref(self) -> str:
        """A content-addressable provision ref ``fi-provision:<work>#<address>``."""
        return f"fi-provision:{self.work_id}#{self.address}"

    def title_skeleton(self) -> str:
        """Whitespace-collapsed, case-folded HEADING — the kinship resemblance key.

        Used ONLY for the model-code resemblance tier: two provisions whose
        normalized headings match *resemble* each other (a same-titled section is
        a model-code candidate). This is a weaker signal than a byte match and
        NEVER licenses a lineage claim. The address is included so two acts'
        identically-titled ``2 § Soveltamisala`` share a skeleton even when their
        bodies diverge entirely."""
        head = self.header or self.address
        return _collapse_ws(nfc(head)).casefold()


_OP_COPY = "copy"
_OP_INSERT = "insert"
_DOMAIN_EDIT_SCRIPT = "fi_derivation_edit_script"
_DOMAIN_TEXT = "fi_derivation_text"


def _text_hash(text: str) -> str:
    return leaf_hash(_DOMAIN_TEXT, {"text": text})


@dataclass(frozen=True, slots=True)
class EditOp:
    """One edit-script operation. ``copy`` carries ``[start, end)`` source indices;
    ``insert`` carries the literal target ``text``."""

    op: str
    start: int = 0
    end: int = 0
    text: str = ""

    def to_json(self) -> JsonValue:
        if self.op == _OP_COPY:
            return {"op": _OP_COPY, "start": self.start, "end": self.end}
        return {"op": _OP_INSERT, "text": self.text}


@dataclass(frozen=True, slots=True)
class EditScript:
    """A deterministic source→target edit script + its content hash + coverage."""

    ops: tuple[EditOp, ...]
    source_text_hash: str
    target_text_hash: str
    copy_coverage: float
    edit_script_id: str


def compute_edit_script(source_text: str, target_text: str) -> EditScript:
    """Compute a DETERMINISTIC source→target copy/insert edit script.

    Character-level ``difflib.SequenceMatcher`` opcodes (deterministic for a fixed
    input pair) lowered to copy (verbatim source span) / insert (literal target
    span) ops. ``copy_coverage`` is the fraction of TARGET bytes that came from a
    copy op — how much of the target is verbatim source."""
    matcher = difflib.SequenceMatcher(a=source_text, b=target_text, autojunk=False)
    ops: list[EditOp] = []
    copied = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.append(EditOp(op=_OP_COPY, start=i1, end=i2))
            copied += i2 - i1
        elif tag in ("replace", "insert"):
            ops.append(EditOp(op=_OP_INSERT, text=target_text[j1:j2]))
        # ``delete`` contributes nothing to the output (source-only span).
    coverage = (copied / len(target_text)) if target_text else 1.0
    body: dict[str, JsonValue] = {
        "ops": [op.to_json() for op in ops],
        "source_text_hash": _text_hash(source_text),
        "target_text_hash": _text_hash(target_text),
    }
    return EditScript(
        ops=tuple(ops),
        source_text_hash=_text_hash(source_text),
        target_text_hash=_text_hash(target_text),
        copy_coverage=coverage,
        edit_script_id=leaf_hash(_DOMAIN_EDIT_SCRIPT, body),
    )


def apply_edit_script(source_text: str, script: EditScript) -> str:
    """REPLAY an edit script over ``source_text`` and return the produced text.

    Pure + deterministic. The result is compared BYTE-FOR-BYTE to the target by
    the classifier; only an exact match earns ``delta_verified``."""
    out: list[str] = []
    for op in script.ops:
        if op.op == _OP_COPY:
            out.append(source_text[op.start : op.end])
        elif op.op == _OP_INSERT:
            out.append(op.text)
        else:  # pragma: no cover — closed op vocabulary
            raise ValueError(f"unknown edit-script op {op.op!r}")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Edge builders — each pinned to its exact matrix-legal posture.              #
# --------------------------------------------------------------------------- #


def _assert_legal(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Guard: every edge MUST be matrix-legal (§25.3) before it leaves here."""
    reason = edge_authority_violation(body)
    assert reason is None, (
        "fi.derivation_edges produced a matrix-ILLEGAL edge "
        f"(authority_plane={body.get('authority_plane')!r}, "
        f"verification_level={body.get('verification_level')!r}, "
        f"replay_authorized={body.get('replay_authorized')!r}): {reason}"
    )
    return body


def build_textual_edge(
    *,
    source: FiProvision,
    target: FiProvision,
    script: EditScript,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """A byte-reproducible TEXTUAL derivation: ``verified_textual_derivation``.

    The STRONG side of the §25.3 matrix — ``legal_state`` / ``delta_verified`` /
    ``replay_authorized=true``. The caller MUST have already replayed ``script``
    over ``source.normalized_text`` and confirmed it reproduces
    ``target.normalized_text`` byte-for-byte. The edge body CARRIES the
    ``edit_script_id`` + both text hashes so the claim is checkable, not asserted.

    CRITICAL non-conflation: ``effective_scope`` explicitly records what this
    edge does and does NOT mean — shared bytes, NOT lineage / conformance /
    citation. A consumer reading this edge cannot mistake it for any of those.

    The ``replay_authorized`` legal-state grant is NOT author-set on the edge: it
    is carried by an explicit, granting :class:`ExecutionAuthorization` (PROJ-02 —
    a projection row may never hard-code replay authority; only a grant carrier
    may). The forbidden shortcuts name the exact crossings this builder must never
    take — the byte match is the ONLY authority, never a resemblance or a cite."""
    authorization = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="replay_authorized",
        authorization_rule_id="fi_verified_textual_derivation_delta_verified",
        owner_phase="finland.references.derivation_edges",
        strict_disposition="record",
        safe_default="block_without_delta_verified_replay",
        forbidden_shortcuts=(
            "title_skeleton_resemblance_as_replay_authority",
            "citation_pointer_as_textual_derivation",
            "eu_conformance_claim_as_textual_derivation",
        ),
        detail={
            "edit_script_id": script.edit_script_id,
            "source_text_hash": script.source_text_hash,
            "target_text_hash": script.target_text_hash,
            "copy_coverage": repr(script.copy_coverage),
        },
    )
    body = build_relation_edge(
        relation_kind=RelationKind.VERIFIED_TEXTUAL_DERIVATION,
        source_ref=target.ref(),
        target_set=(source.ref(),),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,
        verification_level=VerificationLevel.DELTA_VERIFIED,
        replay_authorized=authorization.replay_authorized,
        status=EdgeStatus.RESOLVED,
        effective_scope={
            "branch_id": "actual",
            "derivation_kind": DerivationKind.TEXTUAL.value,
            "edit_script_id": script.edit_script_id,
            "source_text_hash": script.source_text_hash,
            "target_text_hash": script.target_text_hash,
            "replay_reproduces_target": True,
            # Floats are forbidden in canonical JSON — carry as an exact string.
            "copy_coverage": repr(script.copy_coverage),
            # The honesty boundary, encoded IN the edge:
            "means": "shared_wording_byte_reproducible",
            "does_not_imply": ["model_code_lineage", "eu_conformance", "citation"],
        },
        corpus_version=corpus_version,
        evidence_refs=(script.edit_script_id,),
    )
    return _assert_legal(body)


def build_model_code_kinship_edge(
    *,
    source: FiProvision,
    target: FiProvision,
    skeleton: str,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """A MODEL-CODE resemblance, typed-UNKNOWN as to lineage: ``kinship``.

    Lineage is NOT decidable from bytes (shared text can be coincidence, common
    upstream source, or genuine modelling — bytes cannot distinguish). So this is
    a discovery-only ``overlay`` / ``induced_similarity`` edge with
    ``replay_authorized=false`` (§25.3 weak-evidence rule), status ``qualified``.
    ``effective_scope`` carries ``lineage_decided=False`` so the typed-UNKNOWN is
    explicit: we found a resemblance; we did NOT establish ancestry."""
    body = build_relation_edge(
        relation_kind=RelationKind.KINSHIP,
        source_ref=target.ref(),
        target_set=(source.ref(),),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.OVERLAY,
        verification_level=VerificationLevel.INDUCED_SIMILARITY,
        replay_authorized=False,
        status=EdgeStatus.QUALIFIED,
        effective_scope={
            "branch_id": "actual",
            "derivation_kind": DerivationKind.MODEL_CODE.value,
            "title_skeleton": skeleton,
            "lineage_decided": False,
            "lineage_basis": "bytes_only_not_lineage",
            "means": "resemblance_candidate_for_modelling",
            "does_not_imply": ["established_legal_lineage", "verified_textual_derivation"],
        },
        corpus_version=corpus_version,
    )
    return _assert_legal(body)


def build_conformance_absence_edge(
    *,
    citing_work_id: str,
    directive_celex: Optional[str],
    directive_surface: str,
    binding_status: str,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """The ABSENCE-of-conformance-assessment marker: ``conformance_assessment``.

    We do NOT perform EU-conformance assessment. The honest edge records that no
    assessment exists: ``overlay`` plane, ``external_assessment``,
    ``status=open``, ``replay_authorized=false``. The target is the directive (by
    CELEX when bound, else by surface) and ``effective_scope`` carries the
    binding status verbatim — never guessed."""
    target = (
        f"eu-directive:{directive_celex}"
        if directive_celex
        else f"eu-directive-surface:{directive_surface}"
    )
    body = build_relation_edge(
        relation_kind=RelationKind.CONFORMANCE_ASSESSMENT,
        source_ref=f"fi-work:{citing_work_id}",
        target_set=(target,),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.OVERLAY,
        verification_level=VerificationLevel.EXTERNAL_ASSESSMENT,
        replay_authorized=False,
        status=EdgeStatus.OPEN,
        effective_scope={
            "branch_id": "actual",
            "derivation_kind": DerivationKind.CONFORMANCE.value,
            "assessment_present": False,
            "directive_binding_status": binding_status,
            "directive_surface": directive_surface,
            "means": "conformance_not_assessed",
            "does_not_imply": ["verified_conformance", "verified_textual_derivation"],
        },
        corpus_version=corpus_version,
    )
    return _assert_legal(body)


def build_conformance_claim_edge(
    *,
    citing_work_id: str,
    directive_celex: Optional[str],
    directive_surface: str,
    binding_status: str,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """The act's OWN declaration that it transposes a directive:
    ``source_claimed_transposition``.

    Evidence the act asserts ("pannaan täytäntöön ... direktiivi"), NOT a verified
    conformance: ``evidence`` plane, ``source_asserted``,
    ``replay_authorized=false``, status ``resolved`` when the directive bound, else
    ``open``. This is the act's claim; the paired absence edge records that we did
    not verify it."""
    target = (
        f"eu-directive:{directive_celex}"
        if directive_celex
        else f"eu-directive-surface:{directive_surface}"
    )
    body = build_relation_edge(
        relation_kind=RelationKind.SOURCE_CLAIMED_TRANSPOSITION,
        source_ref=f"fi-work:{citing_work_id}",
        target_set=(target,),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.EVIDENCE,
        verification_level=VerificationLevel.SOURCE_ASSERTED,
        replay_authorized=False,
        status=(
            EdgeStatus.RESOLVED if directive_celex else EdgeStatus.OPEN
        ),
        effective_scope={
            "branch_id": "actual",
            "derivation_kind": DerivationKind.CONFORMANCE.value,
            "claim": "act_declares_transposition",
            "directive_binding_status": binding_status,
            "directive_surface": directive_surface,
            "means": "act_asserts_it_transposes_directive",
            "does_not_imply": ["verified_conformance", "verified_textual_derivation"],
        },
        corpus_version=corpus_version,
    )
    return _assert_legal(body)


def build_citation_edge(
    *,
    citing_provision: FiProvision,
    target_ref: str,
    target_resolved: bool,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """The text POINTS AT a target: ``citation`` on the ``surface`` plane.

    Source-anchored evidence, NEVER legal state — a cross-reference says the text
    mentions a target, nothing about shared wording or lineage.
    ``registry_resolved`` when the target identity is deterministically pinned,
    else ``source_asserted``; either way ``replay_authorized=false``."""
    level = (
        VerificationLevel.REGISTRY_RESOLVED
        if target_resolved
        else VerificationLevel.SOURCE_ASSERTED
    )
    body = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref=citing_provision.ref(),
        target_set=(target_ref,),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=level,
        replay_authorized=False,
        status=(EdgeStatus.RESOLVED if target_resolved else EdgeStatus.OPEN),
        effective_scope={
            "branch_id": "actual",
            "derivation_kind": DerivationKind.CITATION.value,
            "target_resolved": target_resolved,
            "means": "text_points_at_target",
            "does_not_imply": [
                "shared_wording",
                "model_code_lineage",
                "eu_conformance",
            ],
        },
        corpus_version=corpus_version,
    )
    return _assert_legal(body)


# --------------------------------------------------------------------------- #
# The classifier — turn FI relationships into the four DISTINCT edge kinds.    #
# --------------------------------------------------------------------------- #

RESIDUAL_TEXTUAL_NOT_BYTE_IDENTICAL = "fi_derivation_textual_replay_not_byte_identical"
RESIDUAL_TEXTUAL_COVERAGE = "fi_derivation_textual_copy_coverage_below_min"


def classify_textual(
    *,
    source: FiProvision,
    target: FiProvision,
    corpus_version: str,
) -> DerivationEdgeSet:
    """Classify the relationship between two provisions on the BYTE axis only.

    Computes a deterministic source→target edit script and REPLAYS it:

    * replay reproduces the target byte-for-byte AND copy-coverage
      ≥ :data:`TEXTUAL_COPY_COVERAGE_MIN` → a ``textual`` (legal_state) edge;
    * otherwise NO legal-state edge — a ``model_code`` resemblance edge (iff the
      normalized skeletons match) owns the resemblance, plus a typed residual.

    This is the honesty boundary made operational: we NEVER fabricate a textual
    derivation; shared text that does not byte-replay stays a resemblance."""
    result = DerivationEdgeSet()
    src_text = source.normalized_text
    tgt_text = target.normalized_text
    script = compute_edit_script(src_text, tgt_text)
    replayed = apply_edit_script(src_text, script)
    replay_ok = replayed == tgt_text
    verbatim_enough = script.copy_coverage >= TEXTUAL_COPY_COVERAGE_MIN

    if replay_ok and verbatim_enough:
        result.textual.append(
            build_textual_edge(
                source=source, target=target, script=script, corpus_version=corpus_version
            )
        )
        return result

    # NOT a textual derivation. The resemblance (if any) is a model-code kinship
    # candidate — typed-UNKNOWN as to lineage — never a fabricated derivation.
    if source.title_skeleton() == target.title_skeleton():
        result.model_code.append(
            build_model_code_kinship_edge(
                source=source,
                target=target,
                skeleton=target.title_skeleton(),
                corpus_version=corpus_version,
            )
        )
    if not replay_ok:
        result.residuals.append(
            DerivationResidual(
                kind=RESIDUAL_TEXTUAL_NOT_BYTE_IDENTICAL,
                detail=(
                    f"{target.ref()} vs {source.ref()}: replay reproduced "
                    f"{len(replayed)} bytes, target is {len(tgt_text)} bytes "
                    f"(not byte-identical); emitted model_code resemblance only, "
                    f"NOT a textual derivation"
                ),
            )
        )
    else:
        result.residuals.append(
            DerivationResidual(
                kind=RESIDUAL_TEXTUAL_COVERAGE,
                detail=(
                    f"{target.ref()} vs {source.ref()}: copy-coverage "
                    f"{script.copy_coverage:.3f} < {TEXTUAL_COPY_COVERAGE_MIN} "
                    f"(target shares too little of the source to be a verbatim "
                    f"copy); emitted model_code resemblance only, NOT a textual "
                    f"derivation"
                ),
            )
        )
    return result


def classify_model_code_candidate(
    *,
    source: FiProvision,
    target: FiProvision,
    corpus_version: str,
) -> Optional[dict[str, JsonValue]]:
    """Emit a MODEL-CODE resemblance edge iff the provisions resemble each other.

    A resemblance = matching normalized title skeletons. This is a discovery
    signal that the target MIGHT be modelled on the source; it is typed-UNKNOWN as
    to actual lineage. Returns ``None`` when there is no resemblance to report."""
    if source.title_skeleton() != target.title_skeleton():
        return None
    return build_model_code_kinship_edge(
        source=source,
        target=target,
        skeleton=target.title_skeleton(),
        corpus_version=corpus_version,
    )


def classify_conformance(
    *,
    citing_work_id: str,
    directive_celex: Optional[str],
    directive_surface: str,
    binding_status: str,
    corpus_version: str,
) -> list[dict[str, JsonValue]]:
    """Emit the conformance pair: the act's CLAIM + the absence-of-assessment.

    Two DISTINCT edges, never one: a ``source_claimed_transposition`` evidence
    edge (the act says it transposes the directive) and a
    ``conformance_assessment`` absence edge (we did NOT assess whether it actually
    conforms). Neither is a verified-conformance claim."""
    return [
        build_conformance_claim_edge(
            citing_work_id=citing_work_id,
            directive_celex=directive_celex,
            directive_surface=directive_surface,
            binding_status=binding_status,
            corpus_version=corpus_version,
        ),
        build_conformance_absence_edge(
            citing_work_id=citing_work_id,
            directive_celex=directive_celex,
            directive_surface=directive_surface,
            binding_status=binding_status,
            corpus_version=corpus_version,
        ),
    ]


def classify_relationships(
    *,
    textual_candidates: Sequence[tuple[FiProvision, FiProvision]] = (),
    model_code_candidates: Sequence[tuple[FiProvision, FiProvision]] = (),
    conformance_claims: Sequence[tuple[str, Optional[str], str, str]] = (),
    citations: Sequence[tuple[FiProvision, str, bool]] = (),
    corpus_version: str,
) -> DerivationEdgeSet:
    """Classify a batch of FI relationships into the four DISTINCT edge kinds.

    Inputs are kept on SEPARATE axes by the caller (the FI extractors know which
    is which — byte pairs, resemblance pairs, transposition claims, references) so
    the classifier never has to *guess* which kind a relationship is. The output
    keeps each kind in its own list — non-conflation by construction.

    * ``textual_candidates``     — ``(source, target)`` provision pairs to test on
      the byte axis (a textual edge iff byte-replay succeeds, else a resemblance).
    * ``model_code_candidates``  — ``(source, target)`` pairs to test for
      resemblance only (typed-UNKNOWN lineage).
    * ``conformance_claims``     — ``(citing_work_id, celex_or_None, surface,
      binding_status)`` from the FI EU-transposition extractor.
    * ``citations``              — ``(citing_provision, target_ref, resolved)``
      from FI reference extraction.
    """
    out = DerivationEdgeSet()

    for source, target in textual_candidates:
        sub = classify_textual(source=source, target=target, corpus_version=corpus_version)
        out.textual.extend(sub.textual)
        out.model_code.extend(sub.model_code)
        out.residuals.extend(sub.residuals)

    for source, target in model_code_candidates:
        edge = classify_model_code_candidate(
            source=source, target=target, corpus_version=corpus_version
        )
        if edge is not None:
            out.model_code.append(edge)

    for citing_work_id, celex, surface, binding_status in conformance_claims:
        out.conformance.extend(
            classify_conformance(
                citing_work_id=citing_work_id,
                directive_celex=celex,
                directive_surface=surface,
                binding_status=binding_status,
                corpus_version=corpus_version,
            )
        )

    for citing_provision, target_ref, resolved in citations:
        out.citation.append(
            build_citation_edge(
                citing_provision=citing_provision,
                target_ref=target_ref,
                target_resolved=resolved,
                corpus_version=corpus_version,
            )
        )

    return out


__all__ = [
    "DerivationKind",
    "TEXTUAL_COPY_COVERAGE_MIN",
    "DerivationResidual",
    "DerivationEdgeSet",
    "FiProvision",
    "EditOp",
    "EditScript",
    "compute_edit_script",
    "apply_edit_script",
    "build_textual_edge",
    "build_model_code_kinship_edge",
    "build_conformance_absence_edge",
    "build_conformance_claim_edge",
    "build_citation_edge",
    "RESIDUAL_TEXTUAL_NOT_BYTE_IDENTICAL",
    "RESIDUAL_TEXTUAL_COVERAGE",
    "classify_textual",
    "classify_model_code_candidate",
    "classify_conformance",
    "classify_relationships",
]
