# US dry-run findings — first end-to-end Title 11 proof

Status: descriptive. Records the result of the first US dry-run (P7.5) proof and
the typed residuals it surfaced. The dry-run is a witness comparison, not a
replay claim (`replay_authorized=False`).

## Proof window
- Title 11 (Bankruptcy), positive-law, amendments target the USC directly.
- before = USC 2023 annual edition; after-oracle = USC 2024 annual edition.
- Window amendments = Public Laws whose source-credit first appears in the 2024
  edition. Adjacent-edition deltas: 2022→2023 empty; 2023→2024 = PL 118-42 only.

## Result
- Oracle changed-section set (a fact of the two editions): {§109, §507, §1182}.
- Lowered + claimed: {§507(d)} — the one in-Title-11 op PL 118-42 carries
  (PL 118-42 is a 197-instruction omnibus; only §507(d) resolves to Title 11;
  the rest become typed `target_non_us_code` / `target_unresolved` findings, not
  hijacked targets).
- Agreements: 0. Typed residuals: §507 `oracle_suspect` (see F1), §109 + §1182
  `missing_source` (see F2). Witness-anchored coverage 0/3.

## F1 — editorial insert-after spacing (§507(d)) — RESOLVED (stays oracle_suspect)
Enacted (PL 118-42): "Section 507(d) … is amended by inserting
\"excluding subparagraph (F)\" after \"(a)(8)\"."
- before text: `…(a)(8), or (a)(9) of this section…`
- oracle after: `…(a)(8) excluding subparagraph (F), or (a)(9)…`
- faithful materialization of the enacted instruction: `(a)(8)excluding…`
The USLM `<quotedText>` literal is `excluding subparagraph (F)` with **no leading
space** (verified against the source bytes): this is case (ii) — the published USC
inserts a **courtesy space** the enacted amendment does not mandate. We do NOT
invent the space; the lowering preserves the faithful `(a)(8)excluding`.

Resolution: the OLRC insert-after-anchor courtesy space is now modelled as a
declared editorial normalization (`_EDITORIAL_INSERT_AFTER_PAREN_SPACE_RE`,
generalized F1). The residual is **demoted from `lawvm_wrong` to `oracle_suspect`**
by the editorial projection (a `)`-adjacent space is the SOLE divergence), never
repaired to the oracle. The projection is applied symmetrically to both sides and
provably cannot manufacture a false agreement between texts that differ in any
other character (pinned by `test_norm_editorial_undoes_insert_after_anchor_courtesy_space`).
The classification fix also confirms §507(d) is an **insert-after**, not a
strike-and-insert: anchor `(a)(8)`, inserted `excluding subparagraph (F)`.

## F2 — sunset reversion, not amendment (§109, §1182)
The 2023→2024 changes to §109 and §1182 are NOT textual amendments by any public
law in the window. They are the **expiry of a temporary provision**:
- §109: chapter-13 debt limit reverts `$2,750,000` → `$250,000` (adjusted).
- §1182: subchapter-V "debtor" definition collapses from the expanded SBRA form
  back to "means a small business debtor".
This is the SBRA debt-limit increase (PL 116-54, extended by PL 117-5 to
June 21, 2024) **sunsetting**. No public law amends these sections in 2024; the
text reverts because the temporary overlay expired.

Consequence: amendment-replay alone cannot produce these reversions. US has
material temporary/sunset mechanics that require LawVM's temporal/expiry model
(temporary overlay + expiry reversion to the prior permanent version), not op
lowering. The dry-run's `missing_source` was correct at the amendment layer and
correctly refused to invent an amendment.

## Richer window (2018→2020) — the multi-law textual-amendment test

before = USC **2018** Title 11 (2018 Main Edition, current through Jan 14 2019);
after-oracle = USC **2020** Title 11 (2018 Edition and Supplement II, current
through Jan 13 2021). The 116th Congress made real substantive Title-11 textual
amendments across this window, so the SBRA/debt-limit/quoted-block changes are
in force in the 2020 edition (their 2024 sunsets are out of window). This is the
strike/insert/add lowering exercised on real ops.

**Window amending laws** (Public Laws whose source-credit first appears in the
2020 edition, via `usc_witness`): PL 116-51, 116-52, 116-54, 116-92, 116-136,
116-189, 116-260, 116-325 (8 laws). PL 116-54 (SBRA) alone newly credits 28
sections; PL 116-260 newly credits 15; PL 116-136 (CARES) 6.

