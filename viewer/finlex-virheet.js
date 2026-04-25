import { createSQLiteHTTPPool } from './vendor/sqlite-wasm-http/dist/index.js';

// ── Configuration ────────────────────────────────────────────────────────────
const DB_URL = 'finlex_errors_publication.db?v=18';
const DB_HTTP_URL = new URL(DB_URL, window.location.href).href;

// ── Error family labels ─────────────────────────────────────────────────────
const FAMILY_LABEL = {
  oracle_section_stale:                  'Muutos soveltamatta',
  oracle_pending_amendment:              'Odottava muutos',
  editorial_convention:                  'Toimituksellinen ero',
  institutional_editorial_convention:    'Toimituksellinen käytäntö',
  deferred_commencement:                 'Lykkääntynyt voimaantulo',
  replay_structural_diff:                'Rakenteellinen ero',
  replay_wording_diff:                   'Tekstiero',
  oracle_cutoff_version_drift:           'Versioviive',
  oracle_metadata_inconsistency:         'Versioviive (arvio)',
  xml_html_topology_drift:               'XML/HTML-rakennevirhe',
  same_chapter_oracle_range_drift:       'Pykäläalueen virhe',
  cross_chapter_oracle_section_drift:    'Lukuvirhe',
  corrigendum_applied:                   'Oikaisuilmoitus',
};
const FAMILY_COLOR = {
  oracle_section_stale:                  'var(--red)',
  oracle_pending_amendment:              'var(--amber)',
  editorial_convention:                  'var(--dim)',
  institutional_editorial_convention:    'var(--dim)',
  deferred_commencement:                 'var(--purple)',
  replay_structural_diff:                'var(--red)',
  replay_wording_diff:                   'var(--amber)',
  oracle_cutoff_version_drift:           'var(--amber)',
  oracle_metadata_inconsistency:         'var(--amber)',
  xml_html_topology_drift:               'var(--purple)',
  same_chapter_oracle_range_drift:       'var(--purple)',
  cross_chapter_oracle_section_drift:    'var(--purple)',
  corrigendum_applied:                   'var(--green)',
};

// Family groups for filter buttons
const FAMILY_GROUPS = {
  all:                                  null,
  oracle_section_stale:                 ['oracle_section_stale', 'oracle_pending_amendment', 'editorial_convention', 'institutional_editorial_convention', 'deferred_commencement', 'replay_structural_diff', 'replay_wording_diff', 'same_chapter_oracle_range_drift'],
  oracle_cutoff_version_drift:          ['oracle_cutoff_version_drift', 'oracle_metadata_inconsistency', 'oracle_section_stale', 'oracle_pending_amendment', 'replay_structural_diff', 'replay_wording_diff'],
  structural:                           ['cross_chapter_oracle_section_drift', 'xml_html_topology_drift'],
  corrigendum_applied:                  ['corrigendum_applied'],
};

function familyLabel(f) { return FAMILY_LABEL[f] || f; }
function familyBadgeLabel(f) {
  if (f === 'oracle_section_stale' || f === 'institutional_editorial_convention') return '';
  return familyLabel(f);
}
function familyColor(f) { return FAMILY_COLOR[f] || 'var(--dim)'; }
function familyGroupMembers(filter) { return FAMILY_GROUPS[filter] || null; }
function compareAscii(a, b) {
  const x = String(a == null ? '' : a);
  const y = String(b == null ? '' : b);
  if (x < y) return -1;
  if (x > y) return 1;
  return 0;
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function escAttr(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}
function normWs(s) {
  return String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
}
function fiText(text) {
  const raw = String(text == null ? '' : text).trim();
  if (!raw) return '';
  const exact = {
    'Replay has a section not present in the oracle.': 'LawVM:ssä on pykälä, jota ei ole Finlexissä.',
    'Oracle has a section missing from replay.': 'Finlexissä on pykälä, jota LawVM:ssä ei ole.',
    'Oracle section looks stale relative to replay.': 'Finlexin pykälä näyttää vanhentuneelta LawVM:ään verrattuna.',
    'Editorial convention / presentation noise.': 'Toimituksellinen käytäntö / esitysmelu.',
    'Liite / appendix differs.': 'Liite eroaa.',
  };
  if (Object.prototype.hasOwnProperty.call(exact, raw)) return exact[raw];
  if (raw.startsWith('Legacy section diagnosis: ')) {
    return 'Pykälädiagnoosi: ' + raw.slice('Legacy section diagnosis: '.length);
  }
  if (raw.startsWith('Body pairing: ')) {
    return 'Johtolause-/body-analyysi: ' + raw.slice('Body pairing: '.length);
  }
  if (raw.startsWith('content_proof: ')) {
    return 'Sisältötodiste: ' + raw.slice('content_proof: '.length);
  }
  if (raw.startsWith('fetch/parse failed')) {
    return raw.replace(/^fetch\/parse failed/, 'haun/parsinnan epäonnistuminen');
  }
  return raw;
}
function addParagraphs(html) {
  return html.replace(/\n/g, '<br><br>');
}
function finlexAjantasaUrl(sid) {
  const base = String(sid || '').split('-')[0];
  const [year, num] = base.split('/');
  if (!year || !num) return null;
  return `https://finlex.fi/fi/laki/ajantasa/${year}/${year}${num.padStart(4,'0')}`;
}
function finlexLainsaadantoUrl(sid) {
  const base = String(sid || '').split('/').length === 2 ? String(sid || '') : String(sid || '').split('-')[0];
  const [year, num] = base.split('/');
  if (!year || !num) return null;
  return `https://www.finlex.fi/fi/lainsaadanto/${year}/${num}`;
}
function finlexAlkupUrl(sid) {
  const base = String(sid || '').split('-')[0];
  const [year, num] = base.split('/');
  if (!year || !num) return null;
  return `https://finlex.fi/fi/laki/alkup/${year}/${year}${num.padStart(4,'0')}`;
}

function correctionStatusFi(status) {
  const raw = String(status || '').trim();
  if (raw === 'valid') return 'voimassa';
  if (raw === 'not valid') return 'ei voimassa';
  return raw;
}

function correctionConfidenceFi(confidence) {
  const raw = String(confidence || '').trim();
  return {
    confirmed: 'vahvistettu',
    high: 'korkea',
    medium: 'keskitaso',
  }[raw] || raw;
}

function correctionMechanismFi(mechanism) {
  const raw = String(mechanism || '').trim();
  return {
    sunset_clause: 'määräaikainen raukeaminen',
    explicitly_repealed: 'nimenomaisesti kumottu',
    formally_repealed: 'muodollisesti kumottu',
    eu_accession_superseded: 'EU-jäsenyyden myötä korvautunut',
    likely_superseded: 'todennäköisesti korvautunut',
    wartime_completed: 'sotasäädös täyttynyt',
    temporary_tax_exception: 'tilapäinen verohelpotus',
    one_time_completed: 'kertasäädös täyttynyt',
    annual_tax_law: 'vuotuinen verolaki',
    price_control_wartime: 'sodan aikainen hintasäätely',
    temporary_subsidy: 'tilapäinen tukijärjestely',
    excise_tax_abolished: 'valmistevero poistettu',
    temporary_experiment: 'tilapäinen kokeilu',
    currency_control_abolished: 'valuuttasäätely poistettu',
  }[raw] || raw;
}

function correctionBadgeTitle(row) {
  const parts = [];
  const statusFi = row.stale_status_fi || correctionStatusFi(row.stale_status);
  const confidenceFi = row.stale_confidence_fi || correctionConfidenceFi(row.stale_confidence);
  const mechanismFi = row.stale_mechanism_fi || correctionMechanismFi(row.stale_mechanism);
  if (statusFi) parts.push(`Tila: ${statusFi}`);
  if (confidenceFi) parts.push(`Luottamus: ${confidenceFi}`);
  if (mechanismFi) parts.push(`Peruste: ${mechanismFi}`);
  if (row.stale_summary_fi) parts.push(row.stale_summary_fi);
  return parts.join(' · ');
}

// ── State ────────────────────────────────────────────────────────────────────
let db, dmp;
let allStatutes = [];
let searchQ = '';
let familyFilter = 'all';
let showEditorial = false;
let detailRenderSeq = 0;
let errorsIndexLoaded = false;
let absentIndexLoaded = false;
let sourceAbsentIndexLoaded = false;
let errorsIndexPromise = null;
let absentIndexPromise = null;
let sourceAbsentIndexPromise = null;
let metadataCorrectionsCount = 0;
let hasAbsentTables = false;

function setLoadProgress(percent, text) {
  const bar = document.getElementById('progress-bar');
  const status = document.getElementById('load-status');
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  if (status && text) status.textContent = text;
}

// ── DB helpers ───────────────────────────────────────────────────────────────
async function q(sql, params) {
  try {
    return await db.exec(sql, params, { rowMode: 'object' });
  } catch (err) {
    console.error('SQLite query failed', { sql, params, err });
    throw err;
  }
}

// ── DB load ──────────────────────────────────────────────────────────────────
async function initDB() {
  dmp = new diff_match_patch();
  setLoadProgress(8, 'Haetaan tietokantaa…');

  db = await createSQLiteHTTPPool({
    workers: 1,
    httpOptions: {
      backendType: 'sync',
      maxPageSize: 65536,
      timeout: 10000,
      cacheSize: 4096,
    },
  });
  setLoadProgress(28, 'Avataan tietokantaa…');
  await db.open(DB_HTTP_URL);
  try {
    const tbls = await db.exec(
      "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('absent_ajantasa','absent_ajantasa_stats')",
      undefined, { rowMode: 'object' },
    );
    hasAbsentTables = tbls.length >= 2;
  } catch (_) {}
  setLoadProgress(40, 'Luetaan säädösindeksiä…');

  window.addEventListener('beforeunload', () => {
    if (db && typeof db.close === 'function') void db.close();
  }, { once: true });

  await new Promise(resolve => requestAnimationFrame(resolve));

  setLoadProgress(62, 'Yhteenvetoa lasketaan…');
  await new Promise(resolve => requestAnimationFrame(resolve));

  await renderTopbarStats();
  setLoadProgress(74, 'Ladataan näkymiä…');
  await new Promise(resolve => requestAnimationFrame(resolve));

  await renderDashboard();
  setLoadProgress(100, 'Valmis');

  document.getElementById('loading').style.display = 'none';
  document.getElementById('topbar').style.display  = 'flex';
  document.getElementById('app').style.display     = 'flex';
}

// ── Index ────────────────────────────────────────────────────────────────────
async function buildIndex() {
  const rows = await q(`
    SELECT s.statute_id, s.title, COALESCE(s.is_repealed, 0) AS isRepealed,
           COALESCE(error_count, 0) AS errorCount,
           error_families AS errorFamilies,
           error_family_counts AS errorFamilyCounts,
           statute_sort_key
    FROM statutes s
    LEFT JOIN source_absent sa
      ON sa.statute_id = s.statute_id
     AND COALESCE(sa.content_absent, 0) = 1
    WHERE s.error_count > 0
      AND COALESCE(sa.content_absent, 0) = 0
    ORDER BY error_count DESC, statute_sort_key
  `);

  const familyCounts = {};
  for (const r of rows) {
    let familyCountsRow = {};
    try {
      const parsed = JSON.parse(r.errorFamilyCounts || '{}');
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        for (const [family, count] of Object.entries(parsed)) {
          const key = String(family || '').trim();
          const value = Number(count || 0);
          if (key && value > 0) familyCountsRow[key] = value;
        }
      }
    } catch(_) {}
    familyCounts[r.statute_id] = familyCountsRow;
  }

  allStatutes = rows.map(r => ({
    sid:              r.statute_id,
    title:            r.title || '',
    isRepealed:       r.isRepealed || 0,
    errorCount:       r.errorCount || 0,
    errorFamilies:    r.errorFamilies || '[]',
    familyCounts:     familyCounts[r.statute_id] || {},
    statuteSortKey:   r.statute_sort_key || '',
  }));
  errorsIndexLoaded = true;
}

function ensureErrorsIndex() {
  if (errorsIndexLoaded) return Promise.resolve();
  if (!errorsIndexPromise) {
    errorsIndexPromise = buildIndex().finally(() => {
      errorsIndexPromise = null;
    });
  }
  return errorsIndexPromise;
}

function countForGroup(familyCounts, groupKey) {
  const members = familyGroupMembers(groupKey);
  if (!members) return Object.values(familyCounts).reduce((a,b) => a+b, 0);
  return members.reduce((sum, f) => sum + (familyCounts[f] || 0), 0);
}

function publicationScopeStatutes() {
  return allStatutes.filter(s => !s.isRepealed && (s.errorCount || 0) > 0);
}

async function publicationScopeErrorTotals() {
  const totals = {
    statutes: 0,
    totalErrors: 0,
    byCategory: {},
  };
  try {
    const stat = await q(`
      SELECT total_statutes AS statutes,
             total_ready_artifacts AS total_errors,
             review_category_counts
      FROM corpus_stats
      LIMIT 1
    `);
    if (stat.length) {
      totals.statutes = stat[0].statutes || 0;
      totals.totalErrors = stat[0].total_errors || 0;
      try {
        const parsed = JSON.parse(stat[0].review_category_counts || '{}');
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          totals.byCategory = parsed;
        }
      } catch(_) {}
    }
  } catch(_) {}
  return totals;
}

async function renderTopbarStats() {
  const totals = await publicationScopeErrorTotals();

  const sep = `<span class="tstat-sep">·</span>`;
  const html =
    `<span class="tstat"><strong>${totals.statutes}</strong> säädöstä</span>` + sep +
    `<span class="tstat red"><strong>${totals.totalErrors}</strong> virhettä</span>`;

  document.getElementById('topbar-stats').innerHTML = html;
}

