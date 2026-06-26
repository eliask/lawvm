"""lawvm fi-parse — render Finnish parse structures from existing machinery.

A clean, read-only VISUALIZATION surface over the existing Finland parsers. No
new parsing happens here: every view composes an already-built API and renders
its output as a human-readable tree and (with ``--json``) a machine-readable
dict. The rendering logic lives in pure functions that take parsed objects and
return strings / dicts, so the CLI handler stays a thin wrapper and the
renderers are testable without the CLI.

Views:

* FOREST     — the SourceSyntaxGraph forest for a provision (structural skeleton,
               construction leaves + family ownership, non-``contains`` edge
               annotations, residual spans, list constructions, coverage census).
* JOHTOLAUSE — an amendment johtolause parsed to SurfaceClause verb groups (target
               refs, scope blocks, insertions) + the lowered ParsedOps.
* MORPH       — M1 generation (case×number paradigm, rule-tagged) + reverse
               analysis (surface -> lemma(s), fail-loud on unknown / ambiguity).
* CLAUSES     — sentence / clause segmentation over a provision or raw text.
"""

from __future__ import annotations

import argparse
import json
from typing import Any


# ---------------------------------------------------------------------------
# Corpus access (lazy — only the forest / clause-by-statute views need it)
# ---------------------------------------------------------------------------


def _load_statute_body(statute_id: str) -> tuple[Any, Any]:
    """Return ``(bundle, unit)`` for ``statute_id`` from the configured corpus.

    Fail-loud: a missing statute raises with the offending id, never a silent
    empty body.
    """
    from farchive import Farchive
    from lawvm.finland.legal_surface.bundle import build_surface_bundle
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.export_fi_interlinks import _get_statute_xml
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        raise SystemExit(f"ERROR: no archived source XML for statute {statute_id!r}")
    bundle = build_surface_bundle(xml_bytes, statute_id)
    return bundle, bundle.units[0]


def _build_forest(bundle: Any, unit: Any) -> Any:
    from lawvm.finland.legal_surface.source_syntax_graph import (
        assemble_source_syntax_graph_for_unit,
    )

    return assemble_source_syntax_graph_for_unit(
        subject=bundle.subject,
        unit=unit,
    )


def _resolve_span(
    body: str,
    *,
    grep: str | None,
    provision: str | None,
    unit: Any,
) -> tuple[int, int]:
    """Resolve a (lo, hi) char window into ``body`` from --grep / --provision.

    --grep anchors on the first match and widens to surrounding line boundaries
    (the probe convention). --provision resolves via the provision index. With
    neither, the whole body is used.
    """
    if grep is not None:
        idx = body.find(grep)
        if idx < 0:
            raise SystemExit(f"ERROR: --grep text not found in {unit.work_id}: {grep!r}")
        lo = body.rfind("\n", 0, max(0, idx - 220))
        lo = 0 if lo < 0 else lo
        hi = body.find("\n", idx + 120)
        hi = len(body) if hi < 0 else hi
        return lo, hi
    if provision is not None:
        prov_index = unit.metadata.get("provision_index") if unit.metadata else None
        if prov_index is None:
            raise SystemExit("ERROR: provision index unavailable for --provision")
        span = _provision_span(prov_index, provision)
        if span is None:
            raise SystemExit(
                f"ERROR: --provision {provision!r} not found in {unit.work_id}"
            )
        return span
    return 0, len(body)


def _provision_span(prov_index: Any, address: str) -> tuple[int, int] | None:
    """Find the char span of the AKN provision whose eId / address matches."""
    needle = address.strip()
    best: tuple[int, int] | None = None
    for entry in getattr(prov_index, "entries", ()):  # pragma: no branch
        eid = str(getattr(entry, "eid", "") or getattr(entry, "address", ""))
        if needle and (needle == eid or needle in eid):
            lo = int(entry.char_start)
            hi = int(entry.char_end)
            if best is None or (hi - lo) < (best[1] - best[0]):
                best = (lo, hi)
    return best


# ---------------------------------------------------------------------------
# View 1: FOREST
# ---------------------------------------------------------------------------


