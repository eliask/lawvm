// statute-timeline.js — Universal viewer for LawVM "certified transition graph"
// exports (SQLite, schema transition-graph.v1), any jurisdiction.
//
// Loads a per-statute SQLite DB via sql.js (CDN), folds the certified L3
// transitions in the browser to reconstruct the statute at any change-date,
// and self-verifies by recomputing the reproducible tree hash and asserting it
// equals checkpoints.tree_hash — the hash authored by the Python LawVM engine.
//
// What the hash verification proves (and what it does NOT):
//   PROVEN here: the structure rendered in the browser == the structure the
//     engine computed (browser fold tree-hash == engine checkpoint tree-hash).
//   NOT claimed: that the engine matches the official consolidation, nor that
//     either matches enacted law. Those are separate layers, tested engine-side.
//
// Certification vs localization (the honest-modesty contract):
//   Transitions are CERTIFIED at the export's covering-frontier granularity
//   (meta.certification_granularity — chapter for the bundled 301/2004).
//   Any finer-grained attribution shown here (per-§ / per-momentti version
//   trails, changed-provision highlighting) is DERIVED in the browser by
//   diffing the certified pre/post subtrees, and is labelled as derived.
//
// Universality: all UI strings live in STR[lang]; jurisdiction-specific
// presentation (kind labels, address formatting, op-kind vocabulary,
// preparatory-works link building, citation shape) lives in JURIS profiles.
// The active language/profile is chosen from the DB meta (lang, jurisdiction)
// with manifest-entry fallback. A UK/EE/NZ export needs a profile entry and
// localized strings, no structural changes.

'use strict';

// =====================================================================
// Localization: UI strings per language
// =====================================================================
// STR[lang] and OP_KINDS_BY_LANG[lang] live in statute-timeline.i18n.js, loaded
// as a classic <script> before this one (shared top-level scope). T = STR.fi is
// initialized below once that table is present.

// =====================================================================
// Jurisdiction profiles: presentation of legal structure + provenance links
// =====================================================================
const JURIS = {
  fi: {
    lang: 'fi',
    // Label for a structural node. `num` is the node's own printed num text
    // (preferred when present — it is source text, not invented), `lbl` the
    // engine label, `ordinal` a positional fallback only.
    kindLabel(kind, num, lbl, ordinal) {
      if (kind === 'chapter') return num || (lbl ? `${lbl} luku` : 'luku');
      if (kind === 'section') return num || (lbl ? `${lbl} §` : '§');
      if (kind === 'subsection') return `${lbl || ordinal} mom.`;
      if (kind === 'paragraph' || kind === 'subparagraph') return num || (lbl ? `${lbl})` : `${ordinal})`);
      return num || lbl || kind;
    },
    // Address segment formatting ("chapter:4/section:54a" pieces). Finnish
    // statutes have official Swedish citation terminology — honor a Swedish
    // UI; § and mom. are shared notation.
    addrSeg(kind, n) {
      const sv = uiLang === 'sv';
      if (kind === 'chapter') return sv ? `${n} kap.` : `${n} luku`;
      if (kind === 'section') return `${n} §`;
      if (kind === 'subsection') return `${n} mom.`;
      if (kind === 'paragraph') return sv ? `${n} punkten` : `${n} kohta`;
      if (kind === 'subparagraph') return sv ? `${n} underpunkten` : `${n} alakohta`;
      return `${kind} ${n}`;
    },
    opKinds: {
      insert: 'lisätty', replace: 'muutettu', repeal: 'kumottu', delete: 'poistettu',
      move: 'siirretty', substitute: 'korvattu', renumber: 'numeroitu uudelleen',
      expiry: 'määräaikainen voimassaolo päättyi',
    },
    // Preparatory-works reference (HE) → eduskunta valtiopäiväasia page.
    // ref is the human token "HE {n}/{year} vp"; the canonical URL is that token
    // URL-encoded under /asiat-ja-aanestykset/valtiopaivaasiat/ (matches the
    // lawvm CLI OSC 8 link form in tools/hyperlinks.py).
    prepWorksUrl(ref) {
      return 'https://www.eduskunta.fi/asiat-ja-aanestykset/valtiopaivaasiat/' + encodeURIComponent(ref);
    },
    fmtDate(iso) { // 2015-09-01 -> 1.9.2015 for citations; UI stays ISO
      const [y, m, d] = iso.split('-');
      return `${+d}.${+m}.${y}`;
    },
  },
  uk: {
    lang: 'en',
    kindLabel(kind, num, lbl, ordinal) {
      if (num) return num;
      if (kind === 'part') return lbl ? `Part ${lbl}` : 'Part';
      if (kind === 'chapter') return lbl ? `Chapter ${lbl}` : 'Chapter';
      if (kind === 'section') return lbl ? `${lbl}` : 'Section';
      if (kind === 'subsection') return `(${lbl || ordinal})`;
      if (kind === 'paragraph' || kind === 'subparagraph') return `(${lbl || ordinal})`;
      return lbl || kind;
    },
    addrSeg(kind, n) {
      if (kind === 'part') return `Part ${n}`;
      if (kind === 'chapter') return `Chapter ${n}`;
      if (kind === 'section') return `s ${n}`;
      if (kind === 'subsection') return `(${n})`;
      if (kind === 'paragraph') return `(${n})`;
      if (kind === 'subparagraph') return `(${n})`;
      return `${kind} ${n}`;
    },
    opKinds: {
      insert: 'inserted', replace: 'substituted', repeal: 'repealed', delete: 'omitted',
      move: 'moved', substitute: 'substituted', renumber: 'renumbered',
      expiry: 'fixed-term validity expired',
    },
    prepWorksUrl(ref) {
      return 'https://bills.parliament.uk/?SearchTerm=' + encodeURIComponent(ref);
    },
    fmtDate(iso) { return iso; },
  },
  generic: {
    lang: 'en',
    kindLabel(kind, num, lbl, ordinal) {
      if (num) return num;
      const cap = kind.charAt(0).toUpperCase() + kind.slice(1);
      return lbl ? `${cap} ${lbl}` : cap;
    },
    addrSeg(kind, n) {
      const cap = kind.charAt(0).toUpperCase() + kind.slice(1);
      return `${cap} ${n}`;
    },
    opKinds: {
      insert: 'inserted', replace: 'amended', repeal: 'repealed', delete: 'deleted',
      move: 'moved', substitute: 'substituted', renumber: 'renumbered',
      expiry: 'fixed-term validity expired',
    },
    prepWorksUrl() { return null; },
    fmtDate(iso) { return iso; },
  },
};

let T = STR.fi;     // active UI strings
let J = JURIS.fi;   // active jurisdiction profile
let uiLang = 'fi';  // effective UI language (override > statute default)
let uiLangOverride = null;
try { uiLangOverride = localStorage.getItem('lawvm-viewer-lang') || null; } catch (e) { /* storage unavailable */ }

function tr(key, ...args) {
  const v = T[key];
  if (v === undefined) return key;
  return typeof v === 'function' ? v(...args) : v;
}

// Op-kind vocabulary per UI language. The jurisdiction profile's own table is
// authoritative when the UI language matches the jurisdiction's (it carries
// drafting-convention nuance, e.g. UK "substituted/omitted"); otherwise fall
// back to the UI language's generic legal vocabulary.

function opKindLabel(k) {
  if (uiLang === J.lang) return J.opKinds[k] || k;
  const tbl = OP_KINDS_BY_LANG[uiLang] || {};
  return tbl[k] || J.opKinds[k] || k;
}

// =====================================================================
// State
// =====================================================================
let db = null;              // sql.js Database
let blobCache = {};         // content_hash -> parsed IRNode (decoded JSON)
let transitions = [];       // all transitions, sequence-ordered
let checkpointByDate = {};  // date -> {tree_hash, active_node_count}
let changeDates = [];       // sorted ISO date strings
let sourceById = {};        // source_id -> source_artifacts row
let interlinks = [];        // optional precomputed semantic interlink rows
let interlinksByRenderedAddress = new Map();
let displayNodeByDateAddress = new Map();
let evidenceEvents = [];    // optional precomputed LawVM uncertainty rows
let evidenceBySource = new Map();
let evidenceByDate = new Map();
let evidenceWithAddress = [];
let metaInfo = {};          // decoded meta table
let selectedAddress = null; // address with an open inline history panel
let mode = 'law';    // 'law' | 'amendments' | 'search' | 'compare'
let selectedSourceId = null;
let currentStatuteId = null;
let suppressHashUpdate = false;
let curDateIdx = -1;
let curLive = new Map();
let curTombstoned = new Map();
let prevLive = new Map();
let changedAddrs = new Set();   // covering-unit addresses changed vs previous date
let curTreeHash = '';
let allFoldsMemo = null;        // date -> {live, tombstoned} for all change dates
let changeIdxCache = null;      // addr -> sorted date indices where its content changed
let pendingSearchQuery = null;
// Phrase to highlight in the main document pane when a diachronic-search (or a
// q-bearing permalink) lands on a provision. {addr, phrase}. Reapplied after
// every #doc re-render because CSS-highlight Ranges go stale on rebuild.
// Reset matrix — cleared on: panel close (clearSelection), manual pick of a
// different provision (toggleInlineHistory), statute switch (loadStatute).
// Survives (by design): date scrubs and mode round-trips (reapplied on render).
let searchHighlight = null;
const SEARCH_HL_NAME = 'lawvm-search';
let compareSel = { d1: null, d2: null };
const textDecoder = new TextDecoder('utf-8');

