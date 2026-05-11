# LawVM

LawVM compiles ordinary human-written amendment law into executable legal
text-state.

Amendment acts are programs written in legal language. They replace, repeal,
insert, renumber, move, delay commencement, restrict scope, and otherwise
mutate a statute tree. LawVM compiles those instructions into typed operations,
replays them over legal text structure, materializes point-in-time law, and
emits evidence explaining how the result was derived.

The core claim is deliberately strong but bounded: the text-state layer of
amendment-driven law is already far more executable than the usual "law must
first be rewritten as code" assumption allows. LawVM does not compute legal
interpretation. It compiles what provisions say at a point in time, which source
acts changed them, and where replay cannot prove a result.

The v0.1 goal is a zero-to-one construction proof over a real national legal
system. Finland is the reference proving ground. The intended handoff is that
researchers, public institutions, legal publishers, and civic infrastructure
teams can start from a working compiler substrate.

**Current release target:** LawVM `v0.1` alpha / research preview.

LawVM is a research and engineering system for executable amendment replay,
provenance, source-pathology classification, and replay-vs-witness
adjudication. It is not an official legal consolidation or legal advice.

## Why This Exists

Most legal text systems publish a current text surface. LawVM exists to make
auditable legal state history available alongside current text.

The lower-level question is executable: which source acts changed which legal
units, in what order, under what temporal conditions, and with what evidence?

The useful output includes:

- point-in-time statute materialization;
- typed amendment operations;
- provision timelines and lineage;
- source-pathology records;
- replay-vs-oracle classifications;
- explicit unresolved findings instead of silent repair.

## v0.1 Status

`v0.1` is an alpha research preview. The implementation already has substantial
Finland replay coverage, but the public Python API, CLI output schemas, and
frontend internals are not stable.

Finland is the reference frontend and the deepest end-to-end implementation.
Estonia, the UK, Norway, Sweden, EU, and US federal lanes exist at different
maturity levels. They are experimental unless their local docs say otherwise.

LawVM has already found hundreds of replay-vs-Finlex divergences. From those,
22 high-confidence meaningful Finland divergences have been curated and
reported to Finlex as candidate findings for external review. LawVM also
reported one Estonian consolidation omission to Riigi Teataja; Riigi Teataja
confirmed and corrected that omission. Unconfirmed divergences should be
described as divergences or candidate findings unless confirmed by the
responsible authority.

The purpose of v0.1 is to establish that ordinary human-written amendment
streams can be compiled into auditable legal text-state, bringing Finland close
to complete text-state replay, and to make the remaining work concrete enough
for others to continue.

Release docs:

- [RELEASE_V0_1.md](RELEASE_V0_1.md)
- [ROADMAP.md](ROADMAP.md)
- [CHANGELOG.md](CHANGELOG.md)
- [docs/](docs/)

## Repository Map

- [src/lawvm/core/](src/lawvm/core/): shared kernel: IR, legal addresses, tree operations,
  timelines, evidence contracts, replay contracts, and cross-jurisdiction
  abstractions.
- [src/lawvm/finland/](src/lawvm/finland/): reference replay-first frontend over Finnish amendment
  acts and Finlex witness surfaces.
- [src/lawvm/estonia/](src/lawvm/estonia/): authoritative-consolidation consistency frontend.
- [src/lawvm/uk_legislation/](src/lawvm/uk_legislation/): UK effects/version-graph frontend.
- [src/lawvm/norway/](src/lawvm/norway/): Norway structured-amendment and commencement-sidecar
  frontend.
- [src/lawvm/sweden/](src/lawvm/sweden/): Sweden source/current/official-act lane.
- [src/lawvm/tools/](src/lawvm/tools/): developer CLI surface.
- [notes/](notes/): current public specs and architecture records.
- [docs/](docs/): public getting-started, Open Law demo, benchmark,
  jurisdiction, security/privacy, and history docs.
- [jurisdiction_starter/](jurisdiction_starter/): contract-first starter for new frontends.

The public v0.1 tree intentionally keeps only the current release-facing docs
and current architecture notes. Historical investigation packets and noisy
pre-release work queues are not part of the public source tree.

## Start Here

For release status and roadmap:

- [RELEASE_V0_1.md](RELEASE_V0_1.md)
- [ROADMAP.md](ROADMAP.md)
- [ROADMAP_V0_1.md](ROADMAP_V0_1.md)
- [ROADMAP_V1_0.md](ROADMAP_V1_0.md)
- [CHANGELOG.md](CHANGELOG.md)
- [docs/getting-started.md](docs/getting-started.md)
- [docs/open-law-demo.md](docs/open-law-demo.md)
- [docs/benchmark-methodology.md](docs/benchmark-methodology.md)
- [docs/jurisdictions.md](docs/jurisdictions.md)
- [docs/security-privacy.md](docs/security-privacy.md)

For architecture:

- [notes/SPEC_INDEX.md](notes/SPEC_INDEX.md)
- [notes/LAWVM_CONSTITUTION.md](notes/LAWVM_CONSTITUTION.md)
- [notes/THEORY_OF_LAWVM.md](notes/THEORY_OF_LAWVM.md)
- [notes/CROSS_JURISDICTION_ARCHITECTURE.md](notes/CROSS_JURISDICTION_ARCHITECTURE.md)

For Finland:

- [notes/FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](notes/FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [notes/FINLAND_CLAUSE_AST_SPEC.md](notes/FINLAND_CLAUSE_AST_SPEC.md)
- [notes/FINLAND_PAYLOAD_IR_SPEC.md](notes/FINLAND_PAYLOAD_IR_SPEC.md)
- [notes/FINLAND_ELABORATION_RULES.md](notes/FINLAND_ELABORATION_RULES.md)

For Open Law demo work:

- [docs/open-law-demo.md](docs/open-law-demo.md)
- [notes/OPEN_LAW_FRONTEND_SPEC.md](notes/OPEN_LAW_FRONTEND_SPEC.md)
- [notes/OPEN_LAW_CORPUS_REPLAY_PLAN.md](notes/OPEN_LAW_CORPUS_REPLAY_PLAN.md)

For release hygiene:

- [docs/security-privacy.md](docs/security-privacy.md)
- `./scripts/release_hygiene.sh`
- `./scripts/build_release_archive.sh` for a tracked-file source archive plus
  checksum sidecars

## Quick Start

LawVM uses `uv`.

```bash
uv sync
uv run lawvm --help
```

Selected commands:

```bash
# Finland / default frontend
# First import the public Finlex source archives.
uv run lawvm import-zip \
  --statute-zip https://www.finlex.fi/api/assets/open-data/archives/statute.zip \
  --consolidated-zip https://www.finlex.fi/api/assets/open-data/archives/statute-consolidated.zip

# Then replay, explain, diff, and oracle-check statutes from the local archive.
uv run lawvm replay 2002/738 --as-of 2024-01-01
uv run lawvm explain 2002/738
uv run lawvm diff 2002/738
uv run lawvm oracle-check 2002/738

# Estonia
uv run lawvm -j ee replay <STATUTE_ID> --as-of 2024-01-01
uv run lawvm verify-consistency --jurisdiction ee --base <BASE_ID> --consolidated <ID>
uv run lawvm ee-corpus current
uv run lawvm bench -j ee --label ee_current
uv run lawvm ee-publication-db

# UK
uv run lawvm uk-replay <STATUTE_ID> --pit-date 2024-01-01
uv run lawvm uk-fetch-affecting <STATUTE_ID>

# Norway and Sweden
uv run lawvm no-index
uv run lawvm sweden --help
```

`ee-corpus current` writes the broad current/latest Estonia corpus
(`data/estonia/current_replayable_corpus.csv`, currently 2203 comparison
cases). That corpus is the default for Estonia bench and publication DB
tooling; the smaller `data/estonia/bench_corpus.csv` is only a legacy
curated slice.

Full Finland replay workflows require local archived sources under
`data/*.farchive`. Use `uv run lawvm import-zip` with the public Finlex
`statute.zip` and `statute-consolidated.zip` archives before running ordinary
Finland replay, diff, oracle-check, or benchmark commands. Replay and
verification should be archive-first whenever possible. The `.farchive` files
are local, history-preserving source archives backed by
[farchive](https://github.com/eliask/farchive), a small archive format/library
for exact bytes observed at named locators. It keeps large legal corpora
efficiently queryable through SQLite and compact on disk; the Finlex import is
roughly under 5 GB after ingesting about 13 GB of ZIP input.

The repository includes small public corpus indexes and fixtures, not the full
source archives needed for every replay workflow.

## Core Model

LawVM is a phased compiler:

1. acquire and archive source artifacts;
2. parse the operative source surface;
3. derive clause or effect surfaces;
4. extract and normalize payload shape;
5. elaborate targets against live legal state;
6. lower to canonical typed operations/effects;
7. replay over a statute tree;
8. compile timelines and materialize point-in-time law;
9. adjudicate replay against witness/oracle surfaces;
10. emit evidence, findings, and unresolved states.

The central discipline is simple: do not silently delete, reroute, widen,
invent, or repair legal state. If LawVM cannot prove a mutation is valid, the
uncertainty should remain visible.

## API Stability

Stable enough for v0.1 discussion:

- legislation as executable deltas;
- replay as source-backed compilation;
- witness/oracle surfaces as evidence, not automatic truth;
- typed findings for recovery, failure, and source pathology;
- point-in-time materialization as a temporal query.

Not stable in v0.1:

- Python import paths;
- CLI JSON schemas;
- exact finding IDs;
- frontend internal modules;
- serialized evidence bundle formats;
- strict-mode policy details.

## Development Notes

For debugging one Finnish statute:

```bash
uv run lawvm bisect <ID>
uv run lawvm ops <ID> --source <AMENDMENT_ID>
uv run lawvm dump <ID>
uv run lawvm diff <ID>
uv run lawvm explain <ID>
uv run lawvm oracle-check <ID>
```

For structural violation diagnosis:

```bash
uv run lawvm invariant-bisect <ID> --detector all_tree
uv run lawvm diagnose-phase <ID> --source <AMENDMENT_ID> --certificate
uv run lawvm snapshot-debug <ID> --source <AMENDMENT_ID> --target section:20
uv run lawvm product-debug <ID> --source <AMENDMENT_ID> --target section:20
```

If you change core semantics, update the relevant [notes/](notes/) spec. If you change
a frontend, identify the phase boundary and the evidence consequence. If you
add a jurisdiction, start from [jurisdiction_starter/](jurisdiction_starter/).

## License and Legal Disclaimer

LawVM output is not legal advice, not an official legal consolidation, and not a
substitute for authoritative legal sources.

When LawVM replay agrees with an independent consolidated source, that agreement
is strong evidence that the text-state is correct, though still not an absolute
legal guarantee. When LawVM diverges from a witness surface, the divergence is a
candidate for investigation unless confirmed by the responsible authority or
another authoritative process.