// ── Sidebar list ─────────────────────────────────────────────────────────────
function renderList() {
  if (!errorsIndexLoaded) {
    const list = document.getElementById('statute-list');
    if (list) {
      list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Haetaan säädösluetteloa…</div>`;
    }
    const count = document.getElementById('statute-count');
    if (count) count.textContent = 'Ladataan…';
    return;
  }
  const ql = searchQ.toLowerCase();
  const filtered = allStatutes.filter(s => {
    if (ql && !s.sid.toLowerCase().includes(ql) && !s.title.toLowerCase().includes(ql)) return false;
    if (s.isRepealed) return false;
    if (s.errorCount === 0) return false;
    if (familyFilter !== 'all' && countForGroup(s.familyCounts, familyFilter) === 0) return false;
    return true;
  }).sort((a, b) => {
    const errorDelta = (b.errorCount || 0) - (a.errorCount || 0);
    if (errorDelta) return errorDelta;
    const sortKeyDelta = compareAscii(a.statuteSortKey || a.sid, b.statuteSortKey || b.sid);
    if (sortKeyDelta) return sortKeyDelta;
    return compareAscii(a.sid, b.sid);
  });

  document.getElementById('statute-count').textContent = `${filtered.length} säädöstä`;

  const list = document.getElementById('statute-list');
  list.innerHTML = '';

  for (const s of filtered) {
    const fc = s.familyCounts;
    let badgeParts = [];
    const staleN = (fc.oracle_section_stale||0) + (fc.same_chapter_oracle_range_drift||0)
      + (fc.replay_structural_diff||0) + (fc.replay_wording_diff||0)
      + (fc.oracle_pending_amendment||0) + (fc.editorial_convention||0)
      + (fc.institutional_editorial_convention||0) + (fc.deferred_commencement||0);
    const cutoffN = (fc.oracle_cutoff_version_drift||0) + (fc.oracle_metadata_inconsistency||0);
    const crossN = fc.cross_chapter_oracle_section_drift||0;
    const corrN = fc.corrigendum_applied||0;
    const topoN = fc.xml_html_topology_drift||0;

    if (staleN > 0) badgeParts.push(`<span style="color:var(--red)">${staleN} §</span>`);
    if (cutoffN > 0) badgeParts.push(`<span style="color:var(--amber)">versio</span>`);
    if (crossN > 0) badgeParts.push(`<span style="color:var(--purple)">${crossN} luku</span>`);
    if (corrN > 0) badgeParts.push(`<span style="color:var(--green)">${corrN} oik.</span>`);
    if (topoN > 0) badgeParts.push(`<span style="color:var(--purple)">XML</span>`);
    const badgeTxt = badgeParts.join(' + ') || `${s.errorCount}`;
    const badgeCls = staleN > 0 ? 'last' : cutoffN > 0 ? 'chain' : 'mixed';

    const errLabel = s.errorCount === 1 ? '1 virhe' : `${s.errorCount} virhettä`;

    const el = document.createElement('div');
    el.className = 'sitem';
    el.dataset.sid = s.sid;
    el.innerHTML =
      `<div class="sitem-top">
        <span class="sitem-id">${esc(s.sid)}</span>
        <span class="sitem-badge ${badgeCls}">${badgeTxt}</span>
      </div>
      <div class="sitem-title" title="${esc(s.title)}">${esc(s.title || '—')}</div>
      <div class="sitem-meta">${esc(errLabel)}</div>`;
    el.addEventListener('click', () => selectStatute(s.sid, el));
    list.appendChild(el);
  }

  if (!filtered.length) {
    list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Ei tuloksia</div>`;
  }
}

// ── Detail ───────────────────────────────────────────────────────────────────
let selectedSid = null;

function selectStatute(sid, el) {
  selectedSid = sid;
  document.querySelectorAll('.sitem').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  const detail = document.getElementById('detail');
  detail.innerHTML = `<div id="detail-loading" style="padding:24px 18px;color:var(--dim);font-size:12px">
    <div style="display:flex;align-items:center;gap:10px">
      <span class="loading-dot" aria-hidden="true"></span>
      <span>Haetaan yksityiskohtia…</span>
    </div>
  </div>`;
  void renderDetail(sid, ++detailRenderSeq);
  document.getElementById('detail').scrollTop = 0;
}

async function renderDetail(sid, seq = detailRenderSeq) {
  const s = allStatutes.find(x => x.sid === sid);
  const title = s ? s.title : '';

  const rows = await q(`
    SELECT *
    FROM errors
    WHERE statute_id = ?
    ORDER BY
      section_sort_rank,
      section_sort_key,
      error_family,
      section
  `, [sid]);
  if (seq !== detailRenderSeq) return;

  if (!rows.length) {
    document.getElementById('detail').innerHTML =
      `<div id="detail-empty">Ei virheitä tässä säädöksessä</div>`;
    return;
  }

  // Drop degenerate rows that have no structured section path and no supporting
  // evidence. These produce blank "?" or "liitteet" cards with nothing actionable.
  // Statute-level families (cutoff drift, topology, etc.) legitimately have an
  // empty section, so only apply this gate to section-level families.
  // A structured section path contains ':' (e.g. 'chapter:3/section:5').
  // Non-structured labels like '' or 'liitteet' with no blame/text are noise.
  const _SECTION_LEVEL_FAMILIES = new Set([
    'oracle_section_stale', 'replay_structural_diff', 'replay_wording_diff',
    'oracle_pending_amendment', 'deferred_commencement',
    'institutional_editorial_convention', 'blamed_source_lacks_payload_support',
    'source_pathology', 'contingent_effective_date',
  ]);
  const filteredRows = rows.filter(r => {
    if (!_SECTION_LEVEL_FAMILIES.has(r.error_family)) return true;
    const hasStructuredSection = r.section && r.section.includes(':');
    const hasContent = r.blame_source || r.johtolause_text || r.oracle_text || r.replay_text;
    return hasStructuredSection || hasContent;
  });

  const familyVisible = filteredRows;

  // Classify each row as editorial tombstone or not, then apply showEditorial filter
  const editorialFlags = familyVisible.map(r => isEditorialTombstoneRow(r));
  const editorialCount = editorialFlags.filter(Boolean).length;
  const visible = familyVisible.filter((r, i) => showEditorial || !editorialFlags[i]);

  const ajantasaUrl  = finlexAjantasaUrl(sid);
  const ajantasaBtn  = ajantasaUrl
    ? `<a class="ext-btn" href="${ajantasaUrl}" target="_blank" rel="noopener">Finlex ajantasa ↗</a>`
    : '';

  const editorialToggle = editorialCount > 0
    ? `<label style="font-size:11px;color:var(--dim);cursor:pointer;margin-left:12px;user-select:none">
        <input type="checkbox" id="editorial-toggle" ${showEditorial ? 'checked' : ''}
               style="vertical-align:middle;margin-right:3px">
        Toimitukselliset (${editorialCount})
      </label>`
    : '';

  let html = `<div class="detail-hdr">
    <div class="detail-hdr-row">
      <h2>${esc(sid)}${title ? ' — ' + esc(title) : ''}</h2>
      ${ajantasaBtn}
    </div>
    <div class="detail-meta">${visible.length} virhettä näkyvissä · ${rows.length} yhteensä${editorialToggle}</div>
  </div>`;

  // Query amendment chains for this statute
  let chainsBySection = {};
  try {
    const chains = await q(`
      SELECT section_key, amendment_id, amendment_ord, amendment_title,
             is_blame_source, is_later_touch
      FROM section_amendment_chain
      WHERE statute_id = ?
      ORDER BY amendment_ord
    `, [sid]);
    for (const c of chains) {
      const k = c.section_key;
      if (!chainsBySection[k]) chainsBySection[k] = [];
      chainsBySection[k].push(c);
    }
  } catch(_) {}

  // Query manual reviews for this statute (both section-level and statute-level)
  let manualReviewsBySection = {};
  let statuteManualReview = null;
  try {
    const reviews = await q(`
      SELECT section, verdict, explanation, reviewed_at, tier, confidence, root_cause, reviewer, auditor, audited_at
      FROM manual_reviews
      WHERE statute_id = ?
    `, [sid]);
    for (const r of reviews) {
      if (r.section === '') {
        statuteManualReview = r;
      } else {
        manualReviewsBySection[r.section] = r;
      }
    }
  } catch(_) {}

  // Render each section as a full card with structure comparison
  for (let i = 0; i < visible.length; i++) {
    const sectionKey = String(visible[i].section || '').trim();
    const sectionReview = manualReviewsBySection[sectionKey] || statuteManualReview;
    html += renderCard(visible[i], i === 0, chainsBySection[sectionKey], sectionReview);
  }

  document.getElementById('detail').innerHTML = html;
  if (seq !== detailRenderSeq) return;

  // Wire editorial toggle
  const editorialToggleEl = document.getElementById('editorial-toggle');
  if (editorialToggleEl) {
    editorialToggleEl.addEventListener('change', e => {
      showEditorial = e.target.checked;
      void renderDetail(sid);
    });
  }

  // Lazy diff
  const pendingDiffs = document.querySelectorAll('.diff-inline[data-diff-pending]');
  let diffIdx = 0;
  function computeNextDiffs(deadline) {
    while (diffIdx < pendingDiffs.length && (deadline.timeRemaining() > 5 || deadline.didTimeout)) {
      const el = pendingDiffs[diffIdx++];
      const oText = el.dataset.otext || '';
      const rText = el.dataset.rtext || '';
      if (oText && rText) {
        const diffs = wordLevelDiff(oText, rText);
        let h = '';
        for (const [op, txt] of diffs) {
          const s = esc(txt);
          if      (op ===  0) h += s;
          else if (op === -1) h += `<span class="diff-del">${s}</span>`;
          else if (op ===  1) h += `<span class="diff-add">${s}</span>`;
        }
        el.innerHTML = addParagraphs(h);
      }
      el.removeAttribute('data-diff-pending');
    }
    if (diffIdx < pendingDiffs.length) {
      requestIdleCallback(computeNextDiffs, { timeout: 100 });
    }
  }
  if (pendingDiffs.length) {
    requestIdleCallback(computeNextDiffs, { timeout: 50 });
  }
}

// ── Section display fix ──────────────────────────────────────────────────────
function fixSecDisplay(raw) {
  let s = String(raw || '');
  s = s.replace(/part:([ivxlc]+)/gi, (_, p) => p.toUpperCase() + ' osa');
  // Uppercase stray Roman numeral chapter labels from cached data
  s = s.replace(/\b([ivxlc]+)\s+luku\b/gi, (_, p) => p.toUpperCase() + ' luku');
  return s;
}

function parseStructure(jsonText) {
  if (!jsonText) return null;
  try {
    const parsed = JSON.parse(jsonText);
    // Server-side normalization (normalize_structure.py) stamps _normalized.
    // Skip client-side normalization when the data is already viewer-ready.
    if (parsed && parsed._normalized) return parsed;
    return normalizeStructureNode(parsed);
  } catch (_) { return null; }
}

function parseMaybeJson(value) {
  if (value == null || value === '') return null;
  if (typeof value === 'object') return value;
  try { return JSON.parse(String(value)); }
  catch (_) { return null; }
}

function firstPresent(...values) {
  for (const value of values) {
    if (value == null) continue;
    const text = String(value).trim();
    if (text) return value;
  }
  return null;
}

function parseStoredSemanticArtifact(row) {
  const artifact = parseMaybeJson(firstPresent(
    row.aligned_structure,
    row.aligned_semantic_tree,
    row.semantic_alignment,
    row.structure_alignment,
    row.semantic_tree,
  ));
  if (!artifact || typeof artifact !== 'object') return null;
  if (artifact.left || artifact.right) return artifact;
  if (artifact.tree && typeof artifact.tree === 'object' && (artifact.tree.left || artifact.tree.right)) return artifact.tree;
  if (artifact.aligned_tree && typeof artifact.aligned_tree === 'object' && (artifact.aligned_tree.left || artifact.aligned_tree.right)) {
    return artifact.aligned_tree;
  }
  return null;
}

function parseStoredSemanticEvents(row) {
  const artifact = parseMaybeJson(firstPresent(
    row.aligned_structure,
    row.aligned_semantic_tree,
    row.semantic_alignment,
    row.structure_alignment,
    row.semantic_tree,
  ));
  if (artifact && typeof artifact === 'object' && Array.isArray(artifact.events)) {
    return artifact.events;
  }
  const events = parseMaybeJson(firstPresent(
    row.semantic_diff_events,
    row.structure_diff_events,
    row.semantic_events,
  ));
  return Array.isArray(events) ? events : [];
}

function parseStoredSemanticStats(row) {
  const structural = Number(firstPresent(
    row.structure_diff_structural,
    row.semantic_diff_structural,
    row.semantic_structural,
    0,
  ) || 0);
  const label = Number(firstPresent(
    row.structure_diff_label,
    row.semantic_diff_label,
    row.semantic_label,
    0,
  ) || 0);
  const text = Number(firstPresent(
    row.structure_diff_text,
    row.semantic_diff_text,
    row.semantic_text,
    0,
  ) || 0);
  const kind = String(firstPresent(
    row.structure_diff_kind,
    row.semantic_diff_kind,
    '',
  ) || '').trim();
  const summary = String(firstPresent(
    row.structure_diff_summary,
    row.semantic_diff_summary,
    '',
  ) || '').trim();
  if (!kind && !summary && !structural && !label && !text) return null;
  return { kind, summary, structural, label, text };
}

function rawStructureChildren(node) {
  return Array.isArray(node?.children) ? node.children : [];
}

function canonicalStructureKind(kind) {
  const raw = String(kind || '').trim();
  if (raw === 'paragraph' || raw === 'item') return 'item';
  if (raw === 'subparagraph') return 'subitem';
  if (raw === 'section' || raw === 'subsection' || raw === 'heading' || raw === 'intro') return raw;
  return '';
}

function extractStructureNum(node) {
  if (!node) return '';
  if (String(node.kind || '').trim() === 'num') return normWs(node.text || '');
  for (const child of rawStructureChildren(node)) {
    if (String(child?.kind || '').trim() === 'num') return normWs(child.text || '');
  }
  return '';
}

function normalizeStructureLabel(kind, label) {
  let raw = normWs(label || '');
  if (!raw) return '';
  if (kind === 'section') {
    raw = raw.replace(/\s*§\s*$/i, '');
    return raw;
  }
  if (kind === 'subsection') {
    const m = raw.match(/^(\d+[a-zåäö]?)/i);
    return m ? m[1] : raw;
  }
  if (kind === 'item') {
    raw = raw.replace(/\s+kohta\s*$/i, '');
    raw = raw.replace(/[)\s.]+$/g, '');
    raw = raw.replace(/^(\d+)\s+([a-zåäö])$/i, '$1$2');
    return raw;
  }
  if (kind === 'subitem') {
    raw = raw.replace(/\s+alakohta\s*$/i, '');
    raw = raw.replace(/[)\s.]+$/g, '');
    raw = raw.replace(/^(\d+)\s+([a-zåäö])$/i, '$1$2');
    return raw;
  }
  return raw;
}