// =====================================================================
// sql.js helpers
// =====================================================================
function q(sql, params) {
  if (!db) return [];
  try {
    const stmt = db.prepare(sql);
    if (params) stmt.bind(params);
    const rows = [];
    while (stmt.step()) rows.push(stmt.getAsObject());
    stmt.free();
    return rows;
  } catch (e) {
    console.warn('SQL error:', e.message, sql);
    return [];
  }
}
function q1(sql, params) { return q(sql, params)[0] || null; }
function tableExists(name) {
  return !!q1("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [name]);
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
function escAttr(s) { return escHtml(s).replace(/"/g, '&quot;'); }
function cssEsc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'); }

// =====================================================================
// Reference layer: a generic kind-keyed registry for clickable, hoverable
// cross-references (amending acts, dates, and — later — structured inline
// statute citations). Each kind declares:
//   display(payload) -> { text, title, cls }   inner text + a11y title + class
//   nav(payload)                                click action (navigation)
//   hover(payload)   -> HTMLString | Promise<HTMLString> | null   (optional)
// hover MAY be async (return a Promise) for future cross-statute fetches. All
// accessors MUST degrade — never throw on missing data; fall back to plain text.
// Adding a kind is a localized change here; render sites use refLink() and all
// click/hover wiring is delegated, so nothing else needs to change.
const REF_KINDS = {
  // amending act → Amendments mode, that act selected
  source: {
    display(p) {
      const src = sourceById[p.id];
      const text = p.text || (src ? (src.canonical_id || src.title || p.id) : p.id);
      return { text, title: src ? (src.title || src.canonical_id || p.id) : p.id, cls: 'ref-source' };
    },
    nav(p) { goToAmendment(p.id); },
    hover(p) { return sourceHovercardHtml(p.id); },
  },
  // date → law in force on that date (optionally at a provision, carrying a
  // search phrase so the highlight survives the jump)
  date: {
    display(p) { return { text: p.text || p.date, title: p.date, cls: 'ref-date' }; },
    nav(p) { goToAddrAtDate(p.addr || null, p.date, p.phrase || undefined); },
    hover: null,
  },
  semantic: {
    display(p) {
      const text = p.text || p.surface_text || p.target_work_id || p.interlink_id || '';
      const title = [p.role, p.target_work_id, p.target_locator].filter(Boolean).join(' · ');
      return { text, title, cls: 'ref-semantic' };
    },
    nav: null,
    hover(p) { return semanticInterlinkHovercardHtml(p); },
  },
};

// Emit a reference as an <a>. The payload rides in a data-* attr (JSON) so the
// DELEGATED click/hover handlers rehydrate it without per-element closures (the
// viewer rebuilds #doc/#view constantly). title= is the no-JS/no-card fallback.
function refLink(kind, payload, displayText) {
  const def = REF_KINDS[kind];
  if (!def) return escHtml(displayText != null ? displayText : (payload && (payload.id || payload.date)) || '');
  let info = null;
  try { info = def.display(payload); } catch (e) { /* degrade */ }
  const text = displayText != null ? displayText : (info ? info.text : '');
  const title = info && info.title ? info.title : '';
  const cls = info && info.cls ? info.cls : '';
  const hoverable = typeof def.hover === 'function' ? ' data-ref-hover="1"' : '';
  return `<a href="#" class="ref-link ${cls}" data-ref-kind="${escAttr(kind)}"`
    + ` data-ref-payload="${escAttr(JSON.stringify(payload))}"${hoverable}`
    + (title ? ` title="${escAttr(title)}"` : '') + `>${escHtml(text)}</a>`;
}

function refPayloadFrom(el) {
  try { return JSON.parse(el.getAttribute('data-ref-payload')); } catch (e) { return null; }
}

// Click delegation (installed once). Works for any present or future ref kind.
document.addEventListener('click', (e) => {
  const a = e.target.closest('a.ref-link');
  if (!a) return;
  e.preventDefault();
  e.stopPropagation();           // don't also toggle a row / select an amend item
  const def = REF_KINDS[a.dataset.refKind];
  if (!def || !def.nav) return;
  const p = refPayloadFrom(a);
  if (p) { try { def.nav(p); } catch (err) { console.warn('ref nav failed', err); } }
});

// ---- hovercard: a single shared popover, reused for every hover ----
let _hcEl = null, _hcShowTimer = null, _hcHideTimer = null, _hcToken = 0;
const _hasHover = !!(window.matchMedia && window.matchMedia('(hover: hover)').matches);

function hovercardEl() {
  if (_hcEl) return _hcEl;
  _hcEl = document.createElement('div');
  _hcEl.className = 'ref-hovercard';
  _hcEl.setAttribute('role', 'tooltip');
  _hcEl.hidden = true;
  _hcEl.addEventListener('mouseenter', () => clearTimeout(_hcHideTimer));
  _hcEl.addEventListener('mouseleave', hideHovercard);
  document.body.appendChild(_hcEl);
  return _hcEl;
}

function positionHovercard(anchor) {
  const el = hovercardEl();
  const r = anchor.getBoundingClientRect();
  el.hidden = false; // measure with content present
  const cw = el.offsetWidth, ch = el.offsetHeight;
  let left = Math.max(window.scrollX + 8,
    Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - cw - 8));
  let top = r.top + window.scrollY - ch - 8;
  if (r.top - ch - 8 < 0) top = r.bottom + window.scrollY + 8; // flip below if clipped
  el.style.left = left + 'px';
  el.style.top = top + 'px';
}

async function showHovercardFor(anchor) {
  const def = REF_KINDS[anchor.dataset.refKind];
  if (!def || typeof def.hover !== 'function') return;
  const p = refPayloadFrom(anchor);
  if (!p) return;
  const token = ++_hcToken; // guards async + re-render races: last hover wins
  let html = null;
  try { html = await def.hover(p); } catch (e) { html = null; }
  if (token !== _hcToken || !html || !anchor.isConnected) return;
  const el = hovercardEl();
  el.innerHTML = html;
  positionHovercard(anchor);
}

function scheduleShow(anchor) {
  clearTimeout(_hcHideTimer); clearTimeout(_hcShowTimer);
  _hcShowTimer = setTimeout(() => showHovercardFor(anchor), 220);
}
function hideHovercard() {
  clearTimeout(_hcShowTimer);
  _hcHideTimer = setTimeout(() => { _hcToken++; if (_hcEl) _hcEl.hidden = true; }, 120);
}

if (_hasHover) {
  document.addEventListener('mouseover', (e) => {
    const a = e.target.closest('a.ref-link[data-ref-hover]');
    if (a) scheduleShow(a);
  });
  document.addEventListener('mouseout', (e) => {
    const a = e.target.closest('a.ref-link[data-ref-hover]');
    if (a && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.ref-hovercard'))) hideHovercard();
  });
}
// Keyboard accessibility (always on — focus is real on touch+keyboard too).
document.addEventListener('focusin', (e) => {
  const a = e.target.closest('a.ref-link[data-ref-hover]');
  if (a) showHovercardFor(a);
});
document.addEventListener('focusout', (e) => {
  if (e.target.closest('a.ref-link[data-ref-hover]')) hideHovercard();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _hcEl && !_hcEl.hidden) { _hcToken++; _hcEl.hidden = true; }
});
window.addEventListener('scroll', () => { if (_hcEl && !_hcEl.hidden) hideHovercard(); }, true);

// Sync hovercard content for an amending act. Returns null (→ no card) if the
// source is unknown, so the link still navigates but nothing pops.
function sourceHovercardHtml(id) {
  const src = sourceById[id];
  if (!src) return null;
  const am = amendmentList().find(a => a.source_id === id);
  const effDates = [...new Set(transitions.filter(t => t.source_id === id).map(t => t.effective_date))].sort();
  const heRef = (transitions.find(t => t.source_id === id && t.he_ref) || {}).he_ref;
  let h = `<div class="hc-title">${escHtml(src.title || src.canonical_id || id)}</div><dl class="hc-meta">`;
  if (src.canonical_id) h += `<div><dt>${escHtml(tr('amendingAct'))}</dt><dd>${escHtml(src.canonical_id)}</dd></div>`;
  if (src.date) h += `<div><dt>${escHtml(tr('givenDate'))}</dt><dd>${escHtml(src.date)}</dd></div>`;
  if (effDates.length) h += `<div><dt>${escHtml(tr('effectiveLbl'))}</dt><dd>${escHtml(effDates.join(', '))}</dd></div>`;
  if (am) h += `<div><dt>${escHtml(tr('amendWhat'))}</dt><dd>${escHtml(tr('targetings', am.opCount))}</dd></div>`;
  if (heRef) h += `<div><dt>${escHtml(tr('prepWorks'))}</dt><dd>${prepWorksHtml(heRef)}</dd></div>`;
  return h + `</dl>`;
}

function indexInterlinks() {
  interlinksByRenderedAddress = new Map();
  for (const row of interlinks) {
    if (!row.rendered_address) continue;
    let rows = interlinksByRenderedAddress.get(row.rendered_address);
    if (!rows) { rows = []; interlinksByRenderedAddress.set(row.rendered_address, rows); }
    rows.push(row);
  }
}

function semanticInterlinkHovercardHtml(row) {
  if (!row) return null;
  let h = `<div class="hc-title">${escHtml(row.surface_text || row.target_work_id || row.interlink_id || '')}</div><dl class="hc-meta">`;
  if (row.role) h += `<div><dt>${escHtml(tr('interlinkRole'))}</dt><dd>${escHtml(row.role)}</dd></div>`;
  if (row.target_work_id) h += `<div><dt>${escHtml(tr('interlinkTarget'))}</dt><dd>${escHtml(row.target_work_id)}</dd></div>`;
  if (row.target_locator) h += `<div><dt>${escHtml(tr('interlinkLocator'))}</dt><dd>${escHtml(row.target_locator)}</dd></div>`;
  if (row.resolution_status) h += `<div><dt>${escHtml(tr('interlinkStatus'))}</dt><dd>${escHtml(row.resolution_status)}</dd></div>`;
  if (row.confidence) h += `<div><dt>${escHtml(tr('interlinkConfidence'))}</dt><dd>${escHtml(row.confidence)}</dd></div>`;
  if (row.resolver_id) h += `<div><dt>${escHtml(tr('interlinkResolver'))}</dt><dd>${escHtml(row.resolver_id)}</dd></div>`;
  return h + `</dl>`;
}

function interlinkActiveAt(row, date) {
  if (date && row.rendered_effective_date && row.rendered_effective_date !== date) return false;
  if (date && row.valid_at_start && row.valid_at_start > date) return false;
  if (date && row.valid_at_end && row.valid_at_end < date) return false;
  return true;
}

function renderedInterlinksForSegment(addr, segmentIndex, text) {
  const rows = interlinksByRenderedAddress.get(addr) || [];
  return rows
    .filter(row => Number(row.rendered_segment_index) === segmentIndex && interlinkActiveAt(row, changeDates[curDateIdx]))
    .map(row => ({ row, start: Number(row.rendered_char_start), end: Number(row.rendered_char_end) }))
    .filter(item => Number.isInteger(item.start) && Number.isInteger(item.end)
      && item.start >= 0 && item.end > item.start && item.end <= text.length)
    .filter(item => !item.row.surface_text || text.slice(item.start, item.end) === item.row.surface_text)
    .sort((a, b) => a.start - b.start || a.end - b.end);
}

function renderTextWithInterlinks(addr, segmentIndex, text) {
  const spans = renderedInterlinksForSegment(addr, segmentIndex, text);
  if (!spans.length) return escHtml(text);
  let html = '', pos = 0;
  for (const span of spans) {
    if (span.start < pos) continue;
    html += escHtml(text.slice(pos, span.start));
    const surface = text.slice(span.start, span.end);
    html += refLink('semantic', { ...span.row, text: surface }, surface);
    pos = span.end;
  }
  html += escHtml(text.slice(pos));
  return html;
}

function pushMapList(map, key, row) {
  if (!key) return;
  let rows = map.get(key);
  if (!rows) { rows = []; map.set(key, rows); }
  rows.push(row);
}

function evidenceSeverityRank(row) {
  const s = (row && row.severity) || '';
  if (s === 'error' || s === 'critical') return 3;
  if (s === 'warning' || s === 'warn') return 2;
  return 1;
}

function evidenceSeverityClass(row) {
  const rank = evidenceSeverityRank(row);
  return rank >= 3 ? 'error' : rank === 2 ? 'warn' : 'info';
}

function indexEvidenceEvents() {
  evidenceBySource = new Map();
  evidenceByDate = new Map();
  evidenceWithAddress = [];
  for (const row of evidenceEvents) {
    pushMapList(evidenceBySource, row.source_id || '', row);
    pushMapList(evidenceByDate, row.effective_date || '', row);
    if (row.target_address) evidenceWithAddress.push(row);
  }
}

function addressesRelated(a, b) {
  if (!a || !b) return false;
  return a === b || a.startsWith(b + '/') || b.startsWith(a + '/');
}

function sortEvidenceRows(rows) {
  return rows.sort((a, b) =>
    evidenceSeverityRank(b) - evidenceSeverityRank(a)
    || String(a.event_id || '').localeCompare(String(b.event_id || '')));
}

function dedupeEvidenceRows(rows) {
  const out = [];
  const seen = new Set();
  for (const row of rows) {
    const key = row.event_id || `${row.surface}|${row.kind}|${row.source_id}|${row.target_address}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return sortEvidenceRows(out);
}

function evidenceRelatedToAddress(addr) {
  if (!addr || !evidenceWithAddress.length) return [];
  return dedupeEvidenceRows(evidenceWithAddress.filter(row => addressesRelated(addr, row.target_address)));
}

function evidenceForSource(sourceId) {
  return dedupeEvidenceRows([...(evidenceBySource.get(sourceId || '') || [])]);
}

function evidenceForChange(addr, date, sourceId) {
  const rows = [];
  for (const row of evidenceRelatedToAddress(addr)) {
    if (row.effective_date && date && row.effective_date !== date) continue;
    if (row.source_id && sourceId && row.source_id !== sourceId) continue;
    rows.push(row);
  }
  return dedupeEvidenceRows(rows);
}

function evidenceBadgeHtml(addr) {
  const rows = evidenceRelatedToAddress(addr);
  if (!rows.length) return '';
  const cls = evidenceSeverityClass(rows[0]);
  return `<button class="evidence-badge ev-${cls}" data-addr="${escAttr(addr)}"`
    + ` title="${escAttr(tr('evidenceBadgeTip', rows.length))}">! ${escHtml(String(rows.length))}</button>`;
}

function evidenceLabel(row) {
  return row.title || row.kind || row.surface || row.event_id || tr('evidence');
}

function evidenceMetaHtml(row) {
  const bits = [];
  if (row.surface) bits.push(row.surface);
  if (row.role) bits.push(row.role);
  if (row.phase) bits.push(row.phase);
  if (row.source_id) bits.push(refLink('source', { id: row.source_id }, row.source_id));
  if (row.effective_date) bits.push(refLink('date', { date: row.effective_date, addr: row.target_address || undefined }, row.effective_date));
  if (row.target_address) bits.push(escHtml(prettyAddr(row.target_address)));
  if (row.rule_id && row.rule_id !== row.kind) bits.push(escHtml(row.rule_id));
  return bits.join(' · ');
}

function evidenceListHtml(rows, title) {
  const items = dedupeEvidenceRows(rows || []);
  if (!items.length) return '';
  let html = `<details class="evidence-list" open><summary>${escHtml(title || tr('evidenceListTitle', items.length))}</summary>`;
  for (const row of items) {
    const cls = evidenceSeverityClass(row);
    html += `<div class="evidence-item ev-${cls}">`
      + `<div class="evidence-item-head">`
      + `<span class="evidence-kind">${escHtml(row.kind || row.surface || '')}</span>`
      + `<span class="evidence-title">${escHtml(evidenceLabel(row))}</span>`
      + `</div>`;
    const meta = evidenceMetaHtml(row);
    if (meta) html += `<div class="evidence-meta">${meta}</div>`;
    html += `</div>`;
  }
  html += `</details>`;
  return html;
}

function indexDisplayNodes(rows) {
  displayNodeByDateAddress = new Map();
  for (const row of rows || []) {
    if (!row.date || !row.address) continue;
    displayNodeByDateAddress.set(`${row.date}\n${row.address}`, row);
  }
}

function displayNodeFor(addr, date) {
  if (!addr || !date) return null;
  return displayNodeByDateAddress.get(`${date}\n${addr}`) || null;
}

function displayLabelHeading(addr, node) {
  if (node) return { label: kindLabel(node, 0), heading: nodeHeading(node) };
  const [k, n] = addr.split('/').pop().split(':');
  const display = displayNodeFor(addr, changeDates[curDateIdx]);
  if (!display) return { label: J.addrSeg(k, n), heading: '' };
  return {
    label: J.kindLabel(display.kind || k, display.num || '', display.label || n, parseInt(n || '0', 10) || 0),
    heading: display.heading || '',
  };
}

// Navigate to an amending act in the Amendments view.
function goToAmendment(sourceId) {
  if (!sourceId) return;
  selectedSourceId = sourceId;
  setMode('amendments');
  setTimeout(() => {
    const li = document.querySelector(`.amend-item[data-src="${cssEsc(sourceId)}"]`);
    if (li) {
      li.scrollIntoView({ block: 'nearest' });
      li.classList.add('flash');
      setTimeout(() => li.classList.remove('flash'), 1200);
    }
  }, 30);
}

// ---- content blob decoding (BLOB → Uint8Array → JSON) ----
function getBlob(contentHash) {
  if (!contentHash) return null;
  if (contentHash in blobCache) return blobCache[contentHash];
  const row = q1('SELECT content_json FROM content_blobs WHERE content_hash = ?', [contentHash]);
  let node = null;
  if (row && row.content_json != null) {
    let txt = row.content_json;
    if (txt instanceof Uint8Array) txt = textDecoder.decode(txt);
    try { node = JSON.parse(txt); } catch (e) { console.warn('blob parse fail', contentHash, e.message); }
  }
  blobCache[contentHash] = node;
  return node;
}

// =====================================================================
// Certified fold + verification
// =====================================================================
// NOTE on expires_date: the engine encodes temporal reversion as EXPLICIT
// engine-authored transitions, not as expires_date rows. A silent expires_date
// delete here would render WRONG LAW, so we FAIL LOUDLY if one is encountered.
function foldAt(date) {
  const live = new Map();
  const tombstoned = new Map();
  const failures = [];
  for (const t of transitions) {
    if (t.effective_date > date) break;
    if (t.expires_date && t.expires_date !== '') {
      throw new Error(
        `expires_date unsupported in viewer fold — refusing to render possibly-wrong law `
        + `(transition ${t.transition_id}, address ${t.target_address}, expires ${t.expires_date}). `
        + `Reversion must be encoded as explicit transitions.`);
    }
    const cur = live.get(t.target_address) || '';
    if (cur !== t.pre_hash) {
      failures.push({ kind: 'pre_hash_mismatch', address: t.target_address, expected: t.pre_hash, actual: cur });
    }
    if (t.action === 'delete_subtree' || t.action === 'tombstone' || t.post_hash === '') {
      live.delete(t.target_address);
      tombstoned.set(t.target_address, { date: t.effective_date, source_id: t.source_id, he_ref: t.he_ref });
    } else {
      live.set(t.target_address, t.post_hash);
      tombstoned.delete(t.target_address);
    }
  }
  return { live, tombstoned, failures };
}

function allFolds() {
  if (!allFoldsMemo) {
    allFoldsMemo = {};
    for (const d of changeDates) allFoldsMemo[d] = foldAt(d);
  }
  return allFoldsMemo;
}

// Reproducible tree hash over the covering set — same recipe as the engine.
async function reproducibleTreeHash(live) {
  const entries = [...live.entries()].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const chunks = [];
  const enc = new TextEncoder();
  for (const [addr, sub] of entries) {
    chunks.push(enc.encode(addr), new Uint8Array([0x00]), enc.encode(sub), new Uint8Array([0x01]));
  }
  let total = 0; for (const c of chunks) total += c.length;
  const buf = new Uint8Array(total);
  let off = 0; for (const c of chunks) { buf.set(c, off); off += c.length; }
  const digest = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

// =====================================================================
// Statute selection / loading
// =====================================================================
const statuteSel = document.getElementById('statute-select');
let manifest = [];

applyLocale('fi', 'fi'); // boot locale (honors a stored override) before any data loads

fetch('statute-timeline-manifest.json').then(r => r.json()).then(m => {
  manifest = m;
  rebuildStatuteOptions();
  const initial = parseHash();
  const wanted = initial && manifest.find(s => s.statute_id === initial.statute) ? initial.statute
    : (manifest.length ? manifest[0].statute_id : null);
  if (wanted) { statuteSel.value = wanted; loadStatute(wanted, initial); }
}).catch(e => {
  document.getElementById('app').innerHTML = `<p class="error-box">${escHtml(tr('manifestFail'))}: ${escHtml(e.message)}</p>`;
});

statuteSel.addEventListener('change', () => { if (statuteSel.value) loadStatute(statuteSel.value); });
document.querySelectorAll('#lang-toggle button').forEach(b => {
  b.addEventListener('click', () => setUiLang(b.dataset.lang));
});

function applyLocale(statuteLang, juris) {
  const lang = uiLangOverride || statuteLang || 'fi';
  uiLang = STR[lang] ? lang : 'en';
  T = STR[uiLang];
  J = JURIS[juris] || JURIS.generic;
  document.documentElement.lang = uiLang;
  const tg = document.getElementById('tagline');
  if (tg) tg.textContent = tr('tagline');
  const ft = document.getElementById('footer-text');
  if (ft) ft.textContent = tr('footer');
  const sl = document.getElementById('statute-label');
  if (sl) sl.textContent = tr('statuteLabel');
  const ll = document.getElementById('lang-label');
  if (ll) ll.textContent = tr('langLabel');
  document.querySelectorAll('#lang-toggle button').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === uiLang);
  });
}

// UI language toggle: override persists across sessions and re-renders the
// whole app in place (statute data is untouched — source text stays in its
// source language).
function setUiLang(lang) {
  if (lang === uiLang) return;
  uiLangOverride = lang;
  try { localStorage.setItem('lawvm-viewer-lang', lang); } catch (e) { /* ignore */ }
  applyLocale(metaInfo.lang || 'fi', metaInfo.jurisdiction || 'fi');
  rebuildStatuteOptions();
  rerenderAll();
}

function rebuildStatuteOptions() {
  if (!statuteSel) return;
  const cur = statuteSel.value;
  statuteSel.innerHTML = `<option value="">${escHtml(tr('chooseStatute'))}</option>`;
  for (const s of manifest) {
    const opt = document.createElement('option');
    opt.value = s.statute_id;
    opt.textContent = `${s.statute_id} — ${s.title} (${tr('changeDays', s.change_count)})`;
    statuteSel.appendChild(opt);
  }
  statuteSel.value = cur;
}

async function rerenderAll() {
  if (!db || !changeDates.length) return;
  const m = mode;
  suppressHashUpdate = true;
  try {
    renderShell();
    setMode(m, /*skipRender*/ true);
    if (m === 'law') await selectDate(curDateIdx >= 0 ? curDateIdx : changeDates.length - 1);
    else if (m === 'amendments') renderAmendments();
    else if (m === 'search') renderSearch();
    else renderCompare();
  } finally {
    suppressHashUpdate = false;
    updateHash();
  }
}

function metaValue(key) {
  const row = q1('SELECT value FROM meta WHERE key=?', [key]);
  if (!row) return null;
  try { return JSON.parse(row.value); } catch (e) { return row.value; }
}

async function loadStatute(statuteId, permalink) {
  const app = document.getElementById('app');
  app.innerHTML = `<p class="loading">${escHtml(tr('loadingStatute'))}</p>`;
  const entry = manifest.find(s => s.statute_id === statuteId);
  if (!entry) { app.innerHTML = `<p class="error-box">${escHtml(tr('notInManifest'))}</p>`; return; }
  currentStatuteId = statuteId;

  try {
    const SQL = await initSqlJs({ locateFile: f => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.13.0/${f}` });
    const resp = await fetch(entry.db);
    if (!resp.ok) throw new Error(`HTTP ${resp.status} (${entry.db})`);
    const buf = await resp.arrayBuffer();
    db = new SQL.Database(new Uint8Array(buf));
    blobCache = {}; selectedAddress = null; selectedSourceId = null;
    allFoldsMemo = null; changeIdxCache = null; blobTextByHash = {};
    interlinks = []; interlinksByRenderedAddress = new Map();
    displayNodeByDateAddress = new Map();
    evidenceEvents = []; evidenceBySource = new Map(); evidenceByDate = new Map(); evidenceWithAddress = [];
    compareSel = { d1: null, d2: null };
    // A search highlight + its `q` permalink param belong to the previous
    // statute; drop both so they never leak across a statute switch.
    searchHighlight = null;
    if (window.CSS && CSS.highlights) CSS.highlights.delete(SEARCH_HL_NAME);

    transitions = q('SELECT * FROM transitions ORDER BY sequence ASC');
    checkpointByDate = {};
    for (const c of q('SELECT date, tree_hash, active_node_count FROM checkpoints')) {
      checkpointByDate[c.date] = c;
    }
    sourceById = {};
    for (const s of q('SELECT * FROM source_artifacts')) sourceById[s.source_id] = s;
    indexDisplayNodes(q('SELECT * FROM display_nodes ORDER BY date, address'));
    if (tableExists('interlinks')) {
      interlinks = q('SELECT * FROM interlinks ORDER BY interlink_id');
      indexInterlinks();
    } else if (tableExists('lawvm_interlinks')) {
      interlinks = q('SELECT * FROM lawvm_interlinks ORDER BY interlink_id');
      indexInterlinks();
    }
    evidenceEvents = q('SELECT * FROM evidence_events ORDER BY event_id');
    indexEvidenceEvents();

    metaInfo = {
      title: metaValue('title') || entry.title || '',
      lang: metaValue('lang') || entry.lang || 'fi',
      jurisdiction: metaValue('jurisdiction') || entry.jurisdiction || 'fi',
      certGranularity: metaValue('certification_granularity') || metaValue('granularity') || 'chapter',
    };
    applyLocale(metaInfo.lang, metaInfo.jurisdiction);

    const cd = metaValue('change_dates');
    changeDates = cd || Object.keys(checkpointByDate).sort();

    renderShell();

    if (permalink && permalink.statute === statuteId) {
      // A permalink landing in a mode that doesn't pick a date (amendments/search)
      // still needs a baseline fold so a later switch to the law view has one.
      if (permalink.mode !== 'law') await selectDate(changeDates.length - 1, { skipRender: true });
      applyPermalink(permalink);
    } else {
      await selectDate(changeDates.length - 1);
    }
  } catch (e) {
    app.innerHTML = `<p class="error-box">${escHtml(tr('loadFail'))}: ${escHtml(e.message)}</p>`;
    console.error(e);
  }
}

// =====================================================================
// Shell: sticky topbar (modes + § jump + verify) + time-axis scrubber
// =====================================================================
function renderShell() {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="topbar" id="topbar">
      <div class="topbar-row">
        <div class="mode-bar">
          <button class="mode-btn" data-mode="law">${escHtml(tr('modeLaw'))}</button>
          <button class="mode-btn" data-mode="amendments">${escHtml(tr('modeAmendments'))}</button>
          <button class="mode-btn" data-mode="search">${escHtml(tr('modeSearch'))}</button>
          <button class="mode-btn" data-mode="compare">${escHtml(tr('modeCompare'))}</button>
        </div>
        <input type="search" id="sec-jump" class="sec-jump" placeholder="${escAttr(tr('secJumpPlaceholder'))}" autocomplete="off" title="${escAttr(tr('secJumpPlaceholder'))}">
        <div id="verify-slot"><span class="verify-badge verify-pending">${escHtml(tr('verifyPending'))}</span></div>
      </div>
      <div class="scrubber" id="scrubber">
        <div class="scrubber-row">
          <div class="law"><span class="date" id="sel-date">—</span>
            <span class="validity" id="validity"></span></div>
          <div class="date-nav">
            <button id="prev-date">${escHtml(tr('prevDate'))}</button>
            <button id="next-date">${escHtml(tr('nextDate'))}</button>
            <select id="date-jump">${changeDates.map((d, i) => `<option value="${i}">${escHtml(d)}</option>`).join('')}</select>
          </div>
          <span class="date-meta" id="date-meta"></span>
        </div>
        <div class="timeaxis" id="timeaxis" title="">${timeAxisInnerHtml()}</div>
      </div>
    </div>
    <p class="mode-hint" id="mode-hint"></p>
    <div class="view" id="view"></div>`;

  for (const b of app.querySelectorAll('.mode-btn')) {
    b.addEventListener('click', () => setMode(b.dataset.mode));
  }
  document.getElementById('prev-date').addEventListener('click', () => selectDate(Math.max(0, curDateIdx - 1)));
  document.getElementById('next-date').addEventListener('click', () => selectDate(Math.min(changeDates.length - 1, curDateIdx + 1)));
  document.getElementById('date-jump').addEventListener('change', (e) => selectDate(parseInt(e.target.value, 10)));
  wireTimeAxis();
  wireSecJump();

  setMode('law', /*skipRender*/ true);
}

// ---- real time axis: ticks at change dates, proportional positions ----
function axisRange() {
  const t0 = Date.parse(changeDates[0]);
  const t1 = Date.parse(changeDates[changeDates.length - 1]);
  return { t0, t1: t1 > t0 ? t1 : t0 + 1 };
}

function timeAxisInnerHtml() {
  if (!changeDates.length) return '';
  const { t0, t1 } = axisRange();
  const frac = (d) => ((Date.parse(d) - t0) / (t1 - t0)) * 100;
  let html = '<div class="ta-line"></div>';
  // year gridlines/labels every ~5 years
  const y0 = new Date(t0).getUTCFullYear(), y1 = new Date(t1).getUTCFullYear();
  const span = Math.max(1, y1 - y0);
  const step = span > 30 ? 10 : span > 12 ? 5 : span > 5 ? 2 : 1;
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
    const f = ((Date.UTC(y, 0, 1) - t0) / (t1 - t0)) * 100;
    if (f < 0 || f > 100) continue;
    html += `<div class="ta-year" style="left:${f}%"><span>${y}</span></div>`;
  }
  for (let i = 0; i < changeDates.length; i++) {
    html += `<div class="ta-tick" data-idx="${i}" style="left:${frac(changeDates[i])}%" title="${escAttr(changeDates[i])}"></div>`;
  }
  html += `<div class="ta-cursor" id="ta-cursor" style="left:0%"></div>`;
  return html;
}

function updateAxisCursor() {
  const cur = document.getElementById('ta-cursor');
  if (!cur || curDateIdx < 0) return;
  const { t0, t1 } = axisRange();
  cur.style.left = `${((Date.parse(changeDates[curDateIdx]) - t0) / (t1 - t0)) * 100}%`;
}

function wireTimeAxis() {
  const axis = document.getElementById('timeaxis');
  if (!axis) return;
  let seekPending = false;
  const seek = (e) => {
    const r = axis.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const { t0, t1 } = axisRange();
    const target = t0 + f * (t1 - t0);
    let best = 0, bestD = Infinity;
    for (let i = 0; i < changeDates.length; i++) {
      const d = Math.abs(Date.parse(changeDates[i]) - target);
      if (d < bestD) { bestD = d; best = i; }
    }
    if (best !== curDateIdx && !seekPending) {
      seekPending = true;
      selectDate(best).finally(() => { seekPending = false; });
    }
  };
  axis.addEventListener('pointerdown', (e) => {
    axis.setPointerCapture(e.pointerId);
    seek(e);
    const move = (ev) => seek(ev);
    const up = () => {
      axis.removeEventListener('pointermove', move);
      axis.removeEventListener('pointerup', up);
      axis.removeEventListener('pointercancel', up);
    };
    axis.addEventListener('pointermove', move);
    axis.addEventListener('pointerup', up);
    axis.addEventListener('pointercancel', up);
  });
}

// ---- § quick jump (topbar) ----
function wireSecJump() {
  const inp = document.getElementById('sec-jump');
  if (!inp) return;
  inp.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const raw = inp.value.trim().toLowerCase().replace(/[§.\s]/g, '');
    if (!raw) return;
    if (mode !== 'law') setMode('law');
    const el = document.querySelector(`#doc .node[data-addr$="section:${cssEsc(raw)}"]`)
      || document.querySelector(`#doc [data-addr$="section:${cssEsc(raw)}"]`);
    if (el) { jumpToAddr(el.dataset.addr); inp.select(); return; }
    // Not in force on the selected date (e.g. repealed, or not yet enacted).
    // The history knows it — time-travel to the last date it existed.
    const suffix = `section:${raw}`;
    const known = [...changeIndex().keys()].find(a => a.endsWith(suffix) || a.includes(suffix + '/'));
    if (known) {
      const target = known.slice(0, known.indexOf(suffix) + suffix.length);
      const folds = allFolds();
      for (let i = changeDates.length - 1; i >= 0; i--) {
        if (nodeAtAddress(folds[changeDates[i]].live, target)) {
          selectDate(i).then(() => setTimeout(() => jumpToAddr(target), 60));
          inp.select();
          return;
        }
      }
    }
    inp.classList.add('nf');
    setTimeout(() => inp.classList.remove('nf'), 700);
  });
}

