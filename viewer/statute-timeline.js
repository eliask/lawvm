// statute-timeline.js — Standalone viewer for LawVM "certified transition graph" exports.
//
// Loads a per-statute SQLite DB (schema transition-graph.v1) via sql.js (CDN),
// folds the certified L3 transitions in the browser to reconstruct the full
// statute tree at any change-date, and self-verifies by recomputing the
// reproducible tree hash and asserting it equals checkpoints.tree_hash — the
// hash authored by the Python LawVM engine. Ported from exp1_certified_reducer.mjs.
//
// Two modes:
//   Oikeustila  — point-in-time structure tree at the selected date.
//   Muutokset   — amendment-as-ops changelog: what each säädös concretely did.
//
// Granularity-agnostic: drives off target_address depth and the node tree, never
// hardcodes "chapter". The current export records transitions at chapter
// granularity; a future export may record at section/subsection granularity.

let db = null;          // sql.js Database
let blobCache = {};     // content_hash -> parsed IRNode (decoded JSON)
let transitions = [];   // all transitions, sequence-ordered
let checkpointByDate = {}; // date -> {tree_hash, active_node_count}
let changeDates = [];   // sorted ISO date strings
let sourceById = {};    // source_id -> source_artifacts row
let selectedAddress = null; // currently selected node address (for detail pane)
let mode = 'oikeustila';    // 'oikeustila' | 'muutokset'
let selectedSourceId = null; // amendment selected in Muutokset mode
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
// transitions with effective_date <= D (respecting expires_date and sequence).
function foldAt(date) {
  const live = new Map();
  const failures = [];
  for (const t of transitions) {
    if (t.effective_date > date) break;            // sequence-ordered by date
    if (t.expires_date && t.expires_date !== '' && t.expires_date <= date) {
      // provision has expired by D
      live.delete(t.target_address);
      continue;
    }
    const cur = live.get(t.target_address) || '';
    if (cur !== t.pre_hash) {
      failures.push({ kind: 'pre_hash_mismatch', address: t.target_address, expected: t.pre_hash, actual: cur });
    }
    if (t.action === 'delete_subtree' || t.action === 'tombstone' || t.post_hash === '') {
      live.delete(t.target_address);
    } else {
      live.set(t.target_address, t.post_hash);
    }
  }
  return { live, failures };
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
  if (manifest.length) { statuteSel.value = manifest[0].statute_id; loadStatute(manifest[0].statute_id); }
}).catch(e => {
  document.getElementById('app').innerHTML = `<p class="error-box">Manifestia ei voitu ladata: ${escHtml(e.message)}</p>`;
});

statuteSel.addEventListener('change', () => { if (statuteSel.value) loadStatute(statuteSel.value); });

async function loadStatute(statuteId) {
  const app = document.getElementById('app');
  app.innerHTML = '<p class="loading">Ladataan säädöstä…</p>';
  const entry = manifest.find(s => s.statute_id === statuteId);
  if (!entry) { app.innerHTML = '<p class="error-box">Säädöstä ei löydy manifestista.</p>'; return; }

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
    selectDate(changeDates.length - 1); // default: latest
  } catch (e) {
    app.innerHTML = `<p class="error-box">Virhe ladattaessa: ${escHtml(e.message)}</p>`;
    console.error(e);
  }
}

// ---- shell render (scrubber + mode toggle + layout) ----
function renderShell(entry) {
  const titleRow = q1("SELECT value FROM meta WHERE key='title'");
  const title = titleRow ? JSON.parse(titleRow.value) : entry.title;
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="mode-bar">
      <button class="mode-btn" data-mode="oikeustila">Oikeustila</button>
      <button class="mode-btn" data-mode="muutokset">Muutokset</button>
      <span class="mode-hint" id="mode-hint"></span>
    </div>
    <div class="scrubber" id="scrubber">
      <div class="scrubber-top">
        <div class="oikeustila">Oikeustila <span class="date" id="sel-date">—</span></div>
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

  setMode('oikeustila');
}

