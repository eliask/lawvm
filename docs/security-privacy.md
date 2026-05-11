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

Before a public tag, build the release artifact from tracked files only and run
a scan on that exact artifact.

## Local Data

Full Finland replay workflows require local Finlex archives imported into
`data/*.farchive`. Those archives are not shipped in the repository. Open Law
demo workflows clone public Git repositories into `.tmp/open_law/repos/` and
record local clone identity in the generated evidence-pack manifest.

## Recommended Pre-Release Scan

```bash
git status --short
git ls-files
rg -n "(api[_-]?key|secret|token|password|BEGIN PRIVATE KEY|Inbox|External)" .
find . -type f -size +25M -not -path "./.git/*"
```

The final scan should be run after cleaning or excluding `.tmp/`, `data/`,
cache directories, and generated databases.

## Legal-Use Boundary

LawVM output is not legal advice and is not an official consolidation. Public
claims should describe replay-vs-witness differences as divergences, candidate
findings, or confirmed corrections only when the responsible authority has
confirmed them.
