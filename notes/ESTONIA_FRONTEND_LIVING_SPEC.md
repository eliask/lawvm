Status: living spec, intentionally partial.

# Estonia Frontend Living Spec

This note captures EE-specific frontend contracts that have become stable enough
to guide further work. It is not a full architecture document; it is a
constraint note for recurring Estonia replay/parser patterns.

## 1. Multi-subsection replace payloads are structural

If a subsection-level `replace` payload contains more than one numbered
subsection block, replay must not discard the later blocks.

Canonical example:

- `(2) ... 1) ... 5) ... (2 1) ...`

Required behavior:

- the targeted subsection is replaced
- appended numbered subsection blocks are materialized as real sibling
  subsections
- existing later siblings are not renumbered unless the source actually implies
  a real insert/shift

This is a replay-time structural contract, not an oracle-specific hack.

## 2. Plain-text subsection replaces clear stale child items

If a subsection with existing item children is replaced by a plain sentence
payload with no item markers, replay must clear the stale child items.

Do preserve child items when:

- the replacement is sentence-scoped and the subsection tail stays live
- the replacement is only an intro-style payload ending in `:`

Do not preserve child items merely because the old subsection happened to have
them.

## 3. Three-quote text_replace clauses split to many-old -> one-new

EE amendment language can express a chapter-scoped or statute-scoped text
replacement like:

- `asendatakse sõnad "OLD1" ja "OLD2" sõnadega "NEW" vastavas käändes`

This must compile to two `text_replace` ops:

- `OLD1 -> NEW`
- `OLD2 -> NEW`

Do not miscompile it as:

- `OLD1 -> OLD2`

## 4. EE case-inflected text_replace needs bounded phrase morphology

The bounded EE case-aware replacer must support recurring legal noun families
that appear in replay-critical global replacements.

Currently confirmed necessary families include:

- `minister`
- `ministeerium`
- `seadus`
- `riik`
- `relv`
- `süsteem`
- `moon`

It must also support comma-joined coordinated phrases when each segment is
individually inflectable, for example:

- `sõjarelv, laskemoon`
- `sõjarelv, relvasüsteem, sõjarelva laskemoon`

## 5. Source-backed vs unsourced oracle shifts must stay separated

When replay and oracle differ, the first question is whether any applied source
act actually contributes the oracle-side change.

If no applied source op or source reference supports the oracle-side wording,
the row is not automatically a replay target. It is a candidate for:

- `source_oracle_drift`
- `source_pathology`
- other adjudicated residual inventory buckets

Do not keep forcing parser/apply work on unsourced oracle-only shifts.

## 6. EE frontier hygiene rule

The active EE replay frontier should prefer:

- source-backed, currently open, commensurable rows

and deprioritize:

- already adjudicated non-zero rows
- unsourced oracle-only shifts
- future-oracle comparisons
- same-chain editorial drift

## 7. Same-section inherited subsection targets must fan out

EE amendment prose can target more than one subsection under the same section
without repeating the section number, for example:

- `paragrahvi 83 52 lõiget 2 ning lõike 3 esimest lauset ...`

This must compile to separate subsection targets under the same section:

- `section:83_52/subsection:2`
- `section:83_52/subsection:3`

Do not collapse the clause to only the first subsection target merely because
the later target inherits the section label from the earlier phrase.

## 8. Whole-chapter replace payloads must stay structural

When EE amendment prose says a whole chapter is replaced, for example:

- `10. peatükk muudetakse ja sõnastatakse järgmiselt: ...`

the parser must emit a chapter-level `replace`, and replay must materialize the
payload as a structured chapter with child sections, not as raw chapter text
attached to the first section found in the payload.

## 9. Chapter-qualified section inserts must prefer the real split chapter parent

EE source text can qualify an inserted section by an older plain chapter number
even when the live statute already stores the relevant section range under a
split chapter such as `10_1`.

When inserting a superscript section like `§ 54^12`, replay should prefer the
parent that already contains the best same-base predecessor (for example
`§ 54^11`) instead of blindly trusting the literal `chapter:10` qualifier from
the clause text.

## 10. Item-targeted sentence inserts must merge into the existing item body

If an EE act says a point is supplemented with an additional sentence, replay
must append that sentence to the existing item text rather than turning the new
sentence into a replacement or a duplicate item node.

If the existing item ends with list-style `;`, replay must restore the internal
sentence boundary before appending and then keep the final item-ending `;`.

## 11. Explicit deeper structural parents beat global predecessor heuristics

The superscript-section parent heuristic is only for ambiguous inserts.

If the amendment already names a deeper structural parent such as
`chapter + division`, replay must keep that explicit parent and must not jump
back to some other branch merely because an older same-base predecessor like
`§ 47` exists there.

The predecessor heuristic still applies for genuinely ambiguous chapter-only
cases such as split chapters, but it must stop at explicit deeper containers.

## 12. Mixed repeal clauses must fan out every trailing target family

EE repeal prose can mix several target families in a single instruction, for
example:

- leading item repeals
- same-section subsection repeals
- later sentence-only subsection repeals
- later section repeals and ranges

A clause like:

- `paragrahvi 21 lõike 1 punktid 5, 6 1 ja lõige 1 1 ning §-d 22, 22 1 ja 24, § 27 lõike 1 teine lause, lõike 3 teine lause ja lõige 4 ning §-d 27 1–29 tunnistatakse kehtetuks`

must not stop after the first matched family. The parser has to emit all of the
later subsection, sentence, and section repeals from the same clause.

## 13. EE numeric range expansion must bridge plain and superscript labels

EE source ranges can cross between plain and superscript section labels, for
example:

- `42–42 2`
- `27 1–29`

These are contiguous legal ranges, so expansion must include the missing middle
labels:

- `42, 42_1, 42_2`
- `27_1, 28, 29`

Treating those as only the endpoints silently drops real repeal ops.

## 14. Case-inflected replacement must support coordinated `või` phrases

Some EE source acts replace a single noun with a coordinated alternative phrase
that still needs case agreement, for example:

- `registripidaja`
- `perekonnaseisuametnik või perekonnaseisuasutuse ülesandeid täitev isik`

The bounded morphology layer must be able to inflect both sides of the `või`
coordination together so forms like:

- `registripidajale`
- `registripidajal`

become:

- `perekonnaseisuametnikule või perekonnaseisuasutuse ülesandeid täitvale isikule`
- `perekonnaseisuametnikul või perekonnaseisuasutuse ülesandeid täitval isikul`

This note should grow only when a pattern becomes reusable across more than one
statute family.

## 15. EE sentence-boundary logic must not split on ordinal date markers

EE sentence-targeted replace and insert rules operate on prose that often
contains ordinal dates inside a single sentence, for example:

- `... sellele aastale eelneva aasta 1. novembriks, kui Euroopa Liit ...`

Naive splitting on `. ` corrupts those operations by treating the `1.` ordinal
as the end of the first sentence, which then reattaches the old sentence tail
after a replacement payload.

The shared EE sentence splitter used by:

- first-sentence replacement
- second/third-sentence replacement
- `pärast esimest lauset lausega ...` insertion

must keep ordinal/date markers like `1. novembriks` inside the same sentence.

## 16. Omnibus target-title filtering must not accept prefix statute titles

EE multi-act amendment laws often contain sections such as:

- `Kaitseväeteenistuse seaduse muutmine`

## 17. Section-block splitting must ignore in-text `§` citations

EE section insert payloads can contain ordinary internal cross-references like:

- `... tulenevalt raamatupidamise seaduse § 13 2. lõikest.`

Section-block parsing must not treat those citations as the start of a new
section block. Real section boundaries should only be recognized where the
payload actually starts a new `§ N.` section heading/body.

