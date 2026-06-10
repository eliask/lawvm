# LawVM statute-timeline viewer

Zero-build, vanilla-JS browser viewer for a LawVM **certified transition graph**
export (SQLite, schema `transition-graph.v1`). Reconstructs the point-in-time
structure of a statute at any change-date, shows what each amending säädös did,
and self-verifies the reconstruction against the engine's own checkpoint hashes.

## Run

```sh
cd viewer
python3 -m http.server 8000
# open http://localhost:8000/statute-timeline.html
```

`sql.js` is loaded from CDN; no build step. The statute list is read from
`statute-timeline-manifest.json`; each entry points at a per-statute `.db`.

## What it does

- **Oikeustila** — a 3-column reading view of the statute *voimassaolon mukaan*
  (in force on the selected effective date): a sticky section-level **TOC**
  (jump to any §, filterable), a readable **document** column (momentti text as
  prose, collapsible with Laajenna / Sulje kaikki), and a pinned **detail
  panel** that shows version history without scrolling away from the selection.
  Provisions that changed versus the previous change-date are flagged; repealed/
  removed units render as muted **`[kumottu]`** tombstones, never silently
  vanishing. The validity interval ("voimassa <alku>–<loppu/—>") is shown.
- **Ctrl-F safe** — collapsed bodies use `hidden="until-found"`, so the native
  browser find reveals (and auto-expands) matches inside collapsed §§.
- **Date scrubber** — slider, prev/next, and a jump-to-date list over
  `meta.change_dates`.
- **Muutokset** — amendment-as-ops changelog. Every distinct amending säädös is
  listed (annettu + voimaantulo dates); selecting one shows each operation it
  performed with translated op labels (lisätty / muutettu / kumottu — empty
  op-kinds get an explicit "laji kirjaamatta" fallback, never a blank), target
  address, before→after word-diff, and provenance with a linkified HE reference.
- **Diakroninen haku** — exact-substring search across **all** `content_blobs`
  over the whole history (not just the selected date). For each matching
  provision it reports the in-force interval(s) and which amendment
  **introduced** vs **removed** the phrase, with säädös + HE attribution.
  Dependency-free; no FTS5, no fuzzy matching; case-folded.
- **Versiohistoria** — clicking a provision shows its change history grouped by
  effective date (same-day intermediate states are never shown as citable),
  with säädös title, HE reference (linkified), Finlex link, and an on-demand
  before/after word-diff. "Kopioi viittaus" / "Kopioi pysyvä linkki" produce a
  footnote-ready citation and a hash-anchored permalink.
- **Hash-anchored permalinks** — the URL hash encodes (statute, date, address,
  tree-hash prefix). On load the viewer re-folds, re-verifies, deep-links to the
  cited provision, and shows **"sitaatti todennettu"** when the embedded hash
  matches the freshly computed state — a citable legal state any reader's
  browser re-proves.
- **Hash verification (precise modesty)** — the tree is rebuilt by folding the
  certified L3 transitions in the browser; its reproducible tree hash is asserted
  equal to the engine's `checkpoints.tree_hash`. On a match the
  **✓ Näkymä vastaa LawVM-moottoria** badge is shown, with an info popover
  stating exactly what is proven (näkymä = moottorin laskema tila) and what is
  **not** claimed (moottori = Finlexin konsolidointi, or = voimassa oleva
  oikeus). `expires_date` rows are unsupported and trigger a loud visible error
  rather than a silent (and legally wrong) deletion — reversion must be encoded
  as explicit transitions.

## Granularity

The viewer is granularity-agnostic: it drives off `target_address` depth and the
decoded node tree, never assuming a fixed level. Provision addresses are derived
from each node's own `num`/`label` (e.g. `section:104a`), **never** from
positional counters, so non-contiguous §§ (104 a §, repealed gaps) address
correctly and deep-address joins stay valid. Transitions in the bundled
`301/2004` (Ulkomaalaislaki) export are recorded at chapter granularity, so the
per-amendment op list and Versiohistoria match at chapter granularity (the detail
pane says so explicitly); the changed-provision highlighting and word-diffs still
resolve down to the affected § / momentti / kohta by comparing the node subtrees.
A section/subsection-grained export needs no code changes.

## Data contract

SQLite tables used: `meta`, `content_blobs` (BLOB node JSON, UTF-8),
`transitions`, `checkpoints`, `source_artifacts`, `active_at`. See the export
schema `transition-graph.v1` for the authoritative definition.
