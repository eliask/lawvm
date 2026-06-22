> **Status (2026-06-22):** Current-with-noted-drift. Kind: Descriptive/derivative drafting guide (changes no parsing behaviour). Tier counts drifted vs `johtolause/rule_registry.py` `_REGISTER_TIERS`: now 32 canonical / 8 accepted / 23 discouraged / 5 archaic = 68 forms (doc says 7/22/66). Re-derive the accepted+discouraged tables and the headline count from the registry.

# Finnish Amendment-Clause Drafting Grammar (Best Practice)

Status: descriptive drafting guide for lainvalmistelijat (legislative drafters).
Kind: derivative documentation — **recommends** a canonical subset of johtolause
forms. It does not change parsing behaviour.

## What this is

A **johtolause** is the amendment instruction at the head of an amending act:
the sentence(s) that say *which* provisions of a target statute are changed and
*how* — e.g. `muutetaan 12 §`, `kumotaan 3 luku`, `lisätään 8 §:ään uusi 3
momentti`, `Tämä laki tulee voimaan ...`. LawVM's Finland frontend recognises 66
distinct johtolause constructions, each catalogued as a parse rule with a stable
`rule_id`.

Every one of those 66 forms is **parsed** — old laws are immutable, and the
compiler must read whatever was actually enacted, however it was phrased. But
not every parseable form is one a drafter *should reach for today*. This guide
classifies each construction into a **register tier** and, for the forms below
the canonical line, gives the canonical rewrite and the reason.

### The principle: parsed ≠ recommended

The register tier is **metadata only**. It never affects parsing. It is a
recommendation for *new* drafting, derived from a multi-axis criterion — a form
is pushed below `canonical` if it trips any of:

- **archaic register** — bad even if unambiguous (old ministerial anaphora,
  genitive-plural prefix forms); plain-drafting and convention-uniformity
  grounds;
- **ambiguous / hack-requiring / silent-drop-prone** — needs a special-case
  parser arm or a precedence rule, or leans on discourse/anaphora resolution of
  an antecedent that a fresh reader cannot see locally;
- **needless variant** — a second way to say exactly what a canonical form
  already says.

Frequency is *evidence*, not the criterion. A rare-but-clear modern composition
of standard parts stays `accepted`.

### The four tiers

| Tier | Count | Meaning for new drafting |
|------|-------|--------------------------|
| **canonical** | 32 | The dominant clean modern forms. Use these. |
| **accepted** | 7 | Rare but modern and unambiguous. Fine to use. |
| **discouraged** | 22 | Needlessly variant / ambiguous / anaphora- or context-dependent / catch-all. Prefer the canonical alternative. |
| **archaic** | 5 | Archaic register. Parsed forever, never recommend. |

The counts are authoritative against
`src/lawvm/finland/johtolause/rule_registry.py` (the single source of truth).

---

## 1. Canonical core — the drafting vocabulary (32 forms)

These are the forms to build new johtolauses from. Grouped by family.

### 1.1 Target references — section / chapter / part / nimike / appendix

| Form | Example | Notes |
|------|---------|-------|
| Section ref (`fi.section_ref`) | `muutetaan 12 §` | The workhorse. Lists and ranges too: `muutetaan 3, 5 ja 7 §`, `muutetaan 21–23 §`; letter suffix: `muutetaan 5 a §`. |
| Chapter ref (`fi.chapter_ref`) | `kumotaan 3 luku` | Carries context down: `muutetaan 3 luvun 12 §:n 2 momentti`. |
| Part ref (`fi.part_ref`) | `muutetaan 1 osa` | Roman numerals supported: `muutetaan III ja V osa`. |
| Nimike ref (`fi.nimike_ref`) | `muutetaan nimike ja 1 §` | The statute's own title. |
| Appendix ref (`fi.appendix_ref`) | `muutetaan 1 § ja liite` | `liite` with optional number. |
| Version binding (`fi.target_version_binding`) | `... sellaisena kuin se on laissa X` | Binds the cited labels to a specific statute version. |

### 1.2 Sub-references — qualifying within a provision

