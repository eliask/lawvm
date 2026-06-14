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

## F1 — editorial insert-after spacing (§507(d))
Enacted (PL 118-42): "Section 507(d) … is amended by inserting
\"excluding subparagraph (F)\" after \"(a)(8)\"."
- before text: `…(a)(8), or (a)(9) of this section…`
- oracle after: `…(a)(8) excluding subparagraph (F), or (a)(9)…`
- faithful materialization of the enacted instruction: `(a)(8)excluding…`
The published USC inserts a **courtesy space** the enacted amendment does not
mandate. This is editorial normalization on the oracle side, not a lowering
defect. Disposition: `oracle_suspect` (do NOT repair our text to the oracle).
Open question: whether to model OLRC's insert-after spacing convention as a
declared, named normalization (so such residuals downgrade to compare-shape)
rather than `oracle_suspect`. Decision deferred.

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

**Numbers (honest, `replay_authorized=False`):**
- Oracle changed-section count (a fact of the two editions): **40**.
- Sections claimed/lowered: **11**; sections refused (typed): **641**
  (635 off-title; 4 sub-section structural; 2 target-section-not-in-2018).
- Section agreements: **0**. Residuals: **11** (all `lawvm_wrong` after
  composition). Witness-anchored coverage **0/40**; boundary `unresolved`.

The 0/40 is honest: this window's substantive Title-11 amendments are dominated
by sub-section structural redesignations (SBRA subchapter V) and quoted-block
inserts whose terminal-punctuation/footnote handling exposes a lowering-side gap.
No agreement was forced. What the kernel now does *correctly*:

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

**Per-section residual decomposition (11 sections):**
- `match_text_not_found` (§103, §347, **§547**): the amendatory lowering produced
  a strike anchor absent from the 2018 edition. §547 (PL 116-54 §3) is the clean
  case: the enacted text *strikes* `"may"` and *inserts* `"may, based on
  reasonable due diligence…"`, but the lowering inverted the direction (struck the
  long phrase, inserted `"may"`). This is a lowering gap (F5), not a dry-run
  defect — recorded, not papered over.
- `materialized_text_mismatch` (§101, §366, §501, §525, §1325, §1328, §1329):
  composed text disagrees substantively. §101 is partial (its paragraph
  redesignations refused, so the materialized debt-limit strike alone ≠ the
  fully-amended oracle — honest incompleteness). §366/§525/§1328 are
  structurally-correct add-at-end inserts that drop the terminal period inside the
  enacted `<quotedText>` (F4); §1328 additionally carries an OLRC footnote digit
  (F3); §501/§1325/§1329 inline a `<heading>` (`Definitions.`, `Time period.`)
  the consolidated text renders differently.
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

## F4 — terminal period inside an inserted `<quotedText>` (§366, §525, §1328)
The enacted PL 116-260 §1001 inserts blocks that end `…becomes due.”` — the
period is **inside** the quoted content. The section-text append drops this
terminal period (materialized `…becomes due` vs oracle `…becomes due.`). This is
a lowering/materialization detail on **our** side (the period is in the source),
NOT oracle editorializing — so it stays a `lawvm_wrong`/lowering residual and is
NOT absorbed by the editorial projection. Trace: the amendatory/section-text
append loses the final `<quotedText>`-internal punctuation.

## F5 — strike/insert direction inverted (§547, PL 116-54 §3)
The enacted amendment strikes `"may"` and inserts the longer due-diligence
clause; the lowered op carries the long clause as the *strike* anchor and `"may"`
as the *replacement*. Against the 2018 edition (which has only `"may"`) the strike
anchor is absent → `match_text_not_found`. This is an `amendatory.py` lowering
gap (recorded as a typed finding per the Prime Directive; NOT repaired in
`dry_run.py`).

## Implications for next work (priority order)
1. **Lowering robustness (F4/F5) — the binding constraint on agreement.** The
   2018→2020 window proved the dry-run kernel composes and types correctly, but
   surfaced the real blockers to non-zero agreement: (a) strike/insert direction
   inversion (§547), and (b) terminal `<quotedText>`-internal punctuation dropped
   on section-text append (§366/§525/§1328). Both live in `amendatory.py` /
   section-text materialization, NOT the dry-run surface. Fixing F5 then F4 is the
   path to the first real agreements on substantive textual amendments.
2. US temporal/sunset modeling (F2) — wire temporary-overlay + expiry so the
   §109/§1182-class reversions are produced by the temporal layer, and the
   dry-run can distinguish `missing_source` (un-lowered amendment) from
   `sunset_reversion` (expired temporary provision).
3. Insert-after editorial spacing (F1) — decide whether to declare a named OLRC
   normalization or keep as `oracle_suspect`.
