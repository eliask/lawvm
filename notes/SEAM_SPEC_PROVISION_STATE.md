> **Status (2026-06-22):** Current. Kind: Normative (versioned consumer contract, spec_version 0.3). All cited modules/tests verified: provision_state.py (`lawvm.provision_state.v1`), consumer-contract + fixed-term-expiry test suites, timeline_integrity tool, core/timeline_selection.py. Embedded code-vs-notes block is accurate.

---
title: LawVM Provision-State Seam — Consumer Contract
spec_version: 0.3
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
- `provision` — a canonical `LegalAddress` string, slash-joined
  `kind:label` segments (e.g. `section:6a`,
  `chapter:4a/section:30a`). The string MUST NOT contain whitespace around
  segment separators, kind names, or labels; non-canonical selector
  whitespace is `invalid_address` with a canonical suggestion.
- `as_of` — exact ISO date string (`YYYY-MM-DD`); MUST be non-empty and
  MUST NOT contain leading or trailing whitespace.
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

Every response is a JSON object carrying `schema`, `spec_version`,
`jurisdiction`, `statute_id`, `status`, and the echoed `query`. The
`spec_version` field is the seam contract version (`"0.3"` for this document).
The `status` field is the primary control signal.

### 2.1 Top-level statuses

| status | meaning |
|---|---|
| `selected` | A version was resolved AND selected at `as_of`. Full `version`, `text`, `hashes`, `resolved_address` present. |
| `absent` | Address resolved, but no version is active at `as_of`. |
| `ambiguous_missing_scope` | Address resolved, active candidates differ along a scope dimension (e.g. `territory`) not supplied in the query. `selection.required_dimensions` lists what is missing. |
| `address_not_found` | No exact and no unique-suffix address match. **Safe failure.** |
| `ambiguous_address` | Address matched ≥2 timelines by suffix; `address_candidates` lists them. Never a silent pick. |
| `invalid_address` | The provision string did not parse into any `kind:label` segment. |
| `invalid_query` | The PIT query itself is malformed, e.g. `as_of` is missing/not a real `YYYY-MM-DD` date, or `query_type` is not recognised. **Safe failure.** No replay is run. |
| `unsupported_jurisdiction` | `jurisdiction` not in supported set (`["fi"]`). |
| `expired` | (since 0.2) A whole-statute fixed-term validity bound has lapsed at `as_of`. `version` is null, `text.available=false`; top-level `valid_until` (inclusive) and `expires` (exclusive) dates plus an `expiry` provenance block are present (§6.1). |
| `expiry_unverified` | (since 0.2) A whole-law fixed-term expiry clause was recognised on the governing version but its validity end could not be determined (unparseable date, conflicting bounds, or ambiguous anaphoric year). **Blocking**: `version` is null and the `expiry` block carries the blocking diagnostic code. Never read as confirmed-live. |
| `timeline_unverified` | (since 0.2.x, §7.3; narrowed in 0.3 for proved temporary-twin windows, §7.4) The replayed timeline this query runs over carries break evidence governing at `as_of` — e.g. an occupancy-contract violation at amendment X, a failed op targeting the queried provision, or a temporary-twin window whose payload, bounds, or deferred occupant cannot be proved. **Blocking**: `version` is null, `text.available=false`; top-level `timeline_broken_at` `{amendment_id, diagnostic_code}` plus a `timeline_integrity` block enumerate the typed breaks. Never read as a legal fact — neither presence NOR absence of the provision is asserted. |

Consumers MUST treat any status other than `selected` as "no asserted
text-state". In particular `address_not_found`, `ambiguous_address`, and
`invalid_address`/`invalid_query` are *fail-loud* outcomes: the seam resolves an address
exactly or by a UNIQUE suffix, never by arbitrary order. A near-miss address
MUST be expected to fail rather than resolve to a different provision.
`expiry_unverified` is likewise fail-loud: the engine refuses to assert
either live text or expiry when the stated bound cannot be proven.

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
- `lineage` — `{status, address_chain, migration_event_count_considered,
  fingerprint, fingerprint_algorithm, fingerprint_semantics}`. `status` ∈
  `self_only | migration_chain | unresolved_address`. The `address_chain`
  traces renumbering/migration history considered at `as_of`. `fingerprint` is
  a compact SHA-256 handle over jurisdiction, statute id, status, address
  chain, and migration count; it is excluded from `derived_state_hash`.
