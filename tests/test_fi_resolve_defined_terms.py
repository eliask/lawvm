"""Tests for wiring local defined-term / alias bindings into resolution.

Covers the ``defined_terms`` lever added to
``src/lawvm/finland/references/resolve.py``: a statute introduces a SHORT local
name bound to an act (``… asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) …``)
and later uses it inflected (``sivutuoteasetuksen 3 artiklassa``). With the local
table threaded in, that later use resolves EXACT to the binding's target instead
of falling to ``open`` / ``statute_only``.

Discipline asserted here (fail-loud / tag-don't-guess):

* a use BEFORE the binding site does NOT resolve via the binding;
* an ``unsupported_morphology`` binding resolves only on an EXACT surface match;
* >1 distinct target for the same term is ambiguous and never picked;
* a binding with no ``target_ref`` is not resolvable;
* the parameter is OPTIONAL (default ``None``) — existing behavior is unchanged.
"""

from __future__ import annotations

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
    STATUS_UNSUPPORTED_MORPHOLOGY,
    DefinedTermBinding,
    recognize_defined_term_bindings,
)
from lawvm.finland.references.registries import eu_nickname
from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    build_registry,
)
from lawvm.finland.references.resolve import (
    ResolutionStatus,
    build_defined_term_table,
    resolve_mentions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_registry():
    """A registry that knows NOTHING — so a resolution can only come from the
    local defined-term table, never the statute-name registry."""
    return build_registry([], aliases=None)


def _mention(
    name: str,
    *,
    use_offset: int | None = None,
    surface_text: str = "",
    section_label: str = "",
) -> ReferenceMention:
    """A ``fi-name:<name>`` placeholder mention, optionally byte-anchored."""
    span = (
        SourceSpan(source_file="s.xml", byte_offset=use_offset, byte_len=len(surface_text))
        if use_offset is not None
        else None
    )
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="517/2015", section_label="1"),
        target_provision_ref=ProvisionRef(
            statute_id=f"fi-name:{name}", section_label=section_label
        ),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.STATUTE_ONLY,
        phrase_lemma="statute_name_head",
        source_span=span,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface_text or name,
    )


def _binding(
    term: str,
    target_ref: str | None,
    *,
    offset: int = 100,
    status: str = STATUS_OK,
) -> DefinedTermBinding:
    return DefinedTermBinding(
        term=term,
        target_ref=target_ref,
        expansion=None,
        scope="statute",
        source_span=SourceSpan(source_file="s.xml", byte_offset=offset, byte_len=10),
        binding_kind=BINDING_PARENTHETICAL_ALIAS,
        binding_status=status,
    )


# ---------------------------------------------------------------------------
# Core: a use after the binding resolves EXACT via the local binding
# ---------------------------------------------------------------------------


