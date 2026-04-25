from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Optional

from lxml import etree

from lawvm.corpus_store import get_corpus_store


@dataclass(frozen=True)
class _AddressPart:
    kind: str
    label: str


def _parse_address(address: str | None) -> list[_AddressPart]:
    if not address:
        return []
    parts: list[_AddressPart] = []
    for segment in address.split("/"):
        if ":" not in segment:
            continue
        kind, label = segment.split(":", 1)
        kind = kind.strip()
        label = label.strip()
        if not kind or not label:
            continue
        parts.append(_AddressPart(kind=kind, label=label))
    return parts


def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return " ".join(num.text.split()).strip()
    return ""


def _normalize_label(value: str) -> str:
    return " ".join(value.replace("§", "").split()).strip()


def _nearest_ancestor(node: etree._Element, kind: str) -> Optional[etree._Element]:
    current = node.getparent()
    while current is not None:
        if _tag(current) == kind:
            return current
        current = current.getparent()
    return None


def _label_for_kind(node: etree._Element) -> str:
    if _tag(node) in {"chapter", "part", "section"}:
        return _normalize_label(_num_text(node))
    return ""


def _matches_address(node: etree._Element, parts: list[_AddressPart]) -> bool:
    if not parts:
        return True
    section_part = next((part for part in parts if part.kind == "section"), None)
    if section_part is None:
        return False
    if _tag(node) != "section" or _normalize_label(_num_text(node)) != _normalize_label(section_part.label):
        return False
    chapter_part = next((part for part in parts if part.kind == "chapter"), None)
    if chapter_part is not None:
        chapter = _nearest_ancestor(node, "chapter")
        if chapter is None or _normalize_label(_num_text(chapter)) != _normalize_label(chapter_part.label):
            return False
    part_part = next((part for part in parts if part.kind == "part"), None)
    if part_part is not None:
        part = _nearest_ancestor(node, "part")
        if part is None or _normalize_label(_num_text(part)) != _normalize_label(part_part.label):
            return False
    return True


def _find_addressed_element(root: etree._Element, address: str | None) -> etree._Element:
    parts = _parse_address(address)
    if not parts:
        body = root.find(".//{*}body")
        return body if body is not None else root

    # Prefer exact section matches when an address includes a section.
    section_part = next((part for part in parts if part.kind == "section"), None)
    if section_part is not None:
        sections = root.findall(".//{*}section")
        for section in sections:
            if _matches_address(section, parts):
                return section

    # Fall back to the first matching node of the requested terminal kind.
    terminal = parts[-1]
    nodes = root.findall(f".//{{*}}{terminal.kind}")
    for node in nodes:
        if _normalize_label(_label_for_kind(node)) == _normalize_label(terminal.label):
            if terminal.kind == "chapter":
                part_part = next((part for part in parts if part.kind == "part"), None)
                if part_part is not None:
                    part = _nearest_ancestor(node, "part")
                    if part is None or _normalize_label(_num_text(part)) != _normalize_label(part_part.label):
                        continue
            return node

    raise ValueError(f"address not found in source XML: {address}")


def _format_xml_lines(xml_text: str) -> str:
    lines = xml_text.splitlines()
    width = max(3, len(str(len(lines))))
    return "\n".join(f"{idx:>{width}} | {line}" for idx, line in enumerate(lines, start=1))


def build_source_dump(statute_id: str, address: str | None = None) -> dict[str, Any]:
    """Return a source XML inspection payload for one statute/address."""
    corpus = get_corpus_store()
    xml_bytes = corpus.read_source(statute_id)
    if xml_bytes is None:
        raise SystemExit(f"source XML not found in corpus for {statute_id}")

    root = etree.fromstring(xml_bytes)
    selected = _find_addressed_element(root, address)
    xml_text = etree.tostring(selected, encoding="unicode", pretty_print=True).strip()
    if not xml_text:
        xml_text = etree.tostring(root, encoding="unicode", pretty_print=True).strip()

    title_el = root.find(".//{*}docTitle")
    title = (
        etree.tostring(title_el, method="text", encoding="unicode").strip()
        if title_el is not None
        else ""
    )

    return {
        "statute_id": statute_id,
        "title": title,
        "address": address or "",
        "selected_kind": _tag(selected),
        "selected_label": _label_for_kind(selected),
        "xml": xml_text,
        "lines": xml_text.splitlines(),
    }


def _format_text(bundle: dict[str, Any]) -> str:
    header = [
        f"Statute  : {bundle['statute_id']}",
        f"Title    : {bundle.get('title') or '(unknown)'}",
        f"Address  : {bundle.get('address') or '(entire source XML)'}",
        f"Kind     : {bundle.get('selected_kind') or '(unknown)'}",
        f"Label    : {bundle.get('selected_label') or '(none)'}",
        "",
    ]
    return "\n".join(header + [_format_xml_lines(bundle["xml"])])


def main(args) -> None:
    try:
        bundle = build_source_dump(args.statute_id, getattr(args, "address", None))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(_format_text(bundle))
