"""EXPERIMENTAL one-work certificate bundle writer (schema-pressure fixture).

Emits a complete ``lawvm.certificate.v0`` bundle directory for ONE Finnish
statute per notes/CERTIFICATE_SCHEMA_V0.md (spec_version 0.4.1) and
notes/CERTIFIED_TREE_TRANSITION_TRACE_V0.md (spec_version 0.3), within the
experimental-writer boundary of certificate spec §11.3:

* one Finnish legal work, ``closed_interval`` time scope, subsection or
  section granularity;
* all source bytes bundled from the local corpus (no URL-only references);
* one projection family: seam rows (``lawvm.provision_state.v1`` / seam
  spec 0.2);
* transitions are DERIVED FROM OBSERVED STATE DIFFS (the certificate spec
  §10 experimental carve-out) — the engine's covering-state evolution per
  change date, exactly the shape ``export_transition_graph`` computes.

THE OUTPUT IS A BUNDLE-WRITER FIXTURE, NOT A CHECKED CERTIFICATE. No checker
exists; nothing here emits or implies a ``VALID_*`` verdict. The writer-side
self-check (:func:`verify_bundle`) recomputes every committed root from the
bundle artifacts independently so the WRITER cannot ship an internally
inconsistent bundle — it is not checker v0 and asserts nothing beyond the
writer's own consistency. Bundles MUST NOT be published or presented as
checkable public claims (certificate spec §10, §11.3).

The interim decisions this writer surfaced on first emission were ratified
(or resolved) into certificate spec 0.4.1 §11.4; remaining engine-surface
limits are marked with ``SPEC-NOTE:`` comments referencing the exact spec
section.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.observation_registry import FINDING_REGISTRY, FindingSpec
from lawvm.tools.export_transition_graph import (
    DEFAULT_GRANULARITY,
    _canonical_statute_id,
    _engine_statute_id,
    _index_ops_by_date,
    _index_ops_by_expiry_date,
    _legal_op_summary,
    _ops_for_covering,
    covering_units,
    materialize_oracle_tree,
    run_engine_replay,
    structural_subtree_hash,
)

# ---------------------------------------------------------------------------
# Frozen identifiers (certificate spec §3.1.1, §6; trace spec §4, §5.2, §8.2)
# ---------------------------------------------------------------------------

CERTIFICATE_SCHEMA = "lawvm.certificate.v0"
CERTIFICATE_SPEC_VERSION = "0.4.1"
TRACE_SPEC_VERSION = "0.3"
SEAM_SPEC_VERSION = "0.2"
SEAM_SCHEMA = "lawvm.provision_state.v1"
PROFILE_ID = "fi.strict.current"
POLICY_ID = "lawvm.fi.default.v1"
HASH_PROFILE = "lawvm.hash.canonical_json.v1"
CHECKER_VERSION = "lawvm.checker.v0"

D_CERT_ROOT = "lawvm.certificate.v0.root"
D_TRACE = "lawvm.certified_tree_transition_trace.v0"
D_TRANSITION = "lawvm.certified_tree_transition.v0"
D_SOURCE_BUNDLE = "lawvm.source_bundle.v0"
D_SOURCE_ARTIFACT = "lawvm.source_artifact.v0"
D_BASE_TREE = "lawvm.base_tree.v0"
D_CONTENT_BLOBS = "lawvm.content_blobs.v0"
D_CONTENT_BLOB = "lawvm.content_blob.v0"
D_STATE_ROOT = "lawvm.state_root.v0"
D_MATERIALIZATION = "lawvm.materialization_index.v0"
D_PROJECTION_PAYLOAD = "lawvm.projection_payload.v0"
D_PROJECTION_SEAM = "lawvm.projection.seam.v0"
D_PROJECTION_ROOT = "lawvm.projection_root.v0"
D_RESIDUAL_LEDGER = "lawvm.residual_ledger.v0"
D_FINDING_LEDGER = "lawvm.finding_ledger.v0"
D_SOURCE_UNIT_COVERAGE = "lawvm.source_unit_coverage.v0"
D_POTENTIAL_OP_COVERAGE = "lawvm.potential_operation_coverage.v0"
D_COVERAGE = "lawvm.coverage.v0"
D_STRICT_PROFILE = "lawvm.strict_profile.v0"
D_INTERPRETATION_POLICY = "lawvm.interpretation_policy.v0"
D_PROJECTION_SPECS = "lawvm.projection_specs.v0"
D_DIAGNOSTIC_REGISTRY = "lawvm.diagnostic_registry.v0"
D_CHECKER_CONTRACT = "lawvm.checker_contract.v0"
# Certificate spec §2.1: change_dates_root is a VALUE SET — raw ISO date
# strings under this domain, the one named exception to the digest-member
# rule of §3.1.1.
D_CHANGE_DATES = "lawvm.change_dates.v0"

# Certificate spec §3.5: bundle-local certificate-layer code (CERT.
# namespace) carried by kind=source_anchor_unavailable residuals (§5.4,
# trace spec §7).
SOURCE_ANCHOR_UNAVAILABLE_CODE = "CERT.SOURCE_ANCHOR_UNAVAILABLE"

# Certificate spec §3.4 + §3.5: per-family run-provenance exclusion list.
# The seam payload's `engine` block (git commit/dirty/repository) is
# excluded from the projection-hash input — visible in the artifact row,
# never hashed (mirrors seam spec §3.1's derived_state_hash exclusion).
SEAM_HASH_EXCLUDED_MEMBERS: Tuple[str, ...] = ("engine",)

# Certificate spec §5.4 typed blocking fixed-term codes mapped to
# kind=expiry_unverified (matches seam spec 0.2 §6.1, including
# TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED).
_EXPIRY_BLOCKING_CODES = frozenset(
    {
        "TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE",
        "TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS",
        "TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS",
        "TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING",
        "TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED",
        "TEMPORAL.EVENT_BOUND_RESOLVER_MISSING",
        "TEMPORAL.EVENT_BOUND_OUT_OF_DOCTRINE",
        "TEMPORAL.SOURCE_IMPOSSIBLE_DATE",
    }
)

# Certificate spec §11.3 alias-migration example: the renamed universal code
# carries its deprecated surface-lexeme alias in registry metadata.
_DEPRECATED_ALIASES: Dict[str, Tuple[str, ...]] = {
    "TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED": (
        "TEMPORAL.NON_VALIDITY_VOIMASSA_SUPPRESSED",
    ),
}

# StrictProfile channel gates: codes whose strict-profile disposition is
# governed by an explicit allows_* channel. When the channel is open the
# disposition softens from blocks to qualifies. (Writer-side approximation of
# the engine's verdict-rail composition; see module report.)
_PROFILE_GATED_CODES: Dict[str, str] = {
    "TIME.ESTIMATED_EFFECTIVE_DATE": "allows_estimated_dates",
    "PARSE.TARGET_GUESSING": "allows_target_guessing",
    "ELAB.OMISSION_EXPANSION": "allows_omission_expansion",
    "APPLY.UNCOVERED_BODY_RECOVERY": "allows_uncovered_body_recovery",
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE": "allows_fallback_whole_section_replace",
    "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION": "allows_context_dependent_anchor_resolution",
    "APPLY.WORD_SUBSTITUTION": "allows_word_substitution",
    "APPLY.SOURCE_CORRECTED_BY_PATCH": "allows_source_correction_rules",
}

_RESIDUAL_KINDS = (
    "expiry_unverified",
    "failed_operation",
    "manual_frontier",
    "source_pathology",
    "grounding_unclassified",
    "quirks_recovery",
    "unsupported_scoped_expiry",
    "source_anchor_unavailable",
)


class BundleSpecError(ValueError):
    """A spec rule was violated while constructing bundle artifacts."""


class BundleSelfCheckError(AssertionError):
    """Writer-side self-check failed: a recomputed root or status disagrees.

    This is the WRITER's own consistency gate, not a checker verdict.
    """


# ---------------------------------------------------------------------------
# Canonical hash profile (certificate spec §3.1) and root constructors
# (certificate spec §3.1.1) — frozen
# ---------------------------------------------------------------------------


def canonical_json_bytes(obj: Any) -> bytes:
    """UTF-8 bytes of the canonical JSON encoding (§3.1 frozen profile)."""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _rendered(digest: "hashlib._Hash") -> str:
    return "sha256:" + digest.hexdigest()


def leaf_hash(domain: str, obj: Any) -> str:
    """``LeafHash(domain, obj)`` per certificate spec §3.1.1."""
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b"\x00")
    h.update(canonical_json_bytes(obj))
    return _rendered(h)


def list_root(domain: str, ordered_leaf_hashes: Sequence[str]) -> str:
    """``ListRoot(domain, ordered)`` per §3.1.1. Duplicate leaves are INVALID."""
    ordered = list(ordered_leaf_hashes)
    if len(set(ordered)) != len(ordered):
        raise BundleSpecError(f"duplicate leaf under ListRoot({domain!r}) — INVALID per spec §3.1.1")
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b":list\x00")
    h.update(canonical_json_bytes(ordered))
    return _rendered(h)


def set_root(domain: str, leaf_hashes: Iterable[str]) -> str:
    """``SetRoot(domain, leaves)`` per §3.1.1. Duplicate leaves are INVALID."""
    leaves = sorted(leaf_hashes)
    for a, b in zip(leaves, leaves[1:], strict=False):
        if a == b:
            raise BundleSpecError(f"duplicate leaf under SetRoot({domain!r}) — INVALID per spec §3.1.1")
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b":set\x00")
    h.update(canonical_json_bytes(leaves))
    return _rendered(h)


def _sha256_rendered(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def projection_hash_view(payload: Mapping[str, Any], excluded_members: Sequence[str]) -> Dict[str, Any]:
    """Certificate spec §3.4 hash view: payload minus declared run-provenance.

    Excluded members stay VISIBLE in the emitted artifact row; only the hash
    input drops them, so engine commit/dirty-state churn cannot reach
    ``projection_hash`` or ``certificate_root``.
    """
    excluded = set(excluded_members)
    return {k: v for k, v in payload.items() if k not in excluded}


def projection_payload_hash(payload: Mapping[str, Any], excluded_members: Sequence[str]) -> str:
    """``projection_hash`` per certificate spec §3.4 (hash-view normalized)."""
    return leaf_hash(D_PROJECTION_PAYLOAD, projection_hash_view(payload, excluded_members))


def _plainify(value: Any, path: str = "") -> Any:
    """Convert frozen mappings/tuples from engine carriers into plain JSON values."""
    if isinstance(value, Mapping):
        return {str(k): _plainify(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plainify(v, f"{path}[]") for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise BundleSpecError(f"non-JSON value of type {type(value).__name__} at {path or '<root>'}")


# ---------------------------------------------------------------------------
# Diagnostic registry manifest (certificate spec §3.5)
# ---------------------------------------------------------------------------


def _residual_kind_for_code(code: str, spec: Optional[FindingSpec]) -> str:
    """Map a diagnostic code to its §5.4 residual kind (total, deterministic)."""
    if code == SOURCE_ANCHOR_UNAVAILABLE_CODE:
        return "source_anchor_unavailable"
    if code in _EXPIRY_BLOCKING_CODES:
        return "expiry_unverified"
    if code == "TEMPORAL.SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED":
        return "unsupported_scoped_expiry"
    if code == "APPLY.FAILED_OPERATION":
        return "failed_operation"
    if spec is None:
        return "grounding_unclassified"
    if spec.family == "source_pathology":
        return "source_pathology"
    if spec.family == "recovery":
        return "quirks_recovery"
    if spec.family == "violation":
        return "failed_operation"
    if spec.family == "ambiguity":
        # Certificate spec §5.4: non-expiry blocking ambiguity findings
        # (e.g. TIME.TRIGGER_COVERAGE_INCOMPLETE) map to manual_frontier —
        # resolution awaits external/manual input.
        return "manual_frontier"
    return "grounding_unclassified"


def _profile_disposition(code: str, spec: Optional[FindingSpec], profile_fields: Mapping[str, Any]) -> str:
    """Derive the (code, fi.strict.current) disposition: blocks/qualifies/permits."""
    if code == SOURCE_ANCHOR_UNAVAILABLE_CODE:
        # Diff-derived experimental transitions carry no byte anchors; the
        # gap qualifies the asserted state — it never reads as clean.
        return "qualifies"
    if spec is None:
        raise BundleSpecError(f"unregistered diagnostic code {code!r} has no derivable disposition")
    gate = _PROFILE_GATED_CODES.get(code)
    if gate is not None and bool(profile_fields.get(gate, False)):
        return "qualifies"
    if spec.role == "violation":
        return "blocks"
    if spec.role == "obligation":
        if spec.default_enforcement in ("hard_fail", "strict_fail"):
            return "blocks"
        return "qualifies"
    # observation (and anything informational)
    return "permits"


def build_diagnostic_registry_rows(profile_fields: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Emit §3.5 registry rows from the live engine observation registry.

    Registry "barrier" roles are strictness taxonomy metadata that can never
    appear on a runtime Finding; certificate spec §3.5 forbids registering
    codes whose role cannot produce a runtime finding, so they are excluded.
    """
    rows: List[Dict[str, Any]] = []
    for code in sorted(FINDING_REGISTRY):
        spec = FINDING_REGISTRY[code]
        if spec.role == "barrier":
            continue
        is_fixed_term = spec.owner == "fixed_term_expiry"
        kind = _residual_kind_for_code(code, spec)
        allowed_kinds: List[str] = [kind] if spec.role in ("obligation", "violation") or is_fixed_term else []
        if code.startswith("uk_"):
            jurisdiction_scope = ["uk"]
        elif is_fixed_term:
            jurisdiction_scope = ["fi"]
        else:
            jurisdiction_scope = []
        rows.append(
            {
                "code": code,
                "canonical_semantic_code": code,
                "deprecated_aliases": list(_DEPRECATED_ALIASES.get(code, ())),
                "introduced_in": "lawvm.certificate.v0.4",
                "deprecated_in": None,
                "role": spec.role,
                "allowed_residual_kinds": allowed_kinds,
                "profile_disposition": {PROFILE_ID: _profile_disposition(code, spec, profile_fields)},
                "jurisdiction_scope": jurisdiction_scope,
                "doctrine_scope": ["fi.fixed_term_expiry.v1"] if is_fixed_term else [],
                "surface_language": "fi" if is_fixed_term else None,
                "surface_lexemes": (
                    ["voimassa"] if code == "TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED" else []
                ),
            }
        )
    # Bundle-local writer code (see SOURCE_ANCHOR_UNAVAILABLE_CODE SPEC-GAP).
    rows.append(
        {
            "code": SOURCE_ANCHOR_UNAVAILABLE_CODE,
            "canonical_semantic_code": SOURCE_ANCHOR_UNAVAILABLE_CODE,
            "deprecated_aliases": [],
            "introduced_in": "lawvm.certificate.v0.4",
            "deprecated_in": None,
            "role": "obligation",
            "allowed_residual_kinds": ["source_anchor_unavailable"],
            "profile_disposition": {PROFILE_ID: "qualifies"},
            "jurisdiction_scope": [],
            "doctrine_scope": [],
            "surface_language": None,
            "surface_lexemes": [],
        }
    )
    rows.sort(key=lambda r: r["code"])
    return rows


