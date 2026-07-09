#!/usr/bin/env python3
"""Inventory the ROLE of every ``src/lawvm`` module: live / test_only_live / dead.

A monotone scan+baseline ratchet (mirrors ``scripts/inventory_deprecated_callsites.py``
and ``scripts/inventory_parser_smells.py``) that makes "is this module dead / who
consumes it" a committed, CI-enforced FACT instead of folklore.

WHAT IT DERIVES
---------------
For every module under ``src/lawvm/`` it computes:

  * ``reachable_from_entrypoint`` — is the module reachable, over the import graph,
    by a BFS from the five real ``[project.scripts]`` entrypoints (read from
    ``pyproject.toml`` at scan time, never hardcoded, so a removed entrypoint
    cannot silently shrink the live set)?
  * ``importer_kind`` — the strongest edge that reaches it:
    ``production`` | ``registry`` | ``drill_only`` | ``optional_backend`` |
    ``test_only`` | ``none``.
  * ``classification`` — ``live`` | ``test_only_live`` | ``dead``.

THE TWO STATIC-ANALYSIS TRAPS (encoded here so no future audit re-discovers them)
---------------------------------------------------------------------------------
A pure import-graph BFS produces two FALSE deads.  Both are fixed by augmenting
the graph with NON-import edges before the BFS:

  1. ``apply_promotion_chain`` has ZERO import call sites but IS production-live
     via registry STRINGS: it is named as ``FindingSpec.owner`` in
     ``core/observation_registry.py`` and as ``RECORDED_DEAD`` owner strings in
     ``core/fire_drill_registry.py``.  We add a synthetic edge from each registry
     module to the named owner module.  The ``FindingSpec.owner`` edge is a
     PRODUCTION edge (the registry is itself production-reachable); the
     ``RECORDED_DEAD`` edge is tagged ``drill_only`` (it keeps the module OUT of
     the dead set without faking production-live — the deadness is OWNED in
     ``fire_drill_registry.RECORDED_DEAD``).

  2. ``qwen_local`` is reached only by a lazy function-body import from
     ``tools.cmd_propose_claims`` and is tested only under
     ``@pytest.mark.requires_local_llm`` — 0% coverage but NOT dead.  Modules in
     the ``OPTIONAL_BACKEND_MODULES`` allowlist are tagged ``optional_backend``
     and excluded from the dead set.

Owner strings are short leaf tokens (e.g. ``"grafter"``) that are NOT always
uniquely resolvable to a module.  Resolution is best-effort against a
``{leaf_name -> module}`` map; an owner that resolves to exactly one real
``lawvm.*`` module yields a synthetic edge, and any owner that does NOT uniquely
resolve is reported as a typed residual (``unresolved_owner_residual``) — never
silently dropped.

CHEAP-CI MODE (no coverage run)
-------------------------------
The classification is import-reach + registry-edge only.  It does NOT require the
~900s replay-coverage census; the gate works without any coverage artifact.  A
module that is import-reachable but only from test/registry/optional edges (no
production importer) is ``test_only_live`` — the ~33-module surprise the census
found.  A module reachable from NO edge at all (and not an optional backend) is
``dead``.

The committed baseline (``tests/data/module_roles_baseline.json``) records the
current ``dead`` set and ``test_only_live`` set.  The companion test
(``tests/test_module_role_consistency.py``) FAILS if either set GROWS — both are
one-way shrink-only populations (wire it or delete it; never grow the
unconsumed-producer population).

THE REPLAY DIMENSION (``replay_exercised``)
-------------------------------------------
Import-reach answers "is this module DEAD"; it does NOT answer "does this module
run during REPLAY".  Those are different axes and conflating them cost a 900s
census to learn that ``johtolause.*`` is INGEST-phase (live, but 0% under
replay), not dead.  So for every ``lawvm.finland.*`` module we also emit
``replay_exercised: bool`` — generated, not declared: True iff the module has
ANY executed statement (``exec > 0``) in a full-corpus ``lawvm replay-all`` run
under coverage.

Threshold choice: ``exec > 0`` (no floor).  coverage.py records executed
PHYSICAL statement lines; a module never imported during replay records
``exec == 0`` even when it has hundreds of statements (e.g.
``johtolause.census_accounting``: 170 stmts, 0 exec).  The few small-exec
modules (2–5 exec over 4–23 stmts) are genuine partial replay execution, not
import noise — an arbitrary floor would falsely drop them.  ``exec > 0`` is the
honest "did any replay code path touch this module" signal.

The committed replay-coverage snapshot
(``tests/data/replay_coverage_snapshot.json``) carries the per-module
``{stmts, exec, pct}`` map plus PROVENANCE (source artifact, sha256, corpus,
mode, and the heavy refresh command) so the snapshot cannot go stale silently.
The cheap CI tier reads the committed snapshot (``--replay-coverage`` defaults to
it); only the heavy refresh re-runs ``replay-all --coverage`` — documented in the
snapshot's ``provenance.refresh_command``, NOT run here.

The population to NAME: ``classification == 'live' AND replay_exercised == False``
is the "live but ingest/analyze-phase" set (``johtolause/**``, ``references/**``,
``legal_surface/**``) — phase, not deadness.  The companion test snapshots
``replay_exercised`` and FAILS on any flip in EITHER direction without
``--update-baseline`` (a replay-path module silently falling off = regression
signal; an ingest module suddenly replay-hit = also worth a look).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]

BASELINE_PATH = Path("tests/data/module_roles_baseline.json")

# Committed replay-coverage snapshot (the cheap-CI input for replay_exercised).
REPLAY_COVERAGE_PATH = Path("tests/data/replay_coverage_snapshot.json")

_SRC_ROOT = Path("src")
_PKG = "lawvm"

# The replay dimension only applies to the Finland pipeline (replay-all replays
# the FI corpus; only ``lawvm.finland.*`` modules are in scope for the coverage
# census).  Modules outside this prefix get ``replay_exercised = None`` (N/A).
_REPLAY_SCOPE_PREFIX = "lawvm.finland."

# A module is replay-exercised iff it has STRICTLY MORE than this many executed
# statements in the replay-coverage snapshot.  ``> 0`` (no floor): coverage.py
# counts executed physical statement lines, so a module never imported during
# replay records exec==0 even with hundreds of statements; the few small-exec
# modules are genuine partial execution, not import noise.  See module docstring.
_REPLAY_EXEC_FLOOR = 0

# ---------------------------------------------------------------------------
# Trap #2: optional env-gated backends — lazy-import-only, 0% coverage EXPECTED,
# never dead.  Kept as a small explicit allowlist (the design's §4.1 step 2).
# ---------------------------------------------------------------------------
OPTIONAL_BACKEND_MODULES: frozenset[str] = frozenset(
    {
        "lawvm.finland.llm_backends.qwen_local",
        # Draft-HE adjudication + vision transcription backends: env-gated on a
        # local llama.cpp/OpenAI server (:8080), lazy-imported, 0% coverage
        # EXPECTED offline — optional, never dead (like qwen_local).
        "lawvm.finland.llm_backends.llm_adjudicator",
        "lawvm.finland.llm_backends.vision_producer",
        # Nemotron-Parse thin client: env-gated (LAWVM_NEMOTRON_PARSE_CMD) on
        # the process-isolated subprojects/nemotron_parse service; 0% coverage
        # EXPECTED offline — optional, never dead (like vision_producer).
        "lawvm.finland.llm_backends.nemotron_client",
    }
)

# Registry modules whose string fields name owner modules (the non-import edges).
_OBSERVATION_REGISTRY_MODULE = "lawvm.core.observation_registry"
_FIRE_DRILL_REGISTRY_MODULE = "lawvm.core.fire_drill_registry"


# ---------------------------------------------------------------------------
# Import-graph construction (AST over src/lawvm/**/*.py).
# Captures static imports, lazy function-body imports, and
# importlib.import_module("...") string targets.  Ported from the census engine
# (LawVM-census/.tmp/build_import_graph.py) and made deterministic + importable.
# ---------------------------------------------------------------------------


def _path_to_module(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(
    cur_mod: str, level: int, module: str | None, mod_to_path: dict[str, Path]
) -> str:
    base_parts = cur_mod.split(".")
    is_pkg = mod_to_path.get(cur_mod, Path("")).name == "__init__.py"
    pkg_parts = base_parts[:] if is_pkg else base_parts[:-1]
    up = level - 1
    if up > 0:
        pkg_parts = pkg_parts[:-up] if up <= len(pkg_parts) else []
    target = ".".join(pkg_parts)
    if module:
        target = target + "." + module if target else module
    return target


def build_import_graph(src_root: Path) -> dict[str, Any]:
    """Return {modules, edges, dynamic_targets, leaf_index, parse_failures}.

    ``edges`` maps importer-module -> sorted list of imported lawvm modules.
    ``leaf_index`` maps a leaf name -> sorted list of modules whose dotted tail
    is that leaf (used for best-effort owner-string resolution).
    """
    mod_to_path: dict[str, Path] = {}
    for path in sorted(src_root.rglob("*.py")):
        mod_to_path[_path_to_module(path, src_root)] = path

    all_mods = set(mod_to_path)
    edges: dict[str, set[str]] = {m: set() for m in all_mods}
    dynamic_targets: dict[str, set[str]] = {}
    parse_failures: list[str] = []

    for mod, path in sorted(mod_to_path.items()):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover
            parse_failures.append(f"{path}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name
                    if target in all_mods:
                        edges[mod].add(target)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    base = _resolve_relative(mod, node.level, node.module, mod_to_path)
                else:
                    base = node.module or ""
                if node.level == 0 and not base.startswith(_PKG):
                    continue
                if base in all_mods:
                    edges[mod].add(base)
                for alias in node.names:
                    sub = f"{base}.{alias.name}" if base else alias.name
                    if sub in all_mods:
                        edges[mod].add(sub)
            elif isinstance(node, ast.Call):
                fn = node.func
                name = (
                    fn.attr
                    if isinstance(fn, ast.Attribute)
                    else fn.id
                    if isinstance(fn, ast.Name)
                    else None
                )
                if name == "import_module" and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                        target = arg0.value
                        dynamic_targets.setdefault(mod, set()).add(target)
                        if target in all_mods:
                            edges[mod].add(target)

    # Ancestor-package edges: importing ``a.b.c`` executes the ``__init__`` of
    # every ancestor package (``a.b``, ``a``).  Python reaches the parent package
    # whenever a child module is imported, so a package ``__init__`` is live iff
    # any descendant is reachable.  Model this as an edge child -> parent-package
    # so the BFS propagates reachability UP to the package markers (otherwise a
    # consumed subpackage's ``__init__`` is a false dead).
    for mod in sorted(all_mods):
        parts = mod.split(".")
        for cut in range(1, len(parts)):
            ancestor = ".".join(parts[:cut])
            if ancestor in all_mods and ancestor != mod:
                edges[mod].add(ancestor)

    leaf_index: dict[str, list[str]] = defaultdict(list)
    for mod in all_mods:
        leaf_index[mod.split(".")[-1]].append(mod)

    return {
        "modules": sorted(all_mods),
        "edges": {m: sorted(v) for m, v in edges.items()},
        "dynamic_targets": {m: sorted(v) for m, v in dynamic_targets.items()},
        "leaf_index": {leaf: sorted(mods) for leaf, mods in leaf_index.items()},
        "parse_failures": parse_failures,
    }


# ---------------------------------------------------------------------------
# Entrypoint roots from pyproject.toml [project.scripts] (never hardcoded).
# ---------------------------------------------------------------------------


def entrypoint_roots(repo_root: Path, all_mods: set[str]) -> list[str]:
    """Return the [project.scripts] target modules that resolve to real modules."""
    pyproject = repo_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts: dict[str, str] = data.get("project", {}).get("scripts", {})
    roots: set[str] = set()
    for target in scripts.values():
        # target is "module.path:callable"
        module = target.split(":", 1)[0]
        if module in all_mods:
            roots.add(module)
    return sorted(roots)


# ---------------------------------------------------------------------------
# Non-import registry edges (the trap-fix).
# ---------------------------------------------------------------------------


def _resolve_owner(
    owner: str, all_mods: set[str], leaf_index: dict[str, list[str]]
) -> str | None:
    """Best-effort resolve an owner string to a single real lawvm module.

    Owners are short leaf tokens (``"grafter"``) or relative dotted paths
    (``"finland.apply_promotion_chain"``).  Returns the unique resolved module,
    or ``None`` if it does not uniquely resolve (caller records a residual).
    """
    # Fully-qualified already.
    if owner in all_mods:
        return owner
    # Relative dotted form: prepend the package.
    qualified = f"{_PKG}.{owner}"
    if qualified in all_mods:
        return qualified
    # Bare leaf token: resolve only when exactly one module has that leaf.
    candidates = leaf_index.get(owner, [])
    if len(candidates) == 1:
        return candidates[0]
    return None


def registry_edges(
    repo_root: Path, all_mods: set[str], leaf_index: dict[str, list[str]]
) -> dict[str, Any]:
    """Derive the non-import registry edges + the unresolved-owner residual.

    Returns:
      - ``production_edges``: {src_module: [owner_module, ...]} — FindingSpec.owner.
      - ``drill_edges``: {src_module: [owner_module, ...]} — RECORDED_DEAD owners.
      - ``unresolved_owner_residual``: sorted list of owner strings that did NOT
        uniquely resolve (typed residual, never silently dropped).
    """
    # Import the registries from the repo's src so we read the LIVE data, not a
    # stale copy.  The test drives this against the worktree's own src.
    src_dir = str((repo_root / _SRC_ROOT).resolve())
    added = src_dir not in sys.path
    if added:
        sys.path.insert(0, src_dir)
    try:
        from lawvm.core.fire_drill_registry import RECORDED_DEAD
        from lawvm.core.observation_registry import FINDING_REGISTRY
    finally:
        if added and sys.path and sys.path[0] == src_dir:
            sys.path.pop(0)

    production_owner_strings = sorted({spec.owner for spec in FINDING_REGISTRY.values()})
    drill_owner_strings = sorted({owner for owner, _reason in RECORDED_DEAD.values()})

    unresolved: set[str] = set()

    production_targets: set[str] = set()
    for owner in production_owner_strings:
        resolved = _resolve_owner(owner, all_mods, leaf_index)
        if resolved is None:
            unresolved.add(owner)
        else:
            production_targets.add(resolved)

    drill_targets: set[str] = set()
    for owner in drill_owner_strings:
        resolved = _resolve_owner(owner, all_mods, leaf_index)
        if resolved is None:
            unresolved.add(owner)
        else:
            drill_targets.add(resolved)

    prod_edges: dict[str, list[str]] = {}
    if _OBSERVATION_REGISTRY_MODULE in all_mods and production_targets:
        prod_edges[_OBSERVATION_REGISTRY_MODULE] = sorted(production_targets)

    drill_edges: dict[str, list[str]] = {}
    if _FIRE_DRILL_REGISTRY_MODULE in all_mods and drill_targets:
        drill_edges[_FIRE_DRILL_REGISTRY_MODULE] = sorted(drill_targets)

    return {
        "production_edges": prod_edges,
        "drill_edges": drill_edges,
        "unresolved_owner_residual": sorted(unresolved),
    }


# ---------------------------------------------------------------------------
# Replay-coverage snapshot (the replay dimension).
# ---------------------------------------------------------------------------


def load_replay_coverage(
    repo_root: Path, coverage_path: Path | None = None
) -> dict[str, Any]:
    """Load the committed replay-coverage snapshot.

    Returns ``{provenance, exec_by_module}`` where ``exec_by_module`` maps a
    module to its executed-statement count in the full-corpus replay run.  The
    cheap CI tier reads the committed snapshot; the heavy refresh re-runs
    ``replay-all`` under coverage (see ``provenance.refresh_command``).
    """
    path = (
        coverage_path
        if coverage_path is not None
        else repo_root / REPLAY_COVERAGE_PATH
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    coverage = data.get("coverage", {})
    exec_by_module = {
        mod: int(rec.get("exec", 0)) for mod, rec in coverage.items()
    }
    return {
        "provenance": data.get("provenance", {}),
        "exec_by_module": exec_by_module,
    }


def _replay_exercised(mod: str, exec_by_module: dict[str, int]) -> bool | None:
    """Generated fact: is ``mod`` exercised during full-corpus replay?

    ``None`` for modules outside the replay scope (non-Finland — the replay
    census does not cover them, so the question is N/A, not False).  ``True``
    iff the module's executed-statement count exceeds ``_REPLAY_EXEC_FLOOR``.
    A Finland module ABSENT from the snapshot is treated as not-exercised
    (False): the snapshot enumerates every Finland module the coverage run saw;
    a genuinely new Finland module missing from it is replay-cold until proven
    otherwise (and the flip ratchet will demand a refresh).
    """
    if not mod.startswith(_REPLAY_SCOPE_PREFIX):
        return None
    return exec_by_module.get(mod, 0) > _REPLAY_EXEC_FLOOR


# ---------------------------------------------------------------------------
# BFS reachability + classification.
# ---------------------------------------------------------------------------


def _bfs(roots: set[str], edges: dict[str, set[str]]) -> set[str]:
    seen = set(roots)
    queue = deque(roots)
    while queue:
        node = queue.popleft()
        for target in edges.get(node, ()):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


# ---------------------------------------------------------------------------
# DETERMINISM FIREWALL (Fable 5 #5): the replay/projection import cone must not
# import any LLM-consuming client module.  LLM output (adjudication, vision
# transcription) may only ever create typed candidate proposals BELOW an
# assurance ceiling; the replay/projection path itself stays byte-deterministic
# and must NEVER reach a live LLM client.  Adjudication results enter replay only
# as content-addressed, versioned records carrying the model id in provenance.
# See notes/DETERMINISM_FIREWALL.md.
# ---------------------------------------------------------------------------

# The LLM-consuming client modules to fence.  These are the modules that speak to
# a live model (llama.cpp/OpenAI-compat servers): the claim-proposal backend, the
# draft-HE adjudicator, and the vision transcriber.  A future nemotron/docling
# client lands here too.  Kept as an explicit prefix+exact set so a newly-added
# sibling under ``finland.llm_backends`` is fenced by default (prefix match).
LLM_CLIENT_PREFIX = "lawvm.finland.llm_backends."
LLM_CLIENT_MODULES: frozenset[str] = frozenset(
    {
        "lawvm.finland.llm_backends.llm_adjudicator",
        "lawvm.finland.llm_backends.vision_producer",
        "lawvm.finland.llm_backends.qwen_local",
    }
)

# The replay/projection import-cone roots.  DELIBERATELY NOT the [project.scripts]
# entrypoints: the monolithic ``lawvm`` CLI dispatcher reaches EVERYTHING
# (including the ``propose-claims`` tool that legitimately calls an LLM), so its
# whole cone is not "the replay/projection path".  Instead we root the cone at the
# per-jurisdiction replay engines + the neutral projection/graph-build/gate cores.
# The firewall protects THIS cone: the deterministic replay + projection spine.
#
# Kept as an explicit, reviewed list (never hardcoded silently) — a missing root
# is a guard-liveness risk, so ``firewall_report`` asserts every root resolves to
# a real module and fails loud otherwise.
REPLAY_PROJECTION_CONE_ROOTS: tuple[str, ...] = (
    # Finland replay engine + statute-graph build + apply pipeline.
    "lawvm.finland.replay_entrypoint",
    "lawvm.finland.replay_pipeline",
    "lawvm.finland.graph",
    # Neutral projection + certified-transition gate cores.
    "lawvm.core.branch_projection",
    "lawvm.core.ctsf_gate",
    "lawvm.core.replay_conservation",
    "lawvm.semantic.projection",
    # Per-jurisdiction replay engines.
    "lawvm.uk_legislation.uk_amendment_replay",
    "lawvm.eu.pipeline",
    "lawvm.new_zealand.actual_replay",
    "lawvm.norway.replay",
    "lawvm.us_federal.bench",
    "lawvm.replay_adjudication",
)


def _is_llm_client(mod: str) -> bool:
    """True iff ``mod`` is an LLM-consuming client (exact set or the fenced prefix).

    The prefix arm fences a newly-added ``finland.llm_backends`` sibling by
    default so a future nemotron/docling client cannot silently leak into the
    replay cone before anyone updates the exact set.  The package ``__init__``
    itself (``lawvm.finland.llm_backends`` with no trailing name) is NOT a client
    — it carries no live-model code — so the prefix requires a trailing segment.
    """
    if mod in LLM_CLIENT_MODULES:
        return True
    return mod.startswith(LLM_CLIENT_PREFIX) and mod != LLM_CLIENT_PREFIX.rstrip(".")


def compute_firewall_edges(
    edges: dict[str, set[str]], cone: set[str]
) -> list[tuple[str, str]]:
    """Return the offending edges (importer -> llm_client) INSIDE the cone.

    An offending edge is any import edge whose SOURCE is a replay/projection-cone
    module and whose TARGET is an LLM-client module.  Lazy function-body imports
    and ``importlib.import_module`` string targets are already folded into
    ``edges`` by ``build_import_graph``, so a lazily-imported client still counts
    (a deterministic cone must not reach a live LLM at all, eager OR lazy).
    """
    offending: list[tuple[str, str]] = []
    for src_mod in sorted(cone):
        for target in sorted(edges.get(src_mod, ())):
            if _is_llm_client(target):
                offending.append((src_mod, target))
    return offending


def firewall_report(
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Compute the determinism-firewall state over the replay/projection cone.

    Returns a dict with:
      - ``cone``: sorted list of modules in the replay/projection import cone.
      - ``offending_edges``: sorted list of ``[importer, llm_client]`` pairs where
        a cone module imports an LLM client (the firewall violations).
      - ``roots``: the cone roots actually used (all resolved).
      - ``missing_roots``: any declared root that did NOT resolve to a real module
        (a guard-liveness failure — the caller fails loud on a non-empty list).
      - ``llm_client_modules``: the fenced LLM-client modules that exist in the
        tree (sanity: the fence must actually name real modules).
      - ``parse_failures``: any AST parse failures from the graph build.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    src_root = root / _SRC_ROOT

    graph = build_import_graph(src_root)
    all_mods = set(graph["modules"])
    edges: dict[str, set[str]] = {m: set(v) for m, v in graph["edges"].items()}
    for m in all_mods:
        edges.setdefault(m, set())

    present_roots = [r for r in REPLAY_PROJECTION_CONE_ROOTS if r in all_mods]
    missing_roots = [r for r in REPLAY_PROJECTION_CONE_ROOTS if r not in all_mods]

    cone = _bfs(set(present_roots), edges)
    offending = compute_firewall_edges(edges, cone)

    present_clients = sorted(m for m in all_mods if _is_llm_client(m))

    return {
        "cone": sorted(cone),
        "offending_edges": [[src_mod, tgt] for src_mod, tgt in offending],
        "roots": sorted(present_roots),
        "missing_roots": sorted(missing_roots),
        "llm_client_modules": present_clients,
        "parse_failures": graph["parse_failures"],
    }


def scan_module_roles(
    repo_root: Path | None = None, coverage_path: Path | None = None
) -> dict[str, Any]:
    """Classify every ``src/lawvm`` module by role.  Import-reach + replay dim.

    Classification is import-reach only (no coverage run needed).  The
    ``replay_exercised`` field is read from the committed replay-coverage
    snapshot (``coverage_path``, default ``tests/data/replay_coverage_snapshot``)
    — a FACT, not a re-run of the ~900s census.

    Returns a dict with:
      - ``modules``: {module: {reachable_from_entrypoint, importer_kind,
        classification, replay_exercised}}
      - ``dead``: sorted list of modules classified ``dead``.
      - ``test_only_live``: sorted list of modules classified ``test_only_live``.
      - ``live_replay_cold``: sorted list of ``live`` modules with
        ``replay_exercised == False`` (the ingest/analyze-phase population).
      - ``roots``: the entrypoint roots used.
      - ``replay_provenance``: the snapshot's provenance block.
      - ``unresolved_owner_residual``: owner strings that did not uniquely resolve.
      - ``parse_failures``: any AST parse failures (should be empty).
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    src_root = root / _SRC_ROOT

    replay = load_replay_coverage(root, coverage_path)
    exec_by_module: dict[str, int] = replay["exec_by_module"]

    graph = build_import_graph(src_root)
    all_mods = set(graph["modules"])
    leaf_index = graph["leaf_index"]
    edges: dict[str, set[str]] = {m: set(v) for m, v in graph["edges"].items()}
    for m in all_mods:
        edges.setdefault(m, set())

    reg = registry_edges(root, all_mods, leaf_index)

    # Production edge set: import edges + FindingSpec.owner registry edges.
    prod_edges: dict[str, set[str]] = {m: set(v) for m, v in edges.items()}
    for src_mod, targets in reg["production_edges"].items():
        prod_edges.setdefault(src_mod, set()).update(targets)

    # Full edge set adds the drill_only edges (so drill-reached modules are not
    # "dead", but they reach via a drill-tagged edge, so they are not production).
    full_edges: dict[str, set[str]] = {m: set(v) for m, v in prod_edges.items()}
    for src_mod, targets in reg["drill_edges"].items():
        full_edges.setdefault(src_mod, set()).update(targets)

    roots = set(entrypoint_roots(root, all_mods))

    production_reach = _bfs(roots, prod_edges)
    full_reach = _bfs(roots, full_edges)

    # Reverse import edges (pure imports, no registry edges) for importer_kind.
    rev: dict[str, set[str]] = defaultdict(set)
    for m, ts in edges.items():
        for t in ts:
            rev[t].add(m)

    # Modules that gain a registry production edge from observation_registry.
    registry_targets = set(reg["production_edges"].get(_OBSERVATION_REGISTRY_MODULE, []))
    drill_targets = set(reg["drill_edges"].get(_FIRE_DRILL_REGISTRY_MODULE, []))

    modules: dict[str, dict[str, Any]] = {}
    for mod in sorted(all_mods):
        is_optional = mod in OPTIONAL_BACKEND_MODULES
        in_production = mod in production_reach
        in_full = mod in full_reach
        live_importers = {i for i in rev.get(mod, set()) if i in production_reach}

        # importer_kind: the strongest edge reaching the module.
        if mod in roots or (in_production and live_importers):
            importer_kind = "production"
        elif in_production and mod in registry_targets:
            # Reached only via the FindingSpec.owner registry edge.
            importer_kind = "registry"
        elif is_optional:
            importer_kind = "optional_backend"
        elif in_full and mod in drill_targets and not in_production:
            importer_kind = "drill_only"
        elif rev.get(mod):
            # Has importers but none production-reachable (test/other-phase only).
            importer_kind = "test_only"
        else:
            importer_kind = "none"

        # classification.
        if is_optional:
            classification = "live"  # optional backend is not dead
        elif importer_kind in ("production", "registry"):
            classification = "live"
        elif importer_kind == "drill_only":
            classification = "live"  # owned by RECORDED_DEAD; reachable, not dead
        elif importer_kind == "test_only":
            classification = "test_only_live"
        else:
            classification = "dead"

        modules[mod] = {
            "reachable_from_entrypoint": in_production,
            "importer_kind": importer_kind,
            "classification": classification,
            "replay_exercised": _replay_exercised(mod, exec_by_module),
        }

    dead = sorted(m for m, r in modules.items() if r["classification"] == "dead")
    test_only_live = sorted(
        m for m, r in modules.items() if r["classification"] == "test_only_live"
    )
    # The phase-disambiguated population: live, but cold under replay (ingest /
    # analyze-phase modules — johtolause/**, references/**, legal_surface/**).
    live_replay_cold = sorted(
        m
        for m, r in modules.items()
        if r["classification"] == "live" and r["replay_exercised"] is False
    )

    return {
        "modules": modules,
        "dead": dead,
        "test_only_live": test_only_live,
        "live_replay_cold": live_replay_cold,
        "roots": sorted(roots),
        "optional_backend_modules": sorted(OPTIONAL_BACKEND_MODULES),
        "unresolved_owner_residual": reg["unresolved_owner_residual"],
        "replay_provenance": replay["provenance"],
        "parse_failures": graph["parse_failures"],
    }


