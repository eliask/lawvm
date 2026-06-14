# US Statutes at Large acquisition (deep-history public laws)

Extends the U.S. federal amendment-source corpus in `data/us_federal.farchive`
(gitignored) BACKWARD in time. The govinfo PLAW bulkdata collection only reaches
the 113th Congress (2013); older enacted public laws are published by GPO as the
**Statutes at Large** USLM collection. This task acquired the bridge range —
**100th–112th Congress (1987–2012)** — into the same canonical locator scheme as
`import_plaw`, enabling ~1990-onward deep-history amendment windows.

Pure data acquisition + provenance: no lowering / dry-run / bench code touched.

## Source-reachability finding

**Keyless USLM: YES, at the volume granularity (not per-law).**

- The govinfo **bulkdata** PLAW channel (`/bulkdata/PLAW/{congress}/public/`)
  does NOT serve pre-113th laws, and there is no STATUTE bulkdata zip
  (`/bulkdata/json/STATUTE` returns the Bulkdata Service error page).
- The `api.govinfo.gov` STATUTE collection endpoint requires an API key (HTTP
  401 keyless) — not used.
- The keyless `/content/pkg/` + `/metadata/pkg/` channels (the same channels
  that worked for USCODE) DO serve STATUTE. Per Statutes-at-Large **volume**
  there is exactly one USLM rendition:

  ```
  https://www.govinfo.gov/content/pkg/STATUTE-{volume}/uslm/STATUTE-{volume}.xml
  ```

  `application/xml`, USLM 2.0.10, ~20–30 MB per volume. Confirmed HTTP 200,
  keyless, for every volume 101–128. The package `mods.xml`
  (`/metadata/pkg/STATUTE-{volume}/mods.xml`) declares this single
  `USLM rendition`; all per-granule (per-page) renditions are **PDF only**.

- There is **no per-law and no per-granule USLM** — `/content/pkg/STATUTE-{v}/
  uslm/STATUTE-{v}-Pg{N}.xml` 302-redirects (absent). The per-law unit must be
  sliced out of the whole-volume document.

### Locator / granule structure

