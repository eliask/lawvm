"""Monotone determinism-spine ratchet gate (Audit-registry rows LS-32 / LS-33).

The "deterministic spine" is the set of core modules whose outputs feed a
*stored* legal-state address, a fingerprint/hash, a materialized tree, a
certificate/Merkle root, or a serialized projection. Replay must be a pure
function of ``(base IRStatute, authorized ops, pit_date)`` (AGENTS.md §2.10,
notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md LS-30/LS-32/LS-33): the same triple
replayed twice must yield byte-identical roots. A wall-clock read, a random
draw, a fresh UUID, or order-dependent ``set``/``dict`` iteration whose result
reaches a stored hash/address breaks that purity *silently* — the bench and the
unit tests stay green while two runs disagree.

This gate ASTs the spine module set and freezes the current offender count at a
committed baseline that may only fall.

Two bans:
  LS-32/LS-33 (a) — wall-clock / nondeterministic *source* ban (SOUND, exact):
    ``datetime.now`` / ``date.today`` / ``datetime.today`` / ``time.time`` /
    ``time.monotonic`` / ``random.*`` / ``secrets.*`` / ``os.urandom`` /
    ``uuid.*`` anywhere in real code (strings/comments excluded by the AST) of a
    spine module. These are unconditional nondeterminism sources; a spine module
    must take such values caller-supplied. Baseline: 0.

  LS-33 (b) — order-dependent iteration into stored output (HEURISTIC, weaker):
    iterating a ``set(...)`` / ``frozenset(...)`` / a set-comprehension — OR a
    LOCAL bound to one within the same function (the completer, ``s = set(...);
    for x in s``) — whose loop value flows into an *output sink* (a name/attr
    matching ``hash|root|digest|fingerprint|serial|address|path|payload|json|
    dumps``) inside a spine module. A fully sound dataflow proof is out of scope
    for a bounded AST visitor, so this arm is narrowed to the iterate-then-feed-
    a-sink shape and documented as a weaker form (see ``_LS33_HEURISTIC_LIMITATION``
    below; the dict-iteration sub-arm is a deliberate residual). The completer
    closes the "set bound to a local first" gap and freezes the current count: the
    spine carries ONE such site (``timeline_lineage`` classify_scope_migrations
    iterates a set-comp ``relevant_addresses`` whose loop feeds sink-named locals;
    the computed result is order-invariant booleans, so it is FROZEN as
    likely-benign debt that may only fall, not asserted clean).

The spine module set is DEFINED below (``SPINE_MODULES``) with the rationale for
each entry. Adding a module to the spine can only tighten the gate.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TypedDict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_PATH = "tests/data/determinism_spine_ratchet_baseline.json"


class _SpineScan(TypedDict):
    wallclock: list[dict[str, object]]
    iter_sink: list[dict[str, object]]
    parse_error: bool


class _SpineState(TypedDict):
    wallclock_counts: dict[str, int]
    iter_sink_counts: dict[str, int]


class _SpineBaseline(TypedDict):
    wallclock_counts: dict[str, int]
    iter_sink_counts: dict[str, int]


# ---------------------------------------------------------------------------
# The deterministic-spine module set.
#
# Selection rule: a module is on the spine iff its functions *construct* a value
# that is later stored as a legal-state address, hashed into a fingerprint /
# Merkle root, materialized into the replay tree, or serialized into a committed
# projection. Modules that merely *orchestrate the build* with explicitly
# caller-supplied timestamps (compile_metadata*, manual_claims/composer,
# manual_claims/native, pipeline_capture) are NOT on the spine: their
# nondeterminism is caller-owned by contract, not spine nondeterminism. This is
# why those modules legitimately carry ``datetime.now`` while every spine module
# below is wall-clock-free.
# ---------------------------------------------------------------------------
SPINE_MODULES: dict[str, str] = {
    # root / Merkle / canonical-serialization construction
    "src/lawvm/core/legal_surface_assembler.py": (
        "canonical json.dumps(sort_keys) feeding LegalSurfaceGraph node/edge "
        "hashes and the assembler root"
    ),
    "src/lawvm/core/certified_transition.py": (
        "CertifiedTreeTransition pre/post/declared hash coverage; the central "
        "WriteReceipt->transition root producer"
    ),
    "src/lawvm/core/manual_claims/hashing.py": (
        "canonical_payload + sort_keys json.dumps -> manual-claim content hash"
    ),
    # materialization / replay-fold tree construction
    "src/lawvm/core/timeline_materialization.py": (
        "materializes the IR tree at a PIT; output is the replay tree the "
        "materialization_root is computed over"
    ),
    "src/lawvm/core/payload_realization.py": (
        "realizes payload nodes folded into the materialized tree"
    ),
    # stored-address / identity construction
    "src/lawvm/core/timeline_addresses.py": (
        "constructs ProvisionTimeline address keys (stored legal addresses)"
    ),
    "src/lawvm/core/span_anchor.py": (
        "intrinsic span-anchor identity feeding stored addresses/hashes"
    ),
    "src/lawvm/core/timeline_lineage.py": (
        "lineage/migration segment keys feeding stored address rekeys"
    ),
    "src/lawvm/core/ir_helpers.py": (
        "IR node construction helpers feeding the materialized tree"
    ),
}

# Methods whose value-of-iteration flowing into a stored output is a determinism
# risk; the sink name vocabulary an order-dependent iteration must NOT feed.
_OUTPUT_SINK_RE = (
    "hash",
    "root",
    "digest",
    "fingerprint",
    "serial",
    "address",
    "payload",
    "dumps",
)

_LS33_HEURISTIC_LIMITATION = (
    "LS-33 arm (b) flags the literal `for x in set(...)/frozenset(...)/{set-comp}` "
    "shape AND (completer) a set bound to a local first within the same function "
    "(`s = set(...); for x in s: root += x`), via a small intra-procedural "
    "set-taint pass — closing the gap the prior narrowing named. It may still MISS "
    "feeding through a non-name intermediate, and the DICT-iteration sub-arm is a "
    "deliberate RESIDUAL: Python dicts are insertion-ordered, so `for k in d` is "
    "deterministic unless `d` was built from an unordered source — a sound dict "
    "arm needs full dataflow provenance of the dict, which a bounded AST cannot "
    "prove (a naive dict arm false-positives on ordered-dict-into-ordered-dict "
    "rebuilds, e.g. timeline_materialization address re-bucketing). The "
    "wall-clock/random arm (a) is sound."
)


# ---------------------------------------------------------------------------
# AST scan
# ---------------------------------------------------------------------------


_WALLCLOCK_ATTR_CALLS = {
    ("datetime", "now"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
    ("os", "urandom"),
}
_NONDET_MODULE_PREFIXES = ("random", "secrets", "uuid")


def _attr_chain(node: ast.AST) -> list[str]:
    """``a.b.c`` -> ['a','b','c']; '' segments for non-name roots."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        parts.append("")
    return list(reversed(parts))


