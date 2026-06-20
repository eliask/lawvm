# LAWVM_PIPELINE_CONTRACT.md

> Normative. This is the constitution of the LawVM pipeline. It is checkable, not aspirational.
> Cross-frontend: FI/UK/US/NZ/EE all inherit this contract; the core types named below are jurisdiction-neutral.

## 0. Doctrine (one page)

LawVM is a deterministic, proof-carrying compiler for legal text-state. It transforms official source bundles into typed temporal text-state through a sequence of narrow **waists**. Each waist has a canonical input type, a canonical output type, a coverage account, source/provenance witnesses, a residual ledger, and an authority surface.

Stages are **forward-only**: a later stage MAY cite earlier/lower representations as evidence, but MAY NOT re-derive semantic authority from a lossier representation after a typed owner exists.

Every transform is **total in the accounting sense**: input material is accepted, rejected, residualized, or marked benign — never silently dropped.

The six planes (source, surface, legal-state, evidence, projection, overlay — enumerated in §3) are **type-distinct**. Authority is not a plane but a firewall surface carried across them (§7). Evidence and overlays do not authorize replay unless a separate execution-authorization/proof path promotes them.

All public outputs are **projections from checkable dossiers**. Clean claims are forbidden when scoped blocking residue exists. The checker contract — not the generator's confidence — is the public trust boundary.

**The engineering aim is to make silent divergence a type error and every remaining unknown a first-class object.**

## 1. The central invariant

> No stage may silently convert uncertainty, incompleteness, source pathology, projection drift, or external assertion into legal-state authority.

This splits into four sub-invariants. A violation of any is a defect, ranked by whether it is **witnessed** (recorded as a typed Finding/Residual reachable from a public surface) or **silent** (no such record). Silent violations rank highest.

1. **No silent drop.** Every input unit/token/candidate/op/row is accepted, rejected-with-reason, residualized, or marked benign.
2. **No silent guess.** Any heuristic/positional/fallback decision emits a typed Finding or Residual carrying its rationale.
3. **No silent authority promotion.** No stage sets/derives `replay_authorized=True` or mutates legal state except through a named ExecutionAuthorization / phase-local replay gate.
4. **No silent representation regression.** See §4.

## 2. The waist list (canonical input/output types)

Each waist MUST eventually return `StageResult[T] = {value, evidence, residuals, findings, coverage, authority}`. Today most return only `value` plus convention-bridged side-channels; the gap between the columns below and the actual return type IS the audit backlog.

| Waist | Canonical input type | Canonical output type | Coverage cert | Authority |
|---|---|---|---|---|
| source_identity | SourceBundle / SourceWitness + SourceLocator (TODAY: sid `str` + bare `bytes\|None`) | AmendmentSourceModel + SourceWitness(+DigestWitness) | SourceUnitCoverage | surface_only, non-executable |
| token_structure | SourceWitness/SourceUnit (TODAY: bare `str` / `lxml._Element`) | ONE TokenTape (TODAY: 4 rival token types + IRNode) | TokenPartitionCertificate | source_witness, non-replay |
| surface_syntax | (SurfaceGraphSubject, source_units, body) + TokenTape | SourceSyntaxGraph → LegalSurfaceGraph | SyntaxCoverage (4-class union partition) | surface_only=True (firewalled, RAISES) |
| surface_families | SourceSurfaceBundle (TODAY public entry is `bytes`+`str`) | LegalSurfaceGraph (closed NODE/EDGE_KINDS) | TokenPartitionCertificate + certify_graph_coverage | surface_only=True (firewalled) |
| canonical_op | ResolvedSurfaceClause | ClauseAST + LegalOperation (+ TemporalEvent) | PartitionResult (TODAY: untyped residual bag) | surface-derived, NO authority field |
| apply_receipt | ApplyResolvedOpRequest (ResolvedOp) | ReplayState + WriteReceipt + ObservedWriteAudit + MutationAccountingResult | MutationBoundaryProof (TODAY: ~3 of N writes) | ExecutionAuthorization (TODAY: never checked) |
| timeline_materialization | Timelines + MaterializationLineagePlan + base IRStatute | MaterializationResult (status, IRStatute, certificate) | MaterializationCertificate (TODAY discarded on FI raising path) | legal-state plane (no firewall type read) |
| certificate | StageResult bundle (TODAY: sid `str` → engine replay internals) | lawvm.certificate.v0 envelope (8 named roots) | coverage_root (declared-only, doc-granularity) | derived-not-author-set; NOT checked (no checker v0) |
| projection | LegalSurfaceGraph / SourceSyntaxGraph | rows + projection_root + projection_coverage + verifier_status (TODAY: `dict[str,Any]` rows) | UnionOwnershipResult (standalone, not attached) | surface_only — BUT fi_refs stamps replay_authorized=True (LEAK) |
| overlay | ProvenanceAssertion / FetchedSource / surface `str` | AuthorizationResult/ExecutionAuthorization / MorphCandidate / RegistryResult | per-item status (no waist aggregate) | composer-derived only (best-disciplined waist) |