**Numbers (honest, `replay_authorized=False`) — AFTER the F1/F4/F5 lowering fixes:**
- Oracle changed-section count (a fact of the two editions): **40**.
- Sections claimed/lowered: **14** (was 11 — the subsection-unit split surfaced
  three more instruction units, e.g. §547(b) is no longer merged with §1409(b)).
- Section agreements: **1** (§525, PL 116-260 add-at-end materializes the oracle
  exactly once F4 preserves the block's terminal period). Witness-anchored
  coverage **1/40**; boundary `unresolved`.
- Residuals (13): **12 `lawvm_wrong`** (genuine incompleteness — multiply-amended
  sections where another window op is un-lowered, plus heading-inlining and the
  F3 footnote digit) + **1 `oracle_suspect`** (§366, the OLRC per-paragraph
  quote-stripping editorial projection).

The single agreement is the FIRST real agreement on a substantive Title-11 textual
amendment. The remaining residuals are honest: this window's amendments are
dominated by sub-section structural redesignations (SBRA subchapter V) and
multiply-amended sections whose other window ops are not yet lowered. No agreement
was forced. What the kernel now does *correctly*:

- **Multi-op composition per section.** Title 11 §101 is amended by 5 window ops
  (PL 116-51/52/92/136). The kernel now composes a section's ops in source order
  onto its before-text and compares **once**; comparing each op independently
  against the fully-amended oracle spuriously failed every multiply-amended
  section. Fix in `dry_run.py` only (Phase-1 routing + Phase-2 composition).
- **Sub-section structural refusal.** A REPLACE/INSERT whose target is deeper
  than `section` (paragraph/clause — e.g. §101(10A) redesignation, §502(b)
  paragraph) is not section-text representable: its payload is a fragment, not the
  section body. Now typed-refused (4 ops) instead of wrong-materialized.
- **Editorial classification path (generalized F1).** A residual that vanishes
  once the OLRC quote-stripping + dash-paren courtesy-space editorial projection
  is applied is typed `oracle_suspect`, never repaired to the oracle. (It does not
  fire on this window — see F3/F4 — but it is the correct mechanism and is
  covered by a synthetic test.)

**Per-section residual decomposition (13 residuals + 1 agreement, AFTER fixes):**
- AGREEMENT (§525): PL 116-260 add-at-end. With F4 preserving the terminal period
  inside the inserted `<quotedContent>` block, the composed section text now equals
  the 2020 oracle exactly. First substantive textual-amendment agreement.
- `match_text_not_found` (§103, §347): the amendatory lowering produced a strike
  anchor absent from the 2018 edition (separate, un-fixed gaps; not F5).
  **§547 is no longer here** — F5 is fixed: PL 116-54 §3(a) is now correctly read
  as an *insert-after* (anchor `"may"`, inserted clause appended after it), the
  anchor is found in the 2018 edition.
- `materialized_text_mismatch` (§101, §366, §501, §547, §1325, §1328, §1329):
  composed text disagrees substantively. §101 is partial (its paragraph
  redesignations refused, so the materialized debt-limit strike alone ≠ the
  fully-amended oracle — honest incompleteness). §547 now materializes the
  due-diligence clause at the right anchor but the section also acquired a `(j)`
  subsection from another window amendment not lowered here — honest incompleteness,
  `lawvm_wrong`. §366 is now `oracle_suspect` (OLRC per-paragraph quote-stripping,
  generalized F1). §1328 still carries an OLRC footnote digit (F3); §501/§1325/§1329
  inline a `<heading>` (`Definitions.`, `Time period.`) the consolidated text
  renders differently.
- `claimed_section_unchanged_in_oracle` (§503): an over-claim — PL 116-260 §320's
  `and`→`; and` resolved to a Title-11 §503 the 2020 edition did not change.

## F3 — OLRC footnote-digit injection (§1328)
The 2020 consolidated §1328 carries a bare `1` (`… 2605(i)) 1 of the mortgage …`)
— an OLRC editorial footnote-reference marker rendered as plain text in the htm.
Statutory text never contains bare footnote numbers; this is an oracle editorial
insertion no amendment mandates. Disposition `oracle_suspect` in spirit (we do
NOT strip the oracle's footnote to force agreement). Not folded into the editorial
projection: footnote markers are an OLRC rendering artifact, distinct from the
F1 quote/spacing convention, and would need a declared named normalization.

## F4 — terminal period inside an inserted `<quotedContent>` (§366, §525, §1328) — RESOLVED
The enacted PL 116-260 inserts blocks that end `…becomes due.”` — the period is
**inside** the quoted content. The old `_quoted_content_node` did
`.strip().strip("“”\".")`, which stripped the trailing `.` along with the curly
quote, dropping the period (materialized `…becomes due` vs oracle `…becomes due.`).

Resolution (in `amendatory.py`): `_quoted_content_node` now collapses only internal
formatting whitespace, trims the block's outer serialization whitespace, and peels
**only a matched enclosing curly-quote pair** (`_peel_enclosing_quotes`). The
terminal period survives INSIDE the quote, and the leading `(d)`/`(i)` paragraph
label is kept without a leading curly quote. This is what turns §525 into the
window's first agreement. Pinned by
`test_add_at_end_payload_preserves_terminal_period_inside_quoted_block`.

## F5 — action misclassification + inverted operands (§547, PL 116-54 §3(a)) — RESOLVED
The enacted instruction is an **insert-after**: "is amended by inserting
\"<due-diligence clause>\" after \"may\"" — there is NO striking. The old lowering
mis-classified it as `strike_insert` with inverted operands (long clause as the
strike anchor, `"may"` as the replacement). Two root causes:

1. **Unit merging.** PL 116-54 SEC. 3 carries two `<subsection role="instruction">`
   units — (a) the §547(b) insert-after and (b) the §1409(b) strike-and-insert.
   `_iter_instruction_units` did not recognize `subsection` as a splittable unit, so
   it yielded the whole section as one flat instruction; (b)'s "striking" bled into
   (a)'s raw text and forced the `strike_insert` branch. Fix: `subsection` is now a
   first-class instruction-unit tag, so the two are lowered separately.
2. **Classification ordering.** `_classify_action` now tests "inserting X
   after/before Y" with NO strike verb as `insert_after` BEFORE the strike_insert
   and add_at_end branches, keyed off the actual amendingAction verbs + the
   "after"/"before" anchor prose. Genuine strike-and-insert ("striking X and
   inserting Y") still classifies as `strike_insert` with match=X (struck),
   replacement=Y (inserted) — verified against PL 116-51 §101(18).

For `insert_after` the anchor is the "after 'Y'" quotedText and the materialization
is match=`Y`, replacement=`Y`+inserted (real whitespace preserved). §547 now finds
the `"may"` anchor in the 2018 edition; it stays a residual only because the section
acquired an unrelated `(j)` subsection from another un-lowered window op — honest
incompleteness, never repaired. Pinned by `test_insert_after_classifies_and_assigns_operands_at_the_anchor`,
`test_sibling_subsections_split_so_striking_does_not_bleed_into_insert_after`, and
`test_strike_insert_operand_order_struck_matches_inserted_replaces`.

## Implications for next work (priority order)
1. **Lowering robustness (F4/F5) — DONE; first agreement achieved.** F5 (insert-after
   mis-classification + subsection-unit merging) and F4 (terminal period peeled with
   the curly quote) are fixed in `amendatory.py`. Result: the 2018→2020 window now
   yields **1 agreement (§525, coverage 1/40)** and §366 demotes to `oracle_suspect`;
   the 2023→2024 §507(d) demotes to `oracle_suspect` via the declared insert-after
   courtesy-space normalization. Remaining residuals are honest incompleteness
   (multiply-amended sections with un-lowered sibling ops: §547 `(j)`, §101
   redesignations) and the F3 footnote / heading-inlining classes — the NEXT
   lowering levers for more agreements.
2. US temporal/sunset modeling (F2) — wire temporary-overlay + expiry so the
   §109/§1182-class reversions are produced by the temporal layer, and the
   dry-run can distinguish `missing_source` (un-lowered amendment) from
   `sunset_reversion` (expired temporary provision).
3. Insert-after editorial spacing (F1) — decide whether to declare a named OLRC
   normalization or keep as `oracle_suspect`.