# ---------------------------------------------------------------------------
# Baseline payload + ratchet helpers.
# ---------------------------------------------------------------------------


def _baseline_payload(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_module_roles(repo_root)
    counts: dict[str, int] = defaultdict(int)
    for record in state["modules"].values():
        counts[record["classification"]] += 1

    # The committed replay_exercised snapshot: {module: bool} over every module
    # in replay scope (replay_exercised is not None).  This is the snapshot the
    # flip-detection ratchet pins; a True<->False flip without --update-baseline
    # fails (either direction is meaningful).
    replay_exercised = {
        mod: record["replay_exercised"]
        for mod, record in sorted(state["modules"].items())
        if record["replay_exercised"] is not None
    }
    replay_counts = {
        "exercised": sum(1 for v in replay_exercised.values() if v),
        "cold": sum(1 for v in replay_exercised.values() if not v),
        "in_scope": len(replay_exercised),
    }

    return {
        "_doc": (
            "Monotone module-role baseline. The `dead` and `test_only_live` sets "
            "may only SHRINK: a module born dead must be wired or deleted, never "
            "added to the population. The `replay_exercised` map is a SNAPSHOT: a "
            "module flipping replay-exercised status (either direction) fails the "
            "ratchet until re-baselined. Regenerate with `uv run python "
            "scripts/inventory_module_roles.py --update-baseline` after "
            "legitimately retiring (deleting) a dead/test-only module, wiring it "
            "to a production consumer, or refreshing the replay-coverage snapshot "
            "(tests/data/replay_coverage_snapshot.json — see its "
            "provenance.refresh_command)."
        ),
        "counts": dict(sorted(counts.items())),
        "replay_counts": replay_counts,
        "dead": state["dead"],
        "test_only_live": state["test_only_live"],
        "live_replay_cold": state["live_replay_cold"],
        "roots": state["roots"],
        "optional_backend_modules": state["optional_backend_modules"],
        "unresolved_owner_residual": state["unresolved_owner_residual"],
        "replay_provenance": state["replay_provenance"],
        "replay_exercised": replay_exercised,
    }


def update_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / BASELINE_PATH
    payload = _baseline_payload(root)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the committed module-role baseline JSON.",
    )
    parser.add_argument(
        "--replay-coverage",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Replay-coverage snapshot to read for replay_exercised (default: "
            "the committed tests/data/replay_coverage_snapshot.json). The cheap "
            "CI tier uses the default; the heavy refresh re-runs replay-all "
            "under coverage (see the snapshot's provenance.refresh_command)."
        ),
    )
    parser.add_argument(
        "--firewall",
        action="store_true",
        help=(
            "Report the determinism-firewall state: the replay/projection import "
            "cone and any offending edge where a cone module imports an LLM "
            "client. Exit 1 if the firewall is breached (see "
            "notes/DETERMINISM_FIREWALL.md)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.update_baseline:
        out_path = update_baseline()
        print(f"Wrote module-role baseline: {out_path}")
        return 0
    if args.firewall:
        report = firewall_report()
        print(
            f"replay/projection cone: {len(report['cone'])} modules "
            f"(roots: {len(report['roots'])})"
        )
        if report["missing_roots"]:
            print("  MISSING cone roots (guard-liveness failure):")
            for r in report["missing_roots"]:
                print(f"    {r}")
        offending = report["offending_edges"]
        if offending:
            print(f"  FIREWALL BREACH: {len(offending)} offending edge(s):")
            for src_mod, tgt in offending:
                print(f"    {src_mod} -> {tgt}")
            return 1
        print("  firewall holds: no cone module imports an LLM client.")
        return 1 if report["missing_roots"] else 0
    state = scan_module_roles(coverage_path=args.replay_coverage)
    counts: dict[str, int] = defaultdict(int)
    for record in state["modules"].values():
        counts[record["classification"]] += 1
    for klass in sorted(counts):
        print(f"{counts[klass]:5d}  {klass}")
    print(f"  dead: {len(state['dead'])}  test_only_live: {len(state['test_only_live'])}")
    n_scope = sum(
        1 for r in state["modules"].values() if r["replay_exercised"] is not None
    )
    n_exercised = sum(
        1 for r in state["modules"].values() if r["replay_exercised"] is True
    )
    print(
        f"  replay_exercised: {n_exercised}/{n_scope} in-scope finland modules; "
        f"live-but-replay-cold: {len(state['live_replay_cold'])}"
    )
    if state["unresolved_owner_residual"]:
        print(
            f"  unresolved owner residual: "
            f"{len(state['unresolved_owner_residual'])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
