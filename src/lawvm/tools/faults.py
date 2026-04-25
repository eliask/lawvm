"""lawvm faults — fault evidence builder for Finlex divergences.

Reads divergences.db (produced by `lawvm oracle-check --db`) and generates
independently verifiable evidence bundles for each replay fault. Evidence
uses only primary sources (AKN XML amendments from source corpus) and the
cached Finlex oracle text — no LawVM trust required to verify.

Subcommands:
    list     [--min-severity N] [--diagnosis D]  List faults with severity
    evidence <statute_id> [--section S]          Generate 4-step proof JSON
    export   --output FILE [--min-severity N]    Export all faults as JSONL
    summary                                      Aggregate statistics

Usage:
    lawvm faults list
    lawvm faults list --min-severity 2
    lawvm faults list --diagnosis REPLAY_MISSING
    lawvm faults evidence 2006/1299
    lawvm faults evidence 2006/1299 --section 3
    lawvm faults export --output .tmp/faults.jsonl
    lawvm faults export --output .tmp/faults.jsonl --min-severity 2
    lawvm faults summary
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from lxml import etree

# ---------------------------------------------------------------------------
# Paths — same pattern as grafter.py (relative to cwd = LawVM/)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent  # src/lawvm/tools/ → LawVM/

_DEFAULT_DB = _LAWVM_DIR / ".tmp" / "divergences.db"

# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

# Diagnoses that are NOT faults (oracle/editorial issues, not replay bugs)
_ORACLE_DIAGNOSES = frozenset({
    "EDITORIAL_CONVENTION",
    "ORACLE_STALE",
    "CORRIGENDUM_APPLIED",
    "LIITE_DIFF",
})

# Diagnoses that are definitely faults (replay logic failed)
_FAULT_DIAGNOSES = frozenset({
    "REPLAY_MISSING",
    "REPLAY_EXTRA",
    "UNKNOWN",
    "MISSING",
    "EXTRA",
})


def _classify_severity(diagnosis: str, section_score: float) -> int:
    """Return severity 1-3 for a divergence row.

    sev=1  Editorial / cosmetic — whitespace, punctuation, formatting
    sev=2  Substantive but meaning-preserving — reordering, synonym
    sev=3  Legally meaningful — missing amendment, wrong text, stale repeal
    """
    if diagnosis == "EDITORIAL_CONVENTION":
        return 1
    if diagnosis in ("ORACLE_STALE", "LIITE_DIFF"):
        return 1  # not our bug
    if diagnosis == "CORRIGENDUM_APPLIED":
        return 1  # LawVM is MORE correct than Finlex — still not a fault
    # UNKNOWN: grade by section_score
    if diagnosis == "UNKNOWN":
        if section_score >= 0.90:
            return 1
        if section_score >= 0.50:
            return 2
        return 3
    # Definitive fault diagnoses
    if diagnosis in ("REPLAY_MISSING", "REPLAY_EXTRA", "MISSING", "EXTRA"):
        return 3
    # Default fallback
    if section_score >= 0.90:
        return 1
    if section_score >= 0.50:
        return 2
    return 3


def _is_fault(diagnosis: str, section_score: float) -> bool:
    """True if this divergence is a real fault (not an oracle/editorial issue)."""
    if diagnosis in _ORACLE_DIAGNOSES:
        return False
    return True


# ---------------------------------------------------------------------------
# DB access
# ---------------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open divergences.db read-only; exit with clear message if missing."""
    if not db_path.exists():
        print(
            f"ERROR: divergences.db not found: {db_path}\n"
            "Run `lawvm oracle-check --corpus-full --db .tmp/divergences.db` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _query_faults(
    con: sqlite3.Connection,
    min_severity: int = 1,
    diagnosis_filter: str | None = None,
    statute_id: str | None = None,
    section: str | None = None,
) -> list[sqlite3.Row]:
    """Return divergence rows that qualify as faults matching filters."""
    clauses = []
    params: list = []

    # Filter to known-fault diagnoses (exclude oracle/editorial)
    fault_diag_list = ", ".join(f"'{d}'" for d in _FAULT_DIAGNOSES)
    clauses.append(f"diagnosis IN ({fault_diag_list})")

    if diagnosis_filter:
        clauses.append("diagnosis = ?")
        params.append(diagnosis_filter.upper())

    if statute_id:
        clauses.append("statute_id = ?")
        params.append(statute_id)

    if section:
        clauses.append("section = ?")
        params.append(section)

    where = " AND ".join(clauses)
    sql = f"""
        SELECT statute_id, title, overall_score, section_score,
               section, diagnosis, blame_source, blame_title,
               oracle_version, replay_text, oracle_text
        FROM divergences
        WHERE {where}
        ORDER BY section_score ASC
    """
    rows = list(con.execute(sql, params).fetchall())

    # Apply severity filter in Python (avoids re-computing in SQL)
    if min_severity > 1:
        rows = [
            r for r in rows
            if _classify_severity(r["diagnosis"], r["section_score"]) >= min_severity
        ]

    return rows


