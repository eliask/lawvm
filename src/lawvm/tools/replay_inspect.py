"""lawvm replay-inspect -- inspect one replayed section subtree and metadata."""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from lawvm.core.ir_helpers import irnode_to_text
from lawvm.finland.grafter import replay_xml


def _format_path(path: tuple[tuple[str, str], ...]) -> str:
    if not path:
        return "body"
    return "body / " + " / ".join(f"{kind}:{label}" for kind, label in path)


def _render_tree(node: Any, *, depth: int = 0) -> list[str]:
    label = f":{node.label}" if getattr(node, "label", None) else ""
    own_text = " ".join(str(getattr(node, "text", "") or "").split())
    prefix = "  " * depth
    line = f"{prefix}{node.kind}{label}"
    if own_text:
        preview = own_text if len(own_text) <= 120 else own_text[:117] + "..."
        line += f" :: {preview}"
    lines = [line]
    for child in getattr(node, "children", ()):
        lines.extend(_render_tree(child, depth=depth + 1))
    return lines


def build_replay_inspect_bundle(
    statute_id: str,
    section: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    *,
    chapter: Optional[str] = None,
    part: Optional[str] = None,
) -> dict[str, Any]:
    """Build a replay inspection bundle for one Finnish statute section."""
    master = replay_xml(
        statute_id,
        mode=mode,
        quiet=True,
    )
    node = master.find_section(section, chapter, part)
    if node is None:
        scope_bits = [f"section={section}"]
        if chapter:
            scope_bits.append(f"chapter={chapter}")
        if part:
            scope_bits.append(f"part={part}")
        raise SystemExit(f"section not found in replay tree for {statute_id}: " + ", ".join(scope_bits))
    normalized_section = str(getattr(node, "label", "") or section)
    path = master.state.find_section_path(normalized_section, chapter, part)
    path_steps = [{"kind": kind, "label": label} for kind, label in path] if path is not None else []

    section_text = irnode_to_text(node).strip()
    tree_lines = _render_tree(node)

    return {
        "statute_id": statute_id,
        "title": master.title,
        "mode": mode,
        "section": section,
        "chapter": chapter or "",
        "part": part or "",
        "section_path": _format_path(path) if path is not None else "(materialized path unavailable)",
        "section_path_steps": path_steps,
        "section_kind": str(node.kind),
        "section_label": node.label or "",
        "section_metadata": {
            "child_count": len(node.children),
            "text_length": len(section_text),
            "own_text_length": len(" ".join(str(node.text or "").split())),
            "attrs": dict(node.attrs),
        },
        "section_text": section_text,
        "section_tree": tree_lines,
    }


def _format_attrs(attrs: dict[str, Any]) -> str:
    if not attrs:
        return "(none)"
    parts = [f"{key}={value}" for key, value in sorted(attrs.items(), key=lambda item: str(item[0]))]
    return ", ".join(parts)


def _format_text(bundle: dict[str, Any]) -> str:
    metadata = bundle.get("section_metadata") or {}
    lines = [
        f"Statute : {bundle.get('statute_id') or '(none)'}",
        f"Title   : {bundle.get('title') or '(none)'}",
        f"Mode    : {bundle.get('mode') or '(none)'}",
        f"Section : {bundle.get('section') or '(none)'}",
    ]
    if bundle.get("chapter") or bundle.get("part"):
        scope_bits = []
        if bundle.get("part"):
            scope_bits.append(f"part={bundle['part']}")
        if bundle.get("chapter"):
            scope_bits.append(f"chapter={bundle['chapter']}")
        lines.append(f"Scope   : {' '.join(scope_bits)}")
    lines.extend(
        [
            f"Path    : {bundle.get('section_path') or '(none)'}",
            f"Kind    : {bundle.get('section_kind') or '(none)'}",
            f"Label   : {bundle.get('section_label') or '(none)'}",
            f"Children: {metadata.get('child_count', 0)}",
            f"Text len: {metadata.get('text_length', 0)}",
            "",
            "Section metadata:",
            f"  child_count   : {metadata.get('child_count', 0)}",
            f"  own_text_len  : {metadata.get('own_text_length', 0)}",
            f"  text_len      : {metadata.get('text_length', 0)}",
            f"  attrs         : {_format_attrs(metadata.get('attrs') or {})}",
            "",
            "Replay subtree:",
        ]
    )
    lines.extend(f"  {line}" for line in bundle.get("section_tree", []))
    lines.extend(["", "Section text:"])
    section_text = bundle.get("section_text") or ""
    if section_text:
        lines.extend(f"  {line}" for line in section_text.splitlines())
    else:
        lines.append("  (empty)")
    return "\n".join(lines)


def main(args) -> None:
    bundle = build_replay_inspect_bundle(
        statute_id=args.statute_id,
        section=args.section,
        mode=getattr(args, "mode", "legal_pit"),
        chapter=getattr(args, "chapter", None),
        part=getattr(args, "part", None),
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(_format_text(bundle))


__all__ = ["build_replay_inspect_bundle", "main"]
