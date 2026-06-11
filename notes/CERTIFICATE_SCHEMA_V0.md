---
title: LawVM Certificate Schema v0 — Temporal Dossier and Checker Contract
schema: lawvm.certificate.v0
spec_version: 0.2
status: normative draft (spec-first; bundle writer and checker v0 follow it)
---

# LawVM Certificate Schema v0

The certificate is the artifact that converts "trust the LawVM pipeline" into
"check this bundle". It commits, under one root hash, to everything a
text-state claim depends on: source bytes, the canonical transition trace,
materialization roots, the projections consumers actually read, and — first
class, never elided — the typed residue of what could NOT be proven.

The core principle:

```text
Certificate first, projections second.
Incompleteness is explicit and blocks clean assertions.
```

Normative keywords MUST/SHOULD/MAY follow RFC 2119. Companion specs:
[CANONICAL_TRANSITION_TRACE_V0.md](CANONICAL_TRANSITION_TRACE_V0.md) (the
replayable transition grammar, base-tree schema, content blobs, and
state-root/checkpoint semantics the checker folds),
[SEAM_SPEC_PROVISION_STATE.md](SEAM_SPEC_PROVISION_STATE.md) (the seam, now a
certificate projection), [LAWVM_PROOF_SURFACES.md](LAWVM_PROOF_SURFACES.md),
[COMPILER_OBSERVATION_STREAM.md](COMPILER_OBSERVATION_STREAM.md),
[REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md).

## 1. Claim model: the per-statute temporal dossier

A v0 certificate asserts a **declared statute/slice timeline** — not one
provision query, and not one transition:

```text
For jurisdiction J, statute S, source bundle B, profile P, interpretation
policy I, over declared time scope W, LawVM derives a temporal text-state
timeline T for a declared address scope, with source artifacts D,
replay/transition trace R, materialization roots M, projection roots Q, and
typed residual ledger Z.
```

The certificate identity is the tuple:

```json
{
  "jurisdiction": "fi",
  "statute_id": "301/2004",
  "scope": { "kind": "whole_statute | address_prefix | address_set", "addresses": [] },
  "time_scope": { "kind": "closed_interval", "from": "2004-05-01", "to": "2026-06-10" },
  "profile_id": "fi.strict.current",
  "interpretation_policy_id": "lawvm.fi.default.v1",
  "source_bundle_id": "sha256:..."
}
```

`time_scope` declares the temporal interval the certificate claims to cover:

```text
kind = closed_interval     claims cover [from, to] inclusive
kind = open_ended          claims cover [from, ∞); "to" is null
kind = change_dates_only   claims cover exactly the committed change-date set
                           within [from, to]; intervals between change dates
                           are asserted only as "no modeled boundary"
```

Residue accounting, the status algebra (§5.2), and the checker's scope
intersection (§5.3) are all evaluated against `time_scope`. A consumer
querying outside the declared `time_scope` is outside the certificate's claim
and gets NO assertion, clean or otherwise.

Leaf units (the five kinds every artifact in the bundle reduces to):

```text
1. SourceArtifact leaf      — identity metadata + hash of exact bytes
                              observed at a named locator (§3.2)
2. CanonicalTransition leaf — one typed replay step with pre/post hashes
                              (companion spec)
3. ProvisionState leaf      — one (address, date-interval) text-state
4. Residual/Finding leaf    — one typed gap, recovery, or pathology
5. Projection leaf          — one consumer-facing row (seam/dump/graph/packet)
```

Consumer mapping: MeVM reads provision-state/seam projections; the viewer
reads transition + provision-state leaves; Open Law and the checker read the
source bundle + transition trace + materialization roots; public review
packets are projections over residual/finding leaves.

A v0 certificate MUST NOT be scoped to a single seam query (the seam is
already a per-provision surface and explicitly not a stand-alone proof
object) and MUST NOT be scoped to a single transition (a lone transition
cannot honestly claim the PIT state of a statute).

## 2. Envelope

```json
{
  "schema": "lawvm.certificate.v0",
  "certificate_id": "sha256:<certificate_root>",
  "claim_kind": "statute_temporal_text_state",
  "jurisdiction": "fi",
  "statute_id": "301/2004",
  "scope": { "kind": "whole_statute", "addresses": [] },
  "time_scope": { "kind": "closed_interval", "from": "2004-05-01", "to": "2026-06-10" },
  "profile": { "profile_id": "fi.strict.current", "profile_hash": "sha256:..." },
  "interpretation_policy": { "policy_id": "lawvm.fi.default.v1", "policy_hash": "sha256:..." },
  "time_axis": {
    "change_dates_root": "sha256:...",
    "min_date": "2004-05-01",
    "max_date": "2026-06-10"
  },
  "roots": {
    "source_bundle_root": "sha256:...",
    "base_tree_root": "sha256:...",
    "canonical_transition_root": "sha256:...",
    "content_blobs_root": "sha256:...",
    "materialization_root": "sha256:...",
    "projection_root": "sha256:...",
    "residual_root": "sha256:...",
    "finding_root": "sha256:...",
    "coverage_root": "sha256:..."
  },
  "certificate_status": "clean | qualified | blocked",
  "residual_summary": { },
  "projection_coverage": { },
  "artifacts": { },
  "checker_contract": {
    "checker_version": "lawvm.checker.v0",
    "hash_profile": "lawvm.hash.canonical_json.v1"
  }
}
```

