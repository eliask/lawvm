"""§2.9 production-lane guard-liveness for the UK identity-intrinsic probe.

The sweep (``lawvm.core.identity_intrinsic_audit.sweep_identity_intrinsic``)
backs registry rows **LS-12** ``APPLY.POSITIONAL_ID_LEAK`` and **LS-13**
``APPLY.SYNTHETIC_LABEL_LEAK`` (the structural enforcement of AGENTS.md §2.8 /
§2.9 test-6: *"provision/node identity is intrinsic and versioned, never
positional"*; *"synthetic markers never reach user output, persisted
artifacts, ``LegalAddress``, or ``ProvisionTimeline``"*). The walker is
generic over arbitrary nested Python structures so a frontend can hand it a
``LegalAddress``, an ``IRNode`` tree, a list of edges, a projection row, or
any mix — every string leaf in an identity-bearing slot is checked. Until
this commit the sweep had NO production-lane call site in
``src/lawvm/uk_legislation/`` (or ``src/lawvm/finland/``) — the §2.9
worst-case: a check that exists, is registered, passes review, and creates
false confidence in invisible coverage. The probe at
``src/lawvm/uk_legislation/identity_intrinsic_probe.py`` is the wire-in; it
is invoked from ``uk_amendment_replay.apply_ops`` (line ~1105 fold-exit)
behind an opt-in env flag so production UK bench replay output stays
byte-stable.

This test drives a known positional-id leak and a known synthetic-label leak
through the probe and asserts the matching ``uk_replay_positional_id_leak_
observed`` / ``uk_replay_synthetic_label_leak_observed`` adjudication fires
(production-reachable from the fold-exit call site). Strict enforcement stays
multi-session pending a UK ``strict_profile`` lane; the probe is the
discipline-disclosing first step.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.identity_intrinsic_probe import (
    probe_uk_identity_intrinsic,
    UK_POSITIONAL_ID_LEAK_KIND,
    UK_SYNTHETIC_LABEL_LEAK_KIND,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_POSITIONAL_KIND = UK_POSITIONAL_ID_LEAK_KIND
_SYNTHETIC_KIND = UK_SYNTHETIC_LABEL_LEAK_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_IDENTITY_INTRINSIC_PROBE"


def _section(label: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(IRNode(kind=IRNodeKind.P, label="", children=()),),
    )


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(sections))


def _statute(body: IRNode, *, statute_id: str, title: str = "") -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title=title,
        body=body,
        supplements=(),
        metadata={},
    )


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_adjudication_for_positional_id_leak() -> None:
    """Production-lane reachability: a stored ``code`` attribute carrying an
    ``expr#42`` positional-counter drives a ``uk_replay_positional_id_leak_
    observed`` adjudication through the probe — the live code path invoked
    from ``apply_ops`` fold-exit."""
    leaky = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        attrs={"code": "expr#42"},
        children=(),
    )
    statute = _statute(_body(leaky), statute_id="identity/1")
    adjudications: list[CompileAdjudication] = []
    probe_uk_identity_intrinsic(
        statute,
        adjudications_out=adjudications,
        source_statute="identity/1",
    )
    leaks = [a for a in adjudications if a.kind == _POSITIONAL_KIND]
    assert leaks, (
        "expected a uk_replay_positional_id_leak_observed adjudication for "
        "the leaked expr#42 code attribute, but none fired through the UK "
        "probe — the §2.9 guard is unreachable from UK production"
    )
    detail = leaks[0].detail
    assert detail["finding_kind"] == "APPLY.POSITIONAL_ID_LEAK"
    assert detail["vocab"] == "expr_counter"
    assert "expr#42" in detail["value"]
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert leaks[0].blocking is False


def test_probe_fires_adjudication_for_synthetic_label_leak() -> None:
    """Production-lane reachability: a section label carrying an AKN-style
    synthesized ``__n3`` ordinal drives a ``uk_replay_synthetic_label_leak_
    observed`` adjudication through the probe."""
    leaky = IRNode(
        kind=IRNodeKind.SECTION,
        label="section__n3",
        children=(),
    )
    statute = _statute(_body(leaky), statute_id="identity/2")
    adjudications: list[CompileAdjudication] = []
    probe_uk_identity_intrinsic(
        statute,
        adjudications_out=adjudications,
        source_statute="identity/2",
    )
    leaks = [a for a in adjudications if a.kind == _SYNTHETIC_KIND]
    assert leaks, (
        "expected a uk_replay_synthetic_label_leak_observed adjudication for "
        "the leaked __n3 section label, but none fired through the UK probe"
    )
    detail = leaks[0].detail
    assert detail["finding_kind"] == "APPLY.SYNTHETIC_LABEL_LEAK"
    assert detail["vocab"] == "synthetic_n_ordinal"
    assert "__n3" in detail["value"]
    assert detail["probe_mode"] == "observation_only"
    assert leaks[0].blocking is False


def test_probe_honours_source_rule_id_exemption() -> None:
    """Negative: a synthesized rule id under ``attrs.source_rule_id`` is the
    ONE sanctioned home per §2.9 — the probe MUST NOT fire synthetic-label
    for it. A positional id under that slot still fires (a positional id is
    never a legal identity, §2.8)."""
    sanctioned = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        attrs={"source_rule_id": "uk_synthetic_stitch__n7"},
        children=(),
    )
    statute = _statute(_body(sanctioned), statute_id="identity/3")
    adjudications: list[CompileAdjudication] = []
    probe_uk_identity_intrinsic(
        statute,
        adjudications_out=adjudications,
        source_statute="identity/3",
    )
    synth = [a for a in adjudications if a.kind == _SYNTHETIC_KIND]
    assert synth == [], (
        "attrs.source_rule_id is the sanctioned home for a synthesized rule "
        "id — synthetic-label leak must NOT fire here. Got: {}".format(synth)
    )


def test_probe_clean_statute_emits_nothing() -> None:
    """Negative: when the IRStatute carries no positional ids or synthetic
    markers, the probe MUST not fire. A gauge against false positives."""
    statute = _statute(
        _body(_section("1"), _section("2"), _section("3")),
        statute_id="identity/4",
        title="Clean Act 2026",
    )
    adjudications: list[CompileAdjudication] = []
    emitted = probe_uk_identity_intrinsic(
        statute,
        adjudications_out=adjudications,
        source_statute="identity/4",
    )
    assert emitted == []
    assert all(
        a.kind not in {_POSITIONAL_KIND, _SYNTHETIC_KIND} for a in adjudications
    )


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not emit on the same
    leaky input — production UK bench output stays byte-stable until a
    deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    leaky = IRNode(
        kind=IRNodeKind.SECTION,
        label="section__n3",
        attrs={"code": "expr#42"},
        children=(),
    )
    statute = _statute(_body(leaky), statute_id="identity/5")
    adjudications: list[CompileAdjudication] = []
    probe_uk_identity_intrinsic(
        statute,
        adjudications_out=adjudications,
        source_statute="identity/5",
    )
    leaks = [a for a in adjudications if a.kind in {_POSITIONAL_KIND, _SYNTHETIC_KIND}]
    assert leaks == [], (
        "probe must be default-off. Got: {}".format(leaks)
    )


def test_probe_skips_when_statute_is_none() -> None:
    """Degenerate input: a None statute must skip cleanly — no exception, no
    false finding, no probe record. Mirrors FI's defensive posture for
    production-safe diagnostic hooks."""
    out: list[CompileAdjudication] = []
    assert probe_uk_identity_intrinsic(None, adjudications_out=out) == []
    assert out == []


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops returns the unchanged base. The probe
    runs (env on), and because the base carries no leaks, nothing fires —
    proving the probe is wired into the production fold-exit and runs even
    when replay produces no structural change (the cheap default every bench
    replay hits)."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    base = _statute(_body(_section("1")), statute_id="identity/smoke/1")
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    leaks = [
        a for a in adjudications if a.kind in {_POSITIONAL_KIND, _SYNTHETIC_KIND}
    ]
    assert leaks == [], (
        "default no-op replay should not emit any identity-leak — got: "
        "{}".format(leaks)
    )


def test_probe_reachable_through_pipeline_apply_ops_fires_on_leak(
    monkeypatch,
) -> None:
    """End-to-end guard-liveness: a base statute carrying a positional-id leak
    in its body, run through ``UKReplayPipeline.apply_ops`` (the production
    lane, no synthetic shortcuts) with no ops, MUST emit the
    ``uk_replay_positional_id_leak_observed`` adjudication from the fold-exit
    call site. This is the §2.9 worst-case guard — a check that exists but is
    unreachable from production looks live in unit tests and passes review."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    leaky = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        attrs={"code": "expr#42"},
        children=(),
    )
    base = _statute(_body(leaky), statute_id="identity/smoke/2")
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    leaks = [a for a in adjudications if a.kind == _POSITIONAL_KIND]
    assert leaks, (
        "production-lane guard-liveness failed: a positional id leak in the "
        "base statute did not surface through the fold-exit probe — the §2.9 "
        "wire-in is unreachable from the real apply_ops path"
    )


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_identity_intrinsic`` is invoked on
    the UK replay fold-exit — i.e. the call site exists, not dead code.

    Pinned at the import + call-site because the silent-leak class is rare in
    the live corpus by construction (§2.8 is a discipline-against-regression
    rule, not an expected feature of replay output); the static line is the
    dumb-pinned version of "the wire-in landed", complementing the runtime
    probe tests above which exercise the call shape directly."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert "from lawvm.uk_legislation.identity_intrinsic_probe import" in src
    assert "probe_uk_identity_intrinsic(" in src
    assert "probe_uk_identity_intrinsic" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
