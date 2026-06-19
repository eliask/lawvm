"""Unit tests for the content-based census adjudication predicates (P1/P2).

Corpus-free: these build small synthetic ``SurfaceClause`` models directly and
exercise the predicates' soundness, with NEGATIVE tests proving a synthetic
regression (a dropped/relabelled OLD node, or a non-source-witnessed addition)
STAYS unclassified — the fail-loud invariant.
"""

from __future__ import annotations

from lawvm.finland.johtolause.census_adjudication import (
    is_provenance_only_delta,
    is_source_witnessed_additive_recovery,
)
from lawvm.finland.johtolause.grammar.diff import compare_surface_models
from lawvm.finland.johtolause.surface_model import (
    SurfaceClause,
    SurfaceInsertion,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceVerbGroup,
    SurfaceWitness,
    TargetKind,
    VerbKind,
)


def _ref(label, *, kind=TargetKind.SECTION, chapter="", sub=None, rule="fi.section_ref",
         span=(0, 2)):
    return SurfaceTargetRef(
        kind=kind,
        label=label,
        chapter=chapter,
        sub_refs=(sub,) if sub is not None else (),
        witness=SurfaceWitness(rule_id=rule, source_span=span),
    )


def _ins(label, *, kind=TargetKind.SECTION, chapter="", sub=None,
         rule="fi.insertion_alakohta_into_item", span=(0, 4)):
    return SurfaceInsertion(
        kind=kind,
        label=label,
        chapter=chapter,
        sub_target=sub,
        witness=SurfaceWitness(rule_id=rule, source_span=span),
    )


def _clause(*groups, consumed=100):
    return SurfaceClause(verb_groups=tuple(groups), consumed_count=consumed)


def _vg(verb, *nodes):
    return SurfaceVerbGroup(verb=verb, nodes=tuple(nodes))


# ---------------------------------------------------------------------------
# P1 — provenance-only delta.
# ---------------------------------------------------------------------------
def test_p1_true_when_only_rule_id_differs() -> None:
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5", rule="fi.anaphoric_pykala_ill")))
    new = _clause(_vg(VerbKind.MUUTTAA, _ref("5", rule="fi.anaphoric_bare_uusi")))
    deltas = compare_surface_models(old, new).deltas
    assert deltas  # they DO differ
    assert is_provenance_only_delta(deltas)


def test_p1_true_when_only_source_span_differs() -> None:
    old = _clause(_vg(VerbKind.LISATA, _ins("5a", span=(0, 4))))
    new = _clause(_vg(VerbKind.LISATA, _ins("5a", span=(0, 5))))
    deltas = compare_surface_models(old, new).deltas
    assert deltas
    assert is_provenance_only_delta(deltas)


def test_p1_false_when_a_content_field_differs() -> None:
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5", rule="fi.anaphoric_pykala_ill")))
    new = _clause(_vg(VerbKind.MUUTTAA, _ref("6", rule="fi.anaphoric_bare_uusi")))
    deltas = compare_surface_models(old, new).deltas
    assert not is_provenance_only_delta(deltas)


def test_p1_false_on_empty_delta() -> None:
    assert not is_provenance_only_delta([])


# ---------------------------------------------------------------------------
# P2 — source-witnessed additive recovery (POSITIVE).
# ---------------------------------------------------------------------------
def test_p2_bare_ref_upgraded_to_insertion() -> None:
    # OLD: bare LISATA ref 4 §. NEW: insertion 4 §:n 1 mom 2e kohta.
    old = _clause(_vg(VerbKind.LISATA, _ref("4")))
    new = _clause(
        _vg(
            VerbKind.LISATA,
            _ins("4", sub=SurfaceSubRef(momentti=1, item="2e")),
        )
    )
    assert is_source_witnessed_additive_recovery(old, new)


def test_p2_partial_ref_item_extended_with_alakohta() -> None:
    # OLD captured item '5' (dropped the alakohta letter); NEW recovers item '5c'.
    old = _clause(
        _vg(VerbKind.LISATA, _ref("51", sub=SurfaceSubRef(momentti=1, item="5")))
    )
    new = _clause(
        _vg(
            VerbKind.LISATA,
            _ins("51", sub=SurfaceSubRef(momentti=1, item="5c")),
        )
    )
    assert is_source_witnessed_additive_recovery(old, new)


