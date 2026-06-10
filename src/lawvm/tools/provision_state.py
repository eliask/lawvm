"""Stable provision-state seam surface for point-in-time consumers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lawvm.corpus_store import statute_url
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash, irnode_to_text
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_locator import SourceLocator
from lawvm.core.statute_validity import StatuteValidityBound, is_expired_at
from lawvm.core.timeline_lineage import lineage_address_chain
from lawvm.core.timeline_selection import VersionSelectionResult, select_active_version_ex

SCHEMA = "lawvm.provision_state.v1"
DUMP_SCHEMA = "lawvm.dump.v1"

# Stage-2 rollback flag (Pro §5). Extraction/diagnostics are always available;
# only the selection/seam SEMANTICS (flipping status to "expired") are gated.
FIXED_TERM_BOUNDS_FLAG = "LAWVM_ENABLE_FIXED_TERM_STATUTE_BOUNDS"


def _fixed_term_bounds_enabled() -> bool:
    return os.environ.get(FIXED_TERM_BOUNDS_FLAG, "") not in ("", "0", "false", "False")


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

    status: str
    requested: str
    address: LegalAddress | None = None
    timeline: ProvisionTimeline | None = None
    candidates: tuple[LegalAddress, ...] = ()


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
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


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
) -> dict[str, Any]:
    """Return a stable provision-state response for one PIT address query."""

    resolution = resolve_address(timelines, provision)
    query = _query_payload(
        statute_id=statute_id,
        provision=provision,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )
    if resolution.status != "resolved":
        return {
            "schema": SCHEMA,
            "jurisdiction": jurisdiction,
            "statute_id": statute_id,
            "title": title,
            "status": resolution.status,
            "query": query,
            "resolved_address": None,
            "lineage": _lineage_payload(
                address=None,
                migration_events=migration_events,
                as_of=as_of,
            ),
            "address_candidates": [_address_wire(candidate) for candidate in resolution.candidates],
            "selection": None,
            "hashes": _hash_payload(
                status=resolution.status,
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
            "source_locator_status": "unavailable_unresolved_provision",
        }

    selection = select_active_version_ex(
        resolution.timeline,  # ty:ignore[invalid-argument-type]
        as_of=as_of,
        query_type=query_type,
        territory=territory,
    )
    overlay = _fixed_term_overlay(
        timelines=timelines,
        statute_id=statute_id,
        selection=selection,
        as_of=as_of,
        query_type=query_type,
    )
    return _selected_response(
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
    )


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
        if version.content is None:
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
        return AddressResolution(status="invalid_address", requested=provision)
    timeline = timelines.get(target)
    if timeline is not None:
        return AddressResolution(
            status="resolved",
            requested=provision,
            address=target,
            timeline=timeline,
        )
    suffix = target.path
    candidates = tuple(address for address in timelines if address.path[-len(suffix) :] == suffix)
    if len(candidates) == 1:
        address = candidates[0]
        return AddressResolution(
            status="resolved",
            requested=provision,
            address=address,
            timeline=timelines[address],
        )
    if candidates:
        return AddressResolution(
            status="ambiguous_address",
            requested=provision,
            candidates=tuple(sorted(candidates, key=str)),
        )
    return AddressResolution(status="address_not_found", requested=provision)


def _selected_response(
    *,
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
) -> dict[str, Any]:
    address = _require_address(resolution)
    version = selection.version
    # Fixed-term overlay only fires when a live version would otherwise be
    # selected, so past the bound the seam must not expose live content.
    expired = overlay is not None and overlay.kind == "expired"
    blocked = overlay is not None and overlay.kind in ("blocked_unparseable", "blocked_ambiguous")
    payload_version = None if (expired or blocked) else version
    content_hash = _content_hash(payload_version)
    if expired:
        status = "expired"
    elif blocked:
        status = "expiry_unverified"
    else:
        status = "selected" if version is not None else selection.status
    expiry_block = _expiry_block(overlay, statute_id, address)
    lineage = _lineage_payload(
        address=address,
        migration_events=migration_events,
        as_of=query["as_of"],
    )
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "title": title,
        "status": status,
        "query": query,
        "resolved_address": _address_wire(address),
        "lineage": lineage,
        "address_match": {
            "requested": resolution.requested,
            "mode": "exact" if str(address) == resolution.requested else "unique_suffix",
        },
        "selection": _selection_payload(selection),
        "version": _version_payload(payload_version, content_state_override=("expired" if expired else None)),
        "hashes": _hash_payload(
            status=status,
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            query=query,
            address=address,
            lineage=lineage,
            version=payload_version,
            content_hash=content_hash,
            expiry=expiry_block,
            content_state_override=("expired" if expired else None),
        ),
        "text": _text_payload(payload_version),
        "source": _source_payload(payload_version),
        "source_locator": _source_locator_payload(
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            address=address,
            version=payload_version,
        ),
        "engine": _engine_payload(),
    }
    if expiry_block is not None:
        if expired:
            payload["expires"] = overlay.expires_on  # type: ignore[union-attr]
            payload["valid_until"] = overlay.valid_until  # type: ignore[union-attr]
        payload["expiry"] = expiry_block
    payload["source_locator_status"] = (
        "canonical_document_locator" if payload["source_locator"] is not None else "unavailable_no_source"
    )
    if include_ir:
        payload["ir"] = _ir_payload(version)
    if base is not None:
        payload["base"] = {
            "statute_id": base.statute_id,
            "title": base.title,
        }
    return payload


def _parse_addr(addr_str: str) -> LegalAddress | None:
    pairs: list[tuple[str, str]] = []
    for part in addr_str.split("/"):
        if ":" not in part:
            continue
        kind, label = part.split(":", 1)
        kind = kind.strip()
        label = label.strip()
        if not kind or not label:
            return None
        pairs.append((kind, label))
    if not pairs:
        return None
    return LegalAddress(path=tuple(pairs))


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
        "status": selection.status,
        "required_dimensions": list(selection.required_dimensions),
        "certificate": cert_payload,
    }


def _lineage_payload(
    *,
    address: LegalAddress | None,
    migration_events: tuple[MigrationEvent, ...],
    as_of: str,
) -> dict[str, Any]:
    if address is None:
        return {
            "status": "unresolved_address",
            "address_chain": [],
            "migration_event_count_considered": len(migration_events),
        }
    chain = lineage_address_chain(
        address,
        migration_events,
        as_of_date=as_of,
        address_prefix_matches=lambda current, prefix: current.has_prefix(prefix),
    )
    status = "migration_chain" if len(chain) > 1 else "self_only"
    return {
        "status": status,
        "address_chain": [_address_wire(chain_address) for chain_address in chain],
        "migration_event_count_considered": len(migration_events),
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
    content_state = content_state_override or ("tombstone" if version.content is None else "live")
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
    if version.content_hash:
        return version.content_hash
    return irnode_content_hash(version.content)


def _hash_payload(
    *,
    status: str,
    statute_id: str,
    jurisdiction: str,
    query: Mapping[str, Any],
    address: LegalAddress | None,
    lineage: Mapping[str, Any] | None,
    version: ProvisionVersion | None,
    content_hash: str,
    expiry: Mapping[str, Any] | None = None,
    content_state_override: str | None = None,
) -> dict[str, str]:
    derived_input = {
        "schema": SCHEMA,
        "status": status,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "query": query,
        "resolved_address": _address_wire(address) if address is not None else None,
        "lineage": lineage,
        "version": _version_payload(version, content_state_override=content_state_override),
        "content_hash": content_hash,
    }
    # Only mutate the hashed state when the fixed-term overlay is active, so the
    # flag-OFF default path remains byte-identical.
    if expiry is not None:
        derived_input["expiry"] = expiry
    return {
        "content_hash": content_hash,
        "content_hash_semantics": "sha256(irnode_to_text(content)); text-only; empty for absent/tombstone",
        "derived_state_hash": _sha256_canonical(derived_input),
        "derived_state_hash_semantics": (
            "sha256(canonical lawvm.provision_state.v1 state: status, query, "
            "resolved address, version temporal metadata, applicability, content_hash)"
        ),
    }


def _text_payload(version: ProvisionVersion | None) -> dict[str, Any]:
    if version is None or version.content is None:
        return {
            "rendered": "",
            "available": False,
        }
    return {
        "rendered": irnode_to_text(version.content),
        "available": True,
    }


def _ir_payload(version: ProvisionVersion | None) -> dict[str, Any] | None:
    if version is None or version.content is None:
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
    detail: dict[str, Any] = {
        "locator_status": locator_status,
        "selected_target_address": str(address),
        "precision": "document_plus_resolved_target_legal_address",
        "target_legal_address_kind": "lawvm_resolved_target",
        "xpath": "unavailable",
        "byte_span": "unavailable",
    }
    if source_quote is not None:
        detail["source_witness"] = source_quote
    locator = SourceLocator(
        jurisdiction=jurisdiction,
        artifact_kind=artifact_kind,
        source_id=f"finlex:{artifact_kind}:{source_sid}",
        document_uri=statute_url(source_sid),
        structural_path=f"lawvm-target:{address}",
        quote_hash=source_quote["quote_hash"] if source_quote is not None else "",
        statute_id=source_sid,
        normalization_policy="finlex_statute_document_locator.v1",
        detail=detail,
    )
    return locator.to_dict()


def _source_quote_payload(version: ProvisionVersion | None) -> dict[str, Any] | None:
    if version is None or version.source is None:
        return None
    raw_text = str(version.source.raw_text or "").strip()
    if not raw_text:
        return None
    bounded = raw_text[:1000]
    return {
        "kind": "operation_source_raw_text",
        "quote": bounded,
        "quote_hash": _sha256_text(raw_text),
        "quote_hash_semantics": "sha256(full OperationSource.raw_text)",
        "quote_truncated": len(raw_text) > len(bounded),
        "precision": "bounded_source_quote",
    }


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
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "status": "unsupported_jurisdiction",
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
        ("git", "-C", str(repo_root), "status", "--short"),
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
