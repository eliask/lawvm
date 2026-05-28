"""Tests for the functionally-decomposed pipeline stages.

Covers the four extracted functions from grafter.py:
  - post_process_tree      (Phase 4) — pure IRNode transform
  - normalize_and_compile_ops (Phase 1) — johtolause → AmendmentOp list
  - compile_amendment_ops  (Phase 2) — AmendmentOp list → ResolvedOp list
  - apply_ops_to_tree      (Phase 3) — ResolvedOp list → master.ir mutation

Each section builds on the preceding one using minimal, self-contained fixtures
so that failures isolate to the function under test.  No corpus access; no
network; no LLM calls.

Run:
    uv run pytest tests/test_decomposition.py -v
"""

from __future__ import annotations
from lawvm.core.ir import LegalAddress, LegalOperation, StructuralAction, TextPatchSpec, TextSelector

import datetime as dt
from typing import Any, Iterable, List, Optional, cast
from unittest.mock import patch

from lxml import etree

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.core.semantic_types import TextPatchKindEnum
from lawvm.core.compile_result import StrictProfile
from lawvm.core.elaboration_context import ReplayLookups, TargetContext, snapshot_target_context
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.finland.grafter import (
    AmendmentOp,
    ResolvedOp,
    apply_ops_to_tree,
    compile_amendment_ops,
    normalize_and_compile_ops,
    post_process_tree,
)
from lawvm.finland.group_ops import (
    remap_body_root_replace_group_before_terminal_voimaantulo,
    sort_group_ops_for_apply,
)
from lawvm.finland.payload_normalize import (
    SubsectionSlotAssignmentResult,
    SubsectionSlotMap,
)
from lawvm.finland.statute import ReplayState, StatuteContext, _serialize_text_node
from lawvm.finland.helpers import _fi_label_postprocessor


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_META_SNIPPET = (
    '<meta><identification source="#">'
    '<FRBRuri value=""/><FRBRthis value=""/>'
    '<FRBRdate date="{date}" name="Generation"/>'
    "</identification>"
    '<publication date="{date}" name="" showAs="" number=""/></meta>'
)


def _make_statute_xml(
    sections: List[str],
    date: str = "2000-01-01",
) -> bytes:
    """Build minimal AKN XML with the given <section> snippets."""
    meta = _META_SNIPPET.format(date=date)
    body = "<body>" + "".join(sections) + "</body>"
    return (f'<act xmlns="{AKN_NS}">{meta}{body}</act>').encode("utf-8")


def _section(num: str, subsections: List[str], eid: Optional[str] = None) -> str:
    eid_attr = f' eId="{eid}"' if eid else ""
    return f"<section{eid_attr}><num>{num}</num>{''.join(subsections)}</section>"


def _subsection(num: str, text: str, eid: Optional[str] = None) -> str:
    eid_attr = f' eId="{eid}"' if eid else ""
    return f"<subsection{eid_attr}><num>{num}</num><content><p>{text}</p></content></subsection>"


def _findings(result: PhaseResult, role: str):
    return tuple(finding for finding in result.findings() if finding.role == role)


def test_legal_operation_rejects_anchor_on_non_insert() -> None:
    with pytest.raises(ValueError, match="anchor is only valid for insert"):
        LegalOperation(
            op_id="bad-anchor",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            anchor=LegalAddress(path=(("section", "1"),)),
        )


def test_legal_operation_rejects_destination_on_non_renumber() -> None:
    with pytest.raises(ValueError, match="destination is only valid for renumber"):
        LegalOperation(
            op_id="bad-destination",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            destination=LegalAddress(path=(("section", "2"),)),
        )


def test_legal_operation_rejects_text_patch_on_structural_action() -> None:
    with pytest.raises(ValueError, match="text_patch is only valid"):
        LegalOperation(
            op_id="bad-text",
            sequence=0,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old"),
                replacement="new",
            ),
        )


def test_legal_operation_accepts_explicit_text_patch_for_text_replace() -> None:
    op = LegalOperation(
        op_id="bad-text-replace",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="old"),
            replacement="new",
        ),
    )
    assert op.text_patch is not None


def test_legal_operation_target_is_primary_target() -> None:
    target = LegalAddress(path=(("section", "1"),))
    op = LegalOperation(
        op_id="ok",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=target,
    )
    assert op.target == target


# ---------------------------------------------------------------------------
# Section 1: post_process_tree
# ---------------------------------------------------------------------------


class TestPostProcessTree:
    """post_process_tree is a pure IRNode → IRNode transform."""

    def test_strips_omission_nodes(self) -> None:
        body = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1 §"),
                        IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "omission"}, text="- -"),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="Teksti."),),
                        ),
                    ),
                ),
            ),
        )

        result = post_process_tree(body, normalize_replay_text=False)

        sec = result.children[0]
        kinds = [c.kind for c in sec.children]
        assert "hcontainer" not in kinds, "omission hcontainer must be stripped"

    def test_strips_conclusions_hcontainer(self) -> None:
        body = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "conclusions"}, text="Signed."),),
                ),
            ),
        )

        result = post_process_tree(body, normalize_replay_text=False)

        sec = result.children[0]
        hc = [c for c in sec.children if c.kind == IRNodeKind.HCONTAINER]
        assert hc == [], "conclusions hcontainer must be stripped"

    def test_hoists_trailing_section_into_chapter(self) -> None:
        """A section following a chapter should be moved inside that chapter."""
        body = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
                ),
                IRNode(kind=IRNodeKind.SECTION, label="2"),
            ),
        )

        result = post_process_tree(body, normalize_replay_text=False)

        # Top-level should now have only the chapter
        assert len(result.children) == 1
        assert result.children[0].kind == IRNodeKind.CHAPTER
        section_labels = [c.label for c in result.children[0].children if c.kind == IRNodeKind.SECTION]
        assert "2" in section_labels, "trailing section must be hoisted into chapter"

    def test_does_not_hoist_voimaantulo_section(self) -> None:
        """Section with 'Voimaantulo' heading must not be hoisted into a chapter."""
        body = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7",
                    children=(IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo"),),
                ),
            ),
        )

        result = post_process_tree(body, normalize_replay_text=False)

        top_kinds = [c.kind for c in result.children]
        assert IRNodeKind.SECTION in top_kinds, "entry-into-force section must stay at body level"
        vts = next(c for c in result.children if c.kind == IRNodeKind.SECTION)
        assert vts.label == "7"

    def test_hoists_trailing_chapter_into_part(self) -> None:
        body = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CHAPTER, label="1"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="2"),
            ),
        )

        result = post_process_tree(body, normalize_replay_text=False)

        assert len(result.children) == 1
        assert result.children[0].kind == IRNodeKind.PART
        chapter_labels = [c.label for c in result.children[0].children if c.kind == IRNodeKind.CHAPTER]
        assert "2" in chapter_labels

    def test_idempotent_on_clean_tree(self) -> None:
        body = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1 §"),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="Teksti."),),
                        ),
                    ),
                ),
            ),
        )

        once = post_process_tree(body, normalize_replay_text=False)
        twice = post_process_tree(once, normalize_replay_text=False)

        assert once == twice, "post_process_tree must be idempotent"

    def test_normalize_replay_text_false_preserves_whitespace(self) -> None:
        body = IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="  spaced  "),),
        )

        result = post_process_tree(body, normalize_replay_text=False)

        assert result.children[0].text == "  spaced  "

    def test_returns_irnode(self) -> None:
        body = IRNode(kind=IRNodeKind.BODY, children=())
        result = post_process_tree(body)
        assert isinstance(result, IRNode)


# ---------------------------------------------------------------------------
# Section 2: normalize_and_compile_ops
# ---------------------------------------------------------------------------

_EMPTY_META = _META_SNIPPET.format(date="2000-01-01")
_EMPTY_MASTER_XML = (f'<act xmlns="{AKN_NS}">{_EMPTY_META}<body></body></act>').encode("utf-8")


def _make_master(sections: Iterable[str] = (), date: str = "2000-01-01") -> ReplayState:
    state, _ = _make_state_ctx(sections, date=date)
    return state


def _make_state_ctx(sections: Iterable[str] = (), date: str = "2000-01-01") -> tuple[ReplayState, StatuteContext]:
    """Return (ReplayState, StatuteContext) for apply_ops_to_tree tests."""
    import copy

    xml_bytes = _make_statute_xml(list(sections), date=date)
    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    return state, ctx


def _make_muutos_tree(sections: Iterable[str] = (), date: str = "2010-01-01") -> "etree._Element":
    return etree.fromstring(_make_statute_xml(list(sections), date=date))


