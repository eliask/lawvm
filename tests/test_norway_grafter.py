from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import json
import tarfile
from typing import cast

import pytest


from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.norway.grafter import (
    _split_no_sentences,
    apply_no_heading_groups,
    apply_no_ops,
    iter_no_document_change_ops,
    lovdata_amendment_filename_to_id,
    lovdata_filename_to_id,
    lovdata_path_to_address,
    normalize_lovdata_refid,
    open_lovdata_amendment_archive,
    parse_no_heading_groups,
    parse_no_amendment_ops,
    parse_no_statute,
)
from lawvm.tools.build import _build_no


def _kind_value(kind: object) -> object:
    if isinstance(kind, IRNodeKind):
        return kind.value
    return kind


def _action_value(action: object) -> object:
    if isinstance(action, StructuralAction):
        return action.value
    return action


_STATUTE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head>
    <title>Testlov om data</title>
  </head>
  <body>
    <main class="documentBody" data-lovdata-URL="NL/lov/2025-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="NL/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="§1" data-lovdata-URL="NL/lov/2025-01-01-1/§1">
          <h3 class="legalArticleHeader">§ 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder testdata.</article>
        </article>
        <article class="legalArticle" data-name="§2" data-lovdata-URL="NL/lov/2025-01-01-1/§2">
          <h3 class="legalArticleHeader">§ 2. Krav</h3>
          <article class="legalP" id="ledd1">
            Kravene er:
            <ol>
              <li data-li-identifier="1." data-name="1.">ett krav</li>
              <li data-li-identifier="2." data-name="2.">to krav</li>
            </ol>
          </article>
          <article class="changesToParent">Endret ved lov ...</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


_AMENDMENT_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change"
               data-change-part="lov/2025-01-01-1/§2/nummer/1"
               data-add-new-part="lov/2025-01-01-1/§2/nummer/3">
        <article class="defaultP">I loven skal nr. 1 endres og ny nr. 3 tilfoyes.</article>
        <li data-li-identifier="1." data-name="1.">oppdatert krav</li>
        <li data-li-identifier="3." data-name="3.">tredje krav</li>
      </article>
      <article class="change"
               data-repeal-part="lov/2025-01-01-1/§1">
        <article class="defaultP">Paragraf 1 oppheves.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def test_lovdata_filename_to_id_skips_nynorsk_and_normalizes_number() -> None:
    assert lovdata_filename_to_id("nl/nl-18840614-003.xml") == "no/lov/1884-06-14-3"
    assert lovdata_filename_to_id("nl/nl-18840614-003-nn.xml") is None


def test_lovdata_amendment_filename_to_id_normalizes_lovtidend_member_path() -> None:
    assert (
        lovdata_amendment_filename_to_id("lti/2025/nl-20250202-005.xml")
        == "no/lovtid/2025-02-02-5"
    )


def test_normalize_lovdata_refid_handles_noisy_document_refs() -> None:
    assert normalize_lovdata_refid("lov/2005-05-20-28/§1/ledd/2") == "no/lov/2005-05-20-28"
    assert normalize_lovdata_refid("no/lov/2005-05-20-28") == "no/lov/2005-05-20-28"
    assert (
        normalize_lovdata_refid("lov/2020-12-18-139 lov/1997-02-28-19")
        == "no/lov/2020-12-18-139"
    )


def test_lovdata_path_to_address_maps_structured_path_components() -> None:
    address = lovdata_path_to_address("lov/2008-06-27-71/§11-10/ledd/1/nummer/5")

    assert address is not None
    assert address.path == (
        ("section", "11-10"),
        ("subsection", "1"),
        ("item", "5"),
    )


def test_parse_no_statute_preserves_chapter_section_and_item_structure() -> None:
    statute = parse_no_statute(_STATUTE_XML, "no/lov/2025-01-01-1")

    assert statute.title == "Testlov om data"
    assert [child.kind for child in statute.body.children] == [IRNodeKind.CHAPTER]

    chapter = statute.body.children[0]
    assert chapter.label == "1"
    assert chapter.children[0].kind == IRNodeKind.HEADING
    assert [child.label for child in chapter.children[1:]] == ["1", "2"]

    section_two = chapter.children[2]
    assert section_two.children[0].kind == IRNodeKind.HEADING
    subsection = section_two.children[1]
    assert subsection.kind == IRNodeKind.SUBSECTION
    assert subsection.text == "Kravene er:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "ett krav"),
        ("2", "to krav"),
    ]


def test_parse_no_statute_normalizes_letter_item_labels_with_trailing_paren() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Lettered testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§1">
        <article class="legalP">
          Punkter:
          <ol>
            <li data-name="a)">første</li>
            <li data-name="b)">andre</li>
          </ol>
        </article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    subsection = section.children[0]
    assert [(item.label, item.text) for item in subsection.children] == [
        ("a", "første"),
        ("b", "andre"),
    ]


def test_parse_no_statute_assigns_unique_labels_to_unlabeled_bullet_items() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Bullet testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§1">
        <article class="numberedLegalP" data-numerator="1">
          (1) Foretak som oppfyller følgende vilkår:
          <ul>
            <li data-name="-">ett</li>
            <li data-name="-">to</li>
            <li data-name="-">tre</li>
          </ul>
        </article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    subsection = section.children[0]
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "ett"),
        ("2", "to"),
        ("3", "tre"),
    ]


def test_parse_no_statute_preserves_nested_list_article_item_text() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Nested list article testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§2">
        <h2 class="legalArticleHeader">§ 2. Begreper</h2>
        <article class="legalP">
          I denne lov forstås med:
          <ol class="defaultList" type="1">
            <li data-name="1">
              <article class="listArticle">
                <article class="legalP">Betalingsmidler:<br />Kontanter.</article>
              </article>
            </li>
            <li data-name="2">
              <article class="listArticle">
                <article class="legalP">Valutaveksling:<br />Kjøp og salg.</article>
              </article>
            </li>
          </ol>
        </article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    subsection = section.children[1]
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "Betalingsmidler: Kontanter."),
        ("2", "Valutaveksling: Kjøp og salg."),
    ]


def test_parse_no_statute_reads_numbered_legal_p_as_subsections() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Numbered testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§7-3">
        <h4 class="legalArticleHeader">§ 7-3. Regler om skatt ved utdeling</h4>
        <article class="numberedLegalP" data-numerator="1">(1) Første ledd.</article>
        <article class="numberedLegalP" data-numerator="5">(5) Femte ledd.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    assert [_kind_value(child.kind) for child in section.children] == [
        "heading",
        "subsection",
        "subsection",
    ]
    assert [(child.label, child.text) for child in section.children[1:]] == [
        ("1", "Første ledd."),
        ("5", "Femte ledd."),
    ]


def test_parse_no_statute_merges_unlabeled_punktum_continuation_into_prior_itemized_subsection() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nn">
  <head><title>Språk testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§3">
        <h4 class="legalArticleHeader">§ 3. Verkeområde</h4>
        <article class="legalP">
          Når det ikkje er fastsett noko anna, gjeld lova for
          <ul>
            <li data-li-identifier="a">staten</li>
            <li data-li-identifier="b">kommunane</li>
          </ul>
        </article>
        <article class="legalP">Første punktum bokstav b gjeld ikkje i slike saker.</article>
        <article class="legalP">Lova gjeld ikkje for intern sakshandsaming.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2"]
    assert subsections[0].text == (
        "Når det ikkje er fastsett noko anna, gjeld lova for "
        "Første punktum bokstav b gjeld ikkje i slike saker."
    )
    assert [item.label for item in subsections[0].children] == ["a", "b"]
    assert subsections[1].text == "Lova gjeld ikkje for intern sakshandsaming."


def test_parse_no_statute_merges_leddfortsettelse_into_prior_numbered_subsection() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Vareførsel testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§6-3" id="kapittel-6-paragraf-3">
        <h3 class="legalArticleHeader"><span class="legalArticleValue">§ 6-3</span>. <span class="legalArticleTitle">Varens transaksjonsverdi</span></h3>
        <article class="numberedLegalP" data-numerator="1" id="kapittel-6-paragraf-3-nummer-1">
          (1) Tollverdien av en vare er transaksjonsverdien.
          <ul>
            <li data-li-identifier="a"><article class="listArticle"><article class="legalP">første vilkår</article></article></li>
          </ul>
        </article>
        <p class="leddfortsettelse">Kjøper og selger anses å være avhengig av hverandre.</p>
        <article class="numberedLegalP" data-numerator="2">(2) Andre ledd.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2"]
    assert subsections[0].text == "Tollverdien av en vare er transaksjonsverdien."
    assert [(child.kind, child.label) for child in subsections[0].children] == [
        (IRNodeKind.ITEM, "a"),
        (IRNodeKind.SENTENCE, "1"),
    ]
    assert subsections[0].children[-1].text == "Kjøper og selger anses å være avhengig av hverandre."


def test_parse_no_statute_keeps_internal_leddfortsettelse_as_sentence_child() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Vareførsel testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§6-3" id="kapittel-6-paragraf-3">
        <h3 class="legalArticleHeader"><span class="legalArticleValue">§ 6-3</span>. <span class="legalArticleTitle">Varens transaksjonsverdi</span></h3>
        <article class="numberedLegalP" data-numerator="1" id="kapittel-6-paragraf-3-nummer-1">
          (1) Tollverdien av en vare er transaksjonsverdien.
          <ul>
            <li data-li-identifier="a"><article class="listArticle"><article class="legalP">første vilkår</article></article></li>
          </ul>
          <p class="leddfortsettelse">Kjøper og selger anses å være avhengig av hverandre.</p>
        </article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1"]
    assert subsections[0].text == "Tollverdien av en vare er transaksjonsverdien."
    assert [(child.kind, child.label) for child in subsections[0].children] == [
        (IRNodeKind.ITEM, "a"),
        (IRNodeKind.SENTENCE, "1"),
    ]
    assert subsections[0].children[-1].text == "Kjøper og selger anses å være avhengig av hverandre."


def test_parse_no_statute_keeps_nested_item_prefix_out_of_parent_item_text() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Suppleringsskatt testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§7-1">
        <article class="numberedLegalP" data-numerator="1">
          (1) Er øverste morselskap en enhet med deltakerfastsetting, skal justert overskudd reduseres dersom:
          <ul class="defaultList">
            <li data-li-identifier="b." data-name="b.">
              <article class="listArticle">
                <article class="legalP">eieren er en fysisk person som
                  <ul class="defaultList">
                    <li data-li-identifier="1." data-name="1.">
                      <article class="listArticle"><article class="legalP">er skattemessig bosatt i samme jurisdiksjon som det øverste morselskapet, og</article></article>
                    </li>
                    <li data-li-identifier="2." data-name="2.">
                      <article class="listArticle"><article class="legalP">har en direkte eierinteresse som gir rett til maksimalt 5 prosent.</article></article>
                    </li>
                  </ul>
                </article>
              </article>
            </li>
          </ul>
        </article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    subsection = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION][0]
    item_b = [child for child in subsection.children if child.kind is IRNodeKind.ITEM][0]
    assert item_b.text == "eieren er en fysisk person som"
    assert [(child.kind, child.label, child.text) for child in item_b.children] == [
        (IRNodeKind.ITEM, "1", "er skattemessig bosatt i samme jurisdiksjon som det øverste morselskapet, og"),
        (IRNodeKind.ITEM, "2", "har en direkte eierinteresse som gir rett til maksimalt 5 prosent."),
    ]


def test_parse_no_statute_dedupes_mixed_explicit_and_implicit_subsection_labels() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Mixed numbering testlov</title></head>
  <body>
    <main class="documentBody">
      <article class="legalArticle" data-name="§14-3">
        <article class="legalP">Innledende ledd.</article>
        <article class="defaultP">1. Første endring.</article>
        <article class="defaultP">2. Andre endring.</article>
        <article class="numberedLegalP" data-numerator="2">(2) Nummerert annet ledd.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    section = statute.body.children[0]
    assert [child.label for child in section.children] == ["1", "2", "3", "4"]


def test_iter_no_document_change_ops_infers_subsection_target_from_lead_when_attr_only_marks_section() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2010-06-04-21">
      <article class="change" data-add-new-part="lov/2010-06-04-21/§10-13">
        <article class="defaultP">I lov 4. juni 2010 nr. 21 om fornybar energiproduksjon til havs skal § 10-13 nytt andre ledd lyde:</article>
        <article class="legalP">Nytt andre ledd.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    grouped = iter_no_document_change_ops(amendment_xml, "no/lovtid/2025-06-20-109")

    assert len(grouped) == 1
    base_id, ops = grouped[0]
    assert base_id == "no/lov/2010-06-04-21"
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "10-13"), ("subsection", "2"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SUBSECTION
    assert ops[0].payload.text == "Nytt andre ledd."


def test_parse_no_amendment_ops_unstructured_supports_new_section_insert() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments"><ul><li>lov/2013-01-11-3</li></ul></dd>
    <main>
      <section data-name="kapI">
        <article class="defaultP">Ny § 6 a skal lyde:</article>
        <article class="futureLegalArticle" data-name="§6a">
          <span class="futureLegalArticleHeader">§ 6 a. Tittel</span>
          <article class="legalP">Første ledd.</article>
          <article class="legalP">Andre ledd.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(amendment_xml, "no/lovtid/2018-12-20-109")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "6a"),)
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SECTION
    assert [child.kind for child in ops[0].payload.children] == [
        IRNodeKind.HEADING,
        IRNodeKind.SUBSECTION,
        IRNodeKind.SUBSECTION,
    ]


def test_parse_no_amendment_ops_unstructured_supports_item_target_lead() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments"><ul><li>lov/2022-03-11-9</li></ul></dd>
    <main>
      <section data-name="kapI">
        <article class="defaultP">
          § 12-2 fyrste ledd bokstav a skal lyde:
          <ul class="defaultList">
            <li data-name="a.">
              <article class="listArticle">
                <article class="legalP">overtrer plikter etter kapittel 2</article>
              </article>
            </li>
          </ul>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(amendment_xml, "no/lovtid/2023-06-16-52")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "12-2"), ("subsection", "1"), ("item", "a"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.ITEM
    assert ops[0].payload.text == "overtrer plikter etter kapittel 2"


def test_parse_no_amendment_ops_unstructured_supports_global_text_replace_clause() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul>
        <li>lov/2003-07-04-84</li>
        <li>lov/2003-12-12-108</li>
      </ul>
    </dd>
    <main>
      <section data-name="kapII">
        <article class="legalP">
          I følgende lover skal «friskolelova» erstattes med «privatskolelova» og
          «frittstående skoler» med «skoler godkjent etter privatskolelova»:
        </article>
        <ul>
          <li><article class="listArticle"><article class="legalP">lov 4. juli 2003 nr. 84 om frittståande skolar.</article></article></li>
          <li><article class="listArticle"><article class="legalP">lov 12. desember 2003 nr. 108 om kompensasjon av merverdiavgift for kommuner.</article></article></li>
        </ul>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(amendment_xml, "no/lovtid/2022-06-10-39")

    text_ops = [op for op in ops if op.action is StructuralAction.TEXT_REPLACE]
    assert len(text_ops) == 4
    assert {op.source.title for op in text_ops if op.source is not None} == {
        "no/lov/2003-07-04-84",
        "no/lov/2003-12-12-108",
    }
    patches = []
    for op in text_ops:
        assert op.text_patch is not None
        patches.append(op.text_patch)
    assert {patch.selector.match_text for patch in patches} == {
        "friskolelova",
        "frittstående skoler",
    }


def test_parse_no_amendment_ops_unstructured_flattens_nested_legal_article_children() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul>
        <li>lov/1915-08-13-5</li>
        <li>lov/2003-12-12-108</li>
      </ul>
    </dd>
    <main>
      <section class="section" data-name="kap16">
        <article class="legalArticle" data-name="§16-3">
          <article class="defaultP">1. I lov 13. august 1915 nr. 5 om domstolene gjøres følgende endringer:</article>
          <article class="defaultP">§ 2 skal lyde:</article>
          <article class="futureLegalArticle" data-name="§2">
            <article class="futureLegalArticleHeader">§ 2.</article>
            <article class="legalP">Annan lov.</article>
          </article>
          <article class="defaultP">26. I lov 12. desember 2003 nr. 108 om kompensasjon av merverdiavgift for kommuner, fylkeskommuner mv. gjøres følgende endringer:</article>
          <article class="defaultP">Ny § 6 a skal lyde:</article>
          <article class="futureLegalArticle" data-name="§6a">
            <article class="futureLegalArticleHeader">§ 6 a.</article>
            <article class="legalP">Ny operativ regel.</article>
          </article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(amendment_xml, "no/lovtid/2016-05-27-14")

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "2"),),
        (("section", "6a"),),
    ]
    assert {op.source.title for op in ops if op.source is not None} == {
        "no/lov/1915-08-13-5",
        "no/lov/2003-12-12-108",
    }


def test_parse_no_amendment_ops_unstructured_supports_heading_repeal_range_and_repeal_renumber() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2003-12-12-108</li></ul>
    </dd>
    <main>
      <section data-name="kap16">
        <article class="defaultP">I lov 12. desember 2003 nr. 108 om kompensasjon av merverdiavgift for kommuner, fylkeskommuner mv. gjøres følgende endringer:</article>
        <article class="defaultP">§ 6 overskriften skal lyde:</article>
        <article class="legalP">Beløpsgrenser og tidfesting</article>
        <article class="defaultP">§ 6 første ledd oppheves. Nåværende annet ledd blir første ledd.</article>
        <article class="defaultP">§ 7 oppheves.</article>
        <article class="defaultP">§§ 13 til 15 oppheves.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(amendment_xml, "no/lovtid/2016-05-27-14")

    assert [(op.action, op.target.path, op.destination.path if op.destination else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "6"),), None),
        (StructuralAction.REPEAL, (("section", "6"), ("subsection", "1")), None),
        (StructuralAction.RENUMBER, (("section", "6"), ("subsection", "2")), (("section", "6"), ("subsection", "1"))),
        (StructuralAction.REPEAL, (("section", "7"),), None),
        (StructuralAction.REPEAL, (("section", "13"),), None),
        (StructuralAction.REPEAL, (("section", "14"),), None),
        (StructuralAction.REPEAL, (("section", "15"),), None),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SECTION
    assert ops[0].payload.children[0].kind is IRNodeKind.HEADING
    assert ops[0].payload.children[0].text == "Beløpsgrenser og tidfesting"


def test_apply_no_ops_supports_global_text_replace() -> None:
    statute = parse_no_statute(_STATUTE_XML, "no/lov/2025-01-01-1")
    chapter = statute.body.children[0]
    section = chapter.children[1]
    sentence = section.children[1]
    section_children = list(section.children)
    section_children[1] = replace(sentence, text="Skole etter friskolelova.")
    chapter_children = list(chapter.children)
    chapter_children[1] = replace(section, children=section_children)
    body_children = list(statute.body.children)
    body_children[0] = replace(chapter, children=chapter_children)
    statute = replace(statute, body=replace(statute.body, children=body_children))

    ops = [
        LegalOperation(
            op_id="no/lovtid/2022-06-10-39:1",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=()),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="friskolelova", occurrence=0),
                replacement="privatskolelova",
            ),
            source=OperationSource(statute_id="no/lovtid/2022-06-10-39", raw_text="generic replace"),
        )
    ]

    updated = apply_no_ops(statute, ops)

    section = updated.body.children[0].children[1]
    assert section.children[1].text == "Skole etter privatskolelova."


def test_apply_no_ops_supports_typed_text_patch() -> None:
    statute = parse_no_statute(_STATUTE_XML, "no/lov/2025-01-01-1")
    chapter = statute.body.children[0]
    section = chapter.children[1]
    sentence = section.children[1]
    section_children = list(section.children)
    section_children[1] = replace(sentence, text="Skole etter friskolelova.")
    chapter_children = list(chapter.children)
    chapter_children[1] = replace(section, children=section_children)
    body_children = list(statute.body.children)
    body_children[0] = replace(chapter, children=chapter_children)
    statute = replace(statute, body=replace(statute.body, children=body_children))

    ops = [
        LegalOperation(
            op_id="no/lovtid/2022-06-10-39:1",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=()),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="friskolelova", occurrence=0),
                replacement="privatskolelova",
            ),
            source=OperationSource(statute_id="no/lovtid/2022-06-10-39", raw_text="typed replace"),
        )
    ]

    updated = apply_no_ops(statute, ops)

    section = updated.body.children[0].children[1]
    assert section.children[1].text == "Skole etter privatskolelova."


def test_parse_no_amendment_ops_unstructured_supports_embedded_multi_act_lead() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <dd class="changesToDocuments">
      <ul>
        <li>lov/2004-12-17-99</li>
        <li>lov/2009-06-19-58</li>
      </ul>
    </dd>
    <main>
      <section data-name="kapI">
        <article class="legalP">
          206. I lov 17. desember 2004 nr. 99 om kvoteplikt og handel med kvoter for utslipp av klimagasser (klimakvoteloven) skal § 21 nytt annet punktum lyde:
        </article>
        <article class="legalP">Medvirkning straffes ikke.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    grouped = iter_no_document_change_ops(amendment_xml, "no/lovtid/2015-06-19-65")

    assert len(grouped) == 1
    base_id, ops = grouped[0]
    assert base_id == "no/lov/2004-12-17-99"
    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (
            StructuralAction.INSERT,
            (("section", "21"), ("sentence", "2")),
            "Medvirkning straffes ikke.",
        ),
    ]


