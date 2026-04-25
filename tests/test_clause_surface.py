"""Tests for the ClauseSurface types and resolver.

Validates that the resolver can reproduce the parser's backref/valio
resolution from surface nodes.
"""

from __future__ import annotations

from lawvm.core.semantic_types import FacetKind, StructuralAction
from lawvm.finland.johtolause.clause_surface import (
    parse_item_shift_after_repeal_clauses,
    parse_item_shift_clauses,
    parse_named_row_clauses,
    SubRef,
    SurfaceBackref,
    SurfaceClause,
    SurfaceTarget,
    SurfaceValioRef,
    SurfaceVerbGroup,
    resolve,
)
from lawvm.finland.johtolause.parsed_op_clause_ast import build_clause_ast
from lawvm.finland.johtolause.types import ParsedOp
from typing import Optional


def _op(
    verb: str,
    kind: str,
    chapter: str,
    number: str,
    momentti: int = 0,
    item: str = "",
    facet: Optional[FacetKind] = None,
    part: str = "",
) -> ParsedOp:
    """Shorthand for creating a ParsedOp."""
    op = ParsedOp(
        verb=verb,
        kind=kind,
        chapter=chapter,
        number=number,
        momentti=momentti,
        item=item,
        facet=facet,
        raw="",
        part=part,
    )
    op.raw = op.code()
    return op


def _target(*ops: ParsedOp) -> SurfaceTarget:
    """Create a SurfaceTarget batch from one or more ParsedOps."""
    return SurfaceTarget(ops=ops)