function setMode(m, skipRender) {
  mode = m;
  for (const b of document.querySelectorAll('.mode-btn')) {
    b.classList.toggle('active', b.dataset.mode === m);
  }
  const scrubber = document.getElementById('scrubber');
  const hint = document.getElementById('mode-hint');
  scrubber.style.display = (m === 'law') ? '' : 'none';
  const hints = { law: 'hintLaw', amendments: 'hintAmendments', search: 'hintSearch', compare: 'hintCompare' };
  hint.textContent = tr(hints[m] || 'hintLaw');
  if (!skipRender) {
    if (m === 'law') renderLaw();
    else if (m === 'amendments') renderAmendments();
    else if (m === 'search') renderSearch();
    else renderCompare();
  }
  if (!suppressHashUpdate) updateHash();
}

// =====================================================================
// Date selection (Oikeustila)
// =====================================================================
function validityInterval(idx) {
  const start = changeDates[idx];
  const next = changeDates[idx + 1];
  return { start, end: next || null };
}

async function selectDate(idx, opts) {
  curDateIdx = idx;
  const date = changeDates[idx];
  const selDateEl = document.getElementById('sel-date');
  if (selDateEl) selDateEl.textContent = date;
  const jump = document.getElementById('date-jump');
  if (jump) jump.value = idx;
  updateAxisCursor();

  const vi = validityInterval(idx);
  const vEl = document.getElementById('validity');
  if (vEl) vEl.textContent = `${tr('inForce')} ${vi.start}–${vi.end || '—'}`;

  let live, tombstoned, failures;
  try {
    ({ live, tombstoned, failures } = foldAt(date));
  } catch (e) {
    const slot = document.getElementById('verify-slot');
    if (slot) slot.innerHTML = `<span class="verify-badge verify-fail">${escHtml(tr('foldFail'))}</span>`;
    const view = document.getElementById('view');
    if (view) view.innerHTML = `<p class="error-box">${escHtml(e.message)}</p>`;
    console.error('FOLD FAIL', date, e);
    return;
  }
  curLive = live;
  curTombstoned = tombstoned;
  prevLive = idx > 0 ? foldAt(changeDates[idx - 1]).live : new Map();
  changedAddrs = new Set();
  for (const [addr, h] of live) {
    if (prevLive.get(addr) !== h) changedAddrs.add(addr);
  }
  for (const addr of prevLive.keys()) {
    if (!live.has(addr)) changedAddrs.add(addr);
  }

  const cp = checkpointByDate[date];
  const got = await reproducibleTreeHash(live);
  curTreeHash = got;
  const slot = document.getElementById('verify-slot');
  const expected = cp ? cp.tree_hash : null;
  if (slot) {
    if (expected && got === expected && failures.length === 0) {
      slot.innerHTML = verifyBadgeHtml(got);
    } else {
      const reason = failures.length ? tr('verifyFailPre', failures.length) : tr('verifyFailHash');
      slot.innerHTML = `<span class="verify-badge verify-fail" title="${escAttr(reason)}">${escHtml(tr('verifyFail'))} — ${escHtml(reason)}</span>`
        + `<span class="verify-hash">${escHtml(got.slice(0, 12))}… vs ${escHtml((expected || '—').slice(0, 12))}…</span>`;
      console.warn('VERIFY FAIL', date, { got, expected, failures });
    }
  }

  const meta = document.getElementById('date-meta');
  const changedCount = [...changedAddrs].filter(a => live.has(a)).length;
  if (meta) {
    meta.textContent = `${tr('topUnits', live.size)} · `
      + (idx === 0 ? tr('originalAct') : tr('changedToday', changedCount))
      + ` · ${tr('changeDayOf', idx + 1, changeDates.length)}`;
  }

  if (mode === 'law' && !(opts && opts.skipRender)) renderLaw({ preserveScroll: true });
  if (!suppressHashUpdate) updateHash();
}

function verifyBadgeHtml(treeHash) {
  const tip = tr('verifyTip');
  return `<span class="verify-badge verify-ok" title="${escAttr(tip)}">${escHtml(tr('verifyOk'))}</span>`
    + `<span class="verify-info" tabindex="0" role="button" aria-label="${escAttr(tr('verifyInfoAria'))}" title="${escAttr(tip)}">ⓘ</span>`
    + `<span class="verify-hash">tree ${escHtml(treeHash.slice(0, 12))}…</span>`;
}

// =====================================================================
// Node helpers (granularity-agnostic; addresses from labels, never position)
// =====================================================================
const ADDR_SEG = {
  part: 'part', chapter: 'chapter', section: 'section', subsection: 'subsection',
  paragraph: 'paragraph', subparagraph: 'subparagraph',
};
// Kinds rendered as collapsible outline rows (everything deeper is prose).
const ROW_KINDS = new Set(['part', 'chapter', 'section']);

function childByKind(node, kind) {
  return (node.children || []).find(c => c.kind === kind) || null;
}
function nodeNum(node) {
  const n = childByKind(node, 'num');
  return n && n.text ? n.text.trim() : '';
}
function nodeHeading(node) {
  const h = childByKind(node, 'heading');
  return h && h.text ? h.text.trim() : '';
}

// REAL address component for a structural node: derive from the node's own
// label/num — NEVER from positional position among siblings.
function addrComponent(node, ordinal) {
  const lbl = node.label != null ? String(node.label).trim() : '';
  if (lbl) return lbl.replace(/\s+/g, '');
  const num = nodeNum(node);
  if (num) {
    const cleaned = num.replace(/[§).]/g, '').replace(/luku/gi, '').trim().replace(/\s+/g, '');
    if (cleaned) return cleaned;
  }
  return String(ordinal);
}

function kindLabel(node, ordinal) {
  return J.kindLabel(node.kind, nodeNum(node), (node.label || '').toString().trim(), ordinal);
}

function structChildren(node, addr) {
  const out = [];
  const counts = {};
  for (const c of (node.children || [])) {
    const seg = ADDR_SEG[c.kind];
    if (!seg) continue;
    counts[c.kind] = (counts[c.kind] || 0) + 1;
    const comp = addrComponent(c, counts[c.kind]);
    out.push({ child: c, ordinal: counts[c.kind], childAddr: `${addr}/${seg}:${comp}` });
  }
  return out;
}

function inlineContent(node) {
  const out = [];
  for (const c of (node.children || [])) {
    if (ADDR_SEG[c.kind]) continue;
    if (c.kind === 'num' || c.kind === 'heading') continue;
    const txt = (c.text || '').trim();
    if (txt) out.push({ kind: c.kind, text: txt });
  }
  if (node.text && node.text.trim()) out.unshift({ kind: node.kind, text: node.text.trim() });
  return out;
}

function subtreeFingerprint(node) {
  if (!node) return '';
  const parts = [];
  (function walk(n) {
    parts.push(n.kind || '', '|', (n.label || ''), '|', (n.text || '').trim(), '\n');
    for (const c of (n.children || [])) walk(c);
  })(node);
  return parts.join('');
}

