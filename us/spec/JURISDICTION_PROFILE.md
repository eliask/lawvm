# U.S. federal statutory jurisdiction profile

This file records jurisdiction facts, not implementation wishes.

Anything uncertain is labeled as uncertain. Gaps are not filled with assumptions
just to make the profile look complete.

---

## 1. Identity

- Jurisdiction name: United States of America (federal statutory law).
- Code / package slug: `us` (package `src/lawvm/us/`).
- Primary citation format: `<title> U.S.C. § <section>` for the consolidated
  Code (e.g. `11 U.S.C. § 362`); `Pub. L. No. <congress>-<num>` and
  `<vol> Stat. <page>` for the enacting instruments (e.g. `Pub. L. No. 119-95`,
  `136 Stat. 1818`).
- Canonical statute identifier shape: two parallel identifier regimes.
  - Codified surface: USC title number + section number, optionally with
    subsection `(a)`, paragraph `(1)`, subparagraph `(A)`, clause `(i)`,
    subclause `(I)` tails.
  - Enacting surface: Public Law number `PL{congress}-{num}` plus internal
    division/title/section structure of the Act itself.
- Primary language(s): English only.
- Script / encoding risks: low. USLM XML is UTF-8. Real risks are typographic,
  not script: smart vs straight quotes in quoted amendment payloads
  ("is amended by striking '…' and inserting '…'"), em/en dashes, and
  non-breaking spaces inside section designators. These bite the
  strike-text matcher, not the parser.
- Major legal instrument families: Public Laws (slip laws), bound into the
  Statutes at Large; the consolidated United States Code; OLRC editorial
  classification tables; the Code of Federal Regulations (regulatory, explicitly
  out of scope here).
- Does the frontend target only statutes, or also regulations/orders/decrees?
  Statutes only. The CFR / regulatory frontend is a separate future effort
  (`us/README.md` already names the statutory/regulatory split). Executive
  orders and proclamations are out of scope.

---

## 2. Source families

| Source family | Exists? | Local substrate | Trust level | Covers current? | Covers history? | Replay semantics? | Verification oracle? | Notes |
|---|---|---|---:|---:|---:|---:|---:|---|
| Current consolidated text | yes | archive (`us://usc/release/{plNNN}/title{N}.xml`) | high (official OLRC) | yes | partial (release points + annual archives) | no (it is a target, not an instruction) | yes — this IS the oracle | USC in USLM XML at a Public-Law-pinned release point |
| Official promulgation acts | yes | archive (`us://plaw/{congress}/{plNum}.xml`) | high (official GPO/govinfo) | n/a | yes (113th Congress, 2013+, in USLM) | yes — explicit amendment instructions | no | Public Laws in USLM XML; the amendment-instruction source |
| Amendment register | yes (as classification tables) | archive (`us://classification/{plNum}`) | high (official OLRC) | yes | yes | partial — maps Act-§ to USC-§, not a payload | witness denominator, not oracle | OLRC classification tables; the ground-truth operation-witness count |
| Structured amendment feed | no | blocked | n/a | n/a | n/a | n/a | n/a | There is no machine effects feed like UK's. Amendment semantics live in the PLAW prose, recoverable because they are drafted as explicit operations. |
| Commencement / in-force source | partial | inline in PLAW text | medium | n/a | yes | n/a | no | Effective dates are scattered prose inside the Act ("applies to taxable years beginning after…", "90 days after enactment"). No separate commencement register. |
| Parliamentary package / preparatory works | yes (committee reports, Congressional Record) | blocked (not acquired) | n/a | n/a | yes | n/a | no | Not needed for replay; deferred. |
| Historical snapshots | yes | archive (prior release points + annual historical USC archives) | high | n/a | yes | no | yes (point-in-time oracle) | Prior OLRC release points are the before-tree source. Critical for contamination control. |
| Official PDF scan only | yes (older Statutes at Large) | blocked | n/a | n/a | yes (pre-2013) | no | no | Pre-USLM Statutes at Large exist only as PDF/scan; out of first scope. |

