from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from lxml import etree

from lawvm.corpus_store import get_corpus_store

_LEG_BASE = "https://www.legislation.gov.uk"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_UK_ARCHIVE_PATH = _REPO_ROOT / "data" / "uk_legislation.farchive"
_UK_ACT_TYPES = frozenset(
    {
        "aep",
        "aosp",
        "apgb",
        "apni",
        "asp",
        "gbla",
        "mnia",
        "mwa",
        "nia",
        "nisi",
        "ssi",
        "ukcm",
        "ukla",
        "ukpga",
        "uksi",
    }
)


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
    num = None
    for name in ("num", "Pnumber", "Number"):
        num = el.find(f"{{*}}{name}")
        if num is None:
            num = el.find(name)
        if num is not None:
            break
    if num is not None and num.text:
        return " ".join(num.text.split()).strip()
    return ""


def _normalize_label(value: str) -> str:
    return " ".join(value.replace("§", "").split()).strip()


def _label_match_key(value: str) -> str:
    normalized = _normalize_label(value).strip().strip(".").lower()
    if normalized.isdecimal():
        return str(int(normalized))
    roman = _roman_to_int(normalized)
    if roman is not None:
        return str(roman)
    return normalized


def _labels_match(source_label: str, requested_label: str) -> bool:
    if _normalize_label(source_label) == _normalize_label(requested_label):
        return True
    return _label_match_key(source_label) == _label_match_key(requested_label)


def _roman_to_int(value: str) -> int | None:
    if not value or any(ch not in _ROMAN_VALUES for ch in value.upper()):
        return None
    total = 0
    previous = 0
    for ch in reversed(value.upper()):
        current = _ROMAN_VALUES[ch]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    if total <= 0 or total > 50 or _int_to_roman(total).lower() != value.lower():
        return None
    return total


def _int_to_roman(value: int) -> str:
    pairs = (
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    remaining = value
    out: list[str] = []
    for amount, token in pairs:
        while remaining >= amount:
            out.append(token)
            remaining -= amount
    return "".join(out)


_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50}


def _nearest_ancestor(node: etree._Element, kind: str) -> Optional[etree._Element]:
    current = node.getparent()
    while current is not None:
        if _tag(current) == kind:
            return current
        current = current.getparent()
    return None


def _label_for_kind(node: etree._Element) -> str:
    if _tag(node) in {"chapter", "part", "section", "P1", "Section", "Article", "Rule"}:
        return _normalize_label(_num_text(node))
    return ""


def _address_kind_matches(node: etree._Element, wanted_kind: str) -> bool:
    tag = _tag(node)
    if wanted_kind == "section":
        return tag in {"section", "P1", "Section", "Article", "Rule"}
    return tag == wanted_kind


def _matches_address(node: etree._Element, parts: list[_AddressPart]) -> bool:
    if not parts:
        return True
    section_part = next((part for part in parts if part.kind == "section"), None)
    if section_part is None:
        return False
    if (
        not _address_kind_matches(node, "section")
        or not _labels_match(_num_text(node), section_part.label)
    ):
        return False
    chapter_part = next((part for part in parts if part.kind == "chapter"), None)
    if chapter_part is not None:
        chapter = _nearest_ancestor(node, "chapter")
        if chapter is None or not _labels_match(_num_text(chapter), chapter_part.label):
            return False
    part_part = next((part for part in parts if part.kind == "part"), None)
    if part_part is not None:
        part = _nearest_ancestor(node, "part")
        if part is None or not _labels_match(_num_text(part), part_part.label):
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
        sections = (
            root.findall(".//{*}section")
            + root.findall(".//{*}P1")
            + root.findall(".//{*}Section")
            + root.findall(".//{*}Article")
            + root.findall(".//{*}Rule")
        )
        for section in sections:
            if _matches_address(section, parts):
                return section

    # Fall back to the first matching node of the requested terminal kind.
    terminal = parts[-1]
    if terminal.kind == "section":
        nodes = (
            root.findall(".//{*}section")
            + root.findall(".//{*}P1")
            + root.findall(".//{*}Section")
            + root.findall(".//{*}Article")
            + root.findall(".//{*}Rule")
        )
    else:
        nodes = root.findall(f".//{{*}}{terminal.kind}")
    for node in nodes:
        if _labels_match(_label_for_kind(node), terminal.label):
            if terminal.kind == "chapter":
                part_part = next((part for part in parts if part.kind == "part"), None)
                if part_part is not None:
                    part = _nearest_ancestor(node, "part")
                    if part is None or not _labels_match(_num_text(part), part_part.label):
                        continue
            return node

    raise ValueError(f"address not found in source XML: {address}")


