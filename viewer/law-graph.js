// law-graph.js — the pack-native relation-graph + transclusion viewer.
//
// Reads a LawVM substrate pack DIRECTLY (plain JSONL, no server logic): renders
// the statute as a readable document, paints each relation edge as an anchored
// interlink carrying its PROOF GRADE (authority_plane x verification_level — the
// firewall made visible), and when an edge target is a resolved EU entity node
// it TRANSCLUDES the regulation article text inline from a second pack. This is
// §25 Step 5: the hypercodex of law made visible.

"use strict";

const STATE = {
  manifest: null,
  entry: null,
  pack: null, // the FI statute pack
  euPacks: new Map(), // celex -> SubstratePack (transclusion targets)
  anchors: {}, // edge_id -> {address, surface_text, ...}
  euOnly: false,
  // ---- TIME lens ---------------------------------------------------------- //
  asOf: null, // the scrubbed date (null = present / latest law)
  dates: [], // the change-date axis (sorted)
};

// ---- boot ---------------------------------------------------------------- //

async function boot() {
  const manifestFile = manifestFromQuery();
  STATE.manifest = await fetchJSONManifest(manifestFile);
  const sel = document.getElementById("statute-select");
  STATE.manifest.forEach((e, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = e.title;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => loadEntry(STATE.manifest[+sel.value]));
  document.getElementById("eu-only").addEventListener("change", (ev) => {
    STATE.euOnly = ev.target.checked;
    render();
  });
  wireTimeControls();
  wireSectionJump();
  wireTocFilter();
  await loadEntry(STATE.manifest[0]);
}

function manifestFromQuery() {
  const q = new URLSearchParams(location.search).get("manifest");
  // Only a bare same-directory .json filename is accepted (no traversal).
  if (q && /^[\w.-]+\.json$/.test(q)) return q;
  return "law-graph-manifest.json";
}

async function fetchJSONManifest(file) {
  const r = await fetch(file, { cache: "no-cache" });
  if (!r.ok) throw new Error(`manifest ${file}: HTTP ${r.status}`);
  return r.json();
}

async function loadEntry(entry) {
  STATE.entry = entry;
  document.getElementById("doc").innerHTML = '<p class="loading">Ladataan pakkausta…</p>';
  document.getElementById("edge-list").innerHTML = "";

  // 1. the statute pack (certified layers).
  const pack = await SubstratePack.load(entry.pack);
  STATE.pack = pack;

  // 2. resolved-edge sidecar + anchor map (presentation overlay, not certified).
  if (entry.resolved_edges) await pack.loadResolvedEdges(entry.resolved_edges);
  STATE.anchors = entry.anchors ? await (await fetch(entry.anchors, { cache: "no-cache" })).json() : {};

  // 3. the cross-work transclusion target packs (e.g. GDPR), loaded lazily but
  //    eagerly here so the first hover is instant.
  STATE.euPacks = new Map();
  for (const [celex, url] of Object.entries(entry.transclude_packs || {})) {
    try {
      STATE.euPacks.set(celex, await SubstratePack.load(url));
    } catch (e) {
      console.warn("transclusion pack failed", celex, e);
    }
  }

  // 4. the TIME axis — the change-date list driving the scrubber. Default to
  //    the present law (asOf = null = latest version of every provision).
  STATE.dates = pack.changeDates.slice();
  STATE.asOf = null;
  buildTimeAxis();

  render();
  runVerifier(pack);
}

// ---- TIME lens: axis, scrubber, point-in-time ---------------------------- //
//
// The whole time lens is driven from the PACK, not sqlite:
//   * STATE.dates       = pack.changeDates  (transition/checkpoint dates)
//   * point-in-time     = pack.textAt(addr, asOf) via the governing_text
//                         selection profile (applicability_fact intervals)
//   * per-provision hist = pack.historyFor(addr) (certified_tree_transition)
//   * lifecycle / ghosts = pack.lifecycle(addr)
//   * self-verify        = pack.checkpointAt(asOf).tree_hash recomputed
//
// asOf === null means "the present law" (latest version of each provision).

const MS_DAY = 86400000;

function dateToMs(d) {
  if (!d) return null;
  const [y, m, day] = d.split("-").map((n) => parseInt(n, 10));
  return Date.UTC(y, (m || 1) - 1, day || 1);
}

// The effective date currently being shown (asOf, or the last change date when
// showing the present law).
function effectiveDate() {
  if (STATE.asOf) return STATE.asOf;
  return STATE.dates.length ? STATE.dates[STATE.dates.length - 1] : null;
}

function buildTimeAxis() {
  const dates = STATE.dates;
  const sel = document.getElementById("date-select");
  sel.innerHTML =
    `<option value="">— voimassa oleva —</option>` +
    dates.map((d) => `<option value="${escAttr(d)}">${escHtml(d)}</option>`).join("");

  const axis = document.getElementById("timeaxis");
  if (!dates.length) {
    axis.innerHTML = "";
    return;
  }
  const lo = dateToMs(dates[0]);
  const hi = dateToMs(dates[dates.length - 1]);
  const span = Math.max(1, hi - lo);
  const pct = (d) => `${(((dateToMs(d) - lo) / span) * 100).toFixed(3)}%`;

  let html = `<div class="ta-line"></div>`;
  // year gridlines
  const y0 = new Date(lo).getUTCFullYear();
  const y1 = new Date(hi).getUTCFullYear();
  for (let y = y0; y <= y1; y++) {
    const ms = Date.UTC(y, 0, 1);
    if (ms < lo || ms > hi) continue;
    const left = `${(((ms - lo) / span) * 100).toFixed(3)}%`;
    html += `<div class="ta-year" style="left:${left}"><span>${y}</span></div>`;
  }
  for (const d of dates) {
    html += `<div class="ta-tick" style="left:${pct(d)}" data-date="${escAttr(d)}" title="${escHtml(d)}"></div>`;
  }
  html += `<div class="ta-cursor" id="ta-cursor" style="left:${pct(effectiveDate())}"></div>`;
  axis.innerHTML = html;
}

function moveCursor() {
  const cur = document.getElementById("ta-cursor");
  if (!cur || !STATE.dates.length) return;
  const lo = dateToMs(STATE.dates[0]);
  const hi = dateToMs(STATE.dates[STATE.dates.length - 1]);
  const span = Math.max(1, hi - lo);
  cur.style.left = `${(((dateToMs(effectiveDate()) - lo) / span) * 100).toFixed(3)}%`;
}

// Snap an arbitrary ms position to the nearest change date <= that position
// (governing_text is left-continuous: you see the law as of the most recent
// change at or before the clicked instant).
function snapDate(ms) {
  const dates = STATE.dates;
  if (!dates.length) return null;
  let pick = dates[0];
  for (const d of dates) {
    if (dateToMs(d) <= ms) pick = d;
    else break;
  }
  return pick;
}

// Set the scrubbed date and re-render, preserving scroll position.
function setAsOf(date, isPresent) {
  STATE.asOf = isPresent ? null : date;
  const y = window.scrollY;
  document.getElementById("date-select").value = STATE.asOf || "";
  render();
  moveCursor();
  window.scrollTo(0, y); // preserve scroll across the re-render
  refreshVerifyBadge();
}

function wireTimeControls() {
  const axis = document.getElementById("timeaxis");
  const onScrub = (clientX) => {
    const r = axis.getBoundingClientRect();
    if (!STATE.dates.length) return;
    const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
    const lo = dateToMs(STATE.dates[0]);
    const hi = dateToMs(STATE.dates[STATE.dates.length - 1]);
    const ms = lo + frac * (hi - lo);
    const d = snapDate(ms);
    if (d) setAsOf(d, false);
  };
  let dragging = false;
  axis.addEventListener("pointerdown", (e) => {
    dragging = true;
    axis.setPointerCapture(e.pointerId);
    onScrub(e.clientX);
  });
  axis.addEventListener("pointermove", (e) => {
    if (dragging) onScrub(e.clientX);
  });
  axis.addEventListener("pointerup", () => {
    dragging = false;
  });

  document.getElementById("date-select").addEventListener("change", (e) => {
    setAsOf(e.target.value || null, !e.target.value);
  });
  document.getElementById("date-prev").addEventListener("click", () => stepDate(-1));
  document.getElementById("date-next").addEventListener("click", () => stepDate(+1));
  document.getElementById("date-now").addEventListener("click", () => setAsOf(null, true));
}

function stepDate(dir) {
  const dates = STATE.dates;
  if (!dates.length) return;
  const cur = effectiveDate();
  let i = dates.indexOf(cur);
  if (i < 0) i = dates.length - 1;
  const ni = Math.max(0, Math.min(dates.length - 1, i + dir));
  const atEnd = ni === dates.length - 1;
  setAsOf(dates[ni], atEnd && STATE.asOf === null);
}

function renderDateHeader() {
  const eff = effectiveDate();
  document.getElementById("cur-date").textContent = eff || "—";
  const meta = document.getElementById("date-meta");
  if (!STATE.dates.length) {
    meta.textContent = "ei muutoshistoriaa";
    return;
  }
  const isPresent = STATE.asOf === null;
  const n = STATE.dates.length;
  meta.textContent = `${n} muutospäivää · ${
    isPresent ? "voimassa oleva oikeustila" : "pistemäinen oikeustila"
  } · alkaen ${STATE.pack.genesisDate || STATE.dates[0]}`;
}

// ---- § quick-jump + TOC filter ------------------------------------------- //

// Normalise a "§" query ("54 a", "54a", "12") to a section label, then find the
// section node's address (or its ghost tombstone if repealed at the scrubbed
// date). Scrolls + flashes.
function jumpToSection(query) {
  const q = (query || "").trim().toLowerCase().replace(/§/g, "").replace(/\s+/g, " ").trim();
  if (!q) return false;
  const want = q.replace(/\s+/g, ""); // "54 a" -> "54a"
  // find the section: address segment section:<label>
  for (const path of STATE.pack.addressByPath.keys()) {
    const m = /(?:^|\/)section:([^/]+)/.exec(path);
    if (!m) continue;
    const label = m[1].toLowerCase().replace(/\s+/g, "");
    // address is the section node itself (no deeper segment after section:)
    if (label === want && /section:[^/]+$/.test(path)) {
      return scrollToAddr(path);
    }
  }
  // fall back: any node whose section label matches (the section subtree root)
  for (const path of STATE.pack.addressByPath.keys()) {
    const m = /(?:^|\/)section:([^/]+)/.exec(path);
    if (m && m[1].toLowerCase().replace(/\s+/g, "") === want) return scrollToAddr(path);
  }
  return false;
}

function scrollToAddr(addr) {
  let node = document.querySelector(`#doc .node[data-addr="${cssEsc(addr)}"]`);
  if (!node) {
    // maybe it's a ghost — find the nearest rendered ancestor/section node
    node = document.querySelector(`#doc [data-addr="${cssEsc(addr)}"]`);
  }
  if (!node) return false;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  const row = node.querySelector(".node-row") || node;
  row.classList.add("flash");
  setTimeout(() => row.classList.remove("flash"), 1300);
  return true;
}

function wireSectionJump() {
  const inp = document.getElementById("sec-jump");
  inp.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const ok = jumpToSection(inp.value);
    inp.classList.toggle("nf", !ok);
  });
}

