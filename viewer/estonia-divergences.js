const DB_URL = new URLSearchParams(window.location.search).get('db')
  || '../data/estonia/ee_divergences_publication.db';
let db;
let dmp;
let cases = [];
let currentKey = '';

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setProgress(percent, text) {
  $('progress-bar').style.width = `${Math.max(0, Math.min(100, percent))}%`;
  $('load-status').textContent = text;
}

function exec(sql, bind = undefined) {
  const stmt = db.prepare(sql);
  if (bind) stmt.bind(bind);
  const rows = [];
  while (stmt.step()) rows.push(stmt.getAsObject());
  stmt.free();
  return rows;
}

function hasTable(name) {
  const rows = exec(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
    [name],
  );
  return rows.length > 0;
}

async function openFetchedDatabase() {
  setProgress(8, 'Fetching SQLite database…');
  const response = await fetch(DB_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Failed to fetch ${DB_URL}: ${response.status}`);
  const total = Number(response.headers.get('content-length') || 0);
  let bytes;
  if (response.body && total) {
    const reader = response.body.getReader();
    const chunks = [];
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.byteLength;
      setProgress(8 + Math.round((received / total) * 42), `Fetching database ${Math.round(received / 1048576)} MB…`);
    }
    bytes = new Uint8Array(received);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
  } else {
    bytes = new Uint8Array(await response.arrayBuffer());
  }

  setProgress(58, 'Opening SQLite…');
  const SQL = await initSqlJs({
    locateFile: (file) => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.13.0/${file}`,
  });
  db = new SQL.Database(bytes);
}

function bucketLabel(bucket, open) {
  if (bucket) return bucket;
  return open ? 'open' : 'classified';
}

function isCoverageDebt(divergenceType, pair = undefined) {
  return Number(pair?.n_ops ?? 1) === 0
    || String(divergenceType || '').toUpperCase() === 'OPS_MISSING';
}

function isAlignmentShadow(divergence) {
  return Number(divergence?.alignment_shadow || 0) === 1;
}

function isSignalDivergence(divergence, pair = undefined) {
  return !isCoverageDebt(divergence?.divergence_type, pair) && !isAlignmentShadow(divergence);
}

function isReplayEvidenceDivergence(divergence, pair = undefined) {
  return Number(pair?.n_ops ?? 1) > 0
    && !isCoverageDebt(divergence?.divergence_type, pair)
    && !isAlignmentShadow(divergence);
}

function currentMode() {
  return $('mode-filter')?.value || 'signal';
}

function modeIncludesDivergence(divergence, pair = undefined) {
  const mode = currentMode();
  if (mode === 'all') return true;
  if (mode === 'alignment') return isAlignmentShadow(divergence);
  if (mode === 'replay') return isReplayEvidenceDivergence(divergence, pair);
  if (mode === 'coverage') {
    return isCoverageDebt(divergence?.divergence_type, pair) && !isAlignmentShadow(divergence);
  }
  return isSignalDivergence(divergence, pair);
}

function rtUrl(id) {
  return `https://www.riigiteataja.ee/akt/${encodeURIComponent(id)}`;
}

function pct(numerator, denominator, digits = 1) {
  const den = Number(denominator || 0);
  if (!den) return 'n/a';
  return `${((Number(numerator || 0) / den) * 100).toFixed(digits)}%`;
}

function topAddress(address) {
  const parts = String(address || '').split('/');
  return parts.slice(0, 2).join('/');
}

function normalizeDashes(value) {
  return String(value || '').replace(/[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]/g, '-');
}

function normalizeForDiffComparison(value) {
  return normalizeDashes(value)
    .replace(/\s*§\s*/g, ' § ')
    .replace(/\s+([.,;:)])/g, '$1')
    .replace(/(\w)-\s+(\w)/g, '$1-$2')
    .replace(/(\w)-(\w)/g, '$1$2')
    .replace(/\s*\(\d{1,2}\.\d{1,2}\.\d{4}\/\d+\)\s*$/, '')
    .replace(/[.\s]+$/, '')
    .trim();
}