def test_iter_no_document_change_ops_unstructured_supports_section_scoped_base_lead() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <dd class="changesToDocuments">
      <ul>
        <li>lov/2005-12-21-124</li>
        <li>lov/2005-06-17-62</li>
      </ul>
    </dd>
    <main>
      <section data-name="kapI">
        <h2>I</h2>
        <article class="defaultP">I lov 17. juni 2005 nr. 62 om arbeidsmiljø, arbeidstid og stillingsvern mv. gjøres følgende endring:</article>
        <article class="defaultP">§ 18-1 skal lyde:</article>
        <article class="futureLegalArticle" data-name="§18-1">
          <span class="futureLegalArticleHeader">§ 18-1. Tilsyn</span>
          <article class="legalP">Arbeidstilsynet fører tilsyn.</article>
        </article>
      </section>
      <section data-name="kapII">
        <h2>II</h2>
        <article class="defaultP">I lov 21. desember 2005 nr. 124 om obligatorisk tjenestepensjon gjøres følgende endringer:</article>
        <article class="defaultP">§ 8 nytt annet ledd skal lyde:</article>
        <article class="numberedLegalP" data-numerator="2">(2) Departementet kan gi forskrift om nivået på og utmåling av tvangsmulkten.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    grouped = dict(iter_no_document_change_ops(amendment_xml, "no/lovtid/2020-12-21-167"))

    assert "no/lov/2005-12-21-124" in grouped
    ops = grouped["no/lov/2005-12-21-124"]
    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (
            StructuralAction.INSERT,
            (("section", "8"), ("subsection", "2")),
            "Departementet kan gi forskrift om nivået på og utmåling av tvangsmulkten.",
        ),
    ]


def test_iter_no_document_change_ops_unstructured_supports_named_law_section_intro() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <dd class="changesToDocuments">
      <ul>
        <li>lov/1993-06-11-100</li>
        <li>lov/2005-06-03-34</li>
      </ul>
    </dd>
    <main>
      <section data-name="kapI">
        <h2>I</h2>
        <article class="legalP">I jernbaneundersøkelsesloven av 3. juni 2005 nr. 34 gjøres følgende endring:</article>
        <article class="defaultP">Ny § 8 a skal lyde:</article>
        <article class="futureLegalArticle" data-name="§8a">
          <span class="futureLegalArticleHeader">§ 8 a. Taushetsplikt</span>
          <article class="legalP">Enhver har taushetsplikt.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    grouped = dict(iter_no_document_change_ops(amendment_xml, "no/lovtid/2016-12-16-102"))

    assert "no/lov/2005-06-03-34" in grouped
    ops = grouped["no/lov/2005-06-03-34"]
    assert [(op.action, op.target.path, op.payload.kind if op.payload else None) for op in ops] == [
        (StructuralAction.INSERT, (("section", "8a"),), IRNodeKind.SECTION),
    ]


def test_iter_no_document_change_ops_unstructured_supports_direct_section_lead_with_base() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <dd class="changesToDocuments">
      <ul>
        <li>lov/1993-06-11-100</li>
        <li>lov/2005-06-03-34</li>
      </ul>
    </dd>
    <main>
      <section data-name="kapII">
        <h2>II</h2>
        <article class="defaultP">I lov 3. juni 2005 nr. 34 om varsling, rapportering og undersøkelse av jernbaneulykker og jernbanehendelser m.m. skal § 13 lyde:</article>
        <article class="futureLegalArticle" data-name="§13">
          <span class="futureLegalArticleHeader">§ 13. Tiltak</span>
          <article class="legalP">Undersøkelsesmyndigheten kan kreve hjelp av politiet.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    grouped = dict(iter_no_document_change_ops(amendment_xml, "no/lovtid/2021-06-11-87"))

    assert "no/lov/2005-06-03-34" in grouped
    ops = grouped["no/lov/2005-06-03-34"]
    assert [(op.action, op.target.path, op.payload.kind if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "13"),), IRNodeKind.SECTION),
    ]


def test_parse_no_amendment_ops_unstructured_supports_plural_section_repeal() -> None:
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <dd class="changesToDocuments"><ul><li>lov/2004-12-17-99</li></ul></dd>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§§ 8a og 8b oppheves.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(amendment_xml, "no/lovtid/2012-05-25-29")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "8a"),)),
        (StructuralAction.REPEAL, (("section", "8b"),)),
    ]


def test_parse_no_statute_recurses_part_chapter_section_structure() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Nested testlov</title></head>
  <body>
    <main class="documentBody">
      <section class="section" data-name="del1" data-lovdata-URL="NL/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Første del</h2>
        <section class="section" data-name="kap2" data-lovdata-URL="NL/lov/2025-01-01-1/KAPITTEL_1-2">
          <h3>Kapittel 2</h3>
          <article class="legalArticle" data-name="§7">
            <h4 class="legalArticleHeader">§ 7. Tittel</h4>
            <article class="legalP">Innhold.</article>
          </article>
        </section>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    statute = parse_no_statute(xml, "no/lov/2025-01-01-1")

    assert [child.kind for child in statute.body.children] == [IRNodeKind.PART]
    part = statute.body.children[0]
    assert part.label == "1"
    chapter = next(child for child in part.children if child.kind is IRNodeKind.CHAPTER)
    assert chapter.label == "2"
    section = next(child for child in chapter.children if child.kind is IRNodeKind.SECTION)
    assert section.label == "7"


def test_parse_no_amendment_ops_uses_lovdata_change_attributes() -> None:
    ops = parse_no_amendment_ops(_AMENDMENT_XML, "no/lovtid/2025-02-02-5")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "2"), ("item", "1"))),
        (StructuralAction.INSERT, (("section", "2"), ("item", "3"))),
        (StructuralAction.REPEAL, (("section", "1"),)),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.label == "1"
    assert ops[1].payload is not None
    assert ops[1].payload.label == "3"
    assert ops[2].payload is None


def test_parse_no_amendment_ops_supports_future_legal_article_payloads() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-change-part="lov/2025-01-01-1/§2">
        <article class="defaultP">§ 2 skal lyde:</article>
        <article class="futureLegalArticle" data-name="§2">
          <span class="futureLegalArticleHeader">
            <span class="legalArticleValue">§ 2</span>.
            <span class="legalArticleTitle">Nytt krav</span>
          </span>
          <article class="legalP">Oppdatert paragraftekst.</article>
        </article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2025-03-03-7")

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "2"),)
    assert ops[0].payload is not None
    assert ops[0].payload.label == "2"
    assert ops[0].payload.children[0].kind is IRNodeKind.HEADING


def test_parse_no_amendment_ops_indexes_nested_item_payload_for_structured_target() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2024-01-12-1">
      <article class="change" data-change-part="lov/2024-01-12-1/§7-1/ledd/1/bokstav/b/nummer/2">
        <article class="defaultP">§ 7-1 fyrste ledd bokstav b nr. 2 skal lyde:</article>
        <li class="consolidationElement">
          <article class="listArticle">
            <ul class="defaultList">
              <li data-li-identifier="2." data-name="2.">
                <article class="listArticle">
                  <article class="legalP">har en direkte eierinteresse som gir rett til maksimalt 5 prosent.</article>
                </article>
              </li>
            </ul>
          </article>
        </li>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2024-06-25-66")

    assert len(ops) == 1
    assert ops[0].target.path == (
        ("section", "7-1"),
        ("subsection", "1"),
        ("item", "b"),
        ("item", "2"),
    )
    assert ops[0].payload is not None
    assert ops[0].payload.text == "har en direkte eierinteresse som gir rett til maksimalt 5 prosent."


def test_parse_no_amendment_ops_parses_section_heading_only_replace_without_dropping_section_shape() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2024-01-12-1">
      <article class="change" data-change-part="lov/2024-01-12-1/§2-4">
        <article class="defaultP">Overskriften til § 2-4 skal lyde:</article>
        <article class="defaultP"><i>Fordeling av suppleringsskatt etter skatteinkluderingsregelen</i></article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2024-12-20-92")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "2-4"),)
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SECTION
    assert [(child.kind, child.text) for child in ops[0].payload.children] == [
        (IRNodeKind.HEADING, "Fordeling av suppleringsskatt etter skatteinkluderingsregelen"),
    ]


def test_iter_no_document_change_ops_finds_nested_change_blocks() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <section class="wrapper">
        <article class="change" data-change-part="lov/2025-01-01-1/§2/ledd/1">
          <article class="legalP">Oppdatert første ledd.</article>
        </article>
      </section>
    </article>
  </body>
</html>
""".encode("utf-8")

    grouped = iter_no_document_change_ops(xml, "no/lovtid/2025-03-03-7")

    assert len(grouped) == 1
    base_id, ops = grouped[0]
    assert base_id == "no/lov/2025-01-01-1"
    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "2"), ("subsection", "1"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SUBSECTION
    assert ops[0].payload.text == "Oppdatert første ledd."


def test_parse_and_apply_no_heading_groups_regroups_section_ranges_under_subchapter() -> None:
    statute = IRStatute(
        statute_id="no/lov/2024-01-12-1",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="I",
                    children=(IRNode(kind=IRNodeKind.HEADING, text="Del I"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.HEADING, text="Kapittel 2"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-1", text="a"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-2", text="b"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-3", text="c"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-4", text="d"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-5", text="e"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-10", text="f"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-11", text="g"),
                                IRNode(kind=IRNodeKind.SECTION, label="2-20", text="h"),),
                        ),),
                ),),
        ),
    )
    amendment_xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2024-01-12-1">
      <article class="defaultP">Ny deloverskrift til §§ 2-1 til 2-5 skal lyde:</article>
      <span class="futuretitle">Skatteinkluderingsregelen</span>
      <article class="defaultP">Ny deloverskrift til nye §§ 2-10 til 2-14 skal lyde:</article>
      <span class="futuretitle">Skattefordelingsregelen</span>
      <article class="defaultP">Ny deloverskrift til ny § 2-20 skal lyde:</article>
      <span class="futuretitle">Nasjonal suppleringsskatt</span>
    </article>
  </body>
</html>
""".encode("utf-8")

    groups = parse_no_heading_groups(amendment_xml, "no/lov/2024-01-12-1")
    assert [(group.start_label, group.end_label, group.title) for group in groups] == [
        ("2-1", "2-5", "Skatteinkluderingsregelen"),
        ("2-10", "2-14", "Skattefordelingsregelen"),
        ("2-20", "2-20", "Nasjonal suppleringsskatt"),
    ]

    updated = apply_no_heading_groups(statute, groups)
    part = updated.body.children[0]
    chapter_2 = next(child for child in part.children if child.kind is IRNodeKind.CHAPTER and child.label == "2")
    assert [(child.kind, child.label) for child in chapter_2.children] == [
        (IRNodeKind.HEADING, None),
        (IRNodeKind.CHAPTER, "1-2-1"),
        (IRNodeKind.CHAPTER, "1-2-2"),
        (IRNodeKind.CHAPTER, "1-2-3"),
    ]
    group_121 = chapter_2.children[1]
    assert [child.label for child in group_121.children if child.kind is IRNodeKind.SECTION] == ["2-1", "2-2", "2-3", "2-4", "2-5"]
    group_122 = chapter_2.children[2]
    assert [child.label for child in group_122.children if child.kind is IRNodeKind.SECTION] == ["2-10", "2-11"]
    group_123 = chapter_2.children[3]
    assert [child.label for child in group_123.children if child.kind is IRNodeKind.SECTION] == ["2-20"]


