// viewer.js — Standalone viewer for LawVM "certified transition graph" exports.
//
// Loads a per-statute SQLite DB (schema transition-graph.v1) via sql.js (CDN),
// folds the certified L3 transitions in the browser to reconstruct the full
// statute tree at any change-date, and self-verifies by recomputing the
// reproducible tree hash and asserting it equals checkpoints.tree_hash — the
// hash authored by the Python LawVM engine. Ported from exp1_certified_reducer.mjs.

let db = null;          // sql.js Database
let blobCache = {};     // content_hash -> parsed IRNode (decoded JSON)
let transitions = [];   // all transitions, sequence-ordered
let checkpointByDate = {}; // date -> {tree_hash, active_node_count}
let changeDates = [];   // sorted ISO date strings
let sourceById = {};    // source_id -> source_artifacts row
let selectedAddress = null; // currently selected node address (for detail pane)
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

fetch('manifest.json').then(r => r.json()).then(m => {
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
    blobCache = {}; selectedAddress = null;

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

// ---- shell render (scrubber + layout) ----
function renderShell(entry) {
  const titleRow = q1("SELECT value FROM meta WHERE key='title'");
  const title = titleRow ? JSON.parse(titleRow.value) : entry.title;
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="scrubber">
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
    <div class="layout">
      <div class="panel">
        <h2 class="panel-title">Rakenne — ${escHtml(title)}</h2>
        <div class="tree" id="tree"></div>
      </div>
      <div class="panel">
        <h2 class="panel-title">Versiohistoria</h2>
        <div id="detail"><p class="detail-empty">Valitse pykälä tai luku rakenteesta nähdäksesi sen muutoshistorian, lähteen ja esitöiden viitteet.</p></div>
      </div>
    </div>`;

  const slider = document.getElementById('date-slider');
  slider.addEventListener('input', () => selectDate(parseInt(slider.value, 10)));
  document.getElementById('prev-date').addEventListener('click', () => selectDate(Math.max(0, curDateIdx - 1)));
  document.getElementById('next-date').addEventListener('click', () => selectDate(Math.min(changeDates.length - 1, curDateIdx + 1)));
  document.getElementById('date-jump').addEventListener('change', (e) => selectDate(parseInt(e.target.value, 10)));
}

// ---- date selection ----
let curDateIdx = -1;
let curLive = new Map();

async function selectDate(idx) {
  curDateIdx = idx;
  const date = changeDates[idx];
  document.getElementById('sel-date').textContent = date;
  document.getElementById('date-slider').value = idx;
  document.getElementById('date-jump').value = idx;

  const { live, failures } = foldAt(date);
  curLive = live;

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
  if (meta) meta.textContent = `${live.size} aktiivista lukua · muutospäivä ${idx + 1}/${changeDates.length}`;

  renderTree(live);
  if (selectedAddress) renderDetail(selectedAddress); // refresh detail for new date context
}

// ---- tree render ----
const KIND_FI = {
  chapter: 'luku', section: 'pykälä', subsection: 'momentti', paragraph: 'kohta',
  subparagraph: 'alakohta', heading: 'otsikko', crossHeading: 'väliotsikko',
  num: 'numero', content: 'teksti', intro: 'johdanto', wrapUp: 'lopetus',
};
const KIND_CLASS = { chapter: 'kind-chapter', section: 'kind-section', subsection: 'kind-subsection' };
// Address-bearing structural kinds (mirror target_address style chapter:N/section:M/...)
const ADDR_SEG = { chapter: 'chapter', section: 'section', subsection: 'subsection', paragraph: 'paragraph', subparagraph: 'subparagraph' };

function nodeNum(node) {
  // First child of kind 'num' carries the printed number like "1 luku" / "3 §"
  const numChild = (node.children || []).find(c => c.kind === 'num');
  return numChild && numChild.text ? numChild.text.trim() : '';
}
function nodeHeading(node) {
  const h = (node.children || []).find(c => c.kind === 'heading');
  return h && h.text ? h.text.trim() : '';
}
// Extract first content snippet under a node (for leaf-ish display)
function nodeSnippet(node) {
  if (node.text && node.text.trim()) return node.text.trim();
  for (const c of (node.children || [])) {
    if (c.kind === 'content' && c.text) return c.text.trim();
  }
  return '';
}

function renderTree(live) {
  const treeEl = document.getElementById('tree');
  const addrs = [...live.keys()].sort((a, b) => {
    // chapter:N numeric sort
    const na = parseInt((a.split(':')[1] || '0'), 10), nb = parseInt((b.split(':')[1] || '0'), 10);
    return na - nb;
  });
  let html = '<ul>';
  for (const addr of addrs) {
    const node = getBlob(live.get(addr));
    if (!node) continue;
    html += renderNode(node, addr);
  }
  html += '</ul>';
  treeEl.innerHTML = html;

  treeEl.querySelectorAll('.node-toggle:not(.leaf)').forEach(t => {
    t.addEventListener('click', (e) => {
      e.stopPropagation();
      t.closest('.node').classList.toggle('collapsed');
      t.textContent = t.closest('.node').classList.contains('collapsed') ? '▸' : '▾';
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

// Render one node and its structural children. `addr` is this node's address
// (chapter:N/section:M/...). Only structural kinds get their own rows.
function renderNode(node, addr) {
  const kind = node.kind;
  const numTxt = nodeNum(node);
  const heading = nodeHeading(node);
  const kindFi = KIND_FI[kind] || kind;
  const kindCls = KIND_CLASS[kind] || '';

  // Structural children (those that carry an address segment)
  const structChildren = [];
  let secCount = 0, subCount = 0, parCount = 0, subParCount = 0;
  for (const c of (node.children || [])) {
    const seg = ADDR_SEG[c.kind];
    if (!seg) continue;
    let n;
    if (c.kind === 'section') n = ++secCount;
    else if (c.kind === 'subsection') n = ++subCount;
    else if (c.kind === 'paragraph') n = ++parCount;
    else if (c.kind === 'subparagraph') n = ++subParCount;
    else n = structChildren.length + 1;
    structChildren.push({ child: c, childAddr: `${addr}/${seg}:${n}` });
  }

  const hasChildren = structChildren.length > 0;
  const collapsedDefault = kind === 'chapter' ? '' : ''; // chapters expanded by default
  let label = '';
  if (numTxt) label += `<span class="node-label">${escHtml(numTxt)}</span> `;
  if (heading) label += `<span class="node-text">${escHtml(heading)}</span>`;
  if (!numTxt && !heading) {
    const snip = nodeSnippet(node);
    label += `<span class="snippet">${escHtml(snip.slice(0, 90))}${snip.length > 90 ? '…' : ''}</span>`;
  }

  let html = `<li class="node ${collapsedDefault}">`;
  html += `<div class="node-row" data-addr="${escHtml(addr)}">`;
  html += `<span class="node-toggle ${hasChildren ? '' : 'leaf'}">${hasChildren ? '▾' : ''}</span>`;
  html += `<span class="kind-badge ${kindCls}">${escHtml(kindFi)}</span>`;
  html += `<span>${label || escHtml(addr)}</span>`;
  html += `</div>`;

  if (hasChildren) {
    html += '<ul>';
    for (const { child, childAddr } of structChildren) html += renderNode(child, childAddr);
    html += '</ul>';
  }
  html += '</li>';
  return html;
}

// ---- detail pane: change history + provenance + before/after ----
function renderDetail(address) {
  const el = document.getElementById('detail');
  // The clicked address may be deeper than chapter-level; transitions are recorded
  // at chapter granularity. Match transitions whose target_address is a prefix of
  // the clicked address (or vice-versa) so a section click shows its chapter's changes.
  const chapterAddr = address.split('/')[0]; // e.g. "chapter:4"
  const hist = transitions
    .filter(t => t.target_address === chapterAddr)
    .sort((a, b) => (a.effective_date < b.effective_date ? -1 : a.effective_date > b.effective_date ? 1 : a.sequence - b.sequence));

  const curDate = changeDates[curDateIdx];
  let html = `<div class="detail-head">${escHtml(prettyAddr(address))}</div>`;
  html += `<div class="detail-addr">${escHtml(address)}</div>`;
  if (address !== chapterAddr) {
    html += `<p class="detail-empty" style="margin-top:0.4rem">Muutokset kirjataan luvun tarkkuudella. Näytetään luvun <strong>${escHtml(prettyAddr(chapterAddr))}</strong> muutokset.</p>`;
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

    if (t.legal_op_kind) {
      html += `<div class="change-op"><span class="op-kind">${escHtml(t.legal_op_kind)}</span></div>`;
    }
    if (t.legal_op_summary) {
      html += `<div class="change-summary">${escHtml(t.legal_op_summary)}</div>`;
    }

    // Provenance: amending statute + HE ref + url
    const src = sourceById[t.source_id];
    if (src || t.he_ref) {
      html += `<div class="provenance">`;
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
    }

    // Before/after diff: pre_hash vs payload_hash content
    if (t.pre_hash || t.payload_hash) {
      html += renderDiff(t);
    }
    html += `</div>`;
  }
  el.innerHTML = html;

  // wire diff toggles
  el.querySelectorAll('details.diff').forEach(d => {
    d.addEventListener('toggle', () => {
      if (d.open && !d.dataset.rendered) {
        const body = d.querySelector('.diff-body');
        body.innerHTML = buildDiff(d.dataset.pre, d.dataset.post);
        d.dataset.rendered = '1';
      }
    });
  });
}

function prettyAddr(addr) {
  return addr.split('/').map(seg => {
    const [k, n] = seg.split(':');
    const fi = KIND_FI[k] || k;
    return `${n} ${fi}`;
  }).join(' › ');
}

// Flatten an IRNode subtree into readable plain text (for before/after diff).
function nodeToText(node) {
  if (!node) return '';
  const parts = [];
  function walk(n) {
    if (n.text && n.text.trim()) parts.push(n.text.trim());
    for (const c of (n.children || [])) walk(c);
  }
  walk(node);
  return parts.join('\n');
}

function renderDiff(t) {
  return `<details class="diff" data-pre="${escHtml(t.pre_hash)}" data-post="${escHtml(t.payload_hash)}">`
    + `<summary>Näytä ennen / jälkeen</summary>`
    + `<div class="diff-body"></div></details>`;
}

function buildDiff(preHash, postHash) {
  const preNode = preHash ? getBlob(preHash) : null;
  const postNode = postHash ? getBlob(postHash) : null;
  const preTxt = nodeToText(preNode);
  const postTxt = nodeToText(postNode);
  if (!preTxt && postTxt) {
    return `<div class="diff-cols"><div class="diff-col"><h5>Ennen</h5><div class="diff-box pre">(uusi sisältö — ei aiempaa versiota)</div></div>`
      + `<div class="diff-col"><h5>Jälkeen</h5><div class="diff-box post">${wordDiffHtml('', postTxt, true)}</div></div></div>`;
  }
  return `<div class="diff-cols">`
    + `<div class="diff-col"><h5>Ennen</h5><div class="diff-box pre">${wordDiffHtml(preTxt, postTxt, false)}</div></div>`
    + `<div class="diff-col"><h5>Jälkeen</h5><div class="diff-box post">${wordDiffHtml(postTxt, preTxt, true, true)}</div></div></div>`;
}

// Highlight differing words. side: false=show as "old" (mark deletions),
// true=show as "new" (mark insertions). Simple LCS word diff.
function wordDiffHtml(text, other, isNew, swap) {
  const a = (swap ? other : text);
  const b = (swap ? text : other);
  // a = old, b = new
  const aw = a.split(/\s+/).filter(Boolean), bw = b.split(/\s+/).filter(Boolean);
  if (aw.length + bw.length > 1600) { // too big for O(nm) LCS; show plain
    return escHtml(isNew ? b : a);
  }
  const ops = diffWords(aw, bw);
  let out = '';
  for (const [type, words] of ops) {
    const txt = escHtml(words.join(' '));
    if (type === 0) out += txt + ' ';
    else if (type === -1) { if (!isNew) out += `<span class="diff-del">${txt}</span> `; }
    else { if (isNew) out += `<span class="diff-ins">${txt}</span> `; }
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
