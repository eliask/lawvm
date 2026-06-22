"""WAIST #10 — the FI projection/overlay plane carried on a PartitionResult.

The bare export-FI projectors (``_project_interlinks_for_statute`` /
``_project_overlays_for_statute``) used to return ``(rows, discarded_diagnostics)``.
They now return a :class:`FiProjectionResult` (a ``PartitionResult`` over the
emitted rows + typed ``residuals`` + a ``CoverageCertificate`` + the load-bearing
``NEUTRAL_AUTHORITY``). The production export entrypoint ``export_fi_interlinks``
READS the residual/coverage account and BRANCHES: it fail-louds blocking residue
to the export console and writes the coverage leaf so a silently-dropped universe
member becomes a recorded, checkable fact.
"""
from __future__ import annotations

import ast
import inspect
import json

from lawvm.core.stage_result import (
    NEUTRAL_AUTHORITY,
    CoverageCertificate,
    PartitionResult,
)


class _CleanStore:
    """A store whose statutes all resolve to (empty) XML — no dropped members."""

    def read_oracle(self, _statute_id: str) -> bytes:
        return b"<akomaNtoso/>"


class _AbsentStore:
    """A store that drops every statute (xml absent) — the silent-drop universe."""

    def read_oracle(self, _statute_id: str) -> None:
        return None


# ---------------------------------------------------------------------------
# (a) converted producer: accepted == bare rows; coverage is a partition.
# ---------------------------------------------------------------------------


def test_interlinks_producer_returns_partition_carrier() -> None:
    from lawvm.tools.export_fi_interlinks import (
        FiProjectionResult,
        _project_interlinks_for_statute,
    )

    projection = _project_interlinks_for_statute("711/2022", _CleanStore())
    assert isinstance(projection, FiProjectionResult)
    assert isinstance(projection, PartitionResult)
    # An empty <akomaNtoso/> body yields no interlink rows but is NOT a drop:
    # the statute XML was present, so coverage is a clean partition.
    assert projection.coverage.is_partition()
    assert projection.coverage.total == (
        len(projection.rows) + projection.coverage.residual + projection.coverage.violation
    )


def test_overlays_producer_returns_partition_carrier() -> None:
    from lawvm.tools.export_fi_interlinks import (
        FiProjectionResult,
        _project_overlays_for_statute,
    )

    projection = _project_overlays_for_statute("711/2022", _CleanStore())
    assert isinstance(projection, FiProjectionResult)
    assert projection.coverage.is_partition()
    assert tuple(projection.rows) == projection.accepted


# ---------------------------------------------------------------------------
# (b) clean statute -> violation == 0, no blocking residual; NEUTRAL_AUTHORITY.
# ---------------------------------------------------------------------------


def test_clean_statute_has_no_violation_and_no_blocking_residual() -> None:
    from lawvm.tools.export_fi_interlinks import _project_interlinks_for_statute

    projection = _project_interlinks_for_statute("711/2022", _CleanStore())
    assert projection.coverage.violation == 0
    assert projection.coverage.is_clean
    assert not projection.has_blocking_residual


def test_projection_authority_is_neutral_and_load_bearing() -> None:
    """A projection row is NOT a legal-state fact and carries NO replay authority
    (Pro §8 / §13.9). NEUTRAL_AUTHORITY is the firewall — the correct value here."""
    from lawvm.tools.export_fi_interlinks import _project_interlinks_for_statute

    projection = _project_interlinks_for_statute("711/2022", _CleanStore())
    assert projection.authority is NEUTRAL_AUTHORITY
    assert projection.authority.is_neutral
    # The firewall: a projection can never claim replay authority.
    assert projection.authority.replay_authorized is False


def test_dropped_universe_member_is_a_blocking_residual_not_a_silent_drop() -> None:
    from lawvm.tools.export_fi_interlinks import _project_interlinks_for_statute

    projection = _project_interlinks_for_statute("711/2022", _AbsentStore())
    assert projection.rows == ()
    # The dropped member is recorded — not silently swallowed.
    assert projection.coverage.violation == 1
    assert not projection.coverage.is_clean
    assert projection.has_blocking_residual
    assert any("statute_xml_absent" in r.reason for r in projection.residuals)


# ---------------------------------------------------------------------------
# (c) FIRE-DRILL — drive the PRODUCTION export entrypoint with a dropped member;
#     assert the export console emits the blocking residual AND the coverage leaf
#     records the violation. The branch is severed -> RED.
# ---------------------------------------------------------------------------


