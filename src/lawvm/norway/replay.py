"""Norway point-in-time replay over local Lovdata public archives.

This path is intentionally narrower than EE/UK:

- it uses local public bulk archives, not authenticated Lovdata endpoints
- it is reliable for post-2001 acts whose original LTI promulgation is present
- commencement handling is currently act-level and coarse

The core goal is to make the Norway archive stack executable without pretending
that authenticated structuredRules/timeline services are already available.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from typing import List, Optional

from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.norway.commencement import (
    apply_no_commencement_overrides,
    load_no_commencement_overrides,
)
from lawvm.norway.grafter import (
    NO_PARSE_REPLACE_PROMOTED_TO_INSERT_FOR_RENUMBER,
    apply_no_heading_groups,
    apply_no_ops,
    iter_no_document_change_ops,
    parse_no_heading_groups,
    parse_no_statute,
)
from lawvm.norway.index import NOAmendmentIndex, build_no_amendment_index, load_no_amendment_index
from lawvm.norway.sources import (
    effective_date_from_amendment,
    load_no_amendment_bytes,
    load_no_original_lti_bytes,
    resolve_no_source_path,
)
from lawvm.replay_adjudication import CompileAdjudication

_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NO_REPLAY_MISSING_AMENDMENT_SOURCE = "no_replay_missing_amendment_source"
NO_REPLAY_CONTINGENT_COMMENCEMENT_SKIPPED = "no_replay_contingent_commencement_skipped"
NO_REPLAY_UNKNOWN_EFFECTIVE_SKIPPED = "no_replay_unknown_effective_skipped"
NO_REPLAY_FUTURE_EFFECTIVE_SKIPPED = "no_replay_future_effective_skipped"


# Back-compat for tests and call sites that imported the helper from replay.py.
_effective_date_from_amendment = effective_date_from_amendment


@dataclass
class NOReplayResult:
    base_id: str
    as_of: str
    base_title: str = ""
    base_source_id: str = ""
    replayed: Optional[IRStatute] = None
    amendments_scanned: List[str] = field(default_factory=list)
    amendments_applied: List[str] = field(default_factory=list)
    amendments_skipped_future: List[str] = field(default_factory=list)
    amendments_skipped_contingent: List[str] = field(default_factory=list)
    amendments_skipped_unknown_effective: List[str] = field(default_factory=list)
    amendments_skipped_missing_source: List[str] = field(default_factory=list)
    adjudications: List[CompileAdjudication] = field(default_factory=list)
    n_ops: int = 0
    error: Optional[str] = None


def _normalize_base_id(base_id: str) -> str:
    if base_id.startswith("no/"):
        return base_id
    if base_id.startswith("lov/"):
        return f"no/{base_id}"
    raise ValueError(f"unsupported Norway base_id: {base_id!r}")


def _source_date_from_id(source_id: str) -> str:
    try:
        _no, _kind, date_part = source_id.split("/", 2)
    except ValueError:
        return ""
    return date_part


def replay_no_to_pit(
    base_id: str,
    as_of: str,
    data_dir: Optional[Path] = None,
    index: Optional[NOAmendmentIndex] = None,
    index_path: Optional[Path] = None,
    commencement_path: Optional[Path] = None,
    strict_action_family: bool = False,
    verbose: bool = False,
) -> NOReplayResult:
    """Replay Norway amendment acts through ``as_of`` using local public archives."""
    def _log(msg: str) -> None:
        if verbose:
            print(f"  {msg}", file=sys.stderr)

    data_dir = resolve_no_source_path(data_dir)
    if index is None and index_path is not None:
        index = load_no_amendment_index(index_path)
    if index is None:
        index = build_no_amendment_index(data_dir)
    if commencement_path is not None:
        overrides = load_no_commencement_overrides(commencement_path)
        index = apply_no_commencement_overrides(index, overrides)
    try:
        norm_base_id = _normalize_base_id(base_id)
    except ValueError as exc:
        return NOReplayResult(base_id=base_id, as_of=as_of, error=str(exc))

    result = NOReplayResult(base_id=norm_base_id, as_of=as_of)
    _no, ref_kind, date_part = norm_base_id.split("/", 2)
    if ref_kind != "lov":
        result.error = f"unsupported Norway ref kind: {ref_kind}"
        return result
    year = int(date_part[:4])
    base_bytes = load_no_original_lti_bytes(norm_base_id, data_dir)
    if base_bytes is None:
        result.error = f"no original-act source available for {norm_base_id} (year {year})"
        return result

    base_source_id = f"no/LTI/{norm_base_id.removeprefix('no/')}"
    result.base_source_id = base_source_id
    base_statute = parse_no_statute(base_bytes, norm_base_id)
    result.base_title = base_statute.title
    _log(f"Base loaded for {norm_base_id}: {base_statute.title}")

    ops: list[LegalOperation] = []
    heading_groups = []
    candidates = [
        entry
        for entry in index.entries_for_base(norm_base_id)
    ]
    candidates.sort(key=lambda entry: entry.source_id)
    for entry in candidates:
        source_id = entry.source_id
        result.amendments_scanned.append(source_id)
        effective_date = entry.effective_date
        if entry.effective_status == "contingent":
            result.amendments_skipped_contingent.append(source_id)
            result.adjudications.append(
                CompileAdjudication(
                    kind=NO_REPLAY_CONTINGENT_COMMENCEMENT_SKIPPED,
                    message="Norway replay skipped amendment: commencement is contingent and unresolved.",
                    source_statute=source_id,
                    detail={
                        "rule_id": NO_REPLAY_CONTINGENT_COMMENCEMENT_SKIPPED,
                        "phase": "temporal",
                        "source_id": source_id,
                        "effective_status": entry.effective_status,
                    },
                )
            )
            continue
        if entry.effective_status in {"missing", "unknown"} or effective_date is None:
            result.amendments_skipped_unknown_effective.append(source_id)
            result.adjudications.append(
                CompileAdjudication(
                    kind=NO_REPLAY_UNKNOWN_EFFECTIVE_SKIPPED,
                    message="Norway replay skipped amendment: effective date is missing or unknown.",
                    source_statute=source_id,
                    detail={
                        "rule_id": NO_REPLAY_UNKNOWN_EFFECTIVE_SKIPPED,
                        "phase": "temporal",
                        "source_id": source_id,
                        "effective_status": entry.effective_status,
                    },
                )
            )
            continue
        if effective_date > as_of:
            result.amendments_skipped_future.append(source_id)
            result.adjudications.append(
                CompileAdjudication(
                    kind=NO_REPLAY_FUTURE_EFFECTIVE_SKIPPED,
                    message="Norway replay skipped amendment: effective date is after the requested point in time.",
                    source_statute=source_id,
                    detail={
                        "rule_id": NO_REPLAY_FUTURE_EFFECTIVE_SKIPPED,
                        "phase": "temporal",
                        "source_id": source_id,
                        "effective_status": entry.effective_status,
                        "effective_date": effective_date,
                        "as_of": as_of,
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    },
                )
            )
            continue

        html_bytes = load_no_amendment_bytes(source_id, data_dir)
        if html_bytes is None:
            result.amendments_skipped_missing_source.append(source_id)
            result.adjudications.append(
                CompileAdjudication(
                    kind=NO_REPLAY_MISSING_AMENDMENT_SOURCE,
                    message="Norway replay skipped amendment: source bytes not found.",
                    source_statute=source_id,
                    detail={
                        "rule_id": NO_REPLAY_MISSING_AMENDMENT_SOURCE,
                        "phase": "acquisition",
                        "source_id": source_id,
                        "effective_date": effective_date,
                    },
                )
            )
            continue
        heading_groups.extend(parse_no_heading_groups(html_bytes, norm_base_id))
        groups = [
            (group_base, group_ops)
            for group_base, group_ops in iter_no_document_change_ops(html_bytes, source_id)
            if group_base == norm_base_id
        ]
        for _group_base, group_ops in groups:
            for op in group_ops:
                if op.source is None:
                    ops.append(op)
                    continue
                ops.append(
                    dc_replace(
                        op,
                        source=dc_replace(
                            op.source,
                            enacted=_source_date_from_id(source_id),
                            effective=effective_date,
                        ),
                    )
                )
        if groups:
            result.amendments_applied.append(source_id)

    result.n_ops = len(ops)
    for op in ops:
        if NO_PARSE_REPLACE_PROMOTED_TO_INSERT_FOR_RENUMBER not in op.provenance_tags:
            continue
        result.adjudications.append(
            CompileAdjudication(
                kind=NO_PARSE_REPLACE_PROMOTED_TO_INSERT_FOR_RENUMBER,
                message=(
                    "Norway parser promoted replace to insert because the same target "
                    "is also renumbered in the amendment group."
                ),
                source_statute=op.source.statute_id if op.source else "",
                op_id=op.op_id,
                detail={
                    "rule_id": NO_PARSE_REPLACE_PROMOTED_TO_INSERT_FOR_RENUMBER,
                    "original_action": "replace",
                    "executed_action": "insert",
                    "target": str(op.target),
                },
            )
        )
    try:
        result.replayed = apply_no_ops(
            base_statute,
            ops,
            adjudications_out=result.adjudications,
            strict_action_family=strict_action_family,
        )
        if heading_groups:
            result.replayed = apply_no_heading_groups(result.replayed, heading_groups)
    except ValueError as exc:
        result.error = str(exc)
    return result


def _statute_text(statute: IRStatute) -> str:
    return irnode_to_text(statute.body)
