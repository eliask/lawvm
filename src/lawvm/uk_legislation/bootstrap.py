from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml


USER_AGENT = "LawVM UK bootstrap/0.1 (+https://www.legislation.gov.uk/)"


@dataclass
class OpenAPISummary:
    name: str
    title: str
    version: str
    server_urls: list[str]
    path_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "version": self.version,
            "server_urls": self.server_urls,
            "path_count": self.path_count,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _extract_embedded_spec_json(text: str) -> dict[str, Any]:
    marker = "var spec = "
    start = text.find(marker)
    if start == -1:
        raise ValueError("No embedded Swagger spec found in HTML snapshot")
    i = start + len(marker)
    depth = 0
    in_string = False
    escaped = False
    json_start = None
    for pos in range(i, len(text)):
        ch = text[pos]
        if json_start is None:
            if ch.isspace():
                continue
            if ch != "{":
                raise ValueError("Embedded Swagger spec does not start with '{'")
            json_start = pos
            depth = 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[json_start : pos + 1])
    raise ValueError("Unterminated embedded Swagger spec")


def _load_openapi_snapshot(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("<"):
        return _extract_embedded_spec_json(text)
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} did not parse into an OpenAPI mapping")
    return loaded


def normalize_openapi() -> None:
    repo = _repo_root()
    openapi_dir = repo / "uk" / "openapi"
    normalized_dir = openapi_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[OpenAPISummary] = []
    for source in sorted(openapi_dir.glob("*.yaml")):
        spec = _load_openapi_snapshot(source)
        out_path = normalized_dir / f"{source.stem}.json"
        out_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        info = spec.get("info", {})
        servers = spec.get("servers", [])
        summaries.append(
            OpenAPISummary(
                name=source.stem,
                title=str(info.get("title", "")),
                version=str(info.get("version", "")),
                server_urls=[str(server.get("url", "")) for server in servers if isinstance(server, dict)],
                path_count=len(spec.get("paths", {})),
            )
        )

    index = {
        "generated_from": "uk/openapi/*.yaml",
        "specs": [summary.to_dict() for summary in summaries],
    }
    (normalized_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Normalized {len(summaries)} OpenAPI snapshots into {normalized_dir}")


def _safe_relative_path(path_text: str) -> Path:
    rel_path = Path(path_text)
    if rel_path.is_absolute():
        raise ValueError(f"Manifest path must be relative: {path_text}")
    normalized = Path(re.sub(r"/+", "/", rel_path.as_posix()))
    if any(part == ".." for part in normalized.parts):
        raise ValueError(f"Manifest path escapes repo root: {path_text}")
    return normalized


def _download(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request) as response:
        return response.read(), response.geturl()


def _load_xml_root(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def fetch_manifest(manifest_path: Path, dry_run: bool = False) -> int:
    repo = _repo_root()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    downloads = manifest.get("sources", [])
    if not isinstance(downloads, list):
        raise ValueError("Manifest 'sources' must be a list")

    failures = 0
    for source in downloads:
        label = source.get("label", source.get("id", "unknown"))
        artifacts = source.get("artifacts", [])
        print(f"[source] {label}")
        for artifact in artifacts:
            url = artifact["url"]
            rel_path = _safe_relative_path(artifact["path"])
            dest = repo / rel_path
            print(f"  - {url} -> {rel_path}")
            if dry_run:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                data, final_url = _download(url)
            except (HTTPError, URLError) as exc:
                failures += 1
                print(f"    ERROR: {exc}", file=sys.stderr)
                continue
            dest.write_bytes(data)
            meta = {
                "requested_url": url,
                "final_url": final_url,
                "bytes": len(data),
            }
            meta_path = dest.with_suffix(dest.suffix + ".meta.json")
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return failures


def _manifest_default() -> Path:
    return _repo_root() / "uk" / "manifests" / "pilot_sources.json"


def summarize_versions(paths: list[Path]) -> int:
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for path in paths:
        root = ET.parse(path).getroot()
        links = root.findall(".//atom:link", ns)
        versions = [
            link.get("title", "")
            for link in links
            if link.get("rel") == "http://purl.org/dc/terms/hasVersion"
        ]
        replaces = [
            link.get("title", "")
            for link in links
            if link.get("rel") == "http://purl.org/dc/terms/replaces"
        ]
        payload = {
            "path": str(path),
            "version_count": len(versions),
            "first_versions": versions[:5],
            "last_versions": versions[-5:],
            "current_replaces": replaces[0] if replaces else "",
        }
        print(json.dumps(payload, ensure_ascii=False))
    return 0


def fetch_effects_pages(seed_feed: Path, out_dir: Path, dry_run: bool = False) -> int:
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "leg": "http://www.legislation.gov.uk/namespaces/legislation",
    }
    root = _load_xml_root(seed_feed)
    total_pages_text = root.findtext("leg:totalPages", default="", namespaces=ns)
    total_pages = int(total_pages_text) if total_pages_text else 1
    self_url = ""
    for link in root.findall("atom:link", ns):
        if link.get("rel") == "self":
            self_url = link.get("href", "")
            break
    if not self_url:
        raise ValueError(f"No self link found in {seed_feed}")
    if "page=" in self_url:
        base_url = re.sub(r"([&?])page=\d+", "", self_url)
    else:
        base_url = self_url
    failures = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for page in range(2, total_pages + 1):
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}page={page}"
        dest = out_dir / f"page-{page}.feed"
        print(f"{url} -> {dest}")
        if dry_run:
            continue
        try:
            data, final_url = _download(url)
        except (HTTPError, URLError) as exc:
            failures += 1
            print(f"ERROR: {exc}", file=sys.stderr)
            continue
        dest.write_bytes(data)
        dest.with_suffix(dest.suffix + ".meta.json").write_text(
            json.dumps({"requested_url": url, "final_url": final_url, "bytes": len(data)}, indent=2) + "\n",
            encoding="utf-8",
        )
    return failures


