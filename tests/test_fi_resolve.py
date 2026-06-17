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
        valid_at_interval=(None, None),
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
