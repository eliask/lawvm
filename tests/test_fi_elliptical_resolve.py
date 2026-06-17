"""Elliptical / anaphoric INTERNAL-reference resolution against the statute tree.

Covers ``lawvm.finland.references.elliptical_resolve``: a bare momentti / bare
kohta reference omits part of its address but is deterministically resolvable
against the ENCLOSING section's materialized child structure. The recognizer
leaves the omitted part empty; this pass fills it from context (convention) or
structural uniqueness, or fails loud (ambiguous / open) — it never silently
picks, and it NEVER resolves to the whole-statute root.
"""
from __future__ import annotations

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.finland.references.elliptical_resolve import (
    EllipticalStatus,
    build_section_structures,
    resolve_elliptical_mention,
    resolve_elliptical_mentions,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A synthetic statute with ONE section (sec 5) whose:
#   * momentti 1 carries NO kohta (a single content paragraph of prose);
#   * momentti 2 carries kohta (two <paragraph> items).
# Plus a second section (sec 6) so byte extents are bounded by a real boundary.
# The body text places a bare ``1 kohdassa`` and a bare ``2 momentissa`` INSIDE
# section 5, after the structure, so a mention anchored there must resolve to
# sec 5 (NOT root).
_STATUTE_XML = f"""<akomaNtoso xmlns="{_AKN}">
<act>
<body>
<section eId="sec_5"><num>5 §</num>
  <subsection eId="sec_5__subsec_1"><num>1</num>
    <content><p>Ensimmaisen momentin proosaa ilman kohtia.</p></content>
  </subsection>
  <subsection eId="sec_5__subsec_2"><num>2</num>
    <intro><p>Toisen momentin johdanto:</p></intro>
    <paragraph eId="sec_5__subsec_2__para_1"><num>1)</num>
      <content><p>ensimmainen kohta;</p></content></paragraph>
    <paragraph eId="sec_5__subsec_2__para_2"><num>2)</num>
      <content><p>toinen kohta.</p></content></paragraph>
  </subsection>
  <subsection eId="sec_5__subsec_3"><num>3</num>
    <content><p>Edella 1 kohdassa ja 2 momentissa tarkoitettu paatos.</p></content>
  </subsection>
</section>
<section eId="sec_6"><num>6 §</num>
  <subsection eId="sec_6__subsec_1"><num>1</num>
    <content><p>Toisen pykalan teksti.</p></content>
  </subsection>
</section>
</body>
</act>
</akomaNtoso>""".encode("utf-8")


def _mention_at(
    *,
    subsection_num: int | None,
    item_label: str | None,
    byte_offset: int,
    surface: str,
) -> ReferenceMention:
    """Build a bare INTERNAL mention as the recognizer emits one (empty section)."""
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="123/2024"),
        target_provision_ref=ProvisionRef(
            statute_id="123/2024",
            section_label="",
            subsection_num=subsection_num,
            item_label=item_label,
        ),
        cite_kind=CiteKind.INTERNAL,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="internal_section_ref",
        source_span=SourceSpan(
            source_file="123/2024", byte_offset=byte_offset, byte_len=len(surface)
        ),
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def _byte_offset_of(needle: str) -> int:
    off = _STATUTE_XML.find(needle.encode("utf-8"))
    assert off >= 0, f"fixture missing {needle!r}"
    return off


def test_section_structure_oracle_is_materialized() -> None:
    """sec 5: only momentti 2 carries kohta; sec 6: no kohta anywhere."""
    structures = build_section_structures(_STATUTE_XML)
    by_label = {s.section_label: s for s in structures}
    assert set(by_label) == {"5", "6"}
    sec5 = by_label["5"]
    assert sec5.subsec_nums == (1, 2, 3)
    # Structural uniqueness: ONLY momentti 2 carries kohta in section 5.
    assert sec5.subsecs_with_kohta == (2,)
    sec6 = by_label["6"]
    assert sec6.subsecs_with_kohta == ()


