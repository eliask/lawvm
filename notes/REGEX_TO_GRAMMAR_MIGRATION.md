# Regex → Grammar Migration Roadmap

Status: living roadmap. Synthesizes a codebase-grounded ChatGPT Pro review
(2026-05-29) with the in-tree regex-grammar census (`.tmp/regex_grammar_census.md`).
Governs how AGENTS.md §1.13 (regex-versus-recognizer) is applied across frontends.

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
   docstrings literally say "FALLBACK: remove when PEG3 handles X"; `peg3.py`
   exists. Lowest-risk recognizer win. (Census rank 1.)
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

## Cross-frontend address grammar — DEFER (Sensor N vs Pro reconciliation)

The census flagged §-reference / legal-address extraction as reimplemented in
FI/EE/NO/SE with the same grammar, different surface tokens, and called shared
`legal_address_grammar` the highest-leverage consolidation. Pro structured
per-frontend and did NOT push cross-frontend unification.

**Resolution: Pro's ordering wins, per AGENTS.md §12** (do not put jurisdiction
idioms in core until proven genuinely shared). Sensor N correctly SPOTTED the
shared pattern, but unifying into core now is premature. Path: build per-frontend
typed recognizers first; let the shared shape emerge from 2-3 real
implementations; extract the core `legal_address_grammar` only after (farchive
model — earn independence). `LegalAddress` is already core; the parser need not
be until proven.

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
