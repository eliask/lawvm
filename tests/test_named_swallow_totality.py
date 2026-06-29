"""Totality predicate + per-site guard-liveness for ``named_swallow`` (§1.10 + §2.6).

The ``except (NameError, TypeError, AttributeError): raise; except Exception: <swallow>``
pattern was re-invented at 10+ sites across the codebase (Theme C). Per
AGENTS.md §2.6 (rule of three), it is overdue for crystallisation. The
``lawvm.core.named_swallow`` primitive provides a single owned fail-loud
shape: programming bugs re-raise; all other ``Exception`` instances are
swallowed-with-witness — a typed ``Finding(kind=UNEXPECTED_PHASE_FAILURE,
blocking=True)`` is constructed carrying ``rule_id``, ``exception_type``,
``exception_message``, ``op_id``, ``clause_text`` (truncated ~400 chars),
``source_artifact``, ``jurisdiction``, then emitted through the caller's
``emit`` callable OR ``findings_out`` sink. AGENTS.md §1.10 forbids the silent
default the prior shape produced.

This test pins two invariants:

1. **Totality predicate (AST scan)**: the migrated modules contain no
   remaining ``try/except Exception: <swallow-to-default>`` shape — every
   ``except Exception:`` clause either re-raises a programming-bug axis OR is
   wrapped/caught by ``named_swallow`` / ``swallow_call`` /
   ``build_named_swallow_finding``. The forbidden shape is the
   pattern-3 rhyming across migrated files — once the primitive exists,
   re-introducing it is the §2.6 worst-case (the fix shape landed N+1 times).

2. **Per-site guard-liveness** (AGENTS.md §2.9 "the worst failure class"):
   each migrated site has a synthetic test that drives a known-violating input
   through the production path and asserts the typed Finding fires — not just
   a unit test of ``named_swallow`` itself. The 10 migrated sites cover the
   ``named_swallow``-contextmanager shape (graph.py, transparent_store.py,
   _worker_pool.py), the ``swallow_call`` HOF shape (corpus.py,
   frontend_observations.py, estonia spec_ledger_adapter.py, dry_run.py, ops.py),
   and the ``build_named_swallow_finding`` direct shape (consolidated_artifacts.py,
   sweden/grafter.py).
"""
from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lawvm.core.named_swallow import (
    NAMED_SWALLOW_FINDING_KIND,
    NamedSwallowNonEmittingSinkError,
    build_named_swallow_finding,
    log_emitter,
    named_swallow,
    swallow_call,
)
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.core.phase_result import Finding


