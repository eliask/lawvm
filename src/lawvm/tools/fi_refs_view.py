"""lawvm fi-refs — annotated-source-canvas viewer for the references overlay.

A read-only VISUALIZATION surface over the existing Legal Surface Graph. No new
parsing happens here: the viewer builds the graph (``build_legal_surface_graph``),
reads its ``reference_expr`` nodes (joined to their ``reference_resolution``),
and renders the references as annotations over the statute's ``raw_text`` canvas.

Discipline mirrors :mod:`lawvm.tools.fi_parse_view`: pure ``build_*`` (→dict) and
``render_*`` (→str) functions that are testable without the CLI, plus a thin
``main`` wrapper. ``_load_statute_body`` / ``_resolve_span`` are reused from
``fi_parse_view`` (imported, not forked).

SUBSTRATE (settled empirically, Phase 0)
────────────────────────────────────────
The flat overlay rows from ``graph_to_overlay_rows`` carry NULL ``rendered_*``
char columns in v0 (no rendered-span context), so they cannot place a mark on the
canvas by themselves. The ``reference_expr`` graph *nodes*, however, carry a real
character anchor into the whole-body ``raw_text`` via ``node.source_ref.{char_start,
char_end}`` (verified: ``raw_text[char_start:char_end] == surface_text``). The
viewer therefore consumes the graph nodes directly, joining each ``reference_expr``
to its ``reference_resolution`` via the same inversion ``overlay_projection``
uses. Nodes whose ``source_ref`` is absent / degenerate (metadata edges:
REPEALS / ISSUES / ISSUED_UNDER, empty surface) have no canvas position and go to
the positionless footer — never dropped (fail-loud completeness).

OVERLAY SEAM
────────────
A :class:`Mark` is the renderer's unit of annotation. ``build_marks`` is the
overlay contract: it turns per-reference dicts into marks for the ``refs`` overlay.
The seam is wired (the Mark abstraction + an overlay key on every mark) so adding
a second overlay later (defs / temporal / …) is a new producer registration, with
zero renderer change. v0 registers ONLY the ``refs`` overlay.

DEFERRED (not built in v0 — clean seams left)
─────────────────────────────────────────────
* ``skeleton`` level + margin / gutter doodads (``--margin``).
* multi-overlay composition beyond the Mark seam (``--show``/``--hide`` other
  families); v0 fixes the overlay set to ``refs``.
* EU / cite.py metadata edges folded into the footer (Phase 3).
* bitemporal broken-since recomputation (``--all-versions``); ``--as-of`` is a
  simple interval-containment filter on the current consolidation only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Optional

from lawvm.tools.fi_parse_view import _emit, _load_statute_body, _resolve_span

# ── Status sigils (CiteConfidence / resolution_status → one char) ─────────────
#
# Maps the resolution-status vocabulary (CiteConfidence.value and the joined
# reference_resolution's resolution_status) to a one-char salience sigil.
_SIGIL_BY_STATUS: dict[str, str] = {
    "exact": "=",
    "resolved": "=",
    "approximate": "=",
    "statute_only": "~",
    "ambiguous": "?",
    "open": "○",
    "broken": "✗",
    "unresolved": "⊘",
}
_SIGIL_UNKNOWN = "⊘"

#: Residue statuses, sorted-first in digest and the target of ``--only`` audit use.
_RESIDUE_STATUSES: frozenset[str] = frozenset(
    {"ambiguous", "open", "broken", "unresolved"}
)

#: Family glyph for the refs overlay (the per-overlay lane tag; only one in v0).
_REFS_GLYPH = "→"


def _sigil(ref_status: Optional[str]) -> str:
    if ref_status is None:
        return _SIGIL_UNKNOWN
    return _SIGIL_BY_STATUS.get(ref_status, _SIGIL_UNKNOWN)


# ── Mark abstraction (the overlay contract) ──────────────────────────────────


@dataclass(frozen=True)
class Mark:
    """One annotation placed on the ``raw_text`` canvas by some overlay.

    The renderer-neutral unit. ``overlay`` tags which producer emitted it (only
    ``refs`` in v0) so future overlays render in their own lane without colliding.
    ``span`` is ``(char_start, char_end)`` into the whole-body ``raw_text``;
    ``payload`` carries the overlay-specific detail (target, cite_kind, valid …).
    """

    overlay: str
    char_start: int
    char_end: int
    glyph: str
    label: str
    ref_status: Optional[str]
    payload: dict[str, Any] = field(default_factory=dict)


# ── Target serialization helpers ─────────────────────────────────────────────


def _serialize_target(ref: dict[str, Any], source_statute_id: str) -> str:
    """Compact target string. Self-references (INTERNAL, same statute) drop the
    statute id and lead with ``§`` (e.g. ``§108``); externals keep the full
    ``statute_id/...`` form (e.g. ``2015/1635/4``)."""
    target = ref.get("target_provision_ref")
    cite_kind = ref.get("cite_kind")
    target_id = ref.get("target_id")
    if not target:
        # statute identity only, or open/unresolved → describe by status.
        status = ref.get("ref_status")
        if status == "open":
            return "(open: vague catch-all)"
        if status == "unresolved":
            return "(unresolved)"
        if target_id:
            return f"{target_id} (act only)"
        return "(no target)"
    self_ref = cite_kind == "internal" or (
        target_id is not None and target_id == source_statute_id
    )
    if self_ref:
        # Drop the leading statute id, lead with §.
        rest = target
        if target_id and rest.startswith(f"{target_id}/"):
            rest = rest[len(target_id) + 1 :]
        return f"§{rest}"
    return target


def _is_self_ref(ref: dict[str, Any], source_statute_id: str) -> bool:
    cite_kind = ref.get("cite_kind")
    target_id = ref.get("target_id")
    return cite_kind == "internal" or (
        target_id is not None and target_id == source_statute_id
    )


# ── Corpus load: graph → per-reference dicts (the only corpus-touching step) ──


def _join_resolution(graph: Any) -> dict[str, Any]:
    """Map each ``reference_expr`` node_id → its ``reference_resolution`` node.

    Same inversion as ``overlay_projection._resolution_by_expr`` (one
    ``resolution_of`` edge per mention, resolution→expr), kept local so the
    viewer reads the join without importing private helpers.
    """
    resolutions = {
        nid: node
        for nid, node in graph.nodes.items()
        if node.node_kind == "reference_resolution"
    }
    by_expr: dict[str, Any] = {}
    for edge in graph.edges:
        if edge.edge_kind != "resolution_of":
            continue
        resolution = resolutions.get(edge.src)
        if resolution is not None:
            by_expr[edge.dst] = resolution
    return by_expr


def _ref_dict(node: Any, resolution: Any, body: str) -> dict[str, Any]:
    """Project one ``reference_expr`` node (+ its resolution) into a flat dict.

    The dict is the viewer's renderer-neutral reference record; ``build_marks``
    consumes these so it is testable on synthetic dicts without the corpus.
    The resolution status (from the joined ``reference_resolution``) overrides the
    expr node's own ``cite_confidence`` when present, mirroring
    ``overlay_projection._overlay_status``.
    """
    payload = node.payload
    status = payload.get("cite_confidence")
    if resolution is not None:
        res_status = resolution.payload.get("resolution_status")
        if isinstance(res_status, str) and res_status:
            status = res_status
    sr = node.source_ref
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    if (
        sr is not None
        and sr.char_start is not None
        and sr.char_end is not None
        and sr.char_end > sr.char_start
    ):
        char_start = int(sr.char_start)
        char_end = int(sr.char_end)
    return {
        "node_id": node.node_id,
        "surface_text": payload.get("surface_text") or "",
        "cite_kind": payload.get("cite_kind"),
        "ref_status": status,
        "phrase_lemma": payload.get("phrase_lemma"),
        "edge_subtype": payload.get("edge_subtype"),
        "target_id": payload.get("target_id"),
        "target_provision_ref": payload.get("target_provision_ref"),
        "source_provision_ref": payload.get("source_provision_ref"),
        "valid_at_start": payload.get("valid_at_start"),
        "valid_at_end": payload.get("valid_at_end"),
        "char_start": char_start,
        "char_end": char_end,
        "candidates": (
            list(resolution.payload.get("candidates", []))
            if resolution is not None
            else []
        ),
    }


def _load_references(statute_id: str) -> tuple[str, list[dict[str, Any]], str]:
    """Build the graph and return ``(body, ref_dicts, source_statute_id)``.

    The single corpus-touching step. ``ref_dicts`` carries one record per
    ``reference_expr`` node (char-anchored or positionless); the pure builders
    take it from here. Fail-loud: a missing statute raises ``SystemExit`` with
    the id (via ``_load_statute_body``).
    """
    from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
    from lawvm.tools.export_fi_interlinks import _get_statute_xml
    from lawvm.tools.parse_bench import _archive_path
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from farchive import Farchive

    bundle, unit = _load_statute_body(statute_id)
    body = unit.raw_text

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:  # pragma: no cover — _load_statute_body already raised
        raise SystemExit(f"ERROR: no archived source XML for statute {statute_id!r}")

    graph = build_legal_surface_graph(xml_bytes, statute_id)
    source_statute_id = graph.subject.work_id or statute_id
    resolutions = _join_resolution(graph)

    refs: list[dict[str, Any]] = []
    for node in graph.nodes.values():
        if node.node_kind != "reference_expr":
            continue
        refs.append(_ref_dict(node, resolutions.get(node.node_id), body))
    refs.sort(key=lambda r: str(r["node_id"]))
    return body, refs, source_statute_id


# ── Filters (--only, --as-of) ────────────────────────────────────────────────


def _passes_only(ref: dict[str, Any], only: Optional[frozenset[str]]) -> bool:
    if only is None:
        return True
    return (ref.get("ref_status") or "unresolved") in only


def _passes_as_of(ref: dict[str, Any], as_of: Optional[str]) -> bool:
    """Interval-containment filter (v0). A ref whose ``[valid_at_start,
    valid_at_end]`` excludes ``as_of`` is dropped. Open-ended bounds are
    inclusive; a ref with no interval always passes."""
    if as_of is None:
        return True
    start = ref.get("valid_at_start")
    end = ref.get("valid_at_end")
    if start is not None and as_of < start:
        return False
    if end is not None and as_of > end:
        return False
    return True


# ── Overlay producer: refs → marks (the pure overlay contract) ────────────────


def build_marks(
    refs: list[dict[str, Any]],
    source_statute_id: str,
    *,
    only: Optional[frozenset[str]] = None,
    as_of: Optional[str] = None,
) -> tuple[list[Mark], list[dict[str, Any]]]:
    """Turn per-reference dicts into ``(canvas_marks, positionless)``.

    The ``refs`` overlay producer. Pure: testable on synthetic dicts. A ref with
    a usable char anchor becomes a canvas :class:`Mark`; one without (metadata
    edge / empty surface) goes to ``positionless`` (never dropped). ``only`` /
    ``as_of`` filter BOTH streams so the footer stays consistent with the canvas.
    """
    marks: list[Mark] = []
    positionless: list[dict[str, Any]] = []
    for ref in refs:
        if not _passes_only(ref, only):
            continue
        if not _passes_as_of(ref, as_of):
            continue
        status = ref.get("ref_status")
        target = _serialize_target(ref, source_statute_id)
        if ref.get("char_start") is None or ref.get("char_end") is None:
            positionless.append(
                {
                    "family": _positionless_family(ref),
                    "role": ref.get("phrase_lemma") or ref.get("edge_subtype"),
                    "surface": ref.get("surface_text") or "",
                    "target": target,
                    "ref_status": status,
                    "sigil": _sigil(status),
                    "cite_kind": ref.get("cite_kind"),
                }
            )
            continue
        marks.append(
            Mark(
                overlay="refs",
                char_start=int(ref["char_start"]),
                char_end=int(ref["char_end"]),
                glyph=_REFS_GLYPH,
                label=ref.get("surface_text") or "",
                ref_status=status,
                payload={
                    "node_id": ref["node_id"],
                    "target": target,
                    "self_ref": _is_self_ref(ref, source_statute_id),
                    "cite_kind": ref.get("cite_kind"),
                    "family": ref.get("edge_subtype") or "xml_ref",
                    "valid": [ref.get("valid_at_start"), ref.get("valid_at_end")],
                    "candidates": ref.get("candidates", []),
                    "source_provision_ref": ref.get("source_provision_ref"),
                },
            )
        )
    marks.sort(key=lambda m: (m.char_start, m.char_end))
    positionless.sort(key=lambda p: (str(p["family"]), str(p["surface"]), str(p["target"])))
    return marks, positionless


def _positionless_family(ref: dict[str, Any]) -> str:
    """Classify a positionless reference for the footer (surface fact)."""
    lemma = ref.get("phrase_lemma") or ""
    cite_kind = ref.get("cite_kind")
    if lemma in ("REPEALS", "ISSUED_UNDER", "ISSUES"):
        return "metadata"
    if cite_kind == "eu":
        return "eu"
    return "metadata"


# ── Counts (always available; the counts level IS just this) ──────────────────


def build_counts(
    marks: list[Mark], positionless: list[dict[str, Any]]
) -> dict[str, Any]:
    """O(1)-output census: refs by family (canvas vs positionless) × status."""
    by_status: dict[str, int] = {}
    for m in marks:
        key = m.ref_status or "unresolved"
        by_status[key] = by_status.get(key, 0) + 1
    pos_by_status: dict[str, int] = {}
    for p in positionless:
        key = p.get("ref_status") or "unresolved"
        pos_by_status[key] = pos_by_status.get(key, 0) + 1
    return {
        "by_family": {
            "refs": len(marks),
            "positionless": len(positionless),
        },
        "by_status": by_status,
        "positionless_by_status": pos_by_status,
        "total": len(marks) + len(positionless),
    }


# ── Clause-window machinery (context level) ───────────────────────────────────


def _clause_index(body: str, source_unit_id: str) -> Any:
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    return build_clause_index(source_unit_id, body)


def _host_clause(clauses: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    """The clause span containing char ``pos`` (the last clause starting at or
    before ``pos``), or a degenerate (pos, pos) if none."""
    best = (pos, pos)
    for lo, hi in clauses:
        if lo <= pos < hi:
            return (lo, hi)
        if lo <= pos:
            best = (lo, hi)
    return best


def _window_for_mark(
    clauses: list[tuple[int, int]], mark: Mark, radius: int
) -> tuple[int, int]:
    """The clause containing the mark ± ``radius`` clauses, snapped to clause
    boundaries. ``radius`` counts clauses; ``-C0`` = just the host clause."""
    if not clauses:
        return (mark.char_start, mark.char_end)
    # Find the index of the host clause.
    host_idx = 0
    for i, (lo, hi) in enumerate(clauses):
        if lo <= mark.char_start < hi:
            host_idx = i
            break
        if lo <= mark.char_start:
            host_idx = i
    lo_idx = max(0, host_idx - radius)
    hi_idx = min(len(clauses) - 1, host_idx + radius)
    return (clauses[lo_idx][0], clauses[hi_idx][1])


def _merge_windows(
    windows: list[tuple[int, int, Mark]], merge_gap: int
) -> list[dict[str, Any]]:
    """Sort by lo and merge overlapping/adjacent windows (gap ≤ merge_gap chars).

    Each merged window lists ALL its marks in char order. Returns a list of
    ``{lo, hi, marks: [Mark]}``.
    """
    if not windows:
        return []
    ordered = sorted(windows, key=lambda w: (w[0], w[1]))
    merged: list[dict[str, Any]] = []
    cur_lo, cur_hi, first_mark = ordered[0]
    cur_marks = [first_mark]
    for lo, hi, mark in ordered[1:]:
        if lo <= cur_hi + merge_gap:
            cur_hi = max(cur_hi, hi)
            cur_marks.append(mark)
        else:
            merged.append({"lo": cur_lo, "hi": cur_hi, "marks": cur_marks})
            cur_lo, cur_hi, cur_marks = lo, hi, [mark]
    merged.append({"lo": cur_lo, "hi": cur_hi, "marks": cur_marks})
    for w in merged:
        w["marks"].sort(key=lambda m: (m.char_start, m.char_end))
    return merged


# ── The renderer-neutral view dict (§7) ──────────────────────────────────────


def _mark_dict(mark: Mark) -> dict[str, Any]:
    return {
        "id": mark.payload.get("node_id"),
        "overlay": mark.overlay,
        "span": [mark.char_start, mark.char_end],
        "glyph": mark.glyph,
        "surface": mark.label,
        "ref_status": mark.ref_status,
        "sigil": _sigil(mark.ref_status),
        "target": mark.payload.get("target"),
        "self_ref": mark.payload.get("self_ref"),
        "family": mark.payload.get("family"),
        "cite_kind": mark.payload.get("cite_kind"),
        "valid": mark.payload.get("valid"),
        "candidates": mark.payload.get("candidates", []),
    }


def build_refs_view(
    statute_id: str,
    body: str,
    marks: list[Mark],
    positionless: list[dict[str, Any]],
    counts: dict[str, Any],
    *,
    level: str,
    as_of: Optional[str],
    radius: int = 1,
    merge_gap: int = 0,
    split: bool = False,
    windows: Optional[list[dict[str, Any]]] = None,
    lo: int = 0,
    hi: Optional[int] = None,
) -> dict[str, Any]:
    """Project the marks + positionless + counts into the stable view dict.

    Renderer-neutral: every ``render_*_view`` and the ``--json`` mode consume this
    exact dict. ``windows`` (context level) are pre-computed clause windows; for
    other levels they are omitted.
    """
    view: dict[str, Any] = {
        "statute_id": statute_id,
        "level": level,
        "as_of": as_of,
        "window": {"lo": lo, "hi": hi if hi is not None else len(body)},
        "counts": counts,
        "overlays": [
            {"overlay": "refs", "marks": [_mark_dict(m) for m in marks]},
        ],
        "positionless": positionless,
        "diagnostics": [],
    }
    if windows is not None:
        view["windows"] = [
            {
                "lo": w["lo"],
                "hi": w["hi"],
                "text": body[w["lo"] : w["hi"]],
                "marks": [_mark_dict(m) for m in w["marks"]],
            }
            for w in windows
        ]
        view["radius"] = radius
        view["merge_gap"] = merge_gap
        view["split"] = split
    return view


# ── Renderers (one per level; pure) ──────────────────────────────────────────


def _counts_line(counts: dict[str, Any]) -> str:
    """One-line census, aggregated BY SIGIL so no status is silently invisible.

    Statuses sharing a sigil (e.g. ``exact``/``resolved``/``approximate`` → ``=``)
    are folded into that sigil's column; any status with an unknown sigil lands in
    ``⊘`` and is therefore still counted, never dropped.
    """
    n = counts["by_family"]["refs"]
    by_sigil: dict[str, int] = {}
    for status, count in counts["by_status"].items():
        by_sigil[_sigil(status)] = by_sigil.get(_sigil(status), 0) + count
    parts = [f"{sig} {by_sigil.get(sig, 0)}" for sig in ("=", "~", "?", "○", "✗", "⊘")]
    pos = counts["by_family"]["positionless"]
    suffix = f"   (+{pos} positionless → footer)" if pos else ""
    return f"refs {n}   " + "  ".join(parts) + suffix


def _header(view: dict[str, Any]) -> list[str]:
    sid = view["statute_id"]
    as_of = view["as_of"] or "current"
    return [
        f"fi-refs {sid}  ·  {view['level']}  ·  as-of {as_of}",
        _counts_line(view["counts"]),
    ]


def render_counts_view(view: dict[str, Any]) -> str:
    lines = _header(view)
    return "\n".join(lines)


def _digest_sort_key(m: dict[str, Any]) -> tuple[int, str, int]:
    """Residue statuses first, then by source provision, then char position."""
    status = m.get("ref_status") or "unresolved"
    residue_first = 0 if status in _RESIDUE_STATUSES else 1
    return (residue_first, str(status), int(m["span"][0]))


def render_digest_view(view: dict[str, Any]) -> str:
    lines = _header(view)
    lines.append("─" * 60)
    marks = view["overlays"][0]["marks"]
    if not marks and not view["positionless"]:
        lines.append(f"{view['statute_id']}: 0 references")
        return "\n".join(lines)
    for m in sorted(marks, key=_digest_sort_key):
        sig = m["sigil"]
        lines.append(
            f"  {m['glyph']}{sig} «{m['surface']}» ▸ {m['target']}"
            f"   [{m['span'][0]}:{m['span'][1]}]"
        )
    _render_positionless(view, lines)
    return "\n".join(lines)


def render_context_view(view: dict[str, Any]) -> str:
    lines = _header(view)
    lines.append("─" * 72)
    windows = view.get("windows", [])
    if not windows and not view["positionless"]:
        lines.append(f"{view['statute_id']}: 0 references")
        return "\n".join(lines)
    for i, w in enumerate(windows):
        if i > 0:
            lines.append("  ⋯")
        text = w["text"].strip()
        # Print the window text (clause prose), wrapped at line boundaries.
        for ln in text.splitlines() or [text]:
            lines.append(f"  {ln.rstrip()}")
        for m in w["marks"]:
            sig = m["sigil"]
            tags = []
            if m.get("self_ref"):
                tags.append("self")
            elif m.get("cite_kind"):
                tags.append(str(m["cite_kind"]))
            if m.get("family"):
                tags.append(str(m["family"]))
            tag = (" (" + " · ".join(tags) + ")") if tags else ""
            lines.append(
                f"   {m['glyph']}{sig} «{m['surface']}» ▸ {m['target']}{tag}"
            )
    _render_positionless(view, lines)
    return "\n".join(lines)


def render_full_view(view: dict[str, Any], body: str) -> str:
    """Whole body with inline footnote markers + a resolution table footer."""
    lines = _header(view)
    lines.append("─" * 72)
    marks = sorted(view["overlays"][0]["marks"], key=lambda m: m["span"][0])
    # Insert inline markers [n] after each surface span, right-to-left so earlier
    # offsets are not shifted.
    out = body
    numbered = list(enumerate(marks, start=1))
    for n, m in sorted(numbered, key=lambda nm: nm[1]["span"][1], reverse=True):
        pos = int(m["span"][1])
        out = out[:pos] + f"[{n}]" + out[pos:]
    lines.append(out.rstrip())
    lines.append("")
    lines.append("RESOLUTION TABLE")
    for n, m in numbered:
        sig = m["sigil"]
        kind = m.get("cite_kind") or ""
        lines.append(
            f"  [{n}] {m['glyph']}{sig} «{m['surface']}» → {m['target']}"
            f"   ({m.get('family', '')} · {kind})"
        )
    _render_positionless(view, lines)
    return "\n".join(lines)


def _render_positionless(view: dict[str, Any], lines: list[str]) -> None:
    pos = view["positionless"]
    if not pos:
        return
    lines.append("")
    lines.append(f"REFERENCES WITHOUT A BODY POSITION ({len(pos)})")
    for p in pos:
        sig = p.get("sigil", _SIGIL_UNKNOWN)
        role = p.get("role") or ""
        fam = p.get("family", "")
        target = p.get("target", "")
        surface = (f" «{p['surface']}»" if p.get("surface") else "")
        lines.append(f"  {fam:8} {_REFS_GLYPH}{sig} {role} {target}{surface}".rstrip())


# ── CLI handler (thin wrapper) ────────────────────────────────────────────────


def _parse_only(spec: Optional[str]) -> Optional[frozenset[str]]:
    if not spec:
        return None
    statuses = {s.strip() for s in spec.split(",") if s.strip()}
    known = set(_SIGIL_BY_STATUS) | {"unresolved"}
    unknown = statuses - known
    if unknown:
        raise SystemExit(
            f"ERROR: --only unknown status(es): {sorted(unknown)}; "
            f"known: {sorted(known)}"
        )
    return frozenset(statuses)


def main(args: argparse.Namespace) -> None:
    as_json: bool = bool(getattr(args, "json", False))
    statute_id: str = args.statute
    if not statute_id:
        raise SystemExit("ERROR: fi-refs requires a statute id, e.g. fi-refs 2009/953")

    level: str = getattr(args, "level", "context")
    only = _parse_only(getattr(args, "only", None))
    as_of: Optional[str] = getattr(args, "as_of", None)
    radius: int = int(getattr(args, "context", 1))
    merge_gap: int = int(getattr(args, "merge_gap", 0))
    split: bool = bool(getattr(args, "split", False))

    body, refs, source_statute_id = _load_references(statute_id)

    # --provision / --grep window: filter refs to the resolved char window.
    bundle, unit = _load_statute_body(statute_id)
    lo, hi = _resolve_span(
        body,
        grep=getattr(args, "grep", None),
        provision=getattr(args, "provision", None),
        unit=unit,
    )
    if (lo, hi) != (0, len(body)):
        refs = [
            r
            for r in refs
            if r.get("char_start") is None
            or not (r["char_end"] <= lo or r["char_start"] >= hi)
        ]

    marks, positionless = build_marks(
        refs, source_statute_id, only=only, as_of=as_of
    )
    counts = build_counts(marks, positionless)

    windows: Optional[list[dict[str, Any]]] = None
    if level == "context":
        ci = _clause_index(body, unit.source_unit_id)
        clauses = [(c.char_start, c.char_end) for c in ci.clauses]
        if split:
            raw = [
                (mk.char_start, mk.char_end, mk)
                if not clauses
                else (*_window_for_mark(clauses, mk, radius), mk)
                for mk in marks
            ]
            windows = [
                {"lo": lo_, "hi": hi_, "marks": [mk]} for (lo_, hi_, mk) in raw
            ]
        else:
            raw = [(*_window_for_mark(clauses, mk, radius), mk) for mk in marks]
            windows = _merge_windows(raw, merge_gap)

    view = build_refs_view(
        statute_id,
        body,
        marks,
        positionless,
        counts,
        level=level,
        as_of=as_of,
        radius=radius,
        merge_gap=merge_gap,
        split=split,
        windows=windows,
        lo=lo,
        hi=hi,
    )

    if as_json:
        _emit(view, "", True)
        return

    if level == "counts":
        rendered = render_counts_view(view)
    elif level == "digest":
        rendered = render_digest_view(view)
    elif level == "full":
        rendered = render_full_view(view, body)
    else:
        rendered = render_context_view(view)
    _emit(view, rendered, False)
