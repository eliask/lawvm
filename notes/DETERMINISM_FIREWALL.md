# Determinism Firewall

Status: normative. Fable 5 direction #5 — the cheap protective move that must land
before LLM adjudication spreads further.

## The invariant

LawVM's epistemic stance rests on **byte-deterministic, replayable execution**:
ratchet baselines, byte-identical self-consistency, falsifiable hypotheses. A
replay or projection that could vary run-to-run — because it made a live model
call — would dissolve every one of those guarantees.

LLM output (draft-HE adjudication, vision transcription, claim proposal) may ONLY
ever create **typed candidate proposals BELOW an assurance ceiling**. It is a
*producer of candidates*, never an authority in the replay path.

> **The firewall.** No module in the replay/projection import cone may import any
> LLM-consuming client module. The deterministic replay + projection spine must
> not reach a live model — eager OR lazy.

The fenced clients are the `lawvm.finland.llm_backends.*` modules that speak to a
live model (llama.cpp / OpenAI-compat servers):

- `llm_adjudicator` — producer-neutral extraction adjudicator;
- `vision_producer` — vision transcription of page images into candidate blocks;
- `qwen_local` — claim-proposal text backend;
- any future sibling under `finland.llm_backends.*` (e.g. a nemotron/docling
  client) — fenced by prefix **by default**, so a new client cannot leak in
  before anyone updates the exact set.

## The record-caching rule

Adjudication / transcription results enter replay ONLY as **content-addressed,
versioned records carrying the adjudicator/model id in provenance** — never via a
live call from a replay-cone module. The live LLM call happens OUTSIDE the cone
(in the acquisition / proposal tooling); its output is frozen into a
content-addressed record; replay consumes the record, not the model. This keeps
the model id auditable and the replay byte-identical: the same record replays the
same way forever, regardless of model availability or drift.

This mirrors the replay-coverage snapshot and the anchor/touch metric discipline:
an expensive, non-deterministic producer runs once, its output is content-pinned
with provenance, and every downstream consumer reads the pin.

## The replay/projection cone (precise definition)

The cone is **NOT** the `[project.scripts]` entrypoints. The monolithic `lawvm`
CLI dispatcher reaches *everything*, including the `propose-claims` tool that
legitimately calls an LLM — so the CLI's whole cone is not "the replay/projection
path". Instead the cone is rooted at the deterministic replay/projection cores
(`scripts/inventory_module_roles.py:REPLAY_PROJECTION_CONE_ROOTS`):

- Finland: `replay_entrypoint`, `replay_pipeline`, `graph`;
- neutral cores: `core.branch_projection`, `core.ctsf_gate`,
  `core.replay_conservation`, `semantic.projection`;
- per-jurisdiction replay engines: `uk_legislation.uk_amendment_replay`,
  `eu.pipeline`, `new_zealand.actual_replay`, `norway.replay`,
  `us_federal.bench`, `replay_adjudication`.

The cone is the BFS closure of those roots over the full import graph (static
imports **plus** lazy function-body imports **plus** `importlib.import_module`
string targets — all folded in by `build_import_graph`, so a lazy client import
still counts).

## The enforcement mechanism

- **Production scanner:** `scripts/inventory_module_roles.py:firewall_report()`
  reuses the existing import-graph builder, BFS-closes the cone roots, and returns
  the offending edges (`importer -> llm_client`). CLI: `uv run python
  scripts/inventory_module_roles.py --firewall` (exit 1 on breach).
- **Gate:** `tests/test_determinism_firewall.py` FAILS on any un-allowlisted
  offending edge. It carries a `FIREWALL_ALLOWLIST` dict (empty today) — each
  entry is a consciously-permitted, **tracked** edge with a loud rationale,
  exactly like `DEAD_ALLOWLIST`; an allowlisted edge is debt to pay down (route
  the result through a content-addressed record), never a silent pass. A stale
  allowlist entry (debt paid) fails the gate so the allowlist only names live
  debt.
- **Guard liveness:** the test drives the production predicate (`_is_llm_client`)
  and edge computer (`compute_firewall_edges`) into their firing state on
  synthetic inputs — a firewall that finds nothing today still provably *can*
  fire — and asserts the cone actually reaches the spine (>100 modules) so it
  cannot pass vacuously.

**Current state (2026-07-09): the firewall HOLDS.** The only `src/` importer of an
LLM client is `lawvm.tools.cmd_propose_claims` (a lazy `qwen_local` import), which
is the manual-claims proposal tool and is **not** in the replay/projection cone.
Zero offending edges; the 13 cone roots close to ~694 modules, none of which
reaches a client.

## `--affected` blind spot

This is a **whole-graph ratchet**: it BFS-walks the entire import graph. Like the
classifier-wrap / regex / module-role / naming-hygiene ratchets, `ci.sh
--affected` selects shards by touched path and **will miss a firewall breach**
introduced by an edit outside the firewall's own files (e.g. adding an
`import ... llm_backends` inside an existing replay-cone module changes no path
the affected-mapping recognizes as firewall-relevant).

Run it **explicitly after any merge** that adds/moves a module in the replay cone
or under `finland.llm_backends`:

    LAWVM_CANONICAL_DATA_ROOT=... uv run pytest tests/test_determinism_firewall.py -q

Registered in `notes/DISCIPLINE_GATES.md` §F (the explicit post-merge
whole-graph-ratchet list) alongside module-role / classifier-wrap / regex.
