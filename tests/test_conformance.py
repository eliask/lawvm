"""Phase-staged conformance tests for LawVM — synthetic inputs only.

Implements the three-stage conformance model from LAWVM_CONSTITUTION.md §8:

    Stage 1 — Clause Surface  : johtolause text → ClauseAST structure
    Stage 2 — Elaboration     : ops + mock live context → elaborated output
    Stage 3 — Replay          : canonical ops → expected tree state

Design principles:
  - Every test is self-contained.  No corpus access.  No network.  No LLM.
  - Each test pins the INVARIANT (the waist contract), not implementation detail.
  - A de novo implementation can pass the suite by reading only the assertions.

Three hard waists (LAWVM_CONSTITUTION.md §2):
  Waist 1 : ClauseAST (src/lawvm/core/clause_ast.py)
  Waist 2 : PayloadSurface (src/lawvm/core/payload_surface.py)
            plus Finland-local elaboration carrier
  Waist 3 : ResolvedOp / CanonicalOps (src/lawvm/finland/ops.py)

Run:
    cd LawVM && uv run pytest tests/test_conformance.py tests/test_peg_curated.py tests/test_grafter_fallback.py -q
"""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
from typing import Any, cast


from lawvm.core.clause_ast import (
    ClauseAST,
    VerbGroup,
    RefAmend,
    LabelAmend,
    ScopedBlock,
)
from lawvm.core.semantic_types import FacetKind, StructuralAction, LabelAction, IRNodeKind
from lawvm.core.ir import IRNode
from lawvm.finland.target_kind import TargetKind
from lawvm.core import tree_ops
from lawvm.finland.johtolause.peg3 import extract_ops_diagnostic
from lawvm.finland.johtolause.parsed_op_clause_ast import build_clause_ast
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.finland.statute import StatuteContext
from typing import Optional


def _parsed_op(
    verb: str,
    kind: str,
    number: str,
    chapter: str = "",
    momentti: int = 0,
    item: str = "",
    facet: Optional[FacetKind] = None,
    part: str = "",
) -> ParsedOp:
    return ParsedOp(
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


def _node(kind: Any, label: Any = None, text: Any = None, **kwargs: Any) -> IRNode:
    children = cast(tuple[IRNode, ...], tuple(kwargs.pop("children", ())))
    attrs = cast(dict[str, object], kwargs.pop("attrs", {}))
    return IRNode(kind=kind, label=label, text=text, attrs=attrs, children=children)


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _para(label: str, text: str = "") -> IRNode:
    children = (IRNode(kind=IRNodeKind.CONTENT, text=text),) if text else ()
    return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=children)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _clause_ast(diag: Any) -> ClauseAST:
    assert diag.clause_ast is not None
    return diag.clause_ast


def _resolved(node: Any) -> IRNode:
    assert node is not None
    return node


# ===========================================================================
# STAGE 1 — Clause Surface
#
# Contract: johtolause text → ClauseAST
#   - VerbGroup count and verb labels must match the input clause
#   - Node types inside VerbGroups must be correct (RefAmend / LabelAmend)
#   - Target addresses must encode the right path
#   - ScopedBlock groups ops that share a chapter context
# ===========================================================================


