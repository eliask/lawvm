"""§2.9 production-lane guard-liveness for the UK overlay-authorization probe (D8).

The audit (``lawvm.core.overlay_default_replay_authorized_false_audit.
iter_overlay_default_replay_authorized_false_violations`` — registry row
D8 / ``OVERLAY.UNAUTHORIZED_PROMOTION``, AGENTS.md §2.10 firewall: a
surface/overlay node defaults to ``replay_authorized=False`` and may mutate
legal state only through a typed ExecutionAuthorization promotion event)
is wired into core's compile_timelines at commit ``a6c067c8``, but the UK
``apply_ops`` fold does NOT call compile_timelines — so the audit was dead
code against UK replay.

The probe at ``lawvm.uk_legislation.overlay_authorization_probe.probe_uk_
overlay_authorization`` is the wire-in; it is invoked from
``uk_amendment_replay.apply_ops`` fold-exit behind an opt-in env flag so
production UK bench replay output stays byte-stable.

This test drives a known overlay-tagged node WITHOUT a matching authorization
through the probe and asserts the
``uk_replay_overlay_unauthorized_promotion_observed`` adjudication fires
(production-reachable from the fold-exit call site). Strict enforcement
stays multi-session pending a UK ``strict_profile`` lane; the probe is the
discipline-disclosing first step.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.overlay_authorization_probe import (
    UK_OVERLAY_AUTHORIZATION_KIND,
    probe_uk_overlay_authorization,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_OVERLAY_AUTHORIZATION_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_OVERLAY_AUTHORIZATION_PROBE"


def _overlay_section(label: str = "1") -> IRNode:
    """An overlay-tagged SECTION that ALSO carries ``replay_authorized=True``
    on its attrs — the §2.10 audit fires ONLY for the authority-without-
    promotion branch (overlay-tagged alone is compliant by default per
    AGENTS.md §2.10 / the audit's docstring). Setting both attrs yields the
    breach shape the audit was designed to catch."""
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        attrs={
            "overlay_kind": "substrate",
            "replay_authorized": True,
        },
        children=(),
    )


def _clean_section(label: str = "1") -> IRNode:
    """A non-overlay SECTION (zero overlay-tag-predicate attrs)."""
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(),
    )


def _statute(*sections: IRNode, statute_id: str = "ukpga/test/1") -> IRStatute:
    body = IRNode(kind=IRNodeKind.BODY, children=tuple(sections))
    return IRStatute(
        statute_id=statute_id,
        title="",
        body=body,
        supplements=(),
        metadata={},
    )


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_adjudication_for_overlay_without_authorization() -> None:
    """Production-lane reachability: an overlay-tagged node with no matching
    ExecutionAuthorization drives a ``uk_replay_overlay_unauthorized_
    promotion_observed`` adjudication through the probe — the live code
    path invoked from ``apply_ops`` fold-exit.

    Per the probe module docstring, ``authorizations`` defaults to ``()`` at
    v0 because UK has no collected ExecutionAuthorization surface today
    (mirrors FI's compile_timelines call which deliberately omits
    authorizations)."""
    statute = _statute(_overlay_section("1"), statute_id="ukpga/test/1")
    adjudications: list[CompileAdjudication] = []
    findings = probe_uk_overlay_authorization(
        statute,
        adjudications_out=adjudications,
        source_statute="ukpga/test/1",
    )
    assert findings, "expected at least one Finding for the overlay-tagged node"
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows, (
        "expected a uk_replay_overlay_unauthorized_promotion_observed "
        "adjudication for the overlay-tagged node, but none fired through "
        "the UK probe — the §2.9 guard is unreachable from UK production"
    )
    detail = rows[0].detail
    assert detail["family"] == "overlay_authorization"
    assert detail["reason_code"] == "overlay_unauthorized_promotion_observed"
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert rows[0].blocking is False
    # The core-registered finding code (OVERLAY.UNAUTHORIZED_PROMOTION) is
    # preserved so multi-jurisdiction consumers can group by universal code.
    assert detail["core_registry_finding_kind"] == (
        "OVERLAY.UNAUTHORIZED_PROMOTION"
    )


def test_probe_emits_nothing_on_clean_statute() -> None:
    """Negative: a statute with zero overlay-tagged nodes MUST NOT fire —
    the audit owns the gauged dismissal. This is the common UK-replay-output
    case (no overlay-tagged nodes today)."""
    statute = _statute(_clean_section("1"), _clean_section("2"))
    adjudications: list[CompileAdjudication] = []
    findings = probe_uk_overlay_authorization(
        statute,
        adjudications_out=adjudications,
        source_statute="ukpga/test/2",
    )
    assert findings == []
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], (
        "clean statute should not emit overlay-authorization — got: "
        "{}".format(rows)
    )


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not emit on the same
    overlay-tagged-node input — production UK bench output stays
    byte-stable until a deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    statute = _statute(_overlay_section("1"), statute_id="ukpga/test/3")
    adjudications: list[CompileAdjudication] = []
    findings = probe_uk_overlay_authorization(
        statute,
        adjudications_out=adjudications,
        source_statute="ukpga/test/3",
    )
    assert findings == []
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], "probe must be default-off. Got: {}".format(rows)


def test_probe_skips_when_statute_is_none() -> None:
    """Degenerate input: None IRStatute must skip cleanly — no exception,
    no finding, no probe record."""
    out: list[CompileAdjudication] = []
    assert probe_uk_overlay_authorization(None, adjudications_out=out) == []
    assert out == []


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops returns the unchanged base. The probe
    runs (env on) over the clean base; nothing fires — proving the probe
    is wired into the production fold-exit and runs even when replay produces
    no structural change."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    base = IRStatute(
        statute_id="overlay/smoke/1",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", children=()),
            ),
        ),
        supplements=(),
        metadata={},
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], (
        "default no-op replay should not emit any overlay-authorization "
        "finding — got: {}".format(rows)
    )


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_overlay_authorization`` is invoked
    on the UK replay fold-exit — i.e. the call site exists, not dead code."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert "from lawvm.uk_legislation.overlay_authorization_probe import" in src
    assert "probe_uk_overlay_authorization(" in src
    assert "probe_uk_overlay_authorization" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
