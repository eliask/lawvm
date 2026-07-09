"""Determinism firewall: the replay/projection cone must not import an LLM client.

Fable 5 direction #5 — the cheap protective move that must land before LLM
adjudication spreads further.

THE INVARIANT
-------------
LawVM's epistemic stance requires byte-deterministic, replayable execution
(ratchet baselines, byte-identical self-consistency, falsifiable hypotheses). LLM
output — adjudication, vision transcription — may ONLY ever create typed candidate
proposals BELOW an assurance ceiling. The replay/projection path itself must stay
deterministic and must NOT import any LLM-consuming client module. Adjudication
results enter replay only as content-addressed, versioned records carrying the
adjudicator/model id in provenance — never via a live call from a replay-cone
module. See ``notes/DETERMINISM_FIREWALL.md``.

THE FIREWALL
------------
No module in the **replay/projection import cone** may import an LLM client
(``lawvm.finland.llm_backends.*``: ``llm_adjudicator``, ``vision_producer``,
``qwen_local``, and any future nemotron/docling sibling). The cone is rooted at
the per-jurisdiction replay engines + the neutral projection/graph/gate cores
(``scripts/inventory_module_roles.py:REPLAY_PROJECTION_CONE_ROOTS``) — NOT the
monolithic ``lawvm`` CLI, whose whole cone legitimately reaches the
``propose-claims`` tool that calls an LLM.

Structure mirrors ``tests/test_module_role_consistency.py`` /
``tests/test_deprecated_callsite_ratchet.py``: a production scanner
(``scripts/inventory_module_roles.py:firewall_report``), an allowlist dict
(``FIREWALL_ALLOWLIST``, empty today) for any consciously-permitted edge with a
rationale, and a monotone test that FAILS on any un-allowlisted violation. A
synthetic guard-liveness check drives the production predicate into its firing
state so the scanner cannot silently pass by finding nothing.

--affected BLIND SPOT: this is a WHOLE-GRAPH ratchet (it BFS-walks the entire
import graph). ``ci.sh --affected`` selects shards by touched path and will MISS
a firewall breach introduced by an edit outside the firewall's own files. Run it
explicitly after any merge that adds/moves a module in the replay cone or under
``finland.llm_backends``. Registered in ``notes/DISCIPLINE_GATES.md`` §F.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_module_roles.py"

# Consciously-permitted firewall edges (importer -> llm_client), each with a LOUD
# rationale — EXACTLY like ``DEAD_ALLOWLIST`` in test_module_role_consistency.py.
#
# EMPTY TODAY: the firewall currently holds — the only src importer of an LLM
# client (``lawvm.tools.cmd_propose_claims``, a lazy ``qwen_local`` import) is the
# manual-claims proposal tool, which is NOT in the replay/projection cone. Every
# entry here is a TRACKED DEBT, not a silent pass: an allowlisted edge means a
# deterministic replay-cone module reaches a live LLM, which violates the
# epistemic stance and must be paid down (route the adjudication result through a
# content-addressed record instead). Keys are ``"importer -> llm_client"``.
FIREWALL_ALLOWLIST: dict[str, str] = {}


def _load_inventory_module() -> Any:
    """Import scripts/inventory_module_roles.py (not a package module)."""
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_module_roles_firewall", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()

# One firewall scan reused across assertions (the scan is pure).
_REPORT = _INV.firewall_report(_REPO_ROOT)


def _edge_key(src_mod: str, target: str) -> str:
    return f"{src_mod} -> {target}"


# ---------------------------------------------------------------------------
# Scan integrity — the guard cannot silently pass by scanning nothing.
# ---------------------------------------------------------------------------


class TestFirewallScanIntegrity:
    def test_no_parse_failures(self) -> None:
        assert not _REPORT["parse_failures"], (
            "AST parse failures while building the import graph:\n  "
            + "\n  ".join(_REPORT["parse_failures"])
        )

    def test_cone_roots_all_resolve(self) -> None:
        """Every declared replay-cone root must resolve to a real module. A
        vanished/renamed root would silently SHRINK the protected cone (a module
        could then import an LLM client undetected)."""
        assert not _REPORT["missing_roots"], (
            "\n[DETERMINISM FIREWALL] replay/projection cone root(s) do NOT resolve "
            "to a real module — the protected cone silently shrank:\n  "
            + "\n  ".join(_REPORT["missing_roots"])
            + "\n\nFix REPLAY_PROJECTION_CONE_ROOTS in "
            "scripts/inventory_module_roles.py (a renamed/removed replay engine "
            "must be re-pinned, never dropped)."
        )

    def test_cone_is_substantial(self) -> None:
        """The BFS must actually reach the deterministic spine — a near-empty cone
        would make the firewall vacuously true."""
        assert len(_REPORT["cone"]) > 100, (
            f"replay/projection cone has only {len(_REPORT['cone'])} modules; the "
            "BFS is not reaching the replay spine — the firewall would pass "
            "vacuously. Check REPLAY_PROJECTION_CONE_ROOTS."
        )

    def test_llm_clients_are_real_modules(self) -> None:
        """The fence must name modules that actually EXIST — otherwise it guards
        nothing."""
        present = set(_REPORT["llm_client_modules"])
        for expected in _INV.LLM_CLIENT_MODULES:
            assert expected in present, (
                f"Fenced LLM client {expected!r} is not a real module in the tree; "
                "the firewall fence names a phantom. Fix LLM_CLIENT_MODULES in "
                "scripts/inventory_module_roles.py."
            )


# ---------------------------------------------------------------------------
# The firewall — no cone module imports an LLM client.
# ---------------------------------------------------------------------------


class TestDeterminismFirewall:
    def test_no_llm_client_in_replay_cone(self) -> None:
        offending = [(src, tgt) for src, tgt in _REPORT["offending_edges"]]
        unexplained = sorted(
            _edge_key(src, tgt)
            for src, tgt in offending
            if _edge_key(src, tgt) not in FIREWALL_ALLOWLIST
        )
        if unexplained:
            pytest.fail(
                "\n[DETERMINISM FIREWALL BREACH] replay/projection-cone module(s) "
                "import an LLM client — the deterministic replay/projection path "
                "must NEVER reach a live model:\n  "
                + "\n  ".join(unexplained)
                + "\n\nLLM output may only ever create typed candidate proposals "
                "below an assurance ceiling; adjudication results enter replay ONLY "
                "as content-addressed, versioned records carrying the model id in "
                "provenance (see notes/DETERMINISM_FIREWALL.md). Either:\n"
                "  (1) REMOVE the import — route the adjudication result through a "
                "content-addressed record instead of a live call, or\n"
                "  (2) if this is a consciously-permitted, TRACKED debt, add the "
                "edge to FIREWALL_ALLOWLIST with a loud rationale (it stays a "
                "tracked violation, never a silent pass)."
            )

    def test_allowlist_entries_are_live(self) -> None:
        """An allowlisted edge that is no longer a real offending edge is stale —
        it must be removed so the allowlist only ever names LIVE tracked debt
        (mirrors the one-way shrink discipline of the module-role ratchet)."""
        offending_keys = {
            _edge_key(src, tgt) for src, tgt in _REPORT["offending_edges"]
        }
        stale = sorted(set(FIREWALL_ALLOWLIST) - offending_keys)
        assert not stale, (
            "\n[DETERMINISM FIREWALL] FIREWALL_ALLOWLIST entr(y/ies) no longer "
            "correspond to a live offending edge (the debt was paid down — good):\n"
            "  " + "\n  ".join(stale)
            + "\n\nRemove the stale entr(y/ies) so the allowlist names only live "
            "tracked debt."
        )


# ---------------------------------------------------------------------------
# Guard liveness — drive the PRODUCTION predicate + edge computer into their
# firing state on synthetic inputs, so a firewall that finds nothing today still
# provably CAN fire. Mandatory per test_module_role_consistency.py /
# test_regex_ratchet.py.
# ---------------------------------------------------------------------------


class TestFirewallGuardLiveness:
    def test_is_llm_client_recognizes_exact_and_prefix(self) -> None:
        assert _INV._is_llm_client("lawvm.finland.llm_backends.qwen_local")
        # A future sibling under the fenced prefix is caught by default.
        assert _INV._is_llm_client("lawvm.finland.llm_backends.nemotron_client"), (
            "A newly-added llm_backends sibling must be fenced by the prefix arm "
            "(so a future nemotron/docling client cannot silently leak)."
        )
        # Non-clients are NOT flagged.
        assert not _INV._is_llm_client("lawvm.finland.replay_entrypoint")
        # The package __init__ itself carries no live-model code — not a client.
        assert not _INV._is_llm_client("lawvm.finland.llm_backends")

    def test_compute_firewall_edges_fires_on_synthetic_breach(self) -> None:
        """A synthetic cone module importing a synthetic LLM client MUST surface as
        an offending edge — the firewall's firing signal."""
        edges = {
            "lawvm.finland.replay_entrypoint": {
                "lawvm.finland.llm_backends.qwen_local",
                "lawvm.core.tree_ops",  # a normal, allowed edge
            },
        }
        cone = {"lawvm.finland.replay_entrypoint"}
        offending = _INV.compute_firewall_edges(edges, cone)
        assert offending == [
            (
                "lawvm.finland.replay_entrypoint",
                "lawvm.finland.llm_backends.qwen_local",
            )
        ], (
            "compute_firewall_edges must flag a cone module importing an LLM "
            "client (and only that edge) — the firewall's firing signal."
        )

    def test_compute_firewall_edges_ignores_out_of_cone_importer(self) -> None:
        """An LLM import from a module OUTSIDE the cone is not a firewall breach —
        the manual-claims proposal tool legitimately calls an LLM."""
        edges = {
            "lawvm.tools.cmd_propose_claims": {
                "lawvm.finland.llm_backends.qwen_local"
            },
        }
        cone: set[str] = set()  # the proposal tool is NOT in the replay cone
        assert _INV.compute_firewall_edges(edges, cone) == [], (
            "An out-of-cone LLM importer must NOT be a firewall breach (the "
            "propose-claims tool is a legitimate LLM consumer outside the "
            "deterministic replay path)."
        )
