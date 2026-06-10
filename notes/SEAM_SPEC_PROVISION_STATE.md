---
title: LawVM Provision-State Seam — Consumer Contract
spec_version: 0.1
status: draft
schema: lawvm.provision_state.v1
---

# LawVM Provision-State Seam Contract

A versioned contract for the LawVM provision-state query surface ("the seam").
Downstream consumers (notably the MeVM proof system) cite this document by
`spec_version`. Normative keywords MUST/SHOULD/MAY follow RFC 2119.

## 1. Scope

The seam answers a single point-in-time question: **what is the state of one
provision of one statute as of one date?** A query is the tuple:

- `statute_id` — jurisdiction-local statute identifier (e.g. `273/2009`).
- `jurisdiction` — currently `fi` only; other values return
  `status=unsupported_jurisdiction`.
- `provision` — a `LegalAddress` string, slash-joined `kind:label` segments
  (e.g. `section:6a`, `chapter:4a/section:30a`).
- `as_of` — ISO date (`YYYY-MM-DD`); MUST be non-empty.
- `query_type` — `governing` (default) or `in_force` (see §5).
- `territory` — optional applicability scope.

This seam IS a deterministic state lookup over the consolidated timeline. It is
NOT:

- an oracle-text/replay overlap benchmark (a separate diagnostic surface);
- a certificate checker — the `selection.certificate` field is an *explanation*
  of one selection decision, not a stand-alone verifiable proof object;
- a guarantee of legal validity beyond what the modeled timeline encodes
  (see §6, KNOWN LIMITATIONS).

## 2. Response envelope

Every response is a JSON object carrying `schema`, `jurisdiction`,
`statute_id`, `status`, and the echoed `query`. The `status` field is the
primary control signal.

### 2.1 Top-level statuses

| status | meaning |
|---|---|
| `selected` | A version was resolved AND selected at `as_of`. Full `version`, `text`, `hashes`, `resolved_address` present. |
| `absent` | Address resolved, but no version is active at `as_of`. |
| `ambiguous_missing_scope` | Address resolved, active candidates differ along a scope dimension (e.g. `territory`) not supplied in the query. `selection.required_dimensions` lists what is missing. |
| `address_not_found` | No exact and no unique-suffix address match. **Safe failure.** |
| `ambiguous_address` | Address matched ≥2 timelines by suffix; `address_candidates` lists them. Never a silent pick. |
| `invalid_address` | The provision string did not parse into any `kind:label` segment. |
| `unsupported_jurisdiction` | `jurisdiction` not in supported set (`["fi"]`). |

Consumers MUST treat any status other than `selected` as "no asserted
text-state". In particular `address_not_found`, `ambiguous_address`, and
`invalid_address` are *fail-loud* outcomes: the seam resolves an address
exactly or by a UNIQUE suffix, never by arbitrary order. A near-miss address
MUST be expected to fail rather than resolve to a different provision.

### 2.2 `version` payload (present when `status=selected`)

| field | semantics |
|---|---|
| `effective` | Date this version takes effect; `effective <= as_of` always holds for a selected version. `0000-00-00` denotes the original-enactment base version (§5). |
| `enacted` | Enactment date; gates `query_type=in_force` (§5). May be empty. |
| `expires` | Exclusive expiry bound; empty means no modeled expiry. Inactive ON the expiry date (§5). |
| `variant_kind` | `temporary` (overlay rail) or `permanent` (background rail). Overlay wins when both are active. |
| `content_state` | `live` (content present) or `tombstone` (content is `None`, i.e. repealed-to-placeholder). |
| `applicability` | List of `{dimension, includes:[...]}` predicates (e.g. territory scoping). |

### 2.3 Other fields

- `resolved_address` — `{path:[{kind,label}], special, text}` for the resolved
  timeline. Null on non-resolution. `address_match.mode` is `exact` or
  `unique_suffix`.
- `lineage` — `{status, address_chain, migration_event_count_considered}`.
  `status` ∈ `self_only | migration_chain | unresolved_address`. The
  `address_chain` traces renumbering/migration history considered at `as_of`.
- `selection` — `{status, required_dimensions, certificate}` explaining the
  version-selection decision (rail, candidate count, selected dates).
- `text` — `{rendered, available}`; `available=false` for tombstone/absent.
- `engine`, `source`, `source_locator` — provenance; NOT part of the state
  hash (§3).

## 3. derived_state_hash

`hashes.derived_state_hash` is the consumer's stable commitment to a
text-state. It is `sha256` of the canonical JSON encoding of EXACTLY these
fields, in this nesting:

- `schema`
- `status`
- `jurisdiction`
- `statute_id`
- `query` — the full echoed query object: `statute_id`, `provision` (the RAW
  requested provision string), `as_of`, `query_type`, `territory`.
- `resolved_address` — `{path, special, text}`, or null.
- `lineage` — `{status, address_chain, migration_event_count_considered}`.
- `version` — `{effective, enacted, expires, variant_kind, content_state,
  applicability}`, or null.
- `content_hash`

Canonical encoding: `json.dumps(..., ensure_ascii=True, sort_keys=True,
separators=(",", ":"))` then `sha256` of the UTF-8 bytes.

### 3.1 Excluded from the hash

The following are NEVER hashed: `engine` (`producer`, `build_id`,
`git_commit`, `git_dirty`, `repository`), `source`, `source_locator`, `text`,
`selection`, `address_match`, `title`. A change in engine build, git commit, or
working-tree dirtiness MUST NOT, by itself, change `derived_state_hash`.

### 3.2 content_hash

`hashes.content_hash` = `sha256(irnode_to_text(content))` — a **text-only**
flattening of the provision subtree. It is the empty string for absent/tombstone
versions.

### 3.3 Aliasing caveats (normative)

- `content_hash` is **structure-blind**: two structurally different IR states
  that flatten to identical text collide on `content_hash`.
- A FULL `derived_state_hash` collision additionally requires identical version
  temporal metadata (`effective`/`enacted`/`expires`/`variant_kind`/
  `content_state`) and identical `applicability`. This makes a full collision
  narrow — probabilistic, not structural. Consumers MAY rely on full-hash
  equality as text-state identity but MUST NOT rely on `content_hash` alone.

### 3.4 Stability guarantees

GUARANTEED:

- **Order-independent** — `sort_keys=True` removes key-order sensitivity.
- **ASCII-forced** — `ensure_ascii=True` removes encoding-form sensitivity.
- **Run-to-run byte-stable** — identical inputs on the same engine produce the
  identical hash byte-for-byte.

NOT GUARANTEED:

- **Cross-engine stability.** When selection or eligibility *semantics* change,
  the hash MAY change for the same query (precedent: the enacted-date semantics
  change that populated `enacted` for base versions). Consumers MUST pin
  `spec_version` and MUST NOT assume hash stability across engine versions.
- The change detector is the **21-pin regression suite**
  (`tests/test_mevm_grounding_pins.py`): it re-runs the consumer's pinned
  `(address, as_of) → derived_state_hash` corpus against the live seam. Any
  semantic hash change surfaces there before reaching consumers.

## 4. Address resolution

Resolution is exact-match first, then unique-suffix match. A unique suffix
match yields `address_match.mode=unique_suffix`. Two or more suffix matches
yield `ambiguous_address` with sorted `address_candidates`. Zero matches yield
`address_not_found`. There is NO order-dependent or fuzzy fallback.

## 5. in_force / temporal semantics

A version is *eligible* at `as_of` iff ALL of:

1. `effective <= as_of` — inclusive lower bound. A new version governs on its
   effective date.
2. For `query_type=in_force`: `enacted` is empty OR `enacted <= as_of`. For
   `query_type=governing` this gate is skipped. (`governing` may select a
   version whose text is effective but not yet enacted; `in_force` requires the
   enactment to have occurred.)
3. `expires` is empty OR `expires > as_of` — **exclusive** upper bound. A
   version with `expires == as_of` is INACTIVE on that date; expiry takes effect
   ON the stated date.

`effective="0000-00-00"` is the original-enactment base version (untouched
original text), not an error. NOTE: pre-enactment base versions may also carry
`enacted` derived from the base; consumers reconciling fact-packs SHOULD
distinguish the base-version convention from a real enactment date.

Selection picks the temporary (overlay) rail over the permanent (background)
rail when both are eligible; within a rail the latest `(effective, enacted)`
wins, with a substantive-over-placeholder bias.

### 5.1 Statute-id format stability

`statute_id` strings (`NNN/YYYY` jurisdiction-local form, e.g. `273/2009`) are
the de-facto join key between LawVM and downstream corpora. The format is a
**stable public interface**: any change to it is a breaking change under §7
regardless of whether hashed fields change.

## 6. KNOWN LIMITATIONS (consumers MUST apply)

### 6.1 Fixed-term whole-law expiry is NOT modeled

