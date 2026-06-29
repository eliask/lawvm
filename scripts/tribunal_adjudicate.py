"""Triage worker for misapplied corrigenda.

Per-misapplied-record write path: appends one verdict (either retry-overlay
or unresolvable-overlay) against a given ``stable_id`` to the canonical
overwrite-persistent carrier. The script NEVER silently overwrites an
existing overlay for the same ``stable_id`` — duplicates emit a typed
diagnostic and skip (§1.8 conservation).

Subagents invoke one of the two subcommands to record verdicts:

  retry       — record a verified byte-exact retry patch:
                --stable-id, --amendment-id (YEAR/NUM),
                --source-pdf-witness (locator),
                --correction-type (johtolause/body_text/table/prose/footnote/...),
                --family (label e.g. llm_ellipsis_span, xml_paragraph_merge),
                --wrong-text (byte-exact substring that occurs in source XML),
                --correct-text (replacement bytes)

  unresolvable — record that the corrigendum genuinely cannot apply:
                --stable-id, --amendment-id,
                --source-pdf-witness, --correction-type,
                --evidence-kind ∈ {source_missing_base_text, byte_anchor_absent,
                                  semantic_only, ambiguous_anchor_unresolvable},
                --evidence-detail (free-text; bounded),
                --pit-filter (bool flag, default false)
                --manual-review-required (bool flag, default true)

The script writes after computing today's ISO date and emits the
existing-overlay ledger as a side-effect diagnostic so operators can audit
collision points.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
RETRY_PATH = _REPO_ROOT / "data" / "finland" / "corrigendum_retry_overlays_fi.jsonl"
UNRESOLVABLE_PATH = _REPO_ROOT / "data" / "finland" / "corrigendum_unresolvable_fi.yaml"
_RETIRED_LOG = _REPO_ROOT / "data" / "finland" / "corrigendum_retired_overlays.jsonl"

RETRY_KIND_OPTIONS = {
    "llm_ellipsis_span": "FINLAND.CORR.EXTRACTION_RETRY_LLM_ELLIPSIS",
    "xml_paragraph_merge": "FINLAND.CORR.EXTRACTION_RETRY_XML_PARAGRAPH_MERGE",
    "truncated_body": "FINLAND.CORR.EXTRACTION_RETRY_TRUNCATED_BODY",
    "extraction_retry": "FINLAND.CORR.EXTRACTION_RETRY",
}
UNRESOLVABLE_KINDS = {
    "source_missing_base_text",
    "byte_anchor_absent",
    "already_applied_in_source",
    "semantic_only",
    "ambiguous_anchor_unresolvable",
}


def _today_iso() -> str:
    return date.today().isoformat()


# File-lock context manager for atomic read-modify-write across concurrent
# invocations (parallel triage subagents). The lock is held on a sentinel
# ``*.lock`` file co-located with the data file and acquired with fcntl
# exclusive lock (blocking). This guarantees serialized appends even when
# 5 subagents call `tribunal_adjudicate.py` concurrently.
class _FileLock:
    """fcntl.flock(LOCK_EX) on a sentinel .lock file."""

    def __init__(self, target: Path) -> None:
        self.path = target.with_suffix(target.suffix + ".lock")
        self._fh = None

    def __enter__(self) -> "_FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def _load_jsonl_records_locked(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _save_jsonl_records_locked(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp + rename so a partially-written file is never seen
    # by a concurrent reader (the fcntl lock also serializes, but tmp+rename
    # protects against process death mid-write).
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    os.replace(tmp, path)


def _load_yaml_list_locked(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path} expected list, got {type(raw)!r}")
    return [e for e in raw if isinstance(e, dict)]


def _save_yaml_list_locked(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(
            "# Auto-managed by scripts/tribunal_adjudicate.py — "
            "verdicts appended by triage orchestration. Do not edit by hand "
            "without preserving the typed schema; see "
            "corrigendum_unresolvable_fi.yaml top-of-file doctrine header.\n\n"
        )
        yaml.safe_dump(
            records,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=160,
        )
    os.replace(tmp, path)


# Public read/save helpers (no lock; safe for one-writer-at-a-time repr-from
# callers like tests). Production write paths go through the locking wrappers.
def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    return _load_jsonl_records_locked(path)


def _save_jsonl_records(records: list[dict[str, Any]], path: Path) -> None:
    _save_jsonl_records_locked(records, path)


def _load_yaml_list(path: Path) -> list[dict[str, Any]]:
    return _load_yaml_list_locked(path)


def _save_yaml_list(records: list[dict[str, Any]], path: Path) -> None:
    _save_yaml_list_locked(records, path)


def add_retry(
    *,
    stable_id: str,
    amendment_id: str,
    source_pdf_witness: str,
    correction_type: str,
    family: str,
    wrong_text: str,
    correct_text: str,
) -> int:
    if not (stable_id and wrong_text and correct_text and wrong_text != correct_text):
        print(
            "ERROR: --stable-id, --wrong-text, --correct-text required and wrong!=correct",
            file=sys.stderr,
        )
        return 2
    family_label = family or "extraction_retry"
    rule_id = RETRY_KIND_OPTIONS.get(family_label, "FINLAND.CORR.EXTRACTION_RETRY")
    # Read-modify-write under exclusive lock — concurrent triage subagents
    # that call this entrypoint in parallel serialize here, so a stable_id
    # written by one agent is observable in the next agent's load.
    with _FileLock(RETRY_PATH):
        records = _load_jsonl_records_locked(RETRY_PATH)
        if any(str(r.get("stable_id") or "") == stable_id for r in records):
            print(
                f"WARN: existing retry overlay for stable_id={stable_id!r} — refusing to overwrite; use manual repair if you need to replace. Skipping.",
                file=sys.stderr,
            )
            return 3
        record = {
            "stable_id": stable_id,
            "rule_id": rule_id,
            "family": family_label,
            "amendment_id": amendment_id,
            "source_pdf_witness": source_pdf_witness,
            "correction_type": correction_type or "johtolause",
            "span_verified": True,
            "verified_at": _today_iso(),
            "patches": [{"wrong_text": wrong_text, "correct_text": correct_text}],
        }
        records.append(record)
        records.sort(key=lambda r: str(r.get("stable_id") or ""))
        _save_jsonl_records_locked(records, RETRY_PATH)
    print(f"appended retry-overlay for stable_id={stable_id!r} (now {len(records)} total)")
    return 0


def retire_overlay(*, stable_id: str, overlay_kind: str) -> int:
    """Remove an existing verdict from one of the overlay files.

    Used during re-triage: when a subagent re-investigates a record that
    was previously classified as ``unresolvable`` and discovers a working
    byte-exact retry patch, this command retires the prior verdict so a
    retry-overlay can subsequently be written for the same stable_id
    without overwrites being silently rejected.

    The retire is recorded in ``data/finland/corrigendum_retired_overlays.jsonl``
    (a separate audit-trail file) so no verdict silently vanishes — the
    retired record is preserved with the retire reason and timestamp
    (AGENTS.md §1.8 conservation).
    """
    if not stable_id:
        print("ERROR: --stable-id required", file=sys.stderr)
        return 2
    if overlay_kind not in ("retry", "unresolvable"):
        print("ERROR: --overlay-kind must be 'retry' or 'unresolvable'", file=sys.stderr)
        return 2
    retired_path = _RETIRED_LOG
    today = _today_iso()
    found = False
    if overlay_kind == "retry":
        with _FileLock(RETRY_PATH):
            records = _load_jsonl_records_locked(RETRY_PATH)
            new_records = []
            for r in records:
                if str(r.get("stable_id") or "") == stable_id:
                    found = True
                    retired_entry = dict(r)
                    retired_entry["_retired_at"] = today
                    retired_entry["_retired_from"] = "retry"
                    _append_audit_log(retired_path, retired_entry)
                else:
                    new_records.append(r)
            if not found:
                print(f"WARN: no retry-overlay found for stable_id={stable_id!r}", file=sys.stderr)
                return 3
            _save_jsonl_records_locked(new_records, RETRY_PATH)
    else:  # unresolvable
        with _FileLock(UNRESOLVABLE_PATH):
            records = _load_yaml_list_locked(UNRESOLVABLE_PATH)
            new_records = []
            for r in records:
                if not isinstance(r, dict):
                    new_records.append(r)
                    continue
                if str(r.get("stable_id") or "") == stable_id:
                    found = True
                    retired_entry = dict(r)
                    retired_entry["_retired_at"] = today
                    retired_entry["_retired_from"] = "unresolvable"
                    _append_audit_log(retired_path, retired_entry)
                else:
                    new_records.append(r)
            if not found:
                print(f"WARN: no unresolvable-overlay found for stable_id={stable_id!r}", file=sys.stderr)
                return 3
            _save_yaml_list_locked(new_records, UNRESOLVABLE_PATH)
    print(f"retired {overlay_kind}-overlay for stable_id={stable_id!r} → audit log {retired_path}")
    return 0


def _append_audit_log(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(__import__("json").dumps(entry, ensure_ascii=False, sort_keys=False))
        f.write("\n")


def add_unresolvable(
    *,
    stable_id: str,
    amendment_id: str,
    source_pdf_witness: str,
    correction_type: str,
    evidence_kind: str,
    evidence_detail: str,
    pit_filter: bool,
    manual_review_required: bool,
) -> int:
    if not (stable_id and evidence_kind):
        print("ERROR: --stable-id and --evidence-kind required", file=sys.stderr)
        return 2
    if evidence_kind not in UNRESOLVABLE_KINDS:
        print(
            f"ERROR: --evidence-kind {evidence_kind!r} not in closed set {sorted(UNRESOLVABLE_KINDS)}",
            file=sys.stderr,
        )
        return 2
    with _FileLock(UNRESOLVABLE_PATH):
        records = _load_yaml_list_locked(UNRESOLVABLE_PATH)
        if any(str(r.get("stable_id") or "") == stable_id for r in records):
            print(
                f"WARN: existing unresolvable overlay for stable_id={stable_id!r} — refusing to overwrite; skipping.",
                file=sys.stderr,
            )
            return 3
        rule_id = f"FINLAND.CORR.UNRESOLVABLE.{evidence_kind.upper()}"
        record = {
            "stable_id": stable_id,
            "rule_id": rule_id,
            "amendment_id": amendment_id,
            "source_pdf_witness": source_pdf_witness,
            "correction_type": correction_type or "johtolause",
            "evidence": {
                "kind": evidence_kind,
                "detail": evidence_detail,
                "alternative_pit_filter": pit_filter,
            },
            "manual_review_required": manual_review_required,
            "verified_at": _today_iso(),
        }
        records.append(record)
        records.sort(
            key=lambda r: (
                str(r.get("amendment_id") or ""),
                str(r.get("stable_id") or ""),
                str(r.get("rule_id") or ""),
            )
        )
        _save_yaml_list_locked(records, UNRESOLVABLE_PATH)
    print(f"appended unresolvable-overlay for stable_id={stable_id!r} (now {len(records)} total)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("retry", help="add a verified retry-overlay record")
    r.add_argument("--stable-id", required=True)
    r.add_argument("--amendment-id", required=True)
    r.add_argument("--source-pdf-witness", default="")
    r.add_argument("--correction-type", default="johtolause")
    r.add_argument("--family", default="extraction_retry")
    r.add_argument("--wrong-text", required=True)
    r.add_argument("--correct-text", required=True)

    u = sub.add_parser("unresolvable", help="add an unresolvable overlay record")
    u.add_argument("--stable-id", required=True)
    u.add_argument("--amendment-id", required=True)
    u.add_argument("--source-pdf-witness", default="")
    u.add_argument("--correction-type", default="johtolause")
    u.add_argument("--evidence-kind", required=True, choices=sorted(UNRESOLVABLE_KINDS))
    u.add_argument("--evidence-detail", default="")
    u.add_argument("--pit-filter", action="store_true")
    u.add_argument("--no-manual-review", dest="manual_review_required", action="store_false")
    u.set_defaults(manual_review_required=True)

    rt = sub.add_parser("retire", help="retire an existing verdict (audit-logged) to allow re-triage")
    rt.add_argument("--stable-id", required=True)
    rt.add_argument("--overlay-kind", required=True, choices=("retry", "unresolvable"))

    args = p.parse_args(argv)
    if args.cmd == "retry":
        return add_retry(
            stable_id=args.stable_id,
            amendment_id=args.amendment_id,
            source_pdf_witness=args.source_pdf_witness,
            correction_type=args.correction_type,
            family=args.family,
            wrong_text=args.wrong_text,
            correct_text=args.correct_text,
        )
    if args.cmd == "unresolvable":
        return add_unresolvable(
            stable_id=args.stable_id,
            amendment_id=args.amendment_id,
            source_pdf_witness=args.source_pdf_witness,
            correction_type=args.correction_type,
            evidence_kind=args.evidence_kind,
            evidence_detail=args.evidence_detail,
            pit_filter=args.pit_filter,
            manual_review_required=args.manual_review_required,
        )
    if args.cmd == "retire":
        return retire_overlay(stable_id=args.stable_id, overlay_kind=args.overlay_kind)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