def test_parse_no_amendment_ops_splits_multi_target_legalp_block_into_subsections() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2004-12-17-99">
      <article class="change"
               data-change-part="lov/2004-12-17-99/§3/ledd/1"
               data-add-new-part="lov/2004-12-17-99/§3/ledd/2">
        <article class="defaultP">§ 3 første og andre ledd skal lyde:</article>
        <article class="legalP">Første ledd tekst.</article>
        <article class="legalP">Andre ledd tekst.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2025-06-20-91")

    assert [(op.action, op.target.path, op.payload.label if op.payload else None, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "3"), ("subsection", "1")), "1", "Første ledd tekst."),
        (StructuralAction.INSERT, (("section", "3"), ("subsection", "2")), "2", "Andre ledd tekst."),
    ]


def test_parse_no_amendment_ops_splits_single_legalp_multi_sentence_payload_across_targets() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2024-01-12-1">
      <article class="change"
               data-change-part="lov/2024-01-12-1/§3-2/ledd/8/setning/2 lov/2024-01-12-1/§3-2/ledd/8/setning/3">
        <article class="defaultP">§ 3-2 åttende ledd andre og tredje punktum skal lyde:</article>
        <article class="legalP">Valget er et femårsvalg. For det året valget tas eller oppheves, skal det gjøres korreksjoner.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2025-12-22-123")

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (
            StructuralAction.REPLACE,
            (("section", "3-2"), ("subsection", "8"), ("sentence", "2")),
            "Valget er et femårsvalg.",
        ),
        (
            StructuralAction.REPLACE,
            (("section", "3-2"), ("subsection", "8"), ("sentence", "3")),
            "For det året valget tas eller oppheves, skal det gjøres korreksjoner.",
        ),
    ]


def test_parse_no_amendment_ops_inferrs_sentence_targets_from_structured_subsection_lead() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change"
               data-change-part="lov/2022-05-12-28/§45/ledd/2"
               data-add-new-part="lov/2022-05-12-28/§45/ledd/3 lov/2022-05-12-28/§45/ledd/4">
        <article class="defaultP">§ 45 andre ledd nytt tredje og fjerde punktum skal lyde:</article>
        <article class="legalP">Advokattilsynet kan kreve refusjon. Kravet er tvangsgrunnlag for utlegg.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2024-06-21-46")

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (
            StructuralAction.INSERT,
            (("section", "45"), ("subsection", "2"), ("sentence", "3")),
            "Advokattilsynet kan kreve refusjon.",
        ),
        (
            StructuralAction.INSERT,
            (("section", "45"), ("subsection", "2"), ("sentence", "4")),
            "Kravet er tvangsgrunnlag for utlegg.",
        ),
    ]


def test_split_no_sentences_does_not_split_after_numeric_day_marker() -> None:
    assert _split_no_sentences(
        "Dersom den kvotepliktige ikke innen 1. juni året etter at oppgjøret skulle ha funnet sted."
    ) == [
        "Dersom den kvotepliktige ikke innen 1. juni året etter at oppgjøret skulle ha funnet sted."
    ]


def test_split_no_sentences_still_splits_after_section_citation() -> None:
    assert _split_no_sentences(
        "Klimakvotemyndigheten skal kontrollere rapportering etter § 14. Kongen kan gi forskrift."
    ) == [
        "Klimakvotemyndigheten skal kontrollere rapportering etter § 14.",
        "Kongen kan gi forskrift.",
    ]


def test_parse_no_amendment_ops_recovers_malformed_cross_act_target_from_lead_text() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2004-12-17-99">
      <article class="change"
               data-change-part="lov/2004-12-17-99/§11/ledd/2 lov/1981-03-13-6/§11/ledd/3">
        <article class="defaultP">§ 11 andre og tredje ledd skal lyde:</article>
        <article class="legalP">Andre ledd tekst.</article>
        <article class="legalP">Tredje ledd tekst.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2025-06-20-91")

    assert len(ops) == 2
    assert ops[0].target.path == (("section", "11"), ("subsection", "2"))
    assert ops[0].payload is not None
    assert ops[0].payload.label == "2"
    assert ops[0].payload.text == "Andre ledd tekst."
    assert ops[1].target.path == (("section", "11"), ("subsection", "3"))
    assert ops[1].payload is not None
    assert ops[1].payload.label == "3"
    assert ops[1].payload.text == "Tredje ledd tekst."


def test_parse_no_amendment_ops_falls_back_to_unstructured_future_section_blocks() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <header>
      <dd class="changesToDocuments">
        <ul><li>lov/2004-12-17-99</li></ul>
      </dd>
    </header>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 9 skal lyde:</article>
        <article class="futureLegalArticle" data-name="§9">
          <span class="futureLegalArticleHeader">
            <span class="legalArticleValue">§ 9</span>.
            <span class="legalArticleTitle">(salg av kvoter)</span>
          </span>
          <article class="legalP">Kongen kan gi nærmere bestemmelser om organiseringen og gjennomføringen av salg av kvoter.</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2012-05-25-29")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "9"),)
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SECTION
    assert ops[0].payload.label == "9"
    assert [(child.kind, child.label, child.text) for child in ops[0].payload.children] == [
        (IRNodeKind.HEADING, None, "(salg av kvoter)"),
        (IRNodeKind.SUBSECTION, "1", "Kongen kan gi nærmere bestemmelser om organiseringen og gjennomføringen av salg av kvoter."),
    ]


def test_parse_no_amendment_ops_falls_back_to_unstructured_subsection_and_repeal_blocks() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <header>
      <dd class="changesToDocuments">
        <ul><li>lov/2004-12-17-99</li></ul>
      </dd>
    </header>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 11 andre og tredje ledd skal lyde:</article>
        <article class="legalP">Andre ledd tekst.</article>
        <article class="legalP">Tredje ledd tekst.</article>
        <article class="defaultP">§ 13 tredje ledd oppheves.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2012-05-25-29")

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "11"), ("subsection", "2")), "Andre ledd tekst."),
        (StructuralAction.REPLACE, (("section", "11"), ("subsection", "3")), "Tredje ledd tekst."),
        (StructuralAction.REPEAL, (("section", "13"), ("subsection", "3")), None),
    ]


def test_parse_no_amendment_ops_falls_back_to_unstructured_sentence_target_blocks() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="no">
  <body>
    <header>
      <dd class="changesToDocuments">
        <ul><li>lov/2017-06-16-60</li></ul>
      </dd>
    </header>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 4 annet ledd første punktum skal lyde:</article>
        <article class="legalP">Ved vurdering av om klimamålene for 2030 er nådd, skal det tas hensyn til effekten av norsk deltakelse i EUs klimakvotesystem for virksomheter.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2021-06-18-129")

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (
            StructuralAction.REPLACE,
            (("section", "4"), ("subsection", "2"), ("sentence", "1")),
            "Ved vurdering av om klimamålene for 2030 er nådd, skal det tas hensyn til effekten av norsk deltakelse i EUs klimakvotesystem for virksomheter.",
        ),
    ]


def test_iter_no_document_change_ops_groups_ops_by_base_act() -> None:
    grouped = iter_no_document_change_ops(_AMENDMENT_XML, "no/lovtid/2025-02-02-5")

    assert len(grouped) == 1
    base_id, ops = grouped[0]
    assert base_id == "no/lov/2025-01-01-1"
    assert len(ops) == 3
    assert ops[0].provenance_tags == ("base_act:no/lov/2025-01-01-1",)
    assert all(op.provenance_tags == ("base_act:no/lov/2025-01-01-1",) for op in ops)


def test_iter_no_document_change_ops_compiles_move_part_to_renumber_ops() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change"
               data-move-part="lov/2025-01-01-1/§4;;lov/2025-01-01-1/§5 lov/2025-01-01-1/§5;;lov/2025-01-01-1/§6 lov/2025-01-01-1/§6;;lov/2025-01-01-1/§7"
               data-add-new-part="lov/2025-01-01-1/§4">
        <article class="defaultP">Nåværende §§ 4 til 6 blir §§ 5 til 7. Ny § 4 tilføyes.</article>
        <article class="futureLegalArticle" data-name="§4">
          <span class="futureLegalArticleHeader">
            <span class="legalArticleValue">§ 4</span>.
            <span class="legalArticleTitle">Ny paragraf</span>
          </span>
          <article class="legalP">Ny tekst.</article>
        </article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2025-06-20-90")

    assert [(op.action, op.target.path, op.destination.path if op.destination else ()) for op in ops] == [
        (StructuralAction.RENUMBER, (("section", "6"),), (("section", "7"),)),
        (StructuralAction.RENUMBER, (("section", "5"),), (("section", "6"),)),
        (StructuralAction.RENUMBER, (("section", "4"),), (("section", "5"),)),
        (StructuralAction.INSERT, (("section", "4"),), ()),
    ]


def test_parse_no_amendment_ops_treats_embedded_semicolon_pairs_in_add_new_part_as_renumber() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2024-01-12-1">
      <article class="change"
               data-add-new-part="lov/2024-01-12-1/§3-2/ledd/4/setning/2;;lov/2024-01-12-1/§3-2/ledd/4/setning/3 lov/2024-01-12-1/§3-2/ledd/4/setning/3;;lov/2024-01-12-1/§3-2/ledd/4/setning/4">
        <article class="defaultP">§ 3-2 fjerde ledd nåværende annet og tredje punktum blir tredje og fjerde punktum.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2024-12-20-92")

    assert [(op.action, op.target.path, op.destination.path if op.destination else ()) for op in ops] == [
        (
            StructuralAction.RENUMBER,
            (("section", "3-2"), ("subsection", "4"), ("sentence", "2")),
            (("section", "3-2"), ("subsection", "4"), ("sentence", "3")),
        ),
        (
            StructuralAction.RENUMBER,
            (("section", "3-2"), ("subsection", "4"), ("sentence", "3")),
            (("section", "3-2"), ("subsection", "4"), ("sentence", "4")),
        ),
    ]


def test_parse_no_amendment_ops_splits_lead_in_tail_across_sentence_targets() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2024-01-12-1">
      <article class="change"
               data-add-new-part="lov/2024-01-12-1/§4-2/ledd/2/setning/3 lov/2024-01-12-1/§4-2/ledd/2/setning/4">
        <article class="defaultP">§ 4-2 andre ledd nytt tredje og fjerde punktum skal lyde: Første nye punktum. Andre nye punktum.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2025-12-22-123")

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (
            StructuralAction.INSERT,
            (("section", "4-2"), ("subsection", "2"), ("sentence", "3")),
            "Første nye punktum.",
        ),
        (
            StructuralAction.INSERT,
            (("section", "4-2"), ("subsection", "2"), ("sentence", "4")),
            "Andre nye punktum.",
        ),
    ]


