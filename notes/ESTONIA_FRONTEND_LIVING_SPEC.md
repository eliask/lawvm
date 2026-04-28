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
