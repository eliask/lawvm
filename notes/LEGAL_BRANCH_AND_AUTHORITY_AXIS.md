# Legal Branch And Authority Axis

Status: initial core contract.
Date: 2026-05-28.

Purpose:

- represent drafts, proposals, consultation texts, and other non-enacted claims
  in the same legal graph as enacted law;
- prevent those claims from mutating ordinary enacted point-in-time state;
- make materialization/export queries explicit about authority layer and branch.

## Core Rule

Draft/proposal material is executable as a claim, not as current law.

Ordinary point-in-time materialization selects only the default enacted context:

```text
authority_layer = enacted
legal_status = commenced
branch_id = ""
scenario_id = ""
```

Proposal/draft operations must carry a non-empty `branch_id`.
They are available to branch/scenario materialization, graph export, and diff
views, but they must not leak into enacted/current materialization without an
explicit enactment or derivation event.

## Current Core Surface

Implemented:

- `src/lawvm/core/authority.py::BranchContext`
- `src/lawvm/core/authority.py::LegalBranch`
- `src/lawvm/core/authority.py::BranchGraphEdge`
- `src/lawvm/core/authority.py::BranchLifecycleEvent`
- `OperationSource.authority_layer`
- `OperationSource.legal_status`
- `OperationSource.branch_id`
- `OperationSource.scenario_id`
- `enacted_materialization_ops(...)`
- `branch_materialization_ops(...)`
- `branch_graph_edge_from_operation(...)`
- `branch_graph_edges_from_operations(...)`
- `CorpusGraph.branches`
- `CorpusGraph.branch_edges`
- `CorpusGraph.branch_lifecycle_events`
- `src/lawvm/core/branch_projection.py::BranchImpactProjection`
- `branch_impact_projection_from_operations(...)`
- `enrich_branch_impact_projection_texts(...)`

The implementation is metadata-first. It does not parse proposal or bill
language yet.

## Export Semantics

Branch graph edges are intended for claims such as:

- `would_amend`
- `would_insert`
- `would_replace`
- `would_repeal`
- `targets`
- `derived_from`
- `terminated_by`

These edges are graph facts, not enacted-state mutations.

`BranchLifecycleEvent` records proposal/draft lifecycle facts such as
introduced, amended, withdrawn, failed, enacted, or superseded. These are
history/status facts for the branch; they do not themselves apply the branch's
operations to enacted law.

`CorpusGraph` rejects duplicate branch ids and branch edges or lifecycle events
whose `branch_id` is not registered in `CorpusGraph.branches`.

The Neo4j CSV export writes:

- `nodes_branches.csv`
- `rels_branch_edges.csv`
- `events_branch_lifecycle.csv`

These files may be empty for jurisdictions that do not yet emit branch facts.

The JSON-LD export also includes branch resources, branch graph edges, and
branch lifecycle events using the `lawvm:` namespace. ELI statute resources
remain separate from these branch/proposal graph facts.

## Demo Command

```bash
uv run lawvm branch-demo --pretty
```

This emits a small synthetic payload showing:

- the default enacted operation lane;
- the selected proposal branch operation lane;
- branch impact rows with current and branch text.

It is a contract demo, not a jurisdiction frontend.

`BranchImpactProjection` is the UI/API-facing summary layer for these edges:
it can say which provisions a branch would affect, optionally with current and
branch-specific text supplied by a frontend. The projection itself does not
execute replay or claim enacted legal effect.

Frontends that already lower proposal/draft material into `LegalOperation`
instances can call `branch_graph_edges_from_operations(...)` to get conservative
would-affect graph facts. Core maps insert/replace/repeal actions to
`would_insert`, `would_replace`, and `would_repeal`; other structural actions
fall back to `would_amend`.

For UI/API payloads, `branch_impact_projection_from_operations(...)` is the
one-step projection from typed branch operations to branch impact rows.
When a frontend has current and branch-specific text, it can attach those
strings with `enrich_branch_impact_projection_texts(...)` using
`target_statute_id#target_address` keys. Core does not fetch or infer that
text.

## Next Steps

Done in the core/demo layer:

- branch lifecycle events exist as graph facts;
- branch graph facts export to Neo4j CSV and JSON-LD;
- `lawvm branch-demo --pretty` shows default enacted vs selected proposal
  operation lanes;
- branch impact rows can be built from operations and enriched with
  frontend-supplied current/branch text.

Remaining:

1. Prototype one real frontend lane:
   pick the jurisdiction/source family with the cleanest proposal or bill
   source extraction, otherwise build a synthetic-to-real bridge first.
2. Add branch-aware materialization beyond the current operation-lane filter if
   a frontend needs actual branch-state execution rather than graph/diff
   projection.
3. Add frontend-owned lifecycle import for introduced, amended, withdrawn,
   failed, enacted, or superseded statuses once a real source surface exists.

## Non-Goals For This Layer

- no legal interpretation;
- no claim that proposals are law;
- no automatic promotion from proposal to enacted;
- no language-specific proposal or bill parsing in core.