function structureLabelForDisplay(label) {
  const raw = String(label || '').trim();
  const m = raw.match(/^(\d+)([a-zåäö])$/i);
  if (m) return `${m[1]} ${m[2]}`;
  return raw;
}

function normalizeStructureText(node, kind) {
  const ownText = normWs(node?.text || '');
  if (kind === 'heading' || kind === 'intro') return ownText;

  const parts = [];
  for (const child of rawStructureChildren(node)) {
    const childKind = String(child?.kind || '').trim();
    if (childKind === 'content') {
      const pChildren = rawStructureChildren(child).filter(grandchild => String(grandchild?.kind || '').trim() === 'p');
      if (pChildren.length) {
        for (const pChild of pChildren) {
          const text = normWs(pChild?.text || '');
          if (text) parts.push(text);
        }
      } else {
        const text = normWs(child?.text || '');
        if (text) parts.push(text);
      }
    } else if (childKind === 'p' || childKind === 'block') {
      const text = normWs(child?.text || '');
      if (text) parts.push(text);
    }
  }

  if (parts.length) return normWs(parts.join(' '));
  return ownText;
}

function assignStructureOrdinals(children) {
  const ordinalKinds = new Set(['subsection', 'item', 'subitem']);
  const nextOrdinals = {};
  for (const child of children) {
    if (!ordinalKinds.has(child.kind)) continue;
    const m = String(child.label || '').match(/^(\d+)/);
    if (m) nextOrdinals[child.kind] = Math.max(nextOrdinals[child.kind] || 0, parseInt(m[1], 10));
  }
  for (const child of children) {
    if (!ordinalKinds.has(child.kind) || child.label) continue;
    nextOrdinals[child.kind] = (nextOrdinals[child.kind] || 0) + 1;
    child.label = String(nextOrdinals[child.kind]);
  }
}

// LEGACY FALLBACK: Structure normalization is now performed server-side in
// src/lawvm/semantic/normalize_structure.py.  Pre-normalized data carries a
// ``_normalized`` marker and parseStructure() skips this function.  This
// client-side path is retained for backward compatibility with older DB rows
// that were stored before the server-side normalization was introduced.
function normalizeStructureNode(node) {
  if (!node || typeof node !== 'object') return null;
  const kind = canonicalStructureKind(node.kind);
  const children = [];
  for (const child of rawStructureChildren(node)) {
    const normalizedChild = normalizeStructureNode(child);
    if (normalizedChild) children.push(normalizedChild);
  }
  const structuralChildren = [];
  const facets = {};
  let wordingText = '';
  for (const child of children) {
    if (child.kind === 'heading' || child.kind === 'intro') {
      if (child.text) facets[child.kind] = { text: child.text };
      continue;
    }
    structuralChildren.push(child);
  }
  const rawFacets = node?.facets && typeof node.facets === 'object' ? node.facets : null;
  if (rawFacets) {
    for (const facetKind of ['heading', 'intro']) {
      const rawFacet = rawFacets[facetKind];
      if (!rawFacet || typeof rawFacet !== 'object') continue;
      const text = normWs(rawFacet.text || '');
      if (text) facets[facetKind] = { text };
    }
    const rawWording = rawFacets.wording;
    if (rawWording && typeof rawWording === 'object') {
      wordingText = normWs(rawWording.text || '');
    }
  }
  assignStructureOrdinals(structuralChildren);

  if (!kind) {
    if (!structuralChildren.length) return null;
    return { kind: 'group', children: structuralChildren };
  }

  const normalized = { kind };
  const label = normalizeStructureLabel(kind, node.label || extractStructureNum(node));
  const text = wordingText || normalizeStructureText(node, kind);
  if (label) normalized.label = label;
  if (text) normalized.text = text;
  if (Object.keys(facets).length) normalized.facets = facets;
  if (structuralChildren.length) normalized.children = structuralChildren;
  return normalized;
}

function structureNodeLabel(node) {
  const kind = String(node?.kind || '');
  const label = structureLabelForDisplay(node?.label || '');
  if (kind === 'section') return label ? `${label} §` : 'pykälä';
  if (kind === 'subsection') return label ? `${label} mom.` : 'mom.';
  if (kind === 'item') return label ? `${label} kohta` : 'kohta';
  if (kind === 'subitem') return label ? `${label} alakohta` : 'alakohta';
  if (kind === 'heading') return 'otsikko';
  if (kind === 'intro') return 'johdanto';
  return label || kind;
}

function structureNodeDisplayLabel(node) {
  const kind = String(node?.kind || '');
  if (!kind || kind === 'group') return '';
  return structureNodeLabel(node);
}

function structureNodeKey(node, index) {
  const kind = String(node?.kind || '');
  const label = String(node?.label || '').trim();
  return `${kind}:${label || index}`;
}

function alignStructureChildren(leftChildren, rightChildren) {
  const aligned = [];
  const rightMap = new Map();
  rightChildren.forEach((child, idx) => {
    const key = structureNodeKey(child, idx);
    const bucket = rightMap.get(key) || [];
    bucket.push(child);
    rightMap.set(key, bucket);
  });
  leftChildren.forEach((child, idx) => {
    const key = structureNodeKey(child, idx);
    const bucket = rightMap.get(key);
    if (bucket && bucket.length) {
      aligned.push([child, bucket.shift() || null]);
      if (!bucket.length) rightMap.delete(key);
    } else {
      aligned.push([child, null]);
    }
  });
  for (const bucket of rightMap.values()) {
    for (const child of bucket) aligned.push([null, child]);
  }
  return aligned;
}

function semanticEventKindLabel(kind) {
  switch (String(kind || '')) {
    case 'unit_missing_left': return 'Puuttuu vasemmalta';
    case 'unit_missing_right': return 'Puuttuu oikealta';
    case 'facet_added': return 'Tekstifasetti lisätty';
    case 'facet_removed': return 'Tekstifasetti poistettu';
    case 'unit_kind_changed': return 'Yksikkö muuttui';
    case 'canonical_label_changed': return 'Semanttinen tunnus muuttui';
    case 'visible_label_changed': return 'Näkyvä tunnus muuttui';
    case 'heading_text_changed': return 'Otsikko muuttui';
    case 'intro_text_changed': return 'Johdanto muuttui';
    case 'wording_text_changed': return 'Teksti muuttui';
    case 'editorial_repeal_notice': return 'Kumottu-ilmoitus (toimituksellinen)';
    default: return String(kind || 'Semanttinen muutos');
  }
}

function semanticFacetKindLabel(kind) {
  switch (String(kind || '')) {
    case 'heading': return 'otsikko';
    case 'intro': return 'johdanto';
    case 'wording': return 'teksti';
    default: return normWs(kind || '');
  }
}

function renderSemanticEventItem(evt) {
  if (!evt || typeof evt !== 'object') return '';
  const path = Array.isArray(evt.semantic_path)
    ? evt.semantic_path.join(' › ')
    : normWs(evt.semantic_path || '');
  const leftBadge = normWs(evt.left_badge || '');
  const rightBadge = normWs(evt.right_badge || '');
  const leftText = normWs(evt.left_text || '');
  const rightText = normWs(evt.right_text || '');
  const matchBasis = normWs(evt.match_basis || '');
  const facetKind = semanticFacetKindLabel(evt.facet_kind || '');
  return `<div class="semantic-event semantic-event-${esc(evt.kind || 'unknown')}">
    <div class="semantic-event-head">
      <span class="semantic-event-kind">${esc(semanticEventKindLabel(evt.kind))}</span>
      ${facetKind ? `<span class="semantic-event-facet">${esc(facetKind)}</span>` : ''}
      ${path ? `<span class="semantic-event-path">${esc(path)}</span>` : ''}
      ${matchBasis ? `<span class="semantic-event-match">${esc(matchBasis)}</span>` : ''}
    </div>
    <div class="semantic-event-body">
      ${leftBadge || rightBadge ? `<div class="semantic-event-side">
        <span class="semantic-event-side-label">Tunnus</span>
        <span class="semantic-event-badges">
          ${leftBadge ? `<span class="semantic-event-badge left">${esc(leftBadge)}</span>` : ''}
          ${rightBadge ? `<span class="semantic-event-badge right">${esc(rightBadge)}</span>` : ''}
        </span>
      </div>` : ''}
      ${leftText || rightText ? `<div class="semantic-event-side">
        <span class="semantic-event-side-label">Teksti</span>
        <div class="semantic-event-texts">
          ${leftText ? `<div class="semantic-event-text left">${esc(leftText)}</div>` : ''}
          ${rightText ? `<div class="semantic-event-text right">${esc(rightText)}</div>` : ''}
        </div>
      </div>` : ''}
    </div>
  </div>`;
}

function renderSemanticEvents(events) {
  if (!Array.isArray(events) || !events.length) return '';
  // Filter out editorial noise events unless showEditorial is active
  const editorial = events.filter(e => e.kind === 'editorial_repeal_notice' || e.kind === 'empty_oracle_shell');
  const real = events.filter(e => e.kind !== 'editorial_repeal_notice' && e.kind !== 'empty_oracle_shell');
  if (!real.length && !showEditorial) return '';
  const display = showEditorial ? events : real;
  if (!display.length) return '';
  const editorialNote = editorial.length && !showEditorial
    ? ` <span style="color:var(--dim);font-size:10px">(+${editorial.length} toimituksellinen)</span>`
    : '';
  return `<details class="semantic-events">
    <summary class="semantic-events-summary">Semanttiset tapahtumat (${display.length})${editorialNote}</summary>
    <div class="semantic-event-list">
      ${display.map(renderSemanticEventItem).join('')}
    </div>
  </details>`;
}

// ── Tombstone & temporal status detection ─────────────────────────────────────

/**
 * Check if a DB row represents an editorial tombstone — a section where
 * ALL semantic diff events are editorial_repeal_notice or empty_oracle_shell.
 * These are confirmed repeals, not real errors.
 */
function isEditorialTombstoneRow(row) {
  const events = parseStoredSemanticEvents(row);
  if (!events.length) return false;
  return events.every(e =>
    e.kind === 'editorial_repeal_notice' ||
    e.kind === 'empty_oracle_shell'
  );
}

/**
 * Check if a structure node represents a repeal placeholder on the replay side.
 * The replay IR stamps lawvm_repeal_placeholder → label_basis = "repeal_placeholder".
 */
function isRepealPlaceholderNode(node) {
  if (!node) return false;
  if (node.label_basis === 'repeal_placeholder') return true;
  // For aligned trees: check .left (replay side)
  if (node.left && node.left.label_basis === 'repeal_placeholder') return true;
  return false;
}

/**
 * Determine temporal status from error row metadata.
 * Returns: 'scheduled' | 'pending_external_resolution' | 'inactive' | null
 */
function detectTemporalStatus(row) {
  const family = row.error_family || '';
  if (family === 'deferred_commencement') {
    // Check suspect_detail to distinguish scheduled vs pending decree
    const detail = String(row.suspect_detail || '').toLowerCase();
    if (detail.includes('asetuksella') || detail.includes('contingent')) {
      return 'pending_external_resolution';
    }
    return 'scheduled';
  }
  return null;
}

/**
 * Render a temporal status badge for a section.
 */
function renderTemporalBadge(status) {
  if (!status) return '';
  if (status === 'scheduled') {
    return '<span class="temporal-badge scheduled">myöhemmin voimaantuleva</span>';
  }
  if (status === 'pending_external_resolution') {
    return '<span class="temporal-badge pending-ext">voimaantulo asetuksella</span>';
  }
  if (status === 'inactive') {
    return '<span class="temporal-badge inactive">ei voimassa</span>';
  }
  return '';
}

/**
 * Render manual review annotation if one exists.
 * Returns HTML string or empty string if no review.
 */
