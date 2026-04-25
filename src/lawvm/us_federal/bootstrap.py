from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _strip_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def summarize_uslm_xml(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    root_tag = _strip_namespace(root.tag)
    nodes = list(root.iter())
    heading_tags = {"heading", "num", "chapeau"}
    headings: list[str] = []
    for node in nodes:
        if _strip_namespace(node.tag) not in heading_tags:
            continue
        text = _normalize_text("".join(str(_t) for _t in node.itertext()))
        if text:
            headings.append(text)
    return {
        "path": str(path),
        "kind": "uslm_xml_summary",
        "root_tag": root_tag,
        "element_count": len(nodes),
        "heading_count": len(headings),
        "sample_headings": headings[:40],
    }


class ClassificationTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._in_link = False
        self._href = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = ""
        for key, value in attrs:
            if key == "href" and value:
                href = value
                break
        self._in_link = True
        self._href = href
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_link:
            return
        text = _normalize_text("".join(self._text_parts))
        self.links.append({"href": self._href, "text": text})
        self._in_link = False
        self._href = ""
        self._text_parts = []


def summarize_classification_html(path: Path) -> dict[str, Any]:
    parser = ClassificationTableParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    links = [link for link in parser.links if link["href"] or link["text"]]
    tableish = [link for link in links if "class" in link["text"].lower() or "table" in link["text"].lower()]
    public_lawish = [link for link in links if "public law" in link["text"].lower()]
    return {
        "path": str(path),
        "kind": "classification_html_summary",
        "link_count": len(links),
        "sample_links": links[:50],
        "tableish_count": len(tableish),
        "public_lawish_count": len(public_lawish),
    }


def summarize_inputs(paths: list[Path], out_dir: Path | None = None) -> int:
    for path in paths:
        suffix = path.suffix.lower()
        if suffix == ".xml":
            summary = summarize_uslm_xml(path)
        elif suffix in {".html", ".htm"}:
            summary = summarize_classification_html(path)
        else:
            summary = {
                "path": str(path),
                "kind": "unknown",
                "error": f"Unsupported file type for summarization: {path.suffix}",
            }
        print(json.dumps(summary, ensure_ascii=False))
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{path.stem}.summary.json"
            out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LawVM U.S. federal bootstrap utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize_parser = subparsers.add_parser(
        "summarize-inputs",
        help="Summarize local U.S. federal source files such as USLM XML or classification HTML",
    )
    summarize_parser.add_argument("paths", nargs="+", type=Path)
    summarize_parser.add_argument("--out-dir", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "summarize-inputs":
        return summarize_inputs(args.paths, out_dir=args.out_dir)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
