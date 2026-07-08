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

## Sunset/temporal (F2 resolution)

The temporal layer (`src/lawvm/us_federal/sunset.py`, wired into `dry_run.py`)
now distinguishes a sunset reversion from a still-un-lowered amendment. For each
oracle-changed section the kernel did NOT claim (an otherwise-`missing_source`
gap), the detector consults the after-edition's editorial notes and the prior
editions before settling on `missing_source`, and reclassifies the disposition
to **`sunset_reversion`** (a new typed disposition) when there is evidence —
never a guess, never a repair-to-oracle, `replay_authorized=False` throughout.

**Mechanism.** The USC annual edition carries a section's temporal mechanics in
its editorial notes (`note-head`/`note-body`), which the statutory comparison
surface excludes. `source_tree.py` now also extracts those note blocks (new
`UscSection.notes` + `iter_section_notes(...)`) **without** changing the
statutory-text surface. The detector models the expiring provision with the
shared core temporal types — a `ProvisionVersion(variant_kind="temporary",
expires=<sunset date>)` reverting to a `ProvisionVersion(variant_kind=
"permanent")` — not a bespoke model. A reversion is asserted only with evidence:

- **(a) prior-edition text match** — the after-text equals an EARLIER permanent
  edition's text (the permanent form the section reverts to); and/or