function wordLevelDiff(a, b, preferB = false, normalizeTokens = true) {
  const tokens = (value) => String(value || '').match(/\S+|\s+/g) || [];
  const aTokens = tokens(a);
  const bTokens = tokens(b);
  const vocab = [{ a: '', b: '' }];
  const seen = Object.create(null);
  const tokenKey = (item) => (normalizeTokens ? normalizeDashes(item) : item);
  const register = (items, side) => {
    for (const item of items) {
      const key = tokenKey(item);
      if (!(key in seen)) {
        seen[key] = vocab.length;
        vocab.push({ a: '', b: '' });
      }
      vocab[seen[key]][side] = item;
    }
  };
  register(aTokens, 'a');
  register(bTokens, 'b');
  const encode = (items) => items.map((item) => String.fromCharCode(seen[tokenKey(item)])).join('');
  const raw = dmp.diff_main(encode(aTokens), encode(bTokens), false);
  return raw.map(([op, encoded]) => {
    let text = '';
    for (let i = 0; i < encoded.length; i += 1) {
      const entry = vocab[encoded.charCodeAt(i)];
      if (op === 1) text += entry.b || entry.a;
      else if (op === -1) text += entry.a || entry.b;
      else text += preferB ? (entry.b || entry.a) : (entry.a || entry.b);
    }
    return [op, text];
  });
}

function renderWordDiffHtml(diffs, side) {
  let html = '';
  let i = 0;
  while (i < diffs.length) {
    const [op, chunk] = diffs[i];
    if (op === 0) {
      html += esc(chunk);
      i += 1;
      continue;
    }
    let thisHtml = '';
    while (i < diffs.length) {
      if (diffs[i][0] === 0) {
        if (diffs[i][1].trim() === '' && i + 1 < diffs.length && diffs[i + 1][0] !== 0) {
          thisHtml += esc(diffs[i][1]);
          i += 1;
          continue;
        }
        break;
      }
      const [op2, chunk2] = diffs[i];
      const isThis = side === 'replay' ? op2 === 1 : op2 === -1;
      if (isThis) thisHtml += esc(chunk2);
      i += 1;
    }
    if (thisHtml) html += `<span class="diff-changed">${thisHtml}</span>`;
  }
  return html;
}

function renderSideDiff(text, other, side) {
  const thisText = String(text || '');
  const otherText = String(other || '');
  if (!thisText && !otherText) return '';
  if (!thisText) {
    return side === 'replay'
      ? '<span class="absent">No active replay provision at this address.</span>'
      : '<span class="absent">No active consolidated provision at this address.</span>';
  }
  if (!otherText) return `<span class="diff-changed">${esc(thisText)}</span>`;
  if (normalizeForDiffComparison(thisText) === normalizeForDiffComparison(otherText)) {
    if (thisText === otherText) return esc(thisText);
    const rawDiffs = side === 'replay'
      ? wordLevelDiff(otherText, thisText, true, false)
      : wordLevelDiff(thisText, otherText, false, false);
    return renderWordDiffHtml(rawDiffs, side);
  }

  const commonWords = thisText.split(/\s+/).filter((word) => otherText.includes(word)).length;
  const totalWords = Math.max(thisText.split(/\s+/).length, otherText.split(/\s+/).length, 1);
  if (commonWords / totalWords < 0.3) {
    return `<span class="diff-changed">${esc(thisText)}</span>`;
  }

  const diffs = side === 'replay'
    ? wordLevelDiff(otherText, thisText, true)
    : wordLevelDiff(thisText, otherText, false);
  return renderWordDiffHtml(diffs, side);
}

function simpleDiff(left, right) {
  return [
    renderSideDiff(left, right, 'replay'),
    renderSideDiff(right, left, 'consolidated'),
  ];
}