function wireTocFilter() {
  const inp = document.getElementById("toc-filter");
  inp.addEventListener("input", () => {
    const q = inp.value.trim().toLowerCase();
    document.querySelectorAll("#toc .toc-link").forEach((a) => {
      const hit = !q || (a.textContent || "").toLowerCase().includes(q);
      a.style.display = hit ? "" : "none";
    });
  });
}

// ---- proof-grade legend -------------------------------------------------- //

const GRADE_ORDER = ["verified", "legal", "resolved", "evidence", "asserted", "kinship", "other"];

function renderLegend() {
  const el = document.getElementById("legend");
  const samples = {
    verified: { icon: "✓", label: "varmennettu johdos" },
    resolved: { icon: "→", label: "ratkaistu viittaus" },
    evidence: { icon: "⁇", label: "näyttö (väitetty)" },
    asserted: { icon: "≈", label: "lähde väittää" },
    kinship: { icon: "≈", label: "sukulaisuus (arvio)" },
  };
  el.innerHTML =
    '<span class="legend-lab">Todistusaste:</span>' +
    Object.entries(samples)
      .map(
        ([g, s]) =>
          `<span class="grade-badge grade-${g}" title="${g}"><span class="gb-icon">${s.icon}</span>${s.label}</span>`
      )
      .join("");
}

