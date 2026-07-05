"""Conformance fixtures for the reference RESOLUTION projection.

This is the resolution-layer counterpart to the EXTRACTION-layer conformance
corpus in ``lawvm.finland.conformance_corpus.refs`` (which drives minimal AKN
XML through ReferenceMention extraction). The extraction corpus is a DIFFERENT
framework — XML bytes in, column-level mention assertions out — and is
deliberately left untouched here.

This module pins, for the RESOLUTION pipeline
(``lawvm.finland.references.resolve.resolve_mentions``), one synthetic witness
per (determinism-tier × resolution-status) cell of the §7 verification matrix in
``notes/FI_REFERENCE_CATALOGUE.md``:

    Tier \\ Status | resolved | statute-only | ambiguous | open | broken
    T1            |   ✅      |     ✅       |   —       |  —   |  ✅(*)
    T2            |   ✅      |     ✅       |   ✅      |  —   |  —
    T3            |   —      |     —        |   —       |  ✅  |  —

The resolution layer's :class:`ResolutionStatus` is a richer ladder than the
catalogue's six surface statuses. The mapping used here:

* A catalogue **resolved** at T1 is an EXPLICIT-ID cite that is already pinned
  upstream (no placeholder, no registry call); at the resolve layer this is a
  pass-through with status ``UNCHANGED`` (``work_id`` = the upstream id). The
  ``RESOLVED`` status proper is produced only by an ACTUAL registry / local
  defined-term resolution of an unresolved-by-identity placeholder (a T2 act).
  Both are covered as distinct cells below.
* **broken** is BITEMPORAL (§0.4): the genuine broken *determination* is made by
  ``references.broken_detection.detect_broken`` against the consolidated store,
  NOT by ``resolve_mentions``. It is covered there (``test_fi_broken_detection``)
  and is documented-as-covered-elsewhere — the cell is NOT faked at the resolve
  layer. What ``resolve_mentions`` *can* do is PASS THROUGH a mention that
  already arrived typed ``CiteConfidence.BROKEN`` (a projection-phase input); the
  pass-through itself is exercised below so every emittable ``ResolutionStatus``
  member has a witness.

Discipline: synthetic, deterministic, ARCHIVE-FREE. Every registry used is a
tiny in-test :class:`StatuteNameRegistry` built via ``build_registry`` — no
farchive, no persisted artifact, runs in plain CI. The fixtures consume ONLY the
public resolution API; they never touch resolve.py / the registries / lenses.

Inline typed ``ReferenceMention`` inputs are used (NOT the XML-bytes extraction
corpus framework) because the resolution layer's natural input is a typed
mention, not source XML — see the module docstring of the extraction corpus for
the contrast.
"""
from __future__ import annotations

import datetime as dt
from typing import cast

import pytest

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.finland.references.defined_terms import (
    BINDING_PARENTHETICAL_ALIAS,
    STATUS_OK,
    DefinedTermBinding,
)
from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    StatuteNameRegistry,
    build_registry,
)
from lawvm.finland.references.resolve import (
    ResolutionStatus,
    build_defined_term_table,
    resolve_mention,
    resolve_mentions,
)

# ---------------------------------------------------------------------------
# Synthetic placeholder / mention builders
# ---------------------------------------------------------------------------
#
# A statute-name by-name cite carries an UNRESOLVED-by-identity placeholder
# ``fi-name:<normalized_name>`` as its target statute_id; an EU-by-nickname cite
# carries ``eu-nickname:<surface>``. These are exactly the placeholders the
# recognizer lanes emit and that ``resolve_mentions`` is the downstream
# projection for (see resolve.py module docstring). We build them by hand here so
# the fixtures are recognizer-independent.

_SOURCE = ProvisionRef(statute_id="2099/1", section_label="1")
_SOURCE_FILE = "synthetic://resolution_conformance"