def test_p2_preserved_group_plus_new_insertions() -> None:
    # MUUTTAA byte-identical; LISATA OLD bare ref 1 § -> NEW two insertions.
    old = _clause(
        _vg(VerbKind.MUUTTAA, _ref("4")),
        _vg(VerbKind.LISATA, _ref("1", sub=SurfaceSubRef(momentti=1, item="1"))),
    )
    new = _clause(
        _vg(VerbKind.MUUTTAA, _ref("4")),
        _vg(
            VerbKind.LISATA,
            _ins("1", sub=SurfaceSubRef(momentti=1, item="1i")),
            _ins("4", sub=SurfaceSubRef(momentti=1, item="6"),
                 rule="fi.insertion_sub_target"),
        ),
    )
    assert is_source_witnessed_additive_recovery(old, new)


# ---------------------------------------------------------------------------
# P2 — NEGATIVE (synthetic regressions MUST stay unclassified, fail-loud).
# ---------------------------------------------------------------------------
def test_p2_rejects_dropped_old_node() -> None:
    # NEW drops one of OLD's two real MUUTTAA targets — a regression.
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5"), _ref("7")))
    new = _clause(_vg(VerbKind.MUUTTAA, _ref("5")))
    assert not is_source_witnessed_additive_recovery(old, new)


def test_p2_rejects_relabelled_old_node() -> None:
    # NEW relabels 5 § -> 6 § (e.g. CHAPTER->SECTION correction shape): not additive.
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5")))
    new = _clause(_vg(VerbKind.MUUTTAA, _ref("6")))
    assert not is_source_witnessed_additive_recovery(old, new)


def test_p2_rejects_narrowed_subtarget() -> None:
    # OLD whole-section bare ref; NEW narrows to a momentti NOT in the OLD ref
    # (the over-attachment regression Codex flagged). OLD item '' is a prefix of
    # everything, but a DIFFERENT-momentti narrowing of a partial ref is rejected.
    old = _clause(
        _vg(VerbKind.LISATA, _ref("4", sub=SurfaceSubRef(momentti=2, item="3")))
    )
    new = _clause(
        _vg(
            VerbKind.LISATA,
            _ins("4", sub=SurfaceSubRef(momentti=5, item="3a")),  # momentti changed
        )
    )
    assert not is_source_witnessed_additive_recovery(old, new)


def test_p2_rejects_item_replaced_not_extended() -> None:
    # NEW item '9z' does NOT start with OLD item '3' — a replacement, not a refine.
    old = _clause(
        _vg(VerbKind.LISATA, _ref("4", sub=SurfaceSubRef(momentti=1, item="3")))
    )
    new = _clause(
        _vg(VerbKind.LISATA, _ins("4", sub=SurfaceSubRef(momentti=1, item="9z")))
    )
    assert not is_source_witnessed_additive_recovery(old, new)


def test_p2_rejects_non_source_witnessed_addition() -> None:
    # NEW adds an insertion whose rule_id is NOT in the allowed recovery set
    # (a hallucinated/contextual node, Codex Q1 counterexample). Stays unclassified.
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5")))
    new = _clause(
        _vg(
            VerbKind.MUUTTAA,
            _ref("5"),
            _ins("99", sub=SurfaceSubRef(momentti=1, item="1"),
                 rule="fi.section_ref"),  # not a recovery rule
        )
    )
    assert not is_source_witnessed_additive_recovery(old, new)


def test_p2_rejects_addition_with_no_source_span() -> None:
    # A NEW-only insertion with witness.source_span=None is not source-witnessed
    # within the johtolause bounds (the fi.jolloin_renumber move-group shape).
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5")))
    new = _clause(
        _vg(
            VerbKind.MUUTTAA,
            _ref("5"),
            _ins("8", sub=SurfaceSubRef(momentti=1, item="1"),
                 rule="fi.insertion_section", span=None),
        )
    )
    assert not is_source_witnessed_additive_recovery(old, new)


def test_p2_rejects_new_only_non_insertion_verb_group() -> None:
    # NEW adds a whole verb group OLD lacks whose nodes are bare refs, not
    # source-witnessed insertions (the SIIRTAA move-recovery correction shape).
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5")))
    new = _clause(
        _vg(VerbKind.MUUTTAA, _ref("5")),
        _vg(VerbKind.SIIRTAA, _ref("28", rule="fi.jolloin_renumber", span=None)),
    )
    assert not is_source_witnessed_additive_recovery(old, new)


def test_p2_rejects_span_out_of_bounds() -> None:
    # A NEW-only insertion whose source span lies beyond the consumed token count
    # is not within the johtolause bounds.
    old = _clause(_vg(VerbKind.MUUTTAA, _ref("5")), consumed=10)
    new = _clause(
        _vg(
            VerbKind.MUUTTAA,
            _ref("5"),
            _ins("8", sub=SurfaceSubRef(momentti=1, item="1"), span=(20, 30)),
        ),
        consumed=10,
    )
    assert not is_source_witnessed_additive_recovery(old, new)