function renderManualReview(review) {
  if (!review || !review.verdict) return '';

  const verdictColor = {
    'lawvm_ok': 'var(--green)',
    'lawvm_bug': 'var(--red)',
    'oracle_stale': 'var(--amber)',
    'stale_viewer': 'var(--amber)',
    'mixed': 'var(--purple)',
    'source_defect': 'var(--blue)',
    'source_pathology': 'var(--blue)',
  }[review.verdict] || 'var(--dim)';

  const verdictLabel = {
    'lawvm_ok': 'LawVM oikein',
    'lawvm_bug': 'LawVM vika',
    'oracle_stale': 'Oracle vanhentunut',
    'stale_viewer': 'Näytön välimuisti vanhentunut',
    'mixed': 'Sekä-että',
    'source_defect': 'Lähdeaineiston virhe',
    'source_pathology': 'Lähdeaineiston virhe',
  }[review.verdict] || review.verdict;

  const verdictBg = {
    'lawvm_ok': '#0c2e18',
    'lawvm_bug': '#2e0c0c',
    'oracle_stale': '#2a1f00',
    'stale_viewer': '#2a1f00',
    'mixed': '#1a1828',
    'source_defect': '#0c1a2e',
    'source_pathology': '#0c1a2e',
  }[review.verdict] || '#1a1a1a';

  // Tier badge
  const tierBadge = (() => {
    const t = review.tier || '';
    if (t === 'platinum') {
      return `<span style="display:inline-block;background:linear-gradient(135deg,#5a4a00,#c8a000,#5a4a00);color:#ffe87a;padding:2px 8px;border-radius:3px;font-weight:700;font-size:11px;letter-spacing:0.03em;border:1px solid #8a6e00;margin-left:5px" title="Vahvistettu löydös">&#9733; Vahvistettu</span>`;
    }
    if (t === 'gold') {
      return `<span style="display:inline-block;background:#1e1a00;color:#c8a000;padding:2px 8px;border-radius:3px;font-size:11px;margin-left:5px">Automaattinen luokitus</span>`;
    }
    if (t === 'silver') {
      return `<span style="display:inline-block;background:#1a1a1a;color:var(--dim);padding:2px 8px;border-radius:3px;font-size:11px;margin-left:5px">Alustava</span>`;
    }
    return '';
  })();

  // Confidence label
  const confidenceLabel = {
    'definitive': 'Korkea luottamus',
    'strong': 'Vahva näyttö',
    'uncertain': 'Epävarma',
    'withdrawn': 'Peruutettu',
  }[review.confidence || ''] || '';

  let html = `<div style="background:#f5f5f5;border-left:3px solid ${verdictColor};padding:10px 12px;margin-top:8px;font-size:12px">
    <div style="margin-bottom:4px">
      <span style="display:inline-block;background:${verdictBg};color:${verdictColor};padding:2px 8px;border-radius:3px;font-weight:600;font-size:11px">${esc(verdictLabel)}</span>${tierBadge}${confidenceLabel ? `<span style="display:inline-block;color:var(--dim);font-size:11px;margin-left:8px">${esc(confidenceLabel)}</span>` : ''}
    </div>`;

  if (review.root_cause) {
    html += `<div style="margin:4px 0"><span style="display:inline-block;background:#222;color:#aaa;padding:1px 6px;border-radius:3px;font-size:10px;font-family:monospace">${esc(review.root_cause)}</span></div>`;
  }

  if (review.explanation) {
    html += `<p style="margin:4px 0;color:var(--text);line-height:1.5">${esc(fiText(review.explanation))}</p>`;
  }

  const footerParts = [];
  if (review.reviewed_at) {
    footerParts.push(`Tarkistettu: ${esc(review.reviewed_at)}`);
  }
  if (review.auditor) {
    const auditorStr = review.audited_at
      ? `Tarkistanut: ${esc(review.auditor)} (${esc(review.audited_at)})`
      : `Tarkistanut: ${esc(review.auditor)}`;
    footerParts.push(auditorStr);
  }
  if (footerParts.length) {
    html += `<div style="font-size:11px;color:var(--dim);margin-top:4px">${footerParts.join(' &mdash; ')}</div>`;
  }

  html += `</div>`;
  return html;
}

// Normalize all dash/hyphen variants to ASCII hyphen for diff comparison only.
// En-dash (–), em-dash (—), figure dash (‒), hyphen (‐), non-breaking hyphen (‑), minus (−).
function normalizeDashes(s) {
  return s.replace(/[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]/g, '-');
}

// Normalize text for visual diff comparison — mirrors Python _normalize_wording_for_diff().
// Strips editorial noise so the viewer doesn't show spurious inline diffs.
function normalizeForDiffComparison(s) {
  s = normalizeDashes(s);
  s = s.replace(/\s*§\s*/g, ' § ');
  s = s.replace(/\s+([.,;:)])/g, '$1');
  s = s.replace(/(\w)-\s+(\w)/g, '$1-$2');
  s = s.replace(/(\w)-(\w)/g, '$1$2');
  s = s.replace(/\s*\(\d{1,2}\.\d{1,2}\.\d{4}\/\d+\)\s*$/, '');
  return s.replace(/[.\s]+$/, '').trim();
}

// Word-level diff using DMP encoding trick.
// preferB: for equal chunks, emit b's original token; otherwise emit a's.
// op=1 always emits b's token; op=-1 always emits a's token.
function wordLevelDiff(a, b, preferB = false) {
  const tokens = s => (s.match(/\S+|\s+/g) || []);
  const aT = tokens(a);
  const bT = tokens(b);
  // vocab[i] = { a: original from a, b: original from b }
  const vocab = [{ a: '', b: '' }]; // index 0 unused
  const seen = Object.create(null);
  const register = (ts, side) => {
    for (const t of ts) {
      const key = normalizeDashes(t);
      if (!(key in seen)) { seen[key] = vocab.length; vocab.push({ a: '', b: '' }); }
      vocab[seen[key]][side] = t;
    }
  };
  register(aT, 'a');
  register(bT, 'b');
  const encode = ts => {
    let s = '';
    for (const t of ts) s += String.fromCharCode(seen[normalizeDashes(t)]);
    return s;
  };
  const raw = dmp.diff_main(encode(aT), encode(bT), false);
  return raw.map(([op, enc]) => {
    let text = '';
    for (let i = 0; i < enc.length; i++) {
      const e = vocab[enc.charCodeAt(i)];
      if (op === 1)       text += e.b || e.a;
      else if (op === -1) text += e.a || e.b;
      else                text += preferB ? (e.b || e.a) : (e.a || e.b);
    }
    return [op, text];
  });
}

function renderStructureTextDiff(text, other, side) {
  if (!text && !other) return '';
  if (!text) {
    return `<div class="structure-text"><span class="diff-absent-text">puuttuu</span></div>`;
  }
  if (!other) {
    return `<div class="structure-text"><span class="diff-unique">${esc(text)}</span></div>`;
  }
  if (normalizeForDiffComparison(text) === normalizeForDiffComparison(other)) {
    return `<div class="structure-text">${esc(text)}</div>`;
  }
  // When texts are very different, word-level diff is unreadable noise.
  // Fall back to showing the full text highlighted as unique.
  const commonWords = text.split(/\s+/).filter(w => other.includes(w)).length;
  const totalWords = Math.max(text.split(/\s+/).length, other.split(/\s+/).length, 1);
  if (commonWords / totalWords < 0.3) {
    return `<div class="structure-text"><span class="diff-unique">${esc(text)}</span></div>`;
  }
  // Both sides have enough in common for a readable word-level diff.
  // preferB=true for replay so equal chunks show LawVM's verbatim text; false for oracle.
  const diffs = side === 'replay'
    ? wordLevelDiff(other, text, true)
    : wordLevelDiff(text, other, false);
  // Group adjacent change spans: show this-side text first, then other-side,
  // so "Liikenne- ja|Liikenteen viestintävirastoa|turvallisuusvirastoa" renders as
  // [Liikenteen turvallisuusvirastoa][Liikenne- ja viestintävirastoa] not interleaved.
  let html = '';
  let i = 0;
  while (i < diffs.length) {
    const [op, chunk] = diffs[i];
    if (op === 0) { html += esc(chunk); i++; continue; }
    // Collect consecutive non-equal spans, treating whitespace-only equal
    // chunks as part of the change group (they don't constitute meaningful
    // shared content, so absorb them to avoid interleaving).
    let thisHtml = '', otherHtml = '';
    while (i < diffs.length) {
      if (diffs[i][0] === 0) {
        // Whitespace-only equal chunk between two change groups — absorb it
        if (diffs[i][1].trim() === '' && i + 1 < diffs.length && diffs[i + 1][0] !== 0) {
          const ws = esc(diffs[i][1]);
          thisHtml += ws;
          otherHtml += ws;
          i++;
          continue;
        }
        break; // real shared content — end change group
      }
      const [op2, chunk2] = diffs[i];
      const isThis = side === 'replay' ? (op2 === 1) : (op2 === -1);
      if (isThis) thisHtml += esc(chunk2);
      else otherHtml += esc(chunk2);
      i++;
    }
    if (thisHtml) html += `<span class="diff-unique">${thisHtml}</span>`;
    if (otherHtml) html += `<span class="diff-absent-text">${otherHtml}</span>`;
  }
  return `<div class="structure-text">${html}</div>`;
}

function alignedFacetEntries(facets) {
  if (!facets || typeof facets !== 'object') return [];
  return ['heading', 'intro']
    .filter(kind => facets[kind] && typeof facets[kind] === 'object')
    .map(kind => [kind, facets[kind]]);
}

function normalizeFacetText(facet) {
  if (!facet || typeof facet !== 'object') return '';
  return normWs(facet.text || '');
}

function alignedWordingText(node, side = 'replay') {
  const wording = node?.facets?.wording;
  if (!wording || typeof wording !== 'object') return '';
  const preferred = side === 'replay'
    ? (wording.left || null)
    : (wording.right || null);
  return normalizeFacetText(preferred);
}

function alignedOtherWordingText(node, side = 'replay') {
  const wording = node?.facets?.wording;
  if (!wording || typeof wording !== 'object') return '';
  const other = side === 'replay'
    ? (wording.right || null)
    : (wording.left || null);
  return normalizeFacetText(other);
}

function renderAlignedSemanticFacet(kind, facet, depth = 0, side = 'replay') {
  if (!facet || typeof facet !== 'object') return '';
  const preferred = side === 'replay'
    ? (facet.left || null)
    : (facet.right || null);
  const other = side === 'replay'
    ? (facet.right || null)
    : (facet.left || null);
  const badge = `<span class="structure-badge">${esc(semanticFacetKindLabel(kind))}</span>`;
  const text = normalizeFacetText(preferred);
  const otherText = normalizeFacetText(other);
  const textHtml = preferred
    ? renderStructureTextDiff(text, otherText, side)
    : `<div class="structure-text"><span class="diff-absent-text">puuttuu</span></div>`;
  const missingCls = preferred ? '' : ' structure-node-missing';
  const basisCls = facet.match_basis ? ` structure-node-basis-${escAttr(facet.match_basis)}` : '';
  return `<div class="structure-node structure-node-facet${missingCls}${basisCls}" style="margin-left:${Math.max(0, depth) * 16}px">
    <div class="structure-row">${badge}${textHtml}</div>
  </div>`;
}

function renderFallbackStructureFacet(kind, preferredFacet, otherFacet, depth = 0, side = 'replay') {
  const badge = `<span class="structure-badge">${esc(semanticFacetKindLabel(kind))}</span>`;
  const text = normalizeFacetText(preferredFacet);
  const otherText = normalizeFacetText(otherFacet);
  const textHtml = preferredFacet
    ? renderStructureTextDiff(text, otherText, side)
    : `<div class="structure-text"><span class="diff-absent-text">puuttuu</span></div>`;
  const missingCls = preferredFacet ? '' : ' structure-node-missing';
  return `<div class="structure-node structure-node-facet${missingCls}" style="margin-left:${Math.max(0, depth) * 16}px">
    <div class="structure-row">${badge}${textHtml}</div>
  </div>`;
}

function isEditorialOrEmptyShell(node) {
  if (!node) return false;
  const left = node.left || null;
  const right = node.right || null;
  // Only hide when the OTHER side has no real content.
  // If both sides exist with real content, it's a genuine diff — show it.
  const leftIsEditorial = !left || (left.label_basis === 'editorial_repeal_notice') || (left.label_basis === 'repeal_placeholder');
  const rightIsEditorial = !right || (right.label_basis === 'editorial_repeal_notice');
  if (leftIsEditorial && rightIsEditorial) return true;
  // Replay repeal placeholder + oracle absent = confirmed repeal, treat as tombstone
  if (left && left.label_basis === 'repeal_placeholder' && !right) return true;
  // empty oracle shell: oracle-only node with no text (e.g. expired temporary law)
  if (!left && right && !right.text && !hasAnyText(right)) return true;
  return false;
}

function hasAnyText(node) {
  if (!node) return false;
  if (node.text) return true;
  const facets = node.facets || {};
  for (const k of Object.keys(facets)) {
    const f = facets[k];
    if (f && (f.text || (f.left && f.left.text) || (f.right && f.right.text))) return true;
  }
  for (const child of (node.children || [])) {
    if (hasAnyText(child.left || child.right || child)) return true;
  }
  return false;
}

function renderAlignedSemanticNode(node, depth = 0, side = 'replay') {
  // Render editorial repeal notice nodes as muted tombstones instead of hiding
  if (isEditorialOrEmptyShell(node)) {
    const present = node?.left || node?.right || null;
    if (!present) return '';
  const label = structureNodeDisplayLabel(present);
  const badge = label ? `<span class="structure-badge">${esc(label)}</span>` : '';
    const textHtml = `<div class="structure-text"><span class="tombstone">kumottu</span></div>`;
    return `<div class="structure-node structure-node-missing" style="margin-left:${Math.max(0, depth) * 16}px">
      <div class="structure-row">${badge}${textHtml}</div>
    </div>`;
  }
  const preferred = side === 'replay'
    ? (node?.left || null)
    : (node?.right || null);
  const other = side === 'replay'
    ? (node?.right || null)
    : (node?.left || null);
  const current = preferred || other;
  if (!node || !current) return '';
  const label = structureNodeDisplayLabel(current);
  const badge = label ? `<span class="structure-badge">${esc(label)}</span>` : '';
  const text = alignedWordingText(node, side) || normWs(preferred?.text || '');
  const otherText = alignedOtherWordingText(node, side) || normWs(other?.text || '');
  const textHtml = preferred
    ? renderStructureTextDiff(text, otherText, side)
    : `<div class="structure-text"><span class="diff-absent-text">puuttuu</span></div>`;
  const missingCls = preferred ? '' : ' structure-node-missing';
  const basisCls = node.match_basis ? ` structure-node-basis-${escAttr(node.match_basis)}` : '';
  const facetsHtml = alignedFacetEntries(node.facets)
    .map(([kind, facet]) => renderAlignedSemanticFacet(kind, facet, depth + 1, side))
    .join('');
  const childrenHtml = (Array.isArray(node.children) ? node.children : [])
    .map(child => renderAlignedSemanticNode(child, depth + 1, side))
    .join('');
  return `<div class="structure-node${missingCls}${basisCls}" style="margin-left:${Math.max(0, depth) * 16}px">
    <div class="structure-row">${badge}${textHtml}</div>${facetsHtml}${childrenHtml}
  </div>`;
}