function setMode(m) {
  mode = m;
  for (const b of document.querySelectorAll('.mode-btn')) {
    b.classList.toggle('active', b.dataset.mode === m);
  }
  const scrubber = document.getElementById('scrubber');
  const hint = document.getElementById('mode-hint');
  if (m === 'oikeustila') {
    scrubber.style.display = '';
    hint.textContent = 'Lain rakenne valittuna voimaantulopäivänä, hash-todennettuna moottoria vastaan.';
    renderOikeustila();
  } else {
    scrubber.style.display = 'none';
    hint.textContent = 'Mitä kukin muutossäädös konkreettisesti teki — ennen/jälkeen jokaiselle kohdalle.';
    renderMuutokset();
  }
}

// ---- date selection (Oikeustila mode) ----
let curDateIdx = -1;
let curLive = new Map();
let prevLive = new Map();        // covering set at the previous change-date
let changedAddrs = new Set();    // chapter-level addresses whose subtree changed

async function selectDate(idx) {
  curDateIdx = idx;
  const date = changeDates[idx];
  document.getElementById('sel-date').textContent = date;
  document.getElementById('date-slider').value = idx;
  document.getElementById('date-jump').value = idx;

  const { live, failures } = foldAt(date);
  curLive = live;
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
  const slot = document.getElementById('verify-slot');
  const expected = cp ? cp.tree_hash : null;
  if (expected && got === expected && failures.length === 0) {
    slot.innerHTML = `<span class="verify-badge verify-ok">✓ Todennettu LawVM-moottoria vastaan</span>`
      + `<span class="verify-hash">tree ${got.slice(0, 12)}…</span>`;
  } else {
    const reason = failures.length ? `${failures.length} pre/post-poikkeamaa` : 'tree-hash ≠ moottorin checkpoint';
    slot.innerHTML = `<span class="verify-badge verify-fail">✗ Ei täsmää moottoriin — ${escHtml(reason)}</span>`
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

  if (mode === 'oikeustila') renderOikeustila();
}

// =====================================================================
// Oikeustila (point-in-time structure) view
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

// Clean per-kind label for a structural node (granularity-agnostic):
//   chapter   -> "N luku"   (+ heading rendered separately)
//   section   -> "N §"      (+ heading rendered separately)
//   subsection-> "N mom."
//   paragraph -> "N)"
//   subparagraph -> "N)"
// `ordinal` is the 1-based position among same-kind siblings (fallback when the
// printed num child is absent — momentit have no num child).
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
// child address segment and a same-kind ordinal.
function structChildren(node, addr) {
  const out = [];
  const counts = {};
  for (const c of (node.children || [])) {
    const seg = ADDR_SEG[c.kind];
    if (!seg) continue;
    counts[c.kind] = (counts[c.kind] || 0) + 1;
    out.push({ child: c, ordinal: counts[c.kind], childAddr: `${addr}/${seg}:${counts[c.kind]}` });
  }
  return out;
}

// Inline non-structural content of a node (intro text, content paragraphs, wrapUp,
// crossHeading). Returns an array of {kind, text} in document order. Does NOT
// recurse into structural children (those become their own rows).
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
  // a node may itself carry leaf text (rare)
  if (node.text && node.text.trim()) out.unshift({ kind: node.kind, text: node.text.trim() });
  return out;
}

// Canonical text fingerprint of a subtree, used to detect which provisions
// changed between two dates (granularity-agnostic; works on the node tree, not
// on stored hashes which only exist at chapter level).
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
  // addr is a top-level (chapter) address present in prevLive.
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

let expandState = 'default'; // 'default' | 'all' | 'none' — controls initial collapse

