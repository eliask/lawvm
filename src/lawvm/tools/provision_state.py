"""Stable provision-state seam surface for point-in-time consumers."""

from __future__ import annotations

import asyncio
import datetime as dt
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from lawvm.corpus_store import statute_url
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash, irnode_to_text
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_locator import SourceLocator
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.statute_validity import StatuteValidityBound, is_expired_at
from lawvm.core.temporal_scheduler import TemporalScheduleDelta
from lawvm.core.timeline import materialize_pit
from lawvm.core.timeline_lineage import lineage_address_chain
from lawvm.core.timeline_selection import (
    VersionSelectionCoverage,
    VersionSelectionResult,
    select_active_version_ex,
)
from lawvm.core.timeline_selection import content_is_repeal_placeholder
from lawvm.core.tree_ops import resolve as resolve_tree
from lawvm.roman import arabic_to_roman
from lawvm.tools.timeline_integrity import (
    TimelineBreak,
    break_governs_as_of,
    break_matches_section,
    sorted_breaks,
)

SCHEMA = "lawvm.provision_state.v1"
SPEC_VERSION = "0.3"
DUMP_SCHEMA = "lawvm.dump.v1"

_FI_PROSE_SECTION_RE = re.compile(r"^\s{0,16}(?P<number>\d{1,6})\s{0,8}(?P<letter>[A-Za-z])?\s{0,8}§\s{0,16}$")
_FI_HYBRID_SECTION_RE = re.compile(
    r"^\s{0,16}section\s{0,8}:\s{0,8}(?P<number>\d{1,6})\s{0,8}(?P<letter>[A-Za-z])?\s{0,8}§\s{0,16}$"
)
_FI_SUFFIX_AS_SUBSECTION_RE = re.compile(
    r"^\s*section\s*:\s*(?P<number>\d+)\s*/\s*subsection\s*:\s*(?P<letter>[A-Za-z])\s*$"
)
_FI_SPACED_SECTION_LABEL_RE = re.compile(r"^(?P<number>\d+)\s+(?P<letter>[A-Za-z])$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_QUERY_TYPES = frozenset({"governing", "in_force"})

# Rollback flag (Pro §5). Fixed-term statute bounds are DEFAULT-ON since seam
# spec 0.2 (corpus soak criteria met: residual blocking statutes well under
# 0.1%, all typed, zero ambiguous). Set to "0"/"false" to restore the 0.1
# flag-OFF behavior (no expired/expiry_unverified statuses). Extraction and
# diagnostics are always available; only the selection/seam SEMANTICS are
# gated.
FIXED_TERM_BOUNDS_FLAG = "LAWVM_ENABLE_FIXED_TERM_STATUTE_BOUNDS"


def _fixed_term_bounds_enabled() -> bool:
    return os.environ.get(FIXED_TERM_BOUNDS_FLAG, "1") not in ("0", "false", "False")


# Rollback flag for timeline-integrity surfacing. When a replayed timeline
# carries break evidence (see lawvm.tools.timeline_integrity), the seam marks
# the response instead of serving a clean-looking answer. Default ON; set to
# "0"/"false" to restore the prior (dishonest) behavior. Responses for
# statutes WITHOUT break evidence are byte-identical either way.
TIMELINE_INTEGRITY_FLAG = "LAWVM_ENABLE_TIMELINE_INTEGRITY_SURFACING"


def _timeline_integrity_enabled() -> bool:
    return os.environ.get(TIMELINE_INTEGRITY_FLAG, "1") not in ("0", "false", "False")


@dataclass(frozen=True)
class FixedTermSeamOverlay:
    """Computed fixed-term outcome for one PIT query, applied at the seam.

    ``kind`` is one of:
      - "expired": a governing whole-law bound has lapsed at as_of.
      - "blocked_unparseable": a recognised whole-law expiry clause governs but
        its date is unparseable — a live answer would be unsafe.
      - "blocked_ambiguous": conflicting whole-law bounds at the governing
        effective date.
    """

    kind: str
    diagnostic_code: str
    valid_until: str = ""
    expires_on: str = ""
    bound: StatuteValidityBound | None = None
    late_extension_gap: bool = False


@dataclass(frozen=True)
class AddressResolution:
    """Resolved timeline address, preserving ambiguity as data."""

    resolution_status: str
    requested: str
    address: LegalAddress | None = None
    timeline: ProvisionTimeline | None = None
    candidates: tuple[LegalAddress, ...] = ()
    suggestions: tuple[LegalAddress, ...] = ()
    mode: str = ""


def main(args: Any) -> None:
    asyncio.run(_main(args))


async def _main(args: Any) -> None:
    from lawvm.provision_state import resolve_provision_state

    payload = resolve_provision_state(
        statute_id=args.statute_id,
        jurisdiction=args.jurisdiction,
        provision=args.provision,
        as_of=args.as_of,
        query_type=args.query_type,
        territory=args.territory,
        include_ir=args.include_ir,
        status_stream=sys.stderr,
    )
    _emit_cli_diagnostic(payload, stream=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    if payload.get("provision_status") in {"invalid_address", "invalid_query"}:
        raise SystemExit(2)


def _emit_cli_diagnostic(payload: Mapping[str, Any], *, stream: Any) -> None:
    """Render human-facing diagnostics while preserving the JSON seam payload."""

    diagnostic = payload.get("diagnostic")
    if not isinstance(diagnostic, Mapping):
        return

    status = str(payload.get("provision_status") or "")
    query = payload.get("query")
    provision = ""
    if isinstance(query, Mapping):
        provision = str(query.get("provision") or "")
    if not provision:
        provision = str(payload.get("section_filter") or "")

    message = str(diagnostic.get("message") or "provision selector could not be resolved")
    target = f" {provision!r}" if provision else ""
    if status == "invalid_address":
        print(f"ERROR: invalid --provision{target}: {message}", file=stream)
    elif status == "invalid_query":
        field = str(diagnostic.get("field") or "query")
        option = f"--{field.replace('_', '-')}"
        print(f"ERROR: invalid {option}: {message}", file=stream)
    elif status == "address_not_found":
        print(f"note: --provision{target} was not found: {message}", file=stream)
    else:
        print(f"note: --provision{target}: {message}", file=stream)

    suggestions = diagnostic.get("suggestions")
    if isinstance(suggestions, list) and suggestions:
        print(f"help: try {str(suggestions[0])!r}", file=stream)

    nearby = diagnostic.get("nearby_address_candidates")
    if isinstance(nearby, list) and nearby:
        candidate_texts = [
            str(item.get("text"))
            for item in nearby
            if isinstance(item, Mapping) and item.get("text")
        ]
        if candidate_texts:
            print(
                "help: nearest materialized addresses include: "
                + ", ".join(candidate_texts[:5]),
                file=stream,
            )


def build_provision_state_response(
    *,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...] = (),
    statute_id: str,
    jurisdiction: str,
    provision: str,
    as_of: str,
    query_type: str = "governing",
    territory: str | None = None,
    include_ir: bool = False,
    title: str = "",
    base: IRStatute | None = None,
    timeline_breaks: tuple[TimelineBreak, ...] = (),
    temporal_schedule_deltas: tuple[TemporalScheduleDelta, ...] = (),
    findings: tuple[Finding, ...] = (),
    source_xml_provider: Callable[[str], bytes | None] | None = None,
) -> dict[str, Any]:
    """Return a stable provision-state response for one PIT address query."""

    query_diagnostic = provision_query_diagnostic(as_of=as_of, query_type=query_type)
    if query_diagnostic is not None:
        return invalid_query_payload(
            jurisdiction=jurisdiction,
            statute_id=statute_id,
            provision=provision,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            diagnostic=query_diagnostic,
        )
    selector_diagnostic = provision_selector_diagnostic(
        jurisdiction=jurisdiction,
        provision=provision,
    )
    if selector_diagnostic is not None:
        return invalid_provision_selector_payload(
            jurisdiction=jurisdiction,
            statute_id=statute_id,
            provision=provision,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            diagnostic=selector_diagnostic,
        )

    query = _query_payload(
        statute_id=statute_id,
        provision=provision,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )
    resolution = resolve_address_for_query(
        timelines,
        provision,
        as_of=as_of,
        query_type=_query_type_literal(query_type),
        territory=territory,
    )
    if not _timeline_integrity_enabled():
        timeline_breaks = ()
    relevant_breaks = _relevant_timeline_breaks(
        timeline_breaks,
        address=resolution.address,
        requested=provision,
        as_of=as_of,
    )
    tl_marker, tl_block, tl_blocking = _timeline_integrity_payloads(relevant_breaks, as_of)
    if resolution.resolution_status != "resolved":
        status = "timeline_unverified" if tl_blocking else resolution.resolution_status
        if tl_block is not None:
            # Preserve the resolution outcome as data: a blocking break means
            # even "address_not_found" is unprovable (the breaking amendment
            # could have created or renumbered the address).
            tl_block = {**tl_block, "resolution_status": resolution.resolution_status}
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "spec_version": SPEC_VERSION,
            "jurisdiction": jurisdiction,
            "statute_id": statute_id,
            "title": title,
            "provision_status": status,
            "query": query,
            "resolved_address": None,
            "lineage": _lineage_payload(
                jurisdiction=jurisdiction,
                statute_id=statute_id,
                address=None,
                migration_events=migration_events,
                as_of=as_of,
            ),
            "address_candidates": [_address_wire(candidate) for candidate in resolution.candidates],
            "selection": None,
            "hashes": _hash_payload(
                payload_status=status,
                statute_id=statute_id,
                jurisdiction=jurisdiction,
                query=query,
                address=None,
                lineage=None,
                version=None,
                content_hash="",
                timeline_broken_at=tl_marker,
                timeline_integrity=tl_block,
            ),
            "engine": _engine_payload(),
            "source_locator": None,
            "source_locator_status": "unavailable_unresolved_provision",
        }
        diagnostic = _address_resolution_diagnostic(resolution)
        if diagnostic is not None:
            payload["diagnostic"] = diagnostic
        if tl_block is not None:
            payload["timeline_broken_at"] = tl_marker
            payload["timeline_integrity"] = tl_block
        return payload

    assert resolution.timeline is not None
    selection = select_active_version_ex(
        resolution.timeline,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )
    selection = _mask_descendant_selection_by_ancestor_tombstone(
        timelines=timelines,
        address=resolution.address,
        selection=selection,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )
    overlay = (
        None
        if tl_blocking
        else _fixed_term_overlay(
            timelines=timelines,
            statute_id=statute_id,
            selection=selection,
            as_of=as_of,
            query_type=query_type,
        )
    )
    return _selected_response(
        timelines=timelines,
        selection=selection,
        resolution=resolution,
        migration_events=migration_events,
        statute_id=statute_id,
        jurisdiction=jurisdiction,
        query=query,
        include_ir=include_ir,
        title=title,
        base=base,
        overlay=overlay,
        timeline_broken_at=tl_marker,
        timeline_integrity=tl_block,
        timeline_blocking=tl_blocking,
        temporal_schedule_deltas=temporal_schedule_deltas,
        findings=findings,
        source_xml_provider=source_xml_provider,
    )


