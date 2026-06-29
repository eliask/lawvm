"""Invariants for the typed ``ClaimAssertion`` / ``ExecutionAuthorizationResult`` /
``CompileAdjudicationProtocol`` carrier Protocols at the core boundary.

Pins the AGENTS.md §1.9 (typed carriers over dynamic shape) and §1.10
(fail loud, never silent-fallback) fix for the three surface->evidence->authority
waist parameters that previously accepted ``Any``:
``frontier_work_item.frontier_work_item_claim_closure_report(assertion=..., authorization_result=...)``
and ``adjudication_evidence._adjudication_input(adjudication=...)`` /
``adjudication_diagnostic_detail(adjudication=...)`` /
``adjudication_finding_evidence_rows(adjudications=...)``.  Each parameter now
accepts only:

  * a typed core carrier instance that structurally conforms to the matching
    ``Protocol`` (``core.provenance_graph.ProvenanceAssertion``,
    ``core.evidence_kernel.AuthorizationResult``,
    ``replay_adjudication.CompileAdjudication``), OR
  * a ``Mapping[str, Any]`` adapter (the §1.9 third-party-adapter exception).

Any other shape raises a named ``TypeError`` subclass
(``UnregisteredClaimAssertion`` / ``UnregisteredAuthorizationResult`` /
``UnregisteredAdjudicationCarrier``) -- never a silent ``None``-default
fallback.

Mirrors ``tests/test_scope_confidence_protocol.py``:
  * AST-scan producer-set: no bare-string ``assertion=``/``authorization_result=``
    callsite at the frontier-work-item closure boundary; no bare-string
    positional arg at the adjudication-evidence boundary.
  * Coerce functions raise on bare string / ``None`` / common smuggle types.
  * Production-lane fire-drill: drive each carrier boundary with a known-bad
    input and assert the typed diagnostic fires through the full path.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from lawvm.core.adjudication_evidence import (
    adjudication_diagnostic_detail,
    adjudication_finding_evidence_rows,
    adjudication_kind_counts,
)
from lawvm.core.evidence_kernel import AuthorizationResult
from lawvm.core.frontier_work_item import (
    FrontierWorkItem,
    frontier_work_item_claim_closure_report,
)
from lawvm.core.provenance_graph import (
    ArtifactRef,
    Interval,
    ProvenanceAssertion,
    SourceRef,
)
from lawvm.core.typed_carrier_protocols import (
    UnregisteredAdjudicationCarrier,
    UnregisteredAuthorizationResult,
    UnregisteredClaimAssertion,
    coerce_adjudication,
    coerce_assertion,
    coerce_authorization_result,
)
from lawvm.replay_adjudication import CompileAdjudication

import lawvm.core.frontier_work_item as _frontier_module


_REPO_ROOT = Path(_frontier_module.__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "lawvm"
# Frontend source roots plus ``core`` itself (the boundary is consumed by core
# helpers and frontend callers -- both must be scanned for bare-string smuggles
# at the listed callsites).
_BOUNDARY_SCAN_DIRS = tuple(
    sorted(
        path
        for path in _SRC_ROOT.iterdir()
        if path.is_dir()
        and path.name != "tools"
        and (path / "__init__.py").exists()
    )
)


def _ast_files() -> list[Path]:
    """Every ``.py`` under the boundary scan roots."""
    files: list[Path] = []
    for root in _BOUNDARY_SCAN_DIRS:
        files.extend(root.rglob("*.py"))
    return sorted(files)


def _parse_ast_safely(path: Path) -> ast.AST | None:
    """Parse a file, returning ``None`` on syntax error.

    Syntax-error files cannot host a smuggle callsite that crosses into the
    production lane (they fail at import), so the skip is safe.  Mirrors
    ``tests/test_scope_confidence_protocol.py``'s skip policy.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def _attribute_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _bare_string_frontier_closure_producers() -> list[str]:
    """AST-scan for bare-string ``assertion=``/``authorization_result=`` at the
    ``frontier_work_item_claim_closure_report`` callsite.

    This is the §1.9 leak the typed Protocols close: a free ``str`` cannot
    cohabit with a frontend's typed ``ProvenanceAssertion`` /
    ``AuthorizationResult`` carrier on the canonical core boundary.  Producers
    MUST pass a typed carrier or a ``Mapping[str, Any]`` adapter.
    """
    offenders: list[str] = []
    for path in _ast_files():
        tree = _parse_ast_safely(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Name)
                and func.id == "frontier_work_item_claim_closure_report"
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg not in ("assertion", "authorization_result"):
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(
                    keyword.value.value, (str, int, type(None), bool)
                ):
                    offenders.append(
                        f"{path.relative_to(_REPO_ROOT)}:{keyword.lineno} "
                        f"{keyword.arg}={keyword.value.value!r}"
                    )
    return offenders