def test_use_after_binding_resolves_exact() -> None:
    """``sivutuoteasetuksen 3 artiklassa`` after the binding -> resolved to target."""
    table = build_defined_term_table([_binding("sivutuoteasetus", "1069/2009", offset=100)])
    # The by-name recognizer reattaches the nominative head, so the inflected use
    # carries the placeholder key ``sivutuoteasetus``.
    mention = _mention(
        "sivutuoteasetus",
        use_offset=400,
        surface_text="sivutuoteasetuksen 3 artiklassa",
        section_label="3",
    )
    [rr] = resolve_mentions(
        [mention],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1069/2009"
    assert rr.candidates == ("1069/2009",)
    assert rr.finding is None
    # Rewritten in a NEW mention; section path preserved; confidence promoted;
    # provenance recorded that it came from a local binding.
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "1069/2009"
    assert rr.mention.target_provision_ref.section_label == "3"
    assert rr.mention.cite_confidence is CiteConfidence.EXACT
    assert rr.mention.phrase_lemma == "defined_term_local_binding"
    # Input mention untouched.
    assert mention.target_provision_ref is not None
    assert mention.target_provision_ref.statute_id == "fi-name:sivutuoteasetus"


# ---------------------------------------------------------------------------
# Negative: use BEFORE the binding site does not resolve via the binding
# ---------------------------------------------------------------------------


def test_use_before_binding_stays_unresolved() -> None:
    """A use whose byte offset precedes the binding site is NOT resolved by it.

    The fixture name is deliberately NOT a known EU nickname, so a decline of the
    local binding falls through to a genuine coverage gap (the empty statute
    registry AND the EU-nickname fallback both miss), isolating the binding's
    ordering rule.
    """
    table = build_defined_term_table([_binding("paikallisasetus", "1069/2009", offset=500)])
    mention = _mention("paikallisasetus", use_offset=100, surface_text="paikallisasetuksen")
    [rr] = resolve_mentions(
        [mention],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    # Falls through to the (empty) registry -> coverage gap, not a silent resolve.
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert rr.work_id is None
    assert rr.mention.target_provision_ref is not None
    assert rr.mention.target_provision_ref.statute_id == "fi-name:paikallisasetus"


def test_unanchored_use_offset_does_not_resolve() -> None:
    """Without a verifiable use offset, ordering is unverifiable -> no local resolve."""
    table = build_defined_term_table([_binding("paikallisasetus", "1069/2009", offset=100)])
    mention = _mention("paikallisasetus", use_offset=None, surface_text="paikallisasetuksen")
    [rr] = resolve_mentions(
        [mention],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY


# ---------------------------------------------------------------------------
# Negative: unsupported morphology -> exact surface match only
# ---------------------------------------------------------------------------


def test_unsupported_morphology_resolves_only_on_exact_surface() -> None:
    """An ``unsupported_morphology`` binding does not match an inflected use ..."""
    table = build_defined_term_table(
        [_binding("paikallisasetus", "1069/2009", offset=100, status=STATUS_UNSUPPORTED_MORPHOLOGY)]
    )
    # Inflected use (surface != term) -> NOT resolved. The fixture name is not an
    # EU nickname, so the decline is a genuine coverage gap (no EU fallback).
    inflected = _mention(
        "paikallisasetus", use_offset=400, surface_text="paikallisasetuksen"
    )
    [rr_inflected] = resolve_mentions(
        [inflected],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr_inflected.resolution_status is ResolutionStatus.STATUTE_ONLY

    # ... but an EXACT surface match DOES resolve.
    exact = _mention(
        "paikallisasetus", use_offset=400, surface_text="paikallisasetus"
    )
    [rr_exact] = resolve_mentions(
        [exact],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr_exact.resolution_status is ResolutionStatus.RESOLVED
    assert rr_exact.work_id == "1069/2009"


# ---------------------------------------------------------------------------
# Negative: conflicting targets are ambiguous -> never picked
# ---------------------------------------------------------------------------


def test_conflicting_targets_are_dropped_never_picked() -> None:
    """Two distinct targets for one term -> the key is dropped (no guess)."""
    table = build_defined_term_table(
        [
            _binding("laki", "111/2001", offset=50),
            _binding("laki", "222/2002", offset=80),
        ]
    )
    mention = _mention("laki", use_offset=400, surface_text="lain")
    [rr] = resolve_mentions(
        [mention],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY
    assert rr.work_id is None


def test_repeated_same_target_keeps_earliest_site() -> None:
    """The same target bound twice keeps the EARLIEST site; a use between them
    (after the first) still resolves."""
    table = build_defined_term_table(
        [
            _binding("sivutuoteasetus", "1069/2009", offset=300),
            _binding("sivutuoteasetus", "1069/2009", offset=100),
        ]
    )
    mention = _mention("sivutuoteasetus", use_offset=200, surface_text="sivutuoteasetuksen")
    [rr] = resolve_mentions(
        [mention],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1069/2009"


# ---------------------------------------------------------------------------
# Negative: a binding with no target_ref carries no resolvable identity
# ---------------------------------------------------------------------------


def test_binding_without_target_is_not_resolvable() -> None:
    table = build_defined_term_table([_binding("jokin", None, offset=100)])
    mention = _mention("jokin", use_offset=400, surface_text="jonkin")
    [rr] = resolve_mentions(
        [mention],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr.resolution_status is ResolutionStatus.STATUTE_ONLY


# ---------------------------------------------------------------------------
# Optionality: default None leaves existing behavior unchanged
# ---------------------------------------------------------------------------


def test_default_none_is_registry_only() -> None:
    """With no table, a fi-name placeholder resolves against the registry only."""
    reg = build_registry(
        [StatuteNameEntry(statute_id="1096/1996", canonical_title="Luonnonsuojelulaki")]
    )
    [rr] = resolve_mentions(
        [_mention("luonnonsuojelulaki", use_offset=10, surface_text="luonnonsuojelulaissa")],
        statute_registry=reg,
        eu_registry=eu_nickname,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1096/1996"
    # Provenance is the registry path, not the local-binding tag.
    assert rr.mention.phrase_lemma != "defined_term_local_binding"


def test_local_binding_shadows_registry_when_both_match() -> None:
    """When both a local binding and the registry know the name, the LOCAL binding
    wins (an in-document alias is authoritative for that document)."""
    reg = build_registry(
        [StatuteNameEntry(statute_id="999/9999", canonical_title="Sivutuoteasetus")]
    )
    table = build_defined_term_table([_binding("sivutuoteasetus", "1069/2009", offset=100)])
    mention = _mention("sivutuoteasetus", use_offset=400, surface_text="sivutuoteasetuksen")
    [rr] = resolve_mentions(
        [mention],
        statute_registry=reg,
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1069/2009"
    assert rr.mention.phrase_lemma == "defined_term_local_binding"


# ---------------------------------------------------------------------------
# End-to-end: build the table from the real recognizer output
# ---------------------------------------------------------------------------


def test_end_to_end_from_recognizer_parenthetical_alias() -> None:
    """A real parenthetical-alias binding feeds the table and resolves a later use.

    The binding term key matches the by-name placeholder key produced for the
    inflected use, on the SAME normalization both go through.
    """
    text = (
        "Tässä laissa noudatetaan, mitä eläimistä saatavista sivutuotteista "
        "annetussa asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) säädetään. "
        "Lisäksi sovelletaan sivutuoteasetuksen 3 artiklassa tarkoitettuja "
        "määritelmiä."
    )
    bindings = recognize_defined_term_bindings(text, source_file="s.xml")
    table = build_defined_term_table(bindings)

    # There is at least one resolvable binding for the alias.
    alias_bindings = [b for b in bindings if b.term == "sivutuoteasetus"]
    assert alias_bindings, "recognizer should bind the parenthetical alias"
    binding_offset = alias_bindings[0].source_span.byte_offset

    use_offset = text.index("sivutuoteasetuksen 3 artiklassa")
    assert use_offset > binding_offset  # the use follows the binding site

    mention = _mention(
        "sivutuoteasetus",
        use_offset=use_offset,
        surface_text="sivutuoteasetuksen 3 artiklassa",
        section_label="3",
    )
    [rr] = resolve_mentions(
        [mention],
        statute_registry=_empty_registry(),
        eu_registry=eu_nickname,
        defined_terms=table,
    )
    assert rr.resolution_status is ResolutionStatus.RESOLVED
    assert rr.work_id == "1069/2009"
    assert rr.mention.phrase_lemma == "defined_term_local_binding"