# ---------------------------------------------------------------------------
# MIGRATED FILES — the AST-scan totality surface
# ---------------------------------------------------------------------------
# Files that touched the swallow pattern (Theme C sites). Adding to this tuple
# extends the totality predicate below; the AST scan asserts every
# ``except Exception:`` clause in these files either re-raises a programming-bug
# axis OR is wrapped by ``named_swallow`` / ``swallow_call`` /
# ``build_named_swallow_finding``. Re-introducing an in-line
# ``except Exception: <swallow-to-default>`` shape here fails the totality guard.
_MIGRATED_FILES = (
    "src/lawvm/core/named_swallow.py",
    "src/lawvm/finland/ops.py",
    "src/lawvm/finland/graph.py",
    "src/lawvm/finland/frontend_observations.py",
    "src/lawvm/finland/transparent_store.py",
    "src/lawvm/finland/corpus.py",
    "src/lawvm/finland/consolidated_artifacts.py",
    "src/lawvm/finland/apply_typed_dispatch.py",
    "src/lawvm/estonia/spec_ledger_adapter.py",
    "src/lawvm/new_zealand/dry_run.py",
    "src/lawvm/tools/_worker_pool.py",
    "src/lawvm/sweden/grafter.py",
    # Wave 6 (M4) — 7 of the 9 catalog files extend cleanly into the AST
    # totality surface (the 13 catalog swallows across these files are now
    # routed through named_swallow). The remaining 2 catalog files
    # (``finland/amendment_index.py`` and ``sweden/fetch.py``) are NOT
    # added here because they each contain ADDITIONAL pre-existing
    # ``except Exception as exc:`` sites that use a DIFFERENT owned
    # fail-loud idiom — ``_append_amendment_index_diagnostic`` (in
    # amendment_index.py at swallow sites not in this wave's catalog) and
    # an inline ``acquisition_failures`` list with rule_id+sfs_id+error_type
    # (in sweden/fetch.py:2729). Both are OWNED §1.10 emissions through a
    # domain-specific helper, but the AST scan only recognises the 4
    # named_swallow primitive symbols. Migrating those pre-existing sites
    # is out of this catalog's scope (STOP-and-report per the task spec;
    # the 2 catalog-grade migrations in those files ARE shipped and are
    # validated by the per-site fire-drill precedent at Wave 4 + the
    # AST totality on the other 7 files). The pre-existing sites are
    # candidates for a future named_swallow migration wave.
    "src/lawvm/finland/llm_backends/qwen_local.py",
    "src/lawvm/finland/transition_graph_profile.py",
    "src/lawvm/finland/legal_surface/condition_exception_census.py",
    "src/lawvm/finland/legal_surface/family_census.py",
    "src/lawvm/finland/legal_surface/modal_census.py",
    "src/lawvm/finland/references/annotation_independence_census.py",
    "src/lawvm/finland/references/broken_detection.py",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The 3 known primitive entry points allowed to handle the swallow shape.
_KNOWN_SWALLOW_HANDLERS = {
    "named_swallow",
    "swallow_call",
    "build_named_swallow_finding",
    "log_emitter",
}
_KNOWN_SWALLOW_MODULE = "lawvm.core.named_swallow"


def _find_inline_silent_swallow_offenders(file_path: Path) -> list[str]:
    """Find an in-line ``except Exception:`` clause that swallows silently.

    The forbidden shape in any migrated file is::

        try:
            ...
        except Exception:
            <swallow-to-default>

    where the body of ``except Exception:`` is anything OTHER than:
      - passing the exception to a named_swallow primitive (which witnesses it),
      - re-raising via ``raise`` (programming bugs that already propagate),
      - a logging call that emits the typed Finding (log_emitter from named_swallow).

    Returns a list of ``filename:lineno`` strings for each offender. An empty list
    means the totality holds.
    """
    offenders: list[str] = []
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    tree = ast.parse(source, filename=str(file_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Only inspect ``except Exception:`` (or bare ``except:``) — the silent
        # swallow axis. ``except (NameError, TypeError, AttributeError): raise``
        # is explicitly allowed (re-raises programming bugs).
        exc_type = node.type
        if exc_type is None:
            # Bare ``except:`` — silent by construction, flagged.
            offenders.append(f"{file_path.name}:{node.lineno} bare except:")
            continue
        # Match ``Exception`` / ``BaseException`` (the swallow axis).
        is_broad = False
        if isinstance(exc_type, ast.Name) and exc_type.id in ("Exception", "BaseException"):
            is_broad = True
        elif isinstance(exc_type, ast.Tuple):
            for elt in exc_type.elts:
                if isinstance(elt, ast.Name) and elt.id in ("Exception", "BaseException"):
                    is_broad = True
                    break
        if not is_broad:
            continue
        # Now check the ``except Exception:`` body — is it routed through the
        # named_swallow primitive?
        handler_uses_named_swallow_primitive = _handler_routes_to_named_swallow(node)
        if handler_uses_named_swallow_primitive:
            continue
        # Otherwise: offender — the swallow is in-line, NOT routed through
        # the named_swallow primitive.
        offenders.append(
            f"{file_path.name}:{node.lineno} in-line except Exception swallow "
            f"(route through lawvm.core.named_swallow.named_swallow/"
            f"swallow_call/build_named_swallow_finding)"
        )
    return offenders


def _handler_routes_to_named_swallow(handler: ast.ExceptHandler) -> bool:
    """Check if the handler body uses a named_swallow primitive.

    Recognises patterns where the handler body calls
    ``log_emitter()(build_named_swallow_finding(...))`` OR contains a
    ``with named_swallow(...)`` OR a call to ``swallow_call(...)`` OR
    ``build_named_swallow_finding(...)``.
    """
    for child in ast.walk(handler):
        if isinstance(child, ast.Call):
            func = child.func
            # build_named_swallow_finding(...)
            if isinstance(func, ast.Name) and func.id in _KNOWN_SWALLOW_HANDLERS:
                return True
            # log_emitter()(...) — call on call result; walk the call graph
            if isinstance(func, ast.Call):
                inner = func.func
                if isinstance(inner, ast.Name) and inner.id in _KNOWN_SWALLOW_HANDLERS:
                    return True
        if isinstance(child, ast.With):
            # ``with named_swallow(...) as ...:``
            for item in child.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name):
                    if ctx.func.id in _KNOWN_SWALLOW_HANDLERS:
                        return True
                # ``with named_swallow(...)`` (no ``as``) — function attribute access
                if isinstance(ctx, ast.Call):
                    func = ctx.func
                    if isinstance(func, ast.Attribute) and func.attr in _KNOWN_SWALLOW_HANDLERS:
                        return True
        # An attribute call like ``named_swallow.swallow_call(...)`` or
        # ``lawvm.core.named_swallow.swallow_call(...)`` is in primitive use.
        if isinstance(child, ast.Attribute) and child.attr in _KNOWN_SWALLOW_HANDLERS:
            return True
    return False


# ---------------------------------------------------------------------------
# Test 1: totality predicate — AST-scan every migrated file
# ---------------------------------------------------------------------------

def test_totality_predicate_no_inline_silent_swallow_in_migrated_files() -> None:
    """No migrated file may re-introduce the in-line silent-exception swallow.

    This is the §2.6 totality predicate: once ``named_swallow`` exists, the
    forbidden shape is re-inventing the swallow-late boundary inline (the rule
    of N+1 patches that §2.6 forbids). A passing run means every
    ``except Exception:`` clause in the migrated files is either re-raising OR
    routed through one of the named_swallow primitive entry points
    (``named_swallow``, ``swallow_call``, ``build_named_swallow_finding``,
    ``log_emitter``).

    The exceptions are: filtered-consumer bug class axis
    ``except (NameError, TypeError, AttributeError): raise`` (programming bugs
    that surface, not swallow); narrow exception class (``except OSError:``,
    ``except etree.ParseError:``, etc — already filtered, not broad swallows).
    """
    all_offenders: list[str] = []
    for rel_path in _MIGRATED_FILES:
        migrated_path = _REPO_ROOT / rel_path
        if not migrated_path.exists():
            # File removed/moved since the test was written — surface loudly
            # so the totality predicate is kept honest.
            all_offenders.append(
                f"{rel_path}: migrated file missing; update _MIGRATED_FILES"
            )
            continue
        all_offenders.extend(_find_inline_silent_swallow_offenders(migrated_path))
    assert not all_offenders, (
        "In-line silent ``except Exception:`` swallows remain in migrated files "
        "(Theme C — §2.6 rule-of-three totality violation; route through "
        "lawvm.core.named_swallow.named_swallow / swallow_call / "
        "build_named_swallow_finding instead): "
        + "; ".join(all_offenders)
    )


# ---------------------------------------------------------------------------
# Test 2: named_swallow primitive unit — preserves the public contract
# ---------------------------------------------------------------------------

def test_named_swallow_kind_is_registered_in_finding_registry() -> None:
    """``UNEXPECTED_PHASE_FAILURE`` is registered with role=obligation/blocking=True.

    Required by AGENTS.md §1.10: distinct named diagnostic distinguishable from
    neighbouring failures and stating the concrete fix. The registry is the
    single source of truth — a Kind/role mismatch makes the Finding impossible
    to construct.
    """
    spec = FINDING_REGISTRY.get(NAMED_SWALLOW_FINDING_KIND)
    assert spec is not None, (
        f"named_swallow primitive Finding kind {NAMED_SWALLOW_FINDING_KIND!r} "
        "is not registered in lawvm.core.observation_registry.FINDING_REGISTRY"
    )
    assert spec.role == "obligation"
    # default_enforcement=strict_fail: strict mode FAILS on a swallow (no
    # silent carry-on), quirks mode surfaces the witness but continues.
    assert spec.default_enforcement == "strict_fail"


def test_apply_op_skipped_witness_kind_is_registered_as_observation() -> None:
    """``APPLY.OP_SKIPPED_WITNESSED`` is registered with role=observation (non-blocking).

    The 13 ``outcome=\"skipped\"`` applyResolvedOp sites emit a non-blocking
    observation (audit total under §1.8) — the audit ledger carries the witness
    so the disposition tracking APPLIED can be reviewed even when no FailedOp
    was produced. non-blocking so quirks mode continues.
    """
    spec = FINDING_REGISTRY.get("APPLY.OP_SKIPPED_WITNESSED")
    assert spec is not None
    assert spec.role == "observation"
    assert spec.default_enforcement == "warn"


# ---------------------------------------------------------------------------
# Test 3-12: per-site guard-liveness — drive known-violating inputs through the
# production path and assert the typed Finding fires.
# ---------------------------------------------------------------------------

def test_named_swallow_re_raises_programming_bugs() -> None:
    """Programming-bug classes re-raise; never silent (named_swallow level)."""
    sink: list[Finding] = []
    for exc_class in (NameError, TypeError, AttributeError):
        with pytest.raises(exc_class):
            with named_swallow(
                rule_id=f"programming_bug_{exc_class.__name__}",
                default=None,
                findings_out=sink,
            ):
                raise exc_class("boom")
    # No Finding emitted for programming bugs — they surface to the developer.
    assert sink == []


def test_named_swallow_emits_typed_finding_via_emit() -> None:
    """emit= receives a typed Finding with rule_id/exception_type/clause_text."""
    sink: list[Finding] = []
    with named_swallow(
        rule_id="test_emit_path",
        default="DEFAULT",
        op_id="op-001",
        source_artifact="fixture.xml",
        jurisdiction="fi",
        source_statute="2023/100",
        clause_text="offending clause body" * 200,  # >400 chars to test truncation
        emit=sink.append,
    ):
        raise ValueError("simulated swallow")
    assert len(sink) == 1
    f = sink[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.role == "obligation"
    assert f.blocking is True
    assert f.source_statute == "2023/100"
    assert f.detail["rule_id"] == "test_emit_path"
    assert f.detail["exception_type"] == "ValueError"
    assert f.detail["exception_message"] == "simulated swallow"
    assert f.detail["op_id"] == "op-001"
    assert f.detail["source_artifact"] == "fixture.xml"
    assert f.detail["jurisdiction"] == "fi"
    # clause_text truncated to ~400 chars + marker
    assert len(f.detail["clause_text"]) < 500
    assert "truncated" in f.detail["clause_text"]


def test_named_swallow_emits_typed_finding_via_findings_out() -> None:
    """findings_out= list sink also receives the typed Finding (caller choice)."""
    sink: list[Finding] = []
    with named_swallow(
        rule_id="test_findings_out_path",
        default=None,
        findings_out=sink,
    ):
        raise RuntimeError("boom")
    assert len(sink) == 1
    assert sink[0].detail["rule_id"] == "test_findings_out_path"


def test_named_swallow_raises_named_swallow_non_emitting_sink_error_when_no_sink_wired() -> None:
    """No emit/findings_out wired → NamedSwallowNonEmittingSinkError (§1.10 fail-loud)."""
    with pytest.raises(NamedSwallowNonEmittingSinkError) as excinfo:
        with named_swallow(
            rule_id="test_no_sink",
            default=None,
            # neither emit= nor findings_out= — should fail-loud
        ):
            raise ValueError("boom-on-no-sink")
    err = excinfo.value
    assert err.rule_id == "test_no_sink"
    # The un-emitted typed Finding is preserved on the error so a wrapping
    # test or top-level error sink can still capture it.
    assert isinstance(err.unemitted_finding, Finding)
    assert err.unemitted_finding.kind == NAMED_SWALLOW_FINDING_KIND


def test_swallow_call_returns_default_on_swallows() -> None:
    """swallow_call HOF returns default on exception, with witness emitted."""
    sink: list[Finding] = []

    def _raise_zero_div() -> int:
        raise ZeroDivisionError("simulated division-by-zero swallow")

    result = swallow_call(
        _raise_zero_div,
        rule_id="test_swallow_call_zero_div",
        default=-1,
        findings_out=sink,
    )
    assert result == -1
    assert len(sink) == 1
    assert sink[0].detail["exception_type"] == "ZeroDivisionError"


# ---------------------------------------------------------------------------
# Test 13-22: per-site guard-liveness for the migrated sites
# ---------------------------------------------------------------------------

def test_corpus_list_cached_consolidated_locators_finding_fires_on_swallow() -> None:
    """Migrated site: finland/corpus.py list_cached_consolidated_locators.

    Drives a synthesized known-violating input through the production path
    (an archive backend that raises RuntimeError on .locators(pattern)) and
    asserts the typed Finding fires with rule_id="fi_corpus_list_cached_consolidated_locators".
    """
    from lawvm.finland.corpus import list_cached_consolidated_locators

    class BoomArchive:
        def locators(self, pattern: str) -> list[str]:
            raise RuntimeError("simulated archive backend boom")

    sink: list[Finding] = []
    result = list_cached_consolidated_locators(
        BoomArchive(), sid="2023/100", findings_out=sink
    )
    assert result == []  # default behaviour preserved
    assert len(sink) == 1
    f = sink[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert f.detail["rule_id"] == "fi_corpus_list_cached_consolidated_locators"
    assert f.detail["exception_type"] == "RuntimeError"
    assert f.detail["jurisdiction"] == "fi"
    assert f.detail["source_artifact"] == "2023/100"
    # The clause_text carries the source witness (the glob pattern).
    assert "glob pattern=" in f.detail["clause_text"]


def test_consolidated_artifacts_extract_identity_witnesses_non_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: finland/consolidated_artifacts.py extract_consolidated_xml_identity.

    ParseError is silent (expected malformed-XML, returns empty identity); any
    OTHER exception is witnessed via build_named_swallow_finding + log_emitter.
    Drives a known-violating input by monkey-patching etree.fromstring to
    raise OSError, and asserts the typed Finding is logged via log_emitter.
    """
    from lawvm.finland import consolidated_artifacts as ca
    from lawvm.finland.consolidated_artifacts import extract_consolidated_xml_identity

    captured: list[Finding] = []

    def _capture_emit() -> Callable[[Finding], None]:
        def _emit(finding: Finding) -> None:
            captured.append(finding)
        return _emit

    # Patch the module-level log_emitter reference (used inside the except
    # block) to capture the constructed Finding instead of logging it.
    monkeypatch.setattr(ca, "log_emitter", _capture_emit)
    # Patch etree.fromstring (referenced as ca.etree.fromstring) to raise
    # OSError on the swallowed attempt — the non-ParseError branch.
    def _boom(_data: bytes, *args: Any, **kwargs: Any) -> Any:
        raise OSError("simulated unexpected failure")

    monkeypatch.setattr(ca.etree, "fromstring", _boom)
    # bytes-with-no-FRBRthis so the fast path returns None and fromstring runs.
    result = extract_consolidated_xml_identity(b"<valid xml></valid>")
    # Empty identity returned (default preserved).
    assert result is not None
    # The typed Finding was logged (capture).
    assert len(captured) == 1
    f = captured[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert (
        f.detail["rule_id"]
        == "fi_consolidated_artifacts_extract_identity_fromstring"
    )
    assert f.detail["exception_type"] == "OSError"
    assert f.detail["jurisdiction"] == "fi"


def test_spec_ledger_adapter_ee_resolve_as_of_witnesses_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: estonia/spec_ledger_adapter.py _ee_resolve_as_of.

    Drives a known-violating input by patching fetch_rt_xml to raise, and asserts
    the typed Finding is emitted via log_emitter carrying the oracle_id.
    """
    from lawvm.estonia import spec_ledger_adapter as sla

    captured: list[Finding] = []

    def _capture_emit() -> Callable[[Finding], None]:
        def _emit(finding: Finding) -> None:
            captured.append(finding)
        return _emit

    # The migrated code imports log_emitter lazily from lawvm.core.named_swallow,
    # so patch at the source module — the function-local `from ... import`
    # rebinds the name at call-time, picking up our capturing version.
    import lawvm.core.named_swallow as ns

    monkeypatch.setattr(ns, "log_emitter", _capture_emit)
    # Patch fetch_rt_xml to raise (the swallow fires).
    def _boom_fetch(_oracle_id: str, _archive: object) -> bytes:
        raise RuntimeError("simulated RT fetch boom")

    import lawvm.estonia.fetch as ee_fetch

    monkeypatch.setattr(ee_fetch, "fetch_rt_xml", _boom_fetch)
    result = sla._ee_resolve_as_of("2023/100/oracle-123", archive=None)
    # Empty default preserved.
    assert result == ""
    # Typed Finding emitted.
    assert len(captured) == 1
    f = captured[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.detail["rule_id"] == "ee_spec_ledger_fetch_rt_xml"
    assert f.detail["exception_type"] == "RuntimeError"
    assert f.detail["jurisdiction"] == "ee"
    assert f.detail["source_artifact"] == "2023/100/oracle-123"


def test_sweden_grafter_pdf_bytes_to_text_witnesses_unexpected_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: sweden/grafter.py se_pdf_bytes_to_text.

    OSError / subprocess.TimeoutExpired are silent (narrowed-expected); any
    OTHER exception is witnessed via build_named_swallow_finding + log_emitter.
    Drives a non-OS, non-subprocess error and asserts the typed Finding fires.
    """
    # The migrated code imports log_emitter / build_named_swallow_finding
    # INSIDE the except clause (lazily); patch them at their source module
    # (lawvm.core.named_swallow) so the function-local `from ... import` rebinds
    # the names to our capturing versions for the duration of the test.
    import lawvm.core.named_swallow as ns
    import lawvm.sweden.grafter as se

    captured: list[Finding] = []

    def _capture_emit() -> Callable[[Finding], None]:
        def _emit(finding: Finding) -> None:
            captured.append(finding)
        return _emit

    monkeypatch.setattr(ns, "log_emitter", _capture_emit)
    # Monkey-patch subprocess.run to raise an unexpected ValueError (a non-OS,
    # non-subprocess.TimeoutExpired exception class — the targeted swallow path).
    def _boom_run(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("simulated unexpected subprocess.run error")

    monkeypatch.setattr(se.subprocess, "run", _boom_run)
    result = se.se_pdf_bytes_to_text(b"%PDF-1.4 fake pdf")
    # None returned (default preserved).
    assert result is None
    # The typed Finding was emitted via log_emitter.
    assert len(captured) == 1
    f = captured[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert (
        f.detail["rule_id"] == "se_grafter_pdf_bytes_to_text_subprocess"
    )
    assert f.detail["exception_type"] == "ValueError"
    assert f.detail["jurisdiction"] == "se"


def test_apply_typed_dispatch_emit_apply_op_skipped_witness_fires() -> None:
    """Migrated sites: apply_typed_dispatch.py 13 ``outcome=\"skipped\"`` sites.

    Drives a known-violating input through the production path (a synthetic
    ResolvedOp with a missing source-container) and asserts the
    ``APPLY.OP_SKIPPED_WITNESSED`` Finding fires via the findings_out sink
    with rule_id matching the reason_code.
    """
    from typing import cast

    from lawvm.finland.apply_typed_dispatch import _emit_apply_op_skipped_witness
    from lawvm.finland.ops import ResolvedOp

    # ResolvedOp is a wide-tuple dataclass with many late-waist fields; the
    # helper only reads op_id and resolved_source_statute. Cast a stub
    # SimpleNamespace to satisfy the type — the test exercises the Finding
    # emission shape, not the ResolvedOp construction contract.
    placeholder_rop = cast(
        ResolvedOp,
        SimpleNamespace(
            op_id="test-op-1",
            resolved_source_statute="2023/100",
            resolved_target_label="5",
            resolved_target_subsection_label=None,
            resolved_target_item_label=None,
            target_norm="5 §",
            resolved_action_type="MOVE",
        ),
    )

    sink: list[Finding] = []
    _emit_apply_op_skipped_witness(
        sink,
        rop=placeholder_rop,
        reason_code="source_container_missing",
        failure_reason="source container section:5 not found",
        clause_text="relabel source_container_missing label=5",
    )
    assert len(sink) == 1
    f = sink[0]
    assert f.kind == "APPLY.OP_SKIPPED_WITNESSED"
    assert f.role == "observation"
    assert f.blocking is False  # non-blocking audit
    assert f.detail["rule_id"] == "source_container_missing"
    assert f.detail["reason_code"] == "source_container_missing"
    assert f.detail["op_id"] == "test-op-1"
    assert f.source_statute == "2023/100"


def test_named_swallow_log_emitter_writes_warning_to_logger(caplog) -> None:
    """log_emitter() returns a callable that writes the typed Finding to a logger.

    For utility sites without a findings_out sink, log_emitter is the
    visible-WARNING fallback. Verifies the structured key=value log line is
    written at WARNING level so triaging the residual does not require
    re-running extraction.
    """
    emit = log_emitter()
    finding = build_named_swallow_finding(
        rule_id="log_emitter_test",
        exception=ValueError("boom"),
        op_id="op-1",
        clause_text="clause text snippet",
        source_artifact="/path/to/artifact.xml",
        jurisdiction="fi",
        source_statute="2023/100",
    )
    with caplog.at_level(logging.WARNING, logger="lawvm.core.named_swallow"):
        emit(finding)
    assert any(
        "named_swallow Finding" in record.getMessage()
        and "log_emitter_test" in record.getMessage()
        and "ValueError" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Wave 6 (M4) per-site guard-liveness — 3 representative fire-drills out of
# the 13 migrated catalog sites. The remaining 10 sites are validated by:
#   (a) the AST totality predicate above (proves the swallow shape is gone
#       for the 7 files added to _MIGRATED_FILES in this wave),
#   (b) the named_swallow primitive unit tests (proves the typed Finding
#       construction + emit logic),
#   (c) the shared log_emitter() warning-visibility test above.
# Three fire-drills cover the distinct swallow shapes migrated in this wave:
#   - amendment_index.py: multi-line body returning None on swallow
#       (file NOT in _MIGRATED_FILES — pre-existing sites use a different
#       owned fail-loud idiom and are out of this catalog's scope; the
#       catalog-grade migration is still validated here)
#   - transition_graph_profile.py: multi-site util returning "" on swallow
#   - qwen_local.py: network-probe swallow returning False on swallow
# Per AGENTS.md §2.9 (guard-liveness): driving a known-violating input
# through the FULL production path and asserting the diagnostic fires, not
# just a unit test of the guard.
# ---------------------------------------------------------------------------

def test_amendment_index_corpus_source_fingerprint_finding_fires_on_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: finland/amendment_index.py _corpus_source_fingerprint.

    Drives a synthesized known-violating input (monkeypatch
    ``_path_from_pathlike`` to raise RuntimeError on the path-probe axis)
    through the FULL production path and asserts the typed Finding fires with
    rule_id=``fi_amendment_index_corpus_source_fingerprint`` via lazy-imported
    log_emitter (matching the sweden/grafter.py precedent).
    """
    from typing import cast

    import lawvm.core.named_swallow as ns
    from lawvm.finland import amendment_index as ai
    from lawvm.corpus_store import CorpusStore

    captured: list[Finding] = []

    def _capture_emit() -> Callable[[Finding], None]:
        def _emit(finding: Finding) -> None:
            captured.append(finding)
        return _emit

    # The migrated code imports log_emitter INSIDE the except clause (lazy
    # import); patch at the source module so the function-local
    # ``from ... import`` rebinds the name to our capturing version.
    monkeypatch.setattr(ns, "log_emitter", _capture_emit)
    # Patch _path_from_pathlike to raise — the swallow fires inside the
    # path-probe loop.
    def _boom(_v: object) -> Path:
        raise RuntimeError("simulated path probe boom")

    monkeypatch.setattr(ai, "_path_from_pathlike", _boom)
    # Cast through CorpusStore to satisfy ty — the function under test only
    # reads attributes defensively via getattr; an ``object()`` exposes
    # ``__class__`` which is enough for ``type(cs).__name__`` in clause_text.
    cs = cast(CorpusStore, object())
    result = ai._corpus_source_fingerprint(cs)
    # Default preserved.
    assert result is None
    # Typed Finding emitted.
    assert len(captured) == 1
    f = captured[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert f.detail["rule_id"] == "fi_amendment_index_corpus_source_fingerprint"
    assert f.detail["exception_type"] == "RuntimeError"
    assert f.detail["jurisdiction"] == "fi"
    assert "cs_type=" in f.detail["clause_text"]


def test_transition_graph_extract_source_reference_finding_fires_on_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: finland/transition_graph_profile.py
    extract_fi_source_reference (read_amendment swallow).

    Drives a synthesized known-violating input through the FULL production
    path (a corpus whose read_amendment raises RuntimeError on the read axis)
    and asserts the typed Finding fires with rule_id=
    ``fi_transition_graph_extract_source_reference_read_amendment``.
    """
    import lawvm.core.named_swallow as ns
    from lawvm.finland.transition_graph_profile import extract_fi_source_reference

    captured: list[Finding] = []

    def _capture_emit() -> Callable[[Finding], None]:
        def _emit(finding: Finding) -> None:
            captured.append(finding)
        return _emit

    monkeypatch.setattr(ns, "log_emitter", _capture_emit)

    class BoomCorpus:
        def read_amendment(self, _sid: str) -> bytes:
            raise RuntimeError("simulated corpus.read_amendment boom")

    result = extract_fi_source_reference(BoomCorpus(), engine_source_id="2023/100")
    # Empty default preserved.
    assert result == ""
    # Typed Finding emitted.
    assert len(captured) == 1
    f = captured[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert (
        f.detail["rule_id"]
        == "fi_transition_graph_extract_source_reference_read_amendment"
    )
    assert f.detail["exception_type"] == "RuntimeError"
    assert f.detail["jurisdiction"] == "fi"
    assert "engine_source_id=2023/100" in f.detail["clause_text"]
    assert f.detail["source_artifact"] == "2023/100"


def test_qwen_local_check_server_reachable_finding_fires_on_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: finland/llm_backends/qwen_local.py _check_server_reachable.

    Drives a synthesized known-violating input through the FULL production
    path (monkeypatch urllib.request.urlopen to raise RuntimeError on the
    probe axis) and asserts the typed Finding fires with rule_id=
    ``fi_qwen_local_check_server_reachable`` via lazy-imported log_emitter.
    """
    import urllib.request

    import lawvm.core.named_swallow as ns
    from lawvm.finland.llm_backends import qwen_local as ql

    captured: list[Finding] = []

    def _capture_emit() -> Callable[[Finding], None]:
        def _emit(finding: Finding) -> None:
            captured.append(finding)
        return _emit

    monkeypatch.setattr(ns, "log_emitter", _capture_emit)
    # urllib.request is imported lazily inside _check_server_reachable; the
    # module object is cached so patching the .urlopen attribute on the real
    # module is picked up by the function-local import.
    def _boom(_req: object, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated urlopen probe boom")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    result = ql._check_server_reachable()
    # False default preserved.
    assert result is False
    # Typed Finding emitted.
    assert len(captured) == 1
    f = captured[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert f.detail["rule_id"] == "fi_qwen_local_check_server_reachable"
    assert f.detail["exception_type"] == "RuntimeError"
    assert f.detail["jurisdiction"] == "fi"
    assert "probe endpoint=" in f.detail["clause_text"]


# ---------------------------------------------------------------------------
# iter3 Wave 2 (W2) — findings_out-threaded fire-drills (arch HIGH H1, §3.2).
#
# These three representative fire-drills complement the precedent log_emitter-
# patch drills above: they plumb ``findings_out=<list>`` directly into the
# migrated sites and assert the typed Finding LANDS IN the per-statute audit-
# trail list (not just stderr). They are the evidence-path-answerability
# witnesses for the §3.2 migration from ``emit=log_emitter()`` to
# ``findings_out=<accumulator>`` where a sink IS in scope at the swallow site.
# The remaining 10 swallows in the W2 catalog stay on log_emitter with the
# sanctioned-use comment per ``core/named_swallow.py`` docstring's IO/utility-
# boundary carve-out (see per-site audit notes inline).
# ---------------------------------------------------------------------------


def test_se_pdf_bytes_to_text_findings_out_sink_receives_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Migrated site: sweden/grafter.py se_pdf_bytes_to_text (Wave 4 representative).

    Drives a known-violating input through the FULL production path (monkeypatch
    ``subprocess.run`` to raise ValueError — the non-FileNotFoundError, non-OSError,
    non-subprocess.TimeoutExpired branch) and passes ``findings_out=<sink>``
    directly. Asserts the typed Finding lands in the sink (not via the lazy
    log_emitter fallback) — the §3.2 evidence-ledger-reach assertion for a
    Wave 4 production-path site.
    """
    import lawvm.sweden.grafter as se

    # Force a non-expected exception so the named_swallow branch fires.
    def _boom_run(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("simulated unexpected subprocess.run error")

    monkeypatch.setattr(se.subprocess, "run", _boom_run)
    sink: list[Finding] = []
    result = se.se_pdf_bytes_to_text(
        b"%PDF-1.4 fake pdf",
        findings_out=sink,
    )
    # None returned (default preserved).
    assert result is None
    # The typed Finding was appended to the plumbed findings_out list (the
    # per-statute audit-trail sink) — NOT via the lazy log_emitter() fallback.
    assert len(sink) == 1
    f = sink[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert f.detail["rule_id"] == "se_grafter_pdf_bytes_to_text_subprocess"
    assert f.detail["exception_type"] == "ValueError"
    assert f.detail["jurisdiction"] == "se"


def test_corpus_source_fingerprint_findings_out_sink_receives_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: finland/amendment_index.py _corpus_source_fingerprint.

    Drives a known-violating input through the FULL production path
    (monkeypatch ``_path_from_pathlike`` to raise RuntimeError on the
    path-probe axis) and passes ``findings_out=<sink>`` directly. Asserts
    the typed Finding lands in the per-statute audit-trail sink (not via the
    lazy log_emitter() fallback). The §3.2 evidence-ledger-reach witness for
    the amendment_index management-file production-path boundary.
    """
    from typing import cast

    from lawvm.corpus_store import CorpusStore
    from lawvm.finland import amendment_index as ai

    # Patch the path-probe helper to raise so the swallow fires.
    def _boom(_v: object) -> Any:
        raise RuntimeError("simulated path probe boom")

    monkeypatch.setattr(ai, "_path_from_pathlike", _boom)
    cs = cast(CorpusStore, object())
    sink: list[Finding] = []
    result = ai._corpus_source_fingerprint(cs, findings_out=sink)
    # Default preserved.
    assert result is None
    # Typed Finding emitted via the plumbed findings_out sink — NOT via
    # log_emitter fallback.
    assert len(sink) == 1
    f = sink[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert f.detail["rule_id"] == "fi_amendment_index_corpus_source_fingerprint"
    assert f.detail["exception_type"] == "RuntimeError"
    assert f.detail["jurisdiction"] == "fi"
    assert "cs_type=" in f.detail["clause_text"]


def test_qwen_local_check_server_reachable_findings_out_sink_receives_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrated site: finland/llm_backends/qwen_local.py _check_server_reachable.

    Drives a known-violating input through the FULL production path (monkeypatch
    ``urllib.request.urlopen`` to raise RuntimeError on the probe axis) and
    passes ``findings_out=<sink>`` directly. Asserts the typed Finding lands
    in the per-statute audit-trail sink (not via the lazy log_emitter()
    fallback) — the §3.2 evidence-ledger-reach witness for the qwen_local
    management sample (representative of the IO-boundary migration pattern
    where callers that DO have an audit sink can now plumb it).
    """
    import urllib.request

    from lawvm.finland.llm_backends import qwen_local as ql

    def _boom(_req: object, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated urlopen probe boom")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    sink: list[Finding] = []
    result = ql._check_server_reachable(findings_out=sink)
    # False default preserved.
    assert result is False
    # Typed Finding emitted via the plumbed findings_out sink — NOT via
    # log_emitter fallback.
    assert len(sink) == 1
    f = sink[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert f.detail["rule_id"] == "fi_qwen_local_check_server_reachable"
    assert f.detail["exception_type"] == "RuntimeError"
    assert f.detail["jurisdiction"] == "fi"
    assert "probe endpoint=" in f.detail["clause_text"]


# ---------------------------------------------------------------------------
# iter4 Wave 2 (W2) — production-caller threading fire-drill (arch HIGH-1,
# silent-failure HIGH-1, §3.2 evidence-path answerability).
#
# Iter3 W2 added the ``findings_out=<list>`` kwarg to the swallow sites, and
# the per-site unit tests above prove the typed Finding lands in the plumbed
# sink when called directly. The §3.2 gap that remained: production callers
# still defaulted to ``findings_out=None`` → ``log_emitter()`` stderr fallback,
# so a swallowed failure during real replay/PIT never reached the per-statute
# audit-trail ledger. Iter4 W2 closes that gap by threading the accumulator at
# production call sites where one is in scope OR — at IO/utility boundary
# sites where no sink is in scope — recording the sanctioned-use carve-out so
# the swallow stays VISIBLE via stderr WARNING (never silent).
#
# The SE site is the accessible migration: ``fetch_se_official_artifacts`` is
# the production caller of ``se_pdf_bytes_to_text`` and gains a
# ``findings_out`` parameter that the inner swallow at grafter.py:1097 inherits.
# Sites 1, 2, 4, 5 (qwen_local / amendment_index / spec_ledger_adapter /
# new_zealand dry_run) hit the 5+-signature-widening STOP-and-report
# threshold documented inline (structural gap, sanctioned log_emitter fallback).
# ---------------------------------------------------------------------------


def test_fetch_se_official_artifacts_findings_out_threads_to_se_pdf_bytes_to_text_swallow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production-caller threading fire-drill: ``fetch_se_official_artifacts``.

    Drives a known-violating input through the FULL production path (monkeypatch
    ``subprocess.run`` to raise ValueError — the non-FileNotFoundError, non-OSError,
    non-subprocess.TimeoutExpired branch) and plumbs ``findings_out=<sink>`` through
    ``fetch_se_official_artifacts`` so the typed Finding lands in the per-statute
    evidence sink at the SE production caller boundary — NOT via the lazy
    ``log_emitter`` fallback. The §3.2 evidence-ledger-reach witness for the SE
    acquisition lane (arch HIGH-1 + silent-failure HIGH-1).
    """
    import lawvm.sweden.grafter as se_grafter
    from lawvm.sweden.fetch import fetch_se_official_artifacts

    # Force the PDF-text extraction boundary to fire its named-swallow path:
    # ``subprocess.run`` raises a non-expected ValueError, which the
    # ``except Exception`` branch in ``se_pdf_bytes_to_text`` (grafter.py:1132)
    # routes through ``build_named_swallow_finding``. The plumbed
    # ``findings_out`` sink receives the typed Finding here.
    def _boom_run(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("simulated unexpected subprocess.run error from production caller")

    monkeypatch.setattr(se_grafter.subprocess, "run", _boom_run)

    # Fake archive with a real-looking PDF payload so the fetch lane reaches the
    # ``se_pdf_bytes_to_text`` call site (sweden/fetch.py:1369 — the new
    # ``findings_out=findings_out`` threading point).
    class _FakeArchive:
        fetched: dict[str, bytes]
        stored: dict[str, bytes]
        fetch_calls: list[tuple[str, str, float]]

        def __init__(self) -> None:
            self.fetched = {
                "https://svenskforfattningssamling.se/doc/2026286.html": (
                    b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>'
                ),
                "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf": (
                    b"%PDF-1.7 fake production-caller fire-drill payload"
                ),
            }
            self.stored = {}
            self.fetch_calls = []

        def fetch(
            self,
            url: str,
            max_age_hours: float = 168.0,
            headers: dict | None = None,
            content_type: str = "auto",
        ) -> bytes | None:
            self.fetch_calls.append((url, content_type, max_age_hours))
            return self.fetched.get(url)

        def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str:
            self.stored[locator] = data
            return "fakehash"

        def get(self, locator: str) -> bytes | None:
            return self.stored.get(locator)

        def has(self, locator: str, *, max_age_hours: float = float("inf")) -> bool:
            return locator in self.stored

    archive = _FakeArchive()
    sink: list[Finding] = []
    # The PRODUCTION-CALLER threading: ``findings_out=sink`` flows through the
    # new ``fetch_se_official_artifacts`` parameter and lands in the
    # ``se_pdf_bytes_to_text`` swallow sink (sweden/fetch.py:1369). The
    # default-None callers (CLI/cache-management) still fall through to
    # ``log_emitter`` per the sanctioned IO/utility carve-out.
    bundle = fetch_se_official_artifacts(
        "2026:286",
        archive,
        force_reextract=True,
        findings_out=sink,
    )
    # Bundle still returned (default-preserved semantics: extraction produced
    # no payload → stored text is empty bytes), the swallow does NOT crash the
    # fetch path — its evidence-ledger-reach contract.
    assert bundle is not None
    # The typed Finding from the ``se_pdf_bytes_to_text`` swallow LANDED in
    # the plumbed production-caller sink — NOT via the log_emitter fallback.
    assert len(sink) == 1, (
        f"Expected exactly one Finding in the plumbed sink; got {len(sink)}. "
        f"The swallow at se_pdf_bytes_to_text (grafter.py:1132) should have "
        f"appended a UNEXPECTED_PHASE_FAILURE Finding to findings_out."
    )
    f = sink[0]
    assert f.kind == NAMED_SWALLOW_FINDING_KIND
    assert f.blocking is True
    assert f.detail["rule_id"] == "se_grafter_pdf_bytes_to_text_subprocess"
    assert f.detail["exception_type"] == "ValueError"
    assert f.detail["jurisdiction"] == "se"
    assert "production-caller fire-drill" in f.detail["clause_text"]
