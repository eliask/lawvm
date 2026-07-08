> **Status (2026-06-22):** Current-with-noted-drift. Kind: Normative (spec-first companion to CERTIFICATE_SCHEMA_V0, spec_version 0.3). Trace grammar + L1/L2/L3 naming authoritative and internally consistent; the checker that folds this trace is not yet implemented (doc states this). `export-transition-graph` CLI verified live.

---
title: LawVM Certified Tree-Transition Trace v0 — Replayable Patch Grammar
schema: lawvm.certified_tree_transition_trace.v0
spec_version: 0.3
status: normative draft (spec-first; bundle writer and checker v0 follow it)
---

# LawVM Certified Tree-Transition Trace v0

This spec defines the artifact family a certificate checker REPLAYS: the
base-tree representation, the content-blob store, the certified
tree-transition row grammar with full action semantics, and the per-date
state-root (checkpoint) semantics. It is the companion to
[CERTIFICATE_SCHEMA_V0.md](CERTIFICATE_SCHEMA_V0.md): the certificate commits
to `base_tree_root`, `certified_tree_transition_root`, `content_blobs_root`, and
`materialization_root`; this document defines how those roots are built and
how a checker with NO access to LawVM code folds the trace and verifies
every hash.

The trace is the L3 layer of the three-level operation model. The three
levels carry three distinct names — conflating them is the load-bearing
naming error this spec exists to prevent:

```text
L1  SourceInstruction                      — raw amendment language ("for X
    (source ops)                             substitute Y"); lives only
                                             inside the compiler
L2  CanonicalLegalOperation                — engine-resolved legal operation
    ("at address A, effective D, replace     ("at address A, effective D,
    /insert/repeal payload P")               replace/insert/repeal payload
                                             P"); carried on transitions for
                                             DISPLAY only
L3  CertifiedTreeTransition                — "at address A with subtree hash
                                             H_pre, set/delete to payload Q;
                                             resulting subtree hash H_post";
                                             the cheap, safe artifacts a
                                             checker or browser reducer can
                                             fold with hash assertions
```

Checker v0 verifies CertifiedTreeTransition traces; it does not
independently recompile SourceInstructions into CanonicalLegalOperations.

A checker that folds this trace verifies state derivation, not legal
meaning: whether the L2 operation correctly captured the amendment language
is outside this artifact's claim (certificate spec §7.1).

Normative keywords MUST/SHOULD/MAY follow RFC 2119. Hashing uses the
canonical hash profile and root constructors of the certificate spec §3.1
and §3.1.1; digest fields are rendered `"sha256:" + lowercase hex`, and the
empty string `""` denotes an absent subtree, never a hash.

## 1. Trace model

A trace describes the evolution of a **covering state**: a map from
addresses to IR subtrees that tiles the declared legal work/slice with no
overlap. Replay is a fold:

```text
state₀ = base tree (§3)
for each transition t in sequence order:
    assert precondition(state, t)        (per-action, §6)
    state = patch(state, t)
    assert postcondition(state, t)
for each declared change date d:
    covering-state hash of state-after-d == checkpoint hash for d (§8)
```

The trace operates on covering units, not on the raw whole tree: every
address in the state map is a covering unit (§2.4), and a transition
replaces or removes one unit's entire subtree. Finer-grained change
attribution (e.g. per-subsection diffs rendered by a viewer) is DERIVED by
diffing certified pre/post subtrees and MUST be labelled as derived, never
presented as certification.

## 2. Tree representation

### 2.1 IR node encoding

An IR subtree is encoded as a JSON object:

```json
{
  "kind": "section",
  "label": "6a",
  "text": "...",
  "attrs": { },
  "children": [ ]
}
```

`kind` and `label` are strings (`label` may be empty for unlabeled wrapper
nodes), `text` is the node's own prose (may be empty), `children` is the
ordered child list in document order, and `attrs` carries auxiliary node
metadata.

### 2.2 Canonical structural subtree hash (frozen)

The structural hash of a subtree is a sha256 over a depth-first byte stream
with explicit separators (NOT a canonical-JSON hash; this recipe is frozen
because the engine already emits it):