def _bare_string_adjudication_producers() -> list[str]:
    """AST-scan for bare-string positional/keyword adjudication args.

    ``adjudication_diagnostic_detail(adjudication)`` and
    ``adjudication_finding_evidence_rows(adjudications, ...)`` accept only a
    typed ``CompileAdjudicationProtocol`` carrier or ``Mapping[str, Any]``
    adapter.  A bare string positional argument is exactly the §1.9 leak the
    Protocol closes.  (``adjudication_kind_counts`` is included for parity: it
    accepts the same iterable shape.)
    """
    targets = {
        "adjudication_diagnostic_detail",
        "adjudication_finding_evidence_rows",
        "adjudication_kind_counts",
    }
    offenders: list[str] = []
    for path in _ast_files():
        tree = _parse_ast_safely(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id in targets):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(
                    arg.value, (str, int, type(None), bool)
                ):
                    offenders.append(
                        f"{path.relative_to(_REPO_ROOT)}:{arg.lineno} "
                        f"{func.id}(<{type(arg.value).__name__}>={arg.value!r})"
                    )
    return offenders


# --------------------------------------------------------------------------- #
# Helper constructors for typed carriers (ProvenanceAssertion / AuthorizationResult)
# --------------------------------------------------------------------------- #


def _make_provenance_assertion(*, kind: str = "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION") -> ProvenanceAssertion:
    """Minimal ``ProvenanceAssertion`` instance conforming to ``ClaimAssertion``."""
    source_ref = SourceRef(
        artifact_digest="a" * 64,
        structural_locator="chapter:1/section:2",
        bounded_quote_hash="b" * 64,
        normalization_policy_id="v1",
        byte_range=(0, 100),
    )
    return ProvenanceAssertion(
        assertion_id="assertion-test-1",
        schema_version="v1",
        jurisdiction="fi",
        kind=kind,
        layer="extraction",
        scope={"statute_id": "555/2024"},
        target={"ref": "chapter:1/section:2"},
        value={"resolution": "laki 1/2024"},
        source_refs=(source_ref,),
        dependency_refs=(),
        valid_at=Interval(start=date(2024, 1, 1)),
    )


def _make_authorization_result() -> AuthorizationResult:
    """Minimal ``AuthorizationResult`` conforming to ``ExecutionAuthorizationResult``."""
    return AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="assertion-test-1",
            content_hash="sha256:" + "a" * 64,
        ),
        policy_id="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION.strict",
        profile_name="fi_strict",
        authorized=True,
        satisfied_clauses=("exists:span_verified",),
        unsatisfied_clauses=(),
        forbidden_present=(),
        evidence_bundle_hash="sha256:" + "b" * 64,
    )


def _make_compile_adjudication(*, kind: str = "uk_replay_target_not_found") -> CompileAdjudication:
    """Minimal ``CompileAdjudication`` conforming to ``CompileAdjudicationProtocol``."""
    return CompileAdjudication(
        kind=kind,
        message="target missing",
        source_statute="ukpga/2000/1",
        blocking=True,
        phase="replay",
        detail={"target": "section:99"},
    )