// ---- document tree ------------------------------------------------------- //
//
// Build a tree from address_path (chapter:1/section:2/subsection:3). Each leaf
// renders its content_leaf text. Edges anchored at an address paint as inline
// interlinks appended to that node's body.

function buildTree(pack) {
  const root = { key: "", label: "", kind: "root", children: new Map(), addr: "" };
  const paths = Array.from(pack.addressByPath.keys()).sort(addrSort);
  for (const path of paths) {
    const node = pack.addressByPath.get(path);
    const segs = path.split("/");
    let cur = root;
    let acc = [];
    for (const seg of segs) {
      acc.push(seg);
      const accPath = acc.join("/");
      if (!cur.children.has(seg)) {
        const [kind, label] = seg.split(":");
        cur.children.set(seg, {
          key: seg,
          kind,
          label: label || "",
          addr: accPath,
          children: new Map(),
        });
      }
      cur = cur.children.get(seg);
    }
    cur.structuralKind = node.structural_kind;
  }
  return root;
}

// Sort address paths so 2 < 10 (numeric-aware on each segment's label).
function addrSort(a, b) {
  const sa = a.split("/");
  const sb = b.split("/");
  for (let i = 0; i < Math.max(sa.length, sb.length); i++) {
    const ka = sa[i] || "";
    const kb = sb[i] || "";
    const la = (ka.split(":")[1] || "");
    const lb = (kb.split(":")[1] || "");
    const na = parseInt(la, 10);
    const nb = parseInt(lb, 10);
    if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;
    if (ka !== kb) return ka < kb ? -1 : 1;
  }
  return 0;
}

const KIND_LABEL = {
  chapter: "luku",
  section: "§",
  subsection: "mom.",
  article: "art.",
  paragraph: "kohta",
  division: "osa",
};