| Form | Example |
|------|---------|
| Momentti (`fi.sub_ref_momentti`) | `muutetaan 5 §:n 2 momentti` |
| Kohta (`fi.sub_ref_kohta`) | `muutetaan 5 §:n 1 momentin 3 kohta` |
| Otsikko (`fi.sub_ref_otsikko`) | `muutetaan 6 §:n otsikko` |
| Johdantokappale (`fi.sub_ref_johdantokappale`) | `muutetaan 15 §:n johdantokappale` |

### 1.3 Insertions — `lisätään ... uusi ...`

Always restate the explicit container the new material goes into.

| Form | Example |
|------|---------|
| Into a section (`fi.insertion_section_ill`) | `lisätään 8 §:ään uusi 3 momentti` |
| Into a momentti (`fi.insertion_momentti_ill`) | `lisätään 3 §:n 1 momenttiin uusi 5 kohta` |
| At law level (`fi.insertion_law_level`) | `lisätään lakiin uusi 5 a §` (also `uusi 3 luku`) |
| Into a chapter, illative (`fi.insertion_chapter_ill`) | `lisätään 10 lukuun ... uusi 14 §` |
| Chapter-scoped section (`fi.insertion_chapter_scoped`) | `N luvun M §:ään uusi ...` (chapter scope stated first) |

### 1.4 Sub-targets — the new unit being inserted

These name the kind of unit created inside an insertion.

| Form | Pattern |
|------|---------|
| Momentti (`fi.sub_target_momentti`) | `... uusi N momentti` |
| Kohta (`fi.sub_target_kohta`) | `... uusi N kohta` |
| Pykälä (`fi.sub_target_pykala`) | `... uusi N §` |
| Luku (`fi.sub_target_luku`) | `... uusi N luku` |

### 1.5 Heading placement

| Form | Example |
|------|---------|
| Heading before a section (`fi.heading_placement`) | `53 §:n edelle uusi luvun otsikko`; `38 §:n edelle uusi väliotsikko` |

### 1.6 Renumbering

| Form | Example |
|------|---------|
| Section renumber (`fi.section_renumber`) | `muutetaan 1 §:n numero 3:ksi` |
| Chapter renumber (`fi.chapter_renumber`) | `... luvun numero N:ksi` |
| Part renumber (`fi.part_renumber`) | `... osan numero N:ksi` |

### 1.7 `jolloin` consequence clauses

The canonical way to express the knock-on renumbering caused by an insertion.

| Form | Example |
|------|---------|
| Section (`fi.jolloin_section_renumber`) | `lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi` |
| Chapter / momentti (`fi.jolloin_chapter_renumber`) | `..., jolloin nykyinen 4–8 momentti siirtyvät 6–10 momentiksi` |
| Enriched tail (`fi.jolloin_renumber`) | canonical consequence-clause emitter (annotation pass) |

### 1.8 Meta clauses — commencement / expiry / transition / delegation

The structured `fi.meta_*` forms are canonical (see §4 for the heuristic
`meta_parse:*` duplicates to avoid).

| Form | Example |
|------|---------|
| Commencement (`fi.meta_commencement`) | `Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.` |
| Expiry (`fi.meta_expiry`) | `Tämä laki on voimassa 31 päivään joulukuuta 2025.` |
| Transition (`fi.meta_transition`) | `Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tuleviin asioihin.` |
| Delegation (`fi.meta_delegation`) | `Valtioneuvoston asetuksella voidaan antaa tarkempia säännöksiä ...` |

### 1.9 Textual amendment — word/phrase substitution

| Form | Example |
|------|---------|
| Single word (`fi.text_amend_sana`) | `5 §:n 2 momentissa sana "lääninhallitus" korvataan sanalla "aluehallintovirasto"` |
| Multiple words (`fi.text_amend_sanat`) | `sanat "kauppa- ja teollisuusministeriö" korvataan sanoilla "työ- ja elinkeinoministeriö"` |

---

## 2. Accepted — rare but modern and clear (7 forms)

Legitimate compositions of standard parts. No reason to discourage; just
uncommon.