def build_forest_view(
    forest: Any,
    body: str,
    lo: int,
    hi: int,
) -> dict[str, Any]:
    """Project the forest into a renderer-neutral dict for the window [lo, hi).

    Returns ``{"trees": [...], "list_constructions": [...], "coverage": {...},
    "parse_status": str}``. Each tree node is a nested dict carrying kind, span,
    families, status, residual info, edge annotations, verbatim text, children.
    """
    nodes = {
        n.node_id: n
        for n in forest.syntax_nodes.values()
        if not (n.char_end <= lo or n.char_start >= hi)
    }
    contains: dict[str, list[str]] = {}
    has_parent: set[str] = set()
    other: dict[str, list[tuple[str, str]]] = {}
    for e in forest.syntax_edges:
        if e.src not in nodes:
            continue
        if e.kind == "contains" and e.dst in nodes:
            contains.setdefault(e.src, []).append(e.dst)
            has_parent.add(e.dst)
        elif e.kind != "contains":
            other.setdefault(e.src, []).append((e.kind, e.dst))

    def _node_dict(nid: str) -> dict[str, Any]:
        n = nodes[nid]
        edges = []
        for kind, dst in other.get(nid, []):
            if dst == nid:
                target = "self"
            elif dst in nodes:
                target = nodes[dst].kind
            else:
                target = dst[:8]
            edges.append({"kind": kind, "target": target})
        out: dict[str, Any] = {
            "kind": n.kind,
            "char_start": n.char_start,
            "char_end": n.char_end,
            "families": list(n.families),
            "node_status": n.node_status,
            "text": body[n.char_start : n.char_end],
            "edges": edges,
        }
        if n.kind == "residual_span":
            out["residual_reason"] = n.residual_reason
            out["residual_text"] = n.residual_text
        out["children"] = [
            _node_dict(c)
            for c in sorted(
                contains.get(nid, []),
                key=lambda i: (nodes[i].char_start, nodes[i].char_end),
            )
        ]
        return out

    roots = sorted(
        (nid for nid in nodes if nid not in has_parent),
        key=lambda i: (nodes[i].char_start, nodes[i].char_end),
    )
    trees = [_node_dict(r) for r in roots]

    lcs = []
    for lc in forest.list_constructions:
        ch = forest.syntax_nodes.get(lc.chapeau_id)
        if ch is None or ch.char_end <= lo or ch.char_start >= hi:
            continue
        frame = forest.syntax_nodes.get(lc.frame_node_id) if lc.frame_node_id else None
        lcs.append(
            {
                "frame_status": lc.frame_status,
                "is_inherited": lc.is_inherited,
                "chapeau": body[ch.char_start : ch.char_end].strip(),
                "n_items": len(lc.item_ids),
                "items": [
                    body[it.char_start : it.char_end].strip()
                    for it in (
                        forest.syntax_nodes[i]
                        for i in lc.item_ids
                        if i in forest.syntax_nodes
                    )
                ],
                "frame_leaf": (
                    None
                    if frame is None
                    else body[frame.char_start : frame.char_end].strip()
                ),
            }
        )

    cov = forest.coverage
    coverage = {
        "total_tokens": cov.total_tokens,
        "owned_tokens": cov.owned_tokens,
        "benign_tokens": cov.benign_tokens,
        "residual_tokens": cov.residual_tokens,
        "silent_tokens": cov.silent_tokens,
        "is_partition": cov.is_partition(),
        "family_token_counts": dict(cov.family_token_counts),
    }
    return {
        "trees": trees,
        "list_constructions": lcs,
        "coverage": coverage,
        "parse_status": str(getattr(forest.parse_status, "value", forest.parse_status)),
    }