```text
hash(node):
  update utf8(kind)   ; update 0x00
  update utf8(label)  ; update 0x00   (empty string when label is null)
  update utf8(text)   ; update 0x01   (empty string when text is null)
  for each child in document order: recurse
  update 0x02
```

The hash of an ABSENT subtree (no node) is the empty string `""`.

Properties (normative):

- Sensitive to kind, label, text, child order, and tree shape — renumbers,
  insertions, deletions, and reorderings all change the hash. This is the
  `structure_hash` role of the certificate spec §3.2 and the value of every
  `pre_hash` / `post_hash` / `payload_hash` / unit `content_hash` field in
  this artifact family.
- **attrs-blind**: node `attrs` are carried in content blobs (§4) but are
  NOT part of the structural hash. Two subtrees differing only in `attrs`
  collide. Consumers MUST NOT key decisions on attrs via this hash —
  mirror of the seam's content_hash structure-blindness caveat.
- **attrs non-semantic rule (hard precondition of attrs-blindness)**: no
  field that affects legal text-state, applicability, temporal eligibility,
  tombstone/live status, addressability, or source identity may live ONLY
  in `attrs`. Such state MUST be represented in the typed node shape
  (kind/label/text/children), transition fields, residuals, or projection
  payloads committed by the relevant roots. If the engine ever needs a
  semantic field that only fits `attrs`, this recipe is no longer sound and
  `attrs` MUST be folded into the structural hash under a schema bump.
- Distinct from the text-only `content_hash` of seam responses
  (`sha256(irnode_to_text(...))`); the two MUST NOT be conflated even though
  both are sha256 hex values.

### 2.3 Address grammar

An address is the slash-joined `kind:label` path of labeled ancestors, e.g.
`chapter:4a/section:30a`. Unlabeled wrapper nodes do not contribute path
segments. Addresses come from engine-exported labels, never from positional
counters; a renumber/relabel CHANGES the address, so structural reordering
that matters legally is visible in addresses and hashes.

### 2.4 Covering frontier and granularity

The covering units of a tree at a declared `granularity` are, on each
root-to-leaf path, the deepest labeled node that is either of a stop kind or
leaf-stable (has no labeled descendant of a stop kind):

```text
granularity = chapter      stop at the shallowest labeled node
              (legacy; one unit per chapter / top-level section)
granularity = section      descend chapters, stop at sections
granularity = subsection   (default) descend to labeled subsections; a
                           section with no labeled subsection child is
                           itself the unit
```

Structural ancestors (chapters; sections above a labeled subsection) are
traversed through, never emitted, so the covering units tile the whole
(sliced) tree with NO overlap — folding the trace reconstructs the complete
tree state. When the trace is sliced (`slice_prefix` non-empty), only units
at or below the prefix are in scope.

The trace declares its `granularity` and `slice_prefix` in the base tree
(§3); all transitions and checkpoints in one trace use that single covering
frontier. Mixing granularities within one trace is INVALID.

## 3. Base tree (`materialization/base_tree.json`)

One JSON object:

```json
{
  "schema": "lawvm.base_tree.v0",
  "work_id": "fi:act:301/2004",
  "jurisdiction": "fi",
  "slice_prefix": "",
  "granularity": "subsection",
  "units": [
    { "address": "chapter:1/section:1", "content_hash": "sha256:..." }
  ]
}
```

- `units` is sorted by address (lexicographic) and is the initial covering
  state of the fold. Every `content_hash` MUST resolve in `content_blobs`
  (§4).
- `units` MAY be empty. The trace family the Finland exporter produces today
  starts from an EMPTY covering state: the first declared change date's
  transitions `set_subtree` every unit present at that date, establishing
  the initial materialized state. An empty `units` array is therefore the
  expected v0 base for that family, and the unamended source text enters the
  trace through those first transitions, not through the base tree.
- `base_tree_root = LeafHash("lawvm.base_tree.v0", <the object above>)`.

## 4. Content blobs (`materialization/content_blobs.jsonl`)

One row per distinct subtree, de-duplicated by structural hash:

```json
{ "content_hash": "sha256:...", "content_json": { "kind": "...", "label": "...", "text": "...", "attrs": {}, "children": [] } }
```

Rules:

- `content_hash` is the §2.2 structural hash of the subtree encoded in
  `content_json`. The blob store is keyed by the STRUCTURAL hash, not by a
  hash of the JSON bytes; the checker verifies each blob by decoding
  `content_json` and recomputing the structural hash
  (`TRACE.BLOB_HASH_MISMATCH` on disagreement).
- Duplicate `content_hash` rows are forbidden (`TRACE.DUPLICATE_LEAF`).
- Blob leaf: `LeafHash("lawvm.content_blob.v0", row)`;
  `content_blobs_root = SetRoot("lawvm.content_blobs.v0", blob leaf hashes)`.
- Every hash referenced by the base tree, by any transition `payload_hash`,
  or by any checkpoint's active set MUST have a blob row
  (`TRACE.PAYLOAD_BLOB_MISSING`). Orphan blobs (referenced by nothing) are
  permitted but SHOULD NOT be emitted.

## 5. Transition rows (`trace/certified_tree_transitions.jsonl`)

### 5.1 Row schema

One row per transition. Fields split into the **certified core** (hashed
into `transition_hash`) and **display annotation** (carried on the row,
excluded from the hash — same hashed/excluded discipline as the seam spec's
§3.1):

```json
{
  "transition_id": "t000017:2009-01-01:chapter:4/section:30a",
  "sequence": 17,
  "effective_date": "2009-01-01",
  "action": "set_subtree",
  "target_address": "chapter:4/section:30a",
  "pre_hash": "sha256:...",
  "post_hash": "sha256:...",
  "payload_hash": "sha256:...",
  "source_refs": ["fi.finlex.alkup.2008.1234"],
  "source_anchors": [
    {
      "source_artifact_id": "fi.finlex.alkup.2008.1234",
      "locator": "sources/<raw_source_hash>.bin",
      "span_unit": "byte",
      "span": [1042, 1311],
      "quote_hash": "sha256:..."
    }
  ],

  "legal_op_kind": "replace",
  "legal_op_summary": "replace chapter:4/section:30a [1234/2008]",
  "preparatory_refs": [
    { "kind": "fi_government_proposal", "display_label": "HE 46/2008 vp" }
  ],
  "expires_date": "",
  "flags": { "created": false, "removed": false, "temporary_expiry": false }
}
```

Certified core (hashed): `transition_id`, `sequence`, `effective_date`,
`action`, `target_address`, `pre_hash`, `post_hash`, `payload_hash`,
`source_refs`, `source_anchors` (possibly empty array).

Display annotation (NOT hashed): `legal_op_kind`, `legal_op_summary`,
`preparatory_refs`, `expires_date`, `flags`, and optional `display_span`
objects (decoded-character spans for UI rendering; the certified anchor is
always byte-level). These carry the L2 attribution a viewer renders — which
amending instrument changed this unit, the preparatory-work reference,
whether the change was a temporary act's scheduled lapse
(`flags.temporary_expiry`) — and are derivable/re-attributable without
changing any committed root. `preparatory_refs` entries are
`{kind, display_label}` pairs whose `kind` is a jurisdiction-adapter value
(`fi_government_proposal`, `uk_bill`, `uk_explanatory_notes`, ...) — the
field name is universal, the species is a value, and the local shorthand
("HE 46/2008 vp") survives only as a display label. Annotation drift never
invalidates a trace; annotation MUST NOT be treated as certified content. A
viewer or report MUST NOT present `preparatory_refs` or other annotation as
certified provenance unless it is also committed through a rooted
projection family (e.g. transition-graph projection rows).

Field rules:

- `sequence` is a positive integer, strictly increasing across the file,
  unique (`TRACE.SEQUENCE_ORDER_VIOLATION`).
- `effective_date` is an ISO date, non-decreasing with `sequence`, and MUST
  be a member of the certificate's committed change-date set
  (`TRACE.CHANGE_DATE_UNDECLARED`). The base sentinel `0000-00-00` is not a
  change date and MUST NOT appear.
- `transition_id` is `"t%06d:" + effective_date + ":" + target_address`
  (a readable convention; row identity for verification purposes is the
  leaf hash).
- `pre_hash`/`post_hash` are §2.2 structural hashes of the covering unit's
  subtree before/after the transition; `""` means absent.
