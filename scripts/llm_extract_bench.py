#!/usr/bin/env python3
"""Benchmark LLM-based op extraction against PEG and oracle.

Samples statutes stratified by PEG error rate, extracts ops via local LLM,
compares against PEG-extracted ops, and scores both against oracle.

Usage:
    uv run python scripts/llm_extract_bench.py --sample 50
    uv run python scripts/llm_extract_bench.py --statutes 1959/324,2009/953
    uv run python scripts/llm_extract_bench.py --sample 20 --verbose
"""

import argparse
import csv
import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aiohttp
import asyncio

from lawvm.core.ir import IRNode
from lawvm.xml_ingest import xml_to_ir_node
from lawvm.finland.grafter import _fi_label_postprocessor

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

LLM_URL = "http://localhost:8080/v1/chat/completions"

SYSTEM_PROMPT = """Olet Suomen lainsäädännön asiantuntija. Tehtäväsi on tunnistaa muutossäädöksen operaatiot."""

EXTRACT_PROMPT = """Muutossäädös {amendment_id} kohdistuu lakiin/asetukseen {parent_id}.

JOHTOLAUSE:
{johtolause}

MUUTOSLAIN PYKÄLÄT (body): {body_labels}

Mitä operaatioita tämä muutoslaki tekee kohdelakiin?

Koodit (yksi rivi per operaatio):
R N = korvaa N § kokonaan
R N M = korvaa N §:n M momentti
K N = kumotaan N §
K N M = kumotaan N §:n M momentti
I N = lisätään uusi N §
I N M = lisätään N §:ään uusi M momentti
T N "vanha" "uusi" = tekstikorvaus N §:ssä
NONE = ei koske kohdelakia

Ei selityksiä, ei sulkeita, ei muuta tekstiä."""


def _parse_llm_ops(text: str) -> List[Dict[str, Any]]:
    """Parse LLM line-based output into structured ops."""
    ops = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line == "NONE":
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        action = parts[0].upper()
        section = parts[1]
        subsection = parts[2] if len(parts) >= 3 and parts[2].isdigit() else None

        action_map = {"R": "REPLACE", "K": "REPEAL", "I": "INSERT", "T": "TEXT_REPLACE"}
        op_type = action_map.get(action, action)
        ops.append(
            {
                "action": op_type,
                "section": section,
                "subsection": subsection,
                "raw": line,
            }
        )
    return ops