function nodeToText(node) {
  if (!node) return '';
  const parts = [];
  (function walk(n) {
    const num = (n.kind === 'num' && n.text) ? n.text.trim() : '';
    if (num) parts.push(num);
    if (n.text && n.text.trim() && n.kind !== 'num') parts.push(n.text.trim());
    for (const c of (n.children || [])) walk(c);
  })(node);
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

function prettyAddr(addr) {
  return addr.split('/').map(seg => {
    const [k, n] = seg.split(':');
    return J.addrSeg(k, n);
  }).join(' › ');
}

function addrCompare(a, b) {
  const sa = a.split('/'), sb = b.split('/');
  for (let i = 0; i < Math.max(sa.length, sb.length); i++) {
    const ca = (sa[i] || '').split(':')[1] || '';
    const cb = (sb[i] || '').split(':')[1] || '';
    const na = parseInt(ca, 10), nb = parseInt(cb, 10);
    if (na !== nb) return (isNaN(na) ? 0 : na) - (isNaN(nb) ? 0 : nb);
    if (ca !== cb) return ca < cb ? -1 : 1;
  }
  return 0;
}

// Resolve the node at `addr` from a covering set, in either direction:
//  * a covering key equals addr → that blob;
//  * a covering key is an ANCESTOR of addr (coarse certification) → walk down
//    inside the blob via label-derived child addresses;
//  * covering keys are DESCENDANTS of addr (fine certification) → synthesize a
//    container node holding the units under the prefix, in address order.
function nodeAtAddress(live, addr) {
  if (live.has(addr)) return getBlob(live.get(addr));
  const segs = addr.split('/');
  for (let i = segs.length - 1; i >= 1; i--) {
    const anc = segs.slice(0, i).join('/');
    if (!live.has(anc)) continue;
    let node = getBlob(live.get(anc));
    for (let j = i; j < segs.length && node; j++) {
      const base = segs.slice(0, j).join('/');
      const want = segs.slice(0, j + 1).join('/');
      const hit = structChildren(node, base).find(k => k.childAddr === want);
      node = hit ? hit.child : null;
    }
    if (node) return node;
  }
  const subKeys = [...live.keys()].filter(k => k.startsWith(addr + '/')).sort(addrCompare);
  if (subKeys.length) {
    const lastSeg = segs[segs.length - 1].split(':');
    return {
      kind: lastSeg[0], label: lastSeg[1] || '',
      children: subKeys.map(k => getBlob(live.get(k))).filter(Boolean),
    };
  }
  return null;
}

// =====================================================================
// Oikeustila: reading document + TOC + inline history
// =====================================================================
function renderLaw(opts) {
  const view = document.getElementById('view');
  if (!view) return;
  const anchor = (opts && opts.preserveScroll) ? captureScrollAnchor() : null;
  view.innerHTML = `
    <div class="layout2">
      <aside class="col-toc panel" id="toc-panel">
        <h2 class="panel-title">${escHtml(tr('toc'))}</h2>
        <input type="search" id="toc-filter" class="toc-filter" placeholder="${escAttr(tr('tocFilter'))}" autocomplete="off">
        <nav class="toc" id="toc"></nav>
      </aside>
      <section class="col-main panel">
        <div class="panel-head">
          <h2 class="doc-title">${escHtml(metaInfo.title)}</h2>
          <div class="tree-tools">
            <button id="expand-all">${escHtml(tr('expandAll'))}</button>
            <button id="collapse-all">${escHtml(tr('collapseAll'))}</button>
          </div>
        </div>
        <div class="tree-legend">
          <span class="leg-changed">▍</span> ${escHtml(tr('legendChanged'))}
          <span class="leg-tomb">${escHtml(tr('tombstone'))}</span> ${escHtml(tr('legendTomb'))}
          ${evidenceEvents.length ? `<span class="leg-evidence">!</span> ${escHtml(tr('legendEvidence'))}` : ''}
        </div>
        <div class="doc" id="doc"></div>
      </section>
    </div>`;
  document.getElementById('expand-all').addEventListener('click', () => setAllCollapsed(false));
  document.getElementById('collapse-all').addEventListener('click', () => setAllCollapsed(true));
  const tf = document.getElementById('toc-filter');
  tf.addEventListener('input', () => filterToc(tf.value));
  tf.addEventListener('keydown', (e) => { if (e.key === 'Enter') jumpFirstTocMatch(); });
  renderDoc();
  buildToc();
  setupScrollSpy();
  if (selectedAddress) openInlineHistory(selectedAddress, /*scroll*/ false);
  reapplySearchHighlight();
  if (anchor) restoreScrollAnchor(anchor);
}

// Virtual render tree: covering units inserted at their address paths; missing
// ancestors become scaffold entries (rendered from the address alone). With a
// chapter-grained export the roots ARE full chapter blobs; with finer exports
// the scaffold rows keep the document structure navigable.
function buildRenderTree(live, tombstoned) {
  const root = new Map(); // seg -> {addr, hash|null, tomb|null, children:Map}
  const insert = (addr, hash, tomb) => {
    const segs = addr.split('/');
    let map = root, path = '';
    for (let i = 0; i < segs.length; i++) {
      path = path ? `${path}/${segs[i]}` : segs[i];
      let e = map.get(segs[i]);
      if (!e) { e = { addr: path, hash: null, tomb: null, children: new Map() }; map.set(segs[i], e); }
      if (i === segs.length - 1) { if (hash) e.hash = hash; if (tomb) e.tomb = tomb; }
      map = e.children;
    }
  };
  for (const [addr, hash] of live) insert(addr, hash, null);
  for (const [addr, info] of tombstoned) { if (!live.has(addr)) insert(addr, null, info); }
  return root;
}

function sortedEntries(map) {
  return [...map.values()].sort((a, b) => addrCompare(a.addr, b.addr));
}

function renderDoc() {
  const docEl = document.getElementById('doc');
  if (!docEl) return;
  const tree = buildRenderTree(curLive, curTombstoned);
  let html = '';
  for (const entry of sortedEntries(tree)) html += renderTreeEntry(entry, 0);
  docEl.innerHTML = html || `<p class="muted-empty">${escHtml(tr('noProvisions'))}</p>`;
  wireDoc(docEl);
}

function renderTreeEntry(entry, depth) {
  if (entry.tomb && !entry.hash) return tombstoneHtml(entry.addr, entry.tomb);
  if (entry.hash) {
    const node = getBlob(entry.hash);
    if (!node) return '';
    const prevMap = prevNodeMap(entry.addr);
    return renderNode(node, entry.addr, depth, prevMap);
  }
  // Scaffold ancestor (no blob at this address — finer-grained export).
  const [k, n] = entry.addr.split('/').pop().split(':');
  const display = displayNodeFor(entry.addr, changeDates[curDateIdx]);
  const displayKind = display && display.kind ? display.kind : k;
  const label = display
    ? J.kindLabel(displayKind, display.num || '', display.label || n, parseInt(n || '0', 10) || 0)
    : J.addrSeg(k, n);
  const heading = display && display.heading ? display.heading : '';
  const changed = [...entry.children.values()].some(c => changedAddrs.has(c.addr));
  const collapsed = collapsedAddrs.has(entry.addr);
  let html = `<div class="node scaffold kind-${escAttr(displayKind)}${changed ? ' changed' : ''}${collapsed ? ' collapsed' : ''}" data-depth="${depth}" data-addr="${escAttr(entry.addr)}">`;
  html += rowHtml(entry.addr, label, heading, changed, true, collapsed);
  html += `<div class="node-body"${collapsed ? ' hidden="until-found"' : ''}>`;
  for (const child of sortedEntries(entry.children)) html += renderTreeEntry(child, depth + 1);
  html += `</div></div>`;
  return html;
}

// Per-root map of address -> node at the PREVIOUS change date (change marking).
const prevMapCache = new Map();
function prevNodeMap(rootAddr) {
  if (prevMapCache.has(rootAddr) && prevMapCache.get(rootAddr).dateIdx === curDateIdx) {
    return prevMapCache.get(rootAddr).map;
  }
  const map = new Map();
  const node = prevLive.has(rootAddr) ? getBlob(prevLive.get(rootAddr)) : null;
  if (node) {
    (function index(n, a) {
      map.set(a, n);
      for (const { child, childAddr } of structChildren(n, a)) index(child, childAddr);
    })(node, rootAddr);
  }
  prevMapCache.set(rootAddr, { dateIdx: curDateIdx, map });
  return map;
}

function rowHtml(addr, label, heading, changed, collapsible, collapsed) {
  const kind = (addr.split('/').pop() || '').split(':')[0];
  let html = `<div class="node-row${collapsible ? ' clk' : ''} spyable" data-addr="${escAttr(addr)}">`;
  html += `<span class="node-toggle${collapsible ? '' : ' leaf'}">${collapsible ? (collapsed ? '▸' : '▾') : ''}</span>`;
  html += `<span class="node-label">${escHtml(label)}</span>`;
  if (heading) html += `<span class="node-heading">${escHtml(heading)}</span>`;
  if (changed) html += `<span class="changed-tag">${escHtml(tr('changedTag'))}</span>`;
  // Lifecycle badge cascades to every outline level (part/chapter/section):
  // an ancestor's strip aggregates all change activity beneath it.
  if (ROW_KINDS.has(kind)) html += changeBadgeHtml(addr);
  html += evidenceBadgeHtml(addr);
  html += historyBtnHtml(addr);
  html += `</div>`;
  return html;
}

// History affordance. With showCount (prose blocks, ghosts): a block that has
// EVER changed gets a persistently visible "⌚ N" chip — the interesting ones
// announce themselves; an unchanged block keeps the quiet hover-only button
// whose tooltip says it has been unchanged since the original act.
function historyBtnHtml(addr, showCount) {
  if (showCount) {
    const events = changeIndex().get(addr) || [];
    const n = events.filter(e => e.idx > 0).length;
    if (n > 0) {
      return `<button class="hist-btn has-hist" data-addr="${escAttr(addr)}" title="${escAttr(tr('historyBtnTipN', n))}">⌚ ${n}</button>`;
    }
    return `<button class="hist-btn" data-addr="${escAttr(addr)}" title="${escAttr(tr('historyBtnTipNone'))}">⌚ ${escHtml(tr('historyBtn'))}</button>`;
  }
  return `<button class="hist-btn" data-addr="${escAttr(addr)}" title="${escAttr(tr('historyBtnTip'))}">⌚ ${escHtml(tr('historyBtn'))}</button>`;
}

function renderNode(node, addr, depth, prevMap) {
  const kind = node.kind;
  const heading = nodeHeading(node);
  const children = structChildren(node, addr);
  const inline = inlineContent(node);

  const prevNode = prevMap.get(addr);
  let changed = false;
  if (curDateIdx > 0) {
    changed = !prevNode ? true : subtreeFingerprint(node) !== subtreeFingerprint(prevNode);
  }

  const ordinal = parseInt((addr.split('/').pop() || '').split(':')[1] || '0', 10);
  const label = kindLabel(node, ordinal);

  if (ROW_KINDS.has(kind)) {
    // Outline row: chapter/section — collapsible, default expanded (reading
    // mode); collapse state is remembered across date scrubs and re-renders.
    const collapsed = collapsedAddrs.has(addr);
    let html = `<div class="node kind-${kind}${changed ? ' changed' : ''}${collapsed ? ' collapsed' : ''}" data-depth="${depth}" data-addr="${escAttr(addr)}">`;
    html += rowHtml(addr, label, heading, changed, true, collapsed);
    html += `<div class="node-body"${collapsed ? ' hidden="until-found"' : ''}>`;
    html += orderedBodyHtml(addr, node, depth, prevMap);
    html += `</div></div>`;
    return html;
  }

  // Prose block: subsection (momentti) / paragraph (kohta) / subparagraph —
  // rendered as readable statute text, addressable + history-hoverable. Class
  // mirrors the outline-node `kind-${kind}` pattern (jurisdiction-neutral).
  const hasEvidence = evidenceRelatedToAddress(addr).length > 0;
  let html = `<div class="pblock kind-${kind}${changed ? ' changed' : ''}${hasEvidence ? ' has-evidence' : ''}" data-addr="${escAttr(addr)}">`;
  // Block-level inner wrapper caps the reading measure; the label and text
  // flow inline within it (an inline-block text body would wrap to its own
  // line whenever the measure doesn't fit beside the label).
  html += `<div class="pblock-inner">`;
  html += `<span class="pblock-num" title="${escAttr(prettyAddr(addr))}">${escHtml(label)}</span>`;
  html += `<span class="pblock-body">`;
  for (let i = 0; i < inline.length; i++) {
    const seg = inline[i];
    html += `<span class="pblock-text">${renderTextWithInterlinks(addr, i, seg.text)} </span>`;
  }
  html += `</span></div>`;
  html += evidenceBadgeHtml(addr);
  html += historyBtnHtml(addr, /*showCount*/ true);
  const kidsHtml = childrenWithGhostsHtml(addr, children, depth, prevMap);
  if (kidsHtml) html += `<div class="pblock-children">${kidsHtml}</div>`;
  html += `</div>`;
  return html;
}

// Children in document order with derived ghost tombstones interleaved at
// their original positions (repealed/expired units never silently vanish).
function childrenWithGhostsHtml(addr, children, depth, prevMap) {
  const ghosts = ghostMap().get(addr) || [];
  const items = [
    ...children.map(c => ({ sort: c.childAddr, render: () => renderNode(c.child, c.childAddr, depth + 1, prevMap) })),
    ...ghosts.map(g => ({ sort: g.addr, render: () => ghostHtml(g) })),
  ].sort((a, b) => addrCompare(a.sort, b.sort));
  let html = '';
  for (const it of items) html += it.render();
  return html;
}

// Body of an outline node in TRUE DOCUMENT ORDER: inline text (väliotsikko
// crossheadings, intro/wrapup prose) interleaved with structural children as
// they appear in the source — never "all headings first, then all sections".
// Ghost tombstones slot in by address order within the structural sequence.
function orderedBodyHtml(addr, node, depth, prevMap) {
  const counts = {};
  const items = [];
  if (node.text && node.text.trim()) items.push({ type: 'text', kind: node.kind, text: node.text.trim() });
  for (const c of (node.children || [])) {
    const seg = ADDR_SEG[c.kind];
    if (seg) {
      counts[c.kind] = (counts[c.kind] || 0) + 1;
      items.push({ type: 'child', child: c, childAddr: `${addr}/${seg}:${addrComponent(c, counts[c.kind])}` });
    } else if (c.kind === 'num' || c.kind === 'heading') {
      continue; // rendered in the row label
    } else if ((c.text || '').trim()) {
      items.push({ type: 'text', kind: c.kind, text: c.text.trim() });
    }
  }
  const ghosts = [...(ghostMap().get(addr) || [])].sort((a, b) => addrCompare(a.addr, b.addr));
  let gi = 0;
  let html = '';
  let textSegmentIndex = 0;
  const flushGhostsBefore = (childAddr) => {
    while (gi < ghosts.length && (childAddr === null || addrCompare(ghosts[gi].addr, childAddr) < 0)) {
      html += ghostHtml(ghosts[gi++]);
    }
  };
  for (const it of items) {
    if (it.type === 'text') {
      const cls = it.kind === 'crossHeading' ? 'crossheading'
        : it.kind === 'intro' ? 'intro'
        : it.kind === 'wrapUp' ? 'wrapup' : 'content';
      html += `<p class="prov-text ${cls}">${renderTextWithInterlinks(addr, textSegmentIndex++, it.text)}</p>`;
    } else {
      flushGhostsBefore(it.childAddr);
      html += renderNode(it.child, it.childAddr, depth + 1, prevMap);
    }
  }
  flushGhostsBefore(null);
  return html;
}

function tombstoneHtml(addr, info) {
  const src = info && info.source_id ? sourceById[info.source_id] : null;
  const srcLabel = src ? (src.canonical_id || src.title || info.source_id) : (info ? info.source_id : '');
  return `<div class="node tombstone" data-addr="${escAttr(addr)}">`
    + `<div class="node-row spyable" data-addr="${escAttr(addr)}">`
    + `<span class="node-toggle leaf"></span>`
    + `<span class="tomb-label">${escHtml(prettyAddr(addr))} <em>${escHtml(tr('tombstone'))}</em></span>`
    + (info && info.date ? `<span class="tomb-meta">${refLink('date', { date: info.date, addr })}`
        + (info.source_id ? ' · ' + refLink('source', { id: info.source_id }, srcLabel) : '') + `</span>` : '')
    + evidenceBadgeHtml(addr)
    + historyBtnHtml(addr)
    + `</div></div>`;
}

function wireDoc(docEl) {
  // Row click anywhere toggles collapse (the outline gesture). History is the
  // explicit ⌚ button — reading/selection gestures stay free for text.
  docEl.querySelectorAll('.node-row.clk').forEach(r => {
    r.addEventListener('click', (e) => {
      if (e.target.closest('.hist-btn') || e.target.closest('.chg-badge') || e.target.closest('.evidence-badge') || e.target.closest('a.ref-link')) return;
      toggleCollapse(r.closest('.node'));
    });
  });
  docEl.querySelectorAll('.hist-btn, .chg-badge, .evidence-badge').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleInlineHistory(b.dataset.addr);
    });
  });
}

// Collapse state survives re-renders (date scrubs, language switches).
const collapsedAddrs = new Set();

function toggleCollapse(nodeEl, force) {
  if (!nodeEl) return;
  const collapsing = force !== undefined ? force : !nodeEl.classList.contains('collapsed');
  nodeEl.classList.toggle('collapsed', collapsing);
  const addr = nodeEl.dataset.addr;
  if (addr) { if (collapsing) collapsedAddrs.add(addr); else collapsedAddrs.delete(addr); }
  const tog = nodeEl.querySelector(':scope > .node-row > .node-toggle');
  if (tog && !tog.classList.contains('leaf')) tog.textContent = collapsing ? '▸' : '▾';
  const body = nodeEl.querySelector(':scope > .node-body');
  if (body) {
    // hidden="until-found" keeps Ctrl-F able to reveal matches inside.
    if (collapsing) body.setAttribute('hidden', 'until-found');
    else body.removeAttribute('hidden');
  }
}

function setAllCollapsed(collapsed) {
  document.querySelectorAll('#doc .node').forEach(n => {
    if (n.querySelector(':scope > .node-body')) toggleCollapse(n, collapsed);
  });
}

// Find-in-page reveal of hidden="until-found" content: expand ancestors.
document.addEventListener('beforematch', (e) => {
  let el = e.target;
  while (el && el !== document.body) {
    if (el.classList && el.classList.contains('node') && el.classList.contains('collapsed')) {
      toggleCollapse(el, false);
    }
    if (el.hasAttribute && el.hasAttribute('hidden')) el.removeAttribute('hidden');
    el = el.parentElement;
  }
});

