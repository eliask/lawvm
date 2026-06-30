> **Status (2026-06-30):** New. Kind: Explanatory / paper-seed. A related-work survey
> situating LawVM's "total-accounting compiler" paradigm in prior art, to seed the
> eventual paper's related-work section. Companion to `INVARIANT_DISCIPLINE_AND_PRECEDENT.md`
> (the *assurance-kernel* lineage: DbC / HAZOP / DO-178C / PCC / audit / Jepsen) —
> that doc surveys the lineage of the *invariant-mining discipline*; this doc surveys
> the lineage of the *compiler paradigm* it governs. Overlap (PCC/CompCert, the audit
> metaphor) is cross-referenced, not re-derived. Sources are cited with venue/year and
> URL where verified; anything unverified is marked **TODO-citation**, never fabricated.

# Related Work — the total-accounting compiler

## 0. The class, stated precisely

LawVM claims membership in (and tries to define) a class of program we will call a
**total-accounting compiler**. The defining invariant is sharper than "a compiler that
also emits diagnostics":

> A transform `T : Input → Output` is a *total-accounting compiler* when its emitted
> product is the **conserved account** of the transformation, not the bare transformed
> value. Concretely: (1) the output waist carries `value + evidence + residuals +
> findings + coverage + authority`, never the value alone (`AGENTS.md` §0); (2) **every**
> input unit (source span, token, candidate, op, row) is *owned* — accepted, marked
> benign, typed as a residual, or recorded as a violation — and is never silently
> dropped; (3) divergence from that conservation is a **type error**, surfaced at a
> typed waist, not a lost row discovered later; (4) the accounting is **per-unit**, which
> is strictly stronger than aggregate-sum conservation: a balanced total can hide two
> compensating silent drops, a per-unit witness ledger cannot.

Two further commitments make the class specific rather than generic:

- **Forward-only, monotone evidence ledger.** A later stage may *cite* an earlier,
  lossier representation as a witness but may not re-derive semantic authority from it
  once a typed owner exists (`AGENTS.md` §1.12). The evidence ledger is monotone:
  uncertainty and residuals never vanish silently, even though legal state itself is
  mutable (§0).
- **The checker, not the generator's confidence, is the trust boundary.** "Generators
  propose; typed validators authorize; replay consumes only authorized operations"
  (`AGENTS.md` §0). A `confidence`/`certified`/`selected` string may never branch control
  flow; promotion across a plane boundary happens only through an explicit typed
  `ExecutionAuthorization` (§2.10). The product whose trust matters is the **account**,
  re-checkable by a third party, not the pipeline's self-report.

The terminal form of (1)–(4) in LawVM is the registry's slogan **"no public claim
without a live accounting path"** (`notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md`), and the
totality it enforces is explicitly *per-unit > aggregate-sum*.

No single prior system instantiates all of this. Many instantiate **one facet sharply**.
The survey below is organized in tiers by how close the *mechanism* is, and each entry
states honestly **what matches** and **where it diverges from LawVM**. §4 isolates the
combination we believe is genuinely new.

---

## 1. Tier 1 — closest mechanism (ownership, provenance, proof-carrying)

These three lineages each instantiate a *load-bearing* facet of the total-accounting
invariant: per-unit ownership (linear types), per-unit derivation tracking (provenance),
and the checker-is-trust-boundary discipline (PCC).

### 1.1 Linear / affine types & substructural logic → Rust ownership / borrow checking

