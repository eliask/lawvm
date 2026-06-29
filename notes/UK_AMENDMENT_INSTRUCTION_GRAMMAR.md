> **Status (2026-06-30):** New / provisional. Kind: Spec-extraction (names the
> kind-3 hidden grammar in `src/lawvm/uk_legislation/nlp_parser.py`) plus a §2.5
> retirement plan for the regex transducer it specifies. Faithful extraction with
> file:line citations — not aspirational. Where the code's behavior is ambiguous
> this doc says so rather than inventing a rule. References: AGENTS.md
> §2.4/§2.5; `notes/REGEX_TO_GRAMMAR_MIGRATION.md` ("SPEC-FIRST ordering");
> `notes/DEFERRED_ROADMAP.md` B3; templated on `notes/FINLAND_CLAUSE_AST_SPEC.md`
> and `notes/FI_JOHTOLAUSE_GRAMMAR_RETIREMENT_PLAN.md`.

# UK Amendment-Instruction Grammar

This is the SPEC-FIRST first deliverable for the UK amendment-instruction hidden
grammar (AGENTS.md §2.5; `notes/REGEX_TO_GRAMMAR_MIGRATION.md` "SPEC-FIRST
ordering"; `notes/DEFERRED_ROADMAP.md` B3). It is a specification, not a
recognizer. Its job is to **name** what `src/lawvm/uk_legislation/nlp_parser.py`
already does: the productions, the typed selector algebra, the precedence /
span-overlap policy, the residual classes, and the lowering from a source phrase
to a LawVM text operation.

## 1. Purpose & scope

UK legislation carries its amendment instructions as *source text* — drafting
phrases like `for "X" substitute "Y"`, `after "A" insert "B"`,
`words from "P" to "Q" are omitted`. `nlp_parser.py` is the **grammar boundary**
that recognizes this small drafting-instruction language and lowers it to legacy
fragment-substitution dictionaries `{original, replacement, occurrence, rule_id,
...}` (`nlp_parser.py:1-8`).

Per AGENTS.md §2.4 this is a **kind-3** classification: not "does pattern P
appear" (a predicate → keep regex) but **a family of related patterns** whose
captures are legal-domain objects, whose match spans overlap and need conflict
resolution, and whose recognizer order changes meaning. AGENTS.md §2.4 names the
deliverable directly: "Naming the hidden grammar is itself a deliverable — a
rewrite with neutral runtime but high specification yield is still worth doing."
`notes/REGEX_TO_GRAMMAR_MIGRATION.md` characterizes the pile precisely as:

> an untyped string-to-operation transducer
>   + a precedence/conflict policy
>   + a span-overlap suppression policy
>   + a legacy dict/string-sentinel output encoding

i.e. a **surface-to-operation lowering language**. The output is not
match/no-match; it is the lowered operation.

Per AGENTS.md §2.5, an unmanaged second recognizer for an existing family is an
audit state, not a feature. The eventual single canonical parser per family is
the §2.5 target; this doc is step 1 of that ordering (spec, then typed objects,
then shadow grammar, then flip — §6).

**In scope.** The cached pure parse `nlp_parser.py:_parse_fragment_substitution_cached`
(`nlp_parser.py:5257`) and its public surfaces
`parse_fragment_substitution` (`nlp_parser.py:956`, legacy-dict view) and
`parse_fragment_substitution_typed` (`nlp_parser.py:966`, typed-fragment view).
The five named families are the module-docstring grammar (`nlp_parser.py:13-36`):
Substitution / Insertion / Omission (repeal) / Range / Definition, plus an
**occurrence-scope** cross-cutting dimension (`nlp_parser.py:38-43`).

**Out of scope of this doc** (named, not specified here): the downstream
*lowering* of a fragment dict to a structural/text op (`effect_table_lowering.py`,
`execution_authorization.py`, `repeal_semantics_witnesses.py`), and the
**dynamically-constructed** rule-id families (`uk_execution_authorization_*`,
`uk_repeal_semantics_*`, `<base>_unresolved`) enumerated in
`spec_ledger_uk_catalog.py:42-54`. This doc covers only the *statically declared*
`uk_effect_*` rule_ids the parser emits.

## 2. Production rules (the five families)

### 2.0 Recognition pipeline

`_parse_fragment_substitution_cached` (`nlp_parser.py:5257`) is an
`lru_cache(maxsize=8192)` pure function. Its shape:

1. **Verb prefilter** (`nlp_parser.py:5274-5289`): unless one of
   `substitut / insert / omit / replac / become / repeal / cease / add /
   include` appears, return `()` immediately. This is the catastrophic-
   backtracking guard for quoted-string-heavy tax tables.
2. **Normalization**: `normalize_uk_parser_text` (`nlp_parser.py:5293`).
3. **Cued dispatch into four recognizer banks** (`nlp_parser.py:5295-5321`),
   each gated by a substring cue so the bank is skipped on irrelevant text:
   - `_parse_leading_substitutions` (`nlp_parser.py:2786`) — cue `for ` /
     `replace … with` / `become`.
   - `_parse_respectively_and_anchored_inserts` (`nlp_parser.py:983`) — cue
     `for / after / from / at the end / before / substitute / replaced with`.
   - `_parse_trailing_inserts` (`nlp_parser.py:3023`) — cue
     `insert / include / add / replac / definition`.
   - `_parse_trailing_repeals_and_omissions` (`nlp_parser.py:4580`) — cue
     `omit / repeal / cease / revok / delete / leave out / definition`.
4. **Reversed-order fallback** (`nlp_parser.py:5328-5331`): only when nothing
   matched (`if not subs:`), `substitute X for "Y"` is recognized (requires the
   `for`-tail to start with a quote, to avoid splitting on a `for` inside a
   quoted payload). This fragment is emitted **without a `rule_id`** (see §3 on
   absent rule_ids).
5. **Compound-lettered marking**: `_mark_compound_lettered_text_patches`
   (`nlp_parser.py:914`) re-stamps certain fragments to
   `uk_effect_compound_lettered_text_patch_instruction`.
6. **Dedup + typing**: `_deduplicate_fragment_substitutions` (§4) then
   `fragment_from_legacy_dict` per surviving dict → a tuple of typed
   `UKTextRewriteFragment` (`nlp_parser.py:5333-5336`).

Each recognizer within a bank is a `re.finditer` / `re.search` pass that appends
a legacy dict to `subs`. There are 44 raw `re.compile` sites (allowlisted in
`tests/test_regex_perf_gate.py`; `notes/DEFERRED_ROADMAP.md` B3).

### 2.1 Family assignment of the 181 `uk_effect_*` rule_ids

The parser emits **181 distinct statically-declared `uk_effect_*` rule_id
strings** (the `witness_rule_id` an op ends up carrying). The five families
**overlap by construction** — a single instruction can be range + substitution +
definition at once (`from "X" to the end of the definition of "Y" substitute …`).
The grouping below is therefore a *primary-family* assignment using a documented
precedence (Definition > Omission/Repeal > Range > Insertion > Substitution) so
each id appears exactly once; the cross-cutting nature is real and is the reason
the families are not a partition of the *grammar*, only of this enumeration.
Counts: Substitution 47, Insertion 54, Omission/Repeal 30, Range 27,
Definition 21, Other 2 → 181.

The one-line `believed_spec` for each id (its falsifiable claim about UK
legislative semantics) is maintained in `tools/spec_ledger_uk_catalog.py`
(`_UK_RULE_SPECS`); this doc groups, that catalog defines. Where an id below is
absent from that catalog it is noted; the catalog's coverage guard is
`tests/test_spec_ledger_uk_catalog.py`.

#### Substitution — `for Selector substitute Payload` / `replace … with …` (47)

Module grammar (`nlp_parser.py:13-19`): `for Selector substitute Payload` /
`for Selector there is/are substituted Payload` / `replace Selector with Payload`
/ `Selector is/are replaced with Payload`, in quoted / block / passive /
child-qualified / mixed body+heading variants.

```
uk_effect_after_anchor_before_final_word_substitution_text_patch
uk_effect_all_occurrences_substitution_text_patch
uk_effect_alternate_preimage_substitution_text_patch
uk_effect_amount_specified_substitution_text_patch
uk_effect_bare_quoted_substitution_text_patch
uk_effect_before_child_block_text_substitution_patch
uk_effect_before_child_text_substitution_patch
uk_effect_both_subsequent_occurrences_substitution_text_patch
uk_effect_child_qualified_quoted_substitution_text_patch
uk_effect_dangling_active_substitution_quote_text_patch
uk_effect_dangling_passive_substitution_quote_text_patch
uk_effect_except_child_substitution_text_patch
uk_effect_except_phrase_substitution_text_patch
uk_effect_first_second_occurrence_substitution_text_patch
uk_effect_imperative_replace_reference_substitution_text_patch
uk_effect_imperative_replace_with_substitution_text_patch
uk_effect_missing_space_there_is_substituted_text_patch
uk_effect_mixed_body_heading_all_occurrences_substitution_text_patch
uk_effect_mixed_body_heading_substitution_split_text_patch
uk_effect_multi_wherever_occurring_substitution_text_patch
uk_effect_nested_quote_substitution_text_patch
uk_effect_opening_words_substitution_text_patch
uk_effect_ordinal_substitution_text_patch
uk_effect_parenthesized_nested_quote_substitution_text_patch
uk_effect_passive_quoted_substitution_text_patch
uk_effect_post_quoted_ordinal_substitution_text_patch
uk_effect_post_quoted_where_ordinal_substitution_text_patch
uk_effect_preposed_passive_substitution_text_patch
uk_effect_proviso_child_substitution_text_patch
uk_effect_quoted_anchor_block_substitution_text_patch
uk_effect_quoted_substitute_dash_quoted_payload_text_patch
uk_effect_quoted_substitution_scope_note_text_patch
uk_effect_quoted_word_ordinal_places_substitution_text_patch
uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch
uk_effect_reference_to_substitution_text_patch
uk_effect_referent_qualified_substitution_text_patch
uk_effect_respectively_all_occurrences_substitution_text_patch
uk_effect_sibling_first_then_each_other_place_substitution_text_patch
uk_effect_target_qualified_passive_substitution_text_patch
uk_effect_unquoted_all_occurrences_substitution_text_patch
uk_effect_unquoted_anchor_quoted_substitution_text_patch
uk_effect_varied_by_substituting_text_patch
uk_effect_wherever_appearing_substitution_text_patch
uk_effect_wherever_occurring_substitution_text_patch
uk_effect_wherever_they_occur_substitution_text_patch
uk_effect_words_before_quoted_anchor_substitution_text_patch
uk_effect_words_in_brackets_substitution_text_patch
```

Note the occurrence-scope-carrying members (`*_all_occurrences_*`,
`*_wherever_*`, `*_ordinal_*`, `*_where_ordinal_*`, `*_both_subsequent_*`,
`*_respectively_*`, `*_first_second_*`, `*_sibling_first_then_*`) — these encode
the occurrence dimension of §3 *within* the substitution family rather than as a
separate op.

#### Insertion — `after/before Selector insert Payload`, `at the end/beginning insert Payload` (54)

Module grammar (`nlp_parser.py:21-24`): `after Selector insert Payload` /
`before Selector insert Payload` / `at the beginning / at the end insert
Payload`.

```
uk_effect_after_child_text_insertion_patch
uk_effect_after_compound_subsection_child_text_insertion_patch
uk_effect_after_ordinal_paragraph_text_insertion_patch
uk_effect_after_parenthesized_anchor_insert_text_patch
uk_effect_after_prefixed_quoted_anchor_ordinal_insert_text_patch
uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch
uk_effect_after_quoted_anchor_block_insert_text_patch
uk_effect_after_quoted_anchor_closing_quote_insert_text_patch
uk_effect_after_quoted_anchor_dangling_insert_quote_text_patch
uk_effect_after_quoted_anchor_each_occasion_insert_text_patch
uk_effect_after_quoted_anchor_each_other_place_insert_text_patch
uk_effect_after_quoted_anchor_except_child_insert_text_patch
uk_effect_after_quoted_anchor_insert_text_patch
uk_effect_after_quoted_anchor_last_occurrence_insert_text_patch
uk_effect_after_quoted_anchor_ordinal_block_insert_text_patch
uk_effect_after_quoted_anchor_ordinal_insert_text_patch
uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch
uk_effect_after_quoted_anchor_space_before_comma_insert_text_patch
uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch
uk_effect_after_quoted_anchor_where_ordinal_nested_quote_insert_text_patch
uk_effect_after_reference_section_insert_text_patch
uk_effect_after_words_in_brackets_insert_text_patch
uk_effect_at_end_carried_parent_context_text_insertion_patch
uk_effect_at_end_dangling_insert_quote_text_patch
uk_effect_at_end_not_as_part_text_insertion_patch
uk_effect_at_end_quoted_dash_text_insertion_patch
uk_effect_at_end_step_insert_text_patch
uk_effect_at_end_stray_full_stop_insert_text_patch
uk_effect_at_end_text_insertion_patch
uk_effect_at_end_unquoted_text_insertion_patch
uk_effect_at_end_words_in_parentheses_insert_text_patch
uk_effect_bare_quoted_anchor_insert_text_patch
uk_effect_before_dangling_nested_quoted_anchor_insert_text_patch
uk_effect_before_nested_quoted_anchor_block_insert_text_patch
uk_effect_before_nested_quoted_anchor_insert_text_patch
uk_effect_before_quoted_anchor_all_occurrences_insert_text_patch
uk_effect_before_quoted_anchor_insert_text_patch
uk_effect_before_quoted_anchor_nested_payload_insert_text_patch
uk_effect_before_quoted_anchor_ordinal_insert_text_patch
uk_effect_before_step_insert_text_patch
uk_effect_beginning_carried_parent_context_text_insertion_patch
uk_effect_beginning_text_insertion_patch
uk_effect_for_insert_text_insertion_patch
uk_effect_for_there_is_inserted_replacement_text_patch
uk_effect_immediately_before_word_insert_text_patch
uk_effect_immediately_before_word_ordinal_insert_text_patch
uk_effect_insert_text_at_end_patch
uk_effect_leave_out_and_insert_text_patch
uk_effect_opening_words_after_quoted_anchor_insert_text_patch
uk_effect_passive_before_quoted_anchor_insert_text_patch
uk_effect_passive_insert_text_at_end_patch
uk_effect_preposed_at_end_text_insertion_patch
uk_effect_preposed_beginning_text_insertion_patch
uk_effect_word_inserted_after_word_where_ordinal_text_patch
```

#### Omission / Repeal — `omit Selector`, `Selector is omitted / repealed / ceases to have effect` (30)

Module grammar (`nlp_parser.py:26-28`): `omit Selector` / `Selector is/are
omitted / repealed / cease(s) to have effect`. `leave out` is also a recognized
omission cue (`nlp_parser.py:5319`), though `leave out … and insert …` lands
under Insertion (`uk_effect_leave_out_and_insert_text_patch`).

```
uk_effect_after_anchor_to_end_omission_text_patch
uk_effect_all_occurrences_word_repeal_text_patch
uk_effect_cease_effect_quoted_word_repeal_text_patch
uk_effect_cease_effect_range_to_end_repeal_text_patch
uk_effect_contextual_adjacent_word_omit_text_patch
uk_effect_contextual_adjacent_word_repeal_text_patch
uk_effect_contextual_nested_word_repeal_text_patch
uk_effect_direct_quoted_word_omission_text_patch
uk_effect_final_bare_quoted_word_repeal_text_patch
uk_effect_final_quoted_word_omit_text_patch
uk_effect_final_quoted_word_repeal_text_patch
uk_effect_from_beginning_omission_text_patch
uk_effect_listed_word_and_range_to_end_repeal_text_patch
uk_effect_multi_quoted_word_repeal_text_patches
uk_effect_omit_paragraph_fragment_patch
uk_effect_omit_quoted_range_text_patch
uk_effect_opening_words_omission_text_patch
uk_effect_ordinal_paragraph_repeal_text_patch
uk_effect_ordinal_sentence_repeal_text_patch
uk_effect_ordinal_word_repeal_text_patch
uk_effect_quoted_word_passive_omit_text_patch
uk_effect_range_independent_end_occurrence_repeal_text_patch
uk_effect_range_occurrence_repeal_text_patch
uk_effect_range_repeal_pre_predicate_comma_text_patch
uk_effect_range_repeal_text_patch
uk_effect_range_to_end_passive_ordinal_repeal_text_patch
uk_effect_range_to_end_passive_repeal_text_patch
uk_effect_repeal_quoted_words_text_patch
uk_effect_section_reference_repeal_text_patch
uk_effect_unquoted_type_label_repeal_text_patch
```

Several range-flavoured repeals (`*_range_repeal_*`, `*_range_to_end_passive_*`,
`*_range_occurrence_repeal_*`) are assigned to Omission here because the *operation*
is the deletion; the range is the selector. They co-belong to Range.

#### Range — `from Anchor to Anchor`, `from Anchor to the end`, `after/following Anchor to the end` (27)

Module grammar (`nlp_parser.py:30-33`): `from Anchor to Anchor` / `from Anchor to
the end` / `after/following Anchor to the end`. (When the operation is a deletion
the id lands in Omission above; the entries below are range *substitutions* and
the range primitives.)

```
uk_effect_after_anchor_to_end_passive_substitution_text_patch
uk_effect_after_anchor_to_end_substitution_text_patch
uk_effect_after_anchor_to_end_unquoted_substitution_text_patch
uk_effect_anchor_to_end_block_substitution_text_patch
uk_effect_anchor_to_end_substitution_text_patch
uk_effect_bare_range_unquoted_substitution_text_patch
uk_effect_from_beginning_block_substitution_text_patch
uk_effect_from_beginning_end_anchor_occurrence_substitution_text_patch
uk_effect_from_beginning_passive_substitution_text_patch
uk_effect_labeled_end_range_substitution_text_patch
uk_effect_ordinal_paragraph_range_substitution_text_patch
uk_effect_paragraphs_range_substitution_text_patch
uk_effect_quoted_anchor_to_end_block_substitution_text_patch
uk_effect_quoted_words_anchor_to_end_substitution_text_patch
uk_effect_range_independent_end_occurrence_substitution_text_patch
uk_effect_range_occurrence_substitution_text_patch
uk_effect_range_substitution_text_patch
uk_effect_range_to_end_bare_quoted_substitution_text_patch
uk_effect_range_to_end_missing_the_substitution_text_patch
uk_effect_range_to_end_open_quote_block_substitution_text_patch
uk_effect_range_to_end_ordinal_block_substitution_text_patch
uk_effect_range_to_end_parenthetical_occurrence_substitution_text_patch
uk_effect_range_to_end_quoted_dash_substitution_text_patch
uk_effect_range_to_end_there_is_substituted_text_patch
uk_effect_range_unquoted_substitution_text_patch
uk_effect_range_where_ordinal_substitution_text_patch
uk_effect_same_anchor_adjacent_occurrence_range_substitution_text_patch
```

The range *selectors* (`RangeFromToSelector`, `RangeToEndSelector`,
`AfterAnchorToEndSelector`, `AfterAnchorBeforeFinalWordSelector`) are the typed
home of this family — §3.

#### Definition — `in/after/before the definition of Term …`, definition child/entry rewrites (21)

Module grammar (`nlp_parser.py:34-36`): `in / after / before the definition of
Term …` and definition-child / definition-entry rewrites. This family overlaps
all four operations (it is "do <op> *inside a definition*"); it is kept distinct
because the **selector context** (a definition list / definition entry) is the
discriminating fact and `DefinitionAnchorSelector` is its typed home.

```
uk_effect_after_definition_child_text_insertion_patch
uk_effect_after_definition_text_insertion_patch
uk_effect_after_definitions_text_insertion_patch
uk_effect_after_quoted_anchor_definition_entry_block_insert_text_patch
uk_effect_before_definition_text_insertion_patch
uk_effect_definition_child_and_tail_substitution_text_patch
uk_effect_definition_child_repeal_text_patch
uk_effect_definition_child_substitution_text_patch
uk_effect_definition_child_tail_after_anchor_to_end_text_patch
uk_effect_definition_entry_repeal_text_patch
uk_effect_definition_entry_substitution_text_patch
uk_effect_definition_range_to_end_occurrence_substitution_text_patch
uk_effect_definition_range_to_end_substitution_text_patch
uk_effect_in_definition_after_anchor_add_text_patch
uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch
uk_effect_in_definition_after_anchor_insert_text_patch
uk_effect_in_definition_after_paragraphs_insert_text_patch
uk_effect_in_definition_at_end_insert_text_patch
uk_effect_in_definition_at_end_target_context_insert_text_patch
uk_effect_in_definition_child_before_anchor_insert_text_patch
uk_effect_unquoted_definition_range_to_end_substitution_text_patch
```

#### Other (2)

Two ids do not sit cleanly under one operation family:

```
uk_effect_after_quoted_anchor_include_text_patch          # "after X … include …" — insert-shaped but cued by "include"
uk_effect_compound_lettered_text_patch_instruction        # a re-stamp marker, not a recognizer (see §4)
```

`uk_effect_compound_lettered_text_patch_instruction` is **not** produced by a
recognizer; it is applied post-hoc by `_mark_compound_lettered_text_patches`
(`nlp_parser.py:914-942`) to fragments whose original `rule_id` was empty,
`uk_effect_after_quoted_anchor_insert_text_patch`, or the passive-quoted
substitution id, when the source is a single paragraph carrying lettered sibling
text patches.

### 2.2 Ambiguities flagged (not invented)

- The five families are **not disjoint** in the grammar. The §2.1 grouping is an
  enumeration convenience, not a claim that an instruction belongs to exactly one
  family. A faithful grammar must model the cross-product (operation × selector
  context × occurrence scope), not five flat lists.
- The boundary between Range and Omission/Substitution is the *operation*, not
  the selector; the same `from … to …` selector appears in all three. This doc
  assigned by operation precedence; the code does not declare a canonical owner.
- `uk_effect_after_quoted_anchor_include_text_patch` is cued by `include` but is
  insert-shaped; its family is genuinely under-determined by the code.

## 3. Typed selector algebra

`src/lawvm/uk_legislation/text_selectors.py` already carries the typed output
that a grammar rewrite targets. Two typed objects:

- **`UKTextSelector`** (`text_selectors.py:214-234`) — a tagged union of selector
  dataclasses: which *region* of a provision an instruction names. Members:
  `LiteralSelector`, `RangeFromToSelector`, `RangeToEndSelector`,
  `AfterAnchorToEndSelector`, `AfterAnchorBeforeFinalWordSelector`,
  `OpeningWordsSelector`, `OpeningWordsAfterAnchorSelector`, `BeginningSelector`,
  `EndSelector`, `BeforeChildSelector`, `AfterChildSelector`,
  `DefinitionAnchorSelector`, `FromChildEndSelector`, `ExceptPhraseSelector`,
  `ExceptChildSelector`, `ExceptSourceSiblingOccurrenceSelector`,
  `ReferentQualifiedSelector`, `NegativeLeftContextExceptChildrenSelector`, and
  the not-yet-migrated catch-all `RawSelector` (`text_selectors.py:50-211`).
- **`UKTextRewriteFragment`** (`text_selectors.py:242-260`) — the typed rewrite:
  `selector: UKTextSelector`, `replacement`, `rule_id` (kept as *provenance* —
  how the fragment was recognized), `occurrence`, `end_occurrence`, and the
  source-child / target-suffix / tail-connector carriers.

**`rule_id` is provenance, the selector type is meaning.** The migration policy
(`text_selectors.py:21-29`) is explicit: consumers should branch on the selector
*type*, not on `rule_id`. The 181 rule_ids of §2 are recognition provenance; the
~19 selector types are the semantic vocabulary they lower into.

### 3.1 The `TEXT_*` sentinel selector language

Historically the selector lived inside the single `original` string field, two
ways (`text_selectors.py:5-19`; `nlp_parser.py:44-52`):

- ordinary quoted text, e.g. `original="the words"` → a `LiteralSelector`;
- a hidden **sentinel language**: symbolic strings constructed in `nlp_parser.py`,
  carried through lowering, and re-parsed in replay — e.g.
  `TEXT_FROM_X_TO_END`, `TEXT_AFTER_<anchor>_TO_END`, `TEXT_OPENING_WORDS`,
  `TEXT_BEGINNING`, `TEXT_END`, `TEXT_BEFORE_CHILD_<kind>_<label>`,
  `TEXT_DEFINITION_CHILD_*`, `TEXT_TABLE_CELL_PARAGRAPH_*`, and `US`-separated
  (ASCII Unit Separator `\x1f`) multi-field sentinels like
  `TEXT_AFTER_ANCHOR_BEFORE_FINAL_WORD\x1f<anchor>\x1f<word>`.

That is a **stringly-typed IR**: the selector semantics exist only as the shape
of a string. `text_selectors.py` makes the selection a typed object so the type
checker, not a downstream regex, enforces its structure
(`text_selectors.py:30-32`).

### 3.2 Legacy-dict-as-serialization relationship

The typed surface is **total** and **round-trips** to the legacy dict:

- `selector_to_legacy_original` (`text_selectors.py:268-327`) emits the exact
  sentinel strings the parser builds, so a migrated production is byte-identical.
- `selector_from_legacy_original` (`text_selectors.py:330-420`) is its inverse:
  `selector_to_legacy_original(selector_from_legacy_original(s)) == s` for all
  `s`. An unrecognized `TEXT_*` sentinel becomes a `RawSelector` (remaining
  migration debt; `text_selectors.py:198-211`); anything not starting with
  `TEXT_` becomes a `LiteralSelector`.
- `fragment_to_legacy_dict` / `fragment_from_legacy_dict`
  (`text_selectors.py:445-496`) round-trip the whole fragment; **absent optional
  keys stay absent** (the serializer omits empty fields), so the dict the parser
  produces and the dict reconstructed from the typed fragment are byte-identical.

Hence `parse_fragment_substitution(t)` (dict view, `nlp_parser.py:956`) is
defined as the serialization of `parse_fragment_substitution_typed(t)` (typed
view, `nlp_parser.py:966`): the cached parse *is* the tuple of typed fragments,
and the dict API is its serialization, not the other way round
(`nlp_parser.py:5333-5336`, 977-980).

### 3.3 Occurrence scope (the `occurrence` field)

A cross-cutting dimension (`nlp_parser.py:38-43`), carried as a string on the
fragment so serialization is byte-identical (`text_selectors.py:247-249`):
`""` = single / omitted, `"-1"` = all occurrences, ordinal `"1".."5"` for
first..fifth. Ordinal words are mapped by `_ORDINAL_OCCURRENCES`
(`nlp_parser.py:88-104`); the all-occurrences signal (`wherever`, `in each/both
places`) by `_RESPECTIVELY_ALL_OCCURRENCES_SIGNAL_RE` (`nlp_parser.py:113-116`).

## 4. Precedence & span-overlap policy

The recognizer order and overlap suppression are **part of the semantics**
(`nlp_parser.py:53-55`): "Do NOT reorder recognizers unless parity tests show no
corpus-visible change."

- **Bank order is precedence.** The four banks run in the fixed order of §2.0
  (leading-substitutions → respectively/anchored-inserts → trailing-inserts →
  trailing-repeals/omissions), each appending to a shared `subs` list. Earlier
  banks win positions; later banks see what earlier ones produced. The
  reversed-order substitution fallback runs **only if `subs` is still empty**
  (`nlp_parser.py:5328`).

- **`_span_overlaps(span, blocked_spans)`** (`nlp_parser.py:844-846`): returns
  True iff the candidate `(start, end)` overlaps any already-claimed span
  (half-open interval test `start < blocked_end and blocked_start < end`).
  Recognizers consult it to **suppress a rival match that overlaps an
  already-claimed span** — e.g. a generic substitution match is skipped when its
  span overlaps a `respectively`-list span or an all-occurrences-multi span
  (`nlp_parser.py:1169, 1377, 1396, 1450, 2682, 2710, 2726, 2739, 2757, 2775`).
  The blocked-span lists are built by the higher-priority recognizer before the
  lower-priority pass scans.

- **`_deduplicate_fragment_substitutions(subs)`** (`nlp_parser.py:873-906`), run
  last before typing, does two things:
  1. **Definition-child vs omit-paragraph suppression**: collects definition-child
     paragraph labels from `TEXT_DEFINITION_CHILD_PARAGRAPH_*` sentinels, then
     drops any `TEXT_OMIT_PARAGRAPH_<label>` fragment whose label is already
     claimed by a definition-child fragment (the definition rewrite owns the
     paragraph; the bare omit is the duplicate).
  2. **Key-dedup with rule_id preference**: dedups on the key
     `(original, replacement, occurrence)`; on a collision it **keeps the first
     occurrence but upgrades it** to a copy that carries a `rule_id` if the
     duplicate has one and the kept copy did not (`nlp_parser.py:904-905`). So a
     later, better-attributed match can donate its `rule_id` to an earlier
     rule_id-less fragment without changing position.

- **Compound-lettered re-stamp** (`_mark_compound_lettered_text_patches`,
  `nlp_parser.py:914-942`): when the source is one paragraph carrying lettered
  sibling patches, fragments with empty / after-quoted-anchor-insert / passive-
  quoted-substitution rule_ids are re-stamped to
  `uk_effect_compound_lettered_text_patch_instruction`; a
  `for_there_is_inserted_replacement` fragment that itself carries a quote is
  *dropped* (`nlp_parser.py:924-928`).

**Ambiguity flagged.** The precedence is *implicit in source order and shared
`blocked_spans` lists*, not declared in one table. A faithful grammar rewrite
must reconstruct it from the call order and the blocked-span plumbing; this doc
asserts only the mechanism (overlap-suppress + order-wins + dedup-with-rule_id-
upgrade), not a complete per-pair priority matrix, because the code does not
state one.

## 5. Residual classes

Per AGENTS.md §2.5, unparsed input must become a **registered typed residual
class — never a silent drop or guessed fallback.** What the *current* parser does
with input it does not lower:

1. **Verb-prefilter miss** → `()` (empty tuple). Input with none of the operative
   verbs is declined cleanly (`nlp_parser.py:5275-5289`). This is a *typed empty
   result*, not a silent mutation: the caller gets "no fragments", and the effect
   is then routed by the lowering layer to its own residual handling (the
   `uk_manual_frontier_*` classification family, `spec_ledger_uk_catalog.py:671-693`,
   labels *why* an effect was not lowered deterministically — `…unclassified`,
   `…unsupported_effect_family`, `…source_pathology_insufficient`,
   `…missing_payload_source_insufficient`, etc.). The manual-frontier family is
   the registered residual class at the *lowering* boundary.
2. **`RawSelector`** (`text_selectors.py:198-211`) is the registered residual at
   the *selector-typing* boundary: a `TEXT_*` sentinel with no typed selector yet
   is wrapped verbatim so the typed surface stays **total** — every legacy
   `original` round-trips — without forcing every family to migrate at once. A
   shrinking `RawSelector` count is the remaining migration debt; a consumer must
   **never branch on a `RawSelector`'s inner string** (that would re-introduce the
   stringly-typed IR).
