> **Status (2026-06-27):** New / provisional. Kind: Normative (closing the §2.5 retirement-plan gap for the sanctioned johtolause grammar shadow lane). References: AGENTS.md §2.5; `notes/REGEX_TO_GRAMMAR_MIGRATION.md` §76 ranked-target-2; `notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md`; `notes/CONFORMANCE_CORPUS.md`.

# Finland Johtolause Grammar — Retirement Plan for `johtolause/surface_parse.py`

Status: normative. Per AGENTS.md §2.5, a sanctioned shadow/audit migration lane
is allowed only when it carries **all three** discipline properties:

1. **No replay authority** — the shadow lane is observation-only until
   promoted. Held: `src/lawvm/finland/parser_facade.py:ParserLane.GRAMMAR_SHADOW`
   routes the grammar parser only through the diff/curated-gate path; the
   production path (`parse_tokens_production`) consumes the grammar primary's
   typed `SurfaceClause` and only **falls back to `surface_parse.parse`** on
   `OutOfScope`, stamping `parser_lane="legacy_reference_fallback"` (and
   `parser_lane="old_parser_forced"` when the env override forces it). The
   grammar never silently authors replay; an OutOfScope decline carries no
   semantic payload of its own.
2. **Explicit parity criteria** — held: `parser_facade.run_curated_shadow_gate`
   partitions the CONFORMANCE_CORPUS `CURATED_CASES` set into buckets
   `{zero_delta, declined, witness_span_normalized, delta, skipped}`;
   `tests/test_fi_parser_facade.py:test_curated_shadow_gate_no_structural_delta`
   pins `delta == 0` and `witness_span_normalized == 1` as the structural-parity
   gate the shadow lane must hold before promotion. The structural parity is
   also re-asserted through the downstream pipeline (resolve → lower → derive
   parsed ops) per `notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md`
   "Downstream parity".
3. **Deletion plan for the recognizer it replaces** — THIS document. Closes
   the gap recorded in the iter2 regex review (M3 — "missing retirement plan
   for the recogniser a sanctioned shadow-lane replaces").

## The recognizer being retired

`src/lawvm/finland/johtolause/surface_parse.py::parse(tokens, jolloin_renumber_pairs)`
is the incumbent canonical surface parser. The frozen observable contract it
must reproduce is `notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md`. The grammar
parser that supersedes it lives in `src/lawvm/finland/johtolause/grammar/`
(entry point `grammar.parser.parse`).

The shadow lane in `parser_facade.py::ParserFacade(lane=GRAMMAR_SHADOW)` exists
to prove parity of the grammar against the incumbent; promotion is gated by
the curated shadow gate and downstream tests, not by accumulator pressure.

## Production status today

`grammar_primary_enabled()` defaults to ON (`LAWVM_FI_NEW_PARSER=1`). The
production path is grammar-primary with `surface_parse.parse` as the
`legacy_reference_fallback`:

* `grammar_owned` — the grammar parser produced the typed `SurfaceClause`.
* `legacy_reference_fallback` — the grammar parser raised `OutOfScope`; the
  incumbent `surface_parse.parse` produced the typed `SurfaceClause` instead.
  The decline reason is carried on `ProductionParseResult.grammar_decline_reason`.
* `old_parser_forced` — `LAWVM_FI_NEW_PARSER=0` forced the incumbent; used only
  in localized diagnostic/A-B workflows.

So the incumbent is **already demoted to the OutOfScope-fallback role**. The
retirement step is the deletion of the fallback path itself, conditioned on
the OutOfScope decline rate reaching and holding zero (or close to zero with
the residual class enumerated).

## Promotion criterion (delete the fallback)

`johtolause/surface_parse.py` (and the `legacy_reference_fallback` /
`old_parser_forced` arms in `parser_facade.parse_tokens_production`) is
provisionally deletable when **all** of the following hold:

1. **Curated shadow gate is green** — `run_curated_shadow_gate` reports
   `delta == 0` and `witness_span_normalized` is stable (the existing
   structural-parity gate in `tests/test_fi_parser_facade.py`). The single
   known `witness_span_normalized` bucket represents a known replay-neutral
   class (per `compare_surface_models_structural`); additional entries require
   a classification before promotion.
2. **OutOfScope decline rate is at or below 0.5% on CONFORMANCE_CORPUS for
   two consecutive release lines.** Measured by routing the corpus through
   `parse_tokens_production` and counting `parser_lane ==
   "legacy_reference_fallback"`. Any non-zero rate MUST associate to an
   enumerated `grammar_decline_reason`; an undocumented decline reason blocks
   retirement (raise a typed `SurfaceParsingDiagnostic` on the grammar side
   and either add the production or keep the fallback for that family).
3. **No production-path observability relies on `surface_parse.parse`**: grep
   `src/lawvm/` for direct imports of `johtolause.surface_parse` (NOT through
   `parser_facade._surface_parse`) returns empty, and tests/fixtures that pin
   the incumbent's output are either deleted (after grammar parity lands) or
   explicitly relabeled as legacy-reference fixtures.
4. **No regression in the FI conformance corpus replay-vs-oracle suite** at
   the score that the grammar-primary path holds; the deletion is a
   no-op from the replay side by construction (the incumbent is not a replay
   authority; it is a parse authority), but the FI replay-vs-oracle gate is
   the standing guard that the grammar is producing source-faithful text
   that downstream replay consumes correctly.
5. **`FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md` is re-shaped from a
   compatibility contract to a historical-contract appendix** (the
   post-retirement role of that doc — see below).

## Provisional target retirement date / milestone

Provisional: **post-v0.2 release line** (the line after the v0.1 public
release). The exact cut is gated on the promotion criterion above; if the
OutOfScope decline rate does not reach the floor, the retirement is deferred
to v0.3, and the deferred state is recorded here. The retirement is **not**
tied to a calendar date — it is tied to the corpus-measured decline rate.

## Conditions under which `surface_parse.py` is deleted

When the promotion criterion holds, the retirement step is:

1. Remove `parser_facade._surface_parse`, `ParserLane.SURFACE_PARSE`, and the
   `legacy_reference_fallback` / `old_parser_forced` arms of
   `parse_tokens_production`. The production path becomes grammar-primary
   with no fallback; an `OutOfScope` decline becomes a typed
   `SurfaceParsingDiagnostic`/typed residual in line with §1.11/§1.12 (surface
   predicate routing into a typed parser owner; unparsed input is a
   registered typed residual, never a silent drop).
2. Delete `src/lawvm/finland/johtolause/surface_parse.py` AND its sole
   grammar shadow lane consumer plumbing (the `_surface_parse` indirection
   in `parser_facade.py`).
3. The `johtolause/grammar/parser.py` becomes the single canonical johtolause
   surface parser per §2.5 (one parser per family).
4. The `notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md` is rewritten as a
   "Historical Surface-Parser Contract Appendix" — preserved as the frozen
   observable contract the grammar parser was validated against, but marked
   HISTORICAL with the retirement SHA recorded.
5. `notes/REGEX_TO_GRAMMAR_MIGRATION.md` ranked-targets-8 ("Finland
   normalize.py fallback cluster → fold into `johtolause/surface_parse.py`")
   is also re-pointed: once `surface_parse.py` is deleted, the fold target
   becomes the grammar parser directly, and the migration cluster is
   closed.
6. Tests/fixtures that pinned the incumbent's behaviour are either deleted
   (when the grammar-parity fixture already covers the case) or migrated to
   a grammar-parity fixture. The `CONFORMANCE_CORPUS`'s `lawvm.finland.
   johtolause.test_peg_curated` legacy name (already reconciled to
   `curated_cases.py`) is reviewed for any remaining incumbent-only rites.

## Anti-patterns that block retirement

The retirement MUST NOT proceed if any of the following is true, regardless
of the decline-rate metric:

* The grammar parser produces a `SurfaceClause` that diverges from the
  incumbent's on the **34 witness rule_ids** enumeration
  (`notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md` §"The 34 witness
  rule_ids") — divergence here is silent evidence loss, not parity.
* The **150 statutes with a tail rule co-occurring with ≥10 ops** are not all
  green on the grammar-primary path — a rare-rule poisoning on a large clause
  is the worst failure class.
* The **784 0-op johtolauses** (legitimate zero-op shapes — archaic
  `Kumoten…`, `näin kuuluva`, statute-name-by-date targets) are not all
  reproduced as zero ops by the grammar — false compilation is worse than
  non-compilation.
* The grammar accepts an ambiguous shape that the incumbent rejected — silent
  acceptance of new shape is a **silent authority bleed**, not parity
  (AGENTS.md §0).

## Reference pointers

* **AGENTS.md §2.5** — the retirement-plan requirement this doc closes.
* **`notes/REGEX_TO_GRAMMAR_MIGRATION.md` §76** ranked-target-2
  ("`normalize.py` fallback cluster → the canonical johtolause grammar") —
  the cluster this retirement unblocks; the surface_parse.py deletion is the
  parallel track to the FI normalize fallback cluster fold.
* **`notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md`** — the frozen
  observable contract the grammar is validated against; post-retirement
  becomes the historical-contract appendix.
* **`notes/CONFORMANCE_CORPUS.md`** — the corpus the curated shadow gate
  draws from; the corpus the OutOfScope decline rate is measured against.
* **`src/lawvm/finland/parser_facade.py`** — the production path + shadow
  gate plumbing.
* **`tests/test_fi_parser_facade.py`** — the parity gate
  (`delta == 0`, `witness_span_normalized == 1`).