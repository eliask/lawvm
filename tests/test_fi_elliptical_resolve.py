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
    enclosing_section: str,
    surface: str,
) -> ReferenceMention:
    """Build a bare INTERNAL mention as the extractor emits one.

    The TARGET is bare (empty section, the part the surface omits); the SOURCE
    provenance carries the ENCLOSING section label the extractor threaded on from
    the citing ``<p>``'s real ``<section>`` ancestry — the authoritative context
    the resolver reads (no byte-offset remap). ``enclosing_section=""`` models a
    citation outside any labeled section (OPEN).
    """
    return ReferenceMention(
        source_provision_ref=ProvisionRef(
            statute_id="123/2024", section_label=enclosing_section
        ),
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
            source_file="123/2024", byte_offset=0, byte_len=len(surface)
        ),
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


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
    # The citation sits in section 5 (threaded onto the source provenance).
    m = _mention_at(
        subsection_num=None, item_label="1", enclosing_section="5", surface="1 kohdassa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.elliptical_status is EllipticalStatus.RESOLVED
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
    m = _mention_at(
        subsection_num=2, item_label=None, enclosing_section="5", surface="2 momentissa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.elliptical_status is EllipticalStatus.RESOLVED
    tgt = res.mention.target_provision_ref
    assert tgt is not None
    assert tgt.section_label == "5"  # the enclosing section, NOT empty / root
    assert tgt.subsection_num == 2
    assert res.mention.cite_confidence is CiteConfidence.EXACT


def test_bare_momentti_unverified_by_structure_is_approximate() -> None:
    """A bare momentti the enclosing section does NOT enumerate resolves APPROXIMATE.

    Section 5 enumerates moments 1..3; a bare ``7 momentissa`` names a momentti
    the materialized structure does not carry. Convention still attaches the
    enclosing section (defensible), but the momentti's existence is unverified, so
    the confidence is APPROXIMATE — not a laundered EXACT.
    """
    structures = build_section_structures(_STATUTE_XML)
    m = _mention_at(
        subsection_num=7, item_label=None, enclosing_section="5", surface="7 momentissa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.elliptical_status is EllipticalStatus.RESOLVED
    tgt = res.mention.target_provision_ref
    assert tgt is not None
    assert tgt.section_label == "5"
    assert tgt.subsection_num == 7
    assert res.mention.cite_confidence is CiteConfidence.APPROXIMATE


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
    m = _mention_at(
        subsection_num=None, item_label="1", enclosing_section="9", surface="1 kohdassa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.elliptical_status is EllipticalStatus.AMBIGUOUS
    # Both kohta-carrying moments are listed; none is picked.
    assert res.candidate_subsections == (1, 2)
    assert res.mention.cite_confidence is CiteConfidence.AMBIGUOUS
    assert res.mention.target_provision_ref is not None
    # Still no section guessed onto the target (the verdict is fail-loud).
    assert res.mention.target_provision_ref.section_label == ""


def test_bare_ref_outside_any_section_is_open() -> None:
    """A bare ref with no enclosing-section label on its provenance is OPEN, not root."""
    structures = build_section_structures(_STATUTE_XML)
    # No enclosing section threaded (the citation sits outside any labeled section).
    m = _mention_at(
        subsection_num=2, item_label=None, enclosing_section="", surface="2 momentissa"
    )
    res = resolve_elliptical_mention(m, structures)
    assert res.elliptical_status is EllipticalStatus.OPEN
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
    assert res.elliptical_status is EllipticalStatus.NOT_ELLIPTICAL
    assert res.mention is m  # unchanged pass-through


def test_resolve_mentions_batch_preserves_order_and_passes_non_internal() -> None:
    """Batch helper resolves bare internals and passes non-internal through."""
    bare_kohta = _mention_at(
        subsection_num=None, item_label="1", enclosing_section="5", surface="1 kohdassa"
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
    assert [r.elliptical_status for r in out] == [
        EllipticalStatus.RESOLVED,
        EllipticalStatus.NOT_ELLIPTICAL,
    ]
    assert out[0].mention.target_provision_ref is not None
    assert out[0].mention.target_provision_ref.section_label == "5"


# A pre-eId Finlex consolidation shape (cf. 1935/62, 1942/598): the <section>
# elements carry NO eId — only a <num> surface (``33 §.``) — and the subsections
# carry neither eId nor <num>. The old byte-offset remap (keyed on eId
# occurrences) found ZERO sections here, so the bare ``Edellä 1 momentissa`` fell
# to OPEN. With the enclosing section threaded from real <num>-derived ancestry,
# it resolves to the section it sits in.
_NO_EID_STATUTE_XML = f"""<akomaNtoso xmlns="{_AKN}">
<act>
<body>
<section><num>10 §.</num>
  <subsection><content><p>Kymmenennen pykalan ensimmainen momentti.</p></content></subsection>
</section>
<section><num>33 §.</num>
  <subsection><content><p>Kolmannenkymmenennenkolmannen alku.</p></content></subsection>
  <subsection><content><p>Edellä 1 momentissa mainittu oikeus.</p></content></subsection>
</section>
</body>
</act>
</akomaNtoso>""".encode("utf-8")


def test_no_eid_section_resolves_via_num_ancestry() -> None:
    """A bare momentti in a NO-eId section resolves via <num> ancestry, not OPEN.

    This is the end-to-end path the threading hardens: the extractor reads the
    enclosing section's ``<num>`` label (``33``) from the citing <p>'s real
    ancestry and stamps it onto the mention's source provenance. The extractor
    now ALSO fills the bare-momentti TARGET section from that enclosing label by
    drafting convention (so the parquet projection, which does not run the
    elliptical resolver, anchors it too). The eId-keyed byte remap could not see
    this section at all (no eId), so this previously fell to OPEN.
    """
    from lawvm.finland.references.ref_mention_extractor import (
        extract_all_reference_mentions,
    )

    extraction = extract_all_reference_mentions(_NO_EID_STATUTE_XML, "1935/62")
    # The bare-momentti mention now carries BOTH the enclosing section (33) on its
    # source provenance AND a TARGET section filled from it by convention.
    internal_bare = [
        m
        for m in extraction.mentions
        if m.cite_kind is CiteKind.INTERNAL
        and m.target_provision_ref is not None
        and m.target_provision_ref.subsection_num == 1
        and (m.surface_text or "").strip().startswith("1 moment")
    ]
    assert internal_bare, "extractor emitted no bare internal momentti mention"
    assert internal_bare[0].source_provision_ref is not None
    assert internal_bare[0].source_provision_ref.section_label == "33"
    # Target section filled at extraction time (convention), not left empty.
    assert internal_bare[0].target_provision_ref is not None
    assert internal_bare[0].target_provision_ref.section_label == "33"

    out = resolve_elliptical_mentions(
        list(extraction.mentions), _NO_EID_STATUTE_XML
    )
    # The momentti target is already anchored at extraction, so the resolver
    # passes it through (NOT_ELLIPTICAL) with the section intact — the end-to-end
    # target is the ENCLOSING section 33, NOT root / OPEN.
    bare = [
        r
        for r in out
        if (r.mention.surface_text or "").strip().startswith("1 moment")
    ]
    assert bare, "bare momentti in a no-eId section missing after resolution"
    tgt = bare[0].mention.target_provision_ref
    assert tgt is not None
    assert tgt.section_label == "33"
    assert tgt.subsection_num == 1
    assert bare[0].mention.cite_confidence is CiteConfidence.EXACT
    # And nothing in this statute fell to OPEN.
    assert not any(r.elliptical_status is EllipticalStatus.OPEN for r in out)