function renderTree(node, pack, edgesByAddr, depth) {
  const asOf = STATE.asOf; // null = present law
  let html = "";
  const kids = Array.from(node.children.values());
  for (const child of kids) {
    const hasKids = child.children.size > 0;
    const kindLabel = KIND_LABEL[child.kind] || child.kind;
    const text = pack.textAt(child.addr, asOf);
    const edges = edgesByAddr.get(child.addr) || [];
    const life = pack.lifecycle(child.addr);

    // Is this provision a GHOST at the scrubbed date? (repealed/lapsed at or
    // before asOf, with no version live now). A leaf with no version live and a
    // repeal date in the past renders as a tombstone in place.
    const isGhost =
      !text &&
      !hasKids &&
      life.repealedAt &&
      lawvmCmpDate(life.repealedAt, effectiveDate()) <= 0 &&
      pack.versionAt(child.addr, asOf) === null;

    const changeBadge = timeBadge(pack, child.addr, life);
    const histBtn = life.transitions.length
      ? `<button class="hist-btn has-hist" data-addr="${escAttr(child.addr)}" title="Pykälän muutoshistoria">⧖</button>`
      : "";

    if (isGhost) {
      // Tombstone line: shown at its original place, still navigable.
      html += `<div class="node kind-${escAttr(child.kind)} tombstone" data-depth="${depth}" data-addr="${escAttr(child.addr)}" data-ghost="1">`;
      html += `<div class="node-row clk spyable" data-addr="${escAttr(child.addr)}">`;
      html += `<span class="node-toggle leaf"></span>`;
      html += `<span class="node-label">${escHtml(kindLabel)} ${escHtml(child.label)}</span>`;
      html += `<span class="changed-tag changed-tag-removed">kumottu ${escHtml(life.repealedAt)}</span>`;
      html += changeBadge + histBtn;
      html += `</div>`;
      html += `<div class="node-body"><div class="pblock ghost-line"><em>Pykälä kumottu/rauennut ${escHtml(
        life.repealedAt
      )}.</em></div></div></div>`;
      continue;
    }

    // Does this provision change AT the scrubbed date? Mark it.
    const changedNow =
      asOf && life.transitions.some((t) => t.effective_date === asOf);
    const cls = "node kind-" + child.kind + (changedNow ? " changed" : "");

    html += `<div class="${escAttr(cls)}" data-depth="${depth}" data-addr="${escAttr(child.addr)}">`;
    html += `<div class="node-row clk spyable" data-addr="${escAttr(child.addr)}">`;
    html += `<span class="node-toggle leaf"></span>`;
    html += `<span class="node-label">${escHtml(kindLabel)} ${escHtml(child.label)}</span>`;
    if (changedNow) html += `<span class="changed-tag">muuttunut</span>`;
    html += changeBadge + histBtn;
    html += `</div>`;
    html += `<div class="node-body">`;
    if (text) {
      html += `<div class="prose" data-addr="${escAttr(child.addr)}">${escHtml(text)}</div>`;
    }
    if (edges.length) {
      html += `<div class="anchors">${edges.map(renderEdgeAnchor).join("")}</div>`;
    }
    if (hasKids) html += renderTree(child, pack, edgesByAddr, depth + 1);
    html += `</div></div>`;
  }
  return html;
}

// The per-section change badge "N/M" + a micro lifecycle strip (in-force /
// repealed-gap duration bars + event ticks), at real temporal positions.
function timeBadge(pack, addr, life) {
  const trans = life.transitions;
  if (!trans.length) return "";
  const eff = effectiveDate();
  const upTo = trans.filter((t) => lawvmCmpDate(t.effective_date, eff) <= 0).length;
  const strip = lifecycleStrip(pack, addr, life);
  return (
    `<span class="chg-badge" data-addr="${escAttr(addr)}" title="${upTo}/${trans.length} muutosta tähän päivään mennessä">` +
    `${upTo}/${trans.length}${strip}</span>`
  );
}

function lifecycleStrip(pack, addr, life) {
  if (!STATE.dates.length) return "";
  const lo = dateToMs(STATE.dates[0]);
  const hi = dateToMs(STATE.dates[STATE.dates.length - 1]);
  const span = Math.max(1, hi - lo);
  const px = (d) => `${(((dateToMs(d) - lo) / span) * 100).toFixed(2)}%`;
  const effMs = dateToMs(effectiveDate());

  let bars = "";
  // in-force segments from the applicability intervals; repealed gap after a
  // trailing delete.
  for (const v of life.versions) {
    const [from, to] = v.interval;
    const left = px(from || STATE.dates[0]);
    const rt = to ? dateToMs(to) : hi;
    const w = `${(((rt - dateToMs(from || STATE.dates[0])) / span) * 100).toFixed(2)}%`;
    bars += `<b class="seg-on" style="left:${left};width:${w}"></b>`;
  }
  if (life.repealedAt) {
    const left = px(life.repealedAt);
    const w = `${(((hi - dateToMs(life.repealedAt)) / span) * 100).toFixed(2)}%`;
    bars += `<b class="seg-rep" style="left:${left};width:${w}"></b>`;
  }
  // event ticks
  let ticks = "";
  for (const t of life.transitions) {
    const k =
      t.action === "delete_subtree" || t.action === "tombstone"
        ? "tk-rem"
        : t.action === "set_subtree" && t.pre_hash === ""
        ? "tk-add"
        : "tk-chg";
    const fut = dateToMs(t.effective_date) > effMs ? " fut" : "";
    ticks += `<i class="${k}${fut}" style="left:${px(t.effective_date)}"></i>`;
  }
  const cursor = `<u class="strip-cursor" style="left:${px(effectiveDate())}"></u>`;
  return `<span class="chg-strip">${bars}${ticks}${cursor}</span>`;
}