When a statute's validity is bounded only by a fixed-term `voimaantulosäännös`
(commencement/validity clause) in prose — and that bound is never lifted to a
machine-readable `expires` on the provision versions — the seam will return
`status=selected`, `content_state=live`, empty `expires`, and full text PAST the
prose term (confirmed class: `482/2024`, valid to `31.12.2026` per its final
section, extended by a later act; queried at `2027-01-01` it returns live text
byte-identical to mid-validity).

Consequence and MANDATORY mitigation: **a `selected`/`CONFIRMED` result on a
fixed-term law past its term is NOT evidence of in-force.** Consumers MUST treat
any statute whose final section is a fixed-term `voimaantulosäännös` as
requiring a manual validity check for any `as_of` past the term.

Status of the fix: statute-level validity bounds are implemented behind the
`LAWVM_ENABLE_FIXED_TERM_STATUTE_BOUNDS` flag (default OFF; this spec describes
flag-OFF behavior). With the flag on, a lapsed whole-law bound returns
`status="expired"` (version null, top-level `expires`/`valid_until`, an
`expiry` provenance block), and a recognised-but-unparseable or ambiguous bound
returns the blocking `status="expiry_unverified"` — never a confirmed-live
answer. When the flag becomes default-on after the corpus soak, this spec bumps
to 0.2 with those statuses added to §2.1 and the expiry fields added to §3.

### 6.2 content_hash text-only aliasing

Per §3.3, `content_hash` does not distinguish structurally distinct states with
identical flattened text. Consumers that key decisions on text-state identity
MUST use the full `derived_state_hash`, never `content_hash` alone.

## 7. Change policy

This spec is versioned by `spec_version`. A change is **breaking** — and MUST
bump `spec_version` — iff it changes any of:

- the set of fields fed into `derived_state_hash` (§3), their nesting, or the
  canonical encoding;
- the `content_hash` definition;
- the `status` enumeration (§2.1) or its meanings;
- the eligibility predicate (§5) or version-selection rail order;
- the resolution rule (§4).

Non-breaking clarifications (added prose, new excluded provenance fields) MAY
ship under the same `spec_version`. Breaking changes are announced via a
`spec_version` bump AND will manifest as divergence in the 21-pin regression
suite (`tests/test_mevm_grounding_pins.py`), which consumers SHOULD also run in
their own CI. Consumers MUST pin the `spec_version` they validated against.

<!--
CODE-VS-NOTES DISAGREEMENTS (code wins, flagged per task instructions):

1. lineage field name. The contract-reply note (LAWVM_MEVM_CONTRACT_REPLY,
   line 42) lists the hashed lineage subfields as "status/address_chain/
   migration_event_count". The code (provision_state.py:308,320) names the
   field "migration_event_count_considered", not "migration_event_count". Spec
   uses the code name. Minor; same datum.

2. Hash source-line citation. The note (line 39) cites
   "provision_state.py:352-382". In the merge-train worktree the hashed-input
   dict is constructed at provision_state.py:363-373 (within _hash_payload
   spanning 352-382). The field SET matches the note exactly once "query" is
   expanded to statute_id/provision/as_of/query_type/territory. No semantic
   disagreement.

3. expires comparison direction. The note (MEVM consumer contract, §3) only
   asks the question; the task brief asserts "expires > expiry_horizon" and
   "inactive ON expiry date". Verified in code: timeline_selection.py:155 is
   `(not v.expires or v.expires > expiry_horizon)`, where expiry_horizon
   defaults to as_of (line 151). So a version with expires == as_of fails the
   predicate and is inactive on the expiry date. Confirmed; expiry is exclusive.

UNVERIFIED CLAIMS COPIED FROM NOTES (not checkable from the two code files read):

a. The fixed-term-expiry example specifics (statute 482/2024 valid to
   31.12.2026, extended by a later act; query at 2027-01-01 returns live
   byte-identical text). Copied from LAWVM_MEVM_CONTRACT_REPLY lines 22-31 and
   the MeVM consumer contract §3; not reproduced against the corpus here
   (read-only, no replays per task constraints). The DEFECT MECHANISM (no
   machine-readable expires bound -> eligible() expires gate never trips) is
   consistent with timeline_selection.py:155 and provision_state.py:332.

b. The "21 pins" count and that tests/test_mevm_grounding_pins.py is the live
   change detector. Copied from LAWVM_MEVM_CONTRACT_REPLY lines 64-69; the test
   file itself was not read.

c. The enacted-date semantics-change precedent (commit b09e0003 populating
   enacted for base versions, causing cross-build hash divergence for pins
   minted earlier). Copied from LAWVM_MEVM_CONTRACT_REPLY lines 51-64; presented
   here only as a precedent illustrating the cross-engine NON-guarantee, not as
   a current behavior claim.
-->