function renderOikeustila() {
  const view = document.getElementById('view');
  const titleRow = q1("SELECT value FROM meta WHERE key='title'");
  const title = titleRow ? JSON.parse(titleRow.value) : (manifest.find(s => s.statute_id) || {}).title || '';
  view.innerHTML = `
    <div class="layout">
      <div class="panel">
        <div class="panel-head">
          <h2 class="panel-title">Rakenne — ${escHtml(title)}</h2>
          <div class="tree-tools">
            <button id="expand-all">Laajenna kaikki</button>
            <button id="collapse-all">Sulje kaikki</button>
          </div>
        </div>
        <div class="tree-legend"><span class="leg-changed">▍</span> muuttunut edelliseen muutospäivään verrattuna</div>
        <div class="tree" id="tree"></div>
      </div>
      <div class="panel">
        <h2 class="panel-title">Versiohistoria</h2>
        <div id="detail"><p class="detail-empty">Valitse pykälä tai luku rakenteesta nähdäksesi sen muutoshistorian, lähteen ja esitöiden viitteet.</p></div>
      </div>
    </div>`;
  document.getElementById('expand-all').addEventListener('click', () => { expandState = 'all'; renderTree(); });
  document.getElementById('collapse-all').addEventListener('click', () => { expandState = 'none'; renderTree(); });
  renderTree();
  if (selectedAddress) renderDetail(selectedAddress);
}

function renderTree() {
  const treeEl = document.getElementById('tree');
  if (!treeEl) return;
  const live = curLive;
  const addrs = [...live.keys()].sort(addrCompare);

  let html = '<ul class="tree-root">';
  for (const addr of addrs) {
    const node = getBlob(live.get(addr));
    if (!node) continue;
    const prevMap = prevChildMap(addr);
    const topChanged = changedAddrs.has(addr);
    html += renderNode(node, addr, 0, prevMap, topChanged);
  }
  html += '</ul>';
  treeEl.innerHTML = html;

  treeEl.querySelectorAll('.node-toggle:not(.leaf)').forEach(t => {
    t.addEventListener('click', (e) => {
      e.stopPropagation();
      const node = t.closest('.node');
      node.classList.toggle('collapsed');
      t.textContent = node.classList.contains('collapsed') ? '▸' : '▾';
    });
  });
  treeEl.querySelectorAll('.node-row').forEach(r => {
    r.addEventListener('click', () => {
      treeEl.querySelectorAll('.node-row.selected').forEach(x => x.classList.remove('selected'));
      r.classList.add('selected');
      selectedAddress = r.dataset.addr;
      renderDetail(selectedAddress);
    });
  });
}

function addrCompare(a, b) {
  const sa = a.split('/'), sb = b.split('/');
  for (let i = 0; i < Math.max(sa.length, sb.length); i++) {
    const na = parseInt((sa[i] || '').split(':')[1] || '0', 10);
    const nb = parseInt((sb[i] || '').split(':')[1] || '0', 10);
    if (na !== nb) return na - nb;
  }
  return 0;
}

// Whether a container is collapsed by default. Default policy: chapters+sections
// open, deeper (subsection/momentti) collapsed so text is reachable but not a wall.
function defaultCollapsed(kind, depth) {
  if (expandState === 'all') return false;
  if (expandState === 'none') return depth > 0;
  return depth >= 2; // depth 0=chapter,1=section open; deeper collapsed
}

