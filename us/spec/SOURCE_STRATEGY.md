# U.S. federal statutory source strategy

This file answers: what sources are authoritative for which claims?

It is the anti-handwaving document. If the frontend later cheats by using the
wrong surface for the wrong claim, this file should make that obvious.

---

## 0. Acquisition reality (as built)

From outside the U.S., **OLRC `uscode.house.gov` is geo-blocked**. Every source
that depends on OLRC (USC release points, classification tables) is therefore
unreachable from the build host. govinfo bulkdata works without a key.

What is **acquired and archived today** (the unblocked half):

- **Amendment source = govinfo bulkdata PLAW USLM XML.** One zip per Congress at
  `https://www.govinfo.gov/bulkdata/PLAW/{congress}/public/PLAW-{congress}-public.zip`,
  keyless, root element `<pLaw xmlns="http://schemas.gpo.gov/xml/uslm" ...>`.
  Each member is one law (`PLAW-118publ5.xml`). Stored at canonical locator
  **`us://plaw/{congress}/publ{N}.xml`** (private-law `pvtl` members are filtered;
  the public bulkdata zips contain none). Ingest mirrors Finland's `import-zip`:
  see `src/lawvm/us_federal/import_plaw.py` and `src/lawvm/us_federal/sources.py`,
  archive `data/us_federal.farchive`. Inventory: `src/lawvm/us_federal/inventory.py`.

- **USC verification oracle = govinfo USCODE annual-edition `.htm` (KEYLESS).**
  The govinfo USCODE annual editions are reachable without any key at
  `https://www.govinfo.gov/content/pkg/USCODE-{year}-title{N}/html/USCODE-{year}-title{N}.htm`
  (`application/xhtml+xml`, well-formed XHTML 1.0 Transitional). There is **no
  USLM-USC** here (USLM-USC is OLRC-only and geo-blocked). Each annual edition is
  stored at canonical locator **`us://usc/{year}/title{N}.htm`** (storage_class
  `html`) with metadata `{year, title, source_url, sha256, laws_enacted_through,
  publication_name}`. Ingest mirrors `import_plaw`: see
  `src/lawvm/us_federal/import_usc.py`. The xhtml is parsed into a typed
  per-section source tree (`src/lawvm/us_federal/source_tree.py`) and the
  per-section `source-credit` amendment lineage into typed Public Law witnesses
  (`src/lawvm/us_federal/usc_witness.py`).

  - **Granularity: per-annual-edition** (not per-Public-Law). The oracle is the
    annual edition's section-level surface (section address + normalized
    statutory text); the dry-run compares a materialized after-tree against it.
  - **Coverage denominator = `source-credit` witnesses.** Each section's
    `source-credit` enumerates every Public Law that enacted/amended it; this is
    the witness-anchored denominator, with **no OLRC classification tables
    needed** (those remain geo-blocked). `usc_witness.count_in_window` counts
    (section, Public Law) witnesses in a Congress/PL window.
  - **Address convention** (shared with op-lowering):
    `(("title","11"),("section","362"),("subsection","c"),("paragraph","1"),
    ("subparagraph","A"),("clause","i"))`. Sections are title-global; chapters/
    subchapters are structural containers only (recorded from the `expcite`
    comment, not in the replay address).
  - **Editorial exclusion.** Only `statutory-body*` paragraphs between the
    `field-start:statute`/`field-end:statute` markers are statutory text;
    `note-*`/`analysis`/`subchapter-head` and the `source-credit` itself are
    editorial and excluded from the comparison surface.

What is **NOT built (blocked / deferred)**:

- **OLRC USC release points (USLM).** The OLRC release-point endpoint in §3 is
  geo-blocked from here and is not the acquisition path; the keyless govinfo
  annual `.htm` edition above is the substitute oracle surface.
