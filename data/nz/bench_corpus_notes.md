# NZ Bench Corpus Notes

**Archive**: data/nz_legislation.farchive

## Summary

- Source: New Zealand legislation Farchive (api.legislation.govt.nz v0).
- **Large corpus (`bench_corpus.csv`): every `act_public` work with >0 amendment
  operation witnesses.**
- **Smoke corpus (`bench_corpus_smoke.csv`): a small curated dev slice** pinning
  the known dry-run canaries / divergence repros and otherwise selected to span
  operation families (repeal, substituted, amended, inserted, replaced, etc.).

## Selection Criteria

1. Population: works whose `work_id` begins with `act_public_` (the
   amendment-bearing public-act class targeted by the NZ dry-run repeal surface
   and coverage scoreboard). The full archive can be scanned with
   `--work-id-prefix ""`, but the shipped corpora are scoped to `act_public`.
2. Amendment filter: a work is kept only when `build_operation_surface` yields
   `>= 1` amendment operation witness row (equivalently, the parsed source
   `history_witnesses` count is positive). Enacted-but-unamended works (the
   majority) carry zero witnesses and are excluded.
3. Works whose latest archived version cannot be parsed (metadata-only, no
   archived XML) are skipped and counted as `parse_failed`, never silently
   dropped.

## Smoke Curation

- Pinned canaries (always present): `act_public_2005_87`, `act_public_2009_38`,
  `act_public_1993_110`, `act_public_1992_122`, `act_public_1955_37`,
  `act_public_1981_23`.
- Remaining budget filled by a deterministic greedy pass that prefers works
  introducing operation families not yet covered (ties broken by `work_id`),
  then tops up in `work_id` order. Default size: 30 works.

## CSV Schema

```
work_id,work_type,year,n_amendment_operations,n_history_witnesses,operation_families,latest_version_id
```

`work_id` is the only column the bench tooling reads (`nz-corpus dry-run-corpus
--corpus`, `nz-corpus benchmark --corpus`). The remaining columns are
deterministic provenance / diversity metadata. `operation_families` is a
`family:count` map joined by `;`, sorted by family.

## Regeneration

```
lawvm nz-corpus build-corpus --out-dir data/nz --smoke-size 30
```

Deterministic: works are scanned in lexicographic order, the `>0` filter is
exact, and there is no clock or randomness. The run prints the scanned
population, parse-failed / zero-op counts, and the final large/smoke sizes.
