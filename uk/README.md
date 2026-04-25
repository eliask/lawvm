# UK Assets

This directory is the UK jurisdiction adapter area inside the public LawVM repo.

## Subdirectories

- `openapi/`
  Raw OpenAPI downloads from legislation.gov.uk plus normalized machine-usable specs.
- `manifests/`
  Small tracked manifests describing sample fetch sets.
- `data/raw/`
  Download target for fetched UK legislation artifacts. This directory is gitignored.

## Acquisition Strategy

Use feeds/search/sitemaps for discovery and direct `data.*` URLs for document fetch.
Do not assume the Finland model of two local zip archives.
