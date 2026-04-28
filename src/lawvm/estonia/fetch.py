"""Riigi Teataja HTTP fetcher and act discovery utilities.

All fetched content is persisted in a content-addressed Farchive.
HTTP fetching uses curl (Cloudflare-safe) — urllib is never
used here. The archive provides deduplication, change tracking, and acts as
the permanent local cache so network access is one-shot per URL.

Default archive: data/ee_riigiteataja.farchive

Public API:
  normalize_aktviide(raw)                               → str
  fetch_rt_xml(aktViide, archive)                       → bytes
  fetch_rt_url(url, archive, max_age_hours)             → bytes
  fetch_redactions_feed(grupi_id, archive)              → List[RedactionInfo]
  extract_grupi_id(xml_bytes)                           → Optional[str]
  extract_amendment_refs(xml_bytes)                     → List[AmendmentRef]
  get_oracle_aktviide_for_pit(grupi_id, as_of, archive) → Optional[str]
  open_rt_archive(db_path)                              → Farchive
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

_BASE_URL = "https://www.riigiteataja.ee"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_DEFAULT_RT_DB = Path(__file__).parent.parent.parent.parent / "data" / "ee_riigiteataja.farchive"

_NS_BASE  = "tyviseadus_1_10.02.2010"
_NS_AMEND = "muutmisseadus_1_10.02.2010"

# RT acts are immutable — once published, never change.
_ACT_CACHE_HOURS: float = float("inf")
# Redactions feed refreshed daily (new redactions get published when RT updates).
_FEED_CACHE_HOURS: float = 24.0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class AmendmentRef:
    """One muutmismarge entry: an act that amended the base statute."""
    aktViide: str    # normalized numeric ID
    passed: str      # aktikuupaev YYYY-MM-DD (date passed by Riigikogu)
    joustumine: str  # YYYY-MM-DD when the change takes legal effect


@dataclass
class RedactionInfo:
    """One consolidated redaction from /akti_redaktsioonid.xml?grupiId=N."""
    aktViide: str    # normalized numeric ID
    title: str       # e.g. "Kohtute seadus (23.03.2019)"
    effective: str   # YYYY-MM-DD extracted from title parenthetical


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_aktviide(raw: str) -> str:
    """Normalize aktViide to a plain numeric ID.

      "http://www.riigiteataja.ee/akt/24368" → "24368"
      "https://www.riigiteataja.ee/akt/113032019003" → "113032019003"
      "113032019003" → "113032019003"
    """
    raw = raw.strip()
    if "riigiteataja.ee/akt/" in raw:
        return raw.split("/akt/")[-1].strip("/").strip()
    return raw


def _parse_date(s: Optional[str]) -> str:
    if not s:
        return ""
    s = re.sub(r'[+\-]\d{2}:\d{2}$', '', s.strip())
    return s[:10]


def _dd_mm_yyyy_to_iso(s: str) -> str:
    m = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', s.strip())
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return ""


_RIIGIKOGU_TERM_START_DATES = {
    "XIII": "2015-03-24",
}


def _partial_commencement_dates(muutm: ET.Element) -> list[str]:
    """Extract explicit delayed partial-effect slices from muutmismarge metadata."""
    note_text = "".join(str(text) for text in muutm.itertext()).replace("\xa0", " ")
    note_text = re.sub(r"\s+", " ", note_text).strip()
    dates: list[str] = []
    for match in re.finditer(
        r"\bosaliselt\s+((?:\d{1,2}\.\d{1,2}\.\d{4})(?:\s*(?:,|ja)\s*\d{1,2}\.\d{1,2}\.\d{4})*)\b",
        note_text,
        flags=re.IGNORECASE,
    ):
        for raw_date in re.findall(r"\d{1,2}\.\d{1,2}\.\d{4}", match.group(1)):
            iso_date = _dd_mm_yyyy_to_iso(raw_date)
            if iso_date and iso_date not in dates:
                dates.append(iso_date)
    for match in re.finditer(
        r"\bosaliselt\s+Riigikogu\s+([IVXLCDM]+)\s+koosseisu\s+volituste\s+algusest\b",
        note_text,
        flags=re.IGNORECASE,
    ):
        iso_date = _RIIGIKOGU_TERM_START_DATES.get(match.group(1).upper(), "")
        if iso_date and iso_date not in dates:
            dates.append(iso_date)
    return dates


def open_rt_archive(db_path: Optional[Path] = None, *, readonly: bool = False) -> Any:
    """Open the Riigi Teataja fetch archive.

    Use ``readonly=True`` for reporting and cache-only tooling that must not
    trigger WAL or write-path initialization.
    """
    from farchive import Farchive
    return Farchive(db_path or _DEFAULT_RT_DB, readonly=readonly)


# ---------------------------------------------------------------------------
# Core fetch — curl-only, stored in Farchive
# ---------------------------------------------------------------------------

def fetch_rt_url(
    url: str,
    archive: Any = None,
    max_age_hours: float = _ACT_CACHE_HOURS,
) -> bytes:
    """Fetch any RT URL via curl, caching in archive.

    Returns cached content if fresh enough (has check); otherwise
    re-fetches with curl and stores the result. Raises RuntimeError on failure.
    """
    _archive = archive or open_rt_archive()

    # Return cached content if still fresh
    if not _is_inf(max_age_hours):
        if _archive.has(url, max_age_hours=max_age_hours):
            data = _archive.get(url)
            if data and not _looks_like_html(data):
                return data
    else:
        # Infinite TTL: return whatever we have in archive (immutable acts)
        data = _archive.get(url)
        if data and not _looks_like_html(data):
            return data

    # Fetch via curl
    data = _curl(url, _archive)
    if data and not _looks_like_html(data):
        return data

    raise RuntimeError(f"Failed to fetch: {url}")


def fetch_rt_xml(aktViide: str, archive: Any = None) -> bytes:
    """Fetch act XML by aktViide (immutable — cached forever after first fetch)."""
    aid = normalize_aktviide(aktViide)
    url = f"{_BASE_URL}/akt/{aid}.xml"
    return fetch_rt_url(url, archive, max_age_hours=_ACT_CACHE_HOURS)


def _is_inf(v: float) -> bool:
    import math
    return math.isinf(v)


def _looks_like_html(data: bytes) -> bool:
    prefix = data[:300].lower()
    return b"<html" in prefix or b"<!doctype" in prefix


def _curl(url: str, archive: Any) -> Optional[bytes]:
    """Fetch URL via curl and store in archive. Returns bytes or None."""
    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        result = subprocess.run(
            ["curl", "-s", "-A", _UA, "-L", "--max-time", "30",
             "-o", str(tmp_path), url],
            capture_output=True,
        )
        if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size < 50:
            return None
        data = tmp_path.read_bytes()
        archive.store(url, data)
        return data
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Redactions feed
# ---------------------------------------------------------------------------

def fetch_redactions_feed(
    grupi_id: str,
    archive: Any = None,
) -> List[RedactionInfo]:
    """Fetch /akti_redaktsioonid.xml?grupiId=N and parse all redactions.

    Lists all consolidated (terviktekst) versions of an act, with effective
    dates in the title parenthetical "(DD.MM.YYYY)". Cached for 24 hours.

    Returns list sorted by effective date descending (newest first).
    """
    url = f"{_BASE_URL}/akti_redaktsioonid.xml?grupiId={grupi_id}"
    try:
        rss_bytes = fetch_rt_url(url, archive, max_age_hours=_FEED_CACHE_HOURS)
    except RuntimeError:
        return []

    text = rss_bytes.decode("utf-8", errors="replace")
    redactions: List[RedactionInfo] = []
    for item in re.findall(r"<item>(.*?)</item>", text, re.DOTALL):
        title_m = re.search(r"<title>(.*?)</title>", item)
        id_m    = re.search(r"riigiteataja\.ee/akt/(\d+)", item)
        if not (title_m and id_m):
            continue
        title = title_m.group(1).strip()
        aid   = id_m.group(1)
        date_m = re.search(r"\((\d{1,2}\.\d{1,2}\.\d{4})\)", title)
        effective = _dd_mm_yyyy_to_iso(date_m.group(1)) if date_m else ""
        redactions.append(RedactionInfo(aktViide=aid, title=title, effective=effective))

    redactions.sort(key=lambda r: r.effective, reverse=True)
    return redactions


# ---------------------------------------------------------------------------
# Metadata extraction from act XML
# ---------------------------------------------------------------------------

def extract_grupi_id(xml_bytes: bytes) -> Optional[str]:
    """Extract terviktekstiGrupiID from act XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "terviktekstiGrupiID" and el.text:
            return el.text.strip()
    return None