class TestResolveSurfaceBackref:
    """Test backref resolution from surface nodes."""

    def test_singular_backref_inherits_last_section(self):
        """'mainitun pykälän 2 momentti' resolves to the last section."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        _target(_op("M", "P", "", "7")),
                        SurfaceBackref(
                            verb="M",
                            is_singular=True,
                            sub_refs=(SubRef(momentti=2),),
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 2
        assert ops[0].code() == "M P 7"
        assert ops[1].code() == "M P 7 2"

    def test_plural_backref_from_single_batch(self):
        """Plural backref resolves to all sections in the last batch.

        'muutetaan 5 ja 6 §:n ... mainittujen pykälien 1 momentti'
        → "5 ja 6 §" parsed as ONE target batch (one number_list).
        """
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        # Single batch: "5 ja 6 §" parsed together
                        _target(
                            _op("M", "P", "", "5"),
                            _op("M", "P", "", "6"),
                        ),
                        SurfaceBackref(
                            verb="M",
                            is_singular=False,
                            sub_refs=(SubRef(momentti=1),),
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 4
        assert ops[0].code() == "M P 5"
        assert ops[1].code() == "M P 6"
        # Plural backref: both sections get the sub-ref
        assert ops[2].code() == "M P 6 1"
        assert ops[3].code() == "M P 5 1"

    def test_plural_backref_from_separate_batches_sees_last_only(self):
        """Plural backref from separate batches only sees the last batch.

        'muutetaan 5 §, 6 § ja mainittujen pykälien 1 momentti'
        → "5 §" and "6 §" are separate _target() calls.
        Backref only sees the last batch (6 §).
        """
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        _target(_op("M", "P", "", "5")),
                        _target(_op("M", "P", "", "6")),
                        SurfaceBackref(
                            verb="M",
                            is_singular=False,
                            sub_refs=(SubRef(momentti=1),),
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 3
        assert ops[0].code() == "M P 5"
        assert ops[1].code() == "M P 6"
        # Only section 6 (last batch)
        assert ops[2].code() == "M P 6 1"

    def test_singular_backref_with_chapter(self):
        """Backref inherits chapter from the referenced section."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        _target(_op("M", "P", "3", "7")),
                        SurfaceBackref(
                            verb="M",
                            is_singular=True,
                            sub_refs=(SubRef(momentti=2, special="otsikko"),),
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 2
        assert ops[0].code() == "M P L:3 7"
        assert ops[1].code() == "M P L:3 7 o"

    def test_backref_whole_section(self):
        """'mainittu pykälä' with no sub-ref → whole section."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        _target(_op("M", "P", "", "11")),
                        SurfaceBackref(
                            verb="M",
                            is_singular=True,
                            sub_refs=(SubRef(),),  # whole section
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 2
        assert ops[0].code() == "M P 11"
        assert ops[1].number == "11"
        assert ops[1].momentti == 0

    def test_backref_with_no_preceding_sections_is_empty(self):
        """Backref with no preceding section ops produces no output."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        SurfaceBackref(
                            verb="M",
                            is_singular=True,
                            sub_refs=(SubRef(momentti=2),),
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 0


class TestResolveSurfaceValioRef:
    """Test valio heading reference resolution."""

    def test_valio_ref_resolves_to_heading_op(self):
        """Valio ref emits otsikko op for the last section."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        _target(_op("M", "P", "2", "5", momentti=3)),
                        SurfaceValioRef(verb="M"),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 2
        assert ops[0].code() == "M P L:2 5 3"
        assert ops[1].facet == FacetKind.HEADING
        assert ops[1].number == "5"
        assert ops[1].chapter == "2"

    def test_valio_ref_with_batch_of_sections(self):
        """Valio ref expands to heading ops for all sections in last batch."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="K",
                    nodes=(
                        # Single batch: "3 ja 4 §" parsed together
                        _target(
                            _op("K", "P", "", "3"),
                            _op("K", "P", "", "4"),
                        ),
                        SurfaceValioRef(verb="K"),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 4
        assert ops[2].number == "4"
        assert ops[2].facet == FacetKind.HEADING
        assert ops[3].number == "3"
        assert ops[3].facet == FacetKind.HEADING

    def test_valio_ref_separate_batches_sees_last_only(self):
        """Valio ref with separate batches only sees the last batch."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="K",
                    nodes=(
                        _target(_op("K", "P", "", "3")),
                        _target(_op("K", "P", "", "4")),
                        SurfaceValioRef(verb="K"),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 3
        assert ops[2].number == "4"
        assert ops[2].facet == FacetKind.HEADING


class TestResolveMultiVerbGroup:
    """Test resolution across multiple verb groups."""

    def test_backref_across_verb_groups(self):
        """Backref in second verb group sees the last batch from its own group."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(_target(_op("M", "P", "", "3")),),
                ),
                SurfaceVerbGroup(
                    verb="K",
                    nodes=(
                        _target(_op("K", "P", "", "5")),
                        SurfaceBackref(
                            verb="K",
                            is_singular=True,
                            sub_refs=(SubRef(momentti=1),),
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 3
        assert ops[0].code() == "M P 3"
        assert ops[1].code() == "K P 5"
        assert ops[2].code() == "K P 5 1"

    def test_mixed_nodes_preserve_order(self):
        """Resolved ops maintain source order."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        _target(_op("M", "P", "", "1")),
                        _target(_op("M", "P", "", "2")),
                        SurfaceBackref(
                            verb="M",
                            is_singular=True,
                            sub_refs=(SubRef(momentti=3),),
                        ),
                        _target(_op("M", "P", "", "4")),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 4
        assert [op.number for op in ops] == ["1", "2", "2", "4"]
        assert ops[2].momentti == 3

    def test_parse_surface_keeps_inline_same_label_move_tail_semantics(self):
        """Inline same-label move tails should be owned natively by the surface."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = "muutetaan 31–34 §, joista 33 ja 34 § samalla siirretään 5 lukuun"
        clause = parse_surface(apply_annotations(tokenize(text)))
        ops = resolve(clause)

        moved = [op for op in ops if op.number in {"33", "34"} and op.chapter == "5"]

        assert len(moved) == 4
        assert [op.number for op in moved] == ["33", "34", "33", "34"]
        assert all(op.move_clause_target_unit_kind == "chapter" for op in moved)

    def test_parse_surface_keeps_direct_same_label_move_semantics(self):
        """Standalone same-label move clauses should survive through the clause waist."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = "muutetaan 85 b §, siirretään muutettu 85 b § 9 lukuun"
        clause = parse_surface(apply_annotations(tokenize(text)))
        ops = resolve(clause)

        assert len(ops) == 1
        moved = ops[0]
        assert moved.verb == "M"
        assert moved.kind == "P"
        assert moved.number == "85b"
        assert moved.chapter == "9"
        assert moved.move_clause_target_unit_kind == "chapter"

    def test_parse_surface_keeps_mixed_batch_move_tail_for_2014_1429(self):
        """Mixed repeal batches must retag 29e into the destination chapter."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = (
            "kumotaan 1–4 ja 6–8 §, 12 §, 5 luvun otsikko, 27–29 §, 5 a luvun otsikko, "
            "29 a–29 d §, 29 e §, joka samalla siirretään 5 b lukuun, sekä 29 g ja 30–32 §"
        )
        clause = parse_surface(apply_annotations(tokenize(text)))
        ops = resolve(clause)

        moved_29e = [op for op in ops if op.number == "29e"]
        assert len(moved_29e) == 1
        assert moved_29e[0].chapter == "5b"
        assert moved_29e[0].move_clause_target_unit_kind == "chapter"

    def test_parse_surface_keeps_leading_destination_chapter_on_same_clause_move(self):
        """'lakiin uusi 3 a luku, johon samalla siirretään muutettu 11 §' carries the chapter lead-in."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = "lakiin uusi 3 a luku, johon samalla siirretään muutettu 11 §"
        clause = parse_surface(apply_annotations(tokenize(text)))

        assert len(clause.verb_groups) == 1
        batch = clause.verb_groups[0].nodes[0]
        assert isinstance(batch, SurfaceTarget)
        moved = batch.ops[0]
        assert moved.kind == "P"
        assert moved.number == "11"
        assert moved.chapter == "3a"
        assert moved.move_clause_target_unit_kind == "chapter"

    def test_parse_surface_keeps_direct_section_relabel_semantics(self):
        """Direct section relabels should preserve full destination path at the clause waist."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = "muutetaan 7 luvun 73 §:ää, joka siirretään 61 §:ksi"
        clause = parse_surface(apply_annotations(tokenize(text)))
        ops = resolve(clause)

        assert len(ops) == 2
        replace_op, relabel_op = ops
        assert replace_op.verb == "M"
        assert replace_op.chapter == "7"
        assert replace_op.number == "73"
        assert relabel_op.verb == "S"
        assert relabel_op.chapter == "7"
        assert relabel_op.number == "73"
        assert relabel_op.renumber_dest == "61"
        assert relabel_op.renumber_dest_chapter == "7"

    def test_parse_surface_keeps_old_move_destination_part_semantics(self):
        """Old move continuations should carry destination part natively."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = "siirretään I osaan, II osan 4 luvun otsikko sekä 38-40 §"
        clause = parse_surface(apply_annotations(tokenize(text)))
        ops = resolve(clause)

        assert [op.code() for op in ops] == [
            "S L O:II 4 o",
            "S P O:II L:4 38",
            "S P O:II L:4 39",
            "S P O:II L:4 40",
        ]
        assert all(op.renumber_dest_part == "I" for op in ops)

    def test_parse_surface_keeps_relative_move_to_part_tail_semantics(self):
        """Relative-clause move tails to a part should retarget prior section refs."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = "muutetaan I osa, 30 ja 31§, jotka samalla siirretään I osaan"
        clause = parse_surface(apply_annotations(tokenize(text)))
        ops = resolve(clause)

        moved = [op for op in ops if op.kind == "P" and op.number in {"30", "31"}]

        assert [op.number for op in moved] == ["30", "31"]
        assert all(op.part == "I" for op in moved)
        assert all(op.move_clause_target_unit_kind == "part" for op in moved)

    def test_parse_surface_keeps_provenance_heavy_relative_move_to_part_tail(self):
        """Provenance after a part ref should not swallow later moved sections."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        text = (
            "muutetaan I osa, sellaisena kuin se on siihen myöhemmin tehtyine muutoksineen, "
            "30 ja 31§, jotka samalla siirretään I osaan"
        )
        clause = parse_surface(apply_annotations(tokenize(text)))
        ops = resolve(clause)

        moved = [op for op in ops if op.kind == "P" and op.number in {"30", "31"}]

        assert [op.number for op in moved] == ["30", "31"]
        assert all(op.part == "I" for op in moved)
        assert all(op.move_clause_target_unit_kind == "part" for op in moved)


class TestItemShiftClauseParsing:
    """Test typed item-shift parsing at the clause waist."""

    def test_parse_item_shift_clauses_parses_basic_family(self) -> None:
        got = parse_item_shift_clauses("kumotaan 2 §:n 1 momentin d kohdan, jolloin kohdat e-h muuttuvat kohdiksi d-g")

        assert len(got) == 1
        clause = got[0]
        assert clause.target_section == "2"
        assert clause.target_paragraph == 1
        assert clause.source_items == ("e", "f", "g", "h")
        assert clause.target_items == ("d", "e", "f", "g")

    def test_parse_item_shift_after_repeal_clauses_parses_extra_repeal(self) -> None:
        got = parse_item_shift_after_repeal_clauses(
            "kumotaan 2 §:n 1 momentin d kohdan, jolloin kohdat e-h muuttuvat kohdiksi d-g ja 2 momentin, muutetaan"
        )

        assert len(got) == 1
        clause = got[0]
        assert clause.clause.target_section == "2"
        assert clause.clause.target_paragraph == 1
        assert clause.extra_repeal_target_paragraph == 2


class TestNamedRowClauseParsing:
    """Test typed named-row parsing at the clause waist."""

    def test_parse_named_row_clauses_parses_mixed_family(self) -> None:
        got = parse_named_row_clauses(
            "kumotaan käräjäoikeuksien kanslioiden ja istuntopaikkojen sijainnista "
            "annetun päätöksen 1 §:n Iitin ja Juvan käräjäoikeuksia koskevat kohdat "
            "sekä muutetaan Kouvolan ja Mikkelin käräjäoikeuksia koskevat kohdat seuraavasti:"
        )

        assert len(got) == 2
        assert got[0].action == StructuralAction.REPEAL
        assert got[0].named_targets == ("iitin", "juvan")
        assert got[0].target_section == "1"
        assert got[1].action == StructuralAction.REPLACE
        assert got[1].named_targets == ("kouvolan", "mikkelin")
        assert got[1].target_section == "1"

    def test_parse_named_row_clauses_parses_single_replace_clause(self) -> None:
        got = parse_named_row_clauses("muutetaan päätöksen 1 §:n Iisalmen käräjäoikeutta koskevan kohdan seuraavasti:")

        assert len(got) == 1
        assert got[0].action == StructuralAction.REPLACE
        assert got[0].named_targets == ("iisalmen",)
        assert got[0].target_section == "1"


class TestLowerToAst:
    """Verify lower_to_ast produces equivalent legal ops."""

    def test_all_curated_cases_legal_op_equivalence(self):
        """lower_to_ast legal ops == resolve + Finland ParsedOp bridge legal ops."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import (
            parse_surface,
            resolve,
            lower_to_ast,
        )
        from lawvm.core.clause_ast import clause_ast_to_legal_ops
        from lawvm.finland.johtolause.curated_cases import CURATED_CASES

        failures = []
        for case in CURATED_CASES:
            text = case["text"]
            assert isinstance(text, str)
            tokens = tokenize(text)
            filtered = apply_annotations(tokens)
            clause = parse_surface(filtered)

            # Bridge path: resolve → build_clause_ast → legal ops
            resolved = resolve(clause)
            ops_old = clause_ast_to_legal_ops(build_clause_ast(resolved, text))

            # New path: lower_to_ast → legal ops
            ops_new = clause_ast_to_legal_ops(lower_to_ast(clause))

            codes_old = sorted((str(lo.action), str(lo.target)) for lo in ops_old)
            codes_new = sorted((str(lo.action), str(lo.target)) for lo in ops_new)
            if codes_old != codes_new:
                failures.append(f"[{case['name']}] old={len(ops_old)} new={len(ops_new)}")

        assert not failures, "Legal op mismatches:\n" + "\n".join(failures)

    def test_lower_preserves_source_order(self):
        """lower_to_ast preserves verb group source order (not merged by verb)."""
        from lawvm.finland.johtolause.clause_surface import lower_to_ast

        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(verb="K", nodes=(_target(_op("K", "P", "", "3")),)),
                SurfaceVerbGroup(verb="M", nodes=(_target(_op("M", "P", "", "5")),)),
                SurfaceVerbGroup(verb="K", nodes=(_target(_op("K", "P", "", "7")),)),
            ),
        )
        ast = lower_to_ast(clause)
        assert len(ast.verb_groups) == 3
        from lawvm.core.semantic_types import StructuralAction
        assert ast.verb_groups[0].verb == StructuralAction.REPEAL
        assert ast.verb_groups[1].verb == StructuralAction.REPLACE
        assert ast.verb_groups[2].verb == StructuralAction.REPEAL


