"""lawvm dump — inspect pipeline state at a named stage.

Named stages (MIGRATION_SPEC.md appendix):
  parse      Base statute XML → structured tree view
  extract    Johtolause → raw PEG ops (pre-repair)
  normalize  Raw ops → repaired ops (post-repair functions)
  apply      Final replayed state (full pipeline)

Usage:
    lawvm dump <statute_id>                              # FI final replayed body text; UK source parse
    lawvm dump <statute_id> --after parse                # base statute structure
    lawvm dump <statute_id> --after extract --source <amendment_id>
    lawvm dump <statute_id> --after normalize --source <amendment_id>
    lawvm dump <statute_id> --after apply                # same as default
    lawvm dump <statute_id> --address "section:12"       # filter to one provision
    lawvm dump <statute_id> --after extract --source 2017/794 --address "section:9a"
"""
from __future__ import annotations

import re
import sys
from typing import Optional, cast

from lxml import etree

from lawvm.finland.corpus import get_corpus
from lawvm.finland.metadata import _normalize_johtolause_verbs, get_johtolause
from lawvm.finland.normalize import parse_ops_fallback_heuristic
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.scope import assign_chapter_scope_from_johtolause as _assign_chapter_scope_from_johtolause
from lawvm.finland.xml_statute import XMLStatute
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml
from lawvm.finland.citation_routing import OP_KEYWORDS
from lawvm.finland.fallback_op_ids import stamp_fallback_op_ids
from lawvm.finland.johtolause import extract_legal_ops as extract_johtolause_legal_ops
from lawvm.finland.johtolause.diagnostics import extract_ops_diagnostic
from lawvm.finland.ops import classify_legal_operation_conversion_skip
from lawvm.tools.pit_projection import comparison_ir_for_pit
from lawvm.finland.statute import ReplayState
from lawvm.core.clause_ast import (
    ClauseAST, VerbGroup, ScopedBlock, RefAmend, TextAmend, LabelAmend, MetaClause,
)


# ---------------------------------------------------------------------------
# ClauseAST formatter
# ---------------------------------------------------------------------------

def _format_clause_ast(ast: ClauseAST) -> str:
    """Return a readable indented-tree string for a ClauseAST."""
    lines = []

    def _addr(addr) -> str:
        return str(addr) if addr is not None else "?"

    def _node(n, indent: str) -> None:
        if isinstance(n, VerbGroup):
            lines.append(f"{indent}VerbGroup({n.verb}):")
            for child in n.nodes:
                _node(child, indent + "  ")
        elif isinstance(n, ScopedBlock):
            lines.append(f"{indent}ScopedBlock({_addr(n.scope)}):")
            for child in n.children:
                _node(child, indent + "  ")
        elif isinstance(n, RefAmend):
            anchor_part = f", anchor={_addr(n.anchor)}" if n.anchor else ""
            lines.append(f"{indent}RefAmend({n.action}, {_addr(n.target)}{anchor_part})")
        elif isinstance(n, LabelAmend):
            label_part = f", new_label={n.new_label}" if n.new_label else ""
            lines.append(f"{indent}LabelAmend({n.action.value}, {_addr(n.target)}{label_part})")
        elif isinstance(n, TextAmend):
            lines.append(f"{indent}TextAmend({n.action}, {_addr(n.target)})")
        elif isinstance(n, MetaClause):
            raw_trunc = n.raw_text[:60].replace("\n", " ")
            lines.append(f"{indent}MetaClause({n.kind.value}: {raw_trunc!r})")
        else:
            lines.append(f"{indent}{n!r}")

    for vg in ast.verb_groups:
        _node(vg, "  ")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage: PARSE — base statute tree structure
# ---------------------------------------------------------------------------

def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def _heading_text(el: etree._Element) -> str:
    h = el.find("{*}heading")
    if h is None:
        h = el.find("heading")
    if h is not None:
        return "".join(str(_t) for _t in h.itertext()).strip()[:60]
    return ""


