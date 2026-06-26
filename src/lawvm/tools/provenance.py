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
        "in_force_status": "selected",
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
from lawvm.finland.references.preparatory_reference_extractor import extract_preparatory_refs
from lawvm.provision_state import resolve_provision_state
from lawvm.tools.hyperlinks import (
    committee_url_from_raw,
    consolidated_url_from_id,
    ev_url_from_raw,
    he_url_from_canonical,
    maybe_link,
    should_hyperlink,
    statute_url_from_id,
)

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

    import pyarrow.parquet as pq

    table = pq.read_table(path)
    rows = [
        row
        for row in table.to_pylist()
        if row.get("he_year") == year and row.get("he_number") == number
    ]
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
            "in_force_status": payload.get("provision_status"),
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


_STATUTE_SCHEMA = "lawvm.provenance_statute.v1"


def _amendment_sources_from_replay(statute_id: str) -> dict[str, Any]:
    """Replay the statute ONCE and return {amendment_id: OperationSource}.

    The timeline versions carry an OperationSource per applied amendment with
    enacted/effective/legal_status — the commencement facts we want — so one
    replay yields the whole statute's amendment commencement map without a
    per-amendment replay.
    """
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml

    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=statute_id, quiet=True),
    )
    sources: dict[str, Any] = {}
    timelines = master.timelines or {}
    iterator = timelines.values() if hasattr(timelines, "values") else timelines
    for timeline in iterator:
        versions = getattr(timeline, "versions", None) or timeline
        if not hasattr(versions, "__iter__"):
            continue
        for v in versions:
            # ProvisionVersion carries the applied amendment as `.source`
            # (an OperationSource with statute_id/enacted/effective/...).
            src = getattr(v, "source", None) or getattr(v, "source_amendment", None)
            sid = getattr(src, "statute_id", None) if src is not None else None
            if sid and sid not in sources:
                sources[sid] = src
    return sources


def _op_source_commencement(src: Any) -> dict[str, str]:
    if src is None:
        return {"enacted": "", "effective": "", "legal_status": "", "title": ""}
    return {
        "enacted": _dateish(getattr(src, "enacted", "")),
        "effective": _dateish(getattr(src, "effective", "")),
        "legal_status": str(getattr(src, "legal_status", "") or ""),
        "title": str(getattr(src, "title", "") or ""),
    }