- **Sources.** Girard, *Linear Logic*, Theoretical Computer Science 50(1), 1987,
  https://doi.org/10.1016/0304-3975(87)90045-4 . Wadler, "Linear types can change the
  world!", IFIP TC2 Working Conference on Programming Concepts and Methods, 1990
  (https://homepages.inf.ed.ac.uk/wadler/papers/linear/linear.ps — author copy).
  Rust: Matsakis & Klock, "The Rust language", HILT 2014,
  https://doi.org/10.1145/2663171.2663188 ; RustBelt: Jung et al., "RustBelt:
  Securing the foundations of the Rust programming language", POPL 2018,
  https://doi.org/10.1145/3158154 .
- **What matches.** A linear/affine type system makes *every value* a resource that must
  be consumed exactly/at-most once; "forgetting" a value (a leak, a silent drop) is a
  **type error**, not a runtime surprise. This is the type-theoretic ancestor of "every
  input unit is owned — never silently dropped." Rust's borrow checker is the most
  widely deployed instance: ownership is a first-class word, move/drop is tracked
  per-value, and the checker (not the programmer's confidence) is the trust boundary that
  authorizes the program. LawVM borrows the vocabulary directly: an *owned* unit, the
  ownership ladder *accepted / benign / residual / violation*.
- **Where it diverges.** (a) Rust conserves *memory/resource lifetimes*; LawVM conserves
  *legal-state units and their evidence* across a pipeline of semantic transforms — the
  "resource" is a source span / op / row, and "owned" can mean *typed-as-residual*, a
  disposition with no analogue in a borrow checker. (b) Rust's accounting is a
  compile-time static property of one program; LawVM's is a *runtime, data-dependent
  account* emitted per compile over adversarial input, and the residual ledger is part of
  the shipped artifact. (c) Rust drops are invisible-by-design (RAII runs the destructor);
  LawVM's whole point is that a drop must be *visible and typed* — the opposite default.

### 1.2 Semiring provenance / data lineage → why/how/where-provenance

- **Sources.** Green, Karvounarakis & Tannen, "Provenance semirings", PODS 2007,
  https://doi.org/10.1145/1265530.1265535 . Cheney, Chiticariu & Tan, "Provenance in
  databases: Why, How, and Where", Foundations and Trends in Databases 1(4), 2009,
  https://doi.org/10.1561/1900000006 .
- **What matches.** Provenance semirings annotate every output tuple with an algebraic
  expression over input-tuple identifiers, so each result *carries* the record of which
  inputs (and which combination) produced it — recoverable, composable, and checkable.
  This is structurally LawVM's `source_anchor` / `witness_rule_id` / `WriteReceipt`
  discipline: every materialized node "traces to the operation and source instruction that
  produced it" (`AGENTS.md` §0), and the receipt records created/replaced/removed/consumed
  paths plus the recovery/migration rule ids. The why/how/where distinction maps cleanly:
  *why* ↔ which source instruction authorized this state; *how* ↔ which rule/op derived it
  (the receipt's recovery/migration rule chain); *where* ↔ `source_span` / `LegalAddress`.
- **Where it diverges.** (a) Database provenance tracks derivation over a *fixed, total*
  relational algebra where every operator's semantics is known; LawVM's "operators" are
  recovered legislative verbs whose semantics is partly *unknown and contested*, so a
  provenance edge can terminate in a **typed residual** ("this could not be derived") —
  provenance semirings have no `⊥`-with-a-reason element. (b) Provenance is typically a
  *passive annotation*; in LawVM the witness is *authority-bearing under a firewall*: a
  surface/overlay witness defaults to `replay_authorized=False` and may not become replay
  authority by existing (§2.10, EV-04). (c) LawVM additionally requires the witness to be
  *monotone* across stages (the ledger only grows), a temporal property orthogonal to
  semiring provenance.

### 1.3 Proof-carrying code & certifying / translation-validating compilers

- **Sources.** Necula, "Proof-carrying code", POPL 1997,
  https://doi.org/10.1145/263699.263712 . Necula & Lee, "Safe kernel extensions without
  run-time checking", OSDI 1996, https://www.usenix.org/legacy/publications/library/proceedings/osdi96/necula.html .
  Pnueli, Siegel & Singerman, "Translation validation", TACAS 1998,
  https://doi.org/10.1007/BFb0054170 . Leroy, "Formal verification of a realistic
  compiler" (CompCert), CACM 52(7), 2009, https://doi.org/10.1145/1538788.1538814 .
- **What matches.** PCC ships a *checkable certificate* with the artifact, and the trust
  boundary is the **small checker**, not the (untrusted, possibly heuristic) generator
  that produced the code. CompCert and translation validation make the same move for
  compilation: rather than trust the optimizer, emit a witness and *validate the
  translation*. This is exactly LawVM's "generators propose; typed validators authorize"
  and its certificate plane: the certificate "converts *trust the LawVM pipeline* into
  *check this bundle*" (`notes/CERTIFICATE_SCHEMA_V0.md`), committing under one root hash
  to source bytes, the certified tree-transition trace, materialization roots, projections,
  and — first-class — the typed residue of what could not be proven.
- **Where it diverges.** (a) PCC/CompCert carry **machine-checked formal proofs** of a
  property against a **formal source semantics**; LawVM carries *evidence and
  root-committed witnesses* checked by a v0 checker, because its source language (irregular
  human-authored law) *has no formal semantics to prove against* — see the honest framing
  in `INVARIANT_DISCIPLINE_AND_PRECEDENT.md` §2.4/§5. (b) A verifying compiler's "trusted
  base" is a fixed list of axioms; LawVM's analogue is the **set of declared
  non-guarantees and typed residuals**, which is *data-dependent and per-compile*. (c)
  Most importantly, PCC assumes a *correct specification* to verify against; LawVM's
  reference oracle is **fallible** (§4) — there is no ground truth to prove preservation
  toward, only a comparison surface that may itself be wrong.

> Cross-reference: the PCC / certifying-compiler lineage and the financial-audit metaphor
> are treated at length in `notes/INVARIANT_DISCIPLINE_AND_PRECEDENT.md` §2.4–§2.5. This
> doc cites them as *mechanism analogues for the compiler paradigm*; that doc cites them as
> *lineage for the invariant-mining discipline*. Same sources, different cut.

---

## 2. Tier 2 — conservation / accounting lineage

These supply the *accounting* intuition (the etymology is literal) and the
*conservation-law* and *forward-only-ledger* intuitions. They are weaker analogues:
each conserves an **aggregate quantity**, where LawVM demands per-unit witnesses.

### 2.1 Double-entry bookkeeping (the etymology, and the honest gap)

- **Sources.** Pacioli, *Summa de arithmetica, geometria, proportioni et proportionalità*,
  Venice, 1494 (the *Particularis de computis et scripturis* treatise — the first printed
  description of double-entry). For a modern historical/analytic treatment:
  **TODO-citation** (a standard accounting-history reference, e.g. Sangster's work on
  Pacioli, not verified here — do not cite from memory).
- **What matches.** Double-entry is the original total-accounting discipline: every
  transaction posts equal-and-opposite entries, so the books *balance* and an unexplained
  imbalance is a detectable error. LawVM's framing is a deliberate descendant — the §0
  language of "owned", "account", and the project's internal *täyslaskenta* (full
  accounting) name come straight from this lineage.
- **Where it diverges.** Double-entry conserves an **aggregate** (debits = credits). This
  is precisely the *aggregate-sum* totality LawVM calls **strictly weaker** than per-unit
  totality: a correct trial balance can hide two compensating misposts that net to zero
  (`LAWVM_AUDIT_INVARIANT_REGISTRY.md`, generative principle). LawVM's per-unit witness
  ledger is double-entry's discipline *refused the sampling/aggregation tolerance* — every
  individual unit must carry its own witness, at any magnitude.

### 2.2 UTXO / blockchain value conservation

- **Sources.** Nakamoto, "Bitcoin: A peer-to-peer electronic cash system", 2008,
  https://bitcoin.org/bitcoin.pdf . (UTXO model — transactions consume prior unspent
  outputs and produce new ones; sum-in = sum-out + fee is enforced per transaction.)
- **What matches.** The UTXO model conserves value *per transaction*, not just in
  aggregate: each output is individually consumed-exactly-once and re-created, which is
  closer to per-unit ownership than double-entry. The "every output is owned and consumed
  exactly once" shape rhymes with LawVM's per-op conservation (EV-01: a conserving filter
  returns accepted **and** rejected lanes; reverting to a bare list-comprehension fails a
  structural ratchet — `AGENTS.md` §1.8).