def _make_frontier_work_item(*, work_item_id: str = "fi-boundary-probe") -> FrontierWorkItem:
    """Minimal ``FrontierWorkItem`` that passes ``validate_frontier_work_item``.

    The validation requires ``executable=False``, ``replay_authorized=False``,
    and non-empty ``required_proofs`` / ``forbidden_shortcuts``.  The defaults
    on the dataclass handle the two flags; the two sequence fields are
    populated with canonical placeholder strings.
    """
    return FrontierWorkItem(
        work_item_id=work_item_id,
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="section:2",
        owner_phase="typed_elaboration",
        frontier_family="fi_typed_carrier_probe",
        frontier_status="manual_claim_needed",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        safe_default="block_until_validated_claim_authorizes_replay",
        required_proofs=("mutation_boundary_proof",),
        forbidden_shortcuts=("manual_claim_as_replay_authorization",),
        authorization_status="blocked_manual_claim_required",
    )


# --------------------------------------------------------------------------- #
# Protocol parity: AST-scan producer-set
# --------------------------------------------------------------------------- #


def test_no_bare_string_at_frontier_work_item_closure_callsite() -> None:
    """No producer may pass a free string/int/bool/None literal as ``assertion=``
    or ``authorization_result=`` at the frontier closure boundary."""
    offenders = _bare_string_frontier_closure_producers()
    assert not offenders, (
        "free-literal ``frontier_work_item_claim_closure_report(...)`` producers "
        "remain; pass a typed carrier (ProvenanceAssertion/AuthorizationResult or "
        f"conforming typed dataclass) or a Mapping[str, Any] adapter: {offenders}"
    )


def test_no_bare_string_at_adjudication_evidence_callsite() -> None:
    """No producer may pass a free literal as the adjudication argument of
    ``adjudication_diagnostic_detail`` / ``adjudication_finding_evidence_rows`` /
    ``adjudication_kind_counts``."""
    offenders = _bare_string_adjudication_producers()
    assert not offenders, (
        "free-literal adjudication producers remain; pass a typed "
        "CompileAdjudication (or conforming typed dataclass) or a "
        f"Mapping[str, Any] adapter: {offenders}"
    )


# --------------------------------------------------------------------------- #
# Coercion fail-loud: common smuggle types rejected at the boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "bare_string_kind",
        "",
        None,
        0,
        42,
        3.14,
        ("tuple", "of", "values"),
        ["list", "of", "values"],
        {"set", "of"},
        frozenset({"frozen", "set"}),
        b"bytes_payload",
        bytearray(b"bytearray"),
    ],
)
def test_coerce_assertion_raises_on_smuggle_types(value: object) -> None:
    """A non-Mapping, non-typed carrier is a registration gap, never a silent
    fallback (AGENTS.md §1.10)."""
    with pytest.raises(UnregisteredClaimAssertion) as info:
        coerce_assertion(value)  # ty: ignore[invalid-argument-type]
    assert type(value).__name__ in str(info.value) or repr(value) in str(info.value)


@pytest.mark.parametrize(
    "value",
    [
        "bare_string_kind",
        None,
        0,
        42,
        ("tuple",),
        ["list"],
        {"set"},
        frozenset({"frozen"}),
    ],
)
def test_coerce_authorization_result_raises_on_smuggle_types(value: object) -> None:
    with pytest.raises(UnregisteredAuthorizationResult):
        coerce_authorization_result(value)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "value",
    [
        "bare_string_kind",
        "",
        None,
        0,
        42,
        ("tuple",),
        ["list"],
        {"set"},
        frozenset({"frozen"}),
        b"bytes_payload",
    ],
)
def test_coerce_adjudication_raises_on_smuggle_types(value: object) -> None:
    with pytest.raises(UnregisteredAdjudicationCarrier):
        coerce_adjudication(value)  # ty: ignore[invalid-argument-type]


# --------------------------------------------------------------------------- #
# Coercion passthrough: typed carrier / Mapping[str, Any] accepted unchanged
# --------------------------------------------------------------------------- #


def test_coerce_assertion_passes_through_provenance_assertion() -> None:
    """The canonical typed ``ProvenanceAssertion`` carrier conforms to
    ``ClaimAssertion``; coerce returns it unchanged."""
    assertion = _make_provenance_assertion()
    assert coerce_assertion(assertion) is assertion


