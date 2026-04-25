#!/usr/bin/env python3
"""Independent LLM-based verification of statute amendment replay.

For each amendment applied to a canary statute, asks an independent LLM
(Gemini CLI) to derive the post-amendment provision text from first
principles. Compares the LLM's derivation against replay output and
oracle to produce three-way verification.

This is the TRUE Tier 1 verification: independent derivation, not
cross-checking two potentially-correlated sources.

Usage:
    # Verify one canary statute:
    uv run python scripts/verify_gold_independent.py 2002/738

    # Verify all canary statutes:
    uv run python scripts/verify_gold_independent.py --all

    # Dry run (show what would be verified, no LLM calls):
    uv run python scripts/verify_gold_independent.py 2002/738 --dry-run

    # Use Claude CLI instead of Gemini:
    uv run python scripts/verify_gold_independent.py 2002/738 --backend claude
"""

import argparse
import asyncio
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import farchive as _farchive
from lxml import etree

from lawvm.corpus_store import ArchiveCorpusStore, statute_url
from lawvm.finland.consolidated_artifacts import (
    build_versioned_consolidated_main_glob,
    parse_versioned_consolidated_main_locator,
)

NS = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"

FARCHIVE_PATH = Path("data/finlex.farchive")
AMENDMENT_PARENTS_CSV = Path("data/finland/amendment_parents.csv")
GOLD_DIR = Path("data/gold")
SCRATCH_DIR = Path(".tmp/migration/verify")

# Canary statutes (from MIGRATION_SPEC.md)
CANARY_STATUTES = [
    "2009/953",   # 3 amends — Laki Rikosseuraamuslaitoksesta
    "1992/1612",  # 2 amends — Ulkomaalaisten yritysostot
    "2007/446",   # 19 amends — Biopolttoaineet
    "2001/1383",  # 19 amends — Työterveyshuoltolaki
    "2002/738",   # 18 amends — Työturvallisuuslaki
    "1996/1124",  # 20 amends — Ennakkoperintäasetus
    "2000/29",    # 18 amends — Eläinlääkärinammatti
    "2009/1599",  # 17 amends — Asunto-osakeyhtiölaki
]

MAX_CONCURRENT = 10

# OpenRouter API
OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "deepseek/deepseek-chat-v3-0324"  # $0.26/M in, $1.10/M out, Finnish OK

import aiohttp
import os

# Output delimiters — LLM is instructed to wrap its answer in these.
# Parser extracts content between them, tolerating any leading/trailing junk.
RESULT_START = "<<VERIFIED_RESULT>>"
RESULT_END = "<</VERIFIED_RESULT>>"
TARGETS_START = "<<TARGET_LIST>>"
TARGETS_END = "<</TARGET_LIST>>"

# Raw output log — every LLM call is recorded here for audit
RAW_LOG_DIR = Path(".tmp/migration/verify/raw_logs")


def _joined_text(parts: Iterable[object], *, sep: str = "") -> str:
    return sep.join(str(part) for part in parts)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_amendment_parents() -> Dict[str, List[str]]:
    parents: Dict[str, List[str]] = {}
    with open(AMENDMENT_PARENTS_CSV) as f:
        for r in csv.reader(f):
            if len(r) >= 2 and r[0] != "amendment_id":
                parents.setdefault(r[1], []).append(r[0])
    return parents


def load_farchive_source_index(archive: object) -> Dict[str, str]:
    """Build statute_id → farchive locator mapping for source (sd/) XMLs."""
    return {sid: statute_url(sid) for sid in ArchiveCorpusStore(archive).list_statute_ids()}  # type: ignore[arg-type]


def load_farchive_oracle_index(archive: object) -> Dict[str, str]:
    """Build statute_id → best versioned oracle farchive locator mapping."""
    best: Dict[str, tuple] = {}
    for url in archive.locators(build_versioned_consolidated_main_glob()):  # type: ignore[attr-defined]
        parts = parse_versioned_consolidated_main_locator(url)
        if parts is None:
            continue
        pit_int = int(parts.version) if parts.version.isdigit() else 0
        prev = best.get(parts.sid)
        if prev is None or pit_int > prev[0]:
            best[parts.sid] = (pit_int, url)
    return {sid: v[1] for sid, v in best.items()}


