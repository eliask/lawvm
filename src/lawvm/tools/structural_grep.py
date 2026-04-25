"""structural_grep.py — Corpus-wide semantic structure query tool.

Iterates over corpus statutes, builds semantic structures (replay IR +
oracle projection), and applies user-specified filters on the resulting
semantic structure nodes.  Reports matches with statute_id, section_key,
and match details.

All filters combine with AND logic.

Usage:
    lawvm structural-grep --oracle-text-matches "kumottu" --not-diff-kind editorial_only
    lawvm structural-grep --replay-missing --oracle-text-matches "kumottu"
    lawvm sgrep --has-op REPEAL --replay-text-matches "." --count
    lawvm sgrep --diff-kind text_only --verbose --corpus .tmp/statutes.txt
"""
from __future__ import annotations

import json
import multiprocessing as mp
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message=".*out-of-order.*")
warnings.filterwarnings("ignore", message=".*duplicate.*label.*")

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Filter specification
# ---------------------------------------------------------------------------


@dataclass
class StructuralGrepFilter:
    """All user-specified filter predicates combined with AND logic."""

    # Structural predicate filters
    replay_label_basis: list[str] = field(default_factory=list)
    oracle_label_basis: list[str] = field(default_factory=list)
    diff_kind: list[str] = field(default_factory=list)
    diff_event: list[str] = field(default_factory=list)
    has_children: bool | None = None  # True / False / None (don't care)
    replay_missing: bool = False
    oracle_missing: bool = False

    # Text regex filters
    oracle_text_matches: str = ""
    replay_text_matches: str = ""
    oracle_text_not_matches: str = ""
    replay_text_not_matches: str = ""

    # Op-level filters
    has_op: list[str] = field(default_factory=list)
    no_op: list[str] = field(default_factory=list)

    # Negation filters
    not_diff_kind: list[str] = field(default_factory=list)
    not_oracle_label_basis: list[str] = field(default_factory=list)
    not_replay_label_basis: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True if no filter criteria specified (would match everything)."""
        return (
            not self.replay_label_basis
            and not self.oracle_label_basis
            and not self.diff_kind
            and not self.diff_event
            and self.has_children is None
            and not self.replay_missing
            and not self.oracle_missing
            and not self.oracle_text_matches
            and not self.replay_text_matches
            and not self.oracle_text_not_matches
            and not self.replay_text_not_matches
            and not self.has_op
            and not self.no_op
            and not self.not_diff_kind
            and not self.not_oracle_label_basis
            and not self.not_replay_label_basis
        )

    def needs_ops(self) -> bool:
        """True if filter requires PEG op extraction."""
        return bool(self.has_op or self.no_op)


# ---------------------------------------------------------------------------
# Match result
# ---------------------------------------------------------------------------


@dataclass
class GrepMatch:
    statute_id: str
    section_key: str
    diff_kind: str
    oracle_label_basis: str
    replay_label_basis: str
    oracle_text: str = ""
    replay_text: str = ""
    events: list[str] = field(default_factory=list)

    def one_line(self) -> str:
        return (
            f"{self.statute_id:<20} {self.section_key:<20} "
            f"[{self.diff_kind}] "
            f"oracle={self.oracle_label_basis} "
            f"replay={self.replay_label_basis}"
        )

    def verbose_line(self) -> str:
        parts = [self.one_line()]
        if self.oracle_text:
            parts.append(f"    oracle: {self.oracle_text}")
        if self.replay_text:
            parts.append(f"    replay: {self.replay_text}")
        if self.events:
            parts.append(f"    events: {', '.join(self.events)}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "section_key": self.section_key,
            "diff_kind": self.diff_kind,
            "oracle_label_basis": self.oracle_label_basis,
            "replay_label_basis": self.replay_label_basis,
            "oracle_text": self.oracle_text,
            "replay_text": self.replay_text,
            "events": self.events,
        }


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def _default_corpus_path() -> Path:
    """Locate the default bench corpus CSV."""
    core = _LAWVM_DIR / "data" / "finland" / "bench_core.csv"
    if core.exists():
        return core
    primary = _LAWVM_DIR / "data" / "finland" / "bench_corpus.csv"
    if primary.exists():
        return primary
    return _LAWVM_DIR / ".tmp" / "batch_test_list.csv"


def _load_statute_ids(corpus_path: str | None) -> list[str]:
    """Load statute IDs from a corpus CSV or text file."""
    if corpus_path:
        p = Path(corpus_path)
    else:
        p = _default_corpus_path()
    if not p.exists():
        print(f"Corpus file not found: {p}", file=sys.stderr)
        sys.exit(1)
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        print(f"Corpus file empty: {p}", file=sys.stderr)
        sys.exit(1)
    # Detect CSV vs plain text
    lines = text.splitlines()
    sids: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # CSV: bench_core.csv format is "count,statute_id"
        parts = line.split(",")
        if len(parts) >= 2 and parts[0].strip().isdigit():
            sid = parts[1].strip().strip('"')
        else:
            sid = parts[0].strip().strip('"')
        if sid and sid != "statute_id" and "/" in sid:
            sids.append(sid)
    return sids


# ---------------------------------------------------------------------------
# Section text extraction
# ---------------------------------------------------------------------------


def _extract_node_text(section_data: dict[str, Any], side: str) -> str:
    """Extract full text from a semantic structure node dict (replay or oracle)."""
    node = section_data.get(side)
    if node is None:
        return ""
    parts: list[str] = []
    _collect_text(node, parts)
    return " ".join(parts).strip()


def _collect_text(node: dict[str, Any], parts: list[str]) -> None:
    """Recursively collect text from a semantic structure node dict."""
    if not isinstance(node, dict):
        return
    text = node.get("text", "")
    if text:
        parts.append(text)
    facets = node.get("facets")
    if isinstance(facets, dict):
        for facet_data in facets.values():
            if isinstance(facet_data, dict):
                ft = facet_data.get("text", "")
                if ft:
                    parts.append(ft)
    for child in node.get("children", []):
        _collect_text(child, parts)


def _node_label_basis(section_data: dict[str, Any], side: str) -> str:
    """Extract label_basis from a semantic structure node dict."""
    node = section_data.get(side)
    if node is None:
        return ""
    return node.get("label_basis", "explicit")


# ---------------------------------------------------------------------------
# Filter matching
# ---------------------------------------------------------------------------


def _matches_filter(
    section_key: str,
    section_data: dict[str, Any],
    filt: StructuralGrepFilter,
    section_ops: set[str] | None = None,
) -> bool:
    """Check if a section matches all filter criteria."""
    sd = section_data.get("semantic_diff")
    if sd is None:
        return False

    diff_kind = sd.get("kind", "")
    replay_node = section_data.get("replay")
    oracle_node = section_data.get("oracle")

    # --- Structural presence filters ---
    if filt.replay_missing and replay_node is not None:
        return False
    if filt.oracle_missing and oracle_node is not None:
        return False
    if filt.replay_missing and replay_node is None and oracle_node is None:
        return False
    if filt.oracle_missing and oracle_node is None and replay_node is None:
        return False

    # --- diff_kind ---
    if filt.diff_kind and diff_kind not in filt.diff_kind:
        return False
    if filt.not_diff_kind and diff_kind in filt.not_diff_kind:
        return False

    # --- label_basis ---
    r_lb = _node_label_basis(section_data, "replay")
    o_lb = _node_label_basis(section_data, "oracle")

    if filt.replay_label_basis and r_lb not in filt.replay_label_basis:
        return False
    if filt.oracle_label_basis and o_lb not in filt.oracle_label_basis:
        return False
    if filt.not_replay_label_basis and r_lb in filt.not_replay_label_basis:
        return False
    if filt.not_oracle_label_basis and o_lb in filt.not_oracle_label_basis:
        return False

    # --- diff events ---
    if filt.diff_event:
        event_kinds = {e.get("kind", "") for e in sd.get("events", [])}
        if not any(ek in event_kinds for ek in filt.diff_event):
            return False

    # --- children ---
    if filt.has_children is not None:
        # Check both sides for children
        has = False
        for side in ("replay", "oracle"):
            node = section_data.get(side)
            if isinstance(node, dict) and node.get("children"):
                has = True
                break
        if filt.has_children and not has:
            return False
        if not filt.has_children and has:
            return False

    # --- Text regex filters ---
    if filt.oracle_text_matches:
        text = _extract_node_text(section_data, "oracle")
        if not re.search(filt.oracle_text_matches, text, re.IGNORECASE):
            return False
    if filt.replay_text_matches:
        text = _extract_node_text(section_data, "replay")
        if not re.search(filt.replay_text_matches, text, re.IGNORECASE):
            return False
    if filt.oracle_text_not_matches:
        text = _extract_node_text(section_data, "oracle")
        if re.search(filt.oracle_text_not_matches, text, re.IGNORECASE):
            return False
    if filt.replay_text_not_matches:
        text = _extract_node_text(section_data, "replay")
        if re.search(filt.replay_text_not_matches, text, re.IGNORECASE):
            return False

    # --- Op-level filters ---
    if section_ops is not None:
        if filt.has_op:
            for op_type in filt.has_op:
                if op_type.upper() not in section_ops:
                    return False
        if filt.no_op:
            for op_type in filt.no_op:
                if op_type.upper() in section_ops:
                    return False

    return True


def _build_match(
    statute_id: str,
    section_key: str,
    section_data: dict[str, Any],
) -> GrepMatch:
    """Build a GrepMatch from section data."""
    sd = section_data.get("semantic_diff", {})
    events = sd.get("events", [])
    return GrepMatch(
        statute_id=statute_id,
        section_key=section_key,
        diff_kind=sd.get("kind", ""),
        oracle_label_basis=_node_label_basis(section_data, "oracle"),
        replay_label_basis=_node_label_basis(section_data, "replay"),
        oracle_text=_extract_node_text(section_data, "oracle"),
        replay_text=_extract_node_text(section_data, "replay"),
        events=[e.get("kind", "") for e in events],
    )


# ---------------------------------------------------------------------------
# Per-statute processing
# ---------------------------------------------------------------------------


def _extract_section_ops(statute_id: str) -> dict[str, set[str]]:
    """Extract compiled ops per section for a statute via replay.

    Returns a dict mapping section_key -> set of op types (REPEAL, REPLACE, etc.).
    Only called when --has-op or --no-op is specified.
    """
    try:
        from lawvm.finland.grafter import replay_xml

        compiled_ops: list[dict] = []
        replay_xml(
            statute_id,
            mode="finlex_oracle",
            compiled_ops_out=compiled_ops,
            quiet=True,
            build_full_products=False,
        )
        ops_by_section: dict[str, set[str]] = {}
        for op in compiled_ops:
            action = str(op.get("action", "")).upper()
            target = op.get("target", {})
            # Build a section key from the target address
            container = target.get("container", "")
            section = target.get("section", "")
            if container == "section" and section:
                sec_key = f"{section} §"
            elif container == "chapter" and section:
                sec_key = f"chapter:{section}"
            elif section:
                sec_key = f"{container}:{section}"
            else:
                continue
            if action:
                ops_by_section.setdefault(sec_key, set()).add(action)
        return ops_by_section
    except Exception:
        return {}


# Module-level state for worker processes
_WORKER_FILTER: StructuralGrepFilter | None = None


def _init_worker(filt_json: str) -> None:
    """Initialize a worker process with filter state."""
    global _WORKER_FILTER
    _WORKER_FILTER = _deserialize_filter(filt_json)


def _serialize_filter(filt: StructuralGrepFilter) -> str:
    """Serialize filter to JSON for multiprocessing transfer."""
    return json.dumps({
        "replay_label_basis": filt.replay_label_basis,
        "oracle_label_basis": filt.oracle_label_basis,
        "diff_kind": filt.diff_kind,
        "diff_event": filt.diff_event,
        "has_children": filt.has_children,
        "replay_missing": filt.replay_missing,
        "oracle_missing": filt.oracle_missing,
        "oracle_text_matches": filt.oracle_text_matches,
        "replay_text_matches": filt.replay_text_matches,
        "oracle_text_not_matches": filt.oracle_text_not_matches,
        "replay_text_not_matches": filt.replay_text_not_matches,
        "has_op": filt.has_op,
        "no_op": filt.no_op,
        "not_diff_kind": filt.not_diff_kind,
        "not_oracle_label_basis": filt.not_oracle_label_basis,
        "not_replay_label_basis": filt.not_replay_label_basis,
    })


def _deserialize_filter(data: str) -> StructuralGrepFilter:
    """Deserialize filter from JSON."""
    d = json.loads(data)
    return StructuralGrepFilter(**d)


def _grep_one_statute(statute_id: str) -> list[dict[str, Any]]:
    """Worker function: compute diffs and apply filter for one statute.

    Returns a list of match dicts (serializable).
    """
    filt = _WORKER_FILTER
    if filt is None:
        return []
    try:
        from lawvm.tools.structural_review import compute_statute_section_diffs

        sections, oracle_content_absent = compute_statute_section_diffs(statute_id)
        if oracle_content_absent:
            return []

        # Extract ops only when needed
        section_ops: dict[str, set[str]] | None = None
        if filt.needs_ops():
            section_ops = _extract_section_ops(statute_id)

        matches: list[dict[str, Any]] = []
        for sec_key, sec_data in sorted(sections.items()):
            ops_for_section = section_ops.get(sec_key) if section_ops is not None else None
            if _matches_filter(sec_key, sec_data, filt, ops_for_section):
                match = _build_match(statute_id, sec_key, sec_data)
                matches.append(match.to_dict())
        return matches
    except Exception as e:
        return [{"error": str(e), "statute_id": statute_id}]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def structural_grep(
    filt: StructuralGrepFilter,
    *,
    corpus_path: str | None = None,
    parallel: int = 0,
    output_mode: str = "default",
) -> list[GrepMatch]:
    """Run structural grep across the corpus.

    Args:
        filt: Filter specification.
        corpus_path: Path to corpus file (CSV or text). None uses default.
        parallel: Number of workers (0 = cpu_count).
        output_mode: One of "default", "verbose", "count", "json".

    Returns:
        List of GrepMatch results.
    """
    sids = _load_statute_ids(corpus_path)
    if not sids:
        print("No statutes to scan.", file=sys.stderr)
        return []

    if parallel == 1:
        # Sequential mode for debugging
        global _WORKER_FILTER
        _WORKER_FILTER = filt
        all_matches: list[GrepMatch] = []
        errors = 0
        for i, sid in enumerate(sids):
            raw = _grep_one_statute(sid)
            for item in raw:
                if "error" in item:
                    errors += 1
                    if output_mode == "verbose":
                        print(f"ERROR {item['statute_id']}: {item['error']}", file=sys.stderr)
                else:
                    match = GrepMatch(**item)
                    all_matches.append(match)
            if (i + 1) % 50 == 0 or (i + 1) == len(sids):
                print(
                    f"  [{i + 1}/{len(sids)}] matches so far: {len(all_matches)}",
                    end="\r",
                    file=sys.stderr,
                    flush=True,
                )
        print(file=sys.stderr)
        _WORKER_FILTER = None
        return all_matches

    # Parallel mode
    if parallel <= 0:
        parallel = min(mp.cpu_count(), 8)

    print(
        f"Scanning {len(sids)} statutes with {parallel} workers...",
        file=sys.stderr,
    )

    filt_json = _serialize_filter(filt)
    all_matches = []
    errors = 0

    with mp.Pool(parallel, initializer=_init_worker, initargs=(filt_json,)) as pool:
        for i, raw in enumerate(pool.imap_unordered(_grep_one_statute, sids)):
            for item in raw:
                if "error" in item:
                    errors += 1
                    if output_mode == "verbose":
                        print(f"ERROR {item['statute_id']}: {item['error']}", file=sys.stderr)
                else:
                    match = GrepMatch(**item)
                    all_matches.append(match)
                    if output_mode == "default":
                        print(match.one_line())
                    elif output_mode == "verbose":
                        print(match.verbose_line())
            if (i + 1) % 50 == 0 or (i + 1) == len(sids):
                print(
                    f"  [{i + 1}/{len(sids)}] matches so far: {len(all_matches)}",
                    end="\r",
                    file=sys.stderr,
                    flush=True,
                )
    print(file=sys.stderr)

    if errors:
        print(f"{errors} statutes had errors.", file=sys.stderr)

    return all_matches


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    """CLI entry point — called from cli.py."""
    filt = StructuralGrepFilter(
        replay_label_basis=args.replay_label_basis or [],
        oracle_label_basis=args.oracle_label_basis or [],
        diff_kind=args.diff_kind or [],
        diff_event=args.diff_event or [],
        has_children=(
            True if getattr(args, "has_children", False)
            else (False if getattr(args, "no_children", False) else None)
        ),
        replay_missing=getattr(args, "replay_missing", False),
        oracle_missing=getattr(args, "oracle_missing", False),
        oracle_text_matches=getattr(args, "oracle_text_matches", "") or "",
        replay_text_matches=getattr(args, "replay_text_matches", "") or "",
        oracle_text_not_matches=getattr(args, "oracle_text_not_matches", "") or "",
        replay_text_not_matches=getattr(args, "replay_text_not_matches", "") or "",
        has_op=args.has_op or [],
        no_op=args.no_op or [],
        not_diff_kind=args.not_diff_kind or [],
        not_oracle_label_basis=args.not_oracle_label_basis or [],
        not_replay_label_basis=args.not_replay_label_basis or [],
    )

    if filt.is_empty():
        print("No filter criteria specified. Use --help for available filters.", file=sys.stderr)
        sys.exit(1)

    output_mode = "default"
    if getattr(args, "json_output", False):
        output_mode = "json"
    elif getattr(args, "count", False):
        output_mode = "count"
    elif getattr(args, "verbose", False):
        output_mode = "verbose"

    corpus_path = getattr(args, "corpus", None)
    parallel = getattr(args, "parallel", 0)

    matches = structural_grep(
        filt,
        corpus_path=corpus_path,
        parallel=parallel,
        output_mode=output_mode,
    )

    # Final output for non-streaming modes
    if output_mode == "json":
        json.dump([m.to_dict() for m in matches], sys.stdout, ensure_ascii=False, indent=2)
        print()
    elif output_mode == "count":
        # Count per statute
        counts: dict[str, int] = {}
        for m in matches:
            counts[m.statute_id] = counts.get(m.statute_id, 0) + 1
        for sid, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"{sid:<20} {n}")
        print(f"\nTotal: {sum(counts.values())} matches in {len(counts)} statutes")
    elif output_mode in ("default", "verbose") and parallel == 1:
        # Sequential mode: print at end (parallel mode streams inline)
        for m in matches:
            if output_mode == "verbose":
                print(m.verbose_line())
            else:
                print(m.one_line())

    total = len(matches)
    statutes = len({m.statute_id for m in matches})
    print(f"\n{total} matches in {statutes} statutes.", file=sys.stderr)
