> **Status (2026-06-22):** Current-with-noted-drift. Normative/living roadmap (governs AGENTS.md regex/recognizer doctrine = §1.11/§1.12, §2.4/§2.5 — NOT '§1.13', which does not exist). All 'PEG3'/'peg3.py' = now johtolause/surface_parse.py (regex-free; peg3.py deleted); the doc's line-84 note acknowledges this but stale refs persist at lines 5/43/98/134/135/144/200.

# Regex → Grammar Migration Roadmap

Status: living roadmap. Synthesizes a codebase-grounded ChatGPT Pro review
(2026-05-29) with the in-tree regex-grammar census (`.tmp/regex_grammar_census.md`).
Governs how AGENTS.md §1.12 (regex-versus-recognizer) is applied across frontends.

## The three-way split (authoritative)

```
KEEP regex for:
  bounded lexical tests, normalization, label parsing, text-patch matching

WRAP/LINT/PREFILTER regex for:
  boolean classifiers and small recognizers
  (route through src/lawvm/core/regex_safety.py)

REPLACE regex with grammar/scanner/parser for:
  repeated amendment-instruction families, source-carried structural selectors,
  definition-entry parsing, string-sentinel text selectors
```

Most LawVM regex is fundamentally linear / O(1)-per-position; blowups are
backtracking-lowering artifacts, not the legal-text problem. Only the
"many overlapping patterns encode one drafting language" class is a true
bespoke-parser case.

## Two smell axes (distinct)

1. **Regex pile = extensional shadow of a recognition grammar.** N overlapping
   `re.finditer` passes over one text, span-overlap dedup, production-named
   patterns. Fix: one single-pass recognizer.
2. **`TEXT_*` sentinel strings = stringly-typed IR.** Symbolic strings
   (`TEXT_FROM_X_TO_END`, `TEXT_AFTER_CHILD_TAIL_paragraph_3`,
   `TEXT_DEFINITION_CHILD_PARAGRAPH_<term>`) travel parser→lowering→replay and
   are re-parsed with regex downstream. A hidden language in strings. Fix:
   typed selector objects (`UKTextSelector` union). This is §1.9 at the IR
   level and is arguably higher value than shaving regex time.

## Core + Finland audit & checkable doctrine (2026-06-20)

A fresh 7-cluster, per-site audit of `core/` (13 files) + `finland/` (~129 files), cross-checked by an independent non-Anthropic pass. **~1391 regex sites: 91 keep / 18 wrap / 25 move / 35 violation-flagged.** Headline: the architecture is healthy. The kernel holds no domain parser (one Finnish-idiom leak), the canonical surface parser is regex-free, the Legal Surface Graph lenses are regex-clean (0 violations), and the flagged "violations" are honest fallback/residue/oracle layers (mostly witnessed), not silent producers. **Do not do a big-bang migration** — most regex is benign-forever lexical/date/path/display and stays.

### The firewall as a yes/no test (refines §1.12)

A regex is an **outright violation** when raw statute / johtolause / transitional **prose is the sole evidence** for any of the six prime-directive facts: (a) action family (REPEAL/REPLACE/INSERT/MOVE), (b) target scope/ownership (chapter/section/momentti/kohta/luku/osa, or which sections a range absorbs), (c) lifecycle (expiry/commencement/validity-bound selection, repeal-vs-not), (d) saved/excepted effect (`lukuun ottamatta` / `siltä osin` / `jää voimaan`), (e) routing/applicability (which statute is affected), (f) drop/widen of a mutation. Date and citation **tokens** are not this; selecting *which* date is the bound or *which* unit is in scope is. Bright line: **if deleting the regex would force you to explain a legal operation differently, it is not lexical.**

### Decision procedure (apply per site, no judgment)

