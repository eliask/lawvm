#!/usr/bin/env python3
"""Classify all structural diffs in triage corpus via local LLM.

Pipes `structural-review --dump --compact` output for each statute through
a local LLM to classify each differing section into categories.

Usage:
    uv run python scripts/llm_classify_diffs.py
    uv run python scripts/llm_classify_diffs.py --corpus .tmp/diff_triage_corpus.txt --workers 4
    uv run python scripts/llm_classify_diffs.py --resume  # skip already-classified statutes
"""

import argparse
import asyncio
import csv
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

LLM_URL = "http://localhost:8080/v1/chat/completions"
DEFAULT_CORPUS = ".tmp/diff_triage_corpus.txt"
OUTPUT_FILE = ".tmp/diff_classifications.csv"
MAX_PROMPT_CHARS = 30_000  # max total chars sent to LLM per request; sections are chunked to fit

SYSTEM_PROMPT = """Olet Suomen lainsäädännön rakenneanalyytiikko. Luokittelet LawVM-järjestelmän ja Finlexin ajantasatekstin väliset erot tarkasti ja yksityiskohtaisesti."""

CLASSIFY_PROMPT = """Luokittele jokainen eroava pykälä alla olevan listan perusteella.

TULOSTE: Täsmälleen yksi rivi per eroava pykälä. Muoto:
PYKÄLÄ § | LUOKKA.ALALUOKKA | lyhyt selite

PÄÄLUOKAT ja ALALUOKAT:

N = normalisointi (kosmeettinen, ei sisältöeroa)
  N.ws = välilyönti, 1195/ 1996 vs 1195/1996
  N.bracket = hakasulkeet [ ] vs ilman
  N.punct = pisteet, pilkut, en-dash vs viiva
  N.case = isot/pienet kirjaimet

t = typo/tavutusvirhe lähteessä (alkuperäissäädöksen ongelma)
  t.ocr = OCR-artefakti: geeniteknii-kalla, Btyyppisestä
  t.hyphen = väärä tavutus tai yhdysmerkki
  t.space = puuttuva/ylimääräinen välilyönti sanassa

V = versioviive (Finlex ei ole päivittynyt)
  V.behind = Finlex näyttää vanhempaa tekstiä kuin LawVM
  V.ahead = LawVM:stä puuttuu viimeisin muutos

E = toimituksellinen (ei-normatiivinen ero)
  E.kumottu = kumottu-merkintä vs tyhjä
  E.attr = attribuutiohäntä "L:lla 123/2024"
  E.boilerplate = "Tätä kaikki noudattakoon" tai vastaava seremoniateksti
  E.ref = viittausteksti, lakiviittaus

R = rakennevirhe (LawVM:n replay-RAKENTEESSA puute tai ylimäärä — momenttien/kohtien MÄÄRÄ tai JÄRJESTYS on väärä)
  R.missing_mom = puuttuva momentti — LawVM:stä puuttuu kokonainen momentti
  R.missing_kohta = puuttuva kohta — LawVM:stä puuttuu kohta momentista
  R.missing_other = puuttuva muu rakenne-elementti (otsikko, johdantokappale)
  R.dup = LawVM toistaa momentin/kohdan kahdesti
  R.order = momentit/kohdat väärässä järjestyksessä
  R.offbyone = väärä momentti/kohta oikealla paikalla (off-by-one: esim. 2 mom paikalla 3)
  R.wrong_section = sisältö väärässä pykälässä tai luvussa
  R.partial = osittainen sisältö: momentti/kohta on olemassa mutta puutteellinen

L = lähdevirhe (alkuperäis-XML viallinen)
  L.xml = rikkinäinen XML-rakenne
  L.encoding = merkistövirhe
  L.missing = lähteestä puuttuu sisältöä

T = taulukkoero
  T.format = taulukon muotoiluero
  T.data = taulukon sisältöero
  T.missing = taulukko puuttuu kokonaan

O = LawVM-virhe (todennettavissa oleva replay-bugi — OIKEA rakenne, VÄÄRÄ sisältö)
  O.truncated = teksti katkeaa kesken lauseen (ei kokonaan puuttuva — se on R)
  O.wrong_text = oikean momentin/kohdan SISÄLLÄ väärä teksti (ei puuttuva — se on R)
  O.stray = ylimääräistä dataa (numeerinen ID, kuvateksti, jne)
  O.dup_text = toistuva fraasi ("tuoda maahan tuoda maahan")

A = viranomaisnimi-muutos (tekstikorvausmuutos jota LawVM ei sovella)
  A.agency = vanhentunut viranomaisen nimi (esim. "Vakuutusvalvontavirasto" vs "Finanssivalvonta")

X = epäselvä
  X.unclear = ei voida luokitella luotettavasti
  X.multi = useita päällekkäisiä syitä, vaikea erottaa

TÄRKEÄ PÄÄTÖSSÄÄNTÖ — R vai O:
- Jos momenttien/kohtien MÄÄRÄ eroaa (LawVM:ssä vähemmän tai enemmän) → R (rakennevirhe)
- Jos momenttien JÄRJESTYS eroaa (sisältö väärässä numerossa) → R.offbyone
- Jos momentti on OLEMASSA JA OIKEASSA PAIKASSA mutta sen TEKSTI on väärä → O
- Jos ainoa ero on viranomaisen nimi → A.agency
- Jos epäselvää kumpi → X

TARKENNUKSET:
- R.missing_mom: Kerro MITKÄ momentit puuttuvat (esim. "puuttuu 2 ja 3 mom")
- R.dup: Kerro MITKÄ toistuvat (esim. "4 mom kahdesti")
- R.offbyone: Kerro mikä on siirtynyt (esim. "2 mom sisältö 3 mom paikalla")
- O.truncated: Kerro missä teksti katkeaa (esim. "katkeaa sanaan 'saaja...'")
- O.wrong_text: Kerro lyhyesti mikä on väärin
- A.agency: Kerro vanhan ja uuden nimen (esim. "Vakuutusvalvontavirasto → Finanssivalvonta")

ESIMERKIT (oikein):
7 § | N.ws | 1195/ 1996 vs 1195/1996 välilyönti
3 § | E.kumottu | kumottu-merkintä puuttuu LawVM:stä
12 § | R.missing_mom | puuttuu 2 ja 3 momentti
5 § | N.bracket | hakasulkeet [vetäköön sakkoa] vs vetäköön sakkoa
22 § | O.wrong_text | 1 mom sisältää väärän pykälän tekstin
2 § | V.behind | Finlex ei ole päivittänyt muutosta 2024/100
8 § | t.ocr | geeniteknii-kalla vs geenitekniikalla
14 § | R.dup | 4 momentti toistuu kahdesti
6 § | R.offbyone | 2 mom sisältö 3 mom paikalla, 3 mom puuttuu
11 § | R.missing_kohta | puuttuu 3 ja 4 kohta 1 momentista
1 § | E.boilerplate | "Tätä kaikki noudattakoon" puuttuu/ylimääräinen
9 § | O.truncated | teksti katkeaa "viranomaisen, jonka..."

VÄÄRIN (ÄLÄ tee näin):
2 § 1 mom. | N | ...    ← EI momenttinumeroa pykälän perään!
30 | 2 | O               ← EI numeroa LUOKKA-sarakkeeseen!
12 § | R | puuttuva 2 momentti  ← KÄYTÄ alaluokkaa: R.missing_mom

SÄÄDÖS: {sid}
EROT:
{diffs}"""