function collectStructureDiffStats(left, right) {
  const stats = { structural: 0, label: 0, text: 0 };
  function visit(a, b) {
    if (!a && !b) return;
    if (!a || !b) {
      stats.structural += 1;
      return;
    }
    const aKind = String(a.kind || '');
    const bKind = String(b.kind || '');
    const aLabel = String(a.label || '');
    const bLabel = String(b.label || '');
    const aVisibleLabel = String(a.visible_label || '');
    const bVisibleLabel = String(b.visible_label || '');
    if (aKind !== bKind) stats.structural += 1;
    else if (aLabel !== bLabel || aVisibleLabel !== bVisibleLabel) stats.label += 1;
    if (normWs(a.text || '') !== normWs(b.text || '')) stats.text += 1;
    const aFacets = a?.facets && typeof a.facets === 'object' ? a.facets : {};
    const bFacets = b?.facets && typeof b.facets === 'object' ? b.facets : {};
    for (const facetKind of ['heading', 'intro']) {
      const aText = normalizeFacetText(aFacets[facetKind]);
      const bText = normalizeFacetText(bFacets[facetKind]);
      if (!aText && !bText) continue;
      if (!aText || !bText || aText !== bText) stats.text += 1;
    }
    const aligned = alignStructureChildren(rawStructureChildren(a), rawStructureChildren(b));
    aligned.forEach(([leftChild, rightChild]) => visit(leftChild, rightChild));
  }
  visit(left, right);
  return stats;
}

function structureSummaryText(stats) {
  if (!stats.structural && stats.label && !stats.text) return 'Sama rakenne, eri tunnus.';
  if (!stats.structural && stats.label && stats.text) return 'Sama rakenne, eri tunnus ja teksti.';
  if (!stats.structural && stats.text) return 'Sama rakenne, eri teksti.';
  if (!stats.structural && !stats.text) return 'Sama rakenne ja teksti.';
  if (stats.structural && !stats.text) return 'Rakenne eroaa.';
  return 'Rakenne ja teksti eroavat.';
}

function renderStructureNodeAligned(node, counterpart, depth = 0, side = 'replay') {
  const current = node || counterpart;
  if (!current) return '';
  const children = Array.isArray(node?.children) ? node.children : [];
  const otherChildren = Array.isArray(counterpart?.children) ? counterpart.children : [];
  const text = normWs(node?.text || '');
  const otherText = normWs(counterpart?.text || '');
  const label = structureNodeLabel(current);
  const pad = Math.max(0, depth) * 16;
  const badge = label ? `<span class="structure-badge">${esc(label)}</span>` : '';
  const missingCls = node ? '' : ' structure-node-missing';
  const textHtml = node
    ? renderStructureTextDiff(text, otherText, side)
    : `<div class="structure-text"><span class="diff-absent-text">puuttuu</span></div>`;
  const facets = node?.facets && typeof node.facets === 'object' ? node.facets : {};
  const otherFacets = counterpart?.facets && typeof counterpart.facets === 'object' ? counterpart.facets : {};
  const facetsHtml = ['heading', 'intro']
    .filter(kind => facets[kind] || otherFacets[kind])
    .map(kind => renderFallbackStructureFacet(kind, facets[kind] || null, otherFacets[kind] || null, depth + 1, side))
    .join('');
  const childrenHtml = alignStructureChildren(children, otherChildren)
    .map(([left, right]) => renderStructureNodeAligned(side === 'replay' ? left : right, side === 'replay' ? right : left, depth + 1, side))
    .join('');
  return `<div class="structure-node${missingCls}" style="margin-left:${pad}px">
    <div class="structure-row">${badge}${textHtml}</div>${facetsHtml}${childrenHtml}
  </div>`;
}

// ── XML/HTML topology triple-view ─────────────────────────────────────────────
// missingFromXml: sections present in HTML but not in XML
// extraInXml: sections present in XML but not in HTML
function renderXmlHtmlTripleView(missingFromXml, extraInXml) {
  // missingFromXml: in HTML but not in XML — the XML is missing these sections
  // extraInXml:     in XML but not in HTML — the HTML is missing these sections
  // We show only the diverging sections (the interesting diagnostic data).
  const htmlOnly = missingFromXml.map(s => ({ label: s, inHtml: true, inXml: false }));
  const xmlOnly  = extraInXml.map(s => ({ label: s, inHtml: false, inXml: true }));

  function presenceMark(present) {
    return present
      ? `<span class="topo-present" title="Löytyy">&#10003;</span>`
      : `<span class="topo-absent"  title="Puuttuu">&#8212;</span>`;
  }

  function renderGroup(items, titleHtml) {
    if (!items.length) return '';
    const rowsHtml = items.map(it => `
      <tr>
        <td class="topo-label">${esc(it.label)}</td>
        <td class="topo-cell">${presenceMark(it.inHtml)}</td>
        <td class="topo-cell">${presenceMark(it.inXml)}</td>
      </tr>`).join('');
    return `
      <thead><tr><th>${titleHtml}</th><th>HTML</th><th>XML</th></tr></thead>
      <tbody>${rowsHtml}</tbody>`;
  }

  const htmlOnlySection = renderGroup(htmlOnly, 'HTML:ssä, ei XML:ssä');
  const xmlOnlySection  = renderGroup(xmlOnly,  'XML:ssä, ei HTML:ssä');

  if (!htmlOnlySection && !xmlOnlySection) return '';

  return `<details class="topo-triple-section" open>
  <summary class="topo-triple-summary">
    Rakennevertailu: XML vs HTML
    ${missingFromXml.length ? `<span class="topo-badge topo-html-only">+${missingFromXml.length} HTML</span>` : ''}
    ${extraInXml.length    ? `<span class="topo-badge topo-xml-only">+${extraInXml.length} XML</span>` : ''}
  </summary>
  <div class="topo-triple-body">
    <table class="topo-table">
      ${htmlOnlySection}
      ${xmlOnlySection}
    </table>
  </div>
</details>`;
}

// ── Table diff rendering ──────────────────────────────────────────────────────

/**
 * Collect all tables from a (possibly nested) structure node.
 * Tables live as a top-level `tables` field on nodes that have table content.
 */
function collectTablesFromNode(node) {
  if (!node || typeof node !== 'object') return [];
  const tables = [];
  if (Array.isArray(node.tables) && node.tables.length) {
    tables.push(...node.tables);
  }
  for (const child of (node.children || [])) {
    tables.push(...collectTablesFromNode(child));
  }
  return tables;
}

/**
 * Render a single HTML table from a table data dict.
 * Highlights cells that differ between oracleTable and replayTable by column key.
 */
function renderSingleTableDiff(table, otherTable, side) {
  const columns = Array.isArray(table.columns) ? table.columns : [];
  const rows = Array.isArray(table.rows) ? table.rows : [];
  if (!rows.length) return '';

  // Build a lookup of other-side cells by row_key value + column_key for fast diff.
  const otherCells = new Map(); // `${rowKeyValue}|${colKey}` → text
  if (otherTable && Array.isArray(otherTable.rows)) {
    for (const oRow of otherTable.rows) {
      const rk = oRow.row_key ? String(oRow.row_key.value || '') : '';
      for (const cell of (Array.isArray(oRow.cells) ? oRow.cells : [])) {
        const ck = String(cell.column_key || cell.text || '');
        otherCells.set(`${rk}|${ck}`, String(cell.text || ''));
      }
    }
  }

  let html = '<table class="table-diff">';
  if (columns.length) {
    html += '<thead><tr>';
    for (const col of columns) {
      html += `<th class="table-diff-th">${esc(col)}</th>`;
    }
    html += '</tr></thead>';
  }
  html += '<tbody>';
  for (const row of rows) {
    const rk = row.row_key ? String(row.row_key.value || '') : '';
    html += '<tr>';
    for (const cell of (Array.isArray(row.cells) ? row.cells : [])) {
      const cellText = String(cell.text || '');
      const ck = String(cell.column_key || cellText || '');
      const otherText = otherCells.get(`${rk}|${ck}`);
      let cls = 'table-diff-td';
      if (otherTable !== null && otherText !== undefined && otherText !== cellText) {
        cls += side === 'replay' ? ' table-cell-replay-diff' : ' table-cell-oracle-diff';
      } else if (otherTable !== null && otherText === undefined) {
        cls += ' table-cell-unique';
      }
      const rowspan = (cell.rowspan && cell.rowspan > 1) ? ` rowspan="${cell.rowspan}"` : '';
      const colspan = (cell.colspan && cell.colspan > 1) ? ` colspan="${cell.colspan}"` : '';
      html += `<td class="${cls}"${rowspan}${colspan}>${esc(cellText)}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  return html;
}

/**
 * Render a table diff panel for a section card.
 * Shows LawVM and Finlex tables side by side with cell-level diff highlighting.
 * Returns empty string when neither side has table data.
 */
function renderTableDiffPanel(replayNode, oracleNode) {
  const replayTables = replayNode ? collectTablesFromNode(replayNode) : [];
  const oracleTables = oracleNode ? collectTablesFromNode(oracleNode) : [];
  if (!replayTables.length && !oracleTables.length) return '';

  const count = Math.max(replayTables.length, oracleTables.length);
  let html = '<div class="table-diff-section">';
  html += '<div class="diff-label">Taulukot</div>';
  for (let i = 0; i < count; i++) {
    const rt = replayTables[i] || null;
    const ot = oracleTables[i] || null;
    const caption = (rt && rt.caption) || (ot && ot.caption) || '';
    html += '<div class="table-diff-pair">';
    if (caption) html += `<div class="table-diff-caption">${esc(caption)}</div>`;
    html += '<div class="table-diff-grid">';
    html += '<div class="table-diff-pane">';
    html += '<div class="table-diff-pane-title">LawVM</div>';
    html += rt ? renderSingleTableDiff(rt, ot, 'replay') : '<span class="diff-absent-text">puuttuu</span>';
    html += '</div>';
    html += '<div class="table-diff-pane">';
    html += '<div class="table-diff-pane-title">Finlex</div>';
    html += ot ? renderSingleTableDiff(ot, rt, 'oracle') : '<span class="diff-absent-text">puuttuu</span>';
    html += '</div>';
    html += '</div>'; // table-diff-grid
    html += '</div>'; // table-diff-pair
  }
  html += '</div>'; // table-diff-section
  return html;
}

function renderStructurePair(row) {
  const storedArtifact = parseStoredSemanticArtifact(row);
  const storedEvents = parseStoredSemanticEvents(row);
  const storedStats = parseStoredSemanticStats(row);
  const hasStoredArtifact = !!storedArtifact;
  const oracle = hasStoredArtifact ? null : parseStructure(row.oracle_structure);
  const replay = hasStoredArtifact ? null : parseStructure(row.replay_structure);
  if (!oracle && !replay && !storedArtifact) return '';
  const replayChildren = Array.isArray(replay?.children) ? replay.children : [];
  const oracleChildren = Array.isArray(oracle?.children) ? oracle.children : [];
  const aligned = hasStoredArtifact ? storedArtifact : alignStructureChildren(replayChildren, oracleChildren);
  const stats = storedStats || (hasStoredArtifact
    ? { structural: 0, label: 0, text: 0 }
    : collectStructureDiffStats(replay, oracle));
  const summary = (storedStats && storedStats.summary)
    || fiText(row.structure_diff_summary || '')
    || (hasStoredArtifact ? 'LawVM:n semanttinen kohdistus.' : structureSummaryText(stats));
  const structuralClass = ((storedStats && storedStats.kind) || row.structure_diff_kind || '').includes('structure') || stats.structural || stats.label
    ? ' structure-summary-structural'
    : '';
  const originText = hasStoredArtifact
    ? 'LawVM:n semanttinen kohdistus'
    : 'Rakenteellinen yhteensovitus';

  // Table diff panel: render below the structure grid when table data is present.
  // Use the raw (non-aligned) oracle/replay structures to get table data.
  const replayForTables = hasStoredArtifact ? parseStructure(row.replay_structure) : replay;
  const oracleForTables = hasStoredArtifact ? parseStructure(row.oracle_structure) : oracle;
  const tableDiffHtml = renderTableDiffPanel(replayForTables, oracleForTables);

  return `<div class="structure-section">
    <div class="diff-label">Rakenne</div>
    <div class="structure-summary${structuralClass}">${esc(summary)}</div>
    <div class="structure-origin">${esc(originText)}</div>
    <div class="structure-grid">
      <div class="structure-pane">
        <div class="structure-pane-title">LawVM</div>
        ${hasStoredArtifact
          ? renderAlignedSemanticNode(aligned, 0, 'replay')
          : renderStructureNodeAligned(replay, oracle, 0, 'replay')}
      </div>
      <div class="structure-pane">
        <div class="structure-pane-title">Finlex</div>
        ${hasStoredArtifact
          ? renderAlignedSemanticNode(aligned, 0, 'oracle')
          : renderStructureNodeAligned(oracle, replay, 0, 'oracle')}
      </div>
    </div>
    ${tableDiffHtml}
    ${renderSemanticEvents(storedEvents)}
  </div>`;
}

// ── Compact missing-section group ────────────────────────────────────────────
function renderMissingGroup(rows) {
  const blame = rows[0].blame_source;
  const blameTitle = rows[0].blame_title || '';
  const amendUrl = rows[0].amendment_url || (blame ? finlexAlkupUrl(blame) : null);
  const blameLink = amendUrl
    ? `<a href="${esc(amendUrl)}" target="_blank" rel="noopener">${esc(blame)}</a>`
    : esc(blame);

  const secs = rows.map(r => fixSecDisplay(r.section_display || r.section || '?'));

  return `<div class="proof-card last-touch">
  <div class="card-hdr">
    <span class="card-sec">${secs.length} pykälää puuttuu</span>
    <span class="card-diag">Puuttuvat pykälät</span>
    <span class="card-blame">Muutossäädös: ${blameLink}${blameTitle ? ' — ' + esc(blameTitle.slice(0,60)) : ''}</span>
  </div>
  <div style="padding:8px 14px;font-size:12px;color:var(--text);line-height:1.8">
    ${secs.map(s => `<span style="display:inline-block;background:var(--border);padding:1px 7px;border-radius:3px;margin:2px 3px;font-size:11px">${esc(s)}</span>`).join('')}
  </div>
  <div class="card-footer">
    <span class="touch-badge clean">&#10003; Viimeisin muutos</span>
    <div class="verify-links">
      ${amendUrl ? `<a class="verify-link" href="${esc(amendUrl)}" target="_blank" rel="noopener">Muutossäädös ↗</a>` : ''}
    </div>
  </div>
</div>`;
}

// ── Amendment timeline bar ────────────────────────────────────────────────────
function renderTimeline(chain, blameSource) {
  if (!chain || chain.length < 2) return '';
  const items = chain.map(c => {
    const isBlame = c.is_blame_source === 1;
    const isLater = c.is_later_touch === 1;
    const cls = isBlame ? 'tl-blame' : isLater ? 'tl-later' : 'tl-earlier';
    const marker = isBlame ? '\u25c9' : isLater ? '\u25cb' : '\u25cf';
    const url = finlexAlkupUrl(c.amendment_id);
    const link = url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(c.amendment_id)}</a>`
      : esc(c.amendment_id);
    const label = isBlame ? ' \u2190 vertailu' : isLater ? ' (my\u00f6hempi)' : '';
    return `<span class="tl-item ${cls}" title="${esc(c.amendment_title || c.amendment_id)}">${marker} ${link}${label}</span>`;
  }).join('<span class="tl-sep">\u2192</span>');
  return `<details class="timeline-section">
    <summary class="timeline-summary">Muutoshistoria (${chain.length} s\u00e4\u00e4d\u00f6st\u00e4)</summary>
    <div class="timeline-bar">${items}</div>
  </details>`;
}