def _mask_descendant_selection_by_ancestor_tombstone(
    *,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    address: LegalAddress | None,
    selection: VersionSelectionResult,
    as_of: str,
    query_type: str,
    territory: str | None,
) -> VersionSelectionResult:
    """Make direct provision reads respect selected ancestor tombstones.

    PIT materialization already suppresses descendants under a selected
    whole-node repeal tombstone. A single-address provision-state query must
    expose the same legal state; otherwise a repealed chapter can disappear from
    the materialized statute while its child sections remain directly readable.
    """

    if address is None or selection.selection_status != "selected" or selection.version is None:
        return selection
    child_version = selection.version
    for depth in range(len(address.path) - 1, 0, -1):
        ancestor_address = LegalAddress(path=address.path[:depth])
        ancestor_timeline = timelines.get(ancestor_address)
        if ancestor_timeline is None:
            continue
        ancestor_selection = select_active_version_ex(
            ancestor_timeline,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
        )
        ancestor_version = ancestor_selection.version
        if ancestor_version is None:
            continue
        if (
            ancestor_version.content is not None
            and not content_is_repeal_placeholder(ancestor_version.content)
        ):
            continue
        if (ancestor_version.effective, ancestor_version.enacted) < (
            child_version.effective,
            child_version.enacted,
        ):
            continue
        ancestor_certificate = ancestor_selection.certificate
        return VersionSelectionResult(
            selection_status="selected",
            version=replace(ancestor_version, content=None, content_hash=""),
            certificate=VersionSelectionCoverage(
                address=address,
                as_of=as_of,
                query_type=query_type,
                territory=territory,
                selected_rail=(
                    ancestor_certificate.selected_rail
                    if ancestor_certificate is not None
                    and ancestor_certificate.selected_rail in {"overlay", "background"}
                    else "background"
                ),
                candidate_count=(
                    ancestor_certificate.candidate_count
                    if ancestor_certificate is not None
                    else 0
                ),
                selected_effective=ancestor_version.effective,
                selected_enacted=ancestor_version.enacted,
            ),
        )
    return selection


def provision_selector_diagnostic(
    *,
    jurisdiction: str,
    provision: str,
) -> dict[str, Any] | None:
    """Return an early diagnostic for definitely malformed provision selectors."""

    if jurisdiction != "fi":
        return None
    raw_text = str(provision or "")
    text = raw_text.strip()
    if not text:
        return {
            "code": "FI_PROVISION_SELECTOR_EMPTY",
            "message": "empty --provision is not a valid LawVM legal address",
            "suggestions": [],
        }
    if raw_text != text:
        canonical = _canonical_address_selector(text)
        return {
            "code": "LAWVM_PROVISION_SELECTOR_NON_CANONICAL_WHITESPACE",
            "message": "LawVM legal-address selectors must not contain leading or trailing whitespace",
            "suggestions": [canonical] if canonical is not None else [],
        }
    hybrid = _FI_HYBRID_SECTION_RE.fullmatch(text)
    if hybrid is not None:
        suggestion = _fi_section_suggestion(hybrid.group("number"), hybrid.group("letter"))
        return {
            "code": "FI_PROVISION_SELECTOR_MALFORMED_HYBRID",
            "message": (
                "LawVM legal-address selectors are kind:label paths without the Finnish § sign"
            ),
            "suggestions": [suggestion],
        }
    prose = _FI_PROSE_SECTION_RE.fullmatch(text)
    if prose is not None:
        suggestion = _fi_section_suggestion(prose.group("number"), prose.group("letter"))
        return {
            "code": "FI_PROVISION_SELECTOR_UNSUPPORTED_PROSE_NOTATION",
            "message": (
                "this looks like Finnish pykälä notation; LawVM CLI selectors expect "
                "a canonical LawVM address"
            ),
            "suggestions": [suggestion],
        }
    suffix_as_subsection = _FI_SUFFIX_AS_SUBSECTION_RE.fullmatch(text)
    if suffix_as_subsection is not None:
        suggestion = _fi_section_suggestion(
            suffix_as_subsection.group("number"),
            suffix_as_subsection.group("letter"),
        )
        return {
            "code": "FI_PROVISION_SELECTOR_SUFFIX_AS_SUBSECTION",
            "message": (
                "Finnish letter suffixes such as '127 a §' are section labels, "
                "not subsection labels"
            ),
            "suggestions": [suggestion],
        }
    malformed_parts = [part.strip() for part in text.split("/") if ":" not in part]
    if malformed_parts:
        return {
            "code": "LAWVM_PROVISION_SELECTOR_MALFORMED_PATH",
            "message": "LawVM legal-address path segments must be kind:label pairs",
            "suggestions": [],
            "malformed_segments": malformed_parts,
        }
    for part in text.split("/"):
        kind, label = (piece.strip() for piece in part.split(":", 1))
        spaced_section = _FI_SPACED_SECTION_LABEL_RE.fullmatch(label)
        if kind == "section" and spaced_section is not None:
            suggestion = _fi_section_suggestion(
                spaced_section.group("number"),
                spaced_section.group("letter"),
            )
            return {
                "code": "FI_PROVISION_SELECTOR_NON_CANONICAL_SECTION_LABEL",
                "message": (
                    "Finnish letter-suffixed section labels must use compact "
                    "canonical LawVM form"
                ),
                "suggestions": [suggestion],
            }
    canonical = _canonical_address_selector(text)
    if canonical is not None and canonical != text:
        return {
            "code": "LAWVM_PROVISION_SELECTOR_NON_CANONICAL_WHITESPACE",
            "message": "LawVM legal-address selectors must not contain whitespace around path separators, kinds, or labels",
            "suggestions": [canonical],
        }
    return None


def _canonical_address_selector(value: str) -> str | None:
    parts: list[str] = []
    for part in value.split("/"):
        if ":" not in part:
            return None
        kind, label = part.split(":", 1)
        kind = kind.strip()
        label = label.strip()
        if not kind or not label:
            return None
        parts.append(f"{kind}:{label}")
    return "/".join(parts)


def provision_query_diagnostic(
    *,
    as_of: str,
    query_type: str,
) -> dict[str, Any] | None:
    """Return an early diagnostic for definitely malformed PIT query fields."""

    if query_type not in _QUERY_TYPES:
        return {
            "code": "LAWVM_PROVISION_QUERY_TYPE_INVALID",
            "field": "query_type",
            "message": "query_type must be one of: governing, in_force",
            "allowed_values": sorted(_QUERY_TYPES),
        }
    text = str(as_of or "")
    if not text:
        return {
            "code": "LAWVM_PROVISION_AS_OF_EMPTY",
            "field": "as_of",
            "message": "as_of must be a non-empty ISO date in YYYY-MM-DD form",
            "expected_format": "YYYY-MM-DD",
        }
    if _ISO_DATE_RE.fullmatch(text) is None:
        return {
            "code": "LAWVM_PROVISION_AS_OF_INVALID",
            "field": "as_of",
            "message": "as_of must be exactly an ISO date in YYYY-MM-DD form",
            "expected_format": "YYYY-MM-DD",
            "received": as_of,
        }
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return {
            "code": "LAWVM_PROVISION_AS_OF_INVALID",
            "field": "as_of",
            "message": "as_of must be a real calendar date in YYYY-MM-DD form",
            "expected_format": "YYYY-MM-DD",
            "received": as_of,
        }
    return None


