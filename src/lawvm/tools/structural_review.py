"""structural_review.py — Interactive structural diff review tool.

Always computes semantic diffs live from farchive — never reads from the
publication DB or its build caches.  For corpus-wide summaries, reads from
the scan result file produced by ``--corpus-scan``.

Iterates through sections with structural differences, showing semantic
diff events and letting the user classify each as:
  ok     — correct difference (real replay defect or real source difference)
  noise  — editorial noise (should be filtered by the pipeline)
  source — Finlex source pathology (nothing LawVM can fix)
  bug    — LawVM parse/replay bug to fix
  skip   — skip for now

Classifications persist across runs in .tmp/structural_review_classifications.jsonl.

Usage:
    uv run lawvm structural-review [STATUTE_ID] [--section SECTION]
    uv run lawvm structural-review --stats
    uv run lawvm structural-review --unreviewed
    uv run lawvm structural-review --corpus-summary
    uv run lawvm structural-review --corpus-scan .tmp/statutes.txt
    uv run lawvm structural-review [STATUTE_ID] --dump [--section SECTION]
    uv run lawvm structural-review --corpus-scan FILE --dump
"""
from __future__ import annotations

import json
import warnings
from collections import Counter
from pathlib import Path
from typing import Any


# Suppress projection warnings during bulk review
warnings.filterwarnings("ignore", message=".*out-of-order.*")
warnings.filterwarnings("ignore", message=".*duplicate.*label.*")


_REVIEW_FILE = Path(".tmp/structural_review_classifications.jsonl")
_SCAN_RESULT = Path(".tmp/structural_corpus_scan.json")


def _selector_from_mode(selector_mode: str):
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector

    if selector_mode == "bench_comparable":
        return ConsolidatedArtifactSelector.bench_comparable()
    if selector_mode == "latest_cached_editorial":
        return ConsolidatedArtifactSelector.latest_cached_editorial()
    raise ValueError(f"unsupported oracle selector mode for structural review: {selector_mode}")


def _load_classifications() -> dict[str, dict[str, Any]]:
    """Load existing classifications keyed by statute_id:section."""
    result: dict[str, dict[str, Any]] = {}
    if _REVIEW_FILE.exists():
        for line in _REVIEW_FILE.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            key = entry.get("key", "")
            if key:
                result[key] = entry
    return result


