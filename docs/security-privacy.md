# Security and Privacy Notes

LawVM is a research-preview compiler and audit system for legal text-state. It
does not require secrets for normal local replay, tests, or public demo
commands.

## Public Tree Boundary

The public source tree is intended to contain code, tests, small fixtures,
public documentation, and small corpus indexes. It should not contain:

- API keys, tokens, passwords, private keys, or service credentials;
- private correspondence or inbox exports;
- generated databases, cache directories, or local `.farchive` corpora;
- downloaded legal source archives unless they are explicitly public fixtures;
- `.tmp/` investigation packets or agent work queues.

Before a public tag, build the release artifact from tracked files only:

```bash
./scripts/build_release_archive.sh
./scripts/verify_release_archive.sh .tmp/release/lawvm-<commit>.tar.gz
```

The helper runs the release hygiene gate first and then creates a `git archive`
from `HEAD`. Dirty working-tree files, `.tmp/`, local corpora, and untracked
cache files are not included. It also writes checksum sidecars:

- `OUT_TAR_GZ.sha256`
- `OUT_TAR_GZ.manifest.json`

The verifier checks the checksum sidecar, manifest digest and commit fields,
archive prefix, and member path safety.

This tracked source archive is distinct from Python wheel/sdist artifacts. Use
`uv build` when validating installable package artifacts; the release hygiene
gate runs that build and checks release-relevant package metadata, CLI entry
points, and packaged generated assets. The same release hygiene gate also
rejects tracked local/generated artifact paths such as `.tmp/`, `.farchive`,
SQLite/DB/DuckDB files, and Parquet exports.

## Local Data

Full Finland replay workflows require local Finlex archives imported into
`data/*.farchive`. Those archives are not shipped in the repository. Open Law
demo workflows clone public Git repositories into `.tmp/open_law/repos/` and
record local clone identity in the generated evidence-pack manifest.

## Recommended Pre-Release Scan

```bash
./scripts/release_hygiene.sh
```

For local iteration before the worktree is clean:

```bash
./scripts/release_hygiene.sh --allow-dirty
```

The final scan should be run on a clean release commit.

## Legal-Use Boundary

LawVM output is not legal advice and is not an official consolidation. Public
claims should describe replay-vs-witness differences as divergences, candidate
findings, or confirmed corrections only when the responsible authority has
confirmed them.