def dump_parse(
    sid: str,
    address: Optional[str] = None,
    *,
    db_path: str | None = None,
) -> None:
    """Show base statute structure (after XML parse, before any amendments)."""
    from lawvm.tools.source_dump import build_uk_source_dump, is_uk_statute_id, _format_text

    if is_uk_statute_id(sid):
        bundle = build_uk_source_dump(sid, address, db_path=db_path)
        print(_format_text(bundle))
        return

    cs = get_corpus()
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        print(f"ERROR: {sid} not in zip", file=sys.stderr)
        sys.exit(1)

    master = XMLStatute(xml_bytes)
    tree = master.tree
    print(f"Statute: {sid}")
    print(f"Title  : {master.title}")
    print("Stage  : PARSE (base statute, no amendments)")
    print()

    body = tree.find(".//{*}body")
    if body is None:
        print("(no body element found)")
        return

    addr_filter = _parse_address(address) if address else None

    def _walk(node, indent=""):
        tag = _tag(node)
        if tag in ("chapter", "part"):
            num = _num_text(node)
            heading = _heading_text(node)
            # num already contains "1 luku" / "2 osa" in Finnish AKN
            label = num if num else _kind_label(tag)
            print(f"{indent}[{label}] {heading}")
            for child in node:
                _walk(child, indent + "  ")
        elif tag == "section":
            _print_section(node, indent=indent, addr_filter=addr_filter)
        elif tag == "hcontainer":
            # wrapper — recurse without extra indent
            for child in node:
                _walk(child, indent)
        # ignore other elements (num, heading already consumed by parent)

    for child in body:
        _walk(child)

    print()
    # Summary
    sections = cast(list[etree._Element], body.xpath(".//*[local-name()='section']"))
    chapters = cast(list[etree._Element], body.xpath(".//*[local-name()='chapter']"))
    print(f"Summary: {len(chapters)} chapters, {len(sections)} sections")


def _kind_label(tag: str) -> str:
    return {"chapter": "luku", "part": "osa", "hcontainer": "hcontainer"}.get(tag, tag)


def _print_section(sec: etree._Element, indent: str = "", addr_filter=None) -> None:
    num = _num_text(sec)
    heading = _heading_text(sec)
    # Match both "12" and "12 §"
    num_norm = num.replace("§", "").strip()
    if addr_filter and addr_filter[0] == "section" and addr_filter[1] != num_norm:
        return
    subsecs = sec.findall("{*}subsection")
    if not subsecs:
        subsecs = sec.findall("subsection")
    paras = sec.findall("{*}paragraph")
    if not paras:
        paras = sec.findall("paragraph")
    label = num if num.endswith("§") else f"{num} §"
    detail = ""
    if subsecs:
        detail = f"  [{len(subsecs)} mom]"
        para_counts = [len(s.findall("{*}paragraph") or s.findall("paragraph")) for s in subsecs]
        if any(p > 0 for p in para_counts):
            detail += f" kohdat: {para_counts}"
    elif paras:
        detail = f"  [{len(paras)} kohta]"
    print(f"{indent}{label}  {heading}{detail}")
    if addr_filter:
        content = "".join(str(_t) for _t in sec.itertext()).strip()
        print(f"{indent}  CONTENT: {content[:500]}...")


def _parse_address(address: str):
    """Parse 'section:12' or 'chapter:3/section:9a' → list of (kind, num) pairs."""
    parts = []
    for seg in address.split("/"):
        if ":" in seg:
            kind, num = seg.split(":", 1)
            parts.append((kind.strip(), num.strip()))
    return parts[-1] if parts else None  # use last segment as filter


def _matches_container(addr_filter, tag: str, num: str) -> bool:
    kind, val = addr_filter
    return tag.startswith(kind) or kind in tag


# ---------------------------------------------------------------------------
# Stage: EXTRACT / NORMALIZE — ops from one amendment
# ---------------------------------------------------------------------------

