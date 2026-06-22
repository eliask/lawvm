// substrate-pack.js — a zero-build, server-less reader for a LawVM substrate
// pack (the latest PLAIN-JSONL pack format produced by `lawvm pack-work` and
// `lawvm.substrate.eu_ingest`). Given a base URL it fetches `manifest.json`,
// then the per-layer `.jsonl` files, parses NDJSON, and exposes:
//
//   * content leaves by content_leaf_hash             (the text payload)
//   * address nodes by struct_node_id / address_path  (the document skeleton)
//   * node versions + applicability facts             (address -> text, dated)
//   * relation edges indexed by source_ref + kind     (the proof-graded graph)
//   * the entity-node index (EU works: entity:celex:.. -> node)  (transclusion)
//
// No decompression, no SQLite — every layer is line-delimited JSON. The reader
// works straight from `python3 -m http.server`. It is deliberately tolerant of
// either the {object_hash, object} wrapper rows (cert/manifest/edge rows) OR a
// bare object row, so the same parse handles every layer.

"use strict";

// ---- low-level fetch + NDJSON ------------------------------------------- //

async function fetchText(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`fetch ${url} -> HTTP ${r.status}`);
  return r.text();
}

async function fetchJSON(url) {
  return JSON.parse(await fetchText(url));
}

// Parse NDJSON into an array of {object_hash, object} rows. A bare object (no
// wrapper) is normalised to { object_hash: null, object } so callers see one
// shape. Blank lines are skipped.
function parseNDJSON(text) {
  const rows = [];
  for (const line of text.split("\n")) {
    const s = line.trim();
    if (!s) continue;
    const parsed = JSON.parse(s);
    if (parsed && typeof parsed === "object" && "object" in parsed && "object_hash" in parsed) {
      rows.push({ object_hash: parsed.object_hash, object: parsed.object });
    } else {
      rows.push({ object_hash: null, object: parsed });
    }
  }
  return rows;
}

// ISO-date string compare (lexical works for YYYY-MM-DD; null/empty sorts first
// as an open lower bound). Returns <0, 0, >0.
function cmpDate(a, b) {
  const sa = a == null ? "" : String(a);
  const sb = b == null ? "" : String(b);
  if (sa === sb) return 0;
  if (sa === "") return -1;
  if (sb === "") return 1;
  return sa < sb ? -1 : 1;
}

// Unwrap a possibly-wrapped manifest.json ({object_hash, object} or bare).
function unwrapManifest(raw) {
  return raw && typeof raw === "object" && "object" in raw && "object_hash" in raw
    ? raw.object
    : raw;
}

// ---- the pack ------------------------------------------------------------ //

