"""Stable provision-state seam surface for point-in-time consumers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lawvm.core.ir import IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash, irnode_to_text
from lawvm.core.provenance import MigrationEvent
from lawvm.core.timeline_lineage import lineage_address_chain
from lawvm.core.timeline_selection import VersionSelectionResult, select_active_version_ex

SCHEMA = "lawvm.provision_state.v1"


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
    from lawvm.finland.grafter import replay_xml

    if args.jurisdiction != "fi":
        payload = _unsupported_jurisdiction_payload(
            jurisdiction=args.jurisdiction,
            statute_id=args.statute_id,
            provision=args.provision,
            as_of=args.as_of,
            query_type=args.query_type,
        )
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return

    print(f"Replaying {args.statute_id}...", file=sys.stderr)
    master = replay_xml(args.statute_id, quiet=True)
    base_ir = IRStatute(
        statute_id=args.statute_id,
        title=master.title,
        body=master.ctx.base_ir,
    )
    payload = build_provision_state_response(
        timelines=master.timelines,
        migration_events=tuple(master.migration_events or ()),
        statute_id=args.statute_id,
        jurisdiction=args.jurisdiction,
        provision=args.provision,
        as_of=args.as_of,
        query_type=args.query_type,
        territory=args.territory,
        include_ir=args.include_ir,
        title=master.title,
        base=base_ir,
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
            "source_locator_status": "unavailable_initial_surface",
        }

    selection = select_active_version_ex(
        resolution.timeline,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
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
    )


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
) -> dict[str, Any]:
    address = _require_address(resolution)
    version = selection.version
    content_hash = _content_hash(version)
    status = "selected" if version is not None else selection.status
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
        "version": _version_payload(version),
        "hashes": _hash_payload(
            status=status,
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            query=query,
            address=address,
            lineage=lineage,
            version=version,
            content_hash=content_hash,
        ),
        "text": _text_payload(version),
        "source": _source_payload(version),
        "engine": _engine_payload(),
        "source_locator_status": "unavailable_initial_surface",
    }
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


def _version_payload(version: ProvisionVersion | None) -> dict[str, Any] | None:
    if version is None:
        return None
    content_state = "tombstone" if version.content is None else "live"
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
) -> dict[str, str]:
    derived_input = {
        "schema": SCHEMA,
        "status": status,
        "jurisdiction": jurisdiction,
        "statute_id": statute_id,
        "query": query,
        "resolved_address": _address_wire(address) if address is not None else None,
        "lineage": lineage,
        "version": _version_payload(version),
        "content_hash": content_hash,
    }
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


def _unsupported_jurisdiction_payload(
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
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