async function loadIndexes() {
  setProgress(76, 'Loading indexes…');
  if (!hasTable('text_blobs')) {
    throw new Error('This viewer requires the lean Estonia publication DB schema. Rebuild with: uv run lawvm ee-publication-db --workers 12');
  }
  cases = exec(`
    WITH annotated AS (
      SELECT
        d.*,
        CASE
          WHEN d.oracle_text_hash IS NOT NULL
            AND EXISTS (
              SELECT 1
              FROM divergences x
              WHERE x.pair_key = d.pair_key
                AND x.replay_text_hash = d.oracle_text_hash
                AND x.address != d.address
            )
            THEN 1
          WHEN d.replay_text_hash IS NOT NULL
            AND EXISTS (
              SELECT 1
              FROM divergences x
              WHERE x.pair_key = d.pair_key
                AND x.oracle_text_hash = d.replay_text_hash
                AND x.address != d.address
            )
            THEN 1
          ELSE 0
        END AS alignment_shadow
      FROM divergences d
    )
    SELECT
      p.pair_key, p.grupi_id, p.base_id, p.oracle_id, p.title, p.schema,
      p.base_effective, p.oracle_effective, p.version_index, p.version_count,
      CASE WHEN p.version_index = p.version_count - 1 THEN 1 ELSE 0 END AS current_pair,
      p.status, p.source_basis, p.comparison_class, p.n_ops,
      p.divergence_count, p.open_current_divergence_count,
      p.section_total_count, p.section_identical_count, p.section_divergent_count,
      p.section_replay_only_count, p.section_consolidated_only_count,
      p.section_text_total_chars, p.section_text_identical_chars,
      COUNT(d.id) AS shown_divergences,
      SUM(CASE WHEN d.alignment_shadow = 1 THEN 1 ELSE 0 END) AS alignment_shadow_divergences,
      SUM(CASE WHEN (p.n_ops = 0 OR d.divergence_type = 'OPS_MISSING') AND d.alignment_shadow = 0 THEN 1 ELSE 0 END) AS coverage_debt_divergences,
      SUM(CASE WHEN p.n_ops > 0 AND d.divergence_type != 'OPS_MISSING' AND d.alignment_shadow = 0 THEN 1 ELSE 0 END) AS signal_divergences,
      SUM(CASE WHEN p.n_ops > 0 AND d.divergence_type != 'OPS_MISSING' AND d.alignment_shadow = 0 THEN 1 ELSE 0 END) AS replay_evidence_divergences,
      GROUP_CONCAT(DISTINCT COALESCE(d.residual_bucket, 'open')) AS buckets
    FROM pairs p
    LEFT JOIN annotated d ON d.pair_key = p.pair_key
    WHERE p.divergence_count > 0
    GROUP BY p.pair_key
    ORDER BY p.open_current_divergence_count DESC, p.divergence_count DESC, p.title
  `);

  const classes = exec("SELECT comparison_class, COUNT(*) AS n FROM pairs GROUP BY comparison_class ORDER BY n DESC");
  $('class-filter').innerHTML = '<option value="">All comparison classes</option>' +
    classes.map((row) => `<option value="${esc(row.comparison_class)}">${esc(row.comparison_class)} (${row.n})</option>`).join('');

  const buckets = exec("SELECT COALESCE(residual_bucket, 'open') AS bucket, COUNT(*) AS n FROM divergences GROUP BY bucket ORDER BY n DESC");
  $('bucket-filter').innerHTML = '<option value="">All residual buckets</option><option value="open">Open only</option>' +
    buckets.filter((row) => row.bucket !== 'open')
      .map((row) => `<option value="${esc(row.bucket)}">${esc(row.bucket)} (${row.n})</option>`).join('');
}

async function renderStats() {
  const [pairStats] = exec(`
    SELECT
      COUNT(*) AS pairs,
      SUM(divergence_count) AS divergences,
      SUM(open_current_divergence_count) AS open_divergences,
      SUM(CASE WHEN status = 'OK' THEN 1 ELSE 0 END) AS ok_pairs,
      SUM(CASE WHEN divergence_count = 0 THEN 1 ELSE 0 END) AS exact_pairs,
      SUM(section_total_count) AS section_total_count,
      SUM(section_identical_count) AS section_identical_count,
      SUM(section_text_total_chars) AS section_text_total_chars,
      SUM(section_text_identical_chars) AS section_text_identical_chars
    FROM pairs
  `);
  $('stats').innerHTML = `
    <span><strong>${pairStats.pairs || 0}</strong> current replayable pairs</span>
    <span><strong>${pairStats.ok_pairs || 0}</strong> OK</span>
    <span><strong>${pairStats.exact_pairs || 0}</strong> zero section-diff</span>
    <span><strong>${pct(pairStats.section_identical_count, pairStats.section_total_count)}</strong> section-address identical</span>
    <span><strong>${pct(pairStats.section_text_identical_chars, pairStats.section_text_total_chars)}</strong> text inside identical sections</span>
    <span><strong>${pairStats.open_divergences || 0}</strong> open section rows</span>
  `;
}

function filteredCases() {
  const q = $('search').value.trim().toLowerCase();
  const mode = currentMode();
  const klass = $('class-filter').value;
  const bucket = $('bucket-filter').value;
  return cases.filter((row) => {
    if (Number(row.current_pair || 0) !== 1) return false;
    if (mode === 'signal' && Number(row.signal_divergences || 0) === 0) return false;
    if (mode === 'replay' && Number(row.replay_evidence_divergences || 0) === 0) return false;
    if (mode === 'alignment' && Number(row.alignment_shadow_divergences || 0) === 0) return false;
    if (mode === 'coverage' && Number(row.coverage_debt_divergences || 0) === 0) return false;
    if (klass && row.comparison_class !== klass) return false;
    if (bucket === 'open' && Number(row.open_current_divergence_count || 0) === 0) return false;
    if (bucket && bucket !== 'open' && !String(row.buckets || '').split(',').includes(bucket)) return false;
    if (!q) return true;
    const haystack = [
      row.title, row.base_id, row.oracle_id, row.grupi_id, row.comparison_class, row.buckets,
    ].join(' ').toLowerCase();
    return haystack.includes(q);
  });
}