def test_coerce_assertion_passes_through_mapping_adapter() -> None:
    """A ``Mapping[str, Any]`` adapter is the §1.9 third-party exception."""
    assertion: dict[str, Any] = {
        "assertion_id": "claim-1",
        "jurisdiction": "fi",
        "kind": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        "scope": {},
        "target": {},
        "value": {},
    }
    assert coerce_assertion(assertion) is assertion


def test_coerce_authorization_result_passes_through_typed() -> None:
    result = _make_authorization_result()
    assert coerce_authorization_result(result) is result


def test_coerce_authorization_result_passes_through_mapping() -> None:
    result: dict[str, Any] = {
        "subject_id": "claim-1",
        "policy_id": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION.strict",
        "profile_name": "fi_strict",
        "authorized": True,
        "satisfied_clauses": (),
        "unsatisfied_clauses": (),
        "forbidden_present": (),
        "evidence_bundle_hash": "sha256:" + "a" * 64,
    }
    assert coerce_authorization_result(result) is result


def test_coerce_adjudication_passes_through_typed() -> None:
    adjudication = _make_compile_adjudication()
    assert coerce_adjudication(adjudication) is adjudication


def test_coerce_adjudication_passes_through_mapping() -> None:
    record: dict[str, Any] = {
        "kind": "text_duplication_warning",
        "blocking": False,
        "phase": "replay_fold",
        "detail": {"kind": "duplicate_suffix_text"},
    }
    assert coerce_adjudication(record) is record


# --------------------------------------------------------------------------- #
# Production-lane fire-drill: drive bare-string through the boundary and
# assert the typed diagnostic fires through the FULL production path
# (AGENTS.md §2.9 -- guard-liveness: the worst failure class is a check that
# exists but is unreachable from production).
# --------------------------------------------------------------------------- #


def test_adjudication_diagnostic_detail_rejects_bare_string() -> None:
    """Production-lane fire-drill for ``adjudication_diagnostic_detail``.

    A bare string previously rode through the ``getattr("bare_string", "kind",
    None)`` path, defaulting to ``compile_adjudication`` and silently producing
    a no-detail envelope.  After the coerce fix, the same input raises
    ``UnregisteredAdjudicationCarrier`` at the boundary.
    """
    with pytest.raises(UnregisteredAdjudicationCarrier) as info:
        adjudication_diagnostic_detail("not_a_carrier")  # ty: ignore[invalid-argument-type]
    assert "not_a_carrier" in str(info.value)


def test_adjudication_diagnostic_detail_rejects_none() -> None:
    with pytest.raises(UnregisteredAdjudicationCarrier):
        adjudication_diagnostic_detail(None)  # ty: ignore[invalid-argument-type]


def test_adjudication_diagnostic_detail_rejects_int() -> None:
    with pytest.raises(UnregisteredAdjudicationCarrier):
        adjudication_diagnostic_detail(42)  # ty: ignore[invalid-argument-type]


def test_adjudication_finding_evidence_rows_rejects_bare_string() -> None:
    """A bare-string smuggle inside the iterable raises at the first boundary
    check, never silently producing a row with default ``kind``."""
    with pytest.raises(UnregisteredAdjudicationCarrier):
        adjudication_finding_evidence_rows(
            ("not_a_carrier",),  # ty: ignore[invalid-argument-type]
            frontend_id="fi",
            base_id="2020/1",
            as_of="2020-01-01",
        )


def test_adjudication_kind_counts_rejects_bare_string() -> None:
    """``adjudication_kind_counts`` routes through ``_adjudication_kind``, which
    now coerces each member -- a bare string no longer silently defaults to
    ``"unknown"``."""
    with pytest.raises(UnregisteredAdjudicationCarrier):
        adjudication_kind_counts(["not_a_carrier"])  # type: ignore[list-item]