- **Where it diverges.** UTXO conserves a *single scalar* (value) under a fixed, total
  transition rule that the network re-checks; LawVM conserves *heterogeneous typed units*
  (spans, tokens, ops, rows) under *recovered, partial* rules, where the conserved
  "quantity" includes *uncertainty itself* (a residual is a conserved unit). There is no
  blockchain analogue of "this unit is owned as an unresolved finding."

### 2.3 Physics conservation laws / reversible computing / Landauer

- **Sources.** Landauer, "Irreversibility and heat generation in the computing process",
  IBM Journal of Research and Development 5(3), 1961,
  https://doi.org/10.1147/rd.53.0183 . Bennett, "Logical reversibility of computation",
  IBM Journal of Research and Development 17(6), 1973,
  https://doi.org/10.1147/rd.176.0525 .
- **What matches.** The intuition that *information destruction is physically costly and
  must be accounted for* is the deep analogue of LawVM's **forward-only, monotone**
  ledger: a stage may lose representational detail, but the loss must be *recorded* (the
  earlier representation survives as a cited witness, §1.12), never silently erased.
  Reversible computing's "don't discard bits, route them to a recorded tail" is exactly
  the §1.8 conservation law's "every filtered/rejected/skipped/downgraded op stays visible
  with a receipt."
