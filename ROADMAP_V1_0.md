# LawVM v1.0 Long-Term Roadmap

v1.0 is the target for a stable executable-legislation substrate. It should
support reproducible replay, durable evidence records, and jurisdiction
frontends that make bounded, source-backed claims about statute history.

## Product Direction

LawVM should become a platform for:

- point-in-time statute materialization with provenance;
- amendment lineage and migration tracking;
- replay-vs-oracle adjudication;
- source-pathology classification;
- cross-jurisdiction frontend development;
- research-grade and public-sector audit workflows.

## Stability Goals

By v1.0, LawVM should define stable contracts for:

- core typed operation IR;
- legal addresses and statute-tree identity;
- timeline and point-in-time materialization semantics;
- migration and lineage events;
- source-pathology and adjudication records;
- CLI command families and machine-readable output;
- frontend conformance expectations.

Internal implementation details may continue to evolve, but public schemas and
documented CLI behavior should follow compatibility rules.

## Major Workstreams

- Harden the core replay kernel around explicit mutation-boundary invariants.
- Make strict mode a dependable way to reject unproven recoveries.
- Move lineage and migration semantics into durable core contracts.
- Expand corpus-backed conformance tests across jurisdictions.
- Separate replay, oracle comparison, and adjudication so no layer silently
  rewrites another.
- Improve acquisition reproducibility through archive-first source workflows.
- Publish developer documentation for adding frontends without copying
  jurisdiction-specific assumptions.
- Ship small archive-free demos that exercise the full compiler pipeline and
  are stable enough for documentation, talks, and regression tests.
- Maintain public benchmark manifests with exact corpus, archive, command,
  commit, and adjudication metadata.

## Evidence and Divergence Handling

v1.0 should preserve LawVM's central discipline: differences between replay and
external legal surfaces are evidence to classify, not errors to hide.

The Finland work has already produced hundreds of replay-vs-Finlex divergences,
including 22 high-confidence meaningful candidate findings reported to Finlex
for review. v1.0 should make this kind of workflow reproducible, auditable, and
clear about confidence, source witnesses, and authority boundaries.

## Legal Disclaimer

Even at v1.0, LawVM output should not be presented as legal advice or as an
official legal consolidation unless an appropriate authority adopts and
publishes it as such. LawVM can produce evidence, replay traces, candidate
findings, and point-in-time materializations; legal authority remains with the
recognized official sources and institutions.