// ---- edge anchors (the interlinks) -------------------------------------- //

// Render ONE edge as an anchored interlink. A range/coordination
// (target_set_semantics=all_valid) is ONE anchor whose expansion lists ALL
// targets — not N separate links. Each anchor carries a proof-grade badge and
// status; a resolved EU target is marked transcludable.
function renderEdgeAnchor(edge) {
  const grade = lawvmProofGrade(edge);
  const anchor = STATE.anchors[edge.edge_id] || {};
  const surface = anchor.surface_text || edge.target_set.join(", ");
  const targets = edge.target_set;
  const isRange = edge.target_set_semantics === "all_valid" && targets.length > 1;
  const euTargets = targets.filter(lawvmIsEntityTarget);
  const hasEu = euTargets.length > 0;

  const badge = `<span class="grade-badge grade-${grade.grade}" title="${escAttr(grade.planeLevel)}"><span class="gb-icon">${grade.icon}</span></span>`;
  const statusChip = `<span class="status-chip status-${escAttr(edge.edge_status)}">${escHtml(edge.edge_status)}</span>`;
  const rangeChip = isRange
    ? `<span class="range-chip" title="${escAttr(edge.target_set_semantics)}">${targets.length} kohdetta</span>`
    : "";
  const euChip = hasEu ? `<span class="eu-chip" title="cross-work transclusion">EU ⇲</span>` : "";

  return (
    `<span class="edge-anchor grade-${grade.grade}${hasEu ? " has-eu" : ""}" ` +
    `data-edge="${escAttr(edge.edge_id)}" tabindex="0">` +
    `${badge}<span class="edge-surface">${escHtml(surface)}</span>` +
    `${rangeChip}${euChip}${statusChip}</span>`
  );
}

// The expansion / hovercard body for one edge: list every target, transcluding
// EU article text inline for resolved entity targets.
function edgeCardHtml(edge) {
  const grade = lawvmProofGrade(edge);
  let html = `<div class="edge-card-head">`;
  html += `<span class="grade-badge grade-${grade.grade}"><span class="gb-icon">${grade.icon}</span>${escHtml(grade.label)}</span>`;
  html += `<span class="ec-plane">${escHtml(grade.planeLevel)}</span>`;
  html += `<span class="status-chip status-${escAttr(edge.edge_status)}">${escHtml(edge.edge_status)}</span>`;
  html += `</div>`;
  html += `<div class="ec-semantics">${escHtml(semanticsLabel(edge.target_set_semantics))} · ${edge.target_set.length} kohde(tta)</div>`;
  html += `<ul class="ec-targets">`;
  for (const t of edge.target_set) {
    if (lawvmIsEntityTarget(t)) {
      html += `<li class="ec-target ec-eu">${renderTransclusion(t)}</li>`;
    } else {
      html += `<li class="ec-target">${escHtml(t)}</li>`;
    }
  }
  html += `</ul>`;
  return html;
}

function semanticsLabel(sem) {
  return (
    {
      single: "yksi kohde",
      all_valid: "kaikki kohteet (esim. luettelo/väli)",
      candidate_ambiguity: "ehdokas-monitulkintaisuus",
      open: "avoin viittaus",
      no_enumerable_extension: "ei lueteltavaa kohdejoukkoa",
    }[sem] || sem
  );
}

// Cross-work transclusion: fetch the GDPR article text from the EU pack and
// inline it. THE KILLER DEMO — Finnish law + the EU regulation it points at, in
// one view.
function renderTransclusion(entityTarget) {
  const celex = lawvmCelexOfEntity(entityTarget);
  const pack = STATE.euPacks.get(celex);
  if (!pack) {
    return `<span class="ec-target-id">${escHtml(entityTarget)}</span>` +
      `<span class="ec-warn">(EU-pakkausta ei ladattu)</span>`;
  }
  const resolved = pack.resolveEntityNode(entityTarget);
  if (!resolved) {
    return `<span class="ec-target-id">${escHtml(entityTarget)}</span>` +
      `<span class="ec-warn">(kohdesolmua ei löytynyt)</span>`;
  }
  const addrLabel = prettyEuAddr(resolved.address);
  const body = (resolved.text || "(ei tekstiä tällä solmulla)")
    .split("\n\n")
    .map((para) => `<p>${escHtml(para)}</p>`)
    .join("");
  return (
    `<div class="transclusion">` +
    `<div class="tc-head"><span class="tc-source">${escHtml(resolved.title)}</span>` +
    `<span class="tc-addr">${escHtml(addrLabel)}</span></div>` +
    `<div class="tc-body">${body}</div>` +
    `</div>`
  );
}

function prettyEuAddr(addr) {
  // division:001/article:006/paragraph:006.001 -> "6 artikla, 1 kohta"
  const parts = [];
  for (const seg of (addr || "").split("/")) {
    const [k, v] = seg.split(":");
    if (k === "article") parts.push(`${(v || "").replace(/^0+/, "") || v} artikla`);
    else if (k === "paragraph") parts.push(`${((v || "").split(".").pop() || "").replace(/^0+/, "")} kohta`);
  }
  return parts.join(", ") || addr;
}

