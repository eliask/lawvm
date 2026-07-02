"""Per-amendment corrigendum-misapplied triage context dumper.

For each misapplied record belonging to ``--amendment-id``:
  1. Loads the official-classified row (correction_index, source_pdf,
     location_desc, raw wrong_text, correct_text, llm_extraction array).
  2. Reads the corrigendum PDF text via ``cs.read_corrigendum_media`` +
     ``_pdf_to_text`` — the authoritative counsel source.
  3. Reads the source XML via ``cs.read_source`` (statute + amendment forms).
  4. For each wrong_text fragment (and corrected_text fragment), checks:
     - exact byte occurrence in source XML
     - case-folded occurrence
     - whitespace-normalised occurrence
  5. Emits a structured per-stable_id triage record. The human/agent
     then decides per row: retry-overlay (full byte pair) OR unresolvable
     with one of {byte_anchor_absent, source_missing_base_text,
     ambiguous_anchor_unresolvable, ellipsis_in_wrong_text}.

Output: ``--out`` JSONL, one record per stable_id, never silently
dropping a misapplied record (AGENTS.md §1.8 conservation).

Run:
    uv run python scripts/triage_corrigenda.py --amendment-id 2002/1248 --out /tmp/triage_2002_1248.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from lawvm.corpus_store import get_corpus_store
from lawvm.finland.corrigendum_records import (
    default_official_records_path,
    load_official_records,
)
from lawvm.tools.corrigendum import _pdf_to_text


def _load_misapplied_by_amendment(amendment_id: str) -> dict[str, list[dict[str, Any]]]:
    """Group misapplied records for one amendment by their stable_id."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mis_path = Path("data/finland/corrigendum_misapplied_fi.jsonl")
    if not mis_path.exists():
        return out
    with mis_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if str(record.get("amendment_id") or "") != amendment_id:
                continue
            stable_id = _stable_id_for_op_id(str(record.get("op_id") or ""))
            out[stable_id].append(record)
    return out


def _stable_id_for_op_id(op_id: str) -> str:
    """Match the runtime's op_id forms to a stable_id, when possible.

    op_id forms:
      ``corr/<amendment_id_numyr>/<idx>``           → lookup in official rows
      ``body_patch/<amendment_mid>/<idx>``           → lookup in official rows
      ``retry/<amendment_id_numyr>/<idx>``           → already an overlay;
                                                       client maps stable_id manually
      ``manual/<amendment_id_numyr>/<i>``            → source-defect; not triaged here
    This script only handles misapplied records (corr/ and body_patch/).
    """
    parts = op_id.split("/")
    if len(parts) < 4:
        return ""
    if parts[0] not in ("corr", "body_patch"):
        return ""
    return ""  # caller resolves via _stable_id_from_misc


def _official_rows_by_amendment(amendment_id_numyr: str) -> dict[str, dict[str, Any]]:
    """Index official records by stable_id; restricted to amendment_id matching.

    The misapplied record's ``op_id`` carries the amendment_mid (e.g.
    ``2002/1248``) but the official row's ``amendment_id`` may be NUM/YEAR
    form (e.g. ``1248/2002``). Both forms are accepted for matching.
    """
    out: dict[str, dict[str, Any]] = {}
    aid_yrnum = amendment_id_numyr  # already YEAR/NUM form per grafter_mid convention
    num, year = aid_yrnum.split("/", 1)
    candidate_aids = {aid_yrnum, f"{num}/{year}"} if "/" in aid_yrnum else {aid_yrnum}
    for row in load_official_records(default_official_records_path()):
        if str(row.get("amendment_id") or "").strip() in candidate_aids:
            sid = str(row.get("stable_id") or "").strip()
            if sid:
                out[sid] = row
    return out