The envelope stays SMALL: roots, summaries, claim fields, and hash-addressed
artifact references. Full traces, source bytes, content blobs, and projection
rows live in the bundle (§4), fetchable by hash — and MANDATORY for checking.
A certificate whose referenced artifacts are unavailable cannot be checked
and MUST NOT be presented as checkable (§7, verdict
`UNCHECKABLE_MISSING_ARTIFACTS`).

### 2.1 time_axis

`time_axis.change_dates_root` commits to **all timeline boundary dates** of
the declared scope, not only amendment effective dates:

```text
base effective dates (excluding the 0000-00-00 base sentinel)
amendment effective dates
per-version expires dates
statute-level fixed-term expires_on dates (määräaikainen laki bounds)
enactment / commencement gate dates where materialized
temporal-event dates (revive / commence / suspend) where materialized
```

A fixed-term statute's lapse date is a real state boundary; a
`change_dates_root` built only from amendment effective dates would let a
projection silently miss the expired interval. The root is `SetRoot` (§3.1.1)
over the ISO date strings. `min_date`/`max_date` are the extremes of that
set and MUST lie inside `time_scope`.

## 3. Hash discipline

One top root, named subroots, existing hashes preserved as leaves or
projection hashes:

```text
certificate_root  (over the whole envelope minus certificate_id, §3.3)
├── source_bundle_root
├── base_tree_root
├── canonical_transition_root
├── content_blobs_root
├── materialization_root
├── projection_root
│   ├── seam_projection_root
│   ├── dump_projection_root
│   └── transition_graph_projection_root
├── residual_root
├── finding_root
└── coverage_root
```

### 3.1 Canonical hash profile (frozen)

```json
{
  "hash_profile": "lawvm.hash.canonical_json.v1",
  "json": { "ensure_ascii": true, "sort_keys": true, "separators": [",", ":"] },
  "hash": "sha256",
  "text_normalization": "lawvm.text.irnode_to_text.v1",
  "structure_normalization": "lawvm.ir.canonical_json.v1"
}
```

`canonical_json(obj)` below means the UTF-8 bytes of the JSON encoding under
this profile. Digest values are rendered as `"sha256:" + lowercase hex` in
every bundle artifact field; the empty string `""` denotes an absent/empty
subject (e.g. an absent subtree), never a hash of nothing.

#### 3.1.1 Canonical root construction (frozen)

Three constructors, all domain-tagged. Domain tags prevent any leaf in one
artifact family from being replayed as a leaf of another:

```text
LeafHash(domain, obj) =
  sha256("lawvm:" + domain + "\x00" + canonical_json(obj))

ListRoot(domain, ordered_leaf_hashes) =
  sha256("lawvm:" + domain + ":list\x00" + canonical_json(ordered_leaf_hashes))

SetRoot(domain, leaf_hashes) =
  sha256("lawvm:" + domain + ":set\x00" + canonical_json(sorted(leaf_hashes)))
```

where `ordered_leaf_hashes` / `leaf_hashes` are JSON arrays of rendered
digest strings (`"sha256:..."`).

Rules:

- **Empty root.** An empty artifact's root is the constructor applied to the
  empty array (`canonical_json([])` = `[]`). There is no special sentinel; an
  empty list-root and an empty set-root of the same domain still differ.
- **Duplicate leaves.** Under `SetRoot`, two rows with identical leaf hashes
  are FORBIDDEN; a bundle containing them is INVALID. Under `ListRoot`,
  ordering keys (e.g. transition `sequence`) MUST be unique, which makes
  duplicate leaves structurally impossible; a duplicate is INVALID.
- **Two non-JSON hash recipes are frozen as-is** because the engine already
  emits them: the canonical structural subtree hash and the covering-state
  (checkpoint) hash. Both are byte-stream recipes defined exactly in the
  companion spec ([CANONICAL_TRANSITION_TRACE_V0.md](CANONICAL_TRANSITION_TRACE_V0.md)
  §2.2, §8.1); the certificate consumes their outputs as leaf values.

Per-artifact root rules:

```text
canonical_transitions.jsonl   ListRoot("lawvm.canonical_transition_trace.v0", ...)
                              ordered by transition sequence; duplicates forbidden
source bundle                 SetRoot("lawvm.source_bundle.v0", source_artifact_hash leaves)
base_tree.json                LeafHash("lawvm.base_tree.v0", base tree object)
                              (single object; units sorted by address inside it)
content_blobs.jsonl           SetRoot("lawvm.content_blobs.v0", blob leaf hashes)
state_roots.jsonl             ListRoot("lawvm.materialization_index.v0", ...)
                              ordered by (date, address_prefix)
projection rows (per family)  SetRoot("lawvm.projection.<family>.v0", projection_hash leaves)
residuals.jsonl               SetRoot("lawvm.residual_ledger.v0", residual leaf hashes)
findings.jsonl                SetRoot("lawvm.finding_ledger.v0", finding leaf hashes)
source_unit_coverage.jsonl    SetRoot("lawvm.source_unit_coverage.v0", row leaf hashes)
potential_operation_coverage  SetRoot("lawvm.potential_operation_coverage.v0", row leaf hashes)
profile / policy manifests    LeafHash over the manifest object (§3.5)
```

Composite roots:

```text
projection_root = LeafHash("lawvm.projection_root.v0", {
  "seam": "<seam_projection_root or null>",
  "dump": "<dump_projection_root or null>",
  "transition_graph": "<transition_graph_projection_root or null>"
})

coverage_root = LeafHash("lawvm.coverage.v0", {
  "source_unit_coverage": "sha256:...",
  "potential_operation_coverage": "sha256:..."
})
```

