"""``lawvm.core.replay_determinism_audit`` — F ``REPLAY.NONDETERMINISM``.

The universal "run it twice, prove byte-identity" guarantee. Replay must be a
**pure function** of its inputs: materializing the same ``(base_state, ops,
pit)`` twice (or N times) must yield byte-identical output and an identical
content hash, with no hidden-state leakage (no wall-clock stamp, unordered
``set`` iteration, un-seeded ``random``, or pre-insertion-order ``dict``
assumption bleeding into the result). This harness asserts that property over an
*injected materialize callable*, so any frontend's replay (FI / UK / EE) can be
fed in without this module knowing the frontend's shape.

WHY A NEW SURFACE (do-not-duplicate, AGENTS.md §2.6). Determinism is guarded
piecewise today — the corpus-xml ratchet pins one frontend's serialized output,
the FI certificate bundle (``tools/certificate_bundle.py``) hashes a committed
dossier, and D9 ``core.projection_rederivation_audit`` re-derives a *committed*
projection row's hash from its *committed* payload. None of those run the
materializer **twice on the same inputs** and compare; they check a single
committed artifact against itself, not the engine's reproducibility. This module
is the missing cross-frontend gate: it invokes the materializer afresh ``runs``
times and proves the *act of materializing* is deterministic.

WHAT IT REUSES (no parallel hash scheme; AGENTS.md §0/§2.6). The content hash is
:func:`lawvm.tools.certificate_bundle.leaf_hash` (the §3.1.1 ``LeafHash(domain,
obj)`` primitive the certificate spec already commits with), applied under a
fixed domain. The harness does NOT invent a hash: a JSON-serializable output is
hashed with ``leaf_hash``; a ``bytes``/``str`` output is hashed directly with the
same SHA-256 rendering vocabulary. If the materialize callable also returns a
content/certificate hash of its own, the harness asserts THAT hash is identical
across runs too, rather than minting a competing one.

PLANE & DISCIPLINE (AGENTS.md §0, §1.10, §2.10). Read-only replay-plane audit
lane. It calls the injected ``materialize_fn`` (which is the caller's replay
entry point — the harness never reaches into an apply lane itself), compares the
results, and returns :class:`~lawvm.core.phase_result.Observation` tuples. It
never mutates legal state, never rewrites an op, never re-orders the inputs, and
never "repairs" a nondeterministic result — a divergence between two runs of the
same inputs is genuine surfaced evidence that the replay path leaks hidden state,
and it is reported, never absorbed (the §0 forbidden move would be to silently
canonicalize the two outputs into agreement).

FAIL-LOUD (AGENTS.md §1.10). ``runs < 2`` is a caller-side programming bug (you
cannot compare fewer than two runs) and raises
:class:`ReplayDeterminismInputError`. An exception RAISED by the materialize
callable is NOT swallowed into a clean verdict — it propagates, because a replay
that crashes is a different failure mode than one that diverges, and hiding it
would violate §1.10. The divergence itself (two successful runs disagree) is the
genuine finding and is emitted as an Observation.

LEAKAGE SCAN (best-effort, honest). :func:`scan_module_for_nondeterminism_sources`
is a focused AST scan over a core materialization module that flags the common
nondeterminism sources reachable from a replay path — ``datetime.now`` /
``time.time`` wall-clock reads, un-seeded ``random`` draws, and ``set`` iteration
feeding output. It is EVIDENCE, not a gate: it reports candidate sites
(``file:line``) for a human to confirm; it does not assert any of them actually
reach output, and it never edits anything. A flagged site is a lead, not a
proven leak.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lawvm.core.phase_result import Observation
from lawvm.tools.certificate_bundle import _sha256_rendered, leaf_hash

# Public finding code, also registered in
# :data:`lawvm.core.observation_registry.FINDING_REGISTRY`.
REPLAY_NONDETERMINISM = "REPLAY.NONDETERMINISM"

# Audit-stage / owner stamped into the emitted Observations. Mirror the registry
# row's phase/owner so the wire point and the registry agree.
_REPLAY_DETERMINISM_AUDIT_STAGE = "replay-determinism"
_REPLAY_DETERMINISM_AUDIT_OWNER = "replay_determinism_audit"
_REPLAY_DETERMINISM_AUDIT_REASON = "replay_output_diverges_across_identical_runs"

# Hash domain for the run-output content hash. A fixed string so two runs of the
# same output hash identically and a different output cannot collide with it.
_REPLAY_OUTPUT_HASH_DOMAIN = "lawvm.replay_determinism.output.v1"


class ReplayDeterminismInputError(ValueError):
    """The harness was called with a structurally invalid request (caller bug).

    Distinct from a nondeterminism *finding*: ``runs < 2`` cannot compare two
    runs at all, so it is a programming error in the caller, not a corpus fact
    about a leaky replay path. Fail-loud per AGENTS.md §1.10 rather than folding
    into a clean verdict.
    """


def _content_hash(output: Any) -> str:
    """Stable content hash of one run's output, reusing the §3.1.1 vocabulary.

    ``bytes`` / ``str`` outputs are hashed directly under the rendered SHA-256
    profile (``_sha256_rendered``); any JSON-serializable output is hashed with
    :func:`leaf_hash` under a fixed domain. No new hash machinery is introduced
    (AGENTS.md §0/§2.6): both paths render ``"sha256:<hex>"`` exactly as the
    certificate bundle does, so a replay output hashes the same way a committed
    artifact would.
    """
    if isinstance(output, bytes):
        return _sha256_rendered(output)
    if isinstance(output, str):
        return _sha256_rendered(output.encode("utf-8"))
    return leaf_hash(_REPLAY_OUTPUT_HASH_DOMAIN, output)


@dataclass(frozen=True)
class RunObservation:
    """One materialization run's observable surface (hash + reported hash).

    ``content_hash`` is the harness-computed content hash of the run's output
    (the universal byte-identity witness). ``reported_hash`` is the hash the
    materialize callable returned for itself, if any (a frontend's own
    certificate/content hash) — the harness asserts it too is stable across runs,
    rather than minting a competing scheme.
    """

    run_index: int
    content_hash: str
    reported_hash: str | None


def _extract_reported_hash(output: Any) -> str | None:
    """Best-effort pull of a frontend's own content/certificate hash from output.

    If the materialize callable's output is a mapping carrying a recognized hash
    member, that hash is lifted so the harness can assert it is stable across
    runs in addition to the byte-identity check. Absent such a member, returns
    ``None`` (the harness then relies solely on its own content hash). The lookup
    is read-only and never raises on a missing/odd shape.
    """
    if not isinstance(output, Mapping):
        return None
    for member in ("certificate_hash", "content_hash", "derived_state_hash", "root_hash"):
        candidate = output.get(member)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _first_divergence(output_a: Any, output_b: Any, *, path: str = "") -> str:
    """Address of the first member at which two run outputs structurally diverge.

    A deterministic descent into the two outputs returning a dotted/indexed
    address (e.g. ``"versions[3].text"``) of the first differing leaf, so a
    triager can jump to the leaking field without diffing two large blobs by
    hand. Falls back to the current ``path`` (or ``"<root>"``) when the divergence
    is a type/length mismatch rather than a locatable leaf. Read-only.
    """
    if type(output_a) is not type(output_b):
        return path or "<root>"
    if isinstance(output_a, Mapping):
        keys_a = set(output_a.keys())
        keys_b = set(output_b.keys())
        only = sorted((keys_a ^ keys_b), key=str)
        if only:
            return f"{path}.{only[0]}" if path else str(only[0])
        for key in sorted(keys_a, key=str):
            child = f"{path}.{key}" if path else str(key)
            if output_a[key] != output_b[key]:
                return _first_divergence(output_a[key], output_b[key], path=child)
        return path or "<root>"
    if isinstance(output_a, (list, tuple)):
        if len(output_a) != len(output_b):
            return f"{path}[len={len(output_a)}vs{len(output_b)}]" if path else f"[len={len(output_a)}vs{len(output_b)}]"
        for index, (item_a, item_b) in enumerate(zip(output_a, output_b, strict=False)):
            if item_a != item_b:
                return _first_divergence(item_a, item_b, path=f"{path}[{index}]")
        return path or "<root>"
    # Scalar / bytes / str leaf that differs (or an opaque object).
    return path or "<root>"


def assert_replay_deterministic(
    materialize_fn: Callable[[], Any],
    inputs: Any,
    *,
    runs: int = 2,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """Assert replay is a pure function of its inputs: materialize N times, prove byte-identity.

    Invokes ``materialize_fn`` ``runs`` times and asserts every run produced a
    byte-identical output — identical harness content hash AND, when the callable
    reports one, an identical self-reported content/certificate hash. On any
    mismatch a single typed ``REPLAY.NONDETERMINISM`` Observation is emitted
    carrying the fixed-shape evidence a triager needs: the first-diverging
    address/field, the run-A vs run-B content hashes, the per-run reported hashes,
    and the run indices that disagreed.

    The materialize callable is the caller's OWN replay entry point (e.g. a thunk
    over ``timeline.materialize_pit_ex`` for FI/UK/EE), so the harness never
    reaches into an apply lane itself — it is generic over the callable exactly
    as D10's ``assert_compare_eid_parity`` is generic over its ``canonicalize``
    callable. ``inputs`` is opaque to the harness (carried into the observation
    detail as a stable label only); the callable closes over the real
    ``(base_state, ops, pit)`` it replays.

    Args:
        materialize_fn: a zero-argument thunk that performs one full
            materialization and returns its output (JSON-serializable, ``bytes``,
            ``str``, or a mapping that may carry the frontend's own content hash).
            It MUST close over the same inputs on every call — the harness calls
            it ``runs`` times with no arguments. An exception raised by the
            callable propagates (a crash is a different failure mode than a
            divergence; §1.10 forbids swallowing it into a clean verdict).
        inputs: an opaque, stable label for the inputs under test (e.g. a
            ``(statute_id, pit)`` tuple or a string). Carried verbatim into the
            observation detail so a triager can identify which materialization
            diverged; the harness never inspects it to drive replay.
        runs: how many times to materialize and cross-compare (default 2). Must
            be ``>= 2`` — fewer than two runs cannot be compared, a caller bug.
        source_statute: base statute id under test, carried into the observation
            for multi-statute routing.

    Returns:
        The empty tuple when all ``runs`` outputs are byte-identical (replay is
        deterministic for these inputs). Otherwise a one-element tuple with the
        ``REPLAY.NONDETERMINISM`` Observation. The caller decides enforcement;
        this function emits observations only, never mutates state, and (beyond a
        caller-bug ``runs`` value or a callable that itself raises) never raises.

    Raises:
        ReplayDeterminismInputError: ``runs < 2`` — a caller-side programming bug
            (cannot compare fewer than two runs), fail-loud per AGENTS.md §1.10.
    """
    if runs < 2:
        raise ReplayDeterminismInputError(
            f"assert_replay_deterministic needs runs >= 2 to compare, got {runs}"
        )

    outputs: list[Any] = []
    observations: list[RunObservation] = []
    for run_index in range(runs):
        output = materialize_fn()
        outputs.append(output)
        observations.append(
            RunObservation(
                run_index=run_index,
                content_hash=_content_hash(output),
                reported_hash=_extract_reported_hash(output),
            )
        )

    baseline = observations[0]
    for current in observations[1:]:
        content_mismatch = current.content_hash != baseline.content_hash
        reported_mismatch = current.reported_hash != baseline.reported_hash
        if not content_mismatch and not reported_mismatch:
            continue
        # First run to disagree with run 0 — emit one finding with fixed-shape
        # evidence and stop (one divergence proves nondeterminism; the address is
        # the lead a triager follows).
        divergence_address = _first_divergence(outputs[0], outputs[current.run_index])
        detail: dict[str, Any] = {
            "inputs": inputs,
            "runs": runs,
            "diverging_run_a": baseline.run_index,
            "diverging_run_b": current.run_index,
            "first_divergence_address": divergence_address,
            "content_hash_a": baseline.content_hash,
            "content_hash_b": current.content_hash,
            "reported_hash_a": baseline.reported_hash if baseline.reported_hash is not None else "",
            "reported_hash_b": current.reported_hash if current.reported_hash is not None else "",
            "content_hash_diverged": content_mismatch,
            "reported_hash_diverged": reported_mismatch,
            "reason": _REPLAY_DETERMINISM_AUDIT_REASON,
            "owner": _REPLAY_DETERMINISM_AUDIT_OWNER,
        }
        return (
            Observation(
                kind=REPLAY_NONDETERMINISM,
                stage=_REPLAY_DETERMINISM_AUDIT_STAGE,
                detail=detail,
                source_statute=source_statute,
            ),
        )
    return ()


# ---------------------------------------------------------------------------
# Leakage scan (best-effort, honest evidence — not a gate)
# ---------------------------------------------------------------------------

# Finding kinds for the scan are deliberately the same ``REPLAY.NONDETERMINISM``
# family conceptually, but the scan does not emit Observations — it returns a
# typed evidence carrier (a candidate leak site), because a flagged site is a
# LEAD a human confirms, not a proven leak the registry should gate on.


@dataclass(frozen=True)
class LeakageCandidate:
    """One flagged nondeterminism-source site in a scanned module.

    ``kind`` is the leak family (``"wall_clock"`` / ``"unseeded_random"`` /
    ``"set_iteration"`` / ``"dict_ordering"``). ``location`` is ``file:line:col``.
    ``snippet`` is the offending source fragment. This is EVIDENCE — the scan
    does not prove the site reaches replay output, only that it is a known
    nondeterminism source present in a module on the replay path.
    """

    kind: str
    module_path: str
    lineno: int
    col_offset: int
    snippet: str

    @property
    def location(self) -> str:
        """``file:line:col`` triage address."""
        return f"{self.module_path}:{self.lineno}:{self.col_offset}"


_WALL_CLOCK_CALLS = frozenset(
    {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("datetime", "today"),
        ("time", "time"),
        ("time", "monotonic"),
        ("time", "perf_counter"),
    }
)
# ``random`` draws without an explicit local seed — any attribute call on the
# ``random`` module is a candidate (the harness cannot prove a seed is absent,
# so it flags and lets a human confirm).
_RANDOM_MODULE = "random"


class _NondeterminismVisitor(ast.NodeVisitor):
    """AST visitor collecting common nondeterminism-source sites.

    Flags four families, each a known way hidden state leaks into output:
      * ``wall_clock``: ``datetime.now``/``utcnow``/``today``, ``time.time``/
        ``monotonic``/``perf_counter`` — a stamp that changes between runs.
      * ``unseeded_random``: any ``random.<fn>(...)`` call — non-reproducible
        unless a seed is pinned (which the scan cannot verify, so it flags).
      * ``set_iteration``: iterating a ``set``/``frozenset`` literal or a
        ``set(...)`` call directly in a ``for`` or comprehension — insertion
        order is undefined, so feeding it to output is order-nondeterministic.
      * ``dict_ordering``: a ``sorted(...)``-free reliance is NOT flagged (too
        noisy); only explicit ``set`` iteration is flagged for the ordering
        family to stay honest about false positives.
    """

    def __init__(self, module_path: str, source_lines: Sequence[str]) -> None:
        self.module_path = module_path
        self._source_lines = source_lines
        self.candidates: list[LeakageCandidate] = []

    def _snippet(self, node: ast.AST) -> str:
        lineno = getattr(node, "lineno", 0)
        if 1 <= lineno <= len(self._source_lines):
            return self._source_lines[lineno - 1].strip()
        return ""

    def _record(self, kind: str, node: ast.AST) -> None:
        self.candidates.append(
            LeakageCandidate(
                kind=kind,
                module_path=self.module_path,
                lineno=getattr(node, "lineno", 0),
                col_offset=getattr(node, "col_offset", 0),
                snippet=self._snippet(node),
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner = func.value.id
            attr = func.attr
            if (owner, attr) in _WALL_CLOCK_CALLS:
                self._record("wall_clock", node)
            elif owner == _RANDOM_MODULE:
                self._record("unseeded_random", node)
        self.generic_visit(node)

    def _is_set_source(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Set):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("set", "frozenset")
        ):
            return True
        return False

    def visit_For(self, node: ast.For) -> None:
        if self._is_set_source(node.iter):
            self._record("set_iteration", node)
        self.generic_visit(node)

    def _check_comprehension(self, generators: Sequence[ast.comprehension], node: ast.AST) -> None:
        for generator in generators:
            if self._is_set_source(generator.iter):
                self._record("set_iteration", node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._check_comprehension(node.generators, node)
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._check_comprehension(node.generators, node)
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._check_comprehension(node.generators, node)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._check_comprehension(node.generators, node)
        self.generic_visit(node)


def scan_source_for_nondeterminism_sources(
    source: str, *, module_path: str
) -> tuple[LeakageCandidate, ...]:
    """Scan one module's source text for candidate nondeterminism sources.

    Returns the flagged sites in deterministic (lineno, col, kind) order. This is
    best-effort EVIDENCE, not a proof: a flagged ``random`` call may be seeded
    elsewhere, a flagged ``set`` iteration may be sorted before reaching output.
    The scan reports leads; a human confirms which actually leak into replay
    output. Read-only — it parses, never edits.

    Raises:
        SyntaxError: the source does not parse (a caller-supplied bad module path
            is a programming error, fail-loud per §1.10 rather than a silent
            empty result).
    """
    tree = ast.parse(source)
    visitor = _NondeterminismVisitor(module_path, source.splitlines())
    visitor.visit(tree)
    return tuple(
        sorted(visitor.candidates, key=lambda c: (c.lineno, c.col_offset, c.kind))
    )


def scan_module_for_nondeterminism_sources(
    module_path: str,
) -> tuple[LeakageCandidate, ...]:
    """Scan a module FILE for candidate nondeterminism sources (read-only).

    Convenience wrapper over :func:`scan_source_for_nondeterminism_sources` that
    reads the file at ``module_path``. The returned candidates carry that path so
    each site is a ``file:line:col`` triage address.
    """
    with open(module_path, "r", encoding="utf-8") as handle:
        source = handle.read()
    return scan_source_for_nondeterminism_sources(source, module_path=module_path)


__all__ = [
    "REPLAY_NONDETERMINISM",
    "LeakageCandidate",
    "ReplayDeterminismInputError",
    "RunObservation",
    "assert_replay_deterministic",
    "scan_module_for_nondeterminism_sources",
    "scan_source_for_nondeterminism_sources",
]