def _match_substring_variants(text: str, source_xml: str) -> dict[str, int]:
    """Return exact, case-folded, ws-norm occurrences for each text fragment."""
    def ws_norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    exact = source_xml.count(text)
    cf = source_xml.lower().count(text.lower())
    sn = ws_norm(source_xml).count(ws_norm(text))
    return {"exact": exact, "case_folded": cf, "ws_normalized": sn}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amendment-id", required=True, help="amendment_mid (YEAR/NUM, e.g. 2002/1248)")
    parser.add_argument("--out", type=Path, default=None, help="output JSONL path")
    args = parser.parse_args(argv)

    amendment_id = args.amendment_id
    cs = get_corpus_store()

    # For body_patch op_ids the stable_id is not directly carried; fall back to
    # grouping by source_pdf+#N pattern. The misapplied record itself has no
    # stable_id — we recover it by reading the official records, grouping on
    # (amendment_id, correction_index) deterministically.
    # mis_by_stable_id therefore remains empty for body_patch op_ids; build
    # the inverse by yielding each misapplied record alone with a synthetic
    # key, then resolve the real stable_id by walking the official rows.

    # Build lookup: official rows keyed by (amendment_id_numyr OR statute_id) +
    # correction_index, in absence of stable_id on misapplied records we
    # enumerate and emit per-misapplied-record outputs (each carries op_id).
    official_rows = load_official_records(default_official_records_path())
    # Canonical storage is now YEAR/NUM (after the 2026-06-28 normalise
    # migration). Match exactly.
    candidate_aids = {amendment_id}

    relevant_mis: list[dict[str, Any]] = []
    with Path("data/finland/corrigendum_misapplied_fi.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if str(record.get("amendment_id") or "") != amendment_id:
                continue
            relevant_mis.append(record)

    if not relevant_mis:
        print(f"no misapplied records found for amendment {amendment_id}", file=sys.stderr)
        return 1

    print(
        f"triaging {len(relevant_mis)} misapplied records for amendment {amendment_id}...",
        file=sys.stderr,
    )

    # Source XML: try both YEAR/NUM and NUM/YEAR forms; the statute 2002/1248
    # is read as '2002/1248', amendment 1248/2002 stored elsewhere.
    # Source XML amendment_id is now year/num form, matching the file storage (post-2026-06-28 normalise).
    source_xml_candidate_forms = [amendment_id]
    source_xml: bytes | None = None
    for candidate in source_xml_candidate_forms:
        try:
            candidate_xml = cs.read_source(candidate)
        except (OSError, RuntimeError, KeyError):
            candidate_xml = None
        if candidate_xml:
            source_xml = candidate_xml
            break
    source_xml_str = source_xml.decode("utf-8", errors="replace") if source_xml else ""

    out_path = args.out or Path(f"/tmp/triage_{amendment_id.replace('/', '_')}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8") as out:
        for mis in relevant_mis:
            op_id = str(mis.get("op_id") or "")
            # Find the matching official row by index. op_id form
            # ``body_patch/<mid>/<idx>`` → idx is the body_patch index from
            # `enumerate(body_patch_list)`, which appends body_patches in the
            # order rows came from load_patch_records, filtered by
            # amendment_id == candidate_aids. We can't perfectly recover the
            # matching official row from op_id alone — fall back to scanning
            # by wrong_text equality.
            wrong_text = str(mis.get("wrong_text") or "")
            correct_text = str(mis.get("correct_text") or "")
            matching_official = None
            for row in official_rows:
                r_aid = str(row.get("amendment_id") or "").strip()
                if r_aid not in candidate_aids:
                    continue
                if (str(row.get("wrong_text") or "") == wrong_text
                        or str(row.get("correct_text") or "") == correct_text):
                    matching_official = row
                    break
            if matching_official is None:
                # Fall back: take the row whose wrong_text contains the
                # misapplied record's wrong_text (LLM ellipsis case)
                for row in official_rows:
                    r_aid = str(row.get("amendment_id") or "").strip()
                    if r_aid not in candidate_aids:
                        continue
                    if wrong_text in str(row.get("wrong_text") or ""):
                        matching_official = row
                        break

            stable_id = ""
            source_pdf_witness = ""
            location_desc = ""
            pdf_text: str | None = None
            if matching_official is not None:
                stable_id = str(matching_official.get("stable_id") or "")
                source_pdf_witness = str(matching_official.get("source_pdf") or "")
                location_desc = str(matching_official.get("location_desc") or "")
                # Try to read corrigendum PDF text
                pdf_name = Path(source_pdf_witness).name if source_pdf_witness else ""
                if pdf_name:
                    try:
                        pdf_bytes = cs.read_corrigendum_media(amendment_id, pdf_name)
                        pdf_text = _pdf_to_text(pdf_bytes, max_pages=2) if pdf_bytes else None
                    except (OSError, KeyError, RuntimeError):
                        pdf_text = None

            # Substring search for each variant
            wrong_search = _match_substring_variants(wrong_text, source_xml_str)
            correct_search = _match_substring_variants(correct_text, source_xml_str)
            # If wrong_text contains "...", search for the prefix before "..."
            stripped_wrong = wrong_text.split("...", 1)[0].strip() if "..." in wrong_text else ""
            prefix_search = _match_substring_variants(stripped_wrong, source_xml_str) if stripped_wrong else {"exact": 0, "case_folded": 0, "ws_normalized": 0}

            out_record = {
                "amendment_id": amendment_id,
                "op_id": op_id,
                "reason": mis.get("reason"),
                "stable_id": stable_id,
                "source_pdf_witness": source_pdf_witness,
                "location_desc": location_desc,
                "misapplied_wrong_text": wrong_text,
                "misapplied_correct_text": correct_text,
                "wrong_text_search_in_source": wrong_search,
                "correct_text_search_in_source": correct_search,
                "wrong_text_prefix_search_in_source": prefix_search,
                "misapplied_count_field": mis.get("count"),
                "official_wrong_text": str(matching_official.get("wrong_text") or "") if matching_official else "",
                "official_correct_text": str(matching_official.get("correct_text") or "") if matching_official else "",
                "official_correction_type": str(matching_official.get("correction_type") or "") if matching_official else "",
                "pdf_text_excerpt": pdf_text[:1200] if pdf_text else "",
                "source_xml_size": len(source_xml) if source_xml else 0,
            }
            out.write(json.dumps(out_record, ensure_ascii=False, sort_keys=False) + "\n")
            written += 1

    print(f"wrote {written} triage records to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