def dump_extract(sid: str, source_mid: str, after_normalize: bool = False,
                 address: Optional[str] = None) -> None:
    """Show ops extracted from one amendment (EXTRACT stage, optionally NORMALIZE)."""
    cs = get_corpus()
    xml_bytes = cs.read_source(source_mid)
    if xml_bytes is None:
        print(f"ERROR: amendment {source_mid} not in zip", file=sys.stderr)
        sys.exit(1)

    stage = "NORMALIZE" if after_normalize else "EXTRACT"
    print(f"Statute  : {sid}")
    print(f"Amendment: {source_mid}")
    print(f"Stage    : {stage}")
    print()

    johto = get_johtolause(xml_bytes)
    if not johto or len(johto) < 50:
        tree = etree.fromstring(xml_bytes)
        sec1 = tree.find(".//{*}section[@eId='sec_1']")
        if sec1 is not None:
            johto = etree.tostring(sec1, method="text", encoding="unicode").strip()
            johto = re.sub(r'^\d+\s*[a-zäöå]?\s*§\s*', '', johto).strip()

    johto = _normalize_johtolause_verbs(johto)

    print("Johtolause:")
    for line in johto.splitlines():
        print(f"  {line}")
    print()

    if not any(kw in johto.lower() for kw in OP_KEYWORDS):
        print("(no op keywords found — amendment may be a voimaantuloasetus or similar)")
        return

    # EXTRACT: Phase 2 PEG output via LegalOperation boundary
    legal_ops = extract_johtolause_legal_ops(johto)
    print(f"PEG legal_ops ({len(legal_ops)}):")
    for lo in legal_ops:
        print(f"  [{lo.action}] {lo.target}")
    print()

    # ClauseAST: structured AST from PEG ParsedOp level
    diag = extract_ops_diagnostic(johto)
    ast = diag.clause_ast
    if ast and ast.verb_groups:
        print("ClauseAST:")
        print(_format_clause_ast(ast))
        print()
    else:
        print("ClauseAST: (empty)")
        print()

    base_xml = cs.read_source(sid)
    if base_xml is not None:
        master = XMLStatute(base_xml)
        master_state = ReplayState(ir=master.ir)
    else:
        print(f"WARNING: base statute {sid} not in zip — skipping context-aware repairs",
              file=sys.stderr)
        master_state = None

    if legal_ops:
        if master_state:
            legal_ops = _assign_chapter_scope_from_johtolause(legal_ops, johto, master_state)
        skipped_los = []
        ops = []
        for i, lo in enumerate(legal_ops):
            converted_ops = AmendmentOp.from_lo(lo, i)
            if not converted_ops:
                skip = classify_legal_operation_conversion_skip(lo)
                if skip is not None:
                    skipped_los.append(skip)
            ops.extend(converted_ops)
    else:
        ops = []
        skipped_los = []

    if not ops:
        fallback = parse_ops_fallback_heuristic(johto)
        if fallback:
            ops = stamp_fallback_op_ids(fallback, sid)
            print("(PEG returned empty — fallback heuristic used)")
            print()

    print(f"Compiled AmendmentOps ({len(ops)}):")
    for op in ops:
        print(f"  {op.description()}")
    for skip in skipped_los:
        print(f"  (skipped LO {skip.op_id}: {skip.reason_code})")
    print()

    if not after_normalize:
        return

    # Repairs are now integrated into the LO compile step above.
    # "Compiled AmendmentOps" already reflects all LO repair passes.
    print("(Repairs integrated into LO compile step — see 'Compiled AmendmentOps' above.)")
    print()


# ---------------------------------------------------------------------------
# Stage: APPLY — final replayed text (or a single provision)
# ---------------------------------------------------------------------------

def _comparison_ir(master):
    return comparison_ir_for_pit(master, query_type="in_force")