# ---------------------------------------------------------------------------
# Scope intersection (certificate spec §5.3) and status algebra (§5.2, §5.5)
# ---------------------------------------------------------------------------


def _address_overlaps(residual_address: Optional[str], row_address: Optional[str]) -> bool:
    """Address overlap: null scopes everything; otherwise prefix in either direction."""
    if residual_address is None or row_address is None:
        return True
    if residual_address == row_address:
        return True
    return row_address.startswith(residual_address + "/") or residual_address.startswith(row_address + "/")


def _date_ranges_overlap(
    a: Tuple[Optional[str], Optional[str]],
    b: Tuple[Optional[str], Optional[str]],
) -> bool:
    """Half-open ISO-date interval overlap; ``None`` end = unbounded (§5.3)."""
    a_start, a_end = a
    b_start, b_end = b
    if a_end is not None and b_start is not None and a_end <= b_start:
        return False
    if b_end is not None and a_start is not None and b_end <= a_start:
        return False
    return True


def residual_intersects_row(
    residual: Mapping[str, Any],
    *,
    row_address: str,
    row_interval: Tuple[str, Optional[str]],
) -> bool:
    scope = residual.get("scope") or {}
    date_range = scope.get("date_range") or [None, None]
    return _address_overlaps(scope.get("address"), row_address) and _date_ranges_overlap(
        (date_range[0], date_range[1]), row_interval
    )


def residual_effect(residual: Mapping[str, Any], profile_id: str) -> str:
    effect = (residual.get("profile_effect") or {}).get(profile_id)
    return effect if effect in ("blocks", "qualifies", "permits") else "permits"


_SEAM_TO_CERTIFICATION = {
    # §5.5 normative mapping for seam 0.2 statuses.
    "selected": "confirmed",
    "absent": "confirmed",
    "expired": "confirmed",  # confirmed NON-LIVE temporal state, never live text
    "expiry_unverified": "blocked",
    "address_not_found": "blocked",
    "ambiguous_address": "blocked",
    "invalid_address": "blocked",
    "ambiguous_missing_scope": "blocked",
    "unsupported_jurisdiction": "not_applicable",
}

# §5.5: the qualifying-residual override applies to live/absent assertions;
# expired stays "confirmed" (the spec text attaches the override to selected
# and absent only).
_QUALIFIABLE_SEAM_STATUSES = frozenset({"selected", "absent"})


def certification_status_for_row(
    seam_status: str,
    *,
    row_address: str,
    row_interval: Tuple[str, Optional[str]],
    residual_rows: Sequence[Mapping[str, Any]],
    profile_id: str = PROFILE_ID,
) -> str:
    base = _SEAM_TO_CERTIFICATION.get(seam_status)
    if base is None:
        raise BundleSpecError(f"seam status {seam_status!r} has no §5.5 certification mapping")
    if base == "confirmed" and seam_status in _QUALIFIABLE_SEAM_STATUSES:
        for residual in residual_rows:
            if residual_effect(residual, profile_id) == "qualifies" and residual_intersects_row(
                residual, row_address=row_address, row_interval=row_interval
            ):
                return "qualified"
    return base


