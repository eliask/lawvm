# LawVM statute-timeline viewer

Zero-build, vanilla-JS browser viewer for a LawVM **certified transition graph**
export (SQLite, schema `transition-graph.v1`), any jurisdiction. Reconstructs
the point-in-time text of a statute at any change-date, shows what each
amending act did and why each provision reads the way it does, and self-verifies
the reconstruction against the engine's own checkpoint hashes.

## Run

```sh
cd viewer
python3 -m http.server 8000
# open http://localhost:8000/statute-timeline.html
```

`sql.js` and `diff_match_patch` load from CDN; no build step. The statute list
comes from `statute-timeline-manifest.json`; each entry points at a per-statute
`.db` (exported by `lawvm export-transition-graph`).

A different manifest can be selected with a `?manifest=<file.json>` query param
(only a bare same-directory `.json` filename is accepted). A UK sample set ships
as `statute-timeline-manifest-uk.json`:

```sh
# open http://localhost:8000/statute-timeline.html?manifest=statute-timeline-manifest-uk.json
```

The UK sample is five heavily-amended, well-replaying acts (Road Traffic Act
1988, Police Act 1997, Education Act 2002, New Roads and Street Works Act 1991,
Food Safety Act 1990). Regenerate any entry with the jurisdiction flag:

```sh
uv run lawvm export-transition-graph -j uk --statute ukpga/1988/52 \
  --out viewer/data/ukpga-1988-52.db
```

`.db` artifacts under `viewer/data/` are gitignored; only the manifests and
viewer source are tracked. Re-run the exports locally to populate the data dir.

## Layout & interaction model

Two columns, no detail sidebar: a sticky **TOC minimap** on the left and the
**statute as a readable document** (chapters/sections as collapsible outline
rows, momentti/kohta as prose). Provision history opens **inline under the
clicked unit**.

- **Sticky topbar** — modes, § quick-jump, verification badge, and the date
  scrubber travel with you (a 200-§ statute is unusable otherwise).
- **Real time axis** — change dates at their true temporal positions (bursts
  visible as dense tick clusters); click or drag to scrub. Scroll position is
  preserved across date changes so you can scrub time while reading one §.
- **TOC scroll-spy** — the left minimap follows the main-pane scroll position
  (suppressed while you hover the TOC); filter box + Enter jumps.
- **§ quick jump** — "54 a" jumps to the section; if it is repealed/lapsed on
  the selected date it jumps to its ghost tombstone in place.
- **Change badges + lifecycle strips** (section rows, ghost lines) — count
  `3/12` = changes up to the scrubbed date / total over the timeline, plus a
  micro time-strip: half-height duration bars (in force / repealed gap /
  fixed-term-lapsed gap) and full-height event ticks (insert / amend / repeal /
  expiry) at real time positions, future events dimmed, current date as a
  cursor. Clicking opens the version history.
- **Per-block history chips** — a momentti/kohta that has *ever* changed shows
  a persistent `⌚ N` chip; unchanged blocks keep a quiet hover button whose
  tooltip says they are unchanged since the original act.
- **Ghost tombstones** — a §/momentti repealed or expired before the selected
  date renders as a muted line at its original position (date + repealing act),
  with its own history. Removals never silently vanish, at any depth.
- **Versiohistoria (inline)** — every version with validity interval, op-kind
  badges, amending act, preparatory-works link, and a **hierarchically
  localized diff**: decomposed into the changed sub-provisions (addressed),
  never one flat wall of chapter text. Fixed-term expiries are attributed to
  the act that scheduled them ("määräaikainen voimassaolo päättyi" + säädös),
  never shown as unexplained deletions. "Kopioi viittaus" / "Kopioi pysyvä
  linkki" produce a footnote-ready citation and a hash-anchored permalink.
- **Muutokset** — amendment-as-ops changelog; each certified transition is
  decomposed into localized per-provision before/after diffs.
- **Diakroninen haku** — exact-substring search across all historical content;
  reports in-force intervals and which amendment introduced/removed the phrase.
- **Vertaa** — two-date compare: every changed provision listed with diffs
  exposed directly plus compact when/what metadata (the change dates in the
  interval and the acts effective each day).
- **Ctrl-F safe** — collapsed bodies use `hidden="until-found"`.
- **Hash-anchored permalinks** — URL hash encodes (statute, date, address,
  tree-hash prefix); on load the viewer re-folds, re-verifies, deep-links and
  shows "sitaatti todennettu" when the embedded hash matches.

## Diffs

Word-level via `diff_match_patch` (token-encoded, same engine as the
finlex/estonia viewers; dependency-free LCS fallback if the CDN is absent),
rendered as a **unified tracked-changes view**. Below a similarity threshold
(35% unchanged material) the change renders as an explicitly labelled
**wholesale replacement** — clean before/after blocks (side-by-side when there
is room) instead of word-level confetti. Oversized diffs degrade loudly, never
silently.

## Certification vs derived localization

Transitions are certified at the export's covering-frontier granularity
(`meta.certification_granularity`; the bundled 301/2004 export is
chapter-grained — finer tilings lose chapter/section heading scaffolding from
the covering blobs, so chapter certification + derived localization is the
current sweet spot). Everything finer-grained the viewer shows — per-§/momentti
version trails, change badges, lifecycle strips, ghost tombstones, localized
diffs — is **derived in the browser by comparing the certified folds**, and the
history panel says so in plain language. The engine remains the only authority;
the browser never resolves legal targets or interprets amendment language.
`expires_date` rows are unsupported and trigger a loud visible error rather
than a silent (and legally wrong) deletion — reversion must arrive as explicit
transitions.

Deliberately NOT in the viewer: a knowledge-time ("law as known on date K")
axis. Filtering transitions by annettu-date browser-side would produce folds
with no engine checkpoint to verify against; bitemporal viewing needs
engine-authored knowledge-time checkpoints in the export first. The
annettu/voimaantulo gap per change and enacted-but-not-yet-effective versions
("tuleva muutos") are already visible.

## Universality

All UI strings live in a per-language table (`fi`, `en`); jurisdiction-specific
presentation (structure-kind labels, address formatting, op-kind vocabulary,
preparatory-works link building, citation dates) lives in profiles (`fi`, `uk`,
`generic`). The active pair is chosen from DB meta (`lang`, `jurisdiction`)
with manifest fallback. Node addresses are derived from each node's own
`num`/`label` (e.g. `section:104a`), **never** positional counters, so
non-contiguous §§ address correctly. A UK/EE/NZ export needs a profile entry
and a strings table — no structural changes.

## Data contract

SQLite tables used: `meta` (incl. `certification_granularity`,
`localization_status`, `jurisdiction`, `lang`), `content_blobs` (BLOB node
JSON, UTF-8), `transitions`, `checkpoints`, `source_artifacts`, `active_at`,
`display_nodes`, `evidence_events`, and `lawvm_interlinks`. The viewer never
parses legal prose for citations: `lawvm_interlinks` carries LawVM-computed
semantic links, and inline painting is enabled only for rows that already carry
rendered span coordinates. See `transition-graph.v1` in
`src/lawvm/tools/export_transition_graph.py` for the authoritative definition.
