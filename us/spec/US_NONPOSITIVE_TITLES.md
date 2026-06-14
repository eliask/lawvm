# Non-positive-law titles: act-section → USC-address mapping

This file answers the open question the jurisdiction profile poses: **can the
act-section → USC-address mapping for the 24 non-positive-law U.S. Code titles
be derived from govinfo-reachable data alone, without the geo-blocked OLRC
classification tables?**

Verdict (short): **Yes, for codified targets — at a near-complete rate.** The
govinfo PLAW USLM XML *already carries the OLRC editorial classification
pre-applied* in two reachable channels (an inline `N U.S.C. M` parenthetical and
a USLM structural `<ref>` href). The residual gap is **not** a missing-mapping
gap; it is amendments to **uncodified** law (appropriations acts, other Public
Laws by Stat. cite, note-only provisions) which have **no USC section** for any
table to map them to.

---

## 1. The problem

27 USC titles are **positive law** (Title 11, 18, 28, 35, 38, 49, …): the title
*is* enacted text, so a Public Law cites the Code directly ("Section 362 of
title 11, United States Code") and the target address is read straight off the
prose/href (handled in `amendatory.py`).

The other 24 titles are **non-positive** (Title 15 Commerce, 26 Internal
Revenue, 42 Public Health, 7 Agriculture, 20 Education, …): the title is an OLRC
**editorial arrangement** of free-standing Acts. An amendment targets the
originating Act — "Section 5 of the Securities Act of 1933 is amended…" — and
the OLRC editorially **classifies** that act-section into a USC section
(`15 U.S.C. 77e`). To replay such an amendment we need the act-section → USC
mapping. The official OLRC classification tables are on geo-blocked
`uscode.house.gov`.

## 2. Data-reachability finding

The classification has **already been done** by GPO's USLM converter and is
embedded in the *already-acquired* govinfo PLAW USLM XML. Two channels carry it:

1. **Inline parenthetical.** The drafted target phrase carries the USC cite in
   parentheses: `Section 487(a)(24) of the Higher Education Act of 1965 (20
   U.S.C. 1094(a)(24)) is amended`. The parenthetical resolves the act-section
   directly to a USC address.

2. **USLM structural href.** The converter attaches a structural ref to the
   target citation: `<ref href="/us/usc/t20/s1094/a/24">20 U.S.C.
   1094(a)(24)</ref>`. The href is a USC address.

A **third** form is *not* a structural target: a `... note` ref
(`/us/usc/t7/s2011/note`, prose "7 USC 2011 note") or a `(N U.S.C. M note)`
parenthetical is an editorial cross-reference to an **uncodified** provision (a
Statutes-at-Large note), not a codified section. These are recognized and held
out, never mapped to a guessed section.

So the OLRC classification mapping for **codified** targets is reachable from
govinfo without the classification tables. The IRC (Title 26) special case:
amendments to "the Internal Revenue Code of 1986" usually carry no inline `(26
U.S.C. …)` paren and no `t26` href — because the act-section number *is* the USC
section number (Title 26 is the IRC verbatim). They still resolve, via the href
the converter emits, at near-100%.

## 3. Prototype design (`src/lawvm/us_federal/nonpositive.py`)

`resolve_nonpositive_target(target_phrase, target_href, raw_text)` →
`NonPositiveTargetWitness`. It consumes the same fields the amendatory lowering
already extracts and applies a no-guess policy:

| Channels present | Status | Rule id |
|---|---|---|
| paren + structural href, agree | `paren_href_agree` | `us_nonpositive_target_paren_href_agree` |
| paren + structural href, disagree | `href` (USLM ref is canonical landing; disagreement flagged) | `us_nonpositive_target_paren_href_disagree` |
| structural href only | `href` | `us_nonpositive_target_via_href` |
| inline parenthetical only | `paren` | `us_nonpositive_target_via_paren` |
| only a `note` cross-ref | `note_only` (unmapped) | `us_nonpositive_target_note_only` |
| neither | `unmapped` | `us_nonpositive_target_unmapped` |

The resolved `LegalAddress` uses the pinned USC convention shared with the
amendatory adapter (`("title", N) → ("section", M) → typed sub-segments`). The
single-letter roman-numeral ambiguity (IRC `(l)` is a subsection, not clause
"l") is disambiguated by nesting position. A `note`/`et seq.` cite is **never**
mapped to a codified section — the Prime Directive holds: unresolved targets are
typed findings, not guessed mappings.

`measure_nonpositive_resolve_rate(archive, title, congress_window)` scans the
real PLAW corpus and reports the resolved-vs-unmapped feasibility number.

## 4. Measured resolve-rate (govinfo-only, no classification tables)

Prototype title: **Title 15 (Commerce and Trade)**, with Title 26, 42, 20, 7 for
cross-confirmation. PLAW corpus: 113th–119th Congress (2013–2025), the acquired
govinfo bulkdata. Oracle for address validation: govinfo USCODE annual edition
**2023, Title 15** (`us://usc/2023/title15.htm`), keyless-acquired.

### 4a. Per-title denominator (units carrying a Title-N USC signal)

"Given a unit whose target touches Title N, can we place it on a USC address?"

| Title | Units | Resolved | Unmapped (note-only) | Resolve-rate |
|---|---:|---:|---:|---:|
| 15 Commerce | 534 | 517 | 17 | **96.8 %** |
| 26 Internal Revenue | 928 | 928 | 0 | **100.0 %** |
| 42 Public Health | 2533 | 2338 | 195 | **92.3 %** |
| 20 Education | 536 | 508 | 28 | **94.8 %** |
| 7 Agriculture | 878 | 846 | 32 | **96.4 %** |

Every unmapped unit in this denominator is a `note_only` target (an uncodified
provision), not a missing classification.

### 4b. All-act-named denominator (every "Section X of the <Act>" target)

The honest broad denominator — every act-named amendment unit, including those
that carry no USC signal at all (which 4a's per-title view cannot see):

- **7184** act-named non-positive amendment units (113th–119th)
- **5590 resolved → 77.8 %** govinfo-only
- **1594 unmapped**, broken down as:
  - other Public Law / Stat. cite targets: 64
  - appropriations-act targets: 64
  - note-only (uncodified): 28
  - "other": 1438 — **all** of which are nested sub-units (`"(1) in subsection
    (a)— (A) by striking…"`) whose own phrase names no act and whose **parent
    section's target is itself uncodified** (appropriations / note / cross-act).
    **Zero** are act-named amendments to a codified section that the converter
    failed to classify.

### 4c. Oracle cross-check (correctness, not just resolvability)

Of the resolved Title-15 targets, **485 / 524 (92.6 %)** land on a section that
actually exists in the 2023 Title-15 oracle. The 39 misses are explained, not
errors: en-dash vs normalized section labels (`278g–2`), repealed/renumbered
sections, and sections enacted after the 2023 edition. The resolver produces
**correct, oracle-checkable** USC addresses from govinfo-only data.

## 5. Honest verdict and residual gap

- **Feasibility:** govinfo-only non-positive replay is **feasible for codified
  targets at a near-complete rate** (per-title 92–100 %; 100 % for the IRC). The
  OLRC act→USC classification for codified sections is *already pre-applied* in
  the govinfo PLAW USLM and needs no classification table.

- **The residual gap is not a classification-table gap.** The ~22 % of
  act-named units that do not resolve target **uncodified** law — appropriations
  acts, other Public Laws cited by Stat. page, and provisions that live only as
  Statutes-at-Large notes — or are nested sub-instructions of such uncodified
  parents. These have **no USC section**; the OLRC classification table would
  not map them either, because there is nothing in the Code to map to. They are
  correctly emitted as `us_nonpositive_target_note_only` /
  `us_nonpositive_target_unmapped` typed findings, never guessed.

- **Is a one-time classification-table acquisition (via a US network path)
  required?** **No, for the act→USC *target* mapping.** The govinfo PLAW USLM
  converter has done that work. A classification table would still add value as
  an **independent coverage denominator** (the OLRC's own count of how many
  section-level operations each Public Law contained — the witness denominator
  in `SOURCE_STRATEGY.md` §9), to cross-check that the converter's hrefs are
  complete and to catch any silent drop. That remains an optional acquisition,
  not a blocker for replay of codified non-positive amendments.

## 6. Scope, limits, and what this does NOT claim

- This is a **target-resolution** surface only: it maps an amendment's
  act-section target to a USC address. It does **not** lower the operation
  payload (that is `amendatory.py`) or apply/verify it (that is the dry-run).
- The per-title resolve-rate is a **reachability** measure over the PLAW
  amendment-instruction surface; it is not a replay-agreement claim. End-state
  replay still depends on a non-positive USC oracle edition and the same
  effective-date / compare-shape hazards the positive-law path faces.
- The 2023 Title-15 oracle edition was acquired keyless from govinfo to validate
  address correctness; a full non-positive replay would acquire a straddling
  edition window per title (still keyless, still reachable).
- Tests use small committed synthetic PLAW fixtures with no network.
