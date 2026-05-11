# <JURISDICTION> source strategy

This file answers: what sources are authoritative for which claims?

It is the anti-handwaving document. If the frontend later cheats by using the wrong surface for the wrong claim, this file should make that obvious.

---

## 1. Source roles

Fill this table for the intended frontend.

| Claim | Source family | Why this source is allowed | Why other nearby sources are not sufficient |
|---|---|---|---|
| Base-act seed |  |  |  |
| Amending semantics |  |  |  |
| Effective dates / commencement |  |  |  |
| Verification oracle |  |  |  |
| Recovery / historical rebuild |  |  |  |

---

## 2. Source ranking

Rank sources for each purpose.

### Base seed
1.
2.
3.

### Amendment semantics
1.
2.
3.

### Commencement
1.
2.
3.

### Verification
1.
2.
3.

---

## 3. Archival plan

For each source family, define:

- real locator form,
- canonical logical locator form,
- local substrate form (`archive`, `extracted_archive`, `git_clone`,
  `fixture_dir`, `manifest`, or explicit `blocked`),
- local path or manifest field that will identify the bytes or git object,
- storage class (`html`, `xml`, `pdf`, `json`, `text`, etc.),
- immutability expectations,
- refresh TTL,
- whether cleaned/derived artifacts are stored separately.

### Required rule

Raw source bytes must remain archived separately from any cleaned or derived text.

Replay, verification, and audit jobs consume local substrate only. Network
fetching is an acquisition phase that must produce a local archive, clone,
fixture, or manifest before replay begins.

### Local substrate table

| Source family | Local substrate | Required identity | Replay role | If absent, emitted row/status |
|---|---|---|---|---|
|  |  | content hash / git object / archive member | base / amendment / oracle / witness / auxiliary | skipped / unsupported / blocked |
|  |  |  |  |  |

The table records what LawVM reads. It does not itself prove legal authority;
authority remains a frontend-local source-role claim.

---

## 4. Canonical locator examples

Write concrete examples.

- Base act current locator:
- Base act promulgation locator:
- Amending act locator:
- Amendment-register locator:
- Commencement locator:
- Oracle locator:
- Derived clause surface locator:
- Derived canonical effects locator:

---

## 5. Synthetic-equivalent artifact policy

If a source already contains structured amendment targeting, we still emit synthetic waists.

State the compressed phases here.

Template:

- P5 clause surface: `<real | synthetic | blocked>`
- P6 payload surface: `<real | synthetic | blocked>`
- P7 canonical effects: `<real | synthetic | blocked>`

Explain how the synthetic artifacts remain inspectable and reviewable.

---

## 6. Forbidden shortcuts

List shortcuts the frontend may not take.

Examples:
- using current consolidated text as pre-amendment base,
- using verification oracle as replay substrate,
- treating editorial HTML markers as legal semantics,
- inferring historical structure from current numbering alone.

Write jurisdiction-specific forbiddens here.

---

## 7. Known source failures

For each source family, list known pathologies and where they must be adjudicated.

| Source family | Failure mode | Expected adjudication owner |
|---|---|---|
|  |  |  |

---

## 8. Minimum viable source chain

State the smallest source chain that still counts as honest.

Template:

> The frontend will not claim replay support unless it has:
> 1. `<base source>`
> 2. `<amending source>`
> 3. `<effective-date source or explicit status>`
> 4. `<oracle or explicit absence>`

If any one of those is absent, the frontend must downgrade its capability claim.

---

## 9. Inventory-first contract

Before the frontend claims parsing, replay, or verification support, it must be
able to inventory the declared local substrate.

Inventory must preserve:

- input roots and source artifact ids,
- discovered statute, publication, amendment, transition, or local unit ids,
- artifact-to-unit links,
- source role for each artifact,
- content hash or git object id where available,
- omitted, skipped, unsupported, or blocked artifact records with reasons,
- assumptions used to group artifacts into transitions.

An empty accepted-operation set is still meaningful if the inventory explains
which units existed and why they were not claimable.