// ---- render -------------------------------------------------------------- //

function render() {
  const pack = STATE.pack;
  if (!pack) return;
  renderLegend();
  renderDateHeader();

  document.getElementById("doc-title").textContent = (pack.work && pack.work.title) || STATE.entry.title;
  document.getElementById("doc-meta").textContent =
    `${pack.addressByPath.size} osoiteltavaa solmua · ${pack.edges.length} relaatioreunaa · ` +
    `${pack.transitions.length} muutosta · pakkaus ${shortHash(pack.manifest.pack_kind)}`;

  // index edges by anchored address
  const edgesByAddr = new Map();
  let euEdgeCount = 0;
  for (const edge of pack.edges) {
    const hasEu = edge.target_set.some(lawvmIsEntityTarget);
    if (hasEu) euEdgeCount++;
    if (STATE.euOnly && !hasEu) continue;
    const anchor = STATE.anchors[edge.edge_id];
    const addr = anchor && anchor.address;
    if (!addr) continue;
    if (!edgesByAddr.has(addr)) edgesByAddr.set(addr, []);
    edgesByAddr.get(addr).push(edge);
  }

  // document
  const tree = buildTree(pack);
  document.getElementById("doc").innerHTML = renderTree(tree, pack, edgesByAddr, 0);

  // TOC minimap (chapters + sections), with change marks at the scrubbed date
  renderToc(tree, pack);

  // edge rail (the graph summary, grouped by proof grade)
  renderEdgeRail(pack, euEdgeCount);

  wireInteractions();
  wireTimeInteractions();
  wireScrollSpy();
}

// ---- TOC minimap --------------------------------------------------------- //

function renderToc(tree, pack) {
  const eff = effectiveDate();
  let html = `<ul class="toc-list">`;
  const walk = (node) => {
    for (const child of node.children.values()) {
      if (child.kind === "chapter" || child.kind === "section") {
        const life = pack.lifecycle(child.addr);
        const changed =
          STATE.asOf && life.transitions.some((t) => t.effective_date === STATE.asOf);
        const ghost =
          life.repealedAt &&
          lawvmCmpDate(life.repealedAt, eff) <= 0 &&
          pack.versionAt(child.addr, STATE.asOf) === null;
        const kindLabel = KIND_LABEL[child.kind] || child.kind;
        const cls =
          "toc-link toc-" +
          child.kind +
          (child.kind === "chapter" ? " toc-ch" : " toc-sec") +
          (changed ? " ch-changed" : "") +
          (ghost ? " toc-tombstone" : "");
        html +=
          `<li><a class="${escAttr(cls)}" href="#" data-addr="${escAttr(child.addr)}">` +
          `<span class="toc-num">${escHtml(kindLabel)} ${escHtml(child.label)}</span>` +
          (ghost ? `<span class="toc-status">kumottu</span>` : "") +
          `</a></li>`;
      }
      walk(child);
    }
  };
  walk(tree);
  html += `</ul>`;
  document.getElementById("toc").innerHTML = html;

  document.querySelectorAll("#toc .toc-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      scrollToAddr(a.dataset.addr);
    });
  });
  // re-apply any active filter
  const f = document.getElementById("toc-filter").value.trim().toLowerCase();
  if (f) {
    document.querySelectorAll("#toc .toc-link").forEach((a) => {
      a.style.display = (a.textContent || "").toLowerCase().includes(f) ? "" : "none";
    });
  }
}

// ---- per-provision inline history ---------------------------------------- //

function wireTimeInteractions() {
  document.querySelectorAll("#doc .hist-btn, #doc .chg-badge").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleHistory(b.dataset.addr, b.closest(".node"));
    });
  });
}

function toggleHistory(addr, nodeEl) {
  if (!nodeEl) return;
  const body = nodeEl.querySelector(":scope > .node-body");
  if (!body) return;
  const existing = body.querySelector(":scope > .prov-history");
  if (existing) {
    existing.remove();
    return;
  }
  const div = document.createElement("div");
  div.className = "prov-history";
  div.innerHTML = historyHtml(addr);
  body.insertBefore(div, body.firstChild);
}

// The amending-act-attributed change list for one provision (degrades to the
// change + date when no source_artifact attribution exists in the pack).
function historyHtml(addr) {
  const pack = STATE.pack;
  const trans = pack.historyFor(addr); // all, sequence order
  if (!trans.length) return `<p class="muted-empty">Ei muutoshistoriaa.</p>`;
  const eff = effectiveDate();
  let html = `<div class="ph-head">Muutoshistoria — ${escHtml(prettyAddr(addr))}</div><ul class="ph-list">`;
  for (const t of trans) {
    const act = pack.amendingAct(t);
    const future = lawvmCmpDate(t.effective_date, eff) > 0;
    const opLabel =
      t.action === "delete_subtree" || t.action === "tombstone"
        ? "kumottu"
        : t.pre_hash === ""
        ? "lisätty"
        : "muutettu";
    const opCls = t.action === "delete_subtree" ? "op-exp" : "";
    html +=
      `<li class="ph-item${future ? " ph-future" : ""}">` +
      `<span class="ann-date">${escHtml(t.effective_date)}</span> ` +
      `<span class="op-kind ${opCls}">${escHtml(opLabel)}</span> ` +
      (act
        ? `<span class="ph-act">${escHtml(act.title)}</span>`
        : `<span class="ph-act ph-act-none" title="No source_artifact attribution in this pack">(muuttava säädös ei nimettynä)</span>`) +
      (future ? ` <span class="future-tag">tuleva</span>` : "") +
      `</li>`;
  }
  html += `</ul>`;
  return html;
}

