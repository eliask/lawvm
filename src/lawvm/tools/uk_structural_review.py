"""UK structural review -- replay-vs-oracle EID+text diff dump.

Provides ``dump_uk_statute(statute_id, *, compact, section_filter, db_path)``
which produces an LLM-consumable, non-interactive compact diff of the LawVM
replay vs the oracle (current consolidation) for one UK statute.

Per-EID classification:
  only_replay  -- replay has it, oracle doesn't
  only_oracle  -- oracle has it, replay doesn't
  text_diff    -- both have it but normalized text differs
  same         -- both have it with matching text (omitted under --compact)

Output is grouped by top-level container bucket for readability.  Identical
nodes are omitted under ``compact=True`` (the primary use case for LLM review).

Data pipeline mirrors ``lawvm uk-misses`` and ``lawvm uk-replay``:
  1. Load enacted base IR
  2. Load oracle bytes -> extract_eid_map_bytes -> text_map
  3. Compile ops via UKReplayPipeline.compile_ops_for_statute
  4. Apply ops via UKReplayPipeline.apply_ops
  5. Walk replayed_ir to build {eid: text} map
  6. Normalize both EID sets with normalize_uk_replay_compare_eids
  7. Classify per EID; render grouped by bucket
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"

# ---------------------------------------------------------------------------
# Text normalization (mirrors uk_grafter._normalize_text_for_grounding)
# ---------------------------------------------------------------------------

_NONWORD_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """Normalize text for apples-to-apples comparison with oracle text_map."""
    s = _NONWORD_RE.sub("", text.lower())
    return _WS_RE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# Walk replayed IRStatute to collect {eid: raw_text}
# ---------------------------------------------------------------------------

def _collect_replay_eid_texts(replayed_ir: Any) -> dict[str, str]:
    """Walk replayed IRStatute body + supplements and collect {eid: raw_text}.

    Uses irnode_to_text so replay text and oracle text derive from the same
    text-extraction logic.
    """
    from lawvm.core.ir_helpers import irnode_to_text, is_zombie

    results: dict[str, str] = {}

    def _walk(node: Any) -> None:
        if is_zombie(node, pit_date=None):
            return
        eid = node.attrs.get("eId") or node.attrs.get("id")
        if eid:
            results[eid] = irnode_to_text(node)
        for child in node.children:
            _walk(child)

    _walk(replayed_ir.body)
    for schedule in replayed_ir.supplements:
        _walk(schedule)

    return results


# ---------------------------------------------------------------------------
# Bucket helper (mirrors uk_misses._bucket_eid)
# ---------------------------------------------------------------------------

def _bucket_eid(eid: str) -> str:
    """Group an EID into its top-level structural container bucket."""
    parts = [p for p in str(eid or "").split("-") if p]
    if not parts:
        return eid
    type_idx: int | None = None
    for i, part in enumerate(parts):
        if part.isalpha():
            type_idx = i
            break
    if type_idx is None:
        return eid
    label_idx = type_idx + 1
    if label_idx >= len(parts):
        return parts[type_idx]
    return "-".join(parts[: label_idx + 1])


# ---------------------------------------------------------------------------
# EID classification
# ---------------------------------------------------------------------------

_CLASS_ONLY_REPLAY = "only_replay"
_CLASS_ONLY_ORACLE = "only_oracle"
_CLASS_TEXT_DIFF = "text_diff"
_CLASS_SAME = "same"

_SNIPPET_LEN = 160


def _snippet(text: str, length: int = _SNIPPET_LEN) -> str:
    text = " ".join(text.split())
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "..."


def _build_norm_to_raw(raw_eids: set[str]) -> dict[str, str]:
    """Build {normalized_eid -> raw_eid} map using _normalize_uk_source_container_eid."""
    from lawvm.uk_legislation.source_adjudication import (
        _normalize_uk_source_container_eid,  # type: ignore[attr-defined]
    )
    norm_to_raw: dict[str, str] = {}
    for raw_eid in raw_eids:
        norm = _normalize_uk_source_container_eid(raw_eid)
        if norm and norm not in norm_to_raw:
            norm_to_raw[norm] = raw_eid
    return norm_to_raw


def _build_oracle_norm_text_map(
    text_map: dict[str, str],
) -> dict[str, str]:
    """Build {normalized_eid -> oracle_normalized_text} for oracle text lookup.

    ``text_map`` from ``extract_eid_map_bytes`` is keyed by raw oracle EIDs and
    values are already ``_normalize_text_for_grounding``-normalized text.  We
    re-key it by normalized EID so it lines up with the normalized compare sets.
    """
    from lawvm.uk_legislation.source_adjudication import (
        _normalize_uk_source_container_eid,  # type: ignore[attr-defined]
    )
    result: dict[str, str] = {}
    for raw_eid, norm_text in text_map.items():
        norm = _normalize_uk_source_container_eid(raw_eid)
        if norm and norm not in result:
            result[norm] = norm_text
    return result


def _classify_eids(
    replay_raw_texts: dict[str, str],
    oracle_norm_text_map: dict[str, str],
    replay_norm_set: frozenset[str],
    oracle_norm_set: frozenset[str],
    replay_norm_to_raw: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Classify every EID in the union of normalized sets.

    Returns dict keyed by normalized EID with:
      kind: one of the _CLASS_* constants
      replay_text: raw replay text (may be empty for oracle-only)
      oracle_text: normalized oracle text (may be empty for replay-only)
    """
    result: dict[str, dict[str, Any]] = {}
    all_norm = replay_norm_set | oracle_norm_set

    for norm_eid in all_norm:
        in_replay = norm_eid in replay_norm_set
        in_oracle = norm_eid in oracle_norm_set
        raw_replay_eid = replay_norm_to_raw.get(norm_eid, norm_eid)
        replay_raw_text = replay_raw_texts.get(raw_replay_eid, "")
        oracle_norm_text = oracle_norm_text_map.get(norm_eid, "")

        if in_replay and not in_oracle:
            result[norm_eid] = {
                "kind": _CLASS_ONLY_REPLAY,
                "replay_text": replay_raw_text,
                "oracle_text": "",
            }
        elif in_oracle and not in_replay:
            result[norm_eid] = {
                "kind": _CLASS_ONLY_ORACLE,
                "replay_text": "",
                "oracle_text": oracle_norm_text,
            }
        else:
            replay_norm_text = _normalize_text(replay_raw_text)
            if replay_norm_text == oracle_norm_text:
                result[norm_eid] = {
                    "kind": _CLASS_SAME,
                    "replay_text": replay_raw_text,
                    "oracle_text": oracle_norm_text,
                }
            else:
                result[norm_eid] = {
                    "kind": _CLASS_TEXT_DIFF,
                    "replay_text": replay_raw_text,
                    "oracle_text": oracle_norm_text,
                }

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_diff(
    classified: dict[str, dict[str, Any]],
    *,
    compact: bool,
    section_filter: str | None,
) -> list[str]:
    """Render classified EIDs grouped by bucket, respecting compact mode."""
    eids_to_show = sorted(classified)
    if section_filter:
        sf_lower = section_filter.lower()
        eids_to_show = [e for e in eids_to_show if sf_lower in e.lower()]

    divergences = [e for e in eids_to_show if classified[e]["kind"] != _CLASS_SAME]
    same_eids = [e for e in eids_to_show if classified[e]["kind"] == _CLASS_SAME]

    lines: list[str] = []

    counts = {
        _CLASS_ONLY_REPLAY: sum(1 for e in divergences if classified[e]["kind"] == _CLASS_ONLY_REPLAY),
        _CLASS_ONLY_ORACLE: sum(1 for e in divergences if classified[e]["kind"] == _CLASS_ONLY_ORACLE),
        _CLASS_TEXT_DIFF: sum(1 for e in divergences if classified[e]["kind"] == _CLASS_TEXT_DIFF),
        _CLASS_SAME: len(same_eids),
    }
    total = sum(counts.values())
    lines.append(
        f"EIDs: total={total}  "
        f"only_replay={counts[_CLASS_ONLY_REPLAY]}  "
        f"only_oracle={counts[_CLASS_ONLY_ORACLE]}  "
        f"text_diff={counts[_CLASS_TEXT_DIFF]}  "
        f"same={counts[_CLASS_SAME]}"
        + ("  (compact: same omitted)" if compact else "")
    )
    lines.append("")

    if not divergences and not (same_eids and not compact):
        lines.append("(no divergences)")
        return lines

    # Group divergences by bucket, sorted by bucket
    buckets: dict[str, list[str]] = {}
    for eid in divergences:
        bucket = _bucket_eid(eid)
        buckets.setdefault(bucket, []).append(eid)

    for bucket in sorted(buckets):
        members = sorted(buckets[bucket])
        lines.append(f"[{bucket}]  ({len(members)} divergence(s))")
        for eid in members:
            entry = classified[eid]
            kind = entry["kind"]
            replay_text = str(entry.get("replay_text") or "")
            oracle_text = str(entry.get("oracle_text") or "")
            if kind == _CLASS_ONLY_REPLAY:
                lines.append(f"  +REPLAY  {eid}")
                if replay_text:
                    lines.append(f"    replay: {_snippet(replay_text)}")
            elif kind == _CLASS_ONLY_ORACLE:
                lines.append(f"  +ORACLE  {eid}")
                if oracle_text:
                    lines.append(f"    oracle: {_snippet(oracle_text)}")
            elif kind == _CLASS_TEXT_DIFF:
                lines.append(f"  ~DIFF    {eid}")
                if replay_text:
                    lines.append(f"    replay: {_snippet(replay_text)}")
                if oracle_text:
                    lines.append(f"    oracle: {_snippet(oracle_text)}")
        lines.append("")

    # Optionally show same nodes (when not compact)
    if not compact and same_eids:
        lines.append(f"[same]  ({len(same_eids)} identical EID(s))")
        for eid in same_eids:
            lines.append(f"  =SAME    {eid}")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def dump_uk_statute(
    statute_id: str,
    *,
    compact: bool = True,
    section_filter: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Compute replay-vs-oracle EID+text diff for one UK statute.

    Returns an LLM-consumable string.  Never raises -- errors are surfaced in
    the returned string with an error marker.

    Args:
        statute_id:     e.g. "ukpga/1978/30"
        compact:        if True, omit identical nodes (only show divergences)
        section_filter: optional substring filter on EIDs (case-insensitive)
        db_path:        path to uk_legislation.farchive; defaults to the repo default
    """
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids

    resolved_db = db_path if db_path is not None else _DEFAULT_DB
    if not resolved_db.exists():
        return (
            f"=== {statute_id} — UK structural review ERROR ===\n"
            f"Archive not found at {resolved_db}\n"
        )

    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    authority_rejections: list[dict[str, Any]] = []

    with Farchive(resolved_db) as archive:
        # 1. Load enacted base
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        base_bytes = archive.get(enacted_url)
        if base_bytes is None:
            return (
                f"=== {statute_id} — UK structural review ERROR ===\n"
                f"Enacted XML missing from archive: {enacted_url}\n"
            )
        base_ir = parse_uk_statute_ir_bytes(
            base_bytes,
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )

        # 2. Load oracle and extract EID+text maps
        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        if oracle_bytes is None:
            return (
                f"=== {statute_id} — UK structural review ERROR ===\n"
                f"Oracle XML missing from archive: {oracle_url}\n"
            )
        oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=None)
        eid_map: dict[str, str] = oracle_data.get("eid_map", {})
        text_map: dict[str, str] = oracle_data.get("text_map", {})
        oracle_physical_eid_aliases: dict[str, str] = oracle_data.get(
            "physical_eid_aliases", {}
        )
        oracle_visible_number_eid_aliases: dict[str, str] = oracle_data.get(
            "visible_number_eid_aliases", {}
        )
        # oracle current EIDs (raw, before normalization)
        current_eids: set[str] = set(eid_map.values())

        # 3. Compile ops
        pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

        # 4. Apply ops
        replayed_ir = pipeline.apply_ops(
            base_ir,
            ops,
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=True,
        )

    # 5. Collect replay EID texts (raw EID -> raw text)
    replay_eid_texts = _collect_replay_eid_texts(replayed_ir)
    replayed_eids: set[str] = set(replay_eid_texts)

    # 6. Normalize both sets with the canonical normalizer (consistent with uk-misses)
    replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
        replayed_eids,
        current_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )

    # 7. Build norm->raw lookup for replay (needed to find text for normalized EIDs)
    replay_norm_to_raw = _build_norm_to_raw(replayed_eids)

    # 8. Build norm-keyed oracle text map for apples-to-apples text comparison
    oracle_norm_text_map = _build_oracle_norm_text_map(text_map)

    # 9. Classify each normalized EID
    classified = _classify_eids(
        replay_eid_texts,
        oracle_norm_text_map,
        replay_norm_set=frozenset(replay_compare_eids),
        oracle_norm_set=frozenset(oracle_compare_eids),
        replay_norm_to_raw=replay_norm_to_raw,
    )

    # 10. Compute header stats
    common = replay_compare_eids & oracle_compare_eids
    only_oracle = oracle_compare_eids - replay_compare_eids
    only_replay = replay_compare_eids - oracle_compare_eids
    similarity = len(common) / max(len(replay_compare_eids), len(oracle_compare_eids), 1)

    n_rejections = (
        len(effect_feed_parse_rejections)
        + len(lowering_rejections)
        + len(authority_rejections)
    )

    # 11. Render
    lines: list[str] = [
        f"=== {statute_id} — UK structural review ===",
        (
            f"Similarity: {similarity:.1%}  "
            f"replay={len(replay_compare_eids)}  oracle={len(oracle_compare_eids)}  "
            f"common={len(common)}  "
            f"only_replay={len(only_replay)}  only_oracle={len(only_oracle)}"
        ),
        f"Compile: ops={len(ops)}  rejections={n_rejections}",
        f"Archive: {resolved_db}",
        "",
    ]
    lines.extend(
        _render_diff(classified, compact=compact, section_filter=section_filter)
    )

    return "\n".join(lines) + "\n"