function renderNode(node, addr, depth, prevMap, ancestorChanged) {
  const kind = node.kind;
  const heading = nodeHeading(node);
  const children = structChildren(node, addr);
  const inline = inlineContent(node);
  const hasChildren = children.length > 0;
  const isContainer = CONTAINER_KINDS.has(kind);
  const collapsible = hasChildren || inline.length > 0;

  // Change detection: compare this subtree's fingerprint vs previous date.
  const prevNode = prevMap.get(addr);
  let changed = false;
  if (curDateIdx > 0) {
    if (!prevNode) changed = true; // newly present
    else changed = subtreeFingerprint(node) !== subtreeFingerprint(prevNode);
  }

  const kindCls = `kind-${kind}`;
  const label = kindLabel(node, /*ordinal*/ parseInt((addr.split('/').pop() || '').split(':')[1] || '0', 10));

  const collapsed = collapsible && defaultCollapsed(kind, depth) ? ' collapsed' : '';
  const changedCls = changed ? ' changed' : '';

  let html = `<li class="node${collapsed}${changedCls}" data-depth="${depth}">`;
  html += `<div class="node-row" data-addr="${escHtml(addr)}">`;
  html += `<span class="node-toggle ${collapsible ? '' : 'leaf'}">${collapsible ? (collapsed ? '▸' : '▾') : ''}</span>`;
  if (isContainer || kind === 'paragraph' || kind === 'subparagraph') {
    html += `<span class="kind-badge ${kindCls}">${escHtml(KIND_FI[kind] || kind)}</span>`;
  }
  html += `<span class="node-label">${escHtml(label)}</span>`;
  if (heading) html += `<span class="node-heading">${escHtml(heading)}</span>`;
  if (changed) html += `<span class="changed-tag">muuttunut</span>`;
  html += `</div>`;

  // body: inline content (full text, no truncation) + structural children
  if (collapsible) {
    html += `<div class="node-body">`;
    for (const seg of inline) {
      const cls = seg.kind === 'crossHeading' ? 'crossheading'
        : seg.kind === 'intro' ? 'intro'
        : seg.kind === 'wrapUp' ? 'wrapup' : 'content';
      html += `<div class="prov-text ${cls}">${escHtml(seg.text)}</div>`;
    }
    if (hasChildren) {
      html += '<ul>';
      for (const { child, childAddr } of children) {
        html += renderNode(child, childAddr, depth + 1, prevMap, ancestorChanged || changed);
      }
      html += '</ul>';
    }
    html += `</div>`;
  }
  html += '</li>';
  return html;
}