def normalize_text(t: str) -> str:
    return " ".join(t.split()).strip()


def alpha_only(t: str) -> str:
    return re.sub(r"[^a-zäöåA-ZÄÖÅ0-9]", "", t.lower())


# ---------------------------------------------------------------------------
# Provision extraction
# ---------------------------------------------------------------------------

def extract_provisions(tree_el: etree._Element) -> Dict[str, str]:
    """Extract per-provision normalized text keyed by LegalAddress path."""
    provisions: Dict[str, str] = {}
    chapters = tree_el.findall(f".//{NS}chapter")
    if chapters:
        for ch in chapters:
            ch_num_el = ch.find(f"{NS}num")
            ch_num = (
                normalize_text(_joined_text(ch_num_el.itertext()))
                .replace(" luku", "")
                .strip()
                if ch_num_el is not None
                else ""
            )
            for sec in ch.findall(f"{NS}section"):
                sec_num_el = sec.find(f"{NS}num")
                sec_raw = (
                    normalize_text(_joined_text(sec_num_el.itertext()))
                    if sec_num_el is not None
                    else ""
                )
                sec_num = re.sub(r"\s*§.*", "", sec_raw).strip()
                key = f"chapter:{ch_num}/section:{sec_num}"
                if key not in provisions:
                    provisions[key] = normalize_text(_joined_text(sec.itertext(), sep=" "))
    else:
        for sec in tree_el.findall(f".//{NS}section"):
            sec_num_el = sec.find(f"{NS}num")
            sec_raw = (
                normalize_text(_joined_text(sec_num_el.itertext()))
                if sec_num_el is not None
                else ""
            )
            sec_num = re.sub(r"\s*§.*", "", sec_raw).strip()
            key = f"section:{sec_num}"
            if key not in provisions:
                provisions[key] = normalize_text(_joined_text(sec.itertext(), sep=" "))
    return provisions


def extract_johtolause_text(tree_el: etree._Element) -> str:
    """Extract the johtolause (enacting clause) from an amendment act.

    The johtolause lives in <preamble>/<formula>, NOT in <body>.
    It says things like "muutetaan ... 4 §:n 1 momentti sekä lisätään
    12 §:ään uusi 4 momentti seuraavasti:".
    """
    # Try <preamble> first (contains the full enacting clause)
    preamble = tree_el.find(f".//{NS}preamble")
    if preamble is not None:
        text = normalize_text(_joined_text(preamble.itertext(), sep=" "))
        if text:
            return text

    # Fallback: <formula> (sometimes used instead of preamble)
    formula = tree_el.find(f".//{NS}formula")
    if formula is not None:
        text = normalize_text(_joined_text(formula.itertext(), sep=" "))
        if text:
            return text

    # Last resort: search body for amendment keywords (unreliable —
    # may match section content rather than the actual enacting clause)
    body = tree_el.find(f".//{NS}body")
    if body is None:
        return ""
    text = normalize_text(_joined_text(body.itertext(), sep=" "))
    for kw in ["muutetaan", "kumotaan", "lisätään", "siirretään"]:
        idx = text.lower().find(kw)
        if idx >= 0:
            end = text.find("seuraavasti", idx)
            if end >= 0:
                return text[idx : end + len("seuraavasti")]
            return text[idx : idx + 500]
    return text[:500]


def extract_amendment_sections(tree_el: etree._Element) -> Dict[str, str]:
    """Extract amendment section content keyed by section number."""
    sections: Dict[str, str] = {}
    for sec in tree_el.findall(f".//{NS}section"):
        num_el = sec.find(f"{NS}num")
        if num_el is not None:
            num_raw = normalize_text(_joined_text(num_el.itertext()))
            num = re.sub(r"\s*§.*", "", num_raw).strip()
            sections[num] = normalize_text(_joined_text(sec.itertext(), sep=" "))
    return sections


# ---------------------------------------------------------------------------
# LLM verification call
# ---------------------------------------------------------------------------