Trustworthy source chain (paragraph): The honest U.S. federal chain is
**release-point USC (before) + Public Law USLM XML (instructions) -> candidate
after-tree -> next release-point USC (oracle), with OLRC classification tables
supplying the witness denominator.** A Public Law is drafted as a sequence of
explicit textual operations against a target Act or USC section. We parse those
operations from the PLAW USLM XML, apply them to the USC title as it stood at
the *prior* release point, and check the result against the USC title at the
*next* release point. The classification table for that Public Law tells us, as
an official fact, how many section-level operations it contained and where they
landed — that is our coverage denominator, independent of how well our extractor
performs. No source family is absent for the first target; preparatory works,
the structured-feed lane, and pre-2013 PDF Statutes at Large are explicitly
deferred and will be represented by `unsupported` / `blocked` inventory rows
rather than silent omission.

---

## 3. Legal structure

- Top-level containers: **Title** (e.g. Title 11 Bankruptcy). A title may be
  subdivided by **subtitle**, **chapter**, **subchapter**, **part**, and
  **subpart** (not all levels appear in every title).
- Chapter / part / title semantics: organizational containers above the section.
  In positive-law titles these are enacted text; in non-positive titles they are
  partly editorial OLRC arrangement.
- Section / article / rule semantics: the **section** (`§`) is the atomic
  citable operative unit and the primary amendment target. USC has no "articles".
- Subsection / paragraph / item semantics: nested designator ladder beneath a
  section: subsection `(a)`, paragraph `(1)`, subparagraph `(A)`, clause `(i)`,
  subclause `(I)`, item `(aa)`, subitem `(AA)`. Amendments routinely target deep
  into this ladder ("Section 362(b)(2)(A)(ii) is amended…").
- Appendices / schedules / annexes: some titles carry an **Appendix**
  (e.g. Title 11 once carried the Bankruptcy Rules in an appendix; Title 5
  App., Title 28 App.). Appendices are first-class structural and must not be
  flattened into the parent title.
- Crossheadings / side notes / rubric behavior: section headings (catchlines)
  are operative-adjacent and are themselves amendment targets
  ("the heading for section 362 is amended…"). USLM marks them as
  `<heading>`.
- Transitional provisions location: usually **not** codified — they live in
  uncodified notes attached to the USC section in USLM `<note>` elements
  (effective-date notes, savings clauses), or remain only in the Statutes at
  Large. This is a primary temporal-semantics hazard (§5).
- Defined-term markup in sources? USLM marks `<term>` for defined terms; not
  load-bearing for replay.
- Editorial notes mixed into operative text? Yes, heavily, in the **consolidated
  USC surface**: source credits, effective-date notes, amendment notes,
  Editorial Notes, and Statutory Notes are USLM `<note>`/`<notes>` wrappers
  interleaved with operative `<section>` bodies. These are editorial projection,
  not enacted operative text, and are a compare-shape hazard (must be stripped
  in the oracle projection, never replayed).
- Tables / forms / enumerations that behave structurally? Yes — fee tables,
  dollar-threshold tables (Title 11 § 104 dollar adjustments), and enumerated
  lists. Tables can be amendment targets ("the table in section 104 is amended").

Can the shared IR represent this directly? Mostly yes. The container/section/
subsection/paragraph/subparagraph/clause ladder maps onto `core/ir.py`
`LegalAddress` + tree nodes. The two-regime identity (USC address vs Act
address) needs a **jurisdiction-local address adapter**: positive-law titles
address USC directly; non-positive titles address the Act and require the OLRC
classification mapping to land on a USC address. The first proof uses
positive-law titles only, so the adapter starts in its simplest form.

---

## 4. Amendment styles

US Public Laws are drafted as explicit operations. Styles observed, with the
canonical `core/ir.py` `StructuralAction` they lower to:

- whole-section replacement ("is amended to read as follows:") —
  `REPLACE` — directly recoverable from act text.
