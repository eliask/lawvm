"""Build a standalone HTML page of reported Finlex candidate findings.

Reads a local YAML directory and produces a self-contained HTML file with
embedded JSON data and client-side rendering.

Usage:
    uv run python scripts/build_standalone_findings.py [--output viewer/finlex-vahvistetut.html]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Finnish labels
# ---------------------------------------------------------------------------

CATEGORY_FI: dict[str, str] = {
    "missing_applied_amendment": "Muutossäädös soveltamatta",
    "missing_inserted_provision": "Puuttuva lisäys",
    "missing_repeal": "Kumoaminen soveltamatta",
    "partial_amendment_loss": "Osittaismuutoksen sisältöhävikki",
    "wrong_cross_reference": "Virheellinen pykäläviittaus",
    "finlex_introduced_defect": "Finlexin konsolidaatiovirhe",
}

CATEGORY_ORDER: list[str] = list(CATEGORY_FI.keys())

CONFIDENCE_FI: dict[str, str] = {
    "definitive": "Korkea luottamus",
    "strong": "Vahva näyttö",
}

# Severity tiers - determines page grouping order
SEVERITY_FI: dict[str, str] = {
    "substantive_current": "Aineelliset nykyiset ehdokkaat",
    "substantive_historical": "Historialliset väliaikaiset säännökset",
    "text_corruption": "Tekstipoikkeamaehdokkaat",
    "typography": "Typografiset poikkeamat",
}

SEVERITY_ORDER: list[str] = list(SEVERITY_FI.keys())

SEVERITY_DESC_FI: dict[str, str] = {
    "substantive_current": "Nämä poikkeamat koskevat voimassa olevaa lainsäädäntöä ja ovat edelleen havaittavissa Finlexin konsolidoidussa tekstissä.",
    "substantive_historical": "Nämä poikkeamat koskevat väliaikaisia säännöksiä, jotka puuttuivat Finlexin konsolidoidusta tekstistä voimassaoloaikanaan. Nykyinen ajantasateksti on sisällöllisesti oikein, koska säännökset eivät enää ole voimassa.",
    "text_corruption": "Näissä tapauksissa Finlexin konsolidoinnissa on syntynyt merkkitason virheitä, jotka muuttavat sanoja tai paikannimiä.",
    "typography": "Näissä tapauksissa Finlexin konsolidoinnissa on syntynyt typografisia poikkeamia, kuten ylimääräisiä välilyöntejä pykäläviittauksissa.",
}

# Map statute IDs to severity tiers (derived from YAML analysis)
# Temporary/expired provisions -> historical; permanent law defects -> current
SEVERITY_MAP: dict[str, str] = {
    # Current substantive
    "1978/693": "text_corruption",  # old+new text structural corruption
    "1987/437": "substantive_current",
    "1990/1247": "substantive_current",
    "1991/1144": "substantive_current",
    "1992/1330": "substantive_current",
    "1995/34": "substantive_current",
    "1998/28": "substantive_current",
    "2007/110": "substantive_current",
    "2010/352": "substantive_current",
    # Historical (expired temporary provisions)
    "1992/211": "substantive_historical",
    "1995/903": "substantive_historical",
    "2007/1463": "substantive_historical",
    "2011/410": "substantive_historical",
    "2014/716": "substantive_historical",
    "2017/869": "substantive_historical",
    "2019/1465": "substantive_historical",
    # Text corruption (encoding, typos, markup)
    "1988/852": "text_corruption",
    "1989/383": "text_corruption",
    "2000/1125": "text_corruption",
    "2007/446": "text_corruption",
    "2010/789": "text_corruption",
    # Typography (whitespace, spacing)
    "1992/734": "typography",
    "1993/1190": "typography",
    "2000/40": "typography",
    # New batch — current substantive
    "2002/476": "substantive_current",
    "1996/931": "substantive_current",
    "2013/185": "substantive_current",
    "2016/768": "substantive_current",
    "2014/932": "substantive_current",
    "2022/1267": "substantive_current",
    "2024/817": "substantive_current",
    "2019/906": "substantive_current",
    "2007/1302": "substantive_current",
    "2003/1280": "substantive_current",
    "2007/1461": "substantive_current",
    "1991/1083": "substantive_current",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_entries(yaml_dir: Path) -> list[dict]:
    entries = []
    for fpath in sorted(yaml_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(fpath.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  WARNING: skipping {fpath.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        # Must have at minimum a statute_id and category
        if not data.get("statute_id") or not data.get("category"):
            print(f"  WARNING: skipping {fpath.name}: missing statute_id or category", file=sys.stderr)
            continue
        entries.append(data)
    return entries


def statute_sort_key(entry: dict) -> str:
    sid = entry.get("statute_id", "")
    parts = sid.split("/")
    if len(parts) == 2:
        try:
            return f"{int(parts[0]):04d}/{int(parts[1]):05d}"
        except ValueError:
            pass
    return sid


def clean_text(s: str | None) -> str:
    """Strip leading/trailing whitespace from a possibly-None string."""
    return (s or "").strip()


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Finlexin ajantasaistekstien raportoidut ehdokaspoikkeamat - LawVM</title>
<style>
:root {
  --bg: #fafafa; --text: #1a1a1a; --dim: #555; --dimmer: #888;
  --border: #ddd; --card-bg: #fff; --badge-bg: #f0f0f0;
  --green: #15803d; --green-bg: #f0fdf4; --green-border: #bbf7d0;
  --red: #b91c1c; --red-bg: #fef2f2; --red-border: #fecaca;
  --blue: #1d4ed8; --amber: #a16207; --amber-bg: #fefce8; --amber-border: #fef08a;
  --purple: #7c3aed;
  --source-only-bg: #fff1f2; --source-only-border: #dc2626;
  --finlex-only-bg: #eff6ff; --finlex-only-border: #2563eb;
  --changed-bg: #fefce8; --changed-border: #a16207;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f0f0f; --text: #e2e2e2; --dim: #aaa; --dimmer: #666;
    --border: #2a2a2a; --card-bg: #181818; --badge-bg: #222;
    --green: #4ade80; --green-bg: #052e16; --green-border: #166534;
    --red: #f87171; --red-bg: #2d0a0a; --red-border: #7f1d1d;
    --blue: #60a5fa; --amber: #fbbf24; --amber-bg: #1c1300; --amber-border: #713f12;
    --purple: #a78bfa;
    --source-only-bg: #2d0a0a; --source-only-border: #f87171;
    --finlex-only-bg: #0c1524; --finlex-only-border: #60a5fa;
    --changed-bg: #1c1300; --changed-border: #fbbf24;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.6; }
.wrap { max-width: 920px; margin: 0 auto; padding: 0 20px 60px; }

/* Header */
.page-header { padding: 40px 0 24px; border-bottom: 1px solid var(--border); margin-bottom: 28px; }
.page-header h1 { font-size: 22px; font-weight: 700; margin-bottom: 10px; }
.page-header p { font-size: 14px; color: var(--dim); max-width: 720px; line-height: 1.65; }
.page-header p + p { margin-top: 8px; }
.page-header .data-date { font-size: 12px; color: var(--dimmer); margin-top: 10px; }

/* Stats bar */
.stats-bar { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 24px; }
.stat-card { border: 1px solid var(--border); border-radius: 6px; padding: 14px 18px; min-width: 130px; }
.stat-card .num { font-size: 26px; font-weight: 700; }
.stat-card .label { font-size: 11px; color: var(--dim); margin-top: 2px; }

/* Filter bar */
.filter-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }
.filter-btn { border: 1px solid var(--border); border-radius: 4px; padding: 5px 12px;
              font-size: 12px; background: var(--card-bg); color: var(--text); cursor: pointer;
              transition: background 0.1s, color 0.1s; }
.filter-btn.active { background: var(--blue); color: #fff; border-color: var(--blue); }
.filter-btn:hover:not(.active) { border-color: var(--blue); }

/* Severity group */
.severity-section { margin-bottom: 36px; }
.severity-header { font-size: 18px; font-weight: 700; margin: 28px 0 6px;
                   padding-bottom: 8px; border-bottom: 2px solid var(--border); }
.severity-desc { font-size: 13px; color: var(--dim); margin-bottom: 14px; line-height: 1.6; }

/* Category sub-group */
.group-header { font-size: 15px; font-weight: 600; margin: 18px 0 8px;
                padding-bottom: 6px; border-bottom: 1px solid var(--border); color: var(--dim); }

/* Finding card */
.finding { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
           padding: 16px 20px; margin-bottom: 12px; }
.finding-header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.finding-sid { font-weight: 700; font-size: 14px; }
.finding-sid a { color: var(--text); text-decoration: none; }
.finding-sid a:hover { text-decoration: underline; }
.finding-title { font-size: 13px; color: var(--dim); margin-bottom: 10px; }
.claim { font-size: 13px; line-height: 1.7; white-space: pre-line; margin-bottom: 12px; }

/* Badges */
.badge { display: inline-block; padding: 2px 8px; border-radius: 3px;
         font-size: 11px; font-weight: 600; white-space: nowrap; }
.badge-definitive { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }
.badge-strong { background: var(--amber-bg); color: var(--amber); border: 1px solid var(--amber-border); }
.badge-cat { background: var(--badge-bg); color: var(--dim); font-weight: 500; }
.badge-historical { background: var(--badge-bg); color: var(--dimmer); font-weight: 500; font-style: italic; }

/* Evidence blocks — semantic classes instead of del/ins */
.evidence-wrap { margin-bottom: 12px; }
.evidence-block { border-radius: 5px; padding: 10px 14px; font-size: 12px; line-height: 1.65; margin-bottom: 8px; }
.evidence-block pre { white-space: pre-wrap; word-break: break-word; font-family: inherit; }
.evidence-label { font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
              text-transform: uppercase; margin-bottom: 6px; }

/* Side-by-side diff */
.diff-wrap { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }
@media (max-width: 600px) { .diff-wrap { grid-template-columns: 1fr; } }
.diff-side { border-radius: 5px; padding: 10px 14px; font-size: 12px; line-height: 1.65; }
.diff-side pre { white-space: pre-wrap; word-break: break-word; font-family: inherit; }
.diff-side.correct { background: var(--green-bg); border: 1px solid var(--green-border); }
.diff-side.finlex  { background: var(--red-bg);   border: 1px solid var(--red-border); }
.diff-label { font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
              text-transform: uppercase; margin-bottom: 6px; }
.diff-side.correct .diff-label { color: var(--green); }
.diff-side.finlex  .diff-label { color: var(--red); }

/* Semantic diff highlights — no strikethrough */
.source-only { background: var(--source-only-bg); border-bottom: 2px solid var(--source-only-border);
               border-radius: 2px; padding: 0 1px; }
.finlex-only { background: var(--finlex-only-bg); border-bottom: 2px solid var(--finlex-only-border);
               border-radius: 2px; padding: 0 1px; }
@media (prefers-color-scheme: dark) {
  .source-only { background: var(--source-only-bg); border-bottom-color: var(--source-only-border); }
  .finlex-only { background: var(--finlex-only-bg); border-bottom-color: var(--finlex-only-border); }
}

/* Diff legend */
.diff-legend { font-size: 11px; color: var(--dimmer); margin-bottom: 8px; display: flex; gap: 16px; flex-wrap: wrap; }
.diff-legend-item { display: flex; align-items: center; gap: 4px; }
.diff-legend-swatch { display: inline-block; width: 12px; height: 12px; border-radius: 2px; }

/* Johtolause */
.johtolause { font-size: 12px; color: var(--dim); background: var(--badge-bg);
              border-radius: 4px; padding: 6px 10px; margin-bottom: 10px;
              border-left: 3px solid var(--blue); }
.johtolause-label { font-weight: 700; font-size: 10px; text-transform: uppercase;
                    letter-spacing: 0.05em; color: var(--blue); margin-bottom: 2px; }

/* Expandable explanation */
.expand-btn { font-size: 12px; color: var(--blue); cursor: pointer; border: none;
              background: none; padding: 0; margin-bottom: 6px; }
.expand-btn:hover { text-decoration: underline; }
.expand-body { display: none; font-size: 13px; line-height: 1.7; white-space: pre-line;
               color: var(--dim); margin-bottom: 10px; border-left: 2px solid var(--border);
               padding-left: 12px; }
.expand-body.open { display: block; }

/* Amendment citation & footer */
.amend-cite { font-size: 12px; color: var(--dim); margin-bottom: 6px; }
.amend-cite a { color: var(--blue); text-decoration: none; }
.amend-cite a:hover { text-decoration: underline; }
.card-footer { font-size: 11px; color: var(--dimmer); border-top: 1px solid var(--border);
               padding-top: 8px; margin-top: 8px; }

/* Caveats */
.caveat { font-size: 12px; color: var(--amber); background: var(--amber-bg);
          border: 1px solid var(--amber-border); border-radius: 4px;
          padding: 6px 10px; margin-bottom: 8px; white-space: pre-line; }

/* Methodology */
.methodology { margin-top: 48px; padding: 20px 0; border-top: 2px solid var(--border); }
.methodology h2 { font-size: 16px; font-weight: 700; margin-bottom: 10px; }
.methodology p { font-size: 13px; color: var(--dim); line-height: 1.7; max-width: 720px; }
.methodology p + p { margin-top: 8px; }

footer { padding: 24px 0 0; font-size: 12px; color: var(--dimmer); text-align: center; }
footer a { color: var(--blue); text-decoration: none; }
footer a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="wrap">

<header class="page-header">
  <h1>Finlexin ajantasaistekstien raportoidut ehdokaspoikkeamat</h1>
  <p>LawVM on mekaaninen lakikääntäjä, joka kokoaa Suomen ajantasaisen lainsäädännön
  Säädöskokoelman muutossäädöksistä. Tämä sivu esittää ne poikkeamat Finlexin
  konsolidoidun tekstin ja julkaistujen muutossäädösten välillä, jotka on tarkistettu
  erillisellä lähdetarkastuksella ja raportoitu ehdokashavaintoina.</p>
  <p>Jokaisessa julkaistussa havainnossa on verrattu Finlexin yksilöityä
  konsolidaatiopintaa Säädöskokoelman julkaistuun lähdetekstiin ja tarkistettu,
  ettei havaintoa selitä myöhempi muutossäädös tai julkaistu oikaisu.</p>
  <p class="data-date">Finlexin XML-aineisto: 18.4.2026 &middot; Automaattinen vertailu ajettu noin 8 200 säädöksen korpukselle; tällä sivulla näytetään vain erikseen lähdetarkastetut havainnot.</p>
</header>

<div id="stats" class="stats-bar"></div>
<div id="filters" class="filter-bar"></div>
<div id="findings"></div>

<div class="methodology">
  <h2>Menetelmä</h2>
  <p>LawVM käy läpi jokaisen muutossäädöksen Säädöskokoelman XML-arkistosta ja kokoaa
  ajantasaisen lakitekstin mekaanisesti. Tulosta verrataan Finlexin konsolidoituun
  ajantasaistekstiin. Poikkeamat luokitellaan automaattisesti ja merkittävimmät
  tarkistetaan erikseen lukemalla sekä muutossäädöksen alkuperäisteksti että
  Finlexin konsolidoitu XML.</p>
  <p><strong>Korkea luottamus</strong> tarkoittaa, että muutossäädöksen johtolause ja teksti ovat
  yksiselitteiset LawVM:n lähdetarkastuksen perusteella. <strong>Vahva näyttö</strong> tarkoittaa,
  että poikkeama on selkeästi havaittavissa mutta edellyttää tulkintaa johtolauseesta
  tai voimaantulosäännöksestä. Nämä ovat raportoituja ehdokashavaintoja, eivät
  viranomaisen vahvistamia virheitä.</p>
  <p>Tarkastuksessa on käytetty Finlexin avoimen datan Akoma Ntoso -XML-aineistoa
  (ladattu 18.4.2026) sekä Säädöskokoelman alkuperäisten muutossäädösten
  XML-lähdetekstejä. Kukin havainto on tarkistettu seuraavasti: (1) johtolauseen
  laajuus, (2) voimaantulosäännös, (3) lähdetekstin sisältö, (4) Finlexin
  konsolidoitu XML-rakenne, (5) myöhemmät muutossäädökset ja (6) julkaistut
  oikaisut (Säädöskokoelman oikaisurekisteri).</p>
</div>

<footer>
  LawVM &middot; <a href="https://lawvm.org">lawvm.org</a> &middot; Elias Kunnas
</footer>

</div>
<script>
const DATA = __DATA_JSON__;
const CATEGORY_FI = __CATEGORY_FI_JSON__;
const CATEGORY_ORDER = __CATEGORY_ORDER_JSON__;
const CONFIDENCE_FI = __CONFIDENCE_FI_JSON__;
const SEVERITY_FI = __SEVERITY_FI_JSON__;
const SEVERITY_ORDER = __SEVERITY_ORDER_JSON__;
const SEVERITY_DESC_FI = __SEVERITY_DESC_FI_JSON__;

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ---------------------------------------------------------------------------
// Word-level diff — uses semantic classes, no strikethrough
// ---------------------------------------------------------------------------
function wordDiff(aText, bText) {
  // Returns [aHtml, bHtml] with semantic highlight classes
  const aWords = aText.split(/(\s+)/);
  const bWords = bText.split(/(\s+)/);

  const m = aWords.length, n = bWords.length;
  const MAX = 400;
  if (m > MAX || n > MAX) {
    return [esc(aText), esc(bText)];
  }
  const dp = Array.from({length: m+1}, () => new Int16Array(n+1));
  for (let i = m-1; i >= 0; i--) {
    for (let j = n-1; j >= 0; j--) {
      if (aWords[i] === bWords[j]) dp[i][j] = dp[i+1][j+1] + 1;
      else dp[i][j] = Math.max(dp[i+1][j], dp[i][j+1]);
    }
  }
  const aOut = [], bOut = [];
  let i = 0, j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && aWords[i] === bWords[j]) {
      aOut.push(esc(aWords[i])); bOut.push(esc(bWords[j]));
      i++; j++;
    } else if (j < n && (i >= m || dp[i][j+1] >= dp[i+1][j])) {
      bOut.push('<span class="finlex-only">' + esc(bWords[j]) + '</span>'); j++;
    } else {
      aOut.push('<span class="source-only">' + esc(aWords[i]) + '</span>'); i++;
    }
  }
  return [aOut.join(''), bOut.join('')];
}

// Strip markdown blockquote markers ("> ") used in the YAML correct_text/finlex_text
function stripBlockquote(s) {
  return s.split('\n').map(l => l.replace(/^>\s?/, '')).join('\n').trim();
}

// Check if finlex_text is descriptive prose (not actual legal text)
function isDescriptiveProse(text) {
  if (!text) return true;
  const stripped = stripBlockquote(text);
  // Heuristics: contains metadata-like markers or describes absence
  return /^\[.*\]$|puuttu|ei ole lainkaan|seuraa suoraan|Finlexin (konsolidoidussa |versio)/im.test(stripped)
    && stripped.length < 500;
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------
function buildStats() {
  const bySeverity = {};
  for (const s of SEVERITY_ORDER) bySeverity[s] = 0;
  let definitive = 0;
  for (const e of DATA) {
    bySeverity[e.severity] = (bySeverity[e.severity] || 0) + 1;
    if (e.confidence === 'definitive') definitive++;
  }
  const el = document.getElementById('stats');
  el.innerHTML = `
    <div class="stat-card"><div class="num" style="color:var(--green)">${DATA.length}</div><div class="label">Raportoitua ehdokasta</div></div>
    <div class="stat-card"><div class="num">${definitive}</div><div class="label">Korkea luottamus</div></div>
    <div class="stat-card"><div class="num">${DATA.length - definitive}</div><div class="label">Vahva näyttö</div></div>
    ${SEVERITY_ORDER.filter(s => bySeverity[s] > 0).map(s =>
      `<div class="stat-card"><div class="num">${bySeverity[s]}</div><div class="label">${esc(SEVERITY_FI[s] || s)}</div></div>`
    ).join('')}
  `;
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------
let activeFilter = 'all';

function buildFilters() {
  const bySeverity = {};
  for (const e of DATA) bySeverity[e.severity] = (bySeverity[e.severity] || 0) + 1;
  const el = document.getElementById('filters');

  function render() {
    let h = `<button class="filter-btn ${activeFilter==='all'?'active':''}" data-f="all">Kaikki (${DATA.length})</button>`;
    for (const sev of SEVERITY_ORDER) {
      const count = bySeverity[sev] || 0;
      if (!count) continue;
      h += `<button class="filter-btn ${activeFilter===sev?'active':''}" data-f="${esc(sev)}">${esc(SEVERITY_FI[sev]||sev)} (${count})</button>`;
    }
    el.innerHTML = h;
    el.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        activeFilter = btn.dataset.f;
        render();
        renderFindings();
      });
    });
  }
  render();
}

// ---------------------------------------------------------------------------
// Findings — grouped by severity tier
// ---------------------------------------------------------------------------
function renderFindings() {
  const container = document.getElementById('findings');
  const filtered = activeFilter === 'all' ? DATA : DATA.filter(e => e.severity === activeFilter);

  // Group by severity
  const grouped = {};
  for (const sev of SEVERITY_ORDER) grouped[sev] = [];
  for (const e of filtered) (grouped[e.severity] = grouped[e.severity] || []).push(e);

  let h = '';
  for (const sev of SEVERITY_ORDER) {
    const items = grouped[sev] || [];
    if (!items.length) continue;

    h += `<div class="severity-section">`;
    h += `<div class="severity-header">${esc(SEVERITY_FI[sev]||sev)} <span style="font-weight:400;font-size:14px;color:var(--dim)">(${items.length})</span></div>`;
    if (SEVERITY_DESC_FI[sev]) {
      h += `<div class="severity-desc">${esc(SEVERITY_DESC_FI[sev])}</div>`;
    }

    // Sub-group by category within severity
    const byCat = {};
    for (const cat of CATEGORY_ORDER) byCat[cat] = [];
    for (const e of items) (byCat[e.category] = byCat[e.category] || []).push(e);

    for (const cat of CATEGORY_ORDER) {
      const catItems = byCat[cat] || [];
      if (!catItems.length) continue;
      if (items.length > 3) {
        // Only show sub-headers if there are enough items
        h += `<div class="group-header">${esc(CATEGORY_FI[cat]||cat)} (${catItems.length})</div>`;
      }
      for (const e of catItems) {
        h += buildCard(e);
      }
    }
    h += `</div>`;
  }
  container.innerHTML = h;

  // Wire expand buttons
  container.querySelectorAll('.expand-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const body = document.getElementById(btn.dataset.target);
      if (!body) return;
      body.classList.toggle('open');
      btn.textContent = body.classList.contains('open') ? btn.dataset.labelClose : btn.dataset.labelOpen;
    });
  });
}

function buildCard(e) {
  const sid = e.statute_id;
  const safeSid = sid.replace(/\//g, '-');
  const confClass = e.confidence === 'definitive' ? 'badge-definitive' : 'badge-strong';
  const confLabel = esc(CONFIDENCE_FI[e.confidence] || e.confidence);
  const catLabel = esc(CATEGORY_FI[e.category] || e.category);
  const isHistorical = e.severity === 'substantive_historical';

  let h = `<div class="finding">`;
  h += `<div class="finding-header">
    <span class="finding-sid"><a href="${esc(e.finlex_url)}" target="_blank" rel="noopener">${esc(sid)}</a></span>
    <span class="badge badge-cat">${catLabel}</span>
    <span class="badge ${confClass}">${confLabel}</span>`;
  if (isHistorical) {
    h += `<span class="badge badge-historical">Historiallinen</span>`;
  }
  h += `</div>`;
  h += `<div class="finding-title">${esc(e.title)}</div>`;

  // Johtolause scope — critical for credibility
  if (e.johtolause_scope) {
    h += `<div class="johtolause">
      <div class="johtolause-label">Johtolause</div>
      ${esc(e.johtolause_scope)}
    </div>`;
  }

  if (e.claim) {
    h += `<div class="claim">${esc(e.claim.trim())}</div>`;
  }

  // Evidence display — category-specific rendering
  h += renderEvidence(e);

  // Explanation (expandable)
  if (e.explanation_fi) {
    const expId = 'exp-' + safeSid;
    h += `<button class="expand-btn" data-target="${expId}" data-label-open="Näytä selitys &#x25B8;" data-label-close="Piilota selitys &#x25BE;">Näytä selitys &#x25B8;</button>`;
    h += `<div id="${expId}" class="expand-body">${esc(e.explanation_fi.trim())}</div>`;
  }

  // Caveats
  if (e.known_caveats) {
    h += `<div class="caveat">&#9888; ${esc(e.known_caveats.trim())}</div>`;
  }

  // Amendment citation — fix label for original statutes
  if (e.amendment_id) {
    const isBaseSource = e.amendment_id === e.statute_id;
    const sourceLabel = isBaseSource
      ? `Säädöskokoelman lähde ${esc(e.amendment_id)}`
      : `Muutossäädös ${esc(e.amendment_id)}`;

    const amendParts = [sourceLabel];
    if (e.effective_date) amendParts.push(`voimaan ${esc(e.effective_date)}`);
    const linkHtml = e.amendment_url
      ? `<a href="${esc(e.amendment_url)}" target="_blank" rel="noopener">${amendParts.join(', ')}</a>`
      : amendParts.join(', ');
    h += `<div class="amend-cite">${linkHtml}`;
    if (e.amendment_title) h += ` &mdash; ${esc(e.amendment_title)}`;
    h += `</div>`;
  }

  // Card footer with metadata
  const footParts = [];
  if (e.oracle_pit) footParts.push(`Finlex-pinta: ${esc(e.oracle_pit)}`);
  if (e.auditor) footParts.push(`Tarkastaja: ${esc(e.auditor)}`);
  if (e.reviewed_at) footParts.push(`Tarkistettu: ${esc(e.reviewed_at)}`);
  if (footParts.length) {
    h += `<div class="card-footer">${footParts.join(' &middot; ')}</div>`;
  }

  h += `</div>`;
  return h;
}

// ---------------------------------------------------------------------------
// Category-specific evidence rendering
// ---------------------------------------------------------------------------
function renderEvidence(e) {
  const rawA = e.correct_text ? stripBlockquote(e.correct_text) : '';
  const rawB = e.finlex_text ? stripBlockquote(e.finlex_text) : '';

  if (!rawA && !rawB) return '';

  switch (e.category) {
    case 'missing_inserted_provision':
      return renderMissingInserted(e, rawA);

    case 'missing_repeal':
      return renderMissingRepeal(e, rawA, rawB);

    case 'partial_amendment_loss':
      return renderFocusedTextDiff(e, rawA, rawB);

    case 'wrong_cross_reference':
      return renderFocusedTextDiff(e, rawA, rawB);

    case 'finlex_introduced_defect':
      return renderFocusedTextDiff(e, rawA, rawB);

    case 'missing_applied_amendment':
      return renderFocusedTextDiff(e, rawA, rawB);

    default:
      return renderFocusedTextDiff(e, rawA, rawB);
  }
}

function renderMissingInserted(e, rawA) {
  // Content is entirely missing from Finlex — show only what should be there
  if (!rawA) return '';
  let h = `<div class="evidence-wrap">`;
  h += `<div class="evidence-block" style="background:var(--green-bg);border:1px solid var(--green-border)">
    <div class="evidence-label" style="color:var(--green)">Säädöskokoelman teksti <span style="color:var(--red);font-weight:600">(puuttuu Finlexistä kokonaan)</span></div>
    <pre>${esc(rawA)}</pre>
  </div>`;
  h += `</div>`;
  return h;
}

function renderMissingRepeal(e, rawA, rawB) {
  // For missing repeal: show what SHOULD appear (repeal note) and what Finlex shows EXTRA
  let h = `<div class="evidence-wrap">`;

  if (rawA) {
    h += `<div class="evidence-block" style="background:var(--green-bg);border:1px solid var(--green-border)">
      <div class="evidence-label" style="color:var(--green)">Pitäisi näkyä</div>
      <pre>${esc(rawA)}</pre>
    </div>`;
  }

  if (rawB && !isDescriptiveProse(e.finlex_text)) {
    h += `<div class="evidence-block" style="background:var(--red-bg);border:1px solid var(--red-border)">
      <div class="evidence-label" style="color:var(--red)">Finlexissä näkyy lisäksi</div>
      <pre>${esc(rawB)}</pre>
    </div>`;
  } else if (rawB) {
    // Descriptive prose about what Finlex shows — render as description
    h += `<div class="evidence-block" style="background:var(--red-bg);border:1px solid var(--red-border)">
      <div class="evidence-label" style="color:var(--red)">Finlexin tila</div>
      <pre>${esc(rawB)}</pre>
    </div>`;
  }

  h += `</div>`;
  return h;
}

function renderFocusedTextDiff(e, rawA, rawB) {
  // Both sides have real text — show word-level diff with semantic classes
  if (rawA && rawB && !isDescriptiveProse(e.finlex_text)) {
    const [diffA, diffB] = wordDiff(rawA, rawB);

    let h = `<div class="diff-legend">
      <div class="diff-legend-item"><span class="diff-legend-swatch" style="background:var(--source-only-bg);border:1px solid var(--source-only-border)"></span> Puuttuu Finlexistä</div>
      <div class="diff-legend-item"><span class="diff-legend-swatch" style="background:var(--finlex-only-bg);border:1px solid var(--finlex-only-border)"></span> Ylimääräinen Finlexissä</div>
    </div>`;
    h += `<div class="diff-wrap">
      <div class="diff-side correct">
        <div class="diff-label">Säädöskokoelman teksti</div>
        <pre>${diffA}</pre>
      </div>
      <div class="diff-side finlex">
        <div class="diff-label">Finlexin teksti</div>
        <pre>${diffB}</pre>
      </div>
    </div>`;
    return h;
  }

  // Only correct text available, or finlex_text is descriptive
  if (rawA) {
    let h = `<div class="diff-wrap" style="grid-template-columns:1fr">
      <div class="diff-side correct">
        <div class="diff-label">Säädöskokoelman teksti</div>
        <pre>${esc(rawA)}</pre>
      </div>
    </div>`;
    return h;
  }

  return '';
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
buildStats();
buildFilters();
renderFindings();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_standalone(yaml_dir: Path, output: Path) -> None:
    entries = load_entries(yaml_dir)
    if not entries:
        print("No entries found.", file=sys.stderr)
        sys.exit(1)

    entries.sort(key=statute_sort_key)

    # Clean entries to only the fields the template needs
    clean_entries = []
    for e in entries:
        sid = e.get("statute_id", "")
        severity = SEVERITY_MAP.get(sid, "typography")  # default to lowest tier

        clean_entries.append({
            "statute_id": sid,
            "title": clean_text(e.get("title")),
            "category": e.get("category", ""),
            "confidence": e.get("confidence", ""),
            "severity": severity,
            "claim": clean_text(e.get("claim")),
            "amendment_id": e.get("amendment_id", ""),
            "amendment_title": clean_text(e.get("amendment_title")),
            "effective_date": e.get("effective_date", ""),
            "johtolause_scope": clean_text(e.get("johtolause_scope")),
            "correct_text": clean_text(e.get("correct_text")),
            "finlex_text": clean_text(e.get("finlex_text")),
            "oracle_pit": e.get("oracle_pit", ""),
            "finlex_url": e.get("finlex_url", ""),
            "amendment_url": e.get("amendment_url", ""),
            "explanation_fi": clean_text(e.get("explanation_fi")),
            "known_caveats": clean_text(e.get("known_caveats")),
            "auditor": e.get("auditor", ""),
            "reviewed_at": e.get("reviewed_at", ""),
        })

    page = HTML_TEMPLATE
    page = page.replace("__DATA_JSON__", json.dumps(clean_entries, ensure_ascii=False))
    page = page.replace("__CATEGORY_FI_JSON__", json.dumps(CATEGORY_FI, ensure_ascii=False))
    page = page.replace("__CATEGORY_ORDER_JSON__", json.dumps(CATEGORY_ORDER, ensure_ascii=False))
    page = page.replace("__CONFIDENCE_FI_JSON__", json.dumps(CONFIDENCE_FI, ensure_ascii=False))
    page = page.replace("__SEVERITY_FI_JSON__", json.dumps(SEVERITY_FI, ensure_ascii=False))
    page = page.replace("__SEVERITY_ORDER_JSON__", json.dumps(SEVERITY_ORDER, ensure_ascii=False))
    page = page.replace("__SEVERITY_DESC_FI_JSON__", json.dumps(SEVERITY_DESC_FI, ensure_ascii=False))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")

    by_sev: dict[str, int] = {}
    for e in clean_entries:
        by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1

    print(f"Built {output} — {len(clean_entries)} findings, {output.stat().st_size // 1024} KB")
    for sev in SEVERITY_ORDER:
        count = by_sev.get(sev, 0)
        if count:
            label = SEVERITY_FI.get(sev, sev)
            print(f"  {label}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build standalone publication findings HTML page")
    parser.add_argument("--output", default="viewer/finlex-vahvistetut.html", help="Output HTML file")
    parser.add_argument("--yaml-dir", default=".tmp/finlex_candidate_findings", help="YAML directory")
    args = parser.parse_args()

    build_standalone(Path(args.yaml_dir), Path(args.output))


if __name__ == "__main__":
    main()