def compute_certificate_status(
    *,
    residual_rows: Sequence[Mapping[str, Any]],
    certification_statuses: Sequence[str],
    registered_codes: frozenset[str],
    profile_id: str = PROFILE_ID,
    required_artifacts_present: bool = True,
) -> str:
    """Certificate spec §5.2 status algebra — computed, never author-chosen."""
    if not required_artifacts_present:
        return "blocked"
    for residual in residual_rows:
        code = residual.get("diagnostic_code") or ""
        if not code or code == "unclassified" or code not in registered_codes:
            return "blocked"
        if residual_effect(residual, profile_id) == "blocks":
            return "blocked"
    if any(status == "blocked" for status in certification_statuses):
        return "blocked"
    if any(status == "unknown" for status in certification_statuses):
        # unknown is INVALID inside a clean or qualified certificate (§5.5).
        return "blocked"
    if any(status == "qualified" for status in certification_statuses):
        return "qualified"
    return "clean"


# ---------------------------------------------------------------------------
# Bundle construction
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class BundleWriteResult:
    bundle_dir: str
    certificate_id: str
    build_id: str
    certificate_status: str
    statute_id: str
    title: str
    boundary_dates: List[str]
    transition_count: int
    seam_row_count: int
    residual_count: int
    finding_count: int
    roots: Dict[str, str]
    writer_notes: List[str]


def _artifact_id(engine_sid: str) -> str:
    year, num = engine_sid.split("/")
    return f"fi.finlex.alkup.{year}.{num}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _seam_spec_hash() -> str:
    spec_path = _repo_root() / "notes" / "SEAM_SPEC_PROVISION_STATE.md"
    if not spec_path.is_file():
        raise BundleSpecError(
            f"seam spec document not found at {spec_path}; cannot pin projection_spec_hash (§3.4)"
        )
    return _sha256_rendered(spec_path.read_bytes())


