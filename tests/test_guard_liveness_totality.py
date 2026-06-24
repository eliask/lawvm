"""Guard-liveness totality matrix (registry rows XP-05 / GUARD-01 / GUARD-02 / GUARD-03).

The companion harness ``test_fi_guard_liveness.py`` proves *individual*
fire-drills reach their guard surface and enforces an inventory partition. This
module sits one level up: it builds the **per-code totality matrix** over every
``FINDING_REGISTRY`` code with blocking enforcement and asserts the matrix is
total — i.e. each blocking code is classified, across three columns, into a
typed bucket, and any code that fails a column is a *typed dead-gate row* rather
than a silent pass.

The three columns (the guard-liveness triple from the registry rows):

1. **emit** — the code has at least one production emit site (a non-test
   ``src/lawvm`` file that mentions the code constant). A bare registry entry is
   not an emit site.
2. **drill-or-allowlist** — the code has a fire-drill in ``FIRE_DRILLS`` or is
   consciously parked in ``NO_FIRE_DRILL_YET``.
3. **deciding-guard** — if the code is drilled, the drill drives the
   *production-deciding* guard (a real ``lawvm.*`` builder), not merely a
   hand-built ``Finding`` pushed through the verdict mapping.

Rows implemented here:

* **XP-05** — the registry RULE is total: blocking codes partition exactly into
  ``FIRE_DRILLS ∪ NO_FIRE_DRILL_YET`` and every drilled code drives a production
  builder (delegated to / reused from the companion harness, asserted here as a
  single consolidated statement so the RULE is total, not scattered).
* **GUARD-01** — the per-code WALK exists and is total; the matrix below is the
  walk. Any blocking code missing the emit column or the drill-or-allowlist
  column is a ``GUARD.BLOCKING_CODE_NOT_LIVE`` dead-gate row, surfaced by code.
* **GUARD-02** — verdict-mapping-only drill detection: a blocking code whose
  ONLY primary drill is verdict-mapping-only (it hand-builds the Finding and only
  checks verdict mapping) is a live hole — the production-deciding guard is
  untested. These are ``GUARD.SECONDARY_DRILL_MASQUERADES_AS_LIVE`` and are
  pinned in a reason-carrying ratchet so a NEW one fails loudly while the current
  set is consciously typed debt.
* **GUARD-03** — registry/producer-enforcement agreement: a blocking-registered
  code whose only production emit polarity is non-blocking is a
  ``GUARD.REGISTRY_PRODUCER_ENFORCEMENT_MISMATCH`` (the LS-03 occupancy gate was
  the witness; it has since been reconciled). The current candidate set is pinned
  in a reason-carrying ratchet so a NEW mismatch fails loudly.

These ``GUARD.*`` names are the typed *audit kinds* of the matrix rows; they are
meta-audit assertions over the registry + harness, not pipeline ``Finding``
codes, so they are not registered in ``FINDING_REGISTRY`` (consistent with the
existing meta-gates in the companion harness).
"""

from __future__ import annotations

import pathlib
import re
from typing import Dict

from lawvm.core.observation_registry import FINDING_REGISTRY

# Reuse the companion harness as the single source of truth for the drill tables,
# the blocking-code predicate, the emit-site grep, and the deciding-guard check.
# Importing it (rather than re-deriving) guarantees the matrix walks exactly the
# same tables the per-drill gates enforce — there is no second, drifting copy.
import tests.test_fi_guard_liveness as harness


# ---------------------------------------------------------------------------
# Matrix column helpers (reuse harness logic; no second copy of the tables)
# ---------------------------------------------------------------------------


def _blocking_codes() -> set[str]:
    return harness._blocking_codes()


def _has_emit_site(code: str) -> bool:
    """Column 1: the code has at least one non-registry production emit site.

    ``_KNOWN_NO_PRODUCTION_EMIT`` codes are consciously-accepted emit-less debt in
    the companion harness; they count as "accounted" for the totality partition
    here (the harness's own emit gate already polices that allowlist), so this
    column is about *unaccounted* missing-emit dead gates.
    """
    if code in harness._KNOWN_NO_PRODUCTION_EMIT:
        return True
    roots: tuple[str, ...] = ("src/lawvm/core", "src/lawvm/finland")
    if code.startswith(harness._NON_FI_CORE_EMIT_PREFIXES):
        roots = ("src/lawvm",)
    sites = [
        site
        for site in harness._production_emit_grep(code, roots)
        if not site.endswith("observation_registry.py")
    ]
    return bool(sites)


