# LawVM v0.1 Roadmap

v0.1 is the alpha / research-preview milestone for LawVM. The goal is not a
complete legal publishing product. The goal is a credible, inspectable replay
substrate that can show where statute text came from, where replay disagrees
with witness surfaces, and where uncertainty remains.

## Release Goals

- Provide a usable CLI for replay, explanation, diffing, and oracle comparison.
- Make Finland replay the primary demonstration path.
- Preserve audit trails for extracted operations, target resolution, replay
  mutations, source pathologies, and findings.
- Keep uncertain or unsupported cases visible instead of silently repairing
  them.
- Publish concise release-facing documentation for alpha users.

## Included Capabilities

- Typed statute-tree operations for common amendment actions.
- Point-in-time materialization from replayed timelines.
- Evidence records for source artifacts, operation lowering, replay, and
  comparison.
- Candidate divergence reporting against consolidated witness surfaces.
- Regression-oriented corpus fixtures for known hard cases.

LawVM has found hundreds of replay-vs-Finlex divergences during Finland
experiments. 22 high-confidence meaningful divergences have been reported to
Finlex as candidate findings for review, not as confirmed errors.

## API Stability

Stable enough to discuss publicly:

- the core model of legislation as executable state transition;
- the distinction between replay output, witness surfaces, and adjudication;
- the need for typed findings instead of silent repair;
- the broad families of operation, provenance, pathology, and materialization.

Unstable in v0.1:

- Python import paths;
- CLI option names and output formats;
- serialized evidence schemas;
- frontend-specific extraction rules;
- exact finding IDs and strict-mode behavior.

## Exit Criteria

- v0.1 release notes and roadmap docs exist at the repository root.
- The CLI can demonstrate replay and explanation on representative statutes.
- Known candidate divergences are framed as reviewable findings, not official
  determinations.
- Public docs include the alpha status, unstable API warning, and legal
  disclaimer.
- Public docs include getting started, Open Law demo, benchmark methodology,
  jurisdiction maturity, project history, and security/privacy notes.
- The public tree does not track internal archives, private correspondence,
  local source archives, or generated databases.