1. Regex over an **owned/structured string** (label, eId/AKN path, rendered `LegalAddress`, citation id, date token, whitespace, display text) → **KEEP** (benign forever).
2. Regex over prose that only **lexes** a token/range/list, **repairs surface pre-tokenization** (witnessed via a `rule_id`), or is a **site anchor supplementing a shared grammar** (proven 0-loss superset) → **KEEP** (lexer-primitive floor).
3. Regex over prose emitting a **boolean/feature** used as a gate/recall signal that does **not** by itself decide a prime-directive fact → **WRAP** through `core/regex_safety.py` `compile_classifier_regex` (mandatory, not optional, for any classifier over long/adversarial text).
4. Regex deciding a prime-directive fact but with address/structure delegated to the shared grammar and tokens lexical — only the **precedence/ambiguity/attachment** choice lives in regex evaluation order → **EXTRACT_POLICY** (lift the implicit rule into a named, evidence-carrying policy object; keep the lexers).
5. Regex deciding a prime-directive fact with overlapping production-named patterns, semantic-object captures, action-family-from-keyword-map, "add a variant = add a regex" → **MOVE**: a hidden drafting grammar (kind 3) / string-encoded selector (kind 4). Migrate family-by-family into the canonical recognizer **in shadow mode** against the existing regex; flip authority only at corpus parity; preserve `witness_rule_id`; delete the branch.

### Benign-forever / already-terminal (do not touch)

- **Benign-forever** (never migrate, wrap only if profiling/lint demands): label/number normalization & sort keys, roman↔arabic, `1 a)`→`1a)`, §-selector/locator/eId/AKN/href lexers, citation-id & date **token** lexers, month tables, whitespace/punctuation/display normalization, comparison-lane normalization (`oracle_comparison`, `replay_products` display), within-document text-patch matching with uniqueness checks (corrigendum text-replace, merge row-splice), range/list expansion over already-parsed endpoints.
- **Already-terminal** (keep, do **not** re-promote and do **not** delete): a demoted regex pile superseded by a canonical token-native parser, surviving only as a fallible differential **oracle / typed residue / metadata enricher** — `delegation.py` (behind `delegation_canonical`/`delegation_edge_adapter`), the `kumotaan` whole-section path (structure delegated to `parse_body_provision_tail`), the `johto_scope_mentions` §-anchor floor, `metadata.py`'s per-family date grammar (with `rule_id`s + ambiguity-**blocking**). This *is* the prescribed end-state for a migrated kind-3 family; a `violation=true` flag there describes what it computes, but its **role** is oracle/residue, so it is warranted.

### Core/ kernel policy (sharper than the Constitution's §12)

The cleanroom kernel may hold **only jurisdiction-agnostic** regex — its own canonical address/selector/locator lexers, date-shape lexers, structural-label classifiers, display/comparison normalization, and the `regex_safety` wrapper itself. It must **never** hold a jurisdiction-language classifier. Two standing core concerns:

- `core/tree_ops.py` `_FI_DEFINITION_INTRO_PHRASES` (Finnish `tarkoitetaan` idiom deciding a definitions-list structural fact in the kernel) — **a firewall leak; de-leak it**: the frontend supplies a definition-introducer predicate, or the IR carries a typed definition-list flag, so the kernel branches on an owned/typed signal.
- `core/selector.py` embeds `§`/`momentti`/`kohta` + a Finlex materialized-ordinal rule — acceptable as a legacy analyst/CLI compatibility shim **only if fenced**: no replay authority, no frontend parse authority; eventually the Finnish surface moves to `finland/tools` while core keeps `HierarchicalLocator`-style typed locator plumbing.

No new domain regex may enter core.

### Per-cluster state