def _has_drill_or_allowlist(code: str) -> bool:
    """Column 2: the code is drilled or consciously parked as not-yet-drilled."""
    return code in harness.FIRE_DRILLS or code in harness.NO_FIRE_DRILL_YET


def _drives_deciding_guard(code: str) -> bool | None:
    """Column 3: a drilled code's drill drives the production-deciding guard.

    Returns ``True`` if the drill drives a real production builder (the deciding
    guard), ``False`` if the drill's only production surface is the verdict
    mapping (verdict-mapping-only — GUARD-02 hole), and ``None`` if the code is
    not drilled (it lives in the allowlist; the deciding guard is untested by
    conscious debt, not a column failure).
    """
    if code not in harness.FIRE_DRILLS:
        return None
    if code in harness._VERDICT_SURFACE_PRIMARY_DRILLS:
        return False
    source = harness._drill_effective_source(harness.FIRE_DRILLS[code])
    return any(call in source for call in harness._PRODUCTION_BUILDER_CALLS)


# ---------------------------------------------------------------------------
# GUARD-03 production-emit polarity helpers
# ---------------------------------------------------------------------------

# A production emit-site window expresses BLOCKING polarity when it raises, or
# constructs a blocking Finding / Violation / Obligation, or sets blocking=True.
_BLOCKING_EMIT = re.compile(
    r"blocking\s*=\s*True|\braise\b|Violation\(|Obligation\("
    r"|record_violation|record_obligation"
)
# It expresses explicit NON-BLOCKING polarity when it constructs an Observation /
# sets blocking=False / declares role="observation" at the emit.
_NON_BLOCKING_EMIT = re.compile(
    r"blocking\s*=\s*False|Observation\(|record_observation"
    r"|role\s*=\s*['\"]observation['\"]"
)


def _emit_polarity_windows(code: str) -> list[str]:
    """Return source windows around each non-test production mention of *code*."""
    src_root = pathlib.Path(__file__).resolve().parent.parent / "src" / "lawvm"
    needle = f'"{code}"'
    alt_needle = f"'{code}'"
    windows: list[str] = []
    for path in src_root.rglob("*.py"):
        spath = str(path)
        if "/test" in spath or spath.endswith("observation_registry.py"):
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if needle in line or alt_needle in line:
                windows.append("\n".join(lines[max(0, i - 6) : i + 7]))
    return windows