- `source_refs` lists the source-artifact ids (certificate spec §3.2) whose
  instruments drive this transition; for a scheduled lapse of a temporary
  act, the ref points at the act that scheduled the expiry — a lapse is
  never an unexplained deletion.
- `source_anchors` pins the driving clauses. Certified anchor spans are
  BYTE-LEVEL only: `span_unit` MUST be `"byte"`, `span` is the half-open
  `[start_byte, end_byte)` range over the source artifact's RAW bytes, and
  `quote_hash = sha256(raw_source_bytes[start_byte:end_byte])`. No decoded
  string offsets in the certified anchor — two implementations disagreeing
  on decoding would verify different substrings under the same numbers;
  decoded-character spans live only in `display_span` annotation. When no
  anchor covers a `source_ref`, the array entry is simply absent AND a
  `kind=source_anchor_unavailable` residual scoped to this transition MUST
  exist in the certificate's residual ledger (§7).

### 5.2 Trace root

```text
transition leaf = LeafHash("lawvm.certified_tree_transition.v0", certified core)
certified_tree_transition_root =
  ListRoot("lawvm.certified_tree_transition_trace.v0",
           transition leaf hashes ordered by sequence)
```

Duplicate sequences make duplicate-leaf detection structural; any duplicate
is INVALID (`TRACE.DUPLICATE_LEAF`).

### 5.3 Same-date ordering

All transitions sharing an `effective_date` form one date-batch. Checkpoint
assertions (§6) apply only at date-batch boundaries: intra-batch
intermediate states are not checkpointed and carry no claim. Within a batch,
rows appear in emitter order (document order today); since each covering
address may change at most once per date-batch
(`TRACE.DUPLICATE_TARGET_IN_BATCH` otherwise), intra-batch order cannot
change the post-batch state.

## 6. Actions

### 6.1 v0 action vocabulary

Exactly two actions are normative in v0 — these are what the engine's
covering-state diff actually produces:

```text
set_subtree      the unit at target_address becomes the payload subtree
                 (covers: first materialization of a unit, insertion,
                 in-place amendment, repeal-to-placeholder, and restoration
                 of earlier content when a temporary overlay lapses)
delete_subtree   the unit at target_address is removed from the covering
                 state (covers: repeal-without-placeholder and whole-unit
                 expiry)
```

Representation notes (normative):

- **Tombstones are subtrees, not a distinct action.** A repeal that leaves
  an addressable placeholder materializes as `set_subtree` to the
  placeholder subtree. Only full disappearance from the covering frontier is
  `delete_subtree`.
- **Moves/renumbers are a delete+set pair.** The address carries identity
  (§2.3), so a renumbered unit appears as `delete_subtree` at the old
  address and `set_subtree` at the new one. Cross-address identity is L2
  display/lineage material, not a certified trace primitive.
- **Restoration is a plain set.** When a temporary act lapses and earlier
  content resurfaces, the trace records `set_subtree` to the earlier
  subtree (often a previously seen `payload_hash`;
  `flags.temporary_expiry` annotates the cause).

Reserved action names — `move_subtree`, `tombstone_subtree`,
`restore_subtree` — are set aside for future spec versions and MUST NOT be
emitted under v0. A checker encountering a reserved or unknown action
returns `TRACE.UNKNOWN_ACTION` → INVALID; it MUST NOT guess semantics.

### 6.2 `set_subtree`

```text
required fields   target_address; post_hash ≠ ""; payload_hash = post_hash;
                  pre_hash = current state ("" when the unit is new)
precondition      state[target_address] exists with hash = pre_hash, or
                  pre_hash = "" and target_address ∉ state
                  (TRACE.PRE_HASH_MISMATCH / TRACE.PRECONDITION_VIOLATION)
patch operation   state[target_address] := payload subtree
postcondition     state[target_address] hash = post_hash
hash checks       payload blob exists for payload_hash
                  (TRACE.PAYLOAD_BLOB_MISSING); decoded blob recomputes to
                  payload_hash (TRACE.BLOB_HASH_MISMATCH); post_hash =
                  payload_hash (TRACE.PAYLOAD_HASH_MISMATCH)
failure mode      any check fails → the trace is INVALID; a checker MUST
                  stop attributing state claims at the first failed
                  transition and report the offending transition_id
```