- `selection` — `{status, required_dimensions, certificate}` explaining the
  version-selection decision (rail, candidate count, selected dates).
- `text` — `{rendered, available}`; `available=false` for tombstone/absent.
- `engine`, `source`, `source_locator` — provenance; NOT part of the state
  hash (§3). `engine.git_dirty` reports tracked LawVM code/index dirtiness
  relative to `engine.git_commit`; untracked local artifacts do not taint the
  engine identity because they do not change executable LawVM code.
- `diagnostics` — optional list of non-clean proof/recovery findings attached
  to an otherwise servable answer. A selected response MAY carry diagnostics
  such as `COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED` or
  `APPLY.UNCOVERED_BODY_RECOVERY` when the selected source path depends on a
  profile-authorized recovery. These rows are not control signals: each row
  carries `seam_blocking=false` when the response remains servable, while
  `finding_blocking` preserves the original strict/profile finding severity.
  Consumers SHOULD surface or log these rows instead of treating the selected
  answer as proof-clean.

### 2.4 `source_locator` XPath footing

`source_locator` is provenance and is excluded from `derived_state_hash`.

When LawVM loads the referenced source XML bytes, `source_locator` MAY include
top-level `artifact_digest` and `artifact_digest_algorithm="sha256"`, with
matching `detail.artifact_digest*` fields. This digest identifies the exact
source artifact used for locator/span footing. It is provenance only and is not
part of `derived_state_hash`.

For Finland base-statute versions, `source_locator.xpath` MAY contain a
deterministic Finlex AKN structural XPath candidate derived from the resolved
`LegalAddress`. The corresponding status is
`detail.xpath_status="finlex_structural_xpath_candidate"`.

Since 0.2.x, base-statute rows MAY also include top-level
`source_locator.char_span` and `source_locator.byte_span` when LawVM can anchor
the selected element in the raw UTF-8 Finlex source XML. In that case:

- `detail.source_xml_span_status="available"`;
- `detail.char_span_status="finlex_raw_xml_eid_element_scan"`;
- `detail.byte_span_status="finlex_raw_xml_eid_element_scan_utf8"`;
- `detail.source_xml_span_match_basis` is either `xpath_candidate` or
  `fallback_eid`;
- `detail.source_xml_eid` and `detail.source_xml_local_tag` identify the raw
  XML element that was scanned.

The `fallback_eid` basis means the structural XPath candidate did not match
exactly one raw XML element, but the Finlex `eId` derived from the resolved
address did. This is common when the public XML contains wrapper containers
such as `hcontainer` between `body` and `section`. If no exact raw element can
be anchored, `byte_span` remains absent and the detail status explains the
reason, e.g. `unavailable_xpath_match_count_not_one`.

For Finland operation-source versions, the cited document is the amending act,
not the amended target provision. The target address is still exposed as
`detail.target_xpath_candidate`, but top-level `source_locator.xpath` remains
absent and `detail.xpath_status` is
`unavailable_operation_source_target_not_xml_anchored`. Operation-source footing
comes from the bounded `OperationSource.raw_text` witness when available. That
nested `detail.source_witness` object MAY include `quote_char_span` and
`full_raw_text_char_span` over the stored `OperationSource.raw_text` string
after boundary-whitespace trimming. Those spans are over the stored witness
string, not the source artifact.

The same nested witness also carries shared `SourceWitness`-compatible fields:
`source_role="operation_source_raw_text"`, `artifact_id`, `locator`,
`source_lane`, `bounded_preview`, and `preview_digest*`. When the referenced
source XML bytes were loaded, it also carries `digest*` for the source artifact.
These fields support shared digest-coverage reporting; they do not make the
quote executable authority by themselves.

Since 0.2.x, operation-source rows MAY also include top-level
`source_locator.char_span` and `source_locator.byte_span`, plus nested
`detail.source_witness.artifact_char_span` and `artifact_byte_span`, when the
trimmed `OperationSource.raw_text` appears exactly once in the raw UTF-8
amending-source XML. In that case:

- `detail.operation_source_xml_span_status="available"`;
- `detail.char_span_status="operation_source_raw_xml_quote_scan"`;
- `detail.byte_span_status="operation_source_raw_xml_quote_scan_utf8"`;
- `detail.source_witness.artifact_span_status="operation_source_raw_xml_quote_scan"`;
- `detail.source_witness.artifact_span_match_count=1`.