def build_statute_provenance(
    statute_id: str,
    *,
    as_of: str = "",
    jurisdiction: str = "fi",
    data_dir: str = _DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    """Build the statute-level HE -> [enacted amendments] inversion.

    For a statute id with NO section, emit every HE that touched the statute,
    each with its enacted law + commencement (from one replay) + committee /
    parliament-response preparatory refs (from the live extractor). Schema
    ``lawvm.provenance_statute.v1``.
    """
    if jurisdiction != "fi":
        raise ValueError(
            f"lawvm provenance currently supports only jurisdiction='fi' (got {jurisdiction!r})"
        )

    as_of_value = as_of or _today_iso()
    notes: list[str] = []

    # 1. Enacted amendments of this statute (the amendment-index graph).
    from lawvm.finland.amendment_index import get_amendment_children

    children = get_amendment_children()
    amend_ids = sorted(
        set(children.get(statute_id, [])),
        key=_amendment_sort_key,
        reverse=True,
    )

    # 2. One replay → per-amendment commencement (enacted/effective/status).
    try:
        replay_sources = _amendment_sources_from_replay(statute_id)
    except Exception as exc:
        replay_sources = {}
        notes.append(
            f"Replay of {statute_id} for commencement facts failed: "
            f"{type(exc).__name__}: {str(exc)[:120]}"
        )

    store = get_corpus_store()
    amendments: list[dict[str, Any]] = []
    try:
        for amend_id in amend_ids:
            entry: dict[str, Any] = {
                "amendment_id": amend_id,
                "commencement": _op_source_commencement(replay_sources.get(amend_id)),
                "applied_in_replay": amend_id in replay_sources,
                "originating_he": None,
                "committee_refs": [],
                "parliament_response_refs": [],
                "preparatory": [],
            }
            # 3. Live preparatory chain for this amendment.
            try:
                xml = store.read_source(amend_id)
            except (OSError, KeyError):
                xml = None
            if xml is None:
                entry["preparatory_available"] = False
            else:
                entry["preparatory_available"] = True
                result = extract_preparatory_refs(xml, amend_id)
                refs = list(result.refs)
                entry["preparatory"] = [_prep_row(r) for r in refs]
                for r in refs:
                    kind = getattr(r, "kind", None)
                    canonical = getattr(r, "canonical_id", None)
                    raw = getattr(r, "raw_text", None)
                    if kind == PreparatoryReferenceKind.HE and entry["originating_he"] is None:
                        meta = _lookup_he_meta(canonical, data_dir) if canonical else None
                        entry["originating_he"] = {
                            "he_id": canonical,
                            "raw_text": raw,
                            "confidence": _confidence(r),
                            "title": (meta or {}).get("title"),
                            "ministry": (meta or {}).get("ministry"),
                            "date_issued": (meta or {}).get("date_issued"),
                            "finlex_state": (meta or {}).get("finlex_state"),
                        }
                    elif kind == PreparatoryReferenceKind.COMMITTEE_REPORT:
                        entry["committee_refs"].append(
                            {"canonical_id": canonical, "raw_text": raw}
                        )
                    elif kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE:
                        entry["parliament_response_refs"].append(
                            {"canonical_id": canonical, "raw_text": raw}
                        )
            amendments.append(entry)
    finally:
        store.close()

    he_count = sum(1 for a in amendments if a["originating_he"] is not None)
    return {
        "schema": _STATUTE_SCHEMA,
        "statute_id": statute_id,
        "as_of": as_of_value,
        "amendment_count": len(amendments),
        "he_resolved_count": he_count,
        "amendments": amendments,
        "notes": notes,
    }


def _amendment_sort_key(amend_id: str) -> tuple[int, int]:
    """Sort key (year, number) for 'YYYY/N' amendment ids; safe on odd ids."""
    try:
        year_s, num_s = amend_id.split("/", 1)
        num = int(num_s.split("-", 1)[0])
        return (int(year_s), num)
    except (ValueError, IndexError):
        return (0, 0)


def _render_statute_human(record: dict[str, Any], *, link: bool = False) -> str:
    lines: list[str] = []
    statute_token = maybe_link(
        record["statute_id"], consolidated_url_from_id(record["statute_id"]), enabled=link
    )
    lines.append(
        f"{statute_token}  statute provenance @ {record['as_of']}  "
        f"({record['amendment_count']} amendments, "
        f"{record['he_resolved_count']} with originating HE)"
    )
    lines.append("")
    for a in record["amendments"]:
        comm = a["commencement"]
        applied = "" if a["applied_in_replay"] else "  [not applied in replay]"
        amend_token = maybe_link(
            f"L {a['amendment_id']}", statute_url_from_id(a["amendment_id"]), enabled=link
        )
        lines.append(
            f"{amend_token}"
            f"  · enacted {comm['enacted'] or '?'}"
            f" · in force {comm['effective'] or '?'}"
            f" · {comm['legal_status'] or '?'}{applied}"
        )
        if comm.get("title"):
            lines.append(f"    {comm['title']}")
        he = a["originating_he"]
        if he is not None:
            he_id = he.get("he_id") or "?"
            he_token = maybe_link(he_id, he_url_from_canonical(he.get("he_id")), enabled=link)
            bits = [he_token]
            if he.get("title"):
                bits.append(he["title"])
            if he.get("finlex_state"):
                bits.append(f"finlex_state={he['finlex_state']}")
            lines.append("    HE   : " + " · ".join(bits))
            if he.get("ministry"):
                lines.append(
                    f"           {he['ministry']} · {he.get('date_issued') or 'date unknown'}"
                )
        elif a.get("preparatory_available") is False:
            lines.append("    HE   : (amendment source XML unavailable)")
        else:
            lines.append("    HE   : (none found in preliminaryWork)")
        for c in a["committee_refs"]:
            token = maybe_link(c["raw_text"], committee_url_from_raw(c["raw_text"]), enabled=link)
            lines.append(f"    cmte : {token} ({c['canonical_id']})")
        for ev in a["parliament_response_refs"]:
            token = maybe_link(ev["raw_text"], ev_url_from_raw(ev["raw_text"]), enabled=link)
            lines.append(f"    EV   : {token} ({ev['canonical_id']})")
        lines.append("")
    if record["notes"]:
        lines.append("notes:")
        for note in record["notes"]:
            lines.append(f"  - {note}")
    return "\n".join(lines)


def _prep_token(row: dict[str, Any], *, link: bool) -> str:
    """Hyperlink a section-level preparatory row's raw_text by its kind."""
    kind = row.get("kind")
    raw = row.get("raw_text")
    canonical = row.get("canonical_id")
    if not raw:
        return str(raw)
    if kind == PreparatoryReferenceKind.HE.value:
        url = he_url_from_canonical(canonical)
    elif kind == PreparatoryReferenceKind.COMMITTEE_REPORT.value:
        url = committee_url_from_raw(raw)
    elif kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE.value:
        url = ev_url_from_raw(raw)
    else:
        url = None
    return maybe_link(raw, url, enabled=link)


def _render_human(record: dict[str, Any], *, link: bool = False) -> str:
    lines: list[str] = []
    statute_token = maybe_link(
        record["statute_id"], consolidated_url_from_id(record["statute_id"]), enabled=link
    )
    lines.append(f"{statute_token} {record['selector']}  (provenance @ {record['as_of']})")
    lines.append(f"locator           : {record['locator']}")
    lines.append(f"query             : {record['query_type']}")
    in_force = record["in_force"]
    lines.append(f"status            : {in_force['in_force_status']}")
    lines.append("")
    if in_force["available"] and in_force["text"]:
        lines.append(in_force["text"])
    else:
        lines.append("[no text available]")
    lines.append("")
    source = in_force["source_amendment"] or "(base statute)"
    if source != "(base statute)":
        source_token = maybe_link(f"L {source}", statute_url_from_id(source), enabled=link)
        lines.append(f"source amendment  : {source_token}")
    else:
        lines.append(f"source amendment  : {source}")

    he = record["originating_he"]
    if he is None:
        lines.append("originating HE    : (none)")
    else:
        he_token = maybe_link(he["he_id"], he_url_from_canonical(he["he_id"]), enabled=link)
        bits = [he_token]
        if he.get("title"):
            bits.append(he["title"])
        if he.get("finlex_state"):
            bits.append(f"finlex_state={he['finlex_state']}")
        lines.append("originating HE    : " + " · ".join(bits))
        if he.get("ministry"):
            lines.append(f"HE ministry/date  : {he['ministry']} · {he.get('date_issued') or 'date unknown'}")
        enacted_token = maybe_link(
            f"L {he['enacted_law_surfaced']}",
            statute_url_from_id(he["enacted_law_surfaced"]),
            enabled=link,
        )
        lines.append(f"enacted law       : {enacted_token} (surfaced by amendment→HE inversion)")

    prep = record["preparatory"]
    lines.append("preparatory       :")
    if prep:
        for row in prep:
            token = _prep_token(row, link=link)
            lines.append(f"  - {row['kind']}: {token} ({row['canonical_id']})")
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

    emit_json = getattr(args, "json", False)
    hyperlinks_mode = getattr(args, "hyperlinks", "auto") or "auto"
    # Gate: never emit OSC 8 into JSON/structured or a non-tty/dumb stream.
    link = should_hyperlink(hyperlinks_mode, sys.stdout, is_json=emit_json)

    selector = getattr(args, "selector", "") or ""
    if not selector:
        # Statute-level: HE -> [enacted amendments] inversion (no section).
        record = build_statute_provenance(
            statute_id=args.statute_id,
            as_of=getattr(args, "as_of", "") or _today_iso(),
            jurisdiction=jurisdiction,
            data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        )
        if emit_json:
            print(json.dumps(record, ensure_ascii=False, indent=2, default=str))
            return
        print(_render_statute_human(record, link=link))
        return

    record = build_provenance(
        statute_id=args.statute_id,
        selector=selector,
        as_of=getattr(args, "as_of", "") or _today_iso(),
        query_type=getattr(args, "query_type", "in_force"),
        jurisdiction=jurisdiction,
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
    )
    if emit_json:
        print(json.dumps(record, ensure_ascii=False, indent=2, default=str))
        return
    print(_render_human(record, link=link))