- **OLRC classification tables** (PL § → USC §) are geo-blocked and unreachable.
  They were the intended witness-anchored coverage denominator. Until/unless
  reachable, the coverage denominator must instead come from the **USLM
  source-credit / history `<note>`s inside the USC oracle XML** (a substitute
  denominator), recorded as such — not from the classification tables.

The §1-§10 design below remains the target; this section records where the
acquired reality currently diverges from it.

---

## 1. Source roles

| Claim | Source family | Why this source is allowed | Why other nearby sources are not sufficient |
|---|---|---|---|
| Base-act seed | **Prior** OLRC USC release-point USLM XML for the target title | It is the official consolidated Code as it stood *before* the amendment window — the only honest pre-amendment tree | The current/next release point is post-amendment and would leak future structure; the Public Law text is the instruction, not the base |
| Amending semantics | Public Law USLM XML (govinfo bulkdata PLAW) | The Act is drafted as explicit textual operations ("striking… inserting…", "is repealed", "adding at the end") that lower directly to canonical ops | Classification tables give *where* an op landed but not the payload; the USC surface shows the *result*, not the instruction |
| Effective dates / commencement | Inline effective-date prose in the Public Law text (+ USC `<note>` effective-date notes as corroboration) | The Act itself states when each provision takes effect | The release point only shows in-force-as-of-pin state; it cannot tell you a per-provision effective date |
| Verification oracle | **Next** OLRC USC release-point USLM XML for the target title | Official point-in-time consolidated surface pinned to a Public Law number; an independent end-state witness | The Public Law is the input, not an independent check; using it as oracle would be circular |
| Recovery / historical rebuild | Annual historical USC archives + prior release points; pre-2013 Statutes at Large PDFs (deferred) | Provides earlier before-trees for windows before USLM coverage | Current USC cannot be walked backward without the amendment chain |

---

## 2. Source ranking

### Base seed
1. Prior OLRC USC release-point USLM XML for the target title (preferred).
2. Annual historical USC archive XML at the closest pre-window pin.
3. (Deferred) reconstructed base from earlier Statutes at Large — only as an
   explicit recovery claim, never as default replay base.

### Amendment semantics
1. Public Law USLM XML from govinfo bulkdata PLAW (preferred — structured).
2. Public Law plaintext from govinfo (fallback when USLM is absent; lower
   confidence, emits a source-quality finding).
3. (Forbidden as semantics) USC release-point diff — that is a witness, not an
   instruction.

### Commencement
1. Inline effective-date clause parsed from the Public Law text.
2. USC `<note>` effective-date note as corroboration.
3. Default-to-enactment only when the Act is explicitly silent, recorded as
   `us_effective_date_defaulted_to_enactment` (a finding, not a guess).

### Verification
1. Next OLRC USC release-point USLM XML for the target title (preferred).
2. Annual historical USC archive at the next available pin.
3. (Smoke only) current USC release point, when no intermediate pin exists —
   flagged as a coarse oracle.

---

## 3. Archival plan

For each source family:

- **Prior/next USC release point (title XML)**
  - real locator: `https://uscode.house.gov/download/releasepoints/us/pl/{congress}/{num}/xml_usc{NN}@{congress}-{num}.zip`
  - canonical logical locator: `us://usc/release/pl{congress}-{num}/title{N}.xml`
  - local substrate: `archive` (farchive member), extracted from the release-point zip.
  - identity: SHA-256 of the title XML member.
  - storage class: `xml` (zip member).
  - immutability: a release point is immutable once published (pinned to a PL
    number); never re-fetch over an existing hash silently.
  - refresh TTL: none for a pinned release point; new release points are new
    locators, not refreshes.
  - derived separate? yes — parsed source-tree JSON is stored under a derived
    namespace, never overwriting the raw XML.