# ---------------------------------------------------------------------------
# Amendment XML extraction
# ---------------------------------------------------------------------------

def _el_text(el: etree._Element) -> str:
    return etree.tostring(el, method="text", encoding="unicode").strip()


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _load_amendment_xml(amendment_id: str) -> "etree._Element | None":
    """Load the Finnish AKN XML for an amendment via CorpusStore."""
    from lawvm.finland.grafter import get_corpus
    xml_bytes = get_corpus().read_source(amendment_id)
    if xml_bytes is None:
        return None
    try:
        return etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None


def _extract_amendment_info(
    root: etree._Element,
    section_key: str,
) -> dict:
    """Extract title, preamble (johtolause), section body text from amendment XML.

    section_key is the normalized section number (e.g. '3', '12a').
    Returns a dict with keys: title, johtolause, body_text, section_found.
    """
    result: dict = {
        "title": "",
        "johtolause": "",
        "body_text": "",
        "section_found": False,
    }

    # Title from docTitle in preface
    for el in root.findall(".//{*}docTitle"):
        result["title"] = _norm_ws(el.text or "")
        break

    # Preamble / johtolause
    preamble = root.find(".//{*}preamble")
    if preamble is not None:
        result["johtolause"] = _norm_ws(_el_text(preamble))

    # Body: find the section matching section_key
    body = root.find(".//{*}body")
    if body is None:
        return result

    _SEC_NUM_RE = re.compile(r"(\d+\s*[a-zäöå]?)\s*§", re.I)

    def _norm_sec_num(raw: str) -> str:
        m = _SEC_NUM_RE.search(raw)
        if m:
            return re.sub(r"\s+", "", m.group(1)).lower()
        return re.sub(r"[^0-9a-zäöå]", "", raw.lower())

    for sec in body.findall(".//{*}section"):
        num_el = sec.find(".//{*}num")
        if num_el is None:
            continue
        num_raw = num_el.text or ""
        if _norm_sec_num(num_raw) == section_key:
            result["body_text"] = _norm_ws(_el_text(sec))
            result["section_found"] = True
            break

    return result


def _get_amendment_date(amendment_id: str) -> str:
    """Best-effort: extract year from ID and return YYYY-XX-XX."""
    parts = amendment_id.split("/")
    # Try to find 4-digit year part
    for p in parts:
        if len(p) == 4 and p.isdigit():
            return f"{p}-??-??"
    return "????-??-??"


# ---------------------------------------------------------------------------
# Word-level diff (HTML)
# ---------------------------------------------------------------------------