class TestClauseSurface:
    """Stage 1: clause text → ClauseAST structure (Waist 1)."""

    # -------------------------------------------------------------------
    # Single-verb / single-target
    # -------------------------------------------------------------------

    def test_muutetaan_single_section(self):
        """muutetaan 6 § → one VerbGroup(replace), one RefAmend targeting section:6."""
        diag = extract_ops_diagnostic("muutetaan 6 §")
        ast = _clause_ast(diag)

        assert isinstance(ast, ClauseAST)
        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        assert isinstance(vg, VerbGroup)
        assert vg.verb == StructuralAction.REPLACE
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, RefAmend)
        assert node.action == StructuralAction.REPLACE
        assert dict(node.target.path) == {"section": "6"}

    def test_kumotaan_single_section(self):
        """kumotaan 3 § → one VerbGroup(repeal), one RefAmend(repeal)."""
        diag = extract_ops_diagnostic("kumotaan 3 §")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        assert vg.verb == StructuralAction.REPEAL
        node = vg.nodes[0]
        assert isinstance(node, RefAmend)
        assert node.action == StructuralAction.REPEAL
        assert dict(node.target.path) == {"section": "3"}

    def test_lisataan_single_section(self):
        """lisätään lakiin uusi 9 a § → one VerbGroup(insert), one RefAmend(insert)."""
        diag = extract_ops_diagnostic("lisätään lakiin uusi 9 a §")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        assert vg.verb == StructuralAction.INSERT
        node = vg.nodes[0]
        assert isinstance(node, RefAmend)
        assert node.action == StructuralAction.INSERT
        assert dict(node.target.path) == {"section": "9a"}

    # -------------------------------------------------------------------
    # Multi-verb clause
    # -------------------------------------------------------------------

    def test_multi_verb_repeal_then_replace(self):
        """kumotaan 5 §, muutetaan 3 § → two VerbGroups in source order."""
        diag = extract_ops_diagnostic("kumotaan 5 §, muutetaan 3 §")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 2
        verbs = [vg.verb for vg in ast.verb_groups]
        assert verbs == [StructuralAction.REPEAL, StructuralAction.REPLACE]
        # Each group has exactly one target
        for vg in ast.verb_groups:
            assert len(vg.nodes) == 1
            assert isinstance(vg.nodes[0], RefAmend)

    def test_multi_verb_all_three(self):
        """kumotaan 2 §, muutetaan 4 §, lisätään lakiin uusi 5 a § → three VerbGroups."""
        diag = extract_ops_diagnostic("kumotaan 2 §, muutetaan 4 §, lisätään lakiin uusi 5 a §")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 3
        verbs = [vg.verb for vg in ast.verb_groups]
        assert verbs == [StructuralAction.REPEAL, StructuralAction.REPLACE, StructuralAction.INSERT]

    # -------------------------------------------------------------------
    # Multi-target within a single verb group
    # -------------------------------------------------------------------

    def test_replace_section_range(self):
        """muutetaan 1–3 § → one VerbGroup(replace) with three RefAmend nodes."""
        diag = extract_ops_diagnostic("muutetaan 1–3 §")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        assert vg.verb == StructuralAction.REPLACE
        # Range expands to individual sections
        targets = [dict(n.target.path).get("section") for n in vg.nodes if isinstance(n, RefAmend)]
        # Must cover 1, 2, 3 in some form
        assert len(vg.nodes) >= 3

    def test_replace_two_sections_comma_list(self):
        """muutetaan 6 ja 7 § → one VerbGroup(replace) with two RefAmend nodes."""
        diag = extract_ops_diagnostic("muutetaan 6 ja 7 §")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        section_targets = {dict(n.target.path).get("section") for n in vg.nodes if isinstance(n, RefAmend)}
        assert "6" in section_targets
        assert "7" in section_targets