def test_fire_drill_export_entrypoint_branches_on_dropped_member(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from lawvm.tools import export_fi_interlinks

    # The production entrypoint resolves the corpus store; point it at a store
    # that drops every statute (xml absent) so the projectors record a blocking
    # universe-drop residual instead of silently emitting nothing.
    monkeypatch.setattr(export_fi_interlinks, "_load_corpus_store", lambda: _AbsentStore())

    count = export_fi_interlinks.export_fi_interlinks(
        [(1, "711/2022"), (2, "9/2023")],
        data_dir=str(tmp_path),
        use_parquet=False,
        workers=1,
    )

    # No rows emitted (every member dropped) — but the drop is now LOUD.
    assert count == 0

    captured = capsys.readouterr()
    # The export console (stderr) fail-louds the blocking residual.
    assert "blocking projection residual" in captured.err
    assert "statute_xml_absent" in captured.err

    # The coverage leaf records the violation (silent drop -> checkable fact).
    leaf = json.loads(
        (tmp_path / "lawvm_interlinks.coverage.json").read_text(encoding="utf-8")
    )
    assert leaf["omitted_row_count"] == 2
    assert leaf["is_clean"] is False
    assert leaf["row_count"] == 0

    overlay_leaf = json.loads(
        (tmp_path / "lawvm_surface_overlays.coverage.json").read_text(encoding="utf-8")
    )
    assert overlay_leaf["omitted_row_count"] == 2
    assert overlay_leaf["is_clean"] is False


def test_clean_export_entrypoint_writes_clean_coverage_leaf(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """The 0-delta companion to the fire-drill: a present-XML corpus produces a
    clean coverage leaf and NO blocking-residual console line."""
    from lawvm.tools import export_fi_interlinks

    monkeypatch.setattr(export_fi_interlinks, "_load_corpus_store", lambda: _CleanStore())

    export_fi_interlinks.export_fi_interlinks(
        [(1, "711/2022")],
        data_dir=str(tmp_path),
        use_parquet=False,
        workers=1,
    )

    captured = capsys.readouterr()
    assert "blocking projection residual" not in captured.err

    leaf = json.loads(
        (tmp_path / "lawvm_interlinks.coverage.json").read_text(encoding="utf-8")
    )
    assert leaf["omitted_row_count"] == 0
    assert leaf["is_clean"] is True


# ---------------------------------------------------------------------------
# Structural-contract ratchet: pin the producer return type + the entrypoint's
# residual/coverage branch (mirrors the Wave-1 conservation ratchets). RED if a
# call-site revert drops the branch back to a discarded-diagnostics list.
# ---------------------------------------------------------------------------


def _coverage_return_is_consumed(func: ast.FunctionDef) -> bool:
    """Whether the entrypoint CONSUMES the `_corpus_projection_coverage` return.

    Structural (AST) replacement for the old `inspect.getsource` substring grep
    (which a commented-out call kept GREEN). Asserts:
      1. a `_corpus_projection_coverage(...)` Call result is BOUND to a name;
      2. a `_write_coverage_leaf(...)` Call passes that bound name among its args
         (the coverage return is actually written, not dropped);
      3. a `_emit_projection_residual_branch(...)` Call exists (the residual lane
         is read, not merely referenced).
    RED if the coverage return is dropped or the residual branch is commented out.
    """
    coverage_names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_corpus_projection_coverage"
        ):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    coverage_names.add(tgt.id)
    if not coverage_names:
        return False

    written_names: set[str] = set()
    residual_branch_read = False
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "_write_coverage_leaf":
            written_names |= {a.id for a in node.args if isinstance(a, ast.Name)}
        if node.func.id == "_emit_projection_residual_branch":
            residual_branch_read = True
    # EVERY bound coverage return must be passed to a _write_coverage_leaf (a
    # commented-out write of ANY coverage leaf leaves its bound name unconsumed).
    coverage_all_written = coverage_names.issubset(written_names)
    return coverage_all_written and residual_branch_read


def test_entrypoint_reads_residual_and_coverage_branch() -> None:
    from lawvm.tools import export_fi_interlinks

    src = inspect.getsource(export_fi_interlinks.export_fi_interlinks)
    func = ast.parse(src).body[0]
    assert isinstance(func, ast.FunctionDef)
    # The entrypoint must CONSUME the coverage return (bind + pass to
    # _write_coverage_leaf) AND read the residual lane via
    # _emit_projection_residual_branch — structurally, not by substring.
    assert _coverage_return_is_consumed(func), (
        "export_fi_interlinks must bind the _corpus_projection_coverage return and "
        "pass it to _write_coverage_leaf, and call _emit_projection_residual_branch "
        "— else dropped universe members are silent again (a commented-out call "
        "must not keep this GREEN)."
    )


def test_producers_return_projection_carrier_annotation() -> None:
    from lawvm.tools import export_fi_interlinks

    for fn in (
        export_fi_interlinks._project_interlinks_for_statute,
        export_fi_interlinks._project_overlays_for_statute,
    ):
        ret = inspect.signature(fn).return_annotation
        assert ret in ("FiProjectionResult", export_fi_interlinks.FiProjectionResult), (
            f"{fn.__name__} must return FiProjectionResult (got {ret!r}); a bare "
            "(rows, diagnostics) tuple is a conservation regression."
        )


def test_coverage_certificate_is_the_canonical_core_type() -> None:
    from lawvm.tools.export_fi_interlinks import _project_interlinks_for_statute

    projection = _project_interlinks_for_statute("711/2022", _CleanStore())
    assert isinstance(projection.coverage, CoverageCertificate)
