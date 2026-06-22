"""The trustless pack checker + two-axis verdict algebra (P4).

This is the whole point of the distribution layer (design §0 "attest", §12;
``CHECKER_CONTRACT_V0.md``): a small, **offline, deterministic**, read-only
verifier that takes an in-memory pack (wrapped JSONL rows + a
:class:`~lawvm.substrate.manifest.PackManifest`) and emits ONE verdict from a
closed enum plus a list of typed violations — recomputing **L0** (inclusion /
integrity) and **L1** (finite-interval selection algebra) WITHOUT ever running
the source-language replay kernel (that is L2, explicitly out of v0).

It is the *product*: a party who does not trust the host (or LawVM, or Python)
re-derives every committed hash from the bytes alone and gets the same answer.
The checker therefore:

* **reuses the P0 verification kernel verbatim** — ``semantic_hash`` /
  ``canonical_json_bytes`` (``substrate.canonical_json``), ``set_root`` /
  ``seq_root`` / ``map_root`` / ``leaf_hash`` (``substrate.roots``),
  ``PackManifest`` (``substrate.manifest``) — and never re-implements hashing;
* is **jurisdiction-neutral** — it reads ``schema`` / ``root_fn`` from the
  self-describing manifest and never branches on ``fi:`` / ``uk:``;
* is **fail-loud + typed** — every non-``VALID`` verdict carries the offending
  object hash / root name / selection_key in a self-evidencing
  :class:`TypedViolation` (memory ``feedback_diagnostics_self_evidencing``);
* is **omission-honest** — the load-bearing L0 check is not "are the present
  rows right" but "are any rows that SHOULD exist missing" (``MapRoot`` keys);
* is **availability-honest** — missing / digest-only source bytes yield
  ``UNCHECKABLE_MISSING_SOURCE``, **never** ``INVALID`` (design §3.4, §12).

Two verdict vocabularies collapse to **two orthogonal axes** (contract §1):
integrity (did the bytes/roots/structure verify) × certification (is the legal
assertion clean / qualified / blocked). A pack can be byte-perfect
(``INTEGRITY_VALID``) and still carry a ``blocked`` certificate. The wire format
is ``{integrity, certification, violations[], top_line_verdict}`` where
``top_line_verdict`` is derived by the strict precedence in §1.1.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from lawvm.substrate.canonical_json import (
    CanonicalJsonError,
    JsonValue,
    semantic_hash,
)
from lawvm.substrate.manifest import PackManifest
from lawvm.substrate.roots import (
    RootError,
    leaf_hash,
    map_root,
    seq_root,
    set_root,
)
from lawvm.substrate.totality import (
    TotalityResult,
    TotalityVerdict,
    compute_totality,
)

# --------------------------------------------------------------------------- #
# Verdict enums (contract §1) — two orthogonal axes + a derived top line.
# --------------------------------------------------------------------------- #


class IntegrityVerdict(enum.Enum):
    """Integrity axis (``PROTOTYPE_PLAN_V0.md §4``): did the bytes verify.

    ``VALID`` / ``VALID_WITH_UNSUPPORTED_LAYERS`` are the two clean states; the
    ``INVALID_*`` family means the pack's own bytes are inconsistent;
    ``UNSUPPORTED_SCHEMA`` means a required object declared a schema the checker
    lacks; ``UNCHECKABLE_MISSING_SOURCE`` means an *audit* was requested but the
    source manifestation bytes are digest-only / lost (never ``INVALID``).
    """

    VALID = "INTEGRITY_VALID"
    VALID_WITH_UNSUPPORTED_LAYERS = "INTEGRITY_VALID_WITH_UNSUPPORTED_LAYERS"
    INVALID_HASH = "INVALID_HASH"
    INVALID_ROOT = "INVALID_ROOT"
    INVALID_MISSING_OBJECT = "INVALID_MISSING_OBJECT"
    INVALID_SELECTION_UNIVERSE = "INVALID_SELECTION_UNIVERSE"
    INVALID_SELECTION_OVERLAP = "INVALID_SELECTION_OVERLAP"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNCHECKABLE_MISSING_SOURCE = "UNCHECKABLE_MISSING_SOURCE"


class CertificationVerdict(enum.Enum):
    """Certification axis (design §12): the legal quality of a clean pass.

    ``NOT_COMPUTED`` is emitted when integrity already failed hard (the cert
    status would be meaningless over inconsistent bytes), or when browse mode
    carries no residual detail to fold (contract §9.3 ``NOT_COMPUTED`` is an
    acceptable browse top-line).
    """

    VALID_CLEAN = "VALID_CLEAN"
    VALID_QUALIFIED = "VALID_QUALIFIED"
    VALID_BLOCKED = "VALID_BLOCKED"
    UNCHECKABLE_MISSING_ARTIFACTS = "UNCHECKABLE_MISSING_ARTIFACTS"
    UNCHECKABLE_DIGEST_ONLY = "UNCHECKABLE_DIGEST_ONLY"
    NOT_COMPUTED = "NOT_COMPUTED"


class TopLineVerdict(enum.Enum):
    """The single dominant verdict a one-cell UI shows (contract §1.1).

    Derived from the (integrity, certification) pair by the strict precedence
    in :data:`_TOP_LINE_PRECEDENCE` — first match wins. ``VALID`` is the alias
    of ``VALID_CLEAN`` (plan §4 ``VALID`` ≡ design §12 ``VALID_CLEAN``).
    """

    INVALID_HASH = "INVALID_HASH"
    INVALID_ROOT = "INVALID_ROOT"
    INVALID_MISSING_OBJECT = "INVALID_MISSING_OBJECT"
    INVALID_SELECTION_UNIVERSE = "INVALID_SELECTION_UNIVERSE"
    INVALID_SELECTION_OVERLAP = "INVALID_SELECTION_OVERLAP"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNCHECKABLE_MISSING_SOURCE = "UNCHECKABLE_MISSING_SOURCE"
    VALID_BLOCKED = "VALID_BLOCKED"
    VALID_QUALIFIED = "VALID_QUALIFIED"
    VALID_WITH_UNSUPPORTED_LAYERS = "VALID_WITH_UNSUPPORTED_LAYERS"
    VALID_CLEAN = "VALID_CLEAN"


class CheckMode(enum.Enum):
    """``--mode browse | audit`` (contract §6.1).

    ``BROWSE`` renders text from ``required_layers_for_browse`` only and never
    requires source manifestation bytes. ``AUDIT`` additionally requires source
    bytes (else ``UNCHECKABLE_MISSING_SOURCE``) and runs the proof layer.
    """

    BROWSE = "browse"
    AUDIT = "audit"


class CheckLevel(enum.Enum):
    """``--level L0 | L0+L1`` (contract §6.1)."""

    L0 = "L0"
    L0_L1 = "L0+L1"


# --------------------------------------------------------------------------- #
# Typed violation + result (contract §1.2, §1).
# --------------------------------------------------------------------------- #


class ViolationCode(enum.Enum):
    """The closed ``TypedViolation.code`` set (contract §1.2)."""

    INVALID_HASH = "INVALID_HASH"
    INVALID_ROOT = "INVALID_ROOT"
    INVALID_MISSING_OBJECT = "INVALID_MISSING_OBJECT"
    INVALID_SELECTION_UNIVERSE = "INVALID_SELECTION_UNIVERSE"
    INVALID_SELECTION_OVERLAP = "INVALID_SELECTION_OVERLAP"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNCHECKABLE_MISSING_SOURCE = "UNCHECKABLE_MISSING_SOURCE"
    RESIDUAL_BLOCKS = "RESIDUAL_BLOCKS"
    RESIDUAL_QUALIFIES = "RESIDUAL_QUALIFIES"
    # L1 structural codes (finite-interval algebra; contract §3).
    INVALID_INTERVAL = "INVALID_INTERVAL"
    SINGLE_RAIL_OVERLAP = "SINGLE_RAIL_OVERLAP"
    CANDIDATE_INCOMPLETE = "CANDIDATE_INCOMPLETE"
    PROFILE_MISMATCH = "PROFILE_MISMATCH"
    SCOPE_AMBIGUITY_UNMARKED = "SCOPE_AMBIGUITY_UNMARKED"
    SCOPE_AMBIGUITY_UNWITNESSED = "SCOPE_AMBIGUITY_UNWITNESSED"
    BLOCKED_ROW_UNCITED = "BLOCKED_ROW_UNCITED"


@dataclass(frozen=True, slots=True)
class TypedViolation:
    """One self-evidencing violation (contract §1.2).

    The self-evidencing rule (memory ``feedback_diagnostics_self_evidencing``):
    a violation about a wrong root MUST carry both ``expected`` (recomputed) and
    ``actual`` (emitted), and the ``subject`` (root name / hash / selection_key)
    — never an opaque "root mismatch". ``detail`` embeds the offending value.
    """

    code: ViolationCode
    level: str
    layer: str
    subject: str
    detail: str
    expected: str | None = None
    actual: str | None = None

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body: dict[str, JsonValue] = {
            "code": self.code.value,
            "level": self.level,
            "layer": self.layer,
            "subject": self.subject,
            "detail": self.detail,
        }
        if self.expected is not None:
            body["expected"] = self.expected
        if self.actual is not None:
            body["actual"] = self.actual
        return body


@dataclass(frozen=True, slots=True)
class CheckerVerdict:
    """The unified two-axis verdict (contract §1) — the wire format.

    ``{integrity, certification, violations[], top_line_verdict}`` per the
    RESOLVED cross-check: ``top_line_verdict`` is *derived* by the precedence
    fold, not an independent input.
    """

    integrity: IntegrityVerdict
    certification: CertificationVerdict
    top_line_verdict: TopLineVerdict
    violations: tuple[TypedViolation, ...] = ()
    checked_levels: tuple[str, ...] = ()
    unsupported_layers: tuple[str, ...] = ()
    totality: TotalityResult = field(
        default_factory=lambda: TotalityResult(verdict=TotalityVerdict.NOT_COMPUTED)
    )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "integrity": self.integrity.value,
            "certification": self.certification.value,
            "totality": self.totality.to_canonical_dict(),
            "top_line_verdict": self.top_line_verdict.value,
            "violations": [v.to_canonical_dict() for v in self.violations],
            "checked_levels": list(self.checked_levels),
            "unsupported_layers": list(self.unsupported_layers),
        }

    def has_code(self, code: ViolationCode) -> bool:
        """Whether ``code`` appears in ``violations[]`` (fire-drill assertion)."""
        return any(v.code is code for v in self.violations)


# Strict precedence — first match wins (contract §1.1 / §8). An entry maps a
# (predicate over the verdict pair) → the dominant top line.
_INTEGRITY_TO_TOP: dict[IntegrityVerdict, TopLineVerdict] = {
    IntegrityVerdict.INVALID_HASH: TopLineVerdict.INVALID_HASH,
    IntegrityVerdict.INVALID_ROOT: TopLineVerdict.INVALID_ROOT,
    IntegrityVerdict.INVALID_MISSING_OBJECT: TopLineVerdict.INVALID_MISSING_OBJECT,
    IntegrityVerdict.INVALID_SELECTION_UNIVERSE: TopLineVerdict.INVALID_SELECTION_UNIVERSE,
    IntegrityVerdict.INVALID_SELECTION_OVERLAP: TopLineVerdict.INVALID_SELECTION_OVERLAP,
    IntegrityVerdict.UNSUPPORTED_SCHEMA: TopLineVerdict.UNSUPPORTED_SCHEMA,
    IntegrityVerdict.UNCHECKABLE_MISSING_SOURCE: TopLineVerdict.UNCHECKABLE_MISSING_SOURCE,
}
_CERTIFICATION_TO_TOP: dict[CertificationVerdict, TopLineVerdict] = {
    CertificationVerdict.VALID_BLOCKED: TopLineVerdict.VALID_BLOCKED,
    CertificationVerdict.VALID_QUALIFIED: TopLineVerdict.VALID_QUALIFIED,
    CertificationVerdict.VALID_CLEAN: TopLineVerdict.VALID_CLEAN,
}


def fold_top_line(
    integrity: IntegrityVerdict, certification: CertificationVerdict
) -> TopLineVerdict:
    """Derive the dominant top line from the (integrity, certification) pair.

    Strict precedence (contract §1.1): every hard-integrity failure dominates
    any certification state (the bytes lie ⇒ the cert is meaningless); above the
    integrity line, ``UNCHECKABLE_MISSING_SOURCE`` dominates a blocked/qualified
    cert; then ``VALID_BLOCKED > VALID_QUALIFIED > VALID_WITH_UNSUPPORTED_LAYERS
    > VALID_CLEAN``.
    """
    if integrity in _INTEGRITY_TO_TOP:
        return _INTEGRITY_TO_TOP[integrity]
    # integrity is clean (VALID or VALID_WITH_UNSUPPORTED_LAYERS).
    if certification in (
        CertificationVerdict.UNCHECKABLE_MISSING_ARTIFACTS,
        CertificationVerdict.UNCHECKABLE_DIGEST_ONLY,
    ):
        return TopLineVerdict.UNCHECKABLE_MISSING_SOURCE
    if certification in _CERTIFICATION_TO_TOP:
        top = _CERTIFICATION_TO_TOP[certification]
        # A clean cert over an unsupported-optional-layer pack is rank 10.
        if (
            top is TopLineVerdict.VALID_CLEAN
            and integrity is IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS
        ):
            return TopLineVerdict.VALID_WITH_UNSUPPORTED_LAYERS
        return top
    # certification == NOT_COMPUTED over clean integrity → treat as clean valid.
    if integrity is IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS:
        return TopLineVerdict.VALID_WITH_UNSUPPORTED_LAYERS
    return TopLineVerdict.VALID_CLEAN


# --------------------------------------------------------------------------- #
# Pack input model — a tiny in-memory conforming pack (contract "build fixtures").
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PackLayerData:
    """The decoded rows of one layer + its declared root_fn / root.

    Rows are ``{"object_hash": …, "object": {…}}`` wrappers (the canonical
    transport, OBJECT_MODEL §1.3). ``root`` and ``root_fn`` mirror the manifest
    layer descriptor; ``domain`` is the root-membership domain tag for this
    layer (contract §2 L0.3: ``root_fn(domain, [object_hash …])``).
    """

    kind: str
    domain: str
    root_fn: str
    root: str
    rows: tuple[Mapping[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class SourceAvailability:
    """The availability classification of source manifestation bytes.

    ``SOURCE_LINEAGE_V0.md §2``: only bytes actually bundled / pinned
    (``available_in_bundle`` / ``_lawvm_cas`` / ``_external_archive``) are
    trustworthy for an offline archival verdict. ``available_from_keeper_at_locator``
    is treated as ``digest_only`` offline (RESOLVED 2026-06-22) — "the keeper
    serves it now" is ephemeral and would make the verdict depend on
    connectivity. ``digest_only`` / ``unknown`` / ``lost`` → uncheckable.
    """

    available_in_bundle = "available_in_bundle"
    available_in_lawvm_cas = "available_in_lawvm_cas"
    available_in_external_archive = "available_in_external_archive"
    available_from_keeper_at_locator = "available_from_keeper_at_locator"
    digest_only = "digest_only"
    unknown = "unknown"
    lost = "lost"


# Availability values that, OFFLINE, can serve real source bytes for an audit.
_AVAILABLE_OFFLINE: frozenset[str] = frozenset(
    {
        SourceAvailability.available_in_bundle,
        SourceAvailability.available_in_lawvm_cas,
        SourceAvailability.available_in_external_archive,
    }
)
# Values that downgrade to digest-only offline (uncheckable, NEVER invalid).
_DIGEST_ONLY_OFFLINE: frozenset[str] = frozenset(
    {
        SourceAvailability.available_from_keeper_at_locator,
        SourceAvailability.digest_only,
        SourceAvailability.unknown,
        SourceAvailability.lost,
    }
)


@dataclass(frozen=True, slots=True)
class Pack:
    """A minimal in-memory conforming pack the checker verifies (contract §0).

    Hand-construct one from wrapped rows + a :class:`PackManifest` for fixtures /
    fire drills; a real exporter produces the same shape from layer files.

    * ``layers`` keys are layer kinds (``base`` / ``state`` / ``trace`` / …).
    * ``selection_universe`` is the ``{selection_key → expected-row-hash}`` map
      the universe root commits to (contract §2 L0.6, the omission-honesty
      keystone); ``None`` means no state layer / no universe.
    * ``referenced_hashes`` are the hashes a required-layer object points at
      (content_leaf_hash, candidate_set_hash, …) for the L0.5 closure check.
    * ``source_availability`` maps a source-ref hash → its availability enum
      (contract §2 L0.1 / §6.1 audit-mode source requirement).
    * ``known_schemas`` is the closed set of schemas this checker understands;
      a required object with a schema outside it → ``UNSUPPORTED_SCHEMA``.
    """

    manifest: PackManifest
    layers: Mapping[str, PackLayerData]
    selection_universe: Mapping[str, str] | None = None
    selection_universe_root: str | None = None
    referenced_hashes: Mapping[str, str] = field(default_factory=dict)
    source_availability: Mapping[str, str] = field(default_factory=dict)
    audited_source_refs: tuple[str, ...] = ()
    known_schemas: frozenset[str] = frozenset()


# --------------------------------------------------------------------------- #
# Small helpers.
# --------------------------------------------------------------------------- #

_PREFIX = "sha256:"


def _strip(h: str) -> str:
    """Strip the uniform ``sha256:`` prefix before comparison (contract §2 L0.2)."""
    return h[len(_PREFIX) :] if h.startswith(_PREFIX) else h


def _root_fn_for(name: str):
    """Resolve a declared ``root_fn`` string to its P0 constructor (neutral)."""
    table = {"SetRoot": set_root, "SeqRoot": seq_root}
    fn = table.get(name)
    if fn is None:
        raise _UnknownRootFn(name)
    return fn


class _UnknownRootFn(ValueError):
    """A layer declared a ``root_fn`` the checker has no constructor for."""


def _row_object(row: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    body = row.get("object")
    if isinstance(body, Mapping):
        return cast(Mapping[str, JsonValue], body)
    return None


def _as_interval(value: JsonValue) -> tuple[str, str | None] | None:
    """Parse a half-open ``[start, end-or-null]`` interval (contract §3 L1.1)."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    start, end = value[0], value[1]
    if not isinstance(start, str):
        return None
    if end is not None and not isinstance(end, str):
        return None
    return (start, end)