def _save_classification(key: str, classification: str, note: str, events: list[dict]) -> None:
    """Append a classification to the review file."""
    _REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "key": key,
        "classification": classification,
        "note": note,
        "event_count": len(events),
        "event_kinds": sorted(set(e.get("kind", "") for e in events)),
    }
    with open(_REVIEW_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _format_event(event: dict) -> str:
    """Format a semantic diff event for terminal display."""
    kind = event.get("kind", "?")
    path = event.get("semantic_path", "")
    if isinstance(path, list):
        path = " › ".join(path)
    basis = event.get("match_basis", "")
    left_badge = event.get("left_badge", "")
    right_badge = event.get("right_badge", "")
    left_text = event.get("left_text", "")
    right_text = event.get("right_text", "")

    oracle_diag = event.get("oracle_diagnosis", "")
    lines = [f"  \033[1m{kind}\033[0m  {path}  [{basis}]" + (f"  [{oracle_diag}]" if oracle_diag else "")]
    if left_badge or right_badge:
        if left_badge != right_badge:
            lines.append(f"    badge: {left_badge or '—'} → {right_badge or '—'}")
    if left_text != right_text and (left_text or right_text):
        lt = (left_text or "∅")[:120]
        rt = (right_text or "∅")[:120]
        if lt != rt:
            lines.append(f"    LawVM:  {lt}")
            lines.append(f"    Finlex: {rt}")
    return "\n".join(lines)


def _compact_context_value(value: Any) -> str:
    """Render a short, stable preview for section-local audit context."""
    if isinstance(value, dict):
        preferred_keys = ("kind", "code", "rule", "source", "section", "target", "selected", "status", "count", "summary")
        parts: list[str] = []
        for key in preferred_keys:
            if key not in value:
                continue
            item = value.get(key)
            if item in (None, "", [], {}, ()):
                continue
            parts.append(f"{key}={_compact_context_value(item)}")
        if parts:
            return ", ".join(parts)
        fallback: list[str] = []
        for key in sorted(value)[:4]:
            item = value.get(key)
            if item in (None, "", [], {}, ()):
                continue
            fallback.append(f"{key}={_compact_context_value(item)}")
        if fallback:
            suffix = " ..." if len(value) > 4 else ""
            return ", ".join(fallback) + suffix
        return ""
    if isinstance(value, (list, tuple)):
        items = [
            _compact_context_value(item)
            for item in value[:2]
            if item not in (None, "", [], {}, ())
        ]
        if not items:
            return ""
        if len(value) == 1:
            return items[0]
        extra = f" (+{len(value) - 1} more)" if len(value) > 1 else ""
        return "; ".join(items) + extra
    return str(value)


def _section_context_lines(sec_data: dict[str, Any], sd: dict[str, Any]) -> list[str]:
    """Extract already-available compiler/apply/blocker context for display."""
    lines: list[str] = []
    for source_name, payload in (("section", sec_data), ("diff", sd)):
        if not isinstance(payload, dict):
            continue
        for key in sorted(payload):
            key_lower = str(key).lower()
            if not any(token in key_lower for token in ("observation", "blocker", "claim", "compiler", "apply")):
                continue
            value = payload.get(key)
            if value in (None, "", [], {}, ()):
                continue
            preview = _compact_context_value(value)
            if preview:
                lines.append(f"  context[{source_name}].{key}: {preview}")
    return lines


def _sections_with_diffs(data: dict) -> list[tuple[str, dict, list[dict]]]:
    """Extract sections with non-trivial diffs from a live computation result."""
    sections = data.get("sections", {})
    results = []
    for sec_key, sec_data in sorted(sections.items()):
        sd = sec_data.get("semantic_diff")
        if not sd:
            continue
        # editorial_only sections are confirmed repeals / tombstones — not real diffs
        if sd.get("kind") == "editorial_only":
            continue
        events = sd.get("events", [])
        structural = sd.get("structural", 0)
        label = sd.get("label", 0)
        text = sd.get("text", 0)
        if not events and not structural and not label and not text:
            continue
        results.append((sec_key, sd, events))
    return results


# ---------------------------------------------------------------------------
# Dump mode — LLM-consumable non-interactive output
# ---------------------------------------------------------------------------

_REPEAL_BASES = frozenset({"repeal_placeholder", "editorial_repeal_notice"})


def _get_statute_title(statute_id: str, corpus: Any) -> str:
    """Extract docTitle from the oracle XML for the statute.

    Falls back to the statute ID if the XML is absent or has no title element.
    """
    try:
        import lxml.etree as etree
        from lawvm.finland.corpus import get_corpus

        if corpus is None:
            corpus = get_corpus()
        oracle_bytes = corpus.read_oracle(statute_id)
        if oracle_bytes is None:
            return statute_id
        tree = etree.fromstring(oracle_bytes)
        title_el = tree.find(".//{*}docTitle")
        if title_el is not None:
            import re as _re
            text = etree.tostring(title_el, method="text", encoding="unicode").strip()
            # Strip amendment-date suffixes like "(12.01.2024/30)" appended by grafter
            text = _re.sub(r"\s*\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)\s*$", "", text).strip()
            return text if text else statute_id
    except Exception:
        pass
    return statute_id


def _node_label_line(node_dict: dict[str, Any]) -> str:
    """Return the display label for a SemanticStructureNode dict.

    Display priority:
    1. ``visible_label`` when present and non-empty in the dict.
    2. ``label`` when present and not a synthetic opaque token (``__ord_*``).
    3. Empty string, rendered as ``"(unlabeled)"`` for structural items or kind-only
       for other kinds.

    Synthetic opaque labels (``__ord_N__``) must never appear in user-visible output.
    """
    kind = node_dict.get("kind", "")
    visible_label = node_dict.get("visible_label", "")
    raw_label = node_dict.get("label", "")
    label_basis = node_dict.get("label_basis", "explicit")

    if label_basis in _REPEAL_BASES:
        return "kumottu"

    # Prefer visible_label; fall back to label only when it's not a synthetic token.
    if visible_label:
        display = visible_label
    elif raw_label and not raw_label.startswith("__ord_"):
        display = raw_label
    else:
        display = ""

    if kind == "section":
        from lawvm.semantic.model import display_structure_label
        return f"{display_structure_label(display)} §" if display else "§"
    if kind == "subsection":
        return f"{display} mom." if display else "mom."
    if kind == "item":
        return f"{display} kohta" if display else "(unlabeled)"
    if kind == "subitem":
        return f"{display} alakohta" if display else "(unlabeled)"
    if kind == "heading":
        return "otsikko"
    if kind == "intro":
        return "johdanto"
    if kind == "wrapUp":
        return "loppukappale"
    return display or kind


def _facet_text(facets: dict[str, Any], key: str) -> str:
    """Extract text from a facet dict, handling both aligned and raw shapes."""
    f = facets.get(key, {})
    if not isinstance(f, dict):
        return ""
    return f.get("text", "")


def _render_table_grid(table: dict[str, Any], indent: int) -> list[str]:
    """Render a single table dict as a grid with pipe-delimited columns."""
    pad = "  " * indent
    lines: list[str] = []
    lines.append(f"{pad}taulukko:")
    inner = "  " * (indent + 1)

    columns: list[str] = table.get("columns", []) or []
    rows: list[dict[str, Any]] = table.get("rows", []) or []

    if not rows:
        return lines

    # Collect all cell texts so we can compute column widths.
    # Each row's cells list is ordered by column position.
    col_count = max(len(columns), max((len(r.get("cells", [])) for r in rows), default=0))
    if col_count == 0:
        return lines

    # Build a display grid: header row + data rows.
    grid: list[list[str]] = []
    if columns:
        grid.append([str(c) for c in columns])

    for row in rows:
        cells = row.get("cells", [])
        # cells can be dicts with "text" or plain strings
        row_texts: list[str] = []
        for c in cells:
            if isinstance(c, dict):
                row_texts.append(str(c.get("text", "")))
            else:
                row_texts.append(str(c))
        # Pad/truncate to col_count
        while len(row_texts) < col_count:
            row_texts.append("")
        grid.append(row_texts[:col_count])

    if not grid:
        return lines

    # Compute max width per column.
    col_widths = [0] * col_count
    for grid_row in grid:
        for ci, cell in enumerate(grid_row):
            col_widths[ci] = max(col_widths[ci], len(cell))

    for grid_row in grid:
        parts = []
        for ci, cell in enumerate(grid_row):
            parts.append(cell.ljust(col_widths[ci]))
        lines.append(f"{inner}| {' | '.join(parts)} |")

    return lines


def _render_tables(node_dict: dict[str, Any], indent: int) -> list[str]:
    """Render all tables from a normalized node dict."""
    tables = node_dict.get("tables")
    if not isinstance(tables, list) or not tables:
        return []
    lines: list[str] = []
    for table in tables:
        if isinstance(table, dict):
            lines.extend(_render_table_grid(table, indent))
    return lines


def _aligned_facet_texts(facets: dict[str, Any], key: str) -> tuple[str, str]:
    """Extract (left_text, right_text) from an aligned facet entry."""
    f = facets.get(key, {})
    if not isinstance(f, dict):
        return "", ""
    left = f.get("left", {})
    right = f.get("right", {})
    lt = left.get("text", "") if isinstance(left, dict) else ""
    rt = right.get("text", "") if isinstance(right, dict) else ""
    return lt, rt


def _render_sem_node(node_dict: dict[str, Any], indent: int = 2) -> list[str]:
    """Recursively render a SemanticStructureNode dict into display lines."""
    pad = "  " * indent
    lines: list[str] = []

    label_basis = node_dict.get("label_basis", "explicit")
    if label_basis in _REPEAL_BASES:
        lines.append(f"{pad}kumottu")
        return lines

    node_label = _node_label_line(node_dict)
    lines.append(f"{pad}{node_label}")

    inner_pad = "  " * (indent + 1)

    facets = node_dict.get("facets", {})
    if isinstance(facets, dict):
        for fkey, flabel in (("heading", "otsikko"), ("intro", "johdanto"), ("wording", "teksti"), ("wrapUp", "loppukappale")):
            text = _facet_text(facets, fkey)
            if text:
                lines.append(f"{inner_pad}{flabel}: {text}")
    elif node_dict.get("text"):
        lines.append(f"{inner_pad}teksti: {node_dict['text']}")

    # Render table data when present (top-level field on normalized nodes).
    lines.extend(_render_tables(node_dict, indent + 1))

    children = node_dict.get("children", [])
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                lines.extend(_render_sem_node(child, indent=indent + 1))

    return lines


def _render_sem_node_marked(
    node_dict: dict[str, Any],
    indent: int = 2,
    *,
    side_mark: str,
) -> list[str]:
    """Render one side of a node subtree with an explicit diff marker on every line."""
    pad = "  " * indent
    lines: list[str] = []

    label_basis = node_dict.get("label_basis", "explicit")
    if label_basis in _REPEAL_BASES:
        lines.append(f"{side_mark}{pad}kumottu")
        return lines

    node_label = _node_label_line(node_dict)
    lines.append(f"{side_mark}{pad}{node_label}")

    inner_pad = "  " * (indent + 1)
    facets = node_dict.get("facets", {})
    if isinstance(facets, dict):
        for fkey, flabel in (("heading", "otsikko"), ("intro", "johdanto"), ("wording", "teksti"), ("wrapUp", "loppukappale")):
            text = _facet_text(facets, fkey)
            if text:
                lines.append(f"{side_mark}{inner_pad}{flabel}: {text}")
    elif node_dict.get("text"):
        lines.append(f"{side_mark}{inner_pad}teksti: {node_dict['text']}")

    for tline in _render_tables(node_dict, indent + 1):
        lines.append(f"{side_mark}{tline}")

    children = node_dict.get("children", [])
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                lines.extend(_render_sem_node_marked(child, indent=indent + 1, side_mark=side_mark))

    return lines


# ---------------------------------------------------------------------------
# Aligned diff-aware rendering (--dump mode)
# ---------------------------------------------------------------------------

_DIFF_MARK_EQ = "  =  "   # identical
_DIFF_MARK_CH = "  ~  "   # text changed
_DIFF_MARK_PL = "  +L "   # only on LawVM (left) side
_DIFF_MARK_PR = "  +F "   # only on Finlex (right) side
_DIFF_MARK_K  = "  K  "   # kumottu/tombstone
_DIFF_MARK_S  = "  S  "   # structural mismatch
_DIFF_MARK_HD = "     "   # header/context (no marker)


def _render_aligned_node(
    node: dict[str, Any],
    indent: int = 1,
    *,
    compact: bool = False,
) -> list[str]:
    """Render one aligned node with diff-status prefix markers.

    Uses the aligned structure: each node has optional 'left', 'right',
    'match_basis', and aligned 'facets'/'children'.
    """
    pad = "  " * indent
    lines: list[str] = []
    left: dict[str, Any] | None = node.get("left")
    right: dict[str, Any] | None = node.get("right")

    # Determine which side(s) exist
    has_left = left is not None
    has_right = right is not None
    present = left if has_left else right

    if present is None:
        return lines

    kind = node.get("kind", present.get("kind", ""))

    # Check repeal
    left_repeal = has_left and left is not None and left.get("label_basis", "") in _REPEAL_BASES
    right_repeal = has_right and right is not None and right.get("label_basis", "") in _REPEAL_BASES

    if left_repeal or right_repeal:
        if compact and left_repeal and right_repeal:
            return lines  # both kumottu — skip in compact mode
        mark = _DIFF_MARK_K
        node_label = _node_label_line(present)
        lines.append(f"{mark}{pad}{node_label}: kumottu")
        return lines

    # One side missing
    if not has_left:
        assert right is not None
        lines.extend(_render_sem_node_marked(right, indent=indent, side_mark=_DIFF_MARK_PR))
        return lines

    if not has_right:
        assert left is not None
        lines.extend(_render_sem_node_marked(left, indent=indent, side_mark=_DIFF_MARK_PL))
        return lines

    # Both sides present — compare facets
    assert left is not None and right is not None
    node_label = _node_label_line(left)
    aligned_facets = node.get("facets", {})
    any_facet_diff = False
    facet_lines: list[tuple[bool, str]] = []
    inner_pad = "  " * (indent + 1)

    for fkey, flabel in (("heading", "otsikko"), ("intro", "johdanto"), ("wording", "teksti"), ("wrapUp", "loppukappale")):
        lt, rt = _aligned_facet_texts(aligned_facets, fkey)
        if not lt and not rt:
            continue
        if lt == rt:
            facet_lines.append((False, f"{_DIFF_MARK_HD}{inner_pad}{flabel}: {lt}"))
        else:
            any_facet_diff = True
            if lt:
                facet_lines.append((True, f"{_DIFF_MARK_PL}{inner_pad}{flabel}: {lt}"))
            if rt:
                facet_lines.append((True, f"{_DIFF_MARK_PR}{inner_pad}{flabel}: {rt}"))

    # Tables: show from left (LawVM) and/or right (Finlex) side nodes.
    # We show them after facets as informational context lines.
    left_tables = left.get("tables")
    right_tables = right.get("tables")
    left_table_lines = _render_tables(left, indent + 1) if isinstance(left_tables, list) and left_tables else []
    right_table_lines = _render_tables(right, indent + 1) if isinstance(right_tables, list) and right_tables else []

    # Determine node-level marker
    aligned_children = node.get("children", [])
    any_child_diff = any(
        c.get("left") is None or c.get("right") is None
        or any(
            _aligned_facet_texts(c.get("facets", {}), fk) != ("", "")
            and _aligned_facet_texts(c.get("facets", {}), fk)[0] != _aligned_facet_texts(c.get("facets", {}), fk)[1]
            for fk in ("heading", "intro", "wording", "wrapUp")
        )
        for c in aligned_children if isinstance(c, dict)
    )

    if kind != left.get("kind", kind):
        mark = _DIFF_MARK_S
    elif any_facet_diff or any_child_diff:
        mark = _DIFF_MARK_CH
    else:
        mark = _DIFF_MARK_EQ
        if compact:
            return lines  # skip identical nodes

    lines.append(f"{mark}{pad}{node_label}")
    for is_diff, line in facet_lines:
        if compact and not is_diff:
            continue
        lines.append(line)

    # Emit table grids (both sides, if present).
    for tline in left_table_lines:
        lines.append(f"{_DIFF_MARK_HD}{tline}")
    if right_table_lines and right_table_lines != left_table_lines:
        for tline in right_table_lines:
            lines.append(f"{_DIFF_MARK_HD}{tline}")

    # Recurse into children
    for child in aligned_children:
        if isinstance(child, dict):
            lines.extend(_render_aligned_node(child, indent=indent + 1, compact=compact))

    return lines


def _render_section_side(node_dict: dict[str, Any] | None, side_label: str) -> list[str]:
    """Render one side (LawVM or Finlex) of a section diff (legacy stacked mode)."""
    lines = [f"{side_label}:"]
    if node_dict is None:
        lines.append("  puuttuu")
        return lines
    lines.extend(_render_sem_node(node_dict, indent=1))
    return lines


def dump_statute(
    statute_id: str,
    *,
    corpus: Any = None,
    mode: str = "finlex_oracle",
    oracle_selector_mode: str = "bench_comparable",
    compact: bool = False,
    section_filter: str | None = None,
) -> str:
    """Compute structural diffs for one statute and return an LLM-consumable dump.

    Returns an empty string if the oracle is content-absent or there are no
    non-trivial diffs.  Never raises — errors are surfaced in the returned string.
    """
    try:
        if corpus is None:
            from lawvm.finland.corpus import get_corpus
            corpus = get_corpus()

        title = _get_statute_title(statute_id, corpus)
        sections, oracle_content_absent = compute_statute_section_diffs(
            statute_id,
            corpus=corpus,
            mode=mode,
            oracle_selector_mode=oracle_selector_mode,
        )
    except Exception as exc:
        return f"=== {statute_id} — VIRHE ===\n{exc}\n"

    if oracle_content_absent:
        return ""

    diffs = _sections_with_diffs({"sections": sections})
    if section_filter:
        from lawvm.tools._section_debug import resolve_section_key

        try:
            resolved_key = resolve_section_key(sections, section_filter)
        except Exception as exc:
            return f"=== {statute_id} — VIRHE ===\n{exc}\n"
        diffs = [item for item in diffs if item[0] == resolved_key]
    if not diffs:
        return ""

    out: list[str] = [f"=== {statute_id} — {title} ==="]

    for sec_key, sd, _events in diffs:
        diff_kind = sd.get("kind", "?")
        sec_data = sections.get(sec_key, {})
        aligned = sec_data.get("aligned")
        events = sd.get("events", [])

        out.append(f"--- {sec_key} [{diff_kind}] ---")
        if events:
            out.append(f"  events ({len(events)}):")
            for event in events:
                out.append(_format_event(event))

        out.extend(_section_context_lines(sec_data, sd))

        if aligned and isinstance(aligned, dict):
            out.extend(_render_aligned_node(aligned, indent=0, compact=compact))
        else:
            # Fallback: stacked rendering
            for line in _render_section_side(sec_data.get("replay"), "LawVM"):
                out.append(line)
            for line in _render_section_side(sec_data.get("oracle"), "Finlex"):
                out.append(line)

    return "\n".join(out) + "\n"


def dump_single_side(
    statute_id: str,
    *,
    side: str = "replay",
    section_filter: str | None = None,
    corpus: Any = None,
    mode: str = "finlex_oracle",
    oracle_selector_mode: str = "bench_comparable",
) -> str:
    """Dump full text of one side (replay or oracle) for a statute.

    Unlike dump_statute which only shows sections with diffs, this shows
    ALL sections — the complete text as LawVM sees it or as Finlex has it.
    """
    from lawvm.core.ir import IRNode
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.finland.corpus import get_corpus, get_ground_truth_tree
    from lawvm.finland.grafter import replay_xml
    from lawvm.tools.section_keys import extract_ir_sections, extract_oracle_sections
    from typing import cast, Literal

    if corpus is None:
        corpus = get_corpus()

    title = _get_statute_title(statute_id, corpus)
    out: list[str] = [f"=== {statute_id} — {title} ({side}) ==="]

    if side == "replay":
        replay_master = replay_xml(
            statute_id,
            mode=cast(Literal["finlex_oracle", "legal_pit"], mode),
            quiet=True,
            corpus=corpus,
            oracle_selector=_selector_from_mode(oracle_selector_mode),
        )
        if replay_master is None or getattr(replay_master, "materialized_state", None) is None:
            return f"=== {statute_id} — replay failed ===\n"
        sections = extract_ir_sections(replay_master.materialized_state.ir)
        for key in sorted(sections):
            if section_filter and section_filter not in key:
                continue
            node = sections[key]
            text = irnode_to_text(node) if isinstance(node, IRNode) else str(node)
            out.append(f"--- {key} ---")
            out.append(text.strip())
            out.append("")
    else:
        oracle_root = get_ground_truth_tree(
            statute_id,
            corpus=corpus,
            selector=_selector_from_mode(oracle_selector_mode),
        )
        if oracle_root is None:
            return f"=== {statute_id} — no oracle ===\n"
        sections = extract_oracle_sections(oracle_root)
        for key in sorted(sections):
            if section_filter and section_filter not in key:
                continue
            node = sections[key]
            if hasattr(node, "itertext"):
                text = "".join(str(part) for part in node.itertext())
            else:
                text = str(node)
            out.append(f"--- {key} ---")
            out.append(text.strip())
            out.append("")

    return "\n".join(out) + "\n"


def dump_corpus(
    statute_list: str,
    *,
    workers: int = 0,
    mode: str = "finlex_oracle",
    oracle_selector_mode: str = "bench_comparable",
) -> None:
    """Dump structural diffs for all statutes in the list file to stdout.

    Statutes with zero non-trivial diffs are skipped entirely.  Uses sequential
    processing (parallel replay is not safe for this output mode).
    """
    import sys
    from pathlib import Path as _Path

    sids = _Path(statute_list).read_text().strip().splitlines()
    sids = [s.strip() for s in sids if s.strip()]

    from lawvm.finland.corpus import get_corpus
    corpus = get_corpus()

    total = len(sids)
    shown = 0
    for i, sid in enumerate(sids):
        if (i + 1) % 50 == 0:
            print(f"# progress: {i+1}/{total}", file=sys.stderr, flush=True)
        chunk = dump_statute(
            sid,
            corpus=corpus,
            mode=mode,
            oracle_selector_mode=oracle_selector_mode,
        )
        if chunk:
            sys.stdout.write(chunk)
            sys.stdout.write("\n")
            shown += 1

    print(f"# done: {shown}/{total} statutes had diffs", file=sys.stderr)


def is_oracle_content_absent(oracle_root: Any) -> bool:
    """Return True if the oracle XML marks this statute as contentAbsent.

    Checks for an hcontainer element with name="contentAbsent" anywhere in
    the parsed lxml element tree.
    """
    if oracle_root is None:
        return False
    for elem in oracle_root.iter():
        if callable(getattr(elem, "get", None)) and elem.get("name") == "contentAbsent":
            return True
    return False


def compute_statute_section_diffs(
    statute_id: str,
    *,
    corpus: Any = None,
    mode: str = "finlex_oracle",
    oracle_selector_mode: str = "bench_comparable",
    replay_master: Any = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Compute per-section semantic diffs for one statute.

    Performs replay + oracle parse, extracts and reconciles section maps, and
    calls ``build_semantic_support()`` for each section.  Does NOT read from
    the publication DB.

    Returns ``(sections, oracle_content_absent)`` where:
    - ``sections`` is a ``dict[section_key, support_dict]`` (may be empty).
    - ``oracle_content_absent`` is True when the oracle XML carries the
      ``contentAbsent`` marker, meaning LawVM cannot replay the statute.

    Pass ``replay_master`` to reuse an already-replayed master (avoids a second
    replay call when the caller has already replayed the statute, e.g. bench).
    """
    from lawvm.finland.corpus import get_corpus, get_ground_truth_tree
    from lawvm.finland.grafter import replay_xml
    from lawvm.semantic.contracts import build_semantic_support
    from lawvm.semantic.structure import (
        semantic_structure_from_ir,
        semantic_structure_from_oracle,
    )
    from lawvm.tools.section_keys import (
        extract_ir_sections,
        extract_oracle_sections,
        reconcile_unique_unscoped_aliases,
    )
    from typing import cast, Literal

    if corpus is None:
        corpus = get_corpus()

    if replay_master is None:
        replay_master = replay_xml(
            statute_id,
            mode=cast(Literal["finlex_oracle", "legal_pit"], mode),
            quiet=True,
            corpus=corpus,
            oracle_selector=_selector_from_mode(oracle_selector_mode),
        )
    oracle_root = get_ground_truth_tree(
        statute_id,
        corpus=corpus,
        selector=_selector_from_mode(oracle_selector_mode),
    )

    if is_oracle_content_absent(oracle_root):
        return {}, True

    replay_sections = (
        extract_ir_sections(replay_master.materialized_state.ir)
        if replay_master is not None and getattr(replay_master, "materialized_state", None) is not None
        else {}
    )
    oracle_sections = extract_oracle_sections(oracle_root) if oracle_root is not None else {}
    replay_sections, oracle_sections = reconcile_unique_unscoped_aliases(
        replay_sections, oracle_sections,
    )

    sections: dict[str, dict[str, Any]] = {}
    for key in sorted(set(replay_sections) | set(oracle_sections)):
        replay_node = replay_sections.get(key)
        oracle_node = oracle_sections.get(key)
        replay_sem = semantic_structure_from_ir(replay_node) if replay_node is not None else None
        oracle_sem = semantic_structure_from_oracle(oracle_node) if oracle_node is not None else None
        item = build_semantic_support(replay_sem, oracle_sem)
        if item:
            sections[key] = item

    return sections, False


def _compute_live(statute_id: str, *, oracle_selector_mode: str = "bench_comparable") -> dict | None:
    """Compute structural diff live for one statute."""
    sections, oracle_content_absent = compute_statute_section_diffs(
        statute_id,
        oracle_selector_mode=oracle_selector_mode,
    )
    if oracle_content_absent:
        return {"statute_id": statute_id, "oracle_content_absent": True, "sections": {}}
    return {"statute_id": statute_id, "sections": sections}


def review_sections(
    statute_filter: str | None = None,
    section_filter: str | None = None,
    unreviewed_only: bool = True,
    oracle_selector_mode: str = "bench_comparable",
) -> None:
    """Interactive review loop — always computes live."""
    if statute_filter:
        print(f"Computing live structural diff for {statute_filter}...")
        data = _compute_live(statute_filter, oracle_selector_mode=oracle_selector_mode)
        entries = [(statute_filter, data)] if data else []
    else:
        sid_file = Path(".tmp/statutes.txt")
        if not sid_file.exists():
            print("No .tmp/statutes.txt found. Generate one or pass a statute ID.")
            return
        sids = sid_file.read_text().strip().split("\n")
        print(f"Computing live diffs for {len(sids)} statutes (sequential)...")
        entries = []
        for i, sid in enumerate(sids):
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(sids)}", end="\r", flush=True)
            data = _compute_live(sid, oracle_selector_mode=oracle_selector_mode)
            if data:
                entries.append((sid, data))
        print()

    if not entries:
        print("No data found.")
        return

    existing = _load_classifications()
    reviewed = 0
    skipped = 0
    total_diffs = 0

    for sid, data in entries:
        diffs = _sections_with_diffs(data)
        for sec_key, sd, events in diffs:
            if section_filter and section_filter not in sec_key:
                continue
            total_diffs += 1
            key = f"{sid}:{sec_key}"

            if unreviewed_only and key in existing:
                skipped += 1
                continue

            # Display
            print(f"\n{'='*80}")
            print(f"\033[1m{sid} — {sec_key}\033[0m")
            print(f"  structural={sd.get('structural',0)} label={sd.get('label',0)} text={sd.get('text',0)}")
            print(f"  summary: {sd.get('summary','')}")
            sec_data = data.get("sections", {}).get(sec_key, {})
            for line in _section_context_lines(sec_data if isinstance(sec_data, dict) else {}, sd):
                print(line)
            print(f"  events ({len(events)}):")
            for event in events:
                print(_format_event(event))

            prev = existing.get(key)
            if prev:
                print(f"\n  (previously: {prev['classification']}{' — ' + prev.get('note','') if prev.get('note') else ''})")
            print("\n  [o]k / [n]oise / [s]ource / [b]ug / [k]skip / [q]uit")
            try:
                choice = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nStopping.")
                break

            if choice in ("q", "quit"):
                break
            if choice in ("k", "skip", ""):
                continue

            cmap = {"o": "ok", "ok": "ok", "n": "noise", "noise": "noise",
                    "s": "source", "source": "source", "b": "bug", "bug": "bug"}
            classification = cmap.get(choice, "")
            if not classification:
                print(f"  Unknown '{choice}', skipping.")
                continue

            note = ""
            if classification in ("bug", "noise"):
                try:
                    note = input("  note (optional): ").strip()
                except (EOFError, KeyboardInterrupt):
                    pass

            _save_classification(key, classification, note, events)
            existing[key] = {"classification": classification, "note": note}
            reviewed += 1
            print(f"  → {classification}" + (f" ({note})" if note else ""))
        else:
            continue
        break  # quit was triggered

    print(f"\n{'='*80}")
    print(f"Reviewed: {reviewed}, Skipped (already classified): {skipped}, Total with diffs: {total_diffs}")


def _load_scan_results() -> list[dict]:
    """Load corpus scan results from the JSON file produced by --corpus-scan."""
    if not _SCAN_RESULT.exists():
        print(f"No scan results at {_SCAN_RESULT}. Run --corpus-scan first.")
        return []
    return json.loads(_SCAN_RESULT.read_text(encoding="utf-8"))


def show_stats() -> None:
    """Show classification stats from scan results."""
    results = _load_scan_results()
    if not results:
        return
    existing = _load_classifications()

    total_statutes = len(results)
    total_with_diffs = 0
    total_sections_with_diffs = 0
    event_kind_counts: Counter[str] = Counter()

    for r in results:
        if r is None or "error" in r or r.get("oracle_content_absent"):
            continue
        n = r.get("sections_with_diffs", 0)
        if n > 0:
            total_with_diffs += 1
            total_sections_with_diffs += n
        for k, c in r.get("events", {}).items():
            event_kind_counts[k] += c

    print(f"Scan: {total_statutes} statutes, {total_with_diffs} with diffs, {total_sections_with_diffs} sections with diffs")
    print(f"Reviewed (classifications): {len(existing)}")
    print("\nEvent kinds:")
    for k, c in event_kind_counts.most_common():
        print(f"  {c:>6}  {k}")

    if existing:
        print("\nClassifications:")
        cls_counts: dict[str, int] = {}
        for entry in existing.values():
            cls = entry.get("classification", "?")
            cls_counts[cls] = cls_counts.get(cls, 0) + 1
        for cls in sorted(cls_counts):
            print(f"  {cls_counts[cls]:>6}  {cls}")


def show_corpus_summary() -> None:
    """Compact corpus-wide summary: statutes ranked by diff severity."""
    results = _load_scan_results()
    if not results:
        return

    rows = []
    for r in results:
        if r is None or "error" in r or r.get("oracle_content_absent"):
            continue
        n_secs = r.get("sections_with_diffs", 0)
        if n_secs == 0:
            continue
        n_events = r.get("total_events", 0)
        n_struct = sum(
            c for k, c in r.get("events", {}).items()
            if k in {"unit_missing_right", "unit_missing_left", "unit_kind_changed"}
        )
        rows.append((r["statute_id"], n_secs, n_events, n_struct))

    rows.sort(key=lambda r: -r[2])  # sort by total events desc
    print(f"{'Statute':<20} {'Secs':>5} {'Events':>7} {'Struct':>7}")
    print("-" * 45)
    for sid, n_secs, n_events, n_struct in rows[:50]:
        print(f"{sid:<20} {n_secs:>5} {n_events:>7} {n_struct:>7}")
    if len(rows) > 50:
        print(f"  ... and {len(rows) - 50} more statutes")


def show_unreviewed() -> None:
    """Show statutes with unreviewed diffs (requires section keys in scan results)."""
    results = _load_scan_results()
    if not results:
        return
    existing = _load_classifications()

    rows = []
    for r in results:
        if r is None or "error" in r or r.get("oracle_content_absent"):
            continue
        section_keys = r.get("section_keys_with_diffs", [])
        if not section_keys:
            continue
        sid = r["statute_id"]
        reviewed = sum(1 for sec_key in section_keys if f"{sid}:{sec_key}" in existing)
        remaining = len(section_keys) - reviewed
        if remaining > 0:
            rows.append((sid, len(section_keys), reviewed, remaining))

    rows.sort(key=lambda r: -r[3])
    print(f"{'Statute':<20} {'Total':>6} {'Done':>5} {'Left':>5}")
    print("-" * 40)
    for sid, total, done, left in rows[:50]:
        print(f"{sid:<20} {total:>6} {done:>5} {left:>5}")


_EDITORIAL_REPEAL_KIND = "editorial_repeal_notice"


def _scan_one_statute(
    sid: str,
    *,
    oracle_selector_mode: str = "bench_comparable",
) -> dict[str, Any] | None:
    """Worker function for parallel corpus scan. Returns summary dict or None."""
    try:
        data = _compute_live(sid, oracle_selector_mode=oracle_selector_mode)
    except Exception as e:
        return {"statute_id": sid, "error": str(e)}
    if not data:
        return {"statute_id": sid, "error": "no_data"}
    if data.get("oracle_content_absent"):
        return {"statute_id": sid, "oracle_content_absent": True, "sections_with_diffs": 0, "events": {}}
    diffs = _sections_with_diffs(data)
    if not diffs:
        return {"statute_id": sid, "sections_with_diffs": 0, "events": {}}
    event_counts: dict[str, int] = {}
    editorial_repeal_count = 0
    section_keys: list[str] = []
    for sec_key, sd, events in diffs:
        section_keys.append(sec_key)
        for ev in events:
            k = ev.get("kind", "?")
            if k == _EDITORIAL_REPEAL_KIND:
                editorial_repeal_count += 1
            else:
                event_counts[k] = event_counts.get(k, 0) + 1
    return {
        "statute_id": sid,
        "sections_with_diffs": len(diffs),
        "section_keys_with_diffs": section_keys,
        "total_events": sum(event_counts.values()),
        "events": event_counts,
        "editorial_repeal_count": editorial_repeal_count,
    }


def corpus_scan(
    statute_list: str,
    workers: int = 0,
    *,
    oracle_selector_mode: str = "bench_comparable",
) -> None:
    """Parallel corpus scan — compute live structural diffs for all statutes."""
    from functools import partial
    import multiprocessing as mp

    sids = Path(statute_list).read_text().strip().split("\n")
    if workers <= 0:
        workers = min(mp.cpu_count(), 8)

    print(f"Scanning {len(sids)} statutes with {workers} workers...")

    scan_one = partial(_scan_one_statute, oracle_selector_mode=oracle_selector_mode)
    with mp.Pool(workers) as pool:
        results = []
        for i, result in enumerate(pool.imap_unordered(scan_one, sids)):
            results.append(result)
            if (i + 1) % 20 == 0 or (i + 1) == len(sids):
                print(f"  {i+1}/{len(sids)}", end="\r", flush=True)

    print()

    # Aggregate
    global_events: dict[str, int] = {}
    total_diffs = 0
    clean = 0
    content_absent = 0
    editorial_repeal = 0
    total_editorial_repeal_events = 0
    errors = 0
    for r in results:
        if r is None or "error" in r:
            errors += 1
            continue
        if r.get("oracle_content_absent"):
            content_absent += 1
            continue
        if r["sections_with_diffs"] == 0:
            clean += 1
            continue
        total_diffs += r["sections_with_diffs"]
        ern = r.get("editorial_repeal_count", 0)
        if ern:
            editorial_repeal += 1
            total_editorial_repeal_events += ern
        for k, c in r["events"].items():
            global_events[k] = global_events.get(k, 0) + c

    print(
        f"Statutes: {len(sids)} total, {clean} clean, {content_absent} content_absent,"
        f" {editorial_repeal} editorial_repeal, {errors} errors"
    )
    if total_editorial_repeal_events:
        print(f"  ({total_editorial_repeal_events} editorial_repeal_notice events — confirmations, not diffs)")
    print(f"Sections with diffs: {total_diffs}")
    print("\nEvent kinds:")
    for k, c in sorted(global_events.items(), key=lambda x: -x[1]):
        print(f"  {c:>6}  {k}")

    # Save detailed results
    _SCAN_RESULT.parent.mkdir(parents=True, exist_ok=True)
    with open(_SCAN_RESULT, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed results: {_SCAN_RESULT}")


# ---------------------------------------------------------------------------
# Triple-view: LawVM vs Finlex XML vs Finlex HTML
# ---------------------------------------------------------------------------

def _collect_section_struct_labels(
    sem: Any,
    depth: int = 0,
    max_depth: int = 3,
) -> list[tuple[int, str, str]]:
    """Walk a SemanticStructureNode tree and return (depth, kind, display_label) tuples.

    Only structural kinds are emitted (section, subsection, item, subitem).
    Facets (heading, intro, wrapUp) are skipped — we want structure only.
    """
    if sem is None:
        return []
    from lawvm.semantic.model import is_semantic_facet_kind, display_structure_label

    kind = sem.kind
    if is_semantic_facet_kind(kind):
        return []

    # Use visible_label for display; fall back to label only when it's not synthetic.
    # Synthetic opaque labels (e.g. "__ord_2__") must never appear in user output.
    _raw_label = sem.visible_label or (
        sem.label if sem.label and not sem.label.startswith("__ord_") else ""
    )
    display = display_structure_label(_raw_label) if _raw_label else ""

    if kind == "section":
        row_label = f"{display} §" if display else "§"
    elif kind == "subsection":
        row_label = f"{display} mom." if display else "mom."
    elif kind == "item":
        row_label = f"{display} kohta" if display else "kohta"
    elif kind == "subitem":
        row_label = f"{display} alakohta" if display else "alakohta"
    else:
        row_label = f"{kind}:{display}" if display else kind

    rows: list[tuple[int, str, str]] = [(depth, kind, row_label)]
    if depth < max_depth:
        for child in sem.children:
            rows.extend(_collect_section_struct_labels(child, depth + 1, max_depth))
    return rows


def _html_section_key_norm(label: str) -> str:
    """Normalise an HTML section label to a lookup key matching section_keys format."""
    import re
    s = re.sub(r"\s+", " ", label.strip())
    m = re.match(r"^(\d+)\s*([a-z]?)\s*§$", s, flags=re.IGNORECASE)
    if not m:
        # Fallback: strip § and whitespace
        return re.sub(r"[\s§]", "", s).lower()
    num = m.group(1)
    suffix = m.group(2).lower()
    return f"{num}{suffix}"


def dump_triple_view(
    statute_id: str,
    *,
    cache_only: bool = False,
    section_filter: str | None = None,
    oracle_selector_mode: str = "bench_comparable",
) -> None:
    """Print a three-column structural comparison: LawVM | XML oracle | HTML.

    Each structural node is shown on one row with a presence marker [LXH]:
      L — present in LawVM replay
      X — present in Finlex XML oracle
      H — present in Finlex HTML

    HTML provides section-level headings only; subsection/item rows always
    show H='-' (no subsection data available from HTML ToC).

    Args:
        statute_id: e.g. "1993/796"
        cache_only: If True, skip live HTML fetch (use cache only).
        section_filter: Optional section key substring filter.
    """
    from lawvm.finland.corpus import get_corpus, get_ground_truth_tree
    from lawvm.finland.grafter import replay_xml
    from lawvm.finland.finlex_html import html_heading_entries
    from lawvm.semantic.projection import (
        semantic_structure_from_ir,
        semantic_structure_from_oracle,
    )
    from lawvm.tools.section_keys import (
        extract_ir_sections,
        extract_oracle_sections,
        reconcile_unique_unscoped_aliases,
        leaf_section_label,
        section_key_sort_key,
    )
    from typing import cast, Literal

    corpus = get_corpus()

    # --- Replay ---
    replay_master = replay_xml(
        statute_id,
        mode=cast(Literal["finlex_oracle", "legal_pit"], "finlex_oracle"),
        quiet=True,
        corpus=corpus,
        oracle_selector=_selector_from_mode(oracle_selector_mode),
    )
    oracle_root = get_ground_truth_tree(
        statute_id,
        corpus=corpus,
        selector=_selector_from_mode(oracle_selector_mode),
    )

    replay_sections: dict[str, Any] = (
        extract_ir_sections(replay_master.materialized_state.ir)
        if replay_master is not None and getattr(replay_master, "materialized_state", None) is not None
        else {}
    )
    oracle_sections: dict[str, Any] = (
        extract_oracle_sections(oracle_root) if oracle_root is not None else {}
    )
    replay_sections, oracle_sections = reconcile_unique_unscoped_aliases(
        replay_sections, oracle_sections
    )

    # --- HTML section presence ---
    year, num = statute_id.split("/", 1)
    html_entries = html_heading_entries(
        year, num,
        force_refresh=not cache_only,
    )
    html_section_keys: set[str] = set()
    if html_entries is not None:
        for entry in html_entries:
            if entry["kind"] == "section":
                html_section_keys.add(_html_section_key_norm(entry["text"]))

    html_available = html_entries is not None

    # --- Union of section keys ---
    all_keys = sorted(set(replay_sections) | set(oracle_sections), key=section_key_sort_key)
    if section_filter:
        all_keys = [k for k in all_keys if section_filter in k]

    print(f"\n=== {statute_id} — triple-view structure ===")
    if not html_available:
        print("  (HTML unavailable — use without --cache-only or warm cache first)")
    print()

    col_w = 16  # display column width for label
    header = f"{'Marker':<8}  {'Label':<{col_w}}  note"
    print(header)
    print("-" * 60)

    for sec_key in all_keys:
        replay_node = replay_sections.get(sec_key)
        oracle_node = oracle_sections.get(sec_key)

        replay_sem = semantic_structure_from_ir(replay_node) if replay_node is not None else None
        oracle_sem = semantic_structure_from_oracle(oracle_node) if oracle_node is not None else None

        # Determine HTML presence for this section
        sec_leaf = leaf_section_label(sec_key)
        in_html = sec_leaf in html_section_keys if html_available else None

        # Collect structural rows from LawVM and XML
        lawvm_rows = _collect_section_struct_labels(replay_sem) if replay_sem is not None else []
        xml_rows = _collect_section_struct_labels(oracle_sem) if oracle_sem is not None else []

        # Section-level row: [LXH] or [LX?] if HTML unavailable
        l_flag = "L" if replay_node is not None else "-"
        x_flag = "X" if oracle_node is not None else "-"
        if in_html is None:
            h_flag = "?"
        else:
            h_flag = "H" if in_html else "-"

        marker = f"[{l_flag}{x_flag}{h_flag}]"

        # Display label: prefer oracle num, fall back to replay
        if oracle_sem is not None and oracle_sem.visible_label:
            from lawvm.semantic.model import display_structure_label
            dl = display_structure_label(oracle_sem.visible_label)
            sec_display = f"{dl} §" if dl else f"{sec_leaf} §"
        else:
            sec_display = f"{sec_leaf} §"

        print(f"{marker:<8}  {sec_display:<{col_w}}")

        # Subsection/item rows: merge LawVM and XML, HTML always '-' at sub level
        # Build keyed maps from (depth, kind, label) for alignment
        def _row_key(r: tuple[int, str, str]) -> str:
            depth, kind, label = r
            return f"{depth}:{kind}:{label}"

        # Skip section-level row (depth==0) — already printed above
        lawvm_sub = [r for r in lawvm_rows if r[0] > 0]
        xml_sub = [r for r in xml_rows if r[0] > 0]

        # Build ordered union preserving order from both sides
        seen_keys: set[str] = set()
        ordered: list[str] = []
        # Interleave: walk oracle order as primary, fill in replay-only
        for row in xml_sub:
            k = _row_key(row)
            if k not in seen_keys:
                ordered.append(k)
                seen_keys.add(k)
        for row in lawvm_sub:
            k = _row_key(row)
            if k not in seen_keys:
                ordered.append(k)
                seen_keys.add(k)

        lawvm_set = {_row_key(r): r for r in lawvm_sub}
        xml_set = {_row_key(r): r for r in xml_sub}

        for k in ordered:
            r_l = lawvm_set.get(k)
            r_x = xml_set.get(k)
            row = r_l or r_x
            if row is None:
                continue
            depth, kind, sub_label = row
            indent = "  " * depth
            sub_l = "L" if k in lawvm_set else "-"
            sub_x = "X" if k in xml_set else "-"
            sub_h = "-"  # HTML has no subsection-level data
            sub_marker = f"[{sub_l}{sub_x}{sub_h}]"
            print(f"{sub_marker:<8}  {indent}{sub_label:<{col_w - 2 * depth}}")

    print()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Structural diff review tool.")
    parser.add_argument("statute_id", nargs="?")
    parser.add_argument("--section")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--unreviewed", action="store_true")
    parser.add_argument("--corpus-summary", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--corpus-scan", metavar="FILE", help="parallel corpus scan from statute list file")
    parser.add_argument("--workers", type=int, default=0, help="number of parallel workers (default: cpu_count)")
    parser.add_argument("--dump", action="store_true", help="dump structural view (non-interactive)")
    parser.add_argument("--triple", action="store_true", help="with --dump: show three-column LawVM/XML/HTML view")
    parser.add_argument("--cache-only", action="store_true", help="with --triple: skip live HTML fetch, use cache only")
    parser.add_argument("--replay-only", action="store_true", help="dump full LawVM replay text (all sections, no diff)")
    parser.add_argument("--oracle-only", action="store_true", help="dump full Finlex oracle text (all sections, no diff)")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.unreviewed:
        show_unreviewed()
    elif args.corpus_summary:
        show_corpus_summary()
    elif args.corpus_scan:
        corpus_scan(args.corpus_scan, workers=args.workers)
    elif getattr(args, "replay_only", False) or getattr(args, "oracle_only", False):
        if not args.statute_id:
            print("ERROR: statute_id required for --replay-only / --oracle-only", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        side = "replay" if args.replay_only else "oracle"
        print(dump_single_side(args.statute_id, side=side, section_filter=args.section))
    elif args.dump and args.triple:
        if not args.statute_id:
            print("ERROR: statute_id required for --dump --triple", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        dump_triple_view(
            args.statute_id,
            cache_only=args.cache_only,
            section_filter=args.section,
        )
    else:
        review_sections(
            statute_filter=args.statute_id,
            section_filter=args.section,
            unreviewed_only=not args.all,
        )