### 6.3 `delete_subtree`

```text
required fields   target_address; pre_hash ≠ ""; post_hash = "";
                  payload_hash = ""
precondition      state[target_address] exists with hash = pre_hash
                  (TRACE.PRE_HASH_MISMATCH / TRACE.PRECONDITION_VIOLATION)
patch operation   remove target_address from state
postcondition     target_address ∉ state
hash checks       none beyond pre/post (no payload)
failure mode      as for set_subtree
```

## 7. Source anchoring

Every `source_ref` of every transition MUST satisfy ONE of:

```text
1. at least one source_anchors entry references that source_artifact_id,
   resolves into the certificate's source bundle, its byte span exists in
   the artifact's raw bytes, and quote_hash matches sha256 of the spanned
   bytes; or
2. a residual row with kind=source_anchor_unavailable exists in the
   certificate's residual ledger whose scope names this transition (by
   transition_id or leaf hash) and the uncovered source_ref.
```

A multi-source transition (one date-batch step driven by several
instruments or clauses) carries one anchor per driving clause. Missing
anchors are NEVER silent (`TRACE.SOURCE_ANCHOR_MISSING` when neither holds
for some source_ref). The residual's `profile_effect` determines whether the
certificate is `qualified` or `blocked` under the certificate's profile
(certificate spec §5). Anchor verification is byte-level only; whether the
anchored clause legally entails the transition is outside the trace's claim
(certificate spec §7.1).

## 8. State roots and checkpoints (`materialization/state_roots.jsonl`)

### 8.1 Covering-state hash (frozen)

The certified checkpoint hash over a covering state is a sha256 over the
(address, subtree-hash) pairs sorted by address (NOT canonical-JSON; frozen
as the engine emits it):

```text
for (address, subtree_hash_hex) sorted by address:
  update utf8(address)          ; update 0x00
  update utf8(subtree_hash_hex) ; update 0x01
```

where `subtree_hash_hex` is the bare lowercase hex of the §2.2 structural
hash (no `sha256:` prefix inside this recipe). Depending only on the sorted
covering set, the hash is reproducible by any reducer that folds the same
transitions — never on engine internals or document order. Document order
is display material and carries no certification.

### 8.2 Checkpoint rows

One row per declared change date, after that date's full batch is applied:

```json
{ "date": "2009-01-01", "address_prefix": "", "tree_hash": "sha256:...", "active_unit_count": 87 }
```

- `tree_hash` is the §8.1 covering-state hash of the post-batch state;
  `active_unit_count` is the number of units in that state.
- Rows are ordered by `(date, address_prefix)`; in v0 a trace has a single
  `address_prefix` (its slice), so this is date order.
- Checkpoint leaf: `LeafHash("lawvm.state_root.v0", row)`;
  `materialization_root = ListRoot("lawvm.materialization_index.v0",
  checkpoint leaf hashes in row order)`.
- Replay check: after folding all transitions with
  `effective_date <= date`, the recomputed covering-state hash MUST equal
  `tree_hash` (`TRACE.CHECKPOINT_MISMATCH`). Every declared change date has
  exactly one checkpoint row and vice versa
  (`TRACE.CHECKPOINT_DATE_MISMATCH`).

The checkpoint set is the trace's bridge to the certificate's `time_axis`:
the row dates MUST equal the change-date set committed under
`change_dates_root` — which includes per-version expiry dates and
work-level fixed-term `expires_on` dates, since an expiry day is a real
state boundary (the tree on/after that date differs).

## 9. Failure model

Typed checker failures; each maps the bundle to verdict INVALID and MUST
embed the offending row identity (transition_id / content_hash / date) and
enough row content to be self-evidencing:

```text
TRACE.SEQUENCE_ORDER_VIOLATION    sequence not strictly increasing / dates
                                  decreasing with sequence
TRACE.CHANGE_DATE_UNDECLARED      effective_date outside the committed
                                  change-date set
TRACE.DUPLICATE_LEAF              duplicate transition / blob / checkpoint
                                  leaf
TRACE.DUPLICATE_TARGET_IN_BATCH   one address changed twice in one
                                  date-batch
TRACE.UNKNOWN_ACTION              action outside the v0 vocabulary
                                  (including reserved names)
TRACE.PRECONDITION_VIOLATION      target presence/absence contradicts the
                                  action's precondition
TRACE.PRE_HASH_MISMATCH           state hash at target ≠ pre_hash
TRACE.POST_HASH_MISMATCH          state hash after patch ≠ post_hash
TRACE.PAYLOAD_HASH_MISMATCH       payload_hash ≠ post_hash on set_subtree
TRACE.PAYLOAD_BLOB_MISSING        referenced content_hash has no blob row
TRACE.BLOB_HASH_MISMATCH          decoded blob does not recompute to its
                                  content_hash
TRACE.BLOB_DECODE_FAILURE         content_json not decodable as §2.1
TRACE.SOURCE_ANCHOR_MISSING       null anchor without a mapped
                                  source_anchor_unavailable residual
TRACE.CHECKPOINT_MISMATCH         replayed covering-state hash ≠ checkpoint
                                  tree_hash
TRACE.CHECKPOINT_DATE_MISMATCH    checkpoint dates ≠ declared change dates
TRACE.ROOT_MISMATCH               a recomputed artifact root ≠ the
                                  committed root
```

Missing artifact files are NOT trace failures; they yield the certificate
checker's `UNCHECKABLE_MISSING_ARTIFACTS` (certificate spec §7) before
trace replay begins.

## 10. Relationship to the transition-graph projection

The browser-facing transition graph (`transition-graph.v1`, SQLite + L2
sidecar) is a PROJECTION of this trace family, not a second authority: its
transitions, blobs, and checkpoints carry the same structural and
covering-state hashes defined here, plus display annotation and rendering
indexes. Where the two disagree, the certified trace wins; the graph's
exporter self-checks are `exporter_invariants_passed`, never certification
(certificate spec §6.1).

## 11. Versioning

FROZEN in v0 (changing any is a schema bump):

```text
1. the structural subtree hash recipe (§2.2), including attrs-blindness
2. the covering-state hash recipe (§8.1)
3. the certified-core field set and the hashed/annotation split (§5.1)
4. the v0 action vocabulary and per-action semantics (§6)
5. the address grammar and covering-frontier rule (§2.3, §2.4)
6. root domains and ordering rules (§4, §5.2, §8.2)
7. the source-anchor-or-residual rule (§7)
```

EXPLICITLY UNSTABLE in v0:

```text
annotation field set and rendering; transition_id readable format;
attrs payload contents; reserved-action future semantics; sliced-trace
multi-prefix layout; inclusion-proof optimization
```

### 11.1 Changes 0.1 → 0.2

Second adversarial-review round; pre-implementation contract repairs.

- `source_anchor` (singular, nullable) → `source_anchors` (array): a
  multi-source transition carries one anchor per driving clause, and the
  anchor-or-residual rule is enforced PER source_ref (§5.1, §7).
- Certified anchor spans are byte-level only (`span_unit="byte"`, half-open
  range over raw bytes, quote_hash over the raw byte slice); decoded
  character spans are display annotation (`display_span`) (§5.1).
- attrs non-semantic rule added as a hard precondition of the attrs-blind
  structural hash (§2.2).
- `he_ref` annotation → `preparatory_refs` (`{kind, display_label}` pairs;
  jurisdiction species as values, never field names); base tree
  `statute_id` → `work_id`; viewer labeling rule for uncommitted annotation
  (§3, §5.1).

### 11.2 Changes 0.2 → 0.3 (freeze round)

- Schema/domain ids renamed before first emission (hash inputs):
  `lawvm.canonical_transition.v0` → `lawvm.certified_tree_transition.v0`,
  `lawvm.canonical_transition_trace.v0` →
  `lawvm.certified_tree_transition_trace.v0`; trace file
  `trace/certified_tree_transitions.jsonl`; this document renamed from
  CANONICAL_TRANSITION_TRACE_V0.md. "Canonical" was too loaded — checker v0
  replays tree patches, not canonical legal operations.
- Three-level terminology fixed: SourceInstruction (L1) /
  CanonicalLegalOperation (L2) / CertifiedTreeTransition (L3); the checker
  claim is stated in those terms.
