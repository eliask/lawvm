---
title: LawVM Certificate Schema v0 — Temporal Dossier and Checker Contract
schema: lawvm.certificate.v0
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
[SEAM_SPEC_PROVISION_STATE.md](SEAM_SPEC_PROVISION_STATE.md) (the seam, now a
certificate projection), [LAWVM_PROOF_SURFACES.md](LAWVM_PROOF_SURFACES.md),
[COMPILER_OBSERVATION_STREAM.md](COMPILER_OBSERVATION_STREAM.md),
[REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md).

## 1. Claim model: the per-statute temporal dossier

A v0 certificate asserts a **declared statute/slice timeline** — not one
provision query, and not one transition:

```text
For jurisdiction J, statute S, source bundle B, profile P, interpretation
policy I, LawVM derives a temporal text-state timeline T for a declared
address scope, with source artifacts D, replay/transition trace R,
materialization roots M, projection roots Q, and typed residual ledger Z.
```

The certificate identity is the tuple:

```json
{
  "jurisdiction": "fi",
  "statute_id": "301/2004",
  "scope": { "kind": "whole_statute | address_prefix | address_set", "addresses": [] },
  "profile_id": "fi.strict.current",
  "interpretation_policy_id": "lawvm.fi.default.v1",
  "source_bundle_id": "sha256:..."
}
```

Leaf units (the five kinds every artifact in the bundle reduces to):

```text
1. SourceArtifact leaf      — exact bytes observed at a named locator
2. CanonicalTransition leaf — one typed replay step with pre/post hashes
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
  "profile": { "profile_id": "fi.strict.current", "profile_hash": "sha256:..." },
  "interpretation_policy": { "policy_id": "lawvm.fi.default.v1", "policy_hash": "sha256:..." },
  "time_axis": {
    "change_dates_root": "sha256:...",
    "min_date": "2004-05-01",
    "max_date": "2026-06-10"
  },
  "roots": {
    "source_bundle_root": "sha256:...",
    "canonical_transition_root": "sha256:...",
    "materialization_root": "sha256:...",
    "projection_root": "sha256:...",
    "residual_root": "sha256:...",
    "finding_root": "sha256:..."
  },
  "assertion_status": "clean | qualified | blocked",
  "residue_summary": { },
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
and MUST NOT be presented as checkable.

## 3. Hash discipline

One top root, named Merkle subroots, existing hashes preserved as leaves or
projection hashes:

```text
certificate_root
├── source_bundle_root
├── canonical_transition_root
├── materialization_root
├── projection_root
│   ├── seam_projection_root
│   ├── dump_projection_root
│   └── transition_graph_projection_root
├── residual_root
└── finding_root
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

### 3.2 Canonical leaf hashes

```text
content_hash            sha256(normalized text of provision subtree)
                        (exists today; remains text-only and structure-blind)
structure_hash          sha256(canonical JSON of IR/provision subtree structure)
                        (NEW: content_hash intentionally aliases structural
                        variants; the certificate must be able to commit to
                        structure)
provision_state_hash    sha256(canonical JSON of the provision-state leaf)
seam_derived_state_hash existing seam hash, preserved as a projection hash
transition_hash         sha256(canonical JSON of canonical transition leaf)
source_artifact_hash    sha256(raw source bytes)
```

### 3.3 What certificate_root commits to

"Signs" means hashes over; there is NO PKI in v0:

```text
schema, jurisdiction, statute_id, scope,
profile hash, interpretation policy hash,
source_bundle_root, canonical_transition_root, materialization_root,
projection_root, residual_root, finding_root,
checker contract version
```

### 3.4 Projection rule

Every seam/dump/viewer/packet artifact MUST carry its parentage:

```json
{
  "certificate_id": "sha256:...",
  "certificate_root": "sha256:...",
  "projection_kind": "lawvm.provision_state.v1",
  "projection_hash": "sha256:...",
  "inclusion_path": ["..."]
}
```

In v0, `inclusion_path` MAY be a simple explicit path inside the bundle
rather than an optimized Merkle proof. The contract — every projection is
traceable to exactly one certificate root — is what is frozen, not the proof
encoding.