def _parse_version_links(path: Path) -> dict[str, Any]:
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.parse(path).getroot()
    links = root.findall(".//atom:link", ns)
    versions = [
        {
            "label": link.get("title", ""),
            "href": link.get("href", ""),
        }
        for link in links
        if link.get("rel") == "http://purl.org/dc/terms/hasVersion"
    ]
    replaces = [
        {
            "label": link.get("title", ""),
            "href": link.get("href", ""),
        }
        for link in links
        if link.get("rel") == "http://purl.org/dc/terms/replaces"
    ]
    versions_sorted = sorted(versions, key=lambda item: item["label"])
    return {
        "path": str(path),
        "versions": versions_sorted,
        "current_replaces": replaces[0] if replaces else {},
    }


def _parse_in_force_dates(effect_el: ET.Element, ns: dict[str, str]) -> list[dict[str, Any]]:
    dates: list[dict[str, Any]] = []
    for in_force in effect_el.findall(".//ukm:InForceDates/ukm:InForce", ns):
        commencing_provisions = []
        for section in in_force.findall(".//ukm:CommencingProvisions/ukm:Section", ns):
            commencing_provisions.append(
                {
                    "text": (section.text or "").strip(),
                    "ref": section.get("Ref", ""),
                    "uri": section.get("URI", ""),
                }
            )
        dates.append(
            {
                "date": in_force.get("Date", ""),
                "applied": in_force.get("Applied", ""),
                "prospective": in_force.get("Prospective", ""),
                "qualification": in_force.get("Qualification", ""),
                "commencing_uri": in_force.get("CommencingURI", ""),
                "commencing_class": in_force.get("CommencingClass", ""),
                "commencing_year": in_force.get("CommencingYear", ""),
                "commencing_number": in_force.get("CommencingNumber", ""),
                "commencing_provisions": commencing_provisions,
            }
        )
    return dates