If this rule is violated, replay silently truncates the current inserted
section and drops later subsections.

## 18. Plain subsection repeal ranges must cover intervening superscript siblings

When the source says a plain subsection range like:

- `lõiked 2–4 tunnistatakse kehtetuks`

replay must also clear existing inserted siblings that fall inside that live
range, such as:

- `2_1`
- `2_2`

This must be resolved against the current subtree at apply time, not guessed
purely from parser-side text expansion.

## 19. Item-list repeal must re-finalize the last surviving nonempty item

If a repeal wave empties later items in a subsection list, replay must
recompute list punctuation so the last surviving nonempty item ends with `.`
instead of a stale `;`.

Canonical shape:

- item `10_1` keeps `;` while items `11–18` still exist
- after items `11–18` are repealed, item `10_1` becomes the last live item and
  must be rewritten to end with `.`

## 20. Untitled omnibus intros with inline RT refs still carry target-isolation meaning

Untitled EE omnibus amendment paragraphs can begin with a statute intro like:

- `Kriminaalmenetluse seadustiku ja teiste seaduste muutmise seaduses (RT I, 21.03.2011, 2) tehakse järgmised muudatused:`

The inline RT parenthetical must not prevent target-statute extraction for
filtering. If replay is targeting some other act, that untitled paragraph must
be skipped before any inner `paragrahv N muudetakse` blocks are compiled.

Otherwise self-amendment sections of a foreign omnibus act leak into the wrong
target statute and create large bogus frontier rows.
- `Kaitseväeteenistuse seaduse rakendamise seaduse muutmine`

When replay is targeting the longer application-law title, strict target-act
filtering must not accept the shorter main-law title merely because it is a
substring prefix of the longer one.

In omnibus filtering:

- `Kaitseväeteenistuse seaduse muutmine` must not match
  `Kaitseväeteenistuse seaduse rakendamise seadus`
- but title checks should still accept the real target when the same statute
  name appears with extra structural prose such as
  `... 1. peatükki täiendatakse §-ga ...`

Otherwise a whole foreign-act amendment block can leak into the wrong EE base
statute and produce large false replay fronts.

## 17. Mixed statutes must resolve flat section inserts against the nearest real section family

Some EE statutes are structurally mixed: they have a few direct body-level
sections, but later numbered ranges live under nested `part/chapter/division`
containers.

For a flat insert like:

- `section:720_1`

replay must not default to body root merely because *some* direct sections
exist somewhere in the statute. It must first look for the nearest existing
same-base predecessor section and prefer that real parent container.

Otherwise inserts like `§ 720^1` land at the root while the oracle keeps them
under their actual nested branch.

## 18. Plural item text-replace clauses must recognize the `punkte` form

EE amendment prose can target plural items using accusative `punkte`, not only
nominative `punktid`, for example:

- `paragrahvi 709 lõike 15 1 punkte 4 ja 5 täiendatakse pärast sõnu ...`

Plural item fanout must treat `punkte` the same as `punktid`; otherwise the
clause falls through to subsection scope and rewrites the wrong descendant
items.

The plural-target branch must also preserve the normal `text_replace`
normalization for:

- delete-style clauses
- `pärast sõnu X sõnadega Y` insert-after-word clauses

so item-targeted fanout behaves the same as the ordinary single-target path.

## 19. Case-aware EE noun-family replacement must cover plural `-a` nouns and participial phrase plurals

Some large EE global rename acts rely on case-aware replacement from a simple
source noun into a multiword target phrase, for example:

- `teabevaldaja` -> `töötlev üksus`

That replacement is not usable in practice unless the bounded inflection layer
also covers:

- plural `-a` noun forms like `teabevaldajaid`
- participial phrase plurals like `töötlevaid üksusi`
- singular genitive noun-phrase contexts like
  `teabevaldaja salastatud ...` or `teabevaldaja taotlusel`

Without those forms, broad source-backed 2023 rename acts leave large false
replay fronts even though the parser already extracted the correct global op.

## 20. Same-source broad EE renames may require compatibility with later targeted rewrites

An EE amendment act can contain both:

- an early broad rename such as `teabevaldaja` -> `töötlev üksus`
- and a later targeted subsection replace such as
  `teabevaldajale` -> `töötlevale üksusele ja juurdepääsuõigusega füüsilisele isikule`

If replay executes the broad rename first, the later targeted clause may no
longer find its original old text. The EE text-replace layer therefore needs a
bounded compatibility path so later targeted rewrites can still extend the
already-renamed phrase instead of silently no-oping.

This is a replay-ordering survival rule, not a license for unbounded repeated
self-replacement. The compatibility forms should be minimal and source-backed.

## 21. Generic EE ministry/ministry-title helpers must supplement, not suppress, statute-specific parsing

Some EE omnibus acts carry both:

- a broad all-laws reorganization clause
- and dedicated target-statute paragraphs like
  `§ 5. Eesti territooriumi haldusjaotuse seaduse muutmine`

The generic helper path is useful, but it must not short-circuit normal
target-statute parsing. Mixed acts need both:

- the global rename ops from the generic clause
- the statute-specific ops from the dedicated amendment paragraph

Otherwise replay silently drops real target-specific amendments whenever the
same act also contains a blanket ministry-reorganization provision.

## 22. EE text-replace classification must survive explicit target lists between the verb and the replaced word

EE amendment prose often uses the form:

- `seaduses asendatakse § 8 lõike 4 punktis 2 ja lõikes 5, ... sõna "X" sõnaga "Y"`

Here the provision list sits between `asendatakse` and `sõna/sõnaga`. Text
replacement classification cannot assume the noun follows the verb immediately.

When this pattern appears, replay must still classify the clause as
`text_replace` and fan it out across all explicit targets in the preamble.

## 23. EE plural subsection ranges must treat the figure dash `‒` as a real range separator

Riigi Teataja source text uses multiple dash characters in numeric lists. For
plural subsection clauses like:

- `paragrahvi 6 lõiked 1‒3 muudetakse ja sõnastatakse järgmiselt`

the extractor must treat `‒` the same as `-` and `–`. Otherwise only the first
subsection is captured and the shared replace payload never reaches the rest of
the range.

## 24. Case-aware EE replacement must cover the `maavanem` noun family

Bounded case-aware replacement for institutional renames must include the
`maavanem` family so source-backed clauses like:

- `maavanem` -> `Rahandusministeerium`

rewrite forms such as:

- `maavanema`
- `maavanemale`
- `maavanemalt`

Without those forms, ministry-transfer acts compile correctly but replay leaves
the old office-holder wording behind in live provisions.

## 25. Singular EE sentence repeals must compile as sentence-scoped `replace`, not whole-subsection `repeal`

EE source acts use singular sentence-repeal clauses like:

- `paragrahvi 10 lõike 1 teine lause tunnistatakse kehtetuks`
- `paragrahvi 18 lõike 2 teine lause tunnistatakse kehtetuks`

These are not subsection repeals. They must compile as:

- target = the addressed subsection
- action = `replace`
- payload = empty content
- note = the sentence ordinal (`esimene|teine|kolmas lause ...`)

Otherwise replay deletes the whole subsection and creates a false frontier
cluster that is much larger than the real amendment semantics.

## 26. EE bounded phrase inflection needs adjective-modifier phrases plus case-insensitive noun-family support

EE case-aware `text_replace` does not just need bare noun families. It also
needs two bounded phrase capabilities:

- adjective-like modifiers such as `kohalik` in phrases like
  `Ameti kohalik asutus`
- capitalized institution heads such as `Amet`, plus head nouns like
  `juht` / `direktor`

That is enough to support source-backed renames like:

- `Ameti kohalik asutus` -> `Amet`
- `Ameti kohaliku asutuse juht` -> `Ameti peadirektor`