- **Where it diverges.** This is an *analogy*, not a mechanism: LawVM does not require
  literal reversibility (legal state is *not* reversible — a repeal genuinely destroys a
  provision's force) and does not bound entropy. What it borrows is the discipline that
  *the act of dropping is itself an accountable event*. The asymmetry "over-retention is
  the safe wrong; over-repeal is the forbidden one" (`AGENTS.md` §0) is a deliberate
  *break* with conservation symmetry — destruction is penalized harder than retention.

---

## 3. Tier 3 — totality & soundness in PL / verification; build hermeticity; legal sibling

These instantiate individual *engineering* facets: exhaustive coverage (totality
checking), no-false-negatives (sound analysis), error-tolerant total parsing, and
reproducibility (hermetic builds). The legal sibling (§3.5) is the nearest *domain*
relative.

### 3.1 Total functional programming & totality / coverage checking

- **Sources.** Turner, "Total functional programming", Journal of Universal Computer
  Science 10(7), 2004, https://doi.org/10.3217/jucs-010-07-0751 . Coverage/termination
  checking in dependently-typed languages: Norell, *Towards a practical programming
  language based on dependent type theory* (Agda), PhD thesis, Chalmers, 2007,
  https://www.cse.chalmers.se/~ulfn/papers/thesis.pdf ; Brady, "Idris, a
  general-purpose dependently typed programming language: Design and implementation",
  Journal of Functional Programming 23(5), 2013, https://doi.org/10.1017/S095679681300018X .
- **What matches.** A *total* function is defined on **every** input — no missing case,
  no partial fall-through. Agda/Idris/Coq enforce this with coverage checking
  (every constructor of the scrutinee is handled) and termination checking. This is the
  PL-theoretic statement of LawVM's "completion is accounting, not silence": a transform
  is not *done* when it handles the common cases, it is done when **every** input unit
  lands in exactly one disposition bucket. The registry's stopping rule — every candidate
  in exactly one allowed bucket, the `implicit_convention` bucket empty — *is* a coverage
  check lifted from "every constructor handled" to "every claim accounted."
- **Where it diverges.** Totality checkers operate over a *closed, formally-typed* input
  domain (the algebraic datatype's constructors are finite and known). LawVM's input
  domain is *open and adversarial* (arbitrary legislative prose); it cannot enumerate
  cases a priori, so it substitutes a **typed residual** as the catch-all constructor —
  "input you cannot classify is a typed residual, not a new ad-hoc family" (`AGENTS.md`
  §2.1). LawVM achieves *totality over an open domain* by making "unhandled, here is the
  evidence" a first-class, type-checked outcome.

### 3.2 Exhaustiveness checking (compilers' everyday totality)

- **Sources.** Maranget, "Warnings for pattern matching", Journal of Functional
  Programming 17(3), 2007, https://doi.org/10.1017/S0956796807006223 .
- **What matches.** The non-exhaustive-match warning is the most widely deployed totality
  mechanism in practice: the compiler proves your `match` covers every case or warns.
  LawVM's per-unit accounting is "exhaustiveness checking for the data flowing through a
  pipeline" rather than for the control flow of one function.
- **Where it diverges.** Same open-vs-closed-domain divergence as §3.1, plus: a
  non-exhaustive match is a *warning by default* in most languages; LawVM makes the
  equivalent gap *blocking-by-default at a typed waist* (a residual kind absent from the
  pinned registry is blocking — `notes/INVARIANT_DISCIPLINE_AND_PRECEDENT.md` §4).

### 3.3 Sound static analysis / abstract interpretation

- **Sources.** Cousot & Cousot, "Abstract interpretation: a unified lattice model for
  static analysis of programs by construction or approximation of fixpoints", POPL 1977,
  https://doi.org/10.1145/512950.512973 .
- **What matches.** A *sound* static analysis has **no false negatives**: it may
  over-approximate (false positives) but never misses a real instance of the property.
  "No false negative" is exactly "no silent miss" — the analytic statement of LawVM's
  §0 directive that over-retention is the safe wrong and silent loss (over-repeal) is the
  forbidden one. LawVM deliberately chooses the sound-analysis trade: prefer a *visible
  residual / retained-but-flagged unit* over a confident-but-wrong drop.
- **Where it diverges.** Abstract interpretation is sound *with respect to a formal
  concrete semantics* it approximates; LawVM has no concrete semantics for the source
  language, so its "soundness" is operational (every unit owned) rather than a lattice
  Galois-connection guarantee. And LawVM's residuals are *concrete typed objects carrying
  the offending text* (`AGENTS.md` §1.10), not abstract lattice elements.

### 3.4 Error-tolerant / total parsing → the `PARSE.*` residual family

- **Sources.** Brunsfeld et al., *tree-sitter* — incremental, error-recovering parsing
  with explicit `ERROR` and `MISSING` nodes in the concrete syntax tree,
  https://tree-sitter.github.io/tree-sitter/ (project documentation; a primary peer-
  reviewed venue citation is **TODO-citation** — tree-sitter is best cited to its
  documentation/repo, not a paper, unless a specific write-up is located).
- **What matches.** tree-sitter never *fails* to produce a tree: unparsable regions become
  typed `ERROR` nodes *inside* the tree, so the failure is a first-class, located object
  rather than an exception that loses the input. This is precisely LawVM's `PARSE.*`
  residual family (SURF-08): "every fact-bearing clause that cannot reach a typed owner
  emits a `PARSE.*` / fallback residual carrying the offending clause text (~300–400
  chars)." The shared principle: *parsing is total — the unparsable is a node, not a void.*
- **Where it diverges.** tree-sitter localizes *syntactic* failure within one tree; LawVM
  carries the residual *forward through subsequent semantic stages* under the monotone-
  ledger and no-reach-back rules (§1.12) — the residual is not just located, it is
  *propagated as authority-less evidence* and forbidden from being silently re-parsed into
  a guess downstream.

### 3.5 Hermetic / content-addressed build systems → the determinism firewall

- **Sources.** Dolstra, Jonge & Visser, "Nix: A safe and policy-free system for software
  deployment", LISA 2004, https://www.usenix.org/legacy/event/lisa04/tech/dolstra.html ;
  Dolstra, *The Purely Functional Software Deployment Model*, PhD thesis, Utrecht, 2006,
  https://edolstra.github.io/pubs/phd-thesis.pdf . Bazel: hermetic, content-addressed,
  reproducible builds — https://bazel.build/basics/hermeticity (project documentation;
  a peer-reviewed Bazel citation is **TODO-citation**).
