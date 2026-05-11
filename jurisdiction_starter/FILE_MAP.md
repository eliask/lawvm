# <JURISDICTION> repo file map

This file says what the eventual frontend package should look like and what each module owns.

Do not cram everything into one `grafter.py`.

---

## 1. Recommended module layout

```text
src/lawvm/<code>/
    __init__.py
    sources.py
    inventory.py
    evidence.py             # row/finding/evidence-pack emitters
    fetch.py                # only if live/public fetch is part of the boundary
    grafter.py              # parse current / official artifacts into IR or intermediate surfaces
    replay.py
    verify.py
    commencement.py         # if commencement is a meaningful separate lane
    source_adjudication.py
```

Add more modules only when a phase boundary or source family genuinely needs one.

---

## 2. Ownership by file

### `sources.py`
Owns:
- source path resolution,
- locators,
- archive iteration,
- raw artifact loading.
- local substrate identity: content hashes, git objects, archive members, or
  fixture paths.

Must not own:
- replay semantics,
- verification logic,
- broad heuristic parsing.

### `inventory.py`
Owns:
- source inventory,
- status summaries,
- replayability classification at source level.
- omitted, skipped, unsupported, and blocked source-unit rows.

Must not own:
- detailed replay,
- semantic lowering.

### `evidence.py`
Owns:
- operation/effect row serialization,
- replay and audit row serialization,
- findings JSONL emission,
- evidence-pack claim/non-claim summaries.

Must not own:
- parsing doctrine,
- source authority decisions,
- replay mutation semantics.

### `fetch.py`
Owns:
- live acquisition,
- storage policy,
- caching policy,
- mirroring to canonical locators.

Must not own:
- semantic parse,
- replay.

### `grafter.py`
Owns:
- current IR parse,
- official-act parse,
- clause/payload/effect lowering if still small enough.

Must not become:
- an unreviewable everything-module.
When it gets too large, split by phase surface, not by random helper accumulation.

### `commencement.py`
Owns:
- commencement sidecars,
- contingent effective-date resolution,
- commencement reports.

### `replay.py`
Owns:
- applying canonical effects,
- replay result object,
- replay ordering and invariants.

Must not own:
- raw source scraping,
- broad source inference.

### `verify.py`
Owns:
- replay-vs-oracle comparison,
- divergence partitioning,
- coverage summaries.
- audit rows and evidence-pack verification summaries.

Must not own:
- raw effect compilation.

### `source_adjudication.py`
Owns:
- typed adjudication families and classification helpers.

Must not own:
- replay execution,
- source fetching.

---

## 3. Starter implementation order

Recommended order:

1. `sources.py`
2. `inventory.py`
3. `evidence.py` minimal findings and summary emitters
4. `grafter.py` current IR
5. official-act / structured-amendment parse in `grafter.py` or split module
6. `replay.py`
7. `verify.py`
8. `commencement.py`
9. `source_adjudication.py` refinement and partitioning helpers

---

## 4. When to split modules

Split when one of these becomes true:

- the file owns multiple phase claims,
- the file mixes acquisition and replay,
- the file mixes source pathology and replay bugs,
- the file crosses ~1–2 stable phase waists and review becomes unclear.

The split criterion is semantic ownership, not line count alone.