without turning the phrase layer into an open-ended morphology system.

## 27. Self-containing EE renames must not re-match inside inserted text, but must still rewrite later original occurrences

Some EE rename clauses produce a new phrase that contains the old phrase as a
suffix, for example:

- `Tehnilise Järelevalve Amet` -> `Tarbijakaitse ja Tehnilise Järelevalve Amet`

Sequential variant replacement will otherwise rescan its own inserted suffix and
produce duplicates like:

- `Tarbijakaitse ja Tarbijakaitse ja Tehnilise Järelevalve Amet`

But simply stopping after the first replacement is also wrong, because later
original occurrences in the same subsection still need rewriting. The safe EE
contract is:

- replace matched old-form variants against the original surface
- shield inserted text from later variant passes
- still allow other untouched original occurrences later in the string to rewrite

## 28. `aastaarv` clauses are ordinary EE `text_replace`

EE amendment clauses like:

- `paragrahvi 9 2 lõigetes 1 1 ja 1 2 asendatakse aastaarv "2019" aastaarvuga "2024"`

are not a special structural amendment. They are normal `text_replace` clauses
with:

- `old_text = 2019`
- `new_text = 2024`
- fanout across the explicit provision list

If `aastaarv` is not recognized as a text-replace noun, these clauses fall
through as `unknown` and leave clean year-only residuals on otherwise solved
rows.

## 29. EE `loetakse §-ks` must compile as `renumber` before any same-clause insert

Clauses like:

- `paragrahv 27 1 loetakse §-ks 27 2 ja seadust täiendatakse §-ga 27 1 ...`

are not just a replace-plus-insert pair. The old subtree keeps its identity and
must move to the destination label before the new provision with the old label
is inserted.

The EE contract is:

- emit a `renumber` op from the old section label to the new section label
- keep the op before the same-clause insert in sequence order
- snapshot the moved provision at the destination address for replay history

Without that, replay leaves the old subtree under the wrong label and creates a
fake block of missing oracle sections.

## 30. Mixed EE item-plus-subsection repeal clauses must fan out every trailing same-section repeal

EE repeal clauses can combine an item repeal and a later same-section
subsection repeal in one sentence, for example:

- `paragrahvi 14 lõike 1 punkt 7 ja lõige 2 tunnistatakse kehtetuks`

That is not only an item repeal. The parser must emit:

- repeal of `§ 14 lg 1 p 7`
- repeal of `§ 14 lg 2`

If the trailing same-section subsection repeal is dropped, replay preserves a
source-backed subsection that the source act explicitly repealed.

## 31. EE bench and pair-status scoring must replay explicit pairs at the oracle redaction's own effective date

EE explicit pair scoring is not allowed to use a fixed global cutoff. For:

- bench rows
- `ee-pair-status`

the replay cutoff must come from the explicit oracle XML's
`kehtivuseAlgus`.

Otherwise historical rows are silently re-scored against a later date, which
can:

- misclassify forward-looking pairs as commensurable
- pull in amendments that postdate the chosen oracle
- inflate active frontier rows with fake replay failures

## 32. Embedded EE target-section discovery must ignore stray in-text citations

When a giant omnibus `HTMLKonteiner` is split into embedded target sections,
selection must not accept a foreign section merely because its first body
paragraph mentions the target statute in a citation, for example:

- `Väljasõidukohustuse ... § 26 11 lõikes 3 asendatakse sõnad "jälitustegevuse seaduses" ...`

The safe contract is:

- header paragraphs like `§ 19. Jälitustegevuse seaduse ...` may match by
  section-header title
- non-header paragraphs may match only by the extracted statute intro
  fragment, not by arbitrary later citations

Without that guard, EE replay pulls foreign omnibus amendments into the target
statute and creates fake residual frontier work.

## 33. EE plural item clauses using the `§ N lõike M punktid ...` form are the same operation as `paragrahvi ... punktid ...`

EE source acts use both of these clause surfaces:

- `paragrahvi 14 lõike 1 punktid 11 ja 12 tunnistatakse kehtetuks`
- `§ 5 lõike 2 punktid 15 ja 16 tunnistatakse kehtetuks`

They are not different semantics. Both must compile to per-item operations under
the addressed subsection.

If the `§` form is missed, replay can degrade a narrow item repeal into a blunt
whole-subsection change, which then creates a large fake residual cluster on an
otherwise well-aligned row.

## 34. EE publisher-side generic rename acts must behave as persistent postpasses

Some EE acts do not behave like ordinary one-shot local amendments. They define
publisher-side replacement rules that RT continues to surface across the live
text, even when a later ordinary amendment reintroduces the old title surface.

Currently confirmed persistent-postpass family:

- `129062014109 § 107^3`
  - specific minister titles -> `valdkonna eest vastutav minister`
  - list / `ja` / `või` groupings -> `valdkondade eest vastutavad ministrid`

The EE contract is:

- parse these as global text-replace ops
- keep them in chronological position for ordinary replay
- also reapply them once as a bounded final postpass after later ordinary ops

Without that persistent pass, later source acts can reintroduce the old title
surface and create fake open frontier rows against oracle texts that visibly
carry the RT publisher-side normalization.

Other generic rename acts such as:

- `130062015004 § 107^4`
  - `Põllumajandusministeerium` -> `Maaeluministeerium`

should still be parsed as global text-replace families, but they are not yet
assumed to require the same persistent postpass. Reapplying them blindly can
over-normalize later reorganization chains like
`Maaeluministeerium -> Regionaal- ja Põllumajandusministeerium`.

## 35. Chapter-level EE repeal stubs must carry the same `kehtetu` marker as section and division repeal stubs

When EE replay materializes a whole-chapter repeal as a chapter heading plus
childless section stubs, those section stubs must be marked:

- `attrs={'kehtetu': True}`

The comparison layer already knows how to serialize childless `kehtetu` section
stubs to the empty oracle surface. If chapter-level repeal stubs omit that
marker, replay preserves titled sections like:

- `§ 28^1. Mobilisatsioonivaru ebaseaduslik kasutamine`
- `§ 28^2. Menetlus`

as false live text instead of matching the oracle's empty repealed shells.

## 36. EE case-inflected global renames need bounded `-ioon` noun support, including object-position override after verbs like `teavitab`

EE source acts can define statute-wide case-inflected renames such as:

- `Keskkonnainspektsioon` -> `Keskkonnaamet` `vastavas käändes`

The normal declension pass must cover the ordinary `-ioon` family surfaces:

- nominative/genitive/comitative style forms like
  - `Keskkonnaamet`
  - `Keskkonnaameti`
  - `Keskkonnaametiga`

But there is also a bounded ambiguity when the old noun's genitive and
partitive coincide while the new noun splits them. In object-position clauses
like:

- `teavitab Keskkonnainspektsiooni ...`

the replay side must not stop at the genitive-looking intermediate:

- `teavitab Keskkonnaameti ...`

It must rewrite to the bounded partitive object surface:

- `teavitab Keskkonnaametit ...`

This is not a license for broad syntax inference. The safe contract is:

- keep the ordinary case-inflected replacement deterministic and morphology-led
- add a narrow postpass only for clearly bounded verb-object contexts already
  observed in EE source/oracle pairs
- prefer this bounded override to compare-only normalization when the source act
  itself explicitly says the rename is case-inflected across the statute

## 37. EE case-inflected global renames need bounded `-uk` noun support for vehicle-family rewrites

EE global text-replace acts can rename a bare head noun into a longer phrase
while still requiring ordinary case inflection, for example:

- `sõiduk` -> `kindlustuskohustusega hõlmatud sõiduk` `vastavas käändes`

The replay-side bounded morphology must therefore cover the ordinary `-uk`
family at least far enough for the confirmed `Liikluskindlustuse seadus`
surfaces:

- `sõiduk`
- `sõiduki`
- `sõidukit`
- `sõidukiga`

This should stay a bounded noun-family rule, not a broad Estonian morphology
engine.

## 38. Leading subsection repeal must survive later mixed section/item repeal targets in the same clause

EE repeal clauses can start with one provision family and then continue into
later plain `§` targets, for example:

- `paragrahvi 30 lõige 4, § 31 ning § 33 punktid 2, 3, 6 ja 9–11 tunnistatakse kehtetuks`

The compiler contract is:

- keep the leading `paragrahvi 30 lõige 4` repeal
- also compile the later `§ 31` repeal
- also compile the later `§ 33` item repeals

Do not let the later plural-item match swallow the leading subsection repeal.

## 39. Section-level first-sentence replace must preserve the untouched tail of `lõige 1`

Some EE clauses target a section but semantically rewrite only the first
sentence of the existing `subsection:1`, for example:

- `paragrahvi 24 esimene lause muudetakse ja sõnastatakse järgmiselt`

When the section already stores its body under `subsection:1`, the apply
contract is:

- replace only the requested sentence inside `subsection:1`
- preserve the remaining later sentence tail
- do not collapse the whole section to the replacement payload

## 40. When all items under an EE lead-in subsection are repealed, the dead lead-in colon must finalize to a period

EE repeals can delete every item under a subsection whose introductory text ends
with `:`, for example:

- `paragrahvi 23 lõike 1 punktid 1–3 ... tunnistatakse kehtetuks`

After the last surviving live item disappears, replay must not leave the parent
subsection hanging with a bare colon. The bounded finalization contract is:

- if all child items under that subsection are now empty
- and the subsection intro text ends with `:`
- rewrite that terminal colon to `.`

## 41. A sole new EE amendment reference that is foreign to the target statute is source pathology, not replay work

Some EE base/oracle pairs differ by exactly one new amendment reference, but the
referenced source act is an unrelated omnibus that does not actually touch the
target statute.

Confirmed shape:

- base/oracle diff introduces one new amendment ref
- fetching that act shows another statute family entirely
- `parse_ee_amendment_ops(..., target_title=...)` emits `0` ops for the target
- oracle still carries substantive new text

In that case the safe EE contract is:

- do not force parser churn to manufacture fake target ops
- classify the affected replay/oracle deltas as `source_pathology`
- keep frontier ranking focused on rows with real source-backed replay work

This is especially important when the oracle-side changes look broad and clean
(`§§ 27–28` style reorganizations) but the only in-range source act is clearly
about unrelated statutes.

## 42. A sole new EE amendment can be real yet still leave a later oracle-only cluster as source pathology

There is a second boundary shape adjacent to rule 41:

- the base/oracle pair differs by exactly one new amendment reference
- that source act really does target the statute
- replay compiles and applies a bounded set of target ops from that act
- but the remaining replay/oracle deltas sit in later sections that the source
  act never mentions

Confirmed shape:

- the source act's target-statute block compiles cleanly
- the compiled ops cover one coherent early cluster
- the residual oracle-only material appears in a separate later cluster
- direct source inspection shows no mentions of those later sections or
  headings

In that case the safe EE contract is:

- do not treat the later cluster as a hidden parser miss just because the same
  act genuinely touches the statute elsewhere
- classify those later residuals as `source_pathology`
- keep replay work focused on rows where the missing cluster is actually
  source-backed

## 43. Superscripted `1^1. jagu` inserts currently collapse to duplicate `division:1` addresses

EE source acts can insert a new division labeled `1^1. jagu` inside a chapter
that already has `1. jagu`.

Confirmed shape:

- replay/source extraction inserts the new substantive section correctly
- oracle and replay both carry the new `§ 64^1`-style subtree
- but both sides expose the old `1. jagu` and inserted `1^1. jagu` through the
  same shared `division:1` address shape
- current compare/address logic can therefore only retain one division title at
  `chapter:N/division:1`

Current safe contract:

- do not treat the surviving lone division-title mismatch as a normal replay
  semantics bug once the inserted section subtree itself matches
- classify that residual under deterministic inventory until sibling-address
  disambiguation exists in shared IR/reporting

## 44. EE statute-wide `tekstis asendatakse ...` must also match one-word compound statute titles

EE source acts do not always spell the target statute as a separate title phrase
ending in its own `seaduse` word. Some acts use one compound statute-title
token directly, for example:

- `Autoveoseaduse tekstis asendatakse tekstiosa ...`

Confirmed failure shape:

- the clause is a real dedicated target-statute amendment
- the parser already handles ordinary bare forms like `seaduse tekstis
  asendatakse ...`
- but a compound title such as `Autoveoseaduse` falls through the statute-wide
  `text_replace` detector
- replay therefore emits an `ee-unknown ... no_target` op instead of the
  intended global rename

Current safe contract:

- statute-wide EE `text_replace` detection must accept compound one-word titles
  ending in `seadus`, `seadustik`, `koodeks`, or `määrus`
- if the clause is of the form `Xseaduse tekstis asendatakse ...`, compile it
  as an ordinary global `text_replace`
- do not inventory these as drift/pathology when the source clause is explicit
  and in-range

## 45. Later subsection qualifiers in a mixed EE repeal clause must not leak onto the first plain section target

EE mixed repeal clauses can start with a plain section target and then continue
with later subsection-qualified targets, for example:

- `§ 87^2, § 100^3 lõige 3, § 100^4 lõige 2 ... tunnistatakse kehtetuks`

Confirmed failure shape:

- the parser matches the first section reference correctly
- but later `lõige N` text from a different section leaks backward onto that
  first target
- replay then compiles `§ 87^2(3)` instead of a whole-section repeal of
  `§ 87^2`

Current safe contract:

- once a section reference has been matched, subsection/item qualifiers must be
  read only from the local text span that belongs to that section
- the next section reference in the same clause is a hard boundary
- mixed repeal lists must therefore preserve a leading plain section target as
  a whole-section repeal unless that same local span explicitly adds a
  subsection/item qualifier

## 46. EE bounded morphology must handle `-ve`, `-an`, and `-lik` word families

EE case-inflected `text_replace` clauses can target phrases where head nouns
end in `-ve` or `-an`, and modifier adjectives end in `-lik`.  These were
missing from `_ee_declension_forms`, causing `_ee_phrase_forms` to return
`None` and collapsing inflected-form replacement to nominative-only.

Confirmed cases:
- `riiklik järelevalve` (state supervision) — head ends in `-ve`
- `riiklik järelevalveorgan` (state supervision organ) — head ends in `-an`

Required paradigm rules:

**`-ve` nouns** (e.g. `järelevalve`, `haldusjärelevalve`):
- stem = word (same as nominative — no vowel drop)
- `sg_gen = stem`, `sg_part = stem + "t"`, `sg_ine = stem + "s"`, etc.
- `pl_gen = stem + "te"`, `pl_part = stem + "id"`

**`-an` nouns** (e.g. `järelevalveorgan`):
- oblique stem = word + "i" (e.g. `järelevalveorgani`)
- `sg_gen = stem`, `sg_part = stem + "t"`, `sg_ine = stem + "s"`, etc.
- `pl_nom = word + "id"`, `pl_gen = word + "ite"`

**`-lik` adjectives** (e.g. `riiklik`, `kohalik`):
- This must be matched BEFORE the generic `-ik` handler.
- The generic `-ik` handler uses `sg_part = word + "ut"` (e.g. `riiklikut`) —
  incorrect for `-lik` adjectives which require strong-grade gemination.
- Correct: `sg_part = word + "ku"` (e.g. `riiklikku`)
- Other oblique forms: `sg_gen = word + "u"`, `sg_ine = word + "us"`, etc.

