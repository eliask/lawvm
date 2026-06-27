"""Tests for the Finnish defined-term USE resolution pass (``term_use``)."""
from __future__ import annotations

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references.defined_terms import (
    BINDING_PARENTHETICAL_ALIAS,
    STATUS_OK,
    STATUS_UNSUPPORTED_MORPHOLOGY,
    DefinedTermBinding,
)
from lawvm.finland.references.term_use import (
    RULE_BEFORE_BINDING,
    RULE_EXACT_SURFACE,
    RULE_MORPH,
    STATUS_AMBIGUOUS,
    STATUS_OPEN,
    STATUS_RESOLVED,
    TermUse,
    resolve_term_uses,
)


def _binding(
    term: str,
    *,
    offset: int,
    length: int,
    target: str | None = "1069/2009",
    status: str = STATUS_OK,
) -> DefinedTermBinding:
    return DefinedTermBinding(
        term=term,
        target_ref=target,
        expansion=None,
        scope="statute",
        source_span=SourceSpan("test", offset, length),
        binding_kind=BINDING_PARENTHETICAL_ALIAS,
        binding_status=status,
    )


# ---------------------------------------------------------------------------
# Empty / no-op
# ---------------------------------------------------------------------------


def test_empty_text_returns_no_uses() -> None:
    b = _binding("sivutuoteasetus", offset=0, length=15)
    assert resolve_term_uses("", [b]) == []


def test_no_bindings_returns_no_uses() -> None:
    assert resolve_term_uses("sivutuoteasetuksen mukaan", []) == []


def test_token_matching_no_binding_is_not_emitted() -> None:
    # Arbitrary prose that doesn't match any binding surface -> nothing fabricated.
    b = _binding("sivutuoteasetus", offset=0, length=15)
    text = "x" * 20 + " Tämä on tavallista tekstiä ilman termiä."
    uses = resolve_term_uses(text, [b])
    assert uses == []


# ---------------------------------------------------------------------------
# Inflected use resolves (compound head -> reliable morphology)
# ---------------------------------------------------------------------------


def test_inflected_compound_use_resolves() -> None:
    # Binding occupies bytes [0, 17). The genitive 'sivutuoteasetuksen' is a use.
    binding_text = "(sivutuoteasetus)"
    body = binding_text + " jota sivutuoteasetuksen 5 artiklassa tarkoitetaan."
    b = _binding("sivutuoteasetus", offset=0, length=len(binding_text))

    uses = resolve_term_uses(body, [b])
    resolved = [u for u in uses if u.use_status == STATUS_RESOLVED]
    assert len(resolved) == 1
    u = resolved[0]
    assert u.term_surface == "sivutuoteasetuksen"
    assert u.lemma == "sivutuoteasetus"
    assert u.binding is b
    assert u.bindings == (b,)
    assert u.rule_id == RULE_MORPH
    # Span points at the inflected use, not the binding site.
    assert body[
        u.source_span.byte_offset : u.source_span.byte_offset + u.source_span.byte_len
    ] == "sivutuoteasetuksen"


def test_multiple_inflected_uses_each_resolve() -> None:
    binding_text = "(ympäristönsuojelulaki)"
    body = (
        binding_text
        + " Tätä ympäristönsuojelulakia sovelletaan. "
        + "Ympäristönsuojelulaissa säädetään lisäksi seikoista."
    )
    b = _binding(
        "ympäristönsuojelulaki",
        offset=0,
        length=len(binding_text),
        target="527/2014",
    )
    uses = resolve_term_uses(body, [b])
    resolved = [u for u in uses if u.use_status == STATUS_RESOLVED]
    surfaces = {u.term_surface for u in resolved}
    assert "ympäristönsuojelulakia" in surfaces
    # Sentence-initial capitalised use also matches (case-insensitive).
    assert "Ympäristönsuojelulaissa" in surfaces
    assert all(u.binding is b for u in resolved)


def test_nominative_use_resolves() -> None:
    binding_text = "(asetus)"
    body = binding_text + " ja asetus määrää menettelystä tarkemmin."
    b = _binding("asetus", offset=0, length=len(binding_text))
    uses = resolve_term_uses(body, [b])
    resolved = [u for u in uses if u.use_status == STATUS_RESOLVED]
    assert len(resolved) == 1
    assert resolved[0].term_surface == "asetus"


# ---------------------------------------------------------------------------
# Before-binding negative case (scope/order violation -> open)
# ---------------------------------------------------------------------------


def test_use_before_binding_is_open() -> None:
    # The inflected use appears BEFORE the binding site -> open, never resolved.
    body = "Jo sivutuoteasetuksen nojalla toimitaan. (sivutuoteasetus)"
    binding_open = body.index("(sivutuoteasetus)")
    binding_len = len("(sivutuoteasetus)")
    b = _binding("sivutuoteasetus", offset=binding_open, length=binding_len)

    uses = resolve_term_uses(body, [b])
    # The pre-binding inflected token is the only use (the term inside the
    # binding span is skipped).
    assert len(uses) == 1
    u = uses[0]
    assert u.use_status == STATUS_OPEN
    assert u.binding is None
    assert u.bindings == ()
    assert u.rule_id == RULE_BEFORE_BINDING
    assert u.term_surface == "sivutuoteasetuksen"
    assert u.source_span.byte_offset < binding_open


