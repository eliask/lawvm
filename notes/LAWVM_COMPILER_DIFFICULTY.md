# LawVM Compiler Difficulty

This note explains why LawVM, and especially the Finland frontend, is a
particularly hard compiler problem.

The short version is:

- this is not just "a compiler"
- it is a compiler for hostile, underspecified, partially malformed legal
  source artifacts
- the hard part is not only parsing syntax
- the hard part is recovering legal meaning without silently inventing it

## 1. Comparison To Normal Compilers

A conventional compiler usually gets:

- a language intentionally designed to be parsed
- a grammar intended for machines
- a specification that aims at deterministic meaning
- source programs that are intended to compile
- an implementation target where the "ground truth" is mostly stable

LawVM often gets the opposite:

- laws written for humans, institutions, and courts rather than machines
- amendment clauses that omit coordinates or rely on context
- source XML/HTML that may disagree with each other
- consolidated oracles that may be stale, editorial, or outright wrong
- legal effects that depend on prior live state
- commencement and temporary-law interactions that change meaning over time

LawVM is a compiler for a language that was not designed as a language in the
normal PL sense.

## 2. Why Finland Is Especially Hard

Finland is the current stress case because legal meaning is frequently split
across three places:

1. johtolause surface syntax
2. amendment body payload shape
3. live prior statute state

Examples of the resulting difficulty:

- the johtolause targets `1 §`, but the body only changes a few rows
- the payload has omission markers that only make sense relative to the live
  current tree
- a body fragment can only be mapped correctly by inspecting existing numbering
- publication shape loses distinctions that the legal operation still assumes
- a consolidated oracle may disagree with both replay and HTML

This means a pure grammar is not enough, but it also means ad hoc replay-time
string surgery is the wrong shape.

## 3. Where The Difficulty Actually Lives

The real difficulty is split across layers.

### A. Surface syntax difficulty

- verbs, conjunctions, and target lists
- qualifiers like `sellaisena kuin`, `viimeksi muutettuna`
- mixed clauses like `kumotaan X sekä muutetaan Y`
- target families like section, subsection, item, heading, table row

This part should become as grammatical and typed as possible.

### B. Payload-shape difficulty

- tables flattened into subsection-looking structures
- omission markers
- content-only fragments
- malformed wrappers
- sparse amendment bodies

This part is not just syntax. It is source-shape preservation.

### C. Elaboration difficulty

- live-state-dependent omission expansion
- row-table reconciliation
- broad-target to narrow-target rewrites
- subsection/item recovery when source coordinates are incomplete
- temporary-law and commencement interactions

This is the genuinely hard compiler layer.

### D. Replay difficulty

Once canonical operations exist, replay should be comparatively boring:

- deterministic application
- no silent duplication
- strong invariants
- explicit lints and adjudications

If replay starts rediscovering clause structure, the architecture is leaking.

## 4. Why "Just Write A Better Grammar" Is Not Enough

A better grammar helps a lot, but it cannot solve everything.

Grammar can and should own:

- clause structure
- target-family parsing
- conjunction structure
- citation/modifier capture
- typed johto clause ASTs

Grammar cannot fully solve:

- sparse payload alignment
- omission semantics that depend on live structure
- malformed publication artifacts
- table-row reconciliation against the actual live tree
- situations where source coordinates were never made explicit

So the right target is:

- grammar for structure
- typed payload preservation
- typed elaboration for underdetermined meaning
- invariant-checked replay for execution

## 5. What Makes LawVM Harder Than Many Compilers In Practice

The unusually hard parts are:

- the source language is not machine-oriented
- the input artifacts are often inconsistent
- "ground truth" can itself be wrong
- legal meaning is temporal and stateful
- correctness requires distinguishing replay bugs from source/oracle bugs
- auditability matters almost as much as raw execution correctness

LawVM therefore needs not only:

- parsing
- lowering
- execution

but also:

- source-pathology detection
- commensurability classification
- proof/evidence artifacts
- operator-facing adjudication surfaces

That is broader than a typical compiler contract.

## 6. Current Target Architecture

The intended shape is stated in:

- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)

In short:

1. surface syntax frontend
2. payload-shape extraction
3. typed elaboration
4. canonical op compilation
5. replay execution plus invariants

That is the constrained way to handle this difficulty without collapsing into
heuristic soup.

## 7. Practical Conclusion

LawVM is not "impossibly hard", but it is an unusually hard compiler to write.

The reason is not that compilers are easy in general. The reason is that the
source language and source artifacts here are much messier than in normal PL
work, while the correctness bar is unusually high.

So the project should optimize for:

- explicit staged architecture
- typed elaboration instead of hidden heuristics
- strong replay invariants
- clear evidence for replay vs oracle vs source faults

That is the only credible route to a "Correct" LawVM.