## 4. Bundle layout

```text
certificate.json
sources/
  <source_hash>.bin
trace/
  canonical_transitions.jsonl
  canonical_transitions.root
materialization/
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

Envelope references into the bundle:

```json
{
  "artifacts": {
    "canonical_transition_trace": {
      "schema": "lawvm.canonical_transition_trace.v0",
      "root": "sha256:...",
      "locator": "trace/canonical_transitions.jsonl"
    },
    "finding_ledger": {
      "schema": "lawvm.finding_ledger.v0",
      "root": "sha256:...",
      "locator": "residue/findings.jsonl"
    },
    "source_bundle": {
      "schema": "lawvm.source_bundle.v0",
      "root": "sha256:...",
      "locator": "sources/"
    }
  }
}
```

## 5. Residue honesty

Every certificate MUST classify incompleteness at the root and per affected
row. A coverage ratio or confidence score is NOT a substitute: a partially
replayed statute must never read as a clean text-state certificate.

Root:

```json
{
  "assertion_status": "clean | qualified | blocked",
  "residue_summary": {
    "blocking_count": 0,
    "qualified_count": 3,
    "observation_count": 18,
    "frontier_count": 0,
    "by_kind": { "expiry_unverified": 0, "manual_frontier": 0, "quirks_recovery_used": 2 }
  }
}
```

Residual rows:

```json
{
  "residual_id": "sha256:...",
  "kind": "expiry_unverified | failed_operation | manual_frontier | source_pathology | grounding_unclassified | quirks_recovery | unsupported_scoped_expiry",
  "role": "observation | obligation | violation",
  "blocking": true,
  "scope": { "address": "section:7", "date_range": ["2027-01-01", null] },
  "source_refs": ["sha256:..."],
  "finding_refs": ["sha256:..."],
  "profile_effect": { "fi.strict.current": "blocks", "fi.quirks.current": "qualifies" }
}
```

Projection row statuses:

```text
confirmed       cleanly certified text-state for this row
qualified       text-state emitted with named non-blocking recovery/residue
blocked         no asserted text-state for this row under this profile
not_applicable  row outside scope
unknown         certificate cannot classify; INVALID inside a clean certificate
```

INVARIANTS (checker-enforced, §7 step 12):

- `assertion_status=clean` is FORBIDDEN when any scoped blocking residue
  exists within the declared scope.
- A `blocked` projection row MUST NOT expose confirmed text-state.
- `expiry_unverified` MUST NOT appear as confirmed live.

Consumers MUST branch on `assertion_status`, projection row status, and
residual role/blocking — never on a confidence field. Confidence MAY exist as
a diagnostic only.

## 6. Versioning: freeze the waist, projections stay in place

`lawvm.provision_state.v1` and `lawvm.dump.v1` remain in place and become
certificate projections by carrying the §3.4 reference block. They are NOT
deprecated.

Schema family:

```text
lawvm.certificate.v0            lawvm.source_bundle.v0
lawvm.canonical_transition_trace.v0
lawvm.materialization_index.v0  lawvm.residual_ledger.v0
lawvm.provision_state.v1        lawvm.dump.v1
lawvm.transition_graph.v1       lawvm.checker.v0
```

FROZEN in v0 (changing any of these is a schema bump):

```text
1. certificate envelope field names
2. canonical hash profile
3. source artifact identity fields
4. root names and meanings
5. assertion_status enum
6. residual role/blocking semantics
7. projection inclusion mechanism
8. statute_id as stable public join key
```

EXPLICITLY UNSTABLE in v0:

```text
internal operation lowering fields; finding detail payloads beyond
kind/role/blocking/scope; source-acquisition strategy; optional viewer
fields; textual rendering outside content/state hashes; Merkle
inclusion-proof layout; transition-graph SQLite internals;
multi-jurisdiction normalized address grammar
```

Migration note: the transition-graph exporter's current `certified` flag
means "internal exporter invariants passed" and MUST be renamed/clarified
(`exporter_invariants_passed`) so that "certified" is never ambiguous between
internal checks and an externally checkable certificate.

## 7. Checker v0 contract

Checker v0 verifies **source anchoring + canonical transition replay +
projection inclusion + residue honesty**. It does NOT re-parse legal source
language — and the independence boundary MUST be stated wherever the checker
is described.

Inputs: certificate.json; source artifacts (or resolvable URLs + expected
hashes); base tree; canonical transition trace; content/payload blobs;
residual/finding ledger; projection rows or roots; profile/policy manifests.

Procedure:

```text
 1. Validate schemas and the canonical hash profile.
 2. Recompute source_artifact_hash for every source blob.
 3. Verify source locators/spans/quote hashes where present.
 4. Recompute source_bundle_root.
 5. Recompute canonical_transition_root from the trace.
 6. Validate each transition carries sequence, effective date, action,
    target address/node id, source refs, pre/post/payload hashes where
    applicable.
 7. From the base tree, apply certified transitions in sequence.
 8. Per transition: verify pre_hash before applying; apply the certified
    tree operation; verify post_hash after.
 9. Recompute materialization roots/checkpoints.