// ── Proof card ───────────────────────────────────────────────────────────────
function renderCard(row, firstCard, chain, manualReview) {
  const family = row.error_family || 'oracle_section_stale';
  const secLabel = fixSecDisplay(row.section_display || row.section || '?');
  const fBadge = familyBadgeLabel(family);
  const rawDetail = String(row.suspect_detail || '');
  const detailText = fiText(rawDetail);

  // Temporal status and tombstone detection
  const temporalStatus = detectTemporalStatus(row);
  const isTombstone = isEditorialTombstoneRow(row);
  const temporalBadge = renderTemporalBadge(temporalStatus);

  const finlexAjUrl = row.finlex_url || finlexAjantasaUrl(row.statute_id);
  const sectionUrl  = row.section_url || '';
  const amendUrl    = row.amendment_url || (row.blame_source ? finlexAlkupUrl(row.blame_source) : null);
  let verifyLinks = '';
  if (sectionUrl)   verifyLinks += `<a class="verify-link" href="${esc(sectionUrl)}" target="_blank" rel="noopener">Finlex pykälä ↗</a>`;
  else if (finlexAjUrl) verifyLinks += `<a class="verify-link" href="${esc(finlexAjUrl)}" target="_blank" rel="noopener">Finlex ajantasa ↗</a>`;
  if (amendUrl)      verifyLinks += `<a class="verify-link" href="${esc(amendUrl)}"    target="_blank" rel="noopener">Muutossäädös ↗</a>`;

  // ── Cutoff drift card ──
  if (family === 'oracle_cutoff_version_drift') {
    const detail = rawDetail;
    // Content-based proof: "content_proof: behind_by=N matched_at=X unapplied=A,B,C"
    const cp = detail.match(/^content_proof:\s+behind_by=(\d+)\s+matched_at=(\S*)\s+unapplied=(.+)$/);
    const m = detail.match(/^(\d+\/\d+)\s+eff\s+(\d{4}-\d{2}-\d{2})\s*>\s*cutoff\s+(\d{4}-\d{2}-\d{2})$/);
    let bodyHtml;
    if (cp) {
      const [, behindBy, matchedAt, unappliedStr] = cp;
      const unapplied = unappliedStr.split(',').filter(Boolean);
      const amendLinks = unapplied.map(id => {
        const aUrl = finlexAlkupUrl(id);
        return aUrl
          ? `<a href="${esc(aUrl)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(id)}</a>`
          : esc(id);
      });
      bodyHtml = `
        <p>Finlex on <strong>${esc(behindBy)} muutosta j\u00e4ljess\u00e4</strong> (sis\u00e4lt\u00f6pohjainen todiste).</p>
        <table style="font-size:12px;color:var(--dim);margin-top:8px;border-collapse:collapse">
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Puuttuvat muutokset</td><td>${amendLinks.join(', ')}</td></tr>
          ${matchedAt ? `<tr><td style="padding:2px 12px 2px 0;color:var(--text)">Viimeisin p\u00e4ivitys</td><td>${esc(matchedAt)}</td></tr>` : ''}
        </table>`;
    } else if (m) {
      const [, amendId, effDate, cutDate] = m;
      const fmtDate = d => { const [y,mo,da] = d.split('-'); return `${parseInt(da)}.${parseInt(mo)}.${y}`; };
      const amendUrl2 = finlexAlkupUrl(amendId);
      const amendLink = amendUrl2
        ? `<a href="${esc(amendUrl2)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(amendId)}</a>`
        : esc(amendId);
      bodyHtml = `
        <p>Säädöstä on muutettu (${amendLink}), mutta Finlex ei ole päivittänyt ajantasatekstiä.</p>
        <table style="font-size:12px;color:var(--dim);margin-top:8px;border-collapse:collapse">
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Muutossäädös</td><td>${amendLink}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Voimaantulo</td><td>${fmtDate(effDate)}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Finlexin versio</td><td>${fmtDate(cutDate)}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Viive</td><td style="color:var(--amber)">${esc(fmtDate(cutDate))} → ${esc(fmtDate(effDate))} puuttuu</td></tr>
        </table>`;
    } else {
      bodyHtml = `<p>Finlexin ajantasaversio on jäänyt jälkeen säädöskokoelman muutoksista.</p>
        ${detail ? `<p style="font-size:12px;color:var(--dim);margin-top:6px">${esc(detailText)}</p>` : ''}`;
    }
    let driftVerify = verifyLinks;
    if (cp) {
      const unapplied = cp[3].split(',').filter(Boolean);
      unapplied.forEach(id => {
        const aUrl = finlexAlkupUrl(id);
        if (aUrl) driftVerify += `<a class="verify-link" href="${esc(aUrl)}" target="_blank" rel="noopener">Muutossäädös ${esc(id)} ↗</a>`;
      });
    } else if (m) {
      const aUrl = finlexAlkupUrl(m[1]);
      if (aUrl) driftVerify += `<a class="verify-link" href="${esc(aUrl)}" target="_blank" rel="noopener">Muutossäädös ${esc(m[1])} ↗</a>`;
    }
    return `<div class="proof-card last-touch" style="border-left-color:var(--amber)">
    <div class="card-hdr">
      <span class="card-sec">${esc(secLabel)}</span>
      ${fBadge ? `<span class="card-diag" style="background:#2a1f00;color:var(--amber)">${esc(fBadge)}</span>` : ''}
    </div>
    <div style="padding:10px 14px;font-size:13px;color:var(--text)">${bodyHtml}</div>
    ${renderManualReview(manualReview)}
    <div class="card-footer">
      <div class="verify-links">${driftVerify}</div>
    </div>
  </div>`;
  }

  // ── Metadata inconsistency card (heuristic version drift) ──
  if (family === 'oracle_metadata_inconsistency') {
    const detail = rawDetail;
    // Formats:
    //   "YYYY/NNN eff YYYY-MM-DD > cutoff YYYY-MM-DD"
    //   "YYYY/NNN expires YYYY-MM-DD < cutoff YYYY-MM-DD"
    //   "pending: YYYY/NNN eff YYYY-MM-DD > cutoff YYYY-MM-DD"
    //   "base_oracle_only despite amendment YYYY/NNN eff YYYY-MM-DD <= cutoff YYYY-MM-DD"
    const fmtDate = d => { const [y,mo,da] = d.split('-'); return `${parseInt(da)}.${parseInt(mo)}.${y}`; };
    const mEff = detail.match(/^(\d+\/\d+)\s+eff\s+(\d{4}-\d{2}-\d{2})\s*>\s*cutoff\s+(\d{4}-\d{2}-\d{2})$/);
    const mExpires = detail.match(/^(\d+\/\d+)\s+expires\s+(\d{4}-\d{2}-\d{2})\s*<\s*cutoff\s+(\d{4}-\d{2}-\d{2})$/);
    const mPending = detail.match(/^pending:\s+(\S+)\s+eff\s+(\d{4}-\d{2}-\d{2})\s*>\s*cutoff\s+(\d{4}-\d{2}-\d{2})$/);
    const mBase = detail.match(/^base_oracle_only despite amendment\s+(\S+)\s+eff\s+(\d{4}-\d{2}-\d{2})\s*<=?\s*cutoff\s+(\d{4}-\d{2}-\d{2})$/);
    let bodyHtml;
    let metaVerifyLinks = verifyLinks;
    if (mEff) {
      const [, amendId, effDate, cutDate] = mEff;
      const amendUrl2 = finlexAlkupUrl(amendId);
      const amendLink = amendUrl2
        ? `<a href="${esc(amendUrl2)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(amendId)}</a>`
        : esc(amendId);
      bodyHtml = `
        <p>Metatietojen perusteella Finlexin ajantasaversio ei vastaa viimeisint\u00e4 muutosta.</p>
        <table style="font-size:12px;color:var(--dim);margin-top:8px;border-collapse:collapse">
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Ep\u00e4ilty puuttuva muutos</td><td>${amendLink}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Voimaantulo</td><td>${fmtDate(effDate)}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Finlexin versio</td><td>${fmtDate(cutDate)}</td></tr>
        </table>`;
      if (amendUrl2) metaVerifyLinks += `<a class="verify-link" href="${esc(amendUrl2)}" target="_blank" rel="noopener">Muutoss\u00e4\u00e4d\u00f6s ${esc(amendId)} \u2197</a>`;
    } else if (mExpires) {
      const [, amendId, expiryDate, cutDate] = mExpires;
      const amendUrl2 = finlexAlkupUrl(amendId);
      const amendLink = amendUrl2
        ? `<a href="${esc(amendUrl2)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(amendId)}</a>`
        : esc(amendId);
      bodyHtml = `
        <p>Metatietojen perusteella Finlexin ajantasaversio viittaa vanhentuneeseen muutokseen.</p>
        <table style="font-size:12px;color:var(--dim);margin-top:8px;border-collapse:collapse">
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Viitattu muutoss\u00e4\u00e4d\u00f6s</td><td>${amendLink}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Vanhentunut</td><td>${fmtDate(expiryDate)}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Finlexin versio</td><td>${fmtDate(cutDate)}</td></tr>
        </table>`;
      if (amendUrl2) metaVerifyLinks += `<a class="verify-link" href="${esc(amendUrl2)}" target="_blank" rel="noopener">Muutoss\u00e4\u00e4d\u00f6s ${esc(amendId)} \u2197</a>`;
    } else if (mPending) {
      const [, amendId, effDate, cutDate] = mPending;
      const amendUrl2 = finlexAlkupUrl(amendId);
      const amendLink = amendUrl2
        ? `<a href="${esc(amendUrl2)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(amendId)}</a>`
        : esc(amendId);
      bodyHtml = `
        <p>Finlexin metatiedot viittaavat muutokseen joka ei ole viel\u00e4 voimassa.</p>
        <table style="font-size:12px;color:var(--dim);margin-top:8px;border-collapse:collapse">
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Odottava muutoss\u00e4\u00e4d\u00f6s</td><td>${amendLink}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Voimaantulo</td><td>${fmtDate(effDate)}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Finlexin versio</td><td>${fmtDate(cutDate)}</td></tr>
        </table>`;
      if (amendUrl2) metaVerifyLinks += `<a class="verify-link" href="${esc(amendUrl2)}" target="_blank" rel="noopener">Muutoss\u00e4\u00e4d\u00f6s ${esc(amendId)} \u2197</a>`;
    } else if (mBase) {
      const [, amendId, effDate, cutDate] = mBase;
      const amendUrl2 = finlexAlkupUrl(amendId);
      const amendLink = amendUrl2
        ? `<a href="${esc(amendUrl2)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(amendId)}</a>`
        : esc(amendId);
      bodyHtml = `
        <p>Finlex n\u00e4ytt\u00e4\u00e4 peruss\u00e4\u00e4d\u00f6ksen, vaikka muutos ${amendLink} olisi pit\u00e4nyt soveltaa.</p>
        <table style="font-size:12px;color:var(--dim);margin-top:8px;border-collapse:collapse">
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Muutoss\u00e4\u00e4d\u00f6s</td><td>${amendLink}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Voimaantulo</td><td>${fmtDate(effDate)}</td></tr>
          <tr><td style="padding:2px 12px 2px 0;color:var(--text)">Finlexin versio</td><td>${fmtDate(cutDate)}</td></tr>
        </table>`;
      if (amendUrl2) metaVerifyLinks += `<a class="verify-link" href="${esc(amendUrl2)}" target="_blank" rel="noopener">Muutoss\u00e4\u00e4d\u00f6s ${esc(amendId)} \u2197</a>`;
    } else {
      bodyHtml = `<p>Finlexin metatiedot viittaavat ep\u00e4johdonmukaiseen versiotietoon.</p>
        ${detail ? `<p style="font-size:12px;color:var(--dim);margin-top:6px">${esc(detailText)}</p>` : ''}`;
    }
    return `<div class="proof-card last-touch" style="border-left-color:var(--amber)">
    <div class="card-hdr">
      <span class="card-sec">${esc(secLabel)}</span>
      ${fBadge ? `<span class="card-diag" style="background:#2a1f00;color:var(--amber)">${esc(fBadge)}</span>` : ''}
    </div>
    <div style="padding:10px 14px;font-size:13px;color:var(--text)">${bodyHtml}</div>
    ${renderManualReview(manualReview)}
    <div class="card-footer">
      <span style="font-size:11px;color:var(--dim);font-style:italic">Arvio metatiedoista \u2014 ei sis\u00e4lt\u00f6pohjainen todiste</span>
      <div class="verify-links">${metaVerifyLinks}</div>
    </div>
  </div>`;
  }

  // ── Topology drift card ──
  if (family === 'xml_html_topology_drift') {
    const detail = detailText;
    // oracle_text carries JSON array of sections in HTML but not XML (missing_from_xml)
    // replay_text carries JSON array of sections in XML but not HTML (extra_in_xml)
    let missingFromXml = [];
    let extraInXml = [];
    try { if (row.oracle_text) missingFromXml = JSON.parse(row.oracle_text); } catch(_) {}
    try { if (row.replay_text) extraInXml = JSON.parse(row.replay_text); } catch(_) {}
    const hasTripleData = missingFromXml.length > 0 || extraInXml.length > 0;
    const triplePanel = hasTripleData ? renderXmlHtmlTripleView(missingFromXml, extraInXml) : '';
    return `<div class="proof-card last-touch" style="border-left-color:var(--purple)">
    <div class="card-hdr">
      <span class="card-sec">${esc(secLabel)}</span>
      ${fBadge ? `<span class="card-diag" style="background:#1a1828;color:var(--purple)">${esc(fBadge)}</span>` : ''}
    </div>
    <div style="padding:10px 14px;font-size:13px;color:var(--text)">
      ${detail ? `<p>${esc(detail)}</p>` : '<p>XML- ja HTML-versioiden rakenne eroaa.</p>'}
    </div>
    ${triplePanel}
    ${renderManualReview(manualReview)}
    <div class="card-footer">
      <div class="verify-links">${verifyLinks}</div>
    </div>
  </div>`;
  }

  // ── Cross-chapter oracle section drift card ──
  if (family === 'cross_chapter_oracle_section_drift') {
    const detail = detailText;
    let blameHtml = '';
    if (row.blame_source) {
      const url = amendUrl;
      const link = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(row.blame_source)}</a>` : esc(row.blame_source);
      const tit = row.blame_title ? ` — ${esc(row.blame_title.slice(0, 60))}` : '';
      blameHtml = `<span class="card-blame">Muutossäädös: ${link}${tit}</span>`;
    }
    return `<div class="proof-card last-touch" style="border-left-color:var(--purple)">
    <div class="card-hdr">
      <span class="card-sec">${esc(secLabel)}</span>
      ${fBadge ? `<span class="card-diag" style="background:#1a1828;color:var(--purple)">${esc(fBadge)}</span>` : ''}
      ${blameHtml}
    </div>
    <div style="padding:10px 14px;font-size:13px;color:var(--text)">
      ${detail ? `<p>${esc(detail)}</p>` : '<p>Pykälä löytyy Finlexin ajantasatekstistä väärästä luvusta.</p>'}
    </div>
    ${renderManualReview(manualReview)}
    <div class="card-footer">
      <div class="verify-links">${verifyLinks}</div>
    </div>
  </div>`;
  }

  // ── Corrigendum card ──
  if (family === 'corrigendum_applied') {
    const detail = detailText;
    const wrong = normWs(row.oracle_text);
    const correct = normWs(row.replay_text);
    let blameHtml = '';
    if (row.blame_source) {
      const url = amendUrl;
      const link = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(row.blame_source)}</a>` : esc(row.blame_source);
      blameHtml = `<span class="card-blame">Muutossäädös: ${link}</span>`;
    }
    let diffBody = '';
    if (wrong && correct) {
      diffBody = `
      <div class="diff-section">
        <div class="diff-label">Oikaisu</div>
        <div style="font-size:13px;line-height:1.7;color:var(--text)">
          <span class="diff-del">${esc(wrong)}</span> → <span class="diff-add">${esc(correct)}</span>
        </div>
      </div>`;
    }
    return `<div class="proof-card last-touch" style="border-left-color:var(--green)">
    <div class="card-hdr">
      <span class="card-sec">${esc(secLabel)}</span>
      ${fBadge ? `<span class="card-diag" style="background:#0c2e18;color:var(--green)">${esc(fBadge)}</span>` : ''}
      ${blameHtml}
    </div>
    ${diffBody}
    ${detail && !wrong ? `<div style="padding:10px 14px;font-size:13px;color:var(--text)"><p>${esc(detail)}</p></div>` : ''}
    ${renderManualReview(manualReview)}
    <div class="card-footer">
      <span class="touch-badge clean">&#10003; Julkaistu oikaisuilmoitus</span>
      <div class="verify-links">${verifyLinks}</div>
    </div>
  </div>`;
  }

  // ── Section stale card (default, with diff) ──
  const isLast = row.is_last_touch == 1;
  let cardCls = isLast ? 'last-touch' : 'chain-case';
  // Apply temporal status class
  if (temporalStatus === 'scheduled') cardCls += ' deferred';
  else if (temporalStatus === 'pending_external_resolution') cardCls += ' pending-decree';
  // Apply editorial tombstone class
  if (isTombstone) cardCls += ' editorial-tombstone';

  let blameHtml = '';
  if (row.blame_source) {
    const url  = amendUrl;
    const link = url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(row.blame_source)}</a>`
      : esc(row.blame_source);
    const tit  = row.blame_title ? ` — ${esc(row.blame_title.slice(0, 60))}` : '';
    blameHtml = `<span class="card-blame">Muutossäädös: ${link}${tit}</span>`;
  }

  const jText = normWs(row.johtolause_text);
  function renderJohtolauseWithSpan(text, spanJson) {
    // spanJson is a JSON string "[start, end]" or null/undefined.
    // Offsets are character positions in the normalized text.
    if (!spanJson) return addParagraphs(esc(text));
    let span;
    try { span = JSON.parse(spanJson); } catch (_) { return addParagraphs(esc(text)); }
    if (!Array.isArray(span) || span.length !== 2) return addParagraphs(esc(text));
    const [s, e] = span;
    if (typeof s !== 'number' || typeof e !== 'number' || s < 0 || e > text.length || s >= e) {
      return addParagraphs(esc(text));
    }
    return addParagraphs(esc(text.slice(0, s)) + '<mark class="johtolause-hl">' + esc(text.slice(s, e)) + '</mark>' + esc(text.slice(e)));
  }
  const johtolauseContent = jText
    ? `<div class="johtolause-quote">${renderJohtolauseWithSpan(jText, row.johtolause_char_span)}</div>`
    : `<span class="johtolause-absent">Johtolauseteksti ei saatavilla</span>`;

  let touchHtml;
  if (isLast) {
    touchHtml = `<span class="touch-badge clean">&#10003; Viimeisin muutos</span>`;
  } else {
    let laterLinks = '';
    if (row.later_touches) {
      try {
        const touches = JSON.parse(row.later_touches);
        laterLinks = touches.map(t => {
          const url = finlexAlkupUrl(t);
          return url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(t)}</a>` : esc(t);
        }).join(', ');
      } catch(_) {}
    }
    touchHtml = laterLinks ? `<span class="later-touches">Myöhemmät muutokset: ${laterLinks}</span>` : '';
  }

  return `<div class="proof-card ${cardCls}">
  <div class="card-hdr">
    <span class="card-sec">${esc(secLabel)}</span>
    ${fBadge ? `<span class="card-diag">${esc(fBadge)}</span>` : ''}${temporalBadge}
    ${blameHtml}
  </div>
  <details class="johtolause-section"${firstCard ? ' open' : ''}>
    <summary class="johtolause-summary">
      <span class="johtolause-label">Muutossäädöksen ohje</span>
    </summary>
    <div class="johtolause-body">${johtolauseContent}</div>
  </details>
  ${renderTimeline(chain, row.blame_source)}
  ${renderStructurePair(row)}
  ${renderManualReview(manualReview)}
  <div class="card-footer">
    ${touchHtml}
    <div class="verify-links">${verifyLinks}</div>
  </div>
</div>`;
}

document.getElementById('search-input').addEventListener('input', e => {
  searchQ = e.target.value.trim();
  renderList();
});

// ── Family filter buttons ────────────────────────────────────────────────────
document.querySelectorAll('#filter-bar .toggle-btn[data-family]').forEach(btn => {
  btn.addEventListener('click', () => {
    familyFilter = btn.dataset.family;
    document.querySelectorAll('#filter-bar .toggle-btn[data-family]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderList();
    if (selectedSid) void renderDetail(selectedSid);
  });
});

// ── Tabs ─────────────────────────────────────────────────────────────────────
let activeTab = 'dashboard';
const TYPE_LABEL = { act: 'Laki', decree: 'Asetus', decision: 'Päätös', announcement: 'Ilmoitus' };

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  document.getElementById('panel-dashboard').style.display = tab === 'dashboard' ? 'flex' : 'none';
  document.getElementById('panel-errors').style.display = tab === 'errors' ? 'contents' : 'none';
  document.getElementById('panel-absent').style.display = tab === 'absent' ? 'flex' : 'none';
  document.getElementById('panel-source-absent').style.display = tab === 'source-absent' ? 'flex' : 'none';
  if (tab === 'errors') {
    if (!errorsIndexLoaded) {
      const list = document.getElementById('statute-list');
      list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Haetaan säädösluetteloa…</div>`;
      document.getElementById('statute-count').textContent = 'Ladataan…';
      void ensureErrorsIndex().then(() => {
        if (activeTab === 'errors') renderList();
      });
    } else {
      renderList();
    }
  }
  if (tab === 'absent') {
    if (!absentIndexLoaded) {
      const list = document.getElementById('absent-list');
      list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Haetaan konsolidoimattomia lakeja…</div>`;
      document.getElementById('absent-count').textContent = 'Ladataan…';
      void ensureAbsentIndex().then(() => {
        if (activeTab === 'absent') renderAbsentList();
      });
    } else {
      void renderAbsentList();
    }
  }
  if (tab === 'source-absent') {
    if (!sourceAbsentIndexLoaded) {
      const list = document.getElementById('source-absent-list');
      list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Haetaan lähde-XML-aukkoja…</div>`;
      document.getElementById('source-absent-count').textContent = 'Ladataan…';
      void ensureSourceAbsentIndex().then(() => {
        if (activeTab === 'source-absent') renderSourceAbsentList();
      });
    } else {
      renderSourceAbsentList();
    }
  }
  if (tab === 'dashboard') void renderDashboard();
  void renderTopbarStats();
}

document.getElementById('tab-dashboard').addEventListener('click', () => switchTab('dashboard'));
document.getElementById('tab-errors').addEventListener('click', () => switchTab('errors'));
document.getElementById('tab-absent').addEventListener('click', () => switchTab('absent'));
document.getElementById('tab-source-absent').addEventListener('click', () => switchTab('source-absent'));

// ── Dashboard ────────────────────────────────────────────────────────────────
async function renderDashboard() {
  let stats = {};
  try {
    const r = await q('SELECT * FROM corpus_stats LIMIT 1');
    if (r.length) stats = r[0];
  } catch(_) {}

  const totals = await publicationScopeErrorTotals();
  const sCorr = totals.byCategory.corrigendum_applied || 0;
  const sManual = totals.byCategory.manual_review || 0;
  const sOracleTotal = stats.total_oracle_indexed||0;
  const sSourceAbsent = stats.total_source_absent||0;

  let sAbsent = 0, sCorrections = 0;
  let sAbsentActs = 0, sAbsentAmendedActs = 0, sAbsentDecrees = 0, sAbsentDecisions = 0, sAbsentModernAmendedActs = 0;
  if (hasAbsentTables) {
    try {
      const r = await q(`
        SELECT total_in_force_absent, total_acts, total_decrees, total_decisions,
               total_amended, total_modern_amended, total_stale_known,
               total_metadata_corrections
        FROM absent_ajantasa_stats
        LIMIT 1
      `);
      if (r.length) {
        sAbsent = r[0].total_in_force_absent || 0;
        sCorrections = r[0].total_stale_known || 0;
        metadataCorrectionsCount = r[0].total_metadata_corrections || 0;
        sAbsentActs = r[0].total_acts || 0;
        sAbsentAmendedActs = r[0].total_amended || 0;
        sAbsentDecrees = r[0].total_decrees || 0;
        sAbsentDecisions = r[0].total_decisions || 0;
        sAbsentModernAmendedActs = r[0].total_modern_amended || 0;
      }
    } catch(_) {}
  }

  const totalErrors = totals.totalErrors;
  const liveCount = totals.statutes;
  const sStructural = (totals.byCategory.structural_extra || 0) + (totals.byCategory.structural_topology_drift || 0);

  function card(color, count, title, desc, action) {
    return `<div style="border:1px solid var(--border);border-left:3px solid ${color};border-radius:5px;padding:16px 20px;cursor:pointer" onclick="${action}">
      <div style="font-size:28px;font-weight:700;color:${color};margin-bottom:4px">${count}</div>
      <div style="font-size:14px;font-weight:600;color:var(--bright);margin-bottom:6px">${title}</div>
      <div style="font-size:12px;color:var(--dim);line-height:1.5">${desc}</div>
    </div>`;
  }

  document.getElementById('panel-dashboard').innerHTML = `
  <div style="max-width:900px;margin:0 auto;padding:30px 24px">
    <h2 style="font-size:20px;font-weight:700;color:var(--bright);margin-bottom:6px">Finlex-tietokannan laadunvalvonta</h2>
    <p style="font-size:13px;color:var(--dim);margin-bottom:24px;line-height:1.6">
      LawVM-kääntäjä toistaa Suomen säädöskokoelman muutokset mekaanisesti ja vertaa tulosta Finlexin ajantasatekstiin.
      Eroavuudet ovat kohtia, joissa Finlex ei ole päivittänyt konsolidoitua lakitekstiä vastaamaan säädöskokoelmassa julkaistuja muutoksia.
      Jokainen havainto sisältää todistusaineiston: muutossäädöksen numeron, johtolauseen ja eroavuuden.
    </p>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-bottom:28px">
      ${card('var(--green)', sCorr, 'Oikaisuilmoituksia',
        `Säädöskokoelmassa julkaistuja oikaisuja (corrigenda), joilla on muutossäädöksen johtolauseessa todistettu virhe ja sen korjaus.`,
        "switchTab('errors');document.querySelector('[data-family=corrigendum_applied]').click()")}
      ${card('var(--dim)', sManual, 'Manuaalitarkistuksia',
        `Rivejä, jotka julkaisutietokanta on nostanut erikseen manuaaliseen tarkistushavaintoon.`,
        "switchTab('errors')")}
      ${card('var(--dim)', sAbsentAmendedActs, 'Puuttuvat ajantasaistetut lait',
        `Muutettuja lakeja joille Finlex ei tarjoa ajantasatekstiä — lakia on muutettu, mutta ajantasaistettua versiota ei ole.<br>
        <span style="color:var(--bright)">${sAbsentModernAmendedActs}</span> muutettu 1995 jälkeen ·
        <span style="color:var(--bright)">${sAbsentAmendedActs}</span> muutettu yhteensä<br>
        ${sCorrections ? `<span style="color:var(--green)">${sCorrections}</span> säädöksen voimassaolometatieto todennettu virheelliseksi.` : ''}`,
        "switchTab('absent')")}
    </div>

    <div style="border:1px solid var(--border);border-radius:5px;padding:16px 20px;margin-bottom:20px">
      <h3 style="font-size:14px;font-weight:600;color:var(--bright);margin-bottom:8px">Yhteensä</h3>
      <table style="font-size:13px;color:var(--text);border-collapse:collapse;width:100%">
        <tr><td style="padding:3px 16px 3px 0">Säädöksiä joissa virheitä</td><td style="font-weight:600">${liveCount}</td></tr>
        <tr><td style="padding:3px 16px 3px 0">Havaitut virheet yhteensä</td><td style="font-weight:600;color:var(--red)">${totalErrors}</td></tr>
        <tr><td style="padding:3px 16px 3px 0;color:var(--dim)">– oikaisuilmoituksia</td><td>${sCorr}</td></tr>
        <tr><td style="padding:3px 16px 3px 0;color:var(--dim)">– rakenteellisia eroja</td><td>${sStructural}</td></tr>
        <tr><td style="padding:3px 16px 3px 0;color:var(--dim)">– manuaalitarkistuksia</td><td>${sManual}</td></tr>
        <tr><td style="padding:3px 16px 3px 0">Puuttuvat ajantasaistetut muutetut lait</td><td style="font-weight:600">${sAbsentAmendedActs}</td></tr>
        ${sCorrections ? `<tr><td style="padding:3px 16px 3px 0">Erikseen todennetut metatietovirheet</td><td style="font-weight:600;color:var(--green)">${sCorrections}</td></tr>` : ''}
        ${sSourceAbsent > 0 ? `<tr><td style="padding:3px 16px 3px 0;color:var(--dim)">Säädöksiä ilman lähde-XML:ää (ei tarkistettavissa)</td><td style="color:var(--dim)">${sSourceAbsent.toLocaleString('fi')} / ${sOracleTotal.toLocaleString('fi')}</td></tr>` : ''}
      </table>
    </div>

    ${sSourceAbsent > 0 ? `
    <div style="border:1px solid var(--border);border-left:3px solid var(--amber);border-radius:5px;padding:14px 20px;margin-bottom:20px;background:#111">
      <div style="font-size:13px;font-weight:600;color:var(--amber);margin-bottom:6px">Lähdeauditointiaukko</div>
      <p style="font-size:12px;color:var(--text);line-height:1.6;margin:0 0 6px 0">
        ${sSourceAbsent.toLocaleString('fi')} säädökseltä (${Math.round(100*sSourceAbsent/sOracleTotal)}&thinsp;% Finlex-hakemistosta)
        puuttuu koneluettava lähde-XML — niitä ei voida tarkistaa tai toistaa digitaalisesti lainkaan.
        Joukossa on <strong style="color:var(--amber)">voimassaolevia säädöksiä</strong>, joiden muutoshistoria ei ole koneellisesti
        todennettavissa. Voimassaolevan lain auditointiaukko on periaatteellinen ongelma riippumatta siitä,
        kuinka vanha säädös on.
      </p>
      <p style="font-size:12px;color:var(--dim);line-height:1.6;margin:0">
        Ylläolevat virheluvut kattavat vain sen ${(sOracleTotal - sSourceAbsent).toLocaleString('fi')} säädöksen joukon,
        joille lähdedata on saatavilla. Aukko ei ole LawVM:n rajoite — Finlexin avoimesta datasta puuttuu rakenne.
      </p>
    </div>` : ''}
    <div style="font-size:11px;color:var(--dim);line-height:1.6">
      <p style="margin-bottom:6px"><strong style="color:var(--text)">LawVM</strong> on avoimen lähdekoodin lakikääntäjä, joka toistaa Suomen lainsäädännön muutoshistorian deterministisesti.</p>
      <p>Tiedot perustuvat Finlexin avoimeen dataan (XML-rajapinta ja säädöskokoelman ZIP-paketti). Havainnot ovat koneellisesti todennettavissa.</p>
    </div>
  </div>`;
}

// ── Absent tab state ─────────────────────────────────────────────────────────
let absentFilter = 'modern';
let absentSearch = '';
let allAbsent = [];

const ABSENT_FILTERS = {
  modern:  { label: '1995+ muutetut',              test: r => r.is_amended && r.type_statute === 'act' && r.year >= 1995 },
  amended: { label: 'Kaikki muutetut',             test: r => r.is_amended && r.type_statute === 'act' },
  all:     { label: 'Kaikki (sis. muuttamattomat)', test: () => true },
};

async function buildAbsentIndex() {
  if (!hasAbsentTables) { allAbsent = []; absentIndexLoaded = true; return; }
  try {
    allAbsent = await q(`
      SELECT statute_id, title, year, type_statute, is_amended, amendment_count,
             latest_amendment, finlex_url, alkup_url,
             stale_known, stale_status, stale_status_fi, stale_confidence,
             stale_confidence_fi, stale_mechanism, stale_mechanism_fi,
             stale_notes, stale_summary_fi
      FROM absent_ajantasa
      ORDER BY year DESC, amendment_count DESC
    `);
    absentIndexLoaded = true;
  } catch(_) { allAbsent = []; }
}

function ensureAbsentIndex() {
  if (absentIndexLoaded) return Promise.resolve();
  if (!absentIndexPromise) {
    absentIndexPromise = buildAbsentIndex().finally(() => {
      absentIndexPromise = null;
    });
  }
  return absentIndexPromise;
}

async function renderAbsentList() {
  if (!absentIndexLoaded) {
    const list = document.getElementById('absent-list');
    if (list) {
      list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Haetaan konsolidoimattomia lakeja…</div>`;
    }
    const count = document.getElementById('absent-count');
    if (count) count.textContent = 'Ladataan…';
    return;
  }
  if (!hasAbsentTables) {
    const list = document.getElementById('absent-list');
    if (list) {
      list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Ei ajantasaistettua versiota -data ei saatavilla tässä julkaisussa.</div>`;
    }
    const count = document.getElementById('absent-count');
    if (count) count.textContent = '';
    return;
  }
  const ql = absentSearch.toLowerCase();
  const filterFn = ABSENT_FILTERS[absentFilter].test;
  const filtered = allAbsent.filter(r => {
    if (ql && !String(r.statute_id).toLowerCase().includes(ql) && !String(r.title||'').toLowerCase().includes(ql)) return false;
    return filterFn(r);
  });

  document.getElementById('absent-count').textContent = `${filtered.length} säädöstä`;

  const list = document.getElementById('absent-list');
  let html = '';

  const corrCount = metadataCorrectionsCount || 0;
  if (corrCount > 0) {
    html += `<div style="padding:8px 14px;font-size:11px;color:var(--green);background:#0c1a10;border-bottom:1px solid var(--border)">
      ✓ <strong>${corrCount}</strong> säädöksen voimassaolometatieto todennettu virheelliseksi (erikseen tarkistettu)
      (merkitty <span style="background:#2a0a0a;color:var(--red);padding:0 4px;border-radius:2px;font-size:10px">⚠ metatieto</span>).
    </div>`;
  }

  if (absentFilter !== 'modern') {
    html += `<div style="padding:8px 14px;font-size:11px;color:var(--amber);background:#1a1700;border-bottom:1px solid var(--border)">
      ⚠ Ennen vuotta 1995 säädettyjen lakien voimassaolometatieto voi olla vanhentunut. Monet näistä ovat tosiasiallisesti kumottuja (esim. EU-jäsenyyden myötä).
    </div>`;
  }

  const max = Math.min(filtered.length, 2000);
  for (let i = 0; i < max; i++) {
    const r = filtered[i];
    const typeLabel = TYPE_LABEL[r.type_statute] || r.type_statute;
    const amendBadge = r.is_amended
      ? `<span class="absent-badge amended">${r.amendment_count} muutosta</span>`
      : '';
    const typeBadge = `<span class="absent-badge type">${esc(typeLabel)}</span>`;
    const staleBadge = r.stale_known
      ? `<span class="absent-badge" style="background:#2a0a0a;color:var(--red)" title="${escAttr(correctionBadgeTitle(r))}">⚠ metatieto</span>`
      : '';
    const alkupLink = r.alkup_url
      ? `<a href="${esc(r.alkup_url)}" target="_blank" rel="noopener" style="font-size:11px;color:var(--accent);text-decoration:none;white-space:nowrap">alkup ↗</a>`
      : '';
    html += `<div class="absent-row"${r.stale_known ? ' style="opacity:0.6"' : ''}>
      <span class="absent-sid">${esc(r.statute_id)}</span>
      <span class="absent-title" title="${escAttr(r.title)}">${esc(r.title || '—')}</span>
      ${staleBadge} ${amendBadge} ${typeBadge} ${alkupLink}
    </div>`;
  }
  if (filtered.length > max) {
    html += `<div style="padding:12px 14px;color:var(--dim);font-size:12px">… ja ${filtered.length - max} lisää</div>`;
  }
  if (!filtered.length) {
    html = `<div style="padding:20px;color:var(--dim);font-size:12px">Ei tuloksia</div>`;
  }
  list.innerHTML = html;
}

function setAbsentFilter(level) {
  absentFilter = level;
  document.querySelectorAll('.absent-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.level === level);
    btn.classList.toggle('active-chain', btn.dataset.level === level && level !== 'modern');
  });
  void renderAbsentList();
}

