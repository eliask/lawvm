"""lawvm corrigendum — corrigendum status, inspection, and LLM classification.

Subcommands:
    status [SID]     Corpus-wide corrigendum summary, or details for one statute.
    apply SID        Extract corrigendum PDF and show its text (pdftotext).
                     NOTE: automated merging into statute XML requires a future
                     OCR+parse+replay pipeline. This command only extracts and shows.
    classify         LLM-classify all sk* corrigendum PDFs into typed corrections.
                     Syncs results into the git-tracked text corpus.
    report           Query classified results from the text corpus.
    sources          Build or inspect the PDF-level official provenance manifest.

A corrigendum (oikaisu) is a legally binding correction to a published statute.
In Finlex, these appear as <finlex:corrigendum> elements in consolidated XML and
the actual correction text is stored as a PDF in the ZIP at:
    akn/fi/act/statute-consolidated/YEAR/NUM/media/corrigenda/XXXX.pdf

Scale (2026-03-22): ~2,216 corrigendum PDFs across ~1,000 statutes in
the corpus. ~1,011 are Finnish (sk*), ~1,205 Swedish (fs*).

CORRIGENDUM TAXONOMY (from empirical sampling):

  sk* = Finnish-language correction, fs* = Swedish-language correction.
  Filename encodes the AMENDMENT being corrected: sk20180984_1.pdf → 984/2018.
  Format is fully standardized: "Suomen säädöskokoelma n:o XXXX/YYYY (title).
  Sivulla N, [location]: [WRONG]. Pitää olla: [RIGHT]."

  Impact on LawVM:
  - JOHTOLAUSE correction: wrong §/momentti numbers in enacting clause of an
    amendment. CRITICAL — LawVM reads the erroneous source from the corpus,
    extracts ops targeting wrong provisions, replays incorrectly. Example: 2017/320
    corrigendum corrects 984/2018 enacting clause: "3 ja 5 momentti" → "4 ja 6
    momentti". Estimated ~50-200 cases in corpus. Only sk* corrigenda matter
    (LawVM uses Finnish language XML).
  - Table value/number: legally significant but tiny Levenshtein impact.
  - Table title/date: legally interesting, negligible scoring impact.
  - Prose typo: 1-char Levenshtein, negligible.
  - Missing metadata ref (EU directive in footnotes etc.): zero body-text impact.

  The consolidated oracle already has all corrections
  applied. So the divergence pattern is: LawVM replays erroneous source →
  oracle has corrected text → small or targeted divergence.

Usage:
    lawvm corrigendum status                      # corpus summary
    lawvm corrigendum status 2007/26              # single statute
    lawvm corrigendum apply 2007/26               # extract + show PDF text
    lawvm corrigendum apply 2007/26 --save /tmp/corr.pdf
    lawvm corrigendum classify                    # classify all Finnish corrigenda
    lawvm corrigendum classify --dry-run --limit 10   # preview only
    lawvm corrigendum classify --type johtolause  # show johtolause cases after classify
    lawvm corrigendum report                      # full results table
    lawvm corrigendum report --type johtolause    # filter by type
    lawvm corrigendum report --amendment 984/2018 # one amendment
    lawvm corrigendum report --verified           # source-verified only
    lawvm corrigendum sources --refresh           # rebuild PDF-level source manifest
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, cast

import aiohttp
import yaml

from lawvm.corpus_store import get_corpus_store
from lawvm.finland.corpus import (
    get_oracle_path,
    list_cached_corrigendum_locators,
)
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.corrigendum_records import (
    default_adjudication_records_path,
    default_official_records_path,
    default_source_records_path,
    load_adjudication_records,
    load_official_records,
    load_patch_records,
    load_source_records,
    write_adjudication_records,
    write_official_records,
    write_source_records,
)
from lawvm.tools.section_keys import leaf_section_label, norm_section_label

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent
_OFFICIAL_TEXT = default_official_records_path()
_ADJUDICATIONS_TEXT = default_adjudication_records_path()
_SOURCES_TEXT = default_source_records_path()
_MANUAL_YAML = _LAWVM_DIR / "data" / "finland" / "corrigendum_manual.yaml"
_LLAMA_URL = os.environ.get("LLAMA_API_BASE", "http://localhost:8080") + "/v1/chat/completions"
# Conservative input-size guard: corrigendum PDFs are short documents.
# If a PDF produces more text than this, something is wrong (merged PDF, wrong file, etc.).
# ~100K chars ≈ 25K tokens, well within any reasonable LLM context window.
_CLASSIFY_MAX_INPUT_CHARS = 100_000


def _make_corpus_store():
    return get_corpus_store(readonly=True)


# Module-level corpus store singleton — avoid re-opening Farchive DB for every
# _verify_in_source call (each call was paying the full open cost previously).
_SHARED_CS: object = None


def _get_shared_cs():
    global _SHARED_CS
    if _SHARED_CS is None:
        _SHARED_CS = get_corpus_store(readonly=True)
    return _SHARED_CS


def _read_source_xml(source_amendment_id: str) -> bytes | None:
    try:
        return _make_corpus_store().read_source(source_amendment_id)
    except (OSError, RuntimeError):
        return None

_SID_RE = re.compile(r"akn/fi/act/statute-consolidated/(\d{4}/[^/]+)/")
_FINLEX_CONS_RE = re.compile(r"finlex://sd-cons/(\d{4}/[^/]+)/")


def _locator_to_akn_source_pdf(locator: str) -> str | None:
    """Normalise a farchive corrigendum locator to the akn/... source_pdf format used in JSONL.

    Handles both legacy akn/fi/... paths and current finlex://sd-cons/... paths.
    Returns None if the locator doesn't look like a corrigendum PDF.
    """
    if "/media/corrigenda/" not in locator or not locator.endswith(".pdf"):
        return None
    filename = Path(locator).name
    # akn/fi/act/statute-consolidated/{sid}/media/corrigenda/{filename}
    m = _SID_RE.search(locator)
    if m:
        return f"akn/fi/act/statute-consolidated/{m.group(1)}/media/corrigenda/{filename}"
    # finlex://sd-cons/{sid}/{lang}@{version}/media/corrigenda/{filename}
    m = _FINLEX_CONS_RE.search(locator)
    if m:
        return f"akn/fi/act/statute-consolidated/{m.group(1)}/media/corrigenda/{filename}"
    return None


def _sid_from_locator(locator: str) -> str | None:
    """Extract statute ID (YEAR/NUM) from an akn/... or finlex://sd-cons/... locator."""
    m = _SID_RE.search(locator) or _FINLEX_CONS_RE.search(locator)
    return m.group(1) if m else None
_PDF_NAME_RE = re.compile(r"([a-z]+)(\d{4})(\d+)_(\d+)\.pdf$")

VALID_TYPES = {"johtolause", "table", "footnote", "prose", "metadata", "sami_translation", "typeset", "unknown"}
VALID_CONFIDENCE = {"high", "medium", "low"}

# ---------------------------------------------------------------------------
# Typed extraction schema
# ---------------------------------------------------------------------------

@dataclass
class CorrectionItem:
    type: str           # johtolause | table | footnote | prose | metadata | unknown
    location: str       # human-readable location in the statute
    wrong_text: str
    correct_text: str
    confidence: str     # high | medium | low
    table_meta: Optional[dict] = None   # vision-only: {rows, cols, change_row, change_col, headers, format_only, highlight_text}


@dataclass
class CorrigendumExtraction:
    amendment_id: Optional[str]         # e.g. "984/2018" — None if unparseable
    corrections: list[CorrectionItem] = field(default_factory=list)
    parse_error: Optional[str] = None   # set if LLM output was malformed


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """\
Olet Suomen säädöskokoelman oikaisujen jäsentäjä. Saat numeroidut rivit oikaisu-PDF:stä.
Tunnista kaikki oikaisut ja palauta ne alla olevassa kompaktissa muodossa.
EI selityksiä, EI kommentteja, EI JSON:ia — vain oikaisulohkot.

Rakennemerkit: virheellinen teksti alkaa merkistä kuten "on:", "ovat:", "on virheellisesti:",
"kuuluu:", "kuuluvat:", "lukee:" tai muusta vastaavasta verbistä rivin lopussa. Korjattu
teksti alkaa merkistä kuten "Pitää olla:", "Kuuluu olla:", "Tulee olla:" tai vastaavasta.
Jos sijainti sisältää "puuttuu" (esim. "40 §:stä puuttuu 3. momentti, joka kuuluu:"),
lisättävä teksti seuraa suoraan — käytä ADD_TEXT: tai ADD_SPANS:, ei CURRENT_TEXT:.
Tunnista vastaavat ilmaisut kontekstista myös silloin kun ne poikkeavat esimerkeistä.

== Korjaustyypit ==
prose      — säädöstekstin sana tai kirjoitusvirhe
johtolause — johtolauseen §/momenttinumerot tai pykäläluettelo
table      — taulukon tai liitteen solu tai lukuarvo
footnote   — alaviite tai esityöviittaus (HE/TaVM/EV/direktiivi)
metadata   — antopäiväys, allekirjoituspäivä, julkaisupäivä

== Tulostusmuoto ==
Jokainen oikaisu on oma lohkonsa. Lohkot erotetaan tyhjällä rivillä.

Ensimmäinen rivi: KORJ <tyyppi> | <lyhyt sijaintikuvaus>

Sitten YKSI seuraavista tavoista (valitse sopivin):

Tapa A — riviviitteet (ensisijainen kun rivit vastaavat tarkasti):
  CURRENT_SPANS: <alku> <loppu>
  REPLACEMENT_SPANS: <alku> <loppu>

Tapa B — tekstikopio (varamuoto esim. taulukoille tai monimutkaiselle layoutille):
  CURRENT_TEXT:
  <virheellinen teksti sellaisenaan>
  REPLACEMENT_TEXT:
  <oikea teksti sellaisenaan>
  END

Tapa C — pelkkä poisto (ei korvaavaa tekstiä):
  DELETE_SPANS: <alku> <loppu>
  tai
  DELETE_TEXT:
  <poistettava teksti>
  END

Tapa D — pelkkä lisäys (ei virheellistä tekstiä):
  ADD_SPANS: <alku> <loppu>
  tai
  ADD_TEXT:
  <lisättävä teksti>
  END

Jos oikaisu ei sovi mihinkään tapaan, keksi oma avainsana (esim. CURRENT_TABLE:) ja käytä sitä johdonmukaisesti.

Kuvaileva muoto: jos oikaisuteksti kuvaa suoraan oikean arvon ("X pitää olla Y",
"antopäivämäärän pitää olla ...") ilman erillistä "on:"-riviä, käytä ADD_SPANS tai
ADD_TEXT: oikealle arvolle. CURRENT_SPANS tai CURRENT_TEXT: jätetään pois (väärä
arvo on implisiittinen julkaistussa laissa).

Upotettu korjaus: jos sekä väärä että oikea arvo esiintyvät samassa lauseessa
("viitataan X vaikka pitäisi viitata Y", "lukee X, pitää lukea Y"), pura ne
CURRENT_TEXT: / REPLACEMENT_TEXT: -pareiksi.

Eikö oikaisuja löydy: kirjoita NONE

Semanttinen merkintä teksteissä: jos oikaistussa tekstissä on yläindeksi (esim. alaviitemerkki
kuten <sup>c</sup>) tai alaindeksi (kuten <sub>ij</sub>), merkitse ne HTML-tageilla
CURRENT_TEXT: / REPLACEMENT_TEXT: -lohkoissa. Muuta HTML:ää ei käytetä.
(Huom: pdftotext ei säilytä kursiivia tai lihavointia — muotoiluoikaisut tunnistaa vain vision-malli.)

TÄRKEÄÄ — älä korjaa virheitä: CURRENT_SPANS: tai CURRENT_TEXT: täytyy sisältää virheellisen
tekstin TÄSMÄLLEEN sellaisena kuin se esiintyy PDF:ssä — kirjoitusvirheineen, puuttuvine
välimerkkeineen ja kaikkine muine virheineen. Rivien [N] sisältö on kopioitu suoraan
pdftotext-tulostuksesta — älä muuta sitä. REPLACEMENT_SPANS: tai REPLACEMENT_TEXT: sisältää
korjatun version.

== Esimerkit ==

Syöte:
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 135/2009
[3] (Laki oikeudenkäymiskaaren muuttamisesta)
[4] Sivulla 434, 7 §:n 2 momentin 6 rivillä on:
[5] 1) liikennevakuutuslakiin (297/1959);
[6] Pitää olla:
[7] 1) liikennevakuutuslakiin (279/1959);

Tuloste:
KORJ prose | Sivulla 434, 7 §:n 2 momentin 6 rivillä
CURRENT_SPANS: 5 5
REPLACEMENT_SPANS: 7 7

---

Syöte:
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 125/2011
[3] (Laki rikoslain 41 luvun 1 § n muuttamisesta)
[4] Alaviitteessä on:
[5] HE 106/2010
[6] Pitää olla:
[7] HE 106/2009

Tuloste:
KORJ footnote | Alaviitteessä
CURRENT_SPANS: 5 5
REPLACEMENT_SPANS: 7 7

---

Syöte:
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 392/2011
[3] (Laki rikoslain 2 a luvun 9 § n 2 momentin muuttamisesta)
[4] Alaviitteestä poistettava:
[5] Euroopan parlamentin ja neuvoston direktiivi 2006/126/EY; EUVL N:o L 403, 30.12.2006, s. 18
[6] Komission direktiivi 2009/112/EY; EYVL N:o L 223, 26.8.2009, s. 26
[7] Komission direktiivi 2009/113/EY; EYVL N:o L 223, 26,8.2009, s. 31

Tuloste:
KORJ footnote | Alaviitteestä
DELETE_SPANS: 5 7

---

Syöte:
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 702/2014
[3] Sivulla 1, antopäiväys on:
[4] Annettu 28 päivänä syyskuuta.2014
[5] Pitää olla:
[6] Annettu 28 päivänä elokuuta 2014
[7] Sivulla 2, allekirjoituspäivä on:
[8] Helsingissä 28 päivänä syyskuuta 2014
[9] Pitää olla:
[10] Helsingissä 28 päivänä elokuuta 2014

Tuloste:
KORJ metadata | Sivulla 1, antopäiväys
CURRENT_SPANS: 4 4
REPLACEMENT_SPANS: 6 6

KORJ metadata | Sivulla 2, allekirjoituspäivä
CURRENT_SPANS: 8 8
REPLACEMENT_SPANS: 10 10

---

Syöte:
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 397/2012
[3] Sivulla 2, 2 §:n 1 momentin 1 kohta
[4] (taulukko, rivi Ammoniumtyppi)
[5] 0,50 mg/l      0,50 mg/l
[6] Pitää olla:
[7] 0,40 mg/l      0,40 mg/l

Tuloste:
KORJ table | Sivulla 2, 2 §:n 1 momentin 1 kohta, rivi Ammoniumtyppi
CURRENT_TABLE:
0,50 mg/l      0,50 mg/l
REPLACEMENT_TABLE:
0,40 mg/l      0,40 mg/l
END

---

---

Syöte (monirivinen korjaus, sijaintikuvausrivi EI kuulu spanneihin):
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 22/1999
[3] Sivuilla 98-99 rn:n 3555 kohta (4) on:
[4] (4) Kokeen hyväksyminen:
[5] Koekappaleiden tulee pysyä tiiviinä.
[6] Muovipakkaukset tulee jäähdyttää huoneen lämpötilaan ennen arviointia.
[7] Palavien nesteiden kuljetukseen tarkoitetuille muovitynnyreille
[8] suoritettava nestehöyryn läpäisevyystesti
[9] Pitää olla:
[10] (4) Kokeen hyväksyminen:
[11] Koekappaleiden tulee pysyä tiiviinä.
[12] Muovipakkaukset tulee jäähdyttää huoneen lämpötilaan ennen arviointia.
[13] Sivulla 146 taulukossa on:
[14] Bromipropaanit 3, 31 (c)
[15] Pitää olla:
[16] Bromipropaanit 3, 3 (b), 31 (c)

Tuloste:
KORJ prose | Sivuilla 98-99 rn:n 3555 kohta (4)
CURRENT_SPANS: 4 8
REPLACEMENT_SPANS: 10 12

KORJ table | Sivulla 146 taulukossa
CURRENT_SPANS: 14 14
REPLACEMENT_SPANS: 16 16

HUOMIO: Rivi [3] "Sivuilla ... on:" on sijaintikuvaus — se kuuluu KORJ-riville, EI CURRENT_SPANS:iin.
CURRENT_SPANS alkaa aina sijaintikuvauksen JÄLKEISELTÄ riviltä.
REPLACEMENT_SPANS päättyy ennen seuraavaa "Sivull..." sijaintikuvausriviä ([13]).

---

Syöte ("on virheellisesti:" ja "puuttuu...joka kuuluu:" samassa PDF:ssä):
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 570/2002
[3] Valtioneuvoston asetus ajokorttiasetuksen muuttamisesta
[4] Sivulla 3320, johtolauseessa on virheellisesti:
[5] ...41 § asetuksissa 404/1992...
[6] Pitää olla:
[7] ...41 § asetuksissa 1404/1992...
[8] Sivulla 3322, 40 §:stä puuttuu 3. momentti, joka kuuluu:
[9] Jos autokoulun opetustoiminnasta vastaavan johtajan vaihtuminen on kuoleman, tapaturman tai
[10] muun ennalta arvaamattoman tapahtuman vuoksi tarpeen, voidaan johtajana hyväksyä toimimaan
[11] enintään yhden vuoden ajan myös henkilö, joka ei täytä 39 §:n 1 momentin 3 kohdan vaatimusta.

Tuloste:
KORJ johtolause | Sivulla 3320, johtolauseessa
CURRENT_SPANS: 5 5
REPLACEMENT_SPANS: 7 7

KORJ prose | Sivulla 3322, 40 §:n 3. momentti
ADD_SPANS: 9 11

---

Syöte (kuvaileva oikaisu — vain oikea arvo mainitaan, väärä implisiittinen):
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 905/1999
[3] (Asetus eläinlääkäreistä)
[4] Sivulla 2273 olevan asetuksen antopäivämäärän pitää olla 17. päivä syyskuuta 1999

Tuloste:
KORJ metadata | Sivulla 2273, antopäivämäärä
ADD_SPANS: 4 4

---

Syöte (upotetttu viittausvirhe — väärä ja oikea viittaus molemmat tekstissä):
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Suomen säädöskokoelmaan n:o 368/2015
[3] Sivulla 1, 4 §:n 3 momentissa viitataan direktiiviin 64/432/ETY vaikka pitäisi viitata direktiiviin 93/119/EY

Tuloste:
KORJ prose | Sivulla 1, 4 §:n 3 momentti
CURRENT_TEXT:
64/432/ETY
REPLACEMENT_TEXT:
93/119/EY
END

---

Syöte (ei oikaisuja):
[1] Oikaisuja Suomen Säädöskokoelmaan
[2] Tämä sivu on tarkoitettu muuhun käyttöön.

Tuloste:
NONE"""