3. **Reversed-order fallback** (`nlp_parser.py:5328-5331`) is the one
   guess-adjacent path; it is fenced (`if not subs`, requires a quoted tail) and
   emits a `rule_id`-less fragment, which §4 dedup may later upgrade.

**Gap flagged for the future grammar.** The current parser does **not** emit a
single *typed residual object for an in-scope-but-unrecognized instruction* at
the parse boundary — it emits an empty/partial fragment list and relies on the
downstream lowering's `uk_manual_frontier_*` classification to register the
miss. A §2.5-clean grammar should raise a typed parse-side residual
(analogous to the FI `SurfaceParsingDiagnostic`) rather than leaning on the
lowering layer to notice the absence. This is named here, not built.

## 6. §2.5 retirement plan

This mirrors `notes/FI_JOHTOLAUSE_GRAMMAR_RETIREMENT_PLAN.md`. The regex
transducer in `nlp_parser.py` is the **incumbent AUTHORITY**; a future grammar
recognizer runs **SHADOW** until corpus parity. The SPEC-FIRST ordering
(`notes/REGEX_TO_GRAMMAR_MIGRATION.md`) is:

```
1. write UK_AMENDMENT_INSTRUCTION_GRAMMAR.md   ← THIS document (done)
2. introduce typed UKTextSelector / UKTextRewriteFragment   ← already exist
3. old regex parser remains AUTHORITY; new grammar runs in SHADOW MODE
4. record diffs; stabilize typed selector output; legacy dict produced FROM typed
5. flip authority only after corpus parity
```