- **Public Law (PLAW USLM XML)**
  - real locator (per-law member): `https://www.govinfo.gov/bulkdata/PLAW/{congress}/public/PLAW-{congress}publ{num}.xml`
  - real locator (per-Congress zip, the acquired form): `https://www.govinfo.gov/bulkdata/PLAW/{congress}/public/PLAW-{congress}-public.zip`
  - canonical logical locator: `us://plaw/{congress}/publ{N}.xml` (as built)
  - local substrate: `archive` (farchive).
  - identity: SHA-256 of the PLAW XML.
  - storage class: `xml`.
  - immutability: an enacted Public Law is immutable; pinned by content hash.
  - refresh TTL: none.
  - derived separate? yes — parsed clause/operation rows are derived artifacts.

- **OLRC classification tables** — *unreachable from this host (geo-blocked).*
  Designed denominator below; in practice the witness-anchored coverage
  denominator must come from USC-oracle USLM source-credit/history notes instead
  (see §0 and §9), until OLRC is reachable.
  - real locator: `https://uscode.house.gov/classification/tables.shtml` (index)
    and the per-Public-Law table pages linked from it.
  - canonical logical locator: `us://classification/{plNum}`
  - local substrate: `archive` (farchive), HTML.
  - identity: SHA-256 of the table HTML.
  - storage class: `html`.
  - immutability: editorial; may be revised by OLRC — record fetch timestamp and
    re-fetch produces a new hashed version, both retained.
  - refresh TTL: low priority; re-check on a new release point.
  - derived separate? yes — the parsed witness-operation list (PL § + Stat. cite
    -> USC §) is a derived JSON artifact.

### Required rule

Raw source bytes (release-point zips, PLAW XML, classification HTML) remain
archived separately from any cleaned or derived text. Replay, verification, and
audit jobs consume the local farchive substrate only. Network fetching is an
acquisition phase that must produce a local archive or manifest before replay
begins.

### API/feed acquisition rule

govinfo and OLRC are HTTP bulk endpoints, not authenticated APIs, so secrets are
not in play — but the resumable-acquisition discipline still applies:

- request identity stored in the archive: method + full URL + request headers
  (User-Agent), excluding any cookies.
- response identity stored: HTTP status, `Content-Type`, `Content-Length`,
  `Last-Modified`/`ETag` where present, and the SHA-256 of the body.
- local cache key: the canonical logical locator above; archive member by locator.
- pagination/cursor: govinfo bulkdata is directory-indexed per Congress;
  the acquisition frontier enumerates `{congress}/public/` listings and records
  which Public Law numbers were seen vs fetched.
- rate-limit / timeout behavior: **OLRC release-point endpoints are slow and
  timeout-prone.** Acquisition mirrors the same artifacts via govinfo where
  possible, uses bounded retries with backoff, and on persistent failure emits an
  `acquisition_diagnostics.jsonl` row (`us_acq_timeout`, `us_acq_truncated`)
  rather than writing a partial artifact. A truncated download whose byte length
  disagrees with `Content-Length` is rejected, not stored as the substrate.
- resumable frontier: `acquisition_frontier_state.json` records, per Congress and
  title, which release-point zips and PLAW XMLs are fetched, pending, or failed,
  so a long sync resumes without re-fetching hashed artifacts.
- diagnostics for 429/403/timeout/schema-drift/unavailable: each becomes a
  diagnostics row with a stable rule id; none is hidden by a retry loop.
- secrets rule: no tokens are used; even so, no request headers that could carry
  identity beyond User-Agent are persisted.

### Local substrate table

| Source family | Local substrate | Required identity | Replay role | If absent, emitted row/status |
|---|---|---|---|---|
| Prior USC release-point title XML | archive (farchive) | SHA-256 of title XML member | base | `blocked` (no honest pre-tree) |
| Public Law USLM XML | archive (farchive) | SHA-256 of PLAW XML | amendment | `skipped` (act not yet acquired) / `unsupported` (no USLM, plaintext only) |
| Next USC release-point title XML | archive (farchive) | SHA-256 of title XML member | oracle | `source_sparse` (no oracle window) |
| OLRC classification table | archive (farchive) | SHA-256 of table HTML | witness (denominator) | `unsupported` (witness count unavailable -> coverage unpinned) |
| PLAW plaintext fallback | archive (farchive) | SHA-256 of text | amendment (low-confidence) | `unsupported` |