// =====================================================================
// TOC + scroll-spy (left minimap follows main-pane scroll)
// =====================================================================
function buildToc() {
  const tocEl = document.getElementById('toc');
  if (!tocEl) return;
  const tree = buildRenderTree(curLive, curTombstoned);
  let html = '<ul class="toc-list">';
  for (const entry of sortedEntries(tree)) {
    const node = entry.hash ? getBlob(entry.hash) : null;
    const chapterDisplay = displayLabelHeading(entry.addr, node);
    const chLabel = chapterDisplay.label;
    const chHeading = chapterDisplay.heading;
    const chChanged = changedAddrs.has(entry.addr);
    html += `<li class="toc-chapter">`
      + `<a href="#" class="toc-link toc-ch${chChanged ? ' ch-changed' : ''}" data-addr="${escAttr(entry.addr)}">`
      + `<span class="toc-num">${escHtml(chLabel)}</span> <span class="toc-h">${escHtml(chHeading)}</span></a>`;
    const secs = node
      ? structChildren(node, entry.addr).filter(s => s.child.kind === 'section')
      : sortedEntries(entry.children).map(c => ({ child: null, childAddr: c.addr }))
          .filter(c => c.childAddr.includes('section:'));
    if (secs.length) {
      html += '<ul class="toc-sections">';
      for (const { child, childAddr } of secs) {
        const sectionDisplay = displayLabelHeading(childAddr, child);
        const sLabel = sectionDisplay.label;
        const sHeading = sectionDisplay.heading;
        html += `<li><a href="#" class="toc-link toc-sec" data-addr="${escAttr(childAddr)}" `
          + `data-search="${escAttr((sLabel + ' ' + sHeading).toLowerCase())}">`
          + `<span class="toc-num">${escHtml(sLabel)}</span> <span class="toc-h">${escHtml(sHeading)}</span></a></li>`;
      }
      html += '</ul>';
    }
    html += '</li>';
  }
  html += '</ul>';
  tocEl.innerHTML = html;
  tocEl.querySelectorAll('.toc-link').forEach(a => {
    a.addEventListener('click', (e) => { e.preventDefault(); jumpToAddr(a.dataset.addr); });
  });
  const panel = document.getElementById('toc-panel');
  if (panel && !panel.dataset.hoverWired) {
    panel.dataset.hoverWired = '1';
    panel.addEventListener('mouseenter', () => { spy.hover = true; });
    panel.addEventListener('mouseleave', () => { spy.hover = false; });
  }
}

const spy = { observer: null, visible: new Set(), current: null, hover: false, suppressUntil: 0 };

function setupScrollSpy() {
  if (spy.observer) spy.observer.disconnect();
  spy.visible = new Set();
  spy.current = null;
  spy.observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) spy.visible.add(e.target);
      else spy.visible.delete(e.target);
    }
    updateSpyCurrent();
  }, { rootMargin: '-120px 0px -55% 0px', threshold: 0 });
  document.querySelectorAll('#doc .node-row.spyable').forEach(r => spy.observer.observe(r));
}

function updateSpyCurrent() {
  let best = null, bestTop = Infinity;
  for (const el of spy.visible) {
    const t = el.getBoundingClientRect().top;
    if (t < bestTop) { bestTop = t; best = el; }
  }
  if (!best) {
    // No row inside the observer band — the reader is inside a long section
    // (rows are sparser than the band) or at initial load. The current unit
    // is the LAST row above the reading line.
    let lastAbove = null;
    for (const r of document.querySelectorAll('#doc .node-row.spyable')) {
      if (r.getBoundingClientRect().top < 140) lastAbove = r;
      else { if (!lastAbove) lastAbove = r; break; } // before the first row: take it
    }
    best = lastAbove;
  }
  if (!best) return;
  const addr = best.dataset.addr;
  if (addr === spy.current) return;
  spy.current = addr;
  document.querySelectorAll('#toc .toc-link.current').forEach(x => x.classList.remove('current'));
  let link = document.querySelector(`#toc .toc-link[data-addr="${cssEsc(addr)}"]`);
  if (!link) { // deeper than TOC granularity → mark nearest TOC ancestor
    const segs = addr.split('/');
    for (let i = segs.length - 1; i >= 1 && !link; i--) {
      link = document.querySelector(`#toc .toc-link[data-addr="${cssEsc(segs.slice(0, i).join('/'))}"]`);
    }
  }
  if (link) {
    link.classList.add('current');
    if (!spy.hover && Date.now() > spy.suppressUntil) {
      link.scrollIntoView({ block: 'nearest' });
    }
  }
}

// Pixel-stable scroll anchoring across re-renders (date scrubs): anchor on the
// topmost visible addressed element and restore its exact viewport offset, so
// the text the reader is looking at does not move even when content above it
// is added or removed.
function captureScrollAnchor() {
  const doc = document.getElementById('doc');
  if (!doc) return null;
  const r = doc.getBoundingClientRect();
  const topbar = document.getElementById('topbar');
  const y = Math.max((topbar ? topbar.getBoundingClientRect().bottom : 0) + 10, r.top + 1);
  if (y > r.bottom) return null; // document entirely above the fold line
  const x = r.left + Math.min(r.width / 2, 320);
  let el = document.elementFromPoint(x, y);
  el = el && el.closest ? el.closest('#doc [data-addr]') : null;
  if (!el) return null;
  return { addr: el.dataset.addr, top: el.getBoundingClientRect().top };
}

function restoreScrollAnchor(anchor) {
  if (!anchor) return;
  let el = document.querySelector(`#doc [data-addr="${cssEsc(anchor.addr)}"]`);
  if (!el) { // the anchored unit no longer exists at this date — nearest ancestor
    const segs = anchor.addr.split('/');
    for (let i = segs.length - 1; i >= 1 && !el; i--) {
      el = document.querySelector(`#doc [data-addr="${cssEsc(segs.slice(0, i).join('/'))}"]`);
    }
  }
  if (!el) return;
  const delta = el.getBoundingClientRect().top - anchor.top;
  if (delta) window.scrollBy(0, delta);
  spy.suppressUntil = Date.now() + 600;
}

function jumpToAddr(addr) {
  const segs = addr.split('/');
  for (let i = 1; i <= segs.length; i++) {
    const a = segs.slice(0, i).join('/');
    const n = document.querySelector(`#doc .node[data-addr="${cssEsc(a)}"]`);
    if (n && n.classList.contains('collapsed')) toggleCollapse(n, false);
  }
  const row = document.querySelector(`#doc .node-row[data-addr="${cssEsc(addr)}"]`)
    || document.querySelector(`#doc [data-addr="${cssEsc(addr)}"]`);
  if (row) {
    spy.suppressUntil = Date.now() + 1500;
    // Smooth-scroll reads nicely nearby; over long distances (this statute is
    // huge) it is slow and disorienting — jump instantly instead.
    const dist = Math.abs(row.getBoundingClientRect().top);
    row.scrollIntoView({ behavior: dist > 2500 ? 'auto' : 'smooth', block: 'start' });
    row.classList.add('flash');
    setTimeout(() => row.classList.remove('flash'), 1200);
  }
}

function filterToc(qstr) {
  const norm = qstr.trim().toLowerCase();
  document.querySelectorAll('#toc .toc-sections li').forEach(li => {
    const a = li.querySelector('.toc-sec');
    const hay = a ? (a.dataset.search || '') : '';
    li.style.display = (!norm || hay.includes(norm)) ? '' : 'none';
  });
  document.querySelectorAll('#toc .toc-chapter').forEach(ch => {
    const chLink = ch.querySelector('.toc-ch');
    const chTxt = chLink ? chLink.textContent.toLowerCase() : '';
    const anySec = [...ch.querySelectorAll('.toc-sections li')].some(li => li.style.display !== 'none');
    ch.style.display = (!norm || chTxt.includes(norm) || anySec) ? '' : 'none';
  });
}
function jumpFirstTocMatch() {
  const first = [...document.querySelectorAll('#toc .toc-sec, #toc .toc-ch')]
    .find(a => a.offsetParent !== null);
  if (first) jumpToAddr(first.dataset.addr);
}

// =====================================================================
// Derived per-provision version trail (the localization layer)
// =====================================================================
// For ANY addressable node — independent of the certification granularity —
// walk all change dates, extract the node from the certified fold, and group
// consecutive dates with identical subtree fingerprints into versions. This is
// DERIVED localization over certified states (labelled as such in the UI when
// the certification granularity is coarser than the requested address).
function versionTrail(addr) {
  const folds = allFolds();
  const versions = [];
  let prevFp = null;
  for (let i = 0; i < changeDates.length; i++) {
    const d = changeDates[i];
    const node = nodeAtAddress(folds[d].live, addr);
    const fp = node ? subtreeFingerprint(node) : '';
    if (i === 0 || fp !== prevFp) {
      versions.push({ startIdx: i, endIdx: i, node, fp, present: !!node });
    } else {
      versions[versions.length - 1].endIdx = i;
    }
    prevFp = fp;
  }
  // Drop a leading "absent" pseudo-version (provision not yet enacted).
  while (versions.length && !versions[0].present && versions.length > 1) versions.shift();
  return versions;
}

// ---- per-provision change index (derived, computed once per statute) ----
// addr -> ordered events [{idx, kind}] (kind: added | changed | removed) over
// the change dates. Computed by walking consecutive certified folds and
// recursively comparing the addressable nodes of each changed covering unit
// (plus the initial covering set at the base date). Powers the change badges,
// the per-provision lifecycle strips, and the derived ghost tombstones.
function changeIndex() {
  if (changeIdxCache) return changeIdxCache;
  changeIdxCache = new Map();
  const folds = allFolds();
  const push = (addr, idx, kind) => {
    let l = changeIdxCache.get(addr);
    if (!l) { l = []; changeIdxCache.set(addr, l); }
    const last = l[l.length - 1];
    if (last && last.idx === idx) {
      if (kind !== 'changed') last.kind = kind; // added/removed dominate
      return;
    }
    l.push({ idx, kind });
  };
  const markAllAddressable = (node, addr, idx, kind) => {
    push(addr, idx, kind);
    for (const { child, childAddr } of structChildren(node, addr)) {
      markAllAddressable(child, childAddr, idx, kind);
    }
  };
  const compare = (addr, nA, nB, idx) => {
    if (!nA && !nB) return;
    if (!nA) { markAllAddressable(nB, addr, idx, 'added'); return; }
    if (!nB) { markAllAddressable(nA, addr, idx, 'removed'); return; }
    if (subtreeFingerprint(nA) === subtreeFingerprint(nB)) return;
    push(addr, idx, 'changed');
    const kidsA = new Map(structChildren(nA, addr).map(k => [k.childAddr, k.child]));
    const kidsB = new Map(structChildren(nB, addr).map(k => [k.childAddr, k.child]));
    for (const ca of new Set([...kidsA.keys(), ...kidsB.keys()])) {
      compare(ca, kidsA.get(ca) || null, kidsB.get(ca) || null, idx);
    }
  };
  // Initial presence at the base date (events at idx 0 are excluded from
  // change counts — the original act is a baseline, not an amendment).
  const live0 = folds[changeDates[0]].live;
  for (const [key, h] of live0) {
    const n = getBlob(h);
    if (n) markAllAddressable(n, key, 0, 'added');
  }
  for (let i = 1; i < changeDates.length; i++) {
    const a = folds[changeDates[i - 1]].live;
    const b = folds[changeDates[i]].live;
    for (const key of new Set([...a.keys(), ...b.keys()])) {
      if (a.get(key) === b.get(key)) continue;
      compare(key,
        a.has(key) ? getBlob(a.get(key)) : null,
        b.has(key) ? getBlob(b.get(key)) : null, i);
    }
  }
  return changeIdxCache;
}

// Was a removal at changeDates[idx] a scheduled fixed-term expiry or a repeal?
function removalReason(addr, idx) {
  const ts = transitionsFor(addr, changeDates[idx]);
  return ts.some(t => String(t.legal_op_kind || '').split(',').includes('expiry'))
    ? 'expiry' : 'repeal';
}

// Badge "3/12" (changes up to the scrubbed date / total over the timeline)
// plus a lifecycle strip on the real time axis: half-height duration bars
// (in force / repealed gap / expired gap) + full-height event ticks (insert /
// amend / repeal / expiry), future events dimmed, current date as a cursor.
// Clicking opens the version history.
function changeBadgeHtml(addr) {
  const events = changeIndex().get(addr) || [];
  const countable = events.filter(e => e.idx > 0);
  if (!countable.length) return '';
  const total = countable.length;
  const upto = countable.filter(e => e.idx <= curDateIdx).length;
  const countTxt = upto === total ? `${total}` : `${upto}/${total}`;
  const { t0, t1 } = axisRange();
  const fx = (i) => ((Date.parse(changeDates[i]) - t0) / (t1 - t0)) * 100;
  const lastIdx = changeDates.length - 1;

  // Presence segments (duration bars).
  let segHtml = '';
  let present = false, segFrom = 0, absentCls = '';
  const emitSeg = (from, to, cls) => {
    const l = fx(from), w = Math.max(fx(to) - l, 0.5);
    segHtml += `<b class="${cls}" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%"></b>`;
  };
  for (const e of events) {
    if (e.kind === 'removed') {
      if (present) emitSeg(segFrom, e.idx, 'seg-on');
      present = false; segFrom = e.idx;
      absentCls = removalReason(addr, e.idx) === 'expiry' ? 'seg-exp' : 'seg-rep';
    } else if (!present) {
      if (absentCls) emitSeg(segFrom, e.idx, absentCls);
      present = true; segFrom = e.idx;
    }
  }
  if (present) emitSeg(segFrom, lastIdx, 'seg-on');
  else if (absentCls) emitSeg(segFrom, lastIdx, absentCls);

  // Event ticks.
  let tickHtml = '';
  for (const e of countable) {
    let cls = e.kind === 'added' ? 'tk-add' : 'tk-chg';
    if (e.kind === 'removed') cls = removalReason(addr, e.idx) === 'expiry' ? 'tk-exp' : 'tk-rem';
    if (e.idx > curDateIdx) cls += ' fut';
    const kindTxt = e.kind === 'removed'
      ? opKindLabel(removalReason(addr, e.idx) === 'expiry' ? 'expiry' : 'repeal')
      : opKindLabel(e.kind === 'added' ? 'insert' : 'replace');
    tickHtml += `<i class="${cls}" style="left:${fx(e.idx).toFixed(2)}%" title="${escAttr(`${changeDates[e.idx]} — ${kindTxt}`)}"></i>`;
  }
  const cursor = `<u class="strip-cursor" style="left:${fx(curDateIdx).toFixed(2)}%"></u>`;
  return `<button class="chg-badge" data-addr="${escAttr(addr)}" title="${escAttr(tr('stripTip'))}">`
    + `<span class="chg-count">${escHtml(countTxt)}</span>`
    + `<span class="chg-strip">${segHtml}${tickHtml}${cursor}</span></button>`;
}

// ---- derived ghost tombstones (repealed/expired units shown in place) ----
// parent addr -> [{addr, removedIdx}] for units absent at the selected date
// whose history shows they existed earlier (Finlex renders these as
// "54 a § on kumottu L:lla …" lines; silent disappearance hides law).
let ghostsByParent = null;
let ghostsDateIdx = -1;
function ghostMap() {
  if (ghostsByParent && ghostsDateIdx === curDateIdx) return ghostsByParent;
  ghostsByParent = new Map();
  ghostsDateIdx = curDateIdx;
  for (const [addr, events] of changeIndex()) {
    let last = null;
    for (const e of events) { if (e.idx <= curDateIdx) last = e; else break; }
    if (!last || last.kind !== 'removed') continue;
    const cut = addr.lastIndexOf('/');
    if (cut < 0) continue; // top-level covering tombstones are tracked by the fold
    const parent = addr.slice(0, cut);
    let l = ghostsByParent.get(parent);
    if (!l) { l = []; ghostsByParent.set(parent, l); }
    l.push({ addr, removedIdx: last.idx });
  }
  return ghostsByParent;
}

function ghostHtml(g) {
  const date = changeDates[g.removedIdx];
  const ts = transitionsFor(g.addr, date);
  const t = ts.length ? ts[ts.length - 1] : null;
  const src = t && t.source_id ? sourceById[t.source_id] : null;
  const srcLabel = src ? (src.canonical_id || src.title || t.source_id) : (t ? t.source_id : '');
  const [k, n] = g.addr.split('/').pop().split(':');
  const meta = refLink('date', { date, addr: g.addr })
    + (t && t.source_id ? ' · ' + refLink('source', { id: t.source_id }, srcLabel) : '');
  // Label the ghost exactly as the live node would have read (kindLabel from
  // the pre-removal fold) — same format as live rows, only the muting differs.
  const prevDate = g.removedIdx > 0 ? changeDates[g.removedIdx - 1] : null;
  const wasNode = prevDate ? nodeAtAddress(allFolds()[prevDate].live, g.addr) : null;
  const label = wasNode ? kindLabel(wasNode, parseInt(n, 10) || 0) : J.addrSeg(k, n);
  return `<div class="pblock ghost-line" data-addr="${escAttr(g.addr)}">`
    + `<span class="tomb-label">${escHtml(label)} <em>${escHtml(tr('tombstone'))}</em></span>`
    + `<span class="tomb-meta">${meta}</span>`
    + changeBadgeHtml(g.addr)
    + evidenceBadgeHtml(g.addr)
    // Row-style button (not the count chip): identical trailing width keeps
    // the lifecycle-strip column flush with section rows.
    + historyBtnHtml(g.addr, /*showCount*/ false)
    + `</div>`;
}

// Transitions on `date` whose target is related to `addr` (equal, ancestor or
// descendant) — the certified provenance for a derived version boundary.
function transitionsFor(addr, date) {
  return transitions.filter(t => {
    if (t.effective_date !== date) return false;
    const ta = t.target_address;
    return ta === addr || addr.startsWith(ta + '/') || ta.startsWith(addr + '/');
  }).sort((a, b) => a.sequence - b.sequence);
}

function certCoarserThan(addr) {
  const recorded = new Set(transitions.map(t => t.target_address));
  if (recorded.has(addr)) return false;
  return addr.split('/').length > 1;
}

