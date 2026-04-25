"""lawvm uk-effects -- list/search UK effects-feed rows for one statute."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


@dataclass
class _EffectSummaryContext:
    statute_id: str
    enacted_ir: Any
    oracle_ir: Any
    base_eids: set[str]
    oracle_eids: set[str]
    base_text_map: dict[str, str]
    oracle_eid_map: dict[str, str]
    oracle_text_map: dict[str, str]
    resolver: object | None
    affecting_xml_cache: dict[str, bytes | None]


@dataclass
class _EffectSummary:
    source_pathology: str
    compare_shape: str
    n_ops: int
    candidate: bool
    resolver_eids: tuple[str, ...]


def build_uk_effect_summary_context(
    statute_id: str,
    *,
    archive,  # noqa: ANN001
) -> _EffectSummaryContext:
    from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.tools.uk_effect import _collect_statute_eids
    from lawvm.tools.uk_replay import _archive_url_for_statute

    enacted_ir = None
    oracle_ir = None
    base_eids: set[str] = set()
    oracle_eids: set[str] = set()
    base_text_map: dict[str, str] = {}
    oracle_eid_map: dict[str, str] = {}
    oracle_text_map: dict[str, str] = {}
    resolver = None

    enacted_bytes = archive.get(_archive_url_for_statute(statute_id, pit_date=None, enacted=True))
    if enacted_bytes and len(enacted_bytes) >= 100:
        enacted_maps = extract_eid_map_bytes(enacted_bytes)
        enacted_ir = parse_uk_statute_ir_bytes(
            enacted_bytes,
            statute_id=statute_id,
            version_label="enacted",
            pit_date=None,
            source_path="uk-effects:enacted",
        )
        base_eids = _collect_statute_eids(enacted_ir)
        base_text_map = enacted_maps.get("text_map", {})

    oracle_bytes = archive.get(_archive_url_for_statute(statute_id, pit_date=None, enacted=False))
    if oracle_bytes and len(oracle_bytes) >= 100:
        oracle_ir = parse_uk_statute_ir_bytes(
            oracle_bytes,
            statute_id=statute_id,
            version_label="oracle",
            pit_date=None,
            source_path="uk-effects:oracle",
        )
        oracle_eids = _collect_statute_eids(oracle_ir)
        oracle_maps = extract_eid_map_bytes(oracle_bytes)
        oracle_eid_map = oracle_maps.get("eid_map", {})
        oracle_text_map = oracle_maps.get("text_map", {})
        resolver = UKReplayExecutor(
            oracle_ir,
            eid_map=oracle_eid_map,
            text_map=oracle_text_map,
        )

    return _EffectSummaryContext(
        statute_id=statute_id,
        enacted_ir=enacted_ir,
        oracle_ir=oracle_ir,
        base_eids=base_eids,
        oracle_eids=oracle_eids,
        base_text_map=base_text_map,
        oracle_eid_map=oracle_eid_map,
        oracle_text_map=oracle_text_map,
        resolver=resolver,
        affecting_xml_cache={},
    )


def summarize_uk_effect(
    effect,  # noqa: ANN001
    *,
    archive,  # noqa: ANN001
    context: _EffectSummaryContext,
) -> _EffectSummary:
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_effect_compare_shape,
        classify_uk_effect_source_pathology,
        is_core_uk_effect_compare_candidate,
        is_core_uk_effect_source_candidate,
    )
    from lawvm.uk_legislation.uk_amendment_replay import (
        compile_effect_to_ir_ops,
        extract_provision_element_from_bytes,
        get_affecting_act_xml_from_archive,
    )
    from lawvm.tools.uk_effect import (
        _collect_target_shape,
        _resolve_descendant_presence,
        _resolve_parent_presence,
        _resolve_target_presence,
    )

    affecting_xml = context.affecting_xml_cache.get(effect.affecting_act_id)
    if effect.affecting_act_id not in context.affecting_xml_cache:
        affecting_xml = get_affecting_act_xml_from_archive(effect.affecting_act_id, archive)
        context.affecting_xml_cache[effect.affecting_act_id] = affecting_xml

    extracted = (
        extract_provision_element_from_bytes(affecting_xml, effect.affecting_provisions)
        if affecting_xml
        else None
    )
    ops = compile_effect_to_ir_ops(effect, extracted)
    extracted_tag = extracted.tag.rsplit("}", 1)[-1] if extracted is not None else None
    extracted_text = " ".join(
        t.strip() for t in extracted.itertext() if t and t.strip()
    ) if extracted is not None else ""

    source_pathology = classify_uk_effect_source_pathology(
        extracted_tag=extracted_tag,
        extracted_text=extracted_text,
        op_actions=[op.action.value for op in ops],
        payload_kinds=[str(op.payload.kind) for op in ops if op.payload is not None],
        payload_texts=[op.payload.text or "" for op in ops if op.payload is not None],
        target_paths=["/".join(f"{kind}:{label}" for kind, label in op.target.path) for op in ops],
        effect_type=effect.effect_type,
        is_structural=effect.is_structural,
    )

    op_actions: list[str] = []
    payload_texts: list[str] = []
    resolver_eids: list[str] = []
    base_target_hits: list[bool] = []
    oracle_target_hits: list[bool] = []
    base_descendant_hits: list[bool] = []
    oracle_descendant_hits: list[bool] = []
    base_parent_hits: list[bool] = []
    oracle_parent_hits: list[bool] = []
    base_target_texts: list[str] = []
    oracle_target_texts: list[str] = []
    base_parent_texts: list[str] = []
    oracle_parent_texts: list[str] = []
    base_has_text = False
    base_has_children = False
    oracle_has_text = False
    oracle_has_children = False
    for op in ops:
        op_actions.append(op.action.value)
        if op.payload is not None and op.payload.text:
            payload_texts.append(op.payload.text)
        resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
            op.target,
            resolver=context.resolver,
            base_eids=context.base_eids,
            oracle_eids=context.oracle_eids,
        )
        if not resolver_eid:
            continue
        resolver_eids.append(resolver_eid)
        base_target_hits.append(base_hit)
        oracle_target_hits.append(oracle_hit)
        base_descendant_hit, oracle_descendant_hit = _resolve_descendant_presence(
            resolver_eid,
            base_eids=context.base_eids,
            oracle_eids=context.oracle_eids,
        )
        base_descendant_hits.append(base_descendant_hit)
        oracle_descendant_hits.append(oracle_descendant_hit)
        parent_eid, base_parent_hit, oracle_parent_hit = _resolve_parent_presence(
            resolver_eid,
            base_eids=context.base_eids,
            oracle_eids=context.oracle_eids,
        )
        base_parent_hits.append(base_parent_hit)
        oracle_parent_hits.append(oracle_parent_hit)
        if base_hit:
            hit_has_text, hit_has_children, hit_texts = _collect_target_shape(
                context.enacted_ir,
                eid=resolver_eid,
                text_map=context.base_text_map,
                descendant_hit=base_descendant_hit,
            )
            base_has_text = base_has_text or hit_has_text
            base_has_children = base_has_children or hit_has_children
            base_target_texts.extend(hit_texts)
        if oracle_hit:
            hit_has_text, hit_has_children, hit_texts = _collect_target_shape(
                context.oracle_ir,
                eid=resolver_eid,
                text_map=context.oracle_text_map,
                descendant_hit=oracle_descendant_hit,
            )
            oracle_has_text = oracle_has_text or hit_has_text
            oracle_has_children = oracle_has_children or hit_has_children
            oracle_target_texts.extend(hit_texts)
        if base_parent_hit and context.base_text_map.get(parent_eid):
            base_parent_texts.append(context.base_text_map[parent_eid])
        if oracle_parent_hit and context.oracle_text_map.get(parent_eid):
            oracle_parent_texts.append(context.oracle_text_map[parent_eid])

    compare_shape = classify_uk_effect_compare_shape(
        affecting_title=effect.affecting_title,
        effect_type=effect.effect_type,
        op_actions=op_actions,
        payload_texts=payload_texts,
        resolver_eids=resolver_eids,
        base_target_hits=base_target_hits,
        oracle_target_hits=oracle_target_hits,
        base_descendant_hits=base_descendant_hits,
        oracle_descendant_hits=oracle_descendant_hits,
        base_parent_hits=base_parent_hits,
        oracle_parent_hits=oracle_parent_hits,
        base_target_texts=base_target_texts,
        oracle_target_texts=oracle_target_texts,
        base_parent_texts=base_parent_texts,
        oracle_parent_texts=oracle_parent_texts,
        base_has_text=base_has_text,
        base_has_children=base_has_children,
        oracle_has_text=oracle_has_text,
        oracle_has_children=oracle_has_children,
    )
    candidate = (
        is_core_uk_effect_source_candidate(source_pathology)
        and is_core_uk_effect_compare_candidate(compare_shape)
    )
    return _EffectSummary(
        source_pathology=source_pathology,
        compare_shape=compare_shape,
        n_ops=len(ops),
        candidate=candidate,
        resolver_eids=tuple(resolver_eids),
    )


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.uk_legislation.uk_amendment_replay import (
        load_effects_for_statute_from_archive,
    )

    statute_id: str = args.statute_id
    db_arg: Optional[str] = getattr(args, "db", None)
    affected_contains: str = (getattr(args, "affected_contains", "") or "").lower()
    affecting_contains: str = (getattr(args, "affecting_contains", "") or "").lower()
    effect_type_contains: str = (getattr(args, "effect_type_contains", "") or "").lower()
    limit: Optional[int] = getattr(args, "limit", None)
    applied_only: bool = bool(getattr(args, "applied_only", False))
    structural_only: bool = bool(getattr(args, "structural_only", False))

    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    with Farchive(db_path) as archive:
        effects = load_effects_for_statute_from_archive(statute_id, archive)
        context = build_uk_effect_summary_context(statute_id, archive=archive)

        def _matches(effect) -> bool:  # noqa: ANN001
            if applied_only and not effect.applied:
                return False
            if structural_only and not effect.is_structural:
                return False
            if affected_contains and affected_contains not in effect.affected_provisions.lower():
                return False
            if affecting_contains and affecting_contains not in effect.affecting_provisions.lower():
                return False
            if effect_type_contains and effect_type_contains not in (effect.effect_type or "").lower():
                return False
            return True

        rows = [effect for effect in effects if _matches(effect)]
        rows.sort(key=lambda effect: (effect.effective_date or "9999-99-99", effect.modified, effect.effect_id))
        if limit is not None:
            rows = rows[:limit]

        print(f"Statute: {statute_id}")
        print(f"Matched effects: {len(rows)}")
        if not rows:
            return
        print()

        for effect in rows:
            summary = summarize_uk_effect(effect, archive=archive, context=context)
            print(effect.effect_id)
            print(f"  type:       {effect.effect_type or '(empty)'}")
            print(f"  affected:   {effect.affected_provisions}")
            print(f"  affecting:  {effect.affecting_act_id} {effect.affecting_provisions}")
            print(f"  effective:  {effect.effective_date or '(none)'}")
            print(f"  applied:    {effect.applied}  structural: {effect.is_structural}")
            print(f"  source:     {summary.source_pathology or '(none)'}  ops={summary.n_ops}")
            print(f"  compare:    {summary.compare_shape or '(none)'}")
            print(
                f"  candidate:  "
                f"{'yes' if summary.candidate else 'no'}"
            )
            print()