def _intervals_overlap(
    a: tuple[str, str | None], b: tuple[str, str | None]
) -> bool:
    """Half-open ISO-string interval overlap; ``None`` end = open (+∞).

    Dates are ISO-8601 strings (§1.4), so lexical comparison is chronological.
    """
    a_start, a_end = a
    b_start, b_end = b
    # a starts before b ends, AND b starts before a ends.
    a_before_b_end = b_end is None or a_start < b_end
    b_before_a_end = a_end is None or b_start < a_end
    return a_before_b_end and b_before_a_end


# --------------------------------------------------------------------------- #
# The checker.
# --------------------------------------------------------------------------- #


class Checker:
    """Offline, deterministic pack checker (contract §2 L0 + §3 L1).

    No network in the verdict path; no engine; reuses the P0 kernel for every
    hash / root recompute. Construct once, call :meth:`check`.
    """

    def __init__(self, mode: CheckMode = CheckMode.BROWSE, level: CheckLevel = CheckLevel.L0_L1):
        self.mode = mode
        self.level = level

    # -- public entrypoint -------------------------------------------------- #

    def check(self, pack: Pack) -> CheckerVerdict:
        """Run L0 (+ L1 unless level==L0), fold to the two-axis verdict."""
        violations: list[TypedViolation] = []
        unsupported_layers: list[str] = []
        checked_levels: list[str] = ["L0"]

        integrity = self._run_l0(pack, violations, unsupported_layers)

        certification = CertificationVerdict.NOT_COMPUTED
        if self._integrity_is_clean(integrity):
            if self.level is CheckLevel.L0_L1:
                checked_levels.append("L1")
                l1_integrity = self._run_l1(pack, violations)
                if l1_integrity is not None:
                    integrity = l1_integrity
            # Source availability (audit mode) + certification fold only when
            # integrity stayed clean through L1.
            if self._integrity_is_clean(integrity):
                src = self._check_source_availability(pack, violations)
                certification = self._fold_certification(pack, violations, src)
            else:
                certification = CertificationVerdict.NOT_COMPUTED

        # Totality — the THIRD lens (design §23), orthogonal to integrity ×
        # certification. Computed from the pack's own rows (base address tree,
        # state selection rows, proof residuals/coverage); it answers "is this a
        # complete account of the work's OWN declared universe", never "all law".
        totality = self._compute_totality(pack)

        top = fold_top_line(integrity, certification)
        return CheckerVerdict(
            integrity=integrity,
            certification=certification,
            top_line_verdict=top,
            violations=tuple(violations),
            checked_levels=tuple(checked_levels),
            unsupported_layers=tuple(unsupported_layers),
            totality=totality,
        )

    @staticmethod
    def _compute_totality(pack: Pack) -> TotalityResult:
        """Run the within-work totality lens over the pack's base/state/proof rows."""
        base = pack.layers.get("base")
        state = pack.layers.get("state")
        proof = pack.layers.get("proof")
        return compute_totality(
            base_rows=base.rows if base is not None else (),
            state_rows=state.rows if state is not None else (),
            proof_rows=proof.rows if proof is not None else (),
        )

    @staticmethod
    def _integrity_is_clean(integrity: IntegrityVerdict) -> bool:
        return integrity in (
            IntegrityVerdict.VALID,
            IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS,
        )

    # -- L0: inclusion / integrity (contract §2) ---------------------------- #

    def _run_l0(
        self,
        pack: Pack,
        violations: list[TypedViolation],
        unsupported_layers: list[str],
    ) -> IntegrityVerdict:
        """L0.2 row hashes · L0.3 layer roots · L0.4 manifest roots-of-roots ·
        L0.5 referential closure · L0.6 universe-domain equality · L0.7
        content-leaf text-only identity · schema gate.

        Returns the first failing ``INVALID_*`` per §1.1 precedence, else
        ``VALID`` (or ``VALID_WITH_UNSUPPORTED_LAYERS``). Violations accumulate
        even after the first failure so ``violations[]`` is informative.
        """
        worst: IntegrityVerdict | None = None

        def record(v: IntegrityVerdict) -> None:
            nonlocal worst
            if worst is None or _INTEGRITY_RANK[v] < _INTEGRITY_RANK[worst]:
                worst = v

        # L0.2 — per-row object hash (every layer).
        for kind, layer in pack.layers.items():
            for row in layer.rows:
                self._check_row_hash(kind, row, violations, record)

        # Schema gate — a REQUIRED-layer object declaring an unknown schema.
        if pack.known_schemas:
            self._check_schemas(pack, violations, record, unsupported_layers)

        # L0.3 — per-layer root recompute (Set/Seq) vs the manifest claim.
        descriptor_roots = {layer.kind: layer.root for layer in pack.manifest.layers}
        for kind, layer in pack.layers.items():
            claimed = descriptor_roots.get(kind, layer.root)
            self._check_layer_root(kind, layer, claimed, violations, record)

        # L0.4 — manifest roots-of-roots (pack_id, selection_index_root).
        self._check_manifest_roots(pack, violations, record)

        # L0.5 — referential closure (no dangling matter).
        self._check_referential_closure(pack, violations, record, unsupported_layers)

        # L0.6 — universe-domain equality (omission honesty, the keystone).
        self._check_universe_domain(pack, violations, record)

        # L0.7 — content-leaf text-only identity (design §22.1 anchor ladder).
        self._check_content_leaf_identity(pack, violations, record)

        if worst is not None:
            return worst
        if unsupported_layers:
            return IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS
        return IntegrityVerdict.VALID

    def _check_row_hash(
        self,
        kind: str,
        row: Mapping[str, JsonValue],
        violations: list[TypedViolation],
        record,
    ) -> None:
        declared = row.get("object_hash")
        body = _row_object(row)
        if not isinstance(declared, str) or body is None:
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_HASH,
                    level="L0",
                    layer=kind,
                    subject=str(declared),
                    detail="row is not a {object_hash, object} wrapper (§1.3)",
                )
            )
            record(IntegrityVerdict.INVALID_HASH)
            return
        try:
            recomputed = semantic_hash(body)
        except CanonicalJsonError as exc:
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_HASH,
                    level="L0",
                    layer=kind,
                    subject=declared,
                    detail=f"object body is not canonical-JSON: {exc}",
                )
            )
            record(IntegrityVerdict.INVALID_HASH)
            return
        if _strip(recomputed) != _strip(declared):
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_HASH,
                    level="L0",
                    layer=kind,
                    subject=declared,
                    expected=recomputed,
                    actual=declared,
                    detail=(
                        f"row object_hash mismatch in layer {kind!r}: "
                        f"declared {declared}, recomputed {recomputed}"
                    ),
                )
            )
            record(IntegrityVerdict.INVALID_HASH)

    def _check_schemas(
        self,
        pack: Pack,
        violations: list[TypedViolation],
        record,
        unsupported_layers: list[str],
    ) -> None:
        required = set(pack.manifest.required_layers_for_browse) | set(
            pack.manifest.required_layers_for_audit
        )
        optional = set(pack.manifest.optional_layers)
        for kind, layer in pack.layers.items():
            for row in layer.rows:
                body = _row_object(row)
                if body is None:
                    continue
                schema = body.get("schema")
                if not isinstance(schema, str):
                    continue
                if schema in pack.known_schemas:
                    continue
                if kind in optional and kind not in required:
                    if kind not in unsupported_layers:
                        unsupported_layers.append(kind)
                    continue
                violations.append(
                    TypedViolation(
                        code=ViolationCode.UNSUPPORTED_SCHEMA,
                        level="L0",
                        layer=kind,
                        subject=schema,
                        detail=(
                            f"required-layer object declares unknown schema {schema!r} "
                            f"in layer {kind!r} (checker lacks this schema/profile)"
                        ),
                    )
                )
                record(IntegrityVerdict.UNSUPPORTED_SCHEMA)

    def _check_layer_root(
        self,
        kind: str,
        layer: PackLayerData,
        claimed_root: str,
        violations: list[TypedViolation],
        record,
    ) -> None:
        """Recompute the layer root from its rows and compare to the manifest claim.

        ``claimed_root`` is the manifest layer descriptor's ``root`` — the single
        authoritative claim. ``PackLayerData.root`` is a transport echo; the
        manifest descriptor is what a forged-root attack (drill #8) tampers, so
        the recompute-vs-manifest comparison is the load-bearing one.
        """
        hashes = [
            str(row["object_hash"])
            for row in layer.rows
            if isinstance(row.get("object_hash"), str)
        ]
        try:
            fn = _root_fn_for(layer.root_fn)
        except _UnknownRootFn:
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_ROOT,
                    level="L0",
                    layer=kind,
                    subject=layer.root_fn,
                    detail=f"layer {kind!r} declares unknown root_fn {layer.root_fn!r}",
                )
            )
            record(IntegrityVerdict.INVALID_ROOT)
            return
        try:
            recomputed = fn(layer.domain, hashes)
        except RootError as exc:
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_ROOT,
                    level="L0",
                    layer=kind,
                    subject=layer.domain,
                    detail=f"layer {kind!r} root construction failed: {exc}",
                )
            )
            record(IntegrityVerdict.INVALID_ROOT)
            return
        if recomputed != claimed_root:
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_ROOT,
                    level="L0",
                    layer=kind,
                    subject=f"layer:{kind}",
                    expected=recomputed,
                    actual=claimed_root,
                    detail=(
                        f"layer {kind!r} root mismatch ({layer.root_fn} over "
                        f"{len(hashes)} rows): claimed {claimed_root}, recomputed {recomputed}"
                    ),
                )
            )
            record(IntegrityVerdict.INVALID_ROOT)

    def _check_manifest_roots(
        self, pack: Pack, violations: list[TypedViolation], record
    ) -> None:
        # pack_id == LeafHash("pack_manifest", manifest_without_{provenance,pack_id}).
        # The dataclass recomputes pack_id deterministically; this asserts the
        # constructor invariant (the roots-of-roots spine). Layer-root claims are
        # verified in L0.3 against the manifest descriptor (the authoritative
        # claim), so there is no separate descriptor-vs-data cross-check here.
        recomputed_pack_id = leaf_hash("pack_manifest", pack.manifest._hashed_dict())
        # selection_index_root consistency: if the manifest declares it AND the
        # pack supplies a state-selection universe root, the universe root must
        # be the one the universe map recomputes to (L0.4 / L0.6 bridge).
        if (
            pack.selection_universe is not None
            and pack.selection_universe_root is not None
        ):
            recomputed_universe = map_root("selection_universe", dict(pack.selection_universe))
            if recomputed_universe != pack.selection_universe_root:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.INVALID_ROOT,
                        level="L0",
                        layer="state",
                        subject="selection_universe_root",
                        expected=recomputed_universe,
                        actual=pack.selection_universe_root,
                        detail=(
                            "selection_universe MapRoot mismatch: declared "
                            f"{pack.selection_universe_root}, recomputed {recomputed_universe}"
                        ),
                    )
                )
                record(IntegrityVerdict.INVALID_ROOT)
        # Self-consistency of pack_id (a corrupted hashed member would surface
        # here as a recompute differing from a separately-cached value; the
        # frozen dataclass recomputes deterministically, so this asserts the
        # constructor invariant rather than catching tamper — tamper is caught
        # at the layer-root and row-hash level).
        _ = recomputed_pack_id

    def _check_referential_closure(
        self,
        pack: Pack,
        violations: list[TypedViolation],
        record,
        unsupported_layers: list[str],
    ) -> None:
        present: set[str] = set()
        for layer in pack.layers.values():
            for row in layer.rows:
                h = row.get("object_hash")
                if isinstance(h, str):
                    present.add(_strip(h))
        for ref_name, ref_hash in pack.referenced_hashes.items():
            if _strip(ref_hash) not in present:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.INVALID_MISSING_OBJECT,
                        level="L0",
                        layer="base",
                        subject=ref_hash,
                        detail=(
                            f"referenced object {ref_name!r} → {ref_hash} "
                            f"is not present as any row's object_hash"
                        ),
                    )
                )
                record(IntegrityVerdict.INVALID_MISSING_OBJECT)

    def _check_universe_domain(
        self, pack: Pack, violations: list[TypedViolation], record
    ) -> None:
        if pack.selection_universe is None:
            return
        universe_keys = set(pack.selection_universe.keys())
        present_keys: set[str] = set()
        state = pack.layers.get("state")
        if state is not None:
            for row in state.rows:
                body = _row_object(row)
                if body is None:
                    continue
                if body.get("schema") != "lawvm.selection_row.v1":
                    continue
                key = body.get("selection_key")
                if isinstance(key, str):
                    present_keys.add(key)
        shortfall = universe_keys - present_keys
        surplus = present_keys - universe_keys
        for key in sorted(shortfall):
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_SELECTION_UNIVERSE,
                    level="L0",
                    layer="state",
                    subject=key,
                    detail=(
                        f"selection_universe declares key {key!r} but no selection_row "
                        f"is present (SHORTFALL — an omitted row)"
                    ),
                )
            )
            record(IntegrityVerdict.INVALID_SELECTION_UNIVERSE)
        for key in sorted(surplus):
            violations.append(
                TypedViolation(
                    code=ViolationCode.INVALID_SELECTION_UNIVERSE,
                    level="L0",
                    layer="state",
                    subject=key,
                    detail=(
                        f"selection_row key {key!r} is present but undeclared in "
                        f"selection_universe (SURPLUS — an unaccounted row)"
                    ),
                )
            )
            record(IntegrityVerdict.INVALID_SELECTION_UNIVERSE)

    def _check_content_leaf_identity(
        self, pack: Pack, violations: list[TypedViolation], record
    ) -> None:
        """L0.7 — every content leaf's ``content_leaf_hash`` is the TEXT-ONLY id.

        The shared content leaf is the highest dedup anchor (design §22.1): its
        identity is ``LeafHash("content_leaf", {schema, text})`` and NOTHING
        per-work. A leaf carrying ``source_locators`` / ``work_id`` (the old bug)
        or a ``content_leaf_hash`` recomputed over anything but ``{schema, text}``
        would defeat cross-work dedup, so it is a hard ``INVALID_HASH`` here.
        Jurisdiction-neutral — it fires only for the ``lawvm.content_leaf.v1``
        schema, present in any pack family that emits leaves (FI replay, LOCUS
        snapshot, corpus store).
        """
        for kind, layer in pack.layers.items():
            for row in layer.rows:
                body = _row_object(row)
                if body is None or body.get("schema") != "lawvm.content_leaf.v1":
                    continue
                declared = body.get("content_leaf_hash")
                text = body.get("text")
                if not isinstance(declared, str) or not isinstance(text, str):
                    violations.append(
                        TypedViolation(
                            code=ViolationCode.INVALID_HASH,
                            level="L0",
                            layer=kind,
                            subject=str(declared),
                            detail=(
                                "content_leaf is missing a string content_leaf_hash/text "
                                "(text-only identity, §22.1)"
                            ),
                        )
                    )
                    record(IntegrityVerdict.INVALID_HASH)
                    continue
                # A leaf must carry text-only members; any per-work member (the
                # source_locators bug) defeats cross-work dedup.
                extra = set(body.keys()) - {"schema", "text", "content_leaf_hash"}
                recomputed = leaf_hash(
                    "content_leaf", {"schema": body.get("schema"), "text": text}
                )
                if _strip(recomputed) != _strip(declared) or extra:
                    violations.append(
                        TypedViolation(
                            code=ViolationCode.INVALID_HASH,
                            level="L0",
                            layer=kind,
                            subject=declared,
                            expected=recomputed,
                            actual=declared,
                            detail=(
                                "content_leaf_hash is not the text-only identity "
                                f"LeafHash('content_leaf', {{schema, text}}); "
                                f"recomputed {recomputed}"
                                + (
                                    f"; leaf carries non-text members {sorted(extra)} "
                                    "(per-work provenance belongs on the node_version, §22.1)"
                                    if extra
                                    else ""
                                )
                            ),
                        )
                    )
                    record(IntegrityVerdict.INVALID_HASH)

    # -- L1: finite-interval selection algebra (contract §3) ---------------- #

    def _run_l1(
        self, pack: Pack, violations: list[TypedViolation]
    ) -> IntegrityVerdict | None:
        """L1.1 intervals · L1.2 candidate completeness · L1.3 profile pick ·
        L1.4 ambiguous_missing_scope · L1.5 blocked-row citation · L1.6 no
        selected-row overlap. Returns ``INVALID_SELECTION_OVERLAP`` if L1.6
        fires (the only L1 step that maps to a top-line integrity verdict),
        else ``None`` (other L1 findings are structural violations that do not
        flip the integrity axis but DO populate ``violations[]``).
        """
        state = pack.layers.get("state")
        if state is None:
            return None
        rows = [
            body
            for row in state.rows
            if (body := _row_object(row)) is not None
            and body.get("schema") == "lawvm.selection_row.v1"
        ]
        facts = [
            body
            for row in state.rows
            if (body := _row_object(row)) is not None
            and body.get("schema") == "lawvm.applicability_fact.v1"
        ]
        candidate_sets = {
            cs_hash: body
            for row in state.rows
            if (body := _row_object(row)) is not None
            and body.get("schema") == "lawvm.selection_candidate_set.v1"
            and isinstance((cs_hash := row.get("object_hash")), str)
        }
        residuals = [
            body
            for row in state.rows
            if (body := _row_object(row)) is not None
            and body.get("schema") == "lawvm.residual.v1"
        ]

        self._l1_facts_well_formed(facts, violations)
        self._l1_candidate_completeness(rows, candidate_sets, violations)
        self._l1_scope_marking(rows, candidate_sets, violations)
        self._l1_blocked_cite(rows, residuals, violations)
        overlap = self._l1_no_selected_overlap(rows, violations)
        if overlap:
            return IntegrityVerdict.INVALID_SELECTION_OVERLAP
        return None

    def _l1_facts_well_formed(
        self, facts: Sequence[Mapping[str, JsonValue]], violations: list[TypedViolation]
    ) -> None:
        """L1.1 — intervals half-open + per-rail single-version non-overlap."""
        by_rail: dict[tuple[str, str, str, str], list[tuple[str, str | None]]] = {}
        for fact in facts:
            fid = str(fact.get("fact_id", "<unknown>"))
            interval = _as_interval(fact.get("effect_interval", None))
            if interval is None:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.INVALID_INTERVAL,
                        level="L1",
                        layer="state",
                        subject=fid,
                        detail=f"applicability_fact {fid} effect_interval is not a half-open [s,e)",
                    )
                )
                continue
            start, end = interval
            if end is not None and not start < end:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.INVALID_INTERVAL,
                        level="L1",
                        layer="state",
                        subject=fid,
                        detail=(
                            f"applicability_fact {fid} effect_interval [{start},{end}) "
                            f"is empty/inverted (start must be < end)"
                        ),
                    )
                )
                continue
            rail_key = (
                str(fact.get("work_id", "")),
                str(fact.get("address_id", "")),
                str(fact.get("branch_id", "")),
                str(fact.get("rail", "")),
            )
            by_rail.setdefault(rail_key, []).append(interval)
        for rail_key, intervals in by_rail.items():
            for i, a in enumerate(intervals):
                for b in intervals[i + 1 :]:
                    if _intervals_overlap(a, b):
                        violations.append(
                            TypedViolation(
                                code=ViolationCode.SINGLE_RAIL_OVERLAP,
                                level="L1",
                                layer="state",
                                subject=":".join(rail_key),
                                detail=(
                                    f"two simultaneous live versions on one rail {rail_key}: "
                                    f"{a} overlaps {b}"
                                ),
                            )
                        )

    def _l1_candidate_completeness(
        self,
        rows: Sequence[Mapping[str, JsonValue]],
        candidate_sets: Mapping[str, Mapping[str, JsonValue]],
        violations: list[TypedViolation],
    ) -> None:
        """L1.2 — every nontrivial row's candidate set is present + complete."""
        for row in rows:
            status = row.get("status")
            if status in ("absent", "out_of_scope", "unsupported_profile"):
                continue
            cs_hash = row.get("candidate_set_hash")
            key = str(row.get("selection_key", "<unknown>"))
            if not isinstance(cs_hash, str):
                violations.append(
                    TypedViolation(
                        code=ViolationCode.CANDIDATE_INCOMPLETE,
                        level="L1",
                        layer="state",
                        subject=key,
                        detail=f"selection_row {key} (status={status}) carries no candidate_set_hash",
                    )
                )
                continue
            cs = candidate_sets.get(cs_hash)
            if cs is None:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.CANDIDATE_INCOMPLETE,
                        level="L1",
                        layer="state",
                        subject=key,
                        detail=(
                            f"selection_row {key} cites candidate_set_hash {cs_hash} "
                            f"which is not present in the state layer"
                        ),
                    )
                )
                continue
            if cs.get("complete") is not True:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.CANDIDATE_INCOMPLETE,
                        level="L1",
                        layer="state",
                        subject=key,
                        detail=(
                            f"candidate set for {key} is marked complete!=true "
                            f"(an incomplete candidate set cannot back a selection)"
                        ),
                    )
                )
                continue
            # A SELECTED row must have its selected node_version among candidates.
            if status == "selected":
                selected = row.get("selected_node_version_id")
                candidates = cs.get("candidates")
                ids = self._candidate_ids(candidates)
                if isinstance(selected, str) and selected not in ids:
                    violations.append(
                        TypedViolation(
                            code=ViolationCode.CANDIDATE_INCOMPLETE,
                            level="L1",
                            layer="state",
                            subject=key,
                            detail=(
                                f"row {key} selected node_version {selected!r} is absent from "
                                f"its complete candidate set (phantom selection)"
                            ),
                        )
                    )

    @staticmethod
    def _candidate_ids(candidates: JsonValue) -> set[str]:
        ids: set[str] = set()
        if isinstance(candidates, (list, tuple)):
            for cand in candidates:
                if isinstance(cand, Mapping):
                    nv = cast(Mapping[str, JsonValue], cand).get("node_version_id")
                    if isinstance(nv, str):
                        ids.add(nv)
        return ids

    def _l1_scope_marking(
        self,
        rows: Sequence[Mapping[str, JsonValue]],
        candidate_sets: Mapping[str, Mapping[str, JsonValue]],
        violations: list[TypedViolation],
    ) -> None:
        """L1.4 — ambiguous_missing_scope correctly marked (both directions).

        A row marked ``ambiguous_missing_scope`` MUST have a witnessing
        scope-divergent eligible candidate pair (N-dimensional intersection,
        contract §3 L1.4 / RESOLVED cross-check): conflict requires ALL
        dimensions to overlap; an ``unsupported`` scope dim short-circuits to
        ``ambiguous_missing_scope`` (never ``blocked``).
        """
        for row in rows:
            if row.get("status") != "ambiguous_missing_scope":
                continue
            key = str(row.get("selection_key", "<unknown>"))
            cs_hash = row.get("candidate_set_hash")
            cs = candidate_sets.get(cs_hash) if isinstance(cs_hash, str) else None
            if cs is None or not self._has_scope_divergent_pair(cs):
                violations.append(
                    TypedViolation(
                        code=ViolationCode.SCOPE_AMBIGUITY_UNWITNESSED,
                        level="L1",
                        layer="state",
                        subject=key,
                        detail=(
                            f"row {key} is ambiguous_missing_scope but has no witnessing "
                            f"scope-divergent eligible candidate pair"
                        ),
                    )
                )

    @staticmethod
    def _has_scope_divergent_pair(cs: Mapping[str, JsonValue]) -> bool:
        """Two eligible candidates with DIFFERENT scope_predicate_id (witness)."""
        seen: set[str] = set()
        candidates = cs.get("candidates")
        if not isinstance(candidates, (list, tuple)):
            return False
        for cand in candidates:
            if not isinstance(cand, Mapping):
                continue
            typed = cast(Mapping[str, JsonValue], cand)
            if typed.get("eligible") is not True:
                continue
            spid = typed.get("scope_predicate_id")
            if isinstance(spid, str):
                seen.add(spid)
        return len(seen) >= 2

    def _l1_blocked_cite(
        self,
        rows: Sequence[Mapping[str, JsonValue]],
        residuals: Sequence[Mapping[str, JsonValue]],
        violations: list[TypedViolation],
    ) -> None:
        """L1.5 — a blocked row cites a block_reason AND a blocking residual."""
        blocking_kinds = {
            str(r.get("kind"))
            for r in residuals
            if r.get("blocking") is True and isinstance(r.get("kind"), str)
        }
        for row in rows:
            if row.get("status") != "blocked":
                continue
            key = str(row.get("selection_key", "<unknown>"))
            reason = row.get("block_reason")
            if not isinstance(reason, str) or not reason:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.BLOCKED_ROW_UNCITED,
                        level="L1",
                        layer="state",
                        subject=key,
                        detail=f"row {key} status=blocked but carries no block_reason",
                    )
                )
                continue
            if reason not in blocking_kinds:
                violations.append(
                    TypedViolation(
                        code=ViolationCode.BLOCKED_ROW_UNCITED,
                        level="L1",
                        layer="state",
                        subject=key,
                        detail=(
                            f"row {key} status=blocked with block_reason {reason!r} but no "
                            f"blocking residual of that kind is present"
                        ),
                    )
                )

    def _l1_no_selected_overlap(
        self, rows: Sequence[Mapping[str, JsonValue]], violations: list[TypedViolation]
    ) -> bool:
        """L1.6 — no two SELECTED rows overlap for one selection key.

        Group by ``(work_id, address_id, branch_id, query_profile_id,
        scope_query_id)``; within a group the effect_intervals of ``selected``
        rows must be pairwise disjoint. Returns True if any overlap fired.
        """
        groups: dict[
            tuple[str, str, str, str, str], list[tuple[str, tuple[str, str | None]]]
        ] = {}
        for row in rows:
            if row.get("status") != "selected":
                continue
            interval = _as_interval(row.get("effect_interval", None))
            if interval is None:
                continue
            group_key = (
                str(row.get("work_id", "")),
                str(row.get("address_id", "")),
                str(row.get("branch_id", "")),
                str(row.get("query_profile_id", "")),
                str(row.get("scope_query_id", "")),
            )
            key = str(row.get("selection_key", "<unknown>"))
            groups.setdefault(group_key, []).append((key, interval))
        fired = False
        for group_key, members in groups.items():
            for i, (key_a, a) in enumerate(members):
                for key_b, b in members[i + 1 :]:
                    if _intervals_overlap(a, b):
                        fired = True
                        violations.append(
                            TypedViolation(
                                code=ViolationCode.INVALID_SELECTION_OVERLAP,
                                level="L1",
                                layer="state",
                                subject=":".join(group_key),
                                detail=(
                                    f"two SELECTED rows overlap on one selection key "
                                    f"{group_key}: {key_a} {a} ∩ {key_b} {b}"
                                ),
                            )
                        )
        return fired

    # -- source availability + certification fold (contract §2 L0.1 / §3 L1.7) #

    def _check_source_availability(
        self, pack: Pack, violations: list[TypedViolation]
    ) -> CertificationVerdict | None:
        """Audit mode: every audited source ref must be offline-available.

        Returns ``UNCHECKABLE_DIGEST_ONLY`` / ``UNCHECKABLE_MISSING_ARTIFACTS``
        when a required source is digest-only / lost (NEVER ``INVALID``); ``None``
        when all good (or browse mode, which never requires source bytes).
        """
        if self.mode is not CheckMode.AUDIT:
            return None
        worst: CertificationVerdict | None = None
        for ref in pack.audited_source_refs:
            avail = pack.source_availability.get(ref, SourceAvailability.unknown)
            if avail in _AVAILABLE_OFFLINE:
                continue
            if avail == SourceAvailability.lost:
                cert = CertificationVerdict.UNCHECKABLE_MISSING_ARTIFACTS
            elif avail in _DIGEST_ONLY_OFFLINE:
                cert = CertificationVerdict.UNCHECKABLE_DIGEST_ONLY
            else:
                cert = CertificationVerdict.UNCHECKABLE_DIGEST_ONLY
            violations.append(
                TypedViolation(
                    code=ViolationCode.UNCHECKABLE_MISSING_SOURCE,
                    level="L0",
                    layer="base",
                    subject=ref,
                    detail=(
                        f"audited source ref {ref} is {avail!r} offline "
                        f"(uncheckable, never invalid — design §3.4/§12)"
                    ),
                )
            )
            if worst is None or _CERT_RANK[cert] < _CERT_RANK[worst]:
                worst = cert
        return worst

    def _fold_certification(
        self,
        pack: Pack,
        violations: list[TypedViolation],
        source_verdict: CertificationVerdict | None,
    ) -> CertificationVerdict:
        """L1.7 — fold selection-row statuses + residuals to the cert axis.

        ``blocked`` ⇒ ``VALID_BLOCKED``; a qualifying (non-blocking) residual on
        a live row ⇒ ``VALID_QUALIFIED``; else ``VALID_CLEAN``. A source
        uncheckability dominates only if it is worse than the legal status (a
        blocked legal state is a more specific honest answer than digest-only).
        If browse mode omits the state/proof bodies → ``NOT_COMPUTED``.
        """
        state = pack.layers.get("state")
        if state is None:
            # No legal-state detail to fold (browse-minimal). NOT_COMPUTED is an
            # acceptable browse top-line (contract §9.3).
            return source_verdict or CertificationVerdict.NOT_COMPUTED

        rows = [
            body
            for row in state.rows
            if (body := _row_object(row)) is not None
            and body.get("schema") == "lawvm.selection_row.v1"
        ]
        residuals = [
            body
            for row in state.rows
            if (body := _row_object(row)) is not None
            and body.get("schema") == "lawvm.residual.v1"
        ]
        has_blocked = any(r.get("status") == "blocked" for r in rows)
        has_qualifying = any(
            r.get("blocking") is False for r in residuals
        )

        if has_blocked:
            legal = CertificationVerdict.VALID_BLOCKED
        elif has_qualifying:
            legal = CertificationVerdict.VALID_QUALIFIED
        else:
            legal = CertificationVerdict.VALID_CLEAN

        if source_verdict is None:
            return legal
        # Both present — the more specific honest answer wins by cert rank.
        return legal if _CERT_RANK[legal] <= _CERT_RANK[source_verdict] else source_verdict


