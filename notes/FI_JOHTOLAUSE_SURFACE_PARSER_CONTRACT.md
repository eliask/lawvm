> **Status (2026-06-22):** Current-with-noted-drift. Kind: Normative (the frozen observable contract of `surface_parse.parse(...)`). One stale consumer ref: line 95 "peg3.py (facade)" no longer exists — the canonical parser/facade is `johtolause/surface_parse.py` (consumed via `api.py`); update the consumer list. Everything else verified against `surface_model.py`/`scan.py`.

# FI Johtolause Surface Parser — Compatibility Contract (v0)

Status: normative. The exact observable contract of `surface_parse.parse(...)`, frozen so a rewrite
can be validated against it by tests + the characterization golden, not by prose. Any new parser
implementation MUST reproduce this contract; deviations are classified deltas (see
`parse-characterize`), never silent.

## Entry point

```python
def parse(
    tokens: list[Token],
    jolloin_renumber_pairs: dict[int, list[tuple[str, str, str]]] | None = None,
) -> SurfaceClauseModel
```

- `tokens`: the FILTERED token stream — `tokenize(text)` then
  `apply_annotations_with_jolloin_pairs(raw_tokens)` (scan.py) which hides non-structural spans
  (citation / statute-name / provenance / end-sentinel) behind sentinel tokens. The parser consumes
  this filtered stream, NOT raw tokens.
- `jolloin_renumber_pairs`: out-of-band map `{token_pos: [(src, dst, pair_kind), ...]}` extracted by
  scan for `jolloin nykyinen N momentti siirtyy M momentiksi` consequence clauses. `pair_kind` ∈
  {`M` (momentti, needs section context), section/chapter/part kinds}. First-class discourse INPUT,
  not an emitter patch — must be part of discourse-state initialization in the rewrite.

## Output: `SurfaceClauseModel` (surface_model.py:616)

Fields, in this order:
- `verb_groups: tuple[SurfaceVerbGroup, ...]`
- `meta_clauses: tuple[SurfaceMetaClause, ...]` = `()` from `parse()` itself (meta clauses are
  injected later in the api enrichment phase, not by `parse`).
- `text_amend_clauses: tuple[SurfaceTextAmend, ...]` = `()` from `parse()`.
- `target_version_bindings: tuple[SurfaceTargetVersionBinding, ...]` = `()` from `parse()`.
- `source_text: str` = `" ".join(t.text for t in tokens if t.text)` — reconstructed from the
  FILTERED tokens (so sentinel-hidden spans are absent), single-space-joined. EXACT.
- `consumed_count: int` = `s.pos` at end of the verb-group loop = index one past the last token the
  parser advanced over. NOT len(tokens) in general; trailing END/sentinel tokens may remain. This is
  the field most likely to silently diverge — pin it per fixture.

### `verb_groups` ordering
- One `SurfaceVerbGroup{verb: VerbKind, nodes: tuple[SurfaceNode,...]}` per verb-group, in source
  order, EXCEPT:
- **Jolloin renumber group is PREPENDED.** If `jolloin_renumber_pairs` produced renumber nodes, a
  synthetic `SurfaceVerbGroup(verb=SIIRTAA, nodes=(TargetRef, RenumberTail, ...))` is inserted at
  index 0, before all source-order groups. Node pairs: a `SurfaceTargetRef` (section w/ momentti
  sub-ref for `M`-kind, else bare target) followed by a `SurfaceRenumberTail(new_label=dst)`, both
  witness `fi.jolloin_renumber`.
- `nodes` within a group are in source order as produced by the recognizers.

## VerbKind / TargetKind codes (surface_model.py)
- VerbKind: M(MUUTTAA) K(KUMOTA) L(LISATA) S(SIIRTAA) + META. `from_code`.
- TargetKind: P(section) L(chapter) O(part) N(nimike) A(appendix).

## The 34 witness rule_ids (golden-pinned; phenomenon census frequency)

CORE (96.5% of ops): fi.section_ref (130499), fi.insertion_section (13767),
fi.insertion_sub_target (12000), fi.jolloin_renumber (2692).
MID (100s): fi.chapter_ref, fi.appendix_ref, fi.section_renumber, fi.insertion_chapter,
fi.nimike_ref, fi.valiotsikko_heading_ref, fi.insertion_heading.
TAIL (≤61, 20 rules <50 total — weirdness-ledger fossils, single-owner): fi.heading_edelle_*,
fi.anaphoric_*, fi.cross_verb_*, fi.part_ref, fi.part_renumber, fi.chapter_renumber,
fi.section_ref_pykala_prefix, fi.insertion_section_postfix_chapter, fi.backref_singular/plural,
fi.coordinated_part_chapter_heading_ref, fi.lukuun_ottamatta_exception, fi.direct_section_relabel,
fi.chapter_ref_reversed, fi.insertion_other.

Witness attaches via `SurfaceWitness(rule_id, source_span=(start,end) token indices)`.

## Clause-level census (golden, 32,233 johtolauses)
- tier1_only 90.46% / tier1+2_only 6.40% / tail_present 0.71% / 0_op 2.43%.
- BUT 150 statutes have a tail rule co-occurring with ≥10 ops (e.g. 2019/371: one
  `fi.part_renumber` among 523 ops). A rare rule can poison a large clause → the discourse
  transducer must handle the tail even though it is <0.2% of ops.

## Negative grammar boundary (784 0-op johtolauses)
Amendment-prefixed clauses that legitimately produce ZERO ops (archaic `Kumoten…`, `näin kuuluva`,
statute-name-by-date targets with no extractable section). The new parser MUST reproduce zero ops /
empty verb groups for these; it must NOT over-recognize archaic text into a plausible op. False
compilation is worse than non-compilation. Pin a sampled fixture per reason class.

## Hard invariants the rewrite adds (not in the old parser)
- **Token accounting:** every token has a named disposition (consumed-by-node / trivia /
  provenance-span / explicitly-ignored-by-named-rule / diagnostic). No silent drop.
- **No-silent-ambiguity:** for an ambiguous shape, a NAMED precedence rule decides; unresolved →
  diagnostic/residual, never silent first-parse.
- **Surface-level only:** discourse resolves SURFACE anaphora/scope (`mainitun pykälän` → previous
  mentioned section in clause discourse), NOT live-tree resolution (no occupancy / unique-live
  fallback — that is resolver/lowering/apply, downstream).

## Downstream parity (validate beyond the surface model)
The contract is observed not only at `SurfaceClauseModel` but through
`resolve_surface_clause → lower_to_clause_ast → _derive_parsed_ops_from_ast`. The diff harness
compares canonical surface model AND derived parsed ops AND witness rule_ids.

## Consumers of `parse()` (must keep working)
api.py (main), coverage_audit.py, clause_surface.py, peg3.py (facade), scan.py (imports
`_skip_prov_span` — a leakage to resolve: share the provenance-span boundary logic).
