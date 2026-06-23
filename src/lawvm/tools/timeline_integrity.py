"""Timeline-integrity break classification for replay-backed query surfaces.

A replayed statute timeline is only as trustworthy as the compile fold that
produced it. When the apply machinery records evidence that an amendment's
effect is missing or was applied against a contradicted precondition, every
downstream answer derived from the timeline at/after that amendment is
UNPROVEN — and the query surfaces (provision-state seam, blame) must surface
that instead of returning clean-looking answers.

This module is the single classifier from replay findings to typed
``TimelineBreak`` records. Two scopes:

- ``scope="statute"`` — the shared-state compile fold itself is unproven from
  the breaking amendment onward. Every query with ``as_of`` at/after the
  break's effective date is unprovable; earlier queries are servable with a
  warning. Classified causes:
    * Targetless ``APPLY.OCCUPANCY_POLICY_VIOLATION`` true violations. Known
      section-target occupancy violations are address-scoped below.
    * any finding whose detail carries ``timeline_fatal: True`` — the
      forward-compatible hook for future timeline-fatal diagnostic classes
      (e.g. a recorded mid-timeline abort). Emitters mark the class once;
      every surface using this module picks it up.

- ``scope="address"`` — one op's effect is missing for a known target, the
  rest of the fold is unaffected. Queries on a matching address at/after the
  break are unprovable; other addresses are untouched (their responses must
  stay byte-identical). Classified causes:
    * ``APPLY.OCCUPANCY_POLICY_VIOLATION`` true violations with a known section
      target (the op's occupancy precondition contradicted that slot state).
      The soft ``allowed_non_primary`` observation is NOT a break.
    * ``APPLY.FAILED_OPERATION`` (a compiled op could not be applied; its
      target's post-amendment state is missing from the timeline).

- ``scope="window"`` — discovery/fallback evidence for a temporary-twin
  window. The document-order compile fold may omit a temporary gap-filler's
  text inside its own in-force window (its slot is held by a
  deferred-commencement twin). ``provision-state`` first attempts to
  materialize a proved legal-time interval from the compiled operation stream;
  if that proof boundary is missing, this break blocks queries on a matching
  address whose ``as_of`` falls INSIDE the window (start-inclusive,
  end-exclusive — the kernel ``expires`` convention). Other addresses and
  other ``as_of`` are untouched. Classified cause:
    * ``APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT`` → diagnostic code
      ``TEMPORAL.WINDOW_UNMATERIALIZED``.

Deliberately NOT classified as breaks (recorded obligations whose effect is
present and adjudicated): ``APPLY.RELABEL_SKIPPED`` (governed skip),
``APPLY.FALLBACK_WHOLE_SECTION_REPLACE`` (unproven-but-applied fallback),
``TIME.TRIGGER_COVERAGE_INCOMPLETE`` / ``COVERAGE.*`` (compile-path
completeness obligations, not text-state evidence). Widening the class is a
seam-visible semantics change: update the seam spec note when doing so.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from lawvm.tools.section_keys import norm_section_label

OCCUPANCY_VIOLATION_KIND = "APPLY.OCCUPANCY_POLICY_VIOLATION"
FAILED_OPERATION_KIND = "APPLY.FAILED_OPERATION"
DISJOINT_INSERT_KIND = "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT"
WINDOW_UNMATERIALIZED_CODE = "TEMPORAL.WINDOW_UNMATERIALIZED"


@dataclass(frozen=True)
class TimelineBreak:
    """Typed record of one timeline-integrity break in a replayed statute."""

    amendment_id: str
    diagnostic_code: str
    scope: str  # "statute" | "address" | "window"
    target_unit_kind: str = ""
    target_section: str = ""
    target_chapter: str = ""
    effective: str = ""  # ISO date of the breaking amendment; "" = unknown
    reason: str = ""
    # Window-scoped fields (only set for scope="window"). The break governs
    # exactly the interval [window_start, window_end) for the target address.
    # occupant_* identify the deferred-commencement twin holding the slot in
    # document order; rule_id names the apply-policy lane that detected the
    # disjoint insert. Self-evidencing: a consumer can see WHICH temporary
    # act's window is unmaterialized without reading our code.
    window_start: str = ""  # incoming_effective (inclusive first in-force day)
    window_end: str = ""  # incoming_expires (EXCLUSIVE kernel cutoff: first day not in force)
    occupant_source_statute: str = ""
    occupant_effective: str = ""
    rule_id: str = ""

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {
            "amendment_id": self.amendment_id,
            "diagnostic_code": self.diagnostic_code,
            "scope": self.scope,
            "target_unit_kind": self.target_unit_kind,
            "target_section": self.target_section,
            "target_chapter": self.target_chapter,
            "effective": self.effective,
            "reason": self.reason,
        }
        if self.scope == "window":
            wire["window"] = {
                "start": self.window_start,
                "end": self.window_end,
                "bounds": "start_inclusive_end_exclusive",
                "source_statute": self.amendment_id,
                "occupant_source_statute": self.occupant_source_statute,
                "occupant_effective": self.occupant_effective,
                "rule_id": self.rule_id,
            }
        return wire


def timeline_breaks_from_findings(findings: Iterable[Any]) -> tuple[TimelineBreak, ...]:
    """Classify replay findings into typed timeline breaks (dates unfilled)."""
    findings_tuple = tuple(findings)
    reconciled_occupancy_keys = _reconciled_occupancy_keys(findings_tuple)
    breaks: list[TimelineBreak] = []
    for finding in findings_tuple:
        kind = str(getattr(finding, "kind", "") or "")
        detail: Mapping[str, Any] = getattr(finding, "detail", None) or {}
        source_statute = str(getattr(finding, "source_statute", "") or "")
        if kind == OCCUPANCY_VIOLATION_KIND:
            if detail.get("allowed_non_primary"):
                # Allowed-but-non-primary occupancy note: legitimate lane
                # (e.g. reenactment onto a tombstone), not a break.
                continue
            target_section = str(detail.get("target_label") or "")
            target_chapter = str(detail.get("target_chapter") or "")
            if not target_chapter:
                target_chapter = _chapter_from_ctx_label(str(detail.get("ctx_label") or ""))
            if (
                source_statute,
                norm_section_label(target_section),
                norm_section_label(target_chapter),
            ) in reconciled_occupancy_keys:
                continue
            breaks.append(
                TimelineBreak(
                    amendment_id=source_statute,
                    diagnostic_code=kind,
                    scope="address" if target_section else "statute",
                    target_unit_kind="section",
                    target_section=target_section,
                    target_chapter=target_chapter,
                    reason=str(detail.get("current_occupancy") or ""),
                )
            )
        elif kind == FAILED_OPERATION_KIND:
            breaks.append(
                TimelineBreak(
                    amendment_id=str(detail.get("amendment_id") or source_statute),
                    diagnostic_code=kind,
                    scope="address",
                    target_unit_kind=str(detail.get("target_unit_kind") or ""),
                    target_section=str(detail.get("target_section") or ""),
                    target_chapter=str(detail.get("target_chapter") or ""),
                    reason=str(detail.get("reason_code") or ""),
                )
            )
        elif kind == DISJOINT_INSERT_KIND:
            # Discovery/fallback evidence for a temporary-twin window. The
            # provision-state scheduler may consume this break by proving the
            # exact temporary op payload/bounds/occupant; otherwise it remains
            # a fail-loud window+address block.
            # ``effective`` is seeded to the window start so the break sorts
            # and dedups like any other; window blocking uses the half-open
            # interval below, not this one-sided date.
            window_start = str(detail.get("incoming_effective") or "")
            breaks.append(
                TimelineBreak(
                    amendment_id=source_statute,
                    diagnostic_code=WINDOW_UNMATERIALIZED_CODE,
                    scope="window",
                    target_unit_kind="section",
                    target_section=str(detail.get("target_label") or ""),
                    effective=window_start,
                    reason=str(detail.get("rule_id") or ""),
                    window_start=window_start,
                    window_end=str(detail.get("incoming_expires") or ""),
                    occupant_source_statute=str(detail.get("occupant_source_statute") or ""),
                    occupant_effective=str(detail.get("occupant_effective") or ""),
                    rule_id=str(detail.get("rule_id") or ""),
                )
            )
        elif detail.get("timeline_fatal") is True:
            breaks.append(
                TimelineBreak(
                    amendment_id=str(detail.get("amendment_id") or source_statute),
                    diagnostic_code=kind,
                    scope="statute",
                    reason=str(detail.get("reason_code") or ""),
                )
            )
    return tuple(breaks)


def _reconciled_occupancy_keys(findings: Iterable[Any]) -> set[tuple[str, str, str]]:
    """Return occupancy-violation keys consumed by a temporal-window proof."""
    keys: set[tuple[str, str, str]] = set()
    for finding in findings:
        kind = str(getattr(finding, "kind", "") or "")
        if kind != DISJOINT_INSERT_KIND:
            continue
        detail: Mapping[str, Any] = getattr(finding, "detail", None) or {}
        if detail.get("reconciles_finding") != OCCUPANCY_VIOLATION_KIND:
            continue
        keys.add(
            (
                str(getattr(finding, "source_statute", "") or ""),
                norm_section_label(str(detail.get("target_label") or "")),
                norm_section_label(str(detail.get("target_chapter") or "")),
            )
        )
    return keys


def _chapter_from_ctx_label(ctx_label: str) -> str:
    """Extract a Finnish ``N luku`` chapter from an apply-policy context label."""
    if " luku " not in ctx_label:
        return ""
    prefix = ctx_label.split(" luku ", 1)[0].strip()
    if not prefix:
        return ""
    return prefix.rsplit(maxsplit=1)[-1].strip()


def attach_effective_dates(
    breaks: Iterable[TimelineBreak],
    lineage_records: Iterable[Mapping[str, Any]],
) -> tuple[TimelineBreak, ...]:
    """Fill each break's ``effective`` from replay lineage records."""
    effective_by_id = {
        str(record.get("statute_id") or ""): str(record.get("effective_date") or "")
        for record in lineage_records
    }
    return tuple(
        replace(item, effective=effective_by_id.get(item.amendment_id, ""))
        if not item.effective
        else item
        for item in breaks
    )


