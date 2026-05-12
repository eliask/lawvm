"""lawvm uk-effect -- inspect one UK effects-feed row end to end.

Archive-backed only. Shows the effect metadata, the extracted affecting-act
source node, and the compiled operations for one effect_id.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import argparse
    from lawvm.core.ir import LegalAddress
    from lawvm.core.ir import IRStatute

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _text_snippet(el: Optional[ET.Element], *, limit: int = 300) -> str:
    if el is None:
        return ""
    text = " ".join(t.strip() for t in el.itertext() if t and t.strip())
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _fmt_target(target) -> str:  # noqa: ANN001
    return "/".join(f"{kind}:{label}" for kind, label in target.path) or str(target)


def _print_payload(node, *, indent: str = "    ") -> None:  # noqa: ANN001
    label = f" {node.label}" if node.label else ""
    snippet = " ".join((node.text or "").split())
    if len(snippet) > 100:
        snippet = snippet[:97] + "..."
    if snippet:
        print(f"{indent}- {node.kind}{label}: {snippet}")
    else:
        print(f"{indent}- {node.kind}{label}")
    for child in node.children:
        _print_payload(child, indent=indent + "  ")


def lowering_rejection_rule_counts(rejections: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rejection in rejections:
        rule_id = str(rejection.get("rule_id") or "unknown")
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def print_lowering_rejections(rejections: list[dict[str, Any]], *, prefix: str = "") -> None:
    print(f"{prefix}Lowering rejections: {len(rejections)}")
    for rule_id, count in lowering_rejection_rule_counts(rejections).items():
        print(f"{prefix}  {rule_id}: {count}")


def _collect_statute_eids(statute: "IRStatute") -> set[str]:
    from lawvm.tools.uk_replay import _get_all_eids

    eids = set(_get_all_eids([statute.body]))
    for schedule in statute.supplements:
        eids.update(_get_all_eids([schedule]))
    return eids


def _eid_present(eid: str, candidates: set[str]) -> bool:
    norm = eid.lower()
    return any(candidate.lower() == norm for candidate in candidates)


def _resolve_target_presence(
    target: "LegalAddress",
    *,
    resolver,  # noqa: ANN001
    base_eids: set[str],
    oracle_eids: set[str],
) -> tuple[str, bool, bool]:
    resolver_eid = resolver._derive_target_eid(target) if resolver is not None else ""
    if not resolver_eid:
        return "", False, False
    return (
        resolver_eid,
        _eid_present(resolver_eid, base_eids),
        _eid_present(resolver_eid, oracle_eids),
    )


def _resolve_parent_presence(
    resolver_eid: str,
    *,
    base_eids: set[str],
    oracle_eids: set[str],
) -> tuple[str, bool, bool]:
    parent_eid = _parent_eid(resolver_eid)
    if not parent_eid:
        return "", False, False
    return (
        parent_eid,
        _eid_present(parent_eid, base_eids),
        _eid_present(parent_eid, oracle_eids),
    )


def _resolve_descendant_presence(
    resolver_eid: str,
    *,
    base_eids: set[str],
    oracle_eids: set[str],
) -> tuple[bool, bool]:
    if not resolver_eid:
        return False, False
    prefix = resolver_eid.lower() + "-"
    base_hit = any(eid.lower().startswith(prefix) for eid in base_eids)
    oracle_hit = any(eid.lower().startswith(prefix) for eid in oracle_eids)
    return base_hit, oracle_hit


def _find_node_by_eid(statute: "IRStatute", eid: str):  # noqa: ANN001
    want = eid.lower()
    stack = [statute.body, *statute.supplements]
    while stack:
        node = stack.pop()
        node_eid = node.attrs.get("eId") or node.attrs.get("id")
        if node_eid and node_eid.lower() == want:
            return node
        stack.extend(reversed(node.children))
    return None


def _collect_target_shape(
    statute: "IRStatute | None",
    *,
    eid: str,
    text_map: dict[str, str],
    descendant_hit: bool,
) -> tuple[bool, bool, list[str]]:
    has_text = False
    has_children = bool(descendant_hit)
    texts: list[str] = []

    node = _find_node_by_eid(statute, eid) if statute is not None else None
    if node is not None:
        norm_text = " ".join((node.text or "").split())
        has_text = bool(norm_text)
        has_children = has_children or bool(node.children)
        if node.text:
            texts.append(node.text)
        mapped_text = text_map.get(eid, "")
        norm_mapped = " ".join(mapped_text.split())
        if norm_mapped and not texts:
            has_text = True
            texts.append(mapped_text)
        return has_text, has_children, texts

    mapped_text = text_map.get(eid, "")
    norm_mapped = " ".join(mapped_text.split())
    if norm_mapped:
        has_text = True
        texts.append(mapped_text)
    return has_text, has_children, texts


def _parent_eid(eid: str) -> str:
    if not eid or "-" not in eid:
        return ""
    return eid.rsplit("-", 1)[0]


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_effect_compare_shape,
        classify_uk_effect_source_pathology,
        is_core_uk_effect_compare_candidate,
        is_core_uk_effect_source_candidate,
    )
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation.uk_amendment_replay import (
        UKReplayExecutor,
        compile_effect_to_ir_ops,
        extract_provision_element_from_bytes,
        get_affecting_act_xml_from_archive,
        load_effects_for_statute_from_archive,
    )
    from lawvm.tools.uk_replay import _archive_url_for_statute

    statute_id: str = args.statute_id
    effect_id: str = args.effect_id
    show_text: bool = getattr(args, "show_text", False)
    show_payload: bool = getattr(args, "show_payload", False)
    db_arg: Optional[str] = getattr(args, "db", None)

    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    with Farchive(db_path) as archive:
        effects = load_effects_for_statute_from_archive(statute_id, archive)
        effect = next((e for e in effects if e.effect_id == effect_id), None)
        if effect is None:
            print(
                f"error: effect_id {effect_id!r} not found for {statute_id} ({len(effects)} effects loaded)",
                file=sys.stderr,
            )
            sys.exit(1)

        xml_bytes = get_affecting_act_xml_from_archive(effect.affecting_act_id, archive)
        extracted = None
        if xml_bytes:
            extracted = extract_provision_element_from_bytes(xml_bytes, effect.affecting_provisions)

        lowering_rejections: list[dict[str, Any]] = []
        ops = compile_effect_to_ir_ops(
            effect,
            extracted,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        extracted_tag = _tag(extracted) if extracted is not None else None
        extracted_text = _text_snippet(extracted, limit=100000) if extracted is not None else ""
        enacted_bytes = archive.get(_archive_url_for_statute(statute_id, pit_date=None, enacted=True))
        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        source_pathology = classify_uk_effect_source_pathology(
            extracted_tag=extracted_tag,
            extracted_text=extracted_text,
            op_actions=[op.action.value for op in ops],
            payload_kinds=[str(op.payload.kind) for op in ops if op.payload is not None],
            payload_texts=[" ".join((op.payload.text or "").split()) for op in ops if op.payload is not None],
            target_paths=[_fmt_target(op.target) for op in ops],
            effect_type=effect.effect_type,
            is_structural=effect.is_structural,
        )
        base_eids: set[str] = set()
        oracle_eids: set[str] = set()
        base_text_map: dict[str, str] = {}
        oracle_text_map: dict[str, str] = {}
        resolver = None
        enacted_ir = None
        oracle_ir = None
        if enacted_bytes:
            enacted_maps = extract_eid_map_bytes(enacted_bytes)
            enacted_ir = parse_uk_statute_ir_bytes(
                enacted_bytes,
                statute_id=statute_id,
                version_label="enacted",
                source_path=_archive_url_for_statute(statute_id, pit_date=None, enacted=True),
            )
            base_eids = _collect_statute_eids(enacted_ir)
            base_text_map = enacted_maps.get("text_map", {})
        if oracle_bytes:
            oracle_ir = parse_uk_statute_ir_bytes(
                oracle_bytes,
                statute_id=statute_id,
                version_label="oracle",
                source_path=oracle_url,
            )
            oracle_eids = _collect_statute_eids(oracle_ir)
            oracle_maps = extract_eid_map_bytes(oracle_bytes)
            oracle_text_map = oracle_maps.get("text_map", {})
            resolver = UKReplayExecutor(
                oracle_ir,
                eid_map=oracle_maps.get("eid_map", {}),
                text_map=oracle_text_map,
            )

    print(f"Statute:            {statute_id}")
    print(f"Effect ID:          {effect.effect_id}")
    print(f"Effect type:        {effect.effect_type or '(empty)'}")
    print(f"Affected provs:     {effect.affected_provisions}")
    print(f"Affecting act:      {effect.affecting_act_id}")
    print(f"Affecting provs:    {effect.affecting_provisions}")
    print(f"Modified:           {effect.modified}")
    print(f"Effective date:     {effect.effective_date or '(none)'}")
    print(f"Applied:            {effect.applied}")
    print(f"Requires applied:   {effect.requires_applied}")
    print(f"Structural:         {effect.is_structural}")
    print(f"Source pathology:   {source_pathology or '(none)'}")
    print()

    print("Extracted source:")
    if extracted is None:
        print("  none")
    else:
        extracted_id = extracted.get("id") or extracted.get("eId") or ""
        print(f"  tag:    {_tag(extracted)}")
        if extracted_id:
            print(f"  id:     {extracted_id}")
        print(f"  text:   {_text_snippet(extracted)}")
        if show_text:
            print()
            print("  full text:")
            print(f"  {_text_snippet(extracted, limit=100000)}")
    print()

    print(f"Compiled ops: {len(ops)}")
    print_lowering_rejections(lowering_rejections)
    if not ops:
        print(f"Replay candidate:   {'yes' if is_core_uk_effect_source_candidate(source_pathology) else 'no'}")
        return

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
        payload_kind = op.payload.kind if op.payload is not None else "-"
        op_actions.append(op.action.value)
        if op.payload is not None and op.payload.text:
            payload_texts.append(op.payload.text)
        resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
            op.target,
            resolver=resolver,
            base_eids=base_eids,
            oracle_eids=oracle_eids,
        )
        if resolver_eid:
            resolver_eids.append(resolver_eid)
            base_target_hits.append(base_hit)
            oracle_target_hits.append(oracle_hit)
            base_descendant_hit, oracle_descendant_hit = _resolve_descendant_presence(
                resolver_eid,
                base_eids=base_eids,
                oracle_eids=oracle_eids,
            )
            base_descendant_hits.append(base_descendant_hit)
            oracle_descendant_hits.append(oracle_descendant_hit)
            parent_eid, base_parent_hit, oracle_parent_hit = _resolve_parent_presence(
                resolver_eid,
                base_eids=base_eids,
                oracle_eids=oracle_eids,
            )
            base_parent_hits.append(base_parent_hit)
            oracle_parent_hits.append(oracle_parent_hit)
            if base_hit:
                hit_has_text, hit_has_children, hit_texts = _collect_target_shape(
                    enacted_ir,
                    eid=resolver_eid,
                    text_map=base_text_map,
                    descendant_hit=base_descendant_hit,
                )
                base_has_text = base_has_text or hit_has_text
                base_has_children = base_has_children or hit_has_children
                base_target_texts.extend(hit_texts)
            if base_parent_hit and base_text_map.get(parent_eid):
                base_parent_texts.append(base_text_map[parent_eid])
            if oracle_hit:
                hit_has_text, hit_has_children, hit_texts = _collect_target_shape(
                    oracle_ir,
                    eid=resolver_eid,
                    text_map=oracle_text_map,
                    descendant_hit=oracle_descendant_hit,
                )
                oracle_has_text = oracle_has_text or hit_has_text
                oracle_has_children = oracle_has_children or hit_has_children
                oracle_target_texts.extend(hit_texts)
            if oracle_parent_hit and oracle_text_map.get(parent_eid):
                oracle_parent_texts.append(oracle_text_map[parent_eid])
        print(f"  {op.op_id}  {op.action:<12}  {_fmt_target(op.target):<48}  payload={payload_kind}")
        if resolver_eid:
            print(
                f"    resolver_eid={resolver_eid}  "
                f"base={'yes' if base_hit else 'no'}  "
                f"oracle={'yes' if oracle_hit else 'no'}"
            )
            if base_descendant_hit or oracle_descendant_hit:
                print(
                    f"    descendants  "
                    f"base={'yes' if base_descendant_hit else 'no'}  "
                    f"oracle={'yes' if oracle_descendant_hit else 'no'}"
                )
            if parent_eid and (not base_hit or not oracle_hit):
                print(
                    f"    parent_eid={parent_eid}  "
                    f"base={'yes' if base_parent_hit else 'no'}  "
                    f"oracle={'yes' if oracle_parent_hit else 'no'}"
                )
        if show_payload and op.payload is not None:
            _print_payload(op.payload)

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
    print()
    print(f"Compare shape:      {compare_shape or '(none)'}")
    print(
        f"Replay candidate:   "
        f"{'yes' if (is_core_uk_effect_source_candidate(source_pathology) and is_core_uk_effect_compare_candidate(compare_shape)) else 'no'}"
    )
