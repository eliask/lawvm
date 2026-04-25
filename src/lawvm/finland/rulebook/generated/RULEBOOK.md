# Finland Rulebook

## Clause Rules

Family id: `clause`

Clause parsing rules.

### fi.clause.shared_intro_over_conjuncted_momentti

- Phase: `clause_parse`
- Priority: `220`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Bind johdantokappale to every coordinated momentti in the same genitive chain
- Examples:
  - `dual moment intro`
    - text: `muutetaan 20 §:n 2 ja 3 momentin johdantokappale`
    - expects: `replace section:20/subsection:2 facet:intro`, `replace section:20/subsection:3 facet:intro`

### fi.clause.jolloin_renumber_pair

- Phase: `clause_parse`
- Priority: `210`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Keep jolloin-driven renumber pairs scoped to the immediate pair being renumbered
- Examples:
  - `renumber pair with jolloin`
    - text: `jolloin 3 §:n 1 ja 2 momentit numeroidaan uudelleen`
    - expects: `renumber pair:3/1`, `renumber pair:3/2`

### fi.clause.lukuun_ottamatta_exception_scope

- Phase: `clause_parse`
- Priority: `205`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Keep lukuun ottamatta phrases scoped as exclusions, not as enacted target text
- Examples:
  - `exception scope`
    - text: `muutetaan 1 § lukuun ottamatta 2 momenttia`
    - expects: `exclude section:1/subsection:2`

## Payload Rules

Family id: `payload`

Payload shape rules.

### fi.payload.omission_sibling_context

- Phase: `payload_normalize`
- Priority: `140`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Carry non-claimed siblings beside omission markers as context, not payload
- Examples:
  - `omission sibling context`
    - xml: `<section><kohta>1 kohta</kohta><omissio>2-4</omissio></section>`
    - expects: `item:1=context_carried`, `omission=omitted_context`

### fi.payload.lettered_subitems_attach_previous_if_explicit

- Phase: `payload_normalize`
- Priority: `135`
- Authority: `enacted_text`
- Strength: `conventional`
- Purpose: Attach a lettered subitem run to the preceding numbered item only when the host signal is explicit
- Examples:
  - `explicit parent host`
    - text: `4) ...; a) ...; b) ...; c) ...; 5) ...`
    - expects: `item:a=parented_to_1`, `item:b=parented_to_1`
    - rejects: `auto_attach_to_5`

### fi.payload.lettered_subitems_ambiguous_default

- Phase: `payload_normalize`
- Priority: `120`
- Authority: `lawvm_policy`
- Strength: `policy`
- Purpose: Leave lettered subitem parentage unresolved when the host signal is not explicit enough
- Examples:
  - `ambiguous lettered run`
    - text: `4) ...; a) ...; b) ...; c) ...; 5) ...`
    - expects: `unresolved_subitem_parentage:run`

### fi.payload.table_with_named_rows

- Phase: `payload_normalize`
- Priority: `130`
- Authority: `finlex_akn_profile`
- Strength: `literal`
- Purpose: Preserve table rows with explicit names as named row payload, not anonymous text
- Examples:
  - `table row names`
    - xml: `<table><tr><th>a</th><td>1</td></tr></table>`
    - expects: `table_rows:named`

### fi.payload.sparse_subsection_body

- Phase: `payload_normalize`
- Priority: `128`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Keep sparse subsection bodies explicit instead of collapsing them into surrounding prose
- Examples:
  - `sparse subsection`
    - xml: `<section><momentti>1 mom.</momentti><p>...</p></section>`
    - expects: `subsection:sparse_body`

### fi.payload.intro_list_continuation

- Phase: `payload_normalize`
- Priority: `125`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Carry intro/list continuations as structured continuation payload
- Examples:
  - `intro continuation`
    - text: `edellä 1 momentissa tarkoitetussa kohdassa ...`
    - expects: `intro_continuation:structured`

## Temporal Rules

Family id: `temporal`

Temporal scope rules.

### fi.temporal.valiaikaisesti_immediate_target_cluster

- Phase: `temporal`
- Priority: `180`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Temporary marker applies to the immediately governed insert cluster
- Examples:
  - `21b only temporary`
    - text: `lisätään lakiin väliaikaisesti uusi 21 b § sekä uusi 21 c ja 22 b §`
    - expects: `temporary target section:21b`, `permanent target section:21c`, `permanent target section:22b`

### fi.temporal.commencement_extract

- Phase: `temporal`
- Priority: `175`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Extract commencement targets from explicit voimaantulo-style text
- Examples:
  - `commencement date`
    - text: `Tämä laki tulee voimaan 1.1.2027.`
    - expects: `commencement:1.1.2027`

### fi.temporal.expiry_extract