// ---- diff payload registry (lazy <details> rendering) ----
let diffSeq = 0;
const diffPayloads = new Map(); // id -> {preTxt, postTxt} | {structured, addr, preNode, postNode}
function registerDiff(payload) {
  const id = `dp${++diffSeq}`;
  diffPayloads.set(id, payload);
  return id;
}
function diffDetailsTag(id, open) {
  return `<details class="diff" data-diff-id="${id}"${open ? ' open' : ''}>`
    + `<summary>${escHtml(tr('showDiff'))}</summary>`
    + `<div class="diff-body"></div></details>`;
}
function diffDetailsHtml(preTxt, postTxt, open) {
  return diffDetailsTag(registerDiff({ preTxt, postTxt }), open);
}
// Structured (hierarchically localized) node diff: decomposed into the changed
// addressable sub-provisions on render, never one flat wall of text.
function diffNodeDetailsHtml(addr, preNode, postNode, open) {
  return diffDetailsTag(registerDiff({ structured: true, addr, preNode, postNode }), open);
}
function wireDiffDetails(root) {
  root.querySelectorAll('details.diff').forEach(d => {
    const render = () => {
      if (d.dataset.rendered) return;
      const p = diffPayloads.get(d.dataset.diffId);
      if (!p) return;
      d.querySelector('.diff-body').innerHTML = p.structured
        ? structuredDiffHtml(p.addr, p.preNode, p.postNode)
        : diffBlockHtml(p.preTxt, p.postTxt);
      d.dataset.rendered = '1';
    };
    if (d.open) render();
    d.addEventListener('toggle', () => { if (d.open) render(); });
  });
}

function structuredDiffHtml(addr, preNode, postNode) {
  const changes = [];
  descendCompare(addr, preNode, postNode, changes);
  if (!changes.length) return diffBlockHtml(nodeToText(preNode), nodeToText(postNode));
  changes.sort((a, b) => addrCompare(a.addr, b.addr));
  if (changes.length === 1 && changes[0].addr === addr) {
    return changeEntryDiffHtml(changes[0]);
  }
  let html = '<div class="sdiff">';
  for (const c of changes) {
    html += `<div class="sdiff-item">`
      + `<div class="sdiff-head"><span class="op-kind vk-${c.kind}">${escHtml(changeKindLabel(c.kind))}</span> `
      + `<span class="sdiff-addr">${escHtml(prettyAddr(c.addr))}</span></div>`
      + changeEntryDiffHtml(c)
      + `</div>`;
  }
  html += '</div>';
  return html;
}

// Diff body for one localized change entry. Wholly inserted/removed units
// render as STRUCTURED prose (per-momentti/kohta blocks with labels), never
// one flat slab of section text; in-place changes get the word diff.
function changeEntryDiffHtml(c) {
  if (c.kind === 'added' || c.kind === 'removed') {
    const node = c.kind === 'added' ? c.nodeB : c.nodeA;
    const lbl = c.kind === 'added' ? tr('newContent') : tr('removedContent');
    const cls = c.kind === 'added' ? 'post' : 'pre';
    return `<div class="diff-stack"><div class="diff-side"><div class="diff-lbl">${escHtml(lbl)}</div>`
      + `<div class="diff-box ${cls}">${nodeProseHtml(node, c.addr)}</div></div></div>`;
  }
  return diffBlockHtml(nodeToText(c.nodeA), nodeToText(c.nodeB));
}

// Structured prose for a whole unit: own text, then each addressable child
// as a labelled block, recursively.
function nodeProseHtml(node, addr) {
  if (!node) return '';
  const kids = structChildren(node, addr);
  const own = inlineContent(node).map(s => escHtml(s.text)).join(' ');
  if (!kids.length) return own || escHtml(nodeToText(node));
  const heading = nodeHeading(node);
  let html = '';
  if (heading) html += `<div class="dp-head">${escHtml(kindLabel(node, 0))} ${escHtml(heading)}</div>`;
  if (own) html += `<p class="dp-text">${own}</p>`;
  for (const { child, childAddr } of kids) {
    html += `<div class="dp-block"><span class="dp-lbl">${escHtml(kindLabel(child, 0))}</span> ${nodeProseHtml(child, childAddr)}</div>`;
  }
  return html;
}

// ---- history panel rendering (inline under the clicked provision) ----
function historyHtml(addr) {
  const trail = versionTrail(addr);
  const presentVersions = trail.filter(v => v.present);
  let html = `<div class="hist-head">`
    + `<span class="hist-addr">${escHtml(prettyAddr(addr))}</span>`
    + `<span class="hist-addr-raw">${escHtml(addr)}</span>`
    + `<button class="cite-btn copy-text" type="button">${escHtml(tr('copyText'))}</button>`
    + `<button class="cite-btn copy-cite" type="button">${escHtml(tr('copyCite'))}</button>`
    + `<button class="cite-btn copy-link" type="button">${escHtml(tr('copyLink'))}</button>`
    + `<span class="cite-status"></span>`
    + `<button class="cite-btn hist-close" type="button">${escHtml(tr('historyClose'))}</button>`
    + `</div>`;

  if (certCoarserThan(addr)) {
    const g = tr({ chapter: 'granChapter', section: 'granSection', subsection: 'granSubsection' }[metaInfo.certGranularity] || 'granChapter');
    html += `<p class="hist-derived-note">${escHtml(tr('derivedNote', g))}</p>`;
  }

  html += evidenceListHtml(evidenceRelatedToAddress(addr), tr('evidenceForProvision'));

  if (!trail.some(v => v.present)) {
    html += `<p class="muted-empty">${escHtml(tr('historyEmpty'))}</p>`;
    return html;
  }

  let prevPresentNode = null;
  let verIdx = 0;
  const nPresent = presentVersions.length;
  for (const v of trail) {
    const startDate = changeDates[v.startIdx];
    const endIdx = v.endIdx;
    const endDate = endIdx + 1 < changeDates.length ? changeDates[endIdx + 1] : null;
    const isCurrent = curDateIdx >= v.startIdx && curDateIdx <= v.endIdx;
    const isFuture = v.startIdx > curDateIdx;

    if (!v.present) {
      html += `<div class="change tomb-window${isCurrent ? ' applies' : ''}">`
        + `<div class="change-date">${escHtml(startDate)}–${escHtml(endDate || '—')} <em>${escHtml(tr('repealedWindow'))}</em></div>`;
      // The removal must never be unexplained: show what caused it — a repeal,
      // or a temporary act's scheduled expiry — with the act's provenance.
      const reason = removalReason(addr, v.startIdx);
      html += `<div class="change-op"><span class="op-kind${reason === 'expiry' ? ' op-exp' : ''}">${escHtml(opKindLabel(reason === 'expiry' ? 'expiry' : 'repeal'))}</span></div>`;
      const ts = transitionsFor(addr, startDate);
      const tSrc = ts.find(t => t.source_id) || ts[ts.length - 1];
      if (tSrc) html += provenanceHtml(tSrc);
      html += evidenceListHtml(evidenceForChange(addr, startDate, tSrc ? tSrc.source_id : ''), tr('evidenceForChange'));
      html += `</div>`;
      prevPresentNode = null; // diff after a repeal window compares to nothing
      continue;
    }

    verIdx += 1;
    const ts = transitionsFor(addr, startDate);
    const cls = isCurrent ? 'applies' : (isFuture ? 'future' : '');
    html += `<div class="change ${cls}">`;
    html += `<div class="change-date">${escHtml(tr('effectiveOn'))} ${escHtml(startDate)}`
      + ` <span class="validity-inline">${escHtml(tr('inForce'))} ${escHtml(startDate)}–${escHtml(endDate || '—')}</span>`
      + ` <span class="ver-tag">${escHtml(tr('versionN', verIdx, nPresent))}</span>`
      + (v.startIdx === 0 ? ` <span class="ver-tag">${escHtml(tr('originalAct'))}</span>` : '')
      + (isCurrent ? ` <span class="cur-tag">${escHtml(tr('currentVersion'))}</span>` : '')
      + (isFuture ? ` <span class="future-tag">${escHtml(tr('futureTag'))}</span>` : '')
      + `</div>`;
    // The op kind shown is THIS node's own change (derived from the trail),
    // not the aggregated kinds of the whole certified transition — opening a
    // single momentti must not announce its chapter's unrelated ops. The raw
    // machine summary (addresses, brackets) is deliberately not rendered; the
    // localized diff below shows what actually changed.
    if (v.startIdx > 0) {
      const kind = prevPresentNode ? 'replace' : 'insert';
      html += `<div class="change-op"><span class="op-kind">${escHtml(opKindLabel(kind))}</span></div>`;
    }
    const tSrc = ts.find(t => t.source_id) || ts[ts.length - 1];
    if (tSrc) html += provenanceHtml(tSrc);
    html += evidenceListHtml(evidenceForChange(addr, startDate, tSrc ? tSrc.source_id : ''), tr('evidenceForChange'));
    html += diffNodeDetailsHtml(addr, prevPresentNode, v.node, /*open*/ isCurrent);
    prevPresentNode = v.node;
    html += `</div>`;
  }
  return html;
}

function wireHistory(container, addr) {
  wireDiffDetails(container);
  const cs = container.querySelector('.cite-status');
  const cb = container.querySelector('.copy-cite');
  const cl = container.querySelector('.copy-link');
  const ct = container.querySelector('.copy-text');
  if (ct) ct.addEventListener('click', () => copyToClip(provisionCopyText(addr), cs, tr('textCopied')));
  if (cb) cb.addEventListener('click', () => copyToClip(citationText(addr), cs, tr('citeCopied')));
  if (cl) cl.addEventListener('click', () => copyToClip(permalinkUrl(addr), cs, tr('linkCopied')));
  const hc = container.querySelector('.hist-close');
  if (hc) hc.addEventListener('click', () => clearSelection());
}

// ---- inline panel under the clicked element ----
function toggleInlineHistory(addr) {
  if (selectedAddress === addr) { clearSelection(); return; }
  selectedAddress = addr;
  // Manual provision pick: the search term no longer applies to this node.
  if (searchHighlight && searchHighlight.addr !== addr) clearSearchHighlight();
  openInlineHistory(addr, /*scroll*/ false);
  if (!suppressHashUpdate) updateHash();
}

function removeInlinePanel() {
  document.querySelectorAll('.inline-history').forEach(p => p.remove());
  document.querySelectorAll('.hist-btn.active').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.evidence-badge.active').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.hist-anchor').forEach(b => b.classList.remove('hist-anchor'));
}

function openInlineHistory(addr, scroll) {
  removeInlinePanel();
  const anchor = document.querySelector(`#doc .node[data-addr="${cssEsc(addr)}"]`)
    || document.querySelector(`#doc .pblock[data-addr="${cssEsc(addr)}"]`);
  if (!anchor) return;
  // Make sure the anchor is visible (expand collapsed ancestors).
  let el = anchor.parentElement;
  while (el && el.id !== 'doc') {
    if (el.classList.contains('node') && el.classList.contains('collapsed')) toggleCollapse(el, false);
    el = el.parentElement;
  }
  const panel = document.createElement('div');
  panel.className = 'inline-history';
  panel.innerHTML = `<div class="ih-title">${escHtml(tr('historyTitle'))}</div>` + historyHtml(addr);
  // For outline nodes insert right under the heading row; for prose blocks
  // insert after the block itself.
  const row = anchor.querySelector(':scope > .node-row');
  if (row) row.insertAdjacentElement('afterend', panel);
  else anchor.insertAdjacentElement('afterend', panel);
  wireHistory(panel, addr);
  anchor.classList.add('hist-anchor');
  const btn = document.querySelector(`.hist-btn[data-addr="${cssEsc(addr)}"]`);
  if (btn) btn.classList.add('active');
  const evBtn = document.querySelector(`.evidence-badge[data-addr="${cssEsc(addr)}"]`);
  if (evBtn) evBtn.classList.add('active');
  if (scroll) {
    spy.suppressUntil = Date.now() + 1500;
    (row || anchor).scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function clearSelection() {
  selectedAddress = null;
  if (searchHighlight) clearSearchHighlight();
  removeInlinePanel();
  if (!suppressHashUpdate) updateHash();
}

// =====================================================================
// Provenance + citation helpers
// =====================================================================
function opKindBadges(ts) {
  const kinds = new Set();
  let anyKind = false;
  for (const t of ts) {
    if (t.legal_op_kind) {
      anyKind = true;
      for (const k of String(t.legal_op_kind).split(',')) { const kk = k.trim(); if (kk) kinds.add(kk); }
    }
  }
  if (!anyKind) {
    return `<span class="op-kind op-unknown" title="${escAttr(tr('opUnknownTip'))}">${escHtml(tr('opUnknown'))}</span>`;
  }
  return [...kinds].map(k => `<span class="op-kind">${escHtml(opKindLabel(k))}</span>`).join(' ');
}

function prepWorksHtml(ref) {
  if (!ref) return '';
  const url = J.prepWorksUrl(ref);
  if (!url) return escHtml(ref);
  return `<a href="${escAttr(url)}" target="_blank" rel="noopener">${escHtml(ref)} ↗</a>`;
}

function provenanceHtml(t) {
  const src = sourceById[t.source_id];
  if (!src && !t.he_ref && !t.source_id) return '';
  let html = `<div class="provenance">`;
  if (src) {
    html += `<div><span class="lbl">${escHtml(tr('amendingAct'))}:</span> `;
    if (src.url) html += `<a href="${escAttr(src.url)}" target="_blank" rel="noopener">${escHtml(src.title || src.canonical_id || t.source_id)}</a>`;
    else html += escHtml(src.title || src.canonical_id || t.source_id);
    if (src.canonical_id) html += ` (${refLink('source', { id: t.source_id }, src.canonical_id)})`;
    if (src.date) html += ` <span class="ann-date">${escHtml(tr('givenDate'))} ${escHtml(src.date)}</span>`;
    html += `</div>`;
  } else if (t.source_id) {
    html += `<div><span class="lbl">${escHtml(tr('amendingAct'))}:</span> ${escHtml(t.source_id)}</div>`;
  }
  if (t.he_ref) html += `<div><span class="lbl">${escHtml(tr('prepWorks'))}:</span> ${prepWorksHtml(t.he_ref)}</div>`;
  html += `</div>`;
  return html;
}

function copyToClip(text, statusEl, okMsg) {
  const done = () => { if (statusEl) { statusEl.textContent = okMsg; setTimeout(() => statusEl.textContent = '', 2500); } };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => { fallbackCopy(text); done(); });
  } else { fallbackCopy(text); done(); }
}
function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); } catch (e) { /* ignore */ }
  document.body.removeChild(ta);
}

function citationText(address) {
  const vi = validityInterval(curDateIdx);
  const acts = [...new Set(transitionsForAllDates(address).map(t => {
    const s = sourceById[t.source_id];
    return s ? (s.canonical_id || s.source_id) : t.source_id;
  }).filter(Boolean))];
  let out = tr('citation', metaInfo.title, currentStatuteId, prettyAddr(address),
    J.fmtDate(vi.start), vi.end ? J.fmtDate(vi.end) : null, `${curTreeHash.slice(0, 16)}…`);
  if (acts.length) out += `\n${tr('citationActs', acts.join(', '))}`;
  out += `\n${permalinkUrl(address)}`;
  return out;
}
function transitionsForAllDates(addr) {
  return transitions.filter(t => {
    const ta = t.target_address;
    return ta === addr || addr.startsWith(ta + '/') || ta.startsWith(addr + '/');
  });
}

// Plain-text rendering of a provision subtree with momentti/kohta labels, for
// the clipboard "copy text" affordance. Mirrors the on-screen structure (label
// + text per addressable unit), without chips/badges. Depth 0 = the clicked
// node itself; its label is omitted because the copy header carries the address.
function labelledNodeText(node, addr, depth) {
  depth = depth || 0;
  const lines = [];
  const inline = inlineContent(node).map(s => s.text).join(' ').replace(/\s+/g, ' ').trim();
  const ordinal = parseInt((addr.split('/').pop() || '').split(':')[1] || '0', 10);
  const lbl = depth === 0 ? '' : kindLabel(node, ordinal);
  if (inline) lines.push((lbl ? lbl + ' ' : '') + inline);
  else if (lbl) lines.push(lbl);
  for (const { child, childAddr } of structChildren(node, addr)) {
    const sub = labelledNodeText(child, childAddr, depth + 1);
    if (sub) lines.push(sub);
  }
  return lines.join('\n');
}

// "Copy text" payload: the provision as it reads on the selected date, plus a
// provenance footer (same citation + permalink as Copy citation). The opt-in
// counterpart to a raw drag-selection — every copy is self-citing.
function provisionCopyText(address) {
  const node = nodeAtAddress(curLive, address);
  const vi = validityInterval(curDateIdx);
  const body = node ? labelledNodeText(node, address, 0) : '';
  let out = `${prettyAddr(address)}\n${body}`.trim();
  out += `\n\n${tr('citation', metaInfo.title, currentStatuteId, prettyAddr(address),
    J.fmtDate(vi.start), vi.end ? J.fmtDate(vi.end) : null, `${curTreeHash.slice(0, 16)}…`)}`;
  out += `\n${permalinkUrl(address)}`;
  return out;
}

// =====================================================================
// Muutokset (amendment-as-ops) view
// =====================================================================
function amendmentList() {
  const byId = new Map();
  for (const t of transitions) {
    if (!t.source_id) continue;
    const e = byId.get(t.source_id) || { firstDate: t.effective_date, opCount: 0, src: sourceById[t.source_id] || null };
    if (t.effective_date < e.firstDate) e.firstDate = t.effective_date;
    e.opCount += 1;
    byId.set(t.source_id, e);
  }
  return [...byId.entries()]
    .map(([id, v]) => ({ source_id: id, ...v }))
    .sort((a, b) => (a.firstDate < b.firstDate ? -1 : a.firstDate > b.firstDate ? 1 : (a.source_id < b.source_id ? -1 : 1)));
}