| Cluster | Sites | State | Notable |
|---|---|---|---|
| core | 42 | clean for regex | 1 leak (`tree_ops._FI_DEFINITION_INTRO_PHRASES`) + `selector.py` to fence |
| johtolause | 95 | sound core, 3 piles | `surface_parse` regex-free (the model end-state); MOVE `clause_patterns` rows + `johtolause_supplements` + `api._TEXT_AMEND_RE`; `affected_statute` = the model (typed + morphology) |
| references | 141 | good shape | bulk = stable citation/address lexers; MOVE `defined_terms`; EXTRACT eu embedded-repeal; cluster-wide WRAP gap |
| legal_surface | 89 | **0 violations** | all `replay_authorized=False`; WRAP the 3 producers (`enclosing_anaphora`, `norm_composition`, `delegation_parse`) |
| apply_ops | 95 | benign | label/display normalization; the 1 prose-scope site (`apply_runtime_support`) already wrapped |
| semantic_dense | 389 | the real work | `scope`/`vts`/`citation_routing`/`metadata`-expiry/`kumotaan`-residual = violation core; delegation + kumotaan-whole-section already terminal |
| finland_tail | 540 | mixed | `normalize`/`scope`/`supplements`/`vts`/`kumotaan` = MOVE core; rest lexical/display |

### Ranked Finland backlog (EV-ordered; targeted, shadow-mode, 0-delta gate)

1. **`scope.py` scope-ownership** (MOVE, EV high) — answer from typed op target paths + witnesses; route address matching through `scan_legal_addresses`; extract the chapter-chunk verb-binding precedence as a policy. The canonical violation.
2. **`normalize.py` fallback cluster → the canonical johtolause grammar** (MOVE, high) — fold the `*_FALLBACK_RE` discriminators into the token-native scan/surface parser; the grammar already exists, so this is low-risk-class, high-EV. (NB: older sections of this doc and some code comments still say "PEG3"/`peg3.py`; that parser has since been **decomposed/renamed into `johtolause/`** — read every "PEG3" reference as "the canonical surface parser".)
3. **`johtolause_supplements` productions** (MOVE, high) — extract the `muutetaan`/`lisätään` verb-zone segmentation as a named policy; keep witnesses.
4. **`citation_routing` amendment-title grammar** (MOVE, med) — one typed title recognizer vs 5+ variants; gates corpus routing.
5. **`vts.py` repeal-ordering + cut-point + name-exclusion** (EXTRACT/MOVE, med) — named recognizer over typed clause spans.
6. **`references/defined_terms` definition-entry parser** (MOVE, high) — typed `DefinitionEntry` + explicit scope-ambiguity policy; the home for the core `tree_ops` de-leak.
7. **`metadata` temporary-expiry scope + `ValidityBound` selection** (MOVE/EXTRACT, med) — address onto grammar, bound-selection precedence as policy; fix the `_parse_section_list_labels` FIXME.
8. **`clause_patterns` named-rows + `api._TEXT_AMEND_RE`** (MOVE, med) — typed `RepealRow`/`ReplaceRow` + a quote-aware text-amend production.
9. **`kumotaan` subsection/container + `kumotaan_replay` range-absorption** (MOVE, med) — finish the model; collapse the triplicated clause-boundary/range algorithm (rule-of-three).
10. **Cross-cutting WRAP** (WRAP, low risk) — route every over-long-prose classifier through `compile_classifier_regex`; **adoption is ~zero today — the single biggest, cheapest mechanical win.**
11. **`core/tree_ops` definition-introducer de-leak** + **`selector.py` fence** (EXTRACT, med) — the only kernel firewall concerns.
12. **Surface-lens ambiguity policies** (EXTRACT, low) — name embedded-repeal precedes/follows, exception-wins, gap windows; opportunistic.

### Enforcement gate (keeps the doctrine true — build on what exists, don't reinvent)

The repo already has the right primitives: `core/regex_recognition_coverage.py` declares regex rows **passive evidence, not replay authority** and forbids `regex_match_as_complete_parse` / `bounded_wildcard_as_semantic_proof` / `regex_coverage_as_replay_authorization`; `tests/test_regex_perf_gate.py` validates module-scope patterns; `scripts/inventory_parser_smells.py` is a **starter sensor**. Add a two-part CI lint by **expanding `inventory_parser_smells.py`** (not a second mechanism): (1) any classifier-pattern over prose in `finland/**` must be built via `compile_classifier_regex`, not raw `re.compile` — the biggest current gap; (2) a frozen-residue check that no **new** raw-prose regex deciding a prime-directive fact lands in the migration-cluster files, plus sensors for production-named patterns, semantic capture names (`target`/`action`/`scope`/`repeal`/`range`/`occurrence`), dynamic per-op f-string regex, multiple `finditer` over the same source text, span-overlap dedup, and 3+ copies of the clause-boundary/range algorithm (rule-of-three as a sensor). Fail new `core/` regex containing jurisdiction tokens (`§`/`momentti`/`kohta`/`luku`) unless in an explicit tools/compat allowlist. This converts the hand-maintained §1.11/§1.12 discipline into a standing gate so the firewall cannot silently re-leak.