- **What matches.** Hermetic build systems make the build a *pure function of declared,
  content-addressed inputs*: same inputs → bit-identical outputs, no ambient clock, no
  network, no nondeterministic iteration. This is LawVM's **determinism firewall**
  verbatim: LS-30 ("replay is a pure function of (base IRStatute, authorized ops,
  pit_date): same triple → byte-identical materialized tree AND identical certificate
  roots"), LS-31 (cross-process materialization-hash stability), LS-32 (no wall-clock on
  the spine), LS-33 (no random/nondeterministic-iteration source). Content-addressing
  (everything keyed by hash of inputs) is LawVM's source-bundle-hash and root-commitment
  discipline.
- **Where it diverges.** Nix/Bazel guarantee build *reproducibility*; LawVM additionally
  insists the *deterministic core runs and emits complete honest output with typed
  residuals without any external box* — the firewall is not only "same inputs → same
  output" but "the trustworthy spine has **no** dependency on overlays/LLMs/providers,
  which attach strictly outside it" (`AGENTS.md` §2.10). A Nix derivation may depend on any
  pinned input; LawVM's firewall is *also* an authority boundary (overlay → replay
  promotion is forbidden), which has no build-system analogue.

### 3.6 Domain sibling — Catala and Rules-as-Code / OpenFisca

- **Sources.** Merigoux, Chataing & Protzenko, "Catala: A programming language for the
  law", ICFP 2021 (Proc. ACM Program. Lang. 5, ICFP, Article 77),
  https://doi.org/10.1145/3473582 . OpenFisca — open-source rules-as-code engine,
  https://openfisca.org/ . Rules-as-Code background: OECD/OPSI, "Cracking the code:
  Rulemaking for humans and machines", 2020, https://doi.org/10.1787/3afe6ba5-en .
