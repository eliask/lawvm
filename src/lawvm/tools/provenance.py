"""lawvm provenance / trace -- provision wording provenance assembly.

This module assembles existing LawVM engines; it does not parse, replay, or
project legal state itself.

Stable JSON shape, schema ``lawvm.provenance.v1``::

    {
      "schema": "lawvm.provenance.v1",
      "statute_id": "...",
      "selector": "§3:1",
      "locator": "chapter:3/section:1",
      "as_of": "2026-06-09",
      "query_type": "in_force",
      "in_force": {
        "status": "selected",
        "text": "...",
        "available": true,
        "version": {
          "effective": "...",
          "enacted": "...",
          "content_state": "...",
          "expires": "...",
          "variant_kind": "...",
          "applicability": ...
        },
        "source_amendment": "2026/269"
      },
      "originating_he": {
        "he_id": "he/2025/188",
        "title": "...",
        "ministry": "...",
        "date_issued": "...",
        "finlex_state": "pending",
        "enacted_law_surfaced": "2026/269",
        "confidence": "exact"
      },
      "preparatory": [
        {"kind": "he", "canonical_id": "he/2025/188", "raw_text": "HE 188/2025"}
      ],
      "commencement": {
        "effective": "2026-06-01",
        "enacted": "...",
        "content_state": "live",
        "gate": "in_force"
      },
      "notes": []
    }
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

from lawvm.core.preparatory_reference import PreparatoryReferenceKind
from lawvm.core.selector import to_locator_string
from lawvm.corpus_store import get_corpus_store
from lawvm.finland.preparatory_reference_extractor import extract_preparatory_refs
from lawvm.provision_state import resolve_provision_state

_SCHEMA = "lawvm.provenance.v1"
_DEFAULT_DATA_DIR = "data/fi/v1"


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _dateish(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def _parse_he_id(he_id: str) -> tuple[int, int] | None:
    parts = he_id.split("/")
    if len(parts) != 3 or parts[0] != "he":
        return None
    if not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return int(parts[1]), int(parts[2])


def _lookup_he_meta(he_id: str, data_dir: str) -> dict[str, Any] | None:
    """Return HE metadata from fi_he_corpus, or None when no row is present.

    Kept as a small helper so tests can monkeypatch it without loading the real
    corpus projection.
    """
    parsed = _parse_he_id(he_id)
    if parsed is None:
        return None
    year, number = parsed
    path = Path(data_dir) / "fi_he_corpus.parquet"
    if not path.exists():
        return None

    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    mask = pc.and_(
        pc.equal(table["he_year"], year),
        pc.equal(table["he_number"], number),
    )
    rows = table.filter(mask).to_pylist()
    if not rows:
        return None
    row = rows[0]
    return {
        "he_id": row.get("he_id") or he_id,
        "title": row.get("title"),
        "ministry": row.get("ministry_show_as"),
        "date_issued": _dateish(row.get("date_issued")),
        "finlex_state": row.get("finlex_state"),
    }


def _gate(effective: str, content_state: str, as_of: str) -> str:
    if content_state == "tombstone":
        return "repealed"
    if effective in ("", "0000-00-00"):
        return "base"
    if effective > as_of:
        return "future"
    return "in_force"


def _prep_row(ref: Any) -> dict[str, str | None]:
    kind = getattr(ref, "kind", None)
    kind_value = getattr(kind, "value", kind)
    return {
        "kind": kind_value,
        "canonical_id": getattr(ref, "canonical_id", None),
        "raw_text": getattr(ref, "raw_text", None),
    }


def _confidence(ref: Any) -> str:
    confidence = getattr(ref, "confidence", None)
    value = getattr(confidence, "value", confidence)
    return str(value) if value is not None else "unknown"


def _read_preparatory_refs(amending_law_id: str, notes: list[str]) -> list[Any]:
    store = get_corpus_store()
    try:
        xml = store.read_source(amending_law_id)
    finally:
        store.close()
    if xml is None:
        notes.append(f"Source XML for amending law L {amending_law_id} was not found; preparatory refs unavailable.")
        return []
    result = extract_preparatory_refs(xml, amending_law_id)
    return list(result.refs)


def build_provenance(
    statute_id: str,
    selector: str,
    as_of: str,
    query_type: str = "in_force",
    jurisdiction: str = "fi",
    data_dir: str = _DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    """Build one provision provenance trace as ``lawvm.provenance.v1``."""
    if jurisdiction != "fi":
        raise ValueError(f"lawvm provenance currently supports only jurisdiction='fi' (got {jurisdiction!r})")

    as_of_value = as_of or _today_iso()
    locator = to_locator_string(selector)
    payload = resolve_provision_state(
        statute_id=statute_id,
        jurisdiction=jurisdiction,
        provision=locator,
        as_of=as_of_value,
        query_type=query_type,
        territory=None,
        include_ir=False,
        status_stream=sys.stderr,
    )

    text = payload.get("text") or {}
    version = payload.get("version") or {}
    source = payload.get("source") or {}
    source_amendment = source.get("statute_id") or None
    effective = version.get("effective") or ""
    enacted = version.get("enacted") or ""
    content_state = version.get("content_state") or ""
    notes: list[str] = []

    preparatory_refs: list[Any] = []
    if source_amendment:
        preparatory_refs = _read_preparatory_refs(source_amendment, notes)
    else:
        notes.append("No source amendment was selected; this appears to be base statute text, so originating HE is not available.")

    preparatory = [_prep_row(ref) for ref in preparatory_refs]
    he_ref = next(
        (
            ref for ref in preparatory_refs
            if getattr(ref, "kind", None) == PreparatoryReferenceKind.HE
        ),
        None,
    )
    originating_he = None
    if he_ref is None:
        if source_amendment:
            notes.append(f"No originating HE reference was found in preliminaryWork for L {source_amendment}.")
    else:
        he_id = getattr(he_ref, "canonical_id", None)
        if he_id:
            meta = _lookup_he_meta(he_id, data_dir)
            if meta is None:
                notes.append(f"HE metadata for {he_id} was not found in {data_dir}/fi_he_corpus.parquet.")
                meta = {
                    "he_id": he_id,
                    "title": None,
                    "ministry": None,
                    "date_issued": None,
                    "finlex_state": None,
                }
            originating_he = {
                "he_id": meta.get("he_id") or he_id,
                "title": meta.get("title"),
                "ministry": meta.get("ministry"),
                "date_issued": meta.get("date_issued"),
                "finlex_state": meta.get("finlex_state"),
                "enacted_law_surfaced": source_amendment,
                "confidence": _confidence(he_ref),
            }
            finlex_state = meta.get("finlex_state")
            if source_amendment and finlex_state != "enacted":
                notes.append(
                    f"HE corpus finlex_state={finlex_state}, but this HE enacted L {source_amendment} "
                    f"(in force {effective or 'unknown'}); surfaced by amendment→HE inversion, not the HE-corpus projection."
                )

    return {
        "schema": _SCHEMA,
        "statute_id": statute_id,
        "selector": selector,
        "locator": locator,
        "as_of": as_of_value,
        "query_type": query_type,
        "in_force": {
            "status": payload.get("status"),
            "text": text.get("rendered") or "",
            "available": bool(text.get("available", False)),
            "version": {
                "effective": effective,
                "enacted": enacted,
                "content_state": content_state,
                "expires": version.get("expires") or "",
                "variant_kind": version.get("variant_kind") or "",
                "applicability": version.get("applicability"),
            },
            "source_amendment": source_amendment,
        },
        "originating_he": originating_he,
        "preparatory": preparatory,
        "commencement": {
            "effective": effective,
            "enacted": enacted,
            "content_state": content_state,
            "gate": _gate(effective, content_state, as_of_value),
        },
        "notes": notes,
    }


def _render_human(record: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"{record['statute_id']} {record['selector']}  (provenance @ {record['as_of']})")
    lines.append(f"locator           : {record['locator']}")
    lines.append(f"query             : {record['query_type']}")
    in_force = record["in_force"]
    lines.append(f"status            : {in_force['status']}")
    lines.append("")
    if in_force["available"] and in_force["text"]:
        lines.append(in_force["text"])
    else:
        lines.append("[no text available]")
    lines.append("")
    source = in_force["source_amendment"] or "(base statute)"
    lines.append(f"source amendment  : L {source}" if source != "(base statute)" else f"source amendment  : {source}")

    he = record["originating_he"]
    if he is None:
        lines.append("originating HE    : (none)")
    else:
        bits = [he["he_id"]]
        if he.get("title"):
            bits.append(he["title"])
        if he.get("finlex_state"):
            bits.append(f"finlex_state={he['finlex_state']}")
        lines.append("originating HE    : " + " · ".join(bits))
        if he.get("ministry"):
            lines.append(f"HE ministry/date  : {he['ministry']} · {he.get('date_issued') or 'date unknown'}")
        lines.append(f"enacted law       : L {he['enacted_law_surfaced']} (surfaced by amendment→HE inversion)")

    prep = record["preparatory"]
    lines.append("preparatory       :")
    if prep:
        for row in prep:
            lines.append(f"  - {row['kind']}: {row['raw_text']} ({row['canonical_id']})")
    else:
        lines.append("  - (none)")

    comm = record["commencement"]
    lines.append(
        "commencement      : "
        f"effective {comm['effective'] or '(base)'} · enacted {comm['enacted'] or 'unknown'} · "
        f"content_state {comm['content_state'] or 'unknown'} · gate {comm['gate']}"
    )
    if record["notes"]:
        lines.append("notes             :")
        for note in record["notes"]:
            lines.append(f"  - {note}")
    return "\n".join(lines)


def main(args: Any) -> None:
    jurisdiction = getattr(args, "jurisdiction", "fi")
    if jurisdiction != "fi":
        print(f"ERROR: lawvm provenance currently supports only -j fi (got {jurisdiction!r})", file=sys.stderr)
        raise SystemExit(2)
    record = build_provenance(
        statute_id=args.statute_id,
        selector=getattr(args, "selector", "") or "",
        as_of=getattr(args, "as_of", "") or _today_iso(),
        query_type=getattr(args, "query_type", "in_force"),
        jurisdiction=jurisdiction,
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
    )
    if getattr(args, "json", False):
        print(json.dumps(record, ensure_ascii=False, indent=2, default=str))
        return
    print(_render_human(record))