def test_parse_no_amendment_ops_splits_lead_in_tail_with_jf_abbreviation() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2024-01-12-1">
      <article class="change"
               data-add-new-part="lov/2024-01-12-1/§4-2/ledd/2/setning/3 lov/2024-01-12-1/§4-2/ledd/2/setning/4">
        <article class="defaultP">§ 4-2 andre ledd nytt tredje og fjerde punktum skal lyde: Når det i samsvar med denne loven gjøres justeringer, jf. første og andre punktum. Dette gjelder for inneværende og senere regnskapsår.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2025-12-22-123")

    assert [(op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (
            (("section", "4-2"), ("subsection", "2"), ("sentence", "3")),
            "Når det i samsvar med denne loven gjøres justeringer, jf. første og andre punktum.",
        ),
        (
            (("section", "4-2"), ("subsection", "2"), ("sentence", "4")),
            "Dette gjelder for inneværende og senere regnskapsår.",
        ),
    ]


def test_parse_no_amendment_ops_structured_prefers_sentence_targets_over_subsection_attrs() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change"
               data-change-part="lov/2022-05-12-28/§45/ledd/2"
               data-add-new-part="lov/2022-05-12-28/§45/ledd/3 lov/2022-05-12-28/§45/ledd/4">
        <article class="defaultP">§ 45 andre ledd nytt tredje og fjerde punktum skal lyde:</article>
        <article class="legalP">Første nye punktum. Andre nye punktum.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2024-06-21-46")

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.INSERT, (("section", "45"), ("subsection", "2"), ("sentence", "3")), "Første nye punktum."),
        (StructuralAction.INSERT, (("section", "45"), ("subsection", "2"), ("sentence", "4")), "Andre nye punktum."),
    ]


def test_iter_no_document_change_ops_skips_structured_cross_base_target_without_number() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change" data-change-part="lov/1967-02-10/§12/ledd/2">
        <article class="defaultP">12. I lov 10. februar 1967 om behandlingsmåten i forvaltningssaker skal § 12 andre ledd lyde:</article>
        <article class="legalP">Som fullmektig kan brukes enhver myndig person.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    adjudications: list[CompileAdjudication] = []

    grouped = dict(iter_no_document_change_ops(xml, "no/lovtid/2024-06-21-46", adjudications_out=adjudications))

    assert grouped == {}
    assert len(adjudications) == 1
    adjudication = adjudications[0]
    assert adjudication.kind == "no_parse_cross_base_structured_target_skipped"
    assert adjudication.source_statute == "no/lovtid/2024-06-21-46"
    assert adjudication.detail["rule_id"] == "no_parse_cross_base_structured_target_skipped"
    assert adjudication.detail["phase"] == "parse"
    assert adjudication.detail["strict_disposition"] == "block"
    assert adjudication.detail["quirks_disposition"] == "record"
    assert adjudication.detail["base_id"] == "no/lov/2022-05-12-28"
    assert adjudication.detail["target_base"] == "no/lov/1967-02-10"
    assert adjudication.detail["action"] == "replace"
    assert adjudication.detail["raw_target"] == "lov/1967-02-10/§12/ledd/2"


def test_parse_no_amendment_ops_forwards_structured_cross_base_skip_adjudication() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change" data-change-part="lov/1967-02-10/§12/ledd/2">
        <article class="defaultP">12. I lov 10. februar 1967 om behandlingsmåten i forvaltningssaker skal § 12 andre ledd lyde:</article>
        <article class="legalP">Som fullmektig kan brukes enhver myndig person.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    ops = parse_no_amendment_ops(xml, "no/lovtid/2024-06-21-46", adjudications_out=adjudications)

    assert ops == []
    assert [item.kind for item in adjudications] == ["no_parse_cross_base_structured_target_skipped"]
    assert adjudications[0].detail["target_base"] == "no/lov/1967-02-10"


def test_iter_no_document_change_ops_records_unresolved_structured_target_skip() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change" data-change-part="lov/2022-05-12-28/ukjent">
        <article class="defaultP">§ 12 andre ledd skal lyde:</article>
        <article class="legalP">Som fullmektig kan brukes enhver myndig person.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(
        xml,
        "no/lovtid/2024-06-21-46",
        adjudications_out=adjudications,
    )

    assert grouped == []
    assert [item.kind for item in adjudications] == [
        "no_parse_unresolved_structured_target_skipped"
    ]
    adjudication = adjudications[0]
    assert adjudication.source_statute == "no/lovtid/2024-06-21-46"
    assert adjudication.detail["rule_id"] == "no_parse_unresolved_structured_target_skipped"
    assert adjudication.detail["phase"] == "parse"
    assert adjudication.detail["family"] == "target_resolution_recovery"
    assert adjudication.detail["strict_disposition"] == "block"
    assert adjudication.detail["quirks_disposition"] == "record"
    assert adjudication.detail["base_id"] == "no/lov/2022-05-12-28"
    assert adjudication.detail["target_base"] == "no/lov/2022-05-12-28"
    assert adjudication.detail["action"] == "replace"
    assert adjudication.detail["raw_target"] == "lov/2022-05-12-28/ukjent"


def test_iter_no_document_change_ops_records_cross_base_structured_renumber_skip() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change"
               data-move-part="lov/1967-02-10/§12/ledd/2;;lov/2022-05-12-28/§12/ledd/3">
        <article class="defaultP">Nåværende § 12 andre ledd blir nytt tredje ledd.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(
        xml,
        "no/lovtid/2024-06-21-46",
        adjudications_out=adjudications,
    )

    assert grouped == []
    assert [item.kind for item in adjudications] == [
        "no_parse_cross_base_structured_renumber_skipped"
    ]
    adjudication = adjudications[0]
    assert adjudication.source_statute == "no/lovtid/2024-06-21-46"
    assert adjudication.detail["rule_id"] == "no_parse_cross_base_structured_renumber_skipped"
    assert adjudication.detail["phase"] == "parse"
    assert adjudication.detail["family"] == "source_pathology"
    assert adjudication.detail["strict_disposition"] == "block"
    assert adjudication.detail["quirks_disposition"] == "record"
    assert adjudication.detail["base_id"] == "no/lov/2022-05-12-28"
    assert adjudication.detail["target_base"] == "no/lov/1967-02-10"
    assert adjudication.detail["destination_base"] == "no/lov/2022-05-12-28"
    assert adjudication.detail["target_cross_base"] is True
    assert adjudication.detail["destination_cross_base"] is False


def test_iter_no_document_change_ops_records_unresolved_structured_renumber_skip() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change"
               data-move-part="lov/2022-05-12-28/foo;;lov/2022-05-12-28/§12/ledd/3">
        <article class="defaultP">Nåværende § 12 andre ledd blir nytt tredje ledd.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(
        xml,
        "no/lovtid/2024-06-21-46",
        adjudications_out=adjudications,
    )

    assert grouped == []
    assert [item.kind for item in adjudications] == [
        "no_parse_unresolved_structured_renumber_skipped"
    ]
    adjudication = adjudications[0]
    assert adjudication.source_statute == "no/lovtid/2024-06-21-46"
    assert adjudication.detail["rule_id"] == "no_parse_unresolved_structured_renumber_skipped"
    assert adjudication.detail["phase"] == "parse"
    assert adjudication.detail["family"] == "target_resolution_recovery"
    assert adjudication.detail["strict_disposition"] == "block"
    assert adjudication.detail["quirks_disposition"] == "record"
    assert adjudication.detail["base_id"] == "no/lov/2022-05-12-28"
    assert adjudication.detail["raw_target"] == "lov/2022-05-12-28/foo"
    assert adjudication.detail["target_resolved"] is False
    assert adjudication.detail["destination_resolved"] is True


def test_iter_no_document_change_ops_records_malformed_structured_renumber_tokens() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2022-05-12-28">
      <article class="change"
               data-move-part="lov/2022-05-12-28/§12/ledd/2 lov/2022-05-12-28/§12/ledd/3;;">
        <article class="defaultP">Nåværende § 12 andre ledd blir nytt tredje ledd.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(
        xml,
        "no/lovtid/2024-06-21-46",
        adjudications_out=adjudications,
    )

    assert grouped == []
    assert [item.kind for item in adjudications] == [
        "no_parse_malformed_structured_renumber_attr_skipped",
        "no_parse_malformed_structured_renumber_attr_skipped",
    ]
    missing_destination = adjudications[0]
    missing_separator = adjudications[1]
    assert missing_destination.source_statute == "no/lovtid/2024-06-21-46"
    assert missing_destination.detail["rule_id"] == "no_parse_malformed_structured_renumber_attr_skipped"
    assert missing_destination.detail["phase"] == "parse"
    assert missing_destination.detail["family"] == "source_pathology"
    assert missing_destination.detail["blocking"] is True
    assert missing_destination.detail["strict_disposition"] == "block"
    assert missing_destination.detail["quirks_disposition"] == "record"
    assert missing_destination.detail["base_id"] == "no/lov/2022-05-12-28"
    assert missing_destination.detail["source_doc"] == "lov/2022-05-12-28"
    assert missing_destination.detail["attr_name"] == "data-move-part"
    assert missing_destination.detail["raw_token"] == "lov/2022-05-12-28/§12/ledd/3;;"
    assert missing_destination.detail["reason"] == "missing_destination"
    assert missing_separator.detail["raw_token"] == "lov/2022-05-12-28/§12/ledd/2"
    assert missing_separator.detail["reason"] == "missing_separator"


def test_iter_no_document_change_ops_keeps_valid_structured_renumber_when_malformed_token_present() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change"
               data-move-part="lov/2025-01-01-1/§4;;lov/2025-01-01-1/§5 lov/2025-01-01-1/§6">
        <article class="defaultP">Nåværende § 4 blir § 5.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = dict(
        iter_no_document_change_ops(
            xml,
            "no/lovtid/2025-06-20-90",
            adjudications_out=adjudications,
        )
    )

    ops = grouped["no/lov/2025-01-01-1"]
    assert [(op.action, op.target.path, op.destination.path if op.destination else ()) for op in ops] == [
        (StructuralAction.RENUMBER, (("section", "4"),), (("section", "5"),))
    ]
    assert [item.kind for item in adjudications] == [
        "no_parse_malformed_structured_renumber_attr_skipped"
    ]
    assert adjudications[0].detail["raw_token"] == "lov/2025-01-01-1/§6"
    assert adjudications[0].detail["reason"] == "missing_separator"


def test_iter_no_document_change_ops_records_missing_structured_base() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change">
      <article class="change" data-change-part="lov/2022-05-12-28/§12">
        <article class="legalP">§ 12 skal lyde:</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(
        xml,
        "no/lovtid/2024-06-21-46",
        adjudications_out=adjudications,
    )

    assert grouped == []
    assert [item.kind for item in adjudications] == [
        "no_parse_document_change_base_unresolved"
    ]
    adjudication = adjudications[0]
    assert adjudication.source_statute == "no/lovtid/2024-06-21-46"
    assert adjudication.detail["rule_id"] == "no_parse_document_change_base_unresolved"
    assert adjudication.detail["phase"] == "parse"
    assert adjudication.detail["family"] == "source_pathology"
    assert adjudication.detail["blocking"] is True
    assert adjudication.detail["strict_disposition"] == "block"
    assert adjudication.detail["quirks_disposition"] == "record"
    assert adjudication.detail["source_doc"] == ""
    assert adjudication.detail["reason"] == "missing_data_document"