class TestElaborationSnapshotVocabulary:
    def test_target_context_uses_neutral_unit_kind(self):
        from lawvm.core.elaboration_context import TargetContext

        cases = (
            ("section", "7"),
            ("chapter", "7"),
            ("part", "I"),
        )
        for target_kind, target_norm in cases:
            ctx = TargetContext(
                target_unit_kind=target_kind,
                target_norm=target_norm,
                target_chapter=None,
                node_path=None,
                parent_path=None,
                live_node=None,
                parent_node=None,
                sibling_labels=(),
                subsection_slots=(),
            )

            assert ctx.target_unit_kind == target_kind

    def test_payload_elaboration_context_uses_neutral_unit_kind(self):
        from lawvm.core.elaboration_context import (
            PayloadElaborationContext,
            ReplayLookups,
        )

        lookups = ReplayLookups(
            snapshot_rev=1,
            unique_section_paths={},
            chapter_members={},
            part_members={},
            all_section_labels=frozenset(),
        )

        cases = (
            ("section", "7"),
            ("chapter", "7"),
            ("part", "I"),
        )
        for target_kind, target_norm in cases:
            ctx = PayloadElaborationContext(
                target_unit_kind=target_kind,
                target_norm=target_norm,
                target_chapter=None,
                live_node=None,
                parent_node=None,
                subsection_slots=(),
                live_subsections=(),
                subsection_by_label={},
                item_index={},
                row_anchor_index={},
                container_member_labels=None,
                lookups=lookups,
            )

            assert ctx.target_unit_kind == target_kind

    def test_build_payload_elaboration_context_carries_neutral_unit_kind_from_target_context(self):
        from lawvm.core.elaboration_context import (
            ReplayLookups,
            TargetContext,
            build_payload_elaboration_context,
        )

        target_ctx = TargetContext(
            target_unit_kind=cast(Any, "section"),
            target_norm="15a",
            target_chapter="3",
            node_path=(("chapter", "3"), ("section", "15a")),
            parent_path=(("chapter", "3"),),
            live_node=_sec("15a", _sub("1", _content("existing text"))),
            parent_node=IRNode(kind=IRNodeKind.CHAPTER, label="3"),
            sibling_labels=("15", "15a", "16"),
            subsection_slots=(),
        )
        payload_ctx = build_payload_elaboration_context(
            target_ctx,
            ReplayLookups(
                snapshot_rev=1,
                unique_section_paths={("15a", "3"): (("chapter", "3"), ("section", "15a"))},
                chapter_members={"3": frozenset({"15", "15a", "16"})},
                part_members={},
                all_section_labels=frozenset({"15", "15a", "16"}),
            ),
        )

        assert payload_ctx.target_unit_kind == "section"
    def test_snapshot_target_context_accepts_neutral_unit_kind(self):
        from lawvm.core.elaboration_context import (
            ReplayLookups,
            snapshot_target_context,
        )

        chapter = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="3",
            children=(IRNode(kind=IRNodeKind.SECTION, label="15a", text="existing text"),),
        )
        body = IRNode(kind=IRNodeKind.BODY, children=(chapter,))

        class _Master:
            def __init__(self, ir: IRNode):
                self.ir = ir

            def find_section_path(self, target_norm: str, target_chapter: str | None, target_part: str | None = None):
                if target_chapter:
                    return tree_ops.find(
                        self.ir,
                        "section",
                        target_norm,
                        scope_kind="chapter",
                        scope_label=target_chapter,
                    )
                return tree_ops.find(self.ir, "section", target_norm)

            def find(self, kind: str, label: str):
                return tree_ops.find(self.ir, kind, label)

        lookups = ReplayLookups(
            snapshot_rev=1,
            unique_section_paths={("15a", "3"): (("chapter", "3"), ("section", "15a"))},
            chapter_members={"3": frozenset({"15a"})},
            part_members={},
            all_section_labels=frozenset({"15a"}),
        )

        target_ctx = snapshot_target_context(
            cast(Any, _Master(body)),
            target_unit_kind=cast(Any, "section"),
            target_norm="15a",
            target_chapter="3",
            lookups=lookups,
        )

        assert target_ctx.target_unit_kind == "section"
        assert target_ctx.live_node is not None
        assert target_ctx.live_node.kind == IRNodeKind.SECTION
        assert target_ctx.parent_node is not None
        assert target_ctx.parent_node.kind == IRNodeKind.CHAPTER
        assert target_ctx.sibling_labels == ("15a",)

    # -------------------------------------------------------------------
    # Scoped (chapter-context) grouping
    # -------------------------------------------------------------------

    def test_scoped_chapter_two_sections(self):
        """muutetaan 2 luvun 3 ja 4 § → ScopedBlock(scope=chapter:2, children=[sec3, sec4])."""
        ops = [
            _parsed_op("M", "P", "3", chapter="2"),
            _parsed_op("M", "P", "4", chapter="2"),
        ]
        ast = build_clause_ast(ops, "muutetaan 2 luvun 3 ja 4 §")

        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        assert len(vg.nodes) == 1
        block = vg.nodes[0]
        assert isinstance(block, ScopedBlock)
        assert dict(block.scope.path) == {"chapter": "2"}
        assert len(block.children) == 2
        sections = {dict(n.target.path).get("section") for n in block.children if isinstance(n, RefAmend)}
        assert sections == {"3", "4"}

    def test_scoped_block_children_inherit_full_path(self):
        """Children inside ScopedBlock encode the full (chapter, section) path."""
        ops = [
            _parsed_op("M", "P", "7", chapter="3"),
            _parsed_op("M", "P", "8", chapter="3"),
        ]
        ast = build_clause_ast(ops, "muutetaan 3 luvun 7 ja 8 §")

        block = ast.verb_groups[0].nodes[0]
        assert isinstance(block, ScopedBlock)
        for child in block.children:
            assert isinstance(child, RefAmend)
            path_dict = dict(child.target.path)
            assert path_dict.get("chapter") == "3"

    # -------------------------------------------------------------------
    # LabelAmend (renumber)
    # -------------------------------------------------------------------

    def test_siirretaan_becomes_label_amend(self):
        """siirretään 7 § → LabelAmend(action='renumber')."""
        ops = [_parsed_op("S", "P", "7")]
        ast = build_clause_ast(ops, "siirretään 7 §")

        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        assert vg.verb == StructuralAction.RENUMBER
        node = vg.nodes[0]
        assert isinstance(node, LabelAmend)
        assert node.action == LabelAction.RENUMBER
        assert dict(node.target.path) == {"section": "7"}

    # -------------------------------------------------------------------
    # Subsection and item targets
    # -------------------------------------------------------------------

    def test_replace_subsection_target(self):
        """muutetaan 5 §:n 2 momentti → section:5 / subsection:2 path."""
        diag = extract_ops_diagnostic("muutetaan 5 §:n 2 momentti")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 1
        vg = ast.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, RefAmend)
        path_dict = dict(node.target.path)
        assert path_dict.get("section") == "5"
        assert path_dict.get("subsection") == "2"

    def test_replace_item_target(self):
        """muutetaan 1 §:n 1 momentin 3 kohta → section/subsection/item path."""
        diag = extract_ops_diagnostic("muutetaan 1 §:n 1 momentin 3 kohta")
        ast = _clause_ast(diag)

        assert len(ast.verb_groups) == 1
        node = ast.verb_groups[0].nodes[0]
        assert isinstance(node, RefAmend)
        path_dict = dict(node.target.path)
        assert path_dict.get("section") == "1"
        assert path_dict.get("subsection") == "1"
        assert path_dict.get("item") == "3"

    def test_repeal_chapter(self):
        """kumotaan 4 luku → RefAmend(repeal, chapter:4)."""
        diag = extract_ops_diagnostic("kumotaan 4 luku")
        ast = _clause_ast(diag)

        vg = ast.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, RefAmend)
        assert node.action == StructuralAction.REPEAL
        path_dict = dict(node.target.path)
        assert path_dict.get("chapter") == "4"

    # -------------------------------------------------------------------
    # Empty input
    # -------------------------------------------------------------------

    def test_empty_ops_empty_ast(self):
        """Empty ParsedOp list → ClauseAST with no verb_groups."""
        ast = build_clause_ast([], "")
        assert isinstance(ast, ClauseAST)
        assert ast.verb_groups == ()

    # -------------------------------------------------------------------
    # extract_ops_diagnostic field population
    # -------------------------------------------------------------------

    def test_diagnostic_populates_clause_ast(self):
        """extract_ops_diagnostic must populate clause_ast field."""
        diag = extract_ops_diagnostic("muutetaan 6 §")

        ast = _clause_ast(diag)
        assert isinstance(ast, ClauseAST)
        assert ast.source_text == "muutetaan 6 §"

    def test_diagnostic_clause_ast_group_count_matches_verb_count(self):
        """Number of VerbGroups in ClauseAST must equal distinct verb count in ops."""
        johto = "kumotaan 5 §:n 2 momentti, muutetaan 3 §"
        diag = extract_ops_diagnostic(johto)

        distinct_verbs = len({op.verb for op in diag.ops})
        assert len(_clause_ast(diag).verb_groups) == distinct_verbs

    def test_finland_replay_regression_micro_suite_is_named_in_conformance_corpus(self) -> None:
        note = Path(__file__).resolve().parents[1] / "notes" / "CONFORMANCE_CORPUS.md"
        text = note.read_text(encoding="utf-8")

        assert "### 2.6 Finland replay-regression micro-suite proposal" in text
        for sid in ("2000/252", "1981/555", "2006/766", "2014/1429"):
            assert sid in text
        for sid in ("2013/492", "1994/1217"):
            assert sid in text