document.getElementById('btn-absent-amended').addEventListener('click', () => setAbsentFilter('modern'));
document.getElementById('btn-absent-all').addEventListener('click', () => {
  const next = absentFilter === 'modern' ? 'amended' : absentFilter === 'amended' ? 'all' : 'modern';
  setAbsentFilter(next);
});
document.getElementById('absent-search').addEventListener('input', e => {
  absentSearch = e.target.value.trim();
  void renderAbsentList();
});

// ── Source-absent tab ────────────────────────────────────────────────────────
let allSourceAbsent = [];
let sourceAbsentFilter = 'unclear'; // 'unclear' = no repealedBy AND no contentAbsent; 'all_active' = no repealedBy
let sourceAbsentSearch = '';

async function buildSourceAbsentIndex() {
  try {
    allSourceAbsent = await q(`
      SELECT statute_id, year, consolidated_url, page_title, page_status_label,
             content_absent, repealed
      FROM source_absent
      ORDER BY year DESC, statute_id
    `);
    sourceAbsentIndexLoaded = true;
  } catch(_) { allSourceAbsent = []; }
}

function ensureSourceAbsentIndex() {
  if (sourceAbsentIndexLoaded) return Promise.resolve();
  if (!sourceAbsentIndexPromise) {
    sourceAbsentIndexPromise = buildSourceAbsentIndex().finally(() => {
      sourceAbsentIndexPromise = null;
    });
  }
  return sourceAbsentIndexPromise;
}

