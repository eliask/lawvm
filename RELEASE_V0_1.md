# LawVM v0.1 Alpha / Research Preview

LawVM treats legislation as an executable state transition system. Amendment
acts are compiled into typed operations, replayed over statute structure, and
checked against source and consolidated witness surfaces.

v0.1 is an alpha research preview. It is intended for researchers, legal
informatics developers, public-sector technologists, and maintainers who want
to inspect how executable amendment replay can expose provenance, disagreement,
and uncertainty in legal text.

## What v0.1 Demonstrates

- Replay of selected statute histories from amendment sources into
  point-in-time statute trees.
- Auditable operation traces showing how text and structure were produced.
- Replay-vs-oracle comparison against external consolidated surfaces.
- First-class reporting of source pathologies, unresolved operations, and
  candidate divergences.
- Jurisdiction frontend work led by Finland, with additional experimental
  frontends under development.

LawVM has already found hundreds of replay-vs-Finlex divergences. Of these,
22 high-confidence meaningful divergences have been reported to Finlex as
candidate findings for external review, not as confirmed official errors.

## Finland Benchmark Snapshot

The release-era Finland benchmark snapshot was measured on 2026-04-16 against
an archived Finlex comparison surface.

- Headline metric: `0.65%` mean normalized text edit distance.
- Scope: Finnish statutes replayed from raw amendment acts where source and
  comparison surfaces were available in the local benchmark archive.
- Method: replay amendment operations into point-in-time statute trees, compare
  materialized text and structure against archived consolidated witness
  surfaces, and classify mismatches rather than treating either side as
  automatic truth.
- Caveat: full benchmark reproduction requires local archived source artifacts
  that are not shipped in the public repository.

See [docs/benchmark-methodology.md](docs/benchmark-methodology.md).

## API Status

The public concepts are stabilizing: typed operations, legal addresses,
provenance, source pathology records, replay traces, findings, and
point-in-time materialization.

The Python APIs, CLI flags, serialized schemas, and internal module layout are
not yet stable in v0.1. Expect breaking changes as the evidence model,
timeline semantics, and jurisdiction contracts are refined.

## Not Legal Advice

LawVM is a research and engineering tool. Its output is not legal advice, not
an official consolidation, and not a substitute for authoritative legal
sources. Divergences emitted by LawVM are candidates for investigation unless
and until confirmed by the responsible authority or another authoritative
process.

## Intended Use

Use v0.1 to:

- inspect executable amendment replay;
- reproduce candidate replay-vs-source divergences;
- evaluate audit trails and evidence records;
- build jurisdiction-specific experiments;
- contribute tests, source witnesses, and better failure classifications.

Do not use v0.1 as the sole basis for legal compliance, production legal
publishing, or automated legal decision-making.

## Public Docs

- [docs/getting-started.md](docs/getting-started.md)
- [docs/benchmark-methodology.md](docs/benchmark-methodology.md)
- [docs/jurisdictions.md](docs/jurisdictions.md)
- [docs/project-history.md](docs/project-history.md)
- [docs/security-privacy.md](docs/security-privacy.md)

Before tagging or publishing a source snapshot, run:

```bash
./scripts/release_hygiene.sh
./scripts/build_release_archive.sh
```