def _parse_effect_entries(path: Path) -> dict[str, Any]:
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "leg": "http://www.legislation.gov.uk/namespaces/legislation",
        "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
        "openSearch": "http://a9.com/-/spec/opensearch/1.1/",
    }
    root = ET.parse(path).getroot()
    page = root.findtext("leg:page", default="", namespaces=ns)
    total_pages = root.findtext("leg:totalPages", default="", namespaces=ns)
    total_results = root.findtext("openSearch:totalResults", default="", namespaces=ns)

    effects: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        effect = entry.find(".//ukm:Effect", ns)
        if effect is None:
            continue
        effects.append(
            {
                "effect_id": effect.get("EffectId", ""),
                "type": effect.get("Type", ""),
                "applied": effect.get("Applied", ""),
                "requires_applied": effect.get("RequiresApplied", ""),
                "modified": effect.get("Modified", ""),
                "applied_modified": effect.get("AppliedModified", ""),
                "affected": {
                    "title": effect.findtext("ukm:AffectedTitle", default="", namespaces=ns),
                    "class": effect.get("AffectedClass", ""),
                    "year": effect.get("AffectedYear", ""),
                    "number": effect.get("AffectedNumber", ""),
                    "uri": effect.get("AffectedURI", ""),
                    "provisions_text": effect.get("AffectedProvisions", ""),
                },
                "affecting": {
                    "title": effect.findtext("ukm:AffectingTitle", default="", namespaces=ns),
                    "class": effect.get("AffectingClass", ""),
                    "year": effect.get("AffectingYear", ""),
                    "number": effect.get("AffectingNumber", ""),
                    "uri": effect.get("AffectingURI", ""),
                    "provisions_text": effect.get("AffectingProvisions", ""),
                    "effects_extent": effect.get("AffectingEffectsExtent", ""),
                    "territorial_application": effect.get("AffectingTerritorialApplication", ""),
                },
                "in_force_dates": _parse_in_force_dates(effect, ns),
            }
        )
    return {
        "path": str(path),
        "page": int(page) if page else None,
        "total_pages": int(total_pages) if total_pages else None,
        "total_results": int(total_results) if total_results else None,
        "effects": effects,
    }


def _expand_effect_feed_inputs(effect_feeds: list[Path], effect_feed_dirs: list[Path]) -> list[Path]:
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in effect_feeds:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(path)
    for directory in effect_feed_dirs:
        for path in sorted(directory.glob("*.feed")):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(path)
    return ordered


def _increment_count(bucket: dict[str, int], key: str) -> None:
    if not key:
        return
    bucket[key] = bucket.get(key, 0) + 1


def _build_temporal_summary(
    versions: list[dict[str, str]],
    all_effects: list[dict[str, Any]],
    top_n: int = 15,
) -> dict[str, Any]:
    version_dates = [item["label"] for item in versions if item.get("label") and item["label"] != "enacted"]
    version_date_set = set(version_dates)
    modified_date_counts: dict[str, int] = {}
    applied_modified_date_counts: dict[str, int] = {}
    in_force_date_counts: dict[str, int] = {}
    matching_version_dates: dict[str, dict[str, int]] = {}

    for effect in all_effects:
        modified_date = effect.get("modified", "")[:10]
        applied_modified_date = effect.get("applied_modified", "")[:10]
        _increment_count(modified_date_counts, modified_date)
        _increment_count(applied_modified_date_counts, applied_modified_date)
        if modified_date in version_date_set:
            bucket = matching_version_dates.setdefault(modified_date, {"modified": 0, "in_force": 0})
            bucket["modified"] += 1
        if applied_modified_date in version_date_set:
            bucket = matching_version_dates.setdefault(applied_modified_date, {"modified": 0, "in_force": 0})
            bucket["modified"] += 1
        for in_force in effect.get("in_force_dates", []):
            date = in_force.get("date", "")
            _increment_count(in_force_date_counts, date)
            if date in version_date_set:
                bucket = matching_version_dates.setdefault(date, {"modified": 0, "in_force": 0})
                bucket["in_force"] += 1

    def top_counts(bucket: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"date": key, "count": value}
            for key, value in sorted(bucket.items(), key=lambda item: (-item[1], item[0]))[:top_n]
        ]

    matching_dates = [
        {
            "date": date,
            "modified_count": counts["modified"],
            "in_force_count": counts["in_force"],
            "total_count": counts["modified"] + counts["in_force"],
        }
        for date, counts in sorted(
            matching_version_dates.items(),
            key=lambda item: (-(item[1]["modified"] + item[1]["in_force"]), item[0]),
        )[:top_n]
    ]

    return {
        "version_dates": version_dates,
        "version_date_count": len(version_dates),
        "top_modified_dates": top_counts(modified_date_counts),
        "top_applied_modified_dates": top_counts(applied_modified_date_counts),
        "top_in_force_dates": top_counts(in_force_date_counts),
        "matching_version_dates": matching_dates,
    }


