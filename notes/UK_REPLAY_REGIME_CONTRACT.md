# UK Replay Regime Contract

Status: draft contract.  
Kind: descriptive migration contract.

Purpose:

- make the current UK replay/evidence regimes explicit
- stop UK benchmark outputs from pretending they are produced by a single undifferentiated semantic mode
- preserve a public contract for the UK refactor path

## 1. Problem

The current UK stack still mixes several lanes:

- source-backed replay
- effects-assisted replay
- metadata fallback
- oracle-aware alignment
- current-vs-replayed benchmark comparison

That is workable for development, but it is too implicit for a publication-safe
architecture.

## 2. Required Regime Axes

Every UK replay/evidence result should eventually state at least:

### 2.1 Semantic replay lane

Possible values:

- `source_first_enacted_base`
- `effects_assisted_replay`
- `metadata_backfilled_replay`
- `not_run_source_unavailable`

### 2.2 Oracle alignment lane

Possible values:

- `none`
- `oracle_alignment_adapter`
- `not_run_source_unavailable`

### 2.3 Comparison lane

Possible values:

- `none`
- `current_pair_benchmark`
- `noncommensurable_oracle_comparison`

### 2.4 Source purity lane

Possible values for the current migration tranche:

- `source_backed_effects_assisted`
- `source_backed_with_oracle_adapter`
- `metadata_backfilled_source_semantics`
- `metadata_backfilled_with_oracle_adapter`
- `not_run_source_unavailable`

## 3. Current Reality

Today, the main UK evidence path is best described as:

- semantic replay lane: `effects_assisted_replay`
- oracle alignment lane: `oracle_alignment_adapter`
- comparison lane: `current_pair_benchmark`

That is a good engineering lane, but it is not yet the same thing as the ideal
source-first semantic lane.

## 4. Immediate Rule

Until the lanes are operationally separated:

- UK evidence outputs should declare the current replay regime explicitly
- review/frontier work should not describe `current_pair_benchmark` outputs as
  pure source-first semantic outputs

## 5. Near-Term Implementation Target

The smallest useful current implementation is:

- expose the active UK replay regime in `build_uk_evidence_bundle()`
- keep that regime stable and machine-readable
- expose whether `CURRENT_XML_METADATA_BACKFILL` is enabled as an explicit runtime switch
- make the evidence bundle cache regime-aware so cached UK bundles cannot silently cross replay lanes

This does not solve the whole architecture.
It makes the current lane explicit, which is the prerequisite for cleaning it up.

## 6. Current Runtime Separation

Today the UK evidence surface supports an explicit runtime split between:

- effects-assisted replay with metadata backfill enabled
- effects-assisted replay with metadata backfill disabled
- replay with oracle-alignment adapter enabled
- replay with oracle-alignment adapter disabled

The current CLI surface exposes this through:

- `lawvm evidence -j uk --metadata-backfill`
- `lawvm evidence -j uk --no-metadata-backfill`
- `lawvm evidence-review -j uk ... --metadata-backfill`
- `lawvm evidence-review -j uk ... --no-metadata-backfill`
- `lawvm evidence -j uk --oracle-alignment`
- `lawvm evidence -j uk --no-oracle-alignment`
- `lawvm evidence-review -j uk ... --oracle-alignment`
- `lawvm evidence-review -j uk ... --no-oracle-alignment`
- `lawvm evidence -j uk --source-first-candidate`
- `lawvm evidence-review -j uk ... --source-first-candidate`

Current machine-readable output also exposes:

- `uk_replay_regime.source_purity_lane`
- `uk_replay_regime.source_semantics_clean`
- `uk_replay_regime.source_first_candidate`
- `uk_replay_regime.source_first_candidate_reasons`

Important limitation:

- disabling metadata backfill does not yet produce the full
  `source_first_enacted_base` ideal lane
- it produces the cleaner current lane: effects-assisted replay without
  `CURRENT_XML_METADATA_BACKFILL`
- disabling oracle alignment does not yet remove every oracle dependency from
  the UK stack overall
- it does make the current replay-time oracle-adapter lane explicit
- replay-time mutation is now oracle-blind for repeal semantics
- post-apply EID grounding now runs as an explicit post-replay adapter stage
- stale replay-local oracle scaffolding has now been removed
- remaining oracle-lane cleanup is mostly summary/docs tightening rather than
  replay-boundary surgery

## 7. Current Source-First Candidate Rule

The current UK runtime can honestly mark a run as a `source_first_candidate`
only when all of these hold:

- no metadata-backfilled ops were used
- metadata-only effects were excluded from replay selection
- no replay-time oracle-alignment adapter behavior was active
- applicability selection stayed in the explicit current lane
  `effective_date_plus_feed_applied`
- authority mode stayed in the public source-text lane
  `source_text_only`

This is still not the final ideal UK frontend.
It is the cleanest currently expressible candidate lane inside the present
runtime architecture.

The runtime now also exposes this as an explicit preset:

- `--source-first-candidate`

That preset currently means:

- no metadata backfill
- no metadata-only effects
- no replay-time oracle alignment
- feed-applied gate enabled
- `authority_mode = source_text_only`

Current tightening already in place:

- `source_text_only` now rejects lowered ops whose target expansion depends on
  `metadata_split`, even when extraction authority is source-backed
- `source_text_only` accepts same-provision operations extracted from official
  enacted affecting-act XML (`AFFECTING_ACT_ENACTED_TEXT`) when acquisition
  has already emitted the current-shell/current-missing source-lane observation.
  This remains source text, not metadata backfill; `EFFECT_FEED_INDEX` and
  `CURRENT_XML_METADATA_BACKFILL` are still rejected.