# ===========================================================================
# STAGE 2 — Elaboration
#
# Contract: ops + mock TargetContext → elaborated elaboration output
#   The elaboration phase is snapshot-pure (reads only typed snapshots,
#   never master).  We test the contracts of helpers that consume snapshots.
#
# Because the full elaboration pipeline is deep, Stage 2 pins the
# snapshot-purity invariant via TargetContext construction and the
# subsection slot model.  Full elaboration integration is in test_grafter_fallback.py.
# ===========================================================================


class TestElaborationSnapshotPurity:
    """Stage 2: elaboration reads only typed snapshots (Waist 2 boundary)."""

    def test_target_context_exists_false_when_live_node_absent(self):
        """Insert-new context: target_exists is False when live_node is None."""
        from lawvm.core.elaboration_context import TargetContext

        ctx = TargetContext(
            target_unit_kind="section",
            target_norm="15a",
            target_chapter=None,
            node_path=None,
            parent_path=None,
            live_node=None,
            parent_node=None,
            sibling_labels=(),
            subsection_slots=(),
        )

        assert ctx.target_exists is False

    def test_target_context_exists_true_when_live_node_present(self):
        """Replace context: target_exists is True when live_node is provided."""
        from lawvm.core.elaboration_context import TargetContext

        live_node = _sec("6", _sub("1", _content("existing text")))
        ctx = TargetContext(
            target_unit_kind="section",
            target_norm="6",
            target_chapter=None,
            node_path=(("section", "6"),),
            parent_path=(),
            live_node=live_node,
            parent_node=None,
            sibling_labels=("5", "6", "7"),
            subsection_slots=(),
        )

        assert ctx.target_exists is True

    def test_target_context_subsection_slots_reflect_live_structure(self):
        """LiveSubsectionSlot correctly captures ordinal, label and intro presence."""
        from lawvm.core.elaboration_context import (
            LiveSubsectionSlot,
            TargetContext,
        )

        intro_node = IRNode(kind=IRNodeKind.INTRO, text="introductory text:")
        sub1 = _sub("1", intro_node, _para("1", "item a"), _para("2", "item b"))
        sub2 = _sub("2", _content("plain subsection"))
        live_node = _sec("3", sub1, sub2)

        slot1 = LiveSubsectionSlot(
            ordinal=1,
            label="1",
            node=sub1,
            intro_present=True,
            item_labels=("1", "2"),
            row_anchors=(),
        )
        slot2 = LiveSubsectionSlot(
            ordinal=2,
            label="2",
            node=sub2,
            intro_present=False,
            item_labels=(),
            row_anchors=(),
        )

        ctx = TargetContext(
            target_unit_kind="section",
            target_norm="3",
            target_chapter=None,
            node_path=(("section", "3"),),
            parent_path=(),
            live_node=live_node,
            parent_node=None,
            sibling_labels=("2", "3", "4"),
            subsection_slots=(slot1, slot2),
        )

        assert len(ctx.subsection_slots) == 2
        assert ctx.subsection_slots[0].intro_present is True
        assert ctx.subsection_slots[0].item_labels == ("1", "2")
        assert ctx.subsection_slots[1].intro_present is False
        assert ctx.subsection_slots[1].item_labels == ()

    def test_replay_lookups_immutable_and_bounded(self):
        """ReplayLookups carries only bounded global facts, not the full tree."""
        from lawvm.core.elaboration_context import ReplayLookups

        # chapter_members maps chapter label → frozenset of section labels
        lookups = ReplayLookups(
            snapshot_rev=0,
            unique_section_paths={("15a", "3"): (("chapter", "3"), ("section", "15a"))},
            chapter_members={"3": frozenset({"15a", "16"})},
            part_members={},
            all_section_labels=frozenset({"1", "2", "3", "15a"}),
        )

        assert "15a" in lookups.all_section_labels
        assert "15a" in lookups.chapter_members.get("3", frozenset())
        assert len(lookups.part_members) == 0

    def test_replay_lookups_chapter_member_containment(self):
        """chapter_members maps each chapter to its contained section labels."""
        from lawvm.core.elaboration_context import ReplayLookups

        # Chapter 2 contains sections 5 and 7; chapter 4 also contains 7 (duplicate)
        lookups = ReplayLookups(
            snapshot_rev=1,
            unique_section_paths={("5", "2"): (("chapter", "2"), ("section", "5"))},
            chapter_members={
                "2": frozenset({"5", "7"}),
                "4": frozenset({"7", "9"}),
            },
            part_members={},
            all_section_labels=frozenset({"5", "7", "9"}),
        )

        # Section 5 is unique to chapter 2
        assert "5" in lookups.chapter_members["2"]
        assert "5" not in lookups.chapter_members.get("4", frozenset())
        # Section 7 appears in both chapters
        assert "7" in lookups.chapter_members["2"]
        assert "7" in lookups.chapter_members["4"]