## Ranked replacement targets

1. **UK `nlp_parser.py` → UK amendment-instruction grammar.** `parse_fragment_substitution()`
   is a hand-lowered drafting grammar (~40 `re.finditer` variants: quoted/block/
   child-qualified substitution, mixed body-heading, respectively/all-occurrence,
   wherever-occurring, ordinal, range-to-end, after-anchor, passive, dangling-quote,
   after-anchor insert). Build `instruction_grammar.py` + `instruction_tokens.py` +
   `instruction_surface.py`: quote-aware scanner + small recursive-descent/PEG.
   Output typed `UKTextRewriteInstruction(action, selector, replacement, occurrence,
   source_child_context, rule_id, witness_span)`. Keep `parse_fragment_substitution`
   public API as a `to_legacy_dict()` shim; run new parser in SHADOW MODE against
   old until diffs understood.
2. **`TEXT_*` sentinels → typed `UKTextSelector`.** LiteralTextSelector /
   RangeToEndSelector / AfterAnchorSelector / DefinitionChildSelector /
   TableCellParagraphSelector. Removes the hidden string-language.
3. **`source_definition_fragments.py` + definition replay → `UKDefinitionEntryParser`.**
   Definition grammar is duplicated in lowering AND replay (predicate patterns,
   next-definition detection, entry-range compilation, flat-child bounds).
   One `definition_entry_parser.py` used by both.
4. **UK child-tail / labeled-child / table-entry helpers → scoped parsers.**
   `source_child_tail_rewrites.py`, `source_labeled_child_parts.py`,
   `source_table_entry_paragraph.py`. Separate small parsers emitting typed
   selectors — NOT one giant UK grammar.
5. **`target_anchors.py` → absorb into UK instruction parsing.** after/before
   <unit> <label> insert is another copy of nlp_parser's grammar; call into it
   once instruction parsing exists.
6. **NZ `instruction_workqueue.py` → staged `NZInstructionParser`.** Keep
   evidence-first/diagnostic posture; add parser incrementally (direct text
   replace, omitting/substituting, after-insert structural payloads); workqueue
   calls it and keeps producing diagnostic rows.
7. **Estonia: promote the instruction waist, don't rewrite parser first.**
   `ee_instruction_waist.py` already defines the surface; route
   parser facts → EEParsedInstruction/EEInstructionWaist → LegalOperation.
   Decide later whether EEParsedInstruction converges with core ClauseSurface.
8. **Finland `normalize.py` fallback cluster → fold into existing PEG3.** Code
   docstrings literally say "FALLBACK: remove when PEG3 handles X"; the
   canonical surface parser (`surface_parse.py`) existed and superseded
   `peg3.py`. Lowest-risk recognizer win. (Census rank 1.)
9. **EU `ops_parser.py` → rebuild only when EU is prioritized.** Explicit
   placeholder; leave as compatibility parser.

## KEEP AS-IS (prevents over-extraction)

- **Finland `johtolause` is the MODEL** the others should imitate: canonical API,
  tokenization, surface parse/resolve, lower to ClauseAST, PEG/combinator over a
  token tape. Only improvement: finish `scan.py`'s future (grammar consumes
  annotations directly instead of sentinel tokens).
- **UK `source_text_normalization.py`** — good scanner (parser/comparison views,
  preserves quoted payload). Reuse from the instruction grammar, don't replace.
- **UK `text_matching.py`** — narrow target-local replay recovery with uniqueness
  checks, not a grammar. Optionally formalize a `TextMatchPolicy` dataclass; do
  not replace with a grammar.