A Statutes-at-Large *volume* is NOT a Congress: each two-year Congress spans two
consecutive volumes. Verified mapping (from each volume's `<meta>`):

| Volume | Congress | Year | | Volume | Congress | Year |
| --- | --- | --- | --- | --- | --- | --- |
| 101 | 100 | 1987 | | 114 | 106 | 2000 |
| 102 | 100 | 1988 | | 115 | 107 | 2001 |
| 103 | 101 | 1989 | | 116 | 107 | 2002 |
| 104 | 101 | 1990 | | 117 | 108 | 2003 |
| 105 | 102 | 1991 | | 118 | 108 | 2004 |
| 106 | 102 | 1992 | | 119 | 109 | 2005 |
| 107 | 103 | 1993 | | 120 | 109 | 2006 |
| 108 | 103 | 1994 | | 121 | 110 | 2007 |
| 109 | 104 | 1995 | | 122 | 110 | 2008 |
| 110 | 104 | 1996 | | 123 | 111 | 2009 |
| 111 | 105 | 1997 | | 124 | 111 | 2010 |
| 112 | 105 | 1998 | | 125 | 112 | 2011 |
| 113 | 106 | 1999 | | 126 | 112 | 2012 |
|     |     |      | | 127 | 113 | 2013 (= PLAW corpus start) |

Inside a volume, each enacted instrument is a self-contained `<pLaw>` element
whose first descendant `<meta>` carries `<dc:type>` (Public Law / Private Law /
proclamation / concurrent resolution), `<docNumber>` (the law number within its
Congress), `<congress>`, `<publicPrivate>`, `<citableAs>` ("Public Law 107–1"
and "115 Stat. 3"), and `<approvedDate>`. `<pLaw>` elements do not nest.

## What was acquired

Importer: `src/lawvm/us_federal/import_statute.py` (new). It fetches each
volume's USLM keyless, slices every `<pLaw>`, keeps the **public** laws, wraps
each slice as a standalone well-formed USLM `<statuteSlice>` document, and stores
it at the SAME canonical scheme as `import_plaw` so the two channels share one
corpus:

```
us://plaw/{congress}/publ{N}.xml
```

Provenance is distinguished by metadata `acquisition_channel =
"statutes_at_large_uslm"` (vs `plaw_bulkdata`). SHA-256 dedup makes re-runs
idempotent (`--skip-existing`); a second pass over volumes 115–116 imported 0.

```
python -m lawvm.us_federal.import_statute 101-126
```

**Volumes 101–126 (100th–112th Congress, 1987–2012), 26 volumes:**

| Congress | Public laws | Congress | Public laws |
| --- | --- | --- | --- |
| 100 | 711 | 107 | 377 |
| 101 | 649 | 108 | 498 |
| 102 | 588 | 109 | 482 |
| 103 | 464 | 110 | 460 |
| 104 | 333 | 111 | 383 |
| 105 | 394 | 112 | 283 |
| 106 | 580 | | |

- **Total public laws added: 6,202** at the statute channel (100th–112th).
- 6,354 `<pLaw>` units scanned; ~152 non-public units (private laws,
  proclamations, concurrent resolutions) are **typed skips**, never stored.
- Raw sliced bytes ≈ 515 MB (farchive-compressed on disk; raw bytes stay out of
  git per repo hygiene).
- Combined U.S. amendment-source corpus is now **8,344 `us://plaw/...`
  locators**: 6,202 statute-channel (100–112) + 2,142 plaw-channel (113–119),
  a contiguous Congress range **100 .. 119**.

## Source pathology handled (Prime Directive: typed, not faked)

- **Defective `<congress>` meta.** In volume 123, the `<pLaw>` for **Public Law
  111-78** carries a stray `<congress>110</congress>` while its `<citableAs>`
  correctly reads "Public Law 111–78". The importer treats `<citableAs>` as the
  authoritative identity, files the law at `us://plaw/111/publ78.xml`, records
  the discarded meta value in metadata `source_congress_meta = "110"`, and emits
  the typed finding `us_statute_import_congress_meta_mismatch`. Exactly **one**
  such mismatch exists across the whole 100–112 range. Without this
  reconciliation the law would have been misfiled at `110/publ78`, colliding
  with the genuine PL 110-78.
- **Non-public units** (private laws, proclamations, House/Senate concurrent
  resolutions) lack `publicPrivate=public` / a Public-Law citation and are typed
  skips (`us_statute_import_private_law_filtered`,
  `us_statute_import_unidentified_plaw`).
- Unreachable / unparsable volumes raise typed records
  (`us_statute_import_volume_unreachable`, `us_statute_import_volume_unparsable`)
  rather than producing silent gaps.

## Residual / out of scope

- **PDF-only granules.** Per-page/per-granule content is PDF only; we ingest the
  per-volume USLM exclusively (the only XML form). No PDFs were fetched.
- **Pre-100th Congress (volumes 1–100, 1789–1986).** GPO publishes USLM for
  these too (same `/content/pkg/STATUTE-{v}/uslm/...` pattern, verified keyless
  for sampled volumes), but acquisition was bounded to the 1987-onward bridge.
  Extending further back is a one-line range change (`1-100`) when needed.
- **Deep-history bench wiring is intentionally NOT done here.** The kernel's
  older-edition handling is separate. Follow-up: deep-history bench windows also
  need older USC oracle editions — govinfo USCODE annual editions exist back to
  ~1994 (`import_usc`), which would pair with these older public laws.
- **Cross-volume law continuation.** A small number of laws legitimately span
  two consecutive volumes; the importer dedups logical locators within a volume
  and lets the later whole-law slice win across volumes (content-identical on
  re-import). No data loss observed; the statute-range locator total (6,202)
  reflects distinct laws.

## Tests

`tests/test_jurisdiction_starter_us_federal_import_statute.py` — offline parse +
import over a committed real-derived fixture
(`tests/fixtures/us_federal/STATUTE-115-uslm-sample.xml`, a 3-law slice of
STATUTE-115) plus a synthetic defective-`<congress>` volume. Covers slicing,
public/private classification, standalone well-formedness of stored slices,
idempotent `--skip-existing`, the citation-vs-meta reconciliation, and the
typed-error path for unparsable volumes. No network.