A projection family that is not emitted appears as an explicit JSON `null` in
the `projection_root` preimage AND as `null` in the artifact manifest (§4) —
absence is committed, never ambient.

### 3.2 Canonical leaf hashes

```text
content_hash            sha256(normalized text of provision subtree)
                        (exists today; remains text-only and structure-blind)
structure_hash          canonical structural subtree hash (companion spec §2.2)
                        — sensitive to kind/label/text/order; content_hash
                        intentionally aliases structural variants, so the
                        certificate commits to structure through this hash
provision_state_hash    LeafHash("lawvm.provision_state_leaf.v0", leaf)
seam_derived_state_hash existing seam hash, preserved as a projection-payload
                        member exactly as seam spec 0.2 §3 defines it
transition_hash         LeafHash("lawvm.canonical_transition.v0", certified
                        core fields; companion spec §5.3)
raw_source_hash         sha256(raw source bytes)
source_artifact_hash    LeafHash("lawvm.source_artifact.v0", identity object
                        below)
```

A SourceArtifact leaf is **identity metadata plus the raw byte hash**, never
the byte hash alone — the same bytes observed under two roles or at two
locators are two distinct artifacts:

```json
{
  "source_artifact_id": "fi.finlex.alkup.2004.301",
  "jurisdiction": "fi",
  "role": "source_as_enacted | amending_source | current_consolidation | public_page_snapshot",
  "canonical_id": "301/2004",
  "locator": "sources/<raw_source_hash>.bin",
  "raw_source_hash": "sha256:..."
}
```