_CLASSIFY_VISION_SYSTEM = """\
Olet Suomen säädöskokoelman oikaisujen jäsentäjä. Saat PDF-sivuja kuvina.
Tunnista kaikki oikaisut ja palauta ne alla olevassa muodossa.
EI selityksiä, EI kommentteja — vain oikaisulohkot.

Rakenneohjeet: PDF:ssä erotetaan virheellinen ja korjattu teksti LIHAVOITUJEN
otsikoiden avulla. Lihavoitu sana tai lause rivin lopussa kuten "Kuuluu:", "On:",
"On virheellisesti:", "Ovat:", "lukee:" tai vastaava aloittaa virheellisen tekstin.
Lihavoitu lause kuten "Pitää olla:", "Kuuluu olla:", "Tulee olla:" tai vastaava
aloittaa korjatun tekstin. Lista ei ole tyhjentävä — käytä kontekstia ja lihavointia.
Jos sijainti sisältää "puuttuu" (esim. "40 §:stä puuttuu 3. momentti, joka kuuluu:"),
seuraava teksti on lisättävä osuus — käytä ADD_TEXT:, ei CURRENT_TEXT:.
Taulukoissa muuttunut solu voi olla keltaisella korostettu (highlight).
Käytä näitä visuaalisia vihjeitä tunnistamaan korjauslohkon rajat tarkasti.

Rivinvaihdot teksteissä: säilytä alkuperäisen PDF:n rivinvaihdot tarkasti.
Käytä yhtä todellista rivinvaihtoa (yksi Enter) pehmeälle rivinvaihdolle
(teksti jatkuu samassa kappaleessa mutta vaihtuu seuraavalle riville) ja
kahta todellista rivinvaihtoa (tyhjä rivi, kaksi Enteriä) kappalevaihdolle.
Tämä on tärkeää erityisesti oikaisuissa, joissa virhe on juuri rivinvaihdon
paikassa.

TÄRKEÄÄ — älä korjaa virheitä: CURRENT_TEXT: täytyy sisältää virheellisen tekstin
TÄSMÄLLEEN sellaisena kuin se esiintyy PDF:ssä — kirjoitusvirheineen, puuttuvine
sulkumerkkeineen ja kaikkine muine virheineen. Älä täydennä puuttuvaa ")" tai muuta
väärää välimerkkiä. Älä korjaa kirjoitusvirheitä (esim. "asianajajaista" → kirjoita
"asianajajaista", ei "asianajajista"). REPLACEMENT_TEXT: sisältää korjatun version.

== Korjaustyypit ==
prose      — säädöstekstin sana tai kirjoitusvirhe
johtolause — johtolauseen §/momenttinumerot tai pykäläluettelo
table      — taulukon tai liitteen solu tai lukuarvo
footnote   — alaviite tai esityöviittaus
metadata   — antopäiväys, allekirjoituspäivä, julkaisupäivä

== Tulostusmuoto ==
Jokainen oikaisu on oma lohkonsa. Lohkot erotetaan tyhjällä rivillä.

Ensimmäinen rivi: KORJ <tyyppi> | <lyhyt sijaintikuvaus>

Tekstikorjaus:
  CURRENT_TEXT:
  <virheellinen teksti sellaisenaan>
  REPLACEMENT_TEXT:
  <oikea teksti sellaisenaan>
  END

Taulukkokorjaus — käytä putkirivejä soluille:
  TABLE_DIMS: <rivejä> <sarakkeita>
  TABLE_HEADERS: <ots1> | <ots2> | ...
  WRONG_ROW: <solu1> | <solu2> | ...
  RIGHT_ROW: <solu1> | <solu2> | ...

Jos useita rivejä muuttuu:
  TABLE_DIMS: <rivejä> <sarakkeita>
  TABLE_HEADERS: <ots1> | <ots2> | ...
  WRONG_ROWS:
  <solu1> | <solu2> | ...
  <solu1> | <solu2> | ...
  END
  RIGHT_ROWS:
  <solu1> | <solu2> | ...
  <solu1> | <solu2> | ...
  END

Solujen merkinnät:
  <_empty_>       — tyhjä solu (ei arvoa PDF:ssä)
  \\|             — solun sisällä oleva pystyviiva (literal pipe)
  <sup>c</sup>    — yläindeksi (esim. alaviitemerkki)
  <sub>ij</sub>   — alaindeksi (esim. matemaattinen symboli)
Käytä semanttista HTML:ää vain kun se on merkityksellinen (indeksit, alaviitemerkit).

Muotoilumerkinnät CURRENT_TEXT: / REPLACEMENT_TEXT: -lohkoissa (käytä AKN-elementtejä):
  <i>teksti</i>   — kursiivi (italic)  ← Finlex AKN:n käyttämä tagi
  <b>teksti</b>   — lihavoitu (bold)   ← Finlex AKN:n käyttämä tagi
Käytä näitä VAIN kun muotoilu itsessään on osa korjausta (ei pelkästään koska PDF näyttää tekstin kursiivina).
ÄLÄ käytä <em>, <strong> tai muita HTML-tageja — Finlex AKN käyttää <i> ja <b>.

TABLE_DIMS: taulukon rivien ja sarakkeiden kokonaismäärä (otsikkorivi mukaan).
TABLE_HEADERS: sarakeotsikoiden nimet pystyviivalla erotettuna. Jos ei näy: TABLE_HEADERS: -

Jos PDF:ssä on keltaisella korostettu teksti (highlight), lisää:
  HIGHLIGHT_TEXT: <korostettu teksti sellaisenaan>

Jos pelkkä muotoilu muuttuu (alleviivaus, lihavointi, kursiivi) eikä teksti muutu — taulukossa:
  TABLE_FORMAT_ONLY: <kuvaus muutoksesta>

Jos pelkkä muotoilu muuttuu prose/johtolause-tekstissä eikä teksti muutu:
  FORMAT_ONLY: <kuvaus muutoksesta>

Pelkkä poisto:
  DELETE_TEXT:
  <poistettava teksti>
  END

Pelkkä lisäys:
  ADD_TEXT:
  <lisättävä teksti>
  END

Viittauskorjaus (viitataan X vaikka pitäisi viitata Y) — pura väärä ja oikea erikseen:
  CURRENT_TEXT:
  <väärä viittaus>
  REPLACEMENT_TEXT:
  <oikea viittaus>
  END

Kuvaileva oikaisu (PDF kuvaa suoraan oikean arvon, väärä implisiittinen):
  ADD_TEXT:
  <oikea arvo>
  END

Eikö oikaisuja löydy: kirjoita NONE

== Esimerkit ==

Taulukkokorjaus, yksi solu muuttuu:
KORJ table | Sivulla 5, taulukko 1, rivi Ammoniumtyppi
TABLE_DIMS: 8 4
TABLE_HEADERS: Parametri | Raja-arvo | Yksikkö | Huomio
WRONG_ROW: Ammoniumtyppi | 0,50 mg/l | <_empty_> | <=17
RIGHT_ROW: Ammoniumtyppi | 0,40 mg/l | <_empty_> | >=18

Taulukkokorjaus, yläindeksillä merkitty alaviite:
KORJ footnote | Sivulla 145, UN-säiliön alaviite c
WRONG_ROW: <sup>c</sup>UN 3500, 3501 ja 3502 aineille on käytettävä paineellisen kemikaalin täyttöastetta kaasun enimmäistäyttöasteen sijasta.
RIGHT_ROW: <sup>c</sup>UN 3500, 3501 ja 3502 aineille on käytettävä täyttöastetta enimmäistäyttösuhteen sijasta.

Taulukko, vain muotoilu muuttuu (teksti sama):
KORJ table | Sivulla 3883, aine 1199
TABLE_DIMS: 12 3
TABLE_HEADERS: Rn | Aine | Huomio
TABLE_FORMAT_ONLY: aineen nimi pitää olla alleviivattu

Viittauskorjaus:
KORJ prose | Sivulla 3739, kohta g
CURRENT_TEXT:
liitteeseen IIB
REPLACEMENT_TEXT:
liitteeseen IB
END

Tekstikorjaus:
KORJ prose | Sivulla 434, 7 §:n 2 momentin 6 rivillä
CURRENT_TEXT:
1) liikennevakuutuslakiin (297/1959);
REPLACEMENT_TEXT:
1) liikennevakuutuslakiin (279/1959);
END

"on virheellisesti:" ja "puuttuu...joka kuuluu:" samassa PDF:ssä:
KORJ johtolause | Sivulla 3320, johtolauseessa
CURRENT_TEXT:
...41 § asetuksissa 404/1992...
REPLACEMENT_TEXT:
...41 § asetuksissa 1404/1992...
END

KORJ prose | Sivulla 3322, 40 §:n 3. momentti
ADD_TEXT:
Jos autokoulun opetustoiminnasta vastaavan johtajan vaihtuminen on kuoleman,
tapaturman tai muun ennalta arvaamattoman tapahtuman vuoksi tarpeen, voidaan
johtajana hyväksyä toimimaan enintään yhden vuoden ajan myös henkilö, joka ei
täytä 39 §:n 1 momentin 3 kohdan vaatimusta.
END

Kuvaileva oikaisu (vain oikea arvo mainitaan, väärä implisiittinen):
KORJ metadata | Sivulla 2273, antopäivämäärä
ADD_TEXT:
17. päivä syyskuuta 1999
END

Upotettu viittausvirhe (väärä ja oikea viittaus molemmat tekstissä):
KORJ prose | Sivulla 1, 4 §:n 3 momentti
CURRENT_TEXT:
64/432/ETY
REPLACEMENT_TEXT:
93/119/EY
END

Pelkkä kursivointi muuttuu, teksti sama (käytä FORMAT_ONLY):
KORJ prose | Sivulla 8, liitteen 10 rivi
FORMAT_ONLY: teksti pitää olla kursiivilla

Sekä teksti että kursivointi muuttuvat (käytä <i>-merkintää):
KORJ prose | Sivulla 17, liitteen 5 rivi
CURRENT_TEXT:
Bachelor of Beauty and Cosmetics
REPLACEMENT_TEXT:
<i>Master of Beauty and Cosmetics</i>
END"""


# pdftotext emits the running page header "Oikaisuja Suomen Säädöskokoelmaan"
# at each page break. When a correction block spans a page boundary the header
# lands between the wrong-text lines and "Pitää olla:", confusing both regex
# and LLM. Strip it unconditionally — it carries no correction content.
_PAGE_HEADER_RE = re.compile(
    r"^Oikaisuja Suomen [Ss]äädöskokoelmaan\s*$", re.IGNORECASE
)


def _number_lines(text: str) -> tuple[list[str], str]:
    """Number pdftotext lines for LLM span references.

    Returns (lines, numbered_text) where lines is 0-indexed list of raw line
    content and numbered_text has [N] prefixes for the LLM prompt.
    Strips form-feeds, bare page-number lines, and running page headers.
    """
    raw_lines = text.splitlines()
    lines: list[str] = []
    numbered: list[str] = []
    n = 0
    for raw in raw_lines:
        stripped = raw.replace("\x0c", "").strip()
        if not stripped:
            continue  # skip blank lines
        if re.match(r"^\d{1,4}$", stripped) and not lines:
            continue  # bare page number before content — skip
        if _PAGE_HEADER_RE.match(stripped):
            continue  # running page header at page breaks — not correction content
        lines.append(stripped)
        n += 1
        numbered.append(f"[{n}] {stripped}")
    return lines, "\n".join(numbered)


# Block-level keywords that open a content section (ended by END or next keyword)
_BLOCK_OPEN_KWS = re.compile(
    r"^(CURRENT_TEXT|REPLACEMENT_TEXT|DELETE_TEXT|ADD_TEXT"
    r"|CURRENT_TABLE|REPLACEMENT_TABLE"
    r"|WRONG_ROWS|RIGHT_ROWS"
    r"|[A-Z][A-Z0-9_]*_TEXT|[A-Z][A-Z0-9_]*_TABLE|[A-Z][A-Z0-9_]*_ROWS):?\s*$"
)
_SPAN_KW = re.compile(
    r"^(CURRENT_SPANS|REPLACEMENT_SPANS|DELETE_SPANS|ADD_SPANS"
    r"|[A-Z][A-Z0-9_]*_SPANS):\s*(\d+)\s+(\d+)\s*$"
)


_TABLE_ROW_SPLIT = re.compile(r"(?<!\\)\|")


def _parse_table_row(row_str: str) -> list[str]:
    """Split a pipe-delimited table row into cell values.

    Conventions:
      \\|          — literal pipe character inside a cell
      <_empty_>   — empty cell (blank in original PDF)
      <sup>...</sup>, <sub>...</sub> — semantic inline HTML (superscript/subscript)
    """
    parts = _TABLE_ROW_SPLIT.split(row_str)
    cells = []
    for p in parts:
        cell = p.strip().replace("\\|", "|")
        if cell == "<_empty_>":
            cell = ""
        cells.append(cell)
    return cells


def _parse_llm_lines(
    raw: str,
    lines: list[str],  # 0-indexed raw lines from _number_lines
    pdf_name: str,
) -> CorrigendumExtraction:
    """Parse compact line-based LLM output into CorrigendumExtraction."""

    def _span_text(start: int, end: int) -> str:
        """Extract lines[start-1 .. end-1] (1-indexed, inclusive)."""
        lo = max(0, start - 1)
        hi = min(len(lines), end)
        return "\n".join(lines[lo:hi]).strip()

    corrections: list[CorrectionItem] = []
    current_type: str = "unknown"
    current_loc: str = ""
    current_wrong: str = ""
    current_correct: str = ""
    current_delete: str = ""
    current_add: str = ""
    current_used_spans: bool = False
    current_table_meta: dict = {}
    # open block state
    open_kw: str = ""
    buf: list[str] = []

    def _flush_block() -> None:
        nonlocal open_kw, buf, current_wrong, current_correct, current_delete, current_add
        if not open_kw:
            return
        text = "\n".join(buf).strip()
        kw = open_kw.upper()
        if "WRONG_ROWS" in kw:
            row_lines = [l for l in buf if l.strip()]
            current_table_meta["wrong_rows"] = [_parse_table_row(l) for l in row_lines]
            current_wrong = "\n".join(row_lines)
        elif "RIGHT_ROWS" in kw:
            row_lines = [l for l in buf if l.strip()]
            current_table_meta["right_rows"] = [_parse_table_row(l) for l in row_lines]
            current_correct = "\n".join(row_lines)
        elif "CURRENT" in kw or "WRONG" in kw:
            current_wrong = text
        elif "REPLACEMENT" in kw or "RIGHT" in kw:
            current_correct = text
        elif "DELETE" in kw:
            current_delete = text
        elif "ADD" in kw:
            current_add = text
        open_kw = ""
        buf = []

    def _flush_correction() -> None:
        nonlocal current_type, current_loc, current_wrong, current_correct, current_delete, current_add, current_used_spans, current_table_meta
        _flush_block()
        wrong = current_wrong or current_delete
        correct = current_correct or current_add
        if current_loc or wrong or correct or current_table_meta:
            corrections.append(CorrectionItem(
                type=current_type if current_type in VALID_TYPES else "unknown",
                location=current_loc,
                wrong_text=wrong,
                correct_text=correct,
                confidence="high" if current_used_spans else "medium",
                table_meta=dict(current_table_meta) if current_table_meta else None,
            ))
        current_type = "unknown"
        current_loc = ""
        current_wrong = ""
        current_correct = ""
        current_delete = ""
        current_add = ""
        current_used_spans = False
        current_table_meta = {}

    raw = raw.strip()
    if not raw or raw.upper() == "NONE":
        return CorrigendumExtraction(amendment_id=None, corrections=[])

    for line in raw.splitlines():
        line_s = line.strip()

        if line_s.startswith("KORJ "):
            _flush_correction()
            rest = line_s[5:].strip()
            parts = rest.split("|", 1)
            current_type = parts[0].strip().lower() if parts else "unknown"
            current_loc = parts[1].strip() if len(parts) > 1 else ""
            continue

        if line_s == "END":
            _flush_block()
            continue

        m = _SPAN_KW.match(line_s)
        if m:
            _flush_block()
            kw, start, end = m.group(1), int(m.group(2)), int(m.group(3))
            text = _span_text(start, end)
            kw_up = kw.upper()
            if "CURRENT" in kw_up:
                current_wrong = text
                current_used_spans = True
            elif "REPLACEMENT" in kw_up:
                current_correct = text
                current_used_spans = True
            elif "DELETE" in kw_up:
                current_delete = text
                current_used_spans = True
            elif "ADD" in kw_up:
                current_add = text
                current_used_spans = True
            continue

        if line_s.startswith("WRONG_ROW:"):
            rest = line_s[len("WRONG_ROW:"):].strip()
            current_table_meta.setdefault("wrong_rows", []).append(_parse_table_row(rest))
            current_wrong = (current_wrong + "\n" + rest).strip() if current_wrong else rest
            continue

        if line_s.startswith("RIGHT_ROW:"):
            rest = line_s[len("RIGHT_ROW:"):].strip()
            current_table_meta.setdefault("right_rows", []).append(_parse_table_row(rest))
            current_correct = (current_correct + "\n" + rest).strip() if current_correct else rest
            continue

        if line_s.startswith("TABLE_DIMS:"):
            rest = line_s[len("TABLE_DIMS:"):].strip()
            parts = rest.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                current_table_meta["rows"] = int(parts[0])
                current_table_meta["cols"] = int(parts[1])
            continue

        if line_s.startswith("TABLE_CHANGE:"):
            rest = line_s[len("TABLE_CHANGE:"):].strip()
            m_tc = re.match(r"row=(\d+)\s+col=(\d+)", rest)
            if m_tc:
                current_table_meta["change_row"] = int(m_tc.group(1))
                current_table_meta["change_col"] = int(m_tc.group(2))
            continue

        if line_s.startswith("TABLE_HEADERS:"):
            rest = line_s[len("TABLE_HEADERS:"):].strip()
            if rest and rest != "-":
                current_table_meta["headers"] = [h.strip() for h in rest.split("|")]
            continue

        if line_s.startswith("TABLE_FORMAT_ONLY:"):
            rest = line_s[len("TABLE_FORMAT_ONLY:"):].strip()
            current_table_meta["format_only"] = rest if rest else True
            continue

        if line_s.startswith("FORMAT_ONLY:"):
            rest = line_s[len("FORMAT_ONLY:"):].strip()
            current_table_meta["format_only"] = rest if rest else True
            continue

        if line_s.startswith("HIGHLIGHT_TEXT:"):
            rest = line_s[len("HIGHLIGHT_TEXT:"):].strip()
            if rest:
                current_table_meta["highlight_text"] = rest
            continue

        if _BLOCK_OPEN_KWS.match(line_s):
            _flush_block()
            open_kw = line_s.rstrip(":").strip()
            buf = []
            continue

        if open_kw:
            buf.append(line)
            continue

        # Ignore unrecognised lines between corrections (whitespace, separators)

    _flush_correction()

    return CorrigendumExtraction(amendment_id=None, corrections=corrections)


def _amendment_id_from_filename(pdf_name: str) -> Optional[str]:
    """sk20180984_1.pdf → '984/2018'."""
    m = _PDF_NAME_RE.search(pdf_name)
    if not m:
        return None
    _, year, num, _ = m.groups()
    return f"{int(num)}/{year}"