Step 2 is already met: `text_selectors.py` exists and the cached parse is already
typed-fragment-native, serializing to the legacy dict (§3.2). Steps 3–5 remain.

### 6.1 Lane declaration (the sanctioned §2.5 exception)

- **AUTHORITY (unchanged):** `nlp_parser._parse_fragment_substitution_cached`
  (`nlp_parser.py:5257`) and its public surfaces remain the sole replay
  authority. Every downstream lowering keeps consuming the legacy dict.
- **SHADOW (to build):** a grammar recognizer over the same `believed_spec`
  catalog, **no replay authority** (observation-only), producing
  `UKTextRewriteFragment` tuples compared against the incumbent's typed output.
- **Parity criterion:** `parse_fragment_substitution_typed(t)` from the grammar
  must equal the incumbent's for every `t` in the UK conformance corpus —
  fragment-tuple equality (selector, replacement, occurrence, all carriers, AND
  `rule_id` provenance). Because the dict view is the serialization of the typed
  view (§3.2), typed-tuple parity ⇒ dict parity ⇒ byte-identical downstream.

### 6.2 Promotion criteria (flip authority)

The grammar may become authority when **all** hold:

1. **Typed-fragment parity == 100%** on the UK conformance corpus
   (fragment-tuple equality including `rule_id`). Any divergence is silent
   evidence loss, not parity (AGENTS.md §0 firewall).
