# Changelog

## v0.1.0 - Unreleased

Release target date: 2026-04-25.

### Added

- Public release docs for getting started, benchmark methodology,
  jurisdiction maturity, project history, and security/privacy audit.
- Root roadmap and changelog for the v0.1 release line.
- MIT license and package license metadata.
- Public framing for Finland findings as reported candidate divergences, not
  confirmed official errors.

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
- This changelog does not claim that every future checkout is green; rerun the
  local tests before tagging.

### Known Limitations

- v0.1 APIs, CLI JSON output, and finding identifiers are not stable.
- Full replay workflows require local archived source artifacts that are not
  shipped in the public repository.
- Finland is the reference frontend. Other jurisdiction frontends are
  experimental unless their docs say otherwise.
