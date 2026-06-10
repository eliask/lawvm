// statute-timeline.js — Standalone viewer for LawVM "certified transition graph" exports.
//
// Loads a per-statute SQLite DB (schema transition-graph.v1) via sql.js (CDN),
// folds the certified L3 transitions in the browser to reconstruct the full
// statute tree at any change-date, and self-verifies by recomputing the
// reproducible tree hash and asserting it equals checkpoints.tree_hash — the
// hash authored by the Python LawVM engine. Ported from exp1_certified_reducer.mjs.
//
// What the hash verification proves (and what it does NOT):
//   PROVEN here: the structure rendered in the browser == the structure the
//     engine computed (browser fold tree-hash == engine checkpoint tree-hash).
//   NOT claimed: that the engine matches Finlex's consolidation, nor that either
//     matches enacted law. Those are separate layers, tested engine-side.
//
// The view is "voimassaolon mukaan" — the law in force on the selected effective
// date. Repealed/removed provisions render as muted tombstones ("[kumottu]"),
// never silently vanish.
//
// Granularity-agnostic: drives off target_address depth and the node tree, never
// hardcodes "chapter". Real provision addresses are derived from each node's own
// num/label (never from positional counters), so non-contiguous §§ (104 a §,
// repealed gaps) address correctly. The current export records transitions at
// chapter granularity; a section/subsection-grained export needs no code change.

let db = null;          // sql.js Database
let blobCache = {};     // content_hash -> parsed IRNode (decoded JSON)
let transitions = [];   // all transitions, sequence-ordered
let checkpointByDate = {}; // date -> {tree_hash, active_node_count}
let changeDates = [];   // sorted ISO date strings
let sourceById = {};    // source_id -> source_artifacts row
let selectedAddress = null; // currently selected node address (for detail pane)
let mode = 'oikeustila';    // 'oikeustila' | 'muutokset' | 'haku'
let selectedSourceId = null; // amendment selected in Muutokset mode
let currentStatuteId = null;
let suppressHashUpdate = false; // guard while applying a permalink
const textDecoder = new TextDecoder('utf-8');