def _format_xml_lines(xml_text: str) -> str:
    lines = xml_text.splitlines()
    width = max(3, len(str(len(lines))))
    return "\n".join(f"{idx:>{width}} | {line}" for idx, line in enumerate(lines, start=1))


def is_uk_statute_id(statute_id: str) -> bool:
    parts = statute_id.strip("/").split("/")
    return len(parts) == 3 and parts[0] in _UK_ACT_TYPES and all(parts)


def _uk_enacted_locator(statute_id: str) -> str:
    return f"{_LEG_BASE}/{statute_id}/enacted/data.xml"


def _uk_title(root: etree._Element) -> str:
    for query in (
        ".//{*}docTitle",
        ".//{*}Title",
        ".//{*}LongTitle",
    ):
        title_el = root.find(query)
        if title_el is not None:
            title = etree.tostring(title_el, method="text", encoding="unicode").strip()
            if title:
                return " ".join(title.split())
    return ""


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


def build_uk_source_dump(
    statute_id: str,
    address: str | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a farchive-backed UK enacted XML inspection payload."""
    if not is_uk_statute_id(statute_id):
        raise SystemExit(
            f"invalid UK statute id: {statute_id!r} "
            "(expected act_type/year/number, e.g. ukpga/2020/17)"
        )

    archive_path = Path(db_path) if db_path else _DEFAULT_UK_ARCHIVE_PATH
    if not archive_path.exists():
        raise SystemExit(
            f"UK farchive not found at {archive_path}. "
            "Run: uv run lawvm uk-corpus all"
        )

    from farchive import Farchive

    locator = _uk_enacted_locator(statute_id)
    archive = Farchive(archive_path)
    try:
        xml_bytes = archive.get(locator)
    finally:
        archive.close()
    if xml_bytes is None:
        raise SystemExit(f"UK enacted XML not found in farchive for {statute_id}: {locator}")

    root = etree.fromstring(xml_bytes)
    selected = _find_addressed_element(root, address)
    xml_text = etree.tostring(selected, encoding="unicode", pretty_print=True).strip()
    if not xml_text:
        xml_text = etree.tostring(root, encoding="unicode", pretty_print=True).strip()

    return {
        "statute_id": statute_id,
        "jurisdiction": "uk",
        "stage": "PARSE (UK enacted source XML from farchive, no replay)",
        "archive_path": str(archive_path),
        "source_url": locator,
        "title": _uk_title(root),
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
    ]
    if bundle.get("stage"):
        header.append(f"Stage    : {bundle['stage']}")
    if bundle.get("source_url"):
        header.append(f"Source   : {bundle['source_url']}")
    if bundle.get("archive_path"):
        header.append(f"Archive  : {bundle['archive_path']}")
    header.append("")
    return "\n".join(header + [_format_xml_lines(bundle["xml"])])


def main(args) -> None:
    try:
        statute_id = args.statute_id
        jurisdiction = getattr(args, "jurisdiction", "fi")
        if jurisdiction == "uk" or is_uk_statute_id(statute_id):
            bundle = build_uk_source_dump(
                statute_id,
                getattr(args, "address", None),
                db_path=getattr(args, "db", None),
            )
        else:
            bundle = build_source_dump(statute_id, getattr(args, "address", None))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(_format_text(bundle))