def test_bare_kohta_resolves_to_unique_momentti_with_kohta() -> None:
    """Bare ``1 kohdassa`` -> sec 5 momentti 2 (the only momentti WITH kohta)."""
    structures = build_section_structures(_STATUTE_XML)
    # Anchor the citation inside section 5's momentti 3 prose.
    off = _byte_offset_of("Edella 1 kohdassa")
    m = _mention_at(
        subsection_num=None, item_label="1", byte_offset=off, surface="1 kohdassa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.status is EllipticalStatus.RESOLVED
    tgt = res.mention.target_provision_ref
    assert tgt is not None
    # Resolves to the ENCLOSING section 5, momentti 2 (NOT the root, NOT momentti 1).
    assert tgt.section_label == "5"
    assert tgt.subsection_num == 2
    assert tgt.item_label == "1"
    assert res.mention.cite_confidence is CiteConfidence.EXACT


def test_bare_momentti_resolves_to_enclosing_section() -> None:
    """Bare ``2 momentissa`` -> the ENCLOSING section 5's momentti 2, NOT root."""
    structures = build_section_structures(_STATUTE_XML)
    off = _byte_offset_of("2 momentissa tarkoitettu")
    m = _mention_at(
        subsection_num=2, item_label=None, byte_offset=off, surface="2 momentissa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.status is EllipticalStatus.RESOLVED
    tgt = res.mention.target_provision_ref
    assert tgt is not None
    assert tgt.section_label == "5"  # the enclosing section, NOT empty / root
    assert tgt.subsection_num == 2
    assert res.mention.cite_confidence is CiteConfidence.EXACT


def test_bare_kohta_with_two_kohta_carrying_moments_is_ambiguous() -> None:
    """When >1 momentti carries kohta, a bare kohta is AMBIGUOUS (never picked)."""
    # A section whose momentti 1 AND momentti 2 both carry kohta.
    xml = f"""<akomaNtoso xmlns="{_AKN}"><act><body>
<section eId="sec_9"><num>9 §</num>
  <subsection eId="sec_9__subsec_1"><num>1</num>
    <paragraph eId="sec_9__subsec_1__para_1"><num>1)</num>
      <content><p>a</p></content></paragraph>
  </subsection>
  <subsection eId="sec_9__subsec_2"><num>2</num>
    <paragraph eId="sec_9__subsec_2__para_1"><num>1)</num>
      <content><p>Edella 1 kohdassa tarkoitettu.</p></content></paragraph>
  </subsection>
</section></body></act></akomaNtoso>""".encode("utf-8")
    structures = build_section_structures(xml)
    off = xml.find(b"Edella 1 kohdassa")
    assert off >= 0
    m = _mention_at(
        subsection_num=None, item_label="1", byte_offset=off, surface="1 kohdassa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.status is EllipticalStatus.AMBIGUOUS
    # Both kohta-carrying moments are listed; none is picked.
    assert res.candidate_subsections == (1, 2)
    assert res.mention.cite_confidence is CiteConfidence.AMBIGUOUS
    assert res.mention.target_provision_ref is not None
    # Still no section guessed onto the target (the verdict is fail-loud).
    assert res.mention.target_provision_ref.section_label == ""


def test_bare_ref_outside_any_section_is_open() -> None:
    """A bare ref whose byte span sits outside any <section> is OPEN, not root."""
    structures = build_section_structures(_STATUTE_XML)
    # Byte offset 0 (the <akomaNtoso> open tag) precedes the first section.
    m = _mention_at(
        subsection_num=2, item_label=None, byte_offset=0, surface="2 momentissa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.status is EllipticalStatus.OPEN
    assert res.mention.cite_confidence is CiteConfidence.OPEN


def test_already_anchored_internal_ref_passes_through() -> None:
    """An internal ref that already names its section is NOT elliptical here."""
    structures = build_section_structures(_STATUTE_XML)
    m = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="123/2024"),
        target_provision_ref=ProvisionRef(
            statute_id="123/2024", section_label="5", subsection_num=2
        ),
        cite_kind=CiteKind.INTERNAL,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="internal_section_ref",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text="5 §:n 2 momentissa",
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.status is EllipticalStatus.NOT_ELLIPTICAL
    assert res.mention is m  # unchanged pass-through


def test_resolve_mentions_batch_preserves_order_and_passes_non_internal() -> None:
    """Batch helper resolves bare internals and passes non-internal through."""
    off_k = _byte_offset_of("Edella 1 kohdassa")
    bare_kohta = _mention_at(
        subsection_num=None, item_label="1", byte_offset=off_k, surface="1 kohdassa"
    )
    cross = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="123/2024"),
        target_provision_ref=ProvisionRef(statute_id="fi-name:jokin laki"),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.STATUTE_ONLY,
        phrase_lemma="by_name_ref",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text="jonkin lain",
    )
    out = resolve_elliptical_mentions([bare_kohta, cross], _STATUTE_XML)
    assert [r.status for r in out] == [
        EllipticalStatus.RESOLVED,
        EllipticalStatus.NOT_ELLIPTICAL,
    ]
    assert out[0].mention.target_provision_ref is not None
    assert out[0].mention.target_provision_ref.section_label == "5"
