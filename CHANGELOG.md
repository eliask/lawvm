# Changelog

## v0.1.0 - Unreleased

Release target date: not fixed.

### Added

- Public release docs for getting started, benchmark methodology,
  Open Law demo, jurisdiction maturity, project history, and security/privacy
  notes.
- Root roadmap and changelog for the v0.1 release line.
- MIT license and package license metadata.
- Release hygiene script for dirty-worktree checks, public-doc link checks,
  credential-pattern scans, developer-local path scans, and large tracked file
  scans.
- Tracked-file-only release archive helper that runs release hygiene before
  creating a `git archive` from `HEAD`.
- Public framing for Finland findings as reported candidate divergences, not
  confirmed official errors.
- Estonia replayable corpus and publication SQLite export tooling for browsing
  replay-vs-Riigi-Teataja divergences beyond the small benchmark slice.
- Riigi Teataja-confirmed `Audiitortegevuse seadus` § 95^2(1) correction
  recorded as an Estonia residual-inventory correction notice.

### Changed

- Cleaned the public documentation surface by removing tracked internal notes,
  historical investigation archives, and noisy pre-v0.1 work queues from the
  repository.
- Updated README and docs indexes to point at the current public release
  surface.
- Softened legacy viewer wording from "errors" / "confirmed" framing toward
  candidate-finding framing.

### Verification

- Before this release-doc cleanup, the full test suite passed with
  `7350 passed, 100058 skipped, 46 warnings` using:
  `uv run pytest tests/ -q --override-ini='addopts='`.
- During the 2026-05-11 release-hygiene pass,
  `./scripts/release_hygiene.sh --allow-dirty` passed and the bounded local
  CI gate `./scripts/ci.sh` passed with `7762 passed, 15 skipped`. That CI gate
  excludes network, slow, pipeline-gold, and citation-routing lanes.
- This changelog does not claim that every future checkout is green; rerun the
  local tests before tagging.

### Known Limitations

- v0.1 APIs, CLI JSON output, and finding identifiers are not stable.
- Full replay workflows require local archived source artifacts that are not
  shipped in the public repository.
- Finland is the reference frontend. Other jurisdiction frontends are
  experimental unless their docs say otherwise.
