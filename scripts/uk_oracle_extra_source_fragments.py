#!/usr/bin/env python3
"""Build packet supplements for UK oracle-extra source-chain leads.

This report is evidence-only. It locates effect-feed/source fragments for
oracle-extra review rows; it does not authorize replay or claim the official
consolidation is wrong.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.uk_legislation.effect_source_selection import (
    extracted_tag_and_text,
    select_source_for_effect,
)
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    load_effects_for_statute_from_archive,
)


_DEFAULT_ARCHIVE = Path("data/uk_legislation.farchive")
_LEG_BASE = "https://www.legislation.gov.uk"
_PROOF_BOUNDARY = (
    "This fragment proves only that the oracle target carries amendment "
    "markup/commentary and that an effect-feed row plus affecting-source XML "
    "fragment can be located. It does not prove commencement, extent, savings, "
    "applicability, target identity, payload ownership, canonical lowering, "
    "replay authorization, or official-oracle error."
)
_FORBIDDEN_SHORTCUTS = (
    "oracle_commentary_as_replay_authorization",
    "oracle_changeid_as_operation_authority",
    "amending_fragment_without_commencement_extent_or_savings_review",
    "review_lead_as_automatic_consolidation_change",
)


@dataclass(frozen=True)
class SourceFragmentSupplementRow:
    statute_id: str
    retained_targets: tuple[str, ...]
    current_urls: tuple[str, ...]
    matched_ops: tuple[Mapping[str, Any], ...]
    unresolved_change_ids: tuple[str, ...]


def build_supplement_rows(
    review_path: Path,
    *,
    archive: Any,
    applicability_mode: str = "effective_date_plus_feed_applied",
) -> list[SourceFragmentSupplementRow]:
    review_rows = _load_rows(review_path)
    rows_by_statute: dict[str, list[Mapping[str, Any]]] = {}
    for row in review_rows:
        statute_id = str(row.get("statute_id") or "")
        if statute_id:
            rows_by_statute.setdefault(statute_id, []).append(row)

    out: list[SourceFragmentSupplementRow] = []
    for statute_id, statute_rows in sorted(rows_by_statute.items()):
        change_rows = [row for row in statute_rows if _change_ids(row)]
        if not change_rows:
            continue
        effects = load_effects_for_statute_from_archive(statute_id, archive)
        effect_by_id = _effect_index(effects)
        extraction_cache: dict[str, Any] = {}
        enacted_extraction_cache: dict[str, Any] = {}
        matched_ops: list[Mapping[str, Any]] = []
        unresolved: list[str] = []
        seen_ops: set[tuple[str, str]] = set()
        for row in change_rows:
            target = str(row.get("target") or "")
            for change_id in _change_ids(row):
                effect = effect_by_id.get(change_id) or effect_by_id.get(
                    _base_change_id(change_id)
                )
                if effect is None:
                    _append_unique(unresolved, change_id)
                    continue
                op_key = (target, effect.effect_id)
                if op_key in seen_ops:
                    continue
                seen_ops.add(op_key)
                op = _matched_op_for_effect(
                    effect=effect,
                    row=row,
                    target=target,
                    archive=archive,
                    applicability_mode=applicability_mode,
                    extraction_cache=extraction_cache,
                    enacted_extraction_cache=enacted_extraction_cache,
                )
                if op:
                    matched_ops.append(op)
                else:
                    _append_unique(unresolved, change_id)
        if matched_ops or unresolved:
            targets = _unique(
                str(row.get("target") or "") for row in change_rows if row.get("target")
            )
            out.append(
                SourceFragmentSupplementRow(
                    statute_id=statute_id,
                    retained_targets=targets,
                    current_urls=tuple(
                        _current_url_for_target(statute_id, target) for target in targets
                    ),
                    matched_ops=tuple(matched_ops),
                    unresolved_change_ids=tuple(unresolved),
                )
            )
    return out


def _matched_op_for_effect(
    *,
    effect: UKEffectRecord,
    row: Mapping[str, Any],
    target: str,
    archive: Any,
    applicability_mode: str,
    extraction_cache: dict[str, Any],
    enacted_extraction_cache: dict[str, Any],
) -> Mapping[str, Any]:
    selection = select_source_for_effect(
        effect=effect,
        archive=archive,
        applicability_mode=applicability_mode,
        extraction_cache=extraction_cache,
        enacted_extraction_cache=enacted_extraction_cache,
        effect_diagnostics_out=[],
    )
    tag_text = extracted_tag_and_text(selection.extracted_el)
    source_preview = _squash(tag_text.text)[:700]
    if not source_preview:
        return {}
    source_bytes = selection.source_context.xml_bytes or b""
    source_hash = hashlib.sha256(source_bytes).hexdigest() if source_bytes else ""
    return {
        "action": f"effect_feed:{effect.effect_type}",
        "affected": effect.affected_provisions or target,
        "source_statute": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "effect_id": effect.effect_id,
        "oracle_change_ids": list(_change_ids(row)),
        "oracle_commentaries": list(_string_tuple(row.get("oracle_commentaries"))),
        "effect_type": effect.effect_type,
        "effective_date": effect.effective_date,
        "source_preview": source_preview,
        "affecting_source_sha256": source_hash,
        "source_fragment_role": "oracle_changeid_effect_feed_affecting_source_fragment",
        "source_fragment_tag": tag_text.tag or "",
        "source_fragment_locator": selection.source_context.locator,
        "source_fragment_authority_layer": selection.source_context.authority_layer,
        "proof_boundary": _PROOF_BOUNDARY,
    }


def _load_rows(path: Path) -> list[Mapping[str, Any]]:
    data = json.loads(path.read_text())
    rows = data.get("rows", data) if isinstance(data, Mapping) else data
    if not isinstance(rows, list):
        raise ValueError(f"{path} does not contain row data")
    return [row for row in rows if isinstance(row, Mapping)]


def _change_ids(row: Mapping[str, Any]) -> tuple[str, ...]:
    return _string_tuple(
        row.get("oracle_change_ids")
        or row.get("oracle_only_uncompiled_addition_change_ids")
        or row.get("change_ids")
    )


def _effect_index(effects: Sequence[UKEffectRecord]) -> dict[str, UKEffectRecord]:
    out: dict[str, UKEffectRecord] = {}
    for effect in effects:
        if not effect.effect_id:
            continue
        out.setdefault(effect.effect_id, effect)
        out.setdefault(_base_change_id(effect.effect_id), effect)
    return out


def _base_change_id(change_id: str) -> str:
    head, sep, tail = str(change_id or "").rpartition("-")
    if sep and tail.isdigit() and head:
        return head
    return str(change_id or "")


def _current_url_for_target(statute_id: str, target: str) -> str:
    parts = target.split("-")
    if len(parts) >= 2 and parts[0] == "article":
        return f"{_LEG_BASE}/{statute_id}/article/{parts[1]}"
    if len(parts) >= 2 and parts[0] == "section":
        return f"{_LEG_BASE}/{statute_id}/section/{parts[1]}"
    if len(parts) >= 2 and parts[0] == "schedule":
        return f"{_LEG_BASE}/{statute_id}/schedule/{parts[1]}"
    return f"{_LEG_BASE}/{statute_id}"


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(item) for item in value if str(item))


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _squash(value: str) -> str:
    return " ".join(value.split())


def emit_json(rows: Sequence[SourceFragmentSupplementRow]) -> str:
    rows_payload = [
        {
            "statute_id": row.statute_id,
            "retained_targets": list(row.retained_targets),
            "current_urls": list(row.current_urls),
            "matched_ops": list(row.matched_ops),
            "unresolved_change_ids": list(row.unresolved_change_ids),
        }
        for row in rows
    ]
    summary = {
        "row_count": len(rows),
        "matched_operation_count": sum(len(row.matched_ops) for row in rows),
        "unresolved_change_id_count": sum(len(row.unresolved_change_ids) for row in rows),
        "forbidden_shortcuts": list(_FORBIDDEN_SHORTCUTS),
    }
    report = EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_oracle_extra_source_fragment_supplement",
        schema="lawvm.uk_oracle_extra_source_fragment_supplement.v1",
        truth_claim="source_fragment_supplement_not_replay_authority",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filtered_summary=summary,
        rows=tuple(rows_payload),
        detail={"proof_boundary": _PROOF_BOUNDARY},
    )
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    from farchive import Farchive

    parser = argparse.ArgumentParser(
        description="Build packet supplement rows from UK oracle-extra ChangeId witnesses."
    )
    parser.add_argument("review", type=Path, help="uk_oracle_extra_review JSON")
    parser.add_argument("--archive", type=Path, default=_DEFAULT_ARCHIVE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    archive = Farchive(args.archive, readonly=True)
    try:
        rows = build_supplement_rows(args.review, archive=archive)
    finally:
        archive.close()
    payload = emit_json(rows)
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