def _sort_key(item: TimelineBreak) -> tuple[str, str, str]:
    # Unknown effective dates sort FIRST: an undatable break must be treated
    # as governing every as_of (conservative), so it is the earliest.
    return (item.effective or "0000-00-00", item.amendment_id, item.diagnostic_code)


def sorted_breaks(breaks: Iterable[TimelineBreak]) -> tuple[TimelineBreak, ...]:
    """Sort breaks chronologically and drop exact duplicates."""
    return tuple(sorted(set(breaks), key=_sort_key))


def break_governs_as_of(item: TimelineBreak, as_of: str) -> bool:
    """True when ``as_of`` is governed by the break.

    Statute/address breaks govern every ``as_of`` at/after their effective date
    (and undatable breaks govern always). Window breaks govern ONLY inside
    ``[window_start, window_end)`` — outside the window the timeline is
    materialized normally and the answer is provable.
    """
    if item.scope == "window":
        # window_end carries the kernel's EXCLUSIVE expires cutoff (first day
        # NOT in force): on window_end itself the deferred twin commences and
        # the fold-materialized permanent text is correct, so the guard lifts
        # exactly there. An undatable window (missing either bound) governs
        # always (conservative).
        if not item.window_start or not item.window_end:
            return True
        return item.window_start <= as_of < item.window_end
    if not item.effective:
        return True
    return item.effective <= as_of


def break_matches_section(
    item: TimelineBreak,
    *,
    section_label: str,
    chapter_label: str = "",
) -> bool:
    """True when an address/window-scoped break targets the queried section."""
    if item.scope not in ("address", "window"):
        return False
    if item.target_unit_kind in ("chapter", "part"):
        # Container-scoped failed op: match when the queried chapter is the
        # failed container (unknown query chapter = conservative match).
        if not item.target_chapter and not item.target_section:
            return False
        container = item.target_chapter or item.target_section
        if not chapter_label:
            return True
        return norm_section_label(chapter_label) == norm_section_label(container)
    if not item.target_section or not section_label:
        return False
    if norm_section_label(item.target_section) != norm_section_label(section_label):
        return False
    if item.target_chapter and chapter_label:
        return norm_section_label(item.target_chapter) == norm_section_label(chapter_label)
    return True


__all__ = [
    "TimelineBreak",
    "WINDOW_UNMATERIALIZED_CODE",
    "DISJOINT_INSERT_KIND",
    "timeline_breaks_from_findings",
    "attach_effective_dates",
    "sorted_breaks",
    "break_governs_as_of",
    "break_matches_section",
]
