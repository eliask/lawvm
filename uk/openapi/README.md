# UK OpenAPI Snapshots

The files in this directory are the originally downloaded legislation.gov.uk OpenAPI
artifacts.

Important detail: they are not all in the same shape.

- Some are plain YAML.
- Some are HTML pages that embed the actual Swagger/OpenAPI JSON inside a `var spec = ...`
  block.

Use the bootstrap command below to normalize them:

```bash
uv run --project . lawvm-uk-bootstrap normalize-openapi
```

Normalized outputs are written to `uk/openapi/normalized/`.