| Form | Pattern | Note |
|------|---------|------|
| `fi.scope_block_chapter` | `N luvun` scoping a group of section targets | Chapter-scoped target block. |
| `fi.scope_block_part` | `N osan` scoping a group of targets | Part-scoped target block. |
| `fi.coordinated_part_chapter_heading_ref` | `osan/luvun otsikko` coordinated | Coordinated part + chapter heading reference. |
| `fi.heading_edelle_luvun_otsikko` | `N §:n edelle ... luvun otsikko` | The `edelle luvun otsikko` heading placement. |
| `fi.lukuun_ottamatta_exception` | `lukuun ottamatta N §` | A clear, rare carve-out from scope. |
| `fi.valiotsikko_heading_ref` | `5 § ja sen edellä oleva väliotsikko` | References the väliotsikko preceding a named section. Clear because the section is named in the same clause. |
| `fi.text_amend_target` | section ref inside a text-amend clause | The target ref consumed by a `korvataan` clause. |

---

## 3. Discouraged — prefer the canonical alternative (22 forms)

For each: the pattern, why it's discouraged, and what to write instead.

### 3.1 Anaphora-dependent inserts

These omit the antecedent section/momentti and rely on the reader (and the
parser) resolving it from earlier discourse. Ambiguous for a fresh reader and a
source of cosmetic parse deltas. **Fix: restate the container explicitly**
(`fi.insertion_section_ill` / `fi.insertion_momentti_ill` / etc.).

| Form | Pattern | Canonical rewrite |
|------|---------|-------------------|
| `fi.insertion_chapter_anaphoric` | `lukuun uusi N §` (chapter implied) | Name the chapter: `N lukuun uusi M §`. |
| `fi.anaphoric_pykala_ill` | `pykälään uusi N momentti` | `N §:ään uusi M momentti`. |
| `fi.anaphoric_momentti_ill` | `N momenttiin uusi ...` (section implied) | `N §:n M momenttiin uusi ...`. |
| `fi.anaphoric_bare_uusi` | `uusi N momentti` (container implied) | `N §:ään uusi M momentti`. |
| `fi.anaphoric_determiner_insert` | `sanottuun/mainittuun/samaan pykälään uusi ...` | Restate the number: `N §:ään uusi ...`. |

### 3.2 Cross-verb-group context inheritance

These lean on the parser's `VerbGroupContext` to carry a section across a verb
boundary — fragile, and invisible to a local reader. **Fix: restate the target
in the new clause.**

| Form | Pattern | Canonical rewrite |
|------|---------|-------------------|
| `fi.cross_verb_momentti` | `momenttiin uusi ...` inheriting section across verbs | `N §:n M momenttiin uusi ...`. |
| `fi.cross_verb_bare_uusi` | `uusi ...` inheriting section across verbs | Restate `N §:ään uusi ...`. |
| `fi.cross_verb_move_retarget` | section moved to another chapter, target inherited | State the section and destination chapter explicitly. |
| `fi.direct_section_relabel` | `§:n numero M:ksi` resolved from context | Use the explicit `fi.section_renumber`: `N §:n numero M:ksi`. |

### 3.3 Renumber backref continuation

| Form | Pattern | Canonical rewrite |
|------|---------|-------------------|
| `fi.renumber_backref` | `... ja mainitun/mainittujen pykälän <sub_ref>` | Restate the section number in the continuation rather than `mainittu`-anaphora. |

### 3.4 Heading-arm continuations / glued heading targets

These required parser-precedence work to stop them truncating an enumeration
(witness deltas in 2003/1067, 2007/461), or glue a heading qualifier onto a
section ref with an ambiguous boundary. **Fix: use the plain
`fi.heading_placement` form, and keep the heading and the section ref as
separate, clearly delimited targets.**

| Form | Pattern | Canonical rewrite |
|------|---------|-------------------|
| `fi.heading_edelle_otsikko_after_uusi` | `uusi N § edellä otsikko` | `N §:n edelle uusi väliotsikko` as its own clause. |
| `fi.heading_edelle_otsikko_target_list` | `<list> §:n edelle uusi otsikko` inside a coordinated insert | Split into clear per-section heading placements. |
| `fi.including_preceding_heading_target` | `N § otsikko` (section glued to its heading) | Reference the section and the heading separately. |