function renderCaseList() {
  const rows = filteredCases();
  const mode = currentMode();
  const label = mode === 'coverage'
    ? 'comparison cases with compiler coverage debt'
    : mode === 'alignment'
      ? 'comparison cases with address alignment shadows'
    : mode === 'replay'
      ? 'comparison cases with replay evidence candidates'
    : mode === 'all'
      ? 'comparison cases with divergences'
      : 'comparison cases with current differences';
  $('case-count').textContent = `${rows.length} current-version ${label}`;
  $('case-list').innerHTML = rows.slice(0, 1000).map((row) => `
    <div class="case ${row.pair_key === currentKey ? 'active' : ''}" data-key="${esc(row.pair_key)}">
      <div class="case-title">${esc(row.title || row.grupi_id)}</div>
      <div class="case-meta">${esc(row.base_effective)} → ${esc(row.oracle_effective)} · ${esc(row.base_id)} → ${esc(row.oracle_id)}</div>
      <span class="pill ${Number(row.open_current_divergence_count) ? 'open' : 'closed'}">${row.open_current_divergence_count} open</span>
      <span class="pill">${row.signal_divergences || 0} differences</span>
      <span class="pill">${row.replay_evidence_divergences || 0} replay evidence</span>
      <span class="pill">${row.alignment_shadow_divergences || 0} address shadows</span>
      <span class="pill">${row.coverage_debt_divergences || 0} coverage debt</span>
      <span class="pill">${pct(row.section_identical_count, row.section_total_count, 0)} sections identical</span>
      <span class="pill">${esc(row.comparison_class)}</span>
    </div>
  `).join('');
  for (const item of document.querySelectorAll('.case')) {
    item.addEventListener('click', () => selectCase(item.dataset.key));
  }
}

async function selectCase(pairKey) {
  currentKey = pairKey;
  renderCaseList();
  const [pair] = exec('SELECT * FROM pairs WHERE pair_key = ?', [pairKey]);
  const divergences = exec(`
    SELECT
      d.*,
      rt.text AS replay_text,
      ot.text AS oracle_text,
      CASE
        WHEN d.oracle_text_hash IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM divergences x
            WHERE x.pair_key = d.pair_key
              AND x.replay_text_hash = d.oracle_text_hash
              AND x.address != d.address
          )
          THEN 1
        WHEN d.replay_text_hash IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM divergences x
            WHERE x.pair_key = d.pair_key
              AND x.oracle_text_hash = d.replay_text_hash
              AND x.address != d.address
          )
          THEN 1
        ELSE 0
      END AS alignment_shadow,
      (
        SELECT GROUP_CONCAT(address, ', ')
        FROM (
          SELECT x.address AS address
          FROM divergences x
          WHERE d.oracle_text_hash IS NOT NULL
            AND x.pair_key = d.pair_key
            AND x.replay_text_hash = d.oracle_text_hash
            AND x.address != d.address
          UNION
          SELECT x.address AS address
          FROM divergences x
          WHERE d.replay_text_hash IS NOT NULL
            AND x.pair_key = d.pair_key
            AND x.oracle_text_hash = d.replay_text_hash
            AND x.address != d.address
        )
      ) AS alignment_peer_addresses
    FROM divergences d
    LEFT JOIN text_blobs rt ON rt.text_hash = d.replay_text_hash
    LEFT JOIN text_blobs ot ON ot.text_hash = d.oracle_text_hash
    WHERE pair_key = ?
    ORDER BY section_address, address
  `, [pairKey]).filter((divergence) => modeIncludesDivergence(divergence, pair));
  renderDetail(pair, divergences);
}

