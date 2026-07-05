"""Tests for the reference-resolution PROJECTION.

Covers ``src/lawvm/finland/references/resolve.py``: the downstream stage that
resolves UNRESOLVED-by-identity placeholder mentions
(``fi-name:<name>`` / ``eu-nickname:<surface>``) against the statute-name and
EU-nickname registries into :class:`ResolvedReference` records.

The core tests use HAND-BUILT small registries (no farchive dependency); a
single test exercises ``build_default_registries`` only at the level of "it
returns the right shapes" without asserting a heavy corpus build.
"""

from __future__ import annotations

import datetime as dt

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.references.registries import eu_nickname
from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    build_registry,
)
from lawvm.finland.references.resolve import (
    ResolutionStatus,
    ResolvedReference,
    resolve_mentions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mention(
    statute_id: str,
    *,
    cite_kind: CiteKind = CiteKind.CROSS_STATUTE,
    cite_confidence: CiteConfidence = CiteConfidence.STATUTE_ONLY,
    section_label: str = "",
    target: bool = True,
    surface_text: str = "",
    valid_at_interval: tuple[dt.date | None, dt.date | None] = (None, None),
) -> ReferenceMention:
    """Build a minimal ReferenceMention for resolution tests."""
    tgt = ProvisionRef(statute_id=statute_id, section_label=section_label) if target else None
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="3"),
        target_provision_ref=tgt,
        cite_kind=cite_kind,
        cite_confidence=cite_confidence,
        phrase_lemma="statute_name_head",
        source_span=None,
        valid_at_interval=valid_at_interval,
        edge_subtype=None,
        surface_text=surface_text,
    )