10. Recompute seam/dump/transition projection hashes.
11. Recompute residual/finding roots.
12. Check residue honesty (the §5 invariants).
13. Recompute certificate_root.
```

Verdicts:

```text
VALID_CLEAN      all checks pass, assertion_status=clean
VALID_QUALIFIED  all checks pass, assertion_status=qualified
VALID_BLOCKED    all checks pass, assertion_status=blocked
INVALID          hashes, trace replay, source anchoring, projection
                 inclusion, or residue honesty fail
```

A VALID_QUALIFIED or VALID_BLOCKED certificate is a VALID certificate that
does not assert a clean text-state.

### 7.1 Independence boundary (public README material)

Checker v0 CAN catch: tampered or wrong-hash source blobs; broken source
spans/quote hashes; a trace that does not produce the claimed state; pre/post
hash mismatches; seam/dump projection drift; materialization root mismatches;
residue contradictions; a clean certificate with blocking residue;
viewer/artifact inconsistency with the certificate.

Checker v0 CANNOT catch: a frontend that missed an amendment entirely; legal
amendment language misread into a wrong-but-internally-consistent canonical
transition; omitted source documents (unless source coverage is declared);
errors in the official source itself; legal/normative interpretation; manual
claim truth beyond the declared evidence policy.

Guard-liveness lesson applies: the worst failure class is a check that exists
but is not live in the production lane. The checker MUST be exercised against
deliberately corrupted bundles (fire-drill style) so each verification step
is demonstrably reachable.

## 8. Do-not-build (v0)

```text
 1. PKI/signatures                      hash-rooted bundles first
 2. Blockchain/transparency logs        publication audit, not semantics
 3. Multi-jurisdiction address grammar  freeze per-jurisdiction now
 4. Independent raw-source parser       that is checker v3, not v0
 5. LLM/manual-claim adjudication       checker validates, never adjudicates
 6. Legal interpretation claims         text-state only
 7. Full sources inside the envelope    bundle by hash/reference
 8. Viewer fields in the core schema    the viewer is a projection
 9. Public "official error" labels      review candidates, not conclusions
10. Cross-jurisdiction unification      Finland-first, extensible
```

## 9. Public claim discipline

Promise ONLY: the certificate commits to a declared source bundle, canonical
transition trace, materialization roots, projection roots, and residual
ledger; checker v0 validates their mutual consistency and source anchoring;
incompleteness is explicit and blocks clean assertions.

NEVER promise: "the certificate proves the law is correct"; "the checker
independently parses amendment language"; "a clean hash means no operation
was omitted"; "no official consolidation can disagree"; "manual/LLM claims
are trusted because they are in the certificate"; "hash stability across
semantic engine changes".

## 10. Build sequence

```text
1. This spec (done — keep it ahead of the emitters).
2. certificate_root/projection reference fields into seam + dump outputs
   (§3.4), even before the checker exists.
3. Bundle writer for ONE Finnish statute/slice.
4. Checker v0 per §7, with corrupted-bundle fire-drills.
```