## 3. The six planes — one-line rule each

- **A. Source plane** — *Source identity is evidence footing, not semantic truth.* Owns bytes, locators, normalization, spans, bundle hash.
- **B. Surface/syntax plane** — *Surface facts are not replay authority.* Owns tokens, structure, references, definitions, temporal/modal/condition surfaces, surface residuals.
- **C. Legal-state plane** — *Only execution-authorized operations may mutate legal state.* Owns canonical ops, bindings, temporal events, replay fold, timelines, materialized text-state.
- **D. Evidence/proof plane** — *Evidence explains authority; it does not become authority by existing.* Owns witnesses, findings, proofs, frontiers, residual ledgers, candidate sets, mutation-boundary proofs, pathologies.
- **E. Projection plane** — *Projection is not the source of truth; it must be derivable from a committed dossier.* Owns seam/dump/viewer/parquet/SQLite/packet rows.
- **F. Overlay/enrichment plane** — *Providers may help LawVM see more; they may not become the reason LawVM claims to know.* Owns external assertions, LLM proposals, morphology, human review, manual claims, registry enrichments.

## 4. The no-representation-regression rule (with the witness exception)

```
Raw text MAY be evidence.
Raw text MAY be input to an owning parser.
Raw text MAY NOT be a semantic escape hatch.
```

ALLOWED: a stage carries raw/source spans + surface_text as witnesses (e.g. ReferenceExpr.source_span); a stage feeds raw text into the ONE owning parser for that family; lexical tokenization / closed-class marker detection / sound prefilters / local-format parse / diagnostic rendering.

FORBIDDEN: after a typed upstream object already owns a phenomenon, a later stage re-parses raw/source/rendered/oracle text to (a) invent or drop a LegalOperation, (b) derive a legal-state lifecycle decision, or (c) reconstruct semantic identity — without carrying a blocking-or-witnessed Finding for the parser-miss.

**The witness exception, operationalized:** a raw-text reach-back is tolerable ONLY IF it stamps a `witness_rule_id` on every produced/dropped object AND emits a typed Finding/Residual reachable from a public surface. The canonical positive example is `uncovered_kumotaan_recovery` (witnessed). The canonical violation is `kumotaan_replay._inject_pure_kumotaan_repeal_ops` (identical re-parse, no witness).

## 5. Totality / conservation enums (closed vocabularies)

Every accounting object MUST classify into one of these closed sets — `is_partition()` (buckets sum to total) is the checkable form; an empty/unknown bucket forces a BLOCKED status, never silent.