def _statute_registry():
    """A small hand-built statute-name registry.

    * ``luonnonsuojelulaki`` -> exactly one act (resolves single).
    * ``ympäristönsuojelulaki`` -> two temporal versions (ambiguous when no
      ``as_of`` filter narrows it).
    """
    return build_registry(
        [
            StatuteNameEntry(
                statute_id="1096/1996",
                canonical_title="Luonnonsuojelulaki",
            ),
            StatuteNameEntry(
                statute_id="86/2000",
                canonical_title="Ympäristönsuojelulaki",
                valid_from=dt.date(2000, 1, 1),
                valid_to=dt.date(2014, 9, 1),
            ),
            StatuteNameEntry(
                statute_id="527/2014",
                canonical_title="Ympäristönsuojelulaki",
                valid_from=dt.date(2014, 9, 1),
                valid_to=None,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# fi-name: resolution
# ---------------------------------------------------------------------------


def test_fi_name_single_resolves_and_rewrites_id() -> None:
    """A fi-name placeholder with one registry candidate -> resolved + work_id.

    The placeholder id is rewritten to the real statute id in a NEW mention; the
    input mention is not mutated.
    """
    reg = _statute_registry()
    original = _mention("fi-name:luonnonsuojelulaki", section_label="5")
    [rr] = resolve_mentions(
        [original], statute_registry=reg, eu_registry=eu_nickname
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1096/1996"
    assert rr.candidates == ("1096/1996",)
    assert rr.finding is None
    # Rewritten in a NEW mention; section path preserved; confidence promoted.
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "1096/1996"
    assert rr.mention.target_provision_ref.section_label == "5"
    assert rr.mention.cite_confidence is CiteConfidence.EXACT
    # The input mention is untouched (no mutation).
    assert original.target_provision_ref is not None
    assert original.target_provision_ref.statute_id == "fi-name:luonnonsuojelulaki"


def test_fi_name_multiple_temporal_versions_is_ambiguous() -> None:
    """Two temporal versions, no as_of filter -> ambiguous, both candidates, no pick."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:ympäristönsuojelulaki", surface_text="ympäristönsuojelulaissa")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert rr.work_id is None
    assert set(rr.candidates) == {"86/2000", "527/2014"}
    # Never picks: target id stays the placeholder, confidence becomes AMBIGUOUS.
    assert rr.mention.cite_confidence is CiteConfidence.AMBIGUOUS
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "fi-name:ympäristönsuojelulaki"
    # A finding is emitted naming every candidate.
    assert rr.finding is not None
    assert set(rr.finding.candidate_target_ids) == {"86/2000", "527/2014"}
    assert rr.finding.rule_id == "fi_ref_resolve_ambiguous_name"


def test_fi_name_as_of_filter_narrows_to_single() -> None:
    """An as_of inside one version's window narrows the ambiguity to resolved."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:ympäristönsuojelulaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
        as_of=dt.date(2020, 6, 1),
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "527/2014"


def _open_ended_registry():
    """A registry shaped like the real corpus artifact.

    Each version carries a ``valid_from`` and ``valid_to=None`` (the farchive
    exposes only one open-ended PIT per statute), so an act with two temporal
    versions is ``multiple`` over the whole timeline and an ``as_of`` BEFORE the
    later version's ``valid_from`` narrows it to the earlier one.
    """
    return build_registry(
        [
            StatuteNameEntry(
                statute_id="364/1963",
                canonical_title="Sairausvakuutuslaki",
                valid_from=dt.date(1963, 7, 4),
            ),
            StatuteNameEntry(
                statute_id="1224/2004",
                canonical_title="Sairausvakuutuslaki",
                valid_from=dt.date(2004, 12, 21),
            ),
        ]
    )


def test_mention_validity_interval_narrows_multitemporal_to_single() -> None:
    """A mention interval inside ONE version's window -> resolved to that version.

    ``use_mention_validity`` threads the mention's ``valid_at_interval`` START as
    the per-mention as-of, so a multi-version act name whose citing text was valid
    in (e.g.) 1990 resolves to the version then in force — NOT the latest one, and
    NOT via the citing statute's enactment year.
    """
    reg = _open_ended_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "fi-name:sairausvakuutuslaki",
                valid_at_interval=(dt.date(1990, 1, 1), None),
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
        use_mention_validity=True,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "364/1963"
    assert rr.finding is None


def test_open_interval_stays_ambiguous_under_mention_validity() -> None:
    """SAME name with an open (None) interval start -> stays ambiguous (no guess)."""
    reg = _open_ended_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "fi-name:sairausvakuutuslaki",
                valid_at_interval=(None, None),
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
        use_mention_validity=True,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert rr.work_id is None
    assert set(rr.candidates) == {"364/1963", "1224/2004"}
    assert rr.finding is not None


def test_mention_interval_not_disambiguating_stays_ambiguous() -> None:
    """An interval start AFTER both versions' windows -> both survive -> ambiguous."""
    reg = _open_ended_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "fi-name:sairausvakuutuslaki",
                valid_at_interval=(dt.date(2020, 1, 1), None),
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
        use_mention_validity=True,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert rr.work_id is None
    assert set(rr.candidates) == {"364/1963", "1224/2004"}


def test_mention_interval_before_all_versions_stays_ambiguous_not_miss() -> None:
    """An interval start BEFORE every version -> ambiguous over all, never STATUTE_ONLY.

    An as-of filter that excludes EVERY version is not a coverage gap (the name IS
    known); the act stays ambiguous over the full candidate set rather than being
    downgraded to a registry miss on a guessed instant.
    """
    reg = _open_ended_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "fi-name:sairausvakuutuslaki",
                valid_at_interval=(dt.date(1900, 1, 1), None),
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
        use_mention_validity=True,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert set(rr.candidates) == {"364/1963", "1224/2004"}


def test_mention_validity_off_by_default_keeps_whole_timeline() -> None:
    """Without use_mention_validity, a populated interval is ignored (whole timeline)."""
    reg = _open_ended_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "fi-name:sairausvakuutuslaki",
                valid_at_interval=(dt.date(1990, 1, 1), None),
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert set(rr.candidates) == {"364/1963", "1224/2004"}


def test_explicit_as_of_overrides_mention_validity() -> None:
    """An explicit batch as_of takes precedence over each mention's interval."""
    reg = _open_ended_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "fi-name:sairausvakuutuslaki",
                valid_at_interval=(dt.date(1990, 1, 1), None),
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
        as_of=dt.date(2010, 6, 1),
        use_mention_validity=True,
    )
    # 2010 covers BOTH versions -> ambiguous, even though the interval start (1990)
    # alone would have resolved to the 1963 version.
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert set(rr.candidates) == {"364/1963", "1224/2004"}


def test_fi_name_miss_is_statute_only_not_silent_resolve() -> None:
    """An unknown fi-name -> statute_only (coverage gap), never a silent resolve."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:tuntematonlaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert rr.work_id is None
    assert rr.candidates == ()
    assert rr.finding is None
    # Placeholder left intact — not silently rewritten.
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "fi-name:tuntematonlaki"


# ---------------------------------------------------------------------------
# eu-nickname: resolution
# ---------------------------------------------------------------------------


def test_eu_nickname_single_resolves_to_celex() -> None:
    """A known single-CELEX nickname -> resolved, work_id = celex:<CELEX>."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "eu-nickname:teollisuuspäästödirektiivi",
                cite_kind=CiteKind.EU,
                section_label="33",
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "celex:32010L0075"
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "celex:32010L0075"
    assert rr.mention.target_provision_ref.section_label == "33"
    assert rr.mention.cite_confidence is CiteConfidence.EXACT


def test_eu_nickname_multiple_is_ambiguous() -> None:
    """A deliberately ambiguous nickname (jätedirektiivi) -> ambiguous, both CELEX."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("eu-nickname:jätedirektiivi", cite_kind=CiteKind.EU)],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert rr.work_id is None
    assert set(rr.candidates) == {"celex:32008L0098", "celex:32006L0012"}
    assert rr.finding is not None
    assert set(rr.finding.candidate_target_ids) == {
        "celex:32008L0098",
        "celex:32006L0012",
    }


def test_eu_nickname_miss_is_statute_only() -> None:
    """An unknown nickname-shaped surface -> statute_only (id pending)."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("eu-nickname:keksittydirektiivi", cite_kind=CiteKind.EU)],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert rr.work_id is None
    assert rr.candidates == ()


# ---------------------------------------------------------------------------
# fi-name -> EU-nickname fallback (a Finnish-shaped citation of an EU instrument)
# ---------------------------------------------------------------------------
#
# An EU regulation is often cited in Finnish prose by a Finnish-shaped ``-asetus``
# nickname (``sivutuoteasetuksen``); the by-name lane types that ``fi-name:`` even
# though it denotes an EU instrument. When the statute-name registry misses such a
# placeholder, the resolver falls back to the EU-nickname registry BEFORE declaring
# a coverage gap — statute-first (a real Finnish act is never shadowed), fail-loud.


def test_fi_name_miss_falls_back_to_eu_nickname_single() -> None:
    """A fi-name miss that IS a known EU nickname -> resolved to its CELEX."""
    reg = _statute_registry()  # knows no ``sivutuoteasetus`` (a Finnish miss)
    [rr] = resolve_mentions(
        [_mention("fi-name:sivutuoteasetus", section_label="3")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "celex:32009R1069"
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "celex:32009R1069"
    assert rr.mention.target_provision_ref.section_label == "3"
    # A jurisdiction-flipped EU-nickname fallback is a BEST-EFFORT resolution
    # (the statute registry missed, the EU one hit on a nickname), so it is stamped
    # APPROXIMATE — not laundered to EXACT — to stay distinguishable downstream.
    assert rr.mention.cite_confidence is CiteConfidence.APPROXIMATE
    assert rr.mention.phrase_lemma == "eu_nickname_fallback_from_fi_name"


def test_fi_name_miss_eu_fallback_multiple_is_ambiguous() -> None:
    """A fi-name miss that is an ambiguous EU nickname -> ambiguous, all CELEX listed."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:tietosuojadirektiivi")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert rr.work_id is None
    assert set(rr.candidates) == {"celex:31995L0046", "celex:32016L0680"}
    assert rr.finding is not None


def test_fi_name_miss_not_an_eu_nickname_stays_statute_only() -> None:
    """A fi-name miss unknown to BOTH registries stays a STATUTE_ONLY coverage gap."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:tuntematonlaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert rr.work_id is None
    # The placeholder is left intact — no EU CELEX was fabricated for it.
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "fi-name:tuntematonlaki"


def test_fi_name_hit_not_shadowed_by_eu_fallback() -> None:
    """A fi-name that resolves as a Finnish statute is NOT diverted to the EU lane.

    The EU fallback fires ONLY on a statute-registry miss, so a name the statute
    registry knows resolves to its Finnish id even if an EU nickname existed.
    """
    reg = _statute_registry()  # knows ``luonnonsuojelulaki`` -> 1096/1996
    [rr] = resolve_mentions(
        [_mention("fi-name:luonnonsuojelulaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1096/1996"
    assert rr.mention.phrase_lemma != "eu_nickname_fallback_from_fi_name"


# ---------------------------------------------------------------------------
# pass-through cases
# ---------------------------------------------------------------------------


def test_explicit_id_mention_is_unchanged() -> None:
    """A mention with a real explicit id -> unchanged pass-through, no registry call."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "646/2011",
                cite_confidence=CiteConfidence.EXACT,
                section_label="3",
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.UNCHANGED
    assert rr.work_id == "646/2011"
    assert rr.candidates == ("646/2011",)
    assert rr.finding is None
    # Mention passes through unmodified.
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "646/2011"
    assert rr.mention.cite_confidence is CiteConfidence.EXACT


def test_internal_reference_is_unchanged() -> None:
    """An internal self-reference (empty statute id) -> unchanged pass-through."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [
            _mention(
                "",
                cite_kind=CiteKind.INTERNAL,
                cite_confidence=CiteConfidence.EXACT,
                section_label="5",
            )
        ],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.UNCHANGED


def test_open_mention_passes_through_open() -> None:
    """An OPEN (vague catch-all) mention -> status=open, targetless by construction."""
    reg = _statute_registry()
    open_mention = _mention(
        "",
        cite_confidence=CiteConfidence.OPEN,
        target=False,
        surface_text="muussa laissa säädetään",
    )
    [rr] = resolve_mentions(
        [open_mention], statute_registry=reg, eu_registry=eu_nickname
    )
    assert rr.resolution_status is ResolutionStatus.OPEN
    assert rr.work_id is None
    assert rr.candidates == ()
    assert rr.finding is None
    assert rr.mention.target_provision_ref is None


def test_order_and_mixed_batch_preserved() -> None:
    """resolve_mentions preserves input order across a mixed batch."""
    reg = _statute_registry()
    batch = [
        _mention("fi-name:luonnonsuojelulaki"),
        _mention("646/2011", cite_confidence=CiteConfidence.EXACT),
        _mention("fi-name:tuntematonlaki"),
    ]
    results = resolve_mentions(batch, statute_registry=reg, eu_registry=eu_nickname)
    assert [r.resolution_status for r in results] == [
        ResolutionStatus.RESOLVED,
        ResolutionStatus.UNCHANGED,
        ResolutionStatus.STATUTE_ONLY,
    ]


def test_resolved_reference_is_frozen() -> None:
    """ResolvedReference is a frozen dataclass."""
    import dataclasses

    assert dataclasses.is_dataclass(ResolvedReference)
    params = ResolvedReference.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen is True


# ---------------------------------------------------------------------------
# In-statute name->id anaphora (a bare repeat of an earlier id-anchored name)
# ---------------------------------------------------------------------------
#
# Mirrors the N4a corpus case (2025/943): ``yhteistoimintalain (1333/2021) 5
# luvussa`` establishes the name->id binding; a later bare ``yhteistoimintalain
# 5 §:ssä`` repeat resolves to ``1333/2021`` even though the colloquial name is
# not a registry head. The binding is offset-gated (anaphora points backward) and
# ambiguity-safe (a name bound to >1 id stays statute_only).


from lawvm.core.reference_mention import SourceSpan  # noqa: E402
from lawvm.finland.references.resolve import (  # noqa: E402
    build_name_id_anaphora_table,
)


def _id_anchored(statute_id: str, *, surface: str, offset: int) -> ReferenceMention:
    """An id-anchored cross-statute citation (the anaphora antecedent)."""
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="999/2025"),
        target_provision_ref=ProvisionRef(statute_id=statute_id),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="plain_text",
        source_span=SourceSpan(source_file="999/2025", byte_offset=offset, byte_len=len(surface)),
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def _bare_name(name: str, *, surface: str, offset: int | None, section_label: str = "") -> ReferenceMention:
    """A bare by-name repeat (the anaphor) with an optional span/offset."""
    span = (
        SourceSpan(source_file="999/2025", byte_offset=offset, byte_len=len(surface))
        if offset is not None
        else None
    )
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="999/2025"),
        target_provision_ref=ProvisionRef(statute_id=f"fi-name:{name}", section_label=section_label),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.STATUTE_ONLY,
        phrase_lemma="statute_name_head",
        source_span=span,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def test_name_id_anaphora_bare_repeat_resolves_to_earlier_id() -> None:
    """A bare name repeat AFTER an id-anchored same-name citation -> that id."""
    reg = _statute_registry()
    batch = [
        _id_anchored("55/2001", surface="työsopimuslain (55/2001)", offset=100),
        _bare_name("työsopimuslaki", surface="työsopimuslain 3 §:ssä", offset=400, section_label="3"),
    ]
    results = resolve_mentions(batch, statute_registry=reg, eu_registry=eu_nickname)
    # antecedent passes through UNCHANGED (already a concrete id)
    assert results[0].resolution_status is ResolutionStatus.UNCHANGED
    # bare repeat resolves to the antecedent's id via name anaphora
    assert results[1].resolution_status is ResolutionStatus.RESOLVED
    assert results[1].work_id == "55/2001"
    tgt = results[1].mention.target_provision_ref
    assert tgt is not None and tgt.statute_id == "55/2001" and tgt.section_label == "3"
    assert results[1].mention.phrase_lemma == "name_id_anaphora_local_binding"


def test_name_id_anaphora_use_before_binding_stays_statute_only() -> None:
    """A bare name BEFORE the id-anchored citation does not resolve (backward only)."""
    reg = _statute_registry()
    batch = [
        _bare_name("työsopimuslaki", surface="työsopimuslain 3 §:ssä", offset=50, section_label="3"),
        _id_anchored("55/2001", surface="työsopimuslain (55/2001)", offset=100),
    ]
    results = resolve_mentions(batch, statute_registry=reg, eu_registry=eu_nickname)
    assert results[0].resolution_status is ResolutionStatus.STATUTE_ONLY
    tgt = results[0].mention.target_provision_ref
    assert tgt is not None and tgt.statute_id == "fi-name:työsopimuslaki"


def test_name_id_anaphora_ambiguous_two_ids_stays_statute_only() -> None:
    """A name bound to TWO distinct ids in one statute is ambiguous -> not picked."""
    reg = _statute_registry()
    batch = [
        _id_anchored("55/2001", surface="työsopimuslain (55/2001)", offset=100),
        _id_anchored("320/1970", surface="työsopimuslain (320/1970)", offset=200),
        _bare_name("työsopimuslaki", surface="työsopimuslain 3 §:ssä", offset=400, section_label="3"),
    ]
    results = resolve_mentions(batch, statute_registry=reg, eu_registry=eu_nickname)
    assert results[2].resolution_status is ResolutionStatus.STATUTE_ONLY
    tgt = results[2].mention.target_provision_ref
    assert tgt is not None and tgt.statute_id == "fi-name:työsopimuslaki"


def test_name_id_anaphora_generic_head_establishes_no_binding() -> None:
    """A generic ``lain (id)`` head (no distinctive title) is not a name antecedent."""
    reg = _statute_registry()
    batch = [
        _id_anchored("335/2007", surface="lain (335/2007)", offset=100),
        _bare_name("työsopimuslaki", surface="työsopimuslain 3 §:ssä", offset=400, section_label="3"),
    ]
    results = resolve_mentions(batch, statute_registry=reg, eu_registry=eu_nickname)
    # the bare repeat has no in-statute name binding (lain != työsopimuslaki) and
    # the registry does not know the colloquial name -> statute_only (fail-loud)
    assert results[1].resolution_status is ResolutionStatus.STATUTE_ONLY


def test_name_id_anaphora_no_span_use_stays_statute_only() -> None:
    """A bare repeat with no span (no offset) cannot be ordered -> not resolved."""
    reg = _statute_registry()
    batch = [
        _id_anchored("55/2001", surface="työsopimuslain (55/2001)", offset=100),
        _bare_name("työsopimuslaki", surface="työsopimuslain 3 §:ssä", offset=None, section_label="3"),
    ]
    results = resolve_mentions(batch, statute_registry=reg, eu_registry=eu_nickname)
    assert results[1].resolution_status is ResolutionStatus.STATUTE_ONLY


def test_name_id_anaphora_can_be_disabled() -> None:
    """resolve_name_id_anaphora=False restores the prior registry-only routing."""
    reg = _statute_registry()
    batch = [
        _id_anchored("55/2001", surface="työsopimuslain (55/2001)", offset=100),
        _bare_name("työsopimuslaki", surface="työsopimuslain 3 §:ssä", offset=400, section_label="3"),
    ]
    results = resolve_mentions(
        batch, statute_registry=reg, eu_registry=eu_nickname, resolve_name_id_anaphora=False
    )
    assert results[1].resolution_status is ResolutionStatus.STATUTE_ONLY


def test_build_name_id_anaphora_table_keys_recovered_name() -> None:
    """The table keys on the recovered normalized name, earliest binding offset."""
    table = build_name_id_anaphora_table(
        [
            _id_anchored("55/2001", surface="työsopimuslain (55/2001)", offset=300),
            _id_anchored("55/2001", surface="työsopimuslain (55/2001)", offset=100),
        ]
    )
    # earliest offset kept; a use at 200 (>=100) resolves
    assert table.resolve("työsopimuslaki", use_offset=200) == "55/2001"
    # a use at 50 (<100) does not (binding does not precede)
    assert table.resolve("työsopimuslaki", use_offset=50) is None


def test_name_id_anaphora_compound_modifier_not_conflated() -> None:
    """A multi-word-modifier compound name (whose surface CARRIES the modifier)
    must not bind under the truncated last-conjunct key.

    ``maatalousyrittäjien tapaturmavakuutuslain (1026/81)`` is a DIFFERENT act
    from the plain ``tapaturmavakuutuslaki``; the by-name head regex captures only
    ``tapaturmavakuutuslain``, so binding the truncated key would mis-resolve a
    later plain ``tapaturmavakuutuslain`` repeat to the farmers' act. The recovery
    fail-loud rejects the truncated capture (surface != whole name part).
    """
    from lawvm.finland.references.resolve import _recover_name_key

    # surface carries the modifier -> recognized surface is shorter -> rejected
    assert (
        _recover_name_key("maatalousyrittäjien tapaturmavakuutuslain (1026/81)")
        is None
    )
    # plain name (no dropped modifier) -> accepted
    assert (
        _recover_name_key("tapaturmavakuutuslain (608/1948)")
        == "tapaturmavakuutuslaki"
    )
    # hyphen-coordinated compound is captured WHOLE -> accepted
    assert (
        _recover_name_key("perintö- ja lahjaverolain (378/1940)")
        == "perintö- ja lahjaverolaki"
    )


def test_name_id_anaphora_beats_registry_with_explicit_source_id() -> None:
    """When a name's id is stated explicitly in-source, the bare repeat resolves
    to THAT id even if the registry would pick a different (default) version.

    Models the 1996/448 corpus case: ``opintotukilaissa (28/72)`` explicitly
    cites the OLD act; the in-statute id binding wins over the registry's current
    pick. The anaphora honors the source's own id, not a name-based guess.
    """
    reg = build_registry(
        [StatuteNameEntry(statute_id="65/1994", canonical_title="Opintotukilaki")]
    )
    batch = [
        _id_anchored("28/1972", surface="opintotukilain (28/1972)", offset=100),
        _bare_name("opintotukilaki", surface="opintotukilain 3 §:ssä", offset=400, section_label="3"),
    ]
    results = resolve_mentions(batch, statute_registry=reg, eu_registry=eu_nickname)
    assert results[1].resolution_status is ResolutionStatus.RESOLVED
    assert results[1].work_id == "28/1972"


# ---------------------------------------------------------------------------
# Content-word-set fallback through the resolution projection
# ---------------------------------------------------------------------------


def test_fi_name_content_word_set_fallback_resolves_inflection_diff() -> None:
    """A descriptive cite missing the exact key resolves via the content-word set.

    Official ``Laki maatalousyrittäjien luopumiskorvauksesta`` (sg) cited as the
    plural ``laki maatalousyrittäjien luopumiskorvauksista``: the exact index
    misses, the content-word-set fallback resolves to the unique id, and the
    rewritten mention carries the fallback provenance tag.
    """
    reg = build_registry(
        [
            StatuteNameEntry(
                "1992/1330", "Laki maatalousyrittäjien luopumiskorvauksesta"
            )
        ]
    )
    m = _mention(
        "fi-name:laki maatalousyrittäjien luopumiskorvauksista",
        surface_text="maatalousyrittäjien luopumiskorvauksista annetun lain",
    )
    [rr] = resolve_mentions([m], statute_registry=reg, eu_registry=eu_nickname)
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1992/1330"
    # An inflection-robust content-word-set match is a BEST-EFFORT resolution (the
    # cite's premodifier inflection differed from the official title), so it is
    # stamped APPROXIMATE — not laundered to EXACT.
    assert rr.mention.cite_confidence is CiteConfidence.APPROXIMATE
    assert (
        rr.mention.phrase_lemma == "statute_name_content_word_set_fallback"
    )


def test_fi_name_content_word_set_multiple_is_ambiguous() -> None:
    """Two acts sharing a content-word set -> ambiguous, a finding, no pick."""
    reg = build_registry(
        [
            StatuteNameEntry("1990/100", "Laki valtiontalouden tarkastuksesta"),
            StatuteNameEntry("1993/267", "Laki valtiontalouden tarkastuksessa"),
        ]
    )
    m = _mention("fi-name:laki valtiontalouden tarkastuksista")
    [rr] = resolve_mentions([m], statute_registry=reg, eu_registry=eu_nickname)
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert rr.work_id is None
    assert set(rr.candidates) == {"1990/100", "1993/267"}
    assert rr.finding is not None


def test_fi_name_content_word_set_garbage_complement_stays_statute_only() -> None:
    """A garbage complement is NOT resolved by the fallback (no fabrication)."""
    reg = build_registry(
        [StatuteNameEntry("2018/1", "Laki finanssivalvonnan järjestämisestä")]
    )
    m = _mention("fi-name:laki kun finanssivalvonnasta")
    [rr] = resolve_mentions([m], statute_registry=reg, eu_registry=eu_nickname)
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert rr.work_id is None


def test_fi_name_content_word_set_does_not_override_exact_match() -> None:
    """An exact-key resolution is NOT changed by the content-word-set fallback.

    The fallback is consulted only AFTER the exact lookup misses; a name that
    resolves exactly keeps that resolution unchanged.
    """
    reg = build_registry(
        [
            StatuteNameEntry(
                "1992/1330", "Laki maatalousyrittäjien luopumiskorvauksesta"
            )
        ]
    )
    # exact (sg) surface — resolves via the normal index, NOT the fallback tag
    m = _mention("fi-name:laki maatalousyrittäjien luopumiskorvauksesta")
    [rr] = resolve_mentions([m], statute_registry=reg, eu_registry=eu_nickname)
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1992/1330"
    assert rr.mention.phrase_lemma != "statute_name_content_word_set_fallback"


def test_trailing_period_title_resolves_inflected_cite_through_projection() -> None:
    """A period-stored title resolves a period-free inflected cite end-to-end."""
    reg = build_registry([StatuteNameEntry("1960/465", "Palolaki.")])
    m = _mention("fi-name:palolaki", section_label="26")
    [rr] = resolve_mentions([m], statute_registry=reg, eu_registry=eu_nickname)
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1960/465"


def test_content_word_set_single_out_of_window_stays_resolved_approximate() -> None:
    """A content-word single re-widened past an as-of window stays RESOLVED APPROXIMATE."""
    reg = build_registry(
        [
            StatuteNameEntry(
                "1992/1330",
                "Laki maatalousyrittäjien luopumiskorvauksesta",
                valid_from=dt.date(1992, 1, 1),
                valid_to=dt.date(2000, 1, 1),
            )
        ]
    )
    m = _mention(
        "fi-name:laki maatalousyrittäjien luopumiskorvauksista",
        surface_text="maatalousyrittäjien luopumiskorvauksista annetun lain",
    )
    # as_of AFTER the window: exact/content lookups both miss, re-widen to whole
    # timeline yields the single act -> resolved, but APPROXIMATE (out of window).
    [rr] = resolve_mentions(
        [m], statute_registry=reg, eu_registry=eu_nickname, as_of=dt.date(2010, 1, 1)
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1992/1330"
    assert rr.mention.cite_confidence is CiteConfidence.APPROXIMATE


# ---------------------------------------------------------------------------
# Multi-version disambiguation (honest, APPROXIMATE, gated OFF by default)
# ---------------------------------------------------------------------------


def _multi_version_registry():
    """A registry with an act repealed-and-re-enacted under the same name.

    ``ympäristönsuojelulaki`` has an OLD version (closed window) and a LIVE version
    (``valid_to=None``). A ``laki`` cite of it is multi-version over the whole
    timeline; exactly one candidate is still in force.
    """
    return build_registry(
        [
            StatuteNameEntry(
                statute_id="86/2000",
                canonical_title="Ympäristönsuojelulaki",
                valid_from=dt.date(2000, 1, 1),
                valid_to=dt.date(2014, 9, 1),
            ),
            StatuteNameEntry(
                statute_id="527/2014",
                canonical_title="Ympäristönsuojelulaki",
                valid_from=dt.date(2014, 9, 1),
                valid_to=None,
            ),
        ]
    )


def test_multi_version_stays_ambiguous_by_default() -> None:
    """Without the flag, a multi-version name stays AMBIGUOUS (never picked)."""
    reg = _multi_version_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:ympäristönsuojelulaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert set(rr.candidates) == {"86/2000", "527/2014"}


def test_multi_version_live_pick_resolves_approximate() -> None:
    """With the flag, the still-in-force version is picked APPROXIMATE, others rejected."""
    reg = _multi_version_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:ympäristönsuojelulaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
        disambiguate_multi_version=True,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "527/2014"
    # A heuristic pick among multiple -> APPROXIMATE, never laundered EXACT.
    assert rr.mention.cite_confidence is CiteConfidence.APPROXIMATE
    assert rr.mention.phrase_lemma == "statute_name_as_of_live_version_pick"
    assert rr.rejected_candidates == ("86/2000",)
    assert rr.finding is None


def test_multi_version_no_unique_live_stays_ambiguous_even_with_flag() -> None:
    """Two CLOSED versions (no live) stay AMBIGUOUS even with the flag (no guess)."""
    reg = build_registry(
        [
            StatuteNameEntry(
                "1988/517", "Radiolaki",
                valid_from=dt.date(1988, 6, 10), valid_to=dt.date(2002, 1, 1),
            ),
            StatuteNameEntry(
                "2001/1015", "Radiolaki",
                valid_from=dt.date(2001, 11, 16), valid_to=dt.date(2015, 1, 1),
            ),
        ]
    )
    [rr] = resolve_mentions(
        [_mention("fi-name:radiolaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
        disambiguate_multi_version=True,
    )
    assert rr.resolution_status is ResolutionStatus.AMBIGUOUS
    assert set(rr.candidates) == {"1988/517", "2001/1015"}


def test_multi_version_law_vs_decree_head_filter() -> None:
    """A ``laki`` cite drops an ``asetus`` homonym, resolving to the single law.

    Both candidates are still in force, so the as-of-live signal ALONE is ambiguous
    (two live); the cited head ``laki`` uniquely selects the law over the decree.
    Tested directly on ``_disambiguate_multi_version`` (a real registry does not
    collide a law and a decree under one inflected key).
    """
    from lawvm.finland.references.registries.statute_name import Candidate
    from lawvm.finland.references.resolve import _disambiguate_multi_version

    cands = (
        Candidate("100/1990", "Maankäyttölaki", None, None),
        Candidate("200/1990", "Maankäyttöasetus", None, None),
    )
    # cited head "laki" -> selects the law only, provenance = head pick.
    picked = _disambiguate_multi_version("maankäyttölaki", cands)
    assert picked is not None
    assert picked[0] == "100/1990"
    assert picked[1] == "statute_name_law_decree_head_pick"
    # cited head "asetus" -> selects the decree only.
    picked_dec = _disambiguate_multi_version("maankäyttöasetus", cands)
    assert picked_dec is not None
    assert picked_dec[0] == "200/1990"


def test_disambiguate_two_live_no_head_signal_stays_none() -> None:
    """Two live candidates with the SAME head -> no signal singles one out -> None."""
    from lawvm.finland.references.registries.statute_name import Candidate
    from lawvm.finland.references.resolve import _disambiguate_multi_version

    cands = (
        Candidate("100/1990", "Foolaki", None, None),
        Candidate("200/1995", "Foolaki", None, None),
    )
    # both live, both laki -> neither head nor live-uniqueness picks -> ambiguous
    assert _disambiguate_multi_version("foolaki", cands) is None
