"""lawvm ee-inspect-source — inspect one Estonia source act for replay diagnosis."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse


def _extract_rt_title(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "pealkiri" and (el.text or "").strip():
            return (el.text or "").strip()
    return ""


def _build_ee_source_sections(xml_bytes: bytes, *, target_title: str = "") -> list[dict[str, Any]]:
    from lawvm.estonia.grafter import (
        _extract_intro_statute_fragment,
        _find,
        _ns,
        _strict_title_match_para,
        _text,
        _title_matches_para,
    )

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    root_ns = ""
    if "}" in root.tag:
        root_ns = root.tag.split("}")[0].lstrip("{")

    def _first_descendant_tavatekst_text(para: ET.Element) -> str:
        for tavatekst in para.iter(_ns(root_ns, "tavatekst")):
            text = " ".join(str(chunk) for chunk in tavatekst.itertext()).replace("\xa0", " ")
            text = " ".join(text.split()).strip()
            if text:
                return text
        return ""

    sections: list[dict[str, Any]] = []
    for para in root.iter(_ns(root_ns, "paragrahv")):
        para_nr = _text(_find(para, root_ns, "paragrahvNr")) or ""
        para_title = _text(_find(para, root_ns, "paragrahvPealkiri")) or ""
        first_tava = _first_descendant_tavatekst_text(para)
        intro_fragment = _extract_intro_statute_fragment(first_tava)
        html_block_count = sum(
            1
            for st in para.iter(_ns(root_ns, "sisuTekst"))
            for _hk in st.findall(_ns(root_ns, "HTMLKonteiner"))
        )
        target_match = None
        if target_title:
            target_match = bool(
                (para_title and _strict_title_match_para(target_title, para_title))
                or (intro_fragment and _title_matches_para(target_title, intro_fragment))
            )
        sections.append(
            {
                "paragrahv_nr": para_nr,
                "paragrahv_title": para_title,
                "first_tavatekst": first_tava,
                "intro_target_fragment": intro_fragment,
                "html_block_count": html_block_count,
                "matches_target_title": target_match,
            }
        )
    return sections


def _build_ee_inspect_source_payload(
    *,
    source_id: str,
    base_id: str = "",
    target_title: str = "",
    op_limit: int = 25,
) -> dict[str, Any]:
    from lawvm.estonia.fetch import (
        extract_amendment_refs,
        extract_effective_date,
        extract_grupi_id,
        extract_tekstiliik,
        fetch_rt_xml,
        open_rt_archive,
    )
    from lawvm.estonia.grafter import parse_ee_amendment_ops, parse_ee_statute

    archive = open_rt_archive()
    try:
        source_xml = fetch_rt_xml(source_id, archive=archive)
        resolved_target_title = target_title
        if base_id and not resolved_target_title:
            base_xml = fetch_rt_xml(base_id, archive=archive)
            resolved_target_title = parse_ee_statute(base_xml).title
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    try:
        ops = parse_ee_amendment_ops(source_xml, f"ee/{source_id}", resolved_target_title)
    except Exception as exc:  # pragma: no cover - diagnostic surface
        return {
            "source_id": source_id,
            "base_id": base_id,
            "target_title": resolved_target_title,
            "error": f"{type(exc).__name__}: {exc}",
        }

    op_rows = []
    for op in ops[: max(op_limit, 0)]:
        action = op.action.value if hasattr(op.action, "value") else str(op.action)
        row: dict[str, Any] = {
            "sequence": op.sequence,
            "action": action,
            "address": str(op.target),
        }
        if op.payload is not None and op.payload.text:
            row["payload_preview"] = op.payload.text[:180]
        old_text = op.payload.attrs.get("old_text") if op.payload is not None else None
        if isinstance(old_text, str) and old_text:
            row["old_text"] = old_text
        op_rows.append(row)

    return {
        "source_id": source_id,
        "source_title": _extract_rt_title(source_xml),
        "base_id": base_id,
        "target_title": resolved_target_title,
        "tekstiliik": extract_tekstiliik(source_xml),
        "effective_date": extract_effective_date(source_xml),
        "grupi_id": extract_grupi_id(source_xml),
        "amendment_refs": [
            {
                "aktViide": ref.aktViide,
                "passed": ref.passed,
                "joustumine": ref.joustumine,
            }
            for ref in extract_amendment_refs(source_xml)
        ],
        "section_summaries": _build_ee_source_sections(
            source_xml,
            target_title=resolved_target_title,
        ),
        "parsed_op_count": len(ops),
        "parsed_ops": op_rows,
        "truncated_ops": max(len(ops) - len(op_rows), 0),
    }


def _print_ee_inspect_source(payload: dict[str, Any]) -> None:
    if "error" in payload:
        print(f"ERROR: {payload['error']}", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"=== EE Inspect Source: {payload['source_id']} ===")
    print(f"  title       : {payload.get('source_title', '')[:80]}")
    print(f"  tekstiliik  : {payload.get('tekstiliik', '')}")
    print(f"  effective   : {payload.get('effective_date', '')}")
    print(f"  grupi_id    : {payload.get('grupi_id', '')}")
    if payload.get("base_id"):
        print(f"  base_id     : {payload['base_id']}")
    if payload.get("target_title"):
        print(f"  target      : {payload['target_title']}")
    print(f"  parsed ops  : {payload.get('parsed_op_count', 0)}")

    refs = payload.get("amendment_refs") or []
    if refs:
        print(f"\n  Amendment refs ({len(refs)}):")
        for ref in refs[:10]:
            print(f"    {ref['aktViide']}  passed={ref['passed']}  effective={ref['joustumine']}")
        if len(refs) > 10:
            print(f"    ... and {len(refs) - 10} more")

    sections = payload.get("section_summaries") or []
    display_sections = sections
    if payload.get("target_title"):
        display_sections = [
            row
            for row in sections
            if row.get("matches_target_title") is True
            or (
                row.get("html_block_count", 0) > 0
                and (
                    row.get("intro_target_fragment")
                    or any(
                        kw in (row.get("paragrahv_title") or "").lower()
                        for kw in ("muutmine", "kehtetuks tunnistamine", "täiendamine")
                    )
                )
            )
        ]
    if display_sections:
        label = "candidate source sections" if payload.get("target_title") else "source sections"
        print(f"\n  {label.capitalize()} ({len(display_sections)} shown from {len(sections)} total):")
        for row in display_sections[:20]:
            match = row.get("matches_target_title")
            match_text = ""
            if match is True:
                match_text = "  [target-match]"
            elif match is False:
                match_text = "  [other-target]"
            title = row.get("paragrahv_title") or row.get("intro_target_fragment") or ""
            print(
                f"    § {row.get('paragrahv_nr') or '?'}  html={row.get('html_block_count', 0)}{match_text}"
            )
            if title:
                print(f"      title : {title[:120]}")
            if row.get("first_tavatekst"):
                print(f"      intro : {row['first_tavatekst'][:160]}")
        if len(display_sections) > 20:
            print(f"    ... and {len(display_sections) - 20} more")

    ops = payload.get("parsed_ops") or []
    print(f"\n  Parsed ops preview ({len(ops)} shown):")
    if not ops:
        print("    none")
    for row in ops:
        print(f"    {row['sequence']:>3}  {row['action']:<12} {row['address']}")
        if row.get("old_text"):
            print(f"      old   : {row['old_text'][:140]}")
        if row.get("payload_preview"):
            print(f"      new   : {row['payload_preview'][:160]}")
        if row.get("note_preview"):
            print(f"      note  : {row['note_preview'][:160]}")
    if payload.get("truncated_ops"):
        print(f"    ... and {payload['truncated_ops']} more")


def main(args: argparse.Namespace) -> None:
    payload = _build_ee_inspect_source_payload(
        source_id=args.source_id,
        base_id=getattr(args, "base_id", "") or "",
        target_title=getattr(args, "target_title", "") or "",
        op_limit=getattr(args, "op_limit", 25),
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_ee_inspect_source(payload)


__all__ = ["_build_ee_inspect_source_payload", "main"]