def invalid_provision_selector_payload(
    *,
    jurisdiction: str,
    statute_id: str,
    provision: str,
    as_of: str,
    query_type: str = "governing",
    territory: str | None = None,
    diagnostic: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a stable invalid-address seam payload without running replay."""

    query = _query_payload(
        statute_id=statute_id,
        provision=provision,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )
    return {
        "schema": SCHEMA,
        "spec_version": SPEC_VERSION,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "title": "",
        "provision_status": "invalid_address",
        "query": query,
        "resolved_address": None,
        "lineage": _lineage_payload(
            jurisdiction=jurisdiction,
            statute_id=statute_id,
            address=None,
            migration_events=(),
            as_of=as_of,
        ),
        "address_candidates": [],
        "selection": None,
        "hashes": _hash_payload(
            payload_status="invalid_address",
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            query=query,
            address=None,
            lineage=None,
            version=None,
            content_hash="",
        ),
        "engine": _engine_payload(),
        "source_locator": None,
        "source_locator_status": "unavailable_invalid_provision",
        "diagnostic": dict(diagnostic),
    }


def invalid_query_payload(
    *,
    jurisdiction: str,
    statute_id: str,
    provision: str,
    as_of: str,
    query_type: str = "governing",
    territory: str | None = None,
    diagnostic: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a stable invalid-query seam payload without running replay."""

    query = _query_payload(
        statute_id=statute_id,
        provision=provision,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )
    return {
        "schema": SCHEMA,
        "spec_version": SPEC_VERSION,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "title": "",
        "provision_status": "invalid_query",
        "query": query,
        "resolved_address": None,
        "lineage": _lineage_payload(
            jurisdiction=jurisdiction,
            statute_id=statute_id,
            address=None,
            migration_events=(),
            as_of=as_of,
        ),
        "address_candidates": [],
        "selection": None,
        "hashes": _hash_payload(
            payload_status="invalid_query",
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            query=query,
            address=None,
            lineage=None,
            version=None,
            content_hash="",
        ),
        "engine": _engine_payload(),
        "source_locator": None,
        "source_locator_status": "unavailable_invalid_query",
        "diagnostic": dict(diagnostic),
    }


def _relevant_timeline_breaks(
    timeline_breaks: tuple[TimelineBreak, ...],
    *,
    address: LegalAddress | None,
    requested: str,
    as_of: str,
) -> tuple[TimelineBreak, ...]:
    """Statute-scoped breaks always apply; address-scoped only on target match."""
    if not timeline_breaks:
        return ()
    section_label, chapter_label = _query_section_labels(address, requested)
    relevant = []
    for item in timeline_breaks:
        if item.scope == "statute":
            relevant.append(item)
        elif item.scope == "window":
            # A window break is a localized claim about a single bounded
            # interval, not a permanent defect: outside the window the timeline
            # IS materialized correctly, so surfacing a non-governing window
            # marker would be a false positive. Unlike statute/address breaks
            # (whose warning marker stays visible for non-governing as_of), a
            # window break drops out entirely outside its window — keeping
            # outside-window responses byte-identical to the no-break baseline.
            if break_matches_section(
                item, section_label=section_label, chapter_label=chapter_label
            ) and break_governs_as_of(item, as_of):
                relevant.append(item)
        elif break_matches_section(
            item, section_label=section_label, chapter_label=chapter_label
        ):
            relevant.append(item)
    return sorted_breaks(relevant)


def _query_section_labels(
    address: LegalAddress | None,
    requested: str,
) -> tuple[str, str]:
    """Extract (section_label, chapter_label) from the resolved or requested address."""
    target = address if address is not None else _parse_addr(requested)
    if target is None:
        return "", ""
    section_label = ""
    chapter_label = ""
    for kind, label in target.path:
        if kind == "section":
            section_label = label
        elif kind == "chapter":
            chapter_label = label
    return section_label, chapter_label


def _timeline_integrity_payloads(
    relevant_breaks: tuple[TimelineBreak, ...],
    as_of: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    """Build (timeline_broken_at marker, timeline_integrity block, blocking)."""
    if not relevant_breaks:
        return None, None, False
    governing = tuple(
        item for item in relevant_breaks if break_governs_as_of(item, as_of)
    )
    blocking = bool(governing)
    anchor = governing[0] if governing else relevant_breaks[0]
    marker = {
        "amendment_id": anchor.amendment_id,
        "diagnostic_code": anchor.diagnostic_code,
    }
    block: dict[str, Any] = {
        "timeline_status": "timeline_broken",
        "blocking": blocking,
        "broken_at": anchor.to_wire(),
        "breaks": [item.to_wire() for item in relevant_breaks],
    }
    return marker, block, blocking


def _relevant_temporal_schedule_deltas(
    deltas: tuple[TemporalScheduleDelta, ...],
    *,
    address: LegalAddress,
    as_of: str,
) -> tuple[TemporalScheduleDelta, ...]:
    relevant = []
    for delta in deltas:
        interval = delta.interval
        if interval.target_address != address:
            continue
        if interval.effective <= as_of and (not interval.expires or as_of < interval.expires):
            relevant.append(delta)
    return tuple(relevant)


def _temporal_schedule_payload(
    deltas: tuple[TemporalScheduleDelta, ...],
) -> dict[str, Any] | None:
    if not deltas:
        return None
    return {
        "materialization_status": "materialized",
        "scheduler": "temporal_write_interval_stage_1",
        "hash_role": "excluded_from_derived_state_hash",
        "deltas": [delta.to_wire() for delta in deltas],
    }


def _fixed_term_overlay(
    *,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    statute_id: str,
    selection: VersionSelectionResult,
    as_of: str,
    query_type: str,
) -> FixedTermSeamOverlay | None:
    """Compute the statute-level fixed-term outcome for this query, or None.

    Priority rule (Pro §7): ordinary timeline selection runs FIRST; the
    statute-validity overlay applies ONLY when a LIVE version would otherwise be
    selected. Repeal/tombstone/absent therefore beats expiry — a non-live
    selection is left untouched.
    """
    if not _fixed_term_bounds_enabled():
        return None
    version = selection.version
    if version is None or version.content is None:
        # absent / tombstone / repealed — ordinary selection wins.
        return None

    from lawvm.core.statute_validity import governing_bound, late_extension_gap
    from lawvm.finland.fixed_term_expiry import (
        FIXED_TERM_EXPIRY_AMBIGUOUS,
        extract_fixed_term_bounds,
        governing_unparseable,
        has_ambiguity,
    )

    extraction = extract_fixed_term_bounds(statute_id=statute_id, timelines=timelines)
    if not extraction.has_candidate:
        return None

    if has_ambiguity(extraction):
        return FixedTermSeamOverlay(
            kind="blocked_ambiguous",
            diagnostic_code=FIXED_TERM_EXPIRY_AMBIGUOUS,
        )

    unparseable = governing_unparseable(
        extraction, as_of=as_of, query_type=query_type
    )
    if unparseable is not None:
        return FixedTermSeamOverlay(
            kind="blocked_unparseable",
            diagnostic_code=unparseable.code,
        )

    bound = governing_bound(extraction.bounds, as_of=as_of, query_type=query_type)
    if bound is None or not is_expired_at(bound, as_of):
        return None

    return FixedTermSeamOverlay(
        kind="expired",
        diagnostic_code="",
        valid_until=bound.valid_until,
        expires_on=bound.expires_on,
        bound=bound,
        late_extension_gap=late_extension_gap(extraction.bounds, bound),
    )


def build_statute_dump_response(
    *,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    statute_id: str,
    jurisdiction: str,
    as_of: str,
    title: str = "",
    query_type: str = "governing",
    territory: str | None = None,
    address_filter: str | None = None,
    flags: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a full-statute text-state read with per-section content hashes.

    One JSON document over the governing version of every addressable provision
    at ``as_of``. Per-section ``content_hash`` follows the provision-state seam
    convention (sha256 of the text-only flattening; empty for absent/tombstone).
    Source attribution is read off ``ProvisionVersion.source`` (the amending act),
    never re-derived from johtolause text. Engine identity is excluded from any
    hash, matching the seam discipline.
    """

    selected: list[dict[str, Any]] = []
    filter_addr = _parse_addr(address_filter) if address_filter else None
    filter_suffix = filter_addr.path if filter_addr is not None else None
    for address in sorted(timelines, key=str):
        if filter_suffix is not None and address.path[-len(filter_suffix):] != filter_suffix:
            continue
        timeline = timelines[address]
        selection = select_active_version_ex(
            timeline,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
        )
        version = selection.version
        if version is None:
            continue
        if version.content is None or content_is_repeal_placeholder(version.content):
            # Tombstoned at as_of: the provision is not part of the text-state read.
            continue
        selected.append(
            _dump_section_payload(
                address=address,
                version=version,
            )
        )

    return {
        "schema": DUMP_SCHEMA,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "title": title,
        "as_of": as_of,
        "query": {
            "query_type": query_type,
            "territory": territory,
            "address_filter": address_filter,
        },
        "flags": dict(flags or {}),
        "section_count": len(selected),
        "sections": selected,
        "engine": _engine_payload(),
    }


def _dump_section_payload(
    *,
    address: LegalAddress,
    version: ProvisionVersion,
) -> dict[str, Any]:
    content = version.content
    heading = _heading_text(content) if content is not None else None
    label = content.label if content is not None and content.label else address.leaf_label()
    return {
        "address": _address_wire(address),
        "label": label,
        "heading": heading,
        "text": irnode_to_text(content) if content is not None else "",
        "content_hash": _content_hash(version),
        "version": _version_payload(version),
        "source": _source_payload(version),
    }


def _heading_text(node: IRNode) -> str | None:
    for child in node.children:
        if child.kind is IRNodeKind.HEADING:
            text = irnode_to_text(child).strip()
            return text or None
    return None


def resolve_address(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    provision: str,
) -> AddressResolution:
    """Resolve an address exactly or by unique suffix, never by arbitrary order."""

    target = _parse_addr(provision)
    if target is None:
        return AddressResolution(resolution_status="invalid_address", requested=provision)
    timeline = timelines.get(target)
    if timeline is not None:
        return AddressResolution(
            resolution_status="resolved",
            requested=provision,
            address=target,
            timeline=timeline,
            mode="exact",
        )
    suffix = target.path
    candidates = tuple(address for address in timelines if address.path[-len(suffix) :] == suffix)
    if len(candidates) == 1:
        address = candidates[0]
        return AddressResolution(
            resolution_status="resolved",
            requested=provision,
            address=address,
            timeline=timelines[address],
            mode="unique_suffix",
        )
    if candidates:
        return AddressResolution(
            resolution_status="ambiguous_address",
            requested=provision,
            candidates=tuple(sorted(candidates, key=str)),
        )
    return AddressResolution(
        resolution_status="address_not_found",
        requested=provision,
        suggestions=_nearby_address_suggestions(timelines, target),
    )


def resolve_address_for_query(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    provision: str,
    *,
    as_of: str,
    query_type: Literal["governing", "in_force"],
    territory: Any,
) -> AddressResolution:
    """Resolve a public provision query with PIT-aware exact/suffix evidence.

    Exact matches normally win. A bare section timeline can, however, be a
    historical tombstone while the same provision's live timeline exists at its
    fully qualified chapter/part address. In that specific read-only seam case,
    prefer the unique live suffix candidate and report the resolution mode.
    """

    resolution = resolve_address(timelines, provision)
    if resolution.resolution_status != "resolved" or resolution.address is None or resolution.timeline is None:
        return resolution
    target = _parse_addr(provision)
    if target is None or len(target.path) != 1 or target.path[0][0] != "section":
        return resolution
    if not _selected_timeline_is_tombstone_or_absent(
        resolution.timeline,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    ):
        return resolution
    suffix = target.path
    live_candidates = [
        address
        for address, timeline in timelines.items()
        if address != resolution.address
        and address.path[-len(suffix) :] == suffix
        and _selected_timeline_has_live_content(
            timeline,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
        )
    ]
    if len(live_candidates) != 1:
        return resolution
    address = live_candidates[0]
    return AddressResolution(
        resolution_status="resolved",
        requested=provision,
        address=address,
        timeline=timelines[address],
        mode="unique_live_suffix_over_exact_tombstone",
    )


def _selected_timeline_is_tombstone_or_absent(
    timeline: ProvisionTimeline,
    *,
    as_of: str,
    query_type: Literal["governing", "in_force"],
    territory: Any,
) -> bool:
    selection = select_active_version_ex(
        timeline,
        as_of,
        query_type=query_type,
        territory=str(territory) if territory is not None else None,
    )
    version = selection.version
    return version is None or version.content is None or content_is_repeal_placeholder(version.content)


def _selected_timeline_has_live_content(
    timeline: ProvisionTimeline,
    *,
    as_of: str,
    query_type: Literal["governing", "in_force"],
    territory: Any,
) -> bool:
    return not _selected_timeline_is_tombstone_or_absent(
        timeline,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )


def _selected_response(
    *,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    selection: VersionSelectionResult,
    resolution: AddressResolution,
    migration_events: tuple[MigrationEvent, ...],
    statute_id: str,
    jurisdiction: str,
    query: dict[str, Any],
    include_ir: bool,
    title: str,
    base: IRStatute | None,
    overlay: FixedTermSeamOverlay | None = None,
    timeline_broken_at: dict[str, Any] | None = None,
    timeline_integrity: dict[str, Any] | None = None,
    timeline_blocking: bool = False,
    temporal_schedule_deltas: tuple[TemporalScheduleDelta, ...] = (),
    findings: tuple[Finding, ...] = (),
    source_xml_provider: Callable[[str], bytes | None] | None = None,
) -> dict[str, Any]:
    address = _require_address(resolution)
    version = selection.version
    # Fixed-term overlay only fires when a live version would otherwise be
    # selected, so past the bound the seam must not expose live content.
    expired = overlay is not None and overlay.kind == "expired"
    blocked = overlay is not None and overlay.kind in ("blocked_unparseable", "blocked_ambiguous")
    payload_version = None if (expired or blocked or timeline_blocking) else version
    content_derivation: dict[str, Any] | None = None
    if payload_version is not None:
        payload_version, content_derivation = _materialized_payload_version(
            timelines=timelines,
            base=base,
            address=address,
            version=payload_version,
            as_of=str(query["as_of"]),
            query_type=_query_type_literal(str(query["query_type"])),
            territory=query.get("territory"),
        )
    content_hash = _content_hash(payload_version)
    if timeline_blocking:
        # A governing timeline break makes ANY selection outcome unprovable —
        # the compiled timeline this selection ran over is itself unproven at
        # as_of. Never read as a legal fact.
        status = "timeline_unverified"
    elif expired:
        status = "expired"
    elif blocked:
        status = "expiry_unverified"
    else:
        status = "selected" if version is not None else selection.selection_status
    expiry_block = _expiry_block(overlay, statute_id, address)
    lineage = _lineage_payload(
        jurisdiction=jurisdiction,
        statute_id=statute_id,
        address=address,
        migration_events=migration_events,
        as_of=query["as_of"],
    )
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "spec_version": SPEC_VERSION,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "title": title,
        "provision_status": status,
        "query": query,
        "resolved_address": _address_wire(address),
        "lineage": lineage,
        "address_match": {
            "requested": resolution.requested,
            "mode": resolution.mode
            or ("exact" if str(address) == resolution.requested else "unique_suffix"),
        },
        "selection": _selection_payload(selection),
        "version": _version_payload(payload_version, content_state_override=("expired" if expired else None)),
        "hashes": _hash_payload(
            payload_status=status,
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            query=query,
            address=address,
            lineage=lineage,
            version=payload_version,
            content_hash=content_hash,
            expiry=expiry_block,
            content_state_override=("expired" if expired else None),
            timeline_broken_at=timeline_broken_at,
            timeline_integrity=timeline_integrity,
        ),
        "text": _text_payload(payload_version),
        "source": _source_payload(payload_version),
        "source_locator": _source_locator_payload(
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            address=address,
            version=payload_version,
            source_xml_provider=source_xml_provider,
        ),
        "engine": _engine_payload(),
    }
    if expiry_block is not None:
        if expired:
            assert overlay is not None
            payload["expires"] = overlay.expires_on
            payload["valid_until"] = overlay.valid_until
        payload["expiry"] = expiry_block
    if timeline_integrity is not None:
        payload["timeline_broken_at"] = timeline_broken_at
        payload["timeline_integrity"] = timeline_integrity
    temporal_schedule = _temporal_schedule_payload(
        _relevant_temporal_schedule_deltas(
            temporal_schedule_deltas,
            address=address,
            as_of=query["as_of"],
        )
    )
    if temporal_schedule is not None:
        payload["temporal_schedule"] = temporal_schedule
    diagnostics = _selected_diagnostics(
        findings,
        version=payload_version,
        address=address,
    )
    if diagnostics:
        payload["diagnostics"] = diagnostics
    if content_derivation is not None:
        payload["content_derivation"] = content_derivation
    payload["source_locator_status"] = (
        "canonical_document_locator" if payload["source_locator"] is not None else "unavailable_no_source"
    )
    if include_ir:
        payload["ir"] = _ir_payload(payload_version)
    if base is not None:
        payload["base"] = {
            "statute_id": base.statute_id,
            "title": base.title,
        }
    return payload


def _query_type_literal(value: str) -> Literal["governing", "in_force"]:
    if value == "in_force":
        return "in_force"
    return "governing"


def _materialized_payload_version(
    *,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    base: IRStatute | None,
    address: LegalAddress,
    version: ProvisionVersion,
    as_of: str,
    query_type: Literal["governing", "in_force"],
    territory: Any,
) -> tuple[ProvisionVersion, dict[str, Any] | None]:
    """Use PIT-materialized content for reads of composite ancestor provisions."""

    if base is None or version.content is None:
        return version, None
    if not any(candidate.has_prefix(address) and candidate != address for candidate in timelines):
        return version, None
    materialized = materialize_pit(
        dict(timelines),
        as_of=as_of,
        base=base,
        query_type=query_type,
        territory=str(territory) if territory is not None else None,
    )
    materialized_content = resolve_tree(materialized.body, address.path)
    if materialized_content is None:
        return version, None
    if materialized_content.to_jsonable_dict() == version.content.to_jsonable_dict():
        return version, None

    selected_hash = _content_hash(version)
    materialized_hash = irnode_content_hash(materialized_content)
    materialized_version = replace(
        version,
        content=materialized_content,
        content_hash=materialized_hash,
    )
    return materialized_version, {
        "mode": "pit_materialized_descendant_overlays",
        "hash_role": "included_in_content_hash_and_derived_state_hash",
        "selected_version_content_hash": selected_hash,
        "materialized_content_hash": materialized_hash,
        "selected_version_effective": version.effective,
        "selected_version_enacted": version.enacted,
        "address": _address_wire(address),
    }


_SELECTED_DIAGNOSTIC_CODES = frozenset(
    {
        "APPLY.UNCOVERED_BODY_RECOVERY",
        "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
        "ELAB.OMISSION_EXPANSION",
        "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
    }
)


def _selected_diagnostics(
    findings: tuple[Finding, ...],
    *,
    version: ProvisionVersion | None,
    address: LegalAddress,
) -> list[dict[str, Any]]:
    """Expose non-clean recovery evidence on otherwise servable answers."""

    if version is None or version.source is None or not findings:
        return []
    source_statute = version.source.statute_id
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.kind not in _SELECTED_DIAGNOSTIC_CODES:
            continue
        if finding.source_statute != source_statute:
            continue
        if not _finding_relevant_to_selected_address(finding, address):
            continue
        row = _selected_diagnostic_payload(finding)
        key = _selected_diagnostic_dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _selected_diagnostic_dedupe_key(row: Mapping[str, Any]) -> str:
    if row.get("code") == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED":
        detail = row.get("detail")
        if isinstance(detail, Mapping):
            semantic_key = {
                "code": row.get("code"),
                "source_statute": row.get("source_statute"),
                "uncovered_count": detail.get("uncovered_count"),
                "total_units": detail.get("total_units"),
                "uncov_ratio": detail.get("uncov_ratio"),
                "confidence": detail.get("confidence"),
                "signals": detail.get("signals"),
            }
            return _sha256_canonical(semantic_key)
    return _sha256_canonical(row)


def _finding_relevant_to_selected_address(finding: Finding, address: LegalAddress) -> bool:
    """Return true when a recovery finding is source-wide or target-matched."""

    if finding.kind == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED":
        # Coverage findings are source-wide: they explain that this amendment's
        # chapter-level recovery proceeded under degraded confidence.
        return True
    detail = finding.detail
    target_norm = detail.get("target_norm")
    if target_norm is None:
        return True
    section = _address_label(address, "section")
    if section != str(target_norm):
        return False
    target_chapter = detail.get("target_chapter")
    if target_chapter is not None and _address_label(address, "chapter") != str(target_chapter):
        return False
    target_part = detail.get("target_part")
    return not (target_part is not None and _address_label(address, "part") != str(target_part))


def _address_label(address: LegalAddress, kind: str) -> str | None:
    for part_kind, label in reversed(address.path):
        if part_kind == kind:
            return label
    return None


def _selected_diagnostic_payload(finding: Finding) -> dict[str, Any]:
    return {
        "code": finding.kind,
        "role": finding.role,
        "stage": finding.stage,
        "source_statute": finding.source_statute,
        "finding_blocking": finding.blocking,
        "seam_blocking": False,
        "detail": dict(finding.detail),
        "hash_role": "excluded_from_derived_state_hash",
    }


def _parse_addr(addr_str: str) -> LegalAddress | None:
    pairs: list[tuple[str, str]] = []
    for part in addr_str.split("/"):
        if ":" not in part:
            return None
        kind, label = part.split(":", 1)
        kind = kind.strip()
        label = label.strip()
        if not kind or not label:
            return None
        pairs.append((kind, label))
    if not pairs:
        return None
    return LegalAddress(path=tuple(pairs))


def _fi_section_suggestion(number: str, letter: str | None) -> str:
    suffix = str(letter or "").lower()
    return f"section:{number}{suffix}"


def _address_resolution_diagnostic(resolution: AddressResolution) -> dict[str, Any] | None:
    if resolution.resolution_status != "address_not_found" or not resolution.suggestions:
        return None
    return {
        "code": "LAWVM_PROVISION_ADDRESS_NOT_FOUND",
        "message": "requested provision was not found in the materialized replay timeline",
        "nearby_address_candidates": [
            _address_wire(candidate) for candidate in resolution.suggestions
        ],
        "suggestion_status": "non_authoritative_query_help_only",
    }


def _nearby_address_suggestions(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    target: LegalAddress,
    *,
    limit: int = 5,
) -> tuple[LegalAddress, ...]:
    target_section = _last_path_component(target, "section")
    if not target_section:
        return ()
    normalized_target = _fi_address_label_key(target_section)
    if not normalized_target:
        return ()
    same_normalized = tuple(
        address
        for address in sorted(timelines, key=str)
        if _fi_address_label_key(_last_path_component(address, "section")) == normalized_target
    )
    if same_normalized:
        return same_normalized[:limit]
    target_number = _leading_int(target_section)
    if target_number is None:
        return ()
    nearby = []
    for address in sorted(timelines, key=str):
        section_label = _last_path_component(address, "section")
        section_number = _leading_int(section_label)
        if section_number is None:
            continue
        distance = abs(section_number - target_number)
        if distance <= 3:
            nearby.append((distance, str(address), address))
    return tuple(address for _distance, _text, address in sorted(nearby)[:limit])


def _last_path_component(address: LegalAddress, kind: str) -> str:
    for component_kind, label in reversed(address.path):
        if component_kind == kind:
            return label
    return ""


def _fi_address_label_key(label: str) -> str:
    return re.sub(r"[\s§]+", "", str(label or "")).lower()


def _leading_int(value: str) -> int | None:
    match = re.match(r"\s*(\d+)", str(value or ""))
    if match is None:
        return None
    return int(match.group(1))


def _query_payload(
    *,
    statute_id: str,
    provision: str,
    as_of: str,
    query_type: str,
    territory: str | None,
) -> dict[str, Any]:
    return {
        "statute_id": statute_id,
        "provision": provision,
        "as_of": as_of,
        "query_type": query_type,
        "territory": territory,
    }


def _address_wire(address: LegalAddress) -> dict[str, Any]:
    return {
        "path": [{"kind": kind, "label": label} for kind, label in address.path],
        "special": str(address.special) if address.special else None,
        "text": str(address),
    }


def _selection_payload(selection: VersionSelectionResult) -> dict[str, Any]:
    certificate = selection.certificate
    cert_payload = None
    if certificate is not None:
        cert_payload = {
            "address": _address_wire(certificate.address),
            "as_of": certificate.as_of,
            "query_type": certificate.query_type,
            "territory": certificate.territory,
            "selected_rail": certificate.selected_rail,
            "candidate_count": certificate.candidate_count,
            "selected_effective": certificate.selected_effective,
            "selected_enacted": certificate.selected_enacted,
            "required_dimensions": list(certificate.required_dimensions),
        }
    return {
        "selection_status": selection.selection_status,
        "required_dimensions": list(selection.required_dimensions),
        "certificate": cert_payload,
    }


def _lineage_payload(
    *,
    jurisdiction: str,
    statute_id: str,
    address: LegalAddress | None,
    migration_events: tuple[MigrationEvent, ...],
    as_of: str,
) -> dict[str, Any]:
    if address is None:
        payload = {
            "lineage_status": "unresolved_address",
            "address_chain": [],
            "migration_event_count_considered": len(migration_events),
        }
        return _lineage_payload_with_fingerprint(
            payload,
            jurisdiction=jurisdiction,
            statute_id=statute_id,
        )
    chain = lineage_address_chain(
        address,
        migration_events,
        as_of_date=as_of,
        address_prefix_matches=lambda current, prefix: current.has_prefix(prefix),
    )
    status = "migration_chain" if len(chain) > 1 else "self_only"
    payload = {
        "lineage_status": status,
        "address_chain": [_address_wire(chain_address) for chain_address in chain],
        "migration_event_count_considered": len(migration_events),
    }
    return _lineage_payload_with_fingerprint(
        payload,
        jurisdiction=jurisdiction,
        statute_id=statute_id,
    )


def _lineage_payload_with_fingerprint(
    lineage: Mapping[str, Any],
    *,
    jurisdiction: str,
    statute_id: str,
) -> dict[str, Any]:
    payload = dict(lineage)
    fingerprint_input = {
        "schema": "lawvm.provision_state.lineage.v1",
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "lineage": _lineage_hash_input(lineage),
    }
    payload["fingerprint"] = _sha256_canonical(fingerprint_input)
    payload["fingerprint_algorithm"] = "sha256"
    payload["fingerprint_semantics"] = (
        "sha256(canonical lawvm.provision_state.lineage.v1: jurisdiction, "
        "statute_id, status, address_chain, migration_event_count_considered); "
        "excluded from derived_state_hash"
    )
    return payload


def _lineage_hash_input(lineage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if lineage is None:
        return None
    return {
        "lineage_status": lineage.get("lineage_status"),
        "address_chain": lineage.get("address_chain", []),
        "migration_event_count_considered": lineage.get("migration_event_count_considered", 0),
    }


def _expiry_block(
    overlay: FixedTermSeamOverlay | None,
    statute_id: str,
    address: LegalAddress,
) -> dict[str, Any] | None:
    """Build the seam ``expiry`` provenance/diagnostic block, or None."""
    if overlay is None:
        return None
    if overlay.kind == "expired":
        bound = overlay.bound
        block: dict[str, Any] = {
            "kind": "fixed_term_statute",
            "scope": "whole_statute",
            "source_statute": statute_id,
            "valid_until": overlay.valid_until,
            "expires_on": overlay.expires_on,
        }
        if bound is not None:
            block["source_provision"] = str(bound.source_provision)
            block["source_version_effective"] = bound.effective
            block["source"] = bound.source_version_id
            block["source_text"] = bound.source_text
            block["source_hash"] = bound.source_hash
            block["rule_id"] = bound.rule_id
            block["governing_bound_id"] = bound.bound_id
            if bound.bound_kind != "stated_expiry":
                # Non-default bound kinds (toistaiseksi outer cap, computed
                # duration): status is still expired past the bound (no weaker
                # "possibly expired"), but the consumer can see what kind of
                # bound it is and whether earlier termination was possible.
                block["bound_kind"] = bound.bound_kind
                block["source_phrase_kind"] = bound.source_phrase_kind
                block["earlier_termination_possible"] = (
                    bound.earlier_termination_possible
                )
            if bound.epistemic_status != "grammar_fact":
                # Computed/inferred ends must never read as grammar facts.
                block["epistemic_status"] = bound.epistemic_status
            if bound.arithmetic_authority:
                # Duration arithmetic provenance: the named authority, its
                # recorded scope caveat (150/1930 §1 governs procedural
                # deadlines; applying it to whole-law validity is a recorded
                # inference), and the inputs the end was computed from.
                block["arithmetic_authority"] = bound.arithmetic_authority
                block["authority_scope_caveat"] = bound.authority_scope_caveat
                block["duration_spec"] = bound.duration_spec
            if bound.commencement_date:
                block["commencement_date"] = bound.commencement_date
                block["commencement_source_kind"] = bound.commencement_source_kind
        if overlay.late_extension_gap:
            block["diagnostic"] = "TEMPORAL.FIXED_TERM_LATE_EXTENSION_GAP"
        return block
    # Blocked (unparseable / ambiguous): expose the blocking diagnostic so the
    # consumer never reads the answer as confirmed-in-force.
    return {
        "kind": "fixed_term_statute_unverified",
        "scope": "whole_statute",
        "source_statute": statute_id,
        "diagnostic": overlay.diagnostic_code,
        "blocking": True,
    }


def _version_payload(
    version: ProvisionVersion | None,
    *,
    content_state_override: str | None = None,
) -> dict[str, Any] | None:
    if version is None:
        return None
    content_state = content_state_override or (
        "tombstone"
        if version.content is None or content_is_repeal_placeholder(version.content)
        else "live"
    )
    return {
        "effective": version.effective,
        "enacted": version.enacted,
        "expires": version.expires,
        "variant_kind": version.variant_kind,
        "content_state": content_state,
        "applicability": [
            {
                "dimension": predicate.dimension,
                "includes": sorted(predicate.includes),
            }
            for predicate in version.applicability
        ],
    }


def _content_hash(version: ProvisionVersion | None) -> str:
    if version is None:
        return ""
    if content_is_repeal_placeholder(version.content):
        return ""
    if version.content_hash:
        return version.content_hash
    return irnode_content_hash(version.content)


def _structured_content_hash(version: ProvisionVersion | None) -> str:
    if version is None or version.content is None or content_is_repeal_placeholder(version.content):
        return ""
    return _sha256_canonical(version.content.to_jsonable_dict())


def _hash_payload(
    *,
    payload_status: str,
    statute_id: str,
    jurisdiction: str,
    query: Mapping[str, Any],
    address: LegalAddress | None,
    lineage: Mapping[str, Any] | None,
    version: ProvisionVersion | None,
    content_hash: str,
    expiry: Mapping[str, Any] | None = None,
    content_state_override: str | None = None,
    timeline_broken_at: Mapping[str, Any] | None = None,
    timeline_integrity: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    structured_content_hash = _structured_content_hash(version)
    derived_input = {
        "schema": SCHEMA,
        "provision_status": payload_status,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "query": query,
        "resolved_address": _address_wire(address) if address is not None else None,
        "lineage": _lineage_hash_input(lineage),
        "version": _version_payload(version, content_state_override=content_state_override),
        "content_hash": content_hash,
    }
    # Only mutate the hashed state when the fixed-term overlay is active, so the
    # flag-OFF default path remains byte-identical.
    if expiry is not None:
        derived_input["expiry"] = expiry
    # Same conditional-member discipline for timeline-integrity surfacing:
    # responses without break evidence hash byte-identically.
    if timeline_integrity is not None:
        derived_input["timeline_broken_at"] = timeline_broken_at
        derived_input["timeline_integrity"] = timeline_integrity
    return {
        "content_hash": content_hash,
        "content_hash_semantics": "sha256(irnode_to_text(content)); text-only; empty for absent/tombstone",
        "structured_content_hash": structured_content_hash,
        "structured_content_hash_semantics": (
            "sha256(canonical IRNode.to_jsonable_dict(content)); structure+attrs+labels+text; "
            "empty for absent/tombstone; excluded from derived_state_hash"
        ),
        "derived_state_hash": _sha256_canonical(derived_input),
        "derived_state_hash_semantics": (
            "sha256(canonical lawvm.provision_state.v1 state: status, query, "
            "resolved address, lineage control fields, version temporal metadata, "
            "applicability, content_hash)"
        ),
    }


def _text_payload(version: ProvisionVersion | None) -> dict[str, Any]:
    if version is None or version.content is None or content_is_repeal_placeholder(version.content):
        return {
            "rendered": "",
            "available": False,
        }
    return {
        "rendered": irnode_to_text(version.content),
        "available": True,
    }


def _ir_payload(version: ProvisionVersion | None) -> dict[str, Any] | None:
    if version is None or version.content is None or content_is_repeal_placeholder(version.content):
        return None
    return version.content.to_jsonable_dict()


def _source_payload(version: ProvisionVersion | None) -> dict[str, str] | None:
    if version is None or version.source is None:
        return None
    source = version.source
    return {
        "statute_id": source.statute_id,
        "title": source.title,
        "enacted": source.enacted,
        "effective": source.effective,
        "expires": source.expires,
        "commencement_source": source.commencement_source,
        "branch_id": source.branch_id,
        "scenario_id": source.scenario_id,
    }


def _source_locator_payload(
    *,
    statute_id: str,
    jurisdiction: str,
    address: LegalAddress,
    version: ProvisionVersion | None,
    source_xml_provider: Callable[[str], bytes | None] | None = None,
) -> dict[str, Any] | None:
    source_sid = statute_id
    artifact_kind = "base_statute_xml"
    locator_status = "base_statute_locator"
    source_quote = _source_quote_payload(version)
    if version is not None and version.source is not None and version.source.statute_id:
        source_sid = version.source.statute_id
        artifact_kind = "operation_source_statute_xml"
        locator_status = "operation_source_locator"
    if jurisdiction != "fi":
        return None
    target_xpath = _finlex_target_xpath_candidate(address)
    source_xpath = target_xpath if artifact_kind == "base_statute_xml" else ""
    xpath_status = (
        "finlex_structural_xpath_candidate"
        if source_xpath
        else "unavailable_operation_source_target_not_xml_anchored"
        if artifact_kind == "operation_source_statute_xml"
        else "unavailable_initial_surface"
    )
    detail: dict[str, Any] = {
        "locator_status": locator_status,
        "document_locator_status": "canonical_finlex_document_uri",
        "selected_target_address": str(address),
        "precision": "document_plus_resolved_target_legal_address",
        "target_legal_address_kind": "lawvm_resolved_target",
        "target_address_authority": "resolved_replay_timeline_address",
        "target_xpath_candidate": target_xpath or "unavailable",
        "target_xpath_candidate_status": (
            "finlex_structural_xpath_candidate" if target_xpath else "unavailable_unsupported_address_kind"
        ),
        "xpath": source_xpath or "unavailable",
        "xpath_status": xpath_status,
        "byte_span": "unavailable",
        "byte_span_status": "unavailable_initial_surface",
        "hash_role": "excluded_from_derived_state_hash",
    }
    span = None
    source_xml_bytes = None
    if source_xml_provider is not None and (
        (artifact_kind == "base_statute_xml" and source_xpath)
        or (artifact_kind == "operation_source_statute_xml" and source_quote is not None)
    ):
        source_xml_bytes = source_xml_provider(source_sid)
    artifact_digest = ""
    artifact_digest_algorithm = ""
    if source_xml_bytes is not None:
        artifact_digest = hashlib.sha256(source_xml_bytes).hexdigest()
        artifact_digest_algorithm = "sha256"
        detail["artifact_digest"] = artifact_digest
        detail["artifact_digest_algorithm"] = artifact_digest_algorithm
        detail["artifact_digest_status"] = "source_xml_bytes_sha256"
    else:
        detail["artifact_digest_status"] = "unavailable_source_xml_not_loaded"
    if artifact_kind == "base_statute_xml" and source_xpath and source_xml_provider is not None:
        span = _finlex_source_xml_element_span(
            source_xml_bytes,
            xpath=source_xpath,
            fallback_eid=_finlex_eid_candidate(address),
        )
        detail.update(span["detail"])
    elif (
        artifact_kind == "operation_source_statute_xml"
        and source_quote is not None
        and version is not None
        and version.source is not None
        and source_xml_provider is not None
    ):
        span = _operation_source_xml_quote_span(
            source_xml_bytes,
            raw_source_text=version.source.raw_text,
        )
        detail.update(span["detail"])
    if source_quote is not None:
        if span is not None and span.get("source_witness_detail"):
            source_quote.update(span["source_witness_detail"])
        source_quote = _operation_source_witness_payload(
            source_quote,
            artifact_id=source_sid,
            locator=statute_url(source_sid),
            artifact_digest=artifact_digest,
            artifact_digest_algorithm=artifact_digest_algorithm,
        )
        detail["source_witness"] = source_quote
        detail["source_witness_status"] = "operation_source_raw_text_available"
    else:
        detail["source_witness_status"] = "unavailable_no_operation_source_raw_text"
    locator = SourceLocator(
        jurisdiction=jurisdiction,
        artifact_kind=artifact_kind,
        source_id=f"finlex:{artifact_kind}:{source_sid}",
        document_uri=statute_url(source_sid),
        structural_path=f"lawvm-target:{address}",
        xpath=source_xpath,
        char_span=span["char_span"] if span is not None else None,
        byte_span=span["byte_span"] if span is not None else None,
        artifact_digest=artifact_digest,
        artifact_digest_algorithm=artifact_digest_algorithm,
        quote_hash=source_quote["quote_hash"] if source_quote is not None else "",
        statute_id=source_sid,
        normalization_policy="finlex_statute_document_locator.v1",
        detail=detail,
    )
    return locator.to_dict()


_FINLEX_ADDRESS_KIND_TO_XML_TAG = {
    "part": "part",
    "chapter": "chapter",
    "section": "section",
    "subsection": "subsection",
    "paragraph": "paragraph",
    "subparagraph": "subparagraph",
    "item": "item",
}
_FINLEX_XPATH_TRANSLATE_FROM = "ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ §)."
_FINLEX_XPATH_TRANSLATE_TO = "abcdefghijklmnopqrstuvwxyzåäö"


def _finlex_target_xpath_candidate(address: LegalAddress) -> str:
    """Return a deterministic Finlex AKN XPath candidate for a LawVM address.

    This is intentionally a candidate locator, not a byte-span proof. Finland
    replay timelines carry stable ``LegalAddress`` objects, but provision-state
    does not yet retain the original lxml element pointer. The XPath mirrors the
    same <num> normalization used by Finnish XML ingestion and falls back to
    ordinal matching for generated positional subsection/paragraph labels.
    """
    parts: list[str] = ["//*[local-name()='body']"]
    for kind, label in address.path:
        tag = _FINLEX_ADDRESS_KIND_TO_XML_TAG.get(kind)
        if tag is None:
            return ""
        parts.append(f"/*[local-name()={_xpath_literal(tag)}][{_finlex_num_predicate(kind, tag, label)}]")
    return "".join(parts)


def _finlex_source_xml_element_span(
    xml_bytes: bytes | None,
    *,
    xpath: str,
    fallback_eid: str = "",
) -> dict[str, Any]:
    if not xml_bytes:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "source_xml_span_status": "unavailable_source_xml_not_loaded",
            },
        }
    from lxml import etree

    try:
        text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "source_xml_span_status": "unavailable_source_xml_not_utf8",
            },
        }
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "source_xml_span_status": "unavailable_source_xml_parse_failed",
            },
        }
    matches = root.xpath(xpath)
    match_basis = "xpath_candidate"
    xpath_match_count = len(matches) if isinstance(matches, list) else 0
    if (not isinstance(matches, list) or len(matches) != 1) and fallback_eid:
        matches = root.xpath(f"//*[@eId={_xpath_literal(fallback_eid)}]")
        match_basis = "fallback_eid"
    if not isinstance(matches, list) or len(matches) != 1:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "source_xml_span_status": "unavailable_xpath_match_count_not_one",
                "source_xml_xpath_match_count": xpath_match_count,
                "source_xml_fallback_eid": fallback_eid,
                "source_xml_fallback_match_count": len(matches) if isinstance(matches, list) else 0,
            },
        }
    element = matches[0]
    if not isinstance(element, etree._Element):
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "source_xml_span_status": "unavailable_xpath_match_not_element",
            },
        }
    eid = str(element.get("eId") or "")
    if not eid:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "source_xml_span_status": "unavailable_xpath_match_missing_eid",
            },
        }
    tag = etree.QName(element).localname
    char_span = _raw_xml_eid_element_char_span(text, local_tag=tag, eid=eid)
    if char_span is None:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "source_xml_span_status": "unavailable_raw_xml_eid_scan_failed",
                "source_xml_eid": eid,
                "source_xml_local_tag": tag,
            },
        }
    byte_span = (
        len(text[: char_span[0]].encode("utf-8")),
        len(text[: char_span[1]].encode("utf-8")),
    )
    return {
        "char_span": char_span,
        "byte_span": byte_span,
        "detail": {
            "char_span": list(char_span),
            "char_span_status": "finlex_raw_xml_eid_element_scan",
            "char_span_basis": _finlex_span_basis(match_basis),
            "byte_span": list(byte_span),
            "byte_span_status": "finlex_raw_xml_eid_element_scan_utf8",
            "byte_span_basis": "UTF-8 byte offsets derived from exact raw XML character span.",
            "source_xml_span_status": "available",
            "source_xml_span_match_basis": match_basis,
            "source_xml_xpath_match_count": xpath_match_count,
            "source_xml_eid": eid,
            "source_xml_local_tag": tag,
        },
    }


def _operation_source_xml_quote_span(
    xml_bytes: bytes | None,
    *,
    raw_source_text: str,
) -> dict[str, Any]:
    raw_text = str(raw_source_text or "").strip()
    if not raw_text:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "operation_source_xml_span_status": "unavailable_empty_operation_source_raw_text",
            },
            "source_witness_detail": {
                "artifact_span_status": "unavailable_empty_operation_source_raw_text",
            },
        }
    if not xml_bytes:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "operation_source_xml_span_status": "unavailable_source_xml_not_loaded",
            },
            "source_witness_detail": {
                "artifact_span_status": "unavailable_source_xml_not_loaded",
            },
        }
    try:
        text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "operation_source_xml_span_status": "unavailable_source_xml_not_utf8",
            },
            "source_witness_detail": {
                "artifact_span_status": "unavailable_source_xml_not_utf8",
            },
        }
    match_count = text.count(raw_text)
    first = text.find(raw_text)
    if match_count == 0:
        container_span = _operation_source_xml_text_container_span(
            xml_bytes,
            raw_xml_text=text,
            raw_source_text=raw_text,
        )
        if container_span is not None:
            return container_span
        sequence_span = _operation_source_xml_text_sequence_span(
            xml_bytes,
            raw_xml_text=text,
            raw_source_text=raw_text,
        )
        if sequence_span is not None:
            return sequence_span
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "operation_source_xml_span_status": "unavailable_operation_source_quote_not_found",
                "operation_source_xml_quote_match_count": 0,
            },
            "source_witness_detail": {
                "artifact_span_status": "unavailable_operation_source_quote_not_found",
                "artifact_span_match_count": 0,
            },
        }
    if match_count != 1:
        return {
            "char_span": None,
            "byte_span": None,
            "detail": {
                "operation_source_xml_span_status": "unavailable_operation_source_quote_not_unique",
                "operation_source_xml_quote_match_count": match_count,
            },
            "source_witness_detail": {
                "artifact_span_status": "unavailable_operation_source_quote_not_unique",
                "artifact_span_match_count": match_count,
            },
        }
    char_span = (first, first + len(raw_text))
    byte_span = (
        len(text[: char_span[0]].encode("utf-8")),
        len(text[: char_span[1]].encode("utf-8")),
    )
    detail = {
        "char_span": list(char_span),
        "char_span_status": "operation_source_raw_xml_quote_scan",
        "char_span_basis": (
            "Raw Finlex operation-source XML decoded as UTF-8; trimmed "
            "OperationSource.raw_text matched exactly once."
        ),
        "byte_span": list(byte_span),
        "byte_span_status": "operation_source_raw_xml_quote_scan_utf8",
        "byte_span_basis": "UTF-8 byte offsets derived from exact raw XML quote character span.",
        "operation_source_xml_span_status": "available",
        "operation_source_xml_quote_match_count": 1,
    }
    return {
        "char_span": char_span,
        "byte_span": byte_span,
        "detail": detail,
        "source_witness_detail": {
            "artifact_char_span": list(char_span),
            "artifact_byte_span": list(byte_span),
            "artifact_span_status": "operation_source_raw_xml_quote_scan",
            "artifact_span_basis": detail["char_span_basis"],
            "artifact_span_match_count": 1,
        },
    }


def _operation_source_xml_text_sequence_span(
    xml_bytes: bytes,
    *,
    raw_xml_text: str,
    raw_source_text: str,
) -> dict[str, Any] | None:
    """Anchor a condensed operation quote to a unique XML text-sequence container.

    Finland preambles sometimes split one operation source across sibling
    ``blockContainer`` elements and insert an ``*-originals`` qualification block
    between the executable action blocks. ``OperationSource.raw_text`` may carry
    the executable action sequence without that intervening qualification. This
    fallback therefore checks ordered token containment, not substring
    containment, and returns a containing-element span only when the match is
    unique.
    """
    from lxml import etree

    quote_tokens = _xml_text_sequence_tokens(raw_source_text)
    if len(quote_tokens) < 8:
        return None
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return _unavailable_operation_source_container_span(
            "unavailable_operation_source_xml_parse_failed",
            match_count=0,
        )
    candidates: list[Any] = []
    for element in root.iter():
        if not isinstance(element, etree._Element):
            continue
        candidate_tokens = _xml_text_sequence_tokens(" ".join(str(part) for part in element.itertext()))
        if _contains_ordered_token_sequence(candidate_tokens, quote_tokens):
            candidates.append(element)
    if not candidates:
        return None
    smallest = [
        element
        for element in candidates
        if not any(other is not element and _xml_element_is_ancestor(element, other) for other in candidates)
    ]
    if len(smallest) != 1:
        return _unavailable_operation_source_container_span(
            "unavailable_operation_source_text_sequence_container_not_unique",
            match_count=len(smallest),
            candidate_count=len(candidates),
        )
    element = smallest[0]
    tag = etree.QName(element).localname
    eid = str(element.get("eId") or "")
    if eid:
        char_span = _raw_xml_eid_element_char_span(raw_xml_text, local_tag=tag, eid=eid)
        span_basis_detail = "eid"
    else:
        char_span = _raw_xml_sourceline_element_char_span(
            raw_xml_text,
            local_tag=tag,
            sourceline=int(getattr(element, "sourceline", 0) or 0),
            attrs=_raw_xml_stable_attrs(element),
        )
        span_basis_detail = "source_line_and_stable_attrs"
    if char_span is None:
        return _unavailable_operation_source_container_span(
            "unavailable_operation_source_text_sequence_container_raw_xml_scan_failed",
            match_count=1,
            candidate_count=len(candidates),
            eid=eid,
            local_tag=tag,
            sourceline=int(getattr(element, "sourceline", 0) or 0),
        )
    byte_span = (
        len(raw_xml_text[: char_span[0]].encode("utf-8")),
        len(raw_xml_text[: char_span[1]].encode("utf-8")),
    )
    span_status = "operation_source_raw_xml_text_sequence_container_scan"
    basis = (
        "Raw Finlex operation-source XML decoded as UTF-8; exact raw quote and "
        "contiguous normalized text-container quote did not occur, but the "
        "OperationSource.raw_text tokens appeared in order within exactly one "
        "smallest XML element. Span covers that containing XML element and may "
        "include intervening source qualification text; it does not cover exact "
        "quote bytes."
    )
    detail = {
        "char_span": list(char_span),
        "char_span_status": span_status,
        "char_span_basis": basis,
        "byte_span": list(byte_span),
        "byte_span_status": f"{span_status}_utf8",
        "byte_span_basis": "UTF-8 byte offsets derived from raw XML containing-element character span.",
        "operation_source_xml_span_status": "available",
        "operation_source_xml_quote_match_count": 0,
        "operation_source_xml_text_sequence_match_count": 1,
        "operation_source_xml_text_sequence_candidate_count": len(candidates),
        "operation_source_xml_text_sequence_token_count": len(quote_tokens),
        "operation_source_xml_text_sequence_local_tag": tag,
        "operation_source_xml_text_sequence_span_basis": span_basis_detail,
    }
    if eid:
        detail["operation_source_xml_text_sequence_eid"] = eid
    return {
        "char_span": char_span,
        "byte_span": byte_span,
        "detail": detail,
        "source_witness_detail": {
            "artifact_char_span": list(char_span),
            "artifact_byte_span": list(byte_span),
            "artifact_span_status": span_status,
            "artifact_span_basis": basis,
            "artifact_span_match_count": 1,
        },
    }


def _xml_text_sequence_tokens(value: str) -> list[str]:
    text = _normalize_xml_text_for_span_match(value)
    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "§":
            tokens.append(char)
            index += 1
            continue
        if not _xml_text_sequence_word_char(char):
            index += 1
            continue
        start = index
        index += 1
        while index < len(text) and _xml_text_sequence_word_char(text[index]):
            index += 1
        while (
            index + 1 < len(text)
            and text[index] == "/"
            and _xml_text_sequence_word_char(text[index + 1])
        ):
            index += 2
            while index < len(text) and _xml_text_sequence_word_char(text[index]):
                index += 1
        tokens.append(text[start:index].casefold())
    return tokens


def _xml_text_sequence_word_char(char: str) -> bool:
    return char == "_" or char.isalnum()


def _contains_ordered_token_sequence(candidate_tokens: list[str], quote_tokens: list[str]) -> bool:
    if not quote_tokens or len(candidate_tokens) < len(quote_tokens):
        return False
    offset = 0
    for token in quote_tokens:
        while offset < len(candidate_tokens) and candidate_tokens[offset] != token:
            offset += 1
        if offset == len(candidate_tokens):
            return False
        offset += 1
    return True


def _operation_source_xml_text_container_span(
    xml_bytes: bytes,
    *,
    raw_xml_text: str,
    raw_source_text: str,
) -> dict[str, Any] | None:
    """Anchor an operation quote to the smallest unique XML text container.

    Finlex operation-source raw text is extracted from rendered XML text, so the
    exact quote can cross inline markup boundaries. This fallback is deliberately
    coarser than ``operation_source_raw_xml_quote_scan``: the span covers the
    containing XML element, not exact quote bytes.
    """
    from lxml import etree

    normalized_quote = _normalize_xml_text_for_span_match(raw_source_text)
    if not normalized_quote:
        return None
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return _unavailable_operation_source_container_span(
            "unavailable_operation_source_xml_parse_failed",
            match_count=0,
        )
    candidates: list[Any] = []
    for element in root.iter():
        if not isinstance(element, etree._Element):
            continue
        normalized_text = _normalize_xml_text_for_span_match(
            "".join(str(part) for part in element.itertext())
        )
        if normalized_quote in normalized_text:
            candidates.append(element)
    if not candidates:
        return None
    smallest = [
        element
        for element in candidates
        if not any(other is not element and _xml_element_is_ancestor(element, other) for other in candidates)
    ]
    if len(smallest) != 1:
        return _unavailable_operation_source_container_span(
            "unavailable_operation_source_text_container_not_unique",
            match_count=len(smallest),
            candidate_count=len(candidates),
        )
    smallest_element = smallest[0]
    element = smallest_element
    ancestor_steps = 0
    while element is not None and not str(element.get("eId") or ""):
        element = element.getparent()
        ancestor_steps += 1
    if element is None:
        smallest_tag = etree.QName(smallest_element).localname
        sourceline = int(getattr(smallest_element, "sourceline", 0) or 0)
        char_span = _raw_xml_sourceline_element_char_span(
            raw_xml_text,
            local_tag=smallest_tag,
            sourceline=sourceline,
            attrs=_raw_xml_stable_attrs(smallest_element),
        )
        eid = ""
        tag = smallest_tag
        span_status = "operation_source_raw_xml_text_container_sourceline_scan"
        span_basis_detail = "source_line_and_stable_attrs"
        if char_span is None:
            return _unavailable_operation_source_container_span(
                "unavailable_operation_source_text_container_missing_eid",
                match_count=1,
                candidate_count=len(candidates),
                local_tag=smallest_tag,
                sourceline=sourceline,
            )
    else:
        eid = str(element.get("eId") or "")
        tag = etree.QName(element).localname
        char_span = _raw_xml_eid_element_char_span(raw_xml_text, local_tag=tag, eid=eid)
        span_status = "operation_source_raw_xml_text_container_scan"
        span_basis_detail = "eid_or_nearest_eid_ancestor"
        if char_span is None:
            return _unavailable_operation_source_container_span(
                "unavailable_operation_source_text_container_raw_xml_scan_failed",
                match_count=1,
                candidate_count=len(candidates),
                eid=eid,
                local_tag=tag,
            )
    byte_span = (
        len(raw_xml_text[: char_span[0]].encode("utf-8")),
        len(raw_xml_text[: char_span[1]].encode("utf-8")),
    )
    basis = (
        "Raw Finlex operation-source XML decoded as UTF-8; exact raw quote did "
        "not occur, but normalized OperationSource.raw_text was contained in "
        "exactly one smallest XML element text surface. Span covers that "
        "containing XML element, or its nearest eId ancestor when the text "
        "container itself has no eId; when no eId ancestor exists, the span is "
        "balanced from the element source line and stable raw XML attributes. "
        "It does not cover exact quote bytes."
    )
    detail = {
        "char_span": list(char_span),
        "char_span_status": span_status,
        "char_span_basis": basis,
        "byte_span": list(byte_span),
        "byte_span_status": f"{span_status}_utf8",
        "byte_span_basis": "UTF-8 byte offsets derived from raw XML containing-element character span.",
        "operation_source_xml_span_status": "available",
        "operation_source_xml_quote_match_count": 0,
        "operation_source_xml_text_container_match_count": 1,
        "operation_source_xml_text_container_candidate_count": len(candidates),
        "operation_source_xml_text_container_local_tag": tag,
        "operation_source_xml_text_container_ancestor_steps": ancestor_steps,
        "operation_source_xml_text_container_span_basis": span_basis_detail,
    }
    if eid:
        detail["operation_source_xml_text_container_eid"] = eid
    return {
        "char_span": char_span,
        "byte_span": byte_span,
        "detail": detail,
        "source_witness_detail": {
            "artifact_char_span": list(char_span),
            "artifact_byte_span": list(byte_span),
            "artifact_span_status": span_status,
            "artifact_span_basis": basis,
            "artifact_span_match_count": 1,
        },
    }


def _normalize_xml_text_for_span_match(value: str) -> str:
    return " ".join(str(value or "").split())


def _xml_element_is_ancestor(candidate_ancestor: Any, candidate_child: Any) -> bool:
    parent = candidate_child.getparent()
    while parent is not None:
        if parent is candidate_ancestor:
            return True
        parent = parent.getparent()
    return False


def _unavailable_operation_source_container_span(
    span_status: str,
    *,
    match_count: int,
    candidate_count: int = 0,
    eid: str = "",
    local_tag: str = "",
    sourceline: int = 0,
) -> dict[str, Any]:
    counter_family = "text_sequence" if "text_sequence" in span_status else "text_container"
    detail: dict[str, Any] = {
        "operation_source_xml_span_status": span_status,
        "operation_source_xml_quote_match_count": 0,
        f"operation_source_xml_{counter_family}_match_count": match_count,
        f"operation_source_xml_{counter_family}_candidate_count": candidate_count,
    }
    if eid:
        detail[f"operation_source_xml_{counter_family}_eid"] = eid
    if local_tag:
        detail[f"operation_source_xml_{counter_family}_local_tag"] = local_tag
    if sourceline:
        detail[f"operation_source_xml_{counter_family}_sourceline"] = sourceline
    return {
        "char_span": None,
        "byte_span": None,
        "detail": detail,
        "source_witness_detail": {
            "artifact_span_status": span_status,
            "artifact_span_match_count": match_count,
        },
    }


def _finlex_span_basis(match_basis: str) -> str:
    if match_basis == "xpath_candidate":
        return (
            "Raw Finlex source XML decoded as UTF-8; XPath candidate matched "
            "one element with eId; raw scan balanced the matching element tag."
        )
    return (
        "Raw Finlex source XML decoded as UTF-8; XPath candidate did not "
        "match exactly one element; fallback eId matched one element; raw "
        "scan balanced the matching element tag."
    )


def _finlex_eid_candidate(address: LegalAddress) -> str:
    segments: list[str] = []
    for kind, label in address.path:
        prefix = _FINLEX_EID_SEGMENT_PREFIX.get(kind)
        if not prefix:
            return ""
        segments.append(f"{prefix}_{label}")
    return "__".join(segments)


_FINLEX_EID_SEGMENT_PREFIX = {
    "part": "part",
    "chapter": "chp",
    "section": "sec",
    "subsection": "subsec",
    "paragraph": "para",
    "subparagraph": "subpara",
    "item": "item",
}


def _raw_xml_eid_element_char_span(
    text: str,
    *,
    local_tag: str,
    eid: str,
) -> tuple[int, int] | None:
    start = _raw_xml_eid_start(text, local_tag=local_tag, eid=eid)
    if start is None:
        return None
    return _raw_xml_local_tag_element_char_span_from_start(text, local_tag=local_tag, start=start)


def _raw_xml_local_tag_element_char_span_from_start(
    text: str,
    *,
    local_tag: str,
    start: int,
) -> tuple[int, int] | None:
    tag_re = _raw_xml_local_tag_re(local_tag)
    depth = 0
    for match in tag_re.finditer(text, start):
        token = match.group(0)
        closing = token.startswith("</")
        self_closing = token.rstrip().endswith("/>")
        if closing:
            depth -= 1
            if depth == 0:
                return (start, match.end())
        elif not self_closing:
            depth += 1
        elif match.start() == start:
            return (start, match.end())
    return None


def _raw_xml_sourceline_element_char_span(
    text: str,
    *,
    local_tag: str,
    sourceline: int,
    attrs: Mapping[str, str],
) -> tuple[int, int] | None:
    if sourceline <= 0:
        return None
    line_start = _line_start_offset(text, sourceline)
    if line_start is None:
        return None
    line_end = text.find("\n", line_start)
    if line_end < 0:
        line_end = len(text)
    start_tag_re = _raw_xml_local_start_tag_re(local_tag)
    for match in start_tag_re.finditer(text, line_start, line_end):
        start_tag = match.group(0)
        if _raw_xml_start_tag_has_attrs(start_tag, attrs):
            return _raw_xml_local_tag_element_char_span_from_start(
                text,
                local_tag=local_tag,
                start=match.start(),
            )
    return None


def _line_start_offset(text: str, line_number: int) -> int | None:
    if line_number == 1:
        return 0
    current_line = 1
    offset = 0
    while current_line < line_number:
        newline = text.find("\n", offset)
        if newline < 0:
            return None
        offset = newline + 1
        current_line += 1
    return offset


def _raw_xml_stable_attrs(element: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for name in ("eId", "name"):
        value = str(element.get(name) or "")
        if value:
            attrs[name] = value
    return attrs


def _raw_xml_start_tag_has_attrs(start_tag: str, attrs: Mapping[str, str]) -> bool:
    for name, value in attrs.items():
        if f'{name}="{value}"' not in start_tag and f"{name}='{value}'" not in start_tag:
            return False
    return True


@functools.lru_cache(maxsize=128)
def _raw_xml_local_tag_re(local_tag: str) -> re.Pattern[str]:
    return re.compile(rf"</?(?:[A-Za-z_][\w.-]*:)?{re.escape(local_tag)}\b[^>]*>")


@functools.lru_cache(maxsize=128)
def _raw_xml_local_start_tag_re(local_tag: str) -> re.Pattern[str]:
    return re.compile(rf"<(?:[A-Za-z_][\w.-]*:)?{re.escape(local_tag)}\b[^>]*>")


def _raw_xml_eid_start(text: str, *, local_tag: str, eid: str) -> int | None:
    for quote in ("\"", "'"):
        needle = f"eId={quote}{eid}{quote}"
        search_at = 0
        while True:
            attr_index = text.find(needle, search_at)
            if attr_index < 0:
                break
            start = text.rfind("<", 0, attr_index)
            end = text.find(">", attr_index)
            if start >= 0 and end >= 0:
                start_tag = text[start : end + 1]
                if re.match(rf"<(?:[A-Za-z_][\w.-]*:)?{re.escape(local_tag)}\b", start_tag):
                    return start
            search_at = attr_index + len(needle)
    return None


def _finlex_num_predicate(kind: str, tag: str, label: str) -> str:
    expr = _finlex_normalized_num_expr()
    variants = _finlex_label_variants(kind, label)
    clauses = [f"{expr}={_xpath_literal(variant)}" for variant in variants]
    if label.isdigit() and kind in {"subsection", "paragraph", "subparagraph", "item"}:
        clauses.append(
            f"(not(*[local-name()='num']) and count(preceding-sibling::*[local-name()={_xpath_literal(tag)}]) + 1 = {label})"
        )
    return " or ".join(clauses) or "false()"


def _finlex_normalized_num_expr() -> str:
    return (
        "translate(normalize-space(string(*[local-name()='num'][1])), "
        f"{_xpath_literal(_FINLEX_XPATH_TRANSLATE_FROM)}, {_xpath_literal(_FINLEX_XPATH_TRANSLATE_TO)})"
    )


def _finlex_label_variants(kind: str, label: str) -> tuple[str, ...]:
    normalized = label.strip().lower()
    if not normalized:
        return ()
    variants = {normalized}
    if kind == "chapter":
        variants.add(f"{normalized}luku")
        variants.update(_roman_label_variants(normalized, suffixes=("luku",)))
    elif kind == "part":
        variants.add(f"{normalized}osa")
        variants.add(f"{normalized}osasto")
        variants.update(_roman_label_variants(normalized, suffixes=("osa", "osasto")))
    return tuple(sorted(variants))


def _roman_label_variants(label: str, *, suffixes: tuple[str, ...]) -> set[str]:
    if not label.isdigit():
        return set()
    try:
        roman = arabic_to_roman(int(label)).lower()
    except ValueError:
        return set()
    return {roman, *(f"{roman}{suffix}" for suffix in suffixes)}


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def _source_quote_payload(version: ProvisionVersion | None) -> dict[str, Any] | None:
    if version is None or version.source is None:
        return None
    raw_source_text = str(version.source.raw_text or "")
    raw_text = raw_source_text.strip()
    if not raw_text:
        return None
    start = raw_source_text.find(raw_text)
    if start < 0:
        start = 0
    bounded = raw_text[:1000]
    quote_end = start + len(bounded)
    full_end = start + len(raw_text)
    return {
        "kind": "operation_source_raw_text",
        "quote": bounded,
        "quote_hash": _sha256_text(raw_text),
        "quote_hash_semantics": "sha256(trimmed full OperationSource.raw_text)",
        "quote_truncated": len(raw_text) > len(bounded),
        "quote_char_span": [start, quote_end],
        "full_raw_text_char_span": [start, full_end],
        "char_span_basis": "OperationSource.raw_text after boundary whitespace trimming",
        "char_span_status": "operation_source_raw_text_char_span",
        "precision": "bounded_source_quote",
    }


def _operation_source_witness_payload(
    quote_payload: Mapping[str, Any],
    *,
    artifact_id: str,
    locator: str,
    artifact_digest: str,
    artifact_digest_algorithm: str,
) -> dict[str, Any]:
    bounded_preview = str(quote_payload.get("quote") or "")
    digest = (
        DigestWitness(
            digest_algorithm=artifact_digest_algorithm or "sha256",
            digest=artifact_digest,
        )
        if artifact_digest
        else None
    )
    preview_digest = (
        DigestWitness(digest_algorithm="sha256", digest=_sha256_text(bounded_preview))
        if bounded_preview
        else None
    )
    return SourceWitness(
        source_role="operation_source_raw_text",
        artifact_id=artifact_id,
        locator=locator,
        digest=digest,
        bounded_preview=bounded_preview,
        preview_digest=preview_digest,
        source_lane="finlex_source_xml",
        metadata=quote_payload,
    ).to_dict()


def _engine_payload() -> dict[str, str]:
    identity = _lawvm_code_identity()
    return {
        "producer": "lawvm",
        "build_id": identity["build_id"],
        "interface": "lawvm provision-state",
        "git_commit": identity["git_commit"],
        "git_dirty": identity["git_dirty"],
        "repository": identity["repository"],
    }


def unsupported_jurisdiction_payload(
    *,
    jurisdiction: str,
    statute_id: str,
    provision: str,
    as_of: str,
    query_type: str,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "spec_version": SPEC_VERSION,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "provision_status": "unsupported_jurisdiction",
        "query": _query_payload(
            statute_id=statute_id,
            provision=provision,
            as_of=as_of,
            query_type=query_type,
            territory=None,
        ),
        "supported_jurisdictions": ["fi"],
        "engine": _engine_payload(),
    }


def _require_address(resolution: AddressResolution) -> LegalAddress:
    if resolution.address is None:
        raise ValueError("resolved AddressResolution must carry an address")
    return resolution.address


def _sha256_canonical(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _sha256_text(encoded)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lawvm_code_identity() -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[3]
    inside = subprocess.run(
        ("git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {
            "repository": repo_root.name,
            "git_commit": "",
            "git_dirty": "unknown",
            "build_id": "",
        }
    commit = subprocess.run(
        ("git", "-C", str(repo_root), "rev-parse", "HEAD"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        (
            "git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.strip()
    dirty = "true" if status else "false"
    build_id = f"git:{commit}" if commit else ""
    if build_id and dirty == "true":
        build_id = f"{build_id}+dirty"
    return {
        "repository": repo_root.name,
        "git_commit": commit,
        "git_dirty": dirty,
        "build_id": build_id,
    }