async def _call_llm(
    session: aiohttp.ClientSession,
    prompt: str,
    max_tokens: int = 200,
) -> str:
    """Call local llama.cpp server."""
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with session.post(
            LLM_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[ERROR: {e}]"


async def _extract_ops_llm(
    session: aiohttp.ClientSession,
    amendment_id: str,
    parent_id: str,
    johtolause: str,
    body_labels: List[str],
) -> List[Dict[str, Any]]:
    """Extract ops from one amendment via LLM."""
    prompt = EXTRACT_PROMPT.format(
        amendment_id=amendment_id,
        parent_id=parent_id,
        johtolause=johtolause[:3000],  # truncate very long johtolause
        body_labels=", ".join(body_labels) if body_labels else "(tyhjä)",
    )
    # Budget: base 30 + 5 per expected op (roughly = body sections + some repeals)
    max_tokens = 30 + max(len(body_labels), 3) * 8
    raw = await _call_llm(session, prompt, max_tokens=max_tokens)
    return _parse_llm_ops(raw)


def _extract_amendment_body_sections(xml_bytes: bytes) -> dict[str, IRNode]:
    """Return body section labels from an amendment XML blob."""
    import lxml.etree as etree

    root = etree.fromstring(xml_bytes)
    body = root.find(".//{*}body")
    if body is None:
        return {}
    ir = xml_to_ir_node(body, _fi_label_postprocessor)

    sections: dict[str, IRNode] = {}

    def visit(node: IRNode) -> None:
        for child in node.children:
            if child.kind == "section" and child.label:
                sections[str(child.label)] = child
            visit(child)

    visit(ir)
    return sections


def _get_peg_ops(amendment_id: str, parent_id: str) -> List[Dict[str, Any]]:
    """Extract ops via PEG parser (existing grafter path)."""
    from lawvm.finland.grafter import (
        get_johtolause,
        _normalize_johtolause_verbs,
        _get_corpus_store,
    )
    from lawvm.finland.johtolause import extract_legal_ops

    cs = _get_corpus_store()
    xml = cs.read_source(amendment_id)
    if not xml:
        return []

    johto = get_johtolause(xml)
    if not johto:
        return []
    johto = _normalize_johtolause_verbs(johto)
    legal_ops = extract_legal_ops(johto)
    return [
        {
            "action": op.action.value.upper(),
            "section": str(op.target).split(":")[1] if ":" in str(op.target) else "",
            "raw": f"{op.action.value} {op.target}",
        }
        for op in legal_ops
    ]


def _sample_stratified(n: int) -> List[Tuple[str, float]]:
    """Sample statutes stratified by PEG error rate.

    Returns [(sid, peg_error_rate)] with statutes from each error band.
    """
    # Find latest bench run
    runs_dir = LAWVM_DIR / "data" / "bench_runs"
    candidates = sorted(runs_dir.glob("*.csv"), reverse=True)
    if not candidates:
        print("ERROR: no bench runs found", file=sys.stderr)
        sys.exit(1)

    bench_file = candidates[0]
    print(f"Using bench run: {bench_file.name}")

    with open(bench_file) as f:
        rows = list(csv.DictReader(f))

    scored = []
    for r in rows:
        try:
            sim = float(r["similarity"])
            scored.append((r["statute_id"], 1.0 - sim))
        except (ValueError, KeyError):
            continue

    # Stratify into bands
    bands = [
        ("perfect", 0.0, 0.001),
        ("low_err", 0.001, 0.02),
        ("mid_err", 0.02, 0.10),
        ("high_err", 0.10, 0.50),
        ("very_high", 0.50, 1.01),
    ]

    import random

    random.seed(42)
    per_band = max(1, n // len(bands))
    sample = []
    for name, lo, hi in bands:
        band_items = [(s, e) for s, e in scored if lo <= e < hi]
        k = min(per_band, len(band_items))
        chosen = random.sample(band_items, k) if band_items else []
        sample.extend(chosen)
        print(f"  {name:12s}: {len(band_items):5d} total, sampled {k}")

    # Fill remainder from high-error band
    if len(sample) < n:
        remaining = [(s, e) for s, e in scored if (s, e) not in sample and e >= 0.02]
        extra = random.sample(remaining, min(n - len(sample), len(remaining)))
        sample.extend(extra)

    return sample[:n]


async def _process_statute(
    session: aiohttp.ClientSession,
    sid: str,
    peg_err: float,
    verbose: bool,
) -> Dict[str, Any]:
    """Process one statute: extract ops via LLM for each amendment, score."""
    from lawvm.finland.grafter import (
        _resolve_applicable_amendment_records,
        get_johtolause,
        _get_corpus_store,
    )

    cs = _get_corpus_store()

    # Get amendment chain
    records, _, _ = _resolve_applicable_amendment_records(sid, "finlex_oracle")

    amendment_results = []
    for rec in records:
        amendment_id = str(rec["statute_id"])
        xml = cs.read_source(amendment_id)
        if not xml:
            continue

        johto = get_johtolause(xml) or ""
        body_sections = _extract_amendment_body_sections(xml)
        body_labels = sorted(body_sections.keys())

        # LLM extraction
        llm_ops = await _extract_ops_llm(session, amendment_id, sid, johto, body_labels)

        # PEG extraction (for comparison)
        peg_ops = _get_peg_ops(amendment_id, sid)

        amendment_results.append(
            {
                "amendment_id": amendment_id,
                "llm_ops": llm_ops,
                "peg_ops": peg_ops,
                "body_labels": body_labels,
                "johto_len": len(johto),
                "agree": _ops_agree(llm_ops, peg_ops),
            }
        )

    # Score body_driven + LLM-kumotaan hybrid replay
    # For now just score body-driven (we already have those numbers)
    # TODO: apply LLM ops and score

    n_agree = sum(1 for r in amendment_results if r["agree"])
    n_total = len(amendment_results)

    return {
        "sid": sid,
        "peg_err": peg_err,
        "n_amendments": n_total,
        "n_agree": n_agree,
        "amendments": amendment_results,
    }


def _normalize_op_key(action: str, section: str) -> Tuple[str, str]:
    """Normalize op to section-level for comparison."""
    # Strip /subsection suffix from PEG ops
    sec = section.split("/")[0] if "/" in section else section
    # Normalize action: TEXT_REPLACE → REPLACE for comparison
    act = "REPLACE" if action in ("TEXT_REPLACE", "REPLACE") else action
    return (act, sec)


def _ops_agree(llm_ops: List[Dict], peg_ops: List[Dict]) -> bool:
    """Check if LLM and PEG target the same sections (ignoring subsection granularity)."""
    llm_secs = set()
    for op in llm_ops:
        if op["action"].startswith("[ERROR"):
            continue
        llm_secs.add(_normalize_op_key(op["action"], op["section"]))

    peg_secs = set()
    for op in peg_ops:
        sec = op.get("section", "")
        if sec:
            peg_secs.add(_normalize_op_key(op["action"], sec))

    return llm_secs == peg_secs


def _ops_diff(llm_ops: List[Dict], peg_ops: List[Dict]) -> Tuple[set, set]:
    """Return (llm_only, peg_only) section-level op sets."""
    llm_secs = {_normalize_op_key(o["action"], o["section"]) for o in llm_ops if not o["action"].startswith("[ERROR")}
    peg_secs = {_normalize_op_key(o["action"], o.get("section", "")) for o in peg_ops if o.get("section")}
    return llm_secs - peg_secs, peg_secs - llm_secs


async def _run(args):
    if args.statutes:
        sample = [(s.strip(), 0.0) for s in args.statutes.split(",")]
    else:
        sample = _sample_stratified(args.sample)

    print(f"\nProcessing {len(sample)} statutes...")

    async with aiohttp.ClientSession() as session:
        # Test connection
        test = await _call_llm(session, "Sano 'ok'.", max_tokens=5)
        if test.startswith("[ERROR"):
            print(f"LLM connection failed: {test}", file=sys.stderr)
            return

        results = []
        for i, (sid, peg_err) in enumerate(sample, 1):
            # Suppress grafter print output
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                result = await _process_statute(session, sid, peg_err, args.verbose)
            finally:
                sys.stdout = old_stdout

            results.append(result)
            agree_pct = f"{result['n_agree']}/{result['n_amendments']}" if result["n_amendments"] else "-"
            print(
                f"[{i}/{len(sample)}] {sid:12s} peg_err={peg_err:.2%} amend={result['n_amendments']} agree={agree_pct}"
            )

            if args.verbose and result["amendments"]:
                for ar in result["amendments"]:
                    if not ar["agree"]:
                        llm_only, peg_only = _ops_diff(ar["llm_ops"], ar["peg_ops"])
                        parts = []
                        if llm_only:
                            parts.append(f"LLM_ONLY={sorted(llm_only)}")
                        if peg_only:
                            parts.append(f"PEG_ONLY={sorted(peg_only)}")
                        if parts:
                            print(f"    {ar['amendment_id']}: {' | '.join(parts)}")

    # Summary
    n_total_amend = sum(r["n_amendments"] for r in results)
    n_agree_amend = sum(r["n_agree"] for r in results)
    agree_rate = n_agree_amend / n_total_amend if n_total_amend else 0

    print(f"\n{'=' * 60}")
    print(f"Statutes     : {len(results)}")
    print(f"Amendments   : {n_total_amend}")
    print(f"LLM=PEG agree: {n_agree_amend}/{n_total_amend} ({agree_rate:.1%})")
    print(f"Disagree     : {n_total_amend - n_agree_amend}")

    # Disagreement analysis by error band
    for band_name, lo, hi in [("perfect", 0, 0.001), ("low", 0.001, 0.02), ("mid", 0.02, 0.10), ("high", 0.10, 1.01)]:
        band_results = [r for r in results if lo <= r["peg_err"] < hi]
        if not band_results:
            continue
        ba = sum(r["n_agree"] for r in band_results)
        bt = sum(r["n_amendments"] for r in band_results)
        print(f"  {band_name:8s}: {ba}/{bt} agree ({ba / bt:.1%})" if bt else f"  {band_name:8s}: -")


def main():
    parser = argparse.ArgumentParser(description="LLM op extraction benchmark")
    parser.add_argument("--sample", type=int, default=30, help="statutes to sample")
    parser.add_argument("--statutes", default="", help="comma-separated statute IDs")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