- Phase: `temporal`
- Priority: `170`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Extract expiry targets from explicit määräaikainen / asti-style text
- Examples:
  - `expiry date`
    - text: `Tämä laki on voimassa 31.12.2027 asti.`
    - expects: `expiry:31.12.2027`

### fi.temporal.deferred_commencement

- Phase: `temporal`
- Priority: `172`
- Authority: `enacted_text`
- Strength: `literal`
- Purpose: Keep explicit deferred commencement markers attached to the deferred activation window
- Examples:
  - `deferred commencement`
    - text: `Lain 1 § tulee voimaan myöhemmin erikseen säädettävänä ajankohtana.`
    - expects: `commencement:deferred`

### fi.temporal.phased_activation

- Phase: `temporal`
- Priority: `168`
- Authority: `lawvm_policy`
- Strength: `policy`
- Purpose: Represent phased activation as a structured activation policy rather than free text
- Examples:
  - `phased activation`
    - text: `Pykälät 1-3 tulevat voimaan vaiheittain.`
    - expects: `activation:phased`

## Source Rules

Family id: `source`

Source normalization rules.

### fi.source.editorial_heading_noise

- Phase: `source_normalize`
- Priority: `108`
- Authority: `oracle_editorial`
- Strength: `conventional`
- Purpose: Drop editorial heading noise before source comparison
- Examples:
  - `editorial heading noise`
    - text: `Lain voimaantulo`
    - expects: `source_normalization:drop_heading_noise`

### fi.source.omit_editorial_kumottu_banner

- Phase: `source_normalize`
- Priority: `110`
- Authority: `oracle_editorial`
- Strength: `conventional`
- Purpose: Drop editorial kumottu banners from source normalization output
- Examples:
  - `kumottu banner`
    - text: `kumottu laki ...`
    - expects: `source_normalization:drop_kumottu_banner`

### fi.source.editorial_source_tag_reclassification

- Phase: `source_normalize`
- Priority: `104`
- Authority: `oracle_editorial`
- Strength: `conventional`
- Purpose: Reclassify editorial source-tag wrappers instead of treating them as semantic content
- Examples:
  - `source tag wrapper`
    - xml: `<source>...</source>`
    - expects: `source_normalization:reclassify_source_tag`

### fi.source.reclassify_subsection_with_item_numbering

- Phase: `source_normalize`
- Priority: `102`
- Authority: `finlex_akn_profile`
- Strength: `heuristic`
- Purpose: Reclassify impossible subsection numbering as paragraph-shaped source, while recording the normalization fact
- Examples:
  - `subsection carrying item numbering`
    - xml: `<subsection><num>9)</num><content>...</content></subsection>`
    - expects: `reclassify subsection -> paragraph`, `record source_normalization_fact`

### fi.source.schema_invalid_body

- Phase: `source_normalize`
- Priority: `100`
- Authority: `finlex_akn_profile`
- Strength: `heuristic`
- Purpose: Flag malformed body trees as schema-invalid source instead of silently normalizing them away
- Examples:
  - `invalid body tree`
    - xml: `<body><section><section></body>`
    - expects: `source_normalization:flag_schema_invalid_source`

## Compare Rules

Family id: `compare`

Comparison rules.

### fi.compare.repeal_notice_editorial

- Phase: `compare`
- Priority: `160`
- Authority: `lawvm_policy`
- Strength: `policy`
- Purpose: Classify oracle repeal notice text against a replay repeal placeholder as editorial convention, not replay-missing
- Examples:
  - `oracle repeal notice vs replay placeholder`
    - expects: `compare_equivalent:repeal_notice_editorial`
    - rejects: `false_positive_xml_topology_drift`

### fi.compare.oracle_html_xml_topology_drift

- Phase: `compare`
- Priority: `155`
- Authority: `lawvm_policy`
- Strength: `policy`
- Purpose: Treat oracle HTML/XML topology drift as display-only when the substantive markers still align
- Examples:
  - `html/xml layout drift`
    - expects: `compare_equivalent:topology_drift_display_only`
    - rejects: `substantive_marker_mismatch`

### fi.compare.oracle_omission_blank

- Phase: `compare`
- Priority: `150`
- Authority: `oracle_editorial`
- Strength: `conventional`
- Purpose: Treat oracle omission blanks as a display convention rather than a semantic mismatch
- Examples:
  - `omission blank`
    - expects: `compare_equivalent:oracle_omission_blank`
    - rejects: `semantic_payload_mismatch`

### fi.compare.oracle_stale_source

- Phase: `compare`
- Priority: `148`
- Authority: `lawvm_policy`
- Strength: `policy`
- Purpose: Treat stale oracle source material as a compare-policy concern instead of a replay mismatch
- Examples:
  - `stale oracle source`
    - expects: `compare_equivalent:oracle_stale_source`
    - rejects: `replay_materialization_mismatch`