- **What matches.** Catala is the nearest *legal* relative: it is a real programming
  language with formal semantics, designed so that statute and code track each other
  clause-by-clause, with the explicit goal of faithful, auditable legal computation. It
  shares LawVM's seriousness about *legal text as an executable artifact* and its concern
  for traceability between source law and computed result. OpenFisca / Rules-as-Code share
  the ambition of turning legislation into runnable, testable rules.
- **Where it diverges — the key contrast.** Catala and Rules-as-Code are
  **forward-authoring**: a human expert *transcribes* known law into a formal language,
  and the formal artifact is *authoritative by construction* (the legal logic is written
  down). LawVM runs the **inverse, adversarial** problem: the operational grammar of how
  amendments mutate a statute tree is **unwritten** and must be *reconstructed* from a
  corpus, by hypothesising a construction rule, compiling it, diffing against a **fallible**
  official consolidation, and triaging the residuals (`INVARIANT_DISCIPLINE_AND_PRECEDENT.md`
  §0.5, "differential reconstruction"). Catala *expresses* a spec a lawyer hands it; LawVM
  *recovers* a spec nobody wrote, against an oracle that is itself sometimes wrong. Catala's
  trust comes from a verified expression of stated logic; LawVM's comes from a per-unit
  account of a reconstruction whose target is contested.

---

## 4. What looks genuinely novel

The honest position, consistent with `INVARIANT_DISCIPLINE_AND_PRECEDENT.md` §3: **none of
the pieces is new.** Linear ownership, semiring provenance, proof-carrying / checker-as-
trust-boundary, double-entry conservation, totality checking, sound analysis, total parsing,
and hermetic determinism all predate LawVM and are cited above. The claim is about the
**fusion** and one **disciplinary inversion**.