def _compile_result_force_add_codes() -> frozenset[str]:
    """Codes the strict verdict-ledger lane force-adds regardless of blocking flag.

    ``strict_fail_reasons_from_finding_ledger`` adds a small literal set of codes
    to the ``triggered`` set unconditionally (``if spec.code in {...}: ...``),
    *before* the ``finding.blocking and ...`` gate. A code in that set blocks even
    when its only production emit window is non-blocking (the blocking decision is
    made at the verdict-ledger, not the emit). We parse the literal set out of the
    production function source so this stays honest if the set changes upstream.
    """
    import inspect
    import re

    from lawvm.core.compile_result import strict_fail_reasons_from_finding_ledger

    src = inspect.getsource(strict_fail_reasons_from_finding_ledger)
    match = re.search(r"if spec\.code in \{([^}]*)\}", src)
    if match is None:
        return frozenset()
    return frozenset(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


def _blocks_via_indirection(code: str) -> bool:
    """True if *code* blocks through a path the emit-window grep cannot see.

    The GUARD-03 emit-window matcher only reads polarity markers *at the emit
    site*. Three production indirections decide blocking elsewhere, so a code can
    emit ``blocking=False`` / ``role="observation"`` at its producer yet still be a
    live (or dormant-but-armable) strict gate:

    (a) **force-add set** — ``strict_fail_reasons_from_finding_ledger`` force-adds
        the code to the triggered set regardless of the blocking flag
        (``compile_result.py`` force-add block).
    (b) **role-dispatch blocking** — ``_finding_from_compile_signal`` constructs an
        obligation finding with ``blocking = default_enforcement in (strict_fail,
        hard_fail)``, so an obligation/strict_fail code is dispatched blocking even
        where the call site shows no literal ``blocking=True``.
    (c) **profile-gated strict_fail** — the code is a strict_fail in a
        ``_PROFILE_GATES`` row: it is suppressed only under a permissive profile
        and armable (strict-fails) under a stricter one. A dormant-armable gate is
        a real gate, not a dead one.

    Any of these makes the GUARD-03 "non-blocking-only emit" classification a false
    positive: the registry is correct and the gate is live/armable.
    """
    spec = FINDING_REGISTRY.get(code)
    if spec is None:
        return False
    # (a) force-add set
    if code in _compile_result_force_add_codes():
        return True
    is_strict = spec.default_enforcement in ("strict_fail", "hard_fail")
    # (b) obligation role-dispatched blocking in _finding_from_compile_signal
    if spec.role == "obligation" and is_strict:
        return True
    # (c) profile-gated strict_fail (dormant-armable under a stricter profile)
    if is_strict:
        from lawvm.core.compile_result import _PROFILE_GATES

        if code in _PROFILE_GATES:
            return True
    return False


def _emits_non_blocking_only(code: str) -> bool:
    """True if every production emit window for *code* is non-blocking polarity.

    The LS-03 occupancy-gate mismatch shape generalized: a blocking-registered
    code whose only producer emits it as a non-blocking, off-pipeline Finding is
    structurally a dead gate. Codes with no emit window at all (covered by the
    harness emit gate / ``_KNOWN_NO_PRODUCTION_EMIT``) are not polarity mismatches
    here.

    A code that blocks through a production indirection the emit-window grep
    cannot see (``_blocks_via_indirection``) is NOT a non-blocking-only emit: the
    blocking decision is made downstream of the emit, so the registry is correct
    and the gate is live (or dormant-but-armable).

    A registry/producer *enforcement* mismatch only exists when the registry's
    enforcement actually claims blocking (``strict_fail`` / ``hard_fail``). A
    ``warn``-enforcement code makes no blocking claim at the strict gate
    (``compile_result.py`` requires ``default_enforcement in (strict_fail,
    hard_fail)``), so a non-blocking producer for it is consistent, not a
    mismatch — even when the code is classified "blocking" only by its
    obligation/violation role. Such codes are excluded here.
    """
    spec = FINDING_REGISTRY.get(code)
    if spec is None or spec.default_enforcement not in ("strict_fail", "hard_fail"):
        return False
    if _blocks_via_indirection(code):
        return False
    windows = _emit_polarity_windows(code)
    if not windows:
        return False
    saw_blocking = any(_BLOCKING_EMIT.search(w) for w in windows)
    saw_non_blocking = any(_NON_BLOCKING_EMIT.search(w) for w in windows)
    return saw_non_blocking and not saw_blocking


# ---------------------------------------------------------------------------
# GUARD-02: verdict-mapping-only drills are live holes (deciding guard untested)
# ---------------------------------------------------------------------------

# Blocking codes whose ONLY primary fire-drill is verdict-mapping-only: the drill
# hand-builds the Finding / CompileFailure and asserts only that it surfaces in
# CompileVerdict.barrier_codes. The production-deciding guard — the code that
# DECIDES to emit the runtime finding in the first place — is NOT exercised, so
# the guard could be silently disconnected and the drill would stay green. This
# is a SECONDARY drill per the §6 doctrine; a blocking code whose only drill is
# secondary is a live hole, distinct from "no drill at all".
#
# Each entry carries a one-line reason. This is a reason-carrying ratchet: a NEW
# verdict-mapping-only blocking code must consciously land here (or get a real
# deciding-guard drill); it cannot silently inherit the verdict-surface blessing.
# Empty after the dead-gate reconciliation. The three former verdict-only holes
# (APPLY.FAILED_OPERATION, APPLY.SOURCE_PATHOLOGY_DETECTED,
# APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED) now have real deciding-guard drills:
# the strict ``apply_op`` lane drives a genuine FailedOp / SourcePathology, and
# ``build_finland_effect_lifecycle`` produces a genuine unresolved-target event.
# A NEW verdict-mapping-only blocking code must consciously land here (with a
# reason) or get a real deciding-guard drill.
GUARD02_VERDICT_ONLY_DRILL_HOLES: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# GUARD-03: registry/producer enforcement-polarity mismatches (LS-03 class)
# ---------------------------------------------------------------------------

# Blocking-registered codes whose only production emit polarity is non-blocking
# (an Observation / blocking=False / role="observation" emit, never a raise /
# Violation / Obligation / blocking=True) AND which do not block through a
# production indirection the emit-window grep cannot see. This is the LS-03
# occupancy-gate class: the registry says "blocking" but the only producer emits a
# non-blocking off-pipeline Finding, so the guard is structurally dead.
#
# Reason-carrying ratchet: a NEW genuine non-blocking-only-emit blocking code must
# consciously land here (or be reconciled in the registry / wire a blocking
# producer). The set is currently empty: the dead-gate reconciliation either
# downgraded the genuine over-claims to ``warn`` (registry now matches the
# non-blocking producer) or proved the remainder block via an indirection
# (``_blocks_via_indirection``) the emit-window grep cannot see — force-add set,
# obligation role-dispatch, or profile-gated strict_fail — so they are live or
# dormant-but-armable gates with correct registry entries, not dead gates.
GUARD03_NON_BLOCKING_ONLY_EMIT: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# XP-05 (RULE total) + GUARD-01 (per-code WALK total)
# ---------------------------------------------------------------------------


def test_xp05_blocking_partition_is_total() -> None:
    """XP-05: blocking codes partition exactly into FIRE_DRILLS ∪ NO_FIRE_DRILL_YET.

    The registry RULE as a single consolidated statement: no blocking code may
    exist outside the drilled/allowlisted partition, and nothing in the partition
    may be a non-blocking code. (The companion harness asserts the two inclusion
    halves separately; this pins the equality so the RULE is total here too.)
    """
    blocking = _blocking_codes()
    accounted = set(harness.FIRE_DRILLS) | set(harness.NO_FIRE_DRILL_YET)
    assert blocking == accounted, (
        "XP-05 partition is not total.\n"
        f"  blocking-but-unaccounted (dead-gate, no drill+allowlist): "
        f"{sorted(blocking - accounted)}\n"
        f"  accounted-but-not-blocking (stale): {sorted(accounted - blocking)}"
    )


def test_xp05_every_drill_drives_a_deciding_guard() -> None:
    """XP-05: every primary drill drives the production-deciding guard.

    A drill must drive a real production builder, not hand-build a Finding and
    only check verdict mapping — except the small, explicit verdict-surface lane
    (which GUARD-02 separately types as a hole). This is the RULE's second clause.
    """
    offenders: list[str] = []
    for code in sorted(harness.FIRE_DRILLS):
        if code in harness._VERDICT_SURFACE_PRIMARY_DRILLS:
            continue
        if _drives_deciding_guard(code) is not True:
            offenders.append(code)
    assert not offenders, (
        "drilled blocking codes whose drill does not drive a production-deciding "
        f"guard (and are not in the verdict-surface lane): {offenders}"
    )


def test_guard01_per_code_matrix_is_total() -> None:
    """GUARD-01: the per-code totality WALK over the blocking triple is total.

    Enumerate EVERY blocking code across {emit, drill-or-allowlist} and assert
    none fails a column unaccounted. A code missing the emit column or the
    drill-or-allowlist column is a typed ``GUARD.BLOCKING_CODE_NOT_LIVE`` dead-gate
    row — surfaced by code, never a silent pass. (The third column, deciding-guard,
    is asserted by GUARD-02 below because a verdict-mapping-only drill is a
    *distinct* hole class from a missing column.)
    """
    blocking = _blocking_codes()
    not_live: list[str] = []
    for code in sorted(blocking):
        if not _has_emit_site(code) or not _has_drill_or_allowlist(code):
            not_live.append(code)
    assert not not_live, (
        "GUARD.BLOCKING_CODE_NOT_LIVE — blocking codes that fail the liveness "
        f"matrix (missing a production emit site and/or a fire-drill/allowlist "
        f"entry): {not_live}. Each is a dead gate: wire a producer, add a "
        "fire-drill, or consciously park it in NO_FIRE_DRILL_YET / "
        "_KNOWN_NO_PRODUCTION_EMIT with a stated reason."
    )


def test_guard01_matrix_covers_every_blocking_code() -> None:
    """GUARD-01: the WALK visits every blocking code exactly once (no gaps).

    Guards the matrix itself against silently skipping a code: the set the walk
    classifies must equal the full blocking set. (A walk that quietly dropped a
    code could report 'all live' while never having looked at it.)
    """
    blocking = _blocking_codes()
    # Build the full per-code classification: every code lands in exactly one of
    # {live, dead-gate} and the union must equal the blocking set. A walk that
    # silently dropped a code would leave it out of both buckets.
    live: set[str] = set()
    dead: set[str] = set()
    for code in sorted(blocking):
        if _has_emit_site(code) and _has_drill_or_allowlist(code):
            live.add(code)
        else:
            dead.add(code)
    walked = live | dead
    assert walked == blocking, (
        f"GUARD-01 walk did not visit every blocking code: missing "
        f"{sorted(blocking - walked)}"
    )
    assert not (live & dead), (
        f"GUARD-01 walk double-classified codes: {sorted(live & dead)}"
    )


# ---------------------------------------------------------------------------
# GUARD-02: verdict-mapping-only drills are live holes
# ---------------------------------------------------------------------------


def test_guard02_verdict_only_holes_match_pinned_set() -> None:
    """GUARD-02: the set of verdict-mapping-only blocking codes is pinned.

    A blocking code whose ONLY primary drill is verdict-mapping-only has an
    untested production-deciding guard — a live hole, distinct from "no drill".
    The harness blesses these via ``_VERDICT_SURFACE_PRIMARY_DRILLS``; this gate
    re-types them as ``GUARD.SECONDARY_DRILL_MASQUERADES_AS_LIVE`` debt and pins
    the set so a NEW verdict-only code fails loudly rather than inheriting the
    blessing silently.
    """
    actual = {
        code
        for code in harness.FIRE_DRILLS
        if _drives_deciding_guard(code) is False
    }
    pinned = set(GUARD02_VERDICT_ONLY_DRILL_HOLES)
    new_holes = actual - pinned
    healed = pinned - actual
    assert not new_holes, (
        "GUARD.SECONDARY_DRILL_MASQUERADES_AS_LIVE — NEW blocking codes whose only "
        f"primary drill is verdict-mapping-only: {sorted(new_holes)}. Give the code "
        "a drill that drives the production-deciding guard, or add it to "
        "GUARD02_VERDICT_ONLY_DRILL_HOLES with a stated reason."
    )
    assert not healed, (
        f"GUARD02 pinned codes that are no longer verdict-only holes (remove from "
        f"the ratchet — debt paid down): {sorted(healed)}"
    )


def test_guard02_holes_are_in_verdict_surface_lane() -> None:
    """GUARD-02: each pinned hole is a real verdict-surface primary drill.

    Keeps the GUARD-02 ratchet honest: every pinned code must actually be a
    blocking code drilled only via the verdict-surface lane (not stale, not a
    code that has since gained a deciding-guard drill).
    """
    blocking = _blocking_codes()
    for code, reason in sorted(GUARD02_VERDICT_ONLY_DRILL_HOLES.items()):
        assert reason.strip(), f"GUARD02 entry {code!r} has an empty reason"
        assert code in blocking, f"GUARD02 entry {code!r} is not a blocking code"
        assert code in harness.FIRE_DRILLS, (
            f"GUARD02 entry {code!r} has no primary drill"
        )
        assert code in harness._VERDICT_SURFACE_PRIMARY_DRILLS, (
            f"GUARD02 entry {code!r} is not in the verdict-surface lane; it may now "
            "have a real deciding-guard drill — re-check and remove if healed"
        )


# ---------------------------------------------------------------------------
# GUARD-03: registry/producer enforcement-polarity agreement
# ---------------------------------------------------------------------------


def test_guard03_non_blocking_only_emit_matches_pinned_set() -> None:
    """GUARD-03: blocking codes emitted non-blocking-only are pinned mismatches.

    Generalizes the LS-03 occupancy-gate witness (a blocking-registered code whose
    only producer emitted a non-blocking off-pipeline Finding). Every blocking
    code whose production emit windows are non-blocking-only is a typed
    ``GUARD.REGISTRY_PRODUCER_ENFORCEMENT_MISMATCH``. The set is pinned so a NEW
    mismatch fails loudly; the current set is honest surfaced debt, not a clean
    bill.
    """
    blocking = _blocking_codes()
    actual = {
        code
        for code in blocking
        if code not in harness._KNOWN_NO_PRODUCTION_EMIT and _emits_non_blocking_only(code)
    }
    pinned = set(GUARD03_NON_BLOCKING_ONLY_EMIT)
    new_mismatches = actual - pinned
    healed = pinned - actual
    assert not new_mismatches, (
        "GUARD.REGISTRY_PRODUCER_ENFORCEMENT_MISMATCH — NEW blocking-registered "
        f"codes whose only production emit polarity is non-blocking: "
        f"{sorted(new_mismatches)}. Reconcile in the registry (downgrade) or wire a "
        "blocking producer, or add to GUARD03_NON_BLOCKING_ONLY_EMIT with a reason."
    )
    assert not healed, (
        "GUARD03 pinned codes that now have a blocking producer (remove from the "
        f"ratchet — mismatch reconciled): {sorted(healed)}"
    )


def test_guard03_pinned_entries_are_real_blocking_codes() -> None:
    """GUARD-03: each pinned mismatch is a real, registered blocking code."""
    blocking = _blocking_codes()
    for code, reason in sorted(GUARD03_NON_BLOCKING_ONLY_EMIT.items()):
        assert reason.strip(), f"GUARD03 entry {code!r} has an empty reason"
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"GUARD03 entry {code!r} is unregistered"
        assert code in blocking, f"GUARD03 entry {code!r} is not a blocking code"


# ---------------------------------------------------------------------------
# Synthetic-violation gates: each matrix column fires on an injected violation
# ---------------------------------------------------------------------------


def test_guard01_column_fires_on_synthetic_missing_emit() -> None:
    """A synthetic blocking code with no emit site is caught by the GUARD-01 column."""
    # The column logic (not the registry) is what we exercise: a fabricated code
    # that is neither emitted nor allowlisted must read as not-live.
    fake = "GUARD.SYNTHETIC_NEVER_EMITTED_CODE"
    assert not _has_emit_site(fake)
    assert not _has_drill_or_allowlist(fake)


def test_guard02_column_fires_on_synthetic_verdict_only_drill() -> None:
    """A synthetic verdict-only drill reads as a deciding-guard hole."""

    def _verdict_only_drill() -> None:
        # Mirrors the verdict-surface shape: build a Finding, map to a verdict
        # barrier — never drives a production builder.
        harness._verdict_barrier_codes_from_findings(findings=[])

    # A drill whose effective source touches only the verdict helper does not
    # drive any production builder.
    source = harness._drill_effective_source(_verdict_only_drill)
    assert not any(call in source for call in harness._PRODUCTION_BUILDER_CALLS)


def test_guard03_column_fires_on_synthetic_non_blocking_emit(tmp_path: pathlib.Path) -> None:
    """The GUARD-03 polarity matcher classifies non-blocking-only vs blocking windows."""
    non_blocking_window = 'record_finding(kind="X.Y", role="observation", blocking=False)'
    blocking_window = 'raise InvalidThing("X.Y blocked")\n    blocking=True'
    assert _NON_BLOCKING_EMIT.search(non_blocking_window)
    assert not _BLOCKING_EMIT.search(non_blocking_window)
    assert _BLOCKING_EMIT.search(blocking_window)