def render_forest_view(view: dict[str, Any], statute_id: str) -> str:
    lines: list[str] = []
    lines.append(f"FOREST — {statute_id}")
    lines.append("=" * 60)

    def _emit(node: dict[str, Any], depth: int) -> None:
        pad = "  " * depth
        fam = "  {" + "|".join(node["families"]) + "}" if node["families"] else ""
        status = (
            ""
            if node["node_status"] in ("ok", "owned", "covered")
            else f"  <{node['node_status']}>"
        )
        extra = ""
        if node["kind"] == "residual_span":
            extra = f"  RESIDUAL:{node.get('residual_reason', '')}"
        ann = "".join(f"  ->{e['kind']}({e['target']})" for e in node["edges"])
        lines.append(
            f"{pad}{node['kind']} "
            f"[{node['char_start']}:{node['char_end']}]{fam}{status}{extra}{ann}"
        )
        text = node["text"].replace("\n", "\\n")
        lines.append(f"{pad}  · {text[:88]!r}")
        for child in node["children"]:
            _emit(child, depth + 1)

    for tree in view["trees"]:
        _emit(tree, 0)

    lcs = view["list_constructions"]
    if lcs:
        lines.append("")
        lines.append(f"list_constructions ({len(lcs)}):")
        for lc in lcs:
            lines.append(
                f"  frame_status={lc['frame_status']}  items={lc['n_items']}"
            )
            lines.append(f"    chapeau · {lc['chapeau'][:80]!r}")
            if lc["frame_leaf"]:
                lines.append(f"    frame leaf · {lc['frame_leaf'][:60]!r}")
            for it in lc["items"]:
                lines.append(f"      - {it[:72]!r}")

    cov = view["coverage"]
    total = max(1, cov["total_tokens"])
    lines.append("")
    lines.append("COVERAGE CENSUS (union-ownership partition)")
    lines.append(f"  total signal tokens : {cov['total_tokens']}")
    lines.append(
        f"  owned (>=1 family)  : {cov['owned_tokens']}  "
        f"({100 * cov['owned_tokens'] / total:.2f}%)"
    )
    lines.append(f"  benign (no signal)  : {cov['benign_tokens']}")
    lines.append(f"  typed residual      : {cov['residual_tokens']}")
    lines.append(
        f"  SILENT (frontier)   : {cov['silent_tokens']}  "
        f"({100 * cov['silent_tokens'] / total:.3f}%)"
    )
    lines.append(f"  partition ok        : {cov['is_partition']}")
    lines.append(f"  parse_status        : {view['parse_status']}")
    top = sorted(cov["family_token_counts"].items(), key=lambda kv: -kv[1])[:8]
    if top:
        lines.append(
            "  family token counts : "
            + ", ".join(f"{k}={v}" for k, v in top)
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# View 2: JOHTOLAUSE
# ---------------------------------------------------------------------------


def _sub_ref_summary(sr: Any) -> str:
    bits: list[str] = []
    if getattr(sr, "momentti", 0):
        bits.append(f"mom={sr.momentti}")
    if getattr(sr, "item", ""):
        bits.append(f"kohta={sr.item}")
    facet = getattr(sr, "facet", None)
    if facet is not None:
        bits.append(f"facet={getattr(facet, 'value', facet)}")
    if getattr(sr, "special", ""):
        bits.append(f"special={sr.special!r}")
    return ",".join(bits) or "whole"


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _enum_name(value: Any) -> str:
    """Human-readable enum member name (e.g. ``SECTION``), or str(value)."""
    return str(getattr(value, "name", value))


def build_johtolause_view(text: str, statute_id: str = "") -> dict[str, Any]:
    """Parse an amendment johtolause and project verb groups + lowered ops."""
    from lawvm.finland.johtolause.api import parse_clause

    result = parse_clause(text, statute_id=statute_id)
    sc = result.enriched_surface_clause or result.surface_clause

    verb_groups: list[dict[str, Any]] = []
    if sc is not None:
        for vg in sc.verb_groups:
            nodes: list[dict[str, Any]] = []
            for nd in vg.nodes:
                node_type = type(nd).__name__
                entry: dict[str, Any] = {"node_type": node_type}
                if node_type in ("SurfaceTargetRef", "SurfaceInsertion"):
                    entry["kind"] = _enum_name(getattr(nd, "kind", ""))
                    entry["label"] = getattr(nd, "label", "")
                    entry["chapter"] = getattr(nd, "chapter", "")
                    entry["part"] = getattr(nd, "part", "")
                    sub = getattr(nd, "sub_refs", None)
                    if sub is None:
                        st = getattr(nd, "sub_target", None)
                        sub = (st,) if st is not None else ()
                    entry["sub_refs"] = [_sub_ref_summary(s) for s in sub]
                    entry["is_exception"] = bool(getattr(nd, "is_exception", False))
                elif node_type == "SurfaceScopeBlock":
                    entry["scope_kind"] = _enum_name(getattr(nd, "scope_kind", ""))
                    entry["scope_label"] = getattr(nd, "scope_label", "")
                    entry["n_targets"] = len(getattr(nd, "targets", ()))
                else:
                    entry["repr"] = repr(nd)
                nodes.append(entry)
            verb_groups.append({"verb": _enum_name(vg.verb), "nodes": nodes})

    parsed_ops: list[dict[str, Any]] = []
    for op in result.parsed_ops:
        parsed_ops.append(
            {
                "verb": op.verb,
                "kind": op.kind,
                "chapter": op.chapter,
                "number": op.number,
                "momentti": op.momentti,
                "item": op.item,
                "facet": str(op.facet) if op.facet is not None else None,
                "part": op.part,
            }
        )

    meta = []
    if sc is not None:
        meta = [str(_enum_value(getattr(m, "kind", type(m).__name__))) for m in sc.meta_clauses]

    return {
        "text": text,
        "statute_id": statute_id,
        "verb_groups": verb_groups,
        "meta_clauses": meta,
        "parsed_ops": parsed_ops,
        "parse_error": result.parse_error,
        "diagnostics": list(result.diagnostics),
        "section_target_count": _count_section_targets(verb_groups),
    }


def _count_section_targets(verb_groups: list[dict[str, Any]]) -> int:
    """Number of SECTION SurfaceTargetRef leaves across all verb groups."""
    count = 0
    for vg in verb_groups:
        for nd in vg["nodes"]:
            if nd["node_type"] == "SurfaceTargetRef" and nd.get("kind") == "SECTION":
                count += 1
    return count


def render_johtolause_view(view: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("JOHTOLAUSE")
    lines.append("=" * 60)
    lines.append(f"  {view['text']!r}")
    if view["parse_error"]:
        lines.append(f"  parse_error: {view['parse_error']}")
    for d in view["diagnostics"]:
        lines.append(f"  diag: {d}")
    if not view["verb_groups"]:
        lines.append("  (no surface clause)")
    for vg in view["verb_groups"]:
        lines.append(f"  VERB GROUP: {vg['verb']}")
        for nd in vg["nodes"]:
            if nd["node_type"] in ("SurfaceTargetRef", "SurfaceInsertion"):
                ctx = []
                if nd["part"]:
                    ctx.append(f"part {nd['part']}")
                if nd["chapter"]:
                    ctx.append(f"luku {nd['chapter']}")
                ctxs = (" [" + ", ".join(ctx) + "]") if ctx else ""
                subs = nd["sub_refs"]
                substr = ("  sub: " + " | ".join(subs)) if subs else ""
                exc = "  EXCEPTION" if nd.get("is_exception") else ""
                tag = "INSERT" if nd["node_type"] == "SurfaceInsertion" else nd["kind"]
                lines.append(f"    └─ {tag} {nd['label']!r}{ctxs}{substr}{exc}")
            elif nd["node_type"] == "SurfaceScopeBlock":
                lines.append(
                    f"    └─ SCOPE {nd['scope_kind']} {nd['scope_label']!r} "
                    f"({nd['n_targets']} targets)"
                )
            else:
                lines.append(f"    └─ {nd['node_type']}: {nd['repr']}")
    if view["meta_clauses"]:
        lines.append(f"  META CLAUSES: {view['meta_clauses']}")
    lines.append(f"  PARSED OPS (lowered): {len(view['parsed_ops'])}")
    for op in view["parsed_ops"]:
        facet = f"  facet={op['facet']}" if op["facet"] else ""
        lines.append(
            f"    • verb={op['verb']}  kind={op['kind']}  "
            f"number={op['number']!r}  momentti={op['momentti']}  "
            f"item={op['item']!r}  chapter={op['chapter']!r}{facet}"
        )
    lines.append(f"  SECTION targets: {view['section_target_count']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# View 3: MORPH
# ---------------------------------------------------------------------------


def analyze_surface(surface: str) -> tuple[str, ...]:
    """Reverse-analyze ``surface`` to candidate lemma(s).

    Single seam over the morphology analyzer so a future open-vocabulary
    analyzer can be swapped in here WITHOUT touching the view / CLI surface.
    Returns the (possibly empty / possibly ambiguous) lemma tuple; an empty
    tuple is an honest "out of known vocabulary", never a fabricated guess.
    """
    from lawvm.finland.morphology.lemma_index import build_lemma_index

    return build_lemma_index().analyze(surface)


def build_morph_view(word: str) -> dict[str, Any]:
    """Show M1 generation paradigm for the lemma + reverse analysis of ``word``.

    ``word`` is treated both as a candidate lemma (generation) and as a surface
    to invert (analysis). If it is a surface of a known lemma, that lemma's
    paradigm is also generated, so ``--morph laissa`` shows the laki paradigm.
    """
    from lawvm.finland.morphology.api import MorphNumber
    from lawvm.finland.morphology.generate import generate_forms
    from lawvm.finland.morphology.heads import head_entry

    analysis = analyze_surface(word)

    # Choose the lemma(s) to generate: the analyzed lemma(s) if the word is a
    # recognized surface, else the word itself as a candidate lemma.
    lemmas_to_generate = list(analysis) if analysis else [word]

    paradigms: list[dict[str, Any]] = []
    for lemma in lemmas_to_generate:
        try:
            entry = head_entry(lemma)
        except Exception as ex:  # noqa: BLE001 — surfaced as a typed paradigm error
            paradigms.append({"lemma": lemma, "error": str(ex), "forms": []})
            continue
        forms = generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL))
        paradigms.append(
            {
                "lemma": lemma,
                "morph_class": entry.morph_class,
                "gradation": entry.gradation,
                "locative_series": entry.locative_series,
                "forms": [
                    {
                        "number": f.number.value,
                        "case": f.case.value,
                        "surface": f.surface,
                        "rule_id": f.rule_id,
                        "certainty": f.certainty,
                    }
                    for f in forms
                ],
            }
        )

    return {
        "word": word,
        "analysis": list(analysis),
        "analysis_status": (
            "unknown"
            if not analysis
            else ("ambiguous" if len(analysis) > 1 else "unique")
        ),
        "paradigms": paradigms,
    }


def render_morph_view(view: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"MORPH — {view['word']!r}")
    lines.append("=" * 60)
    lines.append("ANALYSIS (reverse: surface -> lemma, fail-loud)")
    if view["analysis_status"] == "unknown":
        lines.append(f"  {view['word']!r} -> <unknown / out of closed vocabulary>")
    else:
        amb = "  AMBIGUOUS" if view["analysis_status"] == "ambiguous" else ""
        lines.append(f"  {view['word']!r} -> {', '.join(view['analysis'])}{amb}")
    lines.append("")
    lines.append("GENERATION (M1, generation-first, rule-tagged)")
    for para in view["paradigms"]:
        if para.get("error"):
            lines.append(f"  {para['lemma']}: <{para['error']}>")
            continue
        lines.append(
            f"  {para['lemma']}  (morph_class={para['morph_class']}, "
            f"gradation={para['gradation']}, loc={para['locative_series']})"
        )
        for f in para["forms"]:
            flag = "" if f["certainty"] == "deterministic" else f"  [{f['certainty']}]"
            lines.append(
                f"      {f['number']:8} {f['case']:11} -> "
                f"{f['surface']:18} ({f['rule_id']}){flag}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# View 4: CLAUSES
# ---------------------------------------------------------------------------


def build_clause_view(
    source_unit_id: str,
    body: str,
    lo: int,
    hi: int,
) -> dict[str, Any]:
    """Segment ``body`` into sentences / clauses within the window [lo, hi)."""
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    ci = build_clause_index(source_unit_id, body)
    sentences = [
        {
            "char_start": s.char_start,
            "char_end": s.char_end,
            "text": body[s.char_start : s.char_end].strip(),
        }
        for s in ci.sentences
        if not (s.char_end <= lo or s.char_start >= hi)
    ]
    clauses = [
        {
            "char_start": c.char_start,
            "char_end": c.char_end,
            "text": body[c.char_start : c.char_end].strip(),
        }
        for c in ci.clauses
        if not (c.char_end <= lo or c.char_start >= hi)
    ]
    return {"sentences": sentences, "clauses": clauses}


def render_clause_view(view: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("SENTENCE / CLAUSE SEGMENTATION")
    lines.append("=" * 60)
    lines.append(f"  {len(view['sentences'])} sentence(s):")
    for i, s in enumerate(view["sentences"]):
        lines.append(
            f"    S{i} [{s['char_start']}:{s['char_end']}]  {s['text'][:90]!r}"
        )
    lines.append(f"  {len(view['clauses'])} clause(s):")
    for i, c in enumerate(view["clauses"]):
        lines.append(
            f"    C{i} [{c['char_start']}:{c['char_end']}]  {c['text'][:90]!r}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI handler (thin wrapper)
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], rendered: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
    else:
        print(rendered)


def main(args: argparse.Namespace) -> None:
    as_json: bool = bool(getattr(args, "json", False))

    if getattr(args, "johtolause", None) is not None:
        view = build_johtolause_view(args.johtolause, statute_id=args.statute or "")
        _emit(view, render_johtolause_view(view), as_json)
        return

    if getattr(args, "morph", None) is not None:
        view = build_morph_view(args.morph)
        _emit(view, render_morph_view(view), as_json)
        return

    if getattr(args, "text", None) is not None:
        # CLAUSES over raw text (no corpus needed).
        view = build_clause_view("text#inline", args.text, 0, len(args.text))
        _emit(view, render_clause_view(view), as_json)
        return

    if not args.statute:
        raise SystemExit(
            "ERROR: choose a view — one of --johtolause, --morph, --text, or "
            "--statute (with --grep/--provision for forest, or --clauses)."
        )

    bundle, unit = _load_statute_body(args.statute)
    body = unit.raw_text
    lo, hi = _resolve_span(
        body, grep=args.grep, provision=args.provision, unit=unit
    )

    if getattr(args, "clauses", False):
        view = build_clause_view(unit.source_unit_id, body, lo, hi)
        _emit(view, render_clause_view(view), as_json)
        return

    forest = _build_forest(bundle, unit)
    view = build_forest_view(forest, body, lo, hi)
    _emit(view, render_forest_view(view, args.statute), as_json)
