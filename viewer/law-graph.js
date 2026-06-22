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

  render();
  runVerifier(pack);
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
  let html = "";
  const kids = Array.from(node.children.values());
  for (const child of kids) {
    const hasKids = child.children.size > 0;
    const kindLabel = KIND_LABEL[child.kind] || child.kind;
    const text = pack.textAt(child.addr);
    const edges = edgesByAddr.get(child.addr) || [];

    html += `<div class="node kind-${escAttr(child.kind)}" data-depth="${depth}" data-addr="${escAttr(child.addr)}">`;
    html += `<div class="node-row spyable" data-addr="${escAttr(child.addr)}">`;
    html += `<span class="node-toggle leaf"></span>`;
    html += `<span class="node-label">${escHtml(kindLabel)} ${escHtml(child.label)}</span>`;
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
  const statusChip = `<span class="status-chip status-${escAttr(edge.status)}">${escHtml(edge.status)}</span>`;
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
  html += `<span class="status-chip status-${escAttr(edge.status)}">${escHtml(edge.status)}</span>`;
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

  document.getElementById("doc-title").textContent = (pack.work && pack.work.title) || STATE.entry.title;
  document.getElementById("doc-meta").textContent =
    `${pack.addressByPath.size} osoiteltavaa solmua · ${pack.edges.length} relaatioreunaa · pakkaus ${shortHash(pack.manifest.pack_kind)}`;

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

  // edge rail (the graph summary, grouped by proof grade)
  renderEdgeRail(pack, euEdgeCount);

  wireInteractions();
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

async function runVerifier(pack) {
  const badge = document.getElementById("verify-badge");
  try {
    const res = await lawvmVerify.verifyPack(pack, pack._rows);
    if (res.ok) {
      badge.className = "verify-badge verify-ok";
      badge.innerHTML = `<span class="vb-check">✓</span> ${res.rowsChecked} riviä todennettu`;
      badge.title =
        `${res.detail}\ncontent_leaf SetRoot recomputed: ${res.recomputedLeafRoot}\n` +
        `(GAP: manifest selection_index_root composition not reproduced — see substrate-verify.js)`;
    } else {
      badge.className = "verify-badge verify-fail";
      badge.innerHTML = `<span class="vb-x">✗</span> ${res.detail}`;
    }
  } catch (e) {
    badge.className = "verify-badge verify-fail";
    badge.textContent = "verify error: " + e.message;
  }
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