def _classify_transition(match_type_counts: dict[str, int]) -> dict[str, Any]:
    modified = match_type_counts.get("modified", 0)
    applied_modified = match_type_counts.get("applied_modified", 0)
    in_force = match_type_counts.get("in_force", 0)
    editorial_weight = modified + applied_modified

    if in_force == 0 and editorial_weight > 0:
        label = "editorial"
    elif in_force > 0 and editorial_weight == 0:
        label = "commencement_driven"
    elif in_force > 0 and editorial_weight > 0:
        if editorial_weight >= in_force * 3:
            label = "editorial"
        elif in_force >= editorial_weight * 2:
            label = "commencement_driven"
        else:
            label = "mixed"
    else:
        label = "unclear"

    return {
        "label": label,
        "editorial_weight": editorial_weight,
        "in_force_weight": in_force,
    }


def build_effects_graph(current_xml: Path, effect_feeds: list[Path], out: Path | None = None) -> int:
    version_data = _parse_version_links(current_xml)
    feed_summaries = [_parse_effect_entries(path) for path in effect_feeds]
    all_effects: list[dict[str, Any]] = []
    fetched_pages: list[int] = []
    total_pages = None
    total_results = None
    for feed in feed_summaries:
        all_effects.extend(feed["effects"])
        if feed["page"] is not None:
            fetched_pages.append(feed["page"])
        if feed["total_pages"] is not None:
            total_pages = feed["total_pages"]
        if feed["total_results"] is not None:
            total_results = feed["total_results"]

    by_affecting_act: dict[str, dict[str, Any]] = {}
    effect_type_counts: dict[str, int] = {}
    for effect in all_effects:
        effect_type = effect["type"]
        effect_type_counts[effect_type] = effect_type_counts.get(effect_type, 0) + 1
        affecting = effect["affecting"]
        act_key = f"{affecting['class']}:{affecting['year']}:{affecting['number']}"
        bucket = by_affecting_act.setdefault(
            act_key,
            {
                "title": affecting["title"],
                "class": affecting["class"],
                "year": affecting["year"],
                "number": affecting["number"],
                "uri": affecting["uri"],
                "effect_count": 0,
                "effect_types": {},
                "affected_provisions": set(),
                "sample_effects": [],
            },
        )
        bucket["effect_count"] += 1
        bucket["effect_types"][effect_type] = bucket["effect_types"].get(effect_type, 0) + 1
        if effect["affected"]["provisions_text"]:
            bucket["affected_provisions"].add(effect["affected"]["provisions_text"])
        if len(bucket["sample_effects"]) < 5:
            bucket["sample_effects"].append(
                {
                    "effect_id": effect["effect_id"],
                    "type": effect["type"],
                    "affected_provisions": effect["affected"]["provisions_text"],
                    "affecting_provisions": effect["affecting"]["provisions_text"],
                    "applied": effect["applied"],
                    "modified": effect["modified"],
                    "in_force_dates": effect["in_force_dates"][:3],
                }
            )

    top_affecting_acts = sorted(
        (
            {
                **value,
                "affected_provisions": sorted(value["affected_provisions"]),
            }
            for value in by_affecting_act.values()
        ),
        key=lambda item: (-item["effect_count"], item["year"], item["number"]),
    )

    graph = {
        "kind": "uk_effects_version_graph",
        "current_xml": str(current_xml),
        "effect_feeds": [str(path) for path in effect_feeds],
        "coverage": {
            "fetched_pages": sorted(fetched_pages),
            "total_pages": total_pages,
            "fetched_effect_count": len(all_effects),
            "reported_total_results": total_results,
            "partial_feed": bool(total_pages and len(set(fetched_pages)) < total_pages),
        },
        "versions": version_data["versions"],
        "version_count": len(version_data["versions"]),
        "current_replaces": version_data["current_replaces"],
        "effect_count": len(all_effects),
        "effect_type_counts": effect_type_counts,
        "affecting_act_count": len(by_affecting_act),
        "temporal_summary": _build_temporal_summary(version_data["versions"], all_effects),
        "top_affecting_acts": top_affecting_acts[:25],
        "sample_effects": all_effects[:20],
    }
    text = json.dumps(graph, indent=2, ensure_ascii=False) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(json.dumps(graph, ensure_ascii=False))
    return 0