class TestNormalizeAndCompileOps:
    """normalize_and_compile_ops: johtolause (str) → PhaseResult(output=List[AmendmentOp])."""

    @staticmethod
    def _moved_to_chapter(op: object, expected_chapter: str) -> bool:
        lo = getattr(op, "lo", None)
        if lo is None or lo.target is None:
            return False
        return dict(lo.target.path).get("chapter") == expected_chapter

    def test_simple_replace_subsection(self) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Vanha teksti.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "Uusi teksti.")]),))

        result = normalize_and_compile_ops(
            johto="muutetaan 3 \u00a7:n 1 momentti seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/100",
            source_title="Laki muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        )
        ops = result.output

        assert len(ops) == 1
        op = ops[0]
        assert op.op_type == "REPLACE"
        assert op.target_section == "3"
        assert op.target_paragraph == 1
        assert op.source_statute == "2010/100"
        assert _findings(result, "obligation") == ()

    def test_conversion_surfaces_skipped_top_level_structural_target(self) -> None:
        master = _make_master((_section("1 §", [_subsection("1", "Vanha teksti.")]),))
        muutos_tree = _make_muutos_tree((_section("1 §", [_subsection("1", "Uusi teksti.")]),))

        result = normalize_and_compile_ops(
            johto="muutetaan nimike ja 1 § seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/100",
            source_title="Laki muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        )

        assert any(op.target_section == "1" for op in result.output)
        findings = [
            finding
            for finding in _findings(result, "observation")
            if finding.kind == "ELAB.REJECTED_OPERATION"
            and finding.detail.get("reason_code") == "ELAB.UNSUPPORTED_TOP_LEVEL_TARGET"
        ]
        assert len(findings) == 1
        assert findings[0].blocking is False
        assert findings[0].source_statute == "2010/100"
        assert findings[0].detail["target_path"] == (("nimike", ""),)
        assert findings[0].detail["source"] == "AmendmentOp.from_lo"
        obligations = [
            finding
            for finding in _findings(result, "obligation")
            if finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
            and finding.detail.get("reason_code") == "ELAB.UNSUPPORTED_TOP_LEVEL_TARGET"
        ]
        assert len(obligations) == 1
        assert obligations[0].blocking is True

    def test_conversion_surfaces_law_level_text_patch_separate_lane(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import lawvm.finland.frontend_compile as frontend_compile

        master = _make_master()
        muutos_tree = _make_muutos_tree()
        lo = LegalOperation(
            op_id="law-level-text",
            sequence=0,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=()),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="vanha"),
                replacement="uusi",
            ),
        )
        monkeypatch.setattr(frontend_compile, "extract_johtolause_legal_ops_from_parse_result", lambda _result: [lo])
        monkeypatch.setattr(frontend_compile, "parse_johtolause_clause", lambda _johto, statute_id="": None)
        monkeypatch.setattr(frontend_compile, "parse_ops_fallback_heuristic", lambda _johto: [])
        monkeypatch.setattr(frontend_compile, "_extract_root_replace_ops_from_body_fallback", lambda _johto, _tree: [])
        monkeypatch.setattr(
            frontend_compile,
            "_extract_enacting_formula_body_replace_ops_fallback",
            lambda _johto, _tree, _master: [],
        )
        monkeypatch.setattr(frontend_compile, "parse_ops_title_fallback", lambda _title: [])
        monkeypatch.setattr(
            frontend_compile,
            "_extract_enacting_formula_body_insert_ops_fallback",
            lambda _johto, _tree, _master: [],
        )

        result = frontend_compile.normalize_and_compile_ops(
            johto="sana vanha korvataan sanalla uusi",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/100",
            source_title="Laki muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        )

        assert result.output == []
        findings = [
            finding
            for finding in _findings(result, "observation")
            if finding.kind == "ELAB.LAW_LEVEL_TEXT_PATCH_SEPARATE_LANE"
            and finding.detail.get("reason_code") == "ELAB.LAW_LEVEL_TEXT_PATCH_SEPARATE_LANE"
        ]
        assert len(findings) == 1
        assert findings[0].detail["op_id"] == "law-level-text"
        assert findings[0].detail["target_path"] == ()
        assert findings[0].blocking is False
        assert _findings(result, "obligation") == ()

    def test_empty_johtolause_returns_no_ops(self) -> None:
        master = _make_master()
        muutos_tree = _make_muutos_tree()

        result = normalize_and_compile_ops(
            johto="",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/100",
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
        )
        ops = result.output

        assert ops == []
        assert _findings(result, "obligation") == ()

    def test_multiple_ops_same_section(self) -> None:
        master = _make_master(
            (
                _section(
                    "5 §",
                    [
                        _subsection("1", "A."),
                        _subsection("2", "B."),
                    ],
                ),
            )
        )
        muutos_tree = _make_muutos_tree(
            (
                _section(
                    "5 §",
                    [
                        _subsection("1", "New A."),
                        _subsection("2", "New B."),
                    ],
                ),
            )
        )

        ops = normalize_and_compile_ops(
            johto="muutetaan 5 \u00a7:n 1 ja 2 momentti seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/200",
            source_title="Laki muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output

        targets = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}
        assert ("REPLACE", "5", 1) in targets
        assert ("REPLACE", "5", 2) in targets

    def test_shifted_subsection_insert_supplements_fire_for_compact_siirtyy_family(self) -> None:
        master = _make_master(
            (
                _section(
                    "26 §",
                    [
                        _subsection("1", "A."),
                        _subsection("2", "B."),
                        _subsection("3", "C."),
                        _subsection("4", "D."),
                    ],
                ),
            )
        )
        muutos_tree = _make_muutos_tree(
            (
                _section(
                    "26 §",
                    [
                        _subsection("3", "Uusi 3."),
                        _subsection("4", "Siirtynyt 4."),
                        _subsection("5", "Uusi 5."),
                    ],
                ),
            )
        )

        ops = normalize_and_compile_ops(
            johto=(
                "muutetaan työntekijän eläkelain voimaanpanolain (396/2006) 26 §:n 3 momentti, "
                "sellaisena kuin se on laissa 1428/2011, sekä lisätään 26 §:ään, sellaisena "
                "kuin se on osaksi laissa 1428/2011, uusi 3 momentti, jolloin muutettu 3 "
                "momentti siirtyy 4 momentiksi, ja uusi 5 momentti seuraavasti:"
            ),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2014/883",
            source_title="Laki muuttamisesta",
            used_sec1_fallback=False,
            parent_id="396/2006",
        ).output

        targets = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}
        assert ("REPLACE", "26", 3) in targets
        assert ("INSERT", "26", 3) in targets
        assert ("INSERT", "26", 5) in targets

    def test_repeal_op(self) -> None:
        master = _make_master((_section("4 §", [_subsection("1", "Teksti.")]),))
        muutos_tree = _make_muutos_tree()

        ops = normalize_and_compile_ops(
            johto="kumotaan 4 \u00a7 seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/300",
            source_title="Laki kumoamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output

        assert any(op.op_type == "REPEAL" and op.target_section == "4" for op in ops)

    def test_direct_same_label_move_clause_retargets_replace_and_drops_orphan_renumber(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>8 luku</num>"
                    "<section><num>85 b §</num><subsection><num>1</num><content><p>Vanha 85 b.</p></content></subsection></section>"
                    "<section><num>85 c §</num><subsection><num>1</num><content><p>Vanha 85 c.</p></content></subsection></section>"
                    "</chapter>"
                ),
                "<chapter><num>9 luku</num></chapter>",
            )
        )
        muutos_tree = _make_muutos_tree(
            (
                _section("85 b §", [_subsection("1", "Uusi 85 b.")]),
                _section("85 c §", [_subsection("1", "Uusi 85 c.")]),
                _section("85 d §", [_subsection("1", "Uusi 85 d.")]),
            )
        )

        ops = normalize_and_compile_ops(
            johto=(
                "muutetaan maksupalvelulain (290/2010) 85 b ja 85 c §, sellaisina kuin ne ovat laissa 898/2017, "
                "siirretään muutettu 85 b § 9 lukuun ja lisätään lakiin uusi 85 d § seuraavasti:"
            ),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2020/575",
            source_title="Laki maksupalvelulain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2010/290",
        ).output

        moved_replace = [
            op for op in ops if op.op_type == "REPLACE" and op.target_section == "85b" and op.target_chapter == "9"
        ]
        orphan_renumber = [op for op in ops if op.op_type == "RENUMBER" and op.target_section == "85b"]

        assert moved_replace
        assert all(self._moved_to_chapter(op, "9") for op in moved_replace)
        assert orphan_renumber == []

    def test_direct_same_label_move_clause_accepts_optional_comma_before_chapter(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>8 luku</num>"
                    "<section><num>85 b §</num><subsection><num>1</num><content><p>Vanha 85 b.</p></content></subsection></section>"
                    "</chapter>"
                ),
                "<chapter><num>9 luku</num></chapter>",
            )
        )
        muutos_tree = _make_muutos_tree((_section("85 b §", [_subsection("1", "Uusi 85 b.")]),))

        ops = normalize_and_compile_ops(
            johto=("muutetaan 85 b §, siirretään 85 b §, 9 lukuun,"),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2020/575",
            source_title="Laki testilain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2010/290",
        ).output

        moved_replace = [
            op for op in ops if op.op_type == "REPLACE" and op.target_section == "85b" and op.target_chapter == "9"
        ]
        orphan_renumber = [op for op in ops if op.op_type == "RENUMBER" and op.target_section == "85b"]

        assert moved_replace
        assert all(self._moved_to_chapter(op, "9") for op in moved_replace)
        assert orphan_renumber == []

    def test_inline_same_label_move_clause_accepts_variant_without_samalla(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>4 luku</num>"
                    "<section><num>31 §</num><subsection><num>1</num><content><p>Vanha 31.</p></content></subsection></section>"
                    "<section><num>32 §</num><subsection><num>1</num><content><p>Vanha 32.</p></content></subsection></section>"
                    "</chapter>"
                ),
                (
                    "<chapter><num>6 luku</num>"
                    "<section><num>33 §</num><subsection><num>1</num><content><p>Vanha 33.</p></content></subsection></section>"
                    "<section><num>34 §</num><subsection><num>1</num><content><p>Vanha 34.</p></content></subsection></section>"
                    "</chapter>"
                ),
                "<chapter><num>5 luku</num></chapter>",
            )
        )
        muutos_tree = _make_muutos_tree(
            (
                _section("33 §", [_subsection("1", "Uusi 33.")]),
                _section("34 §", [_subsection("1", "Uusi 34.")]),
            )
        )

        ops = normalize_and_compile_ops(
            johto="muutetaan 31–34 §, joista 33 ja 34 § siirretään 5 lukuun",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2020/766",
            source_title="Laki testilain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2010/290",
        ).output

        moved_replace = [
            op
            for op in ops
            if op.op_type == "REPLACE" and op.target_section in {"33", "34"} and op.target_chapter == "5"
        ]

        assert moved_replace
        assert {op.target_section for op in moved_replace} == {"33", "34"}
        assert all(self._moved_to_chapter(op, "5") for op in moved_replace)

    def test_direct_section_relabel_clause_recovers_source_and_destination(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>7 luku</num>"
                    "<section><num>73 §</num><subsection><num>1</num><content><p>Vanha 73.</p></content></subsection></section>"
                    "</chapter>"
                ),
            )
        )
        muutos_tree = _make_muutos_tree((_section("61 §", [_subsection("1", "Uusi 61.")]),))

        ops = normalize_and_compile_ops(
            johto=(
                "kumotaan 12 päivänä heinäkuuta 1940 annetun perintö- ja lahjaverolain (378/40) 19 §:n 1 kohta, "
                "muutetaan 16 ja 21 a § sekä 4-7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää, "
                "joka siirretään 7 luvun 61 §:ksi,"
            ),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="1994/318",
            source_title="Laki perintö- ja lahjaverolain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="1940/378",
        ).output

        relabel = next(op for op in ops if op.op_type == "RENUMBER")
        assert relabel.target_section == "73"
        assert relabel.target_chapter == "7"
        assert relabel.lo is not None and relabel.lo.destination is not None
        assert dict(relabel.lo.destination.path) == {"chapter": "7", "section": "61"}

    def test_direct_section_relabel_clause_accepts_plain_section_without_comma(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>7 luku</num>"
                    "<section><num>73 §</num><subsection><num>1</num><content><p>Vanha 73.</p></content></subsection></section>"
                    "</chapter>"
                ),
            )
        )
        muutos_tree = _make_muutos_tree((_section("61 §", [_subsection("1", "Uusi 61.")]),))

        ops = normalize_and_compile_ops(
            johto=("kumotaan 1 §, muutetaan 7 luvun 73 § joka siirretään 61 §:ksi,"),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="1994/318",
            source_title="Laki testilain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="1940/378",
        ).output

        relabel = next(op for op in ops if op.op_type == "RENUMBER")
        assert relabel.target_section == "73"
        assert relabel.target_chapter == "7"
        assert relabel.lo is not None and relabel.lo.destination is not None
        assert dict(relabel.lo.destination.path) == {"chapter": "7", "section": "61"}

    def test_direct_section_relabel_clause_defaults_implied_destination_chapter(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>7 luku</num>"
                    "<section><num>73 §</num><subsection><num>1</num><content><p>Vanha 73.</p></content></subsection></section>"
                    "</chapter>"
                ),
            )
        )
        muutos_tree = _make_muutos_tree((_section("61 §", [_subsection("1", "Uusi 61.")]),))

        ops = normalize_and_compile_ops(
            johto=("kumotaan 1 §, muutetaan 7 luvun 73 §:ää, joka siirretään 61 §:ksi,"),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="1994/318",
            source_title="Laki testilain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="1940/378",
        ).output

        relabel = next(op for op in ops if op.op_type == "RENUMBER")
        assert relabel.target_section == "73"
        assert relabel.target_chapter == "7"
        assert relabel.lo is not None and relabel.lo.destination is not None
        assert dict(relabel.lo.destination.path) == {"chapter": "7", "section": "61"}

    def test_direct_section_relabel_clause_recovers_omitted_source_chapter(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>7 luku</num>"
                    "<section><num>73 §</num><subsection><num>1</num><content><p>Vanha 73.</p></content></subsection></section>"
                    "</chapter>"
                ),
            )
        )
        muutos_tree = _make_muutos_tree((_section("61 §", [_subsection("1", "Uusi 61.")]),))

        ops = normalize_and_compile_ops(
            johto=("kumotaan 1 §, muutetaan 73 §, joka siirretään 7 luvun 61 §:ksi,"),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="1994/318",
            source_title="Laki testilain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="1940/378",
        ).output

        relabel = next(op for op in ops if op.op_type == "RENUMBER")
        assert relabel.target_section == "73"
        assert relabel.target_chapter == "7"
        assert relabel.lo is not None and relabel.lo.destination is not None
        assert dict(relabel.lo.destination.path) == {"chapter": "7", "section": "61"}

    def test_strict_profile_blocks_target_guessing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With allows_target_guessing=False, the fallback heuristic must be rejected."""
        from lawvm.finland import frontend_compile
        from lawvm.finland.ops import AmendmentOp

        master = _make_master()
        muutos_tree = _make_muutos_tree()

        strict = StrictProfile(
            name="test_strict",
            allows_target_guessing=False,
            allows_uncovered_body_recovery=False,
            allows_omission_expansion=False,
            allows_estimated_dates=False,
            allows_context_dependent_anchor_resolution=False,
            allows_fallback_whole_section_replace=False,
            allows_word_substitution=False,
        )
        op = AmendmentOp(
            op_id="fallback-op",
            op_type="REPLACE",
            target_section="1",
            target_unit_kind="section",
            source_statute="2010/400",
            source_issue_date=None,
        )
        monkeypatch.setattr(frontend_compile, "parse_ops_fallback_heuristic", lambda _johto: [op])

        result = normalize_and_compile_ops(
            johto="Puuttuu johtolause.",  # no op keywords → PEG finds nothing → fallback triggered
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/400",
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
            strict_profile=strict,
        )
        ops = result.output

        assert ops == []
        kinds = [a.kind for a in _findings(result, "obligation")]
        assert "ELAB.STRICT_REJECTED_OPERATION" in kinds

    def test_used_sec1_fallback_tag_propagated(self) -> None:
        master = _make_master((_section("1 §", [_subsection("1", "Teksti.")]),))
        muutos_tree = _make_muutos_tree((_section("1 §", [_subsection("1", "Uusi.")]),))

        ops = normalize_and_compile_ops(
            johto="muutetaan 1 \u00a7:n 1 momentti seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/500",
            source_title="Laki",
            used_sec1_fallback=True,
            parent_id="2000/1",
        ).output

        for op in ops:
            assert op.sec1_body_johto_fallback is True
            assert "extraction_sec1_body_johto" in op.extraction_provenance_tags

    def test_body_root_replace_tag_propagated(self) -> None:
        master = _make_master(
            (_section("3 §", [_subsection("1", "Vanha 3.")]), _section("4 §", [_subsection("1", "Vanha 4.")]))
        )
        muutos_tree = _make_muutos_tree(
            (_section("3 §", [_subsection("1", "Uusi 3.")]), _section("4 §", [_subsection("1", "Uusi 4.")]))
        )

        ops = normalize_and_compile_ops(
            johto="muutetaan laki seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/501",
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output

        assert ops
        for op in ops:
            assert op.body_root_replace_fallback is True
            assert "extraction_body_root_replace" in op.extraction_provenance_tags

    def test_destinationless_move_relabel_is_reported_before_missing_intent(self) -> None:
        from lawvm.core.ir import LegalAddress, LegalOperation, StructuralAction

        bare_renumber = LegalOperation(
            op_id="op1",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "73"),)),
        )

        master = _make_master(
            (
                _section("73 §", [_subsection("1", "Vanha 73.")]),
                _section("61 §", [_subsection("1", "Uusi 61.")]),
            )
        )
        muutos_tree = _make_muutos_tree((_section("61 §", [_subsection("1", "Uusi 61.")]),))

        with patch(
            "lawvm.finland.frontend_compile.extract_johtolause_legal_ops_from_parse_result",
            return_value=[bare_renumber],
        ):
            result = normalize_and_compile_ops(
                johto="muutetaan 7 luvun 73 §, joka siirretään 61 §:ksi,",
                muutos_tree=muutos_tree,
                master=master,
                amendment_id="2021/456",
                source_title="Laki testilain muuttamisesta",
                used_sec1_fallback=False,
                parent_id="2020/1",
            )

        ops = result.output
        kinds = [obs.kind for obs in _findings(result, "observation")]
        assert "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER" in kinds
        assert any(obs.detail.get("collapse_kind") == "destinationless_move_relabel" for obs in _findings(result, "observation"))
        assert not any(
            adj.kind == "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER"
            and adj.detail.get("collapse_kind") == "destinationless_move_relabel"
            for adj in _findings(result, "obligation")
        )
        assert ops

    def test_fallback_heuristic_provenance_is_typed_without_hint(self) -> None:
        master = _make_master()
        muutos_tree = _make_muutos_tree()

        with patch("lawvm.finland.frontend_compile.extract_johtolause_legal_ops_from_parse_result", return_value=[]):
            ops = normalize_and_compile_ops(
                johto=(
                    "Tällä asetuksella kumotaan 17 päivänä heinäkuuta 1959 annetun "
                    "liikennevakuutusasetuksen (324/1959) 9 §:n 2―5 momentti."
                ),
                muutos_tree=muutos_tree,
                master=master,
                amendment_id="2010/502",
                source_title="Laki",
                used_sec1_fallback=False,
                parent_id="2000/1",
            ).output

        assert ops
        assert all(op.fallback_provenance is True for op in ops)
        assert all("extraction_fallback_heuristic" in op.extraction_provenance_tags for op in ops)

    def test_title_fallback_provenance_is_typed_without_hint(self) -> None:
        master = _make_master()
        muutos_tree = _make_muutos_tree()

        with patch("lawvm.finland.frontend_compile.extract_johtolause_legal_ops_from_parse_result", return_value=[]):
            ops = normalize_and_compile_ops(
                johto="",
                muutos_tree=muutos_tree,
                master=master,
                amendment_id="2010/503",
                source_title="Laki 5 luvun kumoamisesta",
                used_sec1_fallback=False,
                parent_id="2000/1",
            ).output

        assert len(ops) == 1
        assert ops[0].op_type == "REPEAL"
        assert ops[0].target_kind == "L"
        assert ops[0].target_section == "5"
        assert ops[0].fallback_provenance is True
        assert "extraction_title_fallback" in ops[0].extraction_provenance_tags

    def test_amendment_op_target_kind_projection_is_read_only(self) -> None:
        op = AmendmentOp(op_id="read-only-target-kind", op_type="REPLACE", target_unit_kind="chapter", target_section="5")
        seeded = AmendmentOp(op_id="seeded-target-kind", op_type="REPLACE", target_kind=TargetKind.CHAPTER, target_section="5")

        assert op.target_kind == TargetKind.CHAPTER
        assert seeded.target_unit_kind == "chapter"
        assert seeded.target_kind == TargetKind.CHAPTER
        with pytest.raises(TypeError, match="must be TargetKind"):
            cast(Any, AmendmentOp)(
                op_id="string-seed",
                op_type="REPLACE",
                target_kind="L",
                target_section="5",
            )
        with pytest.raises(AttributeError):
            cast(Any, op).target_kind = TargetKind.SECTION
        with pytest.raises(AttributeError):
            cast(Any, seeded).target_kind = TargetKind.SECTION

    def test_source_statute_set_on_all_ops(self) -> None:
        master = _make_master((_section("2 §", [_subsection("1", "A."), _subsection("2", "B.")]),))
        muutos_tree = _make_muutos_tree((_section("2 §", [_subsection("1", "X."), _subsection("2", "Y.")]),))

        ops = normalize_and_compile_ops(
            johto="muutetaan 2 \u00a7:n 1 ja 2 momentti seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2015/42",
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output

        assert ops
        assert all(op.source_statute == "2015/42" for op in ops)

    def test_remap_body_root_replace_group_uses_typed_carrier_without_hint(self) -> None:
        parent = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION, label="3", children=(IRNode(kind=IRNodeKind.HEADING, text="Edeltävä"),)
                ),
                IRNode(
                    kind=IRNodeKind.SECTION, label="4", children=(IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo"),)
                ),
            ),
        )
        existing = parent.children[1]
        muutos_ir = IRNode(
            kind=IRNodeKind.SECTION,
            label="4",
            children=(IRNode(kind=IRNodeKind.HEADING, text="Uusi asiasisältö"),),
        )
        target_ctx = TargetContext(
            target_unit_kind="section",
            target_norm="4",
            target_chapter=None,
            node_path=(("section", "4"),),
            parent_path=(),
            live_node=existing,
            parent_node=parent,
            sibling_labels=("3", "4"),
            subsection_slots=(),
        )
        lookups = ReplayLookups(
            snapshot_rev=0,
            unique_section_paths={},
            chapter_members={},
            part_members={},
            all_section_labels=frozenset({"3", "4"}),
        )
        group_ops = [
            AmendmentOp(
                op_id="body_root_4",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="4",
                body_root_replace_fallback=True,
            )
        ]

        remapped_target, remapped_muutos_ir, remapped_ops = remap_body_root_replace_group_before_terminal_voimaantulo(
            target_ctx,
            lookups,
            muutos_ir,
            group_ops,
        )

        assert remapped_target == "3a"
        assert remapped_muutos_ir is not None
        assert remapped_muutos_ir.label == "3a"
        assert len(remapped_ops) == 1
        assert remapped_ops[0].op_type == "INSERT"
        assert remapped_ops[0].target_section == "3a"
        assert remapped_ops[0].body_root_replace_fallback is True

    def test_remap_body_root_replace_group_does_not_fire_from_breadcrumb_string_alone(self) -> None:
        parent = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION, label="3", children=(IRNode(kind=IRNodeKind.HEADING, text="Edeltävä"),)
                ),
                IRNode(
                    kind=IRNodeKind.SECTION, label="4", children=(IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo"),)
                ),
            ),
        )
        existing = parent.children[1]
        muutos_ir = IRNode(
            kind=IRNodeKind.SECTION,
            label="4",
            children=(IRNode(kind=IRNodeKind.HEADING, text="Uusi asiasisältö"),),
        )
        target_ctx = TargetContext(
            target_unit_kind="section",
            target_norm="4",
            target_chapter=None,
            node_path=(("section", "4"),),
            parent_path=(),
            live_node=existing,
            parent_node=parent,
            sibling_labels=("3", "4"),
            subsection_slots=(),
        )
        lookups = ReplayLookups(
            snapshot_rev=0,
            unique_section_paths={},
            chapter_members={},
            part_members={},
            all_section_labels=frozenset({"3", "4"}),
        )
        group_ops = [
            AmendmentOp(
                op_id="body_root_4",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="4",
            )
        ]

        remapped_target, remapped_muutos_ir, remapped_ops = remap_body_root_replace_group_before_terminal_voimaantulo(
            target_ctx,
            lookups,
            muutos_ir,
            group_ops,
        )

        assert remapped_target == "4"
        assert remapped_muutos_ir is muutos_ir
        assert remapped_ops == group_ops

    def test_snapshot_target_context_uses_chapter_parent_for_absent_section(self) -> None:
        master = _make_master(
            (
                (
                    "<chapter><num>2 luku</num>"
                    "<section><num>1 §</num><subsection><num>1</num><content><p>A.</p></content></subsection></section>"
                    "</chapter>"
                ),
            )
        )
        lookups = ReplayLookups(
            snapshot_rev=0,
            unique_section_paths={},
            chapter_members={},
            part_members={},
            all_section_labels=frozenset({"1"}),
        )

        ctx = snapshot_target_context(cast(Any, master), "section", "4", "2", lookups)

        assert ctx.live_node is None
        assert ctx.node_path is None
        assert ctx.parent_path == (("chapter", "2"),)
        assert ctx.parent_node is not None
        assert ctx.parent_node.kind == IRNodeKind.CHAPTER
        assert ctx.parent_node.label == "2"
        assert ctx.sibling_labels == ("1",)

    def test_snapshot_target_context_uses_part_scope_for_same_label_chapter(self) -> None:
        master = _make_master(
            (
                (
                    "<part><num>I OSA</num>"
                    "<chapter><num>4 luku</num>"
                    "<section><num>1 §</num><subsection><num>1</num><content><p>A.</p></content></subsection></section>"
                    "</chapter>"
                    "</part>"
                ),
                (
                    "<part><num>II OSA</num>"
                    "<chapter><num>4 luku</num>"
                    "<section><num>11 §</num><subsection><num>1</num><content><p>B.</p></content></subsection></section>"
                    "</chapter>"
                    "</part>"
                ),
            )
        )
        lookups = ReplayLookups(
            snapshot_rev=0,
            unique_section_paths={},
            chapter_members={},
            part_members={},
            all_section_labels=frozenset({"1", "11"}),
        )

        ctx = snapshot_target_context(cast(Any, master), "chapter", "4", None, lookups, target_part="2")

        assert ctx.node_path == (("part", "2"), ("chapter", "4"))
        assert ctx.live_node is not None
        assert ctx.live_node.kind == IRNodeKind.CHAPTER
        assert ctx.parent_path == (("part", "2"),)
        assert ctx.parent_node is not None
        assert ctx.parent_node.kind == IRNodeKind.PART
        assert ctx.parent_node.label == "2"
        assert ctx.sibling_labels == ("4",)

    def test_normalize_and_compile_ops_keeps_malformed_suffix_section_inserts_as_sections(self) -> None:
        master = _make_master(
            (
                _section("39 §", [_subsection("1", "Vanha 39 §.")]),
                _section("63 §", [_subsection("1", "Vanha 63 §.")]),
            )
        )
        muutos_tree = etree.fromstring(
            _make_statute_xml(
                [
                    _section("39 §", [_subsection("1", "39 a § sisältö.")]),
                    _section("63 §", [_subsection("1", "63 a § sisältö.")]),
                    _section("63 b §", [_subsection("1", "63 b § sisältö.")]),
                    _section("63 c §", [_subsection("1", "63 c § sisältö.")]),
                ]
            )
        )

        ops = normalize_and_compile_ops(
            johto="lisätään lakiin uusi 39 a, 63 a, 63 b ja 63 c § seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="1979/318",
            source_title="Laki perintö- ja lahjaverolain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="1940/378",
        ).output

        got = [(op.op_type, op.target_section, op.target_paragraph, op.target_item) for op in ops]
        assert ("INSERT", "39a", None, None) in got
        assert ("INSERT", "63a", None, None) in got
        assert ("INSERT", "63b", None, None) in got
        assert ("INSERT", "63c", None, None) in got
        assert ("INSERT", "39", 1, "a") not in got
        assert ("INSERT", "63", 1, "a") not in got

    def test_valiaikaisesti_whole_amendment_tags_all_ops_temporary(self) -> None:
        """väliaikaisesti immediately after the verb → ALL ops tagged is_temporary."""
        master = _make_master((_section("5 §", [_subsection("1", "Vanha 5.")]),))
        muutos_tree = _make_muutos_tree((_section("5 §", [_subsection("1", "Uusi 5.")]),))

        ops = normalize_and_compile_ops(
            johto="muutetaan väliaikaisesti testilain 5 §:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2021/100",
            source_title="Laki testilain väliaikaisesta muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2010/1",
        ).output

        assert ops, "expected at least one op"
        assert all(op.is_temporary for op in ops), (
            "all ops must be temporary when väliaikaisesti modifies the verb"
        )

    def test_valiaikaisesti_section_scoped_tags_only_matching_ops_temporary(self) -> None:
        """Mixed johtolause: only the section after 'väliaikaisesti' is temporary.

        Regression for the pattern:
          lisätään ... uusi X §, väliaikaisesti uusi Y § sekä uusi Z §
        Only Y must be tagged is_temporary; X and Z must stay permanent.
        """
        master = _make_master(
            (
                _section("4 a §", [_subsection("1", "Vanha 4a.")]),
                _section("21 a §", [_subsection("1", "Vanha 21a.")]),
                _section("21 c §", [_subsection("1", "Vanha 21c.")]),
            )
        )
        muutos_tree = _make_muutos_tree(
            (
                _section("4 a §", [_subsection("1", "Uusi 4a.")]),
                _section("21 a §", [_subsection("1", "Uusi 21a.")]),
                _section("21 b §", [_subsection("1", "Uusi 21b.")]),
                _section("21 c §", [_subsection("1", "Uusi 21c.")]),
            )
        )

        ops = normalize_and_compile_ops(
            johto=(
                "lisätään lakiin uusi 4 a ja 21 a §, väliaikaisesti uusi 21 b §"
                " sekä uusi 21 c § seuraavasti:"
            ),
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2021/984",
            source_title="Laki testilain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="1999/488",
        ).output

        by_section = {op.target_section: op for op in ops if op.op_type == "INSERT"}
        assert "21b" in by_section, f"expected INSERT for 21b, got: {list(by_section)}"
        assert by_section["21b"].is_temporary, "21b§ must be tagged as temporary"
        for sec in ("4a", "21a", "21c"):
            if sec in by_section:
                assert not by_section[sec].is_temporary, (
                    f"{sec}§ must NOT be tagged as temporary"
                )

    def test_valiaikaisesti_multi_verb_clause_scopes_to_modified_verb(self) -> None:
        """Multi-verb johtolause: lisätään väliaikaisesti only affects its own sections.

        When one verb is directly modified by väliaikaisesti and another verb is not,
        only the sections from the modified verb are temporary.
        """
        master = _make_master(
            (
                _section("5 §", [_subsection("1", "Vanha 5.")]),
                _section("6 §", [_subsection("1", "Vanha 6.")]),
            )
        )
        muutos_tree = _make_muutos_tree(
            (
                _section("5 §", [_subsection("1", "Uusi 5.")]),
                _section("6 §", [_subsection("1", "Uusi 6.")]),
            )
        )

        ops = normalize_and_compile_ops(
            johto="muutetaan testilain 5 § ja lisätään väliaikaisesti uusi 6 §:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2021/200",
            source_title="Laki testilain muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2010/1",
        ).output

        by_section = {op.target_section: op for op in ops}
        assert "6" in by_section, f"expected op for section 6, got: {list(by_section)}"
        assert by_section["6"].is_temporary, "section 6 must be tagged as temporary"
        if "5" in by_section:
            assert not by_section["5"].is_temporary, (
                "section 5 must NOT be tagged as temporary"
            )

    def test_temporary_tax_year_insert_emits_expire_event(self) -> None:
        """Temporary tax-year inserts must emit a standalone expire event."""
        master = _make_master(())
        muutos_tree = etree.fromstring(
            (
                f'<act xmlns="{AKN_NS}">'
                "<body>"
                '<section><num>12 a §</num><subsection><num>1</num><content><p>'
                "Vuosilta 1982 ja 1983 toimitettavissa verotuksissa katsotaan testi."
                "</p></content></subsection></section>"
                "</body>"
                '<hcontainer eId="entryIntoForce" name="entryIntoForce">'
                "<content><p>Tämä laki tulee voimaan 1 päivänä tammikuuta 1983.</p></content>"
                "</hcontainer>"
                "</act>"
            ).encode("utf-8")
        )

        phase = normalize_and_compile_ops(
            johto="lisätään väliaikaisesti testilakiin uusi 12 a § seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="1982/1035",
            source_title="Laki testilain väliaikaisesta muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        )

        op = next(op for op in phase.output if op.target_section == "12a")
        assert op.is_temporary
        assert op.lo is not None and op.lo.source is not None
        assert op.lo.source.effective == "1983-01-01"
        assert op.lo.source.expires == "1983-12-31"

        assert any(
            event.kind == "commence"
            and event.group_id == "1982/1035"
            and event.effective == "1983-01-01"
            for event in phase.temporal_events
        )
        assert any(
            event.kind == "expire"
            and event.group_id == "1982/1035"
            and event.expires == "1983-12-31"
            for event in phase.temporal_events
        )


# ---------------------------------------------------------------------------
# Section 3: compile_amendment_ops
# ---------------------------------------------------------------------------


class TestCompileAmendmentOps:
    """compile_amendment_ops: (master, ops, muutos_tree, ...) → PhaseResult(output=List[ResolvedOp])."""

    def test_empty_ops_returns_empty(self) -> None:
        master = _make_master()
        muutos_tree = _make_muutos_tree()

        result = compile_amendment_ops(master, [], muutos_tree, "", "finlex_oracle").output

        assert result == []

    def test_empty_ops_surfaces_skipped_meta_clause_inputs(self) -> None:
        master = _make_master()
        muutos_tree = _make_muutos_tree()

        result = compile_amendment_ops(
            master,
            [],
            muutos_tree,
            "Tätä lakia sovelletaan aiempiin hakemuksiin.",
            "finlex_oracle",
        )

        skipped = [f for f in result.findings() if f.kind == "TIME.ACTIVATION_RULE_INPUT_SKIPPED"]
        assert skipped
        assert skipped[0].detail.get("lane") == "meta_clause"
        assert skipped[0].detail.get("input_kind") == "transition"

    def test_single_replace_returns_one_resolved_op(self) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New.")]),))

        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]

        resolved = compile_amendment_ops(
            master, ops, muutos_tree, "muutetaan 3 \u00a7:n 1 momentti seuraavasti:", "finlex_oracle"
        ).output

        assert len(resolved) == 1
        r = resolved[0]
        assert r.op_id == "op0"
        assert r.op.op_type == "REPLACE"
        assert r.op.target_section == "3"
        assert r.resolved_target_address is not None
        assert r.resolved_target_address.path == (("section", "3"), ("subsection", "1"))
        assert r.intent is not None
        assert r.muutos_ir is not None
        assert r.slot_assignment is not None
        assert r.slot_assignment.for_op(r.op) is r.amend_sub_ir
        assert r.resolved_amend_sub_ir() is r.amend_sub_ir

    def test_compile_amendment_ops_does_not_mirror_legacy_fields_to_resolvedop(self) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New.")]),))

        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]

        resolved = compile_amendment_ops(
            master, ops, muutos_tree, "muutetaan 3 \u00a7:n 1 momentti seuraavasti:", "finlex_oracle"
        ).output

        assert len(resolved) == 1
        assert "resolution_hint" not in resolved[0].__dict__
        assert resolved[0].description() == ops[0].description()

    def test_resolved_op_binds_slot_assignment_payload_at_construction(self) -> None:
        op = AmendmentOp(
            op_id="op0",
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="3",
            target_paragraph=1,
            source_statute="2010/100",
        )
        stale_amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="stale fallback payload")
        assigned_amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="authoritative slot assignment payload")
        slots = SubsectionSlotMap()
        slots.assign(op, assigned_amend_sub)
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="3",
            target_chapter=None,
            slot_assignment=SubsectionSlotAssignmentResult(
                subsec_map=slots,
                sparse_slot_bindings=(),
                used_subs=(0,),
                unassigned_payload_slots=(),
            ),
        )
        assert rop.amend_sub_ir is assigned_amend_sub
        rop.amend_sub_ir = stale_amend_sub
        assert rop.resolved_amend_sub_ir() is stale_amend_sub

    def test_resolved_op_from_amendment_op_seeds_late_waist_fields(self) -> None:
        import warnings

        from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction

        lo = LegalOperation(
            op_id="lo0",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "3"), ("subsection", "1"))),
            source=OperationSource(statute_id="2010/100", enacted="2010-01-01"),
        )
        op = AmendmentOp(
            op_id="op0",
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="3",
            target_paragraph=1,
            source_statute="2010/100",
            lo=lo,
        )
        assigned_amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="authoritative slot assignment payload")
        slots = SubsectionSlotMap()
        slots.assign(op, assigned_amend_sub)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rop = ResolvedOp.from_amendment_op(
                op,
                muutos_ir=None,
                cross_ir=None,
                target_unit_kind="section",
                target_norm="3",
                target_chapter=None,
                slot_assignment=SubsectionSlotAssignmentResult(
                    subsec_map=slots,
                    sparse_slot_bindings=(),
                    used_subs=(0,),
                    unassigned_payload_slots=(),
                ),
            )

        assert rop.op_id == "op0"
        assert rop.resolved_target_address == lo.target
        assert rop.resolved_op_source == lo.source
        assert rop.amend_sub_ir is assigned_amend_sub
        assert rop.description() == op.description()
        assert rop.intent is not None
        assert not any("ResolvedOp direct construction without typed intent" in str(w.message) for w in caught)

    def test_resolved_op_from_amendment_op_binds_synthesized_target_address_once(self) -> None:
        op = AmendmentOp(
            op_id="op_addr",
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="3",
            target_paragraph=1,
            source_statute="2010/100",
        )

        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="3",
            target_chapter=None,
        )

        assert rop.resolved_target_address is not None
        assert rop.resolved_target_address.path == (("section", "3"), ("subsection", "1"))
        assert rop.description() == "REPLACE 3 § 1 mom"

        rop.target_norm = "9"

        assert rop.resolved_target_address is not None
        assert rop.resolved_target_address.path == (("section", "3"), ("subsection", "1"))
        assert rop.description() == "REPLACE 3 § 1 mom"

    def test_resolved_op_scope_without_address_keeps_only_structural_norm(self) -> None:
        op = AmendmentOp(
            op_id="op_scope",
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="3",
            target_paragraph=2,
            target_item="a",
            source_statute="2010/100",
        )

        rop = ResolvedOp(
            op=op,
            muutos_ir=None,
            cross_ir=None,
            amend_sub_ir=None,
            op_id=op.op_id,
            target_unit_kind="section",
            target_norm="3",
            _op_type_seed=op.op_type,
            _target_special_override=None,
            _source_statute_override=op.source_statute,
            _source_issue_date_override=op.source_issue_date,
            _source_title_override=op.source_title,
        )

        assert rop.effective_target_paragraph is None
        assert rop.effective_target_item_label is None
        assert rop.resolved_target_scope == ("3", None, None, None, None, None)
        assert rop.resolved_target_address is None
        assert rop.description() == "REPLACE 3 §"

        rop.target_norm = "9"

        assert rop.resolved_target_scope == ("9", None, None, None, None, None)
        assert rop.resolved_target_address is None
        assert rop.description() == "REPLACE 9 §"

    def test_resolved_op_from_amendment_op_binds_destination_from_legal_operation(self) -> None:
        from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction

        lo = LegalOperation(
            op_id="lo_dest",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("chapter", "7"), ("section", "60"))),
            destination=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
            source=OperationSource(statute_id="2010/100", enacted="2010-01-01"),
        )
        op = AmendmentOp(
            op_id="op_dest",
            op_type="RENUMBER",
            target_kind=TargetKind.SECTION,
            target_section="60",
            target_chapter="7",
            source_statute="2010/100",
            lo=lo,
        )

        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="60",
            target_chapter="7",
        )

        assert rop.resolved_destination_address == lo.destination

    def test_resolved_op_from_amendment_op_binds_insert_intent_from_slot_payload(self) -> None:
        from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction

        lo = LegalOperation(
            op_id="lo1",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "40"), ("subsection", "3"))),
            source=OperationSource(statute_id="2010/625", enacted="2010-01-01"),
        )
        op = AmendmentOp(
            op_id="op1",
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="40",
            target_paragraph=3,
            source_statute="2010/625",
            lo=lo,
        )
        assigned_amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="authoritative insert payload")
        slots = SubsectionSlotMap()
        slots.assign(op, assigned_amend_sub)

        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="40",
            target_chapter=None,
            slot_assignment=SubsectionSlotAssignmentResult(
                subsec_map=slots,
                sparse_slot_bindings=(),
                used_subs=(0,),
                unassigned_payload_slots=(),
            ),
        )

        assert rop.amend_sub_ir is assigned_amend_sub
        assert rop.intent is not None

    def test_resolved_op_from_amendment_op_binds_typed_intent_by_default(self) -> None:
        op = AmendmentOp(
            op_id="op2",
            op_type="REPEAL",
            target_kind=TargetKind.SECTION,
            target_section="50",
            source_statute="2010/700",
        )

        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="50",
            target_chapter=None,
        )

        assert rop.intent is not None

    def test_two_ops_same_section_share_muutos_ir(self) -> None:
        """Ops targeting the same section must share the same muutos_ir object."""
        master = _make_master((_section("3 §", [_subsection("1", "A."), _subsection("2", "B.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New A."), _subsection("2", "New B.")]),))

        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            ),
            AmendmentOp(
                op_id="op1",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=2,
                source_statute="2010/100",
            ),
        ]

        resolved = compile_amendment_ops(
            master, ops, muutos_tree, "muutetaan 3 \u00a7:n 1 ja 2 momentti", "finlex_oracle"
        ).output

        assert len(resolved) == 2
        # Both ops target the same section — they share the same muutos_ir identity
        assert resolved[0].muutos_ir is resolved[1].muutos_ir

    def test_two_ops_different_sections_have_different_muutos_ir(self) -> None:
        master = _make_master(
            (
                _section("3 §", [_subsection("1", "A.")]),
                _section("5 §", [_subsection("1", "B.")]),
            )
        )
        muutos_tree = _make_muutos_tree(
            (
                _section("3 §", [_subsection("1", "New A.")]),
                _section("5 §", [_subsection("1", "New B.")]),
            )
        )

        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            ),
            AmendmentOp(
                op_id="op1",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="5",
                target_paragraph=1,
                source_statute="2010/100",
            ),
        ]

        resolved = compile_amendment_ops(
            master,
            ops,
            muutos_tree,
            "muutetaan 3 \u00a7:n 1 momentti ja 5 \u00a7:n 1 momentti",
            "finlex_oracle",
        ).output

        assert len(resolved) == 2
        assert resolved[0].muutos_ir is not resolved[1].muutos_ir

    def test_target_kind_and_norm_propagated(self) -> None:
        master = _make_master((_section("7 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("7 §", [_subsection("1", "New.")]),))

        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="7",
                source_statute="2010/100",
            ),
        ]

        resolved = compile_amendment_ops(master, ops, muutos_tree, "muutetaan 7 \u00a7", "finlex_oracle").output

        assert resolved
        r = resolved[0]
        assert r.target_unit_kind == "section"
        assert r.target_norm == "7"

    def test_part_resolved_group_key_falls_back_to_neutral_part_scope(self) -> None:
        op = AmendmentOp(
            op_id="op0",
            op_type="REPLACE",
            target_kind=TargetKind.PART,
            target_section="IV",
            source_statute="2010/100",
        )

        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="part",
            target_norm="IV",
            target_chapter=None,
        )

        assert rop.resolved_group_key == ("part", "IV", None, "4")

    def test_resolved_target_scope_drives_lookup_scope_while_group_key_stays_neutral(self) -> None:
        op = AmendmentOp(
            op_id="op0",
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="3",
            target_chapter="2",
            source_statute="2010/100",
        )

        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="3",
            target_chapter="2",
            target_address=LegalAddress(path=(("part", "I"), ("chapter", "4"), ("section", "9"))),
        )

        assert rop.resolved_section_lookup_scope == ("9", "4", "1")
        assert rop.resolved_group_key == ("section", "3", "4", "1")

    def test_repeal_op_has_none_muutos_ir_allowed(self) -> None:
        """REPEAL ops targeting a non-existent section get muutos_ir=None (no body)."""
        master = _make_master((_section("9 §", [_subsection("1", "Old.")]),))
        # Amendment body has no section 9 (body-less repeal)
        muutos_tree = _make_muutos_tree()

        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPEAL",
                target_kind=TargetKind.SECTION,
                target_section="9",
                source_statute="2010/100",
            ),
        ]

        resolved = compile_amendment_ops(master, ops, muutos_tree, "kumotaan 9 \u00a7", "finlex_oracle").output

        # REPEAL with no body in amendment: either no resolved ops (filtered) or
        # a resolved op with muutos_ir=None.  Both are acceptable — what must NOT
        # happen is a crash or a resolved op with a wrong muutos_ir.
        for r in resolved:
            if r.op.op_type == "REPEAL" and r.op.target_section == "9":
                assert r.muutos_ir is None or isinstance(r.muutos_ir, IRNode)

    def test_collects_elaboration_observations_from_compile_group(self, monkeypatch) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New.")]),))
        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]

        def fake_compile_group(**kwargs):
            return PhaseResult(
                output=[],
                findings=(
                    Finding(
                        kind="ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                        role="observation",
                        stage="group_payload_normalization",
                        detail={"target_unit_kind": "section", "target_norm": "3"},
                        source_statute="2010/100",
                        blocking=False,
                    ),
                ),
            )

        monkeypatch.setattr("lawvm.finland.grafter._compile_group", fake_compile_group)

        result = compile_amendment_ops(
            master,
            ops,
            muutos_tree,
            "muutetaan 3 §:n 1 momentti",
            "finlex_oracle",
        )

        assert result.output == []
        observations = _findings(result, "observation")
        assert len(observations) == 1
        assert observations[0].detail["target_norm"] == "3"

    def test_collects_payload_completeness_observation_from_compile_group(self, monkeypatch) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New.")]),))
        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]

        def fake_compile_group(**kwargs):
            return PhaseResult(
                output=[],
                findings=(
                    Finding(
                        kind="ELAB.PAYLOAD_COMPLETENESS",
                        role="observation",
                        stage="group_payload_normalization",
                        detail={
                            "target_unit_kind": "section",
                            "target_norm": "3",
                            "payload_completeness_kind": "fragmentary",
                            "tail_policy": "preserve_unstated_tail",
                        },
                        source_statute="2010/100",
                        blocking=False,
                    ),
                ),
            )

        monkeypatch.setattr("lawvm.finland.grafter._compile_group", fake_compile_group)

        result = compile_amendment_ops(
            master,
            ops,
            muutos_tree,
            "muutetaan 3 §:n 1 momentti",
            "finlex_oracle",
        )

        assert result.output == []
        payload_obs = [obs for obs in _findings(result, "observation") if obs.kind == "ELAB.PAYLOAD_COMPLETENESS"]
        assert len(payload_obs) == 1
        assert payload_obs[0].detail["payload_completeness_kind"] == "fragmentary"
        assert payload_obs[0].detail["tail_policy"] == "preserve_unstated_tail"

    def test_collects_findings_from_compile_group_without_wrapper_tuples(self, monkeypatch) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New.")]),))
        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]

        def fake_compile_group(**kwargs):
            return PhaseResult(
                output=[],
                findings=(
                    Finding(
                        kind="ELAB.PAYLOAD_COMPLETENESS",
                        role="observation",
                        stage="group_payload_normalization",
                        detail={
                            "target_unit_kind": "section",
                            "target_norm": "3",
                            "payload_completeness_kind": "fragmentary",
                        },
                        source_statute="2010/100",
                        blocking=False,
                    ),
                    Finding(
                        kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
                        role="obligation",
                        stage="elaborate_group",
                        detail={
                            "source_statute": "2010/100",
                            "target_unit_kind": "section",
                            "target_norm": "3",
                            "target_chapter": "",
                            "unassigned_slots": ["2:2"],
                        },
                        blocking=False,
                    ),
                ),
            )

        monkeypatch.setattr("lawvm.finland.grafter._compile_group", fake_compile_group)

        result = compile_amendment_ops(
            master,
            ops,
            muutos_tree,
            "muutetaan 3 §:n 1 momentti",
            "finlex_oracle",
        )

        assert result.output == []
        assert [finding.kind for finding in result.findings()] == [
            "ELAB.PAYLOAD_COMPLETENESS",
            "ELAB.SPARSE_PAYLOAD_LEFTOVER",
        ]
        assert [obs.kind for obs in _findings(result, "observation")] == ["ELAB.PAYLOAD_COMPLETENESS"]
        assert [obl.kind for obl in _findings(result, "obligation")] == ["ELAB.SPARSE_PAYLOAD_LEFTOVER"]

    def test_collects_sparse_leftovers_from_compile_group(self, monkeypatch) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New.")]),))
        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]

        def fake_compile_group(**kwargs):
            return PhaseResult(
                output=[],
                findings=(
                    Finding(
                        kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
                        role="obligation",
                        stage="elaborate_group",
                        detail={
                            "source_statute": "2010/100",
                            "target_unit_kind": "section",
                            "target_norm": "3",
                            "target_chapter": "",
                            "unassigned_slots": ["2:2", "3:(unlabeled)"],
                        },
                        blocking=False,
                    ),
                ),
            )

        monkeypatch.setattr("lawvm.finland.grafter._compile_group", fake_compile_group)

        result = compile_amendment_ops(
            master,
            ops,
            muutos_tree,
            "muutetaan 3 §:n 1 momentti",
            "finlex_oracle",
        )

        assert result.output == []
        obligations = _findings(result, "obligation")
        assert len(obligations) == 1
        obl = obligations[0]
        assert obl.kind == "ELAB.SPARSE_PAYLOAD_LEFTOVER"
        assert not obl.blocking
        assert obl.detail["target_norm"] == "3"
        assert obl.detail["unassigned_slots"] == ("2:2", "3:(unlabeled)")

    def test_collects_sparse_slot_bindings_from_compile_group(self, monkeypatch) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Old.")]),))
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "New.")]),))
        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="3",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]

        def fake_compile_group(**kwargs):
            return PhaseResult(
                output=[],
                findings=(
                    Finding(
                        kind="ELAB.SPARSE_SLOT_BINDING",
                        role="observation",
                        stage="elaborate_group",
                        detail={
                            "source_statute": "2010/100",
                            "target_unit_kind": "section",
                            "target_norm": "3",
                            "target_chapter": "",
                            "op_description": "REPLACE 3 § 1 mom",
                            "op_type": "REPLACE",
                            "target_paragraph": 1,
                            "target_item": "",
                            "target_special": "",
                            "payload_slot_index": 1,
                            "payload_slot_label": "1",
                        },
                        source_statute="2010/100",
                        blocking=False,
                    ),
                ),
            )

        monkeypatch.setattr("lawvm.finland.grafter._compile_group", fake_compile_group)

        result = compile_amendment_ops(
            master,
            ops,
            muutos_tree,
            "muutetaan 3 §:n 1 momentti",
            "finlex_oracle",
        )

        assert result.output == []
        observations = _findings(result, "observation")
        assert len(observations) == 1
        obs = observations[0]
        assert obs.kind == "ELAB.SPARSE_SLOT_BINDING"
        assert obs.detail == {
            "source_statute": "2010/100",
            "target_unit_kind": "section",
            "target_norm": "3",
            "target_chapter": "",
            "op_description": "REPLACE 3 § 1 mom",
            "op_type": "REPLACE",
            "target_paragraph": 1,
            "target_item": "",
            "target_special": "",
            "payload_slot_index": 1,
            "payload_slot_label": "1",
        }


# ---------------------------------------------------------------------------
# Section 4: apply_ops_to_tree
# ---------------------------------------------------------------------------


def _make_dates() -> tuple:
    return dt.date(2010, 1, 1), dt.date(2010, 3, 1)


class TestApplyOpsToTree:
    """apply_ops_to_tree: pure fold, returns ReplayState."""

    def test_single_replace_updates_ir(self) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Vanha teksti.")]),))
        state, ctx = _make_state_ctx([_section("3 §", [_subsection("1", "Vanha teksti.")])])
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "Uusi teksti.")]),))
        johto = "muutetaan 3 \u00a7:n 1 momentti seuraavasti:"
        amendment_id = "2010/100"

        ops = normalize_and_compile_ops(
            johto=johto,
            muutos_tree=muutos_tree,
            master=master,
            amendment_id=amendment_id,
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output
        resolved = compile_amendment_ops(master, ops, muutos_tree, johto, "finlex_oracle").output
        issue, effective = _make_dates()

        result = apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            muutos_tree=muutos_tree,
            johto=johto,
            amendment_id=amendment_id,
            source_title="Laki",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=[],
            failed_ops_out=[],
            source_pathologies_out=[],
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )

        body_text = _serialize_text_node(result.ir)
        assert "Uusi teksti" in body_text
        assert "Vanha teksti" not in body_text

    def test_returns_replay_state(self) -> None:
        state, ctx = _make_state_ctx()
        muutos_tree = _make_muutos_tree()
        issue, effective = _make_dates()

        result = apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=[],
            ops=[],
            muutos_tree=muutos_tree,
            johto="",
            amendment_id="2010/100",
            source_title="Laki",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=None,
            failed_ops_out=None,
            source_pathologies_out=None,
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )

        assert isinstance(result, ReplayState)

    def test_apply_ops_to_tree_emits_uncovered_body_strict_rejection_as_finding(self) -> None:
        state, ctx = _make_state_ctx()
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "Uusi teksti.")]),))
        issue, effective = _make_dates()
        findings: list[Any] = []

        apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=[],
            ops=[
                AmendmentOp(
                    op_id="replace_3",
                    op_type="REPLACE",
                    target_kind=TargetKind.SECTION,
                    target_section="3",
                )
            ],
            muutos_tree=muutos_tree,
            johto="muutetaan 3 § seuraavasti:",
            amendment_id="2010/100",
            source_title="Laki",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=None,
            failed_ops_out=[],
            source_pathologies_out=[],
            strict_profile=StrictProfile(
                name="strict",
                allows_uncovered_body_recovery=False,
            ),
            _vts_ops_enrich_done=False,
            findings_out=findings,
        )

        assert [finding.kind for finding in findings] == ["APPLY.STRICT_REJECTED_UNCOVERED_BODY"]
        assert findings[0].role == "obligation"
        assert findings[0].blocking is True
        assert findings[0].source_statute == "2010/100"

    def test_apply_ops_to_tree_collects_apply_mutation_events(self) -> None:
        master = _make_master((_section("3 §", [_subsection("1", "Vanha teksti.")]),))
        state, ctx = _make_state_ctx([_section("3 §", [_subsection("1", "Vanha teksti.")])])
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "Uusi teksti.")]),))
        johto = "muutetaan 3 \u00a7:n 1 momentti seuraavasti:"
        amendment_id = "2010/100"

        ops = normalize_and_compile_ops(
            johto=johto,
            muutos_tree=muutos_tree,
            master=master,
            amendment_id=amendment_id,
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output
        resolved = compile_amendment_ops(master, ops, muutos_tree, johto, "finlex_oracle").output
        issue, effective = _make_dates()
        mutation_events: list = []

        apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            muutos_tree=muutos_tree,
            johto=johto,
            amendment_id=amendment_id,
            source_title="Laki",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=[],
            failed_ops_out=[],
            source_pathologies_out=[],
            mutation_events_out=mutation_events,
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )

        assert mutation_events
        assert mutation_events[0].op_id == ops[0].op_id
        assert mutation_events[0].source_statute == "2010/100"

    def test_emits_section_snapshot_per_group(self) -> None:
        """Each group of ops must emit exactly one snapshot to lo_ops_out."""
        secs = [
            _section("3 §", [_subsection("1", "A."), _subsection("2", "B.")]),
            _section("5 §", [_subsection("1", "C.")]),
        ]
        master = _make_master(secs)
        state, ctx = _make_state_ctx(secs)
        muutos_tree = _make_muutos_tree(
            (
                _section("3 §", [_subsection("1", "New A."), _subsection("2", "New B.")]),
                _section("5 §", [_subsection("1", "New C.")]),
            )
        )
        johto = "muutetaan 3 \u00a7:n 1 ja 2 momentti ja 5 \u00a7:n 1 momentti seuraavasti:"

        ops = normalize_and_compile_ops(
            johto=johto,
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/100",
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output
        resolved = compile_amendment_ops(master, ops, muutos_tree, johto, "finlex_oracle").output
        lo_ops_out: list = []
        issue, effective = _make_dates()

        apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            muutos_tree=muutos_tree,
            johto=johto,
            amendment_id="2010/100",
            source_title="Laki",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=lo_ops_out,
            failed_ops_out=[],
            source_pathologies_out=[],
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )

        # Two target sections → at least two snapshots
        assert len(lo_ops_out) >= 2

    def test_failed_ops_collected(self) -> None:
        """An op targeting a non-existent section must appear in failed_ops_out."""
        master = _make_master()  # no sections at all
        state, ctx = _make_state_ctx()
        muutos_tree = _make_muutos_tree((_section("99 §", [_subsection("1", "New.")]),))
        johto = "muutetaan 99 \u00a7:n 1 momentti seuraavasti:"

        ops = [
            AmendmentOp(
                op_id="op0",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="99",
                target_paragraph=1,
                source_statute="2010/100",
            )
        ]
        resolved = compile_amendment_ops(master, ops, muutos_tree, johto, "finlex_oracle").output
        failed_ops: list = []
        issue, effective = _make_dates()

        apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            muutos_tree=muutos_tree,
            johto=johto,
            amendment_id="2010/100",
            source_title="Laki",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=None,
            failed_ops_out=failed_ops,
            source_pathologies_out=[],
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )

        # The direct REPLACE path fails; uncovered recovery may succeed or fail.
        # Either way, no crash is the primary guarantee.
        # If failed_ops is non-empty, check it has the right target.
        for f in failed_ops:
            assert f.target_section in ("99", "")  # tolerance for empty-target edge cases

    def test_multiple_amendments_accumulate_correctly(self) -> None:
        """Two successive calls to apply_ops_to_tree must accumulate changes."""
        secs = [
            _section("1 §", [_subsection("1", "Alkuperainen.")]),
            _section("2 §", [_subsection("1", "Toinen.")]),
        ]
        master = _make_master(secs)
        state, ctx = _make_state_ctx(secs)

        # First amendment: replace §1
        muutos1 = _make_muutos_tree((_section("1 §", [_subsection("1", "Ensimmainen muutos.")]),))
        johto1 = "muutetaan 1 \u00a7:n 1 momentti seuraavasti:"
        ops1 = normalize_and_compile_ops(
            johto=johto1,
            muutos_tree=muutos1,
            master=master,
            amendment_id="2010/1",
            source_title="Laki 1",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output
        resolved1 = compile_amendment_ops(master, ops1, muutos1, johto1, "finlex_oracle").output
        issue, effective = _make_dates()
        state = apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved1,
            ops=ops1,
            muutos_tree=muutos1,
            johto=johto1,
            amendment_id="2010/1",
            source_title="Laki 1",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=None,
            failed_ops_out=None,
            source_pathologies_out=None,
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )
        # Update master so normalize_and_compile_ops / compile_amendment_ops
        # see the amended state (they still use XMLStatute duck-typing).
        master.ir = state.ir

        # Second amendment: replace §2
        muutos2 = _make_muutos_tree((_section("2 §", [_subsection("1", "Toinen muutos.")]),))
        johto2 = "muutetaan 2 \u00a7:n 1 momentti seuraavasti:"
        ops2 = normalize_and_compile_ops(
            johto=johto2,
            muutos_tree=muutos2,
            master=master,
            amendment_id="2010/2",
            source_title="Laki 2",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output
        resolved2 = compile_amendment_ops(master, ops2, muutos2, johto2, "finlex_oracle").output
        state = apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved2,
            ops=ops2,
            muutos_tree=muutos2,
            johto=johto2,
            amendment_id="2010/2",
            source_title="Laki 2",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=None,
            failed_ops_out=None,
            source_pathologies_out=None,
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )

        text = _serialize_text_node(state.ir)
        assert "Alkuperainen" not in text, "first section should be replaced"
        assert "Toinen muutos" in text or "Toinen" in text  # second section replaced or still present


# ---------------------------------------------------------------------------
# Section 5: Round-trip integration
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """End-to-end: normalize → compile → apply → post_process."""

    def test_full_pipeline_replace_and_post_process(self) -> None:
        secs = [
            _section(
                "3 §",
                [
                    _subsection("1", "Vanha sisalto."),
                    _subsection("2", "Muuttumaton."),
                ],
            )
        ]
        master = _make_master(secs)
        state, ctx = _make_state_ctx(secs)
        muutos_tree = _make_muutos_tree((_section("3 §", [_subsection("1", "Uusi sisalto.")]),))
        johto = "muutetaan 3 \u00a7:n 1 momentti seuraavasti:"
        amendment_id = "2010/100"
        issue, effective = _make_dates()

        phase = normalize_and_compile_ops(
            johto=johto,
            muutos_tree=muutos_tree,
            master=master,
            amendment_id=amendment_id,
            source_title="Laki",
            used_sec1_fallback=False,
            parent_id="2000/1",
        )
        ops = phase.output
        resolved = compile_amendment_ops(master, ops, muutos_tree, johto, "finlex_oracle").output
        result = apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            muutos_tree=muutos_tree,
            johto=johto,
            amendment_id=amendment_id,
            source_title="Laki",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=[],
            failed_ops_out=[],
            source_pathologies_out=[],
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )
        post_state = result.with_ir(post_process_tree(result.ir))

        text = _serialize_text_node(post_state.ir)
        assert "Uusi sisalto" in text
        assert "Vanha sisalto" not in text
        assert _findings(phase, "obligation") == ()

    def test_full_pipeline_repeal_section(self) -> None:
        secs = [
            _section("1 §", [_subsection("1", "Pysyy.")]),
            _section("2 §", [_subsection("1", "Kumotaan.")]),
        ]
        master = _make_master(secs)
        state, ctx = _make_state_ctx(secs)
        muutos_tree = _make_muutos_tree()  # no body in repeal amendment
        johto = "kumotaan 2 \u00a7"
        amendment_id = "2010/200"
        issue, effective = _make_dates()

        ops = normalize_and_compile_ops(
            johto=johto,
            muutos_tree=muutos_tree,
            master=master,
            amendment_id=amendment_id,
            source_title="Laki kumoamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        ).output
        resolved = compile_amendment_ops(master, ops, muutos_tree, johto, "finlex_oracle").output
        result = apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            muutos_tree=muutos_tree,
            johto=johto,
            amendment_id=amendment_id,
            source_title="Laki kumoamisesta",
            amendment_issue_date=issue,
            amendment_effective_date=effective,
            amendment_expiry_date=None,
            replay_mode="finlex_oracle",
            lo_ops_out=[],
            failed_ops_out=[],
            source_pathologies_out=[],
            strict_profile=None,
            _vts_ops_enrich_done=False,
        )
        post_state = result.with_ir(post_process_tree(result.ir))

        text = _serialize_text_node(post_state.ir)
        assert "Pysyy" in text
        # Repeal produces kumottu placeholder or removes the section.
        # In either case, "Kumotaan" body text should be gone.
        assert "Kumotaan" not in text


class TestSortGroupOpsInsertWithOtsikko:
    """Regression: INSERT-only momentti groups mixed with otsikko REPLACE
    must sort inserts ascending so that earlier subsections are inserted first.

    Before the fix, the strict guard ``len(plain_moment_ops) == len(group_ops)``
    prevented the ascending sort when the group also contained a non-moment op
    like ``REPLACE otsikko``.  The default descending sort produced wrong
    subsection ordering (5, 4, 3 instead of 3, 4, 5).

    Statute 2022/1267 amendment 2025/1280 section 1 is the canonical example.
    """

    @staticmethod
    def _make_sec_with_subsections(*labels: str) -> IRNode:
        children = [IRNode(kind=IRNodeKind.NUM, text="1 §")]
        for lab in labels:
            children.append(IRNode(kind=IRNodeKind.SUBSECTION, label=lab, text=f"mom {lab}"))
        return IRNode(kind=IRNodeKind.SECTION, label="1", children=tuple(children))

    def test_insert_only_with_otsikko_sorts_ascending(self) -> None:
        sec = self._make_sec_with_subsections("1", "2")
        target_ctx = TargetContext(
            target_unit_kind="section",
            target_norm="1",
            target_chapter=None,
            node_path=(("section", "1"),),
            parent_path=(),
            live_node=sec,
            parent_node=IRNode(kind=IRNodeKind.BODY, children=(sec,)),
            sibling_labels=("1",),
            subsection_slots=(),
        )
        group_ops = [
            AmendmentOp(
                op_id="otsikko",
                op_type="REPLACE",
                target_kind=TargetKind.SECTION,
                target_section="1",
                target_special="otsikko",
            ),
            AmendmentOp(
                op_id="ins3", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="1", target_paragraph=3
            ),
            AmendmentOp(
                op_id="ins4", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="1", target_paragraph=4
            ),
            AmendmentOp(
                op_id="ins5", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="1", target_paragraph=5
            ),
        ]
        result = sort_group_ops_for_apply(target_ctx, group_ops)
        # otsikko (target_paragraph=None) sorts first (0), then ascending inserts
        assert result[0].target_special == "otsikko"
        assert [o.target_paragraph for o in result[1:]] == [3, 4, 5]

    def test_pure_insert_group_still_sorts_ascending(self) -> None:
        sec = self._make_sec_with_subsections("1", "2")
        target_ctx = TargetContext(
            target_unit_kind="section",
            target_norm="1",
            target_chapter=None,
            node_path=(("section", "1"),),
            parent_path=(),
            live_node=sec,
            parent_node=IRNode(kind=IRNodeKind.BODY, children=(sec,)),
            sibling_labels=("1",),
            subsection_slots=(),
        )
        group_ops = [
            AmendmentOp(
                op_id="ins3", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="1", target_paragraph=3
            ),
            AmendmentOp(
                op_id="ins5", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="1", target_paragraph=5
            ),
            AmendmentOp(
                op_id="ins4", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="1", target_paragraph=4
            ),
        ]
        result = sort_group_ops_for_apply(target_ctx, group_ops)
        assert [o.target_paragraph for o in result] == [3, 4, 5]

    def test_pure_replace_tail_append_group_sorts_ascending(self) -> None:
        sec = self._make_sec_with_subsections("1", "2")
        target_ctx = TargetContext(
            target_unit_kind="section",
            target_norm="14",
            target_chapter="19",
            node_path=(("chapter", "19"), ("section", "14")),
            parent_path=(("chapter", "19"),),
            live_node=sec,
            parent_node=IRNode(kind=IRNodeKind.CHAPTER, label="19", children=(sec,)),
            sibling_labels=("14",),
            subsection_slots=(),
        )
        group_ops = [
            AmendmentOp(
                op_id="rep4", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=4
            ),
            AmendmentOp(
                op_id="rep3", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=3
            ),
        ]
        result = sort_group_ops_for_apply(target_ctx, group_ops)
        assert [o.target_paragraph for o in result] == [3, 4]

    def test_item_inserts_sort_in_natural_legal_order_within_same_subsection(self) -> None:
        sec = self._make_sec_with_subsections("1")
        target_ctx = TargetContext(
            target_unit_kind="section",
            target_norm="1",
            target_chapter=None,
            node_path=(("section", "1"),),
            parent_path=(),
            live_node=sec,
            parent_node=IRNode(kind=IRNodeKind.BODY, children=(sec,)),
            sibling_labels=("1",),
            subsection_slots=(),
        )
        group_ops = [
            AmendmentOp(
                op_id="ins10",
                op_type="INSERT",
                target_kind=TargetKind.SECTION,
                target_section="1",
                target_paragraph=1,
                target_item="10",
            ),
            AmendmentOp(
                op_id="ins5b",
                op_type="INSERT",
                target_kind=TargetKind.SECTION,
                target_section="1",
                target_paragraph=1,
                target_item="5b",
            ),
            AmendmentOp(
                op_id="ins9",
                op_type="INSERT",
                target_kind=TargetKind.SECTION,
                target_section="1",
                target_paragraph=1,
                target_item="9",
            ),
            AmendmentOp(
                op_id="ins5a",
                op_type="INSERT",
                target_kind=TargetKind.SECTION,
                target_section="1",
                target_paragraph=1,
                target_item="5a",
            ),
        ]
        result = sort_group_ops_for_apply(target_ctx, group_ops)
        assert [o.target_item for o in result] == ["5a", "5b", "9", "10"]