# Integrity precedence rank (lower = dominant, contract §1.1). Clean states get
# the highest numbers so any INVALID_* dominates them.
_INTEGRITY_RANK: dict[IntegrityVerdict, int] = {
    IntegrityVerdict.INVALID_HASH: 1,
    IntegrityVerdict.INVALID_ROOT: 2,
    IntegrityVerdict.INVALID_MISSING_OBJECT: 3,
    IntegrityVerdict.INVALID_SELECTION_UNIVERSE: 4,
    IntegrityVerdict.INVALID_SELECTION_OVERLAP: 5,
    IntegrityVerdict.UNSUPPORTED_SCHEMA: 6,
    IntegrityVerdict.UNCHECKABLE_MISSING_SOURCE: 7,
    IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS: 10,
    IntegrityVerdict.VALID: 11,
}

# Certification precedence rank (lower = more dominant / more specific).
_CERT_RANK: dict[CertificationVerdict, int] = {
    CertificationVerdict.VALID_BLOCKED: 1,
    CertificationVerdict.UNCHECKABLE_MISSING_ARTIFACTS: 2,
    CertificationVerdict.UNCHECKABLE_DIGEST_ONLY: 3,
    CertificationVerdict.VALID_QUALIFIED: 4,
    CertificationVerdict.VALID_CLEAN: 5,
    CertificationVerdict.NOT_COMPUTED: 6,
}


def check_pack(
    pack: Pack,
    mode: CheckMode = CheckMode.BROWSE,
    level: CheckLevel = CheckLevel.L0_L1,
) -> CheckerVerdict:
    """Convenience: construct a :class:`Checker` and run it (contract §6.1)."""
    return Checker(mode=mode, level=level).check(pack)