def extract_effective_date(xml_bytes: bytes) -> str:
    """Extract the effective date (kehtivuseAlgus) from act XML.

    Returns ISO date string YYYY-MM-DD, or '' if not found.
    Used to determine the base terviktekst's own effective date so that
    only NEWER amendments (joustumine > base_effective) are applied.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "kehtivuseAlgus" and el.text:
            return _parse_date(el.text)
    return ""


def extract_tekstiliik(xml_bytes: bytes) -> str:
    """Return tekstiliik value: 'terviktekst', 'algtekst', etc."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "tekstiliik" and el.text:
            return el.text.strip()
    return ""


# ---------------------------------------------------------------------------
# Algtekst (original enactment) discovery
# ---------------------------------------------------------------------------

def extract_rt_pub_ref(xml_bytes: bytes) -> str:
    """Extract the RT publication reference string from act XML metadata.

    Returns e.g. "RT I 2002, 64, 390" or "" if not found.
    Parses RTosa / RTaasta / RTnr / RTartikkel metadata elements.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    osa = aasta = nr = art = ""
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "RTosa" and not osa:
            osa = (el.text or "").strip()
        elif tag == "RTaasta" and not aasta:
            aasta = (el.text or "").strip()
        elif tag == "RTnr" and not nr:
            nr = (el.text or "").strip()
        elif tag == "RTartikkel" and not art:
            art = (el.text or "").strip()
    if aasta and nr and art:
        prefix = f"RT {osa}" if osa else "RT"
        return f"{prefix} {aasta}, {nr}, {art}"
    return ""


def find_algtekst_aktviide(
    grupi_id: str,
    archive: Any = None,
    probe_below: Optional[str] = None,
) -> Optional[str]:
    """Try to find the original algtekst aktViide for a statute group.

    Strategy:
      1. If the oldest entry in the redactions feed has a very early effective date
         that predates all muutmismarge amendments, it may already be the algtekst.
      2. Probe sequential IDs below ``probe_below`` (if provided) looking for an act
         in the same terviktekstiGrupiID group.
      3. Returns None if algtekst cannot be found — this is expected for statutes
         whose original XML was not digitized by RT (common for pre-2010 acts).

    Note:
      The RT digital XML archive (tyviseadus_1_10.02.2010 schema) was established
      around 2010. Original enactments from before ~2003 often have no XML available.
      For such statutes, the oldest available terviktekst is the best starting point.

    Args:
        grupi_id:    terviktekstiGrupiID to search within.
        archive:     Farchive for caching.
        probe_below: Optional aktViide (numeric string). If provided, probes
                     sequential IDs from probe_below-1 downward (up to 2000 attempts)
                     looking for an act with matching grupi_id.

    Returns:
        aktViide string (normalized), or None.
    """
    _archive = archive or open_rt_archive()

    if probe_below:
        # Probe sequential IDs just below the given boundary (typically the
        # first known amendment's aktViide).  Stop after finding one match or
        # exhausting the search range.
        start = int(probe_below) - 1
        for step in range(0, 2001, 10):
            candidate = str(start - step)
            if int(candidate) <= 0:
                break
            try:
                xml = fetch_rt_xml(candidate, _archive)
            except RuntimeError:
                continue  # ID doesn't exist / not XML
            found_grupi = extract_grupi_id(xml)
            if found_grupi == grupi_id:
                tl = extract_tekstiliik(xml)
                if tl in ("algtekst", ""):
                    return candidate
                # Found a terviktekst with the right grupiId — keep looking for algtekst
            elif found_grupi:
                pass  # different statute; keep probing
        return None

    return None


def extract_amendment_refs(xml_bytes: bytes) -> List[AmendmentRef]:
    """Extract all muutmismarge entries from an RT act XML.

    Returns list sorted by joustumine ascending (chronological apply order).
    RT metadata may encode additional delayed partial commencements in note text
    such as ", osaliselt 01.01.2026"; each explicit slice is preserved.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    ns = root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else _NS_BASE

    refs: List[AmendmentRef] = []
    for muutm in root.findall(f"{{{ns}}}muutmismarge"):
        kuup_el = muutm.find(f"{{{ns}}}aktikuupaev")
        passed  = _parse_date(kuup_el.text) if kuup_el is not None else ""

        joust_el = muutm.find(f"{{{ns}}}joustumine")
        joust    = _parse_date(joust_el.text) if joust_el is not None else ""

        avmark = muutm.find(f"{{{ns}}}avaldamismarge")
        if avmark is None:
            continue
        avi_el = avmark.find(f"{{{ns}}}aktViide")
        if avi_el is None or not avi_el.text:
            continue

        aid = normalize_aktviide(avi_el.text.strip())
        if not aid:
            continue

        effective_dates = [joust or passed]
        for extra_date in _partial_commencement_dates(muutm):
            if extra_date not in effective_dates:
                effective_dates.append(extra_date)

        for effective_date in effective_dates:
            refs.append(AmendmentRef(
                aktViide=aid,
                passed=passed,
                joustumine=effective_date,
            ))

    refs.sort(key=lambda r: r.joustumine)
    return refs


# ---------------------------------------------------------------------------
# PIT oracle selection
# ---------------------------------------------------------------------------

def get_oracle_aktviide_for_pit(
    grupi_id: str,
    as_of: str,
    archive: Any = None,
) -> Optional[str]:
    """Return aktViide of the consolidated redaction active at as_of.

    Picks the latest redaction whose effective date <= as_of.
    Returns None if no such redaction exists (as_of predates all redactions).
    """
    redactions = fetch_redactions_feed(grupi_id, archive)
    ascending  = sorted(redactions, key=lambda r: r.effective)
    result: Optional[str] = None
    for r in ascending:
        if r.effective and r.effective <= as_of:
            result = r.aktViide
    return result
