# Reach capabilities — demonstrable artifacts + honesty boundaries

Three already-implemented + tested "reach" capabilities, each with a runnable
demonstration on a REAL corpus example and an explicit honesty boundary (what it
genuinely does vs. what it does NOT do). The honesty boundary is the deliverable —
these docs deliberately do not oversell.

All demos run under
`env LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM` (the canonical data root holding
the FI `.farchive` corpus).

| Capability | Entrypoint | Demo | Doc |
|---|---|---|---|
| Counterfactual "what does this bill do" (3-tier) | `lawvm bill-counterfactual STATUTE_ID` | `lawvm bill-counterfactual 2018/301` | BILL_COUNTERFACTUAL_REACH.md |
| EU-directive transposition + timeliness edge | `lawvm.finland.references.eu_transposition_edges` (API) | `scripts/demos/eu_transposition_timeliness_demo.py` | EU_TRANSPOSITION_TIMELINESS_REACH.md |
| Transclusion / typed-derivation edges (corpus Legal Surface Graph) | `lawvm corpus-graph --ids …` | `lawvm corpus-graph --ids 2022/711,2003/314,2010/100` | TRANSCLUSION_DERIVATION_EDGES_REACH.md |

Each doc ends with a one-line "resolves X but NOT Y" verdict. The two
CLI-exposed capabilities (bill-counterfactual, corpus-graph) are demonstrated by
a documented, reproducible CLI invocation; the transposition timeliness edge has
no CLI subcommand (it is a library API), so its demonstrable artifact is the
script under `scripts/demos/`.
