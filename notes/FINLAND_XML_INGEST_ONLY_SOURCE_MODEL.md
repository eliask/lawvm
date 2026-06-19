# Finland XML-Ingest-Only Source Model

Status: normative target for the Finland frontend.
Kind: phase-boundary and source-model contract.

Finland replay must converge on this boundary:

```text
source XML bytes -> acquisition/corrigendum/XML adapter -> AmendmentSourceModel
                 -> parse/elaborate/lower/apply/temporal using typed IR only
```

After `AmendmentSourceModel` construction, ordinary replay phases must not
receive, store, or query `lxml.etree._Element`, XPath results, XML roots, or
XML-root-retaining opaque refs. XML may reappear only in explicitly separate
oracle ingestion, debug witness rendering, or export projection.

## Allowed XML Zones

XML/lxml is allowed in:

- acquisition and archive lane selection;
- corrigendum/source patching before model construction;
- XML-to-source-model and XML-to-IR adapter code;
- consolidated oracle ingestion/adjudication;
- optional AKN/XML export projection;
- explicit debug witness rendering from stable source witness refs.

XML/lxml is not allowed in:

- frontend normalization after source model construction;
- compile-group surface, elaboration, lowering, or constraints;
- uncovered-body recovery;
- apply/replay fold;
- temporal authority and postprocessing;
- source-pathology, strict-mode, and proof projection logic except as serialized
  witness metadata.

## Source Artifact

Each amendment source model must carry an immutable artifact record:

```python
@dataclass(frozen=True, slots=True)
class SourceArtifact:
    jurisdiction: str
    source_ref: str
    source_lane: str
    retrieval_uri: str
    raw_sha256: str
    normalized_text_sha256: str
    ingest_rule_ids: tuple[str, ...]
```

The artifact record identifies which source lane was used and which ingest
rules changed transport shape. It does not authorize legal mutation by itself.

## Source Witnesses

Every source fact that can affect legal state must carry a stable witness ref:

```python
@dataclass(frozen=True, slots=True)
class SourceWitnessRef:
    artifact_ref: str
    node_path: tuple[SourcePathStep, ...]
    byte_span: ByteSpan | None
    text_span: TextSpan | None
    raw_fragment_sha256: str
    normalized_text_sha256: str
```

`node_path` is a canonical ordinal/source path, not a live XPath object and not
an lxml element. Witness refs may be rendered back to snippets for debugging,
but replay phases consume refs and hashes, not XML handles.

## Source Units

Source identity must be independent of legal labels. A unit ID is not
`section:5` or `chapter:2/section:5`, because duplicate labels, pseudo-chapter
markers, rebirths, malformed wrappers, and overbundled containers must remain
distinguishable.

```python
@dataclass(frozen=True, slots=True)
class SourceUnitId:
    source_ref: str
    local_id: str

@dataclass(frozen=True, slots=True)
class SourceUnit:
    unit_id: SourceUnitId
    kind: SourceUnitKind
    raw_label: str
    normalized_label: str
    parent_id: SourceUnitId | None
    children: tuple[SourceUnitId, ...]
    source_order: int
    part_label: str
    chapter_label: str
    wrapper_state: WrapperState
    witness: SourceWitnessRef
    tags: frozenset[str]
    diagnostics: tuple[SourceDiagnosticRef, ...]
```

`wrapper_state` records source-shape normalization such as
`exact`, `synthetic_logical`, `malformed`, `flattened`, `orphan_attached`,
`pseudo_chapter_marker`, or `container_only`. These states must be produced
with rule IDs and witnesses when they affect payload ownership or target
resolution.

## Body Model

The body graph is the single source of truth for coverage, observed body
inventory, payload lookup, source scope recovery, and uncovered-body recovery.
The current split between `ObservedBodyUnit`, `CoverageUnit`,
`_find_muutos_node`, and payload lookup is transitional.

```python
@dataclass(frozen=True, slots=True)
class SourceBodyModel:
    root_unit_id: SourceUnitId | None
    units: tuple[SourceUnit, ...]
    ignored_units: tuple[IgnoredSourceUnit, ...]
    indexes: SourceBodyIndexes
    payloads: tuple[PayloadSurface, ...]
```

Required derived views:

- `observed_units()` returns pairing units from source units, with
  `source_unit_id`, not `xml_element`;
- `coverage_units()` returns coverage units from the same graph, with
  `payload_ref` as `SourceUnitId` or `PayloadSurfaceId`, not lxml;
- ignored/malformed units are first-class records, not side-channel list
  mutation;
- source indexes cover `(kind, normalized_label, part, chapter)`, direct body
  sections, no-eId sections, chapter/part containers, pseudo-chapter/part
  segments, item labels, omission markers, sparse blocks, and container-only
  chapter facts.

## Lookup

Lookup must return typed results, not `None` plus implicit caller knowledge:

```python
@dataclass(frozen=True, slots=True)
class SourceUnitQuery:
    kind: SourceUnitKind
    normalized_label: str
    chapter: str = ""
    part: str = ""
    purpose: LookupPurpose = "payload"

@dataclass(frozen=True, slots=True)
class SourceLookupResult:
    status: Literal["unique", "missing", "ambiguous", "unsupported"]
    unit_id: SourceUnitId | None
    candidates: tuple[SourceUnitId, ...]
    diagnostics: tuple[SourceDiagnostic, ...]
```

No lookup may silently choose by traversal order. Ambiguity must remain typed.
A caller that proceeds through ambiguity must already carry a strict-mode
governed finding or emit one in the owning phase.

## Payload Surface

Payload IR belongs to the source model. Compile-group code asks the model for a
payload surface; it does not call `fi_xml_to_ir_node`, `_find_muutos_node`, or
sibling XML helpers.

```python
@dataclass(frozen=True, slots=True)
class PayloadSurface:
    payload_id: PayloadSurfaceId
    owning_unit_id: SourceUnitId
    target_query: SourceUnitQuery | None
    payload_ir: IRNode | None
    cross_heading_ir: IRNode | None
    coverage: PayloadCoverageKind
    ownership: PayloadOwnership
    normalization_events: tuple[SourceNormalizationEvent, ...]
    diagnostics: tuple[SourceDiagnostic, ...]
```

Payload ownership records which source unit owns the payload, which child
units are included or omitted, which fragments are carried context, whether
orphan subsections or unlabeled continuations were attached, whether logical
chapters/parts were synthesized, and whether the payload is complete enough
for whole-unit replacement.

## Metadata, Johto, And Temporal Surfaces

`AmendmentSourceModel` must own source metadata currently rediscovered from XML:

- title, issue date, publication identifiers, source lane, and route inputs;
- operative-structure flags and source-title routing facts;
- amendment effective date, expiry date, effective derivation step, contingent
  activation facts, decree/search surfaces, and section/provision expiry
  overrides.

The chosen johtolause surface must also be model-owned:

- raw and normalized johtolause text;
- source witness;
- parse result / ClauseAST;
- citations and meta clauses;
- residual or unparsed spans;
- johto-mentioned sections, moment targets, numbered-table targets, chapter
  mentions, and operative keyword flags used by recovery.

Temporal authority and temporal postprocessing should inspect the model's
temporal surface. They must not rescan XML text after model construction.

## Diagnostics And Strict Mode

Source diagnostics are first-class model records:

```python
@dataclass(frozen=True, slots=True)
class SourceDiagnostic:
    rule_id: str
    phase: Literal["ingest", "source_model", "payload", "johto", "temporal"]
    role: Literal["observation", "obligation", "violation"]
    blocking: bool
    unit_ids: tuple[SourceUnitId, ...]
    witnesses: tuple[SourceWitnessRef, ...]
    strict_gate: str | None
    detail: Mapping[str, object]
```

Every source-shape repair currently hidden in XML traversal becomes either a
`SourceNormalizationEvent` or a `SourceDiagnostic`. Strict mode evaluates these
records; it does not rediscover source facts from XML.

## Export Separation

AKN/XML export is a downstream projection:

```python
class XmlExportProjection(Protocol):
    def export_statute(self, ir: IRNode, *, profile: ExportProfile) -> bytes: ...
```

Exported XML is not compile/apply authority unless it is explicitly ingested as
a new source artifact lane. Oracle-compatible XML rendering is separate from
source witness authority.

## Mechanical Gates

The final migration must add static gates proving that post-acquisition replay
modules do not import or expose `lxml`, `etree._Element`, XPath, `.muutos_tree`,
`payload_ref` as XML, or `xml_element`.

The allowlist is limited to acquisition, corrigendum, XML adapter/model-builder
modules, oracle ingestion/adjudication, export projection, and explicit debug
witness rendering.

## Migration Order

1. Expand `AmendmentSourceModel` into a dossier: artifact, metadata, johto,
   temporal surface, source-unit graph, indexes, payload surfaces, diagnostics,
   and witnesses.
2. Convert body coverage and body pairing to views over source units. Remove
   lxml `payload_ref` and `xml_element` from downstream contracts.
3. Replace `_find_muutos_node` users with typed `find_unit` /
   `payload_for` results. Start with constraints, scope recovery, compile-group
   scope recovery, and `scope.py`.
4. Port frontend XML body fallback helpers to source-model queries: direct body
   sections, no-eId sections, item labels, omission checks, sparse blocks,
   temporary payload text, and metadata enrichment.
5. Port temporal authority and postprocessing to model-owned temporal surfaces.
6. Remove `muutos_tree` from process, frontend, compile, apply, and temporal
   phase request types.
7. Add static lxml exposure gates, parity tests for lookup/coverage/pairing/
   temporal metadata, strict-mode parity, and corpus replay parity for hard
   statutes such as `1992/1535`, `2017/320`, and `2009/862`.

The migration rule is: do not delete evidence to remove XML. Replace XML
handles with stable source witnesses and derived IR, then prove parity at the
operation, finding, strict-mode, temporal, migration, and structural-diagnostic
layers.

