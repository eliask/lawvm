// substrate-verify.js — a tiny in-browser re-implementation of the LawVM
// canonical-JSON-v1 hashing + named-root constructors, so the viewer can
// recompute a layer root from its rows and compare it to the manifest. This is
// the substrate's "verify with a tiny checker, not the engine" story shown as a
// live badge: a few dozen lines of vanilla JS reproduce the trust spine.
//
// Profile (must match src/lawvm/substrate/canonical_json.py + roots.py):
//   canonical_json_bytes(obj) = JSON with ensure_ascii=True, sort_keys=True,
//                               separators=(",", ":")  -> UTF-8 bytes
//   LeafHash(domain, obj) = sha256("lawvm:"+domain+"\x00" + cjson(obj))
//   SetRoot(domain, hs)   = sha256("lawvm:"+domain+":set\x00" + cjson(sorted(hs)))
//   SeqRoot(domain, hs)   = sha256("lawvm:"+domain+":list\x00" + cjson(list(hs)))
//   all rendered as "sha256:" + hexdigest()
//
// The {object_hash, object} row hash is LeafHash WITHOUT a domain tag, i.e.
// sha256(cjson(object)) — `semantic_hash` in canonical_json.py.

"use strict";

// ---- canonical JSON (ensure_ascii=True, sort_keys=True, no spaces) ------- //

// Python json.dumps(ensure_ascii=True) escapes every non-ASCII char as \uXXXX
// (and surrogate pairs for astral chars), with the standard short escapes for
// control chars. We reproduce that exactly so the bytes match the engine.
function jsonEscapeString(s) {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    const ch = s[i];
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (c === 0x08) out += "\\b";
    else if (c === 0x09) out += "\\t";
    else if (c === 0x0a) out += "\\n";
    else if (c === 0x0c) out += "\\f";
    else if (c === 0x0d) out += "\\r";
    else if (c < 0x20 || c > 0x7e) {
      out += "\\u" + c.toString(16).padStart(4, "0");
    } else out += ch;
  }
  return out + '"';
}

function canonicalJSON(obj) {
  if (obj === null) return "null";
  const t = typeof obj;
  if (t === "boolean") return obj ? "true" : "false";
  if (t === "number") {
    if (!Number.isInteger(obj)) throw new Error("canonical JSON forbids floats");
    return String(obj);
  }
  if (t === "string") return jsonEscapeString(obj);
  if (Array.isArray(obj)) return "[" + obj.map(canonicalJSON).join(",") + "]";
  if (t === "object") {
    const keys = Object.keys(obj).sort();
    return "{" + keys.map((k) => jsonEscapeString(k) + ":" + canonicalJSON(obj[k])).join(",") + "}";
  }
  throw new Error("non-canonical JSON value: " + t);
}

function utf8(s) {
  return new TextEncoder().encode(s);
}

function concatBytes(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// ---- named hashes -------------------------------------------------------- //

async function semanticHash(obj) {
  return "sha256:" + (await sha256Hex(utf8(canonicalJSON(obj))));
}

async function leafHash(domain, obj) {
  const prefix = utf8("lawvm:" + domain + "\0");
  const body = utf8(canonicalJSON(obj));
  return "sha256:" + (await sha256Hex(concatBytes(prefix, body)));
}

async function setRoot(domain, hashes) {
  const sorted = Array.from(hashes).sort();
  const prefix = utf8("lawvm:" + domain + ":set\0");
  const body = utf8(canonicalJSON(sorted));
  return "sha256:" + (await sha256Hex(concatBytes(prefix, body)));
}

// ---- the verifier -------------------------------------------------------- //
//
// What this tiny checker proves (L0 row integrity — the strongest claim a few
// dozen lines of vanilla JS can make WITHOUT re-running the engine):
//   1. every wrapped {object_hash, object} row in the base + state layers
//      recomputes: object_hash == semantic_hash(object). One tampered char
//      anywhere in the certified text-state flips the badge to FAIL.
//   2. the content_leaf SetRoot recomputes (reported for transparency).
//
// The documented GAP: chaining the content_leaf root up to the manifest's
// `selection_index_root` requires reproducing the 8-child state_selection_root
// + projection_root composition (build_selection_index_roots) — out of scope
// for the tiny checker, so the manifest-root tie-in is NOT claimed here. The
// row-hash pass IS the substrate's L0 integrity check, run in the browser.

async function verifyPack(pack, rawRows) {
  let rowHashOk = true;
  let rowsChecked = 0;
  let firstMismatch = null;
  const leafHashes = [];

  for (const layerKind of ["base", "state"]) {
    for (const row of rawRows[layerKind] || []) {
      const o = row.object;
      if (!o) continue;
      if (o.schema === "lawvm.content_leaf.v1") leafHashes.push(o.content_leaf_hash);
      if (row.object_hash == null) continue; // bare rows carry no claimed hash
      rowsChecked++;
      const recomputed = await semanticHash(o);
      if (recomputed !== row.object_hash) {
        rowHashOk = false;
        if (!firstMismatch) {
          firstMismatch = { schema: o.schema, declared: row.object_hash, recomputed };
        }
      }
    }
  }

  const recomputedLeafRoot = leafHashes.length ? await setRoot("content_leaf", leafHashes) : null;

  return {
    ok: rowHashOk && rowsChecked > 0,
    rowsChecked,
    rowHashOk,
    contentLeaves: leafHashes.length,
    recomputedLeafRoot,
    firstMismatch,
    detail: rowHashOk
      ? `${rowsChecked} certified rows recomputed and matched (L0 integrity)`
      : `row hash MISMATCH in ${firstMismatch && firstMismatch.schema} (tamper/corruption)`,
  };
}

window.lawvmVerify = { canonicalJSON, semanticHash, leafHash, setRoot, verifyPack };