These are bounded noun/adjective-family rules, not an open-ended morphology
engine.

## 47. EE `text_replace` run sort key must put longer old_text first

When an amendment emits both global ops (empty target path) and section-scoped
ops in the same source, applying scoped ops first can cause global chain ops to
corrupt already-replaced text.

Observed failure (`130062023001`):
- Global chain: `"Põllumajandusministeerium"` → `"Maaeluministeerium"` (A→B), then
  `"Maaeluministeerium"` → `"Regionaal- ja Põllumajandusministeerium"` (B→C)
- Section-scoped: `"Rahandusministeerium"` → `"Regionaal- ja Põllumajandusministeerium"`
- Wrong order (scoped first): section produces C, then global A→B matches B
  suffix inside C → `"Regionaal- ja Maaeluministeerium"`, then B→C doubles the
  prefix → `"Regionaal- ja Regionaal- ja Põllumajandusministeerium"`

The correct fix is NOT "always run global before scoped" — that breaks cases
where a global op's old_text is a proper substring of a section-scoped op's
old_text (global would partially consume the text before the longer scoped op
can match).

**Correct sort key:** `(-len(old_text), scope_rank, sequence)`.
- Primary: longer `old_text` first (more specific match wins).
- Tiebreak: global ops (scope_rank=0) before scoped ops (scope_rank=1).
- Final tiebreak: original authored sequence number.

This ensures that for the chain-corruption case, the longer global
`"Põllumajandusministeerium"` (25 chars) runs before the section-scoped
`"Rahandusministeerium"` (20 chars), while for the substring-preemption case,
the longer scoped op still runs before the shorter global op.

## 48. EE generic ministry reorganization exceptions must be carried onto inferred global ops

Generic ministry reorganization clauses can be parsed from a Vabariigi Valitsuse
seadus transition section and materialized as target-statute global
`text_replace` ops. Those inferred global ops must still preserve explicit
exceptions written in the source.

Observed failure (`130062023001` against `Kalapüügiseadus`):

- `§ 105^19(7)` states that in current and future laws, except for
  `kalapüügiseaduse § 90^2 lõikes 2`, `Maaeluministeerium` is replaced by
  `Regionaal- ja Põllumajandusministeerium`.
- LawVM materialized the global rewrite but did not carry the exception path.
- Replay therefore mutated `Kalapüügiseadus § 90^2(2)` outside the source's
  declared target region.

Correct behavior:

- parse the exception list only for the active target statute;
- attach the excluded structural paths to the inferred global op;
- preserve a stable rule marker:
  `ee_generic_ministry_reorganization_explicit_exceptions`;
- do not treat exceptions for other statutes as exclusions on the current
  target.

This is a source-scope ownership rule, not an oracle-matching rule.

## 49. EE single-occurrence insert rewrites must not silently choose among repeated matches

When source text says an exact target is supplemented before/after a word but
the live target contains that word more than once, LawVM must not silently pick
the first occurrence unless the source supplies a disambiguator such as
`läbivalt`, sentence position, or another local selector.

Observed failure (`102052024002` against `Maagaasiseadus § 26^7(1)`):

- source says the provision is supplemented after the word `gaasivaru`;
- the live subsection contains two occurrences of `gaasivaru`;
- Riigi Teataja's consolidated text applies the insertion to both occurrences;
- the source clause itself does not say whether one or both occurrences were
  intended.

Correct behavior:

- block the single-occurrence insertion for that exact target;
- emit `ee_ambiguous_single_occurrence_text_replace`;
- leave the replay/oracle difference classified as `source_ambiguity`;
- do not widen the operation to all occurrences merely because the oracle did.

This preserves the legal uncertainty instead of resolving it by Python match
order.

## 50. Old-format flat HTML amendment sections beat preambul single-target recovery

Some old `muutmismaarus` XML has no structural `paragrahv` nodes and stores a
single target-act amendment section as flat `HTMLKonteiner` paragraphs:

- a header paragraph naming the amended act
- followed by plain numbered `<p>N) ...</p>` amendment items

For this source shape, the old-format section parser owns lowering. Preambul
single-target recovery must not prepend the act header to every item and then
accept a META-heavy result merely because a few text replacements survived.

The parser-selection witness rule is:

- `ee_old_format_html_section_preferred_over_preambul_plain_body`

It may fire only when the old-format section parser produces strictly more
substantive non-META operations than the preambul recovery path.

## 51. Insert-after phrase anchors may need source-owned surface variants

Some Estonian amendment clauses supplement text after a quoted phrase, while
the live provision contains the same phrase with a narrow source/live surface
variant. Example from `122072011003`:

- source anchor: `projektitaotluse paremusjärjestuse`
- live text: `projektitaotluste paremusjärjestuse`
- inserted tail: `ettepaneku`

This is not permission to search the whole statute or rewrite a different
target. It is an exact-target text rewrite with a bounded morphology variant on
the quoted source surface.

The payload witness family is:

- `ee_insert_after_source_phrase_surface_variants`

Current allowed variant: first modifier ending in `-use` may match `-uste`
inside the same quoted phrase when the replacement is an insert-after expansion
whose new surface starts with the quoted old surface.

## 52. Explicit item replacement punctuation beats list-terminal normalization

When an amendment replaces an item with a quoted payload that already contains
the item terminal punctuation, replay must preserve that source terminal.

List-terminal normalization may still run for repeals, insertions, and payloads
without explicit terminal evidence. It must not turn a quoted replacement ending
in `.` into `;` only because later sibling items remain.

The replay marker is:

- `ee_explicit_item_replacement_terminal_preserved`

## 53. Quoted target names before the operative verb are not replacement payload

Old-format clauses can name a target appendix/title in quotes before the actual
operative verb:

`... määruse lisas „Tasandus- ja toetusfondi jaotus” asendatakse sõna „lisa”
tekstiosaga „lisa 1” vastavas käändes`

The quoted appendix title is target scope evidence. It is not the old text for
the replacement. The parser must search for the first operative verb before
splitting the instruction/payload surface.

The resulting operation family is still ordinary targeted text replacement.
When the replacement is `lisa -> lisa 1` with `vastavas käändes`, replay must
also apply the numeric suffix to declined forms such as `lisas` and `lisale`.

## 54. Singleton unlabeled regulation sections may be `§ 1`

Some old Riigi Teataja regulation XML exposes the only top-level `paragrahv`
with an empty `paragrahvNr`, while later consolidated surfaces expose the same
unit as `§ 1`.

The parser may relabel that section to `1` only when:

- there is exactly one top-level section;
- the section label is empty or absent;
- the section has provision content.

The source-cleanup rule is `ee_singleton_empty_section_label_to_1`. It must not
be generalized to multi-section documents.

## 55. Flat sectionless singleton item inserts are explicit fallback scope

Old-format EE amendments can say a regulation is supplemented with `punktiga N`
without naming a section, while the quoted payload starts with the same `N)`.
Some one-section regulations also state `lõike M punkt N sõnastatakse ...` after
the surrounding amendment act has already named the regulation, without
restating `§ 1`. For singleton regulations this compiles to:

- `section:1/subsection:1/item:N`

The insert-specific rule is `ee_flat_sectionless_singleton_item_insert`; the
subsection/item scope recovery rule is
`ee_flat_sectionless_singleton_subsection_scope`. Both carry
`scope_confidence=inferred_from_live_unique` because the source owns the item
label but not the omitted singleton section/subsection path.

## 56. Insert-after `tekstiosaga` payloads may contain nested quoted titles

When an EE clause says a provision is supplemented after quoted words with a
quoted `tekstiosaga`, the inserted text may itself contain a quoted title:

- old anchor: `tunnistamise kord`
- inserted tail includes: `“Eesti maaelu arengukavaga 2007–2013”`

The marker-aware insert-after parse owns this shape. Generic ordered quote
pairing must not truncate the payload at the nested title's closing quote.

## 57. Registry title aliases must match both surfaces

The act-identity registry is evidence that two title surfaces name one act. It
is not permission to admit every omnibus section once the target title has a
registry record.

Registry-backed target routing must require both the requested target title and
the candidate wrapper/header/fragment to match the same record. This prevents
flat omnibus acts from applying unrelated sections to the target statute.

Corpus witness:

- `128122024013` contains 31 flat old-format wrapper sections.
- For base `106102022005`, only wrapper `§ 26` targets the same act through the
  old `Maaeluministeeriumi...` title, intermediate
  `Regionaal- ja Põllumajandusministeeriumi...` title, and final shorter title.
- Wrapper `§ 25` is a similarly named 2015 act using `ja hoiu`; it is not the
  same record as the 2018 `ning hoiu` act.

The named target-routing repair is not a replay shortcut. It is exact registry
admission plus quoted-title extraction from wrapper headers.

## 57.1. Registry aliases cover title relabels between base and later source acts

Estonian amendment chains can rename a target act title before a later omnibus
amendment targets that same act under the renamed title. Pairwise replay still
parses each amendment against the original base title, so title routing cannot
depend on already-mutated live state at parse time.

This is not a live-unique fallback. It is registry-backed identity evidence:

- the old base title surface must match the registry record;
- the later wrapper/header quoted title surface must match the same record;
- unrelated omnibus sections remain rejected.

Corpus witness:

- base `122042022003` exposes `... Justiitsministeeriumi ametniku ...`;
- source `118072025001` wrapper § 7 targets the same regulation as
  `... Justiits- ja Digiministeeriumi ametniku ...`;
- oracle `118072025011` expects § 2 lõige 2 punktid 3 ja 4 from that wrapper.

The registry entry is family-level `title_relabel_alias` evidence, not a statute
ID special-case in replay.

## 58. Old-format wrapper sections can imply whole-regulation scope

Inside an admitted old-format wrapper section, an item can say only:

`asendatakse tekstiosa „OLD” tekstiosaga „NEW” vastavas käändes`

The wrapper header has already named the target regulation, so this is a
whole-regulation text replacement, not an unknown operation and not a target for
the amendment act's own section number.

The rule is:

- `ee_old_format_wrapper_scope_inherited`

It emits a statute-wide text replacement and keeps the wrapper section as
`old_format_amendment_section:*` provenance only.

Related title-only delete clauses such as `määruse pealkirjast jäetakse välja`
compile under:

- `ee_statute_title_text_delete`

That rule records title-surface scope in the text rewrite witness; it does not
invent a provision address.

## 58.1. Old-format direct-title wrappers can carry case-inflected global rewrites

Some omnibus ministry-name acts use one paragraph per target wrapper section:

`§ N. ... „TARGET” muutmine`

followed by an unnumbered body paragraph saying that in the target regulation
and its appendices a ministry name is replaced by another ministry name
`vastavas käändes`.

Once the wrapper header has been admitted by exact/registry title evidence, the
body clause is a whole-regulation typed text rewrite. It must not be parsed as a
payload-less text op, and the second paragraph must not be smuggled into a
structural replacement payload.

Corpus witness:

- `104072023001` wrapper § 44 targets `113052020006`
  `Teadlaste ja kalurite koostöötoetus`;
- it replaces `Maaeluministeerium` with
  `Regionaal- ja Põllumajandusministeerium` in the target and appendices,
  `vastavas käändes`;
- oracle `104072023045` expects the renamed ministry in § 5 and § 9.

The named extraction rule is:

- `ee_old_format_direct_title_case_inflected_text_replace`

## 59. Compact coordinated agency spacing is a replay morphology variant

Riigi Teataja source/oracle surfaces sometimes collapse coordinated agency names
without both expected spaces:

- source quote: `Põllumajandus- ja Toiduamet`
- live text: `Põllumajandus-jaToiduameti`
- oracle after replacement: `Maa-ja Ruumiameti`

Case-inflected text replacement may match the compact `-ja` form for the same
declined agency phrase. This belongs to text morphology, not target resolution.

## 59.1 Register-to-information-system illative forms are owned morphology

RT 2016 amendment `108122016002` changes the statute title and then applies a
whole-regulation case-inflected rewrite:

`riiklik pensionikindlustuse register` -> `sotsiaalkaitse infosüsteem`

The live regulation uses forms such as `riiklikku pensionikindlustuse
registrisse` in title/body positions. Generic phrase declension must not invent
`registerit`/`registerisse`-style surfaces and miss the witnessed illative.
LawVM owns this as an apply-time morphology family, not target recovery.

The named rule is:

- `ee_case_inflected_riiklik_register_infosusteem_forms`

## 59.2 Case-aware text rewrite morphology is frontend-owned, not grafter logic

Estonian `vastavas käändes` rewrites are represented as typed text-rewrite
payloads with source witnesses. The tree walker applies those payloads, but it
does not own the Estonian morphology or exception-family tables.

The boundary is:

- `peg.py` parses the source and annotates `case_inflected` plus `source_family`;
- `text_morphology.py` owns generic Estonian text-rewrite variant generation,
  explicit source-witnessed exception families, and the shared rule IDs;
- `grafter.py` applies the generated variants within the operation target
  region.

Irreducible forms should become named morphology families or unresolved
findings. They should not be reintroduced as anonymous branches in the tree
mutation code.

## 60. Publication DB outreach triage is a projection, not adjudication

The EE publication DB stores a separate outreach projection on each divergence:

- `outreach_bucket`
- `meaningful_candidate`
- `outreach_evidence`

This layer exists so outreach queries can omit punctuation/whitespace-only and
other already-classified residual rows without reimplementing residual-bucket
logic. It must not mutate replay text, oracle text, residual classifications, or
open-current counts.

The only automatic outreach candidate is an open current divergence with no
residual bucket. Known residual families are explicit exclusions, for example:

- `presentation_punctuation_whitespace` -> `excluded_presentation`
- `replay_coverage_gap` -> `excluded_replay_coverage`
- `table_fragment_replay_gap` -> `excluded_replay_coverage`
- `comparison_descendant_projection` -> `excluded_comparison_projection`
- `source_oracle_drift` -> `excluded_source_surface`
- `pair_surface_classification` -> `excluded_pair_surface`

This is publication/reporting metadata only. A `meaningful_candidate=1` row
still requires human review before Riigi Teataja outreach.

## 61. Cited-act section inserts keep the explicit `§-ga` target

Old-format wrapper clauses can cite a regulation number and title before the
operative verb:

`... määrust nr 22 „Title” täiendatakse §-ga 9^1 järgmises sõnastuses: ...`

The cited title is source scope, not inserted payload. The explicit `§-ga`
target must therefore preempt internal references that appear later inside the
inserted provision body. Otherwise references such as `paragrahvi 25 punkti 8`
inside the new section body hijack the operation target.

The parser rule is:

- `ee_act_citation_section_insert_target`

Corpus witnesses:

- `125082023001` inserts `§ 9^1` into `Gümnaasiumivõrgu korrastamine perioodil 2014-2020`; the inserted body later references `§ 25 punkt 8`.
- `113122023001` inserts `§ 9^1` into `Kaugküttesüsteemide investeeringute toetamise tingimused`; the inserted body later references `§ 27 lõike 2 punkt 8`.

## 62. Publication DB strips bounded electronic-appendix publication notes

Some older RT surfaces carry display/legal-basis notes inline with the section
text:

`Määruse lisad on avaldatud elektroonilises Riigi Teatajas. Alus: "Riigi Teataja seaduse" §4 lõige 2 ...`

Some older RT surfaces also carry bounded directive footnote tails inline in
the final section text, for example a leading footnote marker followed by a
European Parliament/Council directive citation ending in an `ELT L ... lk ...`
publication reference.

When removing exactly one of those bounded notes makes the replay and oracle
section texts equal, the publication DB classifies the row as:

- `publication_note_projection`

This is reporting metadata only. It does not mutate replay or oracle text and it
does not close broader directive footnote tails or other legal text.

Corpus witness:

- `113062014005 -> 126022019015` carries a `1 Euroopa Parlamendi ja EL
  nõukogu direktiiv 2002/19/EÜ ... (ELT L 108, 24.04.2002, lk 7–20).`
  directive footnote tail in the base § 12 XML, while the oracle omits it; the
  sole amendment `126022019001` only performs an agency rename and does not
  authorize deleting the footnote.

## 63. Section-level line-break list prefixes can belong to the first subsection

Some old `tyviseadus` RT XML serializes the beginning of a list directly under
`paragrahv/sisuTekst` and the tail of the same list as an unnumbered `loige`.
The legal surface is one subsection-level item list, but the transport shape is
split across parent and child XML nodes.

The parser may attach the direct section-level `reavahetus` items to the first
subsection only when the item labels are explicit and do not collide with the
first subsection's existing children. The subsection records:

- `ee_section_level_reavahetus_items_attached_to_first_subsection`
- `section_level_reavahetus_item_labels`

Corpus witness:

- `129112022006` stores § 1 items 1-3 and § 2 items 1-3 in direct section
  `sisuTekst`, while item 4 is an unnumbered `loige`; oracle `106122024011`
  materializes all items as subsection children after source act `106122024001`
  inserts items 5 and 6.

## 64. Quoted-act chapter inserts keep chapter scope before payload sections

Some amendment clauses cite a target regulation number and quoted title before
the operative verb:

`... määrust nr 45 „Title” täiendatakse 6. peatükiga järgmises sõnastuses: ...`

The explicit source target is the new chapter, not the first section or
subsection appearing inside the quoted chapter payload. The parser therefore
emits one chapter insert operation under:

- `ee_quoted_act_chapter_insert_target`

This prevents payload-local markers such as `§ 24` and `(1)` from hijacking
the operation target.

Corpus witness:

- `105082025001` inserts chapter 6 into `116072021008`; oracle
  `105082025007` contains the resulting `6. peatükk / § 24` transitional
  provisions.

## 65. Embedded target sections do not split inside open quoted payloads

Some new-format omnibus HTML embeds one target act section as paragraphs. A
chapter insertion payload can itself contain a paragraph-level section heading:

`... täiendatakse 10. peatükiga järgmises sõnastuses:`

followed by a quoted chapter heading paragraph, a `§ 28` heading paragraph, and
only then the closing quote in the body paragraph. The embedded-section splitter
must keep that `§ 28` paragraph inside the same amendment item; otherwise replay
inserts only the chapter heading and drops the section payload.

The source-shape rule is:

- `ee_embedded_open_quote_payload_section_header`

This is extraction ownership only. The executable target remains the explicit
chapter insert rule, for example:

- `ee_quoted_act_chapter_insert_target`

Corpus witness:

- `130052025004` inserts chapter 10 / § 28 into `106112024004`; oracle
  `130052025012` contains the full transitional provision.
- `127062025004` and `127062025003` insert `7. peatükk` and conjoin
  `sinna lisatakse § 22`; the section header and body remain part of the
  chapter payload.

## 66. Old-format open quoted payload section headers stay in the item

Some old-format `HTMLKonteiner` bodies are flat paragraph streams. An
amendment item can insert a new section with payload text beginning:

`... täiendatakse §-ga 31 järgmises sõnastuses: „§ 31. ...`

The inserted payload header can look like a top-level target-act section header
because it names the current regulation. The old-format section splitter must
not split at that header while the quoted payload is still open. Extraction tags
the affected item with:

- `ee_old_format_open_quote_payload_section_header`

This rule preserves the claimed inserted section payload and prevents the
following commencement section from being smuggled into it.

Corpus witness:

- `129062012059` inserts § 31 into `110112010011`; oracle `129062012064`
  contains the inserted section. The following `§ 2. Määrus jõustub...` is
  commencement metadata and must not become payload text.

## 67. Numbered body items beat duplicate preamble recovery

Some single-target amendment HTML has a normal numbered body item followed by
an out-of-body appendix clause:

`1) paragrahvi 2 tekst sõnastatakse...`

`2) määruse lisa kehtestatakse uues sõnastuses (lisatud).`

Preamble recovery can otherwise treat the appendix clause as another body
replacement for the previously seen section and overwrite the real section
payload. When old-format numbered-item extraction owns the body item and
preamble recovery produces duplicate body mutations for the same target, the
numbered-item extraction wins under:

- `ee_old_format_numbered_items_preferred_over_preambul_recovery`

Corpus witness:

- `119052022010` replaces § 2 of `109072013008`; oracle `119052022011`
  contains the § 2 list items. The following appendix-clause publication note is
  not a second § 2 body replacement.

## 68. Out-of-body appendix clauses must not inherit the previous body section

Old and new EE amendment HTML can list an appendix addition immediately after a
body-section replacement:

- `2) paragrahvi 3 tekst sõnastatakse järgmiselt: ...`
- `3) määrust täiendatakse lisaga ... (lisatud);`

The appendix item is not a `section:3` operation merely because `section:3` was
the last body target. It must be emitted as an explicit non-body/meta clause
under `ee_out_of_body_appendix_clause_not_section_scoped` or
`ee_old_format_out_of_body_appendix_clause_not_section_scoped`.

Do not attach the target-act routing intro to such a clause in a way that makes
generic extraction reinterpret it as a body replacement. The intro identifies
the amended act; it does not authorize section-scope carry into the appendix.

Corpus witness:

- `112092023001` changes `121122022022`; oracle `112092023005` keeps the
  replaced § 3 body. The following `määrust täiendatakse lisaga ... (lisatud)`
  item is a publication-side appendix clause, not another § 3 replacement.

## 69. Cross-act transitional helper is section-only, not mixed-list recovery

The fallback cross-act transitional repeal helper is only allowed for clauses
that name a target act and then list whole sections, for example:

- `§-d 2-4, 6 ja 7 tunnistatakse kehtetuks`

It must not parse a mixed section/subsection clause such as:

- `§-d 1-5, § 6 lõiked 1-3 ning 6-10, §-d 7-10, § 11 lõiked ...`

Those mixed clauses belong to the normal structural parser. If the helper also
fires, it creates duplicate broad section repeals and malformed labels such as
`section:§-d 7-10`, which can delete legal siblings not claimed by the source.

Corpus witness:

- `113102015002` changes `126082015026`; oracle `113102015004` is fully
  consistent when the normal mixed-repeal parser owns the clause and the
  cross-act section-only helper stays silent.

## 70. `peale sõna` is an insert-after text rewrite synonym

Some Estonian amendment clauses use `peale sõna` where the usual source formula
uses `pärast sõna`. Example from `121102025007`:

- target: `§ 4 lõike 2 punkt 7`
- anchor: `veoteed`
- inserted tail: `, samuti liikluspiiranguga teelõiku, ...`

This is not a structural insertion of a duplicate item and it must not append
text after the existing item terminator. It is a bounded text rewrite on the
explicit target, equivalent to `pärast sõna`.

The payload witness family is:

- `ee_peale_sona_insert_after_synonym`

## 71. Elative section targets can carry sentence-scope deletion