class TestParseSurfaceRoundTrip:
    """Verify parse_surface → resolve == parse for all curated cases."""

    @staticmethod
    def _round_trip_check(text: str) -> tuple[bool, str]:
        from lawvm.finland.johtolause.peg3 import tokenize, parse
        from lawvm.finland.johtolause.scan import apply_annotations
        from lawvm.finland.johtolause.clause_surface import parse_surface

        tokens = tokenize(text)
        filtered = apply_annotations(tokens)
        direct = parse(filtered)
        clause = parse_surface(filtered)
        resolved = resolve(clause)
        if len(resolved) != len(direct):
            return False, f"LEN {len(resolved)} vs {len(direct)}"
        for i, (r, d) in enumerate(zip(resolved, direct)):
            if r.code() != d.code():
                return False, f"TOK{i} {r.code()} vs {d.code()}"
        return True, "OK"

    def test_all_curated_cases(self):
        from lawvm.finland.johtolause.curated_cases import CURATED_CASES

        failures = []
        for case in CURATED_CASES:
            text = case["text"]
            assert isinstance(text, str)
            ok, msg = self._round_trip_check(text)
            if not ok:
                failures.append(f"[{case['name']}] {msg}")
        assert not failures, "Round-trip failures:\n" + "\n".join(failures)