def test_frontier_work_item_closure_rejects_bare_string_assertion() -> None:
    """Production-lane fire-drill for ``frontier_work_item_claim_closure_report``.

    The bare-string smuggle fails at the existing ``_claim_assertion_mapping``
    ``TypeError`` boundary (preserved by the typed-parameters change).  The new
    typed ``ClaimAssertion | Mapping[str, Any]`` parameter does NOT degrade the
    existing fail-loud posture -- if anything, it makes the boundary statically
    checkable too.
    """
    item = _make_frontier_work_item(work_item_id="fi-smuggle-probe-assertion")
    authorization_result = _make_authorization_result()
    with pytest.raises(TypeError):
        frontier_work_item_claim_closure_report(
            item,
            assertion="bare_string_assertion",  # ty: ignore[invalid-argument-type]
            authorization_result=authorization_result,
        )


def test_frontier_work_item_closure_rejects_bare_string_authorization_result() -> None:
    """Same fail-loud posture for the ``authorization_result`` parameter."""
    item = _make_frontier_work_item(work_item_id="fi-smuggle-probe-authorization")
    assertion = _make_provenance_assertion()
    with pytest.raises(TypeError):
        frontier_work_item_claim_closure_report(
            item,
            assertion=assertion,
            authorization_result="bare_string_result",  # ty: ignore[invalid-argument-type]
        )


def test_frontier_work_item_closure_accepts_typed_carriers() -> None:
    """A typed ``ProvenanceAssertion`` + ``AuthorizationResult`` pair traverses
    the boundary without raising (positive guard-liveness assertion: the new
    typed parameter did not break the legitimate typed-carrier path)."""
    item = _make_frontier_work_item(work_item_id="fi-typed-carrier-probe")
    assertion = _make_provenance_assertion()
    authorization_result = _make_authorization_result()
    report = frontier_work_item_claim_closure_report(
        item,
        assertion=assertion,
        authorization_result=authorization_result,
    )
    # Smoke assertion: the report consumed both typed carriers and produced a
    # structured row -- the boundary did not widen or reject either typed input.
    assert report.rows[0]["frontier_ref_matches"] in (True, False)


def test_frontier_work_item_closure_accepts_mapping_adapters() -> None:
    """``Mapping[str, Any]`` third-party adapters traverse the boundary too
    (§1.9 exception preserved)."""
    item = _make_frontier_work_item(work_item_id="fi-mapping-adapter-probe")
    assertion: dict[str, Any] = {
        "assertion_id": "claim-1",
        "jurisdiction": "fi",
        "kind": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        "scope": {},
        "target": {},
        "value": {},
    }
    authorization_result: dict[str, Any] = {
        "subject_id": "claim-1",
        "policy_id": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION.strict",
        "profile_name": "fi_strict",
        "authorized": True,
        "satisfied_clauses": (),
        "unsatisfied_clauses": (),
        "forbidden_present": (),
        "evidence_bundle_hash": "sha256:" + "a" * 64,
    }
    report = frontier_work_item_claim_closure_report(
        item,
        assertion=assertion,
        authorization_result=authorization_result,
    )
    assert report.rows[0]["frontier_ref_matches"] in (True, False)


# --------------------------------------------------------------------------- #
# Protocol structural conformance: existing typed carriers conform to the new
# Protocols (no explicit inheritance required -- the Protocol's ``getattr``
# surface matches each carrier's dataclass fields).
# --------------------------------------------------------------------------- #


def test_provenance_assertion_structurally_conforms_to_claim_assertion_protocol() -> None:
    """``ProvenanceAssertion`` exposes every field ``ClaimAssertion`` requires.
    Static conformance is what the typed parameter relies on; the runtime
    isinstance check is unnecessary because the carrier is the canonical typed
    instance."""
    assertion = _make_provenance_assertion()
    for field in ("assertion_id", "jurisdiction", "kind", "scope", "target", "value"):
        assert hasattr(assertion, field), (
            f"ProvenanceAssertion missing field {field!r} required by ClaimAssertion"
        )


