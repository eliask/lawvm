from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "http://publications.europa.eu/resource"
USER_AGENT = "LawVM EU Cellar/0.1 (+https://op.europa.eu/en/web/cellar/home)"
DEFAULT_TIMEOUT_S = 30


@dataclass
class NoticeRequest:
    celex: str
    notice_format: str
    notice_type: str
    decode_language: str
    accept_language: str | None = None
    filter_in_notice_only: bool | None = None

    def accept_header(self) -> str:
        if self.notice_format == "xml":
            return f"application/xml;notice={self.notice_type}"
        if self.notice_format == "rdf":
            rdf_type = self.notice_type
            if rdf_type == "object":
                rdf_type = "non-inferred"
            elif rdf_type == "tree":
                rdf_type = "tree"
            return f"application/rdf+xml;notice={rdf_type}"
        raise ValueError(f"Unsupported notice format: {self.notice_format}")

    def url(self) -> str:
        params = {"language": self.decode_language}
        if self.filter_in_notice_only is not None:
            params["filter"] = "true" if self.filter_in_notice_only else "false"

        # If celex is already a full URI, use it directly (with params)
        if self.celex.startswith("http"):
            base = self.celex
            return f"{base}?{urlencode(params)}"

        # If it looks like a Cellar UUID (36 chars with hyphens), use cellar resource path
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', self.celex):
            return f"{BASE_URL}/cellar/{self.celex}?{urlencode(params)}"

        return f"{BASE_URL}/celex/{self.celex}?{urlencode(params)}"


@dataclass(frozen=True)
class ManifestFetchReport:
    fetched_count: int
    failed_count: int
    failed_requests: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_count": self.fetched_count,
            "failed_count": self.failed_count,
            "failed_requests": [dict(row) for row in self.failed_requests],
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _safe_relative_path(path_text: str) -> Path:
    rel_path = Path(path_text)
    if rel_path.is_absolute():
        raise ValueError(f"Manifest path must be relative: {path_text}")
    normalized = Path(re.sub(r"/+", "/", rel_path.as_posix()))
    if any(part == ".." for part in normalized.parts):
        raise ValueError(f"Manifest path escapes repo root: {path_text}")
    return normalized


def _request_notice(notice: NoticeRequest, timeout_s: int = DEFAULT_TIMEOUT_S) -> tuple[bytes, dict[str, Any]]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": notice.accept_header(),
    }
    if notice.accept_language:
        headers["Accept-Language"] = notice.accept_language
    request = Request(notice.url(), headers=headers)
    with urlopen(request, timeout=timeout_s) as response:
        data = response.read()
        meta = {
            "requested_url": notice.url(),
            "final_url": response.geturl(),
            "status": getattr(response, "status", None),
            "bytes": len(data),
            "content_type": response.headers.get("Content-Type", ""),
            "accept": headers["Accept"],
            "accept_language": headers.get("Accept-Language", ""),
            "timeout_s": timeout_s,
        }
        return data, meta


def _request_url(url: str, timeout_s: int = DEFAULT_TIMEOUT_S, accept: str | None = None) -> tuple[bytes, dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_s) as response:
        data = response.read()
        meta = {
            "requested_url": url,
            "final_url": response.geturl(),
            "status": getattr(response, "status", None),
            "bytes": len(data),
            "content_type": response.headers.get("Content-Type", ""),
            "accept": headers.get("Accept", ""),
            "timeout_s": timeout_s,
        }
        return data, meta