def _get_dump(sid: str) -> str:
    """Run structural-review --dump --compact for one statute."""
    # Normalize ID: 1734/3-000 -> 1734/3
    norm_id = sid.split("-")[0] if "-" in sid else sid
    result = subprocess.run(
        ["uv", "run", "lawvm", "structural-review", norm_id, "--dump", "--compact"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout


def _parse_dump_sections(dump: str) -> list[dict]:
    """Parse dump output into per-section diff summaries."""
    sections = []
    current = None
    lines = []

    for line in dump.splitlines():
        if line.startswith("--- "):
            if current and lines:
                sections.append({"key": current, "text": "\n".join(lines)})
            # Extract section key and diff kind
            # Format: --- chapter:1/section:3 [text_only] ---
            parts = line.strip("- ").strip()
            current = parts
            lines = []
        elif line.startswith("=== "):
            continue  # header line
        elif current is not None:
            lines.append(line)

    if current and lines:
        sections.append({"key": current, "text": "\n".join(lines)})

    return sections


def _format_diffs_compact(sections: list[dict]) -> str:
    """Format sections into LLM-consumable text. No truncation."""
    parts = []
    for sec in sections:
        parts.append(f"[{sec['key']}]\n{sec['text']}")
    return "\n\n".join(parts)


async def _call_llm(
    session: aiohttp.ClientSession,
    prompt: str,
    max_tokens: int = 500,
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
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[ERROR: {e}]"


_VALID_MAIN_CATS = frozenset("NVERLTtOAX")

# Valid subcategories per main category
_VALID_SUBCATS: dict[str, frozenset[str]] = {
    "N": frozenset({"ws", "bracket", "punct", "case"}),
    "t": frozenset({"ocr", "hyphen", "space"}),
    "V": frozenset({"behind", "ahead"}),
    "E": frozenset({"kumottu", "attr", "boilerplate", "ref"}),
    "R": frozenset({"missing_mom", "missing_kohta", "missing_other", "dup", "order", "offbyone", "wrong_section", "partial"}),
    "L": frozenset({"xml", "encoding", "missing"}),
    "T": frozenset({"format", "data", "missing"}),
    "O": frozenset({"truncated", "wrong_text", "stray", "dup_text"}),
    "A": frozenset({"agency"}),
    "X": frozenset({"unclear", "multi"}),
}


def _validate_category(cat: str) -> bool:
    """Validate a category string like 'R.missing_mom' or 'N' or 'NR'."""
    if not cat:
        return False
    # Subcategory form: "R.missing_mom"
    if "." in cat:
        main, sub = cat.split(".", 1)
        return main in _VALID_MAIN_CATS and sub in _VALID_SUBCATS.get(main, frozenset())
    # Composite form: "NR", "VE" — 1-4 chars all valid main cats
    return all(c in _VALID_MAIN_CATS for c in cat) and len(cat) <= 4


def _extract_main_category(cat: str) -> str:
    """Extract main category letter(s) for backward compat aggregation."""
    if "." in cat:
        return cat.split(".")[0]
    return cat


def _parse_classifications(raw: str) -> list[dict]:
    """Parse LLM classification output into structured rows."""
    rows = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("[ERROR"):
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        section = parts[0].strip().rstrip(" §")
        category = parts[1].strip()
        code = parts[2].strip() if len(parts) >= 3 else ""

        if _validate_category(category):
            pass  # good
        else:
            # Try to recover: sometimes LLM puts momentti number in category
            # e.g. "30 | 2 | O.wrong_text" — the "2" is a momentti
            if len(parts) >= 3:
                candidate = parts[2].strip()
                if _validate_category(candidate):
                    section = f"{section} ({category})"
                    category = candidate
                    code = parts[3].strip() if len(parts) >= 4 else ""
                else:
                    code = f"parse_error: raw={line!r}"
                    category = "X"
            else:
                code = f"parse_error: raw={line!r}"
                category = "X"

        rows.append(
            {
                "section": section,
                "category": category,
                "code": code,
                "main_category": _extract_main_category(category),
            }
        )
    return rows


async def _process_statute(
    session: aiohttp.ClientSession,
    sid: str,
    sem: asyncio.Semaphore,
) -> list[dict]:
    """Process one statute: dump diffs, classify via LLM.

    If the full diff text exceeds MAX_PROMPT_CHARS, sections are split
    into chunks that each fit within the limit and classified separately.
    """
    async with sem:
        # Run dump synchronously (subprocess)
        loop = asyncio.get_event_loop()
        dump = await loop.run_in_executor(None, _get_dump, sid)

        if not dump or "VIRHE" in dump[:100]:
            return []

        sections = _parse_dump_sections(dump)
        if not sections:
            return []

        # Split sections into chunks that fit within prompt char limit.
        # The prompt template adds ~1500 chars of instructions around the diffs.
        max_diff_chars = MAX_PROMPT_CHARS - 2000
        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        current_len = 0

        for sec in sections:
            sec_text = f"[{sec['key']}]\n{sec['text']}\n\n"
            sec_len = len(sec_text)
            if current_chunk and current_len + sec_len > max_diff_chars:
                chunks.append(current_chunk)
                current_chunk = []
                current_len = 0
            current_chunk.append(sec)
            current_len += sec_len

        if current_chunk:
            chunks.append(current_chunk)

        results = []
        for chunk in chunks:
            diffs_text = _format_diffs_compact(chunk)

            # Budget tokens: base + per-section (generous)
            max_tokens = 50 + len(chunk) * 25

            prompt = CLASSIFY_PROMPT.format(sid=sid, diffs=diffs_text)

            raw = await _call_llm(session, prompt, max_tokens=max_tokens)
            classifications = _parse_classifications(raw)

            for cls in classifications:
                results.append(
                    {
                        "statute_id": sid,
                        "section": cls["section"],
                        "category": cls["category"],
                        "main_category": cls.get("main_category", _extract_main_category(cls["category"])),
                        "code": cls["code"],
                        "n_diff_sections": len(sections),
                    }
                )

        # If LLM returned nothing parseable, still record the statute
        if not results and sections:
            results.append(
                {
                    "statute_id": sid,
                    "section": "*",
                    "category": "X",
                    "code": f"unparsed_llm_response ({len(sections)} sections)",
                    "n_diff_sections": len(sections),
                }
            )

        return results


async def _run(args):
    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"ERROR: {corpus_path} not found", file=sys.stderr)
        sys.exit(1)

    sids = [line.strip() for line in corpus_path.read_text().splitlines() if line.strip()]
    print(f"Corpus: {len(sids)} statutes from {corpus_path}")

    # Resume support: skip already-classified statutes
    done_sids: set[str] = set()
    output_path = Path(args.output)
    if args.resume and output_path.exists():
        with open(output_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                done_sids.add(row["statute_id"])
        print(f"Resuming: {len(done_sids)} already classified, {len(sids) - len(done_sids)} remaining")
        sids = [s for s in sids if s not in done_sids]

    if not sids:
        print("Nothing to do.")
        return

    # Test LLM connection
    async with aiohttp.ClientSession() as session:
        test = await _call_llm(session, "Sano 'ok'.", max_tokens=5)
        if test.startswith("[ERROR"):
            print(f"LLM connection failed: {test}", file=sys.stderr)
            sys.exit(1)
        print(f"LLM connected: {test.strip()}")

        sem = asyncio.Semaphore(args.workers)

        # Open output file
        is_new = not output_path.exists() or not args.resume
        mode = "w" if is_new else "a"
        outf = open(output_path, mode, newline="")
        writer = csv.DictWriter(
            outf,
            fieldnames=[
                "statute_id",
                "section",
                "category",
                "main_category",
                "code",
                "n_diff_sections",
            ],
        )
        if is_new:
            writer.writeheader()

        t0 = time.time()
        done = 0
        skipped = 0
        total_classifications = 0

        # Process in batches to flush output periodically
        batch_size = 20
        for batch_start in range(0, len(sids), batch_size):
            batch = sids[batch_start : batch_start + batch_size]
            tasks = [_process_statute(session, sid, sem) for sid in batch]
            results = await asyncio.gather(*tasks)

            for sid, rows in zip(batch, results):
                if not rows:
                    skipped += 1
                else:
                    for row in rows:
                        writer.writerow(row)
                    total_classifications += len(rows)
                done += 1

            outf.flush()
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(sids) - done - skipped) / rate if rate > 0 else 0
            print(
                f"  [{done + skipped}/{len(sids)}] "
                f"{total_classifications} classifications, "
                f"{skipped} skipped, "
                f"{rate:.1f}/s, "
                f"ETA {eta / 60:.0f}m",
                end="\r",
            )

        outf.close()
        elapsed = time.time() - t0
        print(f"\n\nDone in {elapsed:.0f}s")
        print(f"  Statutes processed: {done}")
        print(f"  Statutes skipped (no diffs): {skipped}")
        print(f"  Classifications: {total_classifications}")
        print(f"  Output: {output_path}")

        # Quick summary
        if output_path.exists():
            with open(output_path) as f:
                reader = csv.DictReader(f)
                cats: dict[str, int] = {}
                main_cats: dict[str, int] = {}
                for row in reader:
                    c = row["category"]
                    cats[c] = cats.get(c, 0) + 1
                    mc = row.get("main_category", _extract_main_category(c))
                    main_cats[mc] = main_cats.get(mc, 0) + 1

            total = sum(cats.values())
            print(f"\nCategory distribution ({total} total):")
            print("\n  Main categories:")
            for mc in sorted(main_cats.keys(), key=lambda k: main_cats[k], reverse=True):
                print(f"    {mc:6s} {main_cats[mc]:5d}  ({100 * main_cats[mc] / total:.0f}%)")

            # Show subcategory detail for actionable categories (R, O, X)
            actionable = {c: n for c, n in cats.items() if _extract_main_category(c) in ("R", "O", "X")}
            if actionable:
                print("\n  Actionable subcategories (R/O/X):")
                for c in sorted(actionable.keys(), key=lambda k: actionable[k], reverse=True):
                    print(f"    {c:25s} {actionable[c]:5d}")
            print()


def main():
    parser = argparse.ArgumentParser(description="Classify structural diffs via local LLM")
    parser.add_argument("--corpus", default=DEFAULT_CORPUS, help="statute list file")
    parser.add_argument("--output", default=OUTPUT_FILE, help="output CSV")
    parser.add_argument("--workers", type=int, default=4, help="concurrent LLM requests")
    parser.add_argument("--resume", action="store_true", help="skip already-classified statutes")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
