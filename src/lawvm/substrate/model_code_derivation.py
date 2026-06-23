"""The model-code derivation ladder — proof-graded city↔city / city→ORC edges.

This is the first substrate producer that emits the STRONG side of the §25.3
authority×evidence legality matrix: a ``verified_textual_derivation`` edge on the
``legal_state`` plane with ``verification_level=delta_verified`` and
``replay_authorized=true``. Everything below it is a profile of the SAME
``lawvm.legal_relation_edge.v0`` algebra, never a special architecture (design
§25.6 three transclusions / §25.7 three adoption shapes / §25.9 Step 3).

Concrete instance (the verified target). US municipal traffic codes built on the
Walter Drane "Part Three / Chapter 33x" template are reprinted VERBATIM across
dozens of Ohio cities. As enacted law they are uncopyrightable (Georgia v.
Public.Resource.Org, 2020), so the clean byte-reproducible derivation axis is
**city ↔ city**: one city is the BASELINE WITNESS, a sibling is an ADOPTER whose
provision text is reconstructed byte-for-byte from the baseline by a trivial
delta (often only a markdown header-level shift, ``## 337.17`` vs ``### 337.17``,
the section body identical). The ``(ORC 4511.xx)`` provenance cites the ordinance
carries are a LOOSER upstream signal: the city NAMES an Ohio Revised Code section
it localizes — it does NOT reproduce the ORC text — so that axis is
``incorporates_by_reference`` (evidence plane), NEVER a verified derivation.

The ladder, per provision (and the exact matrix-legal mapping each tier earns):

* **kinship** — two city provisions share a normalized title skeleton (the same
  dotted number + the same normalized heading). A discovery-only resemblance:
  ``authority_plane=overlay``, ``verification_level=induced_similarity``,
  ``replay_authorized=false`` (§25.3 weak-evidence rule). NEVER legal-state.
* **incorporates_by_reference** — the provision body NAMES an ``(ORC 4xxx.xx)``
  section. The city text asserts the cite; it does not reproduce the ORC:
  ``authority_plane=evidence``, ``verification_level=source_asserted``,
  ``replay_authorized=false``.
* **verified_textual_derivation** — a deterministic edit script
  (:func:`compute_edit_script`) from the BASELINE provision text to the SIBLING
  provision text, REPLAYED (:func:`apply_edit_script`) to reproduce the sibling
  text BYTE-FOR-BYTE. Only then: ``authority_plane=legal_state``,
  ``verification_level=delta_verified``, ``replay_authorized=true``. The edge body
  CARRIES the edit script + its content hash and the replayed-output hash, so the
  ``delta_verified`` claim is checkable, not asserted. If replay does NOT
  reproduce the sibling exactly, the engine FALLS BACK to a kinship edge and
  records a typed residual — it NEVER fabricates a legal-state edge.

Every edge is asserted matrix-legal (``edge_authority_violation(body) is None``)
before it leaves the builder, exactly as the §14 reference bridge does. The
classifier is deterministic and fail-loud: every (baseline, sibling) provision
pair yields edges across ALL applicable tiers — successes AND residuals — never a
silent drop.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from lawvm.substrate.locus import LocusRow

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
# Provision model                                                             #
# --------------------------------------------------------------------------- #

# An Ohio Revised Code cite as it appears INSIDE municipal-ordinance body text:
# ``(ORC 4511.21)``, ``(ORC 4513.99)``, ``(ORC 959.131)``. The numeric form is
# ``<chapter>.<section>`` (chapter is 1–5 digits; section 1–3 digits). The
# parenthesised ``ORC`` label is the Walter-Drane provenance convention.
_ORC_CITE = re.compile(r"\(ORC\s+([0-9]+\.[0-9]+)\)")

# A normalized-skeleton title is the dotted section number + the heading words.
# We strip the markdown ``#`` prefix and collapse whitespace/case so two cities'
# ``## 337.17 ...`` and ``### 337.17 ...`` share a skeleton (the markdown level is
# NOT a legal-state signal — it is exactly the trivial delta the derivation tier
# reproduces).
_MD_PREFIX = re.compile(r"^#+\s*")
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Provision:
    """One addressable provision of a municipal code (baseline or sibling).

    ``address`` is the induced dotted section number (``337.17``) — the join key
    between a baseline witness and a sibling adopter. ``header`` is the raw
    section heading line (carries the markdown level, e.g. ``## 337.17 ...``);
    ``body`` is the section text. ``derivation_text`` (the bytes the derivation
    tier reproduces) is the header + body joined — so a header-level-only delta is
    a REAL, computable difference, not an invisible one.
    """

    address: str
    header: str
    body: str

    @property
    def derivation_text(self) -> str:
        """The full reproducible surface = header line + body, NFC-normalized.

        This is the byte string a ``verified_textual_derivation`` reproduces. We
        include the header so a markdown-level-only change (``##`` vs ``###``) is
        a genuine, replay-checkable delta rather than a silently-ignored one.
        """
        return nfc(f"{self.header}\n{self.body}")

    @property
    def title_skeleton(self) -> str:
        """Normalized ``<dotted-number> <heading-words>`` skeleton for kinship.

        Markdown level stripped, whitespace collapsed, case-folded. Two cities'
        same-numbered same-titled provisions share a skeleton even when their
        markdown header level and surrounding spacing differ.
        """
        stripped = _MD_PREFIX.sub("", nfc(self.header)).strip()
        return _WS.sub(" ", stripped).casefold()

    def orc_cites(self) -> tuple[str, ...]:
        """Distinct ``(ORC <chapter>.<section>)`` cites in the body, in order.

        Returns the numeric forms (``4511.21``), de-duplicated but order-stable.
        These are the adoption/provenance signal → ``incorporates_by_reference``.
        """
        seen: list[str] = []
        for m in _ORC_CITE.finditer(nfc(self.body)):
            cite = m.group(1)
            if cite not in seen:
                seen.append(cite)
        return tuple(seen)


# --------------------------------------------------------------------------- #
# Deterministic edit script + replay (the delta_verified core)                #
# --------------------------------------------------------------------------- #

# Edit-script op kinds. ``copy`` reproduces a span of the BASELINE; ``insert``
# emits literal SIBLING text absent from the baseline. (A replace/delete is
# expressed as copy-the-unchanged + insert-the-new; we never need an explicit
# delete because replay reads the baseline only through ``copy`` spans.)
_OP_COPY = "copy"
_OP_INSERT = "insert"


@dataclass(frozen=True, slots=True)
class EditOp:
    """One edit-script operation. ``copy`` carries ``[start, end)`` baseline
    indices; ``insert`` carries the literal sibling ``text``."""

    op: str
    start: int = 0
    end: int = 0
    text: str = ""

    def to_json(self) -> JsonValue:
        if self.op == _OP_COPY:
            return {"op": _OP_COPY, "start": self.start, "end": self.end}
        return {"op": _OP_INSERT, "text": self.text}


# A derivation is a VERBATIM-adoption claim: the sibling is reproduced FROM the
# baseline by a BOUNDED delta. An edit script always reproduces its target (it is
# a full diff), so "replay reproduces the sibling" alone is necessary but NOT
# sufficient — a script that COPIES nothing of the baseline (a fresh local text)
# replays fine yet is no derivation. The copy-coverage gate makes the legal-state
# claim meaningful: the sibling must be SUBSTANTIALLY the baseline's bytes.
# Below this fraction the pair is kinship + residual, never legal-state.
DERIVATION_COPY_COVERAGE_MIN = 0.80


@dataclass(frozen=True, slots=True)
class EditScript:
    """A deterministic baseline→sibling edit script + its content hash.

    The ops are produced by :func:`compute_edit_script` from
    ``difflib.SequenceMatcher`` opcodes (deterministic for a fixed input pair).
    ``edit_script_id`` content-addresses the ops together with the baseline/
    sibling text hashes, so the delta is a checkable, tamper-evident object: the
    edge body references it, and recomputing the hash detects any corruption.

    ``copy_coverage`` is the fraction of the SIBLING bytes that came from a
    ``copy`` op (verbatim from the baseline) rather than an ``insert`` — the
    measure of how much the sibling is a verbatim adoption of the baseline. A
    derivation requires it ≥ :data:`DERIVATION_COPY_COVERAGE_MIN`.
    """

    ops: tuple[EditOp, ...]
    baseline_text_hash: str
    sibling_text_hash: str
    copy_coverage: float = 1.0
    edit_script_id: str = ""

    def to_json(self) -> dict[str, JsonValue]:
        body: dict[str, JsonValue] = {
            "ops": [op.to_json() for op in self.ops],
            "baseline_text_hash": self.baseline_text_hash,
            "sibling_text_hash": self.sibling_text_hash,
        }
        return body


_DOMAIN_EDIT_SCRIPT = "model_code_edit_script"


def _text_hash(text: str) -> str:
    """Content hash of a provision text (NFC already applied upstream)."""
    return leaf_hash("model_code_text", {"text": text})


def compute_edit_script(baseline_text: str, sibling_text: str) -> EditScript:
    """Compute a DETERMINISTIC baseline→sibling edit script (copy/insert ops).

    Uses ``difflib.SequenceMatcher`` over the two strings (character-level). Its
    opcodes are deterministic for a fixed input pair; we lower them to a
    copy/insert script: ``equal`` → copy the baseline span; ``replace`` /
    ``insert`` → insert the sibling span; ``delete`` → drop the baseline span (no
    op). Replaying the script (:func:`apply_edit_script`) over the baseline MUST
    reproduce the sibling; this is asserted at the call site before any
    legal-state edge is emitted.
    """
    matcher = difflib.SequenceMatcher(a=baseline_text, b=sibling_text, autojunk=False)
    ops: list[EditOp] = []
    copied = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.append(EditOp(op=_OP_COPY, start=i1, end=i2))
            copied += i2 - i1
        elif tag in ("replace", "insert"):
            ops.append(EditOp(op=_OP_INSERT, text=sibling_text[j1:j2]))
        # ``delete`` contributes nothing to the output (baseline-only span).
    coverage = (copied / len(sibling_text)) if sibling_text else 1.0
    body: dict[str, JsonValue] = {
        "ops": [op.to_json() for op in ops],
        "baseline_text_hash": _text_hash(baseline_text),
        "sibling_text_hash": _text_hash(sibling_text),
    }
    edit_script_id = leaf_hash(_DOMAIN_EDIT_SCRIPT, body)
    return EditScript(
        ops=tuple(ops),
        baseline_text_hash=_text_hash(baseline_text),
        sibling_text_hash=_text_hash(sibling_text),
        copy_coverage=coverage,
        edit_script_id=edit_script_id,
    )


def apply_edit_script(baseline_text: str, script: EditScript) -> str:
    """REPLAY an edit script over ``baseline_text`` and return the produced text.

    Pure and deterministic: ``copy`` ops splice baseline ``[start, end)`` spans,
    ``insert`` ops emit literal text. The result is compared BYTE-FOR-BYTE to the
    sibling text by the classifier; only an exact match earns ``delta_verified``.
    A corrupted script (a tampered copy span or insert literal) reproduces the
    WRONG bytes here, so the legal-state edge is never emittable for it.
    """
    out: list[str] = []
    for op in script.ops:
        if op.op == _OP_COPY:
            out.append(baseline_text[op.start : op.end])
        elif op.op == _OP_INSERT:
            out.append(op.text)
        else:  # pragma: no cover — closed op vocabulary
            raise ValueError(f"unknown edit-script op {op.op!r}")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Ladder classification result                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DerivationResidual:
    """A typed residual — a derivation that did NOT byte-reproduce (kinship-only).

    ``kind`` is ``model_code_derivation_replay_mismatch`` (the delta did not
    reproduce the sibling) — the provision is owned as a kinship edge + this
    residual, NEVER as a fabricated legal-state edge. The detail embeds the
    offending address + the byte lengths so the diagnostic is self-evidencing.
    """

    kind: str
    address: str
    detail: str

    def to_json(self) -> dict[str, JsonValue]:
        return {"kind": self.kind, "address": self.address, "detail": nfc(self.detail)}


@dataclass(slots=True)
class DerivationResult:
    """All edges + residuals produced for one baseline↔sibling work pair."""

    edges: list[dict[str, JsonValue]] = field(default_factory=list)
    residuals: list[DerivationResidual] = field(default_factory=list)
    n_kinship: int = 0
    n_incorporates_by_reference: int = 0
    n_verified_textual_derivation: int = 0
    n_replay_mismatch: int = 0


# --------------------------------------------------------------------------- #
# Edge builders (each pinned to its exact matrix-legal posture)               #
# --------------------------------------------------------------------------- #

RESIDUAL_REPLAY_MISMATCH = "model_code_derivation_replay_mismatch"


def _assert_legal(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Guard: every edge MUST be matrix-legal before it leaves the engine."""
    reason = edge_authority_violation(body)
    assert reason is None, (
        "model_code_derivation produced a matrix-ILLEGAL edge "
        f"(authority_plane={body.get('authority_plane')!r}, "
        f"verification_level={body.get('verification_level')!r}, "
        f"replay_authorized={body.get('replay_authorized')!r}): {reason}"
    )
    return body


def _provision_ref(work_id: str, address: str) -> str:
    """A content-addressable provision ref ``provision:<work_id>#<address>``."""
    return f"provision:{work_id}#{address}"


def _orc_ref(orc_section: str) -> str:
    """A stable ref for an Ohio Revised Code section (the incorporation target)."""
    return f"us-oh-orc:section:{orc_section}"


def build_kinship_edge(
    *,
    baseline_work_id: str,
    sibling_work_id: str,
    address: str,
    skeleton: str,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """A discovery-only resemblance: ``kinship`` on the OVERLAY plane (§25.3).

    Two city provisions share a normalized title skeleton. This is NEVER
    legal-state — ``induced_similarity`` evidence on the overlay plane with
    ``replay_authorized=false``. Status ``qualified`` (a resemblance, not a pinned
    identity).
    """
    body = build_relation_edge(
        relation_kind=RelationKind.KINSHIP,
        source_ref=_provision_ref(sibling_work_id, address),
        target_set=(_provision_ref(baseline_work_id, address),),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.OVERLAY,
        verification_level=VerificationLevel.INDUCED_SIMILARITY,
        replay_authorized=False,
        status=EdgeStatus.QUALIFIED,
        effective_scope={"branch_id": "actual", "title_skeleton": skeleton},
        corpus_version=corpus_version,
    )
    return _assert_legal(body)


def build_incorporation_edge(
    *,
    sibling_work_id: str,
    address: str,
    orc_section: str,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """A provenance cite: ``incorporates_by_reference`` on the EVIDENCE plane.

    The city body NAMES ``(ORC <orc_section>)``; it does not reproduce the ORC
    text. ``source_asserted`` evidence, evidence plane, ``replay_authorized=false``,
    status ``resolved`` (the cite target is a named, identifiable ORC section).
    """
    body = build_relation_edge(
        relation_kind=RelationKind.INCORPORATES_BY_REFERENCE,
        source_ref=_provision_ref(sibling_work_id, address),
        target_set=(_orc_ref(orc_section),),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.EVIDENCE,
        verification_level=VerificationLevel.SOURCE_ASSERTED,
        replay_authorized=False,
        status=EdgeStatus.RESOLVED,
        effective_scope={"branch_id": "actual", "orc_section": orc_section},
        corpus_version=corpus_version,
    )
    return _assert_legal(body)


def build_verified_derivation_edge(
    *,
    baseline_work_id: str,
    sibling_work_id: str,
    address: str,
    script: EditScript,
    corpus_version: str,
) -> dict[str, JsonValue]:
    """A byte-reproducible derivation: ``verified_textual_derivation``, LEGAL_STATE.

    The STRONG side of the matrix — ``delta_verified`` evidence, ``legal_state``
    plane, ``replay_authorized=true``. The caller MUST have already replayed the
    ``script`` over the baseline text and confirmed it reproduces the sibling text
    byte-for-byte. The edge body carries the ``edit_script_id`` + the baseline/
    sibling text hashes (in ``effective_scope``), so the ``delta_verified`` claim
    is checkable: a tampered delta changes the script hash / fails replay and can
    never re-earn this posture.
    """
    authorization = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="replay_authorized",
        authorization_rule_id="model_code_verified_textual_derivation_delta_verified",
        owner_phase="substrate.model_code_derivation",
        strict_disposition="record",
        safe_default="block_without_delta_verified_replay",
        forbidden_shortcuts=(
            "raw_similarity_as_replay_authority",
            "source_asserted_orc_citation_as_textual_derivation",
            "author_set_projection_replay_authority_without_execution_authorization",
        ),
        detail={
            "edit_script_id": script.edit_script_id,
            "baseline_text_hash": script.baseline_text_hash,
            "sibling_text_hash": script.sibling_text_hash,
            "copy_coverage": repr(script.copy_coverage),
        },
    )
    body = build_relation_edge(
        relation_kind=RelationKind.VERIFIED_TEXTUAL_DERIVATION,
        source_ref=_provision_ref(sibling_work_id, address),
        target_set=(_provision_ref(baseline_work_id, address),),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,
        verification_level=VerificationLevel.DELTA_VERIFIED,
        replay_authorized=authorization.replay_authorized,
        status=EdgeStatus.RESOLVED,
        effective_scope={
            "branch_id": "actual",
            "edit_script_id": script.edit_script_id,
            "baseline_text_hash": script.baseline_text_hash,
            "sibling_text_hash": script.sibling_text_hash,
            "replay_reproduces_sibling": True,
            # Carried as an exact string (floats are forbidden in canonical JSON).
            "copy_coverage": repr(script.copy_coverage),
        },
        corpus_version=corpus_version,
        evidence_refs=(script.edit_script_id,),
    )
    return _assert_legal(body)


# --------------------------------------------------------------------------- #
# The ladder classifier                                                       #
# --------------------------------------------------------------------------- #


def _index_by_address(provisions: Sequence[Provision]) -> dict[str, Provision]:
    """Index provisions by their dotted address; first occurrence wins.

    A duplicate address within ONE work is a baseline/sibling pathology the LOCUS
    adapter already residualizes upstream; here we keep the first occurrence so
    the join key is unambiguous.
    """
    out: dict[str, Provision] = {}
    for p in provisions:
        out.setdefault(p.address, p)
    return out


def derive_model_code_edges(
    *,
    baseline_work_id: str,
    sibling_work_id: str,
    baseline_provisions: Sequence[Provision],
    sibling_provisions: Sequence[Provision],
    corpus_version: str,
) -> DerivationResult:
    """Run the derivation ladder for one baseline witness ↔ sibling adopter pair.

    For every provision the sibling shares (by dotted address) with the baseline:

    1. emit a **kinship** edge iff the two share a normalized title skeleton
       (discovery resemblance — overlay/induced_similarity);
    2. emit an **incorporates_by_reference** edge per distinct ``(ORC x.y)`` cite
       in the SIBLING body (evidence/source_asserted) — the localized provenance;
    3. compute the city↔city delta + REPLAY it; iff it reproduces the sibling text
       byte-for-byte, emit a **verified_textual_derivation** edge (legal_state/
       delta_verified/replay_authorized=true); else emit NOTHING legal-state, keep
       the kinship edge, and record a typed ``replay_mismatch`` residual.

    Deterministic + fail-loud: provisions are visited in sorted-address order;
    every shared provision yields edges across all applicable tiers; nothing is
    dropped silently. Sibling-only / baseline-only provisions are NOT forced into
    a derivation (no baseline witness / no adopter to derive) — they simply do not
    produce a city↔city edge here.
    """
    result = DerivationResult()
    baseline_idx = _index_by_address(baseline_provisions)
    sibling_idx = _index_by_address(sibling_provisions)

    for address in sorted(sibling_idx.keys() & baseline_idx.keys()):
        baseline = baseline_idx[address]
        sibling = sibling_idx[address]

        # Tier 1 — kinship (shared normalized title skeleton).
        if baseline.title_skeleton == sibling.title_skeleton:
            result.edges.append(
                build_kinship_edge(
                    baseline_work_id=baseline_work_id,
                    sibling_work_id=sibling_work_id,
                    address=address,
                    skeleton=sibling.title_skeleton,
                    corpus_version=corpus_version,
                )
            )
            result.n_kinship += 1

        # Tier 2 — incorporates_by_reference (per distinct ORC cite in sibling).
        for orc_section in sibling.orc_cites():
            result.edges.append(
                build_incorporation_edge(
                    sibling_work_id=sibling_work_id,
                    address=address,
                    orc_section=orc_section,
                    corpus_version=corpus_version,
                )
            )
            result.n_incorporates_by_reference += 1

        # Tier 3 — verified_textual_derivation (compute delta + REPLAY).
        baseline_text = baseline.derivation_text
        sibling_text = sibling.derivation_text
        script = compute_edit_script(baseline_text, sibling_text)
        replayed = apply_edit_script(baseline_text, script)
        replay_ok = replayed == sibling_text
        verbatim_enough = script.copy_coverage >= DERIVATION_COPY_COVERAGE_MIN
        if replay_ok and verbatim_enough:
            # A genuine verbatim adoption: replay reproduces the sibling AND the
            # sibling is SUBSTANTIALLY the baseline's bytes (a bounded delta).
            result.edges.append(
                build_verified_derivation_edge(
                    baseline_work_id=baseline_work_id,
                    sibling_work_id=sibling_work_id,
                    address=address,
                    script=script,
                    corpus_version=corpus_version,
                )
            )
            result.n_verified_textual_derivation += 1
        else:
            # NOT a derivation — either replay did not reproduce the sibling (a
            # tampered/invalid delta) or the sibling shares too little of the
            # baseline to be a verbatim adoption. DO NOT fabricate legal-state;
            # the kinship edge (if any) owns the resemblance. The residual is
            # typed + self-evidencing (embeds the offending address + measure).
            if not replay_ok:
                reason = (
                    f"edit-script replay reproduced {len(replayed)} bytes, sibling "
                    f"is {len(sibling_text)} bytes (not byte-identical)"
                )
            else:
                reason = (
                    f"copy-coverage {script.copy_coverage:.3f} < "
                    f"{DERIVATION_COPY_COVERAGE_MIN} (sibling shares too little of "
                    f"the baseline to be a verbatim adoption)"
                )
            result.residuals.append(
                DerivationResidual(
                    kind=RESIDUAL_REPLAY_MISMATCH,
                    address=address,
                    detail=(
                        f"provision {address!r}: {reason}; emitted kinship only, "
                        f"NOT verified_textual_derivation"
                    ),
                )
            )
            result.n_replay_mismatch += 1

    return result


# --------------------------------------------------------------------------- #
# LOCUS bridge — induce provisions from a municipal work's parquet rows        #
# --------------------------------------------------------------------------- #


def provisions_from_locus_rows(rows: Sequence["LocusRow"]) -> list[Provision]:
    """Induce a list of :class:`Provision` from one work's LOCUS rows.

    Reuses the LOCUS adapter's document-ordered :class:`AddressInducer` (the same
    induction the snapshot pack uses) so a provision's ``address`` is the induced
    dotted skeleton — the join key shared with the sibling work. Rows whose header
    induces no address, or whose induced leaf is a non-dotted container path, are
    skipped here (they cannot be a city↔city derivation join key); the snapshot
    pack already residualizes un-inducible headers, so nothing is lost — this is
    the derivation VIEW, not the totality producer. The first occurrence of a
    dotted address wins (a within-work duplicate is a LOCUS-side residual).
    """
    from lawvm.substrate.locus import AddressInducer

    inducer = AddressInducer()
    out: list[Provision] = []
    seen: set[str] = set()
    for row in rows:
        induced = inducer.induce(row.header)
        if induced is None:
            continue
        # The join key is the authoritative DOTTED number (``337.17``). A
        # word-container / sequential-stack induction is not a stable cross-city
        # section key, so we only derive over exact-dotted leaves.
        if induced.method != "exact_dotted" or "." not in induced.dotted:
            continue
        address = induced.dotted
        if address in seen:
            continue
        seen.add(address)
        out.append(
            Provision(
                address=address,
                header=row.header or "",
                body=row.content or "",
            )
        )
    return out


@dataclass(slots=True)
class ModelCodeCorpusResult:
    """Summary of a model-code corpus pack (the derivation edges + the pack)."""

    corpus_pack_dir: str
    pack_id: str
    baseline_work_id: str
    sibling_work_ids: tuple[str, ...]
    n_edges: int
    n_kinship: int
    n_incorporates_by_reference: int
    n_verified_textual_derivation: int
    n_replay_mismatch: int
    residual_addresses: tuple[str, ...]


def build_model_code_corpus_pack(
    *,
    baseline_pack_dir: str,
    baseline_provisions: Sequence[Provision],
    siblings: "Sequence[tuple[str, str, Sequence[Provision]]]",
    out_dir: str,
    corpus_version: str | None = None,
) -> ModelCodeCorpusResult:
    """Pack the baseline + siblings into a corpus whose ``edges/`` carries the
    derivation-ladder edges (§25.9 Step 3 e2e).

    Each ``siblings`` entry is ``(sibling_pack_dir, sibling_work_id,
    sibling_provisions)``. The function runs :func:`derive_model_code_edges` for
    every (baseline, sibling) pair, collects the resulting
    ``lawvm.legal_relation_edge.v0`` bodies, and emits them into the corpus pack's
    ``edges/<corpus_version>/edges.jsonl`` via :func:`corpus.build_corpus_pack`
    (additive — the edges layer is optional, so the relation-edge schema rides as
    an unsupported-but-VALID layer while L0.8 validates each edge's authority).

    The shared ``base/`` content-leaf store deduplicates the verbatim provision
    text across cities for free (identical leaf text → identical ``object_hash``).
    """
    from pathlib import Path

    from lawvm.substrate.corpus import build_corpus_pack

    member_pack_dirs: dict[str, str | Path] = {}
    baseline_work_id = _work_id_of_pack(baseline_pack_dir)
    member_pack_dirs[baseline_work_id] = baseline_pack_dir

    cv = corpus_version or "us-oh-model-code:corpus"
    all_edges: list[dict[str, JsonValue]] = []
    sibling_work_ids: list[str] = []
    totals = DerivationResult()
    residual_addresses: list[str] = []

    for sib_pack_dir, sib_work_id, sib_provisions in siblings:
        member_pack_dirs[sib_work_id] = sib_pack_dir
        sibling_work_ids.append(sib_work_id)
        res = derive_model_code_edges(
            baseline_work_id=baseline_work_id,
            sibling_work_id=sib_work_id,
            baseline_provisions=baseline_provisions,
            sibling_provisions=sib_provisions,
            corpus_version=cv,
        )
        all_edges.extend(res.edges)
        totals.n_kinship += res.n_kinship
        totals.n_incorporates_by_reference += res.n_incorporates_by_reference
        totals.n_verified_textual_derivation += res.n_verified_textual_derivation
        totals.n_replay_mismatch += res.n_replay_mismatch
        residual_addresses.extend(r.address for r in res.residuals)

    corpus = build_corpus_pack(
        member_pack_dirs=member_pack_dirs,
        out_dir=out_dir,
        resolutions=all_edges,
        corpus_version=cv,
    )
    return ModelCodeCorpusResult(
        corpus_pack_dir=corpus.out_dir,
        pack_id=corpus.pack_id,
        baseline_work_id=baseline_work_id,
        sibling_work_ids=tuple(sibling_work_ids),
        n_edges=corpus.n_edges,
        n_kinship=totals.n_kinship,
        n_incorporates_by_reference=totals.n_incorporates_by_reference,
        n_verified_textual_derivation=totals.n_verified_textual_derivation,
        n_replay_mismatch=totals.n_replay_mismatch,
        residual_addresses=tuple(sorted(set(residual_addresses))),
    )


def _work_id_of_pack(pack_dir: str) -> str:
    """Read the single ``work_id`` a snapshot member pack declares in its manifest."""
    import json
    from pathlib import Path

    mf = json.loads((Path(pack_dir) / "manifest.json").read_text(encoding="utf-8"))
    body = mf.get("object", mf)
    work_ids = body.get("work_ids", ())
    if not work_ids:
        raise ValueError(f"member pack {pack_dir} declares no work_ids")
    return str(work_ids[0])


__all__ = [
    "Provision",
    "EditOp",
    "EditScript",
    "DerivationResidual",
    "DerivationResult",
    "RESIDUAL_REPLAY_MISMATCH",
    "compute_edit_script",
    "apply_edit_script",
    "build_kinship_edge",
    "build_incorporation_edge",
    "build_verified_derivation_edge",
    "derive_model_code_edges",
    "provisions_from_locus_rows",
    "ModelCodeCorpusResult",
    "build_model_code_corpus_pack",
]