function prettyAddr(addr) {
  return (addr || "")
    .split("/")
    .map((seg) => {
      const [k, v] = seg.split(":");
      return `${KIND_LABEL[k] || k} ${v}`;
    })
    .join(" › ");
}

// ---- TOC scroll-spy ------------------------------------------------------ //

let _spyObserver = null;
function wireScrollSpy() {
  if (_spyObserver) _spyObserver.disconnect();
  const links = new Map();
  document.querySelectorAll("#toc .toc-link").forEach((a) => links.set(a.dataset.addr, a));
  if (!("IntersectionObserver" in window)) return;
  _spyObserver = new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const addr = en.target.dataset.addr;
        const link = links.get(addr);
        if (!link) continue;
        document.querySelectorAll("#toc .toc-link.current").forEach((x) => x.classList.remove("current"));
        link.classList.add("current");
      }
    },
    { rootMargin: "-20% 0px -70% 0px", threshold: 0 }
  );
  links.forEach((_link, addr) => {
    const row = document.querySelector(`#doc .node[data-addr="${cssEsc(addr)}"] > .node-row`);
    if (row) _spyObserver.observe(row);
  });
}

function renderEdgeRail(pack, euEdgeCount) {
  const byGrade = new Map();
  for (const edge of pack.edges) {
    const g = lawvmProofGrade(edge).grade;
    byGrade.set(g, (byGrade.get(g) || 0) + 1);
  }
  document.getElementById("edge-summary").innerHTML =
    `${pack.edges.length} reunaa, joista <strong>${euEdgeCount}</strong> osoittaa EU-asetukseen (transkluusio).`;

  let html = "";
  for (const g of GRADE_ORDER) {
    const n = byGrade.get(g);
    if (!n) continue;
    html += `<div class="rail-grade grade-${g}"><span class="grade-badge grade-${g}"><span class="gb-icon">${gradeIcon(g)}</span></span><span class="rg-label">${gradeLabel(g)}</span><span class="rg-count">${n}</span></div>`;
  }
  // resolved EU citations, listed
  const euEdges = pack.edges.filter((e) => e.target_set.some(lawvmIsEntityTarget));
  if (euEdges.length) {
    html += `<h3 class="rail-sub2">Ratkaistut EU-viittaukset</h3>`;
    for (const e of euEdges.slice(0, 40)) {
      const a = STATE.anchors[e.edge_id] || {};
      const arts = e.target_set
        .filter(lawvmIsEntityTarget)
        .map((t) => prettyEuAddr(STATE.euPacks.get(lawvmCelexOfEntity(t))?.resolveEntityNode(t)?.address || t))
        .join("; ");
      html += `<button class="rail-eu-link" data-edge="${escAttr(e.edge_id)}" data-addr="${escAttr(a.address || "")}">` +
        `<span class="rel-surface">${escHtml(a.surface_text || "")}</span> <span class="rel-arrow">→</span> <span class="rel-art">${escHtml(arts)}</span></button>`;
    }
  }
  document.getElementById("edge-list").innerHTML = html;
}

function gradeIcon(g) {
  return { verified: "✓", legal: "§", resolved: "→", evidence: "⁇", asserted: "≈", kinship: "≈", other: "·" }[g] || "·";
}
function gradeLabel(g) {
  return {
    verified: "varmennettu johdos",
    legal: "oikeustila",
    resolved: "ratkaistu viittaus",
    evidence: "näyttö",
    asserted: "lähde väittää",
    kinship: "sukulaisuus (arvio)",
    other: "muu",
  }[g] || g;
}

// ---- interactions: hovercard + click-to-expand --------------------------- //

let _hc = null;
function hovercard() {
  if (_hc) return _hc;
  _hc = document.createElement("div");
  _hc.className = "ref-hovercard edge-hovercard";
  _hc.style.display = "none";
  document.body.appendChild(_hc);
  _hc.addEventListener("mouseenter", () => clearTimeout(_hc._hideT));
  _hc.addEventListener("mouseleave", hideHovercard);
  return _hc;
}
function hideHovercard() {
  if (_hc) _hc.style.display = "none";
}
function showHovercard(anchorEl, edge) {
  const el = hovercard();
  el.innerHTML = edgeCardHtml(edge);
  el.style.display = "block";
  const r = anchorEl.getBoundingClientRect();
  el.style.top = `${window.scrollY + r.bottom + 6}px`;
  el.style.left = `${window.scrollX + Math.min(r.left, window.innerWidth - 460)}px`;
}