def build_version_transitions(current_xml: Path, effect_feeds: list[Path], out: Path | None = None) -> int:
    version_data = _parse_version_links(current_xml)
    feed_summaries = [_parse_effect_entries(path) for path in effect_feeds]
    all_effects: list[dict[str, Any]] = []
    for feed in feed_summaries:
        all_effects.extend(feed["effects"])

    version_dates = [item for item in version_data["versions"] if item.get("label") and item["label"] != "enacted"]
    transitions: list[dict[str, Any]] = []

    for index, version in enumerate(version_dates):
        date = version["label"]
        previous_version = version_dates[index - 1] if index > 0 else None
        next_version = version_dates[index + 1] if index + 1 < len(version_dates) else None
        matching_effects: list[dict[str, Any]] = []
        affecting_counts: dict[str, dict[str, Any]] = {}

        for effect in all_effects:
            matched_on: list[str] = []
            if effect.get("modified", "")[:10] == date:
                matched_on.append("modified")
            if effect.get("applied_modified", "")[:10] == date:
                matched_on.append("applied_modified")
            in_force_hits = sum(1 for item in effect.get("in_force_dates", []) if item.get("date", "") == date)
            if in_force_hits:
                matched_on.append("in_force")
            if not matched_on:
                continue

            matching_effects.append(effect)
            affecting = effect["affecting"]
            act_key = f"{affecting['class']}:{affecting['year']}:{affecting['number']}"
            bucket = affecting_counts.setdefault(
                act_key,
                {
                    "title": affecting["title"],
                    "class": affecting["class"],
                    "year": affecting["year"],
                    "number": affecting["number"],
                    "uri": affecting["uri"],
                    "effect_count": 0,
                    "match_types": {"modified": 0, "applied_modified": 0, "in_force": 0},
                },
            )
            bucket["effect_count"] += 1
            for match_type in matched_on:
                bucket["match_types"][match_type] += 1

        top_affecting = sorted(
            affecting_counts.values(),
            key=lambda item: (-item["effect_count"], item["year"], item["number"]),
        )[:10]

        match_type_counts = {
            "modified": sum(1 for effect in matching_effects if effect.get("modified", "")[:10] == date),
            "applied_modified": sum(1 for effect in matching_effects if effect.get("applied_modified", "")[:10] == date),
            "in_force": sum(
                1 for effect in matching_effects for item in effect.get("in_force_dates", []) if item.get("date", "") == date
            ),
        }

        transitions.append(
            {
                "date": date,
                "version_href": version["href"],
                "previous_version_date": previous_version["label"] if previous_version else None,
                "next_version_date": next_version["label"] if next_version else None,
                "matching_effect_count": len(matching_effects),
                "match_type_counts": match_type_counts,
                "classification": _classify_transition(match_type_counts),
                "top_affecting_acts": top_affecting,
            }
        )

    payload = {
        "kind": "uk_version_transition_summary",
        "current_xml": str(current_xml),
        "effect_feeds": [str(path) for path in effect_feeds],
        "version_count": len(version_dates),
        "transitions": [item for item in transitions if item["matching_effect_count"] > 0],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def compare_effects_graphs(paths: list[Path], out: Path | None = None) -> int:
    summaries: list[dict[str, Any]] = []
    for path in paths:
        graph = json.loads(path.read_text(encoding="utf-8"))
        top_affecting = graph.get("top_affecting_acts", [])
        summaries.append(
            {
                "path": str(path),
                "version_count": graph.get("version_count", 0),
                "effect_count": graph.get("effect_count", 0),
                "affecting_act_count": graph.get("affecting_act_count", 0),
                "partial_feed": graph.get("coverage", {}).get("partial_feed", True),
                "top_affecting_act": top_affecting[0]["title"] if top_affecting else "",
                "top_affecting_effect_count": top_affecting[0]["effect_count"] if top_affecting else 0,
                "effect_to_version_ratio": round(
                    graph.get("effect_count", 0) / max(graph.get("version_count", 1), 1),
                    3,
                ),
            }
        )
    payload = {
        "kind": "uk_effects_graph_comparison",
        "graph_count": len(summaries),
        "graphs": summaries,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LawVM UK bootstrap utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "normalize-openapi",
        help="Normalize downloaded legislation.gov.uk OpenAPI snapshots into JSON specs",
    )

    fetch = subparsers.add_parser(
        "fetch-manifest",
        help="Download the sample UK legislation set described by a manifest",
    )
    fetch.add_argument(
        "--manifest",
        type=Path,
        default=_manifest_default(),
        help="Path to a JSON manifest of URLs to download",
    )
    fetch.add_argument("--dry-run", action="store_true", help="Print planned downloads only")

    fetch_effects = subparsers.add_parser(
        "fetch-effects-pages",
        help="Fetch remaining pages for a local affected-statute effects feed",
    )
    fetch_effects.add_argument("--seed-feed", type=Path, required=True, help="Local page-1 affected-statute feed")
    fetch_effects.add_argument("--out-dir", type=Path, required=True, help="Directory for additional page-N feeds")
    fetch_effects.add_argument("--dry-run", action="store_true")

    summarize = subparsers.add_parser(
        "summarize-versions",
        help="Summarize the version-chain metadata exposed by downloaded UK legislation XML",
    )
    summarize.add_argument("paths", nargs="+", type=Path, help="One or more downloaded legislation XML files")

    graph = subparsers.add_parser(
        "build-effects-graph",
        help="Build a joined version/effects graph artifact from local UK XML and effects feeds",
    )
    graph.add_argument("--current-xml", type=Path, required=True, help="Current legislation XML with hasVersion links")
    graph.add_argument("--effect-feed", dest="effect_feeds", nargs="+", type=Path, required=True, help="One or more local affected-statute effects feeds")
    graph.add_argument("--effect-feed-dir", dest="effect_feed_dirs", nargs="*", type=Path, default=[], help="Optional directories containing additional page-N .feed files")
    graph.add_argument("--out", type=Path, help="Optional JSON output path")

    compare = subparsers.add_parser(
        "compare-effects-graphs",
        help="Compare one or more previously compiled UK effects/version graph artifacts",
    )
    compare.add_argument("paths", nargs="+", type=Path, help="Compiled graph JSON paths")
    compare.add_argument("--out", type=Path, help="Optional JSON output path")

    transitions = subparsers.add_parser(
        "build-version-transitions",
        help="Build per-version-date transition candidates from local UK XML and effects feeds",
    )
    transitions.add_argument("--current-xml", type=Path, required=True, help="Current legislation XML with hasVersion links")
    transitions.add_argument("--effect-feed", dest="effect_feeds", nargs="+", type=Path, required=True, help="One or more local affected-statute effects feeds")
    transitions.add_argument("--effect-feed-dir", dest="effect_feed_dirs", nargs="*", type=Path, default=[], help="Optional directories containing additional page-N .feed files")
    transitions.add_argument("--out", type=Path, help="Optional JSON output path")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "normalize-openapi":
        normalize_openapi()
        return 0
    if args.command == "fetch-manifest":
        failures = fetch_manifest(args.manifest, dry_run=args.dry_run)
        if failures:
            print(f"Completed with {failures} failed download(s)", file=sys.stderr)
            return 1
        return 0
    if args.command == "fetch-effects-pages":
        failures = fetch_effects_pages(args.seed_feed, args.out_dir, dry_run=args.dry_run)
        if failures:
            print(f"Completed with {failures} failed download(s)", file=sys.stderr)
            return 1
        return 0
    if args.command == "summarize-versions":
        return summarize_versions(args.paths)
    if args.command == "build-effects-graph":
        effect_feeds = _expand_effect_feed_inputs(args.effect_feeds, args.effect_feed_dirs)
        return build_effects_graph(args.current_xml, effect_feeds, out=args.out)
    if args.command == "compare-effects-graphs":
        return compare_effects_graphs(args.paths, out=args.out)
    if args.command == "build-version-transitions":
        effect_feeds = _expand_effect_feed_inputs(args.effect_feeds, args.effect_feed_dirs)
        return build_version_transitions(args.current_xml, effect_feeds, out=args.out)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