2. **`RawSelector` residual is enumerated, not growing** — every `TEXT_*`
   sentinel the parser still emits either has a typed selector or is an explicitly
   listed residual class (§5.2). An undocumented sentinel blocks the flip.
3. **`spec_ledger_uk_catalog.py` coverage guard green** — every statically
   declared `uk_effect_*` id remains cataloged
   (`tests/test_spec_ledger_uk_catalog.py`); the grammar introduces no
   uncatalogued id.
4. **No regression in the UK replay-vs-oracle suite** at the score the incumbent
   holds. The deletion is replay-neutral by construction (the grammar produces
   the same legacy dicts), and the oracle suite is the standing guard.
5. **The precedence / span-overlap policy of §4 is reproduced**, not approximated
   — bank order, blocked-span suppression, and dedup-with-rule_id-upgrade must be
   modeled explicitly in the grammar, because order *is* meaning here.

### 6.3 Regex-deletion plan

When the promotion criteria hold:

1. Route `_parse_fragment_substitution_cached` through the grammar; keep the
   regex bank as `legacy_reference_fallback` only for enumerated `RawSelector` /
   manual-frontier residual families.
2. Drive the OutOfScope-style decline rate (fragments the grammar cannot type
   that the regex could) to ≤ a published floor for two release lines, with every
   decline associated to an enumerated residual class (§5).