`retrieved_at` and other acquisition provenance MAY be carried on the source
row but are NOT part of the hashed identity object (non-semantic, mirrors the
seam's engine/source exclusion rule). `source_bundle_root` commits to
SourceArtifact leaves, not raw bytes alone.

### 3.3 certificate_root and certificate_id

```text
certificate_root = LeafHash("lawvm.certificate.v0.root",
                            envelope_without_certificate_id)

certificate_id   = "sha256:" + <hex of certificate_root>
```

`envelope_without_certificate_id` is the COMPLETE envelope object of §2 with
exactly one member removed: `certificate_id`. Nothing else is omitted. In
particular the root therefore commits to:

```text
schema, claim_kind, jurisdiction, statute_id, scope, time_scope,
profile (id + hash), interpretation_policy (id + hash),
time_axis (change_dates_root, min_date, max_date),
ALL roots (§3 tree, including coverage_root and base_tree_root),
certificate_status, residual_summary, projection_coverage,
the FULL artifacts manifest (schemas, roots, locators, nulls),
checker_contract (checker version + hash profile)
```

There are no non-committed envelope fields. Altering a residual summary, an
artifact locator, or a declared-null projection family changes
`certificate_root`. The checker recomputes the root from the envelope it was
handed and rejects on mismatch; it additionally recomputes every subroot from
bundle contents, so an envelope that commits to wrong subroots is caught even
though the envelope is self-consistent.

### 3.4 Projection rule

Two layers, split so that certificate parentage can never feed back into the
hashes the certificate commits to (the parentage block contains
`certificate_root`; hashing it into projection rows would be cyclic):

```text
projection_payload   the consumer-facing seam/dump/viewer/packet row, exactly
                     as its own schema defines it, WITHOUT any certificate
                     parentage

projection_wrapper   { projection_payload, certificate: <parentage block> }
```

Only the payload is hashed:

```text
projection_hash            = LeafHash("lawvm.projection_payload.v0",
                                      projection_payload)
<family>_projection_root   = SetRoot("lawvm.projection.<family>.v0",
                                     projection_hashes)
```

Projection-row order is not semantically meaningful in v0; all projection
families use set roots.

The parentage block every projected artifact MUST carry:

```json
{
  "certificate_id": "sha256:...",
  "certificate_root": "sha256:...",
  "projection_kind": "lawvm.provision_state",
  "projection_schema": "lawvm.provision_state.v1",
  "projection_spec_version": "0.2",
  "projection_spec_hash": "sha256:...",
  "projection_hash": "sha256:...",
  "inclusion_path": ["..."]
}
```

Rules:

- The parentage block is NOT part of `projection_hash` or any projection
  root. It is a portable inclusion witness only.
- Projection identity pins schema AND spec version AND spec hash. A seam row
  is `(projection_schema=lawvm.provision_state.v1,
  projection_spec_version=0.2)`; the 0.2 seam added `expired`,
  `expiry_unverified`, and the conditional `expiry` hash member, so a 0.1 row
  and a 0.2 row with the same schema string are NOT interchangeable.
- `projection_spec_hash` is the sha256 of the normative spec document the
  emitter built against, recorded in the projection-spec manifest (§3.5). It
  is an audit pin; checker dispatch is keyed by
  `(projection_schema, projection_spec_version)` (§7).
- For seam rows, `hashes.derived_state_hash` remains EXACTLY as seam spec 0.2
  §3 defines it. Certificate parentage MUST NOT change `derived_state_hash`
  — parentage is not among the seam's hashed fields, and adding it would be a
  breaking seam change. The projection payload is the full seam response
  (including the `expiry` block on `expired`/`expiry_unverified` rows);
  `derived_state_hash` stays the seam's internal commitment while
  `projection_hash` commits to the whole payload.
- In v0, `inclusion_path` MAY be a simple explicit path inside the bundle
  rather than an optimized Merkle proof. The contract — every projection is
  traceable to exactly one certificate root — is what is frozen, not the
  proof encoding.

### 3.5 Profile, policy, and projection-spec manifests

`profile_hash` and `policy_hash` are NOT detached strings: each MUST be the
`LeafHash` of a manifest that is itself a bundle artifact under `policy/`
(§4), so a checker can inspect what `fi.strict.current` meant when the
bundle was emitted and old bundles stay self-describing when profiles evolve:

```text
profile_hash = LeafHash("lawvm.strict_profile.v0", strict_profile.json object)
policy_hash  = LeafHash("lawvm.interpretation_policy.v0",
                        interpretation_policy.json object)
```

The projection-spec manifest (`policy/projection_specs.json`) lists, per
projection kind, the `{schema, spec_version, spec_hash}` triple of §3.4. The
checker contract manifest (`policy/checker_contract.json`) restates the
envelope's `checker_contract` for bundle-local inspection.

## 4. Bundle layout

```text
certificate.json
sources/
  <raw_source_hash>.bin
policy/
  strict_profile.json
  interpretation_policy.json
  projection_specs.json
  checker_contract.json
trace/
  canonical_transitions.jsonl
  canonical_transitions.root
materialization/
  base_tree.json
  content_blobs.jsonl
  state_roots.jsonl
projections/
  seam_rows.jsonl
  dump_rows.jsonl
  transition_graph.jsonl
residue/
  residuals.jsonl
  findings.jsonl
coverage/
  source_unit_coverage.jsonl
  potential_operation_coverage.jsonl
```

The `artifacts` manifest is EXHAUSTIVE and NORMATIVE: every artifact the
checker may need has an entry with `schema`, `root`, and `locator`; a
projection family or optional artifact that is not emitted is an explicit
`null`, never a missing key. A checker MUST NOT guess paths.

```json
{
  "artifacts": {
    "source_bundle": {
      "schema": "lawvm.source_bundle.v0",
      "root": "sha256:...",
      "locator": "sources/"
    },
    "profile_manifest": {
      "schema": "lawvm.strict_profile.v0",
      "root": "sha256:...",
      "locator": "policy/strict_profile.json"
    },
    "interpretation_policy_manifest": {
      "schema": "lawvm.interpretation_policy.v0",
      "root": "sha256:...",
      "locator": "policy/interpretation_policy.json"
    },
    "projection_spec_manifest": {
      "schema": "lawvm.projection_specs.v0",
      "root": "sha256:...",
      "locator": "policy/projection_specs.json"
    },
    "base_tree": {
      "schema": "lawvm.base_tree.v0",
      "root": "sha256:...",
      "locator": "materialization/base_tree.json"
    },
    "canonical_transition_trace": {
      "schema": "lawvm.canonical_transition_trace.v0",
      "root": "sha256:...",
      "locator": "trace/canonical_transitions.jsonl"
    },
    "content_blobs": {
      "schema": "lawvm.content_blobs.v0",
      "root": "sha256:...",
      "locator": "materialization/content_blobs.jsonl"
    },
    "materialization_index": {
      "schema": "lawvm.materialization_index.v0",
      "root": "sha256:...",
      "locator": "materialization/state_roots.jsonl"
    },
    "seam_projection_rows": {
      "schema": "lawvm.provision_state.v1",
      "root": "sha256:...",
      "locator": "projections/seam_rows.jsonl"
    },
    "dump_projection_rows": null,
    "transition_graph_projection_rows": null,
    "residual_ledger": {
      "schema": "lawvm.residual_ledger.v0",
      "root": "sha256:...",
      "locator": "residue/residuals.jsonl"
    },
    "finding_ledger": {
      "schema": "lawvm.finding_ledger.v0",
      "root": "sha256:...",
      "locator": "residue/findings.jsonl"
    },
    "source_unit_coverage": {
      "schema": "lawvm.source_unit_coverage.v0",
      "root": "sha256:...",
      "locator": "coverage/source_unit_coverage.jsonl"
    },
    "potential_operation_coverage": {
      "schema": "lawvm.potential_operation_coverage.v0",
      "root": "sha256:...",
      "locator": "coverage/potential_operation_coverage.jsonl"
    }
  }
}
```

A `null` projection family MUST also be `null` inside the `projection_root`
preimage (§3.1.1), and the corresponding `projection_coverage` entry (§5.5)
MUST be absent or zero-universe. Manifest `root` values MUST equal the
corresponding `roots` members where both exist.

### 4.1 Coverage artifacts

`coverage/` declares which source units were enumerated and which
operation-shaped cues were considered. These artifacts are COMMITTED — they
are rooted under `coverage_root`, which is inside `certificate_root` — so a
bundle cannot carry decorative coverage files that hash-wise float free of
the certificate.

Honest boundary (also §7.1):

```text
Checker v0 verifies declared coverage integrity: the coverage rows hash to
coverage_root, and their source anchors resolve into the source bundle.
It does NOT independently enumerate source units or operation cues from raw
legal prose. Independent source-unit enumeration is checker v1+.
```

A frontend that never emitted a candidate for a missed amendment is therefore
still not caught by checker v0 — but it can no longer be hidden behind
coverage files that claim otherwise, because the claimed coverage is itself
committed and checkable for integrity.

## 5. Residue honesty

Every certificate MUST classify incompleteness at the root and per affected
row. A coverage ratio or confidence score is NOT a substitute: a partially
replayed statute must never read as a clean text-state certificate.

Terminology: "residue honesty" is the design principle; schema field names
uniformly use `residual` (`residual_summary`, `residual_root`, residual
rows).

### 5.1 Root summary

```json
{
  "certificate_status": "clean | qualified | blocked",
  "residual_summary": {
    "blocking_count": 0,
    "qualified_count": 3,
    "observation_count": 18,
    "frontier_count": 0,
    "by_kind": { "expiry_unverified": 0, "manual_frontier": 0, "quirks_recovery": 2 }
  }
}
```

### 5.2 Root status algebra (normative)

`certificate_status` is fully determined by the scoped rows; an emitter does
not get to choose it:

```text
blocked    iff at least one residual intersecting the certificate (§5.3) has
           profile_effect = blocks for the certificate's profile,
           OR any projection row in declared scope has
           certification_status = blocked,
           OR a materialization or projection required by claim_kind is
           absent (null where the claim needs it),
           OR any intersecting residual carries an unregistered or
           "unclassified" diagnostic_code (§5.4).

qualified  iff not blocked, AND at least one residual intersecting the
           certificate has profile_effect = qualifies AND affects an asserted
           projection or materialization row (equivalently: at least one
           in-scope projection row has certification_status = qualified).

clean      iff neither: no intersecting residual blocks or qualifies any
           asserted row. Observation-role rows that do NOT affect asserted
           state (audit trails, suppressed-candidate observations,
           informational findings) MAY coexist with clean — that is explicit,
           not a loophole: they intersect nothing the certificate asserts.
```

A nonzero `qualified_count` is therefore incompatible with
`certificate_status=clean` whenever any of those qualifying rows touches an
asserted row; the checker enforces the algebra, not the summary's
self-description.

### 5.3 Scope intersection (normative)

A residual **intersects** the certificate iff ALL of:

```text
1. statute overlap   — the residual's statute is the certificate's
                       statute_id (or the residual is scoped corpus-wide);
2. address overlap   — the residual's address scope overlaps the declared
                       scope (a whole_statute certificate overlaps every
                       address in the statute; an address_prefix certificate
                       overlaps descendants, the prefix itself, and its
                       ancestors' statute-level facts);
3. temporal overlap  — the residual's date_range overlaps time_scope:
                       closed_interval = [from, to]; open_ended = [from, ∞);
                       change_dates_only = the committed change-date set
                       within [from, to]. A null end is unbounded.
4. profile effect    — residual.profile_effect for the certificate's
                       profile_id is "blocks" or "qualifies" (a "permits"
                       or absent effect leaves the row observation-only).
```

A blocking residual dated outside `time_scope` does not block — but then the
certificate asserts NOTHING about the dates it covers, and a consumer
re-using the dossier beyond `time_scope` is outside the claim (§1). Emitters
MUST NOT shrink `time_scope` after the fact to dodge a residual while still
presenting the certificate as covering the wider interval; `time_scope` is
part of certificate identity and committed under `certificate_root`.

### 5.4 Residual rows

```json
{
  "residual_id": "sha256:...",
  "kind": "expiry_unverified | failed_operation | manual_frontier | source_pathology | grounding_unclassified | quirks_recovery | unsupported_scoped_expiry | source_anchor_unavailable",
  "diagnostic_code": "TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE",
  "role": "observation | obligation | violation",
  "blocking": true,
  "scope": { "address": "section:7", "date_range": ["2027-01-01", null] },
  "source_text": "...",
  "rule_id": "...",
  "source_refs": ["sha256:..."],
  "finding_refs": ["sha256:..."],
  "profile_effect": { "fi.strict.current": "blocks", "fi.quirks.current": "qualifies" }
}
```

Typed diagnostic codes are REQUIRED, not decorative:

- Every residual row MUST carry a `diagnostic_code` registered in the
  observation registry. A catchall (`unclassified`, unregistered, or empty)
  is permitted ONLY in a `blocked` certificate — it forces
  `certificate_status=blocked` (§5.2) and is INVALID inside a clean or
  qualified one.
- `kind=expiry_unverified` rows MUST carry one of the registered fixed-term
  diagnostic codes —

  ```text
  TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE
  TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS
  TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS
  ```

  — plus the offending `source_text` (self-evidencing: typing a residual
  must never require re-running extraction) and the grammar-family
  `rule_id`. Collapsing distinct failure classes (event-bound clauses,
  duration forms, source-impossible dates, ambiguous anaphora) into one
  untyped `expiry_unverified` bucket loses exactly the honesty the
  fail-loud expiry design bought.
- `kind=unsupported_scoped_expiry` rows carry
  `TEMPORAL.SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED`;
  `kind=source_anchor_unavailable` rows carry the transition identity they
  excuse (companion spec §7).

### 5.5 Projection rows and certification_status

Projection rows carry **`certification_status`** — never a field named
`status`. The seam payload already has a `status` field with seam legal-state
semantics (`selected`, `expired`, `expiry_unverified`, ...); the
certificate-level field answers a different question (is this row's content
certified?) and giving the two axes one name invites consumer bugs.

```text
certification_status values:

confirmed       cleanly certified state for this row (live text-state OR a
                confirmed non-live temporal state — see mapping below)
qualified       state emitted with a named, non-blocking recovery/residual
blocked         no asserted state for this row under this profile
not_applicable  row outside the declared scope/universe
unknown         certificate cannot classify; INVALID inside a clean or
                qualified certificate
```

Mapping from seam 0.2 statuses (normative for seam projection rows):

```text
seam.status = selected             → confirmed (or qualified when a
                                     qualifying residual intersects the row)
seam.status = absent               → confirmed (a confirmed no-active-version
                                     state at as_of; same qualified override)
seam.status = expired              → confirmed — a CONFIRMED NON-LIVE
                                     temporal state, proven by the expiry
                                     provenance block; never a live
                                     text-state assertion
seam.status = expiry_unverified    → blocked (fail-loud; never read as
                                     confirmed live OR confirmed expired)
seam.status = address_not_found,
  ambiguous_address, invalid_address,
  ambiguous_missing_scope          → blocked when the row is inside the
                                     declared universe; not_applicable when
                                     the queried row is outside it
seam.status = unsupported_jurisdiction → not_applicable
```

Projection coverage (envelope member `projection_coverage`) declares, per
emitted projection family, what universe of rows the family is REQUIRED to
cover and what was actually emitted, so partial emission can never read as
all-provision clean:

```json
{
  "projection_coverage": {
    "seam": {
      "universe": "all (address, change-date-interval) provision states in scope",
      "row_count": 412,
      "omitted_row_count": 0,
      "blocked_row_count": 5
    }
  }
}
```

For a full-statute certificate with seam projections, either every
(address, date-interval) provision-state row in scope is emitted, or the
family declares `omitted_row_count > 0` — and a certificate with omitted
rows in an asserted family MUST NOT present `certificate_status=clean` as
all-provision clean; the checker treats undeclared omission (universe says N,
rows + omissions < N) as INVALID.

### 5.6 Invariants (checker-enforced, §7 step 12)

- `certificate_status=clean` is FORBIDDEN when any intersecting (§5.3)
  blocking residual exists.
- A `blocked` projection row MUST NOT expose confirmed state.
- `expiry_unverified` MUST NOT appear as confirmed live (nor as confirmed
  expired).
- Every finding with `blocking=true` whose scope intersects the certificate
  scope MUST have a residual row (or a projection row) recording its
  disposition. `finding_root` commits to the normalized compiler
  observation/finding stream for the declared scope; a blocking observation
  that "disappears" between the finding ledger and the residual ledger is a
  checker failure, not an emitter freedom. (A check that exists but does not
  reach the production certificate is not a proof.)
- Residual rows with unregistered/`unclassified` diagnostic codes force
  `blocked` (§5.4).
- `residual_summary` counts MUST equal recomputed counts from the residual
  ledger.

Consumers MUST branch on `certificate_status`, `certification_status`, and
residual role/blocking — never on a confidence field. Confidence MAY exist
as a diagnostic only.

## 6. Versioning: freeze the waist, projections stay in place

`lawvm.provision_state.v1` and `lawvm.dump.v1` remain in place and become
certificate projections by carrying the §3.4 parentage block. They are NOT
deprecated.

Schema family:

```text
lawvm.certificate.v0            lawvm.source_bundle.v0
lawvm.canonical_transition_trace.v0
lawvm.base_tree.v0              lawvm.content_blobs.v0
lawvm.materialization_index.v0  lawvm.residual_ledger.v0
lawvm.finding_ledger.v0
lawvm.source_unit_coverage.v0   lawvm.potential_operation_coverage.v0
lawvm.strict_profile.v0         lawvm.interpretation_policy.v0
lawvm.projection_specs.v0
lawvm.provision_state.v1        lawvm.dump.v1
lawvm.transition_graph.v1       lawvm.checker.v0
```

FROZEN in v0 (changing any of these is a schema bump):

```text
1. certificate envelope field names
2. canonical hash profile and root-construction profile (§3.1, §3.1.1)
3. source artifact identity fields (§3.2)
4. root names and meanings
5. certificate_status enum and the §5.2 status algebra
6. residual role/blocking semantics and the §5.3 intersection rule
7. projection inclusion mechanism, payload/wrapper split, and identity pins
8. statute_id as stable public join key
```

EXPLICITLY UNSTABLE in v0:

```text
internal operation lowering fields; finding detail payloads beyond
kind/diagnostic_code/role/blocking/scope; source-acquisition strategy;
optional viewer fields; textual rendering outside content/state hashes;
Merkle inclusion-proof layout; transition-graph SQLite internals;
multi-jurisdiction normalized address grammar
```

### 6.1 Transition-graph migration note

The transition-graph exporter's notion of "certified" means "internal
exporter invariants passed" and MUST never be ambiguous with an externally
checkable certificate. The fail-safe migration rule for any transition-graph
artifact (SQLite meta or JSONL projection rows):

- During the transition window, an exported graph that is not yet parented by
  a real certificate MUST emit:

  ```json
  {
    "certified": false,
    "exporter_invariants_passed": true,
    "certificate_root": null,
    "certificate_status": "not_certified"
  }
  ```

  A consumer reading legacy `certified` therefore reads `false` and cannot
  mistake exporter self-checks for public proof.
- Alternatively the `certified` field is REMOVED outright in a breaking
  transition-graph schema bump (`transition-graph.v2`), leaving only
  `exporter_invariants_passed` and the certificate parentage block.
- Under NO version may an artifact carry `certified=true` while lacking a
  non-null `certificate_root`.

## 7. Checker v0 contract

Checker v0 verifies **source anchoring + canonical transition replay +
projection inclusion + residue honesty**. It does NOT re-parse legal source
language — and the independence boundary MUST be stated wherever the checker
is described.

Inputs: the bundle (§4) — certificate.json plus every manifest artifact,
including bundled source bytes, base tree, canonical transition trace,
content blobs, materialization index, residual/finding ledgers, projection
rows, coverage rows, and the profile/policy/projection-spec manifests.

Source bytes MUST be bundled (or resolve from a content-addressed local
store to exact bytes). Live URL fetching is an ACQUISITION HELPER only: a
checker MAY offer to populate a bundle's `sources/` from recorded locators
before checking, but no `VALID_*` verdict may depend on bytes that were not
content-verified into the bundle. A check attempted without required bytes
returns `UNCHECKABLE_MISSING_ARTIFACTS`, never a guess.

The checker maintains a registry of supported projection rules keyed by
`(projection_schema, projection_spec_version)` — e.g.
`(lawvm.provision_state.v1, 0.2)` — so old bundles remain checkable under
the rules they were emitted against when a later seam spec changes hashed
fields.

Procedure:

```text
 0. Resolve the artifact manifest. Any required artifact missing, unreadable,
    or undecodable → UNCHECKABLE_MISSING_ARTIFACTS (stop). Any pinned
    (schema, spec_version) outside the checker registry → likewise
    UNCHECKABLE_MISSING_ARTIFACTS: not checkable here is not the same as
    contradicted.
 1. Validate schemas, the canonical hash profile, and the root-construction
    profile.
 2. Recompute raw_source_hash for every source blob; recompute SourceArtifact
    leaves and source_bundle_root.
 3. Verify source anchoring per transition (companion spec §7): every
    CanonicalTransition has source_refs resolving into the source bundle and
    either a source_anchor whose span exists in the source bytes and whose
    quote_hash matches, or a kind=source_anchor_unavailable residual mapped
    to it. Missing anchors are NEVER silent.
 4. Recompute canonical_transition_root from the trace (ordering + duplicate
    rules of §3.1.1).
 5. Validate each transition row against the companion-spec grammar:
    certified-core fields present, sequence strictly increasing,
    effective_date non-decreasing and committed under change_dates_root,
    action in the v0 vocabulary.
 6. Verify base_tree_root; load the base covering state; verify every base
    unit's content hash resolves in content_blobs.
 7. From the base tree, apply certified transitions in sequence per the
    companion-spec action semantics. Per transition: verify the
    precondition and pre_hash before applying; apply the patch operation;
    verify post_hash after. Verify payload blobs by decoding and recomputing
    the structural subtree hash.
 8. Recompute the covering-state hash at every change date and compare to
    the materialization index; recompute materialization_root and
    content_blobs_root.
 9. Recompute projection payload hashes, per-family projection subroots, and
    projection_root (explicit nulls included); verify each projected
    artifact's parentage block references this certificate and a correct
    inclusion path.
10. Recompute residual_root and finding_root.
11. Verify coverage row integrity and coverage_root (§4.1 boundary: declared
    coverage only).
12. Check residue honesty: the §5.3 intersection, the §5.2 status algebra,
    the §5.6 invariants (including blocking-finding → residual mapping and
    projection-coverage consistency).
13. Recompute certificate_root over the envelope minus certificate_id;
    verify certificate_id = "sha256:" + certificate_root.
```

Verdicts:

```text
VALID_CLEAN                    all checks pass, certificate_status=clean
VALID_QUALIFIED                all checks pass, certificate_status=qualified
VALID_BLOCKED                  all checks pass, certificate_status=blocked
INVALID                        hashes, trace replay, source anchoring,
                               projection inclusion, or residue honesty fail
UNCHECKABLE_MISSING_ARTIFACTS  required artifacts unavailable or pins
                               unsupported; the bundle was NOT contradicted
                               and was NOT verified
```

A VALID_QUALIFIED or VALID_BLOCKED certificate is a VALID certificate that
does not assert a clean text-state. `UNCHECKABLE_MISSING_ARTIFACTS` is not a
verification: tooling and presentation layers MUST NOT present an
uncheckable bundle as checked or checkable-as-is. A missing local file is
not a cryptographic contradiction; conversely a completed check that fails
is INVALID, never "uncheckable".

### 7.1 Independence boundary (public README material)

Checker v0 CAN catch: tampered or wrong-hash source blobs; broken source
spans/quote hashes; a trace that does not produce the claimed state; pre/post
hash mismatches; seam/dump projection drift; materialization root mismatches;
residue contradictions; a clean certificate with blocking residue; blocking
findings that never became residuals; coverage rows that drifted from their
committed root; viewer/artifact inconsistency with the certificate.

Checker v0 CANNOT catch: a frontend that missed an amendment entirely; legal
amendment language misread into a wrong-but-internally-consistent canonical
transition; omitted source documents beyond what declared coverage commits
to; errors in the official source itself; legal/normative interpretation;
manual claim truth beyond the declared evidence policy.

Source-anchor verification is byte-level only: the checker verifies that the
anchored span exists in the source bytes and the quote hash matches. It does
NOT verify that the span legally entails the canonical operation — that
would require semantic source parsing, which is out of v0's boundary.
Likewise coverage verification is declared-coverage integrity only;
independent source-unit enumeration from raw bytes is checker v1+ (§4.1).

Guard-liveness lesson applies: the worst failure class is a check that exists
but is not live in the production lane. The checker MUST be exercised against
deliberately corrupted bundles (fire-drill style) so each verification step
is demonstrably reachable.

## 8. Do-not-build (v0)

```text
 1. PKI/signatures                      hash-rooted bundles first
 2. Blockchain/transparency logs        publication audit, not semantics
 3. Multi-jurisdiction address grammar  freeze per-jurisdiction now
 4. Independent raw-source parser       that is checker v1+, not v0 —
                                        including independent source-unit
                                        enumeration for coverage
 5. LLM/manual-claim adjudication       checker validates, never adjudicates
 6. Legal interpretation claims         text-state only
 7. Full sources inside the envelope    bundle by hash/reference
 8. Viewer fields in the core schema    the viewer is a projection
 9. Public "official error" labels      review candidates, not conclusions
10. Cross-jurisdiction unification      Finland-first, extensible
```

## 9. Public claim discipline

Promise ONLY: the certificate commits to a declared source bundle, canonical
transition trace, materialization roots, projection roots, residual ledger,
and declared coverage; checker v0 validates their mutual consistency and
byte-level source anchoring; incompleteness is explicit and blocks clean
assertions.

NEVER promise: "the certificate proves the law is correct"; "the checker
independently parses amendment language"; "a clean hash means no operation
was omitted"; "declared coverage means complete coverage"; "no official
consolidation can disagree"; "manual/LLM claims are trusted because they are
in the certificate"; "hash stability across semantic engine changes"; "an
uncheckable bundle is a checked bundle".

## 10. Build sequence

```text
1. This spec + the companion transition-trace spec
   (CANONICAL_TRANSITION_TRACE_V0.md) — keep both ahead of the emitters.
2. certificate_root/projection parentage fields into seam + dump outputs
   (§3.4), even before the checker exists.
3. Bundle writer for ONE Finnish statute/slice.
4. Checker v0 per §7, with corrupted-bundle fire-drills.
```

## 11. Change policy

This spec is versioned by `spec_version`. A change is breaking — and MUST
bump `spec_version` — iff it changes the frozen waist of §6.

### 11.1 Changes 0.1 → 0.2

No emitter or checker has shipped against 0.1; these are pre-implementation
contract repairs, applied before the first bundle writer so the public
contract freezes tight rather than loose.

- `certificate_root` is now fully defined: domain-tagged hash over the ENTIRE
  envelope minus `certificate_id` (§3.3); `certificate_id` is derived from
  it. No envelope field is outside the commitment.
- Canonical root construction (LeafHash/ListRoot/SetRoot, domain tags,
  per-artifact ordering, empty roots, duplicate rules) is specified (§3.1.1);
  roots are now portable across implementations.
- Coverage artifacts are committed: `coverage_root` added to `roots`,
  coverage artifact refs added to the manifest, with the declared-coverage
  boundary stated (§4.1).
- Base tree is a first-class artifact: `materialization/base_tree.json`,
  `base_tree_root` in `roots`; `content_blobs_root` likewise split out.
- Profile/interpretation-policy/projection-spec manifests are bundle
  artifacts under `policy/`; `profile_hash`/`policy_hash` are defined as
  hashes of those manifests (§3.5).
- Projection rule split into `projection_payload` vs `projection_wrapper`;
  parentage is never hashed into projection hashes or roots (no hash cycles);
  seam `derived_state_hash` is explicitly unchanged by parentage. Projection
  identity pins `projection_schema` + `projection_spec_version` +
  `projection_spec_hash`; the checker registry is keyed by
  (schema, spec_version) (§3.4, §7).
- Projection rows carry `certification_status` (never `status`); the
  seam-status → certification_status mapping is normative, including
  `expired` = confirmed non-live and `expiry_unverified` = blocked (§5.5).
- `assertion_status` renamed `certificate_status`; the clean/qualified/
  blocked algebra is fully defined (§5.2). `residue_summary` renamed
  `residual_summary`; field naming unified on `residual` while "residue
  honesty" remains the prose principle.
- `time_scope` added to certificate identity; the residual scope-intersection
  rule (statute/address/temporal/profile-effect) is normative (§1, §5.3).
- Residual rows require registered `diagnostic_code`s; `expiry_unverified`
  residuals carry the typed fixed-term codes plus self-evidencing
  `source_text` and `rule_id`; catchall codes force `blocked` (§5.4).
- Blocking-finding → residual mapping is a checker-enforced invariant (§5.6).
- `change_dates_root` is defined over ALL timeline boundary dates, including
  fixed-term `expires_on` (§2.1).
- SourceArtifact leaf is identity metadata + `raw_source_hash`, not raw bytes
  alone (§3.2).
- Artifact manifest is exhaustive and normative with explicit `null` for
  absent projection families; `projection_coverage` declares per-family row
  universes so partial emission cannot read as all-provision clean (§4,
  §5.5).
- Transition semantics moved to the normative companion spec
  ([CANONICAL_TRANSITION_TRACE_V0.md](CANONICAL_TRANSITION_TRACE_V0.md)):
  base-tree representation, content blobs, transition grammar and action
  semantics, and materialization state-root/checkpoint semantics. The
  certificate spec consumes those roots; the companion defines them.
- Verdict `UNCHECKABLE_MISSING_ARTIFACTS` added; URL fetching demoted to an
  acquisition helper; `VALID_*` requires bundled bytes (§7).
- Transition-graph `certified` migration made fail-safe (§6.1).