1. **The fusion: per-unit total-accounting + checker-is-trust-boundary, applied to spec
   *reconstruction*.** LawVM combines (a) Rust/provenance-style *per-unit ownership of
   every transformed unit, with a typed residual as the open-domain catch-all* with (b)
   PCC-style *the checker, not the generator's confidence, is the trust boundary, and the
   certificate is the product*. Each half exists separately. Putting them together so that
   the conserved account is itself the artifact a third party re-checks — over a pipeline
   whose **transformations are the data** (the source language is "legislative delta
   language": the inputs *are* edit operations on a prior state) — is the structural move
   we have not found combined elsewhere.

2. **The inversion: a fallible oracle, and the discipline of never repairing to it.** The
   sharpest novelty is not a mechanism but a *stance*. Every Tier-1/Tier-3 analogue assumes
   a **correct specification** to verify against (PCC's property, CompCert's source
   semantics, Catala's transcribed law, a totality checker's datatype). LawVM's reference —
   the official consolidation — is a **first-class fallible witness**, not ground truth:
   `oracle_suspect` is a typed, first-class disposition ("LawVM is right, the official text
   is stale/editorial/wrong — a finding, not a failure", `AGENTS.md` §0). The operating
   rule is **never repair-to-oracle**: a replay-vs-oracle similarity score is a regression
   guard, and *maximizing it rewards deleting oracle-present state to match a possibly-wrong
   oracle*, so each residual divergence resolves to *deterministic gap* (LawVM wrong) /
   *manual-compilation frontier* (source underspecifies) / *oracle-suspect* (oracle wrong),
   and the per-EID exclusivity of that partition is itself audited (EV-12). In the limit,
   **the account may convict the witness**: LawVM can hold itself correct and the
   authoritative published text wrong, on the strength of its own per-unit accounting. We
   know of no comparable verification system whose ground-truth comparison surface is
   explicitly *fallible and adjudicable* rather than trusted.

Stated as a falsifiable claim: *if* a prior system already (i) conserves every transformed
unit per-unit with typed residuals, (ii) ships the conserved account as a third-party-
checkable certificate where the checker is the trust boundary, **and** (iii) treats its
correctness oracle as a fallible witness it may overrule under a typed `oracle_suspect`
disposition — over a domain whose source language is unwritten edit-operations — then the
novelty claim is refuted and that system should be cited here. We have not found one;
candidates would most plausibly come from legal informatics (which, per §3.6, generally
*forward-authors* against trusted law) or from differential-testing / metamorphic-testing
work (which lacks the per-unit account and the typed-residual ledger).

---

## 5. Summary table — {prior art → LawVM facet it instantiates → where it diverges}

| Prior art | LawVM facet instantiated | Key divergence from LawVM |
|---|---|---|
| Linear/affine types; Rust ownership (Girard 1987; Wadler 1990; Matsakis & Klock 2014) | Per-unit ownership; leak = type error; the *ownership* vocabulary | Conserves memory/lifetimes statically; drops are invisible-by-design — LawVM conserves heterogeneous legal units at runtime and makes drops *visible & typed*; "owned" includes *typed-residual* |
| Semiring provenance / lineage (Green–Karvounarakis–Tannen 2007; Cheney et al. 2009) | `source_anchor` / `witness_rule_id` / `WriteReceipt`; why/how/where | Passive annotation over a total, formal algebra; no `⊥`-with-reason element; LawVM's witness is authority-gated (firewall) and monotone, and may terminate in a typed residual |
| Proof-carrying code; certifying / translation-validating compilers (Necula 1997; Pnueli et al. 1998; Leroy/CompCert 2009) | Checker-is-trust-boundary; the certificate plane; "generators propose, validators authorize" | Machine-checked formal proofs against a formal source semantics and a *correct* spec; LawVM carries evidence/roots checked by a v0 checker, over law with no formal semantics and a *fallible* oracle |
| Double-entry bookkeeping (Pacioli 1494) | The "account" / *täyslaskenta* framing; conservation as the deliverable | Aggregate-balance only (debits=credits) — strictly weaker than LawVM's per-unit witness totality; compensating drops can net to zero |
| UTXO value conservation (Nakamoto 2008) | Per-transaction "consumed exactly once" ≈ EV-01 per-op conservation | Conserves one scalar under a fixed total rule; LawVM conserves typed heterogeneous units (incl. *uncertainty*) under recovered partial rules |
| Conservation laws / reversible computing / Landauer (Landauer 1961; Bennett 1973) | Forward-only, monotone ledger; "dropping is an accountable event" | Analogy only; legal state is *not* reversible; LawVM breaks symmetry (over-repeal penalized harder than over-retention) |
| Total functional programming; coverage checking (Turner 2004; Norell 2007; Brady 2013) | "Completion = accounting, not silence"; every unit in exactly one bucket | Closed formal input domain; LawVM totalizes an *open adversarial* domain via the typed-residual catch-all |
| Exhaustiveness checking (Maranget 2007) | Per-unit accounting ≈ exhaustiveness for pipeline *data* | Warning-by-default for control flow; LawVM is blocking-by-default at a typed waist |
| Sound static analysis / abstract interpretation (Cousot & Cousot 1977) | "No false negative" = "no silent miss"; over-retention is the safe wrong | Sound w.r.t. a formal concrete semantics; LawVM's soundness is operational (every unit owned), residuals are concrete text-carrying objects |
| Error-tolerant parsing (tree-sitter) | `PARSE.*` residual family (SURF-08): unparsable = typed located node | Localizes syntactic failure in one tree; LawVM propagates the residual forward under no-reach-back (§1.12) |
| Hermetic / content-addressed builds (Nix — Dolstra 2004/2006; Bazel) | Determinism firewall (LS-30/31/32/33); root-commitment | Reproducibility only; LawVM's firewall is *also* an authority boundary (overlay→replay forbidden), no build analogue |
| Catala; Rules-as-Code / OpenFisca (Merigoux et al. 2021; OECD 2020) | Nearest *legal* relative: law as executable, source↔result traceability | **Forward-authoring** of trusted, written law vs LawVM's **inverse/adversarial reconstruction** of unwritten edit-grammar against a fallible oracle |

---

## 6. Citations to verify (TODO-citation)

Marked above; collected here so the paper pass can resolve or drop them. **Do not cite
any of these from memory.**

1. A modern historical/analytic reference for Pacioli / double-entry (§2.1) — likely
   Alan Sangster's accounting-history work, *not verified*; verify exact title/venue/year
   before citing.
2. A primary peer-reviewed citation for **tree-sitter** (§3.4) — none located; tree-sitter
   is currently best cited to its documentation/repository. Confirm whether a write-up
   (e.g. a thesis or workshop paper) exists.
3. A peer-reviewed citation for **Bazel** hermeticity (§3.5) — cited to project docs; a
   primary academic reference, if one exists, was not located.

All other URLs/DOIs are given as the canonical published locations and should be
double-checked at paper-prep time (DOI resolution + author copies), but were not invented:
they are real, well-known works in their fields.