- **Token/source span:** `owned_by_parser | benign_uninterpreted | typed_residual | unowned_violation` (unowned_violation MUST → 0; `is_clean()` asserts it).
- **Candidate effect:** `executable | blocked_candidate | manual_frontier | source_pathology | oracle_pathology | out_of_scope`.
- **Reference:** `resolved | statute_only | ambiguous | open | broken | unsupported`.
- **Certificate row:** `confirmed | qualified | blocked | not_applicable | invalid_if_unknown_in_clean_certificate`.
- **Apply disposition:** `APPLIED | APPLY_FAILED | NO_APPLY_PASS`; observed-write status `clean | qualified | violation`.

**Governing principle: Completion = accounting + evidence, not silence.**

## 6. Guard-liveness as a constitutional registry rule

A check does not exist unless it can fire from production.

```
registered_blocking_finding_code
  REQUIRES fire_drill_test_id      # real pipeline input → production builder → production sink → public finding
  REQUIRES production_path          # reachable from the live compile/replay lane, not only a unit-internal call
  REQUIRES expected_surface         # verdict / projection / certificate
```

Enforcement: every code in `FINDING_REGISTRY` with `default_enforcement in {hard_fail, ...blocking}` MUST appear in `FIRE_DRILLS` or in the consciously-maintained `NO_FIRE_DRILL_YET` allowlist (debt, not silence). A fire-drill MUST drive the **production guard that decides to emit**, not hand-construct the Finding (verdict-mapping-only drills are SECONDARY). A blocking-registered code whose only producer emits it non-blocking off-pipeline is a registry/producer mismatch and MUST be reconciled (downgrade the registry entry OR wire a production blocking emit), never left.

## 7. Authority firewall (in types, not prose)

- Surface nodes/edges default `surface_only=True, replay_authorized=False`; the assembler RAISES `AuthorityFirewallError` on any violation.
- Replay authorization is conferred ONLY by an `ExecutionAuthorization` (`executable=True` AND explicit opt-in) or a satisfied `PhaseLocalReplayGate`; `forbidden_shortcuts` strings ban evidence/admission/recovery/candidate-set promotion.
- Manual claims NEVER author-set `replay_authorized=True`; the decision is composer-derived from state+profile+gate.
- `confidence` MUST NOT branch replay/legal-state control flow.
- Projection rows MUST NOT carry author-set `replay_authorized=True` / review/validator-status minted at projection time from deterministic extraction (current fi_refs violation).
- LegalOperation / AmendmentOp SHOULD carry an authority surface (today the surface-vs-authorized distinction is positional/by-producer convention).

## 8. Identity discipline

Identity is first-class, content-bound, and versioned. FORBIDDEN: row index, tuple position, HTML ordinal, lxml object identity, positional enumeration labels, name-only source identity. REQUIRED: content digest (`artifact_digest` sha256), canonical span, construction family, normalized surface; derived-object identity = `hash(rule_id, input_node_ids, policy_id, candidate_set_hash)`. Identity migrations are explicit (old_id, new_id, schema, crosswalk, semantic-equivalence gate). Corrected source bytes MUST be re-bound to a new DigestWitness (pre/post pair).

## 9. Certificate as the architectural destination

If a stage cannot produce a root, a coverage row, or a residual ledger, that is a smell. The dossier emits `source_bundle_root, base_tree_root, transition_trace_root, materialization_root, projection_root, residual_root, finding_root, coverage_root`; a checker emits `valid clean | valid qualified | valid blocked | invalid | uncheckable`. Profile disposition and certificate status are DERIVED (computed from the pinned registry), never author-set. The central transition artifact MUST be consumed from the typed WriteReceipt→CertifiedTreeTransition producer, not re-derived by diffing materialized state.

## 10. What this contract forbids building

No "everything must be pure" absolutism; no universal `Thing` object; no mass regex rewrite; no forcing forest-as-producer where a lens is canonical (prefer ONE canonical producer per family, shared parser by construction); no external providers inside the deterministic core; no benchmark-overlap-as-objective; no source spans as semantic identity without versioning; no viewer/packet rows as legal-state facts; no confidence controlling flow.