Clauses such as `§-st 6 jäetakse välja teine lause` and
`§ 12 tekstist jäetakse välja teine lause` target a sentence inside a section,
not the section as a whole. The elative `§-st` or explicit `tekstist` supplies
section text scope, while `teine lause` supplies sentence scope.

This must lower to a sentence-scoped `REPLACE` with empty payload and
`sentence_target_meta`, not a structural section repeal.

Corpus witness:

- `114082018004` changes `102112016003`; oracle `114082018005` keeps § 6 and
  removes only its second sentence.
- `129122020040` changes `108022017004`; oracle `129122020047` keeps § 12 and
  removes only its second sentence.

## 71.1. Plain numbered clauses may target `paragrahvis`

Flat `tavatekst` amendment bodies can contain multiple numbered clauses without
HTML item markup. The clause boundary must recognize section target case forms,
including inessive `paragrahvis`, so a later text-replacement clause is not
smuggled into the previous section replacement payload.

Corpus witness:

- `117072015009` item 1 replaces § 10 of `109072014029`;
- item 2 says `paragrahvis 11 asendatakse sõna „kaks“ sõnaga „kolm“`;
- oracle `117072015011` expects § 11 to say `kolm aastat`.

The owned split is source-local extraction. It preserves numbered-item
provenance (`old_format_amendment_item:2`) and does not broaden target
resolution.

## 72. Case-inflected agency renames preserve nominative before `ise`

When a global `vastavas käändes` agency rename sees a nominative agency name
followed by `ise`, the name remains nominative. The phrase is appositional
(`Maanteeamet ise`), not a genitive modifier.

This exception is still bounded by the exact old/new rewrite surfaces. It must
not prevent genuine genitive rewrites such as `Maanteeameti poolt`.

Corpus witness:

- `103092021001` changes `105082014018`; oracle `103092021009` has
  `Transpordiameti poolt ... Transpordiamet ise`.

## 73. `muudetakse ja pärast sõna ... asendatakse` preserves text-rewrite intent

Some clauses start with `muudetakse` but the actual executable change is a
phrase replacement after an anchor:

- `paragrahvi 7 lõige 2 muudetakse ja pärast sõna „kui” asendatakse lauseosa
  „31. mai” lauseosaga „30. november”.`

This must lower to `TEXT_REPLACE` on the explicit provision target with
`rewrite_mode=replace` and witness family:

- `ee_text_replace_after_anchor_clause`

It must not become a structural subsection replacement. The anchor identifies
where the phrase replacement occurs; it does not make the new phrase an
insert-after payload.

Corpus witness:

- `128052025010` changes `114052024004`; oracle `128052025011` changes only
  the date phrase in § 7(2).

## 74. Global text rewrites use typed rewrite semantics

Global `TEXT_REPLACE` operations with an empty target address still need the
same typed rewrite semantics as exact-target rewrites. They must not fall back
to case-sensitive Python `str.replace`, because lower-case source surfaces can
legitimately match sentence-initial capitalized live text.

The parser also treats `läbivalt` anywhere in the clause as an
all-occurrences witness. In old-format direct-title clauses the word may occur
after the quoted old surface, not in the pre-quote instruction preamble.

Corpus witness:

- `113062025001` changes `116042024004`; oracle `113062025018` rewrites
  sentence-initial `Kliimaminister` in § 8(2) to `Valdkonna eest vastutav
  minister`, from the source clause `asendatakse sõna „kliimaminister”
  läbivalt sõnadega „valdkonna eest vastutav minister”.`

## 75. Case-aware rewrite agreement postpasses require a matched selector

EE case-aware `TEXT_REPLACE` may run morphology/agreement postpasses only after
the source selector actually changed the live text. If the selector is absent,
the operation is a no-op for that surface. Agreement projection must not mutate
replacement-like live text merely because it resembles the new phrase.

This prevents broad same-source renames from hijacking later, narrower rewrite
targets at string granularity.

Corpus witness:

- `106122017001` changes `107072011007`; oracle `111122019011` expects the
  later `Veterinaar- ja Toiduameti kohalik asutus` rewrite to produce
  `Veterinaar- ja Toiduametit`.
- The earlier broader selector `Veterinaar- ja Toiduameti kohaliku asutuse juht`
  must not partially mutate `Veterinaar- ja Toiduameti kohalikku asutust`.

## 76. Section/item targets may recover only to a unique descendant item

Some EE source clauses spell an item target as `section:item` even though the
live ontology stores the item under a subsection. Replay may recover that target
only if the named section contains exactly one descendant item with the explicit
item label.

This recovery must emit adjudication:

- `ee_section_item_replace_unique_descendant_item`

The adjudication records the source target and recovered target. It is not a
general fallback from item to subsection or section, and ambiguity remains a
failed/unresolved target.

Corpus witness:

- `118042013002` changes `130122011047`; oracle `113122013023` expects the
  explicit `§ 7` / `punkt 7^1` replacement to land on the unique descendant
  item `chapter:2/section:7/subsection:1/item:7_1`.

Strictness:

- This is quirks-mode recovery today. A future EE strict profile should reject
  it unless the profile explicitly allows unique-descendant target recovery.

## 77. Old-format commencement defaults are temporal provenance, not hidden filtering

When an old-format EE act has a whole-act commencement default and section/item
exceptions, unstamped operations in an amendment section inherit the whole-act
default only when that default is the active reference slice. Section-specific
and item-specific dates remain owned by their explicit commencement clauses.

Stamped operations must carry one of these provenance tags:

- `ee_old_format_commencement_item_effective`
- `ee_old_format_commencement_section_effective`
- `ee_old_format_commencement_whole_act_default`

Corpus witness:

- `104012021004` changes `104012021044`; oracle `104012021045` expects § 15
  items 1-4 to apply on `2021-02-01` under the whole-act default, while item 5
  is item-stamped.
- The same source also contains unrelated delayed sections/items, so the
  presence of section effects elsewhere cannot suppress default ownership for
  § 15.

Strictness:

- The tags are sufficient evidence for quirks replay. Strict mode should treat
  default ownership as acceptable only when the whole-act default and target
  amendment section are explicit in the source witness.

## 78. Out-of-body appendix/note clauses are meta operations, not body replay

EE source clauses that replace, establish, or repeal appendices/notes through
an out-of-body lane must remain visible as `META` operations with source-family
payload evidence. They must not be dropped merely because there is no body
target, and they must not be replayed as body mutations.

Owned family:

- `ee_out_of_body_appendix_or_note_clause`

Replay must record these as:

- `ee_replay_meta_non_body_skipped`

They must not be reported as `ee_replay_unsupported_action`, because the source
instruction has been classified as non-body evidence rather than an unsupported
operative body mutation.

Corpus witness:

- `113092017001` changes `106012015009`; oracle `113092017002` exposes
  `Jahitunnistuse ... kord` appendix material via the appendix lane. The source
  clause `lisa 5 kehtestatakse uues sõnastuses` is preserved as meta evidence
  rather than a failed body operation.

## 79. Unparsed operation clauses are coverage debt, not non-body meta

If the Estonia parser cannot classify a source instruction into an executable
body operation, it may preserve the clause as a `META` carrier only as an
evidence lane. This does not mean the clause is non-body law.

Owned family:

- `ee_unparsed_operation_clause`

Replay must record these as:

- `ee_replay_unparsed_operation_skipped`

Publication triage must treat current divergences with this adjudication as
`replay_coverage_gap`, with `unparsed source refs` evidence. They must not be
reported as `ee_replay_meta_non_body_skipped`, because that would falsely
classify an unknown source instruction as a proven appendix/preamble/non-body
lane.

Strictness:

- Strict mode should block unparsed operation clauses for body replay. Quirks
  mode may continue only with the skipped-operation adjudication preserved.