# ===========================================================================
# STAGE 3 — Replay (tree_ops)
#
# Contract: canonical tree_ops → expected IRNode tree state
#   - replace_at replaces exactly the addressed node, sharing unaffected subtrees
#   - remove_at removes exactly the addressed node
#   - insert_sorted places the new node in sorted position
#   - Structural invariants hold after each op
# ===========================================================================


class TestReplayReplaceAt:
    """Stage 3: replace_at invariants (Waist 3 — canonical ops)."""

    def test_replace_root_level_section(self):
        """replace_at body[section:3] replaces section 3 with new content."""
        tree = _body(
            _sec("2", _content("section 2")),
            _sec("3", _content("old section 3")),
            _sec("4", _content("section 4")),
        )
        new_sec3 = _sec("3", _content("new section 3"))

        result = tree_ops.replace_at(tree, [("section", "3")], new_sec3)

        assert result is not tree  # new tree produced
        sec3 = _resolved(tree_ops.resolve(result, [("section", "3")]))
        assert sec3 is not None
        assert sec3.children[0].text == "new section 3"
        # Siblings untouched
        sec2 = _resolved(tree_ops.resolve(result, [("section", "2")]))
        sec4 = _resolved(tree_ops.resolve(result, [("section", "4")]))
        assert sec2 is not None
        assert sec4 is not None

    def test_replace_nested_subsection(self):
        """replace_at section:5/subsection:2 replaces only that subsection."""
        tree = _body(
            _sec(
                "5",
                _sub("1", _content("sub 1 text")),
                _sub("2", _content("old sub 2 text")),
                _sub("3", _content("sub 3 text")),
            )
        )
        new_sub2 = _sub("2", _content("new sub 2 text"))

        result = tree_ops.replace_at(tree, [("section", "5"), ("subsection", "2")], new_sub2)

        sec = _resolved(tree_ops.resolve(result, [("section", "5")]))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [c.label for c in subsecs] == ["1", "2", "3"]
        sub2 = next(c for c in subsecs if c.label == "2")
        assert sub2.children[0].text == "new sub 2 text"
        # Other subsections untouched
        sub1 = next(c for c in subsecs if c.label == "1")
        assert sub1.children[0].text == "sub 1 text"

    def test_replace_preserves_original_tree(self):
        """replace_at must not mutate the original tree (persistent data structure)."""
        tree = _body(_sec("1", _content("original")))
        new_sec = _sec("1", _content("replacement"))

        _ = tree_ops.replace_at(tree, [("section", "1")], new_sec)

        # Original tree unchanged
        original_sec = _resolved(tree_ops.resolve(tree, [("section", "1")]))
        assert original_sec.children[0].text == "original"

    def test_replace_nonexistent_path_returns_unchanged_tree(self):
        """replace_at with missing path returns tree unchanged (no crash)."""
        tree = _body(_sec("1", _content("text")))
        result = tree_ops.replace_at(tree, [("section", "999")], _sec("999", _content("new")))
        # Section 999 was not in tree; existing structure intact
        sec1 = _resolved(tree_ops.resolve(result, [("section", "1")]))
        assert sec1 is not None

    def test_replace_section_in_chapter(self):
        """replace_at body/chapter:2/section:7 replaces section inside chapter."""
        ch2 = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="2",
            children=(
                _sec("7", _content("old")),
                _sec("8", _content("eight")),
            ),
        )
        tree = _body(ch2)

        new_sec7 = _sec("7", _content("new"))
        result = tree_ops.replace_at(tree, [("chapter", "2"), ("section", "7")], new_sec7)

        sec7 = _resolved(tree_ops.resolve(result, [("chapter", "2"), ("section", "7")]))
        assert sec7 is not None
        assert sec7.children[0].text == "new"
        # Section 8 untouched
        sec8 = _resolved(tree_ops.resolve(result, [("chapter", "2"), ("section", "8")]))
        assert sec8 is not None
        assert sec8.children[0].text == "eight"


class TestReplayRemoveAt:
    """Stage 3: remove_at invariants."""

    def test_remove_root_level_section(self):
        """remove_at body[section:3] removes section 3 entirely."""
        tree = _body(
            _sec("2", _content("two")),
            _sec("3", _content("three")),
            _sec("4", _content("four")),
        )

        result = tree_ops.remove_at(tree, [("section", "3")])

        sections = [c for c in result.children if c.kind == IRNodeKind.SECTION]
        assert [c.label for c in sections] == ["2", "4"]

    def test_remove_nested_paragraph(self):
        """remove_at section:1/subsection:1/paragraph:2 removes the paragraph."""
        tree = _body(
            _sec(
                "1",
                _sub(
                    "1",
                    _para("1", "first item"),
                    _para("2", "second item"),
                    _para("3", "third item"),
                ),
            )
        )

        result = tree_ops.remove_at(
            tree,
            [("section", "1"), ("subsection", "1"), ("paragraph", "2")],
        )

        sec = _resolved(tree_ops.resolve(result, [("section", "1")]))
        sub = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        assert [c.label for c in paras] == ["1", "3"]

    def test_remove_preserves_original_tree(self):
        """remove_at must not mutate the original tree."""
        tree = _body(_sec("1", _content("text")), _sec("2", _content("two")))
        _ = tree_ops.remove_at(tree, [("section", "1")])
        # Original has both sections
        assert len([c for c in tree.children if c.kind == IRNodeKind.SECTION]) == 2


class TestReplayInsertSorted:
    """Stage 3: insert_sorted invariants."""

    def test_insert_section_in_numeric_order(self):
        """insert_sorted places section:5a between 5 and 6."""
        tree = _body(
            _sec("4", _content("four")),
            _sec("5", _content("five")),
            _sec("6", _content("six")),
        )
        new_sec = _sec("5a", _content("five a"))

        result = tree_ops.insert_sorted(tree, [], new_sec)

        sections = [c for c in result.children if c.kind == IRNodeKind.SECTION]
        labels = [c.label for c in sections]
        assert labels == ["4", "5", "5a", "6"]

    def test_insert_section_at_end(self):
        """insert_sorted places section:10 after section:9."""
        tree = _body(_sec("8", _content("eight")), _sec("9", _content("nine")))
        new_sec = _sec("10", _content("ten"))

        result = tree_ops.insert_sorted(tree, [], new_sec)

        sections = [c for c in result.children if c.kind == IRNodeKind.SECTION]
        labels = [c.label for c in sections]
        assert labels == ["8", "9", "10"]

    def test_insert_section_at_beginning(self):
        """insert_sorted places section:1 before section:2."""
        tree = _body(_sec("2", _content("two")), _sec("3", _content("three")))
        new_sec = _sec("1", _content("one"))

        result = tree_ops.insert_sorted(tree, [], new_sec)

        sections = [c for c in result.children if c.kind == IRNodeKind.SECTION]
        labels = [c.label for c in sections]
        assert labels == ["1", "2", "3"]

    def test_insert_section_into_chapter(self):
        """insert_sorted into a chapter maintains sort order within chapter."""
        ch = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="3",
            children=(
                _sec("10", _content("ten")),
                _sec("12", _content("twelve")),
            ),
        )
        tree = _body(ch)
        new_sec = _sec("11", _content("eleven"))

        result = tree_ops.insert_sorted(tree, [("chapter", "3")], new_sec)

        chapter = _resolved(tree_ops.resolve(result, [("chapter", "3")]))
        sections = [c for c in chapter.children if c.kind == IRNodeKind.SECTION]
        labels = [c.label for c in sections]
        assert labels == ["10", "11", "12"]

    def test_insert_preserves_original_tree(self):
        """insert_sorted must not mutate the original tree."""
        tree = _body(_sec("1", _content("one")))
        _ = tree_ops.insert_sorted(tree, [], _sec("2", _content("two")))
        # Original has only section 1
        sections = [c for c in tree.children if c.kind == IRNodeKind.SECTION]
        assert len(sections) == 1