The table records what LawVM reads. It does not itself prove legal authority;
authority remains a frontend-local source-role claim.

---

## 4. Canonical locator examples

- Base act current locator: `us://usc/release/pl119-18/title11.xml`
  (illustrative prior release point for Title 11).
- Base act promulgation locator: not applicable as a single act — the title is
  consolidated; the enacting acts are the PLAW locators below.
- Amending act locator: `us://plaw/119/publ95.xml`
  (real: `https://www.govinfo.gov/bulkdata/PLAW/119/public/PLAW-119publ95.xml`).
- Amendment-register locator: `us://classification/PL119-95`
  (parsed from the OLRC classification table for that Public Law).
- Commencement locator: inline span inside `us://plaw/119/PL119-95.xml`
  (no separate register); derived `us://derived/commencement/PL119-95.json`.
- Oracle locator: `us://usc/release/pl119-95/title11.xml` (next release point).
- Derived clause surface locator: `us://derived/clause/PL119-95/title11.json`.
- Derived canonical effects locator: `us://derived/effects/PL119-95/title11.json`.

---

## 5. Synthetic-equivalent artifact policy

The Public Law already contains structured amendment-instruction prose, but the
prose is not a closed canonical op. The clause/payload/effect waists are
therefore **real**, not compressed — they do genuine lowering work — and each
emits an inspectable artifact:

- P5 clause surface: `real`. Each parsed amendment instruction emits a
  `clause_surface.json` row recording the source PLAW span, the target
  expression as written ("section 362(b)(2)(A)(ii)"), the action word
  ("striking", "inserting", "repealed", "adding at the end"), evidence span, and
  confidence.
- P6 payload surface: `real`. Quoted strike/insert text and inserted-section
  bodies are extracted into `payload_surface.json` with the raw quoted bytes
  preserved separately from any normalized-for-match form.
- P7 canonical effects: `real`. Lowered to `core/ir.py` `LegalOperation` /
  `TextPatchSpec` with resolved target `LegalAddress`, emitted into
  `operation_effect_rows.jsonl`.

Synthetic artifacts (if any later) remain inspectable because every lowering step
carries the source PLAW locator + span and a stable witness rule id, so review
does not disappear inside the compiler.

---

## 6. Forbidden shortcuts

- Using the current or any *later* USC release point as the pre-amendment base.
  The before-tree MUST be the prior release point. (Primary contamination
  forbidden.)
- Using the verification oracle (next release point) as the replay substrate or
  as the amendment-instruction source. The oracle only checks the end state.
- Treating a USLM editorial wrapper (`<note>`, source credit, effective-date
  note) as enacted operative text or as a replay operation.
- Treating an OLRC classification-table mapping as an enacted canonical operation
  — it is the witness denominator, not a payload.
- Inferring the amendment payload from the USC before/after diff instead of from
  the Public Law text. The diff is a witness, not an instruction.
- Defaulting a missing effective date to enactment without emitting a finding.
- For non-positive-law titles (out of first scope): matching an Act-section
  amendment directly against a USC address without the classification mapping.

---

## 7. Known source failures

| Source family | Failure mode | Expected adjudication owner |
|---|---|---|
| Public Law USLM XML | smart-quote / dash / NBSP drift in quoted strike text breaks exact match | payload lowering / replay (source pathology: `us_payload_quote_drift`) |
| Public Law USLM XML | distributive multi-target instruction ("each amended by striking…") | clause lowering (unsupported: `us_distributive_target`) |
| Public Law text | effective date conditional/relative/absent | temporal (source pathology: `us_effective_date_unresolved`) |
| USC release point | editorial notes interleaved with operative body | oracle projection (compare-shape: `us_editorial_note_wrapper`) |
| USC release point | release-point pin is a PL number, not a date; mis-straddling | source normalization (`us_release_point_misstraddle`) |
| Classification table | OLRC mapping late or revised after the release point | adjudication (`oracle_suspect` on denominator) |
| OLRC download endpoint | slow/timeout/truncated zip | acquisition (`us_acq_timeout` / `us_acq_truncated`) |
| OLRC (uscode.house.gov) | **geo-blocked** from build host — USC release points + classification tables unreachable | acquisition (`us_usc_oracle_unavailable`) |
| govinfo USCODE oracle | needs free `api.data.gov` key (not configured); USLM-vs-htm format + per-PL-vs-annual granularity **open decision** | acquisition / adjudication (open) |

