# <JURISDICTION> jurisdiction profile

This file records jurisdiction facts, not implementation wishes.

Anything uncertain should be labeled as uncertain. Do not fill gaps with assumptions just to make the profile look complete.

---

## 1. Identity

- Jurisdiction name:
- Code / package slug:
- Primary citation format:
- Canonical statute identifier shape:
- Primary language(s):
- Script / encoding risks:
- Major legal instrument families:
- Does the frontend target only statutes, or also regulations/orders/decrees?

---

## 2. Source families

For each source family, say whether it exists, how trustworthy it is, and what role it can play.

| Source family | Exists? | Local substrate | Trust level | Covers current? | Covers history? | Replay semantics? | Verification oracle? | Notes |
|---|---|---|---:|---:|---:|---:|---:|---|
| Current consolidated text |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |
| Official promulgation acts |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |
| Amendment register |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |
| Structured amendment feed |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |
| Commencement / in-force source |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |
| Parliamentary package / preparatory works |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |
| Historical snapshots |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |
| Official PDF scan only |  | archive / clone / fixture / manifest / blocked |  |  |  |  |  |  |

Write a short paragraph explaining the trustworthy source chain for this jurisdiction.
If a source family has no local substrate plan, say which skipped, unsupported,
or blocked inventory row will represent that absence.

---

## 3. Legal structure

Describe the structural hierarchy actually used in the jurisdiction.

- Top-level containers:
- Chapter / part / title semantics:
- Section / article / rule semantics:
- Subsection / paragraph / item semantics:
- Appendices / schedules / annexes:
- Crossheadings / side notes / rubric behavior:
- Transitional provisions location:
- Defined-term markup in sources?
- Editorial notes mixed into operative text?
- Tables / forms / enumerations that behave structurally?

State whether the shared IR can represent the structure directly or whether local adapters are needed.

---

## 4. Amendment styles

List the amendment styles that appear in the official source.

Examples:
- whole-section replacement,
- insert new section,
- repeal section,
- renumber,
- substitute words,
- insert heading before section,
- insert appendix,
- table row changes,
- contingent commencement,
- amendment of “other laws” blocks,
- partial commencement.

For each style, mark one:

- directly structured by source,
- recoverable from official act text,
- recoverable only with hard parsing,
- currently unsupported.

---

## 5. Temporal semantics

Describe the distinct date fields.

- enactment / issued date:
- publication date:
- effective date:
- repeal date:
- prospective / inactive markers:
- editorial update date:
- verification cutoff date:

Questions to answer:
- Are commencement dates explicit per amendment?
- Can one act commence on multiple dates?
- Can one provision have multiple temporal markers?
- Are future/prospective provisions shown inline in current text?
- Does current text leak post-date structure into pre-date views?

---

## 6. Identity and contamination risks

List the biggest risks.

Examples:
- current surface already includes post-amendment headings,
- renumber placeholders leak future structure,
- editorial footnotes contaminate section text,
- PDFs are scanned / OCR-noisy,
- HTML hides numbering in CSS,
- same act id appears in multiple source families with different dates.

For each risk, state where it should be detected:
- acquisition,
- source normalization,
- clause/payload lowering,
- replay,
- verification.

---

## 7. Oracle story

What independent surfaces can verify replay?

Possible oracles:
- current official text,
- historical snapshot endpoint,
- current API with temporal filter,
- structured effects feed,
- no trustworthy oracle.

Document:
- oracle family,
- date coverage,
- structure quality,
- contamination risks,
- whether it is suitable for end-state verification or only smoke testing.

---

## 8. First honest target

Define the narrowest honest build target.

Template:

> We aim first to support `<instrument family>` for `<date range>` using `<base source>` as the base seed, `<amendment source>` for semantic lowering, and `<oracle source>` for verification. The first supported effect families are `<...>`. The frontend explicitly does not yet support `<...>`.

This paragraph should be short and strict.

Also state the first evidence claim:

> The first evidence pack will claim only rows with status `<accepted/replayed/audited>`. Rows with status `<unsupported/skipped/rejected/failed/unresolved>` remain non-claims and are counted separately.

---

## 9. Archetype classification

Pick one and explain why.

- Sweden-like: official promulgation acts + current surface, but amendment semantics must be recovered from text.
- Norway-like: structured amendment targeting in source, but commencement and history need sidecars/recovery.
- UK-like: effects feed + affecting-act extraction + compare-shape hazards.
- Other: explain.

---

## 10. Open questions

List the unknowns that must be answered before coding.

- Q1:
- Q2:
- Q3:
