"""lawvm reconcile — diff replay-L1 vs oracle-L1 at one selector.

Two independent clean "what is in force at date D" views are computed and
compared:

  A = replay-L1   — provision-state(...).text.rendered, normalized. The
                    materialized PIT in-force text from amendment replay.
  B = oracle-L1   — the Finlex consolidated section run through the structural
                    temporal-span machinery (oracle_text.build_temporal_spans),
                    keeping only IN_FORCE / CURRENT spans (and IN_FORCE spans
                    whose entering-into-force date is ≤ D), dropping NOTE,
                    SUPERSEDED and future ENTERS_FORCE spans.

Divergence is the signal — it is NEVER silently resolved. When A and B agree we
show one clean view; when they disagree we show BOTH and classify the cause:

  - temporal   — the oracle carries a version/note whose entry-into-force date
                 straddles D in a way replay did not apply (or vice-versa). This
                 is the 2011/805 §3:1 case as of 2026: oracle marks 269/2026 in
                 force (eff 1.6.2026) but replay's PIT path is bounded by the
                 consolidated snapshot cutoff and did not materialize it.
  - editorial  — they differ with no temporal straddle (parse / dedup / prior-
                 wording drift).
  - presence   — one side has no provision at this selector.

This is the generalized antidote to the in-force-vs-superseded ambiguity that a
flat L0 read invites.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from lawvm.core.selector import (
    has_subprovision,
    section_scope_locator,
    to_locator_string,
)
from lawvm.tools.section_keys import _clean_section_text

# Agree threshold — matches the existing dedup thresholds elsewhere in the tool.
_AGREE_RATIO = 0.995

# Leading "12 § " (section number marker) the replay render prepends but the
# oracle structural body spans (build_temporal_spans, which skips num/heading)
# do not carry. Stripped before the agree comparison so a heading-only delta is
# never miscounted as a divergence.
_SECTION_NUM_MARKER_RE = re.compile(r"^\s*\d+\s*(?:[a-zäöå])?\s*§\s*")


def _strip_section_heading(replay_text: str, oracle_body: str) -> str:
    """Drop the leading 'N § <heading>' that replay renders but oracle-L1 omits.

    The replay-L1 section render is ``"<num> § <heading> <body...>"`` while
    oracle-L1 (built from build_temporal_spans, which skips <num>/<heading>) is
    body-only. Comparing them raw spuriously reports DISAGREE(editorial) even
    when the in-force bodies are identical (the 2011/805 §3:1 case after replay
    correctly applies a same-day amendment).

    Strategy (anchor-based, self-correcting):
      1. strip a leading "<num> § " marker if present;
      2. if the oracle body is non-trivial and occurs in the remaining replay
         text, drop everything before that anchor (this removes the heading
         without needing to know where the heading ends);
      3. otherwise return the marker-stripped text (best effort — never worse
         than today, and the heading delta is small relative to the body).

    This only normalizes the replay side toward the oracle's body-only view; it
    never invents or reorders content.
    """
    stripped = _SECTION_NUM_MARKER_RE.sub("", replay_text, count=1)
    anchor = (oracle_body or "").strip()
    if len(anchor) >= 24:
        # Anchor on a prefix of the oracle body to tolerate trailing drift.
        probe = anchor[:24]
        idx = stripped.find(probe)
        if idx > 0:
            return stripped[idx:]
    return stripped


@dataclass
class OracleL1:
    """The cruft-stripped 'what the oracle says is in force at D' view."""

    text: str
    available: bool
    basis: str  # "structural" | "inline_unsegmented" | "absent"
    version_markers: list[str] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    straddling_notes: list[dict[str, Any]] = field(default_factory=list)
    superseded_text: str = ""  # concatenated prior-wording spans (for discrimination)
    cutoff_date: str | None = None
    oracle_version_amendment_id: str | None = None
    locator: str = ""


def _parse_date(s: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def build_oracle_l1(
    statute_id: str,
    section_filter: str,
    as_of: str,
    at_amendment: str = "",
) -> OracleL1:
    """Compute oracle-L1 (in-force-at-D, cruft stripped) from structural spans."""
    from lawvm.tools.oracle_text import build_temporal_spans, load_oracle_section

    as_of_date = _parse_date(as_of) or datetime.date.today()
    loaded = load_oracle_section(statute_id, section_filter, at_amendment=at_amendment)
    if not loaded.get("found"):
        return OracleL1(
            text="",
            available=False,
            basis="absent",
            cutoff_date=loaded.get("oracle_cutoff_date"),
            oracle_version_amendment_id=loaded.get("oracle_version_amendment_id"),
            locator=loaded.get("locator", ""),
        )

    section_el = loaded["section_el"]
    spans = build_temporal_spans(section_el, today=as_of_date)
    has_markers = any(
        s["label"] in ("IN_FORCE", "ENTERS_FORCE", "SUPERSEDED", "NOTE") for s in spans
    )

    kept: list[str] = []
    version_markers: list[str] = []
    notes: list[dict[str, Any]] = []
    straddling: list[dict[str, Any]] = []
    superseded_parts: list[str] = []
    for span in spans:
        label = span["label"]
        if label == "NOTE":
            note = {"text": span["text"], "enters_force_date": span.get("enters_force_date")}
            notes.append(note)
            efd = _parse_date(span.get("enters_force_date") or "")
            if efd is not None and efd <= as_of_date:
                # A note whose commencement date has already passed → this is the
                # straddle signal (oracle announces a change in force ≤ D).
                straddling.append(note)
            continue
        if label == "SUPERSEDED":
            superseded_parts.append(span["text"])
            continue  # prior wording — never in force at D
        if label == "ENTERS_FORCE":
            efd = _parse_date(span.get("enters_force_date") or "")
            if efd is not None and efd > as_of_date:
                continue  # future — not yet in force at D
            kept.append(span["text"])
            if span.get("version"):
                version_markers.append(span["version"])
            continue
        # IN_FORCE or CURRENT
        kept.append(span["text"])
        if span.get("version"):
            version_markers.append(span["version"])

    if not has_markers:
        # No structural temporal markers in this section: oracle-L1 degrades to
        # raw prose with an inline_unsegmented basis (don't fake confidence).
        from lawvm.tools.oracle_text import _el_to_text

        return OracleL1(
            text=_el_to_text(section_el),
            available=True,
            basis="inline_unsegmented",
            version_markers=[],
            notes=notes,
            straddling_notes=straddling,
            superseded_text=" ".join(superseded_parts).strip(),
            cutoff_date=loaded.get("oracle_cutoff_date"),
            oracle_version_amendment_id=loaded.get("oracle_version_amendment_id"),
            locator=loaded.get("locator", ""),
        )

    return OracleL1(
        text=" ".join(kept).strip(),
        available=bool(kept),
        basis="structural",
        version_markers=version_markers,
        notes=notes,
        straddling_notes=straddling,
        superseded_text=" ".join(superseded_parts).strip(),
        cutoff_date=loaded.get("oracle_cutoff_date"),
        oracle_version_amendment_id=loaded.get("oracle_version_amendment_id"),
        locator=loaded.get("locator", ""),
    )


@dataclass
class ReconcileResult:
    selector: str
    locator: str
    statute_id: str
    as_of: str
    query_type: str
    verdict: str  # AGREE | DISAGREE
    divergence_class: str | None  # temporal | editorial | presence | None
    agree_ratio: float
    replay_text: str
    replay_available: bool
    replay_version: dict[str, Any] | None
    replay_source: dict[str, Any] | None
    replay_status: str
    oracle: OracleL1
    scope: str = "section"  # section comparison; "section_for_subprovision" caveat
    scope_note: str | None = None


def _classify_disagreement(
    *, clean_replay: str, clean_oracle_in_force: str, oracle: OracleL1
) -> str:
    """Classify a text DISAGREE as temporal vs editorial.

    Temporal requires positive evidence that the divergence is caused by an
    un-applied dated amendment, not just the presence of any old dated note:

      1. the oracle carries a note whose commencement date has passed (straddle),
         AND
      2. replay is plausibly 'stuck' on the prior version — i.e. the replay text
         resembles the oracle's SUPERSEDED (prior-wording) text at least as much
         as it resembles the oracle's in-force text.

    When (1) holds but (2) does not, the dated note is incidental and the diff is
    more likely editorial/structural drift, so we do NOT over-claim temporal.
    """
    if not oracle.straddling_notes:
        return "editorial"
    clean_superseded = _clean_section_text(oracle.superseded_text)
    if not clean_superseded:
        # A straddling note but no recoverable prior wording (e.g. added-only
        # momentti): treat the dated straddle as the temporal signal.
        return "temporal"
    sim_to_superseded = SequenceMatcher(None, clean_replay, clean_superseded).ratio()
    sim_to_in_force = SequenceMatcher(None, clean_replay, clean_oracle_in_force).ratio()
    if sim_to_superseded >= sim_to_in_force:
        return "temporal"
    return "editorial"


def reconcile_provision(
    *,
    statute_id: str,
    selector: str,
    as_of: str,
    query_type: str = "in_force",
    jurisdiction: str = "fi",
    at_amendment: str = "",
) -> ReconcileResult:
    """Compute the replay-L1 vs oracle-L1 reconciliation for one provision."""
    from lawvm.provision_state import resolve_provision_state

    # The oracle consolidated resolver segments whole <section> elements, not
    # momentit. So reconcile compares at SECTION granularity on both sides. When
    # the caller addressed a momentti/kohta we resolve the section and flag the
    # comparison scope rather than silently reporting a false 'presence' diff
    # (the oracle simply cannot address below the section).
    sub = has_subprovision(selector)
    section_locator = section_scope_locator(selector)
    locator = to_locator_string(selector)
    scope = "section"
    scope_note: str | None = None
    if sub:
        scope = "section_for_subprovision"
        scope_note = (
            f"selector {selector!r} addresses below the section; oracle-L1 is "
            f"section-granular, so reconcile compares at section scope "
            f"({section_locator})."
        )

    payload = resolve_provision_state(
        statute_id=statute_id,
        jurisdiction=jurisdiction,
        provision=section_locator,
        as_of=as_of,
        query_type=query_type,
        territory=None,
        include_ir=False,
        status_stream=sys.stderr,
    )
    replay_status = payload.get("provision_status", "")
    text_block = payload.get("text") or {}
    replay_text = text_block.get("rendered", "") if replay_status == "selected" else ""
    replay_available = bool(text_block.get("available")) and replay_status == "selected"

    oracle = build_oracle_l1(statute_id, section_locator, as_of, at_amendment=at_amendment)

    # Normalize the replay section render toward oracle-L1's body-only view:
    # replay prepends "<num> § <heading>" which oracle-L1 (num/heading-stripped
    # structural spans) never carries. Compare bodies, not the heading.
    replay_body = _strip_section_heading(replay_text, oracle.text)

    # Compare cleaned in-force text.
    clean_a = _clean_section_text(replay_body)
    clean_b = _clean_section_text(oracle.text)

    if not replay_available and not oracle.available:
        verdict, divergence, ratio = "DISAGREE", "presence", 0.0
    elif not replay_available or not oracle.available:
        verdict, divergence, ratio = "DISAGREE", "presence", 0.0
    else:
        ratio = SequenceMatcher(None, clean_a, clean_b).ratio()
        if clean_a == clean_b or ratio >= _AGREE_RATIO:
            verdict, divergence = "AGREE", None
        else:
            verdict = "DISAGREE"
            divergence = _classify_disagreement(
                clean_replay=clean_a,
                clean_oracle_in_force=clean_b,
                oracle=oracle,
            )

    return ReconcileResult(
        selector=selector,
        locator=locator,
        statute_id=statute_id,
        as_of=as_of,
        query_type=query_type,
        verdict=verdict,
        divergence_class=divergence,
        agree_ratio=round(ratio, 4),
        replay_text=replay_text,
        replay_available=replay_available,
        replay_version=payload.get("version"),
        replay_source=payload.get("source"),
        replay_status=replay_status,
        oracle=oracle,
        scope=scope,
        scope_note=scope_note,
    )


def _result_to_jsonable(r: ReconcileResult) -> dict[str, Any]:
    return {
        "selector": r.selector,
        "locator": r.locator,
        "statute_id": r.statute_id,
        "as_of": r.as_of,
        "query_type": r.query_type,
        "verdict": r.verdict,
        "divergence_class": r.divergence_class,
        "agree_ratio": r.agree_ratio,
        "scope": r.scope,
        "scope_note": r.scope_note,
        "replay": {
            "replay_status": r.replay_status,
            "available": r.replay_available,
            "text": r.replay_text,
            "version": r.replay_version,
            "source_amendment": (r.replay_source or {}).get("statute_id"),
        },
        "oracle": {
            "available": r.oracle.available,
            "basis": r.oracle.basis,
            "text": r.oracle.text,
            "version_markers": r.oracle.version_markers,
            "notes": r.oracle.notes,
            "straddling_notes": r.oracle.straddling_notes,
            "superseded_text": r.oracle.superseded_text,
            "cutoff_date": r.oracle.cutoff_date,
            "oracle_version_amendment_id": r.oracle.oracle_version_amendment_id,
        },
    }


def _src_of(r: ReconcileResult) -> str:
    return (r.replay_source or {}).get("statute_id") or "(base statute)"


def _render_human(r: ReconcileResult) -> str:
    lines: list[str] = []
    eff = (r.replay_version or {}).get("effective") or "—"
    header = f"{r.statute_id} {r.selector}  (in force @ {r.as_of})"
    scope_lines = [f"  · {r.scope_note}"] if r.scope_note else []
    if r.verdict == "AGREE":
        lines.append(f"{header}   ✔ replay and oracle agree")
        lines.extend(scope_lines)
        lines.append("")
        lines.append(f"  {r.replay_text}")
        lines.append("")
        lines.append(
            f"  ── eff {eff} · src {_src_of(r)} · query {r.query_type}"
            f" · agree {r.agree_ratio}"
        )
        return "\n".join(lines)

    # DISAGREE
    lines.append(f"{header}   ⚠ DISAGREE ({r.divergence_class})")
    lines.extend(scope_lines)
    lines.append("")
    if r.divergence_class == "presence":
        which = "replay" if not r.replay_available else "oracle"
        lines.append(f"  → {which} has no provision in force at this selector.")
        lines.append("")
    lines.append(f"  replay-L1 (effective {eff}, src {_src_of(r)}):")
    lines.append(f"    {r.replay_text or '[no text]'}")
    lines.append("")
    marker = ""
    if r.oracle.version_markers:
        marker = f" [{', '.join(dict.fromkeys(r.oracle.version_markers))}]"
    lines.append(f"  oracle-L1 (Finlex consolidated, basis={r.oracle.basis}){marker}:")
    lines.append(f"    {r.oracle.text or '[no text]'}")
    # The "replay did NOT apply" note and the snapshot-cutoff context line are
    # claims that replay is STUCK behind a dated change. Only emit them when the
    # divergence was actually classified temporal — after the amendment-ingestion
    # fix, replay routinely applies same-day/past-commencement amendments, so a
    # straddling note can co-exist with an editorial-only (or agreeing) result.
    is_temporal = r.divergence_class == "temporal"
    if is_temporal and r.oracle.straddling_notes:
        lines.append("")
        for note in r.oracle.straddling_notes:
            efd = note.get("enters_force_date")
            lines.append(
                f"    note: \"{note['text']}\""
                f" (commencement {efd} ≤ as-of; replay did NOT apply)"
            )
    lines.append("")
    lines.append(
        "  → DIVERGENCE is the signal. Do not assume either side. "
        f"Check: lawvm blame {r.statute_id} ; Finlex."
    )
    if is_temporal and r.oracle.cutoff_date:
        lines.append(
            f"  → context: replay PIT path is bounded by the consolidated oracle "
            f"snapshot cutoff {r.oracle.cutoff_date}."
        )
    return "\n".join(lines)


def main(args: Any) -> None:
    selector = getattr(args, "selector", "") or ""
    as_of = getattr(args, "as_of", "") or datetime.date.today().isoformat()
    query_type = getattr(args, "query_type", "in_force") or "in_force"
    jurisdiction = getattr(args, "jurisdiction", "fi")

    if jurisdiction != "fi":
        print(
            json.dumps({"reconcile_status": "unsupported_jurisdiction", "jurisdiction": jurisdiction})
            if getattr(args, "json", False)
            else f"reconcile currently supports jurisdiction 'fi' only (got {jurisdiction!r})",
            file=sys.stderr if not getattr(args, "json", False) else sys.stdout,
        )
        raise SystemExit(2)

    if not selector:
        _run_statute_scope(args, as_of, query_type)
        return

    result = reconcile_provision(
        statute_id=args.statute_id,
        selector=selector,
        as_of=as_of,
        query_type=query_type,
        jurisdiction=jurisdiction,
        at_amendment=getattr(args, "at_amendment", "") or "",
    )
    if getattr(args, "json", False):
        print(json.dumps(_result_to_jsonable(result), ensure_ascii=False, indent=2, default=str))
    else:
        print(_render_human(result))


def _run_statute_scope(args: Any, as_of: str, query_type: str) -> None:
    """Whole-statute reconcile: list only diverging sections."""
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml

    result = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(
            parent_id=args.statute_id,
            mode="legal_pit",
            as_of=as_of,
            quiet=True,
        ),
    )
    diverging: list[dict[str, Any]] = []
    checked = 0
    for addr in result.timelines:
        addr_str = str(addr)
        # Only reconcile section-level addresses (skip deep momentti rows).
        rr = reconcile_provision(
            statute_id=args.statute_id,
            selector=addr_str,
            as_of=as_of,
            query_type=query_type,
            jurisdiction="fi",
        )
        checked += 1
        if rr.verdict != "AGREE":
            diverging.append(_result_to_jsonable(rr))

    if getattr(args, "json", False):
        print(json.dumps(
            {"statute_id": args.statute_id, "as_of": as_of, "query_type": query_type,
             "checked": checked, "diverging_count": len(diverging), "diverging": diverging},
            ensure_ascii=False, indent=2, default=str,
        ))
        return

    print(f"{args.statute_id} reconcile @ {as_of} — checked {checked}, "
          f"{len(diverging)} diverging")
    for d in diverging:
        print(f"  ⚠ {d['locator']}  ({d['divergence_class']})  agree={d['agree_ratio']}")