class SubstratePack {
  constructor(baseUrl, manifest) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.manifest = manifest;
    // indices (filled by _index)
    this.contentLeaves = new Map(); // content_leaf_hash -> text
    this.addressById = new Map(); // struct_node_id -> address node body
    this.addressByPath = new Map(); // address_path -> address node body
    this.entityNodeById = new Map(); // eu_entity_node_id -> address node body
    this.addrText = new Map(); // address_path -> [{interval:[from,to], leaf}]
    this.edges = []; // all relation-edge bodies
    this.edgesBySource = new Map(); // source_ref -> [edge]
    this.edgesByKind = new Map(); // relation_kind -> [edge]
    this.work = null; // lawvm.work.v1 body
    // ---- TIME layer (trace) indices -------------------------------------- //
    this.transitions = []; // lawvm.certified_tree_transition.v1 bodies (seq order)
    this.transByAddr = new Map(); // target_address -> [transition] (per-provision history)
    this.checkpoints = []; // lawvm.materialization_checkpoint.v1 (date order)
    this.checkpointByDate = new Map(); // effective_date -> checkpoint
    this.changeDates = []; // sorted unique effective_date strings (the time axis)
    this.genesisDate = null; // initial_state_event commencement date
    this.sourceArtifacts = new Map(); // canonical_id/source_ref -> {title, canonical_id}
    this.addrById = new Map(); // struct_node_id -> address_path (the inverse index)
  }

  // Resolve a manifest layer path to a fetchable URL. corpus_version segments
  // contain ':' (e.g. "fi:corpus:sha256:..") which must be percent-escaped per
  // path segment so the static server serves the file.
  _layerUrl(path) {
    const enc = path
      .split("/")
      .map((seg) => encodeURIComponent(seg))
      .join("/");
    return `${this.baseUrl}/${enc}`;
  }

  static async load(baseUrl) {
    const base = baseUrl.replace(/\/+$/, "");
    const manifestRaw = await fetchJSON(`${base}/manifest.json`);
    const manifest = unwrapManifest(manifestRaw);
    const pack = new SubstratePack(base, manifest);
    await pack._loadLayers();
    pack._index();
    return pack;
  }

  async _loadLayers() {
    this._rows = {};
    // Fetch each declared layer in parallel; a missing OPTIONAL layer (e.g. a
    // work with no edges) is tolerated as an empty layer.
    const optional = new Set(this.manifest.optional_layers || []);
    const jobs = (this.manifest.layers || []).map(async (layer) => {
      try {
        const text = await fetchText(this._layerUrl(layer.path));
        this._rows[layer.kind] = parseNDJSON(text);
      } catch (e) {
        if (optional.has(layer.kind)) {
          this._rows[layer.kind] = [];
        } else {
          throw e;
        }
      }
    });
    await Promise.all(jobs);
  }

  _index() {
    const base = this._rows.base || [];
    const state = this._rows.state || [];
    const trace = this._rows.trace || [];

    for (const { object: o } of base) {
      switch (o.schema) {
        case "lawvm.work.v1":
          this.work = o;
          break;
        case "lawvm.content_leaf.v1":
          this.contentLeaves.set(o.content_leaf_hash, o.text || "");
          break;
        case "lawvm.address_node.v1":
          this.addressById.set(o.struct_node_id, o);
          this.addressByPath.set(o.address_path, o);
          this.addrById.set(o.struct_node_id, o.address_path);
          if (o.eu_entity_node_id) this.entityNodeById.set(o.eu_entity_node_id, o);
          break;
        case "lawvm.initial_state_event.v1":
          // The genesis / commencement date — the left edge of the time axis.
          this.genesisDate = o.effective_date || this.genesisDate;
          break;
        default:
          break;
      }
    }

    // address -> dated text, via applicability facts (address_id + interval +
    // content_leaf_hash). node_version rows alone do not carry the address.
    for (const { object: o } of state) {
      if (o.schema !== "lawvm.applicability_fact.v1") continue;
      const node = this.addressById.get(o.address_id);
      if (!node) continue;
      const arr = this.addrText.get(node.address_path) || [];
      arr.push({
        interval: o.effect_interval || [null, null],
        leaf: o.content_leaf_hash,
      });
      this.addrText.set(node.address_path, arr);
    }

    // edges (may live in an edges/<corpus_version>/edges.jsonl layer)
    for (const { object: o } of this._rows.edges || []) {
      this._addEdge(o);
    }

    // ---- TIME axis: the trace layer (transitions + checkpoints) ---------- //
    // The change-history of the work. Transitions are the per-provision history
    // and the time-axis ticks; checkpoints carry the per-date tree_hash for
    // self-verify. A `source_artifact` row (if the attribution lane emitted it)
    // names the amending act behind a source_ref — read it if present, degrade
    // gracefully if absent.
    const dateSet = new Set();
    for (const { object: o } of trace) {
      switch (o.schema) {
        case "lawvm.certified_tree_transition.v1": {
          this.transitions.push(o);
          const addr = o.target_address || "";
          if (!this.transByAddr.has(addr)) this.transByAddr.set(addr, []);
          this.transByAddr.get(addr).push(o);
          if (o.effective_date) dateSet.add(o.effective_date);
          break;
        }
        case "lawvm.materialization_checkpoint.v1":
          this.checkpoints.push(o);
          if (o.effective_date) {
            this.checkpointByDate.set(o.effective_date, o);
            dateSet.add(o.effective_date);
          }
          break;
        case "lawvm.source_artifact.v1":
          this._indexSourceArtifact(o);
          break;
        default:
          break;
      }
    }
    // source_artifact rows may alternatively ride in the base layer.
    for (const { object: o } of base) {
      if (o.schema === "lawvm.source_artifact.v1") this._indexSourceArtifact(o);
    }
    this.transitions.sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
    this.checkpoints.sort((a, b) => cmpDate(a.effective_date, b.effective_date));
    this.changeDates = Array.from(dateSet).sort(cmpDate);
    if (this.genesisDate) dateSet.add(this.genesisDate);
    if (!this.changeDates.length && this.genesisDate) this.changeDates = [this.genesisDate];
  }

  _indexSourceArtifact(o) {
    // Tolerant of the not-yet-finalised schema: key by canonical_id and by any
    // source_ref(s) it claims, value = {title, canonical_id}.
    const title = o.title || o.act_title || o.canonical_id || "";
    const cid = o.canonical_id || o.artifact_id || o.source_ref || "";
    const entry = { title, canonical_id: cid };
    if (cid) this.sourceArtifacts.set(cid, entry);
    for (const ref of o.source_refs || (o.source_ref ? [o.source_ref] : [])) {
      this.sourceArtifacts.set(ref, entry);
    }
  }

  // The amending act behind a transition, IF the attribution lane attached a
  // source_artifact for one of its source_refs. Returns {title, canonical_id}
  // or null (degrade gracefully — show the change + date without an act name).
  amendingAct(transition) {
    for (const ref of transition.source_refs || []) {
      const a = this.sourceArtifacts.get(ref);
      // The bare work self-ref is NOT an amending act; skip it.
      if (a && !/:work:/.test(ref)) return a;
    }
    return null;
  }

  // The point-in-time tree_hash committed at `date` (exact change date), for the
  // viewer's self-verify against a recomputed checkpoint. Null if not a change
  // date.
  checkpointAt(date) {
    return this.checkpointByDate.get(date) || null;
  }

  // The transitions that affect `addressPath` up to and including `asOf` (or all,
  // if asOf omitted), in sequence order — the per-provision inline history.
  historyFor(addressPath, asOf) {
    const all = this.transByAddr.get(addressPath) || [];
    if (!asOf) return all;
    return all.filter((t) => cmpDate(t.effective_date, asOf) <= 0);
  }

  // Lifecycle of an address: the dated intervals it is in force, plus the date
  // (if any) it was deleted/repealed (last delete_subtree). Derived from its
  // applicability_fact intervals + its transitions.
  lifecycle(addressPath) {
    const versions = (this.addrText.get(addressPath) || [])
      .slice()
      .sort((a, b) => cmpDate(a.interval[0], b.interval[0]));
    const trans = this.transByAddr.get(addressPath) || [];
    let repealedAt = null;
    // A trailing delete with no later set = repealed/lapsed at that date.
    for (const t of trans) {
      if (t.action === "delete_subtree" || t.action === "tombstone") repealedAt = t.effective_date;
      else if (t.action === "set_subtree" || t.action === "restore") repealedAt = null;
    }
    return { versions, transitions: trans, repealedAt };
  }

  _addEdge(o) {
    this.edges.push(o);
    const src = o.source_ref || "";
    if (!this.edgesBySource.has(src)) this.edgesBySource.set(src, []);
    this.edgesBySource.get(src).push(o);
    const k = o.relation_kind || "";
    if (!this.edgesByKind.has(k)) this.edgesByKind.set(k, []);
    this.edgesByKind.get(k).push(o);
  }

  // Replace the edge set with a sidecar's resolved edges (kept out of the
  // certified pack, fetched separately by the viewer).
  async loadResolvedEdges(url) {
    const rows = parseNDJSON(await fetchText(url));
    this.edges = [];
    this.edgesBySource = new Map();
    this.edgesByKind = new Map();
    for (const { object: o } of rows) this._addEdge(o);
    return this.edges.length;
  }

  // The version of an address effective AT `asOf` (the `governing_text`
  // selection profile: the version whose half-open effect_interval [start,end)
  // contains asOf, i.e. start <= asOf < end). Returns {leaf, interval} or null
  // when the address has no version live at asOf (deleted / not yet commenced).
  // With no asOf, returns the latest-starting version (the present law).
  versionAt(addressPath, asOf) {
    const versions = this.addrText.get(addressPath);
    if (!versions || !versions.length) return null;
    if (asOf) {
      for (const v of versions) {
        const [vf, vt] = v.interval;
        if ((!vf || cmpDate(vf, asOf) <= 0) && (!vt || cmpDate(asOf, vt) < 0)) return v;
      }
      return null; // no version live at asOf (a ghost / pre-commencement)
    }
    let pick = versions[0];
    for (const v of versions) {
      const from = v.interval[0];
      if (from && (!pick.interval[0] || cmpDate(from, pick.interval[0]) >= 0)) pick = v;
    }
    return pick;
  }

  // The text of an address at (or as-of) a date. "" when no version is live at
  // asOf (the caller treats that as a ghost / not-yet-in-force).
  textAt(addressPath, asOf) {
    const v = this.versionAt(addressPath, asOf);
    return v ? this.contentLeaves.get(v.leaf) || "" : "";
  }

  // Look up a transclusion target: an EU entity node id -> {address, text,
  // title}. Used to inline a GDPR article into a Finnish statute view.
  //
  // An ARTICLE node holds only its title text ("6 artikla"); its substance
  // lives in PARAG children (article:006/paragraph:006.001 ...). So for an
  // article-level resolution we aggregate the title + every child paragraph,
  // each labelled by its kohta number, so an article-only cite transcludes the
  // full article — not just its heading.
  resolveEntityNode(entityNodeId) {
    const node = this.entityNodeById.get(entityNodeId);
    if (!node) return null;
    const base = {
      address: node.address_path,
      structuralKind: node.structural_kind,
      title: this.work ? this.work.title : "",
    };
    if (node.structural_kind === "article") {
      const prefix = node.address_path + "/paragraph:";
      const kids = Array.from(this.addressByPath.keys())
        .filter((p) => p.startsWith(prefix))
        .sort();
      // The PARAG text already carries its own "1." / "2." numbering, so we do
      // NOT re-prefix the kohta number (that produced a "1. 1." double label).
      const parts = [];
      const head = this.textAt(node.address_path);
      for (const p of kids) {
        const t = this.textAt(p);
        if (t) parts.push(t);
      }
      return { ...base, text: parts.length ? parts.join("\n\n") : head, paragraphs: kids.length };
    }
    return { ...base, text: this.textAt(node.address_path), paragraphs: 0 };
  }
}