# ---------------------------------------------------------------------------
# Source verification
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(rb"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _normalize_text(t: str) -> str:
    return _WS_RE.sub(" ", t).strip().lower()


def _looks_like_attachment_only_correction(
    *,
    location_desc: str,
    correction_type: str,
    source_xml: bytes | None,
) -> bool:
    location_norm = _normalize_text(location_desc or "")
    correction_type = str(correction_type or "").strip().lower()
    if not source_xml:
        return False
    has_attachment_pdf = re.search(rb'href="media/[^"]+\.pdf"', source_xml) is not None
    if not has_attachment_pdf:
        return False
    if location_norm.startswith("liite"):
        return True
    if " taulukko" in f" {location_norm}" or correction_type == "table":
        return True
    return False


def _verify_in_source(amendment_id: str, wrong_text: str) -> Optional[bool]:
    """Check if wrong_text can be matched in the amendment's source XML.

    Returns True (found), False (not found), None (amendment not in corpus / error).
    Uses _apply_text_replace on the johtolause fragment — same 6-pass strategy
    as actual patch application — for accurate verification.
    """
    if not wrong_text.strip():
        return None
    try:
        num, year = amendment_id.split("/")
        sid = f"{year}/{num}"
        cs = _get_shared_cs()
        data = cs.read_source(sid)
        if data is None:
            return None
        return _verify_in_source_xml(data, wrong_text)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        return None


def _verify_in_source_xml(xml_bytes: bytes | None, wrong_text: str) -> Optional[bool]:
    if not xml_bytes or not wrong_text.strip():
        return None
    try:
        import time as _time
        from lawvm.finland.corrigendum import _apply_text_replace, _johtolause_byte_range

        frag_start, frag_end = _johtolause_byte_range(xml_bytes)
        if frag_start >= 0:
            # Johtolause is in the preamble fragment — if it's not there it's not
            # anywhere else that matters. Do NOT fall through to full XML: that
            # triggers an expensive pass-6 difflib scan for no good reason.
            fragment = xml_bytes[frag_start:frag_end]
            _t0 = _time.monotonic()
            _, ok = _apply_text_replace(fragment, wrong_text, "PLACEHOLDER")
            _elapsed = _time.monotonic() - _t0
            if _elapsed > 1.0:
                print(f"  SLOW _apply_text_replace (fragment {len(fragment)}B): {_elapsed:.2f}s", flush=True)
            return ok
        # No preamble boundary found — search full XML (rare: statute has no johtolause).
        _t0 = _time.monotonic()
        _, ok = _apply_text_replace(xml_bytes, wrong_text, "PLACEHOLDER")
        _elapsed = _time.monotonic() - _t0
        if _elapsed > 1.0:
            print(f"  SLOW _apply_text_replace (full XML {len(xml_bytes)}B): {_elapsed:.2f}s", flush=True)
        return ok
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        return None


def _load_patch_rows(path: Optional[Path] = None) -> list[dict]:
    target = Path(path) if path is not None else _OFFICIAL_TEXT
    return load_patch_records(target)


def _stable_id(source_pdf: str, correction_index: int) -> str:
    return f"{source_pdf}#{correction_index}"




# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _pdf_to_images_base64(pdf_bytes: bytes, dpi: int = 150) -> list[str]:
    """Render PDF pages to JPEG and return list of base64-encoded strings."""
    import base64
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        pdf_path = f.name
    try:
        out_prefix = pdf_path.replace(".pdf", "_page")
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-jpeg", pdf_path, out_prefix],
            check=True, capture_output=True,
        )
        import glob as _glob
        pages = sorted(_glob.glob(f"{out_prefix}-*.jpg"))
        result = []
        for page in pages:
            with open(page, "rb") as f:
                result.append(base64.b64encode(f.read()).decode())
            import os; os.unlink(page)
        return result
    finally:
        import os; os.unlink(pdf_path)


async def _call_llm(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    numbered_text: str,
) -> str:
    payload = {
        "messages": [
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": numbered_text},
        ],
        "max_tokens": 4096,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # Retry on ServerDisconnectedError: local LLM server drops queued connections
    # that sit idle too long while other requests are being processed. One retry
    # after a short wait is usually enough to get a free slot.
    for attempt in range(3):
        try:
            async with sem:
                async with session.post(
                    _LLAMA_URL, json=payload,
                    timeout=aiohttp.ClientTimeout(total=600),
                ) as resp:
                    data = await resp.json()
                return data["choices"][0]["message"].get("content", "").strip()
        except aiohttp.ServerDisconnectedError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)  # 1s, 2s
    raise RuntimeError("unreachable")