If the quote is absent, duplicated, unavailable, or not UTF-8-decodable, the
top-level spans remain absent and `operation_source_xml_span_status` plus the
nested `artifact_span_status` explain why. This is quote-footing only: it does
not identify the amended target provision inside the amending act, and it does
not authorize replay.

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
  The emitted `lineage.fingerprint*` fields are derived from these fields plus
  jurisdiction/statute id and are excluded from this hash input.
- `version` — `{effective, enacted, expires, variant_kind, content_state,
  applicability}`, or null.
- `content_hash`
- `expiry` — (since 0.2) present IFF the fixed-term overlay fired for this
  query (`status` ∈ `expired | expiry_unverified`): the full `expiry` block
  (§6.1) including bound provenance (`source_text`, `source_hash`,
  `rule_id`, `governing_bound_id`) or the blocking diagnostic. Responses on
  which the overlay does not fire hash EXACTLY as under 0.1 — the member is
  absent, not null.
- `timeline_broken_at` and `timeline_integrity` — (since 0.2.x, §7.3) present
  IFF timeline-break evidence is relevant to this query (statute-scoped break,
  or address-scoped break matching the queried target). Includes the
  non-blocking warning case (break effective AFTER `as_of`): a consumer pin
  must notice that the statute's timeline is broken even when the pre-break
  answer is servable. Responses without relevant break evidence hash EXACTLY
  as before — the members are absent, not null.
- `temporal_schedule` — (since 0.3, §7.4) present IFF a proved legal-time
  scheduler interval was materialized for the selected version. It is an
  evidence/control block and is excluded from `derived_state_hash`; the
  selected version metadata and `content_hash` carry the hashed state change.

Canonical encoding: `json.dumps(..., ensure_ascii=True, sort_keys=True,
separators=(",", ":"))` then `sha256` of the UTF-8 bytes.

### 3.1 Excluded from the hash

The following are NEVER hashed: `spec_version`, `engine` (`producer`,
`build_id`, `git_commit`, `git_dirty`, `repository`), `source`,
`source_locator`, `diagnostics`, `text`, `selection`, `address_match`, `title`.
A change in engine build, git commit, working-tree dirtiness, or non-control
diagnostic/proof metadata MUST NOT, by itself, change `derived_state_hash`.

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
- The change detector is the corpus-backed **consumer-contract regression suite**
  (`tests/test_fi_provision_state_consumer_contract.py`): it re-runs pinned
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

`statute_id` is the legacy/local normative-work identifier OF THIS SEAM
VERSION. Certificate envelopes (CERTIFICATE_SCHEMA_V0.md) identify their
subject by the universal `subject.work_id` (`fi:act:273/2009`) and map it
onto this projection's `statute_id` via `subject.legacy_statute_id`;
the seam keeps `statute_id` unchanged — projections preserve local
contracts.

## 6. Fixed-term whole-law expiry and remaining limitations

### 6.1 Fixed-term whole-law expiry (modeled, default-on since 0.2)

A Finnish fixed-term statute (määräaikainen laki) states its whole-law
validity period in the entry-into-force provision's prose. Since 0.2 the seam
models this as a statute-level validity bound:

- **Lapsed bound** → `status="expired"`: `version` null, `text.available`
  false, top-level `valid_until` (inclusive) and `expires` (exclusive), and an
  `expiry` provenance block carrying the source clause text, source hash, the
  per-grammar-family `rule_id`, and `governing_bound_id`. Extension acts
  text-replace the entry-into-force provision; the governing bound at `as_of`
  is the latest eligible one, so an extended statute stays `selected` until
  its extended term lapses.
- **Toistaiseksi outer cap** ("voimassa toistaiseksi, ei kuitenkaan kauemmin
  kuin ...") → still `status="expired"` past the cap (there is NO weaker
  "possibly expired" status), with `bound_kind="upper_cap"`,
  `source_phrase_kind`, and `earlier_termination_possible=true` exposed on the
  `expiry` block so consumers can see the bound is a cap.