// ---- proof-grade classification ----------------------------------------- //
//
// The "proof grade" is authority_plane x verification_level (the firewall made
// visible). We collapse the combination into a small set of presentation
// grades + a human label so the viewer can badge each edge by strength.

function proofGrade(edge) {
  const plane = edge.authority_plane;
  const level = edge.verification_level;
  const status = edge.status;

  // legal_state + a strong derivation = a verified legal derivation.
  if (plane === "legal_state") {
    if (level === "delta_verified" || level === "replay_verified" || level === "hash_identity") {
      return { grade: "verified", icon: "✓", label: "varmennettu johdos", planeLevel: `${plane} · ${level}` };
    }
    return { grade: "legal", icon: "§", label: "oikeustila", planeLevel: `${plane} · ${level}` };
  }
  // surface + registry_resolved = a deterministically resolved citation.
  if (plane === "surface" && level === "registry_resolved") {
    return { grade: "resolved", icon: "→", label: "ratkaistu viittaus", planeLevel: `${plane} · ${level}` };
  }
  // surface/evidence + source_asserted = source-claimed, target not pinned.
  if (level === "source_asserted") {
    return { grade: "asserted", icon: "≈", label: "lähde väittää", planeLevel: `${plane} · ${level}` };
  }
  // evidence + source_claimed_transposition kind = a claimed transposition.
  if (plane === "evidence") {
    return { grade: "evidence", icon: "⁇", label: "näyttö", planeLevel: `${plane} · ${level}` };
  }
  // overlay + induced_similarity = kinship / guess.
  if (level === "induced_similarity" || plane === "overlay") {
    return { grade: "kinship", icon: "≈", label: "sukulaisuus (arvio)", planeLevel: `${plane} · ${level}` };
  }
  return { grade: "other", icon: "·", label: `${plane} · ${level}`, planeLevel: `${plane} · ${level}`, status };
}

// A target is a cross-work EU transclusion target iff it is a resolved entity
// node id (entity:celex:..#..). An opaque celex:.. target is NOT transcludable.
function isEntityTarget(t) {
  return typeof t === "string" && t.startsWith("entity:celex:");
}
function celexOfEntity(t) {
  // entity:celex:32016R0679#006.001 -> 32016R0679
  const m = /^entity:celex:([0-9A-Za-z]+)#/.exec(t);
  return m ? m[1] : null;
}

// Browser global export (no modules — zero build).
window.SubstratePack = SubstratePack;
window.lawvmProofGrade = proofGrade;
window.lawvmIsEntityTarget = isEntityTarget;
window.lawvmCelexOfEntity = celexOfEntity;
window.lawvmCmpDate = cmpDate;
