# <JURISDICTION> roadmap to ~full LawVM ideal

This roadmap is the staged path from “source-visible” to “historically replayable and verified”.

It is written to keep the frontend honest at every milestone.

---

## 1. End state

The intended end state is:

- source-honest archival of all important source families,
- archive-first or clone-first local replay substrate,
- inventory-first corpus reporting,
- typed source record and capability reporting,
- current-surface IR parse,
- official-act or structured-amendment semantic lowering,
- explicit clause/payload/effect waists,
- preserved unsupported, skipped, and rejected rows,
- deterministic replay with typed adjudications,
- verification against an independent oracle,
- findings JSONL with stable rule ids,
- evidence-pack summaries separating claims from non-claims,
- historical rebuild/recovery for contaminated current surfaces,
- reporting that partitions replay defects from source sparsity.

If the jurisdiction cannot reach that end state with public sources, the roadmap must say where it stops and why.

---

## 2. Milestones

## M0. Inventory honesty
Deliverables:
- P0 inventory
- local substrate manifest
- source metadata report
- current/public/historical availability map
- skipped/unsupported source-unit rows

Exit gate:
- reviewers know exactly what source families exist.
- reviewers know which source units are claimable and which are non-claims.

## M1. Acquisition and provenance
Deliverables:
- P1 archive
- canonical locators
- P2 source record / bundle
- acquisition diagnostics for live/API/feed sources
- resumable acquisition frontier state where corpus sync is long-running or
  rate-limited

Exit gate:
- raw and derived artifacts are clearly separated.
- retry, quota, and unavailable-source behavior is visible.

## M2. Current parse
Deliverables:
- P3 current IR
- source-tree summary where source XML/HTML has meaningful pre-IR structure
- consolidated snapshot diff report where multiple official versions exist
- current-structure fixtures
- contamination notes

Exit gate:
- current text can be parsed honestly without replay claims.

## M2.5. Source closure
Deliverables:
- source-declared dependency extraction from history notes, effect feeds,
  registry links, version graphs, or equivalent witnesses
- unresolved dependency rows with source locators and reasons
- acquisition-frontier expansion for reachable amending or related works

Exit gate:
- reviewers know which amendment/source families have been fetched and which
  remain blocked before semantic lowering begins.

## M3. Amendment semantics surface
Deliverables:
- P4 official-act parse or structured-amendment normalization
- P5 clause surface
- P6 payload surface

Exit gate:
- the frontend can explain amendment meaning before replay.

## M4. Canonical effects
Deliverables:
- P7 canonical effects
- effect fixtures
- unsupported families typed

Exit gate:
- replay inputs are typed and inspectable.

## M5. Replay MVP
Deliverables:
- P8 replay for the smallest honest supported family
- invariant checks
- replay adjudications

Exit gate:
- the frontend can replay a narrow supported subset end-to-end.

## M6. Verification
Deliverables:
- P9 verification/oracle compare
- partition report
- benchmark set

Exit gate:
- replay defects are separated from source-sparse and compare-shape issues.

## M7. Historical recovery
Deliverables:
- P10 recovery planner
- older-base or reverse-chain strategy where needed
- historical rebuild fixtures

Exit gate:
- pre-amendment contaminated surfaces are addressed explicitly.

## M8. Work queue and industrialization
Deliverables:
- P11 work queues / progress reports
- coverage and blocker reports
- agent task decomposition
- findings JSONL
- evidence-pack claim/non-claim summaries

Exit gate:
- the jurisdiction can scale by bounded task cards.

---

## 3. Archetype-specific notes

### Sweden-like roadmap
You likely need:
- strong P4 official-act text surface,
- real P5 clause surface,
- real P6 payload extraction,
- strong P10 historical recovery, because current surfaces may be contaminated by later structure.

The main risk is pretending that current consolidated text is enough. It is not.

### Norway-like roadmap
You likely get:
- easier P5/P7 via structured amendment metadata,
- quicker replay MVP,
- sidecar-heavy commencement and inventory/reporting work,
- a need to strengthen oracle partitioning and longer-range historical completeness.

The main risk is letting structured source shortcuts bypass explicit waists. Do not do that; emit synthetic equivalent artifacts.

### UK-like roadmap
You likely get:
- strong structured effects metadata,
- difficult compare-shape and replay-target issues,
- strong need for typed source-pathology vs compare-shape vs replay defect separation.

---

## 4. Full-ideal checklist

The frontend is near LawVM ideal when all are true:

- [ ] base seed source is honest
- [ ] replay substrate is local and inventoried before claims
- [ ] amendment semantics source is honest
- [ ] current IR exists
- [ ] official-act / structured-amendment surface exists
- [ ] clause surface exists
- [ ] payload surface exists
- [ ] canonical effects exist
- [ ] unsupported, skipped, and rejected rows remain visible
- [ ] replay exists with invariants and typed skips
- [ ] verification exists with partitions
- [ ] findings JSONL uses stable rule ids
- [ ] evidence-pack summary separates claims from non-claims
- [ ] historical recovery plan exists
- [ ] reports/work queues exist
- [ ] agents can work via bounded task cards without re-deriving architecture