// ---- sql.js helpers (mirrors he-viewer q/q1 idiom) ----
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

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
function escAttr(s) { return escHtml(s).replace(/"/g, '&quot;'); }

// ---- content blob decoding ----
// content_json is stored as a BLOB; sql.js returns it as a Uint8Array.
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

// ---- certified fold (port of exp1_certified_reducer.mjs) ----
// Build the live covering set (address -> subtree content_hash) by applying all
// transitions with effective_date <= D, in sequence order.
//
// NOTE on expires_date: the engine encodes temporal reversion (a prior version
// resurfacing when a temporary amendment lapses) as EXPLICIT engine-authored
// transitions, not as expires_date rows (0 such rows exist in current exports).
// A silent expires_date delete here would render WRONG LAW (deletion instead of
// reversion). So we FAIL LOUDLY if any expires_date row is ever encountered,
// rather than silently mis-fold. (Fail-loudly discipline.)
function foldAt(date) {
  const live = new Map();       // address -> subtree content_hash (currently in force)
  const tombstoned = new Map(); // address -> {date, source_id, he_ref} for removed units
  const failures = [];
  for (const t of transitions) {
    if (t.effective_date > date) break;            // sequence-ordered by date
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
      tombstoned.delete(t.target_address); // resurrected
    }
  }
  return { live, tombstoned, failures };
}

// Reproducible tree hash over the covering set — same recipe as the engine:
// sort by address; for each: sha256.update(addr) + 0x00 + update(subtree_hash) + 0x01.
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

// ---- statute selection / loading ----
const statuteSel = document.getElementById('statute-select');
let manifest = [];

fetch('statute-timeline-manifest.json').then(r => r.json()).then(m => {
  manifest = m;
  statuteSel.innerHTML = '<option value="">Valitse säädös…</option>';
  for (const s of manifest) {
    const opt = document.createElement('option');
    opt.value = s.statute_id;
    opt.textContent = `${s.statute_id} — ${s.title} (${s.change_count} muutospäivää)`;
    statuteSel.appendChild(opt);
  }
  const initial = parseHash();
  const wanted = initial && manifest.find(s => s.statute_id === initial.statute) ? initial.statute
    : (manifest.length ? manifest[0].statute_id : null);
  if (wanted) { statuteSel.value = wanted; loadStatute(wanted, initial); }
}).catch(e => {
  document.getElementById('app').innerHTML = `<p class="error-box">Manifestia ei voitu ladata: ${escHtml(e.message)}</p>`;
});

statuteSel.addEventListener('change', () => { if (statuteSel.value) loadStatute(statuteSel.value); });

async function loadStatute(statuteId, permalink) {
  const app = document.getElementById('app');
  app.innerHTML = '<p class="loading">Ladataan säädöstä…</p>';
  const entry = manifest.find(s => s.statute_id === statuteId);
  if (!entry) { app.innerHTML = '<p class="error-box">Säädöstä ei löydy manifestista.</p>'; return; }
  currentStatuteId = statuteId;

  try {
    const SQL = await initSqlJs({ locateFile: f => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.13.0/${f}` });
    const resp = await fetch(entry.db);
    if (!resp.ok) throw new Error(`HTTP ${resp.status} (${entry.db})`);
    const buf = await resp.arrayBuffer();
    db = new SQL.Database(new Uint8Array(buf));
    blobCache = {}; selectedAddress = null; selectedSourceId = null;

    // Load core tables into memory
    transitions = q('SELECT * FROM transitions ORDER BY sequence ASC');
    checkpointByDate = {};
    for (const c of q('SELECT date, tree_hash, active_node_count FROM checkpoints')) {
      checkpointByDate[c.date] = c;
    }
    sourceById = {};
    for (const s of q('SELECT * FROM source_artifacts')) sourceById[s.source_id] = s;

    const cdRow = q1("SELECT value FROM meta WHERE key='change_dates'");
    changeDates = cdRow ? JSON.parse(cdRow.value) : Object.keys(checkpointByDate).sort();

    renderShell(entry);

    // Apply a permalink if present and addressed to this statute; else latest date.
    if (permalink && permalink.statute === statuteId) {
      applyPermalink(permalink);
    } else {
      await selectDate(changeDates.length - 1); // default: latest
    }
  } catch (e) {
    app.innerHTML = `<p class="error-box">Virhe ladattaessa: ${escHtml(e.message)}</p>`;
    console.error(e);
  }
}

// ---- shell render (mode bar + scrubber + 3-column layout) ----
function renderShell(entry) {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="mode-bar">
      <button class="mode-btn" data-mode="oikeustila">Oikeustila</button>
      <button class="mode-btn" data-mode="muutokset">Muutokset</button>
      <button class="mode-btn" data-mode="haku">Diakroninen haku</button>
      <span class="mode-hint" id="mode-hint"></span>
    </div>
    <div class="scrubber" id="scrubber">
      <div class="scrubber-top">
        <div class="oikeustila">Oikeustila <span class="date" id="sel-date">—</span>
          <span class="validity" id="validity"></span></div>
        <div id="verify-slot"><span class="verify-badge verify-pending">Todennetaan…</span></div>
      </div>
      <input type="range" id="date-slider" min="0" max="${changeDates.length - 1}" value="${changeDates.length - 1}" step="1">
      <div class="date-ticks"><span>${escHtml(changeDates[0])}</span><span>${escHtml(changeDates[changeDates.length - 1])}</span></div>
      <div class="date-nav">
        <button id="prev-date">‹ Edellinen</button>
        <button id="next-date">Seuraava ›</button>
        <select id="date-jump">${changeDates.map((d, i) => `<option value="${i}">${escHtml(d)}</option>`).join('')}</select>
        <span class="date-meta" id="date-meta"></span>
      </div>
    </div>
    <div class="view" id="view"></div>`;

  for (const b of app.querySelectorAll('.mode-btn')) {
    b.addEventListener('click', () => setMode(b.dataset.mode));
  }
  const slider = document.getElementById('date-slider');
  slider.addEventListener('input', () => selectDate(parseInt(slider.value, 10)));
  document.getElementById('prev-date').addEventListener('click', () => selectDate(Math.max(0, curDateIdx - 1)));
  document.getElementById('next-date').addEventListener('click', () => selectDate(Math.min(changeDates.length - 1, curDateIdx + 1)));
  document.getElementById('date-jump').addEventListener('change', (e) => selectDate(parseInt(e.target.value, 10)));

  setMode('oikeustila', /*skipRender*/ true);
}

function setMode(m, skipRender) {
  mode = m;
  for (const b of document.querySelectorAll('.mode-btn')) {
    b.classList.toggle('active', b.dataset.mode === m);
  }
  const scrubber = document.getElementById('scrubber');
  const hint = document.getElementById('mode-hint');
  scrubber.style.display = (m === 'oikeustila') ? '' : 'none';
  if (m === 'oikeustila') {
    hint.textContent = 'Lain rakenne voimassaolon mukaan valittuna päivänä, hash-todennettuna moottoria vastaan.';
    if (!skipRender) renderOikeustila();
  } else if (m === 'muutokset') {
    hint.textContent = 'Mitä kukin muutossäädös konkreettisesti teki — ennen/jälkeen jokaiselle kohdalle.';
    if (!skipRender) renderMuutokset();
  } else {
    hint.textContent = 'Hae tekstiä koko lain historiasta: milloin sanonta tuli lakiin ja millä muutossäädöksellä.';
    if (!skipRender) renderHaku();
  }
  if (!suppressHashUpdate) updateHash();
}

// ---- date selection (Oikeustila mode) ----
let curDateIdx = -1;
let curLive = new Map();
let curTombstoned = new Map();
let prevLive = new Map();        // covering set at the previous change-date
let changedAddrs = new Set();    // chapter-level addresses whose subtree changed

// Validity interval: the selected state holds in force from changeDates[idx]
// until the day before the next change-date (or open-ended "—").
function validityInterval(idx) {
  const start = changeDates[idx];
  const next = changeDates[idx + 1];
  return { start, end: next || null };
}

async function selectDate(idx, opts) {
  curDateIdx = idx;
  const date = changeDates[idx];
  document.getElementById('sel-date').textContent = date;
  document.getElementById('date-slider').value = idx;
  document.getElementById('date-jump').value = idx;

  const vi = validityInterval(idx);
  const vEl = document.getElementById('validity');
  if (vEl) vEl.textContent = `voimassa ${vi.start}–${vi.end || '—'}`;

  let live, tombstoned, failures;
  try {
    ({ live, tombstoned, failures } = foldAt(date));
  } catch (e) {
    // expires_date or other loud fold failure — show it, do NOT render wrong law.
    const slot = document.getElementById('verify-slot');
    if (slot) slot.innerHTML = `<span class="verify-badge verify-fail">✗ Taittovirhe — ei renderöidä</span>`;
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
    if (!live.has(addr)) changedAddrs.add(addr); // removed
  }

  // Self-verification against the engine oracle
  const cp = checkpointByDate[date];
  const got = await reproducibleTreeHash(live);
  curTreeHash = got;
  const slot = document.getElementById('verify-slot');
  const expected = cp ? cp.tree_hash : null;
  if (expected && got === expected && failures.length === 0) {
    slot.innerHTML = verifyBadgeHtml(got, true);
  } else {
    const reason = failures.length ? `${failures.length} pre/post-poikkeamaa` : 'tree-hash ≠ moottorin checkpoint';
    slot.innerHTML = `<span class="verify-badge verify-fail" title="${escAttr(reason)}">✗ Ei täsmää moottoriin — ${escHtml(reason)}</span>`
      + `<span class="verify-hash">${escHtml(got.slice(0, 12))}… vs ${escHtml((expected || '—').slice(0, 12))}…</span>`;
    console.warn('VERIFY FAIL', date, { got, expected, failures });
  }

  const meta = document.getElementById('date-meta');
  const changedCount = [...changedAddrs].filter(a => live.has(a)).length;
  if (meta) {
    meta.textContent = `${live.size} ${live.size === 1 ? 'ylätason yksikkö' : 'ylätason yksikköä'} · `
      + (idx === 0 ? 'alkuperäinen säädös' : `${changedCount} muuttunutta tänä päivänä`)
      + ` · muutospäivä ${idx + 1}/${changeDates.length}`;
  }

  if (mode === 'oikeustila' && !(opts && opts.skipRender)) renderOikeustila();
  if (!suppressHashUpdate) updateHash();
}

let curTreeHash = '';

// Reworded verify badge — claims EXACTLY render==engine, with an info popover
// stating what is and is not proven. Precise modesty = credibility for a faculty.
function verifyBadgeHtml(treeHash, ok) {
  const tip = 'Selaimessa laskettu rakenne täsmää moottorin checkpoint-hashiin (SHA-256). '
    + 'Tämä todistaa: näkymä = moottorin laskema tila. '
    + 'Tämä EI väitä: moottori = Finlexin konsolidointi, eikä että jompikumpi = voimassa oleva oikeus.';
  return `<span class="verify-badge verify-ok" title="${escAttr(tip)}">✓ Näkymä vastaa LawVM-moottoria</span>`
    + `<span class="verify-info" tabindex="0" role="button" aria-label="Mitä todennus tarkoittaa" title="${escAttr(tip)}">ⓘ</span>`
    + `<span class="verify-hash">tree ${escHtml(treeHash.slice(0, 12))}…</span>`;
}

// =====================================================================
// Oikeustila (point-in-time structure / document) view
// =====================================================================
const KIND_FI = {
  chapter: 'luku', section: 'pykälä', subsection: 'momentti', paragraph: 'kohta',
  subparagraph: 'alakohta', heading: 'otsikko', crossHeading: 'väliotsikko',
  num: 'numero', content: 'teksti', intro: 'johdanto', wrapUp: 'lopetus',
};
// Address-bearing structural kinds (mirror target_address style chapter:N/section:M/...)
const ADDR_SEG = {
  chapter: 'chapter', section: 'section', subsection: 'subsection',
  paragraph: 'paragraph', subparagraph: 'subparagraph',
};
// Kinds that get their own collapsible row / structural badge.
const CONTAINER_KINDS = new Set(['chapter', 'section', 'subsection']);

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
// label/num — NEVER from positional position among siblings. §§ are
// non-contiguous (104 a §, repeals leave gaps); a positional counter would
// produce wrong addresses the moment any deep-address join is attempted.
//   label "104a" -> "104a";  num "104 a §" -> "104a";  fallback: ordinal.
function addrComponent(node, ordinal) {
  const lbl = node.label != null ? String(node.label).trim() : '';
  if (lbl) return lbl.replace(/\s+/g, '');
  const num = nodeNum(node);
  if (num) {
    // strip trailing legal markers (§, ), .) and collapse internal spaces.
    const cleaned = num.replace(/[§).]/g, '').replace(/luku/gi, '').trim().replace(/\s+/g, '');
    if (cleaned) return cleaned;
  }
  return String(ordinal);
}

// Clean per-kind label for a structural node (granularity-agnostic):
//   chapter   -> "N luku"   (+ heading rendered separately)
//   section   -> "N §"      (+ heading rendered separately)
//   subsection-> "N mom."
//   paragraph -> "N)"
function kindLabel(node, ordinal) {
  const kind = node.kind;
  const num = nodeNum(node);            // printed form like "3 §", "1)", "1 luku"
  const lbl = (node.label || '').toString().trim();
  if (kind === 'chapter') return num || (lbl ? `${lbl} luku` : 'luku');
  if (kind === 'section') return num || (lbl ? `${lbl} §` : '§');
  if (kind === 'subsection') return `${lbl || ordinal} mom.`;
  if (kind === 'paragraph' || kind === 'subparagraph') return num || (lbl ? `${lbl})` : `${ordinal})`);
  return num || lbl || (KIND_FI[kind] || kind);
}

// Collect the structural (address-bearing) children of a node, assigning each a
// REAL child-address segment (from label/num) and a same-kind ordinal (for label
// fallback only).
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

// Inline non-structural content of a node (intro text, content paragraphs, wrapUp,
// crossHeading) in document order. Does NOT recurse into structural children.
function inlineContent(node) {
  const out = [];
  for (const c of (node.children || [])) {
    if (ADDR_SEG[c.kind]) continue;          // structural -> own row
    if (c.kind === 'num' || c.kind === 'heading') continue; // shown in the row label
    const txt = (c.text || '').trim();
    if (c.kind === 'content' && txt) out.push({ kind: 'content', text: txt });
    else if (c.kind === 'intro' && txt) out.push({ kind: 'intro', text: txt });
    else if (c.kind === 'wrapUp' && txt) out.push({ kind: 'wrapUp', text: txt });
    else if (c.kind === 'crossHeading' && txt) out.push({ kind: 'crossHeading', text: txt });
    else if (txt) out.push({ kind: c.kind, text: txt });
  }
  if (node.text && node.text.trim()) out.unshift({ kind: node.kind, text: node.text.trim() });
  return out;
}

// Canonical text fingerprint of a subtree, used to detect which provisions
// changed between two dates (works on the node tree, granularity-agnostic).
function subtreeFingerprint(node) {
  if (!node) return '';
  const parts = [];
  (function walk(n) {
    parts.push(n.kind || '', '|', (n.label || ''), '|', (n.text || '').trim(), '\n');
    for (const c of (n.children || [])) walk(c);
  })(node);
  return parts.join('');
}

// Map of childAddr -> child node from the previous date's tree, for change marking.
function prevChildMap(addr) {
  const top = addr.split('/')[0];
  const node = prevLive.has(top) ? getBlob(prevLive.get(top)) : null;
  const map = new Map();
  if (!node) return map;
  (function index(n, a) {
    map.set(a, n);
    for (const { child, childAddr } of structChildren(n, a)) index(child, childAddr);
  })(node, top);
  return map;
}

let expandState = 'default'; // 'default' | 'all' | 'none'

function renderOikeustila() {
  const view = document.getElementById('view');
  const titleRow = q1("SELECT value FROM meta WHERE key='title'");
  const title = titleRow ? JSON.parse(titleRow.value) : (manifest.find(s => s.statute_id) || {}).title || '';
  view.innerHTML = `
    <div class="layout3">
      <aside class="col-toc panel" id="toc-panel">
        <h2 class="panel-title">Sisällys</h2>
        <input type="search" id="toc-filter" class="toc-filter" placeholder="Hyppää § / luku…" autocomplete="off">
        <nav class="toc" id="toc"></nav>
      </aside>
      <section class="col-main panel">
        <div class="panel-head">
          <h2 class="panel-title">${escHtml(title)}</h2>
          <div class="tree-tools">
            <button id="expand-all">Laajenna kaikki</button>
            <button id="collapse-all">Sulje kaikki</button>
          </div>
        </div>
        <div class="tree-legend">
          <span class="leg-changed">▍</span> muuttunut edelliseen muutospäivään verrattuna
          <span class="leg-tomb">[kumottu]</span> tällä päivällä poistettu/kumottu yksikkö
        </div>
        <div class="doc" id="doc"></div>
      </section>
      <aside class="col-detail panel" id="detail-panel">
        <h2 class="panel-title">Versiohistoria <span class="detail-pin" title="Paneeli pysyy valinnassa">📌</span></h2>
        <div id="detail"><p class="detail-empty">Valitse pykälä tai luku tekstistä nähdäksesi sen muutoshistorian, lähteen ja esitöiden viitteet.</p></div>
      </aside>
    </div>`;
  document.getElementById('expand-all').addEventListener('click', () => { expandState = 'all'; renderDoc(); buildToc(); });
  document.getElementById('collapse-all').addEventListener('click', () => { expandState = 'none'; renderDoc(); buildToc(); });
  const tf = document.getElementById('toc-filter');
  tf.addEventListener('input', () => filterToc(tf.value));
  tf.addEventListener('keydown', (e) => { if (e.key === 'Enter') jumpFirstTocMatch(); });
  renderDoc();
  buildToc();
  if (selectedAddress) { renderDetail(selectedAddress); highlightSelected(selectedAddress); }
}

// ---- document render (readable prose, collapsible, Ctrl-F safe) ----
function renderDoc() {
  const docEl = document.getElementById('doc');
  if (!docEl) return;
  const live = curLive;
  const addrs = [...live.keys()].sort(addrCompare);

  let html = '';
  for (const addr of addrs) {
    const node = getBlob(live.get(addr));
    if (!node) continue;
    const prevMap = prevChildMap(addr);
    html += renderNode(node, addr, 0, prevMap);
  }
  // Tombstones for top-level units removed at this date (never silently vanish).
  for (const [addr, info] of curTombstoned) {
    if (live.has(addr)) continue;
    if (addr.includes('/')) continue; // only top-level here
    html += tombstoneHtml(addr, info);
  }
  docEl.innerHTML = html || '<p class="detail-empty">Ei voimassa olevia säännöksiä tällä päivällä.</p>';

  docEl.querySelectorAll('.node-toggle:not(.leaf)').forEach(t => {
    t.addEventListener('click', (e) => {
      e.stopPropagation();
      const node = t.closest('.node');
      const collapsing = !node.classList.contains('collapsed');
      node.classList.toggle('collapsed', collapsing);
      t.textContent = collapsing ? '▸' : '▾';
      const body = node.querySelector(':scope > .node-body');
      if (body) setBodyHidden(body, collapsing);
    });
  });
  docEl.querySelectorAll('.node-row').forEach(r => {
    r.addEventListener('click', (e) => {
      if (e.target.closest('.node-toggle')) return;
      selectAddress(r.dataset.addr);
    });
  });
}

// hidden="until-found" lets native browser find (Ctrl-F) reveal matches inside
// collapsed bodies (Chromium fires beforematch + auto-expands). Where it isn't
// supported the attribute is ignored and content stays in the DOM/visible via CSS.
function setBodyHidden(el, hidden) {
  if (hidden) el.setAttribute('hidden', 'until-found');
  else el.removeAttribute('hidden');
}

// When the browser reveals a hidden body via find-in-page, expand its ancestors
// so the match isn't clipped by collapsed CSS.
document.addEventListener('beforematch', (e) => {
  let el = e.target;
  while (el && el !== document.body) {
    if (el.classList && el.classList.contains('node')) {
      el.classList.remove('collapsed');
      const tog = el.querySelector(':scope > .node-row > .node-toggle');
      if (tog && !tog.classList.contains('leaf')) tog.textContent = '▾';
    }
    if (el.hasAttribute && el.hasAttribute('hidden')) el.removeAttribute('hidden');
    el = el.parentElement;
  }
});

function addrCompare(a, b) {
  const sa = a.split('/'), sb = b.split('/');
  for (let i = 0; i < Math.max(sa.length, sb.length); i++) {
    const ca = (sa[i] || '').split(':')[1] || '';
    const cb = (sb[i] || '').split(':')[1] || '';
    const na = parseInt(ca, 10), nb = parseInt(cb, 10);
    if (na !== nb) return (isNaN(na) ? 0 : na) - (isNaN(nb) ? 0 : nb);
    if (ca !== cb) return ca < cb ? -1 : 1; // tiebreak on suffix (104 vs 104a)
  }
  return 0;
}

// Whether a container is collapsed by default. Default: chapters+sections open,
// deeper (momentti) collapsed so text is reachable but not a wall.
function defaultCollapsed(kind, depth) {
  if (expandState === 'all') return false;
  if (expandState === 'none') return depth > 0;
  return depth >= 2;
}

function renderNode(node, addr, depth, prevMap) {
  const kind = node.kind;
  const heading = nodeHeading(node);
  const children = structChildren(node, addr);
  const inline = inlineContent(node);
  const hasChildren = children.length > 0;
  const isContainer = CONTAINER_KINDS.has(kind);
  const collapsible = hasChildren || inline.length > 0;

  // Change detection vs previous date.
  const prevNode = prevMap.get(addr);
  let changed = false;
  if (curDateIdx > 0) {
    if (!prevNode) changed = true;
    else changed = subtreeFingerprint(node) !== subtreeFingerprint(prevNode);
  }

  const kindCls = `kind-${kind}`;
  const ordinal = parseInt((addr.split('/').pop() || '').split(':')[1] || '0', 10);
  const label = kindLabel(node, ordinal);

  const collapsed = collapsible && defaultCollapsed(kind, depth);
  const changedCls = changed ? ' changed' : '';

  let html = `<div class="node${collapsed ? ' collapsed' : ''}${changedCls}" data-depth="${depth}" data-addr="${escAttr(addr)}">`;
  html += `<div class="node-row" data-addr="${escAttr(addr)}">`;
  html += `<span class="node-toggle ${collapsible ? '' : 'leaf'}">${collapsible ? (collapsed ? '▸' : '▾') : ''}</span>`;
  if (isContainer || kind === 'paragraph' || kind === 'subparagraph') {
    html += `<span class="kind-badge ${kindCls}">${escHtml(KIND_FI[kind] || kind)}</span>`;
  }
  html += `<span class="node-label">${escHtml(label)}</span>`;
  if (heading) html += `<span class="node-heading">${escHtml(heading)}</span>`;
  if (changed) html += `<span class="changed-tag">muuttunut</span>`;
  html += `</div>`;

  if (collapsible) {
    const hiddenAttr = collapsed ? ' hidden="until-found"' : '';
    html += `<div class="node-body"${hiddenAttr}>`;
    for (const seg of inline) {
      const cls = seg.kind === 'crossHeading' ? 'crossheading'
        : seg.kind === 'intro' ? 'intro'
        : seg.kind === 'wrapUp' ? 'wrapup' : 'content';
      html += `<p class="prov-text ${cls}">${escHtml(seg.text)}</p>`;
    }
    if (hasChildren) {
      for (const { child, childAddr } of children) {
        html += renderNode(child, childAddr, depth + 1, prevMap);
      }
    }
    html += `</div>`;
  }
  html += '</div>';
  return html;
}

function tombstoneHtml(addr, info) {
  const src = info && info.source_id ? sourceById[info.source_id] : null;
  const srcLabel = src ? (src.canonical_id || src.title || info.source_id) : (info ? info.source_id : '');
  return `<div class="node tombstone" data-addr="${escAttr(addr)}">`
    + `<div class="node-row" data-addr="${escAttr(addr)}">`
    + `<span class="node-toggle leaf"></span>`
    + `<span class="tomb-label">${escHtml(prettyAddr(addr))} <em>[kumottu]</em></span>`
    + (info && info.date ? `<span class="tomb-meta">${escHtml(info.date)}${srcLabel ? ' · ' + escHtml(srcLabel) : ''}</span>` : '')
    + `</div></div>`;
}

function selectAddress(addr) {
  selectedAddress = addr;
  highlightSelected(addr);
  renderDetail(addr);
  if (!suppressHashUpdate) updateHash();
}
function highlightSelected(addr) {
  document.querySelectorAll('.node-row.selected').forEach(x => x.classList.remove('selected'));
  const row = document.querySelector(`.node-row[data-addr="${cssEsc(addr)}"]`);
  if (row) row.classList.add('selected');
}
function cssEsc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'); }

// ---- TOC (sticky, section-level jump) ----
function buildToc() {
  const tocEl = document.getElementById('toc');
  if (!tocEl) return;
  const addrs = [...curLive.keys()].sort(addrCompare);
  let html = '<ul class="toc-list">';
  for (const addr of addrs) {
    const node = getBlob(curLive.get(addr));
    if (!node) continue;
    const chHeading = nodeHeading(node);
    const chLabel = kindLabel(node, 0);
    const chChanged = changedAddrs.has(addr);
    html += `<li class="toc-chapter">`
      + `<a href="#" class="toc-link toc-ch${chChanged ? ' ch-changed' : ''}" data-addr="${escAttr(addr)}">`
      + `<span class="toc-num">${escHtml(chLabel)}</span> <span class="toc-h">${escHtml(chHeading)}</span></a>`;
    // section-level entries
    const secs = structChildren(node, addr).filter(s => s.child.kind === 'section');
    if (secs.length) {
      html += '<ul class="toc-sections">';
      for (const { child, childAddr } of secs) {
        const sLabel = kindLabel(child, 0);
        const sHeading = nodeHeading(child);
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
}

function jumpToAddr(addr) {
  // expand ancestors so the target is visible
  const segs = addr.split('/');
  for (let i = 1; i <= segs.length; i++) {
    const a = segs.slice(0, i).join('/');
    const n = document.querySelector(`.node[data-addr="${cssEsc(a)}"]`);
    if (n && n.classList.contains('collapsed')) {
      n.classList.remove('collapsed');
      const tog = n.querySelector(':scope > .node-row > .node-toggle');
      if (tog && !tog.classList.contains('leaf')) tog.textContent = '▾';
      const body = n.querySelector(':scope > .node-body');
      if (body) body.removeAttribute('hidden');
    }
  }
  const row = document.querySelector(`.node-row[data-addr="${cssEsc(addr)}"]`);
  if (row) {
    row.scrollIntoView({ behavior: 'smooth', block: 'start' });
    selectAddress(addr);
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
    .find(a => a.closest('li,.toc-chapter') && a.offsetParent !== null);
  if (first) jumpToAddr(first.dataset.addr);
}

// =====================================================================
// Versiohistoria detail pane (Oikeustila mode) — provenance + word-diff
// =====================================================================
function renderDetail(address) {
  const el = document.getElementById('detail');
  if (!el) return;
  // The clicked address may be deeper than the granularity at which transitions
  // are recorded. Walk up until we find recorded transitions.
  const matchAddr = nearestRecordedAddress(address);
  // Group by effective_date — same-day intermediate states never legally existed,
  // so we present one entry per date (sequence is internal tiebreak only).
  const histRaw = transitions
    .filter(t => t.target_address === matchAddr)
    .sort((a, b) => (a.effective_date < b.effective_date ? -1
      : a.effective_date > b.effective_date ? 1 : a.sequence - b.sequence));
  const byDate = new Map();
  for (const t of histRaw) {
    if (!byDate.has(t.effective_date)) byDate.set(t.effective_date, []);
    byDate.get(t.effective_date).push(t);
  }

  const curDate = changeDates[curDateIdx];
  let html = `<div class="detail-head">${escHtml(prettyAddr(address))}</div>`;
  html += `<div class="detail-addr">${escHtml(address)}</div>`;
  html += `<div class="detail-cite">`
    + `<button id="copy-cite" class="cite-btn" type="button">Kopioi viittaus</button>`
    + `<button id="copy-link" class="cite-btn" type="button">Kopioi pysyvä linkki</button>`
    + `<span class="cite-status" id="cite-status"></span></div>`;
  if (matchAddr !== address) {
    html += `<p class="detail-note">Muutokset on kirjattu osoitteen <strong>${escHtml(prettyAddr(matchAddr))}</strong> tarkkuudella; kohdistus §-tasolle tulossa tarkemmalla viennillä. Näytetään luvun muutoshistoria.</p>`;
  }

  if (!byDate.size) { html += '<p class="detail-empty">Ei kirjattuja muutoksia.</p>'; el.innerHTML = html; wireDetail(el, address); return; }

  for (const [date, ts] of [...byDate.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
    const isFuture = date > curDate;
    const applies = date <= curDate;
    const cls = isFuture ? 'future' : (applies ? 'applies' : '');
    // representative transition = last in sequence that day (the net day-end state)
    const t = ts[ts.length - 1];
    const first = ts[0];
    html += `<div class="change ${cls}">`;
    html += `<div class="change-date">Voimaantulo ${escHtml(date)}`;
    if (isFuture) html += `<span class="future-tag">tuleva muutos</span>`;
    html += `</div>`;
    if (t.legal_op_kind || ts.some(x => x.legal_op_kind)) html += `<div class="change-op">${opKindBadges(ts)}</div>`;
    const summary = ts.map(x => x.legal_op_summary).filter(Boolean).join(' ');
    if (summary) html += `<div class="change-summary">${escHtml(summary)}</div>`;
    html += provenanceHtml(t);
    // diff pre of the first that day -> post of the last that day (net day change)
    if (first.pre_hash || t.payload_hash || t.post_hash) html += diffDetails(first.pre_hash, t.post_hash || t.payload_hash);
    html += `</div>`;
  }
  el.innerHTML = html;
  wireDiffDetails(el);
  wireDetail(el, address);
}

function wireDetail(el, address) {
  const cs = el.querySelector('#cite-status');
  const cb = el.querySelector('#copy-cite');
  const cl = el.querySelector('#copy-link');
  if (cb) cb.addEventListener('click', () => copyToClip(citationText(address), cs, 'Viittaus kopioitu'));
  if (cl) cl.addEventListener('click', () => copyToClip(permalinkUrl(address), cs, 'Linkki kopioitu'));
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

// Formatted footnote-style citation: statute, §, date, säädös refs, permalink, hash.
function citationText(address) {
  const titleRow = q1("SELECT value FROM meta WHERE key='title'");
  const title = titleRow ? JSON.parse(titleRow.value) : '';
  const date = changeDates[curDateIdx];
  const vi = validityInterval(curDateIdx);
  const matchAddr = nearestRecordedAddress(address);
  const srcs = [...new Set(transitions.filter(t => t.target_address === matchAddr && t.source_id).map(t => {
    const s = sourceById[t.source_id]; return s ? (s.canonical_id || s.source_id) : t.source_id;
  }))];
  let out = `${title} (${currentStatuteId}), ${prettyAddr(address)}, voimassa ${vi.start}–${vi.end || '—'}.`;
  if (srcs.length) out += ` Muutossäädökset: ${srcs.join(', ')}.`;
  out += `\nLawVM tree-hash: ${curTreeHash.slice(0, 16)}… (todennettu).`;
  out += `\n${permalinkUrl(address)}`;
  return out;
}

// Find the nearest ancestor (incl. self) of `address` that has recorded transitions.
function nearestRecordedAddress(address) {
  const recorded = new Set(transitions.map(t => t.target_address));
  const segs = address.split('/');
  for (let i = segs.length; i >= 1; i--) {
    const a = segs.slice(0, i).join('/');
    if (recorded.has(a)) return a;
  }
  return segs[0];
}

// Split + translate raw legal_op_kind tokens (insert,replace -> lisätty, muutettu).
const OP_KIND_FI = { insert: 'lisätty', replace: 'muutettu', repeal: 'kumottu', delete: 'poistettu', move: 'siirretty' };
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
    // 17 transitions have empty kind — fail-loudly: explicit fallback, not blank.
    return `<span class="op-kind op-unknown" title="Muutoslaji ei kirjattu lähteessä">muutos (laji kirjaamatta)</span>`;
  }
  return [...kinds].map(k => `<span class="op-kind">${escHtml(OP_KIND_FI[k] || k)}</span>`).join(' ');
}

// Linkify HE reference to a Finlex/eduskunta search.
function heRefHtml(heRef) {
  if (!heRef) return '';
  const url = 'https://www.eduskunta.fi/FI/search/Sivut/vaskiresults.aspx?k=' + encodeURIComponent(heRef);
  return `<a href="${escAttr(url)}" target="_blank" rel="noopener">${escHtml(heRef)} ↗</a>`;
}

function provenanceHtml(t) {
  const src = sourceById[t.source_id];
  if (!src && !t.he_ref && !t.source_id) return '';
  let html = `<div class="provenance">`;
  if (src) {
    html += `<div><span class="lbl">Muutossäädös:</span> `;
    if (src.url) html += `<a href="${escAttr(src.url)}" target="_blank" rel="noopener">${escHtml(src.title || src.canonical_id || t.source_id)}</a>`;
    else html += escHtml(src.title || src.canonical_id || t.source_id);
    if (src.canonical_id) html += ` (${escHtml(src.canonical_id)})`;
    if (src.date) html += ` <span class="ann-date">annettu ${escHtml(src.date)}</span>`;
    html += `</div>`;
  } else if (t.source_id) {
    html += `<div><span class="lbl">Muutossäädös:</span> ${escHtml(t.source_id)}</div>`;
  }
  if (t.he_ref) html += `<div><span class="lbl">Esitöiden viite:</span> ${heRefHtml(t.he_ref)}</div>`;
  html += `</div>`;
  return html;
}

function prettyAddr(addr) {
  return addr.split('/').map(seg => {
    const [k, n] = seg.split(':');
    const fi = KIND_FI[k] || k;
    if (k === 'chapter') return `${n} luku`;
    if (k === 'section') return `${n} §`;
    if (k === 'subsection') return `${n} mom.`;
    return `${n} ${fi}`;
  }).join(' › ');
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

function renderMuutokset() {
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
      + `<div class="amend-meta">${escHtml(a.source_id)} · ${a.opCount} ${a.opCount === 1 ? 'kohdistus' : 'kohdistusta'}</div>`
      + `</li>`;
  }
  listHtml += '</ul>';

  view.innerHTML = `
    <div class="layout layout-amend">
      <div class="panel">
        <h2 class="panel-title">Muutossäädökset (${amendments.length})</h2>
        ${listHtml}
      </div>
      <div class="panel">
        <h2 class="panel-title">Mitä tämä säädös teki</h2>
        <div id="amend-detail"></div>
      </div>
    </div>`;

  for (const li of view.querySelectorAll('.amend-item')) {
    li.addEventListener('click', () => {
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
  html += `<span><span class="lbl">Säädös:</span> ${escHtml(sourceId)}</span>`;
  if (src && src.date) html += `<span><span class="lbl">Annettu:</span> ${escHtml(src.date)}</span>`;
  if (effectiveDates.length) {
    html += `<span><span class="lbl">Voimaantulo:</span> `
      + effectiveDates.map(d => {
        const i = changeDates.indexOf(d);
        return i >= 0 ? `<a href="#" class="jump-date" data-idx="${i}">${escHtml(d)}</a>` : escHtml(d);
      }).join(', ') + `</span>`;
  }
  const heRef = (ops.find(o => o.he_ref) || {}).he_ref;
  if (heRef) html += `<span><span class="lbl">Esitöiden viite:</span> ${heRefHtml(heRef)}</span>`;
  if (src && src.url) html += `<span><span class="lbl">Lähde:</span> <a href="${escAttr(src.url)}" target="_blank" rel="noopener">Finlex ↗</a></span>`;
  html += `</div></div>`;

  html += `<div class="op-list">`;
  for (const t of ops) {
    html += `<div class="op-row">`;
    html += `<div class="op-row-head">`;
    html += `${opKindBadges([t])}`;
    html += `<span class="op-addr">${escHtml(prettyAddr(t.target_address))}</span>`;
    html += `<span class="op-eff">${escHtml(t.effective_date)}</span>`;
    html += `</div>`;
    if (t.legal_op_summary) html += `<div class="op-summary">${escHtml(t.legal_op_summary)}</div>`;
    html += diffDetails(t.pre_hash, t.post_hash || t.payload_hash);
    html += `</div>`;
  }
  html += `</div>`;
  el.innerHTML = html;

  for (const a of el.querySelectorAll('.jump-date')) {
    a.addEventListener('click', (e) => { e.preventDefault(); setMode('oikeustila'); selectDate(parseInt(a.dataset.idx, 10)); });
  }
  wireDiffDetails(el);
}

// =====================================================================
// Diachronic phrase search — exact substring over ALL content_blobs ever.
// For each provision that ever contained the phrase, report its in-force
// intervals and which amendment INTRODUCED vs REMOVED the phrase.
// =====================================================================
let blobTextByHash = {};  // content_hash -> lowercased flat text (lazy, once)
let searchFoldCache = null; // date -> live Map, populated per search run

function renderHaku() {
  const view = document.getElementById('view');
  view.innerHTML = `
    <div class="haku-wrap panel">
      <h2 class="panel-title">Diakroninen haku — milloin sanonta tuli lakiin ja millä muutossäädöksellä</h2>
      <form id="haku-form" class="haku-form">
        <input type="search" id="haku-input" placeholder="esim. biometris, maasta poistaminen…" autocomplete="off">
        <button type="submit">Hae</button>
      </form>
      <p class="haku-note">Tarkka osamerkkijonohaku koko lain historiaan (kaikki versiot, ei vain valittu päivä).
        Tulos: kohta, voimassaolojaksot, ja se muutossäädös joka <strong>toi</strong> tai <strong>poisti</strong> sanonnan.
        Ei sumeaa hakua; isot/pienet kirjaimet samaistetaan.</p>
      <div id="haku-results"></div>
    </div>`;
  const form = document.getElementById('haku-form');
  const input = document.getElementById('haku-input');
  form.addEventListener('submit', (e) => { e.preventDefault(); runDiachronicSearch(input.value); });
  if (pendingSearchQuery) { input.value = pendingSearchQuery; runDiachronicSearch(pendingSearchQuery); pendingSearchQuery = null; }
}
let pendingSearchQuery = null;

// Flat lowercased text of a subtree, cached by content_hash.
function blobText(hash) {
  if (!hash) return '';
  if (hash in blobTextByHash) return blobTextByHash[hash];
  const node = getBlob(hash);
  const txt = node ? nodeToText(node).toLowerCase() : '';
  blobTextByHash[hash] = txt;
  return txt;
}

// For a given target_address, build its ordered version chain across transitions:
// [{date, source_id, he_ref, post_hash, hasPhrase}], using pre/post hashes.
function addressVersionChain(addr, phraseLc) {
  const ts = transitions.filter(t => t.target_address === addr)
    .sort((a, b) => (a.effective_date < b.effective_date ? -1
      : a.effective_date > b.effective_date ? 1 : a.sequence - b.sequence));
  const chain = [];
  for (const t of ts) {
    const post = t.post_hash || '';
    chain.push({
      date: t.effective_date, source_id: t.source_id, he_ref: t.he_ref,
      post_hash: post,
      hasPhrase: post ? blobText(post).includes(phraseLc) : false,
      removed: post === '' || t.action === 'delete_subtree' || t.action === 'tombstone',
    });
  }
  return chain;
}

let phraseLcGlobal = '';
function runDiachronicSearch(rawQuery) {
  const out = document.getElementById('haku-results');
  const phrase = (rawQuery || '').trim();
  if (!phrase) { out.innerHTML = '<p class="detail-empty">Anna hakusana.</p>'; return; }
  const phraseLc = phrase.toLowerCase().replace(/\s+/g, ' ');
  phraseLcGlobal = phraseLc; // used by inForcePhraseIntervals
  // Fold each change-date once and reuse across all matching addresses.
  searchFoldCache = {};
  for (const d of changeDates) searchFoldCache[d] = foldAt(d).live;

  // 1) which target_addresses ever contained the phrase (scan all post_hashes)?
  const matchAddrs = new Set();
  for (const t of transitions) {
    const h = t.post_hash;
    if (h && blobText(h).includes(phraseLc)) matchAddrs.add(t.target_address);
  }
  if (!matchAddrs.size) {
    out.innerHTML = `<p class="detail-empty">Ei osumia haulle “${escHtml(phrase)}” koko lain historiassa.</p>`;
    return;
  }

  // 2) for each matching address build the version chain + intervals + attribution.
  let html = `<p class="haku-count">${matchAddrs.size} ${matchAddrs.size === 1 ? 'kohta' : 'kohtaa'} sisälsi sanonnan “${escHtml(phrase)}” jossakin vaiheessa.</p>`;
  const sorted = [...matchAddrs].sort(addrCompare);
  for (const addr of sorted) {
    const chain = addressVersionChain(addr, phraseLc);
    // find introduce edges: version i-1 lacked phrase, version i has it.
    const introduced = [];
    const removed = [];
    let prevHas = false;
    for (let i = 0; i < chain.length; i++) {
      const v = chain[i];
      if (v.hasPhrase && !prevHas) introduced.push(v);
      if (!v.hasPhrase && prevHas) removed.push(v);
      prevHas = v.hasPhrase;
    }
    // in-force intervals where the phrase was present
    const intervals = inForcePhraseIntervals(addr, chain);

    html += `<div class="haku-hit">`;
    html += `<div class="haku-hit-head"><a href="#" class="haku-goto" data-addr="${escAttr(addr)}" data-date="">`
      + `${escHtml(prettyAddr(addr))}</a></div>`;
    if (intervals.length) {
      html += `<div class="haku-intervals"><span class="lbl">Voimassa sanonnan kanssa:</span> `
        + intervals.map(iv => `${escHtml(iv.start)}–${escHtml(iv.end || '—')}`).join(', ') + `</div>`;
    }
    for (const v of introduced) html += attributionRow('Toi sanonnan', v, addr);
    for (const v of removed) html += attributionRow('Poisti sanonnan', v, addr);
    // snippet from the latest version that has it (or first)
    const sample = [...chain].reverse().find(v => v.hasPhrase) || introduced[0];
    if (sample && sample.post_hash) {
      const snip = snippetAround(blobTextRaw(sample.post_hash), phraseLc);
      if (snip) html += `<div class="haku-snippet">…${escHtml(snip)}…</div>`;
    }
    html += `</div>`;
  }
  out.innerHTML = html;
  out.querySelectorAll('.haku-goto').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      goToAddrAtDate(a.dataset.addr, a.dataset.date);
    });
  });
}

function attributionRow(label, v, addr) {
  const src = v.source_id ? sourceById[v.source_id] : null;
  const srcLabel = src ? (src.title || src.canonical_id || v.source_id) : (v.source_id || 'alkuperäinen säädös');
  const idx = changeDates.indexOf(v.date);
  const dateLink = idx >= 0
    ? `<a href="#" class="haku-goto" data-addr="${escAttr(addr)}" data-date="${escAttr(v.date)}">${escHtml(v.date)}</a>`
    : escHtml(v.date);
  let html = `<div class="haku-attr"><span class="attr-label">${escHtml(label)}:</span> ${dateLink} — ${escHtml(srcLabel)}`;
  if (src && src.canonical_id) html += ` (${escHtml(src.canonical_id)})`;
  if (v.he_ref) html += ` · ${heRefHtml(v.he_ref)}`;
  html += `</div>`;
  return html;
}

// In-force intervals for `addr` restricted to versions containing the phrase.
// Walk change-dates; for each, fold gives the live hash at addr (chapter-level).
function inForcePhraseIntervals(addr, chain) {
  const intervals = [];
  let open = null;
  for (let i = 0; i < changeDates.length; i++) {
    const d = changeDates[i];
    const live = (searchFoldCache && searchFoldCache[d]) || foldAt(d).live;
    const h = live.get(addr);
    const present = h ? blobText(h).includes(phraseLcGlobal) : false;
    if (present && !open) open = d;
    if (!present && open) { intervals.push({ start: open, end: changeDates[i] }); open = null; }
  }
  if (open) intervals.push({ start: open, end: null });
  return intervals;
}

function blobTextRaw(hash) {
  const node = getBlob(hash);
  return node ? nodeToText(node) : '';
}
function snippetAround(text, phraseLc) {
  const lc = text.toLowerCase();
  const i = lc.indexOf(phraseLc);
  if (i < 0) return '';
  const start = Math.max(0, i - 60), end = Math.min(text.length, i + phraseLc.length + 60);
  return text.slice(start, end).replace(/\s+/g, ' ').trim();
}

function goToAddrAtDate(addr, date) {
  setMode('oikeustila', /*skipRender*/ true);
  const idx = date ? changeDates.indexOf(date) : -1;
  const targetIdx = idx >= 0 ? idx : curDateIdx >= 0 ? curDateIdx : changeDates.length - 1;
  selectedAddress = addr;
  selectDate(targetIdx).then(() => { jumpToAddr(addr); });
}

// =====================================================================
// Word-level diff (shared)
// =====================================================================
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

function diffDetails(preHash, postHash) {
  return `<details class="diff" data-pre="${escAttr(preHash || '')}" data-post="${escAttr(postHash || '')}">`
    + `<summary>Näytä ennen / jälkeen</summary>`
    + `<div class="diff-body"></div></details>`;
}

function wireDiffDetails(root) {
  root.querySelectorAll('details.diff').forEach(d => {
    d.addEventListener('toggle', () => {
      if (d.open && !d.dataset.rendered) {
        d.querySelector('.diff-body').innerHTML = buildDiff(d.dataset.pre, d.dataset.post);
        d.dataset.rendered = '1';
      }
    });
  });
}

function buildDiff(preHash, postHash) {
  const preTxt = nodeToText(preHash ? getBlob(preHash) : null);
  const postTxt = nodeToText(postHash ? getBlob(postHash) : null);
  if (!preTxt && !postTxt) return `<p class="diff-empty">Ei sisältöä vertailtavaksi.</p>`;
  if (!preTxt) {
    return `<div class="diff-cols">`
      + `<div class="diff-col"><h5>Ennen</h5><div class="diff-box pre"><em>(uusi sisältö — ei aiempaa versiota)</em></div></div>`
      + `<div class="diff-col"><h5>Jälkeen</h5><div class="diff-box post">${wordDiffHtml('', postTxt, 'new')}</div></div></div>`;
  }
  if (!postTxt) {
    return `<div class="diff-cols">`
      + `<div class="diff-col"><h5>Ennen</h5><div class="diff-box pre">${wordDiffHtml(preTxt, '', 'old')}</div></div>`
      + `<div class="diff-col"><h5>Jälkeen</h5><div class="diff-box post"><em>(poistettu — ei sisältöä)</em></div></div></div>`;
  }
  return `<div class="diff-cols">`
    + `<div class="diff-col"><h5>Ennen</h5><div class="diff-box pre">${wordDiffHtml(preTxt, postTxt, 'old')}</div></div>`
    + `<div class="diff-col"><h5>Jälkeen</h5><div class="diff-box post">${wordDiffHtml(preTxt, postTxt, 'new')}</div></div></div>`;
}

// Render old->new word diff for one side. When the input is too large for the
// O(nm) LCS we fall back to plain text — but say so explicitly (never silently
// present an un-highlighted diff as "identical").
function wordDiffHtml(oldTxt, newTxt, side) {
  const aw = oldTxt.split(/\s+/).filter(Boolean);
  const bw = newTxt.split(/\s+/).filter(Boolean);
  if (aw.length + bw.length > 4000) {
    const notice = `<div class="diff-toobig">Ero liian suuri sanatason korostukseen — näytetään korostamaton teksti.</div>`;
    return notice + escHtml(side === 'new' ? newTxt : oldTxt);
  }
  const ops = diffWords(aw, bw);
  let out = '';
  for (const [type, words] of ops) {
    const txt = escHtml(words.join(' '));
    if (type === 0) out += txt + ' ';
    else if (type === -1) { if (side === 'old') out += `<span class="diff-del">${txt}</span> `; }
    else { if (side === 'new') out += `<span class="diff-ins">${txt}</span> `; }
  }
  return out.trim();
}

function diffWords(a, b) {
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

// =====================================================================
// Hash-anchored permalinks — encode (statute, date, address, mode, tree-hash
// prefix) in the URL hash; on load re-fold, re-verify, deep-link, re-prove.
// =====================================================================
function updateHash() {
  if (!currentStatuteId || curDateIdx < 0) return;
  const params = new URLSearchParams();
  params.set('s', currentStatuteId);
  params.set('m', mode);
  if (mode === 'oikeustila') {
    params.set('d', changeDates[curDateIdx] || '');
    if (selectedAddress) params.set('a', selectedAddress);
    if (curTreeHash) params.set('h', curTreeHash.slice(0, 16));
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
  params.set('m', 'oikeustila');
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
    mode: params.get('m') || 'oikeustila',
    date: params.get('d') || null,
    address: params.get('a') || null,
    hashPrefix: params.get('h') || null,
  };
}

async function applyPermalink(pl) {
  suppressHashUpdate = true;
  try {
    if (pl.mode === 'muutokset') { setMode('muutokset'); return; }
    if (pl.mode === 'haku') { setMode('haku'); return; }
    setMode('oikeustila', /*skipRender*/ true);
    let idx = pl.date ? changeDates.indexOf(pl.date) : -1;
    if (idx < 0) idx = changeDates.length - 1;
    selectedAddress = pl.address || null;
    await selectDate(idx, { skipRender: true });
    renderOikeustila();
    // re-prove: compare embedded hash prefix to the freshly computed tree hash
    if (pl.hashPrefix) showPermalinkProof(pl.hashPrefix);
    if (pl.address) setTimeout(() => jumpToAddr(pl.address), 60);
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
    ? `<span class="perma-proof ok" title="Linkin tree-hash täsmää uudelleenlasketun tilan kanssa">sitaatti todennettu</span>`
    : `<span class="perma-proof fail" title="Linkin tree-hash ${escAttr(embeddedPrefix)} ≠ ${escAttr(curTreeHash.slice(0, 16))}">sitaatti EI täsmää</span>`;
  slot.insertAdjacentHTML('beforeend', ' ' + badge);
}

// React to back/forward navigation between permalinks.
window.addEventListener('hashchange', () => {
  if (suppressHashUpdate) return;
  const pl = parseHash();
  if (!pl) return;
  if (pl.statute !== currentStatuteId) { statuteSel.value = pl.statute; loadStatute(pl.statute, pl); return; }
  applyPermalink(pl);
});
