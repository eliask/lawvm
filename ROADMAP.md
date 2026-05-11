# LawVM Roadmap

LawVM v0.1 is the public zero-to-one proof: ordinary amendment acts can be
compiled into auditable legal text-state at national-corpus scale. The release
is intentionally narrow. It proves the replay substrate, evidence model, and
divergence workflow; it does not claim stable public APIs or legal authority.

## v0.1 Release Gates

- Keep the public repository free of internal investigation archives, private
  correspondence, large local source archives, and noisy pre-release work logs.
- Provide one documented install path: `uv sync`, `uv run lawvm --help`, and
  representative replay/explain commands.
- State the scope boundary clearly: text-state replay, provenance,
  point-in-time materialization, and replay-vs-witness classification.
- Publish the Finland benchmark snapshot with date, method, headline metric,
  caveats, and candidate-finding language.
- Keep architecture docs curated around the constitution, compiler phases,
  core/frontend boundary, evidence model, and strict-vs-quirks distinction.
- Do not claim tests are green unless the checked command actually passes.

## v0.1 After Release

- Cut and publish `v0.1.0` from a clean commit.
- Extend the archive-free Open Law demo toward a fuller parse/lower/replay/
  evidence narrative without requiring Finland local corpora.
- Freeze a public Finland benchmark artifact with the exact input manifest,
  comparison surface, commit, command, and output summary.
- Convert the highest-value remaining Finland corpus cases into owned
  interaction families with findings, strict-mode behavior, and regression
  tests.
- Make the website mirror the README positioning: LawVM exists to show that
  executable amendment replay can and should be done properly.

## v1.0 Direction

- Stabilize the core operation IR, legal address model, timeline semantics,
  migration/lineage semantics, evidence records, and CLI JSON output families.
- Make strict mode a dependable contract for rejecting unproven recoveries.
- Move remaining lineage and materialization hacks into explicit core-owned
  semantics.
- Define frontend conformance for Finland, Estonia, UK, Norway, Sweden, and
  future jurisdictions without copying Finland-specific assumptions.
- Publish reproducible benchmark manifests and adjudication ledgers.
- Make replay-vs-oracle disagreement classification auditable enough for
  institutional adoption.

See [ROADMAP_V0_1.md](ROADMAP_V0_1.md) and
[ROADMAP_V1_0.md](ROADMAP_V1_0.md) for more detail.