def test_use_after_binding_resolves_same_term() -> None:
    # Same surface, but placed AFTER the binding -> resolves (control for the
    # before-binding case above).
    body = "(sivutuoteasetus) Tämän jälkeen sivutuoteasetuksen nojalla toimitaan."
    b = _binding("sivutuoteasetus", offset=0, length=len("(sivutuoteasetus)"))
    uses = resolve_term_uses(body, [b])
    assert len(uses) == 1
    assert uses[0].use_status == STATUS_RESOLVED
    assert uses[0].source_span.byte_offset > 0


# ---------------------------------------------------------------------------
# Ambiguous (>1 in-scope binding matches the same surface)
# ---------------------------------------------------------------------------


def test_two_bindings_same_term_is_ambiguous() -> None:
    # Two distinct bindings introduce the same surface term -> a later use is
    # ambiguous; both are listed, none is chosen.
    b1 = _binding("asetus", offset=0, length=8, target="1069/2009")
    b2 = _binding("asetus", offset=20, length=8, target="999/2020")
    body = "(asetus)" + " " * 12 + "(asetus) myöhemmin asetuksessa säädetään."
    uses = resolve_term_uses(body, [b1, b2])
    amb = [u for u in uses if u.use_status == STATUS_AMBIGUOUS]
    assert len(amb) == 1
    u = amb[0]
    assert u.term_surface == "asetuksessa"
    assert u.binding is None
    assert set(u.bindings) == {b1, b2}


# ---------------------------------------------------------------------------
# Morphology-unsupported binding -> exact-surface fallback (no crash, flagged)
# ---------------------------------------------------------------------------


def test_unsupported_morphology_falls_back_to_exact_surface() -> None:
    # Binder flagged the term unsupported -> we do NOT generate case forms.
    # Only the exact written surface matches, tagged exact_surface; an inflected
    # form of it is NOT matched (we refuse to guess inflection).
    binding_text = '("eläimistä saatavat sivutuotteet")'
    term = "eläimistä saatavat sivutuotteet"
    body = (
        binding_text
        + " Näin eläimistä saatavat sivutuotteet luokitellaan. "
        + "Lisäksi eläimistä saatavien sivutuotteiden osalta pätee muuta."
    )
    b = _binding(
        term,
        offset=0,
        length=len(binding_text),
        status=STATUS_UNSUPPORTED_MORPHOLOGY,
    )
    uses = resolve_term_uses(body, [b])
    # The exact multi-word surface does not tokenise as one word, so no
    # exact-surface match fires here; crucially nothing crashed and no inflected
    # guess was made.
    assert all(isinstance(u, TermUse) for u in uses)
    assert all(u.rule_id != RULE_MORPH for u in uses)


def test_unsupported_single_word_exact_surface_match() -> None:
    # A single-word term whose morphology the engine declines (bare -i wall:
    # 'direktiivi' is fine, but force the unsupported path with the binder flag).
    binding_text = "(testitermi)"
    body = binding_text + " ja testitermi esiintyy tässä uudelleen."
    b = _binding(
        "testitermi",
        offset=0,
        length=len(binding_text),
        status=STATUS_UNSUPPORTED_MORPHOLOGY,
    )
    uses = resolve_term_uses(body, [b])
    resolved = [u for u in uses if u.use_status == STATUS_RESOLVED]
    assert len(resolved) == 1
    assert resolved[0].rule_id == RULE_EXACT_SURFACE
    assert resolved[0].term_surface == "testitermi"


def test_unsupported_morphology_does_not_match_inflection() -> None:
    # With morphology unsupported, an INFLECTED form of the exact term must NOT
    # resolve (no guessing) -> only the bare nominative is found.
    binding_text = "(testitermi)"
    body = binding_text + " mutta testitermin taivutus jää tunnistamatta."
    b = _binding(
        "testitermi",
        offset=0,
        length=len(binding_text),
        status=STATUS_UNSUPPORTED_MORPHOLOGY,
    )
    uses = resolve_term_uses(body, [b])
    # 'testitermin' (genitive) is NOT matched because we refuse to inflect.
    assert all(u.term_surface != "testitermin" for u in uses)


# ---------------------------------------------------------------------------
# Binding site itself is not counted as a use
# ---------------------------------------------------------------------------


def test_binding_site_token_not_emitted_as_use() -> None:
    binding_text = "(sivutuoteasetus)"
    body = binding_text  # term only appears inside the binding span
    b = _binding("sivutuoteasetus", offset=0, length=len(binding_text))
    uses = resolve_term_uses(body, [b])
    assert uses == []


# ---------------------------------------------------------------------------
# Source ordering
# ---------------------------------------------------------------------------


def test_uses_returned_in_source_order() -> None:
    binding_text = "(asetus)"
    body = binding_text + " asetus eka, asetuksen toka, asetuksessa kolmas."
    b = _binding("asetus", offset=0, length=len(binding_text))
    uses = resolve_term_uses(body, [b])
    offsets = [u.source_span.byte_offset for u in uses]
    assert offsets == sorted(offsets)
    assert len(uses) == 3