function renderAmendments() {
  const view = document.getElementById('view');
  const amendments = amendmentList();
  if (!selectedSourceId && amendments.length) selectedSourceId = amendments[amendments.length - 1].source_id;

  let listHtml = '<ul class="amend-list">';
  for (const a of amendments) {
    const src = a.src;
    const title = src ? (src.title || src.canonical_id || a.source_id) : a.source_id;
    const active = a.source_id === selectedSourceId ? ' active' : '';
    listHtml += `<li class="amend-item${active}" data-src="${escAttr(a.source_id)}">`
      + `<div class="amend-date">${escHtml(a.firstDate)}</div>`
      + `<div class="amend-title">${escHtml(title)}</div>`
      + `<div class="amend-meta">${escHtml(a.source_id)} · ${escHtml(tr('targetings', a.opCount))}</div>`
      + `</li>`;
  }
  listHtml += '</ul>';

  view.innerHTML = `
    <div class="layout layout-amend">
      <div class="panel">
        <h2 class="panel-title">${escHtml(tr('amendList', amendments.length))}</h2>
        ${listHtml}
      </div>
      <div class="panel">
        <h2 class="panel-title">${escHtml(tr('amendWhat'))}</h2>
        <div id="amend-detail"></div>
      </div>
    </div>`;

  for (const li of view.querySelectorAll('.amend-item')) {
    li.addEventListener('click', (e) => {
      if (e.target.closest('a.ref-link')) return;
      selectedSourceId = li.dataset.src;
      for (const x of view.querySelectorAll('.amend-item')) x.classList.toggle('active', x.dataset.src === selectedSourceId);
      renderAmendDetail(selectedSourceId);
    });
  }
  if (selectedSourceId) renderAmendDetail(selectedSourceId);
}

function renderAmendDetail(sourceId) {
  const el = document.getElementById('amend-detail');
  if (!el) return;
  const src = sourceById[sourceId];
  const ops = transitions.filter(t => t.source_id === sourceId).sort((a, b) => a.sequence - b.sequence);
  const effectiveDates = [...new Set(ops.map(o => o.effective_date))].sort();

  let html = `<div class="amend-detail-head">`;
  html += `<div class="amend-detail-title">${escHtml(src ? (src.title || sourceId) : sourceId)}</div>`;
  html += `<div class="amend-detail-meta">`;
  html += `<span><span class="lbl">${escHtml(tr('amendingAct'))}:</span> ${escHtml(sourceId)}</span>`;
  if (src && src.date) html += `<span><span class="lbl">${escHtml(tr('givenDate'))}:</span> ${escHtml(src.date)}</span>`;
  if (effectiveDates.length) {
    html += `<span><span class="lbl">${escHtml(tr('effectiveLbl'))}:</span> `
      + effectiveDates.map(d => changeDates.indexOf(d) >= 0 ? refLink('date', { date: d }) : escHtml(d)).join(', ')
      + `</span>`;
  }
  const heRef = (ops.find(o => o.he_ref) || {}).he_ref;
  if (heRef) html += `<span><span class="lbl">${escHtml(tr('prepWorks'))}:</span> ${prepWorksHtml(heRef)}</span>`;
  if (src && src.url) html += `<span><span class="lbl">${escHtml(tr('sourceLink'))}:</span> <a href="${escAttr(src.url)}" target="_blank" rel="noopener">↗</a></span>`;
  html += `</div></div>`;
  html += evidenceListHtml(evidenceForSource(sourceId), tr('evidenceForAmendment'));

  html += `<div class="op-list">`;
  for (const t of ops) {
    html += `<div class="op-row">`;
    html += `<div class="op-row-head">`;
    html += `${opKindBadges([t])}`;
    html += `<span class="op-addr">${escHtml(prettyAddr(t.target_address))}</span>`;
    html += `<span class="op-eff">${escHtml(t.effective_date)}</span>`;
    html += `</div>`;
    html += evidenceListHtml(evidenceForChange(t.target_address, t.effective_date, sourceId), tr('evidenceForChange'));
    html += localizedOpChangesHtml(t);
    html += `</div>`;
  }
  html += `</div>`;
  el.innerHTML = html;

  el.querySelectorAll('.goto-addr').forEach(a => {
    a.addEventListener('click', (e) => { e.preventDefault(); goToAddrAtDate(a.dataset.addr, a.dataset.date || ''); });
  });
  wireDiffDetails(el);
}

// Hierarchically localized rendering of one certified transition: decompose
// the certified pre/post subtrees into the changed addressable nodes (derived
// localization) and diff each separately — never one flat wall of text.
function localizedOpChangesHtml(t) {
  const preNode = t.pre_hash ? getBlob(t.pre_hash) : null;
  const postNode = (t.post_hash || t.payload_hash) ? getBlob(t.post_hash || t.payload_hash) : null;
  const changes = [];
  descendCompare(t.target_address, preNode, postNode, changes);
  if (!changes.length) {
    return diffDetailsHtml(nodeToText(preNode), nodeToText(postNode), false);
  }
  changes.sort((a, b) => addrCompare(a.addr, b.addr));
  const openAll = changes.length <= 4;
  let html = `<div class="op-changes" title="${escAttr(tr('derivedNote', metaInfo.certGranularity))}">`;
  for (const c of changes) {
    html += `<div class="op-change">`
      + `<span class="op-kind vk-${c.kind}">${escHtml(changeKindLabel(c.kind))}</span> `
      + `<a href="#" class="op-change-addr goto-addr" data-addr="${escAttr(c.addr)}" data-date="${escAttr(t.effective_date)}">${escHtml(prettyAddr(c.addr))}</a>`
      + diffNodeDetailsHtml(c.addr, c.nodeA, c.nodeB, openAll)
      + `</div>`;
  }
  html += `</div>`;
  return html;
}

function changeKindLabel(kind) {
  return kind === 'added' ? tr('compareAdded')
    : kind === 'removed' ? tr('compareRemovedKind')
    : tr('compareChangedKind');
}

// =====================================================================
// Diachronic phrase search
// =====================================================================
let blobTextByHash = {};

// Deepest addressable nodes whose text contains the phrase. A phrase spanning
// sibling boundaries falls back to their parent (the deepest node that fully
// contains it).
function deepMatchAddrs(node, addr, phraseLc, out) {
  const kids = structChildren(node, addr);
  let foundDeeper = false;
  for (const { child, childAddr } of kids) {
    if (nodeToText(child).toLowerCase().includes(phraseLc)) {
      foundDeeper = true;
      deepMatchAddrs(child, childAddr, phraseLc, out);
    }
  }
  if (!foundDeeper && nodeToText(node).toLowerCase().includes(phraseLc)) out.add(addr);
}

function renderSearch() {
  const view = document.getElementById('view');
  view.innerHTML = `
    <div class="search-wrap panel">
      <h2 class="panel-title">${escHtml(tr('searchTitle'))}</h2>
      <form id="search-form" class="search-form">
        <input type="search" id="search-input" placeholder="${escAttr(tr('searchPlaceholder'))}" autocomplete="off">
        <button type="submit">${escHtml(tr('searchBtn'))}</button>
      </form>
      <p class="search-note">${tr('searchNote')}</p>
      <div id="search-results"></div>
    </div>`;
  const form = document.getElementById('search-form');
  const input = document.getElementById('search-input');
  form.addEventListener('submit', (e) => { e.preventDefault(); runDiachronicSearch(input.value); });
  if (pendingSearchQuery) { input.value = pendingSearchQuery; runDiachronicSearch(pendingSearchQuery); pendingSearchQuery = null; }
}

function blobText(hash) {
  if (!hash) return '';
  if (hash in blobTextByHash) return blobTextByHash[hash];
  const node = getBlob(hash);
  const txt = node ? nodeToText(node).toLowerCase() : '';
  blobTextByHash[hash] = txt;
  return txt;
}

function runDiachronicSearch(rawQuery) {
  const out = document.getElementById('search-results');
  const phrase = (rawQuery || '').trim();
  if (!phrase) { out.innerHTML = `<p class="muted-empty">${escHtml(tr('searchGiveQuery'))}</p>`; return; }
  const phraseLc = phrase.toLowerCase().replace(/\s+/g, ' ');
  const folds = allFolds();

  // 1) Scan all historical blobs once; localize each match to the DEEPEST
  //    addressable node containing the phrase — a chapter-level hit address
  //    is useless to a researcher.
  const matchAddrs = new Set();
  for (const t of transitions) {
    const h = t.post_hash;
    if (h && blobText(h).includes(phraseLc)) {
      const node = getBlob(h);
      if (node) deepMatchAddrs(node, t.target_address, phraseLc, matchAddrs);
    }
  }
  if (!matchAddrs.size) {
    out.innerHTML = `<p class="muted-empty">${tr('searchNone', escHtml(phrase))}</p>`;
    return;
  }

  let html = `<p class="search-count">${tr('searchCount', matchAddrs.size, escHtml(phrase))}</p>`;
  const sorted = [...matchAddrs].sort(addrCompare);
  for (const addr of sorted) {
    // 2) Per deep address: walk every change date, extract the node from the
    //    certified fold, and track when the phrase entered / left it.
    const intervals = [];
    const introduced = [];
    const removed = [];
    let prevHas = false;
    let open = null;
    let lastHasNode = null;
    for (let i = 0; i < changeDates.length; i++) {
      const d = changeDates[i];
      const node = nodeAtAddress(folds[d].live, addr);
      const has = node ? nodeToText(node).toLowerCase().includes(phraseLc) : false;
      if (has) lastHasNode = node;
      if (has && !prevHas) { introduced.push(i); open = d; }
      if (!has && prevHas) { removed.push(i); intervals.push({ start: open, end: d }); open = null; }
      prevHas = has;
    }
    if (open) intervals.push({ start: open, end: null });
    if (!introduced.length) continue; // defensive: blob matched but no fold did

    html += `<div class="search-hit">`;
    html += `<div class="search-hit-head"><a href="#" class="search-goto" data-addr="${escAttr(addr)}" data-date="${escAttr(intervals.length ? (intervals[intervals.length - 1].start || '') : '')}">`
      + `${escHtml(prettyAddr(addr))}</a></div>`;
    if (intervals.length) {
      html += `<div class="search-intervals"><span class="lbl">${escHtml(tr('searchInForceWith'))}:</span> `
        + intervals.map(iv => `${escHtml(iv.start)}–${escHtml(iv.end || '—')}`).join(', ') + `</div>`;
    }
    for (const i of introduced) html += attributionRow(tr('searchIntroduced'), i, addr, phrase);
    for (const i of removed) html += attributionRow(tr('searchRemoved'), i, addr, phrase);
    if (lastHasNode) {
      const snip = snippetAround(nodeToText(lastHasNode), phraseLc);
      if (snip) html += `<div class="search-snippet">…${highlightPhrase(snip, phrase)}…</div>`;
    }
    html += `</div>`;
  }
  out.innerHTML = html;
  out.querySelectorAll('.search-goto').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      goToAddrAtDate(a.dataset.addr, a.dataset.date, phrase);
    });
  });
}

// Attribution for a phrase entering/leaving at changeDates[idx]: the amending
// act(s) effective that day whose certified transition covers this address.
function attributionRow(label, idx, addr, phrase) {
  const date = changeDates[idx];
  const ts = transitionsFor(addr, date);
  const tSrc = ts.find(t => t.source_id) || null;
  const src = tSrc ? sourceById[tSrc.source_id] : null;
  const srcLabel = src ? (src.title || src.canonical_id || tSrc.source_id)
    : (tSrc ? tSrc.source_id : tr('originalAct'));
  const heRef = (ts.find(t => t.he_ref) || {}).he_ref;
  // Date jumps to the law in force then (carrying the search phrase so the
  // main-pane highlight repaints); the act opens in the Amendments view.
  const dateLink = refLink('date', { date, addr, phrase });
  const actLink = tSrc ? refLink('source', { id: tSrc.source_id }, srcLabel) : escHtml(srcLabel);
  let html = `<div class="search-attr"><span class="attr-label">${escHtml(label)}:</span> ${dateLink} — ${actLink}`;
  if (src && src.canonical_id) html += ` (${escHtml(src.canonical_id)})`;
  if (heRef) html += ` · ${prepWorksHtml(heRef)}`;
  html += `</div>`;
  return html;
}

function snippetAround(text, phraseLc) {
  const lc = text.toLowerCase();
  const i = lc.indexOf(phraseLc);
  if (i < 0) return '';
  const start = Math.max(0, i - 60), end = Math.min(text.length, i + phraseLc.length + 60);
  return text.slice(start, end).replace(/\s+/g, ' ').trim();
}