### 3.5 Catch-all insertion buckets

A witness that signalled an insertion shape but matched no precise rule — by
definition off the recommended subset. If your clause lands here, it means the
shape was underspecified. **Fix: rephrase to one of the precise
`fi.insertion_*_ill` canonical forms.**

| Form | Meaning |
|------|---------|
| `fi.insertion_section` | generic section insertion |
| `fi.insertion_chapter` | generic chapter insertion |
| `fi.insertion_heading` | generic heading insertion |
| `fi.insertion_sub_target` | generic sub-target insertion |
| `fi.insertion_other` | unclassified insertion pattern |

### 3.6 Heuristic meta duplicates

Sentence-level heuristic re-detections of the structured meta clauses — a
needless second path to the same result. **Fix: write the clause in its standard
form; the structured `fi.meta_*` path is canon.**

| Form | Canonical equivalent |
|------|----------------------|
| `meta_parse:commencement` | `fi.meta_commencement` |
| `meta_parse:expiry` | `fi.meta_expiry` |
| `meta_parse:transition` | `fi.meta_transition` |
| `meta_parse:delegation` | `fi.meta_delegation` |

---

## 4. Archaic — never recommend (5 forms)

Parsed forever (legacy text is immutable), but archaic register. Always prefer
the canonical modern form.

| Form | Pattern | Why archaic | Canonical replacement |
|------|---------|-------------|-----------------------|
| `fi.section_ref_pykala_prefix` | genitive-plural prefix `pykälien N, M ...` | Archaic ministerial register; a needless variant of the postfix form. | `N, M §` (postfix `§`, i.e. `fi.section_ref`). |
| `fi.backref_singular` | `mainitun pykälän ...` / `mainittu pykälä` | Old-style anaphora; not self-contained. | Restate the section number (`N §:n ...`). |
| `fi.backref_plural` | `mainittujen pykälien ...` | Old-style plural anaphora. | Restate the section numbers (`N ja M §:n ...`). |
| `fi.chapter_ref_reversed` | reversed numeric chapter range, e.g. `5–2 luku` | Malformed/buggy shape preserved only to parse legacy text. | Ascending range, e.g. `2–5 luku`. |
| `fi.insertion_section_postfix_chapter` | `lisätään uusi N § ... lukuun` (chapter scope trails the section) | Single-fossil weirdness; chapter scope in the wrong place. | Put the chapter scope first: `N lukuun ... uusi M §` (`fi.insertion_chapter_ill`). |

---

## 5. Drafting checklist

When drafting a johtolause, prefer the canonical subset:

1. **Name every container explicitly.** Write `N §:ään uusi M momentti`, not
   `uusi M momentti` or `sanottuun pykälään uusi ...`. No anaphora, no implied
   section inherited from an earlier clause.
2. **Use postfix `§`.** `N, M §`, never `pykälien N, M`.
3. **Restate, don't back-reference.** Replace `mainitun/mainittujen pykälän`
   with the actual section number(s).
4. **Keep headings and section refs as separate, delimited targets.** Use the
   plain `N §:n edelle uusi väliotsikko` form; don't glue `N § otsikko` together.
5. **Ascending ranges only.** `2–5 luku`, never `5–2 luku`.
6. **Chapter scope first in chapter inserts.** `N lukuun ... uusi M §`, not a
   trailing `... lukuun`.
7. **Use the standard meta-clause wording** (`Tämä laki tulee voimaan ...`,
   `on voimassa ...`, `Tätä lakia sovelletaan ...`, `antaa tarkempia
   säännöksiä ...`).
8. **`jolloin` for consequential renumbering** caused by an insertion:
   `..., jolloin nykyinen N § siirtyy M §:ksi`.

If a clause cannot be expressed in a canonical form, that is a signal the
instruction is underspecified — make the target and the operation explicit.

---

*Source of truth: `src/lawvm/finland/johtolause/rule_registry.py` (`register`
field and `_REGISTER_TIERS`). Tier counts: 32 canonical / 7 accepted /
22 discouraged / 5 archaic. This guide is purely derivative documentation and
changes no parsing behaviour.*
