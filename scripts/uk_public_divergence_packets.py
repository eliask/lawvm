#!/usr/bin/env python3
"""Build public verification packets for UK consolidation review leads.

The packets are for external review. They are not legal conclusions and do not
grant execution authority to any candidate row.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


_LEG_BASE = "https://www.legislation.gov.uk"
_FETCH_USER_AGENT = "LawVM-public-divergence-packet/1.0"
_FORBIDDEN_SHORTCUTS = (
    "packet_as_legal_conclusion",
    "current_page_as_source_truth",
    "amending_fragment_without_commencement_extent_or_savings_review",
    "review_lead_as_automatic_consolidation_change",
)


@dataclass(frozen=True)
class PublicSnapshot:
    role: str
    requested_url: str
    final_url: str
    status_code: int
    fetched_at_utc: str
    content_type: str
    sha256: str
    byte_count: int
    storage_path: str
    text_preview: str


@dataclass(frozen=True)
class PublicPageStatusWitness:
    current_page_url: str
    snapshot_sha256: str
    status_warning_class: str
    status_text: str
    no_known_outstanding_effects: bool
    timeline_version_dates: tuple[str, ...]
    current_timeline_date: str
    current_timeline_source_xml_url: str
    current_timeline_source_xml_snapshot_sha256: str
    current_timeline_source_xml_snapshot_path: str
    current_timeline_source_xml_byte_count: int


@dataclass(frozen=True)
class PublicOperationEvidence:
    action: str
    affected_provision: str
    affecting_source_id: str
    affecting_provisions: str
    effect_type: str
    effective_date: str
    source_preview: str
    affecting_source_sha256: str
    public_source_urls: tuple[str, ...]


@dataclass(frozen=True)
class PublicDivergencePacket:
    statute_id: str
    review_family: str
    confidence: str
    current_targets: tuple[str, ...]
    current_page_urls: tuple[str, ...]
    enacted_source_url: str
    current_source_url: str
    amending_source_urls: tuple[str, ...]
    operation_evidence: tuple[PublicOperationEvidence, ...]
    public_snapshots: tuple[PublicSnapshot, ...]
    current_page_status_witnesses: tuple[PublicPageStatusWitness, ...]
    missing_standalone_evidence: tuple[str, ...]
    verification_question: str
    caveats_to_check: tuple[str, ...]
    safe_default: str
    forbidden_shortcuts: tuple[str, ...]


def load_packets(
    candidates_path: Path,
    *,
    supplement_path: Path | None = None,
    fetch_public_snapshots: bool = False,
    fetch_current_timeline_xml: bool = False,
    snapshot_dir: Path | None = None,
    require_standalone_evidence: bool = False,
    limit: int = 0,
    statute_ids: frozenset[str] = frozenset(),
    fetcher: Callable[[str], tuple[str, int, str, bytes]] | None = None,
) -> list[PublicDivergencePacket]:
    candidates = _load_rows(candidates_path)
    supplements = _load_supplements(supplement_path) if supplement_path else {}
    packets: list[PublicDivergencePacket] = []
    for candidate in candidates:
        statute_id = str(candidate.get("statute_id") or "")
        if not statute_id or statute_ids and statute_id not in statute_ids:
            continue
        supplement = supplements.get(statute_id, {})
        if require_standalone_evidence and not _operation_evidence_tuple(supplement):
            continue
        packet = _packet_from_candidate(
            candidate,
            supplement,
            fetch_public_snapshots=fetch_public_snapshots,
            fetch_current_timeline_xml=fetch_current_timeline_xml,
            snapshot_dir=snapshot_dir,
            fetcher=fetcher,
        )
        if require_standalone_evidence and packet.missing_standalone_evidence:
            continue
        packets.append(packet)
        if limit > 0 and len(packets) >= limit:
            break
    return packets


def _load_rows(path: Path) -> list[Mapping[str, Any]]:
    data = json.loads(path.read_text())
    rows = data.get("rows", data) if isinstance(data, Mapping) else data
    if not isinstance(rows, list):
        raise ValueError(f"{path} does not contain a row list")
    return [row for row in rows if isinstance(row, Mapping)]


def _load_supplements(path: Path) -> dict[str, Mapping[str, Any]]:
    rows = _load_rows(path)
    return {str(row.get("statute_id") or ""): row for row in rows}


def _packet_from_candidate(
    candidate: Mapping[str, Any],
    supplement: Mapping[str, Any],
    *,
    fetch_public_snapshots: bool,
    fetch_current_timeline_xml: bool,
    snapshot_dir: Path | None,
    fetcher: Callable[[str], tuple[str, int, str, bytes]] | None,
) -> PublicDivergencePacket:
    statute_id = str(candidate.get("statute_id") or supplement.get("statute_id") or "")
    current_targets = _string_tuple(
        supplement.get("retained_targets") or candidate.get("retained_repeal_targets")
    )
    current_page_urls = _string_tuple(supplement.get("current_urls")) or tuple(
        _current_url_for_target(statute_id, target) for target in current_targets
    )
    enacted_source_url = str(
        supplement.get("base_source")
        or candidate.get("base_source_locator")
        or f"{_LEG_BASE}/{statute_id}/enacted/data.xml"
    )
    current_source_url = str(
        supplement.get("oracle_source")
        or candidate.get("oracle_source_locator")
        or f"{_LEG_BASE}/{statute_id}/data.xml"
    )
    operation_evidence = _operation_evidence_tuple(supplement)
    amending_source_urls = _unique(
        url
        for op in operation_evidence
        for url in op.public_source_urls
    )
    snapshot_urls = _snapshot_url_roles(
        current_page_urls=current_page_urls,
        enacted_source_url=enacted_source_url,
        current_source_url=current_source_url,
        amending_source_urls=amending_source_urls,
    )
    public_snapshots = (
        _fetch_public_snapshots(snapshot_urls, snapshot_dir=snapshot_dir, fetcher=fetcher)
        if fetch_public_snapshots
        else ()
    )
    current_page_status_witnesses = _current_page_status_witnesses(public_snapshots)
    if fetch_current_timeline_xml and current_page_status_witnesses:
        timeline_urls = _unique(
            witness.current_timeline_source_xml_url
            for witness in current_page_status_witnesses
        )
        timeline_snapshots = _fetch_public_snapshots(
            tuple(("current_timeline_source_xml", url) for url in timeline_urls),
            snapshot_dir=snapshot_dir,
            fetcher=fetcher,
        )
        public_snapshots = (*public_snapshots, *timeline_snapshots)
        current_page_status_witnesses = _current_page_status_witnesses(public_snapshots)
    missing = _missing_standalone_evidence(
        operation_evidence=operation_evidence,
        public_snapshots=public_snapshots,
        fetch_public_snapshots=fetch_public_snapshots,
    )
    return PublicDivergencePacket(
        statute_id=statute_id,
        review_family=_review_family(candidate),
        confidence=str(candidate.get("confidence") or "review_lead"),
        current_targets=current_targets,
        current_page_urls=current_page_urls,
        enacted_source_url=enacted_source_url,
        current_source_url=current_source_url,
        amending_source_urls=amending_source_urls,
        operation_evidence=operation_evidence,
        public_snapshots=public_snapshots,
        current_page_status_witnesses=current_page_status_witnesses,
        missing_standalone_evidence=missing,
        verification_question=(
            "Does the current page still expose the listed provision while the "
            "cited public amending source appears to repeal or omit it, and is "
            "there an apparent savings, extent, prospective, retained-law, or "
            "editorial-display reason?"
        ),
        caveats_to_check=(
            "commencement",
            "extent",
            "savings_or_transitional_provisions",
            "prospective_or_retained_law_display",
            "later_revival_or_reinsertion",
            "editorial_policy",
        ),
        safe_default=(
            "treat_as_review_lead_only_until_public_source_context_and_caveats_are_checked"
        ),
        forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
    )


def _review_family(candidate: Mapping[str, Any]) -> str:
    family = str(candidate.get("candidate_family") or "")
    if family == "oracle_retains_source_repealed_state":
        return "current_page_retains_apparently_repealed_or_omitted_provision"
    if family == "oracle_addition_without_compiled_source_chain":
        return "current_page_contains_addition_requiring_source_chain_review"
    return "current_page_divergence_review_lead"


def _operation_evidence_tuple(
    supplement: Mapping[str, Any],
) -> tuple[PublicOperationEvidence, ...]:
    operations = supplement.get("matched_ops")
    if not isinstance(operations, Iterable) or isinstance(operations, str):
        return ()
    out: list[PublicOperationEvidence] = []
    for op in operations:
        if not isinstance(op, Mapping):
            continue
        source_id = str(op.get("source_statute") or op.get("affecting_act_id") or "")
        affecting_provisions = str(op.get("affecting_provisions") or "")
        public_urls = _public_source_urls(source_id, affecting_provisions)
        out.append(
            PublicOperationEvidence(
                action=str(op.get("action") or ""),
                affected_provision=str(op.get("affected") or ""),
                affecting_source_id=source_id,
                affecting_provisions=affecting_provisions,
                effect_type=str(op.get("effect_type") or ""),
                effective_date=str(op.get("effective_date") or op.get("source_effective") or ""),
                source_preview=_squash(str(op.get("source_preview") or "")),
                affecting_source_sha256=str(op.get("affecting_source_sha256") or ""),
                public_source_urls=public_urls,
            )
        )
    return tuple(out)


def _public_source_urls(source_id: str, affecting_provisions: str) -> tuple[str, ...]:
    if not source_id:
        return ()
    urls: list[str] = [f"{_LEG_BASE}/{source_id}"]
    lower = affecting_provisions.lower()
    for kind, path in (
        ("art", "article"),
        ("reg", "regulation"),
        ("s", "section"),
        ("sch", "schedule"),
    ):
        for number in _numbers_after_token(lower, kind):
            urls.append(f"{_LEG_BASE}/{source_id}/{path}/{number}")
    if source_id.startswith(("uksi/", "ssi/", "wsi/", "nisr/")):
        for number in _numbers_after_token(lower, "para"):
            urls.append(f"{_LEG_BASE}/{source_id}/article/{number}")
    return _unique(urls)


def _numbers_after_token(text: str, token: str) -> tuple[str, ...]:
    pattern = rf"\b{re.escape(token)}\.?\s+(\d+)"
    return tuple(match.group(1) for match in re.finditer(pattern, text))


def _current_url_for_target(statute_id: str, target: str) -> str:
    if match := re.match(r"^article-(\d+)", target):
        return f"{_LEG_BASE}/{statute_id}/article/{match.group(1)}"
    if match := re.match(r"^section-(\d+)", target):
        return f"{_LEG_BASE}/{statute_id}/section/{match.group(1)}"
    if match := re.match(r"^schedule-(\d+)", target):
        return f"{_LEG_BASE}/{statute_id}/schedule/{match.group(1)}"
    return f"{_LEG_BASE}/{statute_id}"


def _snapshot_url_roles(
    *,
    current_page_urls: Sequence[str],
    enacted_source_url: str,
    current_source_url: str,
    amending_source_urls: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    pairs.extend(("current_page", url) for url in current_page_urls)
    if enacted_source_url:
        pairs.append(("enacted_source_xml", enacted_source_url))
    if current_source_url:
        pairs.append(("current_source_xml", current_source_url))
    pairs.extend(("amending_source", url) for url in amending_source_urls)
    seen: set[str] = set()
    unique_pairs: list[tuple[str, str]] = []
    for role, url in pairs:
        if url in seen:
            continue
        seen.add(url)
        unique_pairs.append((role, url))
    return tuple(unique_pairs)


def _fetch_public_snapshots(
    url_roles: Sequence[tuple[str, str]],
    *,
    snapshot_dir: Path | None,
    fetcher: Callable[[str], tuple[str, int, str, bytes]] | None,
) -> tuple[PublicSnapshot, ...]:
    if snapshot_dir is None:
        raise ValueError("--snapshot-dir is required with --fetch-public-snapshots")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    snapshots: list[PublicSnapshot] = []
    for role, url in url_roles:
        final_url, status_code, content_type, body = (
            fetcher(url) if fetcher is not None else _fetch_url(url)
        )
        digest = hashlib.sha256(body).hexdigest()
        storage_path = snapshot_dir / f"{digest[:16]}-{_safe_name(role)}"
        storage_path.write_bytes(body)
        snapshots.append(
            PublicSnapshot(
                role=role,
                requested_url=url,
                final_url=final_url,
                status_code=status_code,
                fetched_at_utc=now,
                content_type=content_type,
                sha256=digest,
                byte_count=len(body),
                storage_path=str(storage_path),
                text_preview=_preview(body),
            )
        )
    return tuple(snapshots)


def _current_page_status_witnesses(
    snapshots: Sequence[PublicSnapshot],
) -> tuple[PublicPageStatusWitness, ...]:
    timeline_snapshots = {
        snapshot.requested_url: snapshot
        for snapshot in snapshots
        if snapshot.role == "current_timeline_source_xml"
    }
    witnesses: list[PublicPageStatusWitness] = []
    for snapshot in snapshots:
        if snapshot.role != "current_page" or not snapshot.storage_path:
            continue
        path = Path(snapshot.storage_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        status_text = _status_warning_text(text)
        current_timeline_date = _current_timeline_date(text)
        current_timeline_source_xml_url = _current_timeline_source_xml_url(
            snapshot.final_url or snapshot.requested_url,
            current_timeline_date,
        )
        timeline_snapshot = timeline_snapshots.get(current_timeline_source_xml_url)
        witnesses.append(
            PublicPageStatusWitness(
                current_page_url=snapshot.final_url or snapshot.requested_url,
                snapshot_sha256=snapshot.sha256,
                status_warning_class=_status_warning_class(text),
                status_text=status_text,
                no_known_outstanding_effects=(
                    "no known outstanding effects" in status_text.lower()
                ),
                timeline_version_dates=_timeline_version_dates(text),
                current_timeline_date=current_timeline_date,
                current_timeline_source_xml_url=current_timeline_source_xml_url,
                current_timeline_source_xml_snapshot_sha256=(
                    timeline_snapshot.sha256 if timeline_snapshot else ""
                ),
                current_timeline_source_xml_snapshot_path=(
                    timeline_snapshot.storage_path if timeline_snapshot else ""
                ),
                current_timeline_source_xml_byte_count=(
                    timeline_snapshot.byte_count if timeline_snapshot else 0
                ),
            )
        )
    return tuple(witnesses)


def _status_warning_class(text: str) -> str:
    match = re.search(r'<div\s+id="statusWarning"\s+class="([^"]*)"', text)
    return match.group(1) if match else ""


def _status_warning_text(text: str) -> str:
    match = re.search(
        r'<div\s+id="statusWarning"[^>]*>.*?<p\s+class="intro">(.*?)</p>',
        text,
        flags=re.DOTALL,
    )
    if not match:
        return ""
    return _html_text(match.group(1))


def _timeline_version_dates(text: str) -> tuple[str, ...]:
    timeline_match = re.search(
        r'<div\s+id="timelineData"[^>]*>(.*?)</div>\s*</div>',
        text,
        flags=re.DOTALL,
    )
    if not timeline_match:
        return ()
    return _unique(re.findall(r"/(\d{4}-\d{2}-\d{2})(?:[/?#\"])", timeline_match.group(1)))


def _current_timeline_date(text: str) -> str:
    match = re.search(
        r'class="currentVersion[^"]*".*?/(\d{4}-\d{2}-\d{2})(?:[/?#\"])',
        text,
        flags=re.DOTALL,
    )
    return match.group(1) if match else ""


def _current_timeline_source_xml_url(current_page_url: str, current_date: str) -> str:
    if not current_page_url or not current_date:
        return ""
    base = current_page_url.split("?", 1)[0].rstrip("/")
    return f"{base}/{current_date}/data.xml"


def _html_text(fragment: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", fragment)
    return _squash(html.unescape(without_tags))


def _fetch_url(url: str) -> tuple[str, int, str, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": _FETCH_USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        content_type = str(response.headers.get("Content-Type") or "")
        return response.geturl(), int(response.status), content_type, body


def _missing_standalone_evidence(
    *,
    operation_evidence: Sequence[PublicOperationEvidence],
    public_snapshots: Sequence[PublicSnapshot],
    fetch_public_snapshots: bool,
) -> tuple[str, ...]:
    missing: list[str] = []
    if not operation_evidence:
        missing.append("amending_source_operation_fragment")
    if fetch_public_snapshots:
        roles = {snapshot.role for snapshot in public_snapshots}
        for role in ("current_page", "enacted_source_xml", "current_source_xml"):
            if role not in roles:
                missing.append(f"{role}_snapshot")
    else:
        missing.append("public_response_snapshots")
    return tuple(missing)


def _preview(body: bytes, limit: int = 500) -> str:
    text = body.decode("utf-8", errors="replace")
    return _squash(text)[:limit]


def _squash(value: str) -> str:
    return " ".join(value.split())


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "snapshot"


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(item) for item in value if str(item))


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _emit_json(packets: Sequence[PublicDivergencePacket]) -> str:
    payload = {
        "report_kind": "uk_public_consolidation_verification_packets.v1",
        "truth_claim": "public_review_packet_not_legal_conclusion",
        "source_truth_claims": False,
        "automated_consolidation_claims": False,
        "agreement_claims": False,
        "summary": {
            "packet_count": len(packets),
            "forbidden_shortcuts": list(_FORBIDDEN_SHORTCUTS),
            "packets_with_snapshots": sum(1 for packet in packets if packet.public_snapshots),
            "packets_with_current_page_status_witnesses": sum(
                1 for packet in packets if packet.current_page_status_witnesses
            ),
            "current_timeline_source_xml_snapshot_count": sum(
                1
                for packet in packets
                for snapshot in packet.public_snapshots
                if snapshot.role == "current_timeline_source_xml"
            ),
            "packets_missing_standalone_evidence": sum(
                1 for packet in packets if packet.missing_standalone_evidence
            ),
        },
        "rows": [asdict(packet) for packet in packets],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build public verification packets for UK consolidation review leads."
    )
    parser.add_argument("candidates", type=Path, help="uk_oracle_suspect_candidates JSON")
    parser.add_argument(
        "--supplement",
        type=Path,
        help="Optional evidence JSON carrying current URLs and amending-source fragments",
    )
    parser.add_argument("--out", type=Path, help="Write JSON to this path")
    parser.add_argument("--limit", type=int, default=0, help="Maximum packets to emit")
    parser.add_argument(
        "--statute-id",
        action="append",
        default=[],
        help="Restrict to one statute id; repeatable",
    )
    parser.add_argument(
        "--fetch-public-snapshots",
        action="store_true",
        help="Fetch public URLs and write exact response bytes to --snapshot-dir",
    )
    parser.add_argument(
        "--fetch-current-timeline-xml",
        action="store_true",
        help=(
            "After fetching current pages, also fetch each page-declared current "
            "timeline XML URL as current_timeline_source_xml evidence."
        ),
    )
    parser.add_argument(
        "--require-standalone-evidence",
        action="store_true",
        help=(
            "Only emit packets whose operation evidence and fetched public "
            "snapshots are complete; --limit is applied after this filter."
        ),
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="Directory for fetched public response bytes",
    )
    args = parser.parse_args(argv)

    packets = load_packets(
        args.candidates,
        supplement_path=args.supplement,
        fetch_public_snapshots=args.fetch_public_snapshots,
        fetch_current_timeline_xml=args.fetch_current_timeline_xml,
        snapshot_dir=args.snapshot_dir,
        require_standalone_evidence=args.require_standalone_evidence,
        limit=args.limit,
        statute_ids=frozenset(args.statute_id),
    )
    payload = _emit_json(packets)
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