def _dump_apply(
    sid: str,
    address: Optional[str],
    stop_before: str = "",
    as_of: str = "",
) -> None:
    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=sid, stop_before=stop_before, quiet=True, as_of=as_of),
    )

    if not address:
        print(f"Statute: {sid}")
        print("Stage  : APPLY (full replay)")
        print()
        # ``dump`` always shows technical IR labels (KIND/LABEL/text-snippet)
        # — the pretty human-readable projection is ``lawvm show``'s job. The
        # dump CLI is an IR-inspection tool, not a statute reader.
        from lawvm.finland.ir_tree_dump import format_ir_tree
        replay_ir = _comparison_ir(master) if as_of else master.ir
        att_supps = getattr(getattr(master, "ctx", None), "attachment_supplements", ())
        print(format_ir_tree(replay_ir))
        for supp in att_supps:
            print(f"\n[Attachment: {supp.pdf_name}]")
            print(format_ir_tree(supp.ir))
        return

    # Filter to address
    addr_filter = _parse_address(address)
    print(f"Statute : {sid}")
    print("Stage   : APPLY (full replay)")
    print(f"Address : {address}")
    print()

    if addr_filter is None:
        print("ERROR: could not parse address", file=sys.stderr)
        return

    kind, num = addr_filter
    # Search the materialized PIT tree by label when --as-of is supplied.
    from lawvm.core.ir_helpers import irnode_to_text
    from lawvm.core import tree_ops as _tops
    replay_ir = _comparison_ir(master) if as_of else master.ir
    found_path = _tops.find(replay_ir, kind, num)
    found = _tops.resolve(replay_ir, found_path) if found_path else None

    if found is not None:
        print(irnode_to_text(found))
        return

    # Fallback: search by eId in lxml base (may be stale for replayed)
    body = master.tree.find(".//{*}body")
    search_root = body if body is not None else master.tree
    for sec in search_root.xpath(f".//*[local-name()='{kind}']"):
        num_el = sec.find("{*}num")
        if num_el is None: num_el = sec.find("num")
        if num_el is not None:
            sec_num = re.sub(r'\s+', '', (num_el.text or "")).lower()
            target = re.sub(r'\s+', '', num).lower()
            if sec_num == target or sec_num == target + "§":
                found = sec
                break

    if found is None:
        print(f"(provision {address} not found in replay output)")
        return

    text = etree.tostring(found, method="text", encoding="unicode").strip()
    print(text)


def dump_apply(
    sid: str,
    address: Optional[str] = None,
    stop_before: str = "",
    as_of: str = "",
) -> None:
    _dump_apply(sid, address, stop_before=stop_before, as_of=as_of)


# Open-ended materialization horizon used by replay; selecting the governing
# version at this date reproduces the fully-replayed (latest) text-state.
_DUMP_DEFAULT_AS_OF = "9999-12-31"


