#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["lxml"]
# ///
"""Phase 1: Gather — collect all evidence for Golden Verification Process.

For each statute S, runs existing lawvm CLI tools and reads source XML to
produce a single self-contained JSON evidence package.

Usage (from LawVM/ dir):
    uv run python scripts/golden_gather.py 2006/395
    uv run python scripts/golden_gather.py 2006/395 2018/1121 2019/906
    uv run python scripts/golden_gather.py --from-triage .tmp/golden_statutes_ranked.jsonl --top 10

Output:
    LawVM/.tmp/golden_gathered/{statute_id_normalized}.json
    e.g. .tmp/golden_gathered/2006_395.json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from lxml import etree
from lawvm.corpus_store import statute_url
from lawvm.finland.corrigendum_records import load_patch_records

# ---------------------------------------------------------------------------
# Paths (relative to cwd = LawVM/)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent  # scripts/ → LawVM/

_FARCHIVE_PATH = _LAWVM_DIR / "data" / "finlex.farchive"
_DIVERGENCES_DB = _LAWVM_DIR / ".tmp" / "divergences.db"
_OUTPUT_DIR = _LAWVM_DIR / ".tmp" / "golden_gathered"


class _ArchiveGet(Protocol):
    def get(self, locator: str) -> bytes | None: ...


# ---------------------------------------------------------------------------
# De-editorialization
# ---------------------------------------------------------------------------

def de_editorialize(text: str) -> str:
    """Strip Finlex editorial annotations from oracle text.

    Strips:
    - Kumottu notes: "N § on kumottu L:lla DD.MM.YYYY/NNN."
    - Parenthetical law-reference-only additions like "(2012/567)" where
      the surrounding context makes them editorial (conservative: only strip
      bare year/num refs that appear isolated in parens)
    - Thin space (U+2009) and non-breaking space (U+00A0) → regular space
    - Multiple whitespace → single space
    """
    # Kumottu annotations with full date
    text = re.sub(
        r'\d+\s*[a-zäöå]?\s*§\s+on kumottu\s+L:lla\s+\d+\.\d+\.\d+/\d+\.?',
        '',
        text,
        flags=re.IGNORECASE,
    )
    # Thin / non-breaking spaces
    text = text.replace('\u2009', ' ').replace('\u00a0', ' ')
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ---------------------------------------------------------------------------
# XML helpers (reuse patterns from faults.py)
# ---------------------------------------------------------------------------

_SEC_NUM_RE = re.compile(r'(\d+\s*[a-zäöå]?)\s*§', re.I)


def _norm_sec_num(raw: str) -> str:
    m = _SEC_NUM_RE.search(raw)
    if m:
        return re.sub(r'\s+', '', m.group(1)).lower()
    return re.sub(r'[^0-9a-zäöå]', '', raw.lower())


def _el_text(el: etree._Element) -> str:
    return etree.tostring(el, method='text', encoding='unicode').strip()


def _norm_ws(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def _load_xml_from_farchive(
    statute_id: str,
    archive: _ArchiveGet,
) -> etree._Element | None:
    url = statute_url(statute_id)
    try:
        xml_bytes = archive.get(url)
        if xml_bytes is None:
            return None
        return etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None


def _extract_section_text(root: etree._Element, section_key: str) -> str:
    """Extract body text for a specific section from an AKN XML root."""
    body = root.find('.//{*}body')
    if body is None:
        return ''
    for sec in body.findall('.//{*}section'):
        num_el = sec.find('.//{*}num')
        if num_el is None:
            continue
        if _norm_sec_num(num_el.text or '') == section_key:
            return _norm_ws(_el_text(sec))
    return ''


def _extract_johtolause(root: etree._Element) -> str:
    """Extract preamble (johtolause) text from an AKN XML root."""
    preamble = root.find('.//{*}preamble')
    if preamble is not None:
        return _norm_ws(_el_text(preamble))
    return ''


def _extract_title(root: etree._Element) -> str:
    for el in root.findall('.//{*}docTitle'):
        t = _norm_ws(el.text or '')
        if t:
            return t
    return ''


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------

def _run_lawvm(*args: str, cwd: Path | None = None) -> str:
    """Run `uv run lawvm <args>` and return stdout. Stderr is captured but
    not raised — many commands print progress to stderr."""
    cmd = ['uv', 'run', 'lawvm'] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd or _LAWVM_DIR),
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Divergences DB
# ---------------------------------------------------------------------------

def _query_divergences(statute_id: str) -> list[dict]:
    """Return all divergence rows for a statute from divergences.db."""
    if not _DIVERGENCES_DB.exists():
        return []
    con = sqlite3.connect(f'file:{_DIVERGENCES_DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            '''SELECT statute_id, title, overall_score, section_score,
                      section, diagnosis, blame_source, blame_title,
                      oracle_version, replay_text, oracle_text
               FROM divergences
               WHERE statute_id = ?
               ORDER BY section''',
            (statute_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Corrigendum corpus
# ---------------------------------------------------------------------------

def _to_corrigendum_id(amendment_id: str) -> str:
    """Convert YEAR/NUM (divergences.db format) to NUM/YEAR (official corpus format).

    divergences.db uses YEAR/NUM (e.g. '2021/116', '2013/1311').
    The official corrigendum corpus uses NUM/YEAR (e.g. '116/2021', '1311/2013').

    Detection: if the first component looks like a calendar year (1800-2199),
    treat as YEAR/NUM and flip to NUM/YEAR.  Otherwise assume already flipped.
    """
    parts = amendment_id.split('/')
    if len(parts) == 2:
        a, b = parts
        if a.isdigit() and 1800 <= int(a) <= 2199:
            return f'{b}/{a}'
    return amendment_id


def _query_corrigendum_patches(amendment_id: str) -> list[dict]:
    """Return official Finnish corrigendum items for an amendment from the text corpus.

    Accepts both YEAR/NUM and NUM/YEAR formats; normalises to the corpus' NUM/YEAR.
    """
    corr_id = _to_corrigendum_id(amendment_id)
    rows = load_patch_records()
    filtered = [
        dict(row)
        for row in rows
        if str(row.get('amendment_id') or '').strip() == corr_id
        and str(row.get('lang') or 'fi').strip() == 'fi'
    ]
    filtered.sort(key=lambda row: int(row.get('correction_index') or 0))
    return filtered


# ---------------------------------------------------------------------------
# Blame amendment extraction from bisect output
# ---------------------------------------------------------------------------

_BISECT_SUSPECT_RE = re.compile(r'Primary suspect:\s+(\S+)')
_BISECT_DROP_RE = re.compile(r'\[\s*\d+/\d+\]\s+(\S+)\s+.*?→.*?(-\d+\.?\d*%)')


def _parse_bisect_blame_amendments(bisect_output: str) -> list[str]:
    """Extract blame amendment IDs from bisect output (in order of score drop)."""
    amendments = []
    # Primary suspect first
    m = _BISECT_SUSPECT_RE.search(bisect_output)
    if m:
        primary = m.group(1)
        amendments.append(primary)
    # All drops
    for m in _BISECT_DROP_RE.finditer(bisect_output):
        amend_id = m.group(1)
        if amend_id not in amendments:
            amendments.append(amend_id)
    return amendments


# ---------------------------------------------------------------------------
# Ops extraction from ops output
# ---------------------------------------------------------------------------

_OPS_AMENDMENT_HEADER_RE = re.compile(r'^---\s+(\S+)\s+')
_OPS_OP_RE = re.compile(r'\[\s*\d+\]\s+(\w+)\s+(.*)')


def _parse_ops_for_amendment(ops_output: str, amendment_id: str) -> list[str]:
    """Extract operation strings for a specific amendment from ops output."""
    ops = []
    in_section = False
    for line in ops_output.splitlines():
        header_m = _OPS_AMENDMENT_HEADER_RE.match(line)
        if header_m:
            in_section = (header_m.group(1) == amendment_id)
            continue
        if in_section:
            op_m = _OPS_OP_RE.match(line.strip())
            if op_m:
                ops.append(f"{op_m.group(1)} {op_m.group(2).strip()}")
    return ops


# ---------------------------------------------------------------------------
# Per-section evidence builder
# ---------------------------------------------------------------------------

def _gather_section_evidence(
    statute_id: str,
    section_key: str,
    div_row: dict,
    blame_amendments: list[str],
    ops_output: str,
    base_root: etree._Element | None,
    archive: _ArchiveGet,
) -> dict:
    """Build the evidence record for one diverging section."""

    # --- Replay and oracle text from divergences.db ---
    replay_text = div_row.get('replay_text') or ''
    oracle_text_raw = div_row.get('oracle_text') or ''
    oracle_text_clean = de_editorialize(oracle_text_raw)

    # --- Base statute text for this section ---
    base_text = ''
    if base_root is not None:
        base_text = _extract_section_text(base_root, section_key)

    # --- Build amendment chain ---
    amendment_chain = []
    # Determine which amendments are relevant for this section.
    # Primary: blame_source from divergences row; secondary: bisect blames.
    blame_source = div_row.get('blame_source') or ''
    # All amendments to include: blame_source first, then any bisect-found ones
    amend_ids_for_section: list[str] = []
    if blame_source:
        amend_ids_for_section.append(blame_source)
    for a in blame_amendments:
        if a and a not in amend_ids_for_section:
            amend_ids_for_section.append(a)

    for amend_id in amend_ids_for_section:
        amend_root = _load_xml_from_farchive(amend_id, archive)
        johtolause = ''
        body_text = ''
        if amend_root is not None:
            johtolause = _extract_johtolause(amend_root)
            body_text = _extract_section_text(amend_root, section_key)

        ops_for_amend = _parse_ops_for_amendment(ops_output, amend_id)
        corrigendum_patches = _query_corrigendum_patches(amend_id)

        amendment_chain.append({
            'amendment_id': amend_id,
            'johtolause': johtolause,
            'body_text': body_text,
            'operations_extracted': ops_for_amend,
            'corrigendum_patches': corrigendum_patches,
        })

    return {
        'section_score': div_row.get('section_score'),
        'fault_type': div_row.get('diagnosis'),
        'blame_amendment': blame_source or None,
        'base_text': base_text,
        'oracle_text_raw': oracle_text_raw,
        'oracle_text_clean': oracle_text_clean,
        'replay_text': replay_text,
        'amendment_chain': amendment_chain,
    }


# ---------------------------------------------------------------------------
# Main gather function for one statute
# ---------------------------------------------------------------------------

def gather_statute(statute_id: str) -> dict:
    """Gather all Phase 1 evidence for statute_id. Returns the evidence dict."""
    print(f'  Gathering {statute_id}...', flush=True)

    # --- Run diagnostics ---
    explain_output = _run_lawvm('explain', statute_id)
    bisect_output = _run_lawvm('bisect', statute_id)
    ops_output = _run_lawvm('ops', statute_id)
    corrigendum_output = _run_lawvm('corrigendum', 'status', statute_id)

    # Parse blame amendments from bisect
    blame_amendments = _parse_bisect_blame_amendments(bisect_output)

    # Run ops per blame amendment for richer per-amendment extraction
    per_amend_ops: dict[str, str] = {}
    for amend_id in blame_amendments:
        per_amend_ops[amend_id] = _run_lawvm('ops', statute_id, '--source', amend_id)

    # --- Query divergences.db for all diverging sections ---
    div_rows = _query_divergences(statute_id)

    # --- Open farchive once for all XML reads ---
    import farchive as _farchive

    if not _FARCHIVE_PATH.exists():
        print(f'  WARNING: farchive not found at {_FARCHIVE_PATH}', file=sys.stderr)
        archive = None
    else:
        archive = _farchive.Farchive(str(_FARCHIVE_PATH), readonly=True)

    try:
        # Load base statute XML
        base_root: etree._Element | None = None
        if archive is not None:
            base_root = _load_xml_from_farchive(statute_id, archive)

        # Build sections dict
        sections: dict[str, dict] = {}
        for row in div_rows:
            section_key = str(row.get('section') or '')
            if not section_key:
                continue

            # Per-amendment ops: prefer ops filtered to each amendment, else full
            combined_ops = ops_output
            if blame_amendments:
                # Use per-amend ops where available, merged
                combined_ops = '\n'.join(
                    per_amend_ops.get(a, '') for a in blame_amendments
                )

            assert archive is not None
            sections[section_key] = _gather_section_evidence(
                statute_id=statute_id,
                section_key=section_key,
                div_row=row,
                blame_amendments=blame_amendments,
                ops_output=combined_ops,
                base_root=base_root,
                archive=archive,
            )

        # Also add ops_output per blame amendment to amendment_chain entries
        # (re-run with per-amendment filtering for cleaner data)
        for section_key, sec_data in sections.items():
            for entry in sec_data.get('amendment_chain', []):
                amend_id = entry['amendment_id']
                if amend_id in per_amend_ops and not entry['operations_extracted']:
                    entry['operations_extracted'] = _parse_ops_for_amendment(
                        per_amend_ops[amend_id], amend_id
                    )

    finally:
        if archive is not None:
            archive.close()

    return {
        'statute_id': statute_id,
        'gathered_at': datetime.now(timezone.utc).isoformat(),
        'sections': sections,
        'diagnostics': {
            'explain_output': explain_output,
            'bisect_output': bisect_output,
            'corrigendum_output': corrigendum_output,
            'ops_output': ops_output,
        },
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _normalize_id(statute_id: str) -> str:
    return statute_id.replace('/', '_')


def _write_output(data: dict) -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUT_DIR / f"{_normalize_id(data['statute_id'])}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------

def _read_triage_file(path: Path, top: int | None = None) -> list[str]:
    """Read statute IDs from golden_statutes_ranked.jsonl (Phase 0 output).

    Each line is a JSON object with a 'statute_id' field and a 'total_score'.
    Returns list of statute IDs sorted by total_score descending.
    """
    entries = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                sid = obj.get('statute_id') or obj.get('id')
                score = obj.get('total_score') or obj.get('max_score') or 0
                if sid:
                    entries.append((score, sid))
            except json.JSONDecodeError:
                continue
    # Sort by score descending
    entries.sort(key=lambda x: x[0], reverse=True)
    ids = [sid for _, sid in entries]
    if top is not None:
        ids = ids[:top]
    return ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='golden_gather.py',
        description='Phase 1: collect evidence for Golden Verification Process.',
    )
    p.add_argument(
        'statute_ids',
        nargs='*',
        metavar='STATUTE_ID',
        help='Statute IDs to gather, e.g. 2006/395',
    )
    p.add_argument(
        '--from-triage',
        metavar='FILE',
        help='Read statute IDs from golden_statutes_ranked.jsonl (Phase 0 output)',
    )
    p.add_argument(
        '--top',
        type=int,
        metavar='N',
        help='With --from-triage: only process the top N statutes',
    )
    p.add_argument(
        '--output-dir',
        metavar='DIR',
        help=f'Output directory (default: {_OUTPUT_DIR})',
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    global _OUTPUT_DIR
    if args.output_dir:
        _OUTPUT_DIR = Path(args.output_dir)

    # Collect statute IDs
    statute_ids: list[str] = list(args.statute_ids)
    if args.from_triage:
        triage_path = Path(args.from_triage)
        if not triage_path.exists():
            print(f'ERROR: triage file not found: {triage_path}', file=sys.stderr)
            sys.exit(1)
        triage_ids = _read_triage_file(triage_path, top=args.top)
        statute_ids.extend(triage_ids)

    if not statute_ids:
        print('ERROR: no statute IDs specified. Use positional args or --from-triage.', file=sys.stderr)
        sys.exit(1)

    # Deduplicate preserving order
    seen: set[str] = set()
    unique_ids = []
    for sid in statute_ids:
        if sid not in seen:
            seen.add(sid)
            unique_ids.append(sid)

    print(f'Gathering evidence for {len(unique_ids)} statute(s)...')

    for sid in unique_ids:
        try:
            data = gather_statute(sid)
            out_path = _write_output(data)
            n_sections = len(data['sections'])
            print(f'  -> {out_path}  ({n_sections} diverging sections)')
        except Exception as exc:
            print(f'  ERROR: {sid}: {exc}', file=sys.stderr)
            import traceback
            traceback.print_exc()

    print('Done.')


if __name__ == '__main__':
    main()