def _mention(
    *,
    target: ProvisionRef | None,
    confidence: CiteConfidence,
    kind: CiteKind = CiteKind.CROSS_STATUTE,
    surface: str = "",
    byte_offset: int | None = None,
    phrase_lemma: str = "plain_text",
) -> ReferenceMention:
    """Build a synthetic mention. ``byte_offset`` sets a SourceSpan when given."""
    span = (
        SourceSpan(_SOURCE_FILE, byte_offset, len(surface) or 1)
        if byte_offset is not None
        else None
    )
    return ReferenceMention(
        source_provision_ref=_SOURCE,
        target_provision_ref=target,
        cite_kind=kind,
        cite_confidence=confidence,
        phrase_lemma=phrase_lemma,
        source_span=span,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def _fi_name_placeholder(name_key: str, **kw) -> ReferenceMention:
    """A by-name cross-statute placeholder (``fi-name:<key>``), STATUTE_ONLY."""
    return _mention(
        target=ProvisionRef(statute_id=f"fi-name:{name_key}"),
        confidence=CiteConfidence.STATUTE_ONLY,
        **kw,
    )


def _eu_nickname_placeholder(surface: str, **kw) -> ReferenceMention:
    """An EU-by-nickname placeholder (``eu-nickname:<surface>``)."""
    return _mention(
        target=ProvisionRef(statute_id=f"eu-nickname:{surface}"),
        confidence=CiteConfidence.STATUTE_ONLY,
        kind=CiteKind.EU,
        surface=surface,
        **kw,
    )


# A tiny, archive-free statute-name registry. ``aliases=None`` keeps it
# generation-only (no curated nickname table) so candidate sets are exactly what
# these synthetic entries imply.
_LUONNONSUOJELU = StatuteNameEntry(
    statute_id="1096/1996",
    canonical_title="Luonnonsuojelulaki",
)
# Two entries that share the SAME inflected surface key over disjoint windows —
# an act re-enacted under the same name — so a windowless (whole-timeline) lookup
# yields TWO candidates (AMBIGUOUS), never a silent pick.
_KUNTALAKI_OLD = StatuteNameEntry(
    statute_id="365/1995",
    canonical_title="Kuntalaki",
    valid_from=dt.date(1995, 7, 1),
    valid_to=dt.date(2015, 5, 1),
)
_KUNTALAKI_NEW = StatuteNameEntry(
    statute_id="410/2015",
    canonical_title="Kuntalaki",
    valid_from=dt.date(2015, 5, 1),
    valid_to=None,
)


def _single_candidate_registry():
    """Registry with exactly one act behind ``luonnonsuojelulaki`` (→ RESOLVED)."""
    return build_registry([_LUONNONSUOJELU], aliases=None)


def _ambiguous_registry():
    """Registry where ``kuntalaki`` names two acts over time. One (410/2015) is
    still in force, so the DEFAULT multi-version disambiguation resolves it to the
    live version; the ``disambiguate_multi_version=False`` opt-out keeps AMBIGUOUS."""
    return build_registry([_KUNTALAKI_OLD, _KUNTALAKI_NEW], aliases=None)


# Two DISTINCT acts BOTH in force under one name: no unique live version, so even
# with multi-version disambiguation ON (the default) the cite stays AMBIGUOUS — a
# genuine homonym has no defensible single referent.
_HOMONYM_A = StatuteNameEntry(
    statute_id="100/2001",
    canonical_title="Yhteisnimilaki",
    valid_from=dt.date(2001, 1, 1),
    valid_to=None,
)
_HOMONYM_B = StatuteNameEntry(
    statute_id="200/2010",
    canonical_title="Yhteisnimilaki",
    valid_from=dt.date(2010, 1, 1),
    valid_to=None,
)


def _genuinely_ambiguous_registry():
    """Two live distinct acts under one name → AMBIGUOUS even under the default."""
    return build_registry([_HOMONYM_A, _HOMONYM_B], aliases=None)


def _empty_registry():
    """Registry that knows nothing (every by-name lookup misses → STATUTE_ONLY)."""
    return build_registry([], aliases=None)


# ===========================================================================
# T1 — pure grammar (surface + source document only)
# ===========================================================================


def test_t1_resolved_explicit_id_passes_through_unchanged():
    """T1×resolved: an explicit-id cite ``(2014/527) 5 §`` is already pinned.

    The catalogue's T1 **resolved** is an explicit-id surface that fully names
    act + provision; nothing for the registry to do. At the resolve layer this
    is a pass-through: ``UNCHANGED`` with ``work_id`` = the upstream id (the
    ``RESOLVED`` status proper is reserved for an ACTUAL registry/local
    resolution of a placeholder — covered by the T2 cells).
    """
    m = _mention(
        target=ProvisionRef(statute_id="2014/527", section_label="5"),
        confidence=CiteConfidence.EXACT,
        surface="(2014/527) 5 §",
    )
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.UNCHANGED
    assert res.work_id == "2014/527"
    assert res.candidates == ("2014/527",)
    assert res.finding is None
    # No registry call was needed: the mention is unmutated.
    assert res.mention is m


def test_t1_statute_only_bare_section_no_id_no_registry_hit():
    """T1×statute-only: a by-name cite with no id and a registry MISS.

    The act identity is textual but the id is pending — a coverage gap recorded
    as ``STATUTE_ONLY``, never a silent resolve to nothing. (At T1 this is the
    "bare ``§`` / act named, id pending" cell; here realized as a by-name
    placeholder against an EMPTY registry so no external table can pin it.)
    """
    m = _fi_name_placeholder("tuntematonlaki", surface="tuntemattomassa laissa")
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert res.work_id is None
    assert res.candidates == ()
    assert res.finding is None


def test_t1_broken_passthrough_documented_bitemporal_elsewhere():
    """T1×broken: documented-as-covered-elsewhere, not faked at the resolve layer.

    The genuine **broken** *determination* is BITEMPORAL (catalogue §0.4): it is
    made by ``references.broken_detection.detect_broken`` against the
    consolidated statute store (covered by ``tests/test_fi_broken_detection``),
    NOT by ``resolve_mentions``. ``resolve_mentions`` never DERIVES broken from
    an input surface.

    What it CAN do — and what is pinned here — is PASS THROUGH a mention that
    already arrived typed ``CiteConfidence.BROKEN`` (a projection-phase input
    carrying the bitemporal verdict). This witnesses the ``BROKEN``
    ``ResolutionStatus`` member without fabricating the determination.
    """
    # BROKEN is one of the _NONE_TARGET_OK confidences → target may be None.
    m = _mention(target=None, confidence=CiteConfidence.BROKEN, surface="3 §")
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.BROKEN
    assert res.work_id is None
    assert res.finding is None
    assert res.mention is m


# ===========================================================================
# T2 — grammar + deterministic registry
# ===========================================================================


def test_t2_resolved_by_name_single_candidate():
    """T2×resolved: ``luonnonsuojelulaissa`` → single registry candidate → RESOLVED.

    The placeholder is rewritten to the real statute id in a NEW mention; the
    input mention is never mutated.
    """
    m = _fi_name_placeholder(
        "luonnonsuojelulaki", surface="luonnonsuojelulaissa"
    )
    reg = _single_candidate_registry()
    (res,) = resolve_mentions([m], statute_registry=reg)

    assert res.resolution_status is ResolutionStatus.RESOLVED
    assert res.work_id == "1096/1996"
    assert res.candidates == ("1096/1996",)
    assert res.finding is None
    # NEW mention with the real id; the placeholder input is untouched.
    assert res.mention is not m
    assert res.mention.target_provision_ref is not None
    assert res.mention.target_provision_ref.statute_id == "1096/1996"
    assert res.mention.cite_confidence is CiteConfidence.EXACT
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:luonnonsuojelulaki"


def test_t2_by_name_two_versions_resolves_to_live_by_default():
    """T2×ambiguous, DEFAULT (multi-version disambiguation now on): a whole-timeline
    lookup of ``kuntalaki`` surfaces both the 1995 and the 2015 act, but exactly one
    (410/2015) is still in force, so the as-of-live version is picked — stamped
    APPROXIMATE (a heuristic pick, never EXACT), with the repealed 365/1995 rejected.
    All candidates are still reported. The never-pick contract is preserved under the
    ``disambiguate_multi_version=False`` opt-out (sibling test below)."""
    m = _fi_name_placeholder("kuntalaki", surface="kuntalaissa")
    reg = _ambiguous_registry()
    (res,) = resolve_mentions([m], statute_registry=reg, as_of=None)

    assert res.resolution_status is ResolutionStatus.RESOLVED
    assert res.work_id == "410/2015"
    assert set(res.candidates) == {"365/1995", "410/2015"}
    assert res.rejected_candidates == ("365/1995",)
    assert res.mention.cite_confidence is CiteConfidence.APPROXIMATE
    assert res.finding is None


def test_t2_by_name_two_candidates_never_picks_when_disabled():
    """T2×ambiguous, OPT-OUT: with ``disambiguate_multi_version=False`` the byte-
    unchanged contract holds — ≥2 as-of candidates → AMBIGUOUS, all candidates
    listed, a finding emitted, NO pick, placeholder id NOT rewritten."""
    m = _fi_name_placeholder("kuntalaki", surface="kuntalaissa")
    reg = _ambiguous_registry()
    (res,) = resolve_mentions(
        [m], statute_registry=reg, as_of=None, disambiguate_multi_version=False
    )

    assert res.resolution_status is ResolutionStatus.AMBIGUOUS
    assert res.work_id is None  # never picks
    assert set(res.candidates) == {"365/1995", "410/2015"}
    assert res.finding is not None
    assert set(res.finding.candidate_target_ids) == {"365/1995", "410/2015"}
    assert res.mention.cite_confidence is CiteConfidence.AMBIGUOUS
    assert res.mention.target_provision_ref is not None
    assert res.mention.target_provision_ref.statute_id == "fi-name:kuntalaki"


def test_t2_statute_only_by_name_registry_miss():
    """T2×statute-only: a by-name cite against an EMPTY registry → STATUTE_ONLY.

    The instrument was clearly named (a ``fi-name:`` placeholder) but the
    registry misses the act: id pending, recorded as a coverage gap — not a
    silent ``RESOLVED`` to nothing.
    """
    m = _fi_name_placeholder("merilaki", surface="merilaissa")
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert res.work_id is None
    assert res.candidates == ()
    assert res.finding is None


def test_t2_resolved_eu_nickname_single_celex():
    """T2×resolved (EU lane): an EU nickname with a single CELEX → RESOLVED.

    ``teollisuuspäästödirektiivin`` is in the curated EU nickname registry with
    exactly one CELEX → resolved to ``celex:32010L0075``.
    """
    m = _eu_nickname_placeholder("teollisuuspäästödirektiivin")
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.RESOLVED
    assert res.work_id == "celex:32010L0075"
    assert res.candidates == ("celex:32010L0075",)
    assert res.finding is None


def test_t2_ambiguous_eu_nickname_multi_celex_never_picks():
    """T2×ambiguous (EU lane): a nickname with >1 CELEX → AMBIGUOUS, no pick.

    ``jätedirektiivi`` is a genuinely ambiguous Finnish usage seeded with two
    CELEX ids; the registry lists both and refuses to pick.
    """
    m = _eu_nickname_placeholder("jätedirektiivi")
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.AMBIGUOUS
    assert res.work_id is None
    assert set(res.candidates) == {"celex:32008L0098", "celex:32006L0012"}
    assert res.finding is not None


def test_t2_statute_only_eu_nickname_not_in_registry():
    """T2×statute-only (EU lane): a nickname-shaped head not in the registry.

    A directive-shaped surface the curated table does not know → ``STATUTE_ONLY``
    (the instrument was named; the id is pending), never a silent resolve.
    """
    m = _eu_nickname_placeholder("kuvitteellinendirektiivi")
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert res.work_id is None
    assert res.candidates == ()
    assert res.finding is None


# ===========================================================================
# T3 — closed-list vague markers
# ===========================================================================


def test_t3_open_vague_marker_carries_no_target():
    """T3×open: a closed-list vague marker (``muussa laissa säädetään``) → OPEN.

    OPEN is targetless BY CONSTRUCTION; the resolve layer passes it through with
    no target and no registry call.
    """
    m = _mention(
        target=None,
        confidence=CiteConfidence.OPEN,
        surface="muussa laissa säädetään",
        phrase_lemma="vague_open_catchall",
    )
    (res,) = resolve_mentions([m], statute_registry=_empty_registry())

    assert res.resolution_status is ResolutionStatus.OPEN
    assert res.work_id is None
    assert res.candidates == ()
    assert res.finding is None
    # OPEN never carries a target.
    assert res.mention.target_provision_ref is None


# ===========================================================================
# Defined-term local binding (the alias-after-binding cells)
# ===========================================================================


def _alias_binding(*, term: str, target_ref: str, byte_offset: int):
    """A parenthetical-alias defined-term binding at a given source offset."""
    return DefinedTermBinding(
        term=term,
        target_ref=target_ref,
        expansion=None,
        scope="statute",
        source_span=SourceSpan(_SOURCE_FILE, byte_offset, len(term)),
        binding_kind=BINDING_PARENTHETICAL_ALIAS,
        binding_status=STATUS_OK,
    )


def test_defined_term_alias_resolves_after_binding():
    """Defined-term cell: an alias USED AFTER its binding → RESOLVED via the table.

    ``sivutuoteasetus`` is bound to ``32009R1069`` at offset 10; a later use (at
    offset 200) resolves EXACT to the bound target through the local
    defined-term table, BEFORE the statute-name registry is consulted (here the
    registry is empty, so without the binding it would be STATUTE_ONLY).
    """
    binding = _alias_binding(
        term="sivutuoteasetus", target_ref="32009R1069", byte_offset=10
    )
    table = build_defined_term_table([binding])
    use = _fi_name_placeholder(
        "sivutuoteasetus", surface="sivutuoteasetuksen", byte_offset=200
    )
    res = resolve_mention(
        use,
        statute_registry=_empty_registry(),
        defined_terms=table,
    )

    assert res.resolution_status is ResolutionStatus.RESOLVED
    assert res.work_id == "32009R1069"
    assert res.candidates == ("32009R1069",)
    assert res.finding is None
    assert res.mention.target_provision_ref is not None
    assert res.mention.target_provision_ref.statute_id == "32009R1069"


def test_defined_term_use_before_binding_does_not_resolve():
    """Defined-term cell: an alias USED BEFORE its binding stays unresolved.

    The use is at offset 5, the binding site is at offset 100 — the binding does
    not precede the use, so the local table declines (tag-don't-guess). With an
    empty statute-name registry the placeholder then falls to ``STATUTE_ONLY``
    (act named, id pending), NOT a resolution from a binding it precedes. The
    term is deliberately not a known EU nickname, so the EU-nickname fallback also
    misses and the decline surfaces as a genuine coverage gap.
    """
    binding = _alias_binding(
        term="paikallisasetus", target_ref="32009R1069", byte_offset=100
    )
    table = build_defined_term_table([binding])
    use = _fi_name_placeholder(
        "paikallisasetus", surface="paikallisasetuksen", byte_offset=5
    )
    res = resolve_mention(
        use,
        statute_registry=_empty_registry(),
        defined_terms=table,
    )

    assert res.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert res.work_id is None
    assert res.candidates == ()


# ===========================================================================
# Coverage: every emittable ResolutionStatus member has ≥1 cell
# ===========================================================================

# The resolve-layer statuses each cell above asserts on, with the witness test.
_COVERED_STATUSES: dict[ResolutionStatus, str] = {
    ResolutionStatus.UNCHANGED: "test_t1_resolved_explicit_id_passes_through_unchanged",
    ResolutionStatus.STATUTE_ONLY: "test_t1_statute_only_bare_section_no_id_no_registry_hit",
    ResolutionStatus.BROKEN: "test_t1_broken_passthrough_documented_bitemporal_elsewhere",
    ResolutionStatus.RESOLVED: "test_t2_resolved_by_name_single_candidate",
    ResolutionStatus.AMBIGUOUS: "test_t2_by_name_two_candidates_never_picks_when_disabled",
    ResolutionStatus.OPEN: "test_t3_open_vague_marker_carries_no_target",
}


def test_every_resolution_status_member_has_a_cell():
    """Coverage assertion: every ``ResolutionStatus`` the pipeline emits has a cell.

    ``resolve_mentions`` can emit ALL six members (RESOLVED / AMBIGUOUS /
    STATUTE_ONLY / OPEN / BROKEN / UNCHANGED). Each must have ≥1 synthetic
    witness above. If a member is added to the enum, this fails until a cell is
    added — keeping the conformance set complete.
    """
    all_members = set(ResolutionStatus)
    covered = set(_COVERED_STATUSES)
    missing = all_members - covered
    assert not missing, (
        f"ResolutionStatus members with no conformance cell: "
        f"{sorted(s.name for s in missing)}"
    )
    # And every claimed witness must actually exist in this module.
    module_globals = globals()
    for status, test_name in _COVERED_STATUSES.items():
        assert test_name in module_globals, (
            f"witness {test_name!r} for {status.name} is missing"
        )


def test_each_status_is_actually_produced_by_resolve():
    """Drive one input per status through ``resolve_mentions`` and assert the member.

    A single executable table proving each ``ResolutionStatus`` is genuinely
    REACHABLE through the public API (not just asserted in isolation), so the
    coverage claim is grounded in real pipeline output.
    """
    single_reg = _single_candidate_registry()
    ambig_reg = _ambiguous_registry()
    empty_reg = _empty_registry()

    cases: list[tuple[ResolutionStatus, ReferenceMention, object]] = [
        (
            ResolutionStatus.UNCHANGED,
            _mention(
                target=ProvisionRef(statute_id="2014/527", section_label="5"),
                confidence=CiteConfidence.EXACT,
            ),
            empty_reg,
        ),
        (
            ResolutionStatus.RESOLVED,
            _fi_name_placeholder("luonnonsuojelulaki"),
            single_reg,
        ),
        (
            ResolutionStatus.AMBIGUOUS,
            _fi_name_placeholder("yhteisnimilaki"),
            _genuinely_ambiguous_registry(),
        ),
        (
            ResolutionStatus.STATUTE_ONLY,
            _fi_name_placeholder("merilaki"),
            empty_reg,
        ),
        (
            ResolutionStatus.OPEN,
            _mention(
                target=None,
                confidence=CiteConfidence.OPEN,
                phrase_lemma="vague_open_catchall",
            ),
            empty_reg,
        ),
        (
            ResolutionStatus.BROKEN,
            _mention(target=None, confidence=CiteConfidence.BROKEN),
            empty_reg,
        ),
    ]
    produced = set()
    for expected, mention, reg in cases:
        (res,) = resolve_mentions([mention], statute_registry=cast(StatuteNameRegistry, reg))
        assert res.resolution_status is expected, (
            f"expected {expected.name}, got {res.resolution_status.name}"
        )
        produced.add(res.resolution_status)

    assert produced == set(ResolutionStatus)


if __name__ == "__main__":  # pragma: no cover - manual run convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