def _make_diff_html(expected: str, actual: str) -> str:
    """Produce a simple word-diff HTML string: <del>removed</del> <ins>added</ins>."""
    a_words = expected.split()
    b_words = actual.split()
    sm = difflib.SequenceMatcher(None, a_words, b_words, autojunk=False)
    parts = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(" ".join(a_words[i1:i2]))
        elif tag == "replace":
            parts.append(f"<del>{' '.join(a_words[i1:i2])}</del> <ins>{' '.join(b_words[j1:j2])}</ins>")
        elif tag == "delete":
            parts.append(f"<del>{' '.join(a_words[i1:i2])}</del>")
        elif tag == "insert":
            parts.append(f"<ins>{' '.join(b_words[j1:j2])}</ins>")
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Fault type naming
# ---------------------------------------------------------------------------

def _fault_type_label(diagnosis: str) -> str:
    # Direction: REPLAY_EXTRA = replay > oracle (replay applied amendment, Finlex didn't)
    #            REPLAY_MISSING = replay < oracle (Finlex more complete, LawVM is behind)
    # AMENDMENT_NOT_APPLIED means FINLEX failed to apply → only REPLAY_EXTRA qualifies.
    mapping = {
        "REPLAY_EXTRA":   "AMENDMENT_NOT_APPLIED",    # replay applied, Finlex behind
        "REPLAY_MISSING": "REPLAY_UNDERAPPLIED",       # Finlex applied, LawVM behind — not a Finlex fault
        "EXTRA":          "SECTION_ABSENT_IN_ORACLE",  # section in replay, absent from Finlex
        "MISSING":        "SECTION_ABSENT_IN_REPLAY",  # section in Finlex, absent from replay (LawVM bug)
        "UNKNOWN":        "DIVERGENCE_UNKNOWN",
    }
    return mapping.get(diagnosis, diagnosis)


# Diagnoses where Finlex is behind the amendment (potential Finlex errors):
_FINLEX_BEHIND_DIAGNOSES = frozenset({"REPLAY_EXTRA", "EXTRA"})
# Diagnoses where LawVM is behind Finlex (replay bugs, not Finlex errors):
_REPLAY_BEHIND_DIAGNOSES = frozenset({"REPLAY_MISSING", "MISSING"})


# ---------------------------------------------------------------------------
# Evidence bundle builder
# ---------------------------------------------------------------------------

