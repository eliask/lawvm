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

## Implications for next work (priority order)
1. Validate the kernel on a richer historical window where the SBRA changes are
   *textual additions* (before = 2018, after = 2020, window = 116th-Congress
   Title-11 laws incl. PL 116-54) — exercises strike/insert/add lowering on real
   substantive ops, where agreements should be non-zero.
2. US temporal/sunset modeling (F2) — wire temporary-overlay + expiry so the
   §109/§1182-class reversions are produced by the temporal layer, and the
   dry-run can distinguish `missing_source` (un-lowered amendment) from
   `sunset_reversion` (expired temporary provision).
3. Insert-after editorial spacing (F1) — decide whether to declare a named OLRC
   normalization or keep as `oracle_suspect`.