def test_parse_no_amendment_ops_forwards_missing_structured_base_adjudication() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="not-a-lovdata-ref">
      <article class="change" data-change-part="lov/2022-05-12-28/§12">
        <article class="legalP">§ 12 skal lyde:</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    ops = parse_no_amendment_ops(
        xml,
        "no/lovtid/2024-06-21-46",
        adjudications_out=adjudications,
    )

    assert ops == []
    assert [item.kind for item in adjudications] == [
        "no_parse_document_change_base_unresolved"
    ]
    assert adjudications[0].detail["source_doc"] == "not-a-lovdata-ref"
    assert adjudications[0].detail["reason"] == "unmappable_data_document"


def test_parse_no_amendment_ops_unstructured_supports_mixed_existing_and_new_subsections() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2019-06-21-70</li></ul>
    </dd>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 17 tredje, nytt fjerde og femte ledd skal lyde:</article>
        <article class="legalP">Eksisterende tredje ledd.</article>
        <article class="legalP">Nytt fjerde ledd.</article>
        <article class="legalP">Nytt femte ledd.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    grouped = dict(iter_no_document_change_ops(xml, "no/lovtid/2020-12-18-159"))
    ops = grouped["no/lov/2019-06-21-70"]

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "17"), ("subsection", "3")), "Eksisterende tredje ledd."),
        (StructuralAction.INSERT, (("section", "17"), ("subsection", "4")), "Nytt fjerde ledd."),
        (StructuralAction.INSERT, (("section", "17"), ("subsection", "5")), "Nytt femte ledd."),
    ]


def test_parse_no_amendment_ops_unstructured_marks_new_subsection_as_insert() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2019-06-21-70</li></ul>
    </dd>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 39 nytt tredje ledd skal lyde:</article>
        <article class="legalP">Nytt tredje ledd.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2020-12-18-159")

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.INSERT, (("section", "39"), ("subsection", "3")), "Nytt tredje ledd."),
    ]


def test_iter_no_document_change_ops_unstructured_records_base_unresolved_lead() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 5 skal lyde:</article>
        <article class="futureLegalArticle"><h3>§ 5. Tittel</h3></article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(xml, "no/lovtid/2020-12-18-159", adjudications_out=adjudications)

    assert grouped == []
    assert [item.kind for item in adjudications] == ["no_parse_unstructured_lead_base_unresolved"]
    assert adjudications[0].detail["rule_id"] == "no_parse_unstructured_lead_base_unresolved"
    assert adjudications[0].detail["phase"] == "parse"
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["base_id"] == ""
    assert "§ 5 skal lyde" in adjudications[0].detail["source_excerpt"]


def test_iter_no_document_change_ops_unstructured_records_payload_unresolved() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2019-06-21-70</li></ul>
    </dd>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 39 tredje ledd skal lyde:</article>
        <article class="legalP"></article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(xml, "no/lovtid/2020-12-18-159", adjudications_out=adjudications)

    assert grouped == []
    assert [item.kind for item in adjudications] == ["no_parse_unstructured_payload_unresolved"]
    assert adjudications[0].detail["base_id"] == "no/lov/2019-06-21-70"
    assert adjudications[0].detail["target"] == "section:39/subsection:3"
    assert adjudications[0].detail["payload_family"] == "subsection"
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_iter_no_document_change_ops_unstructured_records_unmatched_operative_lead() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="changesToDocuments">
      <ul><li>lov/2019-06-21-70</li></ul>
    </dd>
    <main>
      <section data-name="kapI">
        <article class="defaultP">§ 5 flyttes til § 6.</article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")
    adjudications: list[CompileAdjudication] = []

    grouped = iter_no_document_change_ops(xml, "no/lovtid/2020-12-18-159", adjudications_out=adjudications)

    assert grouped == []
    assert [item.kind for item in adjudications] == ["no_parse_unstructured_lead_unmatched"]
    assert adjudications[0].detail["base_id"] == "no/lov/2019-06-21-70"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True


def test_parse_no_amendment_ops_promotes_replace_plus_same_target_renumber_to_insert() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <article class="document-change" data-document="lov/2023-06-16-62">
      <article class="change" data-change-part="lov/2023-06-16-62/§5-11/ledd/2/setning/3">
        <article class="defaultP">§ 5-11 andre ledd tredje punktum skal lyde: Den tillitsvalgte skal legge ved en erklæring fra den nye kandidaten.</article>
      </article>
      <article class="change" data-move-part="lov/2023-06-16-62/§5-11/ledd/2/setning/3;;lov/2023-06-16-62/§5-11/ledd/2/setning/4">
        <article class="defaultP">Nåværende § 5-11 andre ledd tredje punktum blir nytt fjerde punktum.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")

    ops = parse_no_amendment_ops(xml, "no/lovtid/2024-06-21-51")

    assert [(_action_value(op.action), op.target.path, op.destination.path if op.destination else None) for op in ops] == [
        (
            _action_value(StructuralAction.INSERT),
            (("section", "5-11"), ("subsection", "2"), ("sentence", "3")),
            None,
        ),
        (
            _action_value(StructuralAction.RENUMBER),
            (("section", "5-11"), ("subsection", "2"), ("sentence", "3")),
            (("section", "5-11"), ("subsection", "2"), ("sentence", "4")),
        ),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text == "Den tillitsvalgte skal legge ved en erklæring fra den nye kandidaten."
    assert "no_parse_replace_promoted_to_insert_for_same_target_renumber" in ops[0].provenance_tags


def test_apply_no_ops_replaces_inserts_and_repeals() -> None:
    statute = parse_no_statute(_STATUTE_XML, "no/lov/2025-01-01-1")
    ops = parse_no_amendment_ops(_AMENDMENT_XML, "no/lovtid/2025-02-02-5")

    updated = apply_no_ops(statute, ops)

    chapter = updated.body.children[0]
    assert [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION] == ["2"]

    section_two = next(child for child in chapter.children if child.kind is IRNodeKind.SECTION)
    subsection = section_two.children[1]
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "oppdatert krav"),
        ("2", "to krav"),
        ("3", "tredje krav"),
    ]


def test_apply_no_ops_merges_section_heading_only_replace_with_existing_subsections() -> None:
    statute = IRStatute(
        statute_id="no/lov/2024-01-12-1",
        title="Heading merge test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2-4",
                    children=(IRNode(kind=IRNodeKind.HEADING, text="Gammel tittel"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Første ledd."),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Andre ledd."),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2-4"),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="2-4",
            children=(IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),),
        ),
        source=OperationSource(statute_id="no/lovtid/2024-12-20-92"),
    )

    updated = apply_no_ops(statute, [op])

    section = updated.body.children[0]
    assert [(child.kind, child.label, child.text) for child in section.children] == [
        (IRNodeKind.HEADING, None, "Ny tittel"),
        (IRNodeKind.SUBSECTION, "1", "Første ledd."),
        (IRNodeKind.SUBSECTION, "2", "Andre ledd."),
    ]


def test_apply_no_ops_insert_builds_missing_parent_chain_under_existing_scope() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Missing parent test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="7-3", children=(IRNode(kind=IRNodeKind.HEADING, text="Heading"),)),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2024-06-25-66",
        enacted="2024-06-25",
        effective="2024-06-25",
    )
    ops = [
        LegalOperation(
            op_id="1",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "7-3"), ("subsection", "5"), ("sentence", "3"))),
            payload=IRNode(kind=IRNodeKind.SENTENCE, label="3", text="Tredje punktum."),
            source=source,
        ),
        LegalOperation(
            op_id="2",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "6-2"), ("subsection", "1"), ("sentence", "3"))),
            payload=IRNode(kind=IRNodeKind.SENTENCE, label="3", text="Annet tredje punktum."),
            source=source,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    section_7_3 = next(child for child in updated.body.children if child.kind is IRNodeKind.SECTION and child.label == "7-3")
    subsection_5 = next(child for child in section_7_3.children if child.kind is IRNodeKind.SUBSECTION and child.label == "5")
    assert [(child.kind, child.label, child.text) for child in subsection_5.children] == [
        (IRNodeKind.SENTENCE, "3", "Tredje punktum."),
    ]

    assert not any(
        child.kind is IRNodeKind.SECTION and child.label == "6-2"
        for child in updated.body.children
    )
    assert not any(
        child.kind is IRNodeKind.SENTENCE and child.label == "3"
        for child in updated.body.children
    )


def test_apply_no_ops_materializes_sentence_children_before_sentence_replace() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Sentence split test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3-2",
                    children=(IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="Første punktum. Andre gamle punktum.",
                        ),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-sentence",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "3-2"), ("subsection", "2"), ("sentence", "2"))),
        payload=IRNode(kind=IRNodeKind.SENTENCE, label="2", text="Andre nye punktum."),
        source=OperationSource(statute_id="no/lovtid/2024-12-20-92"),
    )

    updated = apply_no_ops(statute, [op])

    section = updated.body.children[0]
    subsection = section.children[0]
    assert subsection.text == ""
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.SENTENCE, "1", "Første punktum."),
        (IRNodeKind.SENTENCE, "2", "Andre nye punktum."),
    ]


def test_apply_no_ops_appends_shallow_section_sentence_replace_into_sole_subsection() -> None:
    statute = IRStatute(
        statute_id="no/lov/2004-12-17-99",
        title="Shallow sentence append test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="21",
                    children=(IRNode(kind=IRNodeKind.HEADING, text="(straff)"),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Første punktum.",
                        ),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-shallow-sentence",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "21"), ("sentence", "2"))),
        payload=IRNode(kind=IRNodeKind.SENTENCE, label="2", text="Medvirkning straffes ikke."),
        source=OperationSource(statute_id="no/lovtid/2015-06-19-65"),
    )

    updated = apply_no_ops(statute, [op])

    section = updated.body.children[0]
    subsection = next(child for child in section.children if child.kind is IRNodeKind.SUBSECTION)
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.SENTENCE, "1", "Første punktum."),
        (IRNodeKind.SENTENCE, "2", "Medvirkning straffes ikke."),
    ]


def test_apply_no_ops_reorders_sentence_renumber_before_insert_after_materialization() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Sentence renumber test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2-6",
                    children=(IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="Første punktum. Andre gamle punktum.",
                        ),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2024-06-25-66",
        enacted="2024-06-25",
        effective="2024-06-25",
    )
    ops = [
        LegalOperation(
            op_id="insert-2",
            sequence=4,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2-6"), ("subsection", "2"), ("sentence", "2"))),
            payload=IRNode(kind=IRNodeKind.SENTENCE, label="2", text="Andre nye punktum."),
            source=source,
        ),
        LegalOperation(
            op_id="move-2-3",
            sequence=5,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "2-6"), ("subsection", "2"), ("sentence", "2"))),
            destination=LegalAddress(path=(("section", "2-6"), ("subsection", "2"), ("sentence", "3"))),
            source=source,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    subsection = updated.body.children[0].children[0]
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.SENTENCE, "1", "Første punktum."),
        (IRNodeKind.SENTENCE, "2", "Andre nye punktum."),
        (IRNodeKind.SENTENCE, "3", "Andre gamle punktum."),
    ]