- **UK `addressing.py`** — pure label normalization + operation ordering, bounded
  `fullmatch` helpers. Route through regex_safety if desired; not parser smell.

## Cross-frontend address grammar — DEFER (Pro reconciliation)

The census flagged §-reference / legal-address extraction as reimplemented in
FI/EE/NO/SE with the same grammar, different surface tokens, and called shared
`legal_address_grammar` the highest-leverage consolidation. Pro structured
per-frontend and did NOT push cross-frontend unification.

**Resolution: Pro's ordering wins, per AGENTS.md §12** (do not put jurisdiction
idioms in core until proven genuinely shared). The census correctly SPOTTED the
shared pattern, but unifying into core now is premature. Path: build per-frontend
typed recognizers first; let the shared shape emerge from 2-3 real
implementations; extract the core `legal_address_grammar` only after (farchive
model — earn independence). `LegalAddress` is already core; the parser need not
be until proven.

**Re-confirmed 2026-05-30 (user).** Defer stands: extract per-frontend reference
grammars independently as each frontend warrants it, and unify into a core
`legal_address_grammar` later *if ever*. The shared design (one rank-ordered
`LEVEL/LABEL/RANGE/LIST/FACET` grammar + a per-frontend surface lexicon emitting
the existing core `LegalAddress`) is understood; the only open question is the
trigger, and the answer is "not yet — independence first." Current duplication to
keep in mind when touching either: FI `_expand_sec_range`/`_expand_sec_item`/
`_parse_genitive_tail` vs UK `_expand_parenthesized_range`/`_is_sibling_group_family`/
`_split_metadata_provisions` are the same range/list/context-distribution algorithms.

## Implementation order (authoritative)

```
1. Land regex_safety.py prefilter (lint + adjacent-repeat + required-literal
   AND/OR prefilter + wrapper).                                    [in progress]
2. Route classifier/small-recognizer regexes through it.
3. Introduce UK typed text selectors + fragment objects; legacy dict/string
   conversion kept at the boundary.
4. Build UK instruction grammar in shadow mode (quoted/passive substitution,
   range-to-end, after/before insert, ordinal, all-occurrences, child-tail).
5. Extract UK definition-entry parser; use from BOTH lowering and replay.
6. Convert child-tail / labeled-child / table-entry helpers to typed
   selectors/fragments.
7. Add NZInstructionParser (direct text replace + insert-after payloads).
8. Promote Estonia EEInstructionWaist into the main parse/lowering path.
9. Leave EU as compatibility until EU rebuild is prioritized.
10. Expand scripts/inventory_parser_smells.py into a standing architecture
    sensor (encode rule-of-three in tooling, not memory).
```

Plus the low-risk early win available now: fold Finland `normalize.py` fallbacks
into PEG3 (step 8-class work, but cleanest because the PEG exists and the code
asks for it).

## Parser-smell inventory as standing sensor (step 10)

Expand `scripts/inventory_parser_smells.py` defaults to uk_legislation/,
new_zealand/, estonia/, eu/; add markers: many `re.finditer` in one function,
`TEXT_*` sentinels, rule_id explosion in a parser module, regex `.+`/`.*?`
capture near substitute/insert/omit, post-filter span-overlap suppression,
dict fragments with original/replacement/rule_id, parsing provenance/JSON notes.
This makes "regex pile became a grammar" an automatic signal.

## Why this is worth doing

LawVM is a compiler/evidence system: it compiles amendment law into typed
operations, replays over legal text-state, materializes point-in-time law, and
emits derivation evidence. A pile of regexes is acceptable for lexical facts; it
is the wrong long-term IR for amendment-instruction LANGUAGES. Specifying those
grammars is also a byproduct contribution: the formal operational grammar of
legislation, currently implicit in drafting convention.

## Spec Extraction Yield — the second value axis

A rewrite has two separable payoffs. Judge each candidate on BOTH:

```
implementation value: faster / safer / simpler / fewer blowups / less duplication
specification value:  the grammar becomes visible; semantic objects become named;
                       the ambiguity/precedence policy becomes explicit; residuals
                       become classifiable; the test corpus becomes generative;
                       the domain becomes more formal than it was
```

For some regex uses only implementation value exists (label parsing, normalization,
bounded boundary checks) — do not rewrite unless profiling demands it. For others
(the drafting-phrase pile, the TEXT_* sentinels) the **spec yield is the main
product**, and a rewrite is worth it even at NEUTRAL runtime.

### Classify every discovered regex family into one of five kinds

```
1. lexical recognizer        -> keep regex (+ safety wrapper)
2. boolean classifier        -> keep regex (+ compile_classifier_regex prefilter)
3. parser for a hidden domain language        -> extract a named spec object
4. string encoding of typed semantic objects  -> extract a named spec object
5. ambiguity/conflict policy disguised as post-processing -> extract a named spec object
```

Only kinds 3-5 justify heavier grammar work. Kinds 1-2 stay regex with the
safety/prefilter layer. The checklist that says "this is 3-5":
- adding a new legal variant means adding another regex (no generalization)
- many regexes share the same verbs/nouns/slots
- captures are semantic objects, not strings
- match spans overlap and need conflict resolution
- order of recognizers changes meaning
- TEXT_* style strings encode typed selectors
- rule_id names are more stable than the code paths
- tests are examples of a language, not isolated edge cases

### Sharper characterization of the nlp_parser pile

Not "40 regexes" and not "a union of 40 regular languages." It is:

```
an untyped string-to-operation transducer
  + a precedence/conflict policy
  + a span-overlap suppression policy
  + a legacy dict/string-sentinel output encoding
```

i.e. a **surface-to-operation lowering language**. The output is not match/no-match;
it is `{original, replacement, occurrence, rule_id, source_child_kind, ...}`. The
hidden object is the lowering from a source phrase to a LawVM operation. The grammar
rewrite's job is to NAME: the productions, the typed selector algebra, the allowed
ambiguities, the residual cases, and how a source phrase lowers to an operation.

### SPEC-FIRST ordering (changes step 4)

For the UK drafting-phrase family the FIRST deliverable is NOT better code. It is a
spec document: **`notes/UK_AMENDMENT_INSTRUCTION_GRAMMAR.md`** enumerating the
productions (Substitution / Insertion / Omission / Range / Definition families —
see Pro's draft in the spec discussion). Then:

```
1. write UK_AMENDMENT_INSTRUCTION_GRAMMAR.md (productions + typed selector algebra)
2. introduce typed UKTextSelector / UKTextRewriteFragment objects
3. old regex parser remains AUTHORITY; new grammar runs in SHADOW MODE
4. record diffs; stabilize typed selector output; legacy dict produced FROM typed objects
5. flip authority only after corpus parity
```

This buys insight (and the spec artifact) before any risky migration.

### Spec Extraction Yield as a tracked metric

Alongside runtime-saved / coverage / bugs-fixed, track: new typed object discovered,
hidden grammar named, string sentinel eliminated, ambiguity policy made explicit,
residual category created, cross-jurisdiction analogue found, test generator enabled.
A rewrite with neutral runtime but high spec yield is worth doing. Rough calls:
- simple label regex: low runtime payoff, low spec yield -> do not rewrite
- UK substitution pile: medium/high runtime, VERY high spec yield -> rewrite/specify
- TEXT_* sentinels: low/medium runtime, VERY high spec yield -> do it
- normalization regexes: low runtime, low/medium spec yield -> mostly do not

### The through-line (standing rule)

```
Do not let surface notation masquerade as the semantic object.
```
- regex pile: surface phrases pretending to be operations
- LawVM: human amendment text compiled into typed legal operations
- MeVM: institutional prose/plans forced into typed mechanism objects

Regex is fine as notation for a local lexical fact. Regex is wrong when it becomes
the ONLY place the operation language exists. Spec extraction is not incidental
cleanup here — it is part of LawVM's core research yield (the formal operational
grammar of legislation, made explicit).