- **Duration-form bound** ("on voimassa kahden vuoden ajan sen
  voimaantulosta", "voimassa 12 kuukautta lain voimaantulopäivästä lukien")
  → computed from a CONCRETE commencement date under the pinned 150/1930 §3
  corresponding-day rule (month-end fallback), never ad hoc arithmetic. The
  `expiry` block then carries `bound_kind="duration_from_commencement"`,
  `rule_id="fi_duration_year_month_corresponding_day"`,
  `arithmetic_authority="fi/150/1930"`, the recorded `authority_scope_caveat`
  (150/1930 §1 governs procedural deadlines; applying it to whole-law
  validity is a recorded inference), `epistemic_status=
  "computed_under_pinned_authority"`, `commencement_date`,
  `commencement_source_kind` (`same_sentence` or
  `same_statute_commencement_clause`), and `duration_spec` (`P2Y`, `P12M`).
  A duration clause whose commencement is decree-set, unstated, or ambiguous
  stays blocking (`TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED`) — the pinned
  arithmetic authority never supplies missing commencement facts.
- **Elided-year end** ("tulee voimaan ... <year> ... ja on voimassa vuoden
  loppuun", same sentence only) → end of the commencement year, recorded
  with `rule_id="fi_elided_year_end_from_same_sentence_commencement_year"`
  and `epistemic_status="high_confidence_inference"` — never presented as a
  grammar fact.
- **Recognised but unprovable bound** (unparseable date, conflicting bounds at
  one effective date, ambiguous anaphoric year) → blocking
  `status="expiry_unverified"` with the diagnostic code on the `expiry`
  block. The seam never converts an unproven bound into either a live or an
  expired answer.
- Bare "on voimassa toistaiseksi" (no cap) is the permanent-law default and is
  NOT a bound.
- Cutoff conventions: stated calendar ends ("31 päivään joulukuuta") and
  duration-computed ends are INCLUSIVE `valid_until` / exclusive
  `expires_on = valid_until + 1`. Event-bound validity ("voimassa päivään,
  jona X tulee voimaan") is NOT yet resolved; when it is, the cutoff is
  exclusive at the resolver date (`expires_on = resolver_commencement_date`,
  not + 1) — a deliberate asymmetry pinned by fixtures in
  `tests/test_fi_temporal_fixed_term_expiry.py`.

Rollback: setting `LAWVM_ENABLE_FIXED_TERM_STATUTE_BOUNDS=0` restores the 0.1
flag-OFF behavior (no `expired`/`expiry_unverified`; a lapsed fixed-term law
reads `selected` with live text). The 0.1 MANDATORY mitigation (manual
validity check past a prose term) applies whenever the rollback is active.

Residual coverage caveat: a small typed residue of statutes carries
recognised-but-unprovable bounds (event-bound "kunnes ..." clauses, duration
forms without a resolvable commencement, source typos stating impossible
dates). These return `expiry_unverified` rather than a wrong answer; the
residue is enumerated, typed, and carries the offending clause text in its
diagnostics.

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
`spec_version` bump AND will manifest as divergence in the consumer-contract
regression suite (`tests/test_fi_provision_state_consumer_contract.py`), which
consumers SHOULD also run in their own CI. Consumers MUST pin the
`spec_version` they validated against.

### 7.0.1 Changes within 0.2: response exposes `spec_version`

Non-breaking under §7: `spec_version` is an added top-level contract marker and
is explicitly excluded from `derived_state_hash`. It lets downstream lockfiles
pin the prose contract they validated against without changing existing state
hashes.

### 7.1 Changes 0.1 → 0.2

- New statuses `expired` and `expiry_unverified` (§2.1); fixed-term whole-law
  bounds are modeled and DEFAULT-ON (§6.1). The 0.1 known-limitation 6.1 is
  retired in favor of the modeled behavior; the rollback flag preserves 0.1
  semantics.
- `derived_state_hash` input gains a conditional `expiry` member (§3),
  present only when the fixed-term overlay fires. **Hash impact:** every
  response that is NOT a lapsed/blocked fixed-term statute hashes identically
  to 0.1 (verified by the 21-pin suite passing unchanged across the flip);
  queries on lapsed or unprovable fixed-term statutes change status and hash
  — that change is the feature.

### 7.2 Changes within 0.2: expiry diagnostic vocabulary widened

Non-breaking under §7 (status enumeration, hash member set, eligibility
predicate, and resolution rules unchanged). Consumers branching on `status`
are unaffected.

- The `expiry.diagnostic` code vocabulary on `expiry_unverified` responses is
  WIDENED: recognised-but-unresolved whole-law validity clauses are typed by
  what is missing instead of collapsing into
  `TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE`. The blocking codes are now:

  ```text
  TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE
  TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS
  TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS
  TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING   (duration form outside the
                                                    pinned 150/1930 rule's
                                                    input domain: caps,
                                                    unsupported units,
                                                    non-commencement anchors)
  TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED        (duration form whose
                                                    commencement anchor is
                                                    decree-set, unstated, or
                                                    ambiguous — the period is
                                                    computable, its start is
                                                    not)
  TEMPORAL.EVENT_BOUND_RESOLVER_MISSING            (säädöskokoelma-discernible
                                                    event bound, resolver not
                                                    yet built)
  TEMPORAL.EVENT_BOUND_OUT_OF_DOCTRINE             (substantive event bound)
  TEMPORAL.SOURCE_IMPOSSIBLE_DATE                  (source states a calendar-
                                                    impossible date)
  ```

  The diagnostic-code vocabulary is OPEN within 0.2 for already-blocking
  `expiry_unverified` responses. This is a control-contract-compatible
  refinement: `status` semantics and the hashed field SET do not change.
  However, because the full `expiry` block is part of `derived_state_hash`
  when the overlay fires (§3), refining a row's diagnostic code DOES change
  `derived_state_hash` for that row. Consumers that pin hashes on blocked
  fixed-term rows MUST rerun their canaries.
- Three recognised clause shapes that are NOT whole-law expiry bounds left
  the blocking lane as audited non-candidates (decree-set commencement,
  start-only validity statements, voimassa-text that never predicates
  validity of the act itself). Statutes previously blocked on those shapes
  now answer `selected` with live text — for those rows, status and hash
  change. This is a correction to the expiry recognizer's classification,
  not a seam-schema change; pinned consumers MUST still treat it as a
  semantic output change for the affected rows, covered by canary diffs.

### 7.3 Changes within 0.2: timeline-integrity surfacing

Previously, when a statute's replay fold recorded break evidence — e.g. an op
applied onto a slot whose occupancy contradicted its occupancy contract
(`APPLY.OCCUPANCY_POLICY_VIOLATION`, true violation), or a compiled op that
could not be applied at all (`APPLY.FAILED_OPERATION`) — the seam still served
clean-looking answers over the unproven timeline: `selected` with stale text,
or `address_not_found` for addresses the breaking amendment may have created.
A consumer could (and did) mint "no amendment touched this provision" from
that output. This violated the core discipline: uncertainty must remain
visible.

Since 0.2.x the seam classifies replay break evidence
(`lawvm.tools.timeline_integrity`) into typed `TimelineBreak` records:

- **statute scope** (occupancy true violations; any finding marked
  `timeline_fatal` by its emitter): the shared-state compile fold is unproven
  from the breaking amendment onward. Every query with `as_of` at/after the
  break's effective date (or with an undatable break — conservative) returns
  `status="timeline_unverified"` with `version` null and the typed
  `timeline_broken_at`/`timeline_integrity` members. Queries strictly BEFORE
  the break's effective date are servable from the proven prefix and keep
  their ordinary status, with the marker members present as a hashed warning.
  Unresolved addresses under a governing break also return
  `timeline_unverified` (NOT `address_not_found`) with the resolution outcome
  preserved as `timeline_integrity.resolution_status` — absence is not
  provable over a broken timeline.
- **address scope** (`APPLY.FAILED_OPERATION`): only queries whose target
  matches the failed op's recorded target are affected; all other addresses
  of the statute hash byte-identically. Deliberately NOT classified:
  `APPLY.RELABEL_SKIPPED` (governed skip), `APPLY.FALLBACK_WHOLE_SECTION_
  REPLACE` (unproven-but-applied fallback obligation), `TIME.*`/`COVERAGE.*`
  completeness obligations — widening the class is a seam-visible semantics
  change and gets its own spec note.

Classification under §7: responses without relevant break evidence are
byte/hash-identical (verified against the 21-pin suite and unaffected-statute
diffs), and compliant consumers already treat any status other than
`selected` as "no asserted text-state", so an unknown status fails safe.
Nevertheless a STRICT reading of §7 makes widening the `status` enumeration
and the hashed member set 0.3 material; consumers that enumerate statuses
exhaustively or pin hashes on rows of break-carrying statutes MUST treat this
as a semantic output change for those rows (canary diffs), exactly like the
7.2 recognizer corrections. Rollback:
`LAWVM_ENABLE_TIMELINE_INTEGRITY_SURFACING=0` restores the prior (dishonest)
behavior; responses for statutes without break evidence are byte-identical
either way.

### 7.4 Changes 0.2 → 0.3: temporary-twin legal-time scheduler

Finnish twin laws split a reform into a permanent law with a deferred section
commencement and a temporary gap-filler law that inserts the SAME section for
the gap window. The compile fold applies ops in document order, so the
temporary twin's text is never materialized inside its own in-force window —
the deferred-commencement twin holds the slot in fold order. A PIT query
landing inside that window would otherwise serve silently-wrong text (the
permanent twin's, or absent). The apply layer already detects this exact
situation and records a non-blocking observation
(`APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT`,
`rule_id="temporally_disjoint_twin_insert"`).

In 0.2.x that observation was exposed as a fail-loud
`TEMPORAL.WINDOW_UNMATERIALIZED` break. In 0.3 the seam keeps the same
diagnostic as a discovery signal, but attempts a narrow legal-time scheduler
splice before answering:

- **Proved scheduler path.** If the compiled `LegalOperation` stream contains
  exactly one same-slot operation from the temporary act, with an `IRNode`
  payload, matching `effective` and `expires` bounds, and the existing timeline
  contains the deferred permanent occupant at `occupant_effective`, the seam
  appends a `variant_kind="temporary"` `ProvisionVersion` for the half-open
  interval. In-window PIT queries then return `status="selected"` with the
  temporary text.

- **Fail-loud fallback.** If target exactness, source id, payload, bounds, or
  deferred occupant agreement is missing or ambiguous, the scheduler refuses to
  splice. The original window break remains and the query returns
  `status="timeline_unverified"` with `timeline_broken_at` /
  `timeline_integrity`, exactly as under 0.2.x. The seam never invents a
  temporary version from the diagnostic alone.

- **Scoping.** The scheduler is window+address-scoped, NOT statute-wide. Queries
  on other addresses of the same statute, or on the same address outside the
  half-open window, remain byte/hash-identical to the no-scheduler baseline
  except for the global `spec_version` contract marker. Outside-window window
  diagnostics still drop out entirely.

- **Bounds are start-inclusive, end-exclusive** (`incoming_effective <= as_of
  < incoming_expires`), matching the kernel `expires` convention (§2.2: a
  version is inactive ON its expiry date). On `incoming_expires` itself the
  deferred twin commences and the fold-materialized permanent text is selected.

- **Self-evidencing.** Selected in-window responses include top-level
  `temporal_schedule` with `scheduler="temporal_write_interval_stage_1"`,
  `hash_role="excluded_from_derived_state_hash"`, and one or more typed
  `TemporalScheduleDelta` rows. Each delta carries the diagnostic code,
  occupant source/effective date, and an interval with `write_id`, `op_id`,
  `fold_sequence`, `target_address`, `action`, `effective`, `expires`,
  `enacted`, `variant_kind`, `payload_hash`, `source_work_id`,
  `source_locator`, `receipt_id`, `origin_rule_id`, and
  `provenance_findings`.

- **Versioning.** This is `spec_version=0.3` because affected rows changed from
  fail-loud `timeline_unverified` to `selected` text-state when the proof
  boundary is satisfied. Consumers that pinned 0.2.x window rows must rerun
  their canaries. The diagnostic code remains visible inside
  `temporal_schedule.deltas[*].diagnostic_code` for traceability.

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

b. The original "21 pins" count and live change-detector claim. The current
   in-repo contract suite is `tests/test_fi_provision_state_consumer_contract.py`;
   the initial fixture provenance was MeVM's fact-pack pins.

c. The enacted-date semantics-change precedent (commit b09e0003 populating
   enacted for base versions, causing cross-build hash divergence for pins
   minted earlier). Copied from LAWVM_MEVM_CONTRACT_REPLY lines 51-64; presented
   here only as a precedent illustrating the cross-engine NON-guarantee, not as
   a current behavior claim.
-->
