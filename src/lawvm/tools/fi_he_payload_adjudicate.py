"""``lawvm fi-he-payload-adjudicate`` — T1-adjudicate the HE payload_mismatch tail.

Phase 2's op-structure is solved; the surviving ``payload_mismatch`` residual is what the
objective says to ADJUDICATE (defect→fix / inert→fold / witness_disagreement→record). This
driver re-derives every compared HE's matched-op proposed bodies (XML oracle vs PDF bill
text), takes the pairs that still differ after the inert-encoding quotient, and asks the T1
local-LLM adjudicator (:mod:`lawvm.finland.he_payload_adjudicator`) to type each one. It folds
the verdict distribution and reports how many divergences close as first-class
witness_disagreement (``oracle_artifact`` / ``genuine_difference`` — accounted/done), how many
are ``reader_defect`` (route to a higher-fidelity read), and how many are ``equivalent`` (a
discovery-loop signal to graduate a new inert fold). The LLM is OPTIONAL: with no backend the
driver reports the residual queue and exits cleanly (never a hard failure in CI).
"""
from __future__ import annotations

import argparse
import collections
from typing import Callable, Optional

from lawvm.finland.he_payload_adjudicator import (
    DivergenceVerdict,
    adjudicate_payload_divergence,
)
from lawvm.finland.op_equivalence import text_equivalence
from lawvm.tools.fi_he_ir_compare import (
    _AKN_PATH_PREFIX,
    _pdf_proposed_bodies,
    _xml_proposed_bodies,
    he_pdf_reading_text,
)
from lawvm.tools.fi_he_ir_corpus import _DEFAULT_FARCHIVE, enumerate_he_units


def _residual_body_pairs(
    farchive: str, units, *, max_pages: int
) -> "list[tuple[str, str, str]]":
    """(he_id, xml_body, pdf_body) for every matched-label body still differing post-quotient."""
    from farchive import Farchive

    pairs: list[tuple[str, str, str]] = []
    for unit in units:
        base = f"{_AKN_PATH_PREFIX}{unit.he_year}/{unit.he_number}/fin@/"
        fa = Farchive(farchive)
        try:
            xml_bytes = fa.get(base + "main.xml")
        finally:
            fa.close()
        if not xml_bytes:
            continue
        try:
            reading = he_pdf_reading_text(farchive, base + "main.pdf", max_pages=max_pages)
        except Exception:
            continue
        xb = _xml_proposed_bodies(xml_bytes)
        pb = _pdf_proposed_bodies(reading)
        for label, xtext in xb.items():
            ptext = pb.get(label)
            if ptext and not text_equivalence(xtext, ptext).equal:
                pairs.append((unit.he_id, xtext, ptext))
    return pairs


def _make_chat_fn(base_url: Optional[str]) -> "Optional[Callable[[str, str], str]]":
    """Wire the injected chat transport to the local LLM, or None if unavailable."""
    from lawvm.ingest.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator

    adj = LlmWorkflowAdjudicator(base_url=base_url) if base_url else LlmWorkflowAdjudicator()
    if not adj.is_available():
        return None

    def chat_fn(system: str, user: str) -> str:
        return adj._chat(system, user, region_locator="he_payload_divergence")

    return chat_fn


def main(args: argparse.Namespace) -> None:
    farchive = args.farchive or _DEFAULT_FARCHIVE
    units = enumerate_he_units(farchive, sample=args.sample, seed=args.seed)
    if args.limit:
        units = units[: args.limit]
    pairs = _residual_body_pairs(farchive, units, max_pages=args.max_pages)
    print(f"# fi-he-payload-adjudicate — residual body divergences: {len(pairs)}")

    chat_fn = _make_chat_fn(args.base_url)
    if chat_fn is None:
        print("# local LLM adjudicator unavailable (no backend) — residual queue only, not typed")
        return

    verdicts: collections.Counter[str] = collections.Counter()
    witness_done = 0
    for _he_id, xtext, ptext in pairs:
        try:
            v = adjudicate_payload_divergence(xtext, ptext, chat_fn=chat_fn)
        except Exception as exc:  # transport error is a run outcome, never a crash
            verdicts[f"error:{type(exc).__name__}"] += 1
            continue
        verdicts[str(v)] += 1
        if v.is_witness_disagreement:
            witness_done += 1

    print("## VERDICT DISTRIBUTION")
    for name in DivergenceVerdict:
        print(f"  {name.value:20} {verdicts.get(str(name), 0)}")
    for k, n in sorted(verdicts.items()):
        if k.startswith("error:"):
            print(f"  {k:20} {n}")
    print(
        f"## witness_disagreement (accounted/done): {witness_done} / {len(pairs)} "
        "(oracle_artifact + genuine_difference)"
    )