def test_apply_no_ops_resolves_sentence_replace_with_repeated_subsection_labels_in_correct_section() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Scoped sentence resolution test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1-4",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Old first. Old second."),),
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3-2",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Other first. Other second."),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-1-4-2-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1-4"), ("subsection", "2"), ("sentence", "1"))),
        payload=IRNode(kind=IRNodeKind.SENTENCE, label="1", text="New first."),
        source=OperationSource(statute_id="no/lovtid/2025-12-22-123"),
    )

    updated = apply_no_ops(statute, [op])

    first_section = updated.body.children[0]
    first_subsection = first_section.children[0]
    assert [(child.kind, child.label, child.text) for child in first_subsection.children] == [
        (IRNodeKind.SENTENCE, "1", "New first."),
        (IRNodeKind.SENTENCE, "2", "Old second."),
    ]

    second_section = updated.body.children[1]
    second_subsection = second_section.children[0]
    assert second_subsection.text == "Other first. Other second."
    assert second_subsection.children == ()


def test_apply_no_ops_resolves_shallow_section_sentence_replace_via_unique_subsection() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Shallow sentence target test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1-2",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Old only sentence."),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-1-2-s1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1-2"), ("sentence", "1"))),
        payload=IRNode(kind=IRNodeKind.SENTENCE, label="1", text="New only sentence."),
        source=OperationSource(statute_id="no/lovtid/2025-12-22-123"),
    )

    updated = apply_no_ops(statute, [op])

    subsection = updated.body.children[0].children[0]
    assert subsection.text == ""
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.SENTENCE, "1", "New only sentence."),
    ]


def test_apply_no_ops_treats_missing_section_replace_as_insert() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Missing section replace test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="3", text="three"),
                        IRNode(kind=IRNodeKind.SECTION, label="4", text="four"),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-3a",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "3a"),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="3a",
            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="new section text"),),
        ),
        source=OperationSource(statute_id="no/lovtid/2023-12-15-91"),
    )
    adjudications: list[CompileAdjudication] = []

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    chapter = updated.body.children[0]
    assert [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION] == ["3", "3a", "4"]
    inserted = next(child for child in chapter.children if child.kind is IRNodeKind.SECTION and child.label == "3a")
    assert [(child.kind, child.label, child.text) for child in inserted.children] == [
        (IRNodeKind.SUBSECTION, "1", "new section text"),
    ]
    assert [(item.kind, item.detail["rule_id"]) for item in adjudications] == [
        ("no_replay_replace_recovered_by_insert", "no_replace_missing_section_insert")
    ]
    assert adjudications[0].detail["family"] == "action_family_recovery"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"


def test_apply_no_ops_materializes_sentence_children_before_existing_items() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Sentence plus items test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1-3",
                    children=(IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="Old lead sentence.",
                            children=(IRNode(kind=IRNodeKind.ITEM, label="a", text="første"),
                                IRNode(kind=IRNodeKind.ITEM, label="b", text="andre"),),
                        ),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-1-3-2-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1-3"), ("subsection", "2"), ("sentence", "1"))),
        payload=IRNode(kind=IRNodeKind.SENTENCE, label="1", text="New lead sentence."),
        source=OperationSource(statute_id="no/lovtid/2025-12-22-123"),
    )

    updated = apply_no_ops(statute, [op])

    subsection = updated.body.children[0].children[0]
    assert subsection.text == ""
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.SENTENCE, "1", "New lead sentence."),
        (IRNodeKind.ITEM, "a", "første"),
        (IRNodeKind.ITEM, "b", "andre"),
    ]


def test_apply_no_ops_appends_next_sentence_on_replace_when_target_missing() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Sentence append test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="6",
                    children=(IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Første punktum. Andre punktum.",
                        ),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-6-1-3",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "6"), ("subsection", "1"), ("sentence", "3"))),
        payload=IRNode(kind=IRNodeKind.SENTENCE, label="3", text="Tredje punktum."),
        source=OperationSource(statute_id="no/lovtid/2025-12-22-123"),
    )
    adjudications: list[CompileAdjudication] = []

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    subsection = updated.body.children[0].children[0]
    assert subsection.text == ""
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.SENTENCE, "1", "Første punktum."),
        (IRNodeKind.SENTENCE, "2", "Andre punktum."),
        (IRNodeKind.SENTENCE, "3", "Tredje punktum."),
    ]
    assert [(item.kind, item.detail["rule_id"]) for item in adjudications] == [
        ("no_replay_replace_recovered_by_insert", "no_replace_missing_sentence_append_to_resolved_parent")
    ]


def test_apply_no_ops_appends_last_item_on_replace_when_target_missing() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Item append test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    children=(IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="Første vilkår."),
                                IRNode(kind=IRNodeKind.ITEM, label="2", text="Andre vilkår."),),
                        ),),
                ),),
        ),
    )
    op = LegalOperation(
        op_id="replace-5-1-last",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"), ("subsection", "1"), ("item", "last"))),
        payload=IRNode(kind=IRNodeKind.ITEM, label="last", text="Tredje vilkår."),
        source=OperationSource(statute_id="no/lovtid/2025-01-28-3"),
    )
    adjudications: list[CompileAdjudication] = []

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    subsection = updated.body.children[0].children[0]
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.ITEM, "1", "Første vilkår."),
        (IRNodeKind.ITEM, "2", "Andre vilkår."),
        (IRNodeKind.ITEM, "3", "Tredje vilkår."),
    ]
    assert [(item.kind, item.detail["rule_id"]) for item in adjudications] == [
        ("no_replay_replace_recovered_by_insert", "no_replace_missing_last_item_append_to_parent")
    ]


def test_apply_no_ops_infers_chapter_parent_for_new_section_insert() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Section family test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="2-1", text="one"),
                        IRNode(kind=IRNodeKind.SECTION, label="2-2", text="two"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="3-1", text="three"),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2024-06-25-66",
        enacted="2024-06-25",
        effective="2024-06-25",
    )
    op = LegalOperation(
        op_id="insert-2-10",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2-10"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="2-10", text="new section"),
        source=source,
    )

    updated = apply_no_ops(statute, [op])

    chapter_2 = updated.body.children[0]
    assert [child.label for child in chapter_2.children] == ["2-1", "2-2", "2-10"]
    assert not any(child.kind is IRNodeKind.SECTION and child.label == "2-10" for child in updated.body.children)


def test_apply_no_ops_renumber_keeps_section_under_existing_chapter() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Chapter-preserving renumber test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="21", text="straff"),
                        IRNode(kind=IRNodeKind.SECTION, label="23", text="endringer"),
                        IRNode(kind=IRNodeKind.SECTION, label="24", text="ikraft"),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2012-05-25-29",
        enacted="2012-05-25",
        effective="2012-05-25",
    )
    ops = [
        LegalOperation(
            op_id="renumber-24-23",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "24"),)),
            destination=LegalAddress(path=(("section", "23"),)),
            source=source,
        ),
        LegalOperation(
            op_id="renumber-23-22",
            sequence=2,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "23"),)),
            destination=LegalAddress(path=(("section", "22"),)),
            source=source,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    chapter_6 = updated.body.children[0]
    assert [(child.kind, child.label, child.text) for child in chapter_6.children] == [
        (IRNodeKind.SECTION, "21", "straff"),
        (IRNodeKind.SECTION, "22", "endringer"),
        (IRNodeKind.SECTION, "23", "ikraft"),
    ]
    assert not any(child.kind is IRNodeKind.SECTION and child.label == "22" for child in updated.body.children)


def test_apply_no_ops_invariant_check_uses_norway_roman_chapter_sort() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Roman chapter order test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="VIII",
                    children=(IRNode(
                            kind=IRNodeKind.SECTION,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Første punktum. Andre punktum."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Eldre tekst. Andre setning."),),
                        ),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="IX",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="11", text="Neste kapittel"),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2022-12-20-122",
        enacted="2022-12-20",
        effective="2022-12-20",
    )
    op = LegalOperation(
        op_id="replace-roman-chapter-sentence",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2"), ("subsection", "2"), ("sentence", "2"))),
        payload=IRNode(kind=IRNodeKind.SENTENCE, label="2", text="Ny andre setning."),
        source=source,
    )

    updated = apply_no_ops(statute, [op])

    chapter_viii = updated.body.children[0]
    subsection_2 = chapter_viii.children[0].children[1]
    assert subsection_2.kind is IRNodeKind.SUBSECTION
    assert subsection_2.children[1].text == "Ny andre setning."
    assert [child.label for child in updated.body.children if child.kind is IRNodeKind.CHAPTER] == ["VIII", "IX"]


def test_apply_no_ops_insert_reuses_existing_target_as_replace() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Insert-as-replace test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="16", text="old section 16"),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2023-12-15-91",
        enacted="2023-12-15",
        effective="2023-12-15",
    )
    op = LegalOperation(
        op_id="insert-16",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "16"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="16", text="replacement section 16"),
        source=source,
    )
    adjudications: list[CompileAdjudication] = []

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    chapter_4 = updated.body.children[0]
    assert [(child.label, child.text) for child in chapter_4.children] == [("16", "replacement section 16")]
    assert [(item.kind, item.detail["rule_id"]) for item in adjudications] == [
        ("no_replay_insert_occupied_target_replaced", "no_insert_occupied_target_replace")
    ]


def test_apply_no_ops_renumber_chain_avoids_duplicate_sections() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Renumbering test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="4", text="old 4"),
                IRNode(kind=IRNodeKind.SECTION, label="5", text="old 5"),
                IRNode(kind=IRNodeKind.SECTION, label="6", text="old 6"),
                IRNode(kind=IRNodeKind.SECTION, label="7", text="old 7"),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2025-06-20-90",
        enacted="2025-06-20",
        effective="2025-06-20",
    )
    ops = [
        LegalOperation(
            op_id="1",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "7"),)),
            destination=LegalAddress(path=(("section", "8"),)),
            source=source,
        ),
        LegalOperation(
            op_id="2",
            sequence=2,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "6"),)),
            destination=LegalAddress(path=(("section", "7"),)),
            source=source,
        ),
        LegalOperation(
            op_id="3",
            sequence=3,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "5"),)),
            destination=LegalAddress(path=(("section", "6"),)),
            source=source,
        ),
        LegalOperation(
            op_id="4",
            sequence=4,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "4"),)),
            destination=LegalAddress(path=(("section", "5"),)),
            source=source,
        ),
        LegalOperation(
            op_id="5",
            sequence=5,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "4"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="4", text="new 4"),
            source=source,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    assert [child.label for child in updated.body.children] == ["4", "5", "6", "7", "8"]
    assert [child.text for child in updated.body.children] == ["new 4", "old 4", "old 5", "old 6", "old 7"]