function renderSourceAbsentList() {
  if (!sourceAbsentIndexLoaded) {
    const list = document.getElementById('source-absent-list');
    if (list) {
      list.innerHTML = `<div style="padding:20px;color:var(--dim);font-size:12px">Haetaan lähde-XML-aukkoja…</div>`;
    }
    const count = document.getElementById('source-absent-count');
    if (count) count.textContent = 'Ladataan…';
    return;
  }
  const ql = sourceAbsentSearch.toLowerCase();
  const filtered = allSourceAbsent.filter(r => {
    if (r.repealed) return false;
    if (sourceAbsentFilter === 'unclear' && r.content_absent) return false;
    if (ql && !String(r.statute_id).toLowerCase().includes(ql) && !String(r.page_title || '').toLowerCase().includes(ql)) return false;
    return true;
  });

  document.getElementById('source-absent-count').textContent = `${filtered.length} säädöstä`;

  const list = document.getElementById('source-absent-list');
  const totalUnclear = allSourceAbsent.filter(r => !r.repealed && !r.content_absent).length;
  const totalAllActive = allSourceAbsent.filter(r => !r.repealed).length;

  let html = `<div style="padding:8px 14px;font-size:11px;color:var(--amber);background:#1a1400;border-bottom:1px solid var(--border);line-height:1.6">
    <strong>${totalUnclear}</strong> säädökseltä puuttuu sekä <code>repealedBy</code> että <code>contentAbsent</code> —
    todennäköisesti voimassa mutta lähde-XML kokonaan poissa. Kaikki ei-kumotut: <strong>${totalAllActive}</strong>.
  </div>`;

  for (const r of filtered) {
    const caNote = r.content_absent
      ? `<span style="font-size:10px;color:var(--dim);margin-left:4px">contentAbsent</span>`
      : `<span style="font-size:10px;color:var(--amber);margin-left:4px">⚠ ei sisältömerkintää</span>`;
    const statusNote = r.page_status_label
      ? `<span class="absent-badge type" title="Finlex-sivun tila">${esc(r.page_status_label)}</span>`
      : '';
    const ajUrl = r.consolidated_url || finlexLainsaadantoUrl(r.statute_id);
    const link = ajUrl
      ? `<a href="${esc(ajUrl)}" target="_blank" rel="noopener" style="font-size:11px;color:var(--accent);text-decoration:none;white-space:nowrap">ajantasa ↗</a>`
      : '';
    html += `<div class="absent-row">
      <span class="absent-sid">${esc(r.statute_id)}</span>
      <span class="absent-title" title="${escAttr(r.page_title || '')}">${esc(r.page_title || '—')}</span>
      <span class="absent-badge type" style="font-size:10px">${esc(String(r.year))}</span>
      ${statusNote} ${caNote} ${link}
    </div>`;
  }
  if (!filtered.length) {
    html += `<div style="padding:20px;color:var(--dim);font-size:12px">Ei tuloksia</div>`;
  }
  list.innerHTML = html;
}

document.querySelectorAll('.source-absent-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    sourceAbsentFilter = btn.dataset.saLevel;
    document.querySelectorAll('.source-absent-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderSourceAbsentList();
  });
});
document.getElementById('source-absent-search').addEventListener('input', e => {
  sourceAbsentSearch = e.target.value.trim();
  renderSourceAbsentList();
});

// ── Boot ──────────────────────────────────────────────────────────────────────
initDB().catch(err => {
  document.getElementById('load-status').textContent = 'Latausvirhe: ' + err.message;
});