- insert new section ("is amended by inserting after section X the following
  new section:") — `INSERT` with `anchor` — directly recoverable.
- add at the end ("is amended by adding at the end the following:") —
  `INSERT` (append) — directly recoverable.
- repeal ("is repealed", "is hereby repealed") — `REPEAL` — directly
  recoverable.
- strike-and-insert words ("is amended by striking '…' and inserting
  '…'") — `TEXT_REPLACE` via `TextPatchSpec` — directly recoverable,
  but quote/whitespace-sensitive.
- strike words only ("is amended by striking '…'") — `TEXT_REPEAL` —
  directly recoverable.
- redesignate / renumber ("paragraphs (3) through (6) are redesignated as
  paragraphs (4) through (7)") — `RENUMBER` — recoverable only with hard
  parsing (range arithmetic, ordering).
- amend heading / catchline ("the heading for section X is amended to read") —
  `HEADING_REPLACE` — directly recoverable.
- table row / dollar-threshold changes — currently unsupported (table-cell
  granularity below first MVP).
- amendment of "other laws" / multi-target blocks ("the following provisions
  are each amended by striking…") — recoverable only with hard parsing
  (distributive targeting across an enumerated list).
- conditional / scattered commencement ("applies to taxable years beginning
  after the date of enactment") — recoverable from act text but lands in
  `core/temporal.py` ActivationRule, not in the structural op.

First MVP supported families: `REPLACE`, `INSERT`, `REPEAL`, `TEXT_REPLACE`,
`TEXT_REPEAL`, `HEADING_REPLACE`. `RENUMBER`, distributive multi-target, and
table-cell edits are explicit deferred frontier families.

---

## 5. Temporal semantics

Distinct date fields:

- enactment / issued date: the date the President signs the Public Law (carried
  in PLAW metadata). Single, unambiguous.
- publication date: slip-law / Statutes at Large publication; not legally
  operative for effect ordering.
- effective date: **the hard problem.** Frequently *not* the enactment date,
  frequently *scattered* (different provisions of one Act take effect on
  different dates), frequently *conditional* ("applies to bankruptcy cases
  commenced on or after…", "90 days after the date of enactment").
- repeal date: when a section is struck/repealed by a later Public Law.
- prospective / inactive markers: an Act can contain a section that amends the
  Code effective on a future date; the release-point USC may or may not yet show
  it depending on whether the release point post-dates the effective date.
- editorial update date: OLRC release-point identifier (a Public Law number,
  e.g. "current through PL 119-95"). This is the oracle's "as of" pin.
- verification cutoff date: the release point chosen as oracle.

Answers:
- Are commencement dates explicit per amendment? Sometimes per *Act*, sometimes
  per *section of the Act*, sometimes conditional/relative. Not reliably one
  date per amendment.
- Can one act commence on multiple dates? Yes — routine. A single Public Law
  commonly has provisions with different effective dates.
- Can one provision have multiple temporal markers? Yes — a transition rule
  plus a general effective date.
- Are future/prospective provisions shown inline in current text? The USC
  release point reflects what is in force *as of its pin*. A provision effective
  later than the pin will not yet appear. This is exactly why the before/after
  release points must straddle the amendment.
- Does current text leak post-date structure into pre-date views? **This is the
  central contamination risk.** Using a *later* USC release point as the
  pre-amendment base would leak post-amendment structure backward. The before-tree
  MUST be the prior release point (§6).

Unresolved or conditional effective dates become **typed findings**
(`us_effective_date_unresolved`, `us_effective_date_conditional`), routed to
`core/temporal.py` ActivationRule with `kind=pending_condition`, never guessed.

---

## 6. Identity and contamination risks

| Risk | Where detected |
|---|---|
| Using a later/current USC release point as the pre-amendment base (post-amendment structure leaks backward) | source normalization + replay setup (forbidden; before-tree must be prior release point) |
| USLM editorial wrappers (`<note>`, source credits, effective-date notes) mistaken for operative text | source normalization (strip in oracle projection) |
| Non-positive-law title: amendment targets the Act, not the USC; a naive USC-address match is wrong | source normalization / address adapter (first MVP excludes these titles) |
| Smart-quote / dash / NBSP drift in strike-text payloads causes `TEXT_REPLACE` miss on a present target | payload lowering + replay (normalize for match, never mutate payload) |
| Same Public Law number appears across govinfo collections with different formats (USLM vs plaintext vs PDF) | acquisition (pin the USLM artifact, hash it) |
| Redesignation ranges leak future numbering into the before-tree | replay (`RENUMBER` ordering invariant) |
| Classification table editorial mapping treated as an enacted operation | adjudication (witness denominator only, not a replay op) |
| OLRC release-point endpoints slow/timeout, producing partial/truncated archives | acquisition (mirror via govinfo, hash + resume; truncated download is a diagnostic, not a silent partial) |

---

## 7. Oracle story

- Oracle family: **OLRC USC "release points"** — the full United States Code
  pinned to a specific Public Law number (e.g. "through PL 119-95"), published as
  USLM XML per title. Prior release points and annual historical USC archives are
  also available, giving point-in-time before/after pairs.
- Date coverage: release points exist for recent Congresses; annual historical
  archives extend backward. First scope uses the 113th Congress onward
  (2013+, richest USLM coverage).
- Structure quality: high. Official, structured USLM, title/section/subsection
  hierarchy explicit, headings and notes typed.
- Contamination risks: the release point interleaves editorial notes with
  operative text (compare-shape hazard); the "as of" pin is a Public Law number,
  not a calendar date, so straddling must be by PL number ordering. The oracle is
  a **witness, not ground truth** — OLRC editorial classification can itself be
  late or wrong; residuals carry `lawvm_wrong` / `oracle_suspect` /
  `missing_source` and are never silently repaired to match.
- Suitable for end-state verification or only smoke testing? Suitable for
  **end-state verification** of a single title across one release-point window.
  It is an official point-in-time consolidated surface, which is stronger than a
  scraped current page.

---

## 8. First honest target

> We aim first to support **USC section-level amendments** for **a single
> positive-law title over one Public Law release-point window** (default
> expectation Title 11 Bankruptcy; data-driven fallbacks Title 35 Patents /
> Title 18 Crimes), using the **prior OLRC USC release point** as the base seed,
> **Public Law USLM XML from govinfo bulkdata** for semantic lowering, and the
> **next OLRC USC release point** for verification, with **OLRC classification
> tables** as the witness denominator. The first supported effect families are
> `REPLACE`, `INSERT`, `REPEAL`, `TEXT_REPLACE`, `TEXT_REPEAL`, and
> `HEADING_REPLACE`. The frontend explicitly does not yet support non-positive-law
> titles (Act-to-USC classification mapping), `RENUMBER`/redesignation,
> distributive multi-target blocks, table-cell edits, or pre-2013 Statutes at
> Large PDFs.

The exact first title is a fill-in parameter chosen by amendment-witness count
from the classification tables; the architecture assumes a single positive-law
title over one release-point window as the first replay subset.

First evidence claim:

> The first evidence pack will claim only rows with status `replayed` or
> `audited`. Rows with status `unsupported`, `skipped`, `rejected`, `failed`, or
> `unresolved` remain non-claims and are counted separately.

---

## 9. Archetype classification

**Archetype 4 (API/feed-backed corpus), with a positive-law clean-replay core.**
Like New Zealand, the U.S. corpus is reachable through official bulk endpoints
(govinfo bulkdata for PLAW, OLRC download for USC release points) rather than by
scraping presentation HTML, and acquisition must be resumable, rate/timeout-aware,
and archive-first. Unlike NZ, the amendment instructions are explicit
operation prose inside the enacting act (closer to archetype 1's "structured
amendment source"), so clause/payload lowering is recoverable from the act text
rather than from a separate effects feed. The dominant non-acquisition hazard is
**not** clause recovery but **base-tree contamination + scattered effective
dates** — the archetype-3 warning ("do not confuse current surface with replay
substrate") applies with full force.

---

## 10. Open questions

- Q1: Does the chosen first title have any non-positive-law cross-references in
  the amendment window that would require classification mapping even within a
  "positive-law" title? (Verify against the classification table before
  declaring the window clean.)
- Q2: For redesignation-heavy Public Laws, is `RENUMBER` deferral clean, or do
  later supported ops in the same Act depend on a redesignation having already
  applied (ordering dependency)? If so, those dependent ops must be partitioned
  as `blocked-on-frontier`, not silently applied.
- Q3: Are USLM `<note>` boundaries reliable enough to strip editorially in the
  oracle projection without dropping any operative text the Act actually enacted
  as a note? (Compare-shape projection must be validated against a known fixture.)