- **(b) sunset note** — a temporal note ("Effective Date of YYYY Amendment",
  "Termination Date", reversion language, or a "Prior to amendment, text read as
  follows: …" quote matching the after-text) whose computed sunset date falls
  inside the edition window. The SBRA re-extension form "effective on the date
  that is N years after MONTH DD, YYYY" is parsed to its ISO sunset date.

Channel (b) requires both an in-window sunset date AND a reversion anchor
(reversion language or a substantial quoted-prior-text match) — a bare effective
date with no reversion semantics is an ordinary amendment, not a sunset, and is
emitted as a typed `us_sunset_temporal_note_present_but_reversion_unproven`
finding (self-evidencing, carrying the offending note), never a reversion claim.

**§109 / §1182 result (2023→2024 window, run from the canonical archive with the
2018/2022 prior editions loaded):**
- Oracle changed-section set: {§109, §507, §1182} (unchanged).
- §109 → `sunset_reversion`. Witness: sunset date **2024-06-21** (PL 117-151
  §2(i)(1)(A) is "effective on the date that is 2 years after June 21, 2022"),
  AND the 2024 §109 text reverts **exactly** to the **2018** edition's permanent
  text (channel a). The temporary version (`expires=2024-06-21`) and the prior
  permanent version are both modelled.
- §1182 → `sunset_reversion`. Witness: the same sunset date **2024-06-21**
  (PL 117-151 §2(i)(1)(B)), AND the Amendments note's quoted prior text "The term
  'debtor' means a small business debtor." matches the 2024 §1182 text
  (channel b; §1182 was added by SBRA in 2019, so no earlier full-section
  edition matches — the quoted-text channel carries it).
- `missing_source` for this window is now **empty**; the two reversions are
  surfaced under `sunset_reversion_sections` in the north-star and as
  `temporal_mismatch`-family residuals in the agreement surface. §507 is
  unaffected (it stays the F1 `oracle_suspect` claimed section).

**Honest scope.** This is **detection + classification using the editions and
their notes as witnesses**. The prior permanent edition IS the materialized
reversion witness; the detector does not rebuild the reverted text from a
temporary overlay's expiry. **Next step:** full temporal-replay materialization —
seed the temporary `ProvisionVersion` from the SBRA amendment ops, drive its
expiry through the core timeline at the sunset date, and materialize the reverted
permanent version, so the reversion is *produced* by the temporal layer rather
than *recognized* against the prior edition. The effective dates here are the
editions' Jan-1 anchors; day-precision commencement is part of that next step.

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

**Numbers (honest, `replay_authorized=False`) — AFTER the F1/F4/F5 lowering fixes
AND the F6/F7/F8 editorial-faithfulness + sub-section-granularity levers:**
- Oracle changed-section count (a fact of the two editions): **40**.
- Sections claimed/lowered: **15** (the sub-section-scoped materialization surfaces
  §101's paragraph-redesignation op as a typed residual row instead of a blanket
  refusal).
- Section agreements: **1** (§525, PL 116-260 add-at-end materializes the oracle
  exactly once F4 preserves the block's terminal period). Witness-anchored
  coverage **1/40**; boundary `unresolved`.
- Residuals (14): **10 `lawvm_wrong`** (genuine incompleteness — multiply-amended
  sections where another window op is un-lowered, plus the F3 footnote digit) +
  **4 `oracle_suspect`** (§366 OLRC per-paragraph quote-stripping; §1329 the OLRC
  dropping the "Time period." marginal note; §330 curly-vs-straight quotes; and the
  §365 sunset is surfaced separately as a `sunset_reversion`).

**Why agreements stayed at 1/40 (honest binding-constraint finding).** The
editorial levers below correctly demote three more residuals to `oracle_suspect`
and the sub-section lever scopes the structural ops faithfully, but **no additional
section reaches a full text AGREEMENT** because the binding constraint in this
window is UPSTREAM of the dry-run: the amendatory LOWERING layer leaves 32 of
PL 116-54's (SBRA) conforming-amendment instructions un-lowered (the nested
"in section X— (A) by striking … (B) by inserting …" conforming-amendments form
and several "amend to read" sub-section redesignations). The oracle-changed
sections that are dominated by those un-lowered SBRA ops (§103, §347, §101, §364,
§1325, the §11xx subchapter-V block) cannot compose to the full oracle no matter
how the dry-run materializes the ops it DOES have — the ops do not exist yet. The
sub-section-granularity materialization is the correct architecture and is now in
place; it surfaces these as honest `subsection_target_node_not_located` /
`match_text_not_found` residuals rather than wrong materializations. The next
agreement-yielding lever is the amendatory lowering of the SBRA conforming
amendments, not the dry-run surface. An honest 1/40 with correctly-typed residuals
beats a forced number.

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
- **Sub-section-granularity materialization (F8).** A REPLACE/INSERT/TEXT_REPLACE
  whose target is deeper than `section` (paragraph/clause — e.g. §101(10A)
  redesignation, §547(b) insert-after, §1325(b)(2) strike) is now materialized at
  SUB-SECTION granularity: the targeted node's before-text is located by the pinned
  USC address convention (`split_statutory_subsections`) and the edit is confined to
  that node's span inside the running section text, then recomposed. This (a) scopes
  a text patch to the right sub-section instead of string-replacing the first
  occurrence anywhere, and (b) applies an amend-to-read payload to the right node
  instead of refusing it. When the targeted node is NOT locatable in the before
  edition (the SBRA subchapter-V nodes were introduced by un-lowered sibling ops),
  the section is a typed `subsection_target_node_not_located` residual — never a
  blanket refusal, never a wrong materialization. The four prior structural refusals
  are now sub-section residuals; the structural-refusal count for this window is 0.
- **Editorial classification path (generalized F1 + F6/F7).** A residual that
  vanishes once the editorial projection is applied is typed `oracle_suspect`, never
  repaired to the oracle. The projection now folds curly AND straight quote shapes
  (F6) and the materialization prunes govinfo's marginal sidenotes and page stamps
  (F7); these demote §1329 and §330 to `oracle_suspect` on this window.

**Per-section residual decomposition (14 residuals + 1 agreement, AFTER all fixes):**
- AGREEMENT (§525): PL 116-260 add-at-end. With F4 preserving the terminal period
  inside the inserted `<quotedContent>` block, the composed section text now equals
  the 2020 oracle exactly. First substantive textual-amendment agreement.
- `oracle_suspect` (§366, §1329, §330): the composed text matches the oracle once
  the declared editorial projection is applied (F1 quote-stripping for §366; F7
  marginal-note pruning of "Time period." for §1329; F6 curly-vs-straight quote
  fold around "Chapter 7 Trustee Fund" for §330). Never repaired to the oracle.
- `subsection_target_node_not_located` (§101, §103, §1325, …): the sub-section op
  targets a node the 2018 before-edition does not expose because an un-lowered SBRA
  sibling op was to introduce/renumber it (honest upstream-lowering incompleteness).
- `match_text_not_found` (§347): PL 116-136 §347(b) strikes "1194" → "1191", but
  "1194" is only present after the un-lowered PL 116-54 SBRA §347 insert — honest
  incompleteness, never fuzzy-matched. **§547 is no longer here** — F5/F8: PL 116-54
  §3(a) is correctly read as an *insert-after* and now scoped to subsection (b).
- `materialized_text_mismatch` (§364, §501, §1328, §547): composed text disagrees
  substantively. §364 is missing "1183, 1184" from an un-lowered PL 116-54
  conforming amendment (honest incompleteness). §547 materializes the due-diligence
  clause at the right anchor but the section also acquired a `(j)` subsection from an
  un-lowered op. §501 and §1328 are blocked SOLELY by the OLRC footnote digit (F3) —
  after the F6/F7 editorial projection the only remaining divergence is the bare
  footnote `1` the oracle injects; we do NOT strip it (see F3 — an unsafe generic
  digit projection would mangle real statutory numbers, so these stay typed
  residuals rather than force agreement).
- `claimed_section_unchanged_in_oracle` (§1225, §503): an over-claim — a PL 116-260
  §320 op resolved to a Title-11 section the 2020 edition did not change.

## F3 — OLRC footnote-digit injection (§1328, §501) — INVESTIGATED, deliberately NOT forced
The 2020 consolidated §1328 and §501 each carry a bare `1`
(`… 2605(i)) 1 of the mortgage …` / `… 2605(i)) 1 with a claim …`) — an OLRC
editorial footnote-reference marker rendered as plain text in the htm. After the
F6/F7 editorial projection this bare footnote digit is the SOLE remaining
divergence for both sections. Statutory text never contains bare footnote numbers;
this is an oracle editorial insertion no amendment mandates, so the residual is
oracle-side `oracle_suspect` in spirit.

It is deliberately NOT folded into the editorial projection. A survey of the 2018
and 2020 editions shows space-flanked lone digits are pervasive in LEGITIMATE
statutory text — `1 fund`, `2 years`, `1 person`, `(I) 1 family`, `(iv) 1 radio`,
`(A) 2 years` — indistinguishable by any safe regex from the footnote markers
(`2605(i)) 1 of`, `733(g) 2 of`). A generic footnote-digit normalization would
mangle real statutory numbers and could manufacture false agreements elsewhere.
Per the Prime Directive we keep §1328/§501 as typed `lawvm_wrong` residuals
(faithful materialization; oracle carries an editorial digit) rather than force
them with an unsafe projection. This is the honest call: a self-evidencing
per-marker normalization (reading the footnote anchors from the source) is the
correct future lever, not a digit-pattern guess.

## F6 — quote-shape fold (curly vs straight) — RESOLVED (comparison-side)
The enacted USLM amendment wraps inserted matter and defined terms in CURLY quotes
(`‘CARES forbearance claim’`, `‘Chapter 7 Trustee Fund’`); the OLRC consolidated
Code re-renders them as STRAIGHT quotes (`"CARES forbearance claim"`). The editorial
projection `_norm_editorial` now folds both quote shapes (curly and straight) for
classification only. Equating quote *shape* can never manufacture agreement between
texts that differ in any non-quote character. This demotes §330 to `oracle_suspect`
(the sole divergence was the quote shape around "Chapter 7 Trustee Fund") and
removes the term-quote divergence from §501. Pinned by
`test_norm_editorial_folds_straight_and_curly_quote_shapes` (incl. the
no-false-agreement guard).

## F7 — editorial marginal sidenotes + page stamps in quoted blocks — RESOLVED (materialization-side)
govinfo PLAW USLM interleaves the legislative-counsel marginal sidenotes (topical /
effective-date markers "Time period.", "Definitions.", "Deadline.", "Effective
date.") as small-font `<p class="…fontsize8">` elements, and the Statutes-at-Large
page-break stamps ("134 STAT. 3219") as `<page>` elements, INSIDE `<quotedContent>`.
These are editorial pagination/marginalia, NOT enacted statutory text — the OLRC
consolidated USC body never renders them. The old `_quoted_content_node` flattened
them into the materialized payload (`(2) Time period.A plan …`), which the published
Code lacks. Resolution (in `amendatory.py`): `_itertext_excluding_sidenotes` prunes
the `fontsize8` sidenote and `<page>` subtrees while preserving their tail text and
the full statutory body verbatim. This is a FAITHFULNESS fix (we were materializing
sidenote text the statute does not contain), not a comparison hack. It demotes
§1329 to `oracle_suspect` (the sole divergence was the "Time period." marginal note)
and removes the inline-heading divergence from §501. Pinned by
`test_add_at_end_payload_prunes_editorial_sidenotes_and_page_stamps`.

## F8 — sub-section-granularity materialization — RESOLVED (dry-run-side)
A REPLACE/INSERT/TEXT_REPLACE/TEXT_REPEAL whose target is deeper than `section`
(paragraph/clause/sub-section) is now materialized at SUB-SECTION granularity. The
targeted node's before-text is located by the pinned USC address convention
(`split_statutory_subsections`, matching the op's sub-section segments) and the edit
is confined to that node's span inside the running section text, then recomposed:

- A TEXT_REPLACE on §547(b) edits only subsection (b)'s text, not the first
  occurrence of the anchor anywhere in the section.
- An amend-to-read REPLACE on a paragraph substitutes the payload for THAT node,
  not the whole section body (no fragment masquerading as the section).

When the targeted node is NOT locatable in the before edition — because an
un-lowered SBRA sibling op was to introduce or renumber it — the op is a typed
`subsection_target_node_not_located` residual (`lawvm_wrong`), never a blanket
refusal and never an unscoped whole-section string replace. The four prior
structural refusals on this window are now sub-section residuals; the
structural-refusal count is 0. Pinned by
`test_subsection_text_replace_is_scoped_to_the_target_node`,
`test_subsection_replace_op_materializes_at_the_target_node`, and
`test_subsection_op_without_locatable_node_is_typed_residual_not_wrong_materialization`.

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

## F9 — target threading + structural lowering (coverage 23 -> 24)

The binding-constraint diagnosis in §0 was confirmed AND refined by a fresh survey:
of the ~144 `missing_source` sections, ~136 had **no lowered op targeting them at
all** — the lowering layer never produced an instruction for them. Cause: a nested
instruction leaf ("(1) in subsection (a), by inserting …" / "(B) in section
3675(b)(3), by striking …") carries no `<ref>` and no "of title N" prose, and the
old `_iter_instruction_units` only threaded the section-level ref. Three levers
landed (all in `amendatory.py` / `dry_run.py`):

- **F9a — relative-prose + nested-list target threading (THE coverage lever).**
  `_iter_instruction_units` now threads the address resolved by the nearest
  ENCLOSING instruction into each leaf (`inherited_address`).
  `parse_relative_usc_target` resolves "section X(...) of such title" /
  "in section X, by …" under the inherited title — never invents a title; a
  cross-reference "section 116 of title 18" in inserted text is NOT matched (it
  carries "of title N", handled only by the absolute parser).
  `_refine_with_leading_subunit_anchor` appends a leading "in subsection (a)"
  anchor so two sibling ops ("(1) in subsection (a), …"/"(2) in subsection (b), …")
  do not collapse onto the same section address and double-apply at the section
  surface (fixed §11:104 `1182(1),1182(1),`). This claimed §35:5 (two composed
  strike-and-inserts) into the pass's one NEW agreement (exact, hand-verified).
  `missing_source` fell 155 -> 106; `lawvm_wrong` rose 48 -> 94 because the newly-
  claimed sections are mostly multiply-amended with a still-un-lowered sibling op —
  honest typed residuals, never forced.

- **F9b — structural-op lowering + sub-section materialization.** `strike
  subsection (X)` -> sub-section REPEAL (dry-run removes the located node and
  recomposes); a FUTURE-effective strike ("Effective on the date that is N years
  after …, … striking subsection (d)") is left to the temporal layer (lowering it
  would delete an in-force node — this caused a transient §11:525 regression, fixed
  by the `_FUTURE_EFFECTIVE_RE` guard); a strike of a node absent from the before
  edition is a typed REFUSAL no-op (never an over-broad deletion that tanks the
  section's other ops — this preserved §11:1329's `oracle_suspect`). `redesignating
  paragraphs (3) through (7) as (4) through (8)` -> one RENUMBER per member
  (high-end first; relabels only each node's leading enumerator; non-numeric ranges
  stay typed findings). `inserting after paragraph (N) the following: <block>` -> an
  anchored INSERT (gated on the anchor being a SUB-SECTION so an add-at-end op,
  anchored at the whole section, still appends — fixed a §11:525 mis-route).
  On-title unlowered findings fell 96 -> 73.

- **F9c — comma-anchor editorial projection (generalized F1).** The OLRC adds a
  courtesy space after a `,` or `)` insert-after anchor the enacted quotedText does
  not carry (faithful `Tacoma,Mount Vernon,` vs published `Tacoma, Mount Vernon,`;
  verified against the source bytes). `_norm_editorial` folds that anchor-adjacent
  space symmetrically — `oracle_suspect`, never repaired, never coverage. Pinned by
  `test_norm_editorial_undoes_comma_anchor_courtesy_space` (incl. a no-false-
  agreement guard).

Net: **24/231 = 0.1039**, all 24 agreements exact materialized==oracle in the
oracle changed set. The next AGREEMENT-yielding lever is the punctuation
strike_insert form ("striking the period at the end and inserting '; and'", 16
remaining) — its strike anchor is descriptive prose, not a quotedText.

## Implications for next work (priority order)
0. **THE binding constraint for more agreements is the amendatory LOWERING layer,
   not the dry-run surface.** F9a closed the "no op at all" half of the gap; the
   remaining gap is multiply-amended sections with a still-un-lowered SIBLING op.
   PL 116-54 (SBRA) leaves 32 of its conforming-amendment instructions un-lowered
   (the nested "in section X— (A) by striking … (B) by inserting …" form and several
   "amend to read" sub-section redesignations). Every oracle-changed section
   dominated by those un-lowered ops (§103, §347, §364, §101, §1325, the §11xx
   subchapter-V block) cannot reach a full text agreement until the ops exist. The
   F6/F7/F8/F9 levers are correct and in place; the next AGREEMENT-yielding work is
   the punctuation strike_insert + non-quotedText strike forms in `amendatory.py`'s
   `_classify_action` / strike branch. The dry-run sub-section materialization (F8/F9b)
   is ready to consume those ops once lowered.
1. **Editorial-faithfulness + sub-section levers (F6/F7/F8) — DONE.** F7 prunes
   govinfo marginal sidenotes + page stamps from the materialized payload
   (faithfulness fix); F6 folds curly/straight quote shapes in the comparison
   projection; F8 materializes sub-section-targeted ops at the right node. Result on
   2018→2020: **1 agreement (§525) unchanged at 1/40**, but `oracle_suspect` rises
   from 1 to 4 (§366 + §1329 + §330 + the §365 sunset) — three residuals correctly
   re-typed from `lawvm_wrong` to oracle editorial pathology, never repaired. The
   four prior structural refusals are now typed sub-section residuals. §1328/§501
   remain typed residuals blocked solely by the F3 footnote digit, deliberately not
   forced. 2023→2024 is unchanged (§507 oracle_suspect, §109/§1182 sunset, 0
   missing_source). No agreement was forced; an honest 1/40 with correctly-typed
   residuals beats a forced number.
2. US temporal/sunset modeling (F2) — DONE at the detection+classification layer:
   the dry-run distinguishes `missing_source` (un-lowered amendment) from
   `sunset_reversion` (expired temporary provision), with §109/§1182 reclassified
   on the 2023→2024 window using the prior editions + sunset notes as witnesses
   (see the "Sunset/temporal (F2 resolution)" section). The remaining lever is
   full temporal-replay materialization — produce the reverted text from a
   temporary-overlay expiry rather than recognize it against the prior edition.
2a. F3 deferred-op accounting — DONE at the aggregate/reporting layer: `deferred_op`
   is an OLRC pre-incorporation/editorial-timing bucket, so
   `coverage_source_present` excludes it with F1 `oracle_suspect` and F2
   `sunset_reversion`. `missing_source` and `lawvm_wrong` stay in the denominator
   because those are still billable lowering/replay gaps.
3. Insert-after editorial spacing (F1) — decide whether to declare a named OLRC
   normalization or keep as `oracle_suspect`.
