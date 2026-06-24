"""lawvm analyze-bill — a structured BILL IMPACT REPORT for one amending statute.

This is the first application-layer ("L4") read-only vertical over the existing
Finnish parse + Legal Surface Graph machinery. It turns an amending statute (the
enacted form of a government bill) into a machine-visible account of WHAT the
bill does to the body of law, and surfaces structural patterns that are
CANDIDATES for "a relevant channel left structurally unowned" — for downstream
human/LLM judgment, never adjudicated here.

NO new parsing happens in this module. Every section composes an already-built
API:

* WHAT THE BILL DOES   — the johtolause parsed to ``ParsedOp`` (insert / amend /
                          repeal / renumber of which provisions).
* SURFACE DELTA        — the Legal Surface Graph the amendment text induces:
    - NEW DELEGATIONS    (``delegation_frame`` nodes: who is granted what
                          norm-giving power — the headline "authority transfer").
    - REFERENCES         (``reference_resolution`` nodes by resolution status:
                          resolved / statute_only / ambiguous / open / broken).
    - DEFINITIONS        (``definition_binding`` nodes: terms the bill defines).
    - BROKEN-REF RISK    (for each REPEAL op, references that point at the
                          repealed target — scoped to the bill's own delta;
                          full corpus-wide back-reference scan is out of scope
                          for v0 and clearly labelled as such).
* UNOWNED-CHANNEL       — a thin, clearly-labelled JUDGMENT-FRONTIER layer that
  CANDIDATES              deterministically flags structural patterns (a small
                          closed rule list) as candidates for judgment. These are
                          NOT findings, carry NO magnitude / score, and the
                          target is never guessed (tag-don't-guess).

Rendering lives in pure ``build_*`` / ``render_*`` functions that take parsed
objects (a list of ``ParsedOp`` and a built ``LegalSurfaceGraph``) and return
strings / dicts, so the CLI handler stays a thin wrapper and every builder is
testable on synthetic fixtures with NO corpus dependency. Fail-loud throughout:
a missing statute raises ``SystemExit`` with the offending id, never a silent
empty report.
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lawvm.core.legal_surface_graph import LegalSurfaceGraph, SurfaceNode
    from lawvm.core.stage_result import StageResult
    from lawvm.finland.johtolause.types import ParsedOp


# ---------------------------------------------------------------------------
# Graph node-kind / payload-field constants (mirror the lens definitions; we
# only READ these — they are owned by the legal_surface lenses).
# ---------------------------------------------------------------------------

_KIND_DELEGATION = "delegation_frame"
_KIND_REFERENCE = "reference_resolution"
_KIND_DEFINITION = "definition_binding"

# The four reference-resolution states this report surfaces (BROKEN is folded in
# because a broken reference IS a structural risk; UNSUPPORTED is reported under
# its own label). The graph status vocabulary is the closed ResolutionStatus set.
_RESOLVED = "resolved"
_BROKEN = "broken"

_VERB_LABELS = {
    "M": "AMEND",
    "K": "REPEAL",
    "L": "INSERT",
    "S": "RENUMBER",
}

_KIND_LABELS = {
    "P": "section §",
    "L": "chapter luku",
    "O": "part osa",
    "N": "nimike",
    "A": "appendix liite",
}


# ---------------------------------------------------------------------------
# Corpus access (lazy — only the CLI handler needs it; the builders are pure)
# ---------------------------------------------------------------------------


def _load_amendment(statute_id: str) -> tuple[bytes, str, str]:
    """Return ``(xml_bytes, johtolause_text, raw_body)`` for ``statute_id``.

    Fail-loud: a missing statute raises ``SystemExit`` with the offending id.
    """
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import build_surface_bundle
    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.export_fi_interlinks import _get_statute_xml
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        raise SystemExit(f"ERROR: no archived source XML for statute {statute_id!r}")
    johto = get_johtolause(xml_bytes)
    if not johto:
        raise SystemExit(
            f"ERROR: no johtolause (enacting clause) in statute {statute_id!r} — "
            "not an amending statute, or unsupported source shape"
        )
    bundle = build_surface_bundle(xml_bytes, statute_id)
    body = bundle.units[0].raw_text if bundle.units else ""
    return xml_bytes, johto, body


def _parse_ops(johto: str, statute_id: str) -> list["ParsedOp"]:
    from lawvm.finland.johtolause.api import parse_clause

    result = parse_clause(johto, statute_id=statute_id)
    if result.parse_error:
        raise SystemExit(
            f"ERROR: johtolause parse failed for {statute_id!r}: {result.parse_error}"
        )
    return list(result.parsed_ops)


def _build_graph_stage(
    xml_bytes: bytes, statute_id: str
) -> "StageResult[LegalSurfaceGraph]":
    """Build the Legal Surface Graph as a typed ``StageResult`` (row #5).

    The default registry pair is what gives by-name reference resolution its
    real recall; without it, references degrade to ``statute_only``. The
    registry build announces (via warnings) if it falls back to a small sample,
    so a degraded run is never silent.

    Returns the STAGED form so the broken-reference branch reads the typed
    ``coverage`` / ``residuals`` account (the ``unowned_violation`` channel),
    NOT bare per-node status strings.
    """
    from lawvm.finland.legal_surface.graph_build import (
        build_legal_surface_graph_staged,
    )
    from lawvm.finland.references.resolve import build_default_registries

    statute_registry, eu_registry = build_default_registries()
    return build_legal_surface_graph_staged(
        xml_bytes,
        statute_id,
        statute_registry=statute_registry,
        eu_registry=eu_registry,
    )


# ---------------------------------------------------------------------------
# Small graph helpers (pure)
# ---------------------------------------------------------------------------


def _nodes_of_kind(graph: "LegalSurfaceGraph", kind: str) -> list["SurfaceNode"]:
    return [n for n in graph.nodes.values() if n.node_kind == kind]


def _span_text(node: "SurfaceNode", body: str) -> str:
    """Verbatim source text for a node's span, or '' when unanchored."""
    ref = node.source_ref
    if ref is None:
        return ""
    start = int(ref.char_start)
    end = int(ref.char_end)
    if start < 0 or end <= start or end > len(body):
        return ""
    return body[start:end].strip()


def _status_str(node: "SurfaceNode") -> str:
    """The node's status as a plain string (handles enum-or-str)."""
    return str(getattr(node.status, "value", node.status))


# ---------------------------------------------------------------------------
# Section 1: WHAT THE BILL DOES (lowered ops)
# ---------------------------------------------------------------------------


def build_op_summary(parsed_ops: list["ParsedOp"]) -> dict[str, Any]:
    """Project lowered ParsedOps into a renderer-neutral 'what the bill does'."""
    by_verb: dict[str, int] = {}
    ops: list[dict[str, Any]] = []
    for op in parsed_ops:
        by_verb[op.verb] = by_verb.get(op.verb, 0) + 1
        ops.append(
            {
                "verb": op.verb,
                "verb_label": _VERB_LABELS.get(op.verb, op.verb),
                "kind": op.kind,
                "kind_label": _KIND_LABELS.get(op.kind, op.kind),
                "part": op.part,
                "chapter": op.chapter,
                "number": op.number,
                "momentti": op.momentti,
                "item": op.item,
                "facet": str(op.facet) if op.facet is not None else None,
                "code": op.code(),
            }
        )
    # Canonical verb order for readability (insert -> amend -> repeal -> renumber),
    # unknown verbs trailing in code order.
    verb_order = {"L": 0, "M": 1, "K": 2, "S": 3}
    ordered = sorted(by_verb.items(), key=lambda kv: (verb_order.get(kv[0], 9), kv[0]))
    return {
        "n_ops": len(parsed_ops),
        "by_verb": {_VERB_LABELS.get(v, v): c for v, c in ordered},
        "ops": ops,
    }


def repealed_targets(parsed_ops: list["ParsedOp"]) -> list[dict[str, str]]:
    """The provision targets each REPEAL (verb 'K') op removes.

    Returned as a list of {number, chapter, momentti, item} dicts — used both by
    the broken-reference-risk scan and the unowned-channel candidate layer.
    """
    out: list[dict[str, str]] = []
    for op in parsed_ops:
        if op.verb != "K":
            continue
        out.append(
            {
                "number": op.number,
                "chapter": op.chapter,
                "momentti": str(op.momentti) if op.momentti else "",
                "item": op.item,
                "code": op.code(),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Section 2: SURFACE DELTA — delegations
# ---------------------------------------------------------------------------


def build_delegation_delta(
    graph: "LegalSurfaceGraph", body: str
) -> dict[str, Any]:
    """New / changed delegations the bill's text introduces.

    Each ``delegation_frame`` node is the surface FORM of a norm-giving grant:
    ``delegate_actor`` is who is granted power, ``instrument_kind`` is the
    instrument (asetus / määräys / ohje / päätös), ``binding_strength`` is the
    surface modal (must / may). This is a surface fact, never a legal
    conclusion ('valid delegation' is not asserted).
    """
    delegations: list[dict[str, Any]] = []
    for node in _nodes_of_kind(graph, _KIND_DELEGATION):
        payload = dict(node.payload)
        delegations.append(
            {
                "node_id": node.node_id,
                "delegate_actor": payload.get("delegate_actor", ""),
                "instrument_kind": payload.get("instrument_kind", ""),
                "binding_strength": payload.get("binding_strength", ""),
                "status": _status_str(node),
                "span_text": _span_text(node, body),
            }
        )
    delegations.sort(key=lambda d: (str(d["delegate_actor"]), str(d["instrument_kind"])))
    return {"count": len(delegations), "delegations": delegations}


# ---------------------------------------------------------------------------
# Section 3: SURFACE DELTA — references
# ---------------------------------------------------------------------------


def build_reference_delta(
    graph: "LegalSurfaceGraph", body: str
) -> dict[str, Any]:
    """References the bill's new text makes, grouped by resolution status."""
    refs: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    for node in _nodes_of_kind(graph, _KIND_REFERENCE):
        status = _status_str(node)
        by_status[status] = by_status.get(status, 0) + 1
        payload = dict(node.payload)
        raw_candidates = payload.get("candidates") or ()
        candidates = list(raw_candidates) if isinstance(raw_candidates, (list, tuple)) else []
        refs.append(
            {
                "node_id": node.node_id,
                "status": status,
                "surface_text": payload.get("surface_text", ""),
                "work_id": payload.get("work_id"),
                "candidates": candidates,
                "span_text": _span_text(node, body),
            }
        )
    refs.sort(key=lambda r: (str(r["status"]), str(r["surface_text"])))
    return {
        "count": len(refs),
        "by_status": dict(sorted(by_status.items())),
        "references": refs,
    }


# ---------------------------------------------------------------------------
# Section 4: SURFACE DELTA — broken / dangling reference risk
# ---------------------------------------------------------------------------


def _ref_risk_entry(
    node_id: str, surface_text: str, status_str: str, span_text: str
) -> dict[str, Any]:
    """One reference-risk report entry (single construction site for both arms)."""
    return {
        "node_id": node_id,
        "surface_text": surface_text,
        "status": status_str,
        "span_text": span_text,
    }


def build_broken_ref_risk(
    parsed_ops: list["ParsedOp"],
    graph_stage: "StageResult[LegalSurfaceGraph]",
    body: str,
) -> dict[str, Any]:
    """Reference risk created by the bill's REPEAL ops.

    SCOPE (v0): a full corpus-wide back-reference scan ("every statute that
    cites the repealed target") is deliberately OUT OF SCOPE for v0 — too heavy.
    Two cheaper, fully deterministic signals are reported instead:

    * ``status_broken`` — references the surface waist's TYPED coverage account
      flags as a blocking ``unowned_violation`` residual (a reference that named
      a target which does not exist). The branch rides the typed
      :class:`~lawvm.core.stage_result.StageResult` residual channel (row #5),
      NOT a re-scan of bare per-node status strings: a broken-ref entry is built
      from each ``kind="unowned_violation"`` residual the staged producer
      emitted. This is the load-bearing rewire — sever the residual (or revert to
      bare-string scanning) and this signal disappears.
    * ``self_repeal_then_cited`` — for each REPEAL op in THIS bill, references
      WITHIN the amendment's own text whose surface mentions the repealed
      section number. A textual match (number appears in the cite surface), not
      a resolved target join — clearly a HEURISTIC within-bill scan.
    """
    graph = graph_stage.value
    targets = repealed_targets(parsed_ops)
    target_numbers = {t["number"] for t in targets if t["number"]}

    # BROKEN-REF branch — derived from the TYPED residual account, not bare
    # status strings. Each blocking unowned_violation residual the surface waist
    # emitted IS a broken reference (the genuine "named a target that does not
    # exist" failure class, 2D mapping). We resolve the residual back to its
    # reference node (residual.scope == node.node_id) only for the verbatim span.
    status_broken: list[dict[str, Any]] = []
    for residual in graph_stage.residuals:
        if residual.kind != "unowned_violation":
            continue
        node = graph.nodes.get(residual.scope)
        if node is not None and node.node_kind != _KIND_REFERENCE:
            # The surface broken-ref risk is a REFERENCE-resolution signal; a
            # non-reference violation (none today) is out of this section's scope.
            continue
        span = _span_text(node, body) if node is not None else ""
        status_broken.append(
            _ref_risk_entry(residual.scope, residual.text, _BROKEN, span)
        )
    status_broken.sort(key=lambda e: (str(e["surface_text"]), str(e["node_id"])))

    # Within-bill heuristic (unchanged): a reference whose surface cites a number
    # this very bill repeals. Tagged, never asserted as a confirmed dangling ref.
    self_repeal_cited: list[dict[str, Any]] = []
    for node in _nodes_of_kind(graph, _KIND_REFERENCE):
        surface = str(node.payload.get("surface_text", ""))
        entry = _ref_risk_entry(
            node.node_id, surface, _status_str(node), _span_text(node, body)
        )
        for num in target_numbers:
            if num and _surface_cites_number(surface, num):
                self_repeal_cited.append({**entry, "repealed_number": num})
                break

    return {
        "repealed_targets": targets,
        "scope_note": (
            "v0 scope: within-bill + graph-status only; corpus-wide "
            "back-reference scan deferred."
        ),
        "status_broken": status_broken,
        "self_repeal_then_cited": self_repeal_cited,
    }


def _surface_cites_number(surface: str, number: str) -> bool:
    """True if ``number`` appears as a standalone section token in ``surface``.

    Conservative token match (digit-bounded), not a resolved target join —
    avoids '1' matching '11'. Heuristic by design; the within-bill scan tags
    candidates rather than asserting confirmed dangling references.
    """
    if not number:
        return False
    idx = 0
    n = len(surface)
    while True:
        idx = surface.find(number, idx)
        if idx < 0:
            return False
        before_ok = idx == 0 or not surface[idx - 1].isdigit()
        after = idx + len(number)
        after_ok = after >= n or not surface[after].isdigit()
        if before_ok and after_ok:
            return True
        idx = after


# ---------------------------------------------------------------------------
# Section 5: SURFACE DELTA — definitions
# ---------------------------------------------------------------------------


def build_definition_delta(
    graph: "LegalSurfaceGraph", body: str
) -> dict[str, Any]:
    """Defined terms the bill introduces / changes (scope shifts)."""
    defs: list[dict[str, Any]] = []
    for node in _nodes_of_kind(graph, _KIND_DEFINITION):
        payload = dict(node.payload)
        defs.append(
            {
                "node_id": node.node_id,
                "term": payload.get("term", ""),
                "scope": payload.get("scope", ""),
                "binding_kind": payload.get("binding_kind", ""),
                "status": _status_str(node),
                "span_text": _span_text(node, body),
            }
        )
    defs.sort(key=lambda d: str(d["term"]))
    return {"count": len(defs), "definitions": defs}


# ---------------------------------------------------------------------------
# Section 6: UNOWNED-CHANNEL CANDIDATES (JUDGMENT FRONTIER — NOT findings)
# ---------------------------------------------------------------------------
#
# A SMALL CLOSED LIST of deterministic structural flags. Each is a CANDIDATE
# surfaced for human/LLM judgment — never an adjudicated finding, never scored,
# never given a magnitude, and the target is never guessed (tag-don't-guess).
# The rule id documents exactly what structural pattern fired.

_CANDIDATE_RULES = {
    "delegation_without_accountability": (
        "A norm-giving delegation is granted but the same statute's surface "
        "graph carries no accountability / oversight / reporting provision "
        "co-located with it. Candidate for: is this authority transfer left "
        "unaccountable in the enacting text?"
    ),
    "repeal_strands_reference": (
        "A REPEAL op removes a provision while a reference (graph-broken or a "
        "within-bill textual cite of the repealed number) still points at it. "
        "Candidate for: does this repeal strand a live reference?"
    ),
    "open_reference_introduced": (
        "The bill introduces a reference the resolver classifies as OPEN "
        "(vague catch-all naming no target). Candidate for: is this an "
        "under-specified pointer the enacting text leaves dangling?"
    ),
}

# Surface cues that, when co-located in the body, count as an accountability /
# oversight / reporting channel for the delegation-without-accountability rule.
# A closed, documented lexical list — deliberately conservative; its job is to
# AVOID over-flagging (a present cue suppresses the candidate), not to assert
# adequacy.
_ACCOUNTABILITY_CUES = (
    "valvo",        # valvonta / valvoo (oversight)
    "valvont",
    "raportoi",     # reporting
    "raportt",
    "kertomu",      # kertomus (report)
    "tilivelvol",   # tilivelvollisuus (accountability)
    "seuran",       # seuranta (monitoring)
    "arvioi",       # arviointi (evaluation)
)


def build_unowned_candidates(
    parsed_ops: list["ParsedOp"],
    graph_stage: "StageResult[LegalSurfaceGraph]",
    body: str,
    *,
    broken_ref_risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically flag structural patterns as candidates for judgment.

    Returns a list of candidate dicts, each tagged with its ``rule`` id and a
    plain-language ``why``. These are CANDIDATES for downstream human/LLM
    judgment — NOT findings, NO score, NO magnitude. The reference telos
    (civilizational full-accounting) is the SELECTOR of which channels are worth
    surfacing, never a good/bad grade applied here.

    Takes the surface waist's typed ``StageResult`` so the ``repeal_strands_
    reference`` rule's broken arm derives from the typed ``unowned_violation``
    residual (via :func:`build_broken_ref_risk`), not bare per-node statuses.
    """
    graph = graph_stage.value
    if broken_ref_risk is None:
        broken_ref_risk = build_broken_ref_risk(parsed_ops, graph_stage, body)

    candidates: list[dict[str, Any]] = []

    # Rule 1: delegation granted with no co-located accountability cue anywhere
    # in the bill's body text. Body-level (not span-local) cue check — a
    # conservative suppressor: any accountability cue in the bill suppresses ALL
    # delegation candidates (we flag only the strongest case: a delegating bill
    # with ZERO accountability language).
    body_lower = body.lower()
    has_accountability = any(cue in body_lower for cue in _ACCOUNTABILITY_CUES)
    for node in _nodes_of_kind(graph, _KIND_DELEGATION):
        if has_accountability:
            continue
        payload = dict(node.payload)
        candidates.append(
            {
                "rule": "delegation_without_accountability",
                "node_id": node.node_id,
                "subject": payload.get("delegate_actor", ""),
                "detail": (
                    f"{payload.get('delegate_actor', '?')} granted "
                    f"{payload.get('instrument_kind', '?')} power"
                ),
                "span_text": _span_text(node, body),
                "why": _CANDIDATE_RULES["delegation_without_accountability"],
            }
        )

    # Rule 2: a repeal strands a reference (graph-broken OR within-bill textual
    # cite of a repealed number).
    stranding = list(broken_ref_risk.get("status_broken", [])) + list(
        broken_ref_risk.get("self_repeal_then_cited", [])
    )
    for entry in stranding:
        candidates.append(
            {
                "rule": "repeal_strands_reference",
                "node_id": entry.get("node_id", ""),
                "subject": entry.get("surface_text", ""),
                "detail": (
                    f"reference {entry.get('surface_text', '?')!r} "
                    f"(status={entry.get('status', '?')})"
                ),
                "span_text": entry.get("span_text", ""),
                "why": _CANDIDATE_RULES["repeal_strands_reference"],
            }
        )

    # Rule 3: an OPEN reference is introduced (under-specified pointer).
    for node in _nodes_of_kind(graph, _KIND_REFERENCE):
        if _status_str(node) != "open":
            continue
        surface = str(node.payload.get("surface_text", ""))
        candidates.append(
            {
                "rule": "open_reference_introduced",
                "node_id": node.node_id,
                "subject": surface,
                "detail": f"open reference {surface!r}",
                "span_text": _span_text(node, body),
                "why": _CANDIDATE_RULES["open_reference_introduced"],
            }
        )

    by_rule: dict[str, int] = {}
    for c in candidates:
        by_rule[c["rule"]] = by_rule.get(c["rule"], 0) + 1

    return {
        "disclaimer": (
            "JUDGMENT FRONTIER — these are deterministic structural CANDIDATES "
            "for human/LLM judgment, NOT adjudicated findings. No score, no "
            "magnitude, target never guessed."
        ),
        "rule_catalog": dict(_CANDIDATE_RULES),
        "count": len(candidates),
        "by_rule": dict(sorted(by_rule.items())),
        "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# Top-level report assembly (pure)
# ---------------------------------------------------------------------------


def build_bill_report(
    statute_id: str,
    parsed_ops: list["ParsedOp"],
    graph_stage: "StageResult[LegalSurfaceGraph]",
    body: str,
) -> dict[str, Any]:
    """Assemble the full structured bill-impact report (corpus-free).

    Takes the surface waist's typed ``StageResult`` (row #5): the broken-ref
    branch rides ``graph_stage.residuals`` (the ``unowned_violation`` channel),
    while the surface-fact deltas read the graph value.
    """
    graph = graph_stage.value
    op_summary = build_op_summary(parsed_ops)
    delegation_delta = build_delegation_delta(graph, body)
    reference_delta = build_reference_delta(graph, body)
    broken_ref_risk = build_broken_ref_risk(parsed_ops, graph_stage, body)
    definition_delta = build_definition_delta(graph, body)
    unowned = build_unowned_candidates(
        parsed_ops, graph_stage, body, broken_ref_risk=broken_ref_risk
    )
    return {
        "statute_id": statute_id,
        "what_the_bill_does": op_summary,
        "surface_delta": {
            "delegations": delegation_delta,
            "references": reference_delta,
            "broken_ref_risk": broken_ref_risk,
            "definitions": definition_delta,
        },
        "unowned_channel_candidates": unowned,
    }


# ---------------------------------------------------------------------------
# Rendering (pure)
# ---------------------------------------------------------------------------


def render_bill_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    sid = report["statute_id"]
    lines.append(f"BILL IMPACT REPORT — {sid}")
    lines.append("=" * 64)

    # --- what the bill does ---
    wtbd = report["what_the_bill_does"]
    lines.append("")
    lines.append(f"WHAT THE BILL DOES ({wtbd['n_ops']} op(s))")
    by_verb = ", ".join(f"{k}={v}" for k, v in wtbd["by_verb"].items())
    lines.append(f"  by verb: {by_verb or '(none)'}")
    for op in wtbd["ops"]:
        ctx = []
        if op["part"]:
            ctx.append(f"osa {op['part']}")
        if op["chapter"]:
            ctx.append(f"luku {op['chapter']}")
        ctxs = (" [" + ", ".join(ctx) + "]") if ctx else ""
        sub = ""
        if op["momentti"]:
            sub = f" mom {op['momentti']}"
            if op["item"]:
                sub += f" kohta {op['item']}"
        facet = f" facet={op['facet']}" if op["facet"] else ""
        lines.append(
            f"    • {op['verb_label']:8} {op['kind_label']:14} "
            f"{op['number']!r}{ctxs}{sub}{facet}"
        )

    sd = report["surface_delta"]

    # --- delegations ---
    dele = sd["delegations"]
    lines.append("")
    lines.append(f"NEW DELEGATIONS — authority transfer ({dele['count']})")
    if not dele["delegations"]:
        lines.append("    (none recognised in the bill's text)")
    for d in dele["delegations"]:
        lines.append(
            f"    └─ {d['delegate_actor']!r} -> {d['instrument_kind']} "
            f"({d['binding_strength']})  [{d['status']}]"
        )
        if d["span_text"]:
            lines.append(f"         · {d['span_text'][:88]!r}")

    # --- references ---
    refs = sd["references"]
    lines.append("")
    lines.append(f"REFERENCES IN NEW TEXT ({refs['count']})")
    if refs["by_status"]:
        lines.append(
            "  by status: "
            + ", ".join(f"{k}={v}" for k, v in refs["by_status"].items())
        )
    for r in refs["references"]:
        tgt = f" -> {r['work_id']}" if r["work_id"] else ""
        cand = (
            f"  candidates={r['candidates']}" if r["candidates"] else ""
        )
        lines.append(
            f"    └─ [{r['status']:11}] {r['surface_text']!r}{tgt}{cand}"
        )

    # --- broken / dangling reference risk ---
    brr = sd["broken_ref_risk"]
    lines.append("")
    lines.append("BROKEN / DANGLING-REFERENCE RISK")
    lines.append(f"  ({brr['scope_note']})")
    rts = brr["repealed_targets"]
    lines.append(f"  repeal ops in this bill: {len(rts)}")
    for t in rts:
        lines.append(f"    - repeals {t['code']!r}")
    sb = brr["status_broken"]
    lines.append(f"  references with graph status=broken: {len(sb)}")
    for e in sb:
        lines.append(f"    └─ {e['surface_text']!r}")
    src = brr["self_repeal_then_cited"]
    lines.append(f"  within-bill cites of a repealed number: {len(src)}")
    for e in src:
        lines.append(
            f"    └─ {e['surface_text']!r} cites repealed {e['repealed_number']}"
        )

    # --- definitions ---
    defs = sd["definitions"]
    lines.append("")
    lines.append(f"NEW / CHANGED DEFINITIONS ({defs['count']})")
    if not defs["definitions"]:
        lines.append("    (none recognised in the bill's text)")
    for d in defs["definitions"]:
        scope = f"  scope={d['scope']}" if d["scope"] else ""
        lines.append(f"    └─ {d['term']!r}{scope}  [{d['status']}]")

    # --- unowned-channel candidates ---
    un = report["unowned_channel_candidates"]
    lines.append("")
    lines.append("CANDIDATES FOR JUDGMENT — unowned-channel frontier")
    lines.append(f"  {un['disclaimer']}")
    lines.append(f"  total candidates: {un['count']}")
    if un["by_rule"]:
        lines.append(
            "  by rule: "
            + ", ".join(f"{k}={v}" for k, v in un["by_rule"].items())
        )
    for c in un["candidates"]:
        lines.append(f"    ? [{c['rule']}] {c['detail']}")
        if c["span_text"]:
            lines.append(f"        · {c['span_text'][:84]!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI handler (thin wrapper)
# ---------------------------------------------------------------------------


def main(args: argparse.Namespace) -> None:
    statute_id: str = args.statute_id
    if not statute_id:
        raise SystemExit("ERROR: analyze-bill requires a statute id, e.g. 2018/1138")

    xml_bytes, johto, body = _load_amendment(statute_id)
    parsed_ops = _parse_ops(johto, statute_id)
    graph_stage = _build_graph_stage(xml_bytes, statute_id)

    report = build_bill_report(statute_id, parsed_ops, graph_stage, body)

    if bool(getattr(args, "json", False)):
        print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    else:
        print(render_bill_report(report))