async def _call_llm_vision(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    images_b64: list[str],
) -> str:
    """Send PDF page images to vision-capable LLM for scanned corrigendum extraction."""
    content: list[dict] = []
    for img_b64 in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
        })
    content.append({"type": "text", "text": "Tunnista oikaisut PDF-sivuilta yllä olevien ohjeiden mukaisesti."})
    payload = {
        "messages": [
            {"role": "system", "content": _CLASSIFY_VISION_SYSTEM},
            {"role": "user", "content": content},
        ],
        "max_tokens": 4096,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    last_exc: BaseException | None = None
    for attempt in range(3):
        try:
            async with sem:
                async with session.post(
                    _LLAMA_URL, json=payload,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    data = await resp.json()
            if "choices" not in data:
                last_exc = RuntimeError(f"vision LLM error response: {data}")
                await asyncio.sleep(2 ** attempt)
                continue
            return data["choices"][0]["message"].get("content", "").strip()
        except aiohttp.ServerDisconnectedError as e:
            last_exc = e
            await asyncio.sleep(2 ** attempt)
    print(f"  [vision-error] all retries failed: {last_exc}", flush=True)
    return ""


# ---------------------------------------------------------------------------
# Classify command
# ---------------------------------------------------------------------------

def _normalize_pair(w: str, c: str) -> tuple[str, str]:
    """Normalise (wrong, correct) for agreement comparison: collapse whitespace."""
    return _WS_RE.sub(" ", w).strip(), _WS_RE.sub(" ", c).strip()


def _correction_item_to_dict(c: "CorrectionItem") -> dict:
    d: dict = {
        "type": c.type,
        "location": c.location,
        "wrong_text": c.wrong_text,
        "correct_text": c.correct_text,
        "confidence": c.confidence,
    }
    if c.table_meta:
        d["table_meta"] = c.table_meta
    return d


def _rows_from_corrections(
    corrections: list,  # list[CorrectionItem]
    source_pdf: str,
    statute_id: str,
    lang: str,
    amendment_id: str,
    date_published: Optional[str],
    source_tag: str,  # "regex", "llm", "both", "regex+llm", "vision", "vision+llm", etc.
    agreed: bool,
    precomputed_verified: Optional[dict] = None,  # wrong_text -> bool|None, avoids double call
    llm_extraction: Optional[list] = None,      # list[dict] — text LLM items, for audit
    vision_extraction: Optional[list] = None,   # list[dict] — vision LLM items, for audit
    regex_extraction: Optional[list] = None,    # list[dict] — regex items, for audit
    expected_pair_count: Optional[int] = None,  # regex count of On:/Pitää olla: pairs in raw PDF
) -> tuple[list[dict], list[dict]]:
    """Build JSONL rows from a CorrectionItem list."""
    official_rows: list[dict] = []
    adjudication_rows: list[dict] = []
    for i, c in enumerate(corrections):
        verified: Optional[int] = None
        if c.type == "johtolause" and amendment_id and c.wrong_text:
            if precomputed_verified is not None and c.wrong_text in precomputed_verified:
                v = precomputed_verified[c.wrong_text]
            else:
                v = _verify_in_source(amendment_id, c.wrong_text)
            if v is not None:
                verified = 1 if v else 0
        stable_id = _stable_id(source_pdf, i)
        official_rows.append({
            "stable_id": stable_id,
            "source_pdf": source_pdf,
            "statute_id": statute_id,
            "amendment_id": amendment_id,
            "lang": lang,
            "correction_index": i,
            "correction_type": c.type if c.type in VALID_TYPES else "unknown",
            "location_desc": c.location,
            "wrong_text": c.wrong_text,
            "correct_text": c.correct_text,
            "extraction_source": source_tag,
            "date_published": date_published,
            "llm_extraction": llm_extraction,
            "vision_extraction": vision_extraction,
            "regex_extraction": regex_extraction,
            "parse_error": None,
            "extract_agreed": agreed,
            "expected_pair_count": expected_pair_count,
        })
        adjudication_rows.append({"stable_id": stable_id, "verified_in_source": verified})
    return official_rows, adjudication_rows


async def _classify_pdf(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    source_pdf: str,
    pdf_bytes: bytes,
    statute_id: str,
    lang: str,
    dry_run: bool,
    verbose: bool,
    xml_amendment_id: Optional[str] = None,
    date_published: Optional[str] = None,
    compare: bool = False,   # kept for CLI compat; now always runs both when LLM available
) -> dict:
    from lawvm.finland.corrigendum import (
        count_corrigendum_pairs as _count_pairs,
        parse_corrigendum as _regex_parse,
        _classify_location,
    )

    pdf_name = Path(source_pdf).name
    amendment_id = xml_amendment_id or _amendment_id_from_filename(pdf_name) or ""

    text = _pdf_to_text(pdf_bytes)
    expected_pair_count: Optional[int] = _count_pairs(text) if text else None

    # ---------------------------------------------------------------- sámi translation
    # "Lisätään saamenkielinen/saamenkieliset käännös/käännökset:" — a full translation
    # addition spanning many pages. Recognise and record as sami_translation type;
    # do not attempt LLM extraction on the full translation body.
    _SAMI_RE = re.compile(r"Lisätään saamenkieliset?\s+käännökset?:", re.IGNORECASE)
    if text and _SAMI_RE.search(text[:500]):
        if verbose:
            print(f"  {pdf_name}: {amendment_id}  [sami_translation]")
        rows, arows = [], []
        if not dry_run:
            rows, arows = _rows_from_corrections(
                [CorrectionItem(type="sami_translation", location="saamenkielinen käännös",
                                wrong_text="", correct_text="", confidence="high")],
                source_pdf, statute_id, lang, amendment_id, date_published,
                "regex", agreed=True,
                expected_pair_count=expected_pair_count,
            )
        return {"pdf": pdf_name, "source_pdf": source_pdf, "status": "OK",
                "amendment_id": amendment_id, "types": ["sami_translation"],
                "has_johtolause": False, "official_rows": rows, "adjudication_rows": arows}

    # Page count guard — corrigendum notices are short; bulk/liite/translation PDFs are not.
    pages = _pdf_page_count(pdf_bytes)
    if pages is not None and pages > _PDF_MAX_PAGES:
        print(
            f"  TOO_LARGE {pdf_name}: {pages} pages > {_PDF_MAX_PAGES} — bulk/liite/translation PDF, skipping.",
            flush=True,
        )
        return {"pdf": pdf_name, "source_pdf": source_pdf, "status": "TOO_LARGE",
                "amendment_id": amendment_id, "types": [], "has_johtolause": False,
                "official_rows": [], "adjudication_rows": [], "pages": pages}

    # Truncate at liite boundary — some corrigenda have corrections on the first
    # pages followed by a full appendix body. Only the correction preamble matters.
    _LIITE_CUT_RE = re.compile(r"\n[^\n]*lisätään\s+liite\b", re.IGNORECASE)
    if text:
        m = _LIITE_CUT_RE.search(text)
        if m:
            text = text[:m.start()]

    # ------------------------------------------------------------------ render PDF to images (always, for vision)
    try:
        images_b64 = _pdf_to_images_base64(pdf_bytes)
    except Exception:
        images_b64 = []

    # ------------------------------------------------------------------ scanned (no text at all)
    if not text or not text.strip():
        if not images_b64:
            if verbose:
                print(f"  {pdf_name}: NO_TEXT")
            return {"pdf": pdf_name, "status": "NO_TEXT", "source_pdf": source_pdf,
                    "official_rows": [], "adjudication_rows": []}
        raw_vision = await _call_llm_vision(session, sem, images_b64)
        vision_ext = _parse_llm_lines(raw_vision, [], pdf_name)
        types = [c.type for c in vision_ext.corrections]
        if verbose:
            print(f"  {pdf_name}: {amendment_id}  n={len(vision_ext.corrections)}  [vision]")
        rows, arows = ([], [])
        if not dry_run and vision_ext.corrections:
            rows, arows = _rows_from_corrections(
                vision_ext.corrections, source_pdf, statute_id, lang,
                amendment_id, date_published, "vision", agreed=False,
                vision_extraction=[_correction_item_to_dict(it) for it in vision_ext.corrections],
                expected_pair_count=expected_pair_count,
            )
        return {"pdf": pdf_name, "source_pdf": source_pdf, "status": "OK",
                "amendment_id": amendment_id, "types": types,
                "has_johtolause": "johtolause" in types,
                "official_rows": rows, "adjudication_rows": arows}

    # ----------------------------------------- number lines for LLM span refs
    lines, numbered_text = _number_lines(text)

    if len(numbered_text) > _CLASSIFY_MAX_INPUT_CHARS:
        print(
            f"  ERROR {pdf_name}: text too large for LLM input "
            f"({len(numbered_text)} chars > {_CLASSIFY_MAX_INPUT_CHARS}) — "
            f"merged/bulk PDF? Skipping LLM; regex only.",
            flush=True,
        )
        return {"pdf": pdf_name, "source_pdf": source_pdf, "status": "TOO_LARGE",
                "amendment_id": amendment_id, "types": [], "has_johtolause": False,
                "official_rows": [], "adjudication_rows": [], "chars": len(numbered_text)}

    # ------------------------------------------------------------------- regex
    regex_ops = _regex_parse(text, amendment_id)
    regex_items: list[CorrectionItem] = []
    for op in regex_ops:
        loc = (op.source.raw_text if op.source else "") or ""
        w = (op.text_patch.selector.match_text if op.text_patch else "") or ""
        c = (op.text_patch.replacement if op.text_patch else "") or ""
        regex_items.append(CorrectionItem(
            type=_classify_location(loc), location=loc,
            wrong_text=w, correct_text=c, confidence="high",
        ))

    # -------------------------------------------------------------------- LLM + vision concurrently
    vision_coro = _call_llm_vision(session, sem, images_b64) if images_b64 else None
    if vision_coro is not None:
        raw_llm, raw_vision = await asyncio.gather(_call_llm(session, sem, numbered_text), vision_coro)
    else:
        raw_llm = await _call_llm(session, sem, numbered_text)
        raw_vision = ""
    llm_ext = _parse_llm_lines(raw_llm, lines, pdf_name)
    llm_items = llm_ext.corrections
    vision_ext = _parse_llm_lines(raw_vision, [], pdf_name) if raw_vision else CorrigendumExtraction(amendment_id=None, corrections=[])
    vision_items = vision_ext.corrections

    # ----------------------------------------------------------------- merge
    # LLM is primary: span-based extraction gives precise text boundaries.
    # Regex over-captures context and sometimes merges separate corrections.
    # Regex only supplements items the LLM missed entirely.
    # "Covered" = LLM wrong_text is substring of regex wrong_text (or equal):
    # same correction at different granularity — LLM version is preferred.

    def _norm_wrong(it: CorrectionItem) -> str:
        return _WS_RE.sub(" ", it.wrong_text).strip()

    def _norm_text(t: str) -> str:
        return _WS_RE.sub(" ", t).strip()

    # Identity filter: two cases.
    # 1. wrong == correct exactly → truly spurious (LLM hallucination, pdftotext noise) → drop
    # 2. wrong != correct but norm(wrong) == norm(correct) → whitespace/line-break-only diff
    #    → real published correction, keep as correction_type="typeset" (no XML effect)
    # Vision items with table_meta are always kept regardless (format_only table corrections).
    def _is_exact_identity(it: CorrectionItem) -> bool:
        return it.wrong_text == it.correct_text

    def _is_ws_identity(it: CorrectionItem) -> bool:
        """Texts differ only in whitespace/newlines — real typeset correction."""
        return (
            _norm_text(it.wrong_text) == _norm_text(it.correct_text)
            and it.wrong_text != it.correct_text
        )

    def _reclassify_ws(it: CorrectionItem) -> CorrectionItem:
        from dataclasses import replace as _dc_replace
        return _dc_replace(it, type="typeset")

    def _filter_identity(items: list[CorrectionItem], label: str) -> list[CorrectionItem]:
        out = []
        for it in items:
            if it.table_meta:
                out.append(it)  # always keep — may be format_only table correction
            elif _is_exact_identity(it):
                if verbose:
                    print(f"  {pdf_name} [{label}-exact-drop] {it.wrong_text!r} (type={it.type})", flush=True)
            elif _is_ws_identity(it):
                if verbose:
                    print(f"  {pdf_name} [{label}-ws-typeset] {it.wrong_text!r} (type={it.type})", flush=True)
                out.append(_reclassify_ws(it))
            else:
                out.append(it)
        return out

    llm_items = _filter_identity(llm_items, "llm")
    vision_items = _filter_identity(vision_items, "vision")
    regex_items = _filter_identity(regex_items, "regex")

    def _covered_by(candidate: CorrectionItem, base_norms: list[str]) -> bool:
        c_norm = _norm_wrong(candidate)
        return any(b_norm in c_norm or c_norm in b_norm for b_norm in base_norms)

    # Merge strategy:
    # 1. Text LLM is primary for prose/johtolause/footnote (span-exact extraction)
    # 2. Vision supplements table items text LLM misses (table layout invisible to pdftotext)
    # 3. Regex fills what both LLM and vision miss entirely
    agreed = False
    all_llm_norms = [_norm_wrong(it) for it in llm_items]
    all_vision_norms = [_norm_wrong(it) for it in vision_items]

    # Base: text LLM items
    final_items = list(llm_items)
    # Add vision items not covered by text LLM
    for v_it in vision_items:
        if not _covered_by(v_it, all_llm_norms):
            final_items.append(v_it)
    combined_norms = [_norm_wrong(it) for it in final_items]
    # Add regex items not covered by text LLM or vision
    for r_it in regex_items:
        if not _covered_by(r_it, combined_norms):
            final_items.append(r_it)

    if regex_items and llm_items:
        agreed = all(_covered_by(r_it, all_llm_norms) for r_it in regex_items)
        if vision_items:
            source_tag = "both+vision" if agreed else "regex+llm+vision"
        else:
            source_tag = "both" if agreed else "regex+llm"
    elif regex_items and vision_items:
        agreed = all(_covered_by(r_it, all_vision_norms) for r_it in regex_items)
        source_tag = "both" if agreed else "regex+vision"
    elif llm_items and vision_items:
        source_tag = "llm+vision"
    elif regex_items:
        final_items = regex_items
        source_tag = "regex"
    elif llm_items:
        source_tag = "llm"
    elif vision_items:
        source_tag = "vision"
    else:
        final_items = []
        source_tag = "none"

    types = list({it.type for it in final_items})
    has_johtolause = "johtolause" in types

    # Pre-compute verification for johtolause items once — avoids calling
    # _verify_in_source twice (verbose print + _rows_from_corrections).
    precomputed_verified: dict = {}
    if has_johtolause and amendment_id:
        import time as _time
        _t0_verify = _time.monotonic()
        for it in final_items:
            if it.type == "johtolause" and it.wrong_text and it.wrong_text not in precomputed_verified:
                precomputed_verified[it.wrong_text] = _verify_in_source(amendment_id, it.wrong_text)
        _verify_elapsed = _time.monotonic() - _t0_verify
        if _verify_elapsed > 1.0:
            print(f"  SLOW verify {pdf_name}: {_verify_elapsed:.1f}s for {len(precomputed_verified)} item(s)", flush=True)

    if verbose or has_johtolause:
        verified_str = ""
        if precomputed_verified:
            first_v = next(iter(precomputed_verified.values()))
            verified_str = f" [verified={first_v}]"
        agree_tag = "AGREE" if agreed else f"regex={len(regex_items)}/llm={len(llm_items)}/vis={len(vision_items)}"
        print(f"  {pdf_name}: {amendment_id}  n={len(final_items)}  [{source_tag} {agree_tag}]{verified_str}")
        if not agreed and regex_items and llm_items:
            llm_norms_dbg = [_norm_wrong(it) for it in llm_items]
            for i, r_it in enumerate(regex_items):
                if not _covered_by(r_it, llm_norms_dbg):
                    print(f"    [regex-only {i}] {r_it.wrong_text!r} → {r_it.correct_text!r}")
            regex_norms_dbg = {_norm_wrong(it) for it in regex_items}
            for j, l_it in enumerate(llm_items):
                if _norm_wrong(l_it) not in regex_norms_dbg and not any(
                    _norm_wrong(l_it) in _norm_wrong(r) or _norm_wrong(r) in _norm_wrong(l_it)
                    for r in regex_items
                ):
                    print(f"    [llm-only  {j}] {l_it.wrong_text!r} → {l_it.correct_text!r}")

    official_rows: list[dict] = []
    adjudication_rows: list[dict] = []
    if not dry_run and final_items:
        llm_ext_dicts = [_correction_item_to_dict(it) for it in llm_items] if llm_items else None
        vis_ext_dicts = [_correction_item_to_dict(it) for it in vision_items] if vision_items else None
        rgx_ext_dicts = [_correction_item_to_dict(it) for it in regex_items] if regex_items else None
        official_rows, adjudication_rows = _rows_from_corrections(
            final_items, source_pdf, statute_id, lang,
            amendment_id, date_published, source_tag, agreed,
            precomputed_verified=precomputed_verified,
            llm_extraction=llm_ext_dicts,
            vision_extraction=vis_ext_dicts,
            regex_extraction=rgx_ext_dicts,
            expected_pair_count=expected_pair_count,
        )

    return {
        "pdf": pdf_name,
        "source_pdf": source_pdf,
        "status": "OK",
        "amendment_id": amendment_id,
        "types": types,
        "has_johtolause": has_johtolause,
        "official_rows": official_rows,
        "adjudication_rows": adjudication_rows,
    }




async def _run_classify(args) -> None:
    dry_run = getattr(args, "dry_run", False)
    rerun = getattr(args, "rerun", False)
    lang_filter = getattr(args, "lang", "fi")  # fi=sk*, sv=fs*, all=both
    type_filter = getattr(args, "type", None)
    import os as _os
    _par = getattr(args, "parallel", None)
    parallel = _par if _par is not None else max(8, _os.cpu_count() or 4)
    limit = getattr(args, "limit", None)
    verbose = getattr(args, "verbose", False)
    compare = getattr(args, "compare", False)

    cs = _make_corpus_store()
    # Collect PDFs to classify from the corpus store
    all_names = list_cached_corrigendum_locators(cs)

    lang_prefixes = []
    if lang_filter in ("fi", "all"):
        lang_prefixes.append(("/corrigenda/sk", "fi"))
    if lang_filter in ("sv", "all"):
        lang_prefixes.append(("/corrigenda/fs", "sv"))

    targets: list[tuple[str, str, str, str]] = []  # (source_pdf, statute_id, lang, raw_locator)
    for name in all_names:
        for prefix, lang in lang_prefixes:
            if prefix in name and name.endswith(".pdf"):
                # Normalise to akn/... source_pdf for JSONL stability; keep raw locator for reads.
                source_pdf = _locator_to_akn_source_pdf(name)
                sid = _sid_from_locator(name)
                if source_pdf and sid:
                    targets.append((source_pdf, sid, lang, name))
                break

    if limit:
        targets = targets[:limit]

    official_records = [] if dry_run else load_official_records(_OFFICIAL_TEXT)
    adjudication_records = [] if dry_run else load_adjudication_records(_ADJUDICATIONS_TEXT)
    existing_source_pdfs = {str(row.get("source_pdf") or "") for row in official_records}

    # Skip already classified from the git text corpus first.
    if not rerun and not dry_run:
        targets = [(p, s, l, loc) for p, s, l, loc in targets if p not in existing_source_pdfs]

    print(f"Classifying {len(targets)} corrigendum PDFs"
          f"  lang={lang_filter}  parallel={parallel}"
          f"  dry_run={dry_run}  records={_OFFICIAL_TEXT if not dry_run else 'N/A'}")

    # Pre-read XML metadata: amendment_id (from <finlex:ref>) and date_published
    # Reading from XML is more reliable than LLM for amendment_id (always NUM/YEAR).
    xml_meta: dict[str, dict] = {}  # source_pdf_path → {amendment_id, date_published}
    target_sids = set(s for _, s, _, _loc in targets)
    for sid in target_sids:
        refs = _get_xml_corrigendum_refs(cs, sid)
        for ref in refs:
            pdf_href = ref.get("pdf_href")
            if not pdf_href:
                continue
            pdf_name = Path(pdf_href).name
            # xml_meta keyed by normalised akn/... source_pdf
            candidate = f"akn/fi/act/statute-consolidated/{sid}/media/corrigenda/{pdf_name}"
            xml_meta[candidate] = {
                "amendment_id": ref.get("ref_text"),
                "date_published": ref.get("date"),
            }

    sem = asyncio.Semaphore(parallel)
    results = []

    async with aiohttp.ClientSession() as session:
        tasks = []
        for source_pdf, statute_id, lang, _locator in targets:
            pdf_bytes = cs.read_corrigendum_media(statute_id, Path(source_pdf).name)
            meta = xml_meta.get(source_pdf, {})
            tasks.append(_classify_pdf(
                session, sem, source_pdf, pdf_bytes,
                statute_id, lang, dry_run, verbose,
                xml_amendment_id=meta.get("amendment_id"),
                date_published=meta.get("date_published"),
                compare=compare,
            ))

        results = await asyncio.gather(*tasks)

    if not dry_run:
        processed_source_pdfs = {
            str(result.get("source_pdf") or "")
            for result in results
            if str(result.get("source_pdf") or "")
        }
        official_records = [
            row
            for row in official_records
            if str(row.get("source_pdf") or "") not in processed_source_pdfs
        ]
        adjudication_records = [
            row
            for row in adjudication_records
            if str(row.get("stable_id") or "").rsplit("#", 1)[0] not in processed_source_pdfs
        ]
        for result in results:
            official_records.extend(result.get("official_rows", []))
            adjudication_records.extend(result.get("adjudication_rows", []))
        write_official_records(official_records, _OFFICIAL_TEXT)
        write_adjudication_records(adjudication_records, _ADJUDICATIONS_TEXT)

    # Summary
    ok = [r for r in results if r["status"] == "OK"]
    errors = [r for r in results if r["status"] == "PARSE_ERROR"]
    no_text = [r for r in results if r["status"] == "NO_TEXT"]
    too_large = [r for r in results if r["status"] == "TOO_LARGE"]
    by_type: dict[str, int] = {}
    johtolause_cases = []
    for r in ok:
        for t in r.get("types", []):
            by_type[t] = by_type.get(t, 0) + 1
        if r.get("has_johtolause"):
            johtolause_cases.append(r)

    print("\n=== Classification Summary ===")
    print(f"  Total        : {len(results)}")
    print(f"  OK           : {len(ok)}")
    print(f"  Parse errors : {len(errors)}")
    print(f"  No text      : {len(no_text)}")
    print(f"  Too large    : {len(too_large)}")
    for r in too_large:
        detail = f"{r['pages']} pages" if "pages" in r else f"{r.get('chars', '?')} chars"
        print(f"    {r['pdf']}  {detail}")
    print("\n  By correction type:")
    for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {t:<15} {count}")
    print(f"\n  JOHTOLAUSE corrections ({len(johtolause_cases)}):")
    for r in johtolause_cases:
        print(f"    {r['pdf']:<40} amendment={r['amendment_id']}")

    if type_filter and not dry_run:
        print(f"\n=== Filtered: type={type_filter} ===")
        rows = [
            row for row in load_patch_records(_OFFICIAL_TEXT)
            if row.get("correction_type") == type_filter
        ]
        rows.sort(key=lambda row: str(row.get("amendment_id") or ""))
        for row in rows:
            v = {1: "✓", 0: "✗", None: "?"}[row.get("verified_in_source")]
            print(
                f"  {Path(str(row.get('source_pdf') or '')).name:<40} "
                f"{str(row.get('amendment_id') or '?'):>10} [{v}]  "
                f"{str(row.get('wrong_text') or '')[:40]!r} → {str(row.get('correct_text') or '')[:40]!r}"
            )


def _cmd_classify(args) -> None:
    asyncio.run(_run_classify(args))


# ---------------------------------------------------------------------------
# Verify command (retroactively fix verified_in_source after bug fix)
# ---------------------------------------------------------------------------

def _cmd_verify(args) -> None:
    """Re-run source verification for johtolause corrections (no LLM needed).

    Use after: fixing _verify_in_source bugs, or after updating the corpus store.
    Re-checks wrong_text against the corpus store and updates the git adjudication
    corpus directly.
    """
    if not _OFFICIAL_TEXT.exists():
        print("No classified corrigendum corpus — run: lawvm corrigendum classify", file=sys.stderr)
        sys.exit(1)

    type_filter = getattr(args, "type", "johtolause")
    amendment_id = str(getattr(args, "amendment_id", "") or "").strip()

    if _OFFICIAL_TEXT.exists():
        rows = [
            row
            for row in load_official_records(_OFFICIAL_TEXT)
            if row.get("lang") == "fi"
            and row.get("correction_type") == type_filter
            and row.get("wrong_text")
            and (not amendment_id or str(row.get("amendment_id") or "") == amendment_id)
        ]
        adjudications_by_id = {
            str(row.get("stable_id") or ""): dict(row)
            for row in load_adjudication_records(_ADJUDICATIONS_TEXT)
        }
    else:
        rows = []
        adjudications_by_id = {}

    scope = f" for {amendment_id}" if amendment_id else ""
    print(f"Verifying {len(rows)} {type_filter} corrections against the corpus store{scope}...")
    updated = found = not_found = skipped = 0
    if rows:
        for row in rows:
            current_amendment_id = str(row.get("amendment_id") or "")
            wrong_text = str(row.get("wrong_text") or "")
            if not current_amendment_id or not wrong_text:
                skipped += 1
                continue
            v = _verify_in_source(current_amendment_id, wrong_text)
            if v is None:
                skipped += 1
                continue
            stable_id = str(row.get("stable_id") or "")
            adjudication = adjudications_by_id.get(stable_id, {"stable_id": stable_id})
            adjudication["verified_in_source"] = 1 if v else 0
            adjudications_by_id[stable_id] = adjudication
            updated += 1
            if v:
                found += 1
            else:
                not_found += 1
        write_adjudication_records(list(adjudications_by_id.values()), _ADJUDICATIONS_TEXT)
    print(f"  Updated: {updated}  Found in source: {found}  Not found: {not_found}  Skipped: {skipped}")


# ---------------------------------------------------------------------------
# Test command (dry-run patch application on a specific amendment)
# ---------------------------------------------------------------------------

def _cmd_test(args) -> None:
    """Show whether corrigendum patches apply to an amendment's source XML.

    Loads all classified patches for amendment_id, applies them to the source
    XML from the corpus, and prints a before/after diff for each patch.
    """
    amendment_id = args.amendment_id  # NUM/YEAR format from CLI (e.g. "984/2018")

    if not _OFFICIAL_TEXT.exists():
        print("No classified corrigendum corpus — run: lawvm corrigendum classify", file=sys.stderr)
        sys.exit(1)

    # Normalize to NUM/YEAR (user may pass either)
    from lawvm.finland.corrigendum import _to_grafter_mid as _to_grafter_amendment_id, get_patch_table, reset_patch_table
    reset_patch_table()
    pt = get_patch_table()

    # Convert input to grafter YEAR/NUM for lookup
    grafter_amendment_id = _to_grafter_amendment_id(amendment_id) or amendment_id

    ops = pt._patches.get(grafter_amendment_id)
    if not ops:
        # Also try treating input as YEAR/NUM directly
        ops = pt._patches.get(amendment_id)
    if not ops:
        print(f"No patches found for '{amendment_id}' (tried grafter amendment_id '{grafter_amendment_id}')")
        print(f"Patch table has {pt.amendment_count()} amendments. Run: lawvm corrigendum classify")
        return

    # Load source XML
    cs = get_corpus_store()
    xml_bytes = cs.read_source(grafter_amendment_id)
    if xml_bytes is None:
        print(f"Cannot read source XML for {grafter_amendment_id}: not found in corpus", file=sys.stderr)
        return

    print(f"Amendment: {amendment_id}  (grafter amendment_id: {grafter_amendment_id})")
    print(f"Source XML: {len(xml_bytes):,} bytes")
    print(f"Patches: {len(ops)}")
    print()

    from lawvm.finland.corrigendum import _apply_text_replace
    for i, op in enumerate(ops, 1):
        patch = op.text_patch
        wrong = patch.selector.match_text if patch is not None else ""
        correct = patch.replacement if patch is not None and patch.replacement is not None else ""
        print(f"  [{i}] {op.op_id}")
        print(f"    Wrong  : {wrong[:120]!r}")
        print(f"    Correct: {correct[:120]!r}")
        _, applied = _apply_text_replace(xml_bytes, wrong, correct)
        if applied:
            print("    Status : APPLIES ✓")
            # Show context around the match
            try:
                xml_str = xml_bytes.decode("utf-8", errors="replace")
                idx = xml_str.find(wrong[:40]) if len(wrong) >= 40 else xml_str.find(wrong)
                if idx >= 0:
                    ctx = xml_str[max(0, idx - 60):idx + len(wrong) + 60]
                    ctx = ctx.replace("\n", " ")
                    print(f"    Context: ...{ctx[:200]}...")
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception:
                pass
        else:
            print("    Status : NO MATCH ✗  (text not found in source XML)")
        print()


# ---------------------------------------------------------------------------
# Report command (query classified results)
# ---------------------------------------------------------------------------

def _cmd_report(args) -> None:
    type_filter = getattr(args, "type", None)
    amendment_id = getattr(args, "amendment_id", None)
    verified_only = getattr(args, "verified", False)
    records = _load_patch_rows(Path(args.db) if getattr(args, "db", None) else None)
    rows = []
    for row in records:
        if type_filter and row.get("correction_type") != type_filter:
            continue
        if amendment_id and row.get("amendment_id") != amendment_id:
            continue
        if verified_only and row.get("verified_in_source") != 1:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (str(r.get("correction_type") or ""), str(r.get("amendment_id") or "")))

    if not rows:
        print("No results.")
        return

    print(f"{'TYPE':<15} {'AMENDMENT':>10}  {'STATUTE':>10}  {'VER':>3}  {'CONF':>4}  WRONG → CORRECT")
    print("-" * 100)
    for row in rows:
        t = row.get("correction_type")
        amend = row.get("amendment_id")
        stat = row.get("statute_id")
        wrong = row.get("wrong_text")
        correct = row.get("correct_text")
        conf = row.get("extraction_source") or row.get("llm_confidence")
        ver = row.get("verified_in_source")
        v = {1: "✓", 0: "✗", None: "?"}[ver]
        wrong_s = (wrong or "")[:35]
        correct_s = (correct or "")[:35]
        print(f"  {t:<13} {amend or '?':>10}  {stat:>10}  {v:>3}  {conf or '?':>4}  {wrong_s!r} → {correct_s!r}")

    # Summary stats
    total = len(rows)
    by_type: dict[str, int] = {}
    verified = sum(1 for row in rows if row.get("verified_in_source") == 1)
    for row in rows:
        t = str(row.get("correction_type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    print(f"\n  Total: {total}")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {t:<15} {c}")
    print(f"  Source-verified items: {verified}")


def build_manual_template_bundle(
    amendment_id: str,
    *,
    db_path: Optional[Path] = None,
    include_all: bool = False,
) -> dict:
    """Build a manual-override scaffold bundle for one amendment.

    Returns a typed dict suitable for JSON output or YAML rendering.
    Default behavior only includes items that are still not source-verifiable,
    because those are the cases where manual overrides are usually needed.
    """
    path = db_path or _OFFICIAL_TEXT
    rows = [
        row
        for row in _load_patch_rows(path)
        if str(row.get("amendment_id") or "").strip() == amendment_id
        and str(row.get("lang") or "fi").strip() == "fi"
    ]
    rows.sort(key=lambda r: int(r.get("correction_index") or 0))

    source_xml: bytes | None = None
    try:
        source_amendment_id = None
        num, year = amendment_id.split("/", 1)
        source_amendment_id = f"{year}/{int(num)}"
        source_xml = _read_source_xml(source_amendment_id)
    except (ValueError, OSError, RuntimeError):
        source_xml = None

    manual_entries: list[dict] = []
    if _MANUAL_YAML.exists():
        try:
            loaded = yaml.safe_load(_MANUAL_YAML.read_text(encoding="utf-8")) or []
            if isinstance(loaded, list):
                for entry in loaded:
                    if not isinstance(entry, dict):
                        continue
                    if str(entry.get("amendment_id", "")).strip() == amendment_id:
                        manual_entries.append(entry)
        except (OSError, yaml.YAMLError):
            manual_entries = []

    entries: list[dict] = []
    attachment_only_entry_count = 0
    already_covered = bool(manual_entries) and not include_all
    for row in rows:
        source_pdf = row.get("source_pdf")
        location_desc = row.get("location_desc")
        wrong = str(row.get("wrong_text") or "").strip()
        correct = str(row.get("correct_text") or "").strip()
        corr_type = row.get("correction_type")
        conf = row.get("extraction_source") or row.get("llm_confidence")
        verified = row.get("verified_in_source")
        if not wrong or not correct or wrong == correct:
            continue
        if already_covered:
            continue
        current_verified = _verify_in_source_xml(source_xml, wrong)
        if (
            not include_all
            and current_verified is not True
            and _looks_like_attachment_only_correction(
                location_desc=str(location_desc or ""),
                correction_type=str(corr_type or ""),
                source_xml=source_xml,
            )
        ):
            attachment_only_entry_count += 1
            continue
        if not include_all and current_verified is True:
            continue
        notes = "; ".join(
            part
            for part in [
                f"source_pdf={Path(str(source_pdf or '')).name}" if source_pdf else "",
                f"location={location_desc}" if location_desc else "",
                f"extraction_source={conf}" if conf else "",
                f"db_verified={verified}" if verified is not None else "",
                (
                    f"current_verify={current_verified}"
                    if current_verified is not None
                    else "current_verify=unknown"
                ),
            ]
            if part
        )
        entries.append(
            {
                "amendment_id": amendment_id,
                "wrong_text": wrong,
                "correct_text": correct,
                "correction_type": str(corr_type or "johtolause"),
                "notes": notes,
                "verified": "",
            }
        )

    return {
        "amendment_id": amendment_id,
        "records_path": str(path),
        "include_all": include_all,
        "manual_yaml_path": str(_MANUAL_YAML),
        "manual_entry_count": len(manual_entries),
        "already_covered": already_covered,
        "attachment_only_entry_count": attachment_only_entry_count,
        "entry_count": len(entries),
        "entries": entries,
    }


def _to_db_amendment_id(amendment_id: str) -> str:
    """Normalize amendment ids to DB NUM/YEAR format."""
    value = str(amendment_id or "").strip()
    if not value or "/" not in value:
        return value
    a, b = value.split("/", 1)
    if len(a) == 4 and a.isdigit() and b.isdigit():
        return f"{int(b)}/{a}"
    if len(b) == 4 and a.isdigit() and b.isdigit():
        return f"{int(a)}/{b}"
    return value


def _load_manual_override_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return counts
    if not isinstance(loaded, list):
        return counts
    malformed_indexes: list[int] = []
    for index, entry in enumerate(loaded):
        if not isinstance(entry, dict):
            malformed_indexes.append(index)
            continue
        amendment_id = str(entry.get("amendment_id", "")).strip()
        if amendment_id:
            counts[amendment_id] = counts.get(amendment_id, 0) + 1
    if malformed_indexes:
        indexes = ", ".join(str(index) for index in malformed_indexes)
        raise ValueError(f"manual corrigendum overrides contain non-object entries at indexes: {indexes}")
    return counts


def _load_manual_override_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(loaded, list):
        return []
    entries: list[dict] = []
    malformed_indexes: list[int] = []
    for index, entry in enumerate(loaded):
        if isinstance(entry, dict):
            entries.append(entry)
            continue
        malformed_indexes.append(index)
    if malformed_indexes:
        indexes = ", ".join(str(index) for index in malformed_indexes)
        raise ValueError(f"manual corrigendum overrides contain non-object entries at indexes: {indexes}")
    return entries


def _manual_entry_matches_row(manual_entry: dict, row: dict) -> bool:
    if str(manual_entry.get("amendment_id", "")).strip() != str(row.get("amendment_id") or "").strip():
        return False
    if str(manual_entry.get("wrong_text", "")).strip() != str(row.get("wrong_text") or "").strip():
        return False
    if str(manual_entry.get("correct_text", "")).strip() != str(row.get("correct_text") or "").strip():
        return False
    manual_type = str(manual_entry.get("correction_type") or "johtolause").strip() or "johtolause"
    row_type = str(row.get("correction_type") or "johtolause").strip() or "johtolause"
    return manual_type == row_type


def _xml_corrigendum_meta_by_pdf(records: list[dict], cs) -> dict[str, dict[str, str | None]]:
    xml_meta_by_pdf: dict[str, dict[str, str | None]] = {}
    statute_ids = sorted(
        {
            str(record.get("statute_id") or "").strip()
            for record in records
            if str(record.get("statute_id") or "").strip()
        }
    )
    for sid in statute_ids:
        prefix = f"akn/fi/act/statute-consolidated/{sid}/media/corrigenda/"
        for ref in _get_xml_corrigendum_refs(cs, sid):
            pdf_href = str(ref.get("pdf_href") or "").strip()
            if not pdf_href:
                continue
            xml_meta_by_pdf[prefix + Path(pdf_href).name] = {
                "amendment_id": str(ref.get("ref_text") or "").strip() or None,
                "date_published": str(ref.get("date") or "").strip() or None,
            }
    return xml_meta_by_pdf


def build_source_manifest_records(*, records_path: Optional[Path] = None) -> list[dict]:
    """Build one official provenance record per corrigendum PDF."""
    def _date_sort_key(value: object) -> tuple[int, int, int]:
        text = str(value or "").strip()
        if not text:
            return (9999, 99, 99)
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            y, m, d = text.split("-")
            if y.isdigit() and m.isdigit() and d.isdigit():
                return (int(y), int(m), int(d))
        parts = text.split(".")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            day, month, year = (int(part) for part in parts)
            return (year, month, day)
        return (9999, 99, 99)

    def _amendment_sort_key(value: object) -> tuple[int, int]:
        text = str(value or "").strip()
        if "/" not in text:
            return (9999, 999999)
        a, b = text.split("/", 1)
        if a.isdigit() and b.isdigit() and len(b) == 4:
            return (int(b), int(a))
        if a.isdigit() and b.isdigit() and len(a) == 4:
            return (int(a), int(b))
        return (9999, 999999)

    path = records_path or _OFFICIAL_TEXT
    records = [
        record
        for record in _load_patch_rows(path)
        if str(record.get("lang") or "fi").strip() == "fi"
        and str(record.get("source_pdf") or "").strip()
    ]
    grouped: dict[str, list[dict]] = {}
    for record in records:
        source_pdf = str(record.get("source_pdf") or "").strip()
        grouped.setdefault(source_pdf, []).append(record)

    cs = _make_corpus_store()
    xml_meta_by_pdf = _xml_corrigendum_meta_by_pdf(records, cs)

    manifest: list[dict] = []
    for source_pdf, items in grouped.items():
        items.sort(
            key=lambda item: (
                int(item.get("correction_index") or 0),
                str(item.get("stable_id") or ""),
            )
        )
        first = items[0]
        pdf_bytes = cs.read_corrigendum_media(str(first.get("statute_id") or ""), Path(source_pdf).name)
        xml_meta = xml_meta_by_pdf.get(source_pdf, {})
        amendment_id = (
            str(first.get("amendment_id") or "").strip()
            or str(xml_meta.get("amendment_id") or "").strip()
            or _amendment_id_from_filename(Path(source_pdf).name)
            or ""
        )
        date_published = (
            str(first.get("date_published") or "").strip()
            or str(xml_meta.get("date_published") or "").strip()
        )
        if date_published:
            date_status = "present"
        elif not xml_meta:
            date_status = "no_xml_corrigendum_ref"
        elif not str(xml_meta.get("date_published") or "").strip():
            date_status = "xml_ref_without_date"
        else:
            date_status = "other_missing_date"
        manifest.append(
            {
                "source_pdf": source_pdf,
                "pdf_name": Path(source_pdf).name,
                "statute_id": str(first.get("statute_id") or ""),
                "amendment_id": amendment_id,
                "lang": str(first.get("lang") or ""),
                "date_published": date_published,
                "date_status": date_status,
                "correction_item_count": len(items),
                "sha256": hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes is not None else None,
                "size_bytes": len(pdf_bytes) if pdf_bytes is not None else None,
            }
        )
    return sorted(
        manifest,
        key=lambda item: (
            _date_sort_key(item.get("date_published")),
            _amendment_sort_key(item.get("amendment_id")),
            str(item.get("source_pdf") or ""),
        ),
    )


def build_official_metadata_backfill(*, records_path: Optional[Path] = None) -> dict:
    """Backfill missing official corrigendum metadata from XML refs and filenames."""
    path = records_path or _OFFICIAL_TEXT
    records = load_official_records(path)
    cs = _make_corpus_store()
    xml_meta_by_pdf = _xml_corrigendum_meta_by_pdf(records, cs)
    updated_records: list[dict] = []
    changed_items: list[dict] = []
    residual_missing_date_counts: dict[str, int] = {}
    residual_missing_date_samples: dict[str, list[dict]] = {}
    for record in records:
        updated = dict(record)
        source_pdf = str(record.get("source_pdf") or "").strip()
        pdf_name = Path(source_pdf).name
        xml_meta = xml_meta_by_pdf.get(source_pdf, {})
        before_amendment = str(record.get("amendment_id") or "").strip()
        before_date = str(record.get("date_published") or "").strip()
        after_amendment = (
            before_amendment
            or str(xml_meta.get("amendment_id") or "").strip()
            or _amendment_id_from_filename(pdf_name)
            or ""
        )
        after_date = before_date or str(xml_meta.get("date_published") or "").strip()
        if after_amendment != before_amendment or after_date != before_date:
            updated["amendment_id"] = after_amendment or None
            updated["date_published"] = after_date or None
            changed_items.append(
                {
                    "stable_id": str(record.get("stable_id") or ""),
                    "pdf_name": pdf_name,
                    "source_pdf": source_pdf,
                    "statute_id": str(record.get("statute_id") or ""),
                    "before_amendment_id": before_amendment or None,
                    "after_amendment_id": after_amendment or None,
                    "before_date_published": before_date or None,
                    "after_date_published": after_date or None,
                }
            )
        if not after_date:
            if not xml_meta:
                residual_kind = "no_xml_corrigendum_ref"
            elif not str(xml_meta.get("date_published") or "").strip():
                residual_kind = "xml_ref_without_date"
            else:
                residual_kind = "other_missing_date"
            residual_missing_date_counts[residual_kind] = residual_missing_date_counts.get(residual_kind, 0) + 1
            residual_missing_date_samples.setdefault(residual_kind, [])
            if len(residual_missing_date_samples[residual_kind]) < 8:
                residual_missing_date_samples[residual_kind].append(
                    {
                        "pdf_name": pdf_name,
                        "amendment_id": after_amendment or None,
                        "statute_id": str(record.get("statute_id") or ""),
                    }
                )
        updated_records.append(updated)
    return {
        "records_path": str(path),
        "record_count": len(records),
        "changed_count": len(changed_items),
        "changed_items": changed_items,
        "residual_missing_date_counts": residual_missing_date_counts,
        "residual_missing_date_samples": residual_missing_date_samples,
        "records": updated_records,
    }


def build_provenance_bundle(
    amendment_id: str,
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """Build an amendment-scoped corrigendum provenance and adjudication bundle."""
    path = db_path or _OFFICIAL_TEXT
    rows = [
        row
        for row in _load_patch_rows(path)
        if str(row.get("amendment_id") or "").strip() == amendment_id
        and str(row.get("lang") or "fi").strip() == "fi"
    ]
    rows.sort(
        key=lambda r: (
            int(r.get("correction_index") or 0),
            str(r.get("source_pdf") or ""),
            str(r.get("stable_id") or ""),
        )
    )

    source_xml: bytes | None = None
    try:
        num, year = amendment_id.split("/", 1)
        source_amendment_id = f"{year}/{int(num)}"
        source_xml = _read_source_xml(source_amendment_id)
    except (ValueError, OSError, RuntimeError):
        source_xml = None

    manual_entries = [
        entry
        for entry in _load_manual_override_entries(_MANUAL_YAML)
        if str(entry.get("amendment_id", "")).strip() == amendment_id
    ]

    rendered_rows: list[dict] = []
    for row in rows:
        db_verified = row.get("verified_in_source")
        current_verified = _verify_in_source_xml(source_xml, str(row.get("wrong_text") or ""))
        attachment_only = (
            current_verified is not True
            and _looks_like_attachment_only_correction(
                location_desc=str(row.get("location_desc") or ""),
                correction_type=str(row.get("correction_type") or ""),
                source_xml=source_xml,
            )
        )
        exact_manual_entries = [
            entry for entry in manual_entries if _manual_entry_matches_row(entry, row)
        ]
        exact_manual = bool(exact_manual_entries)
        if current_verified is True:
            status = "source_verified"
        elif exact_manual:
            status = "manual_override_exact"
        elif attachment_only:
            status = "attachment_only"
        elif manual_entries:
            status = "amendment_manually_overridden"
        else:
            status = "open_manual_candidate"

        rendered_rows.append(
            {
                "stable_id": str(row.get("stable_id") or ""),
                "source_pdf": Path(str(row.get("source_pdf") or "")).name,
                "correction_index": int(row.get("correction_index") or 0),
                "correction_type": str(row.get("correction_type") or ""),
                "location_desc": str(row.get("location_desc") or ""),
                "wrong_text": str(row.get("wrong_text") or ""),
                "correct_text": str(row.get("correct_text") or ""),
                "extraction_source": str(row.get("extraction_source") or row.get("llm_confidence") or ""),
                "date_published": str(row.get("date_published") or ""),
                "db_verified": db_verified,
                "current_verified": current_verified,
                "attachment_only": attachment_only,
                "exact_manual_override": exact_manual,
                "manual_override_count_for_amendment": len(manual_entries),
                "status": status,
            }
        )

    return {
        "amendment_id": amendment_id,
        "records_path": str(path),
        "manual_yaml_path": str(_MANUAL_YAML),
        "row_count": len(rendered_rows),
        "verified_count": sum(1 for row in rendered_rows if row["current_verified"] is True),
        "attachment_only_count": sum(1 for row in rendered_rows if row["attachment_only"]),
        "manual_exact_count": sum(1 for row in rendered_rows if row["exact_manual_override"]),
        "open_manual_candidate_count": sum(
            1 for row in rendered_rows if row["status"] == "open_manual_candidate"
        ),
        "manual_entry_count": len(manual_entries),
        "rows": rendered_rows,
    }


def build_overview_bundle(
    *,
    db_path: Optional[Path] = None,
    limit: int = 10,
    live: bool = False,
) -> dict:
    """Build a corpus-level overview of corrigendum adjudication state."""
    path = db_path or _OFFICIAL_TEXT
    records = [
        record
        for record in _load_patch_rows(path)
        if str(record.get("lang") or "fi").strip() == "fi"
    ]
    records.sort(
        key=lambda record: (
            str(record.get("amendment_id") or ""),
            int(record.get("correction_index") or 0),
            str(record.get("source_pdf") or ""),
        )
    )

    manual_entries = _load_manual_override_entries(_MANUAL_YAML)
    manual_counts = _load_manual_override_counts(_MANUAL_YAML)
    source_records = [
        record
        for record in load_source_records(_SOURCES_TEXT)
        if str(record.get("lang") or "fi").strip() == "fi"
    ]
    source_date_status_counts: dict[str, int] = {}
    for record in source_records:
        status = str(record.get("date_status") or "").strip()
        if not status:
            status = "present" if str(record.get("date_published") or "").strip() else "unknown"
        source_date_status_counts[status] = source_date_status_counts.get(status, 0) + 1
    type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    amendment_stats: dict[str, dict] = {}
    source_xml_by_amendment: dict[str, bytes | None] = {}

    for record in records:
        amendment_id = str(record.get("amendment_id") or "").strip()
        if not amendment_id:
            continue
        stored_verified = record.get("verified_in_source")
        current_verified: bool | None
        attachment_only = False
        if live and stored_verified != 1:
            if amendment_id not in source_xml_by_amendment:
                try:
                    num, year = amendment_id.split("/", 1)
                    source_amendment_id = f"{year}/{int(num)}"
                    source_xml_by_amendment[amendment_id] = _read_source_xml(source_amendment_id)
                except (ValueError, OSError, RuntimeError):
                    source_xml_by_amendment[amendment_id] = None
            source_xml = source_xml_by_amendment.get(amendment_id)
            current_verified = _verify_in_source_xml(source_xml, str(record.get("wrong_text") or ""))
            attachment_only = (
                current_verified is not True
                and _looks_like_attachment_only_correction(
                    location_desc=str(record.get("location_desc") or ""),
                    correction_type=str(record.get("correction_type") or ""),
                    source_xml=source_xml,
                )
            )
        elif stored_verified == 1:
            current_verified = True
        elif stored_verified == 0:
            current_verified = False
        else:
            current_verified = None
        exact_manual = any(
            _manual_entry_matches_row(entry, record)
            for entry in manual_entries
            if str(entry.get("amendment_id", "")).strip() == amendment_id
        )

        if current_verified is True:
            status = "source_verified"
        elif exact_manual:
            status = "manual_override_exact"
        elif live and attachment_only:
            status = "attachment_only"
        elif manual_counts.get(amendment_id, 0):
            status = "amendment_manually_overridden"
        elif current_verified is False:
            status = "unresolved_unverified"
        elif current_verified is None:
            status = "unresolved_unreviewed"
        else:
            status = "open_manual_candidate"

        corr_type = str(record.get("correction_type") or "unknown")
        type_counts[corr_type] = type_counts.get(corr_type, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1

        amendment = amendment_stats.setdefault(
            amendment_id,
            {
                "amendment_id": amendment_id,
                "item_count": 0,
                "source_verified": 0,
                "manual_override_exact": 0,
                "attachment_only": 0,
                "amendment_manually_overridden": 0,
                "unresolved_unverified": 0,
                "unresolved_unreviewed": 0,
                "open_manual_candidate": 0,
                "manual_entry_count": manual_counts.get(amendment_id, 0),
            },
        )
        amendment["item_count"] += 1
        amendment[status] += 1

    top_open_manual = sorted(
        (
            amendment
            for amendment in amendment_stats.values()
            if amendment["open_manual_candidate"] > 0
        ),
        key=lambda amendment: (
            -amendment["open_manual_candidate"],
            -amendment["item_count"],
            amendment["amendment_id"],
        ),
    )[: int(limit)]

    top_attachment_only = sorted(
        (
            amendment
            for amendment in amendment_stats.values()
            if amendment["attachment_only"] > 0
        ),
        key=lambda amendment: (
            -amendment["attachment_only"],
            -amendment["item_count"],
            amendment["amendment_id"],
        ),
    )[: int(limit)]

    top_unresolved = sorted(
        (
            amendment
            for amendment in amendment_stats.values()
            if amendment["unresolved_unverified"] > 0 or amendment["unresolved_unreviewed"] > 0
        ),
        key=lambda amendment: (
            -(amendment["unresolved_unverified"] + amendment["unresolved_unreviewed"]),
            -amendment["item_count"],
            amendment["amendment_id"],
        ),
    )[: int(limit)]

    return {
        "mode": "live" if live else "stored",
        "records_path": str(path),
        "manual_yaml_path": str(_MANUAL_YAML),
        "official_item_count": len(records),
        "amendment_count": len(amendment_stats),
        "source_pdf_count": len({str(record.get("source_pdf") or "") for record in records if record.get("source_pdf")}),
        "missing_amendment_id_count": sum(
            1 for record in records if not str(record.get("amendment_id") or "").strip()
        ),
        "missing_date_published_count": sum(
            1 for record in records if not str(record.get("date_published") or "").strip()
        ),
        "source_date_status_counts": dict(sorted(source_date_status_counts.items())),
        "type_counts": dict(sorted(type_counts.items())),
        "status_counts": status_counts,
        "top_unresolved_amendments": top_unresolved,
        "top_open_manual_amendments": top_open_manual,
        "top_attachment_only_amendments": top_attachment_only,
    }


def build_review_bundle(
    statute_id: str,
    *,
    mode: str = "legal_pit",
    db_path: Optional[Path] = None,
) -> dict:
    """Build a review bundle joining live disagreements to corrigendum evidence."""
    from lawvm.tools.oracle_check import _classify_statute

    result = _classify_statute(statute_id, cast(Literal["finlex_oracle", "legal_pit"], mode))
    if not result:
        raise SystemExit(f"Could not classify statute {statute_id}")
    if result.error:
        raise SystemExit(str(result.error))

    dbp = db_path or _OFFICIAL_TEXT
    manual_counts = _load_manual_override_counts(_MANUAL_YAML)
    amendment_groups: dict[str, dict] = {}
    unblamed_sections: list[dict] = []
    all_sections: list[dict] = []
    records = _load_patch_rows(dbp)

    def _chapter_label_for_key(section_key: str) -> str:
        for chunk in section_key.split("/"):
            if chunk.startswith("chapter:"):
                return norm_section_label(chunk.split(":", 1)[1])
        return ""

    def _section_matches_target(
        section_key: str,
        *,
        target_kind: str,
        target_label: str,
    ) -> bool:
        target_kind = str(target_kind or "").strip().upper()
        target_label = str(target_label or "").strip()
        if not section_key or not target_kind or not target_label:
            return False
        if target_kind == "P":
            m = re.match(r"^(\d+[a-z]*)\s*§", target_label, flags=re.I)
            if not m:
                return False
            return leaf_section_label(section_key) == norm_section_label(m.group(1))
        if target_kind == "L":
            m = re.match(r"^(\d+[a-z]*)\s*luku", target_label, flags=re.I)
            if not m:
                return False
            return _chapter_label_for_key(section_key) == norm_section_label(m.group(1))
        return False

    def _ensure_amendment_group(amendment_id: str, blame_title: str = "") -> dict:
        return amendment_groups.setdefault(
            amendment_id,
            {
                "amendment_id": amendment_id,
                "db_amendment_id": _to_db_amendment_id(amendment_id),
                "blame_title": blame_title,
                "sections": [],
                "corrigendum_db_rows": 0,
                "corrigendum_no_match_rows": 0,
                "corrigendum_verified_rows": 0,
                "corrigendum_types": [],
                "corrigendum_pdfs": [],
                "manual_override_count": manual_counts.get(_to_db_amendment_id(amendment_id), 0),
                "manual_template_entry_count": 0,
                "relevance_kinds": [],
                "source_pathology_codes": [],
                "source_pathology_targets": [],
                "source_pathology_details": [],
                "linked_sections": [],
                "contingent_effective": False,
            },
        )

    for sec in result.section_results:
        section = str(sec.get("section") or "")
        diagnosis = str(sec.get("diagnosis") or "")
        blame_source = str(sec.get("blame_source") or "")
        blame_title = str(sec.get("blame_title") or "")
        oracle_version = str(sec.get("oracle_version") or "")
        sec_view = {
            "section": section,
            "diagnosis": diagnosis,
            "oracle_version": oracle_version,
        }
        all_sections.append(sec_view)
        if not blame_source:
            unblamed_sections.append(sec_view)
            continue

        amendment = _ensure_amendment_group(blame_source, blame_title)
        if "blame" not in amendment["relevance_kinds"]:
            amendment["relevance_kinds"].append("blame")
        amendment["sections"].append(sec_view)

    for pathology in result.source_pathologies:
        if not isinstance(pathology, dict):
            continue
        source_statute = str(pathology.get("source_statute") or "")
        if not source_statute:
            continue
        amendment = _ensure_amendment_group(source_statute)
        if "source_pathology" not in amendment["relevance_kinds"]:
            amendment["relevance_kinds"].append("source_pathology")
        code = str(pathology.get("code") or "")
        target_kind = str(pathology.get("target_kind") or "")
        target_label = str(pathology.get("target_label") or "")
        if code and code not in amendment["source_pathology_codes"]:
            amendment["source_pathology_codes"].append(code)
        if target_label and target_label not in amendment["source_pathology_targets"]:
            amendment["source_pathology_targets"].append(target_label)
        detail = {
            "code": code,
            "target_kind": target_kind,
            "target_label": target_label,
            "message": str(pathology.get("message") or ""),
        }
        if detail not in amendment["source_pathology_details"]:
            amendment["source_pathology_details"].append(detail)
        if target_kind and target_label:
            for sec in all_sections:
                if _section_matches_target(
                    str(sec.get("section") or ""),
                    target_kind=target_kind,
                    target_label=target_label,
                ):
                    linked = {
                        "section": str(sec.get("section") or ""),
                        "diagnosis": str(sec.get("diagnosis") or ""),
                        "oracle_version": str(sec.get("oracle_version") or ""),
                        "why": f"{code} {target_label}".strip(),
                    }
                    if linked not in amendment["linked_sections"]:
                        amendment["linked_sections"].append(linked)

    for source_statute in result.contingent_effective_sources:
        source_statute = str(source_statute or "")
        if not source_statute:
            continue
        amendment = _ensure_amendment_group(source_statute)
        if "contingent_effective" not in amendment["relevance_kinds"]:
            amendment["relevance_kinds"].append("contingent_effective")
        amendment["contingent_effective"] = True

    for amendment in amendment_groups.values():
        matched_rows = [
            row
            for row in records
            if str(row.get("amendment_id") or "") == amendment["db_amendment_id"]
            and str(row.get("lang") or "fi") == "fi"
        ]
        amendment["corrigendum_db_rows"] = len(matched_rows)
        amendment["corrigendum_no_match_rows"] = sum(
            1 for row in matched_rows if row.get("verified_in_source") == 0
        )
        amendment["corrigendum_verified_rows"] = sum(
            1 for row in matched_rows if row.get("verified_in_source") == 1
        )
        amendment["corrigendum_types"] = sorted(
            {str(row.get("correction_type") or "") for row in matched_rows if row.get("correction_type")}
        )
        amendment["corrigendum_pdfs"] = sorted(
            {Path(str(row.get("source_pdf") or "")).name for row in matched_rows if row.get("source_pdf")}
        )
        template_bundle = build_manual_template_bundle(
            amendment["db_amendment_id"],
            db_path=dbp,
            include_all=False,
        )
        amendment["manual_template_entry_count"] = int(template_bundle.get("entry_count", 0))

    return {
        "statute_id": statute_id,
        "mode": mode,
        "title": str(result.title or ""),
        "overall_score": float(result.overall_score or 0.0),
        "section_score": float(result.section_score or 0.0),
        "source_pathologies": list(result.source_pathologies or []),
        "contingent_effective_sources": list(result.contingent_effective_sources or []),
        "amendments": sorted(
            amendment_groups.values(),
            key=lambda a: (
                -len(a["sections"]),
                -len(a["linked_sections"]),
                -a["corrigendum_no_match_rows"],
                -len(a["source_pathology_codes"]),
                a["amendment_id"],
            ),
        ),
        "unblamed_sections": unblamed_sections,
    }


def list_open_manual_candidates(
    *,
    db_path: Optional[Path] = None,
    limit: int = 20,
    include_all: bool = False,
) -> list[dict]:
    path = db_path or _OFFICIAL_TEXT
    records = [
        row
        for row in _load_patch_rows(path)
        if str(row.get("lang") or "fi") == "fi"
    ]
    grouped: dict[str, list[dict]] = {}
    for row in records:
        amendment_id = str(row.get("amendment_id") or "")
        if not amendment_id:
            continue
        grouped.setdefault(amendment_id, []).append(row)
    ranked = sorted(
        (
            (
                amendment_id,
                len(rows),
                sum(1 for row in rows if row.get("verified_in_source") == 0),
            )
            for amendment_id, rows in grouped.items()
        ),
        key=lambda item: (-item[2], -item[1], item[0]),
    )[: int(limit)]

    out: list[dict] = []
    for amendment_id, row_count, db_no_match_rows in ranked:
        bundle = build_manual_template_bundle(
            str(amendment_id),
            db_path=path,
            include_all=False,
        )
        out.append(
            {
                "amendment_id": str(amendment_id),
                "db_row_count": int(row_count or 0),
                "db_no_match_rows": int(db_no_match_rows or 0),
                "open_manual_rows": int(bundle.get("entry_count", 0) or 0),
                "attachment_only_rows": int(bundle.get("attachment_only_entry_count", 0) or 0),
                "manual_entry_count": int(bundle.get("manual_entry_count", 0) or 0),
            }
        )
    if include_all:
        return out
    return [
        row
        for row in out
        if row["open_manual_rows"] > 0 and row["manual_entry_count"] == 0
    ]


def _cmd_manual_template(args) -> None:
    bundle = build_manual_template_bundle(
        args.amendment_id,
        db_path=Path(args.db) if getattr(args, "db", None) else None,
        include_all=bool(getattr(args, "all", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    if bundle.get("manual_entry_count"):
        print(
            f"# NOTE: {bundle['amendment_id']} already has "
            f"{bundle['manual_entry_count']} manual override "
            f"entr{'y' if bundle['manual_entry_count'] == 1 else 'ies'} "
            f"in {bundle['manual_yaml_path']}\n"
        )
    if bundle.get("already_covered"):
        print(
            f"No manual-template items for {bundle['amendment_id']} "
            f"(manual override already covers this amendment; use --all to inspect classified items)."
        )
        return
    if not bundle["entries"]:
        if bundle.get("attachment_only_entry_count"):
            print(
                f"No manual-template items for {bundle['amendment_id']} "
                f"(default filtering skipped {bundle['attachment_only_entry_count']} "
                f"attachment-only item"
                f"{'' if bundle['attachment_only_entry_count'] == 1 else 's'} "
                f"that are outside source XML patch scope)."
            )
            return
        print(
            f"No manual-template items for {bundle['amendment_id']} "
            f"({'including all items' if bundle['include_all'] else 'all items already verify in source'})."
        )
        return
    print(
        f"# Manual corrigendum scaffold for {bundle['amendment_id']}\n"
        f"# Generated from {bundle['records_path']}\n"
        f"# Entries here replace loaded classified items for this amendment_id.\n"
    )
    print(
        yaml.safe_dump(
            bundle["entries"],
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).strip()
    )


def _cmd_open_manual(args) -> None:
    rows = list_open_manual_candidates(
        db_path=Path(args.db) if getattr(args, "db", None) else None,
        limit=int(getattr(args, "limit", 20) or 20),
        include_all=bool(getattr(args, "all", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("No open manual-corrigendum candidates.")
        return
    print("AMENDMENT    ITEMS  UNVERIFIED  OPEN_MANUAL  ATTACHMENT_ONLY  MANUAL_ENTRIES")
    print("-" * 82)
    for row in rows:
        print(
            f"{row['amendment_id']:<12} "
            f"{row['db_row_count']:>7}  "
            f"{row['db_no_match_rows']:>11}  "
            f"{row['open_manual_rows']:>11}  "
            f"{row['attachment_only_rows']:>15}  "
            f"{row['manual_entry_count']:>14}"
        )


def _cmd_overview(args) -> None:
    bundle = build_overview_bundle(
        db_path=Path(args.db) if getattr(args, "db", None) else None,
        limit=int(getattr(args, "limit", 10) or 10),
        live=bool(getattr(args, "live", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return

    print(f"Records      : {bundle['records_path']}")
    print(f"Manual YAML  : {bundle['manual_yaml_path']}")
    print(f"Mode         : {bundle['mode']}")
    print(f"Official     : {bundle['official_item_count']} items")
    print(f"Amendments   : {bundle['amendment_count']}")
    print(f"PDFs         : {bundle['source_pdf_count']}")
    print(f"Missing amend: {bundle['missing_amendment_id_count']}")
    print(f"Missing date : {bundle['missing_date_published_count']}")
    source_date_status_counts = bundle.get("source_date_status_counts", {})
    if source_date_status_counts:
        print(
            "Source date  : "
            + ", ".join(
                f"{status}={count}"
                for status, count in sorted(source_date_status_counts.items())
            )
        )
    print()
    print("Status counts:")
    for key in [
        "source_verified",
        "manual_override_exact",
        "amendment_manually_overridden",
        "attachment_only",
        "unresolved_unverified",
        "unresolved_unreviewed",
        "open_manual_candidate",
    ]:
        print(f"  {key:<29} {bundle['status_counts'].get(key, 0)}")
    print()
    print("Type counts:")
    for corr_type, count in sorted(
        bundle["type_counts"].items(),
        key=lambda item: (-item[1], item[0]),
    ):
        print(f"  {corr_type:<16} {count}")
    if bundle["top_unresolved_amendments"]:
        print()
        print("Top unresolved amendments:")
        for item in bundle["top_unresolved_amendments"]:
            print(
                f"  {item['amendment_id']:<12} "
                f"unverified={item['unresolved_unverified']} "
                f"unreviewed={item['unresolved_unreviewed']} "
                f"items={item['item_count']} "
                f"manual={item['manual_entry_count']}"
            )
    if bundle["top_open_manual_amendments"]:
        print()
        print("Top open-manual amendments:")
        for item in bundle["top_open_manual_amendments"]:
            print(
                f"  {item['amendment_id']:<12} "
                f"open={item['open_manual_candidate']} "
                f"items={item['item_count']} "
                f"manual={item['manual_entry_count']}"
            )
    if bundle["top_attachment_only_amendments"]:
        print()
        print("Top attachment-only amendments:")
        for item in bundle["top_attachment_only_amendments"]:
            print(
                f"  {item['amendment_id']:<12} "
                f"attachment={item['attachment_only']} "
                f"items={item['item_count']}"
            )


def _cmd_sources(args) -> None:
    official_path = Path(args.db) if getattr(args, "db", None) else None
    stored_path = _SOURCES_TEXT
    refreshed = bool(getattr(args, "refresh", False))
    if refreshed:
        records = build_source_manifest_records(records_path=official_path)
        write_source_records(records, stored_path)
        mode = "refreshed"
    else:
        records = load_source_records(stored_path)
        if records:
            mode = "stored"
        else:
            records = build_source_manifest_records(records_path=official_path)
            mode = "ephemeral"

    limit = int(getattr(args, "limit", 10) or 10)
    shown = records[:limit] if limit > 0 else records
    bundle = {
        "mode": mode,
        "official_records_path": str(official_path or _OFFICIAL_TEXT),
        "source_records_path": str(stored_path),
        "pdf_count": len(records),
        "amendment_count": len(
            {str(record.get("amendment_id") or "") for record in records if record.get("amendment_id")}
        ),
        "total_item_count": sum(int(record.get("correction_item_count") or 0) for record in records),
        "date_status_counts": {
            status: sum(1 for record in records if str(record.get("date_status") or "").strip() == status)
            for status in sorted(
                {str(record.get("date_status") or "").strip() for record in records if str(record.get("date_status") or "").strip()}
            )
        },
        "records": shown,
    }
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(f"Mode         : {bundle['mode']}")
    print(f"Official     : {bundle['official_records_path']}")
    print(f"Sources      : {bundle['source_records_path']}")
    print(f"PDFs         : {bundle['pdf_count']}")
    print(f"Amendments   : {bundle['amendment_count']}")
    print(f"Items        : {bundle['total_item_count']}")
    if bundle["date_status_counts"]:
        print(
            "Date status  : "
            + ", ".join(
                f"{status}={count}"
                for status, count in sorted(bundle["date_status_counts"].items())
            )
        )
    if not shown:
        return
    print()
    print("DATE         AMENDMENT    ITEMS  SIZE     SHA256       PDF")
    print("-" * 88)
    for record in shown:
        sha = str(record.get("sha256") or "")
        size_bytes = record.get("size_bytes")
        size_text = str(size_bytes) if size_bytes is not None else "?"
        date_text = str(record.get("date_published") or "?")
        amendment_text = str(record.get("amendment_id") or "?")
        print(
            f"{date_text:<12} "
            f"{amendment_text:<12} "
            f"{int(record.get('correction_item_count') or 0):>5}  "
            f"{size_text:>7}  "
            f"{sha[:10]:<10}  "
            f"{str(record.get('pdf_name') or '')}"
        )


def _cmd_backfill_meta(args) -> None:
    official_path = Path(args.db) if getattr(args, "db", None) else None
    bundle = build_official_metadata_backfill(records_path=official_path)
    if getattr(args, "update", False):
        target = official_path or _OFFICIAL_TEXT
        write_official_records(bundle["records"], target)
        bundle["updated_path"] = str(target)
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(f"Official     : {bundle['records_path']}")
    print(f"Items        : {bundle['record_count']}")
    print(f"Changed      : {bundle['changed_count']}")
    if getattr(args, "update", False):
        print(f"Updated path : {bundle['updated_path']}")
    residual_counts = bundle.get("residual_missing_date_counts", {})
    if residual_counts:
        print("Missing date : " + ", ".join(
            f"{kind}={count}" for kind, count in sorted(residual_counts.items())
        ))
    if not bundle["changed_items"]:
        print("No metadata backfill changes.")
        return
    print()
    print("PDF                   AMENDMENT              DATE")
    print("-" * 88)
    for item in bundle["changed_items"][:20]:
        before_amend = item["before_amendment_id"] or "?"
        after_amend = item["after_amendment_id"] or "?"
        before_date = item["before_date_published"] or "?"
        after_date = item["after_date_published"] or "?"
        print(
            f"{item['pdf_name'][:20]:<20}  "
            f"{before_amend:<12} -> {after_amend:<12}  "
            f"{before_date:<12} -> {after_date:<12}"
        )


def _cmd_provenance(args) -> None:
    bundle = build_provenance_bundle(
        args.amendment_id,
        db_path=Path(args.db) if getattr(args, "db", None) else None,
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    if not bundle["rows"]:
        print(f"No corrigendum items for {bundle['amendment_id']}.")
        return

    print(f"Amendment    : {bundle['amendment_id']}")
    print(f"Items        : {bundle['row_count']}")
    print(f"Verified     : {bundle['verified_count']}")
    print(f"Manual exact : {bundle['manual_exact_count']}")
    print(f"Attachment   : {bundle['attachment_only_count']}")
    print(f"Open manual  : {bundle['open_manual_candidate_count']}")
    print(f"Manual YAML  : {bundle['manual_entry_count']} entries in {bundle['manual_yaml_path']}")
    print()
    print(
        "IDX  TYPE          DB  CUR  STATUS                      PDF                   LOCATION"
    )
    print("-" * 108)
    for row in bundle["rows"]:
        db_verified = {1: "✓", 0: "✗", None: "?"}.get(row["db_verified"], "?")
        current_verified = {True: "✓", False: "✗", None: "?"}.get(row["current_verified"], "?")
        print(
            f"{row['correction_index']:>3}  "
            f"{row['correction_type'][:12]:<12}  "
            f"{db_verified:>2}  "
            f"{current_verified:>3}  "
            f"{row['status'][:26]:<26}  "
            f"{row['source_pdf'][:20]:<20}  "
            f"{row['location_desc'][:40]}"
        )


def _cmd_review(args) -> None:
    bundle = build_review_bundle(
        args.statute_id,
        mode=getattr(args, "mode", "legal_pit"),
        db_path=Path(args.db) if getattr(args, "db", None) else None,
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return

    print(f"Statute      : {bundle['statute_id']}")
    print(f"Title        : {bundle['title']}")
    print(f"Mode         : {bundle['mode']}")
    print(f"Overall      : {bundle['overall_score']:.1%}")
    print(f"Section score: {bundle['section_score']:.1%}")
    if bundle["source_pathologies"]:
        codes = sorted(
            {
                str(p.get("code") or "")
                for p in bundle["source_pathologies"]
                if isinstance(p, dict) and str(p.get("code") or "")
            }
        )
        if codes:
            print(f"Pathologies  : {', '.join(codes)}")
    if bundle["contingent_effective_sources"]:
        print(f"Contingent   : {', '.join(bundle['contingent_effective_sources'])}")

    print()
    print(f"Related amendments: {len(bundle['amendments'])}")
    for item in bundle["amendments"]:
        reasons = ", ".join(item["relevance_kinds"]) if item.get("relevance_kinds") else "unknown"
        print(
            f"  {item['amendment_id']}  "
            f"sections={len(item['sections'])}  "
            f"linked={len(item.get('linked_sections', []))}  "
            f"db_rows={item['corrigendum_db_rows']}  "
            f"no_match={item['corrigendum_no_match_rows']}  "
            f"manual={item['manual_override_count']}  "
            f"manual_open={item['manual_template_entry_count']}"
        )
        print(f"    reasons: {reasons}")
        if item.get("blame_title"):
            print(f"    {item['blame_title']}")
        if item["corrigendum_types"]:
            print(f"    types: {', '.join(item['corrigendum_types'])}")
        if item["corrigendum_pdfs"]:
            print(f"    pdfs : {', '.join(item['corrigendum_pdfs'][:4])}")
        if item["source_pathology_codes"]:
            print(f"    pathologies: {', '.join(sorted(item['source_pathology_codes']))}")
        if item["source_pathology_targets"]:
            print(f"    targets: {', '.join(item['source_pathology_targets'][:4])}")
        if item["contingent_effective"]:
            print("    contingent-effective-date source")
        for sec in item["sections"]:
            oracle_version = sec.get("oracle_version") or ""
            oracle_suffix = f" oracle={oracle_version}" if oracle_version else ""
            print(f"    - {sec['section']}: {sec['diagnosis']}{oracle_suffix}")
        if item.get("linked_sections"):
            print("    related current sections:")
            for sec in item["linked_sections"]:
                oracle_version = sec.get("oracle_version") or ""
                oracle_suffix = f" oracle={oracle_version}" if oracle_version else ""
                why = f" via {sec['why']}" if sec.get("why") else ""
                print(
                    f"      - {sec['section']}: {sec['diagnosis']}{oracle_suffix}{why}"
                )

    if bundle["unblamed_sections"]:
        print()
        print("Unblamed sections:")
        for sec in bundle["unblamed_sections"]:
            oracle_version = sec.get("oracle_version") or ""
            oracle_suffix = f" oracle={oracle_version}" if oracle_version else ""
            print(f"  - {sec['section']}: {sec['diagnosis']}{oracle_suffix}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _corrigendum_locators_for_sid(cs, sid: str) -> list[str]:
    return list_cached_corrigendum_locators(cs, sid)

def _parse_corrigendum_xml_refs(xml_bytes: bytes) -> list[dict]:
    """Extract <finlex:corrigendum> metadata from XML bytes without lxml."""
    refs = []
    # Find all corrigendum blocks
    for block in re.finditer(
        rb"<finlex:corrigendum[^>]*>(.*?)</finlex:corrigendum>",
        xml_bytes,
        re.DOTALL,
    ):
        inner = block.group(1)
        href_m = re.search(rb'href="([^"]+\.pdf)"', inner)
        date_m = re.search(rb'<finlex:datePublished[^>]*>([^<]+)</finlex:datePublished>', inner)
        ref_m = re.search(rb'<finlex:ref[^>]*>([^<]+)</finlex:ref>', inner)
        refs.append({
            "pdf_href": href_m.group(1).decode() if href_m else None,
            "date": date_m.group(1).decode().strip() if date_m else None,
            "ref_text": ref_m.group(1).decode().strip() if ref_m else None,
        })
    return refs


def _get_xml_corrigendum_refs(cs, sid: str) -> list[dict]:
    """Read best oracle XML and extract corrigendum metadata."""
    oracle_path = get_oracle_path(
        sid,
        cs,
        selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
    )
    xml_bytes = cs.read_locator(oracle_path) if oracle_path else None
    if not xml_bytes:
        return []
    try:
        return _parse_corrigendum_xml_refs(xml_bytes)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        return []


_PDF_MAX_PAGES = 10  # corrigendum notices are short; more pages = bulk/liite/translation


def _pdf_page_count(pdf_bytes: bytes) -> int | None:
    """Return page count via pdfinfo. Returns None if unavailable."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name
        result = subprocess.run(
            ["pdfinfo", tmp_path],
            capture_output=True,
            timeout=10,
        )
        Path(tmp_path).unlink(missing_ok=True)
        if result.returncode != 0:
            return None
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
        return None
    except (NameError, TypeError, AttributeError):
        raise
    except Exception:
        return None


_PDF_EXTRACT_MAX_PAGES = 3  # extract up to N pages; liite truncation cuts appendix body


def _pdf_to_text(pdf_bytes: bytes, max_pages: int = _PDF_EXTRACT_MAX_PAGES) -> Optional[str]:
    """Extract text from first max_pages pages using pdftotext (poppler)."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name
        result = subprocess.run(
            ["pdftotext", "-l", str(max_pages), tmp_path, "-"],
            capture_output=True,
            timeout=30,
        )
        Path(tmp_path).unlink(missing_ok=True)
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
        return None
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except FileNotFoundError:
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def _cmd_status(args) -> None:
    sid = getattr(args, "statute_id", None)
    cs = _make_corpus_store()
    if sid:
        _status_single(cs, sid)
    else:
        _status_corpus(cs)


def _status_single(cs, sid: str) -> None:
    pdf_paths = _corrigendum_locators_for_sid(cs, sid)
    xml_refs = _get_xml_corrigendum_refs(cs, sid)

    print(f"=== Corrigendum: {sid} ===")
    print(f"  PDFs in corpus : {len(pdf_paths)}")
    print(f"  XML refs       : {len(xml_refs)}")

    if xml_refs:
        print("\n  XML references:")
        for r in xml_refs:
            print(f"    [{r.get('date', '?')}]  {r.get('ref_text', '?')}  →  {r.get('pdf_href', '?')}")

    if pdf_paths:
        print("\n  PDF files in corpus:")
        for p in pdf_paths:
            print(f"    {Path(p).name:<40}")

    if not pdf_paths and not xml_refs:
        print("  No corrigenda found.")


def _status_corpus(cs) -> None:
    from collections import Counter, defaultdict

    by_sid: dict[str, list[str]] = defaultdict(list)
    for name in list_cached_corrigendum_locators(cs):
        if name.endswith(".pdf"):
            m = _SID_RE.match(name)
            if m:
                by_sid[m.group(1)].append(name)

    total_pdfs = sum(len(v) for v in by_sid.values())
    n_statutes = len(by_sid)

    by_decade: Counter = Counter()
    for sid in by_sid:
        year = int(sid.split("/")[0])
        by_decade[(year // 10) * 10] += len(by_sid[sid])

    print("=== Corrigendum Corpus Summary ===")
    print(f"  Total corrigendum PDFs : {total_pdfs}")
    print(f"  Statutes affected      : {n_statutes}")
    print()
    print("  By decade:")
    for decade in sorted(by_decade):
        print(f"    {decade}s : {by_decade[decade]:>5} PDFs")

    top = sorted(by_sid.items(), key=lambda x: -len(x[1]))[:15]
    print("\n  Most-corrected statutes (top 15):")
    for sid, pdfs in top:
        print(f"    {sid:<20}  {len(pdfs)} corrigenda")


# ---------------------------------------------------------------------------
# Apply command
# ---------------------------------------------------------------------------

def _cmd_apply(args) -> None:
    sid = args.statute_id
    save_path = getattr(args, "save", None)

    cs = _make_corpus_store()
    pdf_paths = _corrigendum_locators_for_sid(cs, sid)

    if not pdf_paths:
        print(f"No corrigendum PDFs found for {sid}.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(pdf_paths)} corrigendum PDF(s) for {sid}:")
    for p in pdf_paths:
        print(f"  {p}")

    print()
    print("NOTE: Automated merging into statute XML is not yet implemented.")
    print("      This command extracts the PDF and shows its text for manual review.")
    print("      Future: OCR → parse corrections → emit LegalOperation[] → replay.")
    print()

    for pdf_locator in pdf_paths:
        pdf_bytes = cs.read_corrigendum_media(sid, Path(pdf_locator).name)
        if pdf_bytes is None:
            print(f"  WARNING: missing corrigendum PDF in corpus: {pdf_locator}", file=sys.stderr)
            continue
        pdf_name = Path(pdf_locator).name
        print(f"=== {pdf_name} ({len(pdf_bytes) // 1024} KB) ===")

        if save_path:
            out = Path(save_path)
            out.write_bytes(pdf_bytes)
            print(f"  Saved to: {out}")

        text = _pdf_to_text(pdf_bytes)
        if text:
            print()
            print(text[:3000])
            if len(text) > 3000:
                print(f"  ... [{len(text) - 3000} more chars, use --save to get full PDF]")
        else:
            print("  (pdftotext not available — install poppler-utils to extract text)")
        print()


# ---------------------------------------------------------------------------
# Reextract command (LLM disambiguation for no-match patches)
# ---------------------------------------------------------------------------

# Phase 1 system: line-number output (max ~10 tokens output)
_REEXTRACT_SPAN_SYSTEM = """Sinulle annetaan numeroituja tekstirivejä muutossäädöksestä ja virheellinen teksti (VIRH).
Etsi se rivi joka sisältää VIRH:n tai vastaavan tekstin.

Tulosta VAIN rivin numero. Esim: 42
Jos ei löydy: 0
Ei muuta tekstiä."""

# Phase 2 system: extract exact span (fallback, more tokens)
_REEXTRACT_TEXT_SYSTEM = """Sinulle annetaan yksi rivi muutossäädöksestä ja virheellinen teksti (VIRH).
Kopioi riviltä TÄSMÄLLEEN se osa joka vastaa VIRH:tä. Älä kopioi XML-tageja.

Tulosta VAIN löydetty teksti. Jos ei löydy: EI LÖYDY"""


async def _reextract_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    amendment_id: str,   # YEAR/NUM
    wrong_text: str,
    correct_text: str,
    op_id: str,
    xml_bytes: bytes,
    pdf_bytes: Optional[bytes],
) -> dict:
    """Two-phase span-based reextraction.

    Phase 1: Give LLM numbered plain-text lines, ask for line number only (~5 tokens).
    Phase 2: Give LLM that single line, ask for exact span text (~30 tokens).
    The actual wrong_text for the DB is extracted from XML bytes at the found position
    using fuzzy matching — not from LLM text generation.
    """

    # Build numbered plain-text lines from XML
    xml_decoded = xml_bytes.decode("utf-8", errors="replace")
    xml_plain_ctx = re.sub(r"<[^>]+>", " ", xml_decoded)
    xml_plain_ctx = re.sub(r"\s+", " ", xml_plain_ctx).strip()
    # Split into non-empty lines
    lines = [ln.strip() for ln in xml_plain_ctx.split("  ") if ln.strip()]
    # Numbered lines for LLM
    numbered = "\n".join(f"[{i+1}] {ln[:200]}" for i, ln in enumerate(lines[:150]))

    # Prepare PDF text (optional context)
    pdf_text = ""
    if pdf_bytes:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_bytes)
                tmp = f.name
            r = subprocess.run(["pdftotext", tmp, "-"], capture_output=True, timeout=30)
            Path(tmp).unlink(missing_ok=True)
            if r.returncode == 0:
                pdf_text = r.stdout.decode("utf-8", errors="replace")[:1000]
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass

    # ---- Phase 1: line identification ----
    user_p1 = (
        f"Rivit:\n{numbered}\n\n"
        f"VIRH: {wrong_text[:200]}\n"
        f"PDF-konteksti: {pdf_text[:300] if pdf_text else '(ei)'}"
    )
    async with sem:
        try:
            async with session.post(
                _LLAMA_URL,
                json={
                    "messages": [
                        {"role": "system", "content": _REEXTRACT_SPAN_SYSTEM},
                        {"role": "user", "content": user_p1[:6000]},
                    ],
                    "max_tokens": 10,
                    "temperature": 0,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
            raw_p1 = data["choices"][0]["message"].get("content", "").strip()
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception as e:
            return {"op_id": op_id, "raw": "", "parsed": None, "error": f"phase1: {e}"}

    # Parse line number
    line_num_match = re.search(r"\b(\d+)\b", raw_p1)
    line_num = int(line_num_match.group(1)) if line_num_match else 0
    if line_num == 0 or line_num > len(lines):
        return {"op_id": op_id, "raw": raw_p1, "parsed": {"found": False}, "error": None}

    target_line = lines[line_num - 1]

    # ---- Phase 2: extract exact span from target line ----
    user_p2 = f"Rivi: {target_line[:500]}\n\nVIRH: {wrong_text[:200]}"
    async with sem:
        try:
            async with session.post(
                _LLAMA_URL,
                json={
                    "messages": [
                        {"role": "system", "content": _REEXTRACT_TEXT_SYSTEM},
                        {"role": "user", "content": user_p2[:2000]},
                    ],
                    "max_tokens": 150,
                    "temperature": 0,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
            raw_p2 = data["choices"][0]["message"].get("content", "").strip()
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception as e:
            return {"op_id": op_id, "raw": raw_p1, "parsed": None, "error": f"phase2: {e}"}

    # Strip any tags from the phase-2 response
    new_wrong = re.sub(r"<[^>]+>", "", raw_p2).strip()

    if new_wrong.startswith("EI LÖYDY") or new_wrong.startswith("EI LOYDY") or not new_wrong:
        return {"op_id": op_id, "raw": f"L{line_num}: {raw_p2}", "parsed": {"found": False}, "error": None}

    parsed = {
        "found": True,
        "wrong_text_in_xml": new_wrong,
        "correct_text": correct_text,
        "line_num": line_num,
        "target_line": target_line,
    }
    return {"op_id": op_id, "raw": f"L{line_num}: {raw_p2}", "parsed": parsed, "error": None}


async def _reextract_batch(
    candidates: list[dict],
    concurrency: int = 4,
) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        tasks = [
            _reextract_one(
                session, sem,
                c["amendment_id"], c["wrong"], c["correct"], c["op_id"],
                c["xml_bytes"], c.get("pdf_bytes"),
            )
            for c in candidates
        ]
        return await asyncio.gather(*tasks)


def _cmd_reextract(args) -> None:
    """LLM-assisted reextraction for no-match corrigendum patches.

    Finds patches where wrong_text doesn't appear in the amendment XML (even after
    all 6 apply passes), then calls the local LLM with both the PDF and XML for
    context to find the correct wrong_text in the XML.

    Updates the git official corpus in place if --update is given, with sqlite
    mirrored only as a convenience artifact when it exists.
    """
    sys.path.insert(0, str(_LAWVM_DIR / "src"))
    from lawvm.finland.corrigendum import (
        CorrigendumPatchTable, _apply_text_replace, _to_grafter_mid as _to_grafter_amendment_id,
    )

    limit = getattr(args, "limit", None)
    update_db = getattr(args, "update", False)
    verbose = getattr(args, "verbose", False)

    # --- Find no-match patches ---
    pt = CorrigendumPatchTable.load_from_source()
    all_patches = []
    for amendment_id, ops in pt._patches.items():
        for op in ops:
            patch = op.text_patch
            if patch is None or patch.replacement is None:
                continue
            all_patches.append(
                (
                    amendment_id,
                    patch.selector.match_text,
                    patch.replacement,
                    op.op_id,
                )
            )

    pdf_map: dict[str, str] = {}
    for row in _load_patch_rows():
        amid = row.get("amendment_id")
        idx = row.get("correction_index")
        spdf = row.get("source_pdf")
        if not amid:
            continue
        amendment_id_year = _to_grafter_amendment_id(amid)
        if amendment_id_year:
            pdf_map[f"corr/{amid}/{idx}"] = str(spdf or "")

    cs = _make_corpus_store()

    candidates = []
    for amendment_id, wrong, correct, op_id in all_patches:
        xml_bytes = cs.read_source(amendment_id)
        if xml_bytes is None:
            continue
        _, ok = _apply_text_replace(xml_bytes, wrong, correct)
        if ok:
            continue
        # Load PDF from the corpus store
        pdf_bytes = None
        spdf = pdf_map.get(op_id, "")
        if spdf:
            try:
                pdf_bytes = cs.read_corrigendum_media(amendment_id, Path(spdf).name)
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception:
                pass
        candidates.append({
            "amendment_id": amendment_id, "wrong": wrong, "correct": correct,
            "op_id": op_id, "xml_bytes": xml_bytes, "pdf_bytes": pdf_bytes,
        })
        if limit and len(candidates) >= limit:
            break

    print(f"No-match patches: {len(candidates)}")
    if not candidates:
        return

    print("Running LLM reextraction (concurrency=4)...")
    results = asyncio.run(_reextract_batch(candidates))

    # Process results
    improved = []
    for res in results:
        if res["error"]:
            if verbose:
                print(f"  {res['op_id']}: ERROR {res['error']}")
            continue
        parsed = res.get("parsed") or {}
        if not parsed.get("found"):
            if verbose:
                print(f"  {res['op_id']}: LLM says not found — {parsed.get('reason', '')}")
            continue
        new_wrong = parsed.get("wrong_text_in_xml", "").strip()
        new_correct = parsed.get("correct_text", "").strip()
        conf = parsed.get("confidence", "low")
        if not new_wrong:
            continue
        # Find matching candidate to get xml_bytes
        cand = next((c for c in candidates if c["op_id"] == res["op_id"]), None)
        if not cand:
            continue
        # Verify the new wrong_text actually applies
        _, ok = _apply_text_replace(cand["xml_bytes"], new_wrong, new_correct or cand["correct"])
        status = "APPLIES" if ok else "STILL NO MATCH"
        improved.append({
            "op_id": res["op_id"],
            "amendment_id": cand["amendment_id"],
            "old_wrong": cand["wrong"],
            "new_wrong": new_wrong,
            "new_correct": new_correct or cand["correct"],
            "conf": conf,
            "applies": ok,
        })
        print(f"  {cand['amendment_id']:>10}  [{status}] [{conf}]")
        print(f"    old: {repr(cand['wrong'][:70])}")
        print(f"    new: {repr(new_wrong[:70])}")

    applies_n = sum(1 for x in improved if x["applies"])
    print(f"\nSummary: {len(results)} LLM calls, {len(improved)} proposed changes, {applies_n} verified")

    if update_db and improved:
        official_records = load_official_records(_OFFICIAL_TEXT)
        updated_by_id = {str(row.get("stable_id") or ""): dict(row) for row in official_records}
        updated = 0
        for item in improved:
            if not item["applies"]:
                continue
            parts = item["op_id"].split("/")
            idx = int(parts[3])
            stable_id = _stable_id(pdf_map.get(item["op_id"], ""), idx)
            row = updated_by_id.get(stable_id)
            if row is None:
                continue
            row["wrong_text"] = item["new_wrong"]
            row["correct_text"] = item["new_correct"]
            updated_by_id[stable_id] = row
            updated += 1
        write_official_records(list(updated_by_id.values()), _OFFICIAL_TEXT)
        print(f"Updated {updated} rows in official text corpus.")
    elif improved and not update_db:
        print("(dry run — use --update to apply changes to the official text corpus)")


# ---------------------------------------------------------------------------
# Diff-PDF command (PDF vs XML ground-truth validation)
# ---------------------------------------------------------------------------

def _cmd_diff_pdf(args) -> None:
    """Run PDF vs XML diff for corrigendum-affected amendments.

    Delegates to scripts/diff_pdf_xml_corrigenda.py which contains all the
    logic. We import it directly so we don't need subprocess.
    """
    import importlib.util
    script_path = _LAWVM_DIR / "scripts" / "diff_pdf_xml_corrigenda.py"
    spec = importlib.util.spec_from_file_location("diff_pdf_xml_corrigenda", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module spec from {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from pathlib import Path as _Path
    output = getattr(args, "output", None) or str(mod._DEFAULT_OUTPUT)
    db = getattr(args, "db", None) or str(mod._PATCHES_DB)
    zip_p = getattr(args, "zip", None) or str(mod._STATUTE_ZIP)

    mod.run(
        output_path=_Path(output),
        limit=getattr(args, "limit", None),
        workers=getattr(args, "workers", 8),
        verbose=getattr(args, "verbose", False),
        db_path=_Path(db),
        zip_path=_Path(zip_p),
    )


# ---------------------------------------------------------------------------
# check-completeness command — expected_pair_count vs extracted
# ---------------------------------------------------------------------------

def _cmd_check_completeness(args) -> None:
    """Summarise amendments where expected_pair_count > extracted record count.

    Reads the official records corpus and groups rows by source_pdf.
    For each group, compares the stored expected_pair_count against the
    number of extracted rows.  Prints amendments where the counts diverge,
    i.e. where some pairs were likely missed by the extractors.
    """
    official_path = (
        Path(args.db) if getattr(args, "db", None) else None
    )
    records = load_official_records(official_path)

    from collections import defaultdict
    by_pdf: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        by_pdf[str(row.get("source_pdf") or "")].append(row)

    incomplete: list[tuple[str, str, int, int]] = []
    for source_pdf, rows in sorted(by_pdf.items()):
        epc_vals = [int(r["expected_pair_count"]) for r in rows if r.get("expected_pair_count") is not None]
        if not epc_vals:
            continue
        expected = epc_vals[0]
        extracted = len(rows)
        if expected > extracted:
            amendment_id = str(rows[0].get("amendment_id") or "")
            incomplete.append((source_pdf, amendment_id, expected, extracted))

    if getattr(args, "json", False):
        print(json.dumps(
            [{"source_pdf": s, "amendment_id": a, "expected": e, "extracted": x}
             for s, a, e, x in incomplete],
            ensure_ascii=False, indent=2,
        ))
        return

    if not incomplete:
        print("No incomplete extractions found.")
        return

    print(f"Likely incomplete extractions: {len(incomplete)} PDF(s)\n")
    print(f"{'amendment_id':<16} {'expected':>8} {'extracted':>9}  source_pdf")
    print("-" * 80)
    for source_pdf, amendment_id, expected, extracted in incomplete:
        print(f"{amendment_id:<16} {expected:>8} {extracted:>9}  {source_pdf}")


# ---------------------------------------------------------------------------
# recompute-completeness command — refresh expected_pair_count in JSONL
# ---------------------------------------------------------------------------

def _cmd_recompute_completeness(args) -> None:
    """Re-run the regex pair-count for every PDF and patch expected_pair_count in JSONL.

    Reads PDF bytes from the corpus store (no LLM needed), extracts text with
    pdftotext, runs count_corrigendum_pairs, and writes the updated JSONL back
    to the same path.
    """
    from lawvm.finland.corrigendum import count_corrigendum_pairs as _count_pairs

    official_path = (
        Path(args.db) if getattr(args, "db", None) else None
    ) or Path("data/finland/corrigendum_official_fi.jsonl")

    dry_run = getattr(args, "dry_run", False)

    records = load_official_records(official_path)
    cs = _make_corpus_store()

    # Group record indices by source_pdf so we read each PDF once.
    from collections import defaultdict
    by_pdf: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(records):
        spdf = str(row.get("source_pdf") or "")
        if spdf:
            by_pdf[spdf].append(i)

    updated = 0
    skipped = 0
    for source_pdf, idxs in sorted(by_pdf.items()):
        sid = str(records[idxs[0]].get("statute_id") or "")
        pdf_bytes = cs.read_corrigendum_media(sid, Path(source_pdf).name) if sid else None
        if not pdf_bytes:
            skipped += 1
            continue
        text = _pdf_to_text(pdf_bytes)
        count = _count_pairs(text) if text else None
        old = records[idxs[0]].get("expected_pair_count")
        if count == old:
            continue
        for i in idxs:
            records[i]["expected_pair_count"] = count
        updated += len(idxs)
        if getattr(args, "verbose", False):
            print(f"  {source_pdf}: {old!r} → {count!r}")

    print(f"PDFs processed: {len(by_pdf) - skipped}, skipped (not in corpus): {skipped}")
    print(f"Records updated: {updated}")
    if dry_run:
        print("(dry run — JSONL not written)")
        return

    with official_path.open("w", encoding="utf-8") as fh:
        for row in records:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Written: {official_path}")


# ---------------------------------------------------------------------------
# check-patches command — process-pool workers (module-level for pickling)
# ---------------------------------------------------------------------------

# Per-process state, initialised once by _check_patches_init.
_cp_cs = None
_cp_pt = None


def _check_patches_init() -> None:
    global _cp_cs, _cp_pt
    from lawvm.finland.corrigendum import get_patch_table
    _cp_cs = _make_corpus_store()
    _cp_pt = get_patch_table()


def _check_patches_one(amendment_id: str) -> tuple[int, int, list[dict]]:
    """Worker: apply all patches for one amendment; return (hj, hb, misapplied)."""
    import warnings
    from lawvm.finland.corrigendum import clear_misapplied_records, get_misapplied_records
    assert _cp_cs is not None, "_check_patches_init must run before _check_patches_one"
    assert _cp_pt is not None, "_check_patches_init must run before _check_patches_one"
    clear_misapplied_records()
    xml = _cp_cs.read_source(amendment_id)
    if xml is None:
        return 0, 0, []
    hj = hb = 0
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        if amendment_id in _cp_pt._patches:
            _, applied = _cp_pt.patch_source_xml(xml, amendment_id)
            hj = len(applied)
        if amendment_id in _cp_pt._body_patches:
            _, applied = _cp_pt.patch_source_body_xml(xml, amendment_id)
            hb = len(applied)
    return hj, hb, get_misapplied_records()


def _cmd_check_patches(args) -> None:
    """Iterate all amendments in corpus, apply patches, write misapplied JSONL."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from lawvm.finland.corrigendum import get_patch_table, flush_misapplied_records
    import lawvm.finland.corrigendum as _fi_corr

    out_path = Path(getattr(args, "out", None) or "data/finland/corrigendum_misapplied_fi.jsonl")
    verbose = getattr(args, "verbose", False)
    workers = getattr(args, "workers", 8)

    pt = get_patch_table()
    all_ids = list(set(pt._patches) | set(pt._body_patches))
    total_j = sum(len(v) for v in pt._patches.values())
    total_b = sum(len(v) for v in pt._body_patches.values())

    hits_j = hits_b = 0
    all_misapplied: list[dict] = []

    with ProcessPoolExecutor(max_workers=workers, initializer=_check_patches_init) as ex:
        futs = {ex.submit(_check_patches_one, aid): aid for aid in all_ids}
        done = 0
        for fut in as_completed(futs):
            hj, hb, ma = fut.result()
            hits_j += hj
            hits_b += hb
            all_misapplied.extend(ma)
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(all_ids)} amendments processed", flush=True)

    misses = [r for r in all_misapplied if r["reason"] == "miss"]
    ambigs = [r for r in all_misapplied if r["reason"] == "ambiguous"]
    already = [r for r in all_misapplied if r["reason"] == "already_applied"]

    total = total_j + total_b
    hits = hits_j + hits_b
    ambig_j = len([r for r in ambigs if "body" not in r.get("op_id", "")])
    ambig_b = len([r for r in ambigs if "body" in r.get("op_id", "")])
    already_j = len([r for r in already if "body" not in r.get("op_id", "")])
    already_b = len([r for r in already if "body" in r.get("op_id", "")])
    miss_j = total_j - hits_j - ambig_j - already_j
    miss_b = total_b - hits_b - ambig_b - already_b
    print(f"johtolause : total={total_j}  hit={hits_j}  miss={miss_j}  ambig={ambig_j}  already={already_j}")
    print(f"body       : total={total_b}  hit={hits_b}  miss={miss_b}  ambig={ambig_b}  already={already_b}")
    print(f"total      : {total}  hit={hits} ({hits/total*100:.1f}%)  miss={len(misses)}  ambig={len(ambigs)}  already={len(already)}")

    # Populate the module-level accumulator so flush_misapplied_records can write it.
    _fi_corr._MISAPPLIED.clear()
    _fi_corr._MISAPPLIED.extend(all_misapplied)
    written = flush_misapplied_records(out_path)
    if written:
        print(f"Misapplied records written to: {written}")
        if verbose:
            for r in all_misapplied[:20]:
                print(f"  [{r['reason']}] {r.get('op_id', '?')}  wrong={r['wrong_text'][:70]!r}")
    else:
        print("No misapplied records.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    subcmd = getattr(args, "corrigendum_command", None)
    if subcmd == "status":
        _cmd_status(args)
    elif subcmd == "apply":
        _cmd_apply(args)
    elif subcmd == "classify":
        _cmd_classify(args)
    elif subcmd == "report":
        _cmd_report(args)
    elif subcmd == "verify":
        _cmd_verify(args)
    elif subcmd == "test":
        _cmd_test(args)
    elif subcmd == "reextract":
        _cmd_reextract(args)
    elif subcmd == "diff-pdf":
        _cmd_diff_pdf(args)
    elif subcmd == "manual-template":
        _cmd_manual_template(args)
    elif subcmd == "open-manual":
        _cmd_open_manual(args)
    elif subcmd == "overview":
        _cmd_overview(args)
    elif subcmd == "sources":
        _cmd_sources(args)
    elif subcmd == "backfill-meta":
        _cmd_backfill_meta(args)
    elif subcmd == "provenance":
        _cmd_provenance(args)
    elif subcmd == "review":
        _cmd_review(args)
    elif subcmd == "check-patches":
        _cmd_check_patches(args)
    elif subcmd == "check-completeness":
        _cmd_check_completeness(args)
    elif subcmd == "recompute-completeness":
        _cmd_recompute_completeness(args)
    else:
        print(
            "Usage: lawvm corrigendum status [SID] | apply SID | classify | verify | report | "
            "test AMENDMENT_ID | reextract | diff-pdf | manual-template AMENDMENT_ID | "
            "overview | sources | backfill-meta | "
            "provenance AMENDMENT_ID | "
            "open-manual | "
            "review STATUTE_ID | "
            "check-patches",
            file=sys.stderr,
        )
        sys.exit(1)


def register_cli(sub: Any) -> None:
    """Register the 'corrigendum' subcommand onto an argparse subparsers object."""
    corr_p = sub.add_parser(
        "corrigendum",
        help="corrigendum (oikaisu) status, inspection, and LLM classification",
        description=(
            "Inspect legally binding corrections (corrigenda) to published statutes. "
            "Subcommands: status [SID], apply SID, classify, report."
        ),
    )
    corr_sub = corr_p.add_subparsers(dest="corrigendum_command", metavar="<subcommand>")

    corr_status_p = corr_sub.add_parser(
        "status",
        help="corpus-wide summary or single-statute corrigendum details",
    )
    corr_status_p.add_argument(
        "statute_id", nargs="?",
        help="statute ID (e.g. 2007/26) — omit for corpus summary",
    )

    corr_apply_p = corr_sub.add_parser(
        "apply",
        help="extract corrigendum PDF(s) and show text via pdftotext",
    )
    corr_apply_p.add_argument("statute_id", help="statute ID, e.g. 2007/26")
    corr_apply_p.add_argument(
        "--save", metavar="PATH",
        help="save extracted PDF to this path",
    )

    corr_classify_p = corr_sub.add_parser(
        "classify",
        help="LLM-classify corrigendum PDFs into typed corrections (johtolause/table/prose/…)",
        description=(
            "Run all Finnish (sk*) corrigendum PDFs through a local LLM to extract "
            "typed correction records. Results are synced into the git-tracked "
            "data/finland/corrigendum_official_fi.jsonl and "
            "data/finland/corrigendum_adjudications_fi.jsonl corpora "
            "(with sqlite kept only as a transitional scratch artifact). "
            "Johtolause corrections are source-verified against the corpus store. "
            "Idempotent — already-classified PDFs are skipped unless --rerun."
        ),
    )
    corr_classify_p.add_argument(
        "--lang", choices=["fi", "sv", "all"], default="fi",
        help="language filter: fi=sk* (default), sv=fs*, all=both",
    )
    corr_classify_p.add_argument(
        "--type", metavar="TYPE",
        help="after classification, show only this correction type (e.g. johtolause)",
    )
    corr_classify_p.add_argument(
        "--parallel", type=int, default=None, metavar="N",
        help="concurrent LLM calls (default: cpu_count)",
    )
    corr_classify_p.add_argument(
        "--limit", type=int, metavar="N",
        help="process at most N PDFs (for testing)",
    )
    corr_classify_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="run LLM extraction but do not write to DB",
    )
    corr_classify_p.add_argument(
        "--rerun", action="store_true",
        help="re-classify already-classified PDFs (overwrite)",
    )
    corr_classify_p.add_argument(
        "--compare", action="store_true",
        help="run both regex and LLM; log divergences; write regex result (implies --rerun for comparison scope)",
    )
    corr_classify_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print result for every PDF (default: only johtolause cases)",
    )

    corr_check_p = corr_sub.add_parser(
        "check-patches",
        help="audit patch hit/miss/ambig rates across corpus; write misapplied JSONL",
    )
    corr_check_p.add_argument(
        "--out", metavar="PATH",
        help="output path for misapplied JSONL (default: data/finland/corrigendum_misapplied_fi.jsonl)",
    )
    corr_check_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print first 20 misapplied records",
    )
    corr_check_p.add_argument(
        "--workers", "-j", type=int, default=8, metavar="N",
        help="parallel worker threads (default: 8)",
    )

    corr_compl_p = corr_sub.add_parser(
        "check-completeness",
        help="report PDFs where expected_pair_count exceeds extracted record count",
    )
    corr_compl_p.add_argument(
        "--db", metavar="PATH",
        help="path to official records JSONL (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_compl_p.add_argument(
        "--json", action="store_true",
        help="output as JSON array",
    )

    corr_recomp_p = corr_sub.add_parser(
        "recompute-completeness",
        help="refresh expected_pair_count in JSONL from regex (no LLM)",
    )
    corr_recomp_p.add_argument(
        "--db", metavar="PATH",
        help="official JSONL path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_recomp_p.add_argument(
        "--dry-run", action="store_true",
        help="compute counts but do not write JSONL",
    )
    corr_recomp_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print each PDF whose count changes",
    )

    corr_verify_p = corr_sub.add_parser(
        "verify",
        help="re-run source verification for classified corrections (no LLM needed)",
        description=(
            "Update verified_in_source column without re-running LLM classification. "
            "Use after: fixing _verify_in_source bugs, updating the corpus store, etc."
        ),
    )
    corr_verify_p.add_argument(
        "--type", metavar="TYPE", default="johtolause",
        help="correction type to verify (default: johtolause)",
    )
    corr_verify_p.add_argument(
        "--amendment", metavar="AMENDMENT_ID", dest="amendment_id",
        help="restrict verification to one amendment (e.g. 1246/2002)",
    )

    corr_report_p = corr_sub.add_parser(
        "report",
        help="query classified corrigendum results from the text corpus",
        description=(
            "Print classified correction records from the git-tracked "
            "corrigendum text corpus. "
            "Filter by type, amendment, or verified status."
        ),
    )
    corr_report_p.add_argument(
        "--type", metavar="TYPE",
        help="filter by correction type (johtolause|table|footnote|prose|metadata|unknown)",
    )
    corr_report_p.add_argument(
        "--amendment", metavar="AMENDMENT_ID", dest="amendment_id",
        help="filter to one amendment (e.g. 984/2018)",
    )
    corr_report_p.add_argument(
        "--verified", action="store_true",
        help="only show corrections verified in the corpus store",
    )

    corr_test_p = corr_sub.add_parser(
        "test",
        help="dry-run patch application for one amendment — shows what would change",
        description=(
            "Load classified patches for an amendment, apply them to the source XML "
            "from the corpus store, and show pass/fail + before/after context for each patch. "
            "Useful for debugging why a corrigendum patch does or doesn't match."
        ),
    )
    corr_test_p.add_argument(
        "amendment_id",
        help="amendment ID to test (NUM/YEAR or YEAR/NUM, e.g. '984/2018' or '2018/984')",
    )

    corr_diffpdf_p = corr_sub.add_parser(
        "diff-pdf",
        help="diff PDF vs XML text for corrigendum-affected amendments (ground-truth validation)",
        description=(
            "For each amendment in the classified corrigendum corpus, extract the preamble text from "
            "both the PDF and the XML in the corpus store, and compare them. PDFs have corrigenda "
            "applied; XMLs do not — so diffs reveal corrections not yet in the patch pipeline. "
            "Output: .tmp/pdf_xml_diffs.jsonl with one record per amendment."
        ),
    )
    corr_diffpdf_p.add_argument(
        "--output", "-o", metavar="FILE",
        help="output JSONL file (default: .tmp/pdf_xml_diffs.jsonl)",
    )
    corr_diffpdf_p.add_argument(
        "--limit", type=int, metavar="N",
        help="process only first N amendments (for testing)",
    )
    corr_diffpdf_p.add_argument(
        "--workers", type=int, default=8, metavar="N",
        help="parallel workers for pdftotext (default: 8)",
    )
    corr_diffpdf_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="print each amendment with a diff",
    )
    corr_diffpdf_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_reex_p = corr_sub.add_parser(
        "reextract",
        help="LLM-assisted reextraction for no-match patches (gives LLM both PDF + XML context)",
        description=(
            "For each patch where wrong_text doesn't match the amendment XML, calls the local "
            "LLM with both the corrigendum PDF text and the amendment XML. The LLM finds the "
            "exact bytes in the XML to replace. Use --update to apply changes and resync "
            "the git-tracked corrigendum corpus."
        ),
    )
    corr_reex_p.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="process at most N no-match patches (default: all)",
    )
    corr_reex_p.add_argument(
        "--update", action="store_true",
        help="write verified improvements back to the official corrigendum text corpus",
    )
    corr_reex_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="show LLM output for all patches including failures",
    )

    corr_manual_p = corr_sub.add_parser(
        "manual-template",
        help="emit YAML scaffold entries for corrigendum_manual.yaml from classified patches",
        description=(
            "Load one amendment's classified corrigendum items from the git-tracked "
            "corrigendum corpus, "
            "filter to the items that still do not match source XML by default, and emit "
            "a ready-to-paste YAML scaffold for corrigendum_manual.yaml."
        ),
    )
    corr_manual_p.add_argument(
        "amendment_id", metavar="AMENDMENT_ID",
        help="corrected amendment id in NUM/YEAR format, e.g. 991/2012",
    )
    corr_manual_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_manual_p.add_argument(
        "--all", action="store_true",
        help="include all fi correction items for this amendment, not just current no-match items",
    )
    corr_manual_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of YAML",
    )

    corr_open_manual_p = corr_sub.add_parser(
        "open-manual",
        help="list current live manual-corrigendum candidates",
        description=(
            "Scan high-no-match Finnish corrigendum amendments and recompute "
            "current manual-template viability, separating real open manual "
            "items from attachment-only and already-covered cases."
        ),
    )
    corr_open_manual_p.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="inspect at most N amendments with unverified classified items (default: 20)",
    )
    corr_open_manual_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_open_manual_p.add_argument(
        "--all", action="store_true",
        help="include attachment-only and already-covered amendments in the output",
    )
    corr_open_manual_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_overview_p = corr_sub.add_parser(
        "overview",
        help="summarize corpus-wide corrigendum adjudication state",
        description=(
            "Build a corpus-level view over official corrigendum items, current "
            "verification/adjudication status, and the top amendments that still "
            "look open or attachment-only."
        ),
    )
    corr_overview_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_overview_p.add_argument(
        "--limit", type=int, default=10, metavar="N",
        help="show at most N amendments in each top-list bucket (default: 10)",
    )
    corr_overview_p.add_argument(
        "--live", action="store_true",
        help="recompute unresolved item status against source XML instead of relying on stored adjudications",
    )
    corr_overview_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_sources_p = corr_sub.add_parser(
        "sources",
        help="inspect or rebuild the PDF-level corrigendum provenance manifest",
        description=(
            "Build or inspect the git-tracked one-record-per-PDF provenance "
            "manifest for official Finnish corrigendum PDFs."
        ),
    )
    corr_sources_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_sources_p.add_argument(
        "--refresh", action="store_true",
        help="rebuild data/finland/corrigendum_sources_fi.jsonl from the official corrigendum corpus",
    )
    corr_sources_p.add_argument(
        "--limit", type=int, default=10, metavar="N",
        help="show at most N source records (default: 10; <=0 shows all)",
    )
    corr_sources_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_backfill_meta_p = corr_sub.add_parser(
        "backfill-meta",
        help="backfill missing official corrigendum amendment/date metadata from XML refs",
        description=(
            "Use authoritative <finlex:corrigendum> blocks from the consolidated "
            "oracle XML to fill missing amendment ids and publish dates in the "
            "official corrigendum corpus."
        ),
    )
    corr_backfill_meta_p.add_argument(
        "--db", metavar="PATH",
        help="official corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_backfill_meta_p.add_argument(
        "--update", action="store_true",
        help="write backfilled metadata into the official corrigendum JSONL",
    )
    corr_backfill_meta_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_prov_p = corr_sub.add_parser(
        "provenance",
        help="show one amendment's official items, verification state, and manual coverage together",
        description=(
            "Build an amendment-scoped operator view over official corrigendum items, "
            "current source verification, and manual override coverage so each "
            "corrigendum item can be audited in one place."
        ),
    )
    corr_prov_p.add_argument(
        "amendment_id", metavar="AMENDMENT_ID",
        help="corrected amendment id in NUM/YEAR format, e.g. 442/2016",
    )
    corr_prov_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_prov_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )

    corr_review_p = corr_sub.add_parser(
        "review",
        help="review one statute's live oracle disagreements against corrigendum evidence",
        description=(
            "Run live oracle disagreement classification for one statute and group "
            "diverging sections by blamed amendment, then overlay existing "
            "classified corrigendum items and manual-override counts for those amendments."
        ),
    )
    corr_review_p.add_argument("statute_id", help="statute ID, e.g. 1995/1552")
    corr_review_p.add_argument(
        "--mode", default="legal_pit",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for live disagreement classification (default: legal_pit)",
    )
    corr_review_p.add_argument(
        "--db", metavar="PATH",
        help="classified corrigendum source path (default: data/finland/corrigendum_official_fi.jsonl)",
    )
    corr_review_p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of plain text",
    )
