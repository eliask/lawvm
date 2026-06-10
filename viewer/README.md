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

- **Oikeustila** — the full statute tree at the selected voimaantulopäivä.
  Clean per-kind labels (`N luku`, `N §`, `N mom.`, `N)`), full provision text,
  collapsible containers (Laajenna / Sulje kaikki), and indentation by depth.
  Provisions that changed versus the previous change-date are flagged.
- **Date scrubber** — slider, prev/next, and a jump-to-date list over
  `meta.change_dates`.
- **Muutokset** — amendment-as-ops changelog. Every distinct amending säädös is
  listed (date + title from `source_artifacts`); selecting one shows each
  operation it performed (`legal_op_kind`, `legal_op_summary`, target address,
  before→after word-diff, provenance).
- **Versiohistoria** — clicking a provision shows its change history with the
  amending säädös title, HE reference (Esitöiden viite), and Finlex link, plus an
  on-demand before/after word-diff.
- **Hash verification** — the tree is rebuilt by folding the certified L3
  transitions in the browser; its reproducible tree hash is asserted equal to the
  engine's `checkpoints.tree_hash`. On a match the **✓ Todennettu
  LawVM-moottoria vastaan** badge is shown.

## Granularity

The viewer is granularity-agnostic: it drives off `target_address` depth and the
decoded node tree, never assuming a fixed level. Transitions in the bundled
`301/2004` (Ulkomaalaislaki) export are recorded at chapter granularity, so the
per-amendment op list and Versiohistoria match at chapter granularity; the
changed-provision highlighting and word-diffs still resolve down to the affected
§ / momentti / kohta by comparing the node subtrees. A section/subsection-grained
export needs no code changes.

## Data contract

SQLite tables used: `meta`, `content_blobs` (BLOB node JSON, UTF-8),
`transitions`, `checkpoints`, `source_artifacts`, `active_at`. See the export
schema `transition-graph.v1` for the authoritative definition.