function edgeById(id) {
  return STATE.pack.edges.find((e) => e.edge_id === id);
}

function wireInteractions() {
  const doc = document.getElementById("doc");

  doc.querySelectorAll(".edge-anchor").forEach((a) => {
    const edge = edgeById(a.dataset.edge);
    if (!edge) return;
    a.addEventListener("mouseenter", () => {
      clearTimeout(hovercard()._hideT);
      showHovercard(a, edge);
    });
    a.addEventListener("mouseleave", () => {
      hovercard()._hideT = setTimeout(hideHovercard, 220);
    });
    // Click toggles a persistent inline expansion (the transclusion stays open).
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      toggleInlineExpansion(a, edge);
    });
    a.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        toggleInlineExpansion(a, edge);
      }
    });
  });

  document.querySelectorAll(".rail-eu-link").forEach((b) => {
    b.addEventListener("click", () => {
      const addr = b.dataset.addr;
      const el = document.querySelector(`#doc .edge-anchor[data-edge="${cssEsc(b.dataset.edge)}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("flash");
        setTimeout(() => el.classList.remove("flash"), 1400);
        toggleInlineExpansion(el, edgeById(b.dataset.edge), true);
      } else if (addr) {
        const node = document.querySelector(`#doc .node[data-addr="${cssEsc(addr)}"]`);
        if (node) node.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  });
}

function toggleInlineExpansion(anchorEl, edge, forceOpen) {
  hideHovercard();
  let exp = anchorEl.nextElementSibling;
  if (exp && exp.classList.contains("inline-expansion")) {
    if (forceOpen) {
      exp.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    exp.remove();
    anchorEl.classList.remove("open");
    return;
  }
  exp = document.createElement("div");
  exp.className = "inline-expansion";
  exp.innerHTML = edgeCardHtml(edge);
  anchorEl.classList.add("open");
  anchorEl.after(exp);
}

// ---- verifier badge ------------------------------------------------------ //

// L0 row-integrity (date-independent) — run once per pack load; result cached.
async function runVerifier(pack) {
  const badge = document.getElementById("verify-badge");
  try {
    const res = await lawvmVerify.verifyPack(pack, pack._rows);
    STATE._rowVerify = res;
    await refreshVerifyBadge();
  } catch (e) {
    badge.className = "verify-badge verify-fail";
    badge.textContent = "verify error: " + e.message;
  }
}

// The badge reflects BOTH the cached row-integrity pass AND a fresh recompute of
// the materialization_checkpoint tree_hash for the SCRUBBED date (when the date
// is an exact change date with a committed checkpoint). The ✓ thus tracks time.
async function refreshVerifyBadge() {
  const badge = document.getElementById("verify-badge");
  const res = STATE._rowVerify;
  if (!res) return;
  if (!res.ok) {
    badge.className = "verify-badge verify-fail";
    badge.innerHTML = `<span class="vb-x">✗</span> ${escHtml(res.detail)}`;
    return;
  }
  const eff = effectiveDate();
  const cp = STATE.pack.checkpointAt(eff);
  let cpLine = "";
  let cpOk = true;
  if (cp) {
    const rc = await lawvmVerify.recomputeCheckpoint(STATE.pack, eff);
    cpOk = rc.tree_hash === cp.tree_hash;
    cpLine = cpOk
      ? `checkpoint ${eff}: tree_hash recomputed ✓ (${cp.active_node_count} aktiivista solmua)`
      : `checkpoint ${eff}: tree_hash MISMATCH recomputed=${rc.tree_hash} committed=${cp.tree_hash}`;
  } else {
    cpLine = `(${eff}: ei tarkistuspistettä — ei muutospäivä)`;
  }
  if (!cpOk) {
    badge.className = "verify-badge verify-fail";
    badge.innerHTML = `<span class="vb-x">✗</span> tarkistuspisteen tiiviste ei täsmää (${escHtml(eff)})`;
    badge.title = cpLine;
    return;
  }
  badge.className = "verify-badge verify-ok";
  const cpTag = cp
    ? ` · tarkistuspiste ${escHtml(eff)} ✓`
    : "";
  badge.innerHTML = `<span class="vb-check">✓</span> ${res.rowsChecked} riviä todennettu${cpTag}`;
  badge.title =
    `${res.detail}\ncontent_leaf SetRoot recomputed: ${res.recomputedLeafRoot}\n${cpLine}\n` +
    `(GAP: manifest selection_index_root composition not reproduced — see substrate-verify.js)`;
}

// ---- small html helpers (mirrors the timeline viewer) -------------------- //

function escHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function escAttr(s) {
  return escHtml(s).replace(/"/g, "&quot;");
}
function cssEsc(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&");
}
function shortHash(s) {
  return String(s || "").replace(/^sha256:/, "").slice(0, 10);
}

boot().catch((e) => {
  document.getElementById("doc").innerHTML =
    `<p class="error">Lataus epäonnistui: ${escHtml(e.message)}</p>`;
  console.error(e);
});