def test_apply_no_ops_renumber_can_clear_occupied_destination_not_moved_elsewhere() -> None:
    statute = IRStatute(
        statute_id="no/lov/2004-12-17-99",
        title="Klimakvoteloven tail test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="19", text="old 19"),
                        IRNode(kind=IRNodeKind.SECTION, label="20", text="old 20"),
                        IRNode(kind=IRNodeKind.SECTION, label="21", text="old 21"),
                        IRNode(kind=IRNodeKind.SECTION, label="21a", text="old 21a"),
                        IRNode(kind=IRNodeKind.SECTION, label="22", text="old 22"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="23", text="old 23"),
                        IRNode(kind=IRNodeKind.SECTION, label="24", text="old 24"),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2012-05-25-29",
        enacted="2012-05-25",
        effective="2012-05-25",
    )
    ops = [
        LegalOperation(
            op_id="replace-19",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "19"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="19", text="new 19"),
            source=source,
        ),
        LegalOperation(
            op_id="renumber-21a-20",
            sequence=2,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "21a"),)),
            destination=LegalAddress(path=(("section", "20"),)),
            source=source,
        ),
        LegalOperation(
            op_id="replace-21",
            sequence=3,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "21"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="21", text="new 21"),
            source=source,
        ),
        LegalOperation(
            op_id="renumber-23-22",
            sequence=4,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "23"),)),
            destination=LegalAddress(path=(("section", "22"),)),
            source=source,
        ),
        LegalOperation(
            op_id="renumber-24-23",
            sequence=5,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "24"),)),
            destination=LegalAddress(path=(("section", "23"),)),
            source=source,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    chapter_5 = updated.body.children[0]
    chapter_6 = updated.body.children[1]
    assert [child.label for child in chapter_5.children] == ["19", "20", "21"]
    assert [child.text for child in chapter_5.children] == ["new 19", "old 21a", "new 21"]
    assert [child.label for child in chapter_6.children] == ["22", "23"]
    assert [child.text for child in chapter_6.children] == ["old 23", "old 24"]


def test_apply_no_ops_sorts_by_effective_date_not_local_sequence() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Ordering test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="4", text="base"),)),
    )
    older = OperationSource(
        statute_id="no/lovtid/2023-12-15-90",
        enacted="2023-12-15",
        effective="2023-12-15",
    )
    later = OperationSource(
        statute_id="no/lovtid/2025-06-20-90",
        enacted="2025-06-20",
        effective="2025-06-20",
    )
    ops = [
        LegalOperation(
            op_id="later",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "4"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="4", text="later text"),
            source=later,
        ),
        LegalOperation(
            op_id="older",
            sequence=9,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "4"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="4", text="older text"),
            source=older,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    assert updated.body.children[0].text == "later text"


def test_apply_no_ops_reorders_split_block_renumber_chain_before_insert() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Split renumber test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="first"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="old second"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="old third"),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2025-06-20-91",
        enacted="2025-06-20",
        effective="2025-06-20",
    )
    ops = [
        LegalOperation(
            op_id="insert-2",
            sequence=4,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "3"), ("subsection", "2"))),
            payload=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="new second"),
            source=source,
        ),
        LegalOperation(
            op_id="move-2-3",
            sequence=5,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "3"), ("subsection", "2"))),
            destination=LegalAddress(path=(("section", "3"), ("subsection", "3"))),
            source=source,
        ),
        LegalOperation(
            op_id="move-3-4",
            sequence=6,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "3"), ("subsection", "3"))),
            destination=LegalAddress(path=(("section", "3"), ("subsection", "4"))),
            source=source,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    section = updated.body.children[0]
    assert [child.label for child in section.children] == ["1", "2", "3", "4"]
    assert [child.text for child in section.children] == ["first", "new second", "old second", "old third"]


def test_apply_no_ops_repeal_happens_before_renumber_into_same_label() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Reorder repeal/renumber test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3-1",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="old fifth"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="6", text="old sixth"),),
                ),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2024-06-25-66",
        enacted="2024-06-25",
        effective="2024-06-25",
    )
    ops = [
        LegalOperation(
            op_id="repeal-5",
            sequence=8,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "3-1"), ("subsection", "5"))),
            source=source,
        ),
        LegalOperation(
            op_id="move-6-5",
            sequence=9,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "3-1"), ("subsection", "6"))),
            destination=LegalAddress(path=(("section", "3-1"), ("subsection", "5"))),
            source=source,
        ),
    ]

    updated = apply_no_ops(statute, ops)

    section = updated.body.children[0]
    assert [(child.label, child.text) for child in section.children] == [("5", "old sixth")]


def test_apply_no_ops_exact_target_insert_does_not_duplicate_section() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Invariant test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="4", text="base"),)),
    )
    source = OperationSource(
        statute_id="no/lovtid/2025-06-20-90",
        enacted="2025-06-20",
        effective="2025-06-20",
    )
    ops = [
        LegalOperation(
            op_id="dup",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "4"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="4", text="duplicate"),
            source=source,
        ),
    ]
    adjudications: list[CompileAdjudication] = []

    updated = apply_no_ops(statute, ops, adjudications_out=adjudications)

    assert [(child.label, child.text) for child in updated.body.children] == [("4", "duplicate")]
    assert [(item.kind, item.detail["rule_id"]) for item in adjudications] == [
        ("no_replay_insert_occupied_target_replaced", "no_insert_occupied_target_replace")
    ]


def test_apply_no_ops_direct_child_insert_replacement_is_adjudicated() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Direct child insert-as-replace test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    children=(IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="old item"),),
                        ),),
                ),),
        ),
    )
    source = OperationSource(statute_id="no/lovtid/2025-06-20-90")
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="insert-item-1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "5"), ("item", "9"))),
        payload=IRNode(kind=IRNodeKind.ITEM, label="1", text="new item"),
        source=source,
    )

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    item = updated.body.children[0].children[0].children[0]
    assert item.text == "new item"
    assert [(item.kind, item.detail["rule_id"]) for item in adjudications] == [
        ("no_replay_insert_occupied_direct_child_replaced", "no_insert_occupied_direct_child_replace")
    ]
    assert adjudications[0].detail["target"] == "section:5/item:9"
    assert adjudications[0].detail["occupied_child_path"] == "section:5/subsection:1/item:1"


def test_apply_no_ops_strict_action_family_rejects_recovery() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Strict action family test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="4", text="base"),)),
    )
    source = OperationSource(statute_id="no/lovtid/2025-06-20-90")
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="dup",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "4"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="4", text="duplicate"),
        source=source,
    )

    with pytest.raises(ValueError, match="action-family recovery"):
        apply_no_ops(
            statute,
            [op],
            adjudications_out=adjudications,
            strict_action_family=True,
        )

    assert [(item.kind, item.detail["rule_id"]) for item in adjudications] == [
        ("no_replay_insert_occupied_target_replaced", "no_insert_occupied_target_replace")
    ]


def test_apply_no_ops_collects_missing_target_adjudication() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Adjudication test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2025-06-20-90",
        enacted="2025-06-20",
        effective="2025-06-20",
    )
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="no-target-replace",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "9"),)),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="missing section child"),
        source=source,
    )

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "replay_unresolved_target"
    assert adjudications[0].detail["target"] == "section:9"
    assert adjudications[0].detail["action"] == "replace"
    assert adjudications[0].detail["rule_id"] == "replay_unresolved_target"
    assert adjudications[0].detail["phase"] == "replay"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert updated.body.children[0].label == "1"


def test_apply_no_ops_collects_noop_for_empty_target_path() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="No-op adjudication test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),),
        ),
    )
    source = OperationSource(
        statute_id="no/lovtid/2025-06-20-90",
        enacted="2025-06-20",
        effective="2025-06-20",
    )
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="empty-target-skip",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=()),
        source=source,
    )

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "replay_noop"
    assert adjudications[0].detail["action"] == "replace"
    assert adjudications[0].detail["rule_id"] == "replay_noop"
    assert adjudications[0].detail["phase"] == "replay"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert updated.statute_id == "no/lov/2025-01-01-1"


def test_apply_no_ops_collects_unsupported_action() -> None:
    statute = IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Unsupported action adjudication test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),)),
    )
    source = OperationSource(
        statute_id="no/lovtid/2025-06-20-90",
        enacted="2025-06-20",
        effective="2025-06-20",
    )
    with pytest.raises(TypeError, match="LegalOperation.action must be StructuralAction"):
        LegalOperation(
            op_id="unsupported-action",
            sequence=1,
            action=cast(StructuralAction, "unknown"),
            target=LegalAddress(path=(("section", "1"),)),
            source=source,
        )

    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="unsupported-text-repeal",
        sequence=1,
        action=StructuralAction.TEXT_REPEAL,
        target=LegalAddress(path=(("section", "1"),)),
        source=source,
    )

    updated = apply_no_ops(statute, [op], adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "replay_unsupported_action"
    assert adjudications[0].detail["action"] == "text_repeal"
    assert adjudications[0].detail["target"] == "section:1"
    assert adjudications[0].detail["rule_id"] == "replay_unsupported_action"
    assert adjudications[0].detail["phase"] == "replay"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert updated.body.children[0].text == "base"


def test_open_lovdata_amendment_archive_yields_source_ids(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2025.tar.bz2"
    member_name = "lti/2025/nl-20250202-005.xml"

    with tarfile.open(archive_path, "w:bz2") as tf:
        payload = _AMENDMENT_XML
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    items = list(open_lovdata_amendment_archive(str(archive_path)))

    assert len(items) == 1
    assert items[0][0] == "no/lovtid/2025-02-02-5"
    assert b"document-change" in items[0][1]


def test_build_no_populates_amendment_index_from_lovtidend_archives(tmp_path) -> None:
    base_archive = tmp_path / "gjeldende-lover.tar.bz2"
    amendment_archive = tmp_path / "lovtidend-avd1-2025.tar.bz2"
    output_dir = tmp_path / "out"

    with tarfile.open(base_archive, "w:bz2") as tf:
        payload = _STATUTE_XML
        info = tarfile.TarInfo("nl/nl-20250101-001.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    with tarfile.open(amendment_archive, "w:bz2") as tf:
        payload = _AMENDMENT_XML
        info = tarfile.TarInfo("lti/2025/nl-20250202-005.xml")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    asyncio.run(
        _build_no(
            base_archive,
            output_dir,
            verbose=False,
            amendment_archives=[amendment_archive],
        )
    )

    amendments = json.loads((output_dir / "amendments.json").read_text(encoding="utf-8"))
    stats = json.loads((output_dir / "stats.json").read_text(encoding="utf-8"))
    statutes = json.loads((output_dir / "statutes.json").read_text(encoding="utf-8"))

    assert amendments == {"no/lov/2025-01-01-1": ["no/lovtid/2025-02-02-5"]}
    assert stats["n_statutes"] == 1
    assert stats["n_amendment_links"] == 1
    assert statutes["no/lov/2025-01-01-1"]["title"] == "Testlov om data"