3. Delete the 44 raw `re.compile` recognizer sites (allowlisted in
   `tests/test_regex_perf_gate.py::_KNOWN_UNFIXED`; `notes/DEFERRED_ROADMAP.md`
   B3) once each family they own is grammar-covered, removing the allowlist
   entries in lockstep.
4. The grammar becomes the **single canonical parser per family** (§2.5).

### 6.4 Anti-patterns that block retirement

(Mirroring `FI_JOHTOLAUSE_GRAMMAR_RETIREMENT_PLAN.md`.) Retirement MUST NOT
proceed if any holds, regardless of an aggregate metric:

- The grammar diverges from the incumbent on *any* fragment's `rule_id` (the 181
  witness ids are evidence; divergence is silent evidence loss).
- The grammar accepts an instruction shape the incumbent rejected (silent
  authority bleed, AGENTS.md §0) — e.g. lowers a verb-prefilter-miss to a
  non-empty fragment.
- The span-overlap / dedup policy is approximated rather than reproduced, so a
  rival-match suppression flips on some corpus input.
- A `RawSelector` family is silently dropped instead of carried verbatim.

## 7. Known doc-drift to fix later (NOTE only — not fixed in this pass)

`nlp_parser.py:57` cites **"AGENTS.md §1.11/§1.13"**. AGENTS.md has no §1.13;
the intended references are **§1.11 / §1.12**. This is a stale docstring cite to
correct in a later pass (deferred per the hard constraint that this deliverable
touch only this new doc). Recorded here so the drift is registered rather than
silently carried.