TARGET_IDENTIFICATION_PROMPT = """\
You are analyzing a Finnish statute amendment act.
Your task: identify which provisions of statute {parent_sid} are affected.

IMPORTANT: This amendment act may reference MULTIPLE different statutes.
You must ONLY list operations that target statute {parent_sid}.
Ignore all operations targeting other statutes.

<<AMENDMENT_FULL_TEXT>>
{amendment_text}
<</AMENDMENT_FULL_TEXT>>

Read the enacting clause (johtolause) carefully. It lists operations like:
- "muutetaan ... lain ({parent_sid}) N §" (replace section N)
- "kumotaan ... lain ({parent_sid}) N §" (repeal section N)
- "lisätään ... lakiin ({parent_sid}) uusi N a §" (insert new section Na)

List ONLY operations targeting statute {parent_sid}, one per line:
OPERATION SECTION [SUBSECTION] [ITEM]

Examples:
REPLACE 12
REPEAL 5a
INSERT 7a
REPLACE 3 2

If NO operations target statute {parent_sid}, write NONE.
Wrap your answer in the exact delimiters shown. No text outside the delimiters.

{targets_start}
(your list here)
{targets_end}"""


PROMPT_TEMPLATE = """\
You are independently verifying a Finnish statute amendment.
Your task: determine what section {sec_num} § looks like AFTER this
amendment is applied.

<<CURRENT_FULL_TEXT_OF_SECTION_{sec_num}>>
{current_text}
<</CURRENT_FULL_TEXT_OF_SECTION_{sec_num}>>

<<ENACTING_CLAUSE>>
{johtolause}
<</ENACTING_CLAUSE>>

The enacting clause tells you EXACTLY what is being changed in section
{sec_num} §. Read it carefully. Key patterns:
- "{sec_num} §" alone = WHOLE section replaced
- "{sec_num} §:n N momentti" = ONLY subsection N replaced, rest kept
- "{sec_num} §:n N momentin M kohta" = ONLY item M in subsection N
- "kumotaan {sec_num} §" = section repealed
- "lisätään {sec_num} §:ään" = content added, existing kept

<<AMENDMENT_BODY_FOR_SECTION_{sec_num}>>
{amendment_content}
<</AMENDMENT_BODY_FOR_SECTION_{sec_num}>>

CRITICAL RULES:
1. If the enacting clause specifies a SUBSECTION ("N momentti"), the
   amendment body above is ONLY the new text of that subsection.
   You MUST keep all other subsections from the current text unchanged.
   Example: if "3 §:n 1 momentin" is replaced, output = new subsection 1
   + unchanged subsections 2, 3, 4, ... from the current text.
2. If the enacting clause specifies the WHOLE section (no "momentti"),
   the amendment body replaces everything. Output = the amendment body.
3. For REPEAL: output "[REPEALED]".
4. For INSERT: add the new content at the position described, keep
   everything else from the current text.
5. Output the COMPLETE section {sec_num} § after the amendment — heading,
   all subsections, all items. Nothing omitted.

Wrap your answer in the exact delimiters below. Nothing outside them.

{result_start}
(complete resulting text of section {sec_num} § here)
{result_end}"""


_call_counter = 0
_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


def _extract_delimited(raw: str, start_delim: str, end_delim: str) -> str:
    """Extract content between delimiters, tolerating leading/trailing junk.

    Returns the content between start_delim and end_delim, stripped.
    If delimiters are not found, returns the full raw output (best effort).
    """
    s_idx = raw.find(start_delim)
    if s_idx < 0:
        return raw.strip()  # no delimiter found — return raw as fallback
    content_start = s_idx + len(start_delim)
    e_idx = raw.find(end_delim, content_start)
    if e_idx < 0:
        return raw[content_start:].strip()  # no end delimiter — take rest
    return raw[content_start:e_idx].strip()


