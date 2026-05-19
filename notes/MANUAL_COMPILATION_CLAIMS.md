# Manual Compilation Claims

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define how LawVM may accept human- or LLM-assisted compilation without
  turning replay into hidden editorial guessing
- separate source reconstruction from legal semantic compilation
- preserve deterministic replay as the only executor of accepted legal state

Related:

- [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [COMPILER_OBSERVATION_STREAM.md](COMPILER_OBSERVATION_STREAM.md)
- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)

## 1. Core Rule

A manual or LLM-assisted step may propose legal meaning.
It may not directly mutate legal state.

The only executable artifact is still a validated canonical LawVM program:

```text
source witnesses
  -> deterministic extraction
  -> unresolved work item
  -> manual / LLM compilation claim
  -> deterministic validator
  -> canonical operations or typed non-replayable finding
  -> deterministic replay
```

If the validator cannot prove that a claim is supported by the source witnesses
and target state, the claim remains rejected or unresolved. Replay must not
recover the intended meaning from prose after validation fails.

## 2. Two Different Claim Layers

### 2.1 Source reconstruction claim

Used when the source witness itself is not already reliable machine-readable
law, for example scanned paper, OCR output, or LLM-converted PDF/XML.

A source reconstruction claim says:

- this scan/page/region contains this text
- this text has this legal-unit structure
- these coordinates, image hashes, and source artifacts support the claim

It does not say what amendment operations the text performs.

Required evidence:

- source artifact identifier and stable content hash
- page or region locator where available
- reconstructed text and structure
- production method, for example OCR engine, LLM model, human transcription, or
  double-keyed review
- confidence or review state
- reviewer/signer identity when human-reviewed

### 2.2 Semantic compilation claim

Used when the text/source is available but LawVM cannot deterministically lower
it to unambiguous operations.

A semantic compilation claim says:

- this source phrase has this action family
- these exact target addresses or facets are affected
- this exact old text, new text, structural payload, extent, and temporal scope
  are claimed
- this uncertainty is unresolved or non-replayable if no unique operation is
  justified

It must lower to canonical operations, typed source-pathology records, or typed
non-replayable findings. It must not remain a free-form instruction consumed by
replay.

## 3. Claim Shape

A manual compilation claim should be reviewable as data.

Minimum fields:

- stable claim id
- claim kind: `source_reconstruction`, `semantic_compile`,
  `non_replayable_finding`, or `claim_rejection`
- jurisdiction
- affected statute and affected target surface
- affecting source artifact and provision
- source witness locators and hashes
- quoted source witness snippets, bounded and sufficient for review
- proposed canonical operations or proposed finding
- action family and target facet
- temporal and applicability scope
- claimant: human, LLM, tool, or combined review lane
- validator version and validation result
- status: `proposed`, `validated`, `rejected`, `superseded`, or `withdrawn`

The claim may contain prose explanation, but the executable part must be typed.

## 4. Validator Contract

The validator is deterministic.

It should check:

- source witness exists in the archive or reconstructed-source ledger
- source quote is traceable to the claimed artifact
- action family is compatible with the source verb/effect family
- target address or facet exists, or the claim explicitly records why it does
  not
- old text exists where a text replacement or deletion claims it
- structural payload belongs to the claimed target and does not smuggle
  unrelated siblings
- extent, commencement, expiry, and applicability dimensions are represented or
  explicitly unresolved
- changed paths are inside the target region, declared migration paths,
  declared recovery paths, or declared editorial projection paths
- no claim converts one action family to another without an explicit finding

Validation may be incomplete in early implementations, but incompleteness must
be explicit. A claim accepted under weak validation is not equivalent to a
fully source-proved deterministic compile.

## 5. Strictness And Trust

Manual and LLM claims are an authority layer, not a replacement for source
authority.

Strict mode may reject all manual claims unless the caller opts into a specific
trusted claim ledger. Quirks/manual mode may replay validated claims, but must
preserve the claim id and validation status in operation provenance.

Benchmark reports must distinguish:

- deterministic source-only replay
- replay with validated manual/human claims
- replay with LLM-proposed but unreviewed claims
- replay with reconstructed source

These modes should not be collapsed into one score.

## 6. Non-Replayable Outcomes Are First-Class

The correct output of a manual work item may be:

- canonical operations
- a source-pathology finding
- an oracle/editorial adjudication
- a non-replayable legal-state finding
- a request for more source evidence

For example, if a table repeal row names a target but the public source lacks
the old text needed to identify the deletion safely, the claim should say
`non_replayable_from_available_public_sources` rather than inventing a text
patch.

## 7. Scanned-Paper Frontends

For scanned-paper jurisdictions, such as a future Aruba frontend, LawVM should
not treat OCR/LLM XML as source truth.

The source pipeline should be:

```text
official scan / paper PDF
  -> source reconstruction claim
  -> reviewed machine-readable source witness
  -> deterministic frontend parse
  -> semantic compilation claim only for remaining ambiguity
  -> validator
  -> canonical operations
```

The reconstructed source witness must keep provenance back to the scan.
Page coordinates and image hashes are part of the legal evidence trail, not
debug decoration.
