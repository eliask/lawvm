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
    assert rr.status is ResolutionStatus.RESOLVED
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
    assert rr.status is ResolutionStatus.AMBIGUOUS
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
    assert rr.status is ResolutionStatus.RESOLVED
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
    assert rr.status is ResolutionStatus.RESOLVED
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
    assert rr.status is ResolutionStatus.AMBIGUOUS
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
    assert rr.status is ResolutionStatus.AMBIGUOUS
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
    assert rr.status is ResolutionStatus.AMBIGUOUS
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
    assert rr.status is ResolutionStatus.AMBIGUOUS
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
    assert rr.status is ResolutionStatus.AMBIGUOUS
    assert set(rr.candidates) == {"364/1963", "1224/2004"}


def test_fi_name_miss_is_statute_only_not_silent_resolve() -> None:
    """An unknown fi-name -> statute_only (coverage gap), never a silent resolve."""
    reg = _statute_registry()
    [rr] = resolve_mentions(
        [_mention("fi-name:tuntematonlaki")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.status is ResolutionStatus.STATUTE_ONLY
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
    assert rr.status is ResolutionStatus.RESOLVED
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
    assert rr.status is ResolutionStatus.AMBIGUOUS
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
    assert rr.status is ResolutionStatus.STATUTE_ONLY
    assert rr.work_id is None
    assert rr.candidates == ()


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
    assert rr.status is ResolutionStatus.UNCHANGED
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
    assert rr.status is ResolutionStatus.UNCHANGED


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
    assert rr.status is ResolutionStatus.OPEN
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
    assert [r.status for r in results] == [
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
