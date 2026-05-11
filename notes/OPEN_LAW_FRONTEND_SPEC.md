# Open Law Frontend Spec

Status: draft spike spec.
Kind: frontend contract.

Purpose:

- define what LawVM may claim when consuming Open Law Library XML;
- keep Open Law publishing/codification semantics distinct from hostile-source
  amendment replay;
- make the prototype useful for conversation with Open Law Library without
  implying full Maryland legal reconstruction.

## 1. Source Regime

Open Law XML is a cooperative structured source regime.

The public Maryland surfaces expose at least these source families:

- `maryland-dsd/law-xml`: editable/current Open Law XML and
  `editorial-actions/*.xml`;
- `maryland-dsd/law-xml-codified`: codified publication snapshots on
  `publication/*` branches;
- `regs.maryland.gov`: public HTML view that points bulk users at git
  repositories rather than scraping.

LawVM should treat local clones of the XML repositories as the primary machine
surface for this frontend. HTML is a publication witness, not replay substrate,
and direct network reads are acquisition/exploration only.

## 2. Claim Boundary

This frontend does **not** initially claim:

- Maryland Register prose extraction;
- independent legal amendment interpretation from natural language;
- full historical reconstruction before the Open Law corpus begins;
- correctness of the agency/editorial decision to issue a codification action.

It may claim:

- an Open Law XML tree was parsed into LawVM IR;
- an Open Law `codify:*` operation was parsed into a typed frontend operation;
- a supported operation replayed against a prior tree;
- the replay changed only the declared target region;
- a publication snapshot equals or differs from replay of declared operations;
- unsupported `codify:*` actions were preserved as typed findings.

## 3. Core Model

Open Law paths such as:

```text
10|41|02|.04
```

are frontend locators. They must not be silently treated as generic
`LegalAddress` paths until the frontend has resolved them uniquely against an
Open Law XML tree.

Resolution rule:

- each path segment must match exactly one direct child label of the current
  parent;
- missing segments emit `open_law_target_missing`;
- duplicate segment matches emit `open_law_target_ambiguous`;
- resolution must not broaden search across the tree.

This preserves the LawVM no-target-hijacking invariant while still respecting
Open Law's pipe-delimited locator vocabulary.

## 4. Supported Operations

Supported families:

- `codify:replace`
- `codify:replace-or-insert`

Typed behavior:

- parse `doc`, `path`, `history`, `applicability`, and document-level
  `effective`;
- parse the structural payload into LawVM IR;
- resolve `path` against the current Open Law IR tree;
- replace exactly that resolved target path.

Current explicitly unsupported families:

- `codify:expire`;
- annotation metadata targets as a body-replay claim;
- any unknown `codify:*` action.

Unsupported actions emit:

- `open_law_unsupported_codify_action`

Quirks mode records and skips unsupported operation families. Strict mode marks
unsupported operation families blocking. Annotation metadata targets are visible
as `open_law_metadata_target_not_body_replay` until the metadata lane exists.

## 5. Snapshot Audit

For a before tree, after tree, and action set, LawVM computes:

```text
replay(before, actions) == after
```

and:

```text
changed_paths(before, after) subset_of declared_operation_target_regions
```

Findings:

- `open_law_publication_snapshot_mismatch`
- `open_law_unexplained_publication_mutation`
- `open_law_snapshot_annotation_projection`
- `open_law_snapshot_typography_projection`

Snapshot comparison may apply named presentation projections:

- annotations are projected out of the body-text replay lane;
- straight/curly quotation mark differences are normalized as typography.

These projections do not mutate replay state. They only define which publication
differences LawVM classifies as presentation-layer differences instead of legal
text-state mismatches.

This is the first meaningful audit product for Open Law:

> Given declared structured codification operations and a publication snapshot,
> did the publication artifact follow from the declared operations?

## 6. Demo Prototype Target

Use Maryland's public repositories as a corpus.

Observed public corpus shape on 2026-05-11:

- `law-xml` main: 4516 XML files;
- 10 `editorial-actions/*.xml`;
- 39 `codify:*` operations;
- operation mix: 35 `replace`, 3 `replace-or-insert`, 1 `expire`;
- `law-xml-codified`: 45 publication branches.

The demo prototype should demonstrate:

1. parse a Maryland Open Law XML subtree;
2. parse one `editorial-actions/*.xml` file;
3. replay a `codify:replace`;
4. show the changed target path;
5. compare replay to an expected publication tree or fixture;
6. show unsupported actions as findings rather than drops.

Concrete smoke target:

```bash
lawvm open-law audit \
  .tmp/open_law_demo/10-41-02-before.xml \
  .tmp/open_law_demo/10-41-02-after.xml \
  .tmp/open_law_demo/2026-01-22.xml \
  --path-prefix '10|41'
```

The `--path-prefix` is explicit carried context for a partial subtree file. It
does not authorize target search broadening; it only supplies known parent
labels omitted by the local chapter file.

## 7. Open Questions For Open Law Library

- Is `codify:*` the stable operation language or an export artifact?
- Are `codify:*` actions always authored directly, or generated from drafting
  prose and reviewed?
- What is the intended semantic difference between `law-xml` and
  `law-xml-codified` beyond build/publication state?
- Are publication branches complete point-in-time snapshots or build artifacts
  with additional selection semantics?
- How should `history="false"` be interpreted legally and operationally?
- What does `applicability` encode, and is its vocabulary closed?
- Are operation failures, rejected edits, or draft codification attempts
  preserved anywhere?
- Is there a public XML schema for the `library`, `codify`, and `codified`
  namespaces?

## 8. Non-Goals

Do not put Maryland-specific code into core.

Do not infer Register amendment semantics from prose until the structured
operation audit layer is stable.

Do not use Git diff alone as legal proof. Git is an acquisition/versioning
surface; LawVM's claim is over typed XML trees and typed codification actions.