async def call_llm(
    prompt: str,
    model: str = "",
    timeout: int = 120,
    call_label: str = "",
) -> Tuple[str, str]:
    """Call LLM via OpenRouter API.

    Returns (raw_response_content, raw_response_content).
    Raw output is always saved to disk for audit.
    """
    global _call_counter
    _call_counter += 1
    call_id = f"{_call_counter:04d}_{call_label}" if call_label else f"{_call_counter:04d}"

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raw = "[ERROR: OPENROUTER_API_KEY not set]"
        _save_raw_log(call_id, prompt, raw, "")
        return raw, raw

    use_model = model or OPENROUTER_MODEL
    session = await _get_session()

    try:
        async with session.post(
            OPENROUTER_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": use_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
                "temperature": 0.0,
            },
            timeout=aiohttp.ClientTimeout(total=max(timeout, 180)),
        ) as resp:
            data = await resp.json()

            if resp.status != 200:
                raw = f"[ERROR: HTTP {resp.status}] {json.dumps(data)[:500]}"
                _save_raw_log(call_id, prompt, raw, "")
                return raw, raw

            raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})

            # Save full response for audit
            _save_raw_log(
                call_id, prompt, raw_content,
                f"model={use_model} tokens_in={usage.get('prompt_tokens','')} "
                f"tokens_out={usage.get('completion_tokens','')}",
            )
            return raw_content, raw_content

    except asyncio.TimeoutError:
        raw = "[TIMEOUT]"
        _save_raw_log(call_id, prompt, raw, "(timeout)")
        return raw, raw
    except Exception as e:
        raw = f"[ERROR: {type(e).__name__}: {e}]"
        _save_raw_log(call_id, prompt, raw, str(e))
        return raw, raw


def _save_raw_log(call_id: str, prompt: str, response: str, meta: str):
    """Save raw LLM I/O to disk for audit trail."""
    RAW_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RAW_LOG_DIR / f"{call_id}.log"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"=== PROMPT ===\n{prompt}\n\n")
        f.write(f"=== RESPONSE ===\n{response}\n\n")
        f.write(f"=== META ===\n{meta}\n")


# ---------------------------------------------------------------------------
# Verification result types
# ---------------------------------------------------------------------------

@dataclass
class ProvisionVerification:
    address: str
    amendment_id: str
    diagnosis: str  # TRIPLE_CONFIRMED, REPLAY_BUG, ORACLE_BUG, ALL_DISAGREE, LLM_UNCERTAIN
    replay_text: str
    oracle_text: str
    llm_text: str
    replay_match_llm: bool
    oracle_match_llm: bool
    note: str = ""