function renderDetail(pair, divergences) {
  const sectionMetrics = Number(pair.section_total_count || 0) > 0
    ? `<div class="metric-strip">
        <span><strong>${pct(pair.section_identical_count, pair.section_total_count)}</strong> section-address identical</span>
        <span><strong>${pair.section_identical_count || 0}/${pair.section_total_count || 0}</strong> sections identical</span>
        <span><strong>${pair.section_divergent_count || 0}</strong> text-divergent</span>
        <span><strong>${pair.section_replay_only_count || 0}</strong> replay-only</span>
        <span><strong>${pair.section_consolidated_only_count || 0}</strong> consolidated-only</span>
      </div>`
    : '';
  if (!divergences.length) {
    $('detail').innerHTML = `<div class="empty">No rows match the current viewer mode for this comparison case.${sectionMetrics}</div>`;
    return;
  }
  const grouped = new Map();
  for (const div of divergences) {
    const key = topAddress(div.address);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(div);
  }
  const body = [...grouped.entries()].map(([group, divs]) => `
    <section class="divergence">
      <div class="div-head">
        <div>
          <strong>${esc(group || '(root)')}</strong>
          <span class="address">${divs.length} divergence${divs.length === 1 ? '' : 's'}</span>
        </div>
      </div>
      ${divs.map((div) => renderDivergence(div, pair)).join('')}
    </section>
  `).join('');

  $('detail').innerHTML = `
    <div class="detail-head">
      <h2>${esc(pair.title || pair.grupi_id)}</h2>
      <div class="case-meta">
        ${esc(pair.base_effective)} → ${esc(pair.oracle_effective)}
        · ${esc(pair.base_id)} → ${esc(pair.oracle_id)}
        · ${esc(pair.comparison_class)}
        · ${pair.n_ops} ops
      </div>
      <div class="links">
        <a href="${rtUrl(pair.base_id)}" target="_blank" rel="noreferrer">Base in Riigi Teataja</a>
        <a href="${rtUrl(pair.oracle_id)}" target="_blank" rel="noreferrer">Consolidated text in Riigi Teataja</a>
      </div>
      ${sectionMetrics}
    </div>
    <div class="tree">${body}</div>
  `;
}

function renderDivergence(div, pair) {
  const [left, right] = simpleDiff(div.replay_text, div.oracle_text);
  const open = Number(div.open_current || 0) === 1;
  const alignmentNote = isAlignmentShadow(div)
    ? `<div class="notice">Address alignment shadow: the same text appears verbatim on the opposite side at ${esc(div.alignment_peer_addresses || 'another address')}. This is hidden from the default current-differences view.</div>`
    : '';
  if (isAlignmentShadow(div)) {
    return `
      <article>
        <div class="div-head">
          <span class="address">${esc(div.address)}</span>
          <span>
            <span class="pill ${open ? 'open' : 'closed'}">${esc(bucketLabel(div.residual_bucket, open))}</span>
            <span class="pill ${isCoverageDebt(div.divergence_type, pair) ? 'debt' : ''}">${esc(div.divergence_type)}</span>
            <span class="pill shadow">address shadow</span>
          </span>
        </div>
        ${alignmentNote}
        ${div.residual_evidence ? `<div class="evidence">${esc(div.residual_evidence)}</div>` : ''}
      </article>
    `;
  }
  return `
    <article>
      <div class="div-head">
        <span class="address">${esc(div.address)}</span>
        <span>
          <span class="pill ${open ? 'open' : 'closed'}">${esc(bucketLabel(div.residual_bucket, open))}</span>
          <span class="pill ${isCoverageDebt(div.divergence_type, pair) ? 'debt' : ''}">${esc(div.divergence_type)}</span>
          ${isAlignmentShadow(div) ? '<span class="pill shadow">address shadow</span>' : ''}
        </span>
      </div>
      ${isCoverageDebt(div.divergence_type, pair) ? '<div class="notice">Compiler coverage debt: this comparison lacks replay evidence for this row, often because the applicable amendment source was unavailable or unsupported. This is not publisher-error evidence.</div>' : ''}
      <div class="diff-grid">
        <div class="pane">
          <h3>LawVM replay</h3>
          <div class="text">${left}</div>
        </div>
        <div class="pane">
          <h3>Riigi Teataja consolidated</h3>
          <div class="text">${right}</div>
        </div>
      </div>
      ${div.residual_evidence ? `<div class="evidence">${esc(div.residual_evidence)}</div>` : ''}
    </article>
  `;
}

async function init() {
  try {
    await openFetchedDatabase();
    dmp = new diff_match_patch();
    await loadIndexes();
    await renderStats();
    setProgress(100, 'Ready');
    $('loading').hidden = true;
    $('topbar').hidden = false;
    $('app').hidden = false;
    renderCaseList();
    const first = filteredCases()[0];
    if (first) await selectCase(first.pair_key);
    for (const id of ['search', 'mode-filter', 'class-filter', 'bucket-filter']) {
      $(id).addEventListener('input', () => {
        currentKey = '';
        renderCaseList();
        $('detail').innerHTML = '<div class="empty">Select a comparison case.</div>';
      });
    }
  } catch (err) {
    console.error(err);
    $('load-status').textContent = String(err && err.message ? err.message : err);
  }
}

window.addEventListener('beforeunload', () => {
  if (db) db.close();
});

init();
