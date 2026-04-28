# Benchmark Methodology

LawVM benchmarks replayed legal text-state against external witness surfaces.
The witness is not treated as automatic truth. A mismatch is classified before
it is interpreted.

## Finland Snapshot

The v0.1 Finland headline snapshot is frozen to the release-era benchmark
surface measured on 2026-04-16.

- Reference frontend: Finland.
- Source model: replay from Finnish amendment acts and original statute source
  artifacts into a statute tree.
- Comparison surface: archived Finlex consolidated XML/HTML witness surfaces
  available to the benchmark run.
- Headline text metric: `0.65%` mean normalized text edit distance against the
  archived Finlex comparison surface.
- Structural metric: release-era tree/structure distance was below `5%`, but
  this metric is more sensitive to XML topology and remains secondary to the
  text-state and evidence classification workflow.
- Candidate findings: 22 high-confidence meaningful replay-vs-witness
  divergences were reported to Finlex for external review.

The 22 reported items are candidate findings. They are not official
determinations and should not be described as confirmed errors unless the
responsible authority confirms them.

## Estonia Consistency Corpus

Estonia is measured differently from Finland because Riigi Teataja consolidated
law is an authoritative source surface. LawVM replay is therefore an independent
consistency check, not the primary legal text surface.

The small Estonia benchmark corpus is a release/evaluation slice. For browsing
public replay-vs-Riigi-Teataja divergences, build the current replayable
corpus:

```bash
uv run lawvm ee-corpus current
uv run lawvm ee-publication-db
```

That corpus contains one latest/current comparison per amended structured
Riigi Teataja group that LawVM can replay. It is not the historical
consecutive-version corpus and it is not restricted to the small benchmark
slice. Riigi Teataja has confirmed and corrected one LawVM-reported omission in
`Audiitortegevuse seadus` § 95^2(1).

## Measurement Shape

For each statute in the benchmark corpus, LawVM:

1. acquires source artifacts from the local archive;
2. parses operative amendment language;
3. extracts and normalizes payloads;
4. elaborates source targets against live legal state;
5. lowers to typed operations;
6. replays operations over the statute tree;
7. materializes point-in-time text-state;
8. compares the result against an archived witness surface;
9. emits findings for disagreement, source pathology, or unresolved replay.

Text edit distance measures whether the final visible legal text is close to
the witness text. Tree edit distance measures whether legal units land in the
same structural locations. Neither metric alone decides legal truth.

## Divergence Classification

A replay-vs-witness mismatch can mean several different things:

- LawVM replay or parsing defect.
- Missing, stale, or malformed source artifact.
- Published source correction or corrigendum not represented in a source lane.
- Witness/editorial consolidation difference.
- Noncommensurable comparison surface.
- Bounded unresolved uncertainty.

v0.1 public language should use "divergence", "candidate finding", or
"reported candidate finding" unless an authority has confirmed the issue.

## Caveats

- Finland's consolidated Finlex text is a witness surface for this workflow,
  not automatic legal truth.
- Finlex XML and HTML can disagree or move at different cadences.
- Some older source XML is missing or malformed.
- Corrigendum PDFs require separate alignment. LawVM's main replay path is
  deterministic; some historical corrigendum alignment work used AI assistance
  and must remain explicitly marked.
- The public repository does not ship the full local source archives used for
  all benchmark runs.
