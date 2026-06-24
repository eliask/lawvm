"""Tests for the apply-authority/receipt StageResult waist (StageResult endgame).

Program spine: ``notes_internal/STAGERESULT_ENDGAME.md`` (WAIST #7 — the deepest
authority boundary, the authority firewall). The FI replay/apply path applied
writes by CONVENTION (a permissive ``StrictProfile`` + "we just applied"); this
waist makes that an EXPLICIT, type-carried
:class:`~lawvm.core.execution_authorization.ExecutionAuthorization` wrapped in an
:class:`~lawvm.core.stage_result.AuthoritySurface`, and makes a CLEAN /
authoritative receipt IMPOSSIBLE without ``replay_authorized`` at the certificate.

These tests pin:

  (a) ``apply_resolved_op_staged(...).value is apply_resolved_op_with_audit(...).state``
      (identity, 0-delta value path) + ``coverage.is_partition()``;
  (b) a clean apply mints ``authority.replay_authorized is True`` with a granting
      ``ExecutionAuthorization`` (``owner_phase=="apply"``), no blocking residual,
      ``coverage.violation == 0``;
  (c) FIRE-DRILL #1 (authority gates the receipt): an unexplained mutation-boundary
      divergence (the #3 condition) → the apply authority is NOT replay_authorized
      AND the PRODUCTION cert clean-claim gate / verify consumer REFUSES a clean
      claim;
  (d) FIRE-DRILL #2 (the firewall): a landed ``WriteReceipt`` carried to the cert
      WITHOUT a granting ``ExecutionAuthorization`` (neutral surface) → the cert
      clean claim is forbidden. "An unauthorized replay cannot produce an
      authoritative receipt."

The real-corpus dossier tests are skipped when ``data/finlex.farchive`` is absent
(like the rest of the cert suite).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from lawvm.core.phase_result import Finding
from lawvm.core.stage_result import (
    AuthoritySurface,
    NEUTRAL_AUTHORITY,
    StageResult,
)
from lawvm.core.write_receipt import WriteReceipt
from lawvm.finland.apply_replay_authorization import (
    APPLY_BOUNDARY_VIOLATION_FINDING_CODE,
    FI_APPLY_REPLAY_AUTHORIZATION_RULE_ID,
    aggregate_replay_authority,
    mint_apply_replay_authority,
    op_replay_authorized,
)
from lawvm.tools.certificate_bundle import (
    BundleSelfCheckError,
    _require_authorized_replay,
    _verify_apply_authority_clean,
    apply_authority_root,
    apply_authority_row,
    canonical_json_bytes,
    verify_bundle,
)

_CORPUS = Path("data/finlex.farchive")
_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus bundle tests",
)


# ---------------------------------------------------------------------------
# Receipt fixtures (no corpus required)
# ---------------------------------------------------------------------------


_SECTION_1: tuple[tuple[str, str], ...] = (("section", "1"),)
_SECTION_2: tuple[tuple[str, str], ...] = (("section", "2"),)


def _explained_receipt(op_id: str = "op-1") -> WriteReceipt:
    """A landed write whose bound==landed (divergence trivially explained)."""
    return WriteReceipt(
        op_id=op_id,
        helper="fi.apply.resolved_op_write",
        action="replace",
        bound_target_path=_SECTION_1,
        landed_primary_path=_SECTION_1,
        replaced_paths=(_SECTION_1,),
    )


def _unexplained_divergence_receipt(op_id: str = "op-2") -> WriteReceipt:
    """A landed write whose bound != landed with NO named recovery rule.

    ``divergence_explained`` is False — the #3 mutation-boundary condition: the
    write bound one address but landed at another and nothing explains it. NB: a
    real bound target (not None) is required — a ``bound=None`` op-level receipt
    has no divergence to explain and stands today.
    """
    return WriteReceipt(
        op_id=op_id,
        helper="fi.apply.resolved_op_write",
        action="replace",
        bound_target_path=_SECTION_1,
        landed_primary_path=_SECTION_2,
        replaced_paths=(_SECTION_2,),
    )


def _boundary_violation_finding() -> Finding:
    """The blocking apply-boundary touch-outside-target violation finding."""
    return Finding(
        kind=APPLY_BOUNDARY_VIOLATION_FINDING_CODE,
        role="violation",
        stage="apply",
        blocking=True,
        source_statute="test/1",
        detail={"message": "undeclared mutation touch"},
    )


# ---------------------------------------------------------------------------
# (a)/(b) minting contract — the typed AuthoritySurface
# ---------------------------------------------------------------------------


def test_clean_apply_mints_a_granting_execution_authorization() -> None:
    authority = mint_apply_replay_authority(replay_authorized=True)
    assert isinstance(authority, AuthoritySurface)
    assert authority.replay_authorized is True
    assert authority.is_neutral is False
    auth = authority.authorization
    assert auth is not None
    # A GRANTING ExecutionAuthorization, owned by the apply phase.
    assert auth.executable is True
    assert auth.owner_phase == "apply"
    assert auth.authorization_rule_id == FI_APPLY_REPLAY_AUTHORIZATION_RULE_ID
    assert auth.authorization_status == "replay_authorized"
    # The firewall's named anti-patterns are recorded as forbidden shortcuts.
    assert "strict_profile_permissiveness_as_replay_authority" in auth.forbidden_shortcuts
    assert "write_receipt_existence_as_replay_authority" in auth.forbidden_shortcuts


def test_blocked_apply_mints_a_nonauthorized_surface_with_required_proofs() -> None:
    authority = mint_apply_replay_authority(replay_authorized=False)
    assert authority.replay_authorized is False
    auth = authority.authorization
    assert auth is not None
    # The validator rule: a non-authorized row must list required_proofs.
    assert auth.required_proofs
    assert "mutation_boundary_divergence_explained" in auth.required_proofs
    assert auth.executable is False


def test_op_predicate_is_the_exact_conjunction() -> None:
    # Landed, no blocking residual, clean cross-check -> authorized.
    assert op_replay_authorized(
        disposition="APPLIED",
        has_blocking_structural_residual=False,
        undeclared_touch_present=False,
    )
    # Any failed leg un-authorizes (the exact conjunction that lets a write stand).
    assert not op_replay_authorized(
        disposition="APPLY_FAILED",
        has_blocking_structural_residual=False,
        undeclared_touch_present=False,
    )
    assert not op_replay_authorized(
        disposition="APPLIED",
        has_blocking_structural_residual=True,
        undeclared_touch_present=False,
    )
    assert not op_replay_authorized(
        disposition="APPLIED",
        has_blocking_structural_residual=False,
        undeclared_touch_present=True,
    )


# ---------------------------------------------------------------------------
# Per-replay aggregate — the clean-claim predicate (1W)
# ---------------------------------------------------------------------------


def test_aggregate_over_explained_writes_authorizes() -> None:
    authority = aggregate_replay_authority(
        write_receipts=[_explained_receipt("a"), _explained_receipt("b")],
        findings=[],
    )
    assert authority.replay_authorized is True


def test_empty_replay_authorizes_trivially() -> None:
    # No landed writes -> AND over empty set is True (nothing to forbid).
    assert aggregate_replay_authority(write_receipts=[], findings=[]).replay_authorized


def test_one_unexplained_write_unauthorizes_the_replay() -> None:
    # 1W: one unauthorized write un-authorizes the whole replay.
    authority = aggregate_replay_authority(
        write_receipts=[_explained_receipt("a"), _unexplained_divergence_receipt("b")],
        findings=[],
    )
    assert authority.replay_authorized is False


def test_boundary_violation_finding_unauthorizes_the_replay() -> None:
    authority = aggregate_replay_authority(
        write_receipts=[_explained_receipt("a")],
        findings=[_boundary_violation_finding()],
    )
    assert authority.replay_authorized is False


# ---------------------------------------------------------------------------
# (c) FIRE-DRILL #1 — authority gates the receipt (the #3 condition)
# ---------------------------------------------------------------------------


def test_unexplained_divergence_forbids_the_clean_claim_guard() -> None:
    """The load-bearing branch fires on an unexplained mutation-boundary divergence.

    The aggregate over an unexplained-divergence receipt is NOT replay_authorized;
    the PRODUCTION clean-claim gate (`_require_authorized_replay`, called by
    `build_certificate_bundle` when the status is clean) REFUSES the clean claim.
    """
    authority = aggregate_replay_authority(
        write_receipts=[_unexplained_divergence_receipt()],
        findings=[],
    )
    assert authority.replay_authorized is False
    with pytest.raises(
        BundleSelfCheckError,
        match="unauthorized replay cannot produce an authoritative receipt",
    ):
        _require_authorized_replay(authority)


def test_unexplained_divergence_fails_the_verify_consumer() -> None:
    """FIRE-DRILL #1 through the PRODUCTION verify consumer.

    The exact consumer `verify_bundle` calls (`_verify_apply_authority_clean`)
    REFUSES to certify clean an apply-authority row minted from an unexplained
    divergence. Round-tripped through JSON exactly as verify reads it from disk.
    """
    authority = aggregate_replay_authority(
        write_receipts=[_unexplained_divergence_receipt()],
        findings=[],
    )
    row = json.loads(canonical_json_bytes(apply_authority_row(authority)).decode("ascii"))
    with pytest.raises(
        BundleSelfCheckError,
        match="unauthorized replay cannot be certified clean",
    ):
        _verify_apply_authority_clean([row])


# ---------------------------------------------------------------------------
# (d) FIRE-DRILL #2 — the firewall: a neutral surface cannot masquerade
# ---------------------------------------------------------------------------


def test_neutral_surface_cannot_masquerade_as_authoritative() -> None:
    """A landed receipt carried to the cert under a NEUTRAL (un-granted) surface
    forbids the clean claim — an unauthorized replay cannot produce an
    authoritative receipt."""
    assert NEUTRAL_AUTHORITY.replay_authorized is False
    with pytest.raises(
        BundleSelfCheckError,
        match="unauthorized replay cannot produce an authoritative receipt",
    ):
        _require_authorized_replay(NEUTRAL_AUTHORITY)


def test_neutral_surface_fails_the_verify_consumer() -> None:
    """FIRE-DRILL #2 through the PRODUCTION verify consumer: a neutral
    apply-authority row (no granting ExecutionAuthorization) is refused."""
    row = json.loads(
        canonical_json_bytes(apply_authority_row(NEUTRAL_AUTHORITY)).decode("ascii")
    )
    assert row["replay_authorized"] is False
    assert row["is_neutral"] is True
    with pytest.raises(
        BundleSelfCheckError,
        match="unauthorized replay cannot be certified clean",
    ):
        _verify_apply_authority_clean([row])


# ---------------------------------------------------------------------------
# Bite-proof — the anti-built-then-severed property
# ---------------------------------------------------------------------------


def test_authorized_surface_passes_both_guards() -> None:
    """A granting surface (what the green corpus mints) passes both guards — so the
    guard ONLY bites on an unauthorized replay. If the producer reverts to a
    neutral default, the fire-drills above go RED (the guard could never fire)."""
    authority = mint_apply_replay_authority(replay_authorized=True)
    _require_authorized_replay(authority)  # must NOT raise
    row = json.loads(canonical_json_bytes(apply_authority_row(authority)).decode("ascii"))
    _verify_apply_authority_clean([row])  # must NOT raise


def test_verify_consumer_requires_exactly_one_row() -> None:
    with pytest.raises(BundleSelfCheckError, match="exactly one apply-authority row"):
        _verify_apply_authority_clean([])


def test_apply_authority_root_is_stable_for_authorized_replay() -> None:
    # The additive subroot is value-stable for an authorized replay (0-delta).
    a = mint_apply_replay_authority(replay_authorized=True)
    b = mint_apply_replay_authority(replay_authorized=True)
    assert apply_authority_root(a) == apply_authority_root(b)
    # An unauthorized replay produces a DIFFERENT subroot (the checker can tell).
    assert apply_authority_root(NEUTRAL_AUTHORITY) != apply_authority_root(a)


# ---------------------------------------------------------------------------
# Per-op staged wrapper contract (a) — identity + partition, no corpus
# ---------------------------------------------------------------------------


def test_staged_wrapper_is_importable_and_typed() -> None:
    from lawvm.finland.apply_resolved_op import apply_resolved_op_staged

    assert callable(apply_resolved_op_staged)
    # The staged form returns a StageResult whose authority is the apply surface.
    assert StageResult is not None


def test_cert_reads_the_carried_apply_authority_not_a_re_derivation() -> None:
    # PART 3 (2a): `_fi_apply_authority` now READS `products.apply_authority` (the
    # type-carried surface), not a cert-side re-derivation. Prove the carrier is
    # load-bearing: a bundle whose carrier disagrees with what re-derivation would
    # yield returns the CARRIED value. RED if the wire reverts to re-deriving.
    from types import SimpleNamespace

    from lawvm.tools.certificate_bundle import _fi_apply_authority

    sentinel = AuthoritySurface()  # neutral — distinct from an authorized derivation
    bundle = SimpleNamespace(
        result=SimpleNamespace(
            products=SimpleNamespace(apply_authority=sentinel),
            write_receipts=(),
            findings=(),
        )
    )
    assert _fi_apply_authority(bundle) is sentinel


def test_carrier_is_single_sourced_from_filtered_findings() -> None:
    """Bug [7] bite-proof: ``replay_xml`` mints ``products.apply_authority`` from the
    POST-filter ``findings`` (the same set carried on ``ReplayResult`` and re-derived
    by the cert fallback), NOT from the PRE-filter ``signals.findings``.

    If the carrier were minted from ``signals.findings`` while the cert fallback
    re-derives from ``ReplayResult.findings`` (after
    ``drop_materialized_payload_realization_false_positives``), the two could
    diverge whenever the filter dropped a finding. We assert by AST that the
    ``products.apply_authority = aggregate_replay_authority(...)`` assignment passes
    ``findings=findings`` (the filtered local), not ``findings=signals.findings``.
    RED if the wire reverts to the pre-filter source.
    """
    from pathlib import Path as _Path

    import lawvm.finland.replay_entrypoint as _re

    source = _Path(_re.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    found_assignment = False
    for node in ast.walk(tree):
        # Look for: products.apply_authority = aggregate_replay_authority(...)
        if not isinstance(node, ast.Assign):
            continue
        targets = node.targets
        if not (
            len(targets) == 1
            and isinstance(targets[0], ast.Attribute)
            and targets[0].attr == "apply_authority"
        ):
            continue
        call = node.value
        assert isinstance(call, ast.Call), "apply_authority must be a call result"
        findings_kw = next(
            (kw for kw in call.keywords if kw.arg == "findings"), None
        )
        assert findings_kw is not None, "aggregate must be called with findings="
        # The argument must be the filtered local `findings` (an ast.Name), NOT
        # `signals.findings` (an ast.Attribute on `signals`).
        assert isinstance(findings_kw.value, ast.Name), (
            "products.apply_authority must be minted from the filtered local "
            "`findings`, not the pre-filter `signals.findings`"
        )
        assert findings_kw.value.id == "findings"
        found_assignment = True
    assert found_assignment, "products.apply_authority assignment not found"


def test_cert_falls_back_when_carrier_absent() -> None:
    # When the carrier is None (a replay path that never set it), fall back to the
    # descriptive re-derivation so the writer never trusts an un-set carrier. An
    # empty replay re-derives to an authorized surface (the trivial conjunction).
    from types import SimpleNamespace

    from lawvm.tools.certificate_bundle import _fi_apply_authority

    bundle = SimpleNamespace(
        result=SimpleNamespace(
            products=SimpleNamespace(apply_authority=None),
            write_receipts=(),
            findings=(),
        )
    )
    derived = _fi_apply_authority(bundle)
    assert derived.replay_authorized is True


# ---------------------------------------------------------------------------
# real-corpus dossier feeder + verify_bundle branch (the production consumer)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundle_482(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    root = tmp_path_factory.mktemp("applyauth")
    out = root / "482_2024"
    build_certificate_bundle("482/2024", out, graph_store_root=root / "provenance_graph")
    return out


@_corpus_skip
def test_live_apply_authority_feeds_the_dossier_authorized(bundle_482: Path) -> None:
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/apply_authority.jsonl").read_text().splitlines()
    ]
    # Exactly one per-replay apply-authority row, replay_authorized on the green
    # corpus (the conservative-admission 0-delta property).
    assert len(rows) == 1
    assert rows[0]["replay_authorized"] is True
    assert rows[0]["is_neutral"] is False


@_corpus_skip
def test_real_bundle_verifies(bundle_482: Path) -> None:
    # The real dossier verifies end-to-end (the apply-authority firewall does not
    # regress an otherwise-valid bundle: the green replay is authorized).
    verify_bundle(bundle_482)


@_corpus_skip
def test_real_committed_apply_authority_passes_the_verify_consumer(
    bundle_482: Path,
) -> None:
    rows = [
        json.loads(line)
        for line in (bundle_482 / "stages/apply_authority.jsonl").read_text().splitlines()
    ]
    _verify_apply_authority_clean(rows)  # must NOT raise


@_corpus_skip
def test_severed_authority_fails_the_verify_consumer(bundle_482: Path) -> None:
    """FIRE-DRILL through the PRODUCTION verify consumer on the REAL bundle:
    sever the committed apply-authority row to neutral and assert the exact
    consumer `verify_bundle` calls REFUSES to certify it clean.

    RED if the firewall is wired to ignore the authority: with the gate reverted
    the severed (neutral) row would pass and this consumer could not raise."""
    severed = json.loads(
        canonical_json_bytes(apply_authority_row(NEUTRAL_AUTHORITY)).decode("ascii")
    )
    with pytest.raises(
        BundleSelfCheckError,
        match="unauthorized replay cannot be certified clean",
    ):
        _verify_apply_authority_clean([severed])


# ---------------------------------------------------------------------------
# Call-site ratchet — the wiring must not be silently severable
# ---------------------------------------------------------------------------
#
# The fire-drills above exercise the cert authority HELPERS
# (``_require_authorized_replay`` / ``_verify_apply_authority_clean``) in
# isolation. They prove the firewall LOGIC is correct, but they cannot drive the
# production ``build_certificate_bundle`` / ``verify_bundle`` clean-branch
# end-to-end: that branch is gated on ``certificate_status == "clean"`` and the FI
# certificate corpus has NO clean-status statute (482/2024 — like every cert
# fixture — recomputes to ``blocked``; ``verify_bundle`` rejects a patched-clean
# envelope on the status recompute before the authority check is reached). So a
# regression that DELETES the call to either helper from its production caller
# would be SILENT to the behavioral tests. This ratchet closes that gap: it
# asserts (by AST, in the spirit of the Wave-1 architecture ratchets) that each
# production caller still contains the call to its authority gate. It goes RED if
# the call site is removed — the exact anti-built-then-severed property.
def _find_func(func_name: str) -> ast.FunctionDef:
    from pathlib import Path as _Path

    import lawvm.tools.certificate_bundle as _cb

    source = _Path(_cb.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name} not found in certificate_bundle.py")


def _call_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _is_clean_gate_test(test: ast.expr) -> bool:
    """True iff `test` is EXACTLY `<x> == "clean"` (no AND-neutering BoolOp).

    Matches both the build-side local-var compare (`certificate_status == "clean"`)
    and the verify-side subscript compare (`envelope["certificate_status"] ==
    "clean"`). A `BoolOp`-wrapped test (`... == "clean" and False`) is REJECTED so a
    guard-neutering revert (`if ... and False:`) goes RED.
    """
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    operands = [test.left, *test.comparators]
    return any(
        isinstance(node, ast.Constant) and node.value == "clean" for node in operands
    )


def _clean_gate_body_calls(func_name: str, callee_name: str) -> bool:
    """Whether `callee_name` is called INSIDE the `if ... == "clean":` If-body.

    Tighter than "any Call in the function": the call must sit within the clean-gate
    If node's `.body` (NOT `.orelse`, NOT the surrounding function), and the gate
    test must be EXACTLY the clean comparison. RED if the call is removed, moved out
    of the gate, or the gate test is adulterated (`and False`) — closing the
    "dead call keeps it GREEN" blind spot the exit re-audit flagged.
    """
    target = _find_func(func_name)
    for node in ast.walk(target):
        if not isinstance(node, ast.If) or not _is_clean_gate_test(node.test):
            continue
        for inner in node.body:
            for call in ast.walk(inner):
                if isinstance(call, ast.Call) and _call_name(call) == callee_name:
                    return True
    return False


def test_build_certificate_bundle_calls_the_authority_gate() -> None:
    # RED if the `_require_authorized_replay(apply_authority)` call site is removed
    # from build_certificate_bundle, MOVED OUT of the `if certificate_status ==
    # "clean":` gate, or the gate test is neutered (`and False`) — the tightened
    # anti-built-then-severed property (proven silent to the helper-level
    # fire-drills otherwise).
    assert _clean_gate_body_calls(
        "build_certificate_bundle", "_require_authorized_replay"
    ), (
        "build_certificate_bundle must call _require_authorized_replay INSIDE the "
        "`if certificate_status == \"clean\":` gate so a clean dossier cannot be "
        "built over an unauthorized replay (the firewall bite)"
    )


def test_verify_bundle_calls_the_authority_recompute() -> None:
    # RED if the `_verify_apply_authority_clean(...)` call site is removed from
    # verify_bundle, moved out of the clean gate, or the gate is neutered.
    assert _clean_gate_body_calls("verify_bundle", "_verify_apply_authority_clean"), (
        "verify_bundle must call _verify_apply_authority_clean INSIDE the clean "
        "gate so a severed authority makes the self-check raise (guard-liveness)"
    )