### Open decision (USC oracle)

The USC verification oracle is **not built**. Two questions are explicitly open:

1. **Format** — does the govinfo USCODE collection expose USLM XML per title, or
   only `.htm`? (USLM is required to compare end-state shape against replay.)
2. **Granularity** — is the oracle pinned per Public Law (a true point-in-time
   straddle witness) or only per annual edition (coarser, may bundle several PLs)?

Until resolved (and a key configured), the frontend has **no oracle** and every
end-state claim stays a non-claim; the witness denominator falls back to USLM
source-credit notes (see §0/§9), not OLRC classification tables.

---

## 8. Minimum viable source chain

> The frontend will not claim replay support unless it has:
> 1. the **prior** OLRC USC release-point title XML (base),
> 2. the **Public Law USLM XML** that amends that title (amending semantics),
> 3. an effective-date determination per provision **or** an explicit
>    `us_effective_date_unresolved` status,
> 4. the **next** OLRC USC release-point title XML as oracle (or an explicit
>    `source_sparse` absence).

If any one of those is absent, the frontend downgrades its capability claim
(e.g. to "current IR supported" or "official-act lowering supported") and the
affected rows stay non-claims.

---

## 9. Dependency and closure strategy

- Seed source family: a chosen Public Law (or a title + release-point window).
- Dependency witness family: OLRC classification tables (PL § -> USC §) and the
  USC `<note>` source-credit chain naming amending Public Laws. **Acquisition
  reality:** OLRC classification tables are geo-blocked and unreachable, so the
  USC-oracle source-credit/history `<note>`s are the available witness denominator
  today; the classification-table denominator is deferred until OLRC is reachable.
- Dependency edge types: "Public Law X amended USC title T section S";
  "section S has source-credit referencing PL Y".
- Transitive dependency: to replay a window for a title, acquire every Public
  Law whose classification table touches that title within the window.
- Archived before semantic claims: for each dependency, the PLAW XML + its
  classification table, both hashed, before any operation row is claimed.
- Unresolved dependency rows: a classification-table entry naming a Public Law
  whose USLM XML is not yet acquired becomes an unresolved-dependency row
  (`us_dependency_unacquired`), not a silent gap.
- Consolidated snapshot versions: the release points are fetched as **both**
  replay targets (before/after) and witnesses (version-diff surface).
- Avoiding dependency-as-effect: a classification-table edge counts toward the
  witness denominator and corpus closure, but it is never lowered to a canonical
  operation — only the PLAW instruction prose is.

---

## 10. Inventory-first contract

Before claiming parsing, replay, or verification, the frontend inventories the
declared local substrate. Inventory preserves:

- input roots and source artifact ids (release-point zips, PLAW XMLs, table HTML),
- discovered USC title/section ids and Public Law numbers,
- artifact-to-unit links (which PLAW touches which USC sections, per the table),
- source role for each artifact (base / amendment / oracle / witness / auxiliary),
- SHA-256 for each artifact,
- omitted/skipped/unsupported/blocked artifact records with reasons (e.g.
  PLAW present only as plaintext -> `unsupported`; release point missing ->
  `blocked`),
- assumptions used to group artifacts into a replay window (which prior/next
  release points straddle which Public Law).

An empty accepted-operation set is still meaningful if the inventory explains
which Public Laws and USC sections existed and why they were not claimable.