// HTML-escape the snippet while wrapping case-insensitive phrase matches in
// <mark> for visual pinpointing.
function highlightPhrase(snippet, phrase) {
  const re = new RegExp(phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+'), 'gi');
  let outHtml = '';
  let last = 0;
  for (const m of snippet.matchAll(re)) {
    outHtml += escHtml(snippet.slice(last, m.index)) + `<mark>${escHtml(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  outHtml += escHtml(snippet.slice(last));
  return outHtml;
}

function goToAddrAtDate(addr, date, phrase) {
  setMode('law', /*skipRender*/ true);
  const idx = date ? changeDates.indexOf(date) : -1;
  const targetIdx = idx >= 0 ? idx : curDateIdx >= 0 ? curDateIdx : changeDates.length - 1;
  selectedAddress = addr;
  // A bare nav (amendments/compare link, no phrase) drops any prior search mark.
  searchHighlight = phrase ? { addr, phrase } : null;
  selectDate(targetIdx, { skipRender: true }).then(() => {
    setMode('law');
    setTimeout(() => { openInlineHistory(addr, true); reapplySearchHighlight(); }, 50);
  });
}

// ---- main-pane phrase highlighting (CSS Custom Highlight API) ----
// The Google "jump to highlight" analogue: locate the phrase inside the
// rendered provision and paint it via ::highlight(). No DOM mutation, so it
// neither disturbs diff/derivation logic nor survives a re-render — hence
// reapplySearchHighlight() runs after each renderDoc.
function clearSearchHighlight() {
  searchHighlight = null;
  if (window.CSS && CSS.highlights) CSS.highlights.delete(SEARCH_HL_NAME);
  if (!suppressHashUpdate) updateHash();
}

function reapplySearchHighlight() {
  if (window.CSS && CSS.highlights) CSS.highlights.delete(SEARCH_HL_NAME);
  if (!searchHighlight || mode !== 'law') return;
  applySearchHighlight(searchHighlight.addr, searchHighlight.phrase);
}

function applySearchHighlight(addr, phrase) {
  if (!window.CSS || !CSS.highlights || typeof Highlight === 'undefined' || !phrase) return;
  const anchor = document.querySelector(`#doc .node[data-addr="${cssEsc(addr)}"]`)
    || document.querySelector(`#doc .pblock[data-addr="${cssEsc(addr)}"]`);
  if (!anchor) return;

  // Concatenate the anchor's text nodes (skipping any open inline-history
  // panel, whose text is a repeat) and keep an offset→node map so a match can
  // span element/text-node boundaries.
  const walker = document.createTreeWalker(anchor, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
      for (let p = n.parentElement; p && p !== anchor; p = p.parentElement) {
        if (p.classList && p.classList.contains('inline-history')) return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const spans = []; // {node, start, len} — start is offset into `combined`
  let combined = '';
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    spans.push({ node: n, start: combined.length, len: n.nodeValue.length });
    combined += n.nodeValue;
  }
  if (!spans.length) return;

  const locate = (off) => {
    for (const s of spans) if (off >= s.start && off <= s.start + s.len) return { node: s.node, offset: off - s.start };
    const last = spans[spans.length - 1];
    return { node: last.node, offset: last.len };
  };
  // Whitespace-tolerant, case-insensitive — mirrors the snippet highlighter.
  const re = new RegExp(phrase.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+'), 'gi');
  const ranges = [];
  for (let m = re.exec(combined); m; m = re.exec(combined)) {
    if (m.index === re.lastIndex) { re.lastIndex++; continue; }
    const a = locate(m.index), b = locate(m.index + m[0].length);
    try {
      const r = document.createRange();
      r.setStart(a.node, a.offset);
      r.setEnd(b.node, b.offset);
      ranges.push(r);
    } catch (_) { /* skip unrepresentable range */ }
  }
  if (!ranges.length) return;
  CSS.highlights.set(SEARCH_HL_NAME, new Highlight(...ranges));
}

// =====================================================================
// Vertaa (two-date compare)
// =====================================================================
function renderCompare() {
  const view = document.getElementById('view');
  if (compareSel.d1 == null) compareSel.d1 = 0;
  if (compareSel.d2 == null) compareSel.d2 = changeDates.length - 1;
  const optHtml = (sel) => changeDates.map((d, i) =>
    `<option value="${i}"${i === sel ? ' selected' : ''}>${escHtml(d)}</option>`).join('');
  view.innerHTML = `
    <div class="compare-wrap panel">
      <h2 class="panel-title">${escHtml(tr('compareTitle'))}</h2>
      <form id="compare-form" class="compare-form">
        <label>${escHtml(tr('compareFrom'))} <select id="compare-d1">${optHtml(compareSel.d1)}</select></label>
        <label>${escHtml(tr('compareTo'))} <select id="compare-d2">${optHtml(compareSel.d2)}</select></label>
        <button type="submit">${escHtml(tr('compareRun'))}</button>
      </form>
      <div id="compare-results"></div>
    </div>`;
  document.getElementById('compare-form').addEventListener('submit', (e) => {
    e.preventDefault();
    compareSel.d1 = parseInt(document.getElementById('compare-d1').value, 10);
    compareSel.d2 = parseInt(document.getElementById('compare-d2').value, 10);
    runCompare();
    if (!suppressHashUpdate) updateHash();
  });
  runCompare();
}

// Deepest changed addressable nodes between two folds: recursive fingerprint
// compare from the covering roots downward. DERIVED localization (labelled).
function changedNodesBetween(liveA, liveB) {
  const results = []; // {addr, kind: 'added'|'removed'|'changed', nodeA, nodeB}
  const roots = new Set([...liveA.keys(), ...liveB.keys()].map(a => a.split('/')[0]));
  // Compare per covering key first (covers fine-grained exports), then descend.
  const keys = new Set([...liveA.keys(), ...liveB.keys()]);
  for (const key of keys) {
    const hA = liveA.get(key), hB = liveB.get(key);
    if (hA === hB) continue;
    const nA = hA ? getBlob(hA) : null;
    const nB = hB ? getBlob(hB) : null;
    descendCompare(key, nA, nB, results);
  }
  results.sort((a, b) => addrCompare(a.addr, b.addr));
  return results;
}

function descendCompare(addr, nA, nB, results) {
  if (!nA && !nB) return;
  if (!nA) { results.push({ addr, kind: 'added', nodeA: null, nodeB: nB }); return; }
  if (!nB) { results.push({ addr, kind: 'removed', nodeA: nA, nodeB: null }); return; }
  if (subtreeFingerprint(nA) === subtreeFingerprint(nB)) return;
  const kidsA = new Map(structChildren(nA, addr).map(k => [k.childAddr, k.child]));
  const kidsB = new Map(structChildren(nB, addr).map(k => [k.childAddr, k.child]));
  const childAddrs = new Set([...kidsA.keys(), ...kidsB.keys()]);
  // A node's OWN content = its text + non-addressable children (num, heading,
  // intro, …). When only that changed, diff just it — never the whole subtree
  // (the subtree's addressable children get their own localized entries).
  const ownOnly = (n) => ({ ...n, children: (n.children || []).filter(c => !ADDR_SEG[c.kind]) });
  if (childAddrs.size === 0) {
    results.push({ addr, kind: 'changed', nodeA: nA, nodeB: nB });
    return;
  }
  if (subtreeFingerprint(ownOnly(nA)) !== subtreeFingerprint(ownOnly(nB))) {
    results.push({ addr, kind: 'changed', nodeA: ownOnly(nA), nodeB: ownOnly(nB) });
  }
  for (const ca of childAddrs) {
    descendCompare(ca, kidsA.get(ca) || null, kidsB.get(ca) || null, results);
  }
}

function runCompare() {
  const out = document.getElementById('compare-results');
  if (!out) return;
  let { d1, d2 } = compareSel;
  if (d1 === d2) { out.innerHTML = `<p class="muted-empty">${escHtml(tr('compareSame'))}</p>`; return; }
  if (d1 > d2) { [d1, d2] = [d2, d1]; }
  const dateA = changeDates[d1], dateB = changeDates[d2];
  const folds = allFolds();
  const liveA = folds[dateA].live, liveB = folds[dateB].live;
  const changes = changedNodesBetween(liveA, liveB);
  if (!changes.length) { out.innerHTML = `<p class="muted-empty">${escHtml(tr('compareNoDiff'))}</p>`; return; }

  // Amending acts effective in (dateA, dateB]
  const actsBetween = new Map();
  for (const t of transitions) {
    if (t.effective_date > dateA && t.effective_date <= dateB && t.source_id) {
      actsBetween.set(t.source_id, t);
    }
  }

  let html = `<p class="search-count">${tr('compareCount', changes.length, escHtml(dateA), escHtml(dateB))}</p>`;
  if (actsBetween.size) {
    const links = [...actsBetween.keys()].map(id => {
      const s = sourceById[id];
      return refLink('source', { id }, s ? (s.canonical_id || id) : id);
    }).join(', ');
    html += `<p class="compare-acts"><span class="lbl">${escHtml(tr('compareActs'))}:</span> ${links}</p>`;
  }
  // Expose every change directly (diff visible, no toggle); collapse behind
  // <details> only for very large compares where open-all would be slow.
  const openAll = changes.length <= 120;
  const chgIdx = changeIndex();
  for (const c of changes) {
    // Compact when/what metadata: the change dates in (D1, D2] that touched
    // this provision, each with the amending act(s) effective that day.
    const touchIdxs = (chgIdx.get(c.addr) || []).filter(i => i > d1 && i <= d2);
    const metaBits = touchIdxs.map(i => {
      const date = changeDates[i];
      const acts = [...new Set(transitionsFor(c.addr, date).map(t => t.source_id).filter(Boolean))]
        .map(id => { const s = sourceById[id]; return s ? (s.canonical_id || id) : id; });
      return `${escHtml(date)}${acts.length ? ' (' + escHtml(acts.join(', ')) + ')' : ''}`;
    });
    html += `<div class="op-row compare-row">`;
    html += `<div class="op-row-head">`;
    html += `<span class="op-kind vk-${c.kind}">${escHtml(changeKindLabel(c.kind))}</span>`;
    html += `<span class="op-addr"><a href="#" class="compare-goto" data-addr="${escAttr(c.addr)}">${escHtml(prettyAddr(c.addr))}</a></span>`;
    if (metaBits.length) html += `<span class="compare-touches">${metaBits.join(' · ')}</span>`;
    html += `</div>`;
    html += diffNodeDetailsHtml(c.addr, c.nodeA, c.nodeB, openAll);
    html += `</div>`;
  }
  out.innerHTML = html;
  wireDiffDetails(out);
  out.querySelectorAll('.compare-goto').forEach(a => {
    a.addEventListener('click', (e) => { e.preventDefault(); goToAddrAtDate(a.dataset.addr, changeDates[compareSel.d2]); });
  });
}

// =====================================================================
// Word-level diff: diff_match_patch token-encoded (preferred, same engine as
// the finlex/estonia viewers) with an LCS fallback if the CDN is unavailable.
// Rendering: UNIFIED tracked-changes style by default; below a similarity
// threshold the change is presented as a wholesale replacement (stacked
// before/after blocks) because word-level highlighting is noise there.
// =====================================================================
let dmpInstance = null;
function getDmp() {
  if (dmpInstance) return dmpInstance;
  if (typeof diff_match_patch !== 'undefined') dmpInstance = new diff_match_patch();
  return dmpInstance;
}

function tokenizeKeepWs(s) {
  return String(s || '').match(/\S+|\s+/g) || [];
}

// → [[op, text]] with op in {-1, 0, 1}; whitespace preserved inside chunks.
function computeWordOps(aTxt, bTxt) {
  const aTokens = tokenizeKeepWs(aTxt);
  const bTokens = tokenizeKeepWs(bTxt);
  const d = getDmp();
  if (d) {
    // Encode each distinct token as one char (skip the surrogate range), run
    // the char diff, decode back. Word-mode diff_match_patch, like the sibling
    // viewers, but kept dependency-light.
    const seen = Object.create(null);
    let next = 1;
    const codeFor = (tok) => {
      let c = seen[tok];
      if (c === undefined) {
        if (next === 0xD800) next = 0xE000;
        c = next++;
        if (next > 0xFFFF) return undefined; // vocab overflow → fallback
        seen[tok] = c;
      }
      return c;
    };
    let overflow = false;
    const enc = (toks) => toks.map(t => {
      const c = codeFor(t);
      if (c === undefined) { overflow = true; return ''; }
      return String.fromCharCode(c);
    }).join('');
    const ea = enc(aTokens), eb = enc(bTokens);
    if (!overflow) {
      const vocab = [];
      for (const tok in seen) vocab[seen[tok]] = tok;
      const raw = d.diff_main(ea, eb, false);
      return raw.map(([op, s]) => {
        let text = '';
        for (let i = 0; i < s.length; i++) text += vocab[s.charCodeAt(i)];
        return [op, text];
      });
    }
  }
  // LCS fallback (word-level, whitespace collapsed) with an explicit cap.
  const aw = String(aTxt || '').split(/\s+/).filter(Boolean);
  const bw = String(bTxt || '').split(/\s+/).filter(Boolean);
  if (aw.length + bw.length > 4000) return null; // caller renders too-big notice
  return lcsWordOps(aw, bw).map(([op, words]) => [op, words.join(' ') + ' ']);
}

function lcsWordOps(a, b) {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
  const ops = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) { ops.push([0, [a[i - 1]]]); i--; j--; }
    else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) { ops.push([1, [b[j - 1]]]); j--; }
    else { ops.push([-1, [a[i - 1]]]); i--; }
  }
  ops.reverse();
  const merged = [];
  for (const [type, words] of ops) {
    if (merged.length && merged[merged.length - 1][0] === type) merged[merged.length - 1][1].push(...words);
    else merged.push([type, [...words]]);
  }
  return merged;
}

// Similarity of the two texts measured FROM the computed diff: the share of
// unchanged non-whitespace material relative to the larger side.
function diffSimilarity(ops, aTxt, bTxt) {
  const nws = (s) => String(s || '').replace(/\s+/g, '').length;
  let eq = 0;
  for (const [op, text] of ops) if (op === 0) eq += nws(text);
  const total = Math.max(nws(aTxt), nws(bTxt), 1);
  return eq / total;
}

const WHOLESALE_SIMILARITY = 0.35;
const WHOLESALE_MIN_TOKENS = 8;

function diffSideHtml(label, cls, inner) {
  return `<div class="diff-side"><div class="diff-lbl">${escHtml(label)}</div>`
    + `<div class="diff-box ${cls}">${inner}</div></div>`;
}

function diffBlockHtml(preTxt, postTxt) {
  if (!preTxt && !postTxt) return `<p class="diff-empty">${escHtml(tr('nothingToDiff'))}</p>`;
  if (!preTxt) {
    return `<div class="diff-stack">`
      + diffSideHtml(tr('newContent'), 'post', `<ins class="diff-ins">${escHtml(postTxt)}</ins>`)
      + `</div>`;
  }
  if (!postTxt) {
    return `<div class="diff-stack">`
      + diffSideHtml(tr('removedContent'), 'pre', `<del class="diff-del">${escHtml(preTxt)}</del>`)
      + `</div>`;
  }
  const ops = computeWordOps(preTxt, postTxt);
  if (ops === null) {
    return `<div class="diff-toobig">${escHtml(tr('diffTooBig'))}</div>`
      + `<div class="diff-stack">`
      + diffSideHtml(tr('before'), 'pre', escHtml(preTxt))
      + diffSideHtml(tr('after'), 'post', escHtml(postTxt))
      + `</div>`;
  }
  const aTokenCount = preTxt.split(/\s+/).filter(Boolean).length;
  const bTokenCount = postTxt.split(/\s+/).filter(Boolean).length;
  const similarity = diffSimilarity(ops, preTxt, postTxt);
  if (Math.max(aTokenCount, bTokenCount) >= WHOLESALE_MIN_TOKENS && similarity < WHOLESALE_SIMILARITY) {
    // Wholesale replacement: word-level confetti would mislead — show clean
    // before/after blocks instead (side-by-side when there is room).
    return `<div class="diff-wholesale-note">${escHtml(tr('wholesale'))}</div>`
      + `<div class="diff-stack">`
      + diffSideHtml(tr('before'), 'pre', escHtml(preTxt))
      + diffSideHtml(tr('after'), 'post', escHtml(postTxt))
      + `</div>`;
  }
  // Unified tracked-changes rendering with STREAK COALESCING: alternating
  // del/ins word runs bridged only by whitespace merge into one deletion
  // streak followed by one insertion streak — word-by-word red/green
  // alternation is unreadable (same grouping as the finlex/estonia viewers).
  const isWs = (s) => !String(s).replace(/\s+/g, '');
  let html = '<div class="diff-unified">';
  let i = 0;
  while (i < ops.length) {
    if (ops[i][0] === 0) { html += escHtml(ops[i][1]); i++; continue; }
    let del = '', ins = '';
    while (i < ops.length) {
      const [o, t] = ops[i];
      if (o === -1) { del += t; i++; }
      else if (o === 1) { ins += t; i++; }
      else if (isWs(t) && i + 1 < ops.length && ops[i + 1][0] !== 0) { del += t; ins += t; i++; }
      else break;
    }
    const hasDel = del && !isWs(del), hasIns = ins && !isWs(ins);
    if (hasDel) html += `<del class="diff-del">${escHtml(del.trim())}</del>`;
    if (hasDel && hasIns) html += ' ';
    if (hasIns) html += `<ins class="diff-ins">${escHtml(ins.trim())}</ins>`;
  }
  html += '</div>';
  return html;
}

// =====================================================================
// Hash-anchored permalinks
// =====================================================================
function updateHash() {
  if (!currentStatuteId || curDateIdx < 0) return;
  const params = new URLSearchParams();
  params.set('s', currentStatuteId);
  params.set('m', mode);
  if (mode === 'law') {
    params.set('d', changeDates[curDateIdx] || '');
    if (selectedAddress) params.set('a', selectedAddress);
    if (searchHighlight && searchHighlight.phrase) params.set('q', searchHighlight.phrase);
    if (curTreeHash) params.set('h', curTreeHash.slice(0, 16));
  } else if (mode === 'amendments') {
    if (selectedSourceId) params.set('src', selectedSourceId);
  } else if (mode === 'compare') {
    if (compareSel.d1 != null) params.set('d1', changeDates[compareSel.d1] || '');
    if (compareSel.d2 != null) params.set('d2', changeDates[compareSel.d2] || '');
  }
  const next = '#' + params.toString();
  if (location.hash !== next) {
    suppressHashUpdate = true;
    history.replaceState(null, '', next);
    suppressHashUpdate = false;
  }
}

function permalinkUrl(address) {
  const params = new URLSearchParams();
  params.set('s', currentStatuteId);
  params.set('m', 'law');
  params.set('d', changeDates[curDateIdx] || '');
  if (address) params.set('a', address);
  if (curTreeHash) params.set('h', curTreeHash.slice(0, 16));
  return location.origin + location.pathname + '#' + params.toString();
}

function parseHash() {
  if (!location.hash || location.hash.length < 2) return null;
  const params = new URLSearchParams(location.hash.slice(1));
  if (!params.get('s')) return null;
  return {
    statute: params.get('s'),
    mode: params.get('m') || 'law',
    date: params.get('d') || null,
    address: params.get('a') || null,
    query: params.get('q') || null,
    src: params.get('src') || null,
    hashPrefix: params.get('h') || null,
    d1: params.get('d1') || null,
    d2: params.get('d2') || null,
  };
}

async function applyPermalink(pl) {
  suppressHashUpdate = true;
  try {
    if (pl.mode === 'amendments') {
      if (pl.src) selectedSourceId = pl.src;   // else renderAmendments picks default
      setMode('amendments');
      if (pl.src) setTimeout(() => {
        const li = document.querySelector(`.amend-item[data-src="${cssEsc(pl.src)}"]`);
        if (li) li.scrollIntoView({ block: 'nearest' });
      }, 40);
      return;
    }
    if (pl.mode === 'search') { setMode('search'); return; }
    if (pl.mode === 'compare') {
      const i1 = pl.d1 ? changeDates.indexOf(pl.d1) : -1;
      const i2 = pl.d2 ? changeDates.indexOf(pl.d2) : -1;
      compareSel.d1 = i1 >= 0 ? i1 : 0;
      compareSel.d2 = i2 >= 0 ? i2 : changeDates.length - 1;
      setMode('compare');
      return;
    }
    setMode('law', /*skipRender*/ true);
    let idx = pl.date ? changeDates.indexOf(pl.date) : -1;
    if (idx < 0) idx = changeDates.length - 1;
    selectedAddress = pl.address || null;
    searchHighlight = (pl.query && pl.address) ? { addr: pl.address, phrase: pl.query } : null;
    await selectDate(idx, { skipRender: true });
    renderLaw();
    if (pl.hashPrefix) showPermalinkProof(pl.hashPrefix);
    if (pl.address) setTimeout(() => { openInlineHistory(pl.address, true); reapplySearchHighlight(); }, 60);
  } finally {
    suppressHashUpdate = false;
    updateHash();
  }
}

function showPermalinkProof(embeddedPrefix) {
  const matches = curTreeHash.startsWith(embeddedPrefix);
  const slot = document.getElementById('verify-slot');
  if (!slot) return;
  const badge = matches
    ? `<span class="perma-proof ok" title="${escAttr(tr('citeProofOkTip'))}">${escHtml(tr('citeProofOk'))}</span>`
    : `<span class="perma-proof fail" title="${escAttr(embeddedPrefix)} ≠ ${escAttr(curTreeHash.slice(0, 16))}">${escHtml(tr('citeProofFail'))}</span>`;
  slot.insertAdjacentHTML('beforeend', ' ' + badge);
}

window.addEventListener('hashchange', () => {
  if (suppressHashUpdate) return;
  const pl = parseHash();
  if (!pl) return;
  if (pl.statute !== currentStatuteId) { statuteSel.value = pl.statute; loadStatute(pl.statute, pl); return; }
  applyPermalink(pl);
});
