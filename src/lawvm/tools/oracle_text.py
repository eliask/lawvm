"""lawvm oracle-text — fetch oracle consolidated section text at a specific version.

Reads the Finnish consolidated oracle XML (sd-cons) from the archive at either
the current selected oracle version or at the version pinned to a specific
amendment, and prints the section text with optional subsection breakdown.

This covers the gap where farchive cat + hand-rolled regex was the only way to
inspect oracle section text at a specific consolidated version snapshot.

Usage:
    lawvm oracle-text 2017/530 --section section:2
    lawvm oracle-text 2017/530 --section section:2 --at-amendment 2020/959
    lawvm oracle-text 2017/530 --section section:2 --subsections
    lawvm oracle-text 2017/530 --section section:2 --at-amendment 2020/959 --json
    lawvm oracle-text 2017/530                          # list all section labels
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List


def _amendment_id_to_version_tag(amendment_id: str) -> str:
    """Convert '2020/959' → '20200959' (YYYY + zero-padded 4-digit number)."""
    year, num = amendment_id.strip().split("/")
    return f"{year}{int(num):04d}"


def _el_to_text(el: Any) -> str:
    """Extract plain text from an lxml element."""
    from lxml import etree
    raw = etree.tostring(el, method="text", encoding="unicode")
    return re.sub(r"\s+", " ", raw).strip()


def _find_section_el(oracle_root: Any, section_filter: str) -> Any | None:
    """Find a section element by address or Finnish num label.

    section_filter can be:
      - 'section:2'  → searches eId="sec_2" first, then <num>2 §</num>
      - '2 §'        → searches <num>2 §</num>
    """
    if not section_filter:
        return None

    # Try eId first: 'section:2' → eId='sec_2'
    if section_filter.startswith("section:"):
        label = section_filter[len("section:"):]
        eid = f"sec_{label}"
        el = oracle_root.find(f'.//*[@eId="{eid}"]')
        if el is not None:
            return el

    # Build num text to search
    num_text = section_filter
    if ":" in section_filter:
        num_text = section_filter.split(":", 1)[1].strip()
    if "§" not in num_text:
        num_text = num_text + " §"

    for sec in oracle_root.findall(".//{*}section"):
        num_el = sec.find(".//{*}num")
        if num_el is not None and num_el.text and num_text.split()[0] in num_el.text:
            return sec

    return None


def build_oracle_text_bundle(
    statute_id: str,
    section_filter: str = "",
    at_amendment: str = "",
    lang: str = "fin",
    show_subsections: bool = False,
) -> Dict[str, Any]:
    """Fetch oracle section text from the consolidated archive.

    Parameters
    ----------
    statute_id:
        Statute identifier, e.g. '2017/530'.
    section_filter:
        Section address, e.g. 'section:2'. If empty, lists all section labels.
    at_amendment:
        If given (e.g. '2020/959'), read the oracle at the consolidated version
        pinned to that amendment (version_tag = YYYYNNNN format).
        If empty, use the current selected oracle.
    lang:
        Language code (default 'fin').
    show_subsections:
        If True, include per-subsection text breakdown.
    """
    from lawvm.finland.grafter import get_corpus
    from lawvm.finland.consolidated_artifacts import build_consolidated_main_locator
    from lawvm.finland.corpus import get_consolidated_oracle_context
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
    from lxml import etree

    cs = get_corpus()

    if at_amendment:
        version_tag = _amendment_id_to_version_tag(at_amendment)
        locator = build_consolidated_main_locator(
            sid=statute_id, lang=lang, version_tag=version_tag
        )
    else:
        selector = ConsolidatedArtifactSelector.latest_cached_editorial()
        ctx = get_consolidated_oracle_context(statute_id, corpus=cs, selector=selector)
        locator = ctx.locator

    oracle_bytes = cs.read_locator(locator)
    if oracle_bytes is None:
        raise SystemExit(f"oracle not found in archive: {locator!r}")

    oracle_root = etree.fromstring(oracle_bytes)

    # No section filter → list all section labels and return
    if not section_filter:
        labels: List[str] = []
        for sec in oracle_root.findall(".//{*}section"):
            eid = sec.get("eId") or ""
            num_el = sec.find(".//{*}num")
            num_text = (num_el.text or "").strip() if num_el is not None else ""
            labels.append(f"{eid} ({num_text})" if eid else num_text)
        return {
            "statute_id": statute_id,
            "locator": locator,
            "at_amendment": at_amendment,
            "section_filter": "(none — listing sections)",
            "found": True,
            "section_labels": labels,
            "section_count": len(labels),
            "full_text": "",
            "subsections": [],
        }

    section_el = _find_section_el(oracle_root, section_filter)

    if section_el is None:
        return {
            "statute_id": statute_id,
            "locator": locator,
            "at_amendment": at_amendment,
            "section_filter": section_filter,
            "found": False,
            "error": f"section {section_filter!r} not found at this oracle version",
            "full_text": "",
            "subsections": [],
        }

    full_text = _el_to_text(section_el)
    subsections: List[Dict[str, Any]] = []
    if show_subsections:
        for i, ss in enumerate(section_el.findall(".//{*}subsection"), start=1):
            ss_text = _el_to_text(ss)
            hcontainers = ss.findall(".//{*}hcontainer")
            subsections.append({
                "index": i,
                "text": ss_text,
                "text_length": len(ss_text),
                "hcontainer_count": len(hcontainers),
            })

    return {
        "statute_id": statute_id,
        "locator": locator,
        "at_amendment": at_amendment,
        "section_filter": section_filter,
        "found": True,
        "full_text": full_text,
        "full_text_length": len(full_text),
        "subsection_count": len(section_el.findall(".//{*}subsection")),
        "subsections": subsections,
    }


def _format_text(bundle: Dict[str, Any]) -> str:
    lines = [
        f"Statute  : {bundle['statute_id']}",
        f"Locator  : {bundle['locator']}",
    ]
    if bundle.get("at_amendment"):
        lines.append(f"Version  : @{_amendment_id_to_version_tag(bundle['at_amendment'])} (amendment {bundle['at_amendment']})")
    lines.append(f"Section  : {bundle['section_filter']}")

    # Listing mode
    section_labels = bundle.get("section_labels")
    if section_labels is not None:
        lines.append(f"\n{bundle['section_count']} sections in this oracle version:")
        for lbl in section_labels:
            lines.append(f"  {lbl}")
        return "\n".join(lines)

    if not bundle.get("found"):
        lines.append(f"\nERROR: {bundle.get('error', 'not found')}")
        return "\n".join(lines)

    lines.append(f"Subsections: {bundle.get('subsection_count', 0)}")
    lines.append(f"Text length: {bundle.get('full_text_length', 0)} chars")
    lines.append("")
    lines.append("Full text:")
    lines.append(f"  {bundle.get('full_text', '')}")

    for ss in bundle.get("subsections", []):
        lines.append(f"\nSubsection {ss['index']} ({ss['text_length']} chars):")
        lines.append(f"  {ss['text']}")

    return "\n".join(lines)


def main(args: Any) -> None:
    bundle = build_oracle_text_bundle(
        statute_id=args.statute_id,
        section_filter=getattr(args, "section", "") or "",
        at_amendment=getattr(args, "at_amendment", "") or "",
        show_subsections=getattr(args, "subsections", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
        return
    print(_format_text(bundle))
