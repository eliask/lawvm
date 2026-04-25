# U.S. Assets

This directory is the U.S. federal adapter area inside LawVM.

Initial scope should be narrow.

Recommended first pass:

- federal statutes only
- not regulations yet
- use official U.S. Code, public law, and classification-table sources
- build a small source-map prototype before deciding on a larger frontend shape

## Likely Subareas

- `manifests/`
  tracked fetch manifests for small federal samples
- `data/raw/`
  fetched U.S. source artifacts
- `compiled/`
  machine-usable intermediate artifacts

## Current Direction

The U.S. does not look like one corpus.

It already appears to split into:

- statutory frontend
- regulatory frontend

The statutory side should likely come first.