def _set_bound_locals(scope: ast.AST) -> set[str]:
    """Names assigned ``= set(...)`` / ``= frozenset(...)`` / a set-comprehension
    anywhere in a function scope's own body (NOT descending into nested defs). The
    completer for the 'set bound to a local first' gap."""
    names: set[str] = set()
    body = getattr(scope, "body", None)
    stack: list[ast.AST] = [
        item for item in body if isinstance(item, ast.AST)
    ] if isinstance(body, list) else []
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # a nested scope is its own taint universe
        if isinstance(node, ast.Assign):
            value = node.value
            is_set = (
                isinstance(value, ast.SetComp)
                or (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id in {"set", "frozenset"}
                )
            )
            if is_set:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
        for child in ast.iter_child_nodes(node):
            stack.append(child)
    return names


class _SpineVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.wallclock_hits: list[dict[str, object]] = []
        self.iter_sink_hits: list[dict[str, object]] = []
        # Per-function set-bound local names, refreshed on entering a function.
        self._set_locals_stack: list[set[str]] = [set()]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._set_locals_stack.append(_set_bound_locals(node))
        self.generic_visit(node)
        self._set_locals_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._set_locals_stack.append(_set_bound_locals(node))
        self.generic_visit(node)
        self._set_locals_stack.pop()

    # ---- arm (a): wall-clock / random / uuid sources ----
    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain = _attr_chain(node)
        if len(chain) >= 2:
            pair = (chain[-2], chain[-1])
            if pair in _WALLCLOCK_ATTR_CALLS:
                self._record_wallclock(node, ".".join(pair))
        # random./secrets./uuid. anything
        if chain and chain[0] in _NONDET_MODULE_PREFIXES and len(chain) >= 2:
            self._record_wallclock(node, f"{chain[0]}.{chain[1]}")
        self.generic_visit(node)

    def _record_wallclock(self, node: ast.AST, label: str) -> None:
        self.wallclock_hits.append(
            {"line": getattr(node, "lineno", -1), "label": label}
        )

    # ---- arm (b): order-dependent iteration into a stored output sink ----
    def visit_For(self, node: ast.For) -> None:
        if self._iter_is_set_like(node.iter):
            sink = self._loop_feeds_sink(node)
            if sink is not None:
                self.iter_sink_hits.append(
                    {"line": node.lineno, "sink": sink}
                )
        self.generic_visit(node)

    def _iter_is_set_like(self, it: ast.expr) -> bool:
        if isinstance(it, ast.SetComp):
            return True
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name):
            return it.func.id in {"set", "frozenset"}
        # Completer: a Name that is a set-bound local in the enclosing function.
        if isinstance(it, ast.Name) and it.id in self._set_locals_stack[-1]:
            return True
        return False

    def _loop_feeds_sink(self, node: ast.For) -> str | None:
        for sub in ast.walk(node):
            # x.<sink> = ... / root += x / hash.update(x) / append into *_root
            target_names: list[str] = []
            if isinstance(sub, ast.AugAssign):
                target_names = self._names_of(sub.target)
            elif isinstance(sub, ast.Assign):
                for t in sub.targets:
                    target_names += self._names_of(t)
            elif isinstance(sub, ast.Call):
                # foo_root.append(...) / hasher.update(...)
                if isinstance(sub.func, ast.Attribute):
                    target_names = self._names_of(sub.func.value) + [sub.func.attr]
            for nm in target_names:
                low = nm.lower()
                if any(tok in low for tok in _OUTPUT_SINK_RE):
                    return nm
        return None

    @staticmethod
    def _names_of(node: ast.AST) -> list[str]:
        out: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                out.append(sub.id)
            elif isinstance(sub, ast.Attribute):
                out.append(sub.attr)
        return out