class TestResolveRoundTrip:
    """Verify resolver matches parser output for curated corpus cases."""

    def test_provenance_with_backref_case(self):
        """'muutetaan 7 §, ... ja mainitun pykälän 2 momentti'

        This is the standalone backref pattern from the curated corpus.
        The parser resolves it to [M P 7, M P 7 2].
        """
        # Build the surface clause matching what the parser would emit
        # if it emitted surface nodes instead of resolved ops:
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb="M",
                    nodes=(
                        # "7 §" → direct target
                        _target(_op("M", "P", "", "7")),
                        # "mainitun pykälän 2 momentti" → unresolved backref
                        SurfaceBackref(
                            verb="M",
                            is_singular=True,
                            sub_refs=(SubRef(momentti=2),),
                        ),
                    ),
                ),
            ),
        )
        ops = resolve(clause)
        assert len(ops) == 2
        assert ops[0].code() == "M P 7"
        assert ops[1].code() == "M P 7 2"

        # Cross-check with actual parser output
        from lawvm.finland.johtolause.peg3 import tokenize, parse
        from lawvm.finland.johtolause.scan import apply_annotations

        text = "muutetaan 7 §, sellaisena kuin se on laissa 200/2022, ja mainitun pykälän 2 momentti"
        tokens = tokenize(text)
        filtered = apply_annotations(tokens)
        parser_ops = parse(filtered)

        assert len(parser_ops) == len(ops)
        for p, r in zip(parser_ops, ops):
            assert p.code() == r.code(), f"Parser={p.code()} Resolver={r.code()}"
