"""Monotone ratchet over module ROLES: live / test_only_live / dead.

Makes "is this module dead / who consumes it" a committed, CI-enforced FACT
instead of folklore.  Mirrors ``tests/test_deprecated_callsite_ratchet.py``: a
production scanner (``scripts/inventory_module_roles.py``) derives the role of
every ``src/lawvm`` module from the import graph (BFS from the five real
``[project.scripts]`` entrypoints, augmented with the non-import registry edges),
a committed baseline (``tests/data/module_roles_baseline.json``) snapshots the
``dead`` and ``test_only_live`` populations, and this test FAILS if either
population GROWS.

The gate runs IMPORT-REACH ONLY — it does NOT require the ~900s replay-coverage
census.  It is the cheap, every-CI tier of the module-role mechanism.

Assertions (the design's "highest-ROI first build", §7):
  (a) MONOTONE — the ``dead`` and ``test_only_live`` sets may only SHRINK vs the
      baseline; a NEW dead/test-only module FAILS with the regenerate
      instruction.
  (b) NO-UNEXPLAINED-DEAD — any module classified ``dead`` that is neither in the
      baseline's acknowledged ``dead`` set nor in ``DEAD_ALLOWLIST`` FAILS.
  (c) TRAP FIXTURES — ``apply_promotion_chain`` classifies LIVE (via the
      FindingSpec.owner registry edge) and ``qwen_local`` is NOT dead (optional
      backend); plus synthetic guard-liveness over the production scan helpers.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_module_roles.py"

# Modules that are derived-``dead`` for a deliberate reason and not (yet) in the
# baseline.  Empty today: the baseline IS the acknowledged dead population, and
# the monotone rule already forbids growth.  This allowlist exists so a module
# can be intentionally retained dead WITHOUT re-baselining (e.g. a freshly added
# frontier module pending wiring), with the reason recorded here.
DEAD_ALLOWLIST: dict[str, str] = {
    "lawvm.core.cross_act_same_moment": (
        "Shared same-moment conflict detector frontier; synthetic coverage exists, "
        "production EE/UK wiring is intentionally deferred to the refactor wave."
    ),
    "lawvm.tools.temporal_holdout": (
        "Offline temporal-holdout experiment/reporting module added with synthetic "
        "coverage; production CLI wiring is intentionally deferred until the "
        "holdout corpus list and runtime budget are settled."
    ),
    # #186 CTSF Phase 3: ``ctsf_residual_report`` is no longer here — the new
    # ``core.ctsf_gate`` residual-set-diff gate statically imports it, and the gate
    # is wired into the ``lawvm ctsf-gate`` CLI subcommand, so both modules are now
    # production-reachable (LIVE) from the ``lawvm`` entrypoint. The allowlist entry
    # was removed (a legitimate one-way ratchet shrink: frontier wired to
    # production). ``ctsf_gate`` itself is LIVE from birth (CLI-reachable), never
    # dead, so it needs no allowlist entry.
    "lawvm.uk_legislation.pdf_layout_uk": (
        "UK PDF (#177) C19 marginal-note x-coordinate segmentation — reached only "
        "via pdf_grammar's dynamic pdfplumber path; synthetic coverage exists "
        "(test_uk_pdf_marginal_notes.py). Production wiring is INTENTIONALLY "
        "deferred to the bulk tier-1 PDF acquisition lane (the #177 follow-up)."
    ),
    "lawvm.uk_legislation.pdf_replay_base": (
        "UK PDF (#177) PDF-derived replay-base admission — re-shapes a segmented "
        "PDF body into an IRStatute for replay. Synthetic coverage exists "
        "(test_uk_pdf_marginal_notes.py); production wiring is INTENTIONALLY "
        "deferred to the bulk tier-1 PDF acquisition lane (the #177 follow-up)."
    ),
    # #186 load-bearing follow-up: sweden/norway/finland ``totalization_table`` are
    # no longer here — the grafters now statically import them and dispatch on
    # ``table.lookup(...)`` at their off-domain sites, so the tables (and the
    # neutral ``core.totalization`` type they build on) are LIVE, reachable from
    # the production apply paths. FINLAND (#206 tail): ``restructure_plan.py`` and
    # ``apply_typed_dispatch.py`` now read
    # ``FI_TOTALIZATION_TABLE.lookup(action, failure).code`` at the RENUMBER
    # self-relabel / dest-occupied and the REPEAL parent-absent sites (θ is the
    # single source of those three codes, byte-identical). FI's occupancy-
    # observation cells stay declared (routing N-A — not a single-cell partition),
    # but the module now has a production importer, so it is LIVE and its allowlist
    # entry was removed (a legitimate one-way ratchet shrink: frontier wired to
    # production, mirroring the SE/NO/label_algebra flips).
    # #186 load-bearing follow-up (#206 tail): ``finland.label_algebra`` is no
    # longer here — ``finland/merge.py`` now statically imports ``fi_label_sort_key``
    # and dispatches the container-section sibling-merge ORDERING through it (the
    # ``FI_LABEL_ALGEBRA.order`` operation), so the algebra is LIVE, reachable from
    # the production merge path — the FI analogue of EE routing its sibling-merge
    # sorts through ``ee_label_sort_key``. The allowlist entry + the
    # ``replay_exercised: false`` baseline row were flipped (a legitimate one-way
    # ratchet shrink: frontier wired to production). The generic ``insert_sorted``
    # reroute and the fresh-letter ``successor_set`` mint stay direct — see the
    # module docstring for why FI (built on ``_section_sort_key``, not the core
    # ``default_label_sort_key``) cannot re-route them byte-identically.
    "lawvm.us_federal.label_algebra": (
        "#186 §4.2 item 4 LabelAlgebra extended to U.S. federal as a DECLARED spec. "
        "US_LABEL_ALGEBRA mirrors the U.S. Code numeric-stem + ``106A`` letter-insert "
        "section-label calculus (parse / order / successor / collision) built from "
        "the SHARED kernel primitives the US frontend orders on "
        "(core.tree_ops.default_label_sort_key / normalized_label_key); the "
        "successor is synthesized from that decomposition (US has NO standalone "
        "next-section-label helper — an honest gap, structurally the letter "
        "transpose of EE's synthesized ``stem_(max+1)`` superscript successor). It "
        "is conformance-tested (test_label_algebra_us.py binds each op to that real "
        "code) but, parallel-first per #186, has no production importer yet — like "
        "FI, US's grafter still positions inserts via the shared "
        "default_label_sort_key. Routing the grafter through it is the load-bearing "
        "follow-up (the EE shape), deferred here."
    ),
    "lawvm.uk_legislation.label_algebra": (
        "#186 §4.2 item 4 LabelAlgebra extended to U.K. legislation as a DECLARED "
        "spec. UK_LABEL_ALGEBRA mirrors UK's numeric-stem + ``4A`` / ``4ZA`` "
        "letter-insert label calculus (parse / order / successor / collision) built "
        "from UK's ACTUAL primitives (ordering._label_sort_key / "
        "canonicalize._clean_num / source_parent_payloads._next_same_stem_alnum_label). "
        "It faithfully mirrors UK's REAL lexicographic order (4 < 4A < 4B < 4ZA), "
        "NOT the brief's ZA < A interstitial priority (which UK's ordering primitive "
        "does not encode — a declared-but-honest gap). It is conformance-tested "
        "(test_label_algebra_uk.py binds each op to that real code) but, "
        "parallel-first per #186, has no production importer yet — UK's grafter "
        "still positions inserts via _label_sort_key directly. Routing the grafter "
        "through it is the load-bearing follow-up (the EE shape), deferred here."
    ),
}


def _load_inventory_module() -> Any:
    """Import scripts/inventory_module_roles.py (not a package module)."""
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_module_roles", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.BASELINE_PATH
    assert path.exists(), (
        f"Missing module-role baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_module_roles.py --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# A single scan reused across the ratchet assertions (the scan is pure).
_STATE = _INV.scan_module_roles(_REPO_ROOT)


# ---------------------------------------------------------------------------
# Baseline integrity
# ---------------------------------------------------------------------------


class TestBaselineIntegrity:
    def test_baseline_counts_consistent(self) -> None:
        baseline = _load_baseline()
        counts = baseline["counts"]
        assert counts.get("dead", 0) == len(baseline["dead"]), (
            "Baseline dead count disagrees with the dead list; regenerate."
        )
        assert counts.get("test_only_live", 0) == len(baseline["test_only_live"]), (
            "Baseline test_only_live count disagrees with the list; regenerate."
        )

    def test_scan_has_no_parse_failures(self) -> None:
        assert not _STATE["parse_failures"], (
            "AST parse failures while building the import graph:\n  "
            + "\n  ".join(_STATE["parse_failures"])
        )

    def test_entrypoint_roots_present(self) -> None:
        # The five [project.scripts] roots must resolve to real modules; a
        # vanished root would silently shrink the live set.
        assert len(_STATE["roots"]) == 5, (
            f"Expected 5 [project.scripts] entrypoint roots, got "
            f"{_STATE['roots']}. A removed/renamed entrypoint would silently "
            "shrink the live set."
        )


# ---------------------------------------------------------------------------
# (a) The monotone ratchet — dead and test_only_live may only SHRINK.
# ---------------------------------------------------------------------------


class TestMonotoneShrinkOnly:
    def test_no_new_dead_module(self) -> None:
        baseline = _load_baseline()
        baseline_dead = set(baseline["dead"])
        current_dead = set(_STATE["dead"])
        new_dead = sorted(current_dead - baseline_dead - set(DEAD_ALLOWLIST))
        if new_dead:
            pytest.fail(
                "\n[MODULE-ROLE RATCHET] NEW dead module(s) — reachable from no "
                "production entrypoint, no test importer, no registry/optional "
                "edge:\n  "
                + "\n  ".join(new_dead)
                + "\n\nThe `dead` population is one-way shrink-only. Either:\n"
                "  (1) WIRE the module into a production/test consumer (it then "
                "leaves the dead set), or\n"
                "  (2) DELETE it, or\n"
                "  (3) if it is a deliberate frontier module pending wiring, add "
                "it to DEAD_ALLOWLIST with a reason, or\n"
                "  (4) regenerate the baseline to acknowledge it:\n"
                "      uv run python scripts/inventory_module_roles.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; the dead set may only shrink)."
            )

    def test_no_new_test_only_live_module(self) -> None:
        baseline = _load_baseline()
        baseline_tol = set(baseline["test_only_live"])
        current_tol = set(_STATE["test_only_live"])
        new_tol = sorted(current_tol - baseline_tol)
        if new_tol:
            pytest.fail(
                "\n[MODULE-ROLE RATCHET] NEW test-only-live module(s) — built and "
                "import-reachable but with NO production consumer:\n  "
                + "\n  ".join(new_tol)
                + "\n\nThe `test_only_live` population is one-way shrink-only "
                "(the unconsumed-producer set must not grow). Either wire the "
                "module to a production sink (promoting it to `live`), or — if "
                "this is a deliberate frontier addition — regenerate the "
                "baseline:\n"
                "      uv run python scripts/inventory_module_roles.py "
                "--update-baseline"
            )

    def test_baseline_must_be_lowered_when_dead_shrinks(self) -> None:
        """If a baselined dead/test-only module was wired or deleted, the baseline
        must be re-committed lower to lock the gain in (mirrors the deprecated
        ratchet's tighten-only direction)."""
        baseline = _load_baseline()
        current_dead = set(_STATE["dead"])
        current_tol = set(_STATE["test_only_live"])
        retired_dead = sorted(set(baseline["dead"]) - current_dead)
        retired_tol = sorted(set(baseline["test_only_live"]) - current_tol)
        if retired_dead or retired_tol:
            lines = []
            if retired_dead:
                lines.append("  dead retired: " + ", ".join(retired_dead))
            if retired_tol:
                lines.append("  test_only_live retired: " + ", ".join(retired_tol))
            pytest.fail(
                "\n[MODULE-ROLE RATCHET] Module(s) left the dead/test-only "
                "population — good work, but the baseline must be lowered to lock "
                "the gain in:\n"
                + "\n".join(lines)
                + "\n\nRegenerate and commit the baseline:\n"
                "      uv run python scripts/inventory_module_roles.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )


# ---------------------------------------------------------------------------
# (b) No unexplained dead module.
# ---------------------------------------------------------------------------


class TestNoUnexplainedDead:
    def test_every_dead_is_accounted(self) -> None:
        baseline = _load_baseline()
        baseline_dead = set(baseline["dead"])
        unexplained = sorted(
            m
            for m in _STATE["dead"]
            if m not in baseline_dead and m not in DEAD_ALLOWLIST
        )
        assert not unexplained, (
            "\n[MODULE-ROLE RATCHET] dead module(s) with no account (not in the "
            "baseline's acknowledged dead set, not in DEAD_ALLOWLIST):\n  "
            + "\n  ".join(unexplained)
            + "\n\nEvery dead module must be either wired, deleted, allowlisted, "
            "or acknowledged in a regenerated baseline."
        )


# ---------------------------------------------------------------------------
# (c) Trap fixtures — the edge-augmentation provably works.
# ---------------------------------------------------------------------------


class TestReplayDimension:
    """The replay dimension: replay_exercised is a GENERATED, SNAPSHOTTED fact.

    Import-reach answers "is this DEAD"; replay_exercised answers "does this RUN
    during replay".  Conflating them cost a 900s census to learn johtolause is
    ingest-phase (live, 0% replay) not dead.  These tests pin replay_exercised as
    a committed snapshot with flip-detection in BOTH directions and a guard
    fixture proving the dimension separates phase from deadness.
    """

    def test_replay_provenance_recorded(self) -> None:
        """The snapshot must carry provenance so it cannot go stale silently."""
        prov = _STATE["replay_provenance"]
        for field in ("sha256", "corpus", "refresh_command", "module_count"):
            assert prov.get(field), (
                f"replay-coverage snapshot provenance missing {field!r}; the "
                "snapshot must record where it came from + how to refresh it "
                "(tests/data/replay_coverage_snapshot.json)."
            )

    def test_replay_exercised_is_none_outside_finland(self) -> None:
        """Replay scope is the FI pipeline; non-Finland modules are N/A (None),
        never silently False."""
        rec = _STATE["modules"]["lawvm.core.observation_registry"]
        assert rec["replay_exercised"] is None, (
            "A non-Finland module is outside the replay census scope; its "
            "replay_exercised must be None (N/A), not False."
        )

    def test_replay_exercised_snapshot_no_flip(self) -> None:
        """Flip-detection (BOTH directions): a module changing replay-exercised
        status without --update-baseline FAILS.  A replay-path module silently
        falling off = regression signal; an ingest module suddenly replay-hit =
        also worth a look."""
        baseline = _load_baseline()
        committed: dict[str, bool] = baseline["replay_exercised"]
        current = {
            mod: rec["replay_exercised"]
            for mod, rec in _STATE["modules"].items()
            if rec["replay_exercised"] is not None
        }
        became_hot = sorted(
            m for m, v in current.items() if v and committed.get(m) is False
        )
        became_cold = sorted(
            m for m, v in current.items() if not v and committed.get(m) is True
        )
        new_in_scope = sorted(set(current) - set(committed))
        if became_hot or became_cold or new_in_scope:
            lines: list[str] = []
            if became_cold:
                lines.append(
                    "  replay-path modules that FELL OFF replay (cold now, "
                    "hot in snapshot) — possible regression:\n    "
                    + "\n    ".join(became_cold)
                )
            if became_hot:
                lines.append(
                    "  ingest modules now HIT by replay (hot now, cold in "
                    "snapshot) — worth a look:\n    " + "\n    ".join(became_hot)
                )
            if new_in_scope:
                lines.append(
                    "  Finland modules absent from the snapshot:\n    "
                    + "\n    ".join(new_in_scope)
                )
            pytest.fail(
                "\n[MODULE-ROLE REPLAY RATCHET] replay_exercised flipped vs the "
                "committed snapshot:\n"
                + "\n".join(lines)
                + "\n\nThe replay_exercised map is a snapshot. If this reflects a "
                "real change, refresh the replay-coverage snapshot "
                "(tests/data/replay_coverage_snapshot.json — see its "
                "provenance.refresh_command) and re-baseline:\n"
                "      uv run python scripts/inventory_module_roles.py "
                "--update-baseline"
            )

    def test_replay_counts_consistent(self) -> None:
        baseline = _load_baseline()
        rc = baseline["replay_counts"]
        re_map: dict[str, bool] = baseline["replay_exercised"]
        assert rc["in_scope"] == len(re_map)
        assert rc["exercised"] == sum(1 for v in re_map.values() if v)
        assert rc["cold"] == sum(1 for v in re_map.values() if not v)

    def test_live_replay_cold_population_named(self) -> None:
        """The 'live but ingest/analyze-phase' population is NAMED and non-empty —
        the thing that was confusing, now a checked fact."""
        cold = _STATE["live_replay_cold"]
        assert cold, "live-but-replay-cold set is unexpectedly empty."
        # It is dominated by the known ingest/analyze-phase subpackages.
        phase_prefixes = (
            "lawvm.finland.johtolause",
            "lawvm.finland.references",
            "lawvm.finland.legal_surface",
        )
        in_phase = [m for m in cold if m.startswith(phase_prefixes)]
        assert in_phase, (
            "The live-but-replay-cold set must contain the known ingest/"
            "analyze-phase modules (johtolause/references/legal_surface)."
        )

    def test_known_ingest_module_is_live_and_replay_cold(self) -> None:
        """GUARD FIXTURE: a known ingest-phase module classifies LIVE +
        replay_exercised=False — proving the dimension correctly distinguishes
        PHASE from DEADNESS.  references.defined_terms is import-reachable from a
        production consumer (live) yet 0% executed under replay (ingest-phase)."""
        rec = _STATE["modules"]["lawvm.finland.references.defined_terms"]
        assert rec["classification"] == "live", (
            f"references.defined_terms must be LIVE (production-imported), got "
            f"{rec!r}."
        )
        assert rec["replay_exercised"] is False, (
            f"references.defined_terms is ingest-phase: live but NOT exercised "
            f"under replay. Got replay_exercised={rec['replay_exercised']!r}. "
            "If replay now reaches it, refresh the snapshot + re-baseline."
        )
        assert rec["replay_exercised"] is not None, (
            "An ingest-phase Finland module must have a concrete replay_exercised "
            "fact (not None); the replay dimension applies to it."
        )


class TestStaticAnalysisTraps:
    def test_apply_promotion_chain_classifies_live(self) -> None:
        """Trap #1: zero import call sites, but production-live via the
        FindingSpec.owner registry-string edge. An import-graph BFS alone calls it
        DEAD; the registry-edge augmentation makes it LIVE."""
        record = _STATE["modules"]["lawvm.finland.apply_promotion_chain"]
        assert record["classification"] == "live", (
            f"apply_promotion_chain must classify LIVE via the FindingSpec.owner "
            f"registry edge, got {record!r}. The registry edge-augmentation "
            "regressed."
        )
        assert record["importer_kind"] == "registry", (
            f"apply_promotion_chain must be reached via the `registry` edge "
            f"(no import call site), got {record['importer_kind']!r}."
        )

    def test_qwen_local_is_not_dead(self) -> None:
        """Trap #2: env-gated optional backend, 0% coverage EXPECTED, not dead."""
        record = _STATE["modules"]["lawvm.finland.llm_backends.qwen_local"]
        assert record["classification"] != "dead", (
            f"qwen_local is an env-gated optional backend and must NOT classify "
            f"dead, got {record!r}."
        )

    def test_qwen_local_in_optional_backend_allowlist(self) -> None:
        assert (
            "lawvm.finland.llm_backends.qwen_local"
            in _INV.OPTIONAL_BACKEND_MODULES
        ), "qwen_local must be in the OPTIONAL_BACKEND_MODULES allowlist."


# ---------------------------------------------------------------------------
# Guard liveness — drive synthetic inputs through the PRODUCTION scan helpers so
# the gate provably (i) flags a newly-dead module, (ii) keeps a registry-edge
# module live, (iii) propagates reachability up to package markers. Mandatory per
# tests/test_regex_ratchet.py / test_deprecated_callsite_ratchet.py.
# ---------------------------------------------------------------------------


class TestRatchetGuardLiveness:
    def test_bfs_flags_isolated_module_dead(self) -> None:
        """A module reached by no edge is not in the reachable set."""
        edges = {"root": {"a"}, "a": set(), "orphan": set()}
        reached = _INV._bfs({"root"}, edges)
        assert "a" in reached
        assert "orphan" not in reached, (
            "BFS must NOT reach an isolated module — the dead-detection signal."
        )

    def test_bfs_follows_registry_edge_keeps_module_live(self) -> None:
        """A module with no import edge BUT a synthetic registry edge is reached —
        the apply_promotion_chain mechanism in miniature."""
        edges = {
            "root": {"observation_registry"},
            "observation_registry": {"owned_module"},  # synthetic registry edge
            "owned_module": set(),
        }
        reached = _INV._bfs({"root"}, edges)
        assert "owned_module" in reached, (
            "BFS must follow the synthetic registry edge to keep the owned module "
            "live (otherwise apply_promotion_chain falsely dies)."
        )

    def test_owner_string_resolves_unique_leaf(self) -> None:
        all_mods = {"lawvm.finland.apply_promotion_chain", "lawvm.core.x"}
        leaf_index = {
            "apply_promotion_chain": ["lawvm.finland.apply_promotion_chain"],
            "x": ["lawvm.core.x"],
        }
        resolved = _INV._resolve_owner(
            "apply_promotion_chain", all_mods, leaf_index
        )
        assert resolved == "lawvm.finland.apply_promotion_chain"

    def test_owner_string_relative_dotted_resolves(self) -> None:
        all_mods = {"lawvm.finland.apply_promotion_chain"}
        leaf_index = {"apply_promotion_chain": ["lawvm.finland.apply_promotion_chain"]}
        resolved = _INV._resolve_owner(
            "finland.apply_promotion_chain", all_mods, leaf_index
        )
        assert resolved == "lawvm.finland.apply_promotion_chain", (
            "A relative-dotted owner (RECORDED_DEAD form) must resolve by "
            "prepending the package."
        )

    def test_ambiguous_owner_string_is_unresolved(self) -> None:
        """An owner leaf shared by >1 module does NOT resolve — it becomes a typed
        residual, never silently bound to the wrong module."""
        all_mods = {"lawvm.a.grafter", "lawvm.b.grafter"}
        leaf_index = {"grafter": ["lawvm.a.grafter", "lawvm.b.grafter"]}
        resolved = _INV._resolve_owner("grafter", all_mods, leaf_index)
        assert resolved is None, (
            "An ambiguous owner leaf must NOT resolve (it is reported as a "
            "residual instead of being bound to an arbitrary module)."
        )
