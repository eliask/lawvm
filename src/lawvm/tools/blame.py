"""lawvm blame — per-provision last-modification trace.

Like `git blame` for provisions. Each provision is annotated with the
amendment that last modified it and the sequence number of that op.

Usage:
    lawvm blame <statute_id>                               # all provisions
    lawvm blame <statute_id> --address "section:9a"        # single provision
    lawvm blame <statute_id> --source 2017/794             # filter by amendment
    lawvm blame <statute_id> --format json                 # machine-readable rows

Per-address status enum (the single source of truth — the human text is
DERIVED from it). House-style lowercase snake_case; the consumer's
SCREAMING_CASE names map 1:1:

    unmodified_base_text       — address resolves, no op attributed, AND no
                                 timeline break / failed op could govern it.
                                 The grounded negative.
    modified_by_op             — op(s) attributed to this address; the
                                 last-touching op is identified (``last_op``).
    op_unapplied_or_engine_error — a statute-scope timeline break exists, OR an
                                 address-scope failed op targets this address:
                                 the assessment is unverifiable from the break
                                 point on (``broken_at`` names the amendment).
    address_unresolved         — the address does not resolve in the tree and
                                 no break excuses it.

PRECEDENCE (as implemented in ``_classify_status``):

    op_unapplied_or_engine_error > {modified_by_op, unmodified_base_text}
    op_unapplied_or_engine_error > address_unresolved

``op_unapplied_or_engine_error`` is TERMINAL for the unverifiable portion and
takes precedence over every other state:

  * An address modified by an op in 2020 under a statute broken in 2025 reports
    ``op_unapplied_or_engine_error`` as its terminal status, with the attributed
    op STILL listed in ``last_op`` (the 2020 modification is proven; the CURRENT
    state is not).
  * With a break present, an unresolved address prefers
    ``op_unapplied_or_engine_error`` over ``address_unresolved`` — absence is
    unprovable under a break (this mirrors the provision-state rule).

Otherwise: an attributed op yields ``modified_by_op``; a resolved-but-untouched
address under a clean timeline yields ``unmodified_base_text``.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, cast

from lxml import etree

from lawvm.tools.section_keys import (
    display_section_key,
    extract_ir_sections,
    normalize_address_filter,
    norm_section_label,
    section_key_from_compiled_scope_row,
    section_key_from_target_dict,
    section_key_matches_filter,
    section_key_sort_key,
)
from lawvm.tools.timeline_integrity import (
    TimelineBreak,
    attach_effective_dates,
    break_matches_section,
    sorted_breaks,
    timeline_breaks_from_findings,
)
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.tools.provision_state import provision_selector_diagnostic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def _norm_num(s: str) -> str:
    return norm_section_label(s)


def _section_sort_key(key: str):
    return section_key_sort_key(key)


def _display_section(num: str) -> str:
    if num.endswith("§"):
        return num
    return f"{num} §" if not num.startswith("§") else num


# ---------------------------------------------------------------------------
# Build blame map from compiled_ops
# ---------------------------------------------------------------------------

def _apply_event_outcomes(
    apply_events: object,
) -> dict[tuple[str, str], set[str]]:
    """Return {(source_statute, op_id): outcomes} from replay apply events."""
    outcomes: dict[tuple[str, str], set[str]] = {}
    if not isinstance(apply_events, list):
        return outcomes
    for event in apply_events:
        if not isinstance(event, Mapping):
            continue
        event_map = cast(Mapping[str, object], event)
        source = str(event_map.get("source_statute") or "").strip()
        op_id = str(event_map.get("op_id") or "").strip()
        outcome = str(event_map.get("outcome") or "").strip()
        if not source or not op_id or not outcome:
            continue
        outcomes.setdefault((source, op_id), set()).add(outcome)
    return outcomes


def _op_has_applied_mutation_evidence(
    op: dict[str, Any],
    event_outcomes: dict[tuple[str, str], set[str]],
) -> bool:
    """Whether a compiled op may count as a modifying blame op.

    Legacy compiled rows may not have event evidence; keep those rows. When a
    row has a `(source_statute, op_id)` event key, require at least one applied
    event so skipped relabel/renumber rows cannot win last-modification blame.
    """
    source = str(op.get("source_statute") or "").strip()
    op_id = str(op.get("op_id") or "").strip()
    if not source or not op_id:
        return True
    outcomes = event_outcomes.get((source, op_id))
    if outcomes is None:
        return True
    return "applied" in outcomes


def _op_sequence_value(op: Mapping[str, Any]) -> int:
    sequence = op.get("sequence")
    if isinstance(sequence, bool):
        return -1
    if isinstance(sequence, int):
        return sequence
    if isinstance(sequence, float) and sequence.is_integer():
        return int(sequence)
    text = str(sequence or "").strip()
    return int(text) if text.isdecimal() else -1


def _op_is_later(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    return _op_sequence_value(candidate) >= _op_sequence_value(existing)


_CONTENT_TOUCH_ACTIONS = {"insert", "replace", "repeal"}
_PURE_REKEY_ACTIONS = {"renumber", "relabel", "move"}


def _action_family(op: Mapping[str, Any]) -> str:
    action = str(op.get("action") or "").strip().lower()
    if action in _CONTENT_TOUCH_ACTIONS:
        return "content"
    if action in _PURE_REKEY_ACTIONS:
        return "rekey"
    return "other"


def _op_is_better_blame(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    """Return True when ``candidate`` is a better section-level blame witness.

    Sequence remains the default. Within one source statute, however, an applied
    content mutation is the more useful blame witness than a pure same-wave
    relabel that only carries existing text to its new slot.
    """
    if str(candidate.get("source_statute") or "") == str(existing.get("source_statute") or ""):
        candidate_family = _action_family(candidate)
        existing_family = _action_family(existing)
        if candidate_family == "rekey" and existing_family == "content":
            return False
        if candidate_family == "content" and existing_family == "rekey":
            return True
    return _op_is_later(candidate, existing)


def _build_blame_map(
    compiled_ops: list[dict[str, Any]],
    apply_events: object = None,
) -> Dict[str, dict[str, Any]]:
    """Build {norm_section_num: last_op_dict} from compiled ops list.

    Later ops overwrite earlier ops for the same section, giving us
    the LAST amendment to touch each provision.

    Compiled-op rows are flat (``target_unit_kind``/``target_norm``/
    ``target_chapter`` fields); the legacy nested ``target`` dict shape is
    still accepted for older callers. When apply-event outcomes are available,
    skipped ops are not treated as modifying blame rows.
    """
    blame: Dict[str, dict[str, Any]] = {}
    event_outcomes = _apply_event_outcomes(apply_events)

    for op in compiled_ops:
        if not _op_has_applied_mutation_evidence(op, event_outcomes):
            continue
        key = section_key_from_target_dict(op.get("target") or {})
        if not key:
            key = section_key_from_compiled_scope_row(op)
        if not key:
            continue
        existing = blame.get(key)
        if existing is None or _op_is_better_blame(op, existing):
            blame[key] = op

    return blame


# ---------------------------------------------------------------------------
# Typed per-address status enum (single source of truth)
# ---------------------------------------------------------------------------

# House-style lowercase snake_case. The consumer (MeVM) maps these 1:1 to its
# SCREAMING_CASE names; the mapping is restated in the consumer notice.
STATUS_UNMODIFIED_BASE_TEXT = "unmodified_base_text"
STATUS_MODIFIED_BY_OP = "modified_by_op"
STATUS_OP_UNAPPLIED_OR_ENGINE_ERROR = "op_unapplied_or_engine_error"
STATUS_ADDRESS_UNRESOLVED = "address_unresolved"

BlameStatus = Literal[
    "unmodified_base_text",
    "modified_by_op",
    "op_unapplied_or_engine_error",
    "address_unresolved",
]


@dataclass(frozen=True)
class BlameRow:
    """One typed per-address blame record (the machine-readable shape)."""

    address: str
    status: BlameStatus
    last_op: Optional[dict[str, Any]] = None
    broken_at: str = ""

    def to_wire(self) -> Dict[str, Any]:
        wire: Dict[str, Any] = {"address": self.address, "status": self.status}
        if self.last_op is not None:
            op = self.last_op
            wire["last_op"] = {
                "source_statute": op.get("source_statute", ""),
                "source_title": op.get("source_title", ""),
                "op_id": op.get("op_id", ""),
                "sequence": op.get("sequence"),
                "action": op.get("action", ""),
            }
        if self.broken_at:
            wire["broken_at"] = self.broken_at
        return wire


def _classify_status(
    *,
    resolved: bool,
    op: Optional[dict[str, Any]],
    statute_break: Optional[TimelineBreak],
    address_break: Optional[TimelineBreak],
) -> Tuple[BlameStatus, str]:
    """Derive (status, broken_at) for one address. Single source of truth.

    Precedence: a governing break (statute-scope, or an address-scope failed
    op targeting this address) makes the CURRENT state unverifiable and is
    terminal — it wins over an attributed op, over a clean base text, and over
    an unresolved address (absence is unprovable under a break).
    """
    governing_break = statute_break or address_break
    if governing_break is not None:
        return STATUS_OP_UNAPPLIED_OR_ENGINE_ERROR, governing_break.amendment_id
    if op is not None:
        return STATUS_MODIFIED_BY_OP, ""
    if not resolved:
        return STATUS_ADDRESS_UNRESOLVED, ""
    return STATUS_UNMODIFIED_BASE_TEXT, ""


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _fmt_source(op: dict[str, Any]) -> str:
    src = op.get("source_statute", "?")
    title = str(op.get("source_title") or "")[:40]
    seq = op.get("sequence", "?")
    action = op.get("action", "?").upper()
    return f"{src}  [{seq:>3}] {action:<7}  {title}"


def _key_labels(key: str) -> Tuple[str, str]:
    """Extract (section_label, chapter_label) from a section key string."""
    section = ""
    chapter = ""
    for part in key.split("/"):
        if part.startswith("section:"):
            section = part.split(":", 1)[1]
        elif part.startswith("chapter:"):
            chapter = part.split(":", 1)[1]
    return section, chapter


def _op_for_section_key(
    key: str,
    blame_map: Dict[str, dict[str, Any]],
    all_section_keys: list[str],
) -> Optional[dict[str, Any]]:
    """Look up the blame op for one IR section key.

    Exact key match first. Compiled ops may carry less container context than
    the replayed IR key: e.g. an op scoped as ``chapter:2/section:9`` while the
    materialized section key is ``part:1/chapter:2/section:9``. Attribute such
    suffix-key ops only when that suffix identifies exactly one replay section.
    Section-only ops (``section:22``) use the same uniqueness rule.
    """
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()

    def add_candidate(op: Optional[dict[str, Any]]) -> None:
        if op is None:
            return
        dedupe_key = (
            str(op.get("source_statute") or ""),
            str(op.get("op_id") or ""),
            _op_sequence_value(op),
        )
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        candidates.append(op)

    add_candidate(blame_map.get(key))
    section, chapter = _key_labels(key)
    if not section:
        return max(candidates, key=_op_sequence_value) if candidates else None
    suffixes: list[str] = []
    if chapter:
        suffixes.append(f"chapter:{chapter}/section:{section}")
    suffixes.append(f"section:{section}")
    for suffix in suffixes:
        op = blame_map.get(suffix)
        if op is None:
            continue
        matches = [
            candidate
            for candidate in all_section_keys
            if candidate == suffix or candidate.endswith(f"/{suffix}")
        ]
        if len(matches) == 1 and matches[0] == key:
            add_candidate(op)
    return max(candidates, key=_op_sequence_value) if candidates else None


def _matching_address_break(
    key: str, address_breaks: Tuple[TimelineBreak, ...]
) -> Optional[TimelineBreak]:
    section, chapter = _key_labels(key)
    for item in address_breaks:
        if break_matches_section(item, section_label=section, chapter_label=chapter):
            return item
    return None


def _print_timeline_breaks(
    statute_breaks: Tuple[TimelineBreak, ...],
    address_breaks: Tuple[TimelineBreak, ...],
) -> None:
    seen: set[tuple[str, str, str]] = set()
    for item in statute_breaks:
        display_key = (item.amendment_id, item.diagnostic_code, item.effective)
        if display_key in seen:
            continue
        seen.add(display_key)
        effective = f", effective {item.effective}" if item.effective else ""
        print(f"  TIMELINE BROKEN at {item.amendment_id} ({item.diagnostic_code}{effective})")
    if statute_breaks:
        print(
            "    Compiled state from the breaking amendment onward is UNPROVEN."
            " Absence of ops below is NOT evidence that a provision is unamended."
        )
    if address_breaks:
        targets = ", ".join(
            f"{item.amendment_id} §{item.target_section or item.target_chapter}"
            f" ({item.reason})" if item.reason else
            f"{item.amendment_id} §{item.target_section or item.target_chapter}"
            for item in address_breaks
        )
        print(
            f"  FAILED OPS: {len(address_breaks)} compiled op(s) could not be applied"
            f" — affected provisions are unverifiable: {targets}"
        )
    if statute_breaks or address_breaks:
        print()


@dataclass
class _BlameResult:
    """Everything the renderers (text + JSON) need, built once."""

    statute_breaks: Tuple[TimelineBreak, ...]
    address_breaks: Tuple[TimelineBreak, ...]
    compiled_ops_count: int
    # ordered list of (section_key, display, BlameRow) for resolved sections
    sections: List[Tuple[str, str, BlameRow]] = field(default_factory=list)
    # set when --address was requested but resolved to no IR section
    unresolved: Optional[BlameRow] = None


def _build_blame_result(
    sid: str,
    address_filter: Optional[Tuple[str, str]],
    source_filter: Optional[str],
    mode: Literal["official_consolidation", "legal_pit"],
) -> Tuple[str, "_BlameResult"]:
    from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml

    compiled_ops: list[dict[str, Any]] = []
    replay_meta: dict[str, Any] = {}
    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=sid, mode=mode, quiet=True),
        sinks=ReplayXmlSinks(
            compiled_ops_out=compiled_ops,
            replay_meta_out=replay_meta,
        ),
    )
    timeline_breaks = sorted_breaks(
        attach_effective_dates(
            timeline_breaks_from_findings(getattr(master, "findings", ()) or ()),
            replay_meta.get("lineage") or (),
        )
    )
    statute_breaks = tuple(item for item in timeline_breaks if item.scope == "statute")
    address_breaks = tuple(item for item in timeline_breaks if item.scope == "address")
    # A statute-scope break makes the WHOLE current compiled state unproven;
    # blame reports terminal/current state (no per-address as_of), so any such
    # break governs every address. The chronologically-first one names it.
    governing_statute_break = statute_breaks[0] if statute_breaks else None

    blame_map = _build_blame_map(
        compiled_ops,
        apply_events=replay_meta.get("apply_mutation_events"),
    )

    # Collect sections from replayed IRNode tree (for ordering and display)
    replay_secs_ir = extract_ir_sections(master.ir)
    all_section_keys = sorted(replay_secs_ir, key=_section_sort_key)
    unique_sections = list(all_section_keys)

    # Apply filters
    if address_filter:
        unique_sections = [k for k in unique_sections if section_key_matches_filter(k, address_filter)]

    if source_filter:
        # Only show sections whose last-touching amendment matches source_filter
        unique_sections = [
            k for k in unique_sections
            if (_op_for_section_key(k, blame_map, all_section_keys) or {}).get(
                "source_statute", ""
            ).strip() == source_filter.strip()
        ]

    result = _BlameResult(
        statute_breaks=statute_breaks,
        address_breaks=address_breaks,
        compiled_ops_count=len(compiled_ops),
    )
    for key in unique_sections:
        display = display_section_key(key)
        op = _op_for_section_key(key, blame_map, all_section_keys)
        addr_break = _matching_address_break(key, address_breaks)
        status, broken_at = _classify_status(
            resolved=True,
            op=op,
            statute_break=governing_statute_break,
            address_break=addr_break,
        )
        result.sections.append(
            (key, display, BlameRow(address=key, status=status, last_op=op, broken_at=broken_at))
        )

    # --address requested but no IR section resolved: classify the requested
    # address itself. Under a break, absence is unprovable → prefer
    # op_unapplied_or_engine_error over address_unresolved (mirrors
    # provision-state).
    if address_filter and source_filter is None and not result.sections:
        addr_str = f"{address_filter[0]}:{address_filter[1]}"
        status, broken_at = _classify_status(
            resolved=False,
            op=None,
            statute_break=governing_statute_break,
            address_break=None,
        )
        result.unresolved = BlameRow(address=addr_str, status=status, broken_at=broken_at)

    return master.title, result


def _emit_json(sid: str, title: str, result: "_BlameResult") -> None:
    rows = [row.to_wire() for _k, _d, row in result.sections]
    if result.unresolved is not None:
        rows.append(result.unresolved.to_wire())
    payload = {
        "statute_id": sid,
        "title": title,
        "ops_compiled": result.compiled_ops_count,
        "timeline_breaks": [b.to_wire() for b in (*result.statute_breaks, *result.address_breaks)],
        "provisions": rows,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def _blame_sync(
    sid: str,
    address_filter: Optional[Tuple[str, str]],
    source_filter: Optional[str],
    mode: Literal["official_consolidation", "legal_pit"],
    output_format: str = "text",
) -> None:
    title, result = _build_blame_result(sid, address_filter, source_filter, mode)
    if output_format == "json":
        _emit_json(sid, title, result)
        return

    statute_breaks = result.statute_breaks
    address_breaks = result.address_breaks
    unique_sections = [key for key, _d, _row in result.sections]

    print(f"Statute : {sid}")
    print(f"Title   : {title}")
    if address_filter:
        print(f"Address : {address_filter[0]}:{address_filter[1]}")
    if source_filter:
        print(f"Source  : {source_filter}")
    print(f"Ops     : {result.compiled_ops_count} compiled")
    print()
    _print_timeline_breaks(statute_breaks, address_breaks)

    col_w = 12
    # The human text is DERIVED from each row's typed status. The address-vs-
    # statute break wording detail is looked up from the break records (which
    # carry the diagnostic code / reason the enum deliberately abstracts away).
    unblamed: List[Tuple[str, str]] = []
    for key, display, row in result.sections:
        if row.last_op is None:
            unblamed.append((key, display))
            continue
        failed = _matching_address_break(key, address_breaks)
        suffix = (
            f"  !! later op from {failed.amendment_id} FAILED to apply"
            f" ({failed.reason or failed.diagnostic_code}) — shown state unverifiable"
            if failed is not None
            else ""
        )
        print(f"  {display:<{col_w}}  {_fmt_source(row.last_op)}{suffix}")

    if unblamed:
        unverifiable = [
            (key, display, _matching_address_break(key, address_breaks))
            for key, display in unblamed
        ]
        failed_rows = [(d, brk) for _k, d, brk in unverifiable if brk is not None]
        clean_rows = [d for _k, d, brk in unverifiable if brk is None]
        if failed_rows:
            print()
            print("  (no op applied — op FAILED to compile; state unverifiable:)")
            for display, brk in failed_rows:
                print(
                    f"    {display}  !! op from {brk.amendment_id} FAILED to apply"
                    f" ({brk.reason or brk.diagnostic_code}) — unverifiable"
                )
        if clean_rows:
            print()
            if statute_breaks:
                first = statute_breaks[0]
                print(
                    f"  (no op compiled — UNVERIFIABLE after {first.amendment_id}"
                    f" ({first.diagnostic_code}): the timeline is broken, so 'no op'"
                    " is not evidence of 'unamended':)"
                )
            else:
                print("  (unmodified — base statute text, no op compiled:)")
            for display in clean_rows:
                print(f"    {display}")

    if result.unresolved is not None:
        row = result.unresolved
        if row.status == STATUS_OP_UNAPPLIED_OR_ENGINE_ERROR:
            print(
                f"  {row.address}: UNVERIFIABLE after {row.broken_at} — the timeline"
                " is broken, so 'address not found' is not provable."
            )
        else:
            print(f"  {row.address}: address not resolved in this statute.")

    print()
    if statute_breaks:
        print(f"  {len(unique_sections) - len(unblamed)} provisions annotated, "
              f"{len(unblamed)} without compiled op (UNVERIFIABLE — timeline broken)")
    else:
        print(f"  {len(unique_sections) - len(unblamed)} provisions annotated, "
              f"{len(unblamed)} from base statute")


def _parse_address(address: Optional[str]) -> Optional[Tuple[str, str]]:
    if not address or ":" not in address:
        return None
    if "/" in address:
        return ("path", normalize_address_filter(address))
    kind, num = address.split(":", 1)
    return (kind.strip(), num.strip())


def _reject_invalid_address_selector(args: Any) -> None:
    """Fail fast for definitely malformed CLI address filters.

    ``blame`` is not a provision-state seam, but it consumes the same
    command-line legal-address grammar.  Reuse the provision-state selector
    diagnostic so a malformed filter cannot be silently normalized or ignored.
    """

    address = getattr(args, "address", None)
    if not address:
        return
    diagnostic = provision_selector_diagnostic(
        jurisdiction=str(getattr(args, "jurisdiction", "fi") or "fi"),
        provision=str(address),
    )
    if diagnostic is None:
        return

    message = str(diagnostic.get("message") or "invalid legal-address selector")
    print(
        f"ERROR: invalid --address/--provision {str(address)!r}: {message}",
        file=sys.stderr,
    )
    suggestions = diagnostic.get("suggestions")
    if isinstance(suggestions, list) and suggestions:
        print(f"help: try {str(suggestions[0])!r}", file=sys.stderr)
    raise SystemExit(2)


def main(args) -> None:
    _reject_invalid_address_selector(args)
    address_filter = _parse_address(getattr(args, "address", None))
    source_filter = getattr(args, "source", None)
    mode = getattr(args, "mode", "official_consolidation")
    output_format = getattr(args, "format", "text") or "text"

    _blame_sync(
        sid=args.statute_id,
        address_filter=address_filter,
        source_filter=source_filter,
        mode=mode,
        output_format=output_format,
    )
