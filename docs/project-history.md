# Project History

LawVM reached v0.1 through an internal sprint of roughly 3,700 commits before
the public release cleanup. That history included broad experiments, failed
approaches, source forensics, benchmark iterations, website prototypes, and
large investigation ledgers.

The public v0.1 repository intentionally does not publish the noisy internal
work queues and historical evidence archives. The goal is to publish a clean
compiler substrate and the current architecture contracts, not every scratch
path that produced them.

## What The Sprint Established

- Ordinary human-written amendment acts can be treated as executable legal
  state transitions.
- A real national corpus can be replayed to very high text fidelity without
  rewriting law into a separate rules language.
- Replay output needs provenance, operation traces, timeline identity, and
  divergence classification, not only final consolidated text.
- External consolidated surfaces are evidence surfaces. They can be useful,
  stale, editorial, authoritative, or wrong depending on jurisdiction and
  context.
- The hard remaining work is mostly ownership: make every recovery, repair,
  failed operation, and source pathology visible instead of clever and silent.

## Finland Proof Path

Finland became the reference frontend because it has a rich amendment stream,
public source surfaces, and a useful consolidated witness in Finlex. The v0.1
snapshot measured 0.65% mean text edit distance against an archived Finlex
comparison surface and produced hundreds of replay-vs-witness divergences for
classification. From those, 22 high-confidence meaningful candidate findings
were reported to Finlex for external review.

Those reports are evidence that the method is useful. They are not a claim that
LawVM is an official consolidation or that every mismatch is an external error.

## Public Handoff

The intended v0.1 handoff is practical: LawVM shows that executable amendment
replay can be done properly, at scale, with evidence. Other researchers,
institutions, legal publishers, and civic infrastructure teams should be able
to continue from a working substrate rather than starting from the assumption
that legal text must first be rewritten as code.
