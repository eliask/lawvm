"""Disjoint-window scheduling totality sweep (registry rows SCHED-01/02/03).

A *read-only* per-window totality sweep over the already-produced replay output
(:class:`~lawvm.finland.replay_products.ReplayProducts`). It does NOT touch the
fold/replay/apply engine, the temporal scheduler, or metadata: it CONSUMES the
finished ``temporal_events`` + ``timelines`` carriers and emits a typed
observation. The spirit is the audit-registry §0 generative principle — every
legal-effect window is materialized as a version interval, carried as a typed
residual, or surfaced as a finding; never silently dropped.

The scheduling rows it pins
===========================

SCHED-01 (fold-order event vs legal-effect event are SEPARATE)
    Replay APPLICATION is document/fold order, but an op's LEGAL-EFFECT interval
    (``effective`` .. ``expires``) is a distinct fact that must participate in
    interval construction. The replay output already separates these: fold order
    is the op sequence; the legal-effect interval rides ``TemporalEvent``
    (``commence`` carries ``effective``; ``expire`` carries ``expires`` + the
    scoped target address). This sweep reads the legal-effect interval off the
    temporal-event plane — NOT off fold order — which is the SCHED-01 separation
    made checkable: the window the sweep tests is the legal-effect window, not
    wherever fold order placed the op.

SCHED-02 (disjoint-window must still be materialized OR blocked)
    When fold order installs a later-effective occupant before an earlier
    temporary window, the timeline must STILL contain the earlier window as a
    real interval (or block it) — a non-blocking observation alone is
    insufficient. This sweep checks the materialized ``timelines`` for that
    earlier window's interval; a window with NO matching version interval is
    surfaced (not assumed materialized).

SCHED-03 (disjoint legal-effect interval -> interval OR typed residual, never silent)
    Any window whose legal-effect interval is not represented as a version
    interval AND not carried as a typed residual yields
    ``SCHED.WINDOW_UNMATERIALIZED`` (self-evidencing: the window's group, target
    address, the disjoint interval, and the fold occupant that holds the slot).

Disposition (tag-don't-guess)
=============================
OBSERVATION-role, non-blocking — the SAME disposition as the prior totality
sweeps (SURF-01/02/04/05/07, EV-03). Over a real corpus a disjoint window that
the document-order fold did not materialize in the timeline is a REAL legal fact
about the source (a temporary gap-filler whose slot is held by a
deferred-commencement twin), surfaced — not a pipeline crash. Blocking would
contradict tag-don't-guess and could regress the corpus, so the sweep
NON-BLOCKINGLY surfaces the residual population. The synthetic unit-level bite is
the guard-liveness fire-drill.

This sweep is PURE: it reads already-produced carriers and returns typed finding
records. It sits off the replay/apply path and mutates nothing.

Relationship to the apply-time twin
===================================
The apply path already emits ``APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT`` ->
``TEMPORAL.WINDOW_UNMATERIALIZED`` (``tools/timeline_integrity.py``) when it
*discovers* a disjoint insert during the fold, and the temporal scheduler may
then materialize the window. Those are the apply-time DISCOVERY + repair lane.
This sweep is the complementary read-only TOTALITY audit over the FINAL output:
it does not depend on apply having flagged anything — it independently asks, of
every legal-effect window present on the replay output, "is this interval
materialized in the timeline?". A window the scheduler materialized is silent
here (the interval is present); a window neither materialized nor carried as a
residual is the SCHED finding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from lawvm.core.ir import LegalAddress, ProvisionTimeline
from lawvm.core.temporal import TemporalEvent

if TYPE_CHECKING:
    from lawvm.finland.replay_products import ReplayProducts

# ---------------------------------------------------------------------------
# Finding code (closed set; registered in core/observation_registry.py)
# ---------------------------------------------------------------------------

SCHED_WINDOW_UNMATERIALIZED = "SCHED.WINDOW_UNMATERIALIZED"


# ---------------------------------------------------------------------------
# Typed sweep finding (self-evidencing per AGENTS.md §1.8 / EV-07)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DisjointWindowFinding:
    """One SCHED fact: a legal-effect window with no materialized version interval.

    Attributes:
        code:               ``SCHED.WINDOW_UNMATERIALIZED``.
        group_id:           The temporal-event group the window came from (the
                            source amendment's effect group; the drift anchor).
        target_address:     The scoped target address whose timeline should carry
                            the interval (string form for self-evidencing).
        window_effective:   The window's legal-effect start (inclusive).
        window_expires:     The window's legal-effect end (EXCLUSIVE kernel
                            cutoff: first day NOT in force).
        fold_occupant_effective:
                            The ``effective`` of the version that fold order DID
                            place in the timeline at/around the window — the
                            later-effective occupant that holds the slot in
                            document order. ``""`` when no occupant was found.
        materialized:       Whether the timeline carries ANY version interval
                            matching the window (always ``False`` for an emitted
                            finding; carried for symmetry/audit).
        residualized:       Whether the window is carried as a typed residual on
                            the replay output (always ``False`` for an emitted
                            finding; a residualized window is silent).
        detail:             SELF-EVIDENCING message naming the group, address,
                            the disjoint interval, and the fold occupant, so the
                            finding is auditable from the record alone.
    """

    code: str
    group_id: str
    target_address: str
    window_effective: str
    window_expires: str
    fold_occupant_effective: str
    materialized: bool
    residualized: bool
    detail: str


# ---------------------------------------------------------------------------
# Internal: legal-effect window reconstruction from the temporal-event plane
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LegalEffectWindow:
    """A reconstructed legal-effect window (SCHED-01 separation surface).

    A window is a temporary legal-effect interval ``[effective, expires)`` bound
    to a target address, reconstructed from the temporal-event plane: the
    ``commence`` event of a group carries ``effective``; the ``expire`` event of
    the SAME group carries ``expires`` + the scoped target address. This is the
    legal-effect event, deliberately distinct from fold order (SCHED-01).
    """

    group_id: str
    target_address: LegalAddress
    effective: str
    expires: str


def _legal_effect_windows(
    temporal_events: tuple[TemporalEvent, ...],
) -> tuple[_LegalEffectWindow, ...]:
    """Reconstruct temporary legal-effect windows from the temporal-event plane.

    A window exists when a group has BOTH a non-empty ``effective`` (from a
    ``commence`` event or an ``expire`` event's source) AND a non-empty
    ``expires`` bound to a concrete target address (from an ``expire`` event).
    Only ``expire`` events carry a scoped ``exact_addresses`` target, so each
    target-bound window is anchored on an ``expire`` event and dated by its
    group's ``commence`` (falling back to the expire event's own
    source-effective when the group carries no separate commence event).

    Pure: reads the events; reconstructs NO ops and re-parses NOTHING.
    """
    # group_id -> earliest commence effective seen on the plane.
    commence_effective: dict[str, str] = {}
    for event in temporal_events:
        if event.kind != "commence":
            continue
        group_id = str(event.group_id or "")
        if not group_id or not event.effective:
            continue
        prior = commence_effective.get(group_id)
        if prior is None or event.effective < prior:
            commence_effective[group_id] = event.effective

    windows: list[_LegalEffectWindow] = []
    seen: set[tuple[str, str, str, str]] = set()
    for event in temporal_events:
        if event.kind != "expire" or not event.expires:
            continue
        group_id = str(event.group_id or "")
        # effective: prefer the group's commence date; else the expire event's
        # own source-effective provenance. A window with no datable start cannot
        # be checked against a [effective, expires) version interval.
        effective = commence_effective.get(group_id, "")
        if not effective and event.source is not None:
            effective = str(event.source.effective or "")
        if not effective:
            continue
        if event.expires <= effective:
            # Not a forward window (empty/degenerate interval); LS-19 owns the
            # bad-interval class — not a disjoint-window scheduling fact.
            continue
        for address in event.scope.exact_addresses:
            key = (group_id, str(address), effective, event.expires)
            if key in seen:
                continue
            seen.add(key)
            windows.append(
                _LegalEffectWindow(
                    group_id=group_id,
                    target_address=address,
                    effective=effective,
                    expires=event.expires,
                )
            )
    return tuple(windows)


def _timeline_has_window_interval(
    timeline: ProvisionTimeline,
    window: _LegalEffectWindow,
) -> bool:
    """Whether the timeline carries a version interval matching the window.

    A window is materialized when SOME version of the target's timeline carries
    the SAME ``[effective, expires)`` interval. This is the read-only mirror of
    the temporal scheduler's ``_timeline_already_has_interval`` contract (which
    we MUST NOT touch): a matching interval is a real version row covering the
    legal-effect window. Discipline: the sweep NEVER fabricates a version — it
    reads the timeline's OWN versions.
    """
    return any(
        version.effective == window.effective and version.expires == window.expires
        for version in timeline.versions
    )


def _fold_occupant_effective(
    timeline: Optional[ProvisionTimeline],
    window: _LegalEffectWindow,
) -> str:
    """The later-effective occupant fold order placed at/over the window slot.

    SCHED-02 evidence: when a window is NOT materialized, the slot is typically
    held by a later-effective occupant the document-order fold installed. We
    surface the earliest version whose ``effective`` is >= the window start (the
    deferred-commencement twin), best-effort, for the self-evidencing detail.
    Pure reporting helper; never raises.
    """
    if timeline is None:
        return ""
    candidates = [
        version.effective
        for version in timeline.versions
        if version.effective and version.effective >= window.effective
    ]
    if not candidates:
        return ""
    return min(candidates)


# ---------------------------------------------------------------------------
# SCHED-01/02/03 — disjoint-window materialization totality
# ---------------------------------------------------------------------------


def sweep_disjoint_window_materialization(
    products: "ReplayProducts",
) -> tuple[DisjointWindowFinding, ...]:
    """Assert disjoint-window materialization totality over one replay output.

    For every temporary legal-effect window reconstructed from
    ``products.temporal_events`` (SCHED-01: the legal-effect event, read off the
    temporal-event plane, NOT fold order), check the materialized
    ``products.timelines`` for a matching ``[effective, expires)`` version
    interval (SCHED-02). A window with no matching interval that is also not
    carried as a typed residual is ``SCHED.WINDOW_UNMATERIALIZED`` (SCHED-03),
    self-evidencing.

    Args:
        products: The already-produced :class:`ReplayProducts` (re-folds NOTHING,
                  re-applies NOTHING).

    Returns:
        A tuple of :class:`DisjointWindowFinding`, sorted by
        ``(target_address, window_effective, group_id)``. Empty when every
        legal-effect window is materialized as a version interval (the standing
        guard's clean state) OR carried as a typed residual.

    Discipline (tag-don't-guess): the sweep NEVER materializes a window or
    fabricates a version. Materialization is the timeline's OWN version intervals;
    a missing interval is surfaced, not back-stitched.
    """
    timelines: Mapping[LegalAddress, ProvisionTimeline] = products.timelines or {}
    residual_addresses = _residualized_window_addresses(products)

    findings: list[DisjointWindowFinding] = []
    for window in _legal_effect_windows(products.temporal_events):
        timeline = timelines.get(window.target_address)
        if timeline is not None and _timeline_has_window_interval(timeline, window):
            # SCHED-02 satisfied: the window is a real materialized interval.
            continue
        if window.target_address in residual_addresses:
            # SCHED-03 satisfied the other way: the window is carried as a typed
            # residual on the replay output, not silently dropped.
            continue
        occupant = _fold_occupant_effective(timeline, window)
        findings.append(
            DisjointWindowFinding(
                code=SCHED_WINDOW_UNMATERIALIZED,
                group_id=window.group_id,
                target_address=str(window.target_address),
                window_effective=window.effective,
                window_expires=window.expires,
                fold_occupant_effective=occupant,
                materialized=False,
                residualized=False,
                detail=(
                    f"legal-effect window [{window.effective}, {window.expires}) "
                    f"for {str(window.target_address)!r} (group={window.group_id!r}) "
                    f"is NOT materialized as a version interval and is NOT carried "
                    f"as a typed residual: "
                    + (
                        f"fold order placed a later-effective occupant "
                        f"(effective={occupant}) in the slot"
                        if occupant
                        else "no occupant version covers the slot"
                    )
                ),
            )
        )
    findings.sort(key=lambda f: (f.target_address, f.window_effective, f.group_id))
    return tuple(findings)


def _residualized_window_addresses(products: "ReplayProducts") -> frozenset[LegalAddress]:
    """Addresses whose disjoint window is already carried as a typed residual.

    SCHED-03 is satisfied either by a materialized interval OR by a typed
    residual. The replay output's typed timeline diagnostics
    (``materialization_issues`` :class:`TimelineIssue` records) carry the
    addresses the timeline engine could not cleanly materialize; a window whose
    address appears there is accounted-for (tagged), not silently dropped, so the
    sweep does not double-report it. Pure: reads the OWN typed-residual carrier.
    """
    addresses: set[LegalAddress] = set()
    for issue in products.materialization_issues:
        if issue.address is not None:
            addresses.add(issue.address)
    return frozenset(addresses)


__all__ = [
    "SCHED_WINDOW_UNMATERIALIZED",
    "DisjointWindowFinding",
    "sweep_disjoint_window_materialization",
]
