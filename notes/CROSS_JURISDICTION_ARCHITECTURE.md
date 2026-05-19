# Cross-Jurisdiction Architecture

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define what should stay portable in LawVM
- define what should be shared but parameterized
- define what should stay jurisdiction-local

Main design rule:

- Finland is the stress test for the frontend boundary, not the template for
  the global kernel

If a Finland fix requires teaching the portable kernel about Finnish clause
words, sparse omission payloads, Finlex XML quirks, or row-name morphology, the
boundary is wrong.

## 1. Three-Zone Split

LawVM should use three zones, not a simple shared/local binary.

### 1.1 Portable kernel

This should remain jurisdiction-agnostic.

It owns:

- core legal-address and tree model
- canonical op vocabulary
- replay execution semantics
- timeline/version semantics
- materialization and placeholder semantics
- structural invariants
- generic replay/product observations
- generic evidence bundle shape

### 1.2 Shared but parameterized layer

This is shared code driven by per-jurisdiction declaration, not hardcoded
Finland logic.

It owns:

- provision-family registry
- sibling ordering policy
- collision policy
- placeholder rendering policy
- address-normalization hooks
- generic evidence aggregation over jurisdiction-produced observations

### 1.3 Jurisdiction plugin

This remains local.

It owns:

- source acquisition and corrections
- raw ingestion peculiarities
- surface syntax frontend
- payload-shape extraction
- typed elaboration
- source-pathology production
- oracle/topology/source audit producers
- lowering from elaborated meaning to canonical ops

## 2. Portable Contracts

The portable kernel should only accept semantically closed canonical replay
programs.

Each canonical op must already determine:

- action
- exact target address
- target family
- payload or tombstone semantics
- effective/expiry metadata
- provenance/adjudication tags for non-literal recovery

It must not still carry unresolved meaning like:

- probably this subsection
- broad section target unless rows found later
- free-text hints the kernel must interpret

## 2.1 Manual And LLM Compilation Claims

Some jurisdictions expose public source surfaces that are not sufficient for a
fully deterministic frontend to recover every amendment instruction. Others may
start from scanned paper where the first machine-readable text is itself a
derived witness.

LawVM may use humans, LLMs, OCR systems, or external editorial tools in these
cases, but only as governed claim producers. They do not become replay
executors.

The portable shape is:

- source reconstruction claims for scan/PDF/OCR-to-XML work
- semantic compilation claims for hard amendment interpretation
- deterministic validation of those claims
- canonical operations or typed findings as the only replay input

The kernel must still receive closed canonical operations. A manual or LLM
claim that has not validated to closed operations remains evidence, not legal
state.

Replay and benchmark surfaces must distinguish deterministic source-only
replay from replay using validated manual claims or reconstructed source. A
score that used manual claims is useful, but it is a different authority regime
from source-only replay.

## 3. Finland Implication

For Finland, complexity should move leftward:

- typed clause AST
- typed payload IR
- typed elaboration

and not downward into:

- replay kernel
- timeline engine
- product materialization

This is why `payload_normalize.py` being complicated is not itself a smell.
It only becomes a smell when unresolved ambiguity leaks through into replay.

## 4. Evidence Implication

Evidence bundle schema should be shared.
Observation producers should stay local.

That means:

- proof tiers and claim families can be cross-jurisdiction
- section/source/pathology observations are often jurisdiction-specific

## 5. Current Near-Term LawVM Direction

The best near-term use of this split is:

- keep Finland-specific sparse subsection, row-table, and source-pathology
  logic local
- keep canonical op semantics, timeline semantics, and replay invariants
  portable
- introduce more typed interfaces between those layers instead of letting
  local heuristics leak into shared execution