@dataclass
class StatuteVerification:
    statute_id: str
    title: str
    amendments_processed: int
    provisions_verified: int
    results: List[ProvisionVerification] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self.results:
            counts[r.diagnosis] = counts.get(r.diagnosis, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Main verification loop
# ---------------------------------------------------------------------------

async def verify_statute(
    statute_id: str,
    dry_run: bool = False,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> StatuteVerification:
    """Run independent verification for one statute."""

    parents = load_amendment_parents()
    amendments = sorted(parents.get(statute_id, []))

    archive = _farchive.Farchive(str(FARCHIVE_PATH), readonly=True)
    source_index = load_farchive_source_index(archive)
    oracle_index = load_farchive_oracle_index(archive)

    # Load base statute
    base_url = source_index.get(statute_id)
    if not base_url:
        archive.close()
        print(f"  ERROR: {statute_id} not in farchive", file=sys.stderr)
        return StatuteVerification(statute_id=statute_id, title="?",
                                   amendments_processed=0, provisions_verified=0)

    base_xml = archive.get(base_url)
    base_tree = etree.fromstring(base_xml)
    title_el = base_tree.find(f".//{NS}docTitle")
    title = normalize_text(_joined_text(title_el.itertext())) if title_el is not None else "?"

    print(f"\n{'='*70}")
    print(f"Verifying: {statute_id} — {title}")
    print(f"Amendments: {len(amendments)}")
    print(f"{'='*70}")

    # Load latest oracle
    oracle_provs: Dict[str, str] = {}
    oracle_url = oracle_index.get(statute_id)
    if oracle_url:
        oracle_xml = archive.get(oracle_url)
        if oracle_xml:
            oracle_tree = etree.fromstring(oracle_xml)
            oracle_provs = extract_provisions(oracle_tree)

    # Load replay output (import here to avoid top-level async issues)
    from lawvm.finland.grafter import replay_xml
    master = await replay_xml(statute_id)
    replay_provs = extract_provisions(master.tree)

    # Build cumulative consolidated state from base
    consolidated = extract_provisions(base_tree)
    llm_touched_keys: set = set()  # track which provisions LLM actually derived
    results: List[ProvisionVerification] = []
    sem = semaphore or asyncio.Semaphore(MAX_CONCURRENT)

    for i, amend_id in enumerate(amendments):
        amend_url = source_index.get(amend_id)
        if not amend_url:
            print(f"  [{i+1}/{len(amendments)}] {amend_id}: not in farchive — skipping")
            continue

        amend_xml = archive.get(amend_url)
        amend_tree = etree.fromstring(amend_xml)
        johtolause = extract_johtolause_text(amend_tree)
        amend_sections = extract_amendment_sections(amend_tree)

        if not johtolause or not amend_sections:
            print(f"  [{i+1}/{len(amendments)}] {amend_id}: no johtolause or sections — likely full replacement")
            # Full replacement: update consolidated with amendment's provisions
            amend_provs = extract_provisions(amend_tree)
            for key, text in amend_provs.items():
                consolidated[key] = text
            continue

        print(f"  [{i+1}/{len(amendments)}] {amend_id}: {len(amend_sections)} sections, johtolause={johtolause[:80]}...")

        if dry_run:
            continue

        # PHASE 1: Independent target identification
        # Ask LLM to identify which provisions the amendment targets.
        # Compare against the sections we found in the amendment XML.
        # This catches PEG parse errors (wrong targets) AND our section
        # extraction errors (missing sections in amendment XML).
        # Include johtolause + body for target identification.
        # The johtolause says WHAT is being changed ("muutetaan 3 §"),
        # the body has the new content. Both needed for target ID.
        amend_full_text = ""
        if johtolause:
            amend_full_text = johtolause + "\n\n"
        body_el = amend_tree.find(f".//{NS}body")
        if body_el is not None:
            amend_full_text += normalize_text(_joined_text(body_el.itertext(), sep=" "))
        if amend_full_text:
            target_prompt = TARGET_IDENTIFICATION_PROMPT.format(
                parent_sid=statute_id,
                amendment_text=amend_full_text[:4000],  # bounded
                targets_start=TARGETS_START,
                targets_end=TARGETS_END,
            )
            async with sem:
                raw_out, _ = await call_llm(
                    target_prompt,
                    call_label=f"{statute_id.replace('/', '_')}_{amend_id.replace('/', '_')}_targets",
                )
            llm_targets_raw = _extract_delimited(raw_out, TARGETS_START, TARGETS_END)

            # Parse LLM target list
            llm_target_sections = set()
            for line in llm_targets_raw.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("["):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    llm_target_sections.add(parts[1])  # section number

            # Compare against our extracted amendment sections
            our_sections = set(amend_sections.keys())
            only_llm = llm_target_sections - our_sections
            only_ours = our_sections - llm_target_sections

            if only_llm or only_ours:
                print("    ⚠ TARGET DISAGREEMENT:")
                if only_llm:
                    print(f"      LLM says also: {sorted(only_llm)} (we missed these?)")
                if only_ours:
                    print(f"      We have but LLM missed: {sorted(only_ours)} (LLM parse error?)")

                # Record target disagreements
                for sec in only_llm:
                    results.append(ProvisionVerification(
                        address=f"section:{sec}",
                        amendment_id=amend_id,
                        diagnosis="TARGET_DISAGREEMENT_LLM_ONLY",
                        replay_text="",
                        oracle_text="",
                        llm_text="",
                        replay_match_llm=False,
                        oracle_match_llm=False,
                        note=f"LLM identified §{sec} as target but our extraction did not",
                    ))

        # PHASE 2: Provision-level text verification

        # For each amendment section, verify against the corresponding provision
        async def verify_one_provision(sec_num: str, amend_text: str) -> Optional[ProvisionVerification]:
            # Find the matching provision in consolidated
            matching_keys = [
                k for k in consolidated
                if k.endswith(f"/section:{sec_num}") or k == f"section:{sec_num}"
            ]
            if not matching_keys:
                # New section (insert) — use empty current text
                current_text = "(this section does not yet exist — it is being inserted)"
                address = f"section:{sec_num}"
            else:
                address = matching_keys[0]
                current_text = consolidated[address]

            prompt = PROMPT_TEMPLATE.format(
                sec_num=sec_num,
                current_text=current_text,
                johtolause=johtolause,
                amendment_content=amend_text,
                result_start=RESULT_START,
                result_end=RESULT_END,
            )

            async with sem:
                raw_out, _ = await call_llm(
                    prompt,
                    call_label=f"{statute_id.replace('/', '_')}_{amend_id.replace('/', '_')}_sec{sec_num}",
                )
            llm_result = _extract_delimited(raw_out, RESULT_START, RESULT_END)

            # Per-step: just update consolidated, no comparison yet.
            # Comparison happens at the END against final replay/oracle.
            if "[UNABLE TO DETERMINE]" in llm_result or "[TIMEOUT]" in llm_result or "[ERROR" in llm_result:
                return ProvisionVerification(
                    address=address,
                    amendment_id=amend_id,
                    diagnosis="LLM_UNCERTAIN_STEP",
                    replay_text="",
                    oracle_text="",
                    llm_text=llm_result,
                    replay_match_llm=False,
                    oracle_match_llm=False,
                    note=f"LLM could not derive at step {i+1}: {llm_result[:100]}",
                )

            # Update cumulative LLM-derived state
            consolidated[address] = llm_result
            llm_touched_keys.add(address)
            return None  # no comparison result yet — deferred to end

        # Run provision verifications (bounded concurrency)
        tasks = []
        for sec_num, amend_text in amend_sections.items():
            tasks.append(verify_one_provision(sec_num, amend_text))

        provision_results = await asyncio.gather(*tasks)
        for pr in provision_results:
            if pr is not None:
                results.append(pr)
                print(f"    ? {pr.address} [{pr.diagnosis}] {pr.note}")
            # None = successful LLM derivation, consolidated updated, comparison deferred

    # ================================================================
    # FINAL THREE-WAY COMPARISON
    # Compare LLM's cumulative consolidated state against final replay
    # and final oracle. This is where real findings emerge.
    # ================================================================
    print("\n  --- Final three-way comparison ---")
    final_results: List[ProvisionVerification] = []
    all_keys = sorted(set(list(consolidated.keys()) + list(replay_provs.keys()) + list(oracle_provs.keys())))
    for key in all_keys:
        llm_text = consolidated.get(key, "")
        replay_text = replay_provs.get(key, "")
        oracle_text = oracle_provs.get(key, "")

        if not llm_text and not replay_text and not oracle_text:
            continue

        llm_a = alpha_only(llm_text)
        replay_a = alpha_only(replay_text)
        oracle_a = alpha_only(oracle_text)

        replay_match = llm_a == replay_a
        oracle_match = llm_a == oracle_a
        replay_oracle_match = replay_a == oracle_a

        if replay_match and oracle_match:
            diagnosis = "TRIPLE_CONFIRMED"
            note = "All three sources agree"
        elif replay_oracle_match and not replay_match:
            if key not in llm_touched_keys:
                # LLM never processed this provision (target ID failure).
                # consolidated still has base text, which differs from
                # amended replay/oracle. Replay=oracle agreement is solid.
                diagnosis = "PASSTHROUGH_CONFIRMED"
                note = "LLM missed target (base text unchanged in LLM state); replay=oracle agree"
            else:
                diagnosis = "LLM_OUTLIER"
                note = "Replay and oracle agree, LLM derived different text"
        elif replay_match and not oracle_match:
            diagnosis = "ORACLE_BUG"
            note = "LLM and replay agree, oracle differs"
        elif oracle_match and not replay_match:
            diagnosis = "REPLAY_BUG"
            note = "LLM and oracle agree, replay differs"
        elif not replay_match and not oracle_match and not replay_oracle_match:
            diagnosis = "ALL_DISAGREE"
            note = "All three sources disagree — manual investigation needed"
        else:
            diagnosis = "UNKNOWN"
            note = ""

        marker = {
            "TRIPLE_CONFIRMED": "✓",
            "PASSTHROUGH_CONFIRMED": "✓P",
            "REPLAY_BUG": "✗R",
            "ORACLE_BUG": "✗O",
            "LLM_OUTLIER": "~L",
            "ALL_DISAGREE": "✗✗",
        }.get(diagnosis, "?")

        if diagnosis != "TRIPLE_CONFIRMED":
            print(f"    {marker} {key} [{diagnosis}] {note}")

        final_results.append(ProvisionVerification(
            address=key,
            amendment_id="(final)",
            diagnosis=diagnosis,
            replay_text=replay_text,
            oracle_text=oracle_text,
            llm_text=llm_text,
            replay_match_llm=replay_match,
            oracle_match_llm=oracle_match,
            note=note,
        ))

    # Count final results
    n_triple = sum(1 for r in final_results if r.diagnosis == "TRIPLE_CONFIRMED")
    n_total = len(final_results)
    print(f"  {n_triple}/{n_total} provisions TRIPLE_CONFIRMED")

    # Merge step-level results (LLM_UNCERTAIN_STEP, TARGET_DISAGREEMENT) with final comparison
    results = [r for r in results if r is not None] + final_results

    verification = StatuteVerification(
        statute_id=statute_id,
        title=title,
        amendments_processed=len(amendments),
        provisions_verified=len(final_results),
        results=results,
    )

    # Check for escalation-worthy findings
    escalations = [r for r in results if r.diagnosis in ("REPLAY_BUG", "ALL_DISAGREE")]
    if escalations:
        esc_path = SCRATCH_DIR / f"{statute_id.replace('/', '_')}_ESCALATION.txt"
        esc_path.parent.mkdir(parents=True, exist_ok=True)
        with open(esc_path, "w", encoding="utf-8") as f:
            f.write(f"ESCALATION: {statute_id} — {len(escalations)} findings need human review\n\n")
            for r in escalations:
                f.write(f"{'='*60}\n")
                f.write(f"Provision: {r.address}\n")
                f.write(f"Amendment: {r.amendment_id}\n")
                f.write(f"Diagnosis: {r.diagnosis}\n")
                f.write(f"Note: {r.note}\n\n")
                f.write(f"REPLAY excerpt:\n{r.replay_text}\n\n")
                f.write(f"ORACLE excerpt:\n{r.oracle_text}\n\n")
                f.write(f"LLM excerpt:\n{r.llm_text}\n\n")
        print(f"\n  *** ESCALATION: {len(escalations)} findings written to {esc_path}")

    # Save results
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SCRATCH_DIR / f"{statute_id.replace('/', '_')}_verification.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "statute_id": statute_id,
                "title": title,
                "amendments_processed": len(amendments),
                "provisions_verified": len(results),
                "summary": verification.summary(),
                "results": [
                    {
                        "address": r.address,
                        "amendment_id": r.amendment_id,
                        "diagnosis": r.diagnosis,
                        "replay_match_llm": r.replay_match_llm,
                        "oracle_match_llm": r.oracle_match_llm,
                        "note": r.note,
                        "replay_excerpt": r.replay_text,
                        "oracle_excerpt": r.oracle_text,
                        "llm_excerpt": r.llm_text,
                    }
                    for r in results
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # Print summary
    print(f"\n  Summary for {statute_id}:")
    for diag, count in sorted(verification.summary().items()):
        print(f"    {diag}: {count}")
    print(f"  Results saved to: {out_path}")

    archive.close()
    return verification


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description="Independent LLM verification of statute amendment replay"
    )
    parser.add_argument(
        "statute_id",
        nargs="?",
        help="Statute ID to verify (e.g. 2002/738)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Verify all canary statutes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be verified without making LLM calls",
    )
    parser.add_argument(
        "--model",
        default=OPENROUTER_MODEL,
        help=f"OpenRouter model ID (default: {OPENROUTER_MODEL})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=MAX_CONCURRENT,
        help=f"Max concurrent LLM calls (default: {MAX_CONCURRENT})",
    )
    args = parser.parse_args()

    if not args.statute_id and not args.all:
        parser.error("Provide a statute_id or --all")

    statutes = CANARY_STATUTES if args.all else [args.statute_id]
    sem = asyncio.Semaphore(args.concurrency)

    all_results: List[StatuteVerification] = []
    for sid in statutes:
        result = await verify_statute(
            sid, dry_run=args.dry_run, semaphore=sem
        )
        all_results.append(result)

    if len(all_results) > 1:
        print(f"\n{'='*70}")
        print("OVERALL SUMMARY")
        print(f"{'='*70}")
        total: Dict[str, int] = {}
        for v in all_results:
            for diag, count in v.summary().items():
                total[diag] = total.get(diag, 0) + count
        for diag, count in sorted(total.items()):
            print(f"  {diag}: {count}")
        total_verified = sum(v.provisions_verified for v in all_results)
        print(f"  TOTAL provisions verified: {total_verified}")

    # Cleanup
    if _session and not _session.closed:
        await _session.close()


if __name__ == "__main__":
    asyncio.run(main())