// =====================================================================
// Versiohistoria detail pane (Oikeustila mode) — provenance + word-diff
// =====================================================================
function renderDetail(address) {
  const el = document.getElementById('detail');
  if (!el) return;
  // The clicked address may be deeper than the granularity at which transitions
  // are recorded. Walk up the address until we find recorded transitions.
  const matchAddr = nearestRecordedAddress(address);
  const hist = transitions
    .filter(t => t.target_address === matchAddr)
    .sort((a, b) => (a.effective_date < b.effective_date ? -1
      : a.effective_date > b.effective_date ? 1 : a.sequence - b.sequence));

  const curDate = changeDates[curDateIdx];
  let html = `<div class="detail-head">${escHtml(prettyAddr(address))}</div>`;
  html += `<div class="detail-addr">${escHtml(address)}</div>`;
  if (matchAddr !== address) {
    html += `<p class="detail-note">Muutokset on kirjattu osoitteen <strong>${escHtml(prettyAddr(matchAddr))}</strong> tarkkuudella. Näytetään sen muutoshistoria.</p>`;
  }

  if (!hist.length) { html += '<p class="detail-empty">Ei kirjattuja muutoksia.</p>'; el.innerHTML = html; return; }

  for (const t of hist) {
    const isFuture = t.effective_date > curDate;
    const applies = t.effective_date <= curDate;
    const cls = isFuture ? 'future' : (applies ? 'applies' : '');
    html += `<div class="change ${cls}">`;
    html += `<div class="change-date">Voimaantulo ${escHtml(t.effective_date)}`;
    if (isFuture) html += `<span class="future-tag">tuleva muutos</span>`;
    html += `</div>`;

    if (t.legal_op_kind) html += `<div class="change-op"><span class="op-kind">${escHtml(t.legal_op_kind)}</span></div>`;
    if (t.legal_op_summary) html += `<div class="change-summary">${escHtml(t.legal_op_summary)}</div>`;

    html += provenanceHtml(t);

    if (t.pre_hash || t.payload_hash) html += diffDetails(t.pre_hash, t.payload_hash);
    html += `</div>`;
  }
  el.innerHTML = html;
  wireDiffDetails(el);
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

function provenanceHtml(t) {
  const src = sourceById[t.source_id];
  if (!src && !t.he_ref && !t.source_id) return '';
  let html = `<div class="provenance">`;
  if (src) {
    html += `<div><span class="lbl">Lähde / Muutossäädös:</span> `;
    if (src.url) html += `<a href="${escHtml(src.url)}" target="_blank" rel="noopener">${escHtml(src.title || src.canonical_id || t.source_id)}</a>`;
    else html += escHtml(src.title || src.canonical_id || t.source_id);
    if (src.canonical_id) html += ` (${escHtml(src.canonical_id)})`;
    html += `</div>`;
  } else if (t.source_id) {
    html += `<div><span class="lbl">Lähde / Muutossäädös:</span> ${escHtml(t.source_id)}</div>`;
  }
  if (t.he_ref) html += `<div><span class="lbl">Esitöiden viite:</span> ${escHtml(t.he_ref)}</div>`;
  html += `</div>`;
  return html;
}

function prettyAddr(addr) {
  return addr.split('/').map(seg => {
    const [k, n] = seg.split(':');
    const fi = KIND_FI[k] || k;
    return `${n} ${fi}`;
  }).join(' › ');
}

// =====================================================================
// Muutokset (amendment-as-ops) view
// =====================================================================
function amendmentList() {
  // distinct amending säädökset that appear as transition source_id, ordered by
  // their first effective_date. Excludes the empty/original-enactment source.
  const byId = new Map(); // source_id -> {firstDate, opCount, src}
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
    listHtml += `<li class="amend-item${active}" data-src="${escHtml(a.source_id)}">`
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
  const ops = transitions
    .filter(t => t.source_id === sourceId)
    .sort((a, b) => a.sequence - b.sequence);

  const effectiveDates = [...new Set(ops.map(o => o.effective_date))].sort();
  let html = `<div class="amend-detail-head">`;
  html += `<div class="amend-detail-title">${escHtml(src ? (src.title || sourceId) : sourceId)}</div>`;
  html += `<div class="amend-detail-meta">`;
  html += `<span><span class="lbl">Säädös:</span> ${escHtml(sourceId)}</span>`;
  if (effectiveDates.length) {
    html += `<span><span class="lbl">Voimaantulo:</span> `
      + effectiveDates.map(d => {
        const i = changeDates.indexOf(d);
        return i >= 0
          ? `<a href="#" class="jump-date" data-idx="${i}">${escHtml(d)}</a>`
          : escHtml(d);
      }).join(', ')
      + `</span>`;
  }
  const heRef = (ops.find(o => o.he_ref) || {}).he_ref;
  if (heRef) html += `<span><span class="lbl">Esitöiden viite:</span> ${escHtml(heRef)}</span>`;
  if (src && src.url) html += `<span><span class="lbl">Lähde:</span> <a href="${escHtml(src.url)}" target="_blank" rel="noopener">Finlex ↗</a></span>`;
  html += `</div></div>`;

  html += `<div class="op-list">`;
  for (const t of ops) {
    html += `<div class="op-row">`;
    html += `<div class="op-row-head">`;
    html += `<span class="op-kind">${escHtml(t.legal_op_kind || t.action)}</span>`;
    html += `<span class="op-addr">${escHtml(prettyAddr(t.target_address))}</span>`;
    html += `<span class="op-eff">${escHtml(t.effective_date)}</span>`;
    html += `</div>`;
    if (t.legal_op_summary) html += `<div class="op-summary">${escHtml(t.legal_op_summary)}</div>`;
    html += diffDetails(t.pre_hash, t.payload_hash);
    html += `</div>`;
  }
  html += `</div>`;
  el.innerHTML = html;

  for (const a of el.querySelectorAll('.jump-date')) {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      setMode('oikeustila');
      selectDate(parseInt(a.dataset.idx, 10));
    });
  }
  wireDiffDetails(el);
}

// =====================================================================
// Word-level diff (shared)
// =====================================================================
// Flatten an IRNode subtree into readable plain text (document order).
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
  return `<details class="diff" data-pre="${escHtml(preHash || '')}" data-post="${escHtml(postHash || '')}">`
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

// Render `old`→`new` word diff for one side. side='old' marks deletions,
// side='new' marks insertions. Standard word-level LCS.
function wordDiffHtml(oldTxt, newTxt, side) {
  const aw = oldTxt.split(/\s+/).filter(Boolean);
  const bw = newTxt.split(/\s+/).filter(Boolean);
  if (aw.length + bw.length > 4000) { // guard against pathological O(nm)
    return escHtml(side === 'new' ? newTxt : oldTxt);
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