def _build_evidence_bundle(row: sqlite3.Row) -> dict:
    """Build the 4-step proof bundle for one divergent section."""
    statute_id = row["statute_id"]
    section = row["section"]
    diagnosis = row["diagnosis"]
    section_score = row["section_score"] or 0.0
    blame_source = row["blame_source"] or ""
    blame_title = row["blame_title"] or ""
    replay_text = row["replay_text"] or ""
    oracle_text = row["oracle_text"] or ""

    severity = _classify_severity(diagnosis, section_score)
    fault_type = _fault_type_label(diagnosis)

    # Amendment info
    amend_info: dict = {}
    if blame_source:
        root = _load_amendment_xml(blame_source)
        if root is not None:
            amend_info = _extract_amendment_info(root, section)
        if not amend_info:
            amend_info = {"title": blame_title, "johtolause": "", "body_text": "", "section_found": False}
        amend_info["id"] = blame_source
        amend_info["date"] = _get_amendment_date(blame_source)
        amend_info["title"] = amend_info.get("title") or blame_title
        amend_info["source"] = f"AKN XML: akn/fi/act/statute/{blame_source}/fin@/main.xml"

    # Build Finnish-language summary — direction-aware
    if diagnosis in _REPLAY_BEHIND_DIAGNOSES:
        # Finlex is MORE complete than replay: this is a LawVM replay bug, not a Finlex error
        summary = (
            f"LawVM-toistoteksti säädökselle {statute_id} {section} § on vajavaisempi kuin "
            f"Finlex-ajantasaistusteksti (LawVM jäi muutoksista jälkeen). "
            f"Tekstisamankaltaisuus: {section_score:.1%}. "
            f"HUOM: Tämä on LawVM-bugi, ei Finlex-virhe."
        )
    elif blame_source:
        summary = (
            f"Laki {blame_source} muutti säädöksen {statute_id} {section} §:ää, "
            f"mutta Finlex-ajantasaistusteksti poikkeaa muutoslain mukaisesta tilasta. "
            f"Osioiden tekstisamankaltaisuus: {section_score:.1%}."
        )
    else:
        summary = (
            f"Säädöksen {statute_id} {section} § poikkeaa ajantasaistustekstissä "
            f"odotetusta tilasta (samankaltaisuus {section_score:.1%}, "
            f"diagnoosi: {diagnosis}). Muutoslain lähde tuntematon."
        )

    # Diff HTML: expected = replay (what we computed), actual = oracle (what Finlex shows)
    diff_html = _make_diff_html(replay_text, oracle_text)

    finlex_year = statute_id.split("/")[0] if "/" in statute_id else "?????"
    finlex_num = statute_id.split("/")[1] if "/" in statute_id else "?????"
    finlex_num_padded = finlex_num.zfill(4) if finlex_num.isdigit() else finlex_num
    finlex_url = (
        f"https://www.finlex.fi/fi/laki/ajantasa/{finlex_year}/{finlex_year}{finlex_num_padded}#P{section}"
    )

    fetched_ts = datetime.now(timezone.utc).isoformat()

    verification_commands = [
        f"curl -s 'https://www.finlex.fi/fi/laki/ajantasa/{finlex_year}/{finlex_year}{finlex_num_padded}' | grep -A5 '{section} §'",
        f"uv run lawvm explain {statute_id} --section '{section} §'",
        f"uv run lawvm diff {statute_id}",
    ]
    if blame_source:
        verification_commands.append(
            f"uv run lawvm ops {statute_id} --source {blame_source}"
        )

    human_steps = [
        f"1. Open the amendment statute at https://finlex.fi/fi/laki/alkup/{_get_amendment_date(blame_source).split('-')[0]}/{blame_source.replace('/', '')} (or source corpus: akn/fi/act/statute/{blame_source}/fin@/main.xml)."
        if blame_source else "1. No blame amendment identified — check `lawvm explain` for full history.",
        f"2. Read the johtolause (preamble) and find the provision for § {section}.",
        f"3. Open Finlex: {finlex_url}",
        f"4. Compare § {section} text against the amendment's body text.",
        "5. Note the discrepancy between what the amendment mandates and what Finlex shows.",
    ]

    if blame_source and amend_info.get("johtolause"):
        llm_prompt = (
            f"VÄITE: Finlex-merkintä säädökselle {statute_id} {section} §\n"
            f"poikkeaa lain mukaisesta tilasta.\n\n"
            f"MUUTOSLAKI: {blame_source} ({_get_amendment_date(blame_source)})\n"
            f"Johtolause: \"{amend_info['johtolause'][:500]}\"\n"
            f"Uusi teksti: \"{amend_info.get('body_text', '')[:500]}\"\n\n"
            f"FINLEX-TEKSTI (haettu {fetched_ts}):\n"
            f"\"{oracle_text[:500]}\"\n\n"
            f"KYSYMYS: Vastaako Finlex-teksti muutoslain mukaista tekstiä?\n"
            f"Jos ei, kuvaile ero tarkasti."
        )
    else:
        llm_prompt = (
            f"VÄITE: Finlex-merkintä säädökselle {statute_id} {section} §\n"
            f"poikkeaa LawVM:n laskemasta ajantasaistustilasta.\n\n"
            f"LAWVM-TEKSTI (odotettu):\n\"{replay_text[:500]}\"\n\n"
            f"FINLEX-TEKSTI (haettu {fetched_ts}):\n\"{oracle_text[:500]}\"\n\n"
            f"KYSYMYS: Miten nämä tekstit poikkeavat toisistaan? "
            f"Onko ero oikeudellisesti merkittävä?"
        )

    bundle: dict = {
        "statute_id": statute_id,
        "section": section,
        "fault_type": fault_type,
        "severity": severity,
        "diagnosis": diagnosis,
        "section_score": round(section_score, 6),
        "summary": summary,
    }

    if blame_source:
        bundle["evidence"] = {
            "amendment": {
                "id": amend_info.get("id", blame_source),
                "date": amend_info.get("date", ""),
                "title": amend_info.get("title", ""),
                "johtolause": amend_info.get("johtolause", ""),
                "body_text": amend_info.get("body_text", ""),
                "section_found_in_xml": amend_info.get("section_found", False),
                "source": amend_info.get("source", ""),
            },
            "expected_text": replay_text,
            "finlex_text": oracle_text,
            "finlex_url": finlex_url,
            "finlex_fetched": fetched_ts,
            "diff_html": diff_html,
        }
    else:
        bundle["evidence"] = {
            "amendment": None,
            "expected_text": replay_text,
            "finlex_text": oracle_text,
            "finlex_url": finlex_url,
            "finlex_fetched": fetched_ts,
            "diff_html": diff_html,
        }

    bundle["verification"] = {
        "human": "\n".join(human_steps),
        "commands": verification_commands,
        "llm_prompt": llm_prompt,
    }

    return bundle


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> None:
    db_path = Path(getattr(args, "db", None) or _DEFAULT_DB)
    min_sev = getattr(args, "min_severity", 1)
    diag_filter = getattr(args, "diagnosis", None)

    con = _open_db(db_path)
    rows = _query_faults(con, min_severity=min_sev, diagnosis_filter=diag_filter)
    con.close()

    if not rows:
        print("No faults found matching criteria.")
        return

    for row in rows:
        sev = _classify_severity(row["diagnosis"], row["section_score"])
        fault_type = _fault_type_label(row["diagnosis"])
        blame = f"  (blame: {row['blame_source']})" if row["blame_source"] else ""
        print(
            f"{row['statute_id']:<14} §{row['section']:<6}  "
            f"sev={sev}  {fault_type:<30}  "
            f"score={row['section_score']:.3f}{blame}"
        )

    print(f"\nTotal: {len(rows)} fault(s)")


