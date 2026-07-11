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

Every verdict passes through the determinism-firewall cache
(:mod:`lawvm.finland.he_payload_verdict_store`): content-addressed by the two bodies + the
model/prompt fingerprint, so a re-run is a cache HIT and the verdicts never re-flip. The
per-divergence verdicts (he_id, section label, verdict, both body snippets) are ALSO streamed to
a run JSONL (``--verdicts-jsonl``) for characterization/spot-checking.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from typing import Callable, Optional

from lawvm.finland.he_payload_adjudicator import DivergenceVerdict
from lawvm.finland.he_payload_verdict_store import (
    PayloadVerdictStore,
    adjudicate_payload_divergence_cached,
)
from lawvm.finland.op_equivalence import text_equivalence
from lawvm.tools.fi_he_ir_compare import (
    _AKN_PATH_PREFIX,
    _pdf_proposed_bodies,
    _xml_proposed_bodies,
    he_pdf_reading_text,
)
from lawvm.tools.fi_he_ir_corpus import _DEFAULT_FARCHIVE, enumerate_he_units

#: A bounded snippet of each body streamed to the run JSONL (for characterization/spot-check).
_JSONL_SNIPPET = 400


def _residual_body_pairs(
    farchive: str, units, *, max_pages: int
) -> "list[tuple[str, int, tuple[str, str], str, str]]":
    """(he_id, he_year, label, xml_body, pdf_body) for each matched body still differing post-quotient.

    ``label`` is the ``(statute-id, section label)`` key that :func:`_xml_proposed_bodies` /
    :func:`_pdf_proposed_bodies` use to align a matched body across the two witnesses."""
    from farchive import Farchive

    pairs: list[tuple[str, int, tuple[str, str], str, str]] = []
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
                pairs.append((unit.he_id, unit.he_year, label, xtext, ptext))
    return pairs


def _make_chat_fn(
    base_url: Optional[str],
) -> "Optional[tuple[Callable[[str, str], str], str]]":
    """Wire the injected chat transport to the local LLM, or None if unavailable.

    Returns ``(chat_fn, adjudicator_id)`` where ``adjudicator_id`` embeds the RESOLVED served
    model id, so the firewall cache key reflects a model upgrade (not the ``qwen`` placeholder).
    """
    from lawvm.ingest.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator

    adj = LlmWorkflowAdjudicator(base_url=base_url) if base_url else LlmWorkflowAdjudicator()
    if not adj.is_available():
        return None

    adjudicator_id = f"llm_workflow:{adj._resolve_model()}"

    def chat_fn(system: str, user: str) -> str:
        return adj._chat(system, user, region_locator="he_payload_divergence")

    return chat_fn, adjudicator_id


def _default_jsonl_path() -> Optional[str]:
    """Default run-JSONL sink: ``$CLAUDE_JOB_DIR/tmp/he_payload_verdicts.jsonl`` if set."""
    job_dir = os.environ.get("CLAUDE_JOB_DIR")
    if not job_dir:
        return None
    return os.path.join(job_dir, "tmp", "he_payload_verdicts.jsonl")


def main(args: argparse.Namespace) -> None:
    farchive = args.farchive or _DEFAULT_FARCHIVE
    units = enumerate_he_units(farchive, sample=args.sample, seed=args.seed)
    if args.limit:
        units = units[: args.limit]
    pairs = _residual_body_pairs(farchive, units, max_pages=args.max_pages)
    print(f"# fi-he-payload-adjudicate — residual body divergences: {len(pairs)}")

    wired = _make_chat_fn(args.base_url)
    if wired is None:
        print("# local LLM adjudicator unavailable (no backend) — residual queue only, not typed")
        return
    chat_fn, adjudicator_id = wired
    print(f"# adjudicator: {adjudicator_id} (firewall cache: {args.verdict_cache})")

    jsonl_path = args.verdicts_jsonl or _default_jsonl_path()
    jsonl_fh = None
    if jsonl_path:
        os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
        jsonl_fh = open(jsonl_path, "w", encoding="utf-8")

    store = PayloadVerdictStore(args.verdict_cache)
    verdicts: collections.Counter[str] = collections.Counter()
    witness_done = 0
    cache_hits = 0
    try:
        for he_id, he_year, label, xtext, ptext in pairs:
            try:
                out = adjudicate_payload_divergence_cached(
                    xtext, ptext, chat_fn=chat_fn, adjudicator_id=adjudicator_id, store=store
                )
            except Exception as exc:  # transport error is a run outcome, never a crash
                verdicts[f"error:{type(exc).__name__}"] += 1
                continue
            verdicts[str(out.verdict)] += 1
            cache_hits += int(out.cache_hit)
            if out.verdict.is_witness_disagreement:
                witness_done += 1
            if jsonl_fh is not None:
                jsonl_fh.write(
                    json.dumps(
                        {
                            "he_id": he_id,
                            "he_year": he_year,
                            "label": label,
                            "verdict": out.verdict.value,
                            "is_witness_disagreement": out.verdict.is_witness_disagreement,
                            "cache_hit": out.cache_hit,
                            "xml_snippet": xtext[:_JSONL_SNIPPET],
                            "pdf_snippet": ptext[:_JSONL_SNIPPET],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    finally:
        store.close()
        if jsonl_fh is not None:
            jsonl_fh.close()

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
    print(f"## cache hits: {cache_hits} / {len(pairs)}")
    if jsonl_path:
        print(f"## per-divergence verdicts → {jsonl_path}")
