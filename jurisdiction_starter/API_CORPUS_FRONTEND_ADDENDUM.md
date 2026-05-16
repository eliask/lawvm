# API/feed corpus frontend addendum

Use this addendum when a jurisdiction exposes legislation through an API, feed,
registry export, or local source repository. It captures the acquisition and
source-closure lessons that should not be rediscovered inside production code.

The model is: live source access builds a local corpus substrate; LawVM replay,
verification, and audit consume that substrate. A live API response is not a
replay proof.

---

## 1. Source-access contract

Fill this table for each API, feed, registry, or repository.

| Source lane | Access method | Secret handling | Local substrate | Replay role | If unavailable |
|---|---|---|---|---|---|
|  | API / feed / git / download | env var / none / cookie | farchive / clone / manifest / fixture | base / amendment / oracle / witness / auxiliary | skipped / blocked / unresolved |

Rules:

- Secrets must be read only at request time and must not enter archive keys,
  diagnostics, findings, state files, or console output.
- Store request identity without secrets.
- Store response identity with status, content type, retrieval time, and useful
  rate-limit headers where available.
- Store raw bytes separately from normalized, parsed, or derived artifacts.

---

## 2. Acquisition frontier

Long-running corpus acquisition needs a resumable frontier.

Minimum state:

- source lane and run id,
- completed pages, cursors, work ids, version ids, or commit ids,
- queued but not fetched source units,
- failed source units and retry disposition,
- rate-limit reset information if known,
- unresolved dependencies,
- last successful archive member identity.

Acquisition may sleep, resume, or stop at a request budget. It must not mark a
corpus complete unless the frontier is empty or all remaining rows are typed as
blocked/unavailable.

---

## 3. Rate-limit behavior

Declare the frontend behavior for:

- `429` or equivalent quota responses,
- `403` responses that may represent quota exhaustion,
- missing or malformed `Retry-After` / reset headers,
- daily quota reset assumptions,
- maximum retry attempts before sleeping or stopping,
- whether unattended sync is allowed to sleep until reset,
- what is written to diagnostics for every blocked request.

The diagnostic row should make it possible to distinguish:

- transient quota exhaustion,
- permanent authorization failure,
- source artifact not found,
- API schema drift,
- unsupported source lane.

---

## 4. Dependency closure

If source records expose amendment links, history notes, effect feeds, or version
relationships, define dependency closure before semantic lowering.

Minimum report fields:

- seed work id and version id,
- witness source locator,
- raw citation or edge payload,
- normalized candidate id, if any,
- confidence and parse diagnostics,
- whether the dependency was fetched,
- whether it remains unresolved,
- why unresolved edges are non-claims.

Dependency edges are acquisition evidence. They do not become canonical
operations until P4-P7 have compiled amendment semantics.

---

## 5. Source-tree and snapshot witnesses

Structured XML/HTML snapshots should usually produce a source-tree artifact
before any jurisdiction-specific IR or replay claim.

A source-tree artifact may claim:

- source labels and headings,
- source paths and source ids,
- body text witnesses,
- deletion or repealed markers that appear in source,
- attached history or amendment notes,
- counts and source-shape diagnostics.

It must not claim:

- legal identity migration over time,
- that a text delta is a compiled amendment,
- replay success,
- oracle correctness.

If multiple consolidated versions exist, a snapshot-diff report is useful as a
witness and triage surface. It remains compare evidence until tied to compiled
canonical effects.

---

## 6. Agency/API feedback ledger

For beta APIs or public-interest agency contacts, keep a feedback ledger. The
ledger should be separate from replay findings and should list:

- API feature gaps that block source-honest LawVM use,
- schema ambiguities,
- missing source links,
- unavailable historical versions,
- rate-limit and bulk-access concerns,
- documentation mismatches,
- examples where official source surfaces are internally inconsistent.

This is not an error report against the legal text unless independently
verified by replay/oracle adjudication.

---

## 7. New Zealand relation

New Zealand is a concrete API-corpus archetype:

- the local substrate is an farchive populated from the Legislation API and XML
  URLs;
- latest/current XML can provide structural source-tree witnesses;
- history notes and reprint amendment notes can seed dependency closure;
- consolidated versions can provide snapshot-diff witnesses;
- semantic replay still requires official amendment parsing and canonical
  effect lowering.

The NZ pattern should inform future API-backed jurisdictions, but it should not
be copied as a universal law-source ontology. Each jurisdiction must still fill
`SOURCE_STRATEGY.md`, `PHASE_PLAN.md`, and `ADJUDICATION_PLAN.md` for its own
source authority story.