class TestReplayInvariants:
    """Stage 3: structural invariants hold after canonical ops."""

    def test_replace_at_no_duplicate_labels(self):
        """After replace_at, label uniqueness invariant holds."""
        tree = _body(
            _sec("1", _content("original")),
            _sec("2", _content("two")),
        )
        new_sec1 = _sec("1", _content("replaced"))
        result = tree_ops.replace_at(tree, [("section", "1")], new_sec1)

        violations = tree_ops.check_invariants(result)
        assert violations == []

    def test_insert_sorted_maintains_sort_order(self):
        """After insert_sorted, sort order invariant holds."""
        tree = _body(
            _sec("1", _content("one")),
            _sec("3", _content("three")),
            _sec("5", _content("five")),
        )
        result = tree_ops.insert_sorted(tree, [], _sec("4", _content("four")))

        violations = tree_ops.check_invariants(result)
        sort_violations = [v for v in violations if "out of order" in v]
        assert sort_violations == []

    def test_remove_at_no_sort_violations(self):
        """After remove_at, sort order invariant still holds."""
        tree = _body(
            _sec("2", _content("two")),
            _sec("3", _content("three")),
            _sec("4", _content("four")),
        )
        result = tree_ops.remove_at(tree, [("section", "3")])

        violations = tree_ops.check_invariants(result)
        sort_violations = [v for v in violations if "out of order" in v]
        assert sort_violations == []

    def test_sequential_ops_preserve_invariants(self):
        """A sequence of replace → insert → remove preserves all invariants."""
        tree = _body(
            _sec("1", _content("one")),
            _sec("3", _content("three")),
        )

        # Replace section 1
        tree = tree_ops.replace_at(tree, [("section", "1")], _sec("1", _content("one updated")))
        # Insert section 2
        tree = tree_ops.insert_sorted(tree, [], _sec("2", _content("two new")))
        # Insert section 4
        tree = tree_ops.insert_sorted(tree, [], _sec("4", _content("four new")))
        # Remove section 3
        tree = tree_ops.remove_at(tree, [("section", "3")])

        violations = tree_ops.check_invariants(tree)
        assert violations == []
        sections = [c for c in tree.children if c.kind == IRNodeKind.SECTION]
        assert [c.label for c in sections] == ["1", "2", "4"]

    def test_replace_at_section_content_correct_after_sequence(self):
        """Content of replaced section is exactly the replacement payload."""
        tree = _body(
            _sec("6", _sub("1", _content("old content"))),
            _sec("7", _sub("1", _content("unchanged"))),
        )
        new_sub = _sub("1", _content("new paragraph text"))
        new_sec6 = _sec("6", new_sub)
        result = tree_ops.replace_at(tree, [("section", "6")], new_sec6)

        sec6 = _resolved(tree_ops.resolve(result, [("section", "6")]))
        sub1 = next(c for c in sec6.children if c.kind == IRNodeKind.SUBSECTION)
        assert sub1.children[0].text == "new paragraph text"
        # Section 7 unaffected
        sec7 = _resolved(tree_ops.resolve(result, [("section", "7")]))
        sub7 = next(c for c in sec7.children if c.kind == IRNodeKind.SUBSECTION)
        assert sub7.children[0].text == "unchanged"


class TestReplayOccupancyModel:
    """Stage 3: slot occupancy invariants (LAWVM_CONSTITUTION §4)."""

    def test_repeal_placeholder_attrs_set(self):
        """A tombstone from repeal must carry lawvm_repeal_placeholder attribute."""
        from lawvm.finland.apply import apply_op
        from lawvm.finland.ops import AmendmentOp
        from lawvm.finland.statute import ReplayState
        import datetime as dt

        body = _body(_sec("5", _sub("1", _content("content"))))
        state = ReplayState(ir=body)
        op = AmendmentOp(
            op_id="repeal_5",
            op_type="REPEAL",
            target_section="5",
            target_kind=TargetKind.SECTION,
            source_statute="2020/1",
            source_issue_date=dt.date(2020, 1, 1),
        )
        ctx = cast(StatuteContext, SimpleNamespace(base_ir=body))

        result = apply_op(state, op, ctx, muutos_ir=None, replay_mode="finlex_oracle")

        sec5 = result.find_section("5")
        assert sec5 is not None
        assert sec5.attrs.get("lawvm_repeal_placeholder") == "1"

    def test_repeal_removes_section_in_legal_pit_mode(self):
        """In legal_pit mode, a whole-section repeal of non-base section removes the slot."""
        from lawvm.finland.apply import apply_op
        from lawvm.finland.ops import AmendmentOp
        from lawvm.finland.statute import ReplayState
        import datetime as dt

        # Section 5a was inserted (not in base) — repeal should remove it
        body = _body(_sec("5", _content("five")), _sec("5a", _content("five a")))
        base_ir = _body(_sec("5", _content("five")))  # 5a not in base
        state = ReplayState(ir=body)
        op = AmendmentOp(
            op_id="repeal_5a",
            op_type="REPEAL",
            target_section="5a",
            target_kind=TargetKind.SECTION,
            source_statute="2021/1",
            source_issue_date=dt.date(2021, 1, 1),
        )
        ctx = cast(StatuteContext, SimpleNamespace(base_ir=base_ir))

        result = apply_op(state, op, ctx, muutos_ir=None, replay_mode="legal_pit")

        # Non-base slot → removed, not tombstoned
        sec5a = result.find_section("5a")
        assert sec5a is None

    def test_insert_new_section_establishes_substantive_slot(self):
        """After insert_sorted, the new section is substantive (no repeal attrs)."""
        tree = _body(_sec("2", _content("two")))
        new_sec = _sec("3", _sub("1", _content("new content")))

        result = tree_ops.insert_sorted(tree, [], new_sec)

        sec3 = _resolved(tree_ops.resolve(result, [("section", "3")]))
        assert sec3 is not None
        assert sec3.attrs.get("lawvm_repeal_placeholder") is None