def _extract_urls_from_xml(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    urls: set[str] = set()
    for el in root.iter():
        for value in el.attrib.values():
            if isinstance(value, str) and value.startswith("http"):
                urls.add(value)
        if el.text and "http" in el.text:
            for token in re.findall(r"https?://[^\s<>\"]+", el.text):
                urls.add(token)
    doc_urls = sorted(url for url in urls if "/DOC_" in url)
    cellar_urls = sorted(url for url in urls if "/resource/cellar/" in url or "/resource/celex/" in url)
    return {
        "root_tag": root.tag,
        "url_count": len(urls),
        "cellar_urls": cellar_urls[:50],
        "doc_urls": doc_urls[:50],
    }


def _child_text(el: ET.Element | None, name: str) -> str:
    if el is None:
        return ""
    child = el.find(name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _iter_notice_entities(root: ET.Element, tag: str) -> list[ET.Element]:
    work = root.find(tag)
    if work is None:
        return []
    return list(work)


def _parse_sameas(el: ET.Element) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for sameas in el.findall("SAMEAS"):
        uri = sameas.find("URI")
        if uri is None:
            continue
        items.append(
            {
                "value": _child_text(uri, "VALUE"),
                "identifier": _child_text(uri, "IDENTIFIER"),
                "type": _child_text(uri, "TYPE"),
            }
        )
    return items


def _parse_uriish(el: ET.Element | None) -> dict[str, str]:
    if el is None:
        return {}
    return {
        "value": _child_text(el, "VALUE"),
        "identifier": _child_text(el, "IDENTIFIER"),
        "type": _child_text(el, "TYPE"),
    }


def _group_sameas(items: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for item in items:
        grouped.setdefault(item.get("type", ""), []).append(item)
    return grouped


def _parse_expression(el: ET.Element) -> dict[str, Any]:
    expr_uri = _parse_uriish(el.find("URI"))
    languages: list[dict[str, str]] = []
    for lang in el.findall("EXPRESSION_USES_LANGUAGE"):
        languages.append(
            {
                "op_code": _child_text(lang, "OP-CODE"),
                "identifier": _child_text(lang, "IDENTIFIER"),
                "label": _child_text(lang, "PREFLABEL"),
            }
        )
    return {
        "uri": expr_uri,
        "sameas": _parse_sameas(el),
        "languages": languages,
    }


def _parse_manifestation(el: ET.Element) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in el.findall("MANIFESTATION_HAS_ITEM"):
        items.append(
            {
                "uri": _parse_uriish(item.find("URI")),
                "sameas": _parse_sameas(item),
            }
        )
    return {
        "uri": _parse_uriish(el.find("URI")),
        "sameas": _parse_sameas(el),
        "manifestation_type": el.attrib.get("manifestation-type", ""),
        "items": items,
    }


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        uri = record.get("uri", {})
        key = (uri.get("type", ""), uri.get("value", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _expression_language_code(el: ET.Element) -> str:
    for lang in el.findall("EXPRESSION_USES_LANGUAGE"):
        code = _child_text(lang, "IDENTIFIER") or _child_text(lang, "OP-CODE")
        if code:
            return code.upper()
    lang_text = _child_text(el, "LANG")
    if lang_text:
        return lang_text.upper()
    return ""


def _manifestation_items(el: ET.Element) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in el.findall("MANIFESTATION_HAS_ITEM"):
        items.append(
            {
                "uri": _parse_uriish(item.find("URI")),
                "sameas": _parse_sameas(item),
            }
        )
    return items


def list_manifestation_options(tree_notice_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(tree_notice_path).getroot()

    # 1. Broadly collect all manifests by URI (if available)
    manifests_by_uri: dict[str, ET.Element] = {}
    all_mans: list[ET.Element] = list(root.iter("MANIFESTATION"))
    for manifestation in all_mans:
        uri_el = manifestation.find(".//URI") # Flexible search
        if uri_el is not None:
            v = _child_text(uri_el, "VALUE")
            if v: manifests_by_uri[v] = manifestation

    options: list[dict[str, Any]] = []

    # 2. Extract options by traversing EXPRESSIONs globally
    for expr in root.iter("EXPRESSION"):
        lang = _expression_language_code(expr)
        if not lang: continue

        expr_uri = _parse_uriish(expr.find(".//URI")) # Flexible search

        for link in expr.findall("EXPRESSION_MANIFESTED_BY_MANIFESTATION"):
            # The URI is often under SAMEAS/URI
            man_link_uri_node = link.find(".//URI")
            if man_link_uri_node is None: continue

            man_link_uri = _parse_uriish(man_link_uri_node)
            man_uri_val = man_link_uri.get("value", "")
            if not man_uri_val: continue

            # Try linking by absolute URI first
            manifestation = manifests_by_uri.get(man_uri_val)

            # Fallback 1: link by type within the SAME container if URI is missing on the manifest node
            if manifestation is None:
                man_uri_lower = man_uri_val.lower()
                want_type = ""
                if ".xhtml" in man_uri_lower: want_type = "xhtml"
                elif ".fmx4" in man_uri_lower: want_type = "fmx4"
                elif ".pdf" in man_uri_lower: want_type = "pdf"
                else:
                    want_type = man_uri_val.split(".")[-1].lower()
                    if want_type in ("eng", "fin", "fra"):  # common lang suffix
                        want_type = "xhtml" # heuristic

                if want_type:
                    matching_mans = [m for m in all_mans if m.attrib.get("manifestation-type", "").lower() == want_type]
                    if matching_mans:
                        manifestation = matching_mans[0]

            if manifestation is not None:
                items = _manifestation_items(manifestation)
                options.append({
                    "language": lang,
                    "expression_uri": expr_uri,
                    "manifestation_uri": man_link_uri,
                    "manifestation_type": manifestation.attrib.get("manifestation-type", ""),
                    "sameas": _parse_sameas(manifestation),
                    "items": items,
                })

    return options


def select_manifestation_option(
    tree_notice_path: Path,
    language: str,
    manifestation_type: str,
) -> dict[str, Any]:
    want_language = language.upper()
    want_type = manifestation_type.lower()
    options = list_manifestation_options(tree_notice_path)
    for option in options:
        if option["language"] == want_language and option["manifestation_type"].lower() == want_type:
            return option
    raise ValueError(
        f"No manifestation found for language={language!r} format={manifestation_type!r} in {tree_notice_path}"
    )


def summarize_notice(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    work = root.find("WORK")
    if work is None:
        raise ValueError(f"No WORK element found in {path}")

    work_uri = _parse_uriish(work.find("URI"))
    sameas = _parse_sameas(work)
    grouped_sameas = _group_sameas(sameas)

    expressions: list[dict[str, Any]] = [_parse_expression(el) for el in root.findall("EXPRESSION")]
    manifestations: list[dict[str, Any]] = [_parse_manifestation(el) for el in root.findall("MANIFESTATION")]
    citations: list[dict[str, Any]] = []
    implementing_measures: list[dict[str, Any]] = []

    have_top_level_expressions = bool(expressions)
    for child in work:
        tag = child.tag
        if tag == "WORK_HAS_EXPRESSION" and not have_top_level_expressions:
            expressions.append(
                {
                    "uri": _parse_uriish(child.find("URI")),
                    "sameas": _parse_sameas(child),
                    "languages": [],
                }
            )
        elif tag.endswith("CITES_WORK"):
            citations.append(
                {
                    "relation": tag,
                    "uri": _parse_uriish(child.find("URI")),
                    "sameas": _parse_sameas(child),
                }
            )
        elif "IMPLEMENTED_BY_MEASURE_NATIONAL_IMPLEMENTING" in tag:
            implementing_measures.append(
                {
                    "relation": tag,
                    "uri": _parse_uriish(child.find("URI")),
                    "sameas": _parse_sameas(child),
                }
            )
    expressions = _dedupe_records(expressions)
    manifestations = _dedupe_records(manifestations)

    celex_ids = [item["identifier"] for item in grouped_sameas.get("celex", []) if item.get("identifier")]
    eli_ids = [item["value"] for item in grouped_sameas.get("eli", []) if item.get("value")]

    manifestation_formats: dict[str, int] = {}
    doc_endpoints: list[str] = []
    expression_languages: dict[str, int] = {}
    for man in manifestations:
        man_type = man.get("manifestation_type", "")
        if man_type:
            manifestation_formats[man_type] = manifestation_formats.get(man_type, 0) + 1
        for same in man["sameas"]:
            ident = same.get("identifier", "")
            value = same.get("value", "")
            if ident:
                suffix = ident.split(".")[-1] if "." in ident else ident
                manifestation_formats[suffix] = manifestation_formats.get(suffix, 0) + 1
            if "/DOC_" in value:
                doc_endpoints.append(value)
        if "/DOC_" in man["uri"].get("value", ""):
            doc_endpoints.append(man["uri"]["value"])
        for item in man.get("items", []):
            item_uri = item["uri"].get("value", "")
            if "/DOC_" in item_uri:
                doc_endpoints.append(item_uri)
            for same in item["sameas"]:
                value = same.get("value", "")
                ident = same.get("identifier", "")
                if ident:
                    suffix = ident.split(".")[-1] if "." in ident else ident
                    manifestation_formats[suffix] = manifestation_formats.get(suffix, 0) + 1
                if "/DOC_" in value:
                    doc_endpoints.append(value)
    for expr in expressions:
        for language in expr.get("languages", []):
            key = language.get("identifier") or language.get("op_code") or ""
            if key:
                expression_languages[key] = expression_languages.get(key, 0) + 1

    summary = {
        "path": str(path),
        "notice_type": root.attrib.get("type", ""),
        "decoding": root.attrib.get("decoding", ""),
        "work_uri": work_uri,
        "celex": celex_ids[0] if celex_ids else "",
        "eli": eli_ids[0] if eli_ids else "",
        "sameas": grouped_sameas,
        "expression_count": len(expressions),
        "expression_languages": expression_languages,
        "manifestation_count": len(manifestations),
        "manifestation_formats": manifestation_formats,
        "doc_endpoint_count": len(set(doc_endpoints)),
        "sample_doc_endpoints": sorted(set(doc_endpoints))[:20],
        "sample_manifestations": manifestations[:10],
        "citation_count": len(citations),
        "sample_citations": citations[:20],
        "implementing_measure_count": len(implementing_measures),
        "sample_implementing_measures": implementing_measures[:20],
    }
    return summary


def summarize_notices(paths: list[Path], out_dir: Path | None = None) -> int:
    for path in paths:
        summary = summarize_notice(path)
        print(json.dumps(summary, ensure_ascii=False))
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            celex = summary.get("celex") or path.stem
            out_path = out_dir / f"{celex}__{summary.get('notice_type','notice')}.json"
            out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def summarize_payload_bytes(data: bytes, content_type: str = "", path_hint: str = "") -> dict[str, Any]:
    summary: dict[str, Any] = {
        "bytes": len(data),
        "content_type": content_type,
        "path_hint": path_hint,
    }
    lowered_hint = path_hint.lower()
    if zipfile.is_zipfile(BytesIO(data)) or lowered_hint.endswith(".zip"):
        summary["kind"] = "zip"
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = zf.namelist()
            summary["entry_count"] = len(names)
            summary["entries"] = names[:30]
        return summary
    text = data[:500_000].decode("utf-8", errors="replace")
    if "<html" in text.lower() or lowered_hint.endswith(".html") or lowered_hint.endswith(".xhtml"):
        summary["kind"] = "html"
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        summary["title"] = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        summary["article_mentions"] = len(re.findall(r">\s*Article\s+\d+", text, flags=re.IGNORECASE))
        summary["recital_mentions"] = len(re.findall(r">\s*\(\d+\)\s*<", text))
        summary["eu_doc_refs"] = len(re.findall(r"CELEX:", text))
        return summary
    if text.lstrip().startswith("<"):
        summary["kind"] = "xml"
        try:
            root = ET.fromstring(data)
            summary["root_tag"] = root.tag
            summary["element_count"] = sum(1 for _ in root.iter())
        except ET.ParseError as exc:
            summary["parse_error"] = str(exc)
        return summary
    summary["kind"] = "binary"
    return summary


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _element_text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return _normalize_text("".join(str(_t) for _t in el.itertext()))


def extract_fmx4_structure(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        act_name = next((name for name in names if name.endswith(".xml") and ".doc." not in name and "01000101" in name), "")
        if not act_name:
            act_name = next((name for name in names if name.endswith(".xml") and ".doc." not in name), "")
        if not act_name:
            raise ValueError(f"No main FMX4 XML found in {zip_path}")
        root = ET.fromstring(zf.read(act_name))
        if root.tag != "ACT":
            raise ValueError(f"Expected ACT root in {act_name}, got {root.tag}")

        title = _element_text(root.find("TITLE"))
        preamble = root.find("PREAMBLE")
        enacting_terms = root.find("ENACTING.TERMS")
        final = root.find("FINAL")
        annex_files = [name for name in names if name != act_name and name.endswith(".xml") and ".doc." not in name]
        preamble_considerations = preamble.findall("GR.CONSID/CONSID") if preamble is not None else []

        divisions: list[dict[str, Any]] = []
        article_count = 0
        if enacting_terms is not None:
            for division in enacting_terms.findall("DIVISION"):
                articles: list[dict[str, Any]] = []
                division_title = _element_text(division.find("TITLE"))
                for article in division.findall("ARTICLE"):
                    article_count += 1
                    paragraphs = []
                    for paragraph in article.findall("PARAG"):
                        paragraphs.append(
                            {
                                "identifier": paragraph.attrib.get("IDENTIFIER", ""),
                                "preview": _element_text(paragraph)[:280],
                            }
                        )
                    articles.append(
                        {
                            "article_label": _element_text(article.find("TI.ART")),
                            "article_title": _element_text(article.find("STI.ART")),
                            "paragraph_count": len(paragraphs),
                            "paragraphs": paragraphs[:5],
                        }
                    )
                divisions.append(
                    {
                        "title": division_title,
                        "article_count": len(articles),
                        "articles": articles[:10],
                    }
                )

        return {
            "path": str(zip_path),
            "kind": "fmx4_structure",
            "zip_entries": names,
            "main_act_file": act_name,
            "root_tag": root.tag,
            "title": title,
            "preamble_child_tags": [child.tag for child in list(preamble)[:20]] if preamble is not None else [],
            "preamble_recital_count": len(preamble_considerations),
            "final_child_tags": [child.tag for child in list(final)[:20]] if final is not None else [],
            "division_count": len(divisions),
            "article_count": article_count,
            "divisions": divisions,
            "annex_files": annex_files,
        }


def fetch_notice(args: argparse.Namespace) -> int:
    notice = NoticeRequest(
        celex=args.celex,
        notice_format=args.format,
        notice_type=args.notice,
        decode_language=args.language,
        accept_language=args.accept_language,
        filter_in_notice_only=args.in_notice_only,
    )
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        data, meta = _request_notice(notice, timeout_s=args.timeout)
    except (HTTPError, URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out.write_bytes(data)
    out.with_suffix(out.suffix + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False), flush=True)
    return 0


def fetch_manifestation(args: argparse.Namespace) -> int:
    try:
        option = select_manifestation_option(args.tree_notice, args.language, args.format)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    item = option["items"][0] if option["items"] else None
    if item is None:
        print("ERROR: Manifestation has no items", file=sys.stderr)
        return 1
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        data, meta = _request_url(item["uri"]["value"], timeout_s=args.timeout, accept=args.accept)
    except (HTTPError, URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out.write_bytes(data)
    payload_summary = summarize_payload_bytes(data, meta.get("content_type", ""), path_hint=out.name)
    combined = {
        "selection": {
            "language": option["language"],
            "manifestation_type": option["manifestation_type"],
            "expression_uri": option["expression_uri"],
            "manifestation_uri": option["manifestation_uri"],
            "item_uri": item["uri"],
        },
        "request": meta,
        "payload": payload_summary,
    }
    out.with_suffix(out.suffix + ".meta.json").write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(combined, ensure_ascii=False), flush=True)
    return 0


def _manifest_request_failure_row(
    *,
    source_label: str,
    celex: str,
    request_path: str,
    notice: NoticeRequest,
    exc: HTTPError | URLError,
) -> dict[str, Any]:
    return {
        "rule_id": "eu_cellar_manifest_request_failed",
        "phase": "acquisition",
        "family": "source_pathology",
        "source_label": source_label,
        "celex": celex,
        "request_path": request_path,
        "notice_url": notice.url(),
        "accept_header": notice.accept_header(),
        "error_type": exc.__class__.__name__,
        "error": str(exc),
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def fetch_manifest(manifest_path: Path, dry_run: bool = False) -> ManifestFetchReport:
    repo = _repo_root()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = manifest.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("Manifest 'sources' must be a list")

    failures = 0
    fetched = 0
    failed_requests: list[dict[str, Any]] = []
    for source in sources:
        label = source.get("label", source.get("id", "unknown"))
        celex = source["celex"]
        requests = source.get("requests", [])
        print(f"[source] {label}", flush=True)
        for req in requests:
            notice = NoticeRequest(
                celex=celex,
                notice_format=req.get("format", "xml"),
                notice_type=req.get("notice", "object"),
                decode_language=req.get("language", "eng"),
                accept_language=req.get("accept_language"),
                filter_in_notice_only=req.get("in_notice_only"),
            )
            rel_path = _safe_relative_path(req["path"])
            dest = repo / rel_path
            print(f"  - {notice.url()} -> {rel_path} [{notice.accept_header()}]", flush=True)
            if dry_run:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                data, meta = _request_notice(notice, timeout_s=req.get("timeout_s", DEFAULT_TIMEOUT_S))
            except (HTTPError, URLError) as exc:
                failures += 1
                failed_requests.append(
                    _manifest_request_failure_row(
                        source_label=str(label),
                        celex=str(celex),
                        request_path=str(rel_path),
                        notice=notice,
                        exc=exc,
                    )
                )
                print(f"    ERROR: {exc}", file=sys.stderr)
                continue
            dest.write_bytes(data)
            dest.with_suffix(dest.suffix + ".meta.json").write_text(
                json.dumps(meta, indent=2) + "\n",
                encoding="utf-8",
            )
            fetched += 1
    return ManifestFetchReport(fetched_count=fetched, failed_count=failures, failed_requests=tuple(failed_requests))


def inspect_xml(paths: list[Path]) -> int:
    for path in paths:
        summary = {"path": str(path)}
        try:
            summary.update(_extract_urls_from_xml(path))
        except Exception as exc:
            summary["error"] = str(exc)
        print(json.dumps(summary, ensure_ascii=False))
    return 0


def inspect_payload(paths: list[Path]) -> int:
    for path in paths:
        summary = {"path": str(path)}
        try:
            summary.update(summarize_payload_bytes(path.read_bytes(), path_hint=path.name))
        except Exception as exc:
            summary["error"] = str(exc)
        print(json.dumps(summary, ensure_ascii=False))
    return 0


def extract_fmx4_structure_cmd(args: argparse.Namespace) -> int:
    summary = extract_fmx4_structure(args.zip_path)
    text = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LawVM EU Cellar utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_notice_parser = subparsers.add_parser(
        "fetch-notice",
        help="Fetch one official Cellar metadata notice by CELEX",
    )
    fetch_notice_parser.add_argument("--celex", required=True)
    fetch_notice_parser.add_argument("--format", choices=["xml", "rdf"], default="xml")
    fetch_notice_parser.add_argument("--notice", choices=["object", "branch", "tree"], default="object")
    fetch_notice_parser.add_argument("--language", default="eng", help="Decoding language, ISO 639-3, e.g. eng or fin")
    fetch_notice_parser.add_argument("--accept-language", help="Accept-Language for branch notice, ISO 639-3")
    fetch_notice_parser.add_argument("--in-notice-only", action="store_true")
    fetch_notice_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    fetch_notice_parser.add_argument("--out", type=Path, required=True)

    fetch_manifest_parser = subparsers.add_parser(
        "fetch-manifest",
        help="Fetch a set of Cellar notices from a manifest",
    )
    fetch_manifest_parser.add_argument("--manifest", type=Path, required=True)
    fetch_manifest_parser.add_argument("--dry-run", action="store_true")
    fetch_manifest_parser.add_argument(
        "--failures-jsonl",
        type=Path,
        help="write structured acquisition failure rows for failed manifest requests",
    )

    fetch_manifestation_parser = subparsers.add_parser(
        "fetch-manifestation",
        help="Fetch one manifestation payload from a Cellar tree notice",
    )
    fetch_manifestation_parser.add_argument("--tree-notice", type=Path, required=True)
    fetch_manifestation_parser.add_argument("--language", required=True, help="Language code such as ENG or FIN")
    fetch_manifestation_parser.add_argument("--format", required=True, help="Manifestation type such as xhtml or fmx4")
    fetch_manifestation_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    fetch_manifestation_parser.add_argument("--accept")
    fetch_manifestation_parser.add_argument("--out", type=Path, required=True)

    inspect_parser = subparsers.add_parser(
        "inspect-xml",
        help="Inspect downloaded XML notices for cellar/doc URLs",
    )
    inspect_parser.add_argument("paths", nargs="+", type=Path)

    inspect_payload_parser = subparsers.add_parser(
        "inspect-payload",
        help="Inspect downloaded EU payload files",
    )
    inspect_payload_parser.add_argument("paths", nargs="+", type=Path)

    extract_fmx4_parser = subparsers.add_parser(
        "extract-fmx4-structure",
        help="Extract a minimal structural summary from an FMX4 payload zip",
    )
    extract_fmx4_parser.add_argument("zip_path", type=Path)
    extract_fmx4_parser.add_argument("--out", type=Path)

    summarize_parser = subparsers.add_parser(
        "summarize-notices",
        help="Summarize Cellar XML notices into stable JSON source records",
    )
    summarize_parser.add_argument("paths", nargs="+", type=Path)
    summarize_parser.add_argument("--out-dir", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "fetch-notice":
        return fetch_notice(args)
    if args.command == "fetch-manifest":
        report = fetch_manifest(args.manifest, dry_run=args.dry_run)
        if args.failures_jsonl:
            args.failures_jsonl.parent.mkdir(parents=True, exist_ok=True)
            args.failures_jsonl.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in report.failed_requests),
                encoding="utf-8",
            )
        if report.failed_count:
            print(f"Completed with {report.failed_count} failed fetch(es)", file=sys.stderr)
            return 1
        return 0
    if args.command == "fetch-manifestation":
        return fetch_manifestation(args)
    if args.command == "inspect-xml":
        return inspect_xml(args.paths)
    if args.command == "inspect-payload":
        return inspect_payload(args.paths)
    if args.command == "extract-fmx4-structure":
        return extract_fmx4_structure_cmd(args)
    if args.command == "summarize-notices":
        return summarize_notices(args.paths, out_dir=args.out_dir)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