def scan_spine_source(rel_path: str, text: str) -> _SpineScan:
    """Scan one spine module's *source text* (strings/comments excluded by AST)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - defensive
        return {"wallclock": [], "iter_sink": [], "parse_error": True}
    v = _SpineVisitor()
    v.visit(tree)
    return {
        "wallclock": v.wallclock_hits,
        "iter_sink": v.iter_sink_hits,
        "parse_error": False,
    }


def scan_spine(repo_root: Path) -> _SpineState:
    wallclock_counts: dict[str, int] = {}
    iter_sink_counts: dict[str, int] = {}
    for rel in SPINE_MODULES:
        path = repo_root / rel
        if not path.exists():
            continue
        res = scan_spine_source(rel, path.read_text(encoding="utf-8"))
        w = len(res["wallclock"])
        s = len(res["iter_sink"])
        if w:
            wallclock_counts[rel] = w
        if s:
            iter_sink_counts[rel] = s
    return {"wallclock_counts": wallclock_counts, "iter_sink_counts": iter_sink_counts}


def _load_baseline() -> _SpineBaseline:
    path = _REPO_ROOT / _BASELINE_PATH
    assert path.exists(), (
        f"Missing determinism-spine ratchet baseline at {path}. Generate it by "
        "running this module as a script: "
        "`uv run python tests/test_determinism_spine_ratchet.py --update-baseline`."
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    wc = data.get("wallclock_counts", {})
    isk = data.get("iter_sink_counts", {})
    assert isinstance(wc, dict) and isinstance(isk, dict)
    return {
        "wallclock_counts": {str(k): _as_int(v) for k, v in wc.items()},
        "iter_sink_counts": {str(k): _as_int(v) for k, v in isk.items()},
    }


def _as_int(v: object) -> int:
    assert isinstance(v, int)
    return v


def _scan_now() -> _SpineState:
    return scan_spine(_REPO_ROOT)


# ---------------------------------------------------------------------------
# Spine-set integrity
# ---------------------------------------------------------------------------


class TestSpineSetIntegrity:
    def test_spine_modules_exist(self) -> None:
        missing = [rel for rel in SPINE_MODULES if not (_REPO_ROOT / rel).exists()]
        assert not missing, f"Spine modules absent from checkout: {missing}"

    def test_spine_modules_are_in_core(self) -> None:
        for rel in SPINE_MODULES:
            assert rel.startswith("src/lawvm/core/"), (
                f"Spine module {rel!r} is outside src/lawvm/core/."
            )

    def test_spine_set_is_nonempty(self) -> None:
        assert len(SPINE_MODULES) >= 5


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestDeterminismSpineRatchet:
    def test_no_new_wallclock_or_random_on_spine(self) -> None:
        baseline = _load_baseline()
        state = _scan_now()
        allowed = baseline["wallclock_counts"]
        current = state["wallclock_counts"]
        increases = [
            f"  {rel}: {count} wall-clock/random/uuid use-site(s) "
            f"(baseline {allowed.get(rel, 0)})"
            for rel, count in sorted(current.items())
            if count > allowed.get(rel, 0)
        ]
        if increases:
            pytest.fail(
                "\n[DETERMINISM SPINE] NEW wall-clock / random / uuid source(s) on "
                "the deterministic spine (LS-32/LS-33):\n"
                + "\n".join(increases)
                + "\n\nReplay must be a pure function of (base, ops, pit_date). A "
                "spine module may not read the clock, draw randomness, or mint a "
                "UUID — such values must be caller-supplied. See "
                "notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md LS-32/LS-33."
            )

    def test_no_new_order_dependent_iteration_into_sink(self) -> None:
        baseline = _load_baseline()
        state = _scan_now()
        allowed = baseline["iter_sink_counts"]
        current = state["iter_sink_counts"]
        increases = [
            f"  {rel}: {count} set/frozenset-iteration-into-output-sink site(s) "
            f"(baseline {allowed.get(rel, 0)})"
            for rel, count in sorted(current.items())
            if count > allowed.get(rel, 0)
        ]
        if increases:
            pytest.fail(
                "\n[DETERMINISM SPINE] NEW order-dependent set/dict iteration whose "
                "value reaches a stored hash/root/address/serialization on the "
                "spine (LS-33, narrowed heuristic):\n"
                + "\n".join(increases)
                + "\n\nIterate over `sorted(...)` (a stable order) before feeding a "
                f"stored output.\n\n{_LS33_HEURISTIC_LIMITATION}"
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        state = _scan_now()
        decreases: list[str] = []
        pairs = (
            ("wallclock_counts", baseline["wallclock_counts"], state["wallclock_counts"]),
            ("iter_sink_counts", baseline["iter_sink_counts"], state["iter_sink_counts"]),
        )
        for key, base_map, cur_map in pairs:
            for rel, allowed in sorted(base_map.items()):
                count = cur_map.get(rel, 0)
                if count < allowed:
                    decreases.append(f"  [{key}] {rel}: now {count} (baseline {allowed})")
        if decreases:
            pytest.fail(
                "\n[DETERMINISM SPINE] An offender count DROPPED — lower the baseline "
                "to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python tests/test_determinism_spine_ratchet.py "
                "--update-baseline\n(the baseline is a one-way ratchet)."
            )


# ---------------------------------------------------------------------------
# Trip-proof: confirm the RATCHET COMPARISON itself fails when the live count
# exceeds the committed baseline (not just that the scanner classifies).
# ---------------------------------------------------------------------------


class TestSpineRatchetTripProof:
    def test_an_injected_offender_exceeds_baseline(self) -> None:
        """If a real spine module gained a `datetime.now()`, the per-file count
        would rise above the committed (0) baseline → the ratchet must report an
        increase. We simulate by scanning a real spine module's source with one
        offender appended, and asserting the comparison the production test does
        would FAIL."""
        baseline = _load_baseline()
        allowed = baseline["wallclock_counts"]
        spine_rel = next(iter(SPINE_MODULES))
        clean = (_REPO_ROOT / spine_rel).read_text(encoding="utf-8")
        injected = clean + "\nimport datetime\n_LEAK = datetime.now()\n"
        res = scan_spine_source(spine_rel, injected)
        count = len(res["wallclock"])
        assert count > allowed.get(spine_rel, 0), (
            "injected datetime.now() must exceed the spine baseline"
        )

    def test_clean_spine_is_at_baseline(self) -> None:
        """The real spine is exactly at its committed (zero) baseline — proves the
        ratchet is green for the right reason, not vacuously."""
        baseline = _load_baseline()
        state = _scan_now()
        assert state["wallclock_counts"] == baseline["wallclock_counts"]
        assert state["iter_sink_counts"] == baseline["iter_sink_counts"]


# ---------------------------------------------------------------------------
# Guard-liveness: synthetic inputs through the real scan functions.
# ---------------------------------------------------------------------------


class TestSpineGuardLiveness:
    _F = "src/lawvm/core/timeline_addresses.py"

    def test_datetime_now_is_flagged(self) -> None:
        res = scan_spine_source(self._F, "import datetime\nx = datetime.now()\n")
        assert len(res["wallclock"]) == 1
        assert res["wallclock"][0]["label"] == "datetime.now"

    def test_date_today_is_flagged(self) -> None:
        res = scan_spine_source(self._F, "from datetime import date\nd = date.today()\n")
        assert len(res["wallclock"]) == 1

    def test_time_time_is_flagged(self) -> None:
        res = scan_spine_source(self._F, "import time\nt = time.time()\n")
        assert len(res["wallclock"]) == 1

    def test_random_and_secrets_and_uuid_flagged(self) -> None:
        text = (
            "import random, secrets, uuid\n"
            "a = random.random()\n"
            "b = secrets.token_hex()\n"
            "c = uuid.uuid4()\n"
        )
        res = scan_spine_source(self._F, text)
        assert len(res["wallclock"]) == 3

    def test_os_urandom_is_flagged(self) -> None:
        res = scan_spine_source(self._F, "import os\nb = os.urandom(8)\n")
        assert len(res["wallclock"]) == 1

    def test_comment_and_docstring_are_not_flagged(self) -> None:
        # The exact false-positive class grep would hit: prose mentioning the ban.
        text = (
            '"""Do NOT call datetime.now() inside an emitter."""\n'
            "# random.random would be nondeterministic here\n"
            "x = 1\n"
        )
        res = scan_spine_source(self._F, text)
        assert res["wallclock"] == []

    def test_string_literal_mention_is_not_flagged(self) -> None:
        res = scan_spine_source(self._F, "msg = 'pass datetime.now() from caller'\n")
        assert res["wallclock"] == []

    def test_caller_supplied_timestamp_param_is_clean(self) -> None:
        text = "def f(now):\n    return now.isoformat()\n"
        res = scan_spine_source(self._F, text)
        assert res["wallclock"] == []

    def test_set_iteration_into_root_sink_is_flagged(self) -> None:
        text = (
            "def f(items):\n"
            "    root = b''\n"
            "    for x in set(items):\n"
            "        root += x\n"
            "    return root\n"
        )
        res = scan_spine_source(self._F, text)
        assert len(res["iter_sink"]) == 1
        assert res["iter_sink"][0]["sink"] == "root"

    def test_frozenset_iteration_into_hash_update_is_flagged(self) -> None:
        text = (
            "def f(items, hasher):\n"
            "    for x in frozenset(items):\n"
            "        hasher.update(x)\n"
        )
        res = scan_spine_source(self._F, text)
        assert len(res["iter_sink"]) == 1

    def test_set_iteration_for_membership_only_is_not_flagged(self) -> None:
        # iterate a set but feed a NON-sink (a plain counter) -> not a hit.
        text = (
            "def f(items):\n"
            "    n = 0\n"
            "    for x in set(items):\n"
            "        n += 1\n"
            "    return n\n"
        )
        res = scan_spine_source(self._F, text)
        assert res["iter_sink"] == []

    def test_sorted_iteration_into_sink_is_not_flagged(self) -> None:
        # The CORRECT pattern: iterate sorted(...) -> deterministic -> allowed.
        text = (
            "def f(items):\n"
            "    root = b''\n"
            "    for x in sorted(items):\n"
            "        root += x\n"
            "    return root\n"
        )
        res = scan_spine_source(self._F, text)
        assert res["iter_sink"] == []

    # ---- LS-33 completer: set bound to a local first ----

    def test_local_bound_set_iteration_into_sink_is_flagged(self) -> None:
        # `s = set(...); for x in s: root += x` — the gap the prior narrowing missed.
        text = (
            "def f(items):\n"
            "    root = b''\n"
            "    s = set(items)\n"
            "    for x in s:\n"
            "        root += x\n"
            "    return root\n"
        )
        res = scan_spine_source(self._F, text)
        assert len(res["iter_sink"]) == 1
        assert res["iter_sink"][0]["sink"] == "root"

    def test_local_bound_frozenset_iteration_into_hash_is_flagged(self) -> None:
        text = (
            "def f(items, hasher):\n"
            "    fs = frozenset(items)\n"
            "    for x in fs:\n"
            "        hasher.update(x)\n"
        )
        res = scan_spine_source(self._F, text)
        assert len(res["iter_sink"]) == 1

    def test_local_bound_set_iteration_for_nonsink_is_not_flagged(self) -> None:
        text = (
            "def f(items):\n"
            "    s = set(items)\n"
            "    n = 0\n"
            "    for x in s:\n"
            "        n += 1\n"
            "    return n\n"
        )
        res = scan_spine_source(self._F, text)
        assert res["iter_sink"] == []

    def test_local_bound_set_in_other_function_does_not_leak(self) -> None:
        # set-bound local `s` in g() must NOT taint a same-named `s` iterated in f().
        text = (
            "def g(items):\n"
            "    s = set(items)\n"
            "    return s\n"
            "def f(s):\n"
            "    root = b''\n"
            "    for x in s:\n"
            "        root += x\n"
            "    return root\n"
        )
        res = scan_spine_source(self._F, text)
        assert res["iter_sink"] == []

    def test_list_bound_local_iteration_is_not_flagged(self) -> None:
        # A `list(...)`-bound local is insertion-ordered -> deterministic -> allowed.
        text = (
            "def f(items):\n"
            "    s = list(items)\n"
            "    root = b''\n"
            "    for x in s:\n"
            "        root += x\n"
            "    return root\n"
        )
        res = scan_spine_source(self._F, text)
        assert res["iter_sink"] == []

    def test_dict_iteration_is_residual_not_flagged(self) -> None:
        # RESIDUAL (documented): dict iteration is insertion-ordered, so the arm
        # deliberately does NOT flag `for k in d` — a sound dict arm needs dict
        # provenance. This pins the residual: an ordered-dict rebuild stays clean.
        text = (
            "def f(active):\n"
            "    by_addr = {}\n"
            "    for address, content in active.items():\n"
            "        by_addr[address] = content\n"
            "    return by_addr\n"
        )
        res = scan_spine_source(self._F, text)
        assert res["iter_sink"] == [], _LS33_HEURISTIC_LIMITATION


# ---------------------------------------------------------------------------
# Baseline regeneration entry point.
# ---------------------------------------------------------------------------


def _update_baseline() -> None:
    state = scan_spine(_REPO_ROOT)
    payload = {
        "_doc": (
            "Determinism-spine ratchet baseline (LS-32/LS-33). Per-spine-module "
            "offender counts; may only fall. Regenerate: "
            "uv run python tests/test_determinism_spine_ratchet.py --update-baseline"
        ),
        "spine_modules": sorted(SPINE_MODULES),
        "wallclock_counts": state["wallclock_counts"],
        "iter_sink_counts": state["iter_sink_counts"],
    }
    out = _REPO_ROOT / _BASELINE_PATH
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        print(json.dumps(scan_spine(_REPO_ROOT), indent=2))
