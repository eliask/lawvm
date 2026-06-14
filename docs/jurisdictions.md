# Jurisdiction Status

LawVM is a shared replay kernel plus jurisdiction frontends. Frontend maturity
depends on source availability, legal authority of the available surfaces,
commencement data, and how much replay evidence has been implemented.

## Current Maturity

| Jurisdiction | v0.1 Status | Current Claim | Main Caveat |
| --- | --- | --- | --- |
| Finland | Reference frontend | National-scale amendment replay proof; 2026-04-16 snapshot measured 0.65% mean text distance against archived Finlex witness surfaces. | Finlex XML/HTML can diverge; some source XML and corrigenda need special handling. |
| Estonia | Experimental consistency frontend | Replay can be used to check authoritative Riigi Teataja consolidated text for candidate inconsistencies. Riigi Teataja confirmed and corrected one LawVM-reported omission in `Audiitortegevuse seadus` § 95^2(1). | Authoritative consolidation changes the truth model: replay is consistency evidence, not the primary legal surface. |
| United Kingdom | Experimental effects/version frontend | UK work is oriented around effect feeds, extent, commencement, and version graph reconstruction. | Commencement, extent, and prospective effects make naive text replay insufficient. |
| New Zealand | Experimental dry-run frontend | API-acquired source corpus with dry-run replay proven against the official point-in-time XML oracle for five amendment operation forms: repeal, single text substitution, whole-provision structural replacement (`replaced`/`substituted`), whole-provision insertion, and nested insertion. Dry-run agreement where an operation is emitted is high (roughly 94–99%); end-to-end coverage (operations that dry-run *and* the after-tree confirms against the oracle) is around 11% of all NZ amendment operations. | Dry-run only: actual replay is deliberately blocked (`nz_replay_canonical_effects_not_implemented`). The coverage gap is extraction/emission precision and unsupported families (the frontier), not kernel correctness. |
| Norway | Experimental | Structured source work exists, with replay and verification experiments. | Broad source availability is weaker before roughly 2001, so historical replay coverage has a hard source ceiling. |
| Sweden | Experimental | Source/current/official-act lanes exist for replay experiments. | Broad source availability is weaker before roughly 1999, limiting complete historical replay. |
| EU | Exploratory | Acquisition and treaty/act modeling experiments exist. | Not part of the v0.1 proof claim. |
| US federal | Exploratory | Bootstrap tooling exists. | Not part of the v0.1 proof claim. |

## How To Read These Claims

Finland is the zero-to-one construction proof. It shows that human-written
amendment streams can be compiled into auditable legal text-state to a very
high extent on a real legal system.

The other frontends are important because they test whether the core model
generalizes, but v0.1 does not claim equal maturity across jurisdictions.
Different jurisdictions expose different truth surfaces:

- amendment acts;
- official gazettes;
- authoritative consolidated law;
- editorial consolidated law;
- effect feeds;
- corrigenda;
- XML, HTML, PDF, and cached archive snapshots.

LawVM's job is to keep those surfaces separate and classify disagreement rather
than force every jurisdiction into Finland's replay-first shape.

New Zealand illustrates this. Unlike Finland, NZ does not (yet) run a
replay-vs-oracle benchmark; its proof surface is *dry-run*. Candidate operations
are applied to an immutable parsed before-version tree and the resulting
after-tree is compared whole against the archived on-or-after point-in-time XML,
without mutating canonical state. Because there is no actual-replay oracle,
`lawvm bench -j nz` is not a replay benchmark and fails loudly, pointing to the
`nz-corpus` surfaces instead (it previously fell through to silently scoring the
Finland corpus). The relevant commands are:

- `lawvm nz-corpus build-corpus` — generate the curated bench corpora
  (`data/nz/bench_corpus.csv`, ~2383 works with at least one amendment
  operation, plus a smoke slice);
- `lawvm nz-corpus dry-run` / `dry-run-oracle` — dry-run candidate operations
  for a work and compare the after-tree to the archived oracle;
- `lawvm nz-corpus dry-run-corpus` — run the dry-run surface across a work
  population as a scoreboard;
- `lawvm nz-corpus dry-run-north-star` — report a coverage fraction whose
  denominator is pinned to ground-truth history-note operation witnesses, so the
  number is comparable across cycles instead of growing as extraction improves.