def test_authorization_result_structurally_conforms_to_protocol() -> None:
    """``AuthorizationResult`` exposes every field ``ExecutionAuthorizationResult``
    requires (nested ``subject.artifact_id`` access is validated by the downstream
    consumer, not by this Protocol)."""
    result = _make_authorization_result()
    for field in (
        "subject",
        "policy_id",
        "profile_name",
        "authorized",
        "satisfied_clauses",
        "unsatisfied_clauses",
        "forbidden_present",
        "evidence_bundle_hash",
    ):
        assert hasattr(result, field), (
            f"AuthorizationResult missing field {field!r} required by "
            f"ExecutionAuthorizationResult"
        )


def test_compile_adjudication_structurally_conforms_to_protocol() -> None:
    """``CompileAdjudication`` exposes every field ``CompileAdjudicationProtocol``
    requires; ``blocking`` is a ``bool`` and ``phase`` a non-empty ``str``."""
    adjudication = _make_compile_adjudication()
    for field in (
        "kind",
        "detail",
        "op_id",
        "source_statute",
        "message",
        "blocking",
        "phase",
    ):
        assert hasattr(adjudication, field), (
            f"CompileAdjudication missing field {field!r} required by "
            f"CompileAdjudicationProtocol"
        )


# --------------------------------------------------------------------------- #
# Named-diagnostic-content sanity: smuggling message embeds the offending
# value's type and repr (AGENTS.md §1.10 -- a diagnostic about a smuggle must
# state the concrete fix without re-running extraction).
# --------------------------------------------------------------------------- #


def test_unregistered_claim_assertion_message_embeds_type_and_repr() -> None:
    err = UnregisteredClaimAssertion("smuggled_kind")
    assert "str" in str(err)
    assert "smuggled_kind" in str(err)


def test_unregistered_authorization_result_message_embeds_type_and_repr() -> None:
    err = UnregisteredAuthorizationResult(None)
    assert "NoneType" in str(err)
    assert "None" in str(err)


def test_unregistered_adjudication_carrier_message_embeds_type_and_repr() -> None:
    err = UnregisteredAdjudicationCarrier(42)
    assert "int" in str(err)
    assert "42" in str(err)


# --------------------------------------------------------------------------- #
# Surface smoke: re-export the Protocols + coerce helpers from the module under
# test (catches accidental ``__all__`` drift / typo'd import path).
# --------------------------------------------------------------------------- #


def test_protocol_symbols_are_exported_from_typed_carrier_protocols() -> None:
    from lawvm.core import typed_carrier_protocols as mod

    for name in (
        "ClaimAssertion",
        "ExecutionAuthorizationResult",
        "CompileAdjudicationProtocol",
        "UnregisteredClaimAssertion",
        "UnregisteredAuthorizationResult",
        "UnregisteredAdjudicationCarrier",
        "coerce_assertion",
        "coerce_authorization_result",
        "coerce_adjudication",
    ):
        assert hasattr(mod, name), f"typed_carrier_protocols missing public symbol: {name}"
        assert name in mod.__all__, f"typed_carrier_protocols.__all__ missing: {name}"


# --------------------------------------------------------------------------- #
# No-leak: the typed diagnostics never appear as a public side-effect in
# output ``detail`` / ``evidence`` rows (synthetic smuggle probes never reach
# user output, persisted artifacts, ``LegalAddress``, or ``ProvisionTimeline``).
# --------------------------------------------------------------------------- #


def test_unregistered_adjudication_carrier_does_not_leak_into_legitimate_rows() -> None:
    """A legitimate ``CompileAdjudication`` does not embed the
    ``UnregisteredAdjudicationCarrier`` diagnostic in its evidence row -- the
    diagnostic fires only on the smuggle path, never on the canonical typed
    carrier."""
    adjudication = _make_compile_adjudication(kind="uk_replay_target_not_found")
    rows = adjudication_finding_evidence_rows(
        (adjudication,),
        frontend_id="uk",
        base_id="ukpga/2000/1",
        as_of="2020-01-01",
    )
    assert len(rows) == 1
    evidence_dump = repr(rows[0].to_dict())
    assert "UnregisteredAdjudicationCarrier" not in evidence_dump
    assert "smuggle" not in evidence_dump.lower()