# ---------------------------------------------------------------------------
# Subcommand: evidence
# ---------------------------------------------------------------------------

def _cmd_evidence(args: argparse.Namespace) -> None:
    db_path = Path(getattr(args, "db", None) or _DEFAULT_DB)
    statute_id = args.statute_id
    section = getattr(args, "section", None)

    con = _open_db(db_path)
    rows = _query_faults(con, statute_id=statute_id, section=section)
    con.close()

    if not rows:
        # Also check if statute exists at all with any diagnosis
        con2 = _open_db(db_path)
        all_rows = list(con2.execute(
            "SELECT statute_id, section, diagnosis, section_score FROM divergences WHERE statute_id=?",
            (statute_id,)
        ).fetchall())
        con2.close()
        if all_rows:
            print(f"No faults found for {statute_id}"
                  + (f" §{section}" if section else "") + ".")
            print("Existing divergences (may be editorial/oracle issues):")
            for r in all_rows:
                print(f"  §{r[1]}  {r[2]}  score={r[3]:.3f}")
        else:
            print(f"No divergences found for {statute_id}"
                  + (f" §{section}" if section else "") + ".")
            print("(Statute may be perfect or not in the oracle-check corpus.)")
        return

    bundles = []
    for row in rows:
        bundle = _build_evidence_bundle(row)
        bundles.append(bundle)

    if len(bundles) == 1:
        print(json.dumps(bundles[0], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(bundles, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Subcommand: export
# ---------------------------------------------------------------------------

_TEMP_AMENDMENT_RE = re.compile(r'v[aä]liaikais', re.I)


def _is_temp_amendment_title(title: str) -> bool:
    """Return True if the blame_title suggests a temporary (väliaikainen) amendment.

    Temporary amendments (e.g. COVID-era tartuntatautilaki modifications) may have
    lapsed; Finlex correctly shows current state while LawVM still applies the expired
    provisions, producing false-positive AMENDMENT_NOT_APPLIED faults.
    """
    return bool(_TEMP_AMENDMENT_RE.search(title or ""))


def _build_repealed_index(con: sqlite3.Connection) -> set[str]:
    """Return statute IDs where ALL divergence sections have empty oracle_text.

    These are almost certainly repealed statutes where Finlex shows nothing —
    their SECTION_ABSENT_IN_ORACLE entries are artifacts, not genuine Finlex errors.
    """
    all_sids = {r["statute_id"] for r in
                con.execute("SELECT DISTINCT statute_id FROM divergences").fetchall()}
    repealed: set[str] = set()
    for sid in all_sids:
        rows = con.execute(
            "SELECT oracle_text FROM divergences WHERE statute_id=?", (sid,)
        ).fetchall()
        if rows and all((r["oracle_text"] or "") == "" for r in rows):
            repealed.add(sid)
    return repealed


def _cmd_export(args: argparse.Namespace) -> None:
    db_path = Path(getattr(args, "db", None) or _DEFAULT_DB)
    output = Path(args.output)
    min_sev = getattr(args, "min_severity", 1)
    diag_filter = getattr(args, "diagnosis", None)
    finlex_only = getattr(args, "finlex_only", False)

    con = _open_db(db_path)
    rows = _query_faults(con, min_severity=min_sev, diagnosis_filter=diag_filter)

    # Build repealed-statute index for artifact tagging
    repealed_sids = _build_repealed_index(con)
    con.close()

    # --finlex-only: keep only Finlex-behind cases (REPLAY_EXTRA, EXTRA, UNKNOWN)
    if finlex_only:
        rows = [r for r in rows if r["diagnosis"] in _FINLEX_BEHIND_DIAGNOSES
                or r["diagnosis"] == "UNKNOWN"]

    if not rows:
        print("No faults found matching criteria — nothing exported.")
        return

    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    skipped_artifact = 0
    with open(output, "w", encoding="utf-8") as f:
        for i, row in enumerate(rows):
            try:
                bundle = _build_evidence_bundle(row)
                # Tag repealed-statute artifacts
                if row["statute_id"] in repealed_sids:
                    bundle["repealed_artifact"] = True
                    bundle["note"] = (
                        "Statute appears fully repealed in Finlex "
                        "(all sections have empty oracle text) — "
                        "verify before citing as a Finlex error."
                    )
                    if finlex_only:
                        skipped_artifact += 1
                        continue
                # Tag temporary amendment artifacts
                blame_title = (row["blame_title"] or "") if hasattr(row, "keys") else ""
                if _is_temp_amendment_title(blame_title):
                    bundle["temp_amendment_artifact"] = True
                    bundle["note"] = bundle.get("note") or (
                        "Blame amendment appears to be a temporary (väliaikainen) modification "
                        "that may have lapsed — Finlex correctly shows current state; "
                        "verify before citing as a Finlex error."
                    )
                    if finlex_only:
                        skipped_artifact += 1
                        continue
                f.write(json.dumps(bundle, ensure_ascii=False))
                f.write("\n")
                count += 1
            except Exception as e:
                print(f"  WARN: {row['statute_id']} §{row['section']}: {e}", file=sys.stderr)
            if (i + 1) % 500 == 0:
                print(f"  [{i + 1}/{len(rows)}]...", flush=True)

    print(f"Exported {count:,} faults to {output}")
    if skipped_artifact:
        print(f"  (skipped {skipped_artifact} repealed-statute artifacts)")


# ---------------------------------------------------------------------------
# Subcommand: summary
# ---------------------------------------------------------------------------

def _cmd_summary(args: argparse.Namespace) -> None:
    db_path = Path(getattr(args, "db", None) or _DEFAULT_DB)
    con = _open_db(db_path)

    # Total corpus stats
    cs = con.execute("SELECT * FROM corpus_stats").fetchone()
    total_examined = cs["total_examined"] if cs else 0
    total_diverging = cs["total_diverging"] if cs else 0
    total_errors = cs["total_errors"] if cs else 0

    # All divergence rows
    all_rows = list(con.execute(
        "SELECT statute_id, section, diagnosis, section_score, blame_source FROM divergences"
    ).fetchall())
    con.close()

    total_divs = len(all_rows)

    # Separate faults from non-faults
    fault_rows = [r for r in all_rows if _is_fault(r["diagnosis"], r["section_score"])]
    non_fault_rows = [r for r in all_rows if not _is_fault(r["diagnosis"], r["section_score"])]

    # Split fault rows by direction: Finlex-behind vs LawVM-behind
    finlex_behind_rows = [r for r in fault_rows if r["diagnosis"] in _FINLEX_BEHIND_DIAGNOSES]
    replay_behind_rows = [r for r in fault_rows if r["diagnosis"] in _REPLAY_BEHIND_DIAGNOSES]
    unknown_rows = [r for r in fault_rows if r["diagnosis"] not in _FINLEX_BEHIND_DIAGNOSES
                    and r["diagnosis"] not in _REPLAY_BEHIND_DIAGNOSES]

    fault_type_counts: Counter = Counter()
    sev_counts: Counter = Counter()
    statutes_affected: set = set()
    finlex_statutes: set = set()

    for r in fault_rows:
        ft = _fault_type_label(r["diagnosis"])
        fault_type_counts[ft] += 1
        sev = _classify_severity(r["diagnosis"], r["section_score"])
        sev_counts[sev] += 1
        statutes_affected.add(r["statute_id"])
        if r["diagnosis"] in _FINLEX_BEHIND_DIAGNOSES:
            finlex_statutes.add(r["statute_id"])

    # Non-fault breakdown
    non_fault_type_counts: Counter = Counter()
    for r in non_fault_rows:
        non_fault_type_counts[r["diagnosis"]] += 1

    print("=" * 60)
    print("LawVM Fault Summary")
    print("=" * 60)

    if cs:
        print(f"\nCorpus examined:  {total_examined:,} statutes")
        print(f"  Diverging:      {total_diverging:,}")
        print(f"  Errors:         {total_errors:,}")

    print(f"\nTotal divergent sections:  {total_divs:,}")
    print(f"  All divergences:         {len(fault_rows):,}")
    print(f"  Oracle/editorial issues: {len(non_fault_rows):,}")

    print("\nDivergence direction (critical for publication):")
    print(f"  Finlex behind replay (potential Finlex errors): {len(finlex_behind_rows):,}")
    print(f"    → statutes: {len(finlex_statutes):,}")
    print(f"  Replay behind Finlex (LawVM replay bugs):       {len(replay_behind_rows):,}")
    print(f"  Unknown direction:                              {len(unknown_rows):,}")

    if fault_rows:
        print("\nAll divergences by type:")
        for ft, cnt in sorted(fault_type_counts.items(), key=lambda x: -x[1]):
            is_finlex = ft in ("AMENDMENT_NOT_APPLIED", "SECTION_ABSENT_IN_ORACLE")
            marker = " ← Finlex error" if is_finlex else ""
            print(f"  {ft:<35} {cnt:>5,}{marker}")

        print("\nDivergences by severity:")
        for sev in (3, 2, 1):
            print(f"  sev={sev}: {sev_counts.get(sev, 0):,}")

        print(f"\nAll diverging statutes: {len(statutes_affected):,} / {total_examined:,}"
              + (f"  ({len(statutes_affected) / total_examined * 100:.1f}%)" if total_examined else ""))

    if non_fault_rows:
        print("\nOracle/editorial breakdown:")
        for diag, cnt in sorted(non_fault_type_counts.items(), key=lambda x: -x[1]):
            print(f"  {diag:<35} {cnt:>5,}")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: argparse.Namespace) -> None:
    sub = getattr(args, "faults_command", None)
    if sub is None or sub == "summary":
        _cmd_summary(args)
    elif sub == "list":
        _cmd_list(args)
    elif sub == "evidence":
        _cmd_evidence(args)
    elif sub == "export":
        _cmd_export(args)
    else:
        print(f"Unknown faults subcommand: {sub}", file=sys.stderr)
        sys.exit(1)