def _certified_core(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Trace spec §5.1 certified-core field set (hashed into transition_hash)."""
    return {
        "transition_id": row["transition_id"],
        "sequence": row["sequence"],
        "effective_date": row["effective_date"],
        "action": row["action"],
        "target_address": row["target_address"],
        "pre_hash": row["pre_hash"],
        "post_hash": row["post_hash"],
        "payload_hash": row["payload_hash"],
        "source_refs": row["source_refs"],
        "source_anchors": row["source_anchors"],
    }


def _finding_row(
    *,
    diagnostic_code: str,
    role: str,
    blocking: bool,
    address: Optional[str],
    date_range: List[Optional[str]],
    source_refs: List[str],
    phase: str,
    detail: Mapping[str, Any],
) -> Dict[str, Any]:
    row = {
        "diagnostic_code": diagnostic_code,
        "role": role,
        "blocking": blocking,
        "scope": {"address": address, "date_range": date_range},
        "source_refs": source_refs,
        "phase": phase,
        "detail": _plainify(detail, "finding.detail"),
    }
    row["finding_id"] = leaf_hash(D_FINDING_LEDGER + ".id", row)
    return row


def _residual_row(
    *,
    kind: str,
    diagnostic_code: str,
    role: str,
    blocking: bool,
    address: Optional[str],
    date_range: List[Optional[str]],
    source_text: str,
    rule_id: str,
    source_refs: List[str],
    finding_refs: List[str],
    profile_effect: Mapping[str, str],
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if kind not in _RESIDUAL_KINDS:
        raise BundleSpecError(f"residual kind {kind!r} outside the §5.4 vocabulary")
    row: Dict[str, Any] = {
        "kind": kind,
        "diagnostic_code": diagnostic_code,
        "role": role,
        "blocking": blocking,
        "scope": {"address": address, "date_range": date_range},
        "source_text": source_text,
        "rule_id": rule_id,
        "source_refs": source_refs,
        "finding_refs": finding_refs,
        "profile_effect": dict(profile_effect),
    }
    if extra:
        row.update(extra)
    row["residual_id"] = leaf_hash(D_RESIDUAL_LEDGER + ".id", row)
    return row


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(canonical_json_bytes(row).decode("ascii"))
            fh.write("\n")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=True, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )


def build_certificate_bundle(
    statute_id: str,
    out_dir: str | Path,
    *,
    granularity: str = DEFAULT_GRANULARITY,
    quiet: bool = True,
    graph_store_root: str | Path | None = None,
) -> BundleWriteResult:
    """Write an EXPERIMENTAL certificate bundle for one Finnish statute.

    ``statute_id`` accepts canonical 'num/year' (482/2024) or engine
    'year/num' (2024/482). The bundle is a local schema-pressure fixture —
    see the module docstring for the §11.3 boundary.

    Emission registers the bundle as a taint-checkable build in the
    provenance graph store (``graph_store_root``, defaulting to
    ``$LAWVM_GRAPH_STORE_ROOT`` then ``data/fi/v1/provenance_graph``): a
    build node keyed by ``cert:lawvm.certificate.v{spec}:{certificate_root}``
    plus one consumed_by_build edge per consumed ProvenanceAssertion (currently the
    writer consumes none, so the record carries
    ``consumption_instrumented=True, consumed_subject_count=0``).  If the
    recorder fails, the emission fails — bundle files already on disk are
    NOT considered published (no BundleWriteResult is returned).
    """
    if granularity not in ("subsection", "section"):
        raise BundleSpecError(
            f"granularity {granularity!r} outside the experimental-writer boundary "
            "(certificate spec §11.3: subsection or section)"
        )
    notes: List[str] = []
    canonical_id = _canonical_statute_id(statute_id)
    engine_id = _engine_statute_id(canonical_id)
    out_path = Path(out_dir)

    bundle = run_engine_replay(engine_id)

    # --- time axis: ALL timeline boundary dates (certificate spec §2.1) ---
    from lawvm.finland.fixed_term_expiry import extract_fixed_term_bounds

    extraction = extract_fixed_term_bounds(statute_id=canonical_id, timelines=bundle.timelines)
    boundary_dates = set(bundle.change_dates)
    for bound in extraction.bounds:
        # Work-level fixed-term expires_on dates are real state boundaries (§2.1).
        if bound.expires_on:
            boundary_dates.add(bound.expires_on)
    if not boundary_dates:
        raise BundleSpecError(
            f"statute {canonical_id} has no committed boundary dates; cannot declare a "
            "closed_interval time_scope (§1)"
        )
    boundary = sorted(boundary_dates)
    time_scope = {"kind": "closed_interval", "from": boundary[0], "to": boundary[-1]}

    # --- sources: bundle ALL bytes locally (§11.3 boundary) ---
    from lawvm.finland.corpus import _get_corpus_store

    corpus = _get_corpus_store()
    source_statutes: Dict[str, str] = {engine_id: "enacted_text"}
    for op in bundle.lo_ops:
        src = op.source
        if src is not None and src.statute_id and _engine_statute_id(src.statute_id) != engine_id:
            source_statutes[_engine_statute_id(src.statute_id)] = "amending_text"

    source_blobs: Dict[str, bytes] = {}  # artifact_id -> raw bytes
    source_identities: List[Dict[str, Any]] = []
    artifact_id_by_engine_sid: Dict[str, str] = {}
    for sid in sorted(source_statutes):
        role = source_statutes[sid]
        data = corpus.read_source(sid) if role == "enacted_text" else corpus.read_amendment(sid)
        if data is None:
            raise BundleSpecError(
                f"source bytes for {sid} unavailable in local corpus; the experimental "
                "writer MUST bundle all source bytes (§11.3) — no URL-only references"
            )
        raw_hash = _sha256_rendered(data)
        aid = _artifact_id(sid)
        artifact_id_by_engine_sid[sid] = aid
        source_blobs[aid] = data
        year, num = sid.split("/")
        source_identities.append(
            {
                # §3.2 SourceArtifact identity object — identity metadata plus
                # the raw byte hash, never the byte hash alone.
                "source_artifact_id": aid,
                "jurisdiction": "fi",
                "work_kind": "normative_act",
                "source_role": role,
                "canonical_id": f"{num}/{year}",
                "locator": f"sources/{raw_hash.removeprefix('sha256:')}.bin",
                "raw_source_hash": raw_hash,
            }
        )
    source_identities.sort(key=lambda r: r["source_artifact_id"])
    source_leaves = [leaf_hash(D_SOURCE_ARTIFACT, identity) for identity in source_identities]
    source_bundle_root = set_root(D_SOURCE_BUNDLE, source_leaves)
    base_artifact_id = artifact_id_by_engine_sid[engine_id]

    # --- trace: covering-state evolution per boundary date (§10 carve-out) ---
    ops_by_date = _index_ops_by_date(bundle.lo_ops)
    expiry_ops_by_date = _index_ops_by_expiry_date(bundle.lo_ops)

    prev_state: Dict[str, str] = {}
    states_by_date: Dict[str, Dict[str, str]] = {}
    blobs: Dict[str, Dict[str, Any]] = {}  # bare-hex structural hash -> §2.1 node json
    transition_rows: List[Dict[str, Any]] = []
    checkpoint_rows: List[Dict[str, Any]] = []
    op_transitions: Dict[str, List[str]] = {}
    seq = 0
    for date in boundary:
        tree = materialize_oracle_tree(bundle, date)
        units = covering_units(tree, "", granularity)
        cur_state: Dict[str, str] = {}
        cur_order: List[str] = []
        for addr, node in units:
            h = structural_subtree_hash(node)
            cur_state[addr] = h
            cur_order.append(addr)
            if h not in blobs:
                blobs[h] = node.to_jsonable_dict()
        states_by_date[date] = dict(cur_state)

        # §8.1 covering-state checkpoint hash (frozen byte recipe, reused from
        # the engine via reproducible_tree_hash's exact algorithm below in
        # verify; here computed through the same engine primitive).
        from lawvm.tools.export_transition_graph import reproducible_tree_hash

        tree_hash = reproducible_tree_hash(list(cur_state.items()))
        checkpoint_rows.append(
            {
                "date": date,
                "address_prefix": "",
                "tree_hash": "sha256:" + tree_hash,
                "active_unit_count": len(cur_state),
            }
        )

        ops_on_date = ops_by_date.get(date, [])
        expiring_on_date = expiry_ops_by_date.get(date, [])
        all_addrs = list(dict.fromkeys(list(prev_state.keys()) + cur_order))
        for addr in all_addrs:
            pre = prev_state.get(addr, "")
            post = cur_state.get(addr, "")
            if pre == post:
                continue
            seq += 1
            action = "delete_subtree" if post == "" else "set_subtree"
            transition_id = f"t{seq:06d}:{date}:{addr}"

            ops = _ops_for_covering(ops_on_date, addr)
            expiring = _ops_for_covering(expiring_on_date, addr)
            ref_sids: set[str] = set()
            for op in ops + expiring:
                src = op.source
                if src is not None and src.statute_id:
                    ref_sids.add(_engine_statute_id(src.statute_id))
            if pre == "":
                # First materialization carries the enacted base text; the
                # base statute is a driving instrument of the observed state.
                ref_sids.add(engine_id)
            source_refs = sorted(
                artifact_id_by_engine_sid[sid] for sid in ref_sids if sid in artifact_id_by_engine_sid
            )
            dropped = sorted(sid for sid in ref_sids if sid not in artifact_id_by_engine_sid)
            if dropped:
                raise BundleSpecError(
                    f"transition {transition_id} driven by unbundled source(s) {dropped}; "
                    "all source bytes must be bundled (§11.3)"
                )

            kind_set = {str(o.action) for o in ops}
            summaries = [_legal_op_summary(o) for o in ops[:3]]
            if expiring:
                kind_set.add("expiry")
                summaries.extend(f"expiry of {_legal_op_summary(o)}" for o in expiring[:3])
            flags: Dict[str, Any] = {}
            if post == "":
                flags["removed"] = True
            if pre == "" and post != "":
                flags["created"] = True
            if expiring and not ops:
                flags["temporary_expiry"] = True

            row = {
                # certified core (trace spec §5.1)
                "transition_id": transition_id,
                "sequence": seq,
                "effective_date": date,
                "action": action,
                "target_address": addr,
                "pre_hash": ("sha256:" + pre) if pre else "",
                "post_hash": ("sha256:" + post) if post else "",
                "payload_hash": ("sha256:" + post) if post else "",
                "source_refs": source_refs,
                # Experimental writer: state-diff-derived transitions carry no
                # byte anchors; every source_ref gets a
                # kind=source_anchor_unavailable residual (trace spec §7).
                "source_anchors": [],
                # display annotation (NOT hashed)
                "legal_op_kind": ",".join(sorted(kind_set)),
                "legal_op_summary": " | ".join(summaries[:4]),
                "preparatory_refs": [],
                "expires_date": "",
                "flags": flags,
            }
            transition_rows.append(row)
            for op in ops + expiring:
                op_transitions.setdefault(op.op_id, []).append(transition_id)
        prev_state = cur_state

    transition_leaves = [leaf_hash(D_TRANSITION, _certified_core(r)) for r in transition_rows]
    certified_tree_transition_root = list_root(D_TRACE, transition_leaves)

    blob_rows = [
        {"content_hash": "sha256:" + h, "content_json": blobs[h]} for h in sorted(blobs)
    ]
    content_blobs_root = set_root(
        D_CONTENT_BLOBS, [leaf_hash(D_CONTENT_BLOB, row) for row in blob_rows]
    )

    base_tree = {
        # Trace spec §3: the Finland exporter family starts from an EMPTY
        # covering state; the first change date's transitions establish it.
        "schema": D_BASE_TREE,
        "work_id": f"fi:act:{canonical_id}",
        "jurisdiction": "fi",
        "slice_prefix": "",
        "granularity": granularity,
        "units": [],
    }
    base_tree_root = leaf_hash(D_BASE_TREE, base_tree)
    materialization_root = list_root(
        D_MATERIALIZATION, [leaf_hash(D_STATE_ROOT, row) for row in checkpoint_rows]
    )
    change_dates_root = set_root(D_CHANGE_DATES, boundary)

    # --- policy manifests (§3.5) ---
    from lawvm.core.compile_metadata import compute_strict_profile_fingerprint
    from lawvm.finland.strict_profile import default_finland_strict_profile

    engine_profile = default_finland_strict_profile()
    profile_fields = {
        f.name: getattr(engine_profile, f.name) for f in dataclasses.fields(engine_profile)
    }
    profile_manifest = {
        "schema": D_STRICT_PROFILE,
        "profile_id": PROFILE_ID,
        "engine_profile": profile_fields,
        "engine_profile_fingerprint": "sha256:" + compute_strict_profile_fingerprint(engine_profile),
    }
    profile_hash = leaf_hash(D_STRICT_PROFILE, profile_manifest)

    # SPEC-NOTE §3.5: the engine has no reified interpretation-policy object
    # for lawvm.fi.default.v1 (only an unused fingerprint hook) — an explicit
    # checker-v0 non-goal. The manifest pins the interpretation parameters
    # this bundle was emitted under (the §3.5 policy-manifest minimum).
    policy_manifest = {
        "schema": D_INTERPRETATION_POLICY,
        "policy_id": POLICY_ID,
        "parameters": {
            "jurisdiction": "fi",
            "query_type": "governing",
            "granularity": granularity,
            "synthesize_repeal_placeholders": True,
            "fixed_term_statute_bounds": "default_on",
            "selection": "overlay_rail_over_background; latest (effective, enacted) within rail",
        },
    }
    policy_hash = leaf_hash(D_INTERPRETATION_POLICY, policy_manifest)

    seam_spec_hash = _seam_spec_hash()
    projection_specs_manifest = {
        "schema": D_PROJECTION_SPECS,
        "projections": {
            "seam": {
                "schema": SEAM_SCHEMA,
                "spec_version": SEAM_SPEC_VERSION,
                "spec_hash": seam_spec_hash,
                # §3.4/§3.5: pinned run-provenance exclusion list for the
                # projection-hash input.
                "hash_excluded_members": list(SEAM_HASH_EXCLUDED_MEMBERS),
            }
        },
    }
    projection_specs_hash = leaf_hash(D_PROJECTION_SPECS, projection_specs_manifest)

    registry_rows = build_diagnostic_registry_rows(profile_fields)
    diagnostic_registry_manifest = {"schema": D_DIAGNOSTIC_REGISTRY, "rows": registry_rows}
    diagnostic_registry_hash = leaf_hash(D_DIAGNOSTIC_REGISTRY, diagnostic_registry_manifest)
    registered_codes = frozenset(r["code"] for r in registry_rows)
    disposition_by_code = {r["code"]: r["profile_disposition"][PROFILE_ID] for r in registry_rows}

    checker_contract = {"checker_version": CHECKER_VERSION, "hash_profile": HASH_PROFILE}
    checker_contract_manifest = {"schema": D_CHECKER_CONTRACT, **checker_contract}
    checker_contract_hash = leaf_hash(D_CHECKER_CONTRACT, checker_contract_manifest)

    # --- findings ledger (§5.7) ---
    finding_rows: List[Dict[str, Any]] = []
    seen_finding_ids: set[str] = set()
    full_range: List[Optional[str]] = [time_scope["from"], time_scope["to"]]

    def _add_finding(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if row["finding_id"] in seen_finding_ids:
            # Identical rows collapse under set semantics (§3.1.1 forbids
            # duplicate leaves).
            return None
        seen_finding_ids.add(row["finding_id"])
        finding_rows.append(row)
        return row

    finding_for_residual: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for finding in bundle.result.findings:
        if finding.kind not in registered_codes:
            raise BundleSpecError(
                f"replay finding kind {finding.kind!r} not in the pinned diagnostic registry"
            )
        src_refs = []
        if finding.source_statute:
            sid = _engine_statute_id(finding.source_statute)
            if sid in artifact_id_by_engine_sid:
                src_refs = [artifact_id_by_engine_sid[sid]]
        row = _finding_row(
            diagnostic_code=finding.kind,
            role=finding.role,
            blocking=bool(finding.blocking),
            address=None,
            date_range=list(full_range),
            source_refs=src_refs,
            phase=finding.stage,
            detail=finding.detail,
        )
        added = _add_finding(row)
        if added is not None and finding.blocking:
            finding_for_residual.append((added, dict(finding.detail)))

    for diagnostic in extraction.diagnostics:
        spec = FINDING_REGISTRY.get(diagnostic.code)
        if spec is None:
            raise BundleSpecError(
                f"fixed-term diagnostic code {diagnostic.code!r} not in the engine registry"
            )
        blocking = spec.role == "obligation" and spec.default_enforcement in ("hard_fail", "strict_fail")
        row = _finding_row(
            diagnostic_code=diagnostic.code,
            role="obligation" if spec.role == "obligation" else "observation",
            blocking=blocking,
            address=diagnostic.address or None,
            date_range=[diagnostic.effective or time_scope["from"], None],
            source_refs=[base_artifact_id],
            phase="fixed_term_expiry",
            detail={"detail": diagnostic.detail, "clause_text": diagnostic.clause_text},
        )
        added = _add_finding(row)
        if added is not None and blocking:
            finding_for_residual.append(
                (added, {"source_text": diagnostic.clause_text, "message": diagnostic.detail})
            )

    # --- residual ledger (§5.4, §5.6) ---
    residual_rows: List[Dict[str, Any]] = []
    seen_residual_ids: set[str] = set()

    def _add_residual(row: Dict[str, Any]) -> None:
        if row["residual_id"] in seen_residual_ids:
            return
        seen_residual_ids.add(row["residual_id"])
        residual_rows.append(row)

    for finding_row, detail in finding_for_residual:
        code = finding_row["diagnostic_code"]
        derived = disposition_by_code.get(code)
        if derived != "blocks":
            raise BundleSpecError(
                f"blocking finding {code!r} maps to registry disposition {derived!r}; an "
                "emitter must never soften a blocking finding (§5.4)"
            )
        kind = _residual_kind_for_code(code, FINDING_REGISTRY.get(code))
        source_text = str(
            detail.get("source_text") or detail.get("clause_text") or detail.get("message") or ""
        )
        if kind == "expiry_unverified" and not source_text:
            raise BundleSpecError(
                f"expiry_unverified residual for {code!r} lacks self-evidencing source_text (§5.4)"
            )
        _add_residual(
            _residual_row(
                kind=kind,
                diagnostic_code=code,
                role=finding_row["role"],
                blocking=True,
                address=finding_row["scope"]["address"],
                date_range=list(finding_row["scope"]["date_range"]),
                source_text=source_text,
                # §5.4: rule_id required where the producing surface carries
                # one; "" has fixed semantics — no grammar-family attribution
                # exists (FixedTermDiagnostic fails before family selection).
                rule_id=str(detail.get("rule_id") or ""),
                source_refs=list(finding_row["source_refs"]),
                finding_refs=[finding_row["finding_id"]],
                profile_effect={PROFILE_ID: "blocks"},
            )
        )

    # Trace spec §7: every source_ref without an anchor needs a
    # kind=source_anchor_unavailable residual naming the transition and ref.
    for row in transition_rows:
        for ref in row["source_refs"]:
            _add_residual(
                _residual_row(
                    kind="source_anchor_unavailable",
                    diagnostic_code=SOURCE_ANCHOR_UNAVAILABLE_CODE,
                    role="obligation",
                    blocking=False,
                    address=row["target_address"],
                    date_range=[row["effective_date"], None],
                    source_text="",
                    rule_id="",
                    source_refs=[ref],
                    finding_refs=[],
                    profile_effect={PROFILE_ID: disposition_by_code[SOURCE_ANCHOR_UNAVAILABLE_CODE]},
                    extra={"transition_id": row["transition_id"]},
                )
            )

    residual_root = set_root(
        D_RESIDUAL_LEDGER, [leaf_hash(D_RESIDUAL_LEDGER, row) for row in residual_rows]
    )
    finding_root = set_root(
        D_FINDING_LEDGER, [leaf_hash(D_FINDING_LEDGER, row) for row in finding_rows]
    )

    # --- seam projection rows (§3.4, §5.5) ---
    from lawvm.tools.provision_state import build_provision_state_response

    migration_events = tuple(bundle.result.products.migration_events)
    intervals: List[Tuple[str, Optional[str]]] = [
        (boundary[i], boundary[i + 1] if i + 1 < len(boundary) else None)
        for i in range(len(boundary))
    ]
    seam_entries: List[Dict[str, Any]] = []  # wrapper rows, parentage filled later
    projection_hashes: List[str] = []
    blocked_row_count = 0
    qualified_row_count = 0
    certification_statuses: List[str] = []
    for start, end in intervals:
        for addr in sorted(states_by_date[start]):
            payload = build_provision_state_response(
                timelines=bundle.timelines,
                migration_events=migration_events,
                statute_id=canonical_id,
                jurisdiction="fi",
                provision=addr,
                as_of=start,
                query_type="governing",
                territory=None,
                title=bundle.title,
            )
            # §3.4: only the payload's hash view is hashed (run-provenance
            # excluded); parentage is a wrapper member.
            projection_hash = projection_payload_hash(payload, SEAM_HASH_EXCLUDED_MEMBERS)
            projection_hashes.append(projection_hash)
            certification_status = certification_status_for_row(
                payload["status"],
                row_address=addr,
                row_interval=(start, end),
                residual_rows=residual_rows,
            )
            certification_statuses.append(certification_status)
            if certification_status == "blocked":
                blocked_row_count += 1
            if certification_status == "qualified":
                qualified_row_count += 1
            seam_entries.append(
                {
                    "projection_payload": payload,
                    "certification_status": certification_status,
                    "universe": {"address": addr, "interval": [start, end]},
                    "_projection_hash": projection_hash,
                }
            )
    seam_projection_root = set_root(D_PROJECTION_SEAM, projection_hashes)
    projection_root_preimage = {
        "seam": seam_projection_root,
        "dump": None,
        "transition_graph": None,
    }
    projection_root = leaf_hash(D_PROJECTION_ROOT, projection_root_preimage)

    # --- coverage artifacts (§4.1, §5.7; declared-coverage-only boundary) ---
    source_unit_rows: List[Dict[str, Any]] = []
    for identity in source_identities:
        aid = identity["source_artifact_id"]
        data = source_blobs[aid]
        if not any(aid in r["source_refs"] for r in transition_rows):
            raise BundleSpecError(
                f"coverage row for {aid} would claim compiled with no transition source-ref (§5.7)"
            )
        source_unit_rows.append(
            {
                # Document-granularity declared coverage: the writer enumerates
                # whole source artifacts, not intra-document units (§4.1 makes
                # declared coverage a committed claim, not a completeness one).
                "source_unit_id": f"{aid}:document",
                "source_anchor": {
                    "source_artifact_id": aid,
                    "locator": identity["locator"],
                    "span_unit": "byte",
                    "span": [0, len(data)],
                    "quote_hash": _sha256_rendered(data),
                },
                "classification": "operative",
                "status": "compiled",
                "refs": [],
            }
        )
    potential_op_rows: List[Dict[str, Any]] = []
    for op in bundle.lo_ops:
        src = op.source
        sid = _engine_statute_id(src.statute_id) if src is not None and src.statute_id else engine_id
        aid = artifact_id_by_engine_sid.get(sid, base_artifact_id)
        data = source_blobs[aid]
        refs = sorted(set(op_transitions.get(op.op_id, [])))
        if not refs:
            notes.append(
                f"L2 op {op.op_id!r} produced no covering-state diff; declared as 'suppressed' "
                "in potential_operation_coverage"
            )
        potential_op_rows.append(
            {
                "potential_operation_id": op.op_id,
                "source_anchor": {
                    "source_artifact_id": aid,
                    "locator": next(
                        i["locator"] for i in source_identities if i["source_artifact_id"] == aid
                    ),
                    "span_unit": "byte",
                    "span": [0, len(data)],
                    "quote_hash": _sha256_rendered(data),
                },
                "classification": "compiled" if refs else "suppressed",
                "refs": refs,
                "action": str(op.action),
                "target": str(op.target) if op.target is not None else "",
            }
        )
    source_unit_coverage_root = set_root(
        D_SOURCE_UNIT_COVERAGE, [leaf_hash(D_SOURCE_UNIT_COVERAGE, r) for r in source_unit_rows]
    )
    potential_op_coverage_root = set_root(
        D_POTENTIAL_OP_COVERAGE, [leaf_hash(D_POTENTIAL_OP_COVERAGE, r) for r in potential_op_rows]
    )
    coverage_root = leaf_hash(
        D_COVERAGE,
        {
            "source_unit_coverage": source_unit_coverage_root,
            "potential_operation_coverage": potential_op_coverage_root,
        },
    )

    # --- residual summary + certificate status (§5.1, §5.2 — computed) ---
    by_kind: Dict[str, int] = {}
    blocking_count = qualified_count = observation_count = frontier_count = 0
    for row in residual_rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        effect = residual_effect(row, PROFILE_ID)
        if effect == "blocks":
            blocking_count += 1
        elif effect == "qualifies":
            qualified_count += 1
        else:
            observation_count += 1
        if row["kind"] == "manual_frontier":
            frontier_count += 1
    residual_summary = {
        "blocking_count": blocking_count,
        "qualified_count": qualified_count,
        "observation_count": observation_count,
        "frontier_count": frontier_count,
        "by_kind": dict(sorted(by_kind.items())),
    }
    certificate_status = compute_certificate_status(
        residual_rows=residual_rows,
        certification_statuses=certification_statuses,
        registered_codes=registered_codes,
    )
    projection_coverage = {
        "seam": {
            "universe_kind": "all_address_interval_states",
            "address_source": "materialization.covering_states",
            "interval_source": "time_axis.boundary_dates",
            "row_count": len(seam_entries),
            "omitted_row_count": 0,
            "blocked_row_count": blocked_row_count,
        }
    }

    # --- artifacts manifest (§4, exhaustive; absent families explicit null) ---
    artifacts = {
        "source_bundle": {
            "schema": D_SOURCE_BUNDLE,
            "root": source_bundle_root,
            "locator": "sources/",
        },
        # §4: REQUIRED index of §3.2 SourceArtifact identity rows; its root
        # IS source_bundle_root (no new root — index and bundle cannot drift).
        "source_artifact_index": {
            "schema": "lawvm.source_artifact_index.v0",
            "root": source_bundle_root,
            "locator": "sources/source_artifacts.json",
        },
        "profile_manifest": {
            "schema": D_STRICT_PROFILE,
            "root": profile_hash,
            "locator": "policy/strict_profile.json",
        },
        "interpretation_policy_manifest": {
            "schema": D_INTERPRETATION_POLICY,
            "root": policy_hash,
            "locator": "policy/interpretation_policy.json",
        },
        "projection_spec_manifest": {
            "schema": D_PROJECTION_SPECS,
            "root": projection_specs_hash,
            "locator": "policy/projection_specs.json",
        },
        "diagnostic_registry_manifest": {
            "schema": D_DIAGNOSTIC_REGISTRY,
            "root": diagnostic_registry_hash,
            "locator": "policy/diagnostic_registry.json",
        },
        "checker_contract_manifest": {
            "schema": D_CHECKER_CONTRACT,
            "root": checker_contract_hash,
            "locator": "policy/checker_contract.json",
        },
        "base_tree": {
            "schema": D_BASE_TREE,
            "root": base_tree_root,
            "locator": "materialization/base_tree.json",
        },
        "certified_tree_transition_trace": {
            "schema": D_TRACE,
            "root": certified_tree_transition_root,
            "locator": "trace/certified_tree_transitions.jsonl",
        },
        "content_blobs": {
            "schema": D_CONTENT_BLOBS,
            "root": content_blobs_root,
            "locator": "materialization/content_blobs.jsonl",
        },
        "materialization_index": {
            "schema": D_MATERIALIZATION,
            "root": materialization_root,
            "locator": "materialization/state_roots.jsonl",
        },
        "seam_projection_rows": {
            "schema": SEAM_SCHEMA,
            "root": seam_projection_root,
            "locator": "projections/seam_rows.jsonl",
        },
        "dump_projection_rows": None,
        "transition_graph_projection_rows": None,
        "residual_ledger": {
            "schema": D_RESIDUAL_LEDGER,
            "root": residual_root,
            "locator": "residue/residuals.jsonl",
        },
        "finding_ledger": {
            "schema": D_FINDING_LEDGER,
            "root": finding_root,
            "locator": "residue/findings.jsonl",
        },
        "source_unit_coverage": {
            "schema": D_SOURCE_UNIT_COVERAGE,
            "root": source_unit_coverage_root,
            "locator": "coverage/source_unit_coverage.jsonl",
        },
        "potential_operation_coverage": {
            "schema": D_POTENTIAL_OP_COVERAGE,
            "root": potential_op_coverage_root,
            "locator": "coverage/potential_operation_coverage.jsonl",
        },
    }

    roots = {
        "source_bundle_root": source_bundle_root,
        "base_tree_root": base_tree_root,
        "certified_tree_transition_root": certified_tree_transition_root,
        "content_blobs_root": content_blobs_root,
        "materialization_root": materialization_root,
        "projection_root": projection_root,
        "residual_root": residual_root,
        "finding_root": finding_root,
        "coverage_root": coverage_root,
    }

    envelope: Dict[str, Any] = {
        "schema": CERTIFICATE_SCHEMA,
        "claim_kind": "legal_work_temporal_text_state",
        "subject": {
            "jurisdiction": "fi",
            "work_id": f"fi:act:{canonical_id}",
            "work_kind": "normative_act",
            "local_id": canonical_id,
            "legacy_statute_id": canonical_id,
        },
        "scope": {"kind": "whole_work", "addresses": []},
        "time_scope": time_scope,
        "profile": {"profile_id": PROFILE_ID, "profile_hash": profile_hash},
        "interpretation_policy": {"policy_id": POLICY_ID, "policy_hash": policy_hash},
        "time_axis": {
            "change_dates_root": change_dates_root,
            "min_date": boundary[0],
            "max_date": boundary[-1],
        },
        "roots": roots,
        "certificate_status": certificate_status,
        "residual_summary": residual_summary,
        "projection_coverage": projection_coverage,
        "artifacts": artifacts,
        "checker_contract": checker_contract,
    }
    # §3.3: certificate_root commits to the COMPLETE envelope minus
    # certificate_id; certificate_id is derived from it.
    certificate_root = leaf_hash(D_CERT_ROOT, envelope)
    certificate_id = certificate_root
    envelope_with_id = dict(envelope)
    envelope_with_id["certificate_id"] = certificate_id

    # --- write the bundle ---
    out_path.mkdir(parents=True, exist_ok=True)
    _write_json(out_path / "certificate.json", envelope_with_id)
    for identity in source_identities:
        blob_path = out_path / identity["locator"]
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(source_blobs[identity["source_artifact_id"]])
    _write_json(out_path / "sources" / "source_artifacts.json", source_identities)
    _write_json(out_path / "policy" / "strict_profile.json", profile_manifest)
    _write_json(out_path / "policy" / "interpretation_policy.json", policy_manifest)
    _write_json(out_path / "policy" / "projection_specs.json", projection_specs_manifest)
    _write_json(out_path / "policy" / "diagnostic_registry.json", diagnostic_registry_manifest)
    _write_json(out_path / "policy" / "checker_contract.json", checker_contract_manifest)
    _write_jsonl(out_path / "trace" / "certified_tree_transitions.jsonl", transition_rows)
    (out_path / "trace" / "certified_tree_transitions.root").write_text(
        certified_tree_transition_root + "\n", encoding="utf-8"
    )
    _write_json(out_path / "materialization" / "base_tree.json", base_tree)
    _write_jsonl(out_path / "materialization" / "content_blobs.jsonl", blob_rows)
    _write_jsonl(out_path / "materialization" / "state_roots.jsonl", checkpoint_rows)
    wrapper_rows: List[Dict[str, Any]] = []
    for entry in seam_entries:
        wrapper_rows.append(
            {
                "projection_payload": entry["projection_payload"],
                "certification_status": entry["certification_status"],
                "universe": entry["universe"],
                "certificate": {
                    "certificate_id": certificate_id,
                    "certificate_root": certificate_root,
                    "projection_kind": "lawvm.provision_state",
                    "projection_schema": SEAM_SCHEMA,
                    "projection_spec_version": SEAM_SPEC_VERSION,
                    "projection_spec_hash": seam_spec_hash,
                    "projection_hash": entry["_projection_hash"],
                    "inclusion_path": ["projections/seam_rows.jsonl"],
                },
            }
        )
    _write_jsonl(out_path / "projections" / "seam_rows.jsonl", wrapper_rows)
    _write_jsonl(out_path / "residue" / "residuals.jsonl", residual_rows)
    _write_jsonl(out_path / "residue" / "findings.jsonl", finding_rows)
    _write_jsonl(out_path / "coverage" / "source_unit_coverage.jsonl", source_unit_rows)
    _write_jsonl(out_path / "coverage" / "potential_operation_coverage.jsonl", potential_op_rows)

    # Writer-side self-check: recompute every committed root from the bundle
    # files independently. Not a checker; raises on writer inconsistency.
    verify_bundle(out_path)

    # Register the bundle as a taint-checkable build (consumed_by_build
    # contract): the edges/record live in the persistent provenance graph,
    # AFTER artifact emission, never inside the certificate root (no
    # certificate_root <-> graph cycle).  Recorder failure propagates and
    # fails the emission — the artifact is then not considered published.
    import os

    from lawvm.core.build_consumption import record_build_in_store
    from lawvm.core.provenance_graph import ArtifactRef
    from lawvm.core.provenance_graph_storage import GraphStore

    resolved_graph_root = Path(
        graph_store_root
        or os.environ.get("LAWVM_GRAPH_STORE_ROOT")
        or "data/fi/v1/provenance_graph"
    )
    build_ref = record_build_in_store(
        GraphStore(resolved_graph_root),
        artifact_ref=ArtifactRef(
            artifact_type="certificate_bundle",
            artifact_id=certificate_id,
            content_hash=certificate_root,
        ),
        build_kind="cert",
        # Versioned schema string: "lawvm.certificate.v" + spec version
        # (CERTIFICATE_SCHEMA's bare major "v0" is subsumed by "v0.4.1").
        build_schema=f"lawvm.certificate.v{CERTIFICATE_SPEC_VERSION}",
        consumed_assertion_ids=(),  # the experimental writer admits no manual-claim assertions
        profile_fingerprint=profile_hash,
        source_bundle_hash=source_bundle_root,
        scope={"jurisdiction": "fi", "work_id": f"fi:act:{canonical_id}", "kind": "whole_work"},
        time_scope=dict(time_scope),
    )

    if not quiet:
        for note in notes:
            print(f"[certificate-bundle] note: {note}", flush=True)

    return BundleWriteResult(
        bundle_dir=str(out_path),
        certificate_id=certificate_id,
        build_id=build_ref.build_id,
        certificate_status=certificate_status,
        statute_id=canonical_id,
        title=bundle.title,
        boundary_dates=boundary,
        transition_count=len(transition_rows),
        seam_row_count=len(wrapper_rows),
        residual_count=len(residual_rows),
        finding_count=len(finding_rows),
        roots=dict(roots, certificate_root=certificate_root),
        writer_notes=notes,
    )


# ---------------------------------------------------------------------------
# Writer-side self-check (independent root recomputation; NOT checker v0)
# ---------------------------------------------------------------------------


def _vf_structural_hash(node: Mapping[str, Any]) -> str:
    """Independent §2.2 structural-hash recompute from a content_json dict."""
    h = hashlib.sha256()

    def _rec(n: Mapping[str, Any]) -> None:
        h.update(str(n.get("kind") or "").encode("utf-8"))
        h.update(b"\x00")
        h.update(str(n.get("label") or "").encode("utf-8"))
        h.update(b"\x00")
        h.update(str(n.get("text") or "").encode("utf-8"))
        h.update(b"\x01")
        for child in n.get("children") or []:
            _rec(child)
        h.update(b"\x02")

    _rec(node)
    return h.hexdigest()


def _vf_covering_state_hash(state: Mapping[str, str]) -> str:
    """Independent §8.1 covering-state hash recompute (bare-hex values)."""
    h = hashlib.sha256()
    for addr in sorted(state):
        h.update(addr.encode("utf-8"))
        h.update(b"\x00")
        h.update(state[addr].encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleSelfCheckError(message)


def verify_bundle(bundle_dir: str | Path) -> Dict[str, str]:
    """Recompute every committed root from bundle contents and compare.

    Writer-side self-check ONLY. This function asserts the writer's internal
    consistency (roots, status algebra, coverage universes recompute from the
    emitted artifacts). It is NOT checker v0, performs no trace-precondition
    replay against claims, and never produces a verdict — a passing
    self-check does not make the bundle a checked certificate.
    """
    bundle_path = Path(bundle_dir)
    envelope = json.loads((bundle_path / "certificate.json").read_text(encoding="utf-8"))
    roots: Dict[str, str] = envelope["roots"]
    artifacts = envelope["artifacts"]
    recomputed: Dict[str, str] = {}

    # sources
    identities = json.loads(
        (bundle_path / artifacts["source_artifact_index"]["locator"]).read_text(encoding="utf-8")
    )
    for identity in identities:
        data = (bundle_path / identity["locator"]).read_bytes()
        _require(
            _sha256_rendered(data) == identity["raw_source_hash"],
            f"raw_source_hash mismatch for {identity['source_artifact_id']}",
        )
    recomputed["source_bundle_root"] = set_root(
        D_SOURCE_BUNDLE, [leaf_hash(D_SOURCE_ARTIFACT, identity) for identity in identities]
    )

    # base tree
    base_tree = json.loads(
        (bundle_path / artifacts["base_tree"]["locator"]).read_text(encoding="utf-8")
    )
    recomputed["base_tree_root"] = leaf_hash(D_BASE_TREE, base_tree)

    # content blobs: structural hashes recompute from content_json
    blob_rows = _read_jsonl(bundle_path / artifacts["content_blobs"]["locator"])
    blob_hashes: set[str] = set()
    for row in blob_rows:
        bare = row["content_hash"].removeprefix("sha256:")
        _require(
            _vf_structural_hash(row["content_json"]) == bare,
            f"content blob {row['content_hash']} does not recompute from content_json",
        )
        blob_hashes.add(bare)
    recomputed["content_blobs_root"] = set_root(
        D_CONTENT_BLOBS, [leaf_hash(D_CONTENT_BLOB, row) for row in blob_rows]
    )

    # trace: leaf/list roots over the certified core, ordering rules
    transition_rows = _read_jsonl(
        bundle_path / artifacts["certified_tree_transition_trace"]["locator"]
    )
    leaves = []
    prev_seq = 0
    prev_date = ""
    for row in transition_rows:
        _require(row["sequence"] > prev_seq, f"sequence not strictly increasing at {row['transition_id']}")
        _require(
            row["effective_date"] >= prev_date,
            f"effective_date decreasing at {row['transition_id']}",
        )
        prev_seq, prev_date = row["sequence"], row["effective_date"]
        leaves.append(leaf_hash(D_TRANSITION, _certified_core(row)))
    recomputed["certified_tree_transition_root"] = list_root(D_TRACE, leaves)
    root_file = (bundle_path / "trace" / "certified_tree_transitions.root").read_text(
        encoding="utf-8"
    ).strip()
    _require(root_file == recomputed["certified_tree_transition_root"], "trace .root file mismatch")

    # fold the trace from the base tree; recompute checkpoints + universe
    checkpoint_rows = _read_jsonl(bundle_path / artifacts["materialization_index"]["locator"])
    base_units = {u["address"]: u["content_hash"].removeprefix("sha256:") for u in base_tree["units"]}
    state: Dict[str, str] = dict(base_units)
    transitions_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in transition_rows:
        transitions_by_date.setdefault(row["effective_date"], []).append(row)
    boundary = [row["date"] for row in checkpoint_rows]
    _require(boundary == sorted(boundary), "checkpoint rows not in date order")
    declared_dates = set(boundary)
    for row in transition_rows:
        _require(
            row["effective_date"] in declared_dates,
            f"transition {row['transition_id']} effective_date outside declared change dates",
        )
    states_by_date: Dict[str, Dict[str, str]] = {}
    for checkpoint in checkpoint_rows:
        date = checkpoint["date"]
        batch = transitions_by_date.get(date, [])
        touched: set[str] = set()
        for row in batch:
            addr = row["target_address"]
            _require(addr not in touched, f"duplicate target {addr} in date-batch {date}")
            touched.add(addr)
            pre = row["pre_hash"].removeprefix("sha256:") if row["pre_hash"] else ""
            _require(
                state.get(addr, "") == pre,
                f"pre_hash mismatch folding {row['transition_id']}",
            )
            if row["action"] == "set_subtree":
                post = row["post_hash"].removeprefix("sha256:")
                _require(post != "", f"set_subtree with empty post_hash at {row['transition_id']}")
                _require(
                    row["payload_hash"] == row["post_hash"],
                    f"payload_hash != post_hash at {row['transition_id']}",
                )
                _require(post in blob_hashes, f"payload blob missing for {row['transition_id']}")
                state[addr] = post
            elif row["action"] == "delete_subtree":
                _require(row["post_hash"] == "", f"delete_subtree with post_hash at {row['transition_id']}")
                state.pop(addr, None)
            else:
                raise BundleSelfCheckError(f"unknown action {row['action']!r} at {row['transition_id']}")
        _require(
            "sha256:" + _vf_covering_state_hash(state) == checkpoint["tree_hash"],
            f"checkpoint tree_hash mismatch at {date}",
        )
        _require(
            checkpoint["active_unit_count"] == len(state),
            f"checkpoint active_unit_count mismatch at {date}",
        )
        states_by_date[date] = dict(state)
    recomputed["materialization_root"] = list_root(
        D_MATERIALIZATION, [leaf_hash(D_STATE_ROOT, row) for row in checkpoint_rows]
    )

    # time axis
    recomputed_change_dates_root = set_root(D_CHANGE_DATES, boundary)
    _require(
        recomputed_change_dates_root == envelope["time_axis"]["change_dates_root"],
        "change_dates_root mismatch",
    )
    _require(
        envelope["time_axis"]["min_date"] == boundary[0]
        and envelope["time_axis"]["max_date"] == boundary[-1],
        "time_axis min/max do not match committed boundary dates",
    )
    time_scope = envelope["time_scope"]
    _require(time_scope["kind"] == "closed_interval", "experimental writer requires closed_interval")
    _require(
        time_scope["from"] <= boundary[0] and boundary[-1] <= time_scope["to"],
        "boundary dates outside time_scope",
    )

    # seam projection rows: payload hashes, family root, projection_root,
    # parentage consistency, universe reconciliation (§5.5)
    # §3.4: recompute projection hashes under the bundle's OWN pinned
    # hash_excluded_members, never a hardcoded table.
    projection_specs = json.loads(
        (bundle_path / artifacts["projection_spec_manifest"]["locator"]).read_text(
            encoding="utf-8"
        )
    )
    seam_excluded = projection_specs["projections"]["seam"]["hash_excluded_members"]
    wrapper_rows = _read_jsonl(bundle_path / artifacts["seam_projection_rows"]["locator"])
    projection_hashes = []
    emitted_universe: set[Tuple[str, str]] = set()
    certification_statuses: List[str] = []
    for wrapper in wrapper_rows:
        payload = wrapper["projection_payload"]
        projection_hash = projection_payload_hash(payload, seam_excluded)
        parentage = wrapper["certificate"]
        _require(
            parentage["projection_hash"] == projection_hash,
            "parentage projection_hash does not recompute from payload",
        )
        _require(
            parentage["certificate_id"] == envelope["certificate_id"]
            and parentage["certificate_root"] == envelope["certificate_id"],
            "parentage does not reference this certificate",
        )
        projection_hashes.append(projection_hash)
        universe = wrapper["universe"]
        emitted_universe.add((universe["address"], universe["interval"][0]))
        certification_statuses.append(wrapper["certification_status"])
    recomputed_seam_root = set_root(D_PROJECTION_SEAM, projection_hashes)
    _require(
        artifacts["seam_projection_rows"]["root"] == recomputed_seam_root,
        "seam projection root mismatch",
    )
    _require(
        artifacts["dump_projection_rows"] is None
        and artifacts["transition_graph_projection_rows"] is None,
        "experimental writer emits only the seam family",
    )
    recomputed["projection_root"] = leaf_hash(
        D_PROJECTION_ROOT,
        {"seam": recomputed_seam_root, "dump": None, "transition_graph": None},
    )

    # universe reconciliation: row_count + omitted == recomputed universe size
    universe_pairs: set[Tuple[str, str]] = set()
    for date in boundary:
        for addr in states_by_date[date]:
            universe_pairs.add((addr, date))
    coverage_decl = envelope["projection_coverage"]["seam"]
    _require(
        coverage_decl["row_count"] + coverage_decl["omitted_row_count"] == len(universe_pairs),
        f"projection coverage mismatch: rows {coverage_decl['row_count']} + omitted "
        f"{coverage_decl['omitted_row_count']} != universe {len(universe_pairs)}",
    )
    _require(coverage_decl["row_count"] == len(wrapper_rows), "row_count != emitted rows")
    _require(emitted_universe == universe_pairs, "emitted universe differs from recomputed universe")
    _require(
        coverage_decl["blocked_row_count"]
        == sum(1 for s in certification_statuses if s == "blocked"),
        "blocked_row_count mismatch",
    )

    # residue + findings
    residual_rows = _read_jsonl(bundle_path / artifacts["residual_ledger"]["locator"])
    finding_rows = _read_jsonl(bundle_path / artifacts["finding_ledger"]["locator"])
    recomputed["residual_root"] = set_root(
        D_RESIDUAL_LEDGER, [leaf_hash(D_RESIDUAL_LEDGER, row) for row in residual_rows]
    )
    recomputed["finding_root"] = set_root(
        D_FINDING_LEDGER, [leaf_hash(D_FINDING_LEDGER, row) for row in finding_rows]
    )
    registry = json.loads(
        (bundle_path / artifacts["diagnostic_registry_manifest"]["locator"]).read_text(
            encoding="utf-8"
        )
    )
    registry_rows = registry["rows"]
    registered_codes = frozenset(row["code"] for row in registry_rows)
    registry_by_code = {row["code"]: row for row in registry_rows}
    for row in residual_rows:
        code = row["diagnostic_code"]
        _require(code in registered_codes, f"residual carries unregistered code {code!r}")
        _require(
            row["kind"] in registry_by_code[code]["allowed_residual_kinds"],
            f"residual kind {row['kind']!r} not allowed for code {code!r}",
        )
        # §5.4: profile_effect is derived; the cached copy must equal the
        # registry-derived disposition.
        _require(
            row["profile_effect"].get(PROFILE_ID)
            == registry_by_code[code]["profile_disposition"][PROFILE_ID],
            f"residual profile_effect for {code!r} disagrees with the pinned registry",
        )
    # §5.6: every blocking finding has a residual recording its disposition.
    residual_finding_refs = {ref for row in residual_rows for ref in row["finding_refs"]}
    for row in finding_rows:
        if row["blocking"]:
            _require(
                row["finding_id"] in residual_finding_refs,
                f"blocking finding {row['diagnostic_code']} has no residual row (§5.6)",
            )

    # coverage roots
    source_unit_rows = _read_jsonl(bundle_path / artifacts["source_unit_coverage"]["locator"])
    potential_op_rows = _read_jsonl(
        bundle_path / artifacts["potential_operation_coverage"]["locator"]
    )
    identity_by_id = {identity["source_artifact_id"]: identity for identity in identities}
    for cov_row in source_unit_rows + potential_op_rows:
        anchor = cov_row["source_anchor"]
        identity = identity_by_id.get(anchor["source_artifact_id"])
        _require(identity is not None, f"coverage anchor names unbundled {anchor['source_artifact_id']}")
        data = (bundle_path / anchor["locator"]).read_bytes()
        start, end = anchor["span"]
        _require(
            anchor["span_unit"] == "byte" and 0 <= start <= end <= len(data),
            "coverage anchor span outside source bytes",
        )
        _require(
            _sha256_rendered(data[start:end]) == anchor["quote_hash"],
            "coverage anchor quote_hash mismatch",
        )
    src_cov_root = set_root(
        D_SOURCE_UNIT_COVERAGE, [leaf_hash(D_SOURCE_UNIT_COVERAGE, r) for r in source_unit_rows]
    )
    op_cov_root = set_root(
        D_POTENTIAL_OP_COVERAGE, [leaf_hash(D_POTENTIAL_OP_COVERAGE, r) for r in potential_op_rows]
    )
    recomputed["coverage_root"] = leaf_hash(
        D_COVERAGE,
        {"source_unit_coverage": src_cov_root, "potential_operation_coverage": op_cov_root},
    )

    # policy manifest hashes
    for key, domain, envelope_hash in (
        ("profile_manifest", D_STRICT_PROFILE, envelope["profile"]["profile_hash"]),
        (
            "interpretation_policy_manifest",
            D_INTERPRETATION_POLICY,
            envelope["interpretation_policy"]["policy_hash"],
        ),
        ("projection_spec_manifest", D_PROJECTION_SPECS, None),
        ("diagnostic_registry_manifest", D_DIAGNOSTIC_REGISTRY, None),
        ("checker_contract_manifest", D_CHECKER_CONTRACT, None),
    ):
        manifest = json.loads(
            (bundle_path / artifacts[key]["locator"]).read_text(encoding="utf-8")
        )
        manifest_hash = leaf_hash(domain, manifest)
        _require(manifest_hash == artifacts[key]["root"], f"{key} root mismatch")
        if envelope_hash is not None:
            _require(manifest_hash == envelope_hash, f"{key} hash != envelope commitment")

    # envelope root members vs recomputed
    for name, value in recomputed.items():
        _require(
            roots[name] == value,
            f"envelope root {name} = {roots[name]} but artifacts recompute to {value}",
        )
    # manifest roots must equal the corresponding roots members where both exist (§4)
    for key, root_name in (
        ("source_bundle", "source_bundle_root"),
        ("base_tree", "base_tree_root"),
        ("certified_tree_transition_trace", "certified_tree_transition_root"),
        ("content_blobs", "content_blobs_root"),
        ("materialization_index", "materialization_root"),
        ("residual_ledger", "residual_root"),
        ("finding_ledger", "finding_root"),
    ):
        _require(artifacts[key]["root"] == roots[root_name], f"artifacts.{key}.root != roots.{root_name}")

    # status algebra recompute (§5.2) and summary counts (§5.6)
    recomputed_status = compute_certificate_status(
        residual_rows=residual_rows,
        certification_statuses=certification_statuses,
        registered_codes=registered_codes,
    )
    _require(
        recomputed_status == envelope["certificate_status"],
        f"certificate_status {envelope['certificate_status']!r} != recomputed {recomputed_status!r}",
    )
    summary = envelope["residual_summary"]
    counts = {"blocks": 0, "qualifies": 0, "permits": 0}
    by_kind: Dict[str, int] = {}
    for row in residual_rows:
        counts[residual_effect(row, PROFILE_ID)] += 1
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    _require(
        summary["blocking_count"] == counts["blocks"]
        and summary["qualified_count"] == counts["qualifies"]
        and summary["observation_count"] == counts["permits"]
        and summary["frontier_count"] == by_kind.get("manual_frontier", 0)
        and summary["by_kind"] == dict(sorted(by_kind.items())),
        "residual_summary counts do not recompute from the residual ledger",
    )

    # certificate_root over envelope minus certificate_id (§3.3)
    envelope_without_id = {k: v for k, v in envelope.items() if k != "certificate_id"}
    certificate_root = leaf_hash(D_CERT_ROOT, envelope_without_id)
    _require(
        certificate_root == envelope["certificate_id"],
        "certificate_id does not recompute from envelope minus certificate_id",
    )
    recomputed["certificate_root"] = certificate_root
    return recomputed


# ---------------------------------------------------------------------------
# CLI entry (EXPERIMENTAL)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    statute = getattr(args, "statute", None) or "482/2024"
    out = getattr(args, "out", None)
    granularity = getattr(args, "granularity", DEFAULT_GRANULARITY) or DEFAULT_GRANULARITY
    if not out:
        print("error: --out is required", flush=True)
        raise SystemExit(2)
    result = build_certificate_bundle(
        statute,
        out,
        granularity=granularity,
        quiet=False,
        graph_store_root=getattr(args, "graph_store_root", None),
    )
    print("", flush=True)
    print("  EXPERIMENTAL schema-pressure fixture — NOT a checked certificate.", flush=True)
    print("  No checker exists; do not publish or present as a verified claim.", flush=True)
    print("", flush=True)
    print(f"  statute:            {result.statute_id}  ({result.title})", flush=True)
    print(f"  bundle dir:         {result.bundle_dir}", flush=True)
    print(f"  certificate_id:     {result.certificate_id}", flush=True)
    print(f"  build_id:           {result.build_id}", flush=True)
    print(f"  certificate_status: {result.certificate_status}", flush=True)
    print(f"  boundary dates:     {', '.join(result.boundary_dates)}", flush=True)
    print(f"  transitions:        {result.transition_count}", flush=True)
    print(f"  seam rows:          {result.seam_row_count}", flush=True)
    print(f"  residuals:          {result.residual_count}", flush=True)
    print(f"  findings:           {result.finding_count}", flush=True)
    for name, value in result.roots.items():
        print(f"  {name}: {value}", flush=True)
