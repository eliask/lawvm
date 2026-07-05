"""Gate for the anaphoric reference recognizer (first DISCOURSE-level tier).

Covers ``recognize_anaphoric_refs`` (src/lawvm/finland/references/anaphora.py):
an anaphor (determiner + reference head) resolves to its ANTECEDENT — the nearest
preceding concrete reference of the matching kind. Statuses: RESOLVED (one
antecedent), AMBIGUOUS (several equally-recent — listed, none picked), OPEN (no
antecedent in scope — tagged, never fabricated). ``tämän lain`` ("this act") is
the self-referential variant, always bound to the citing statute (INTERNAL).
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.anaphora import (
    AnaphorStatus,
    _downgrade_for_approximate,
    _locate_offset,
    recognize_anaphoric_refs,
)


# --- RESOLVED: anaphor binds to the most-recent NAMED act -------------------


def test_mainitun_lain_resolves_to_preceding_named_act() -> None:
    text = (
        "Luonnonsuojelulaissa säädetään suojelusta. "
        "Mainitun lain mukaan toiminta on kiellettyä."
    )
    refs = recognize_anaphoric_refs(text)
    assert len(refs) == 1
    r = refs[0]
    assert r.anaphor_status is AnaphorStatus.RESOLVED
    assert r.surface_text == "Mainitun lain"
    assert r.mention.cite_kind is CiteKind.CROSS_STATUTE
    assert r.mention.target_provision_ref is not None
    # Bound to the named act that preceded it (by-name antecedent), act-level.
    assert r.mention.target_provision_ref.statute_id == "fi-name:luonnonsuojelulaki"
    assert r.mention.target_provision_ref.section_label == ""


def test_kyseisen_lain_also_resolves_to_named_antecedent() -> None:
    text = "Ympäristönsuojelulaissa säädetään. Kyseisen lain nojalla annetaan."
    refs = recognize_anaphoric_refs(text)
    assert len(refs) == 1
    assert refs[0].anaphor_status is AnaphorStatus.RESOLVED
    assert refs[0].mention.target_provision_ref is not None
    assert (
        refs[0].mention.target_provision_ref.statute_id
        == "fi-name:ympäristönsuojelulaki"
    )


def test_edella_mainitun_lain_fronted_cue_resolves() -> None:
    text = "Jätelaissa säädetään jätteistä. Edellä mainitun lain mukaisesti toimitaan."
    refs = recognize_anaphoric_refs(text)
    assert len(refs) == 1
    assert refs[0].anaphor_status is AnaphorStatus.RESOLVED
    assert refs[0].surface_text.lower().startswith("edellä mainitun lain")
    assert refs[0].mention.target_provision_ref is not None
    assert refs[0].mention.target_provision_ref.statute_id == "fi-name:jätelaki"


# --- RESOLVED: provision-kind anaphor binds to nearest SECTION --------------


def test_mainitussa_pykalassa_resolves_to_preceding_section() -> None:
    text = "5 §:ssä säädetään asiasta. Mainitussa pykälässä tarkoitettu toiminta."
    refs = recognize_anaphoric_refs(text, statute_id="1/2020")
    assert len(refs) == 1
    r = refs[0]
    assert r.anaphor_status is AnaphorStatus.RESOLVED
    assert r.mention.cite_kind is CiteKind.INTERNAL
    assert r.mention.target_provision_ref is not None
    assert r.mention.target_provision_ref.statute_id == "1/2020"
    assert r.mention.target_provision_ref.section_label == "5"


# --- SELF: "this act" binds to the citing statute (INTERNAL) ----------------


def test_taman_lain_resolves_to_citing_statute() -> None:
    text = "Tämän lain 5 §:ssä säädetään asiasta."
    refs = recognize_anaphoric_refs(text, statute_id="123/2020")
    assert len(refs) == 1
    r = refs[0]
    assert r.anaphor_status is AnaphorStatus.RESOLVED
    assert r.surface_text == "Tämän lain"
    assert r.mention.cite_kind is CiteKind.INTERNAL
    assert r.mention.cite_confidence is CiteConfidence.EXACT
    assert r.mention.target_provision_ref is not None
    assert r.mention.target_provision_ref.statute_id == "123/2020"


def test_taman_lain_resolves_self_even_without_known_id() -> None:
    # "this act" is determinate by construction; with an unknown citing id it is
    # still INTERNAL (empty-id self), never OPEN.
    refs = recognize_anaphoric_refs("Tämän lain mukaan toimitaan.")
    assert len(refs) == 1
    assert refs[0].anaphor_status is AnaphorStatus.RESOLVED
    assert refs[0].mention.cite_kind is CiteKind.INTERNAL
    assert refs[0].mention.target_provision_ref is not None
    assert refs[0].mention.target_provision_ref.statute_id == ""


# --- OPEN: anaphor with no antecedent ---------------------------------------


def test_anaphor_without_antecedent_is_open() -> None:
    text = "Mainitun lain mukaan toiminta on kiellettyä."
    refs = recognize_anaphoric_refs(text)
    assert len(refs) == 1
    r = refs[0]
    assert r.anaphor_status is AnaphorStatus.OPEN
    assert r.mention.cite_confidence is CiteConfidence.OPEN
    assert r.mention.target_provision_ref is None
    assert r.candidates == ()


def test_provision_anaphor_with_only_act_antecedent_is_open() -> None:
    # A PROVISION head (pykälässä) does NOT bind to an act-only antecedent.
    text = "Luonnonsuojelulaissa säädetään. Mainitussa pykälässä tarkoitettu."
    refs = recognize_anaphoric_refs(text, statute_id="1/2020")
    assert len(refs) == 1
    assert refs[0].anaphor_status is AnaphorStatus.OPEN
    assert refs[0].mention.target_provision_ref is None


# --- AMBIGUOUS: two competing equally-recent antecedents --------------------


def test_two_competing_antecedents_are_ambiguous() -> None:
    # "6 ja 8 §:ssä" expands to two section targets at the same document
    # position → both are equally "the most recent" → ambiguous, none picked.
    text = "6 ja 8 §:ssä säädetään. Mainitussa pykälässä tarkoitettu."
    refs = recognize_anaphoric_refs(text, statute_id="1/2020")
    assert len(refs) == 1
    r = refs[0]
    assert r.anaphor_status is AnaphorStatus.AMBIGUOUS
    assert r.mention.target_provision_ref is None
    assert {c.serialized() for c in r.candidates} == {"1/2020/6", "1/2020/8"}
    assert r.finding is not None
    assert set(r.finding.candidate_target_ids) == {"1/2020/6", "1/2020/8"}


# --- RESOLUTION RULE: nearest antecedent wins, kind-matched -----------------


def test_anaphor_binds_to_nearest_not_earliest_act() -> None:
    text = (
        "Jätelaissa säädetään jätteistä. "
        "Luonnonsuojelulaissa säädetään luonnosta. "
        "Mainitun lain mukaan toimitaan."
    )
    refs = recognize_anaphoric_refs(text)
    assert len(refs) == 1
    assert refs[0].anaphor_status is AnaphorStatus.RESOLVED
    assert refs[0].mention.target_provision_ref is not None
    # Nearest preceding act is luonnonsuojelulaki, NOT the earlier jätelaki.
    assert (
        refs[0].mention.target_provision_ref.statute_id
        == "fi-name:luonnonsuojelulaki"
    )
    assert refs[0].mention.target_provision_ref.statute_id != "fi-name:jätelaki"


# --- GUARD: no determiner stem → no work ------------------------------------


def test_guard_returns_empty_without_determiner() -> None:
    assert recognize_anaphoric_refs("Yleissopimus tuli voimaan vuonna 2020.") == []


# --- HONESTY: cursor-fallback offset is signalled + downgrades EXACT ---------


def test_locate_offset_signals_located_vs_fallback() -> None:
    """_locate_offset reports whether the surface was really found or a fallback."""
    text = "alpha beta gamma"
    # found at/after cursor
    assert _locate_offset(text, "beta", 0) == (6, True)
    # found on whole-text re-search when cursor is past it
    assert _locate_offset(text, "beta", 10) == (6, True)
    # not present at all -> cursor fallback, located=False
    assert _locate_offset(text, "zzz", 4) == (4, False)
    # empty surface -> fallback, located=False
    assert _locate_offset(text, "", 3) == (3, False)


def test_downgrade_for_approximate_floors_at_exact_only() -> None:
    """An EXACT inheritance is lowered to APPROXIMATE; lower levels are unchanged."""
    assert (
        _downgrade_for_approximate(CiteConfidence.EXACT)
        is CiteConfidence.APPROXIMATE
    )
    # already not-parsed-exact -> left as-is (never RAISED)
    assert (
        _downgrade_for_approximate(CiteConfidence.STATUTE_ONLY)
        is CiteConfidence.STATUTE_ONLY
    )
    assert (
        _downgrade_for_approximate(CiteConfidence.APPROXIMATE)
        is CiteConfidence.APPROXIMATE
    )