def _dump_apply_json(
    sid: str,
    address: Optional[str],
    *,
    stop_before: str = "",
    as_of: str = "",
) -> None:
    """Emit a machine-readable lawvm.dump.v1 document for the apply read."""
    import json as _json

    from lawvm.tools.provision_state import build_statute_dump_response

    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=sid, stop_before=stop_before, quiet=True),
    )
    payload = build_statute_dump_response(
        timelines=master.timelines,
        statute_id=sid,
        jurisdiction="fi",
        as_of=as_of or _DUMP_DEFAULT_AS_OF,
        title=getattr(master, "title", "") or "",
        address_filter=address,
        flags={
            "after": "apply",
            "before": stop_before or None,
            "address": address,
            "as_of_defaulted": not as_of,
        },
    )
    print(_json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def _dump_apply_hashes(
    sid: str,
    address: Optional[str],
    *,
    stop_before: str = "",
    as_of: str = "",
) -> None:
    """Print the human apply read with a short per-section content_hash appended."""
    from lawvm.tools.provision_state import build_statute_dump_response

    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=sid, stop_before=stop_before, quiet=True),
    )
    payload = build_statute_dump_response(
        timelines=master.timelines,
        statute_id=sid,
        jurisdiction="fi",
        as_of=as_of or _DUMP_DEFAULT_AS_OF,
        title=getattr(master, "title", "") or "",
        address_filter=address,
    )
    print(f"Statute: {sid}")
    print("Stage  : APPLY (full replay, per-section content hashes)")
    if address:
        print(f"Address: {address}")
    print()
    for section in payload["sections"]:
        addr_text = section["address"]["text"]
        short = section["content_hash"][:12] if section["content_hash"] else "(empty)"
        heading = section.get("heading") or ""
        suffix = f"  {heading}" if heading else ""
        print(f"{short}  {addr_text}{suffix}")
    print()
    print(f"Sections: {payload['section_count']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _print_unsupported_uk_dump_stage(stage: str) -> None:
    if stage in {"extract", "normalize"}:
        next_step = (
            "use `lawvm uk-effects <statute_id>` to inspect UK effect-feed extraction, "
            "or `lawvm uk-effect <statute_id> <effect-key>` for one effect row."
        )
    elif stage in {"resolve", "apply"}:
        next_step = (
            "use `lawvm uk-replay <statute_id>` for UK replay/materialization output, "
            "or `lawvm dump <statute_id> --after parse --db <farchive>` for source XML."
        )
    else:
        next_step = "use `lawvm dump <statute_id> --after parse --db <farchive>` for source XML."
    print(
        "ERROR: UK `lawvm dump` supports only archive-backed source parse "
        f"(`--after parse`); requested `--after {stage}`. {next_step}",
        file=sys.stderr,
    )


def main(args) -> None:
    after = getattr(args, "after", None)
    source = getattr(args, "source", None)
    address = getattr(args, "address", None)
    sid = args.statute_id
    from lawvm.tools.source_dump import build_uk_source_dump, is_uk_statute_id, _format_text

    jurisdiction = getattr(args, "jurisdiction", "fi")
    is_uk_dump = jurisdiction == "uk" or is_uk_statute_id(sid)
    want_json = bool(getattr(args, "json", False))
    want_hashes = bool(getattr(args, "hashes", False))
    as_of = getattr(args, "as_of", None) or ""

    if (want_json or want_hashes) and (is_uk_dump or after in ("parse", "extract", "normalize")):
        flag = "--json" if want_json else "--hashes"
        print(
            f"ERROR: `lawvm dump {flag}` is supported only for the Finnish apply "
            "(full replay) read; it is not available for UK statutes or for "
            "`--after parse/extract/normalize`.",
            file=sys.stderr,
        )
        sys.exit(2)

    if (want_json or want_hashes) and not (is_uk_dump or after in ("parse", "extract", "normalize")):
        stop_before = getattr(args, "before", "") or ""
        if want_json:
            _dump_apply_json(sid, address, stop_before=stop_before, as_of=as_of)
        else:
            _dump_apply_hashes(sid, address, stop_before=stop_before, as_of=as_of)
        return

    if after == "parse" or (after is None and is_uk_dump):
        if is_uk_dump:
            bundle = build_uk_source_dump(sid, address, db_path=getattr(args, "db", None))
            print(_format_text(bundle))
        else:
            dump_parse(sid, address)
    elif after in ("extract", "normalize"):
        if is_uk_dump:
            _print_unsupported_uk_dump_stage(after)
            sys.exit(2)
        if not source:
            print("ERROR: --after extract/normalize requires --source <amendment_id>",
                  file=sys.stderr)
            sys.exit(1)
        dump_extract(sid, source, after_normalize=(after == "normalize"), address=address)
    else:
        if is_uk_dump:
            _print_unsupported_uk_dump_stage(after or "apply")
            sys.exit(2)
        # Default ("apply" or no --after): full replay
        stop_before = getattr(args, "before", "") or ""
        dump_apply(sid, address, stop_before=stop_before, as_of=as_of)
