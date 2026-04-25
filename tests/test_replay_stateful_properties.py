"""Stateful replay-sequence properties for the Finland subsection seam.

These tests keep the replay/apply slot behavior honest for a tiny semantic
world: one body, one section, a short list of numbered subsections.

The goal is not to fuzz full statutes. The goal is to catch the specific
sequence bugs that matter for replay:

* replace a subsection slot without disturbing untouched siblings
* insert a subsection slot and renumber later numeric siblings deterministically
* repeal a subsection slot without clobbering unrelated siblings
* preserve section-level structure across arbitrary short op sequences
"""

from __future__ import annotations

import string
from dataclasses import dataclass

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import check_invariants
from lawvm.finland.apply_subsection_ops import (
    _apply_subsection_insert,
    _apply_subsection_repeal,
    _apply_subsection_replace,
)
from lawvm.finland.ops import AmendmentOp, OpType, get_replay_profile
from lawvm.finland.statute import ReplayState


SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=24,
)

_PROFILE = get_replay_profile("legal_pit")


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _sub(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=text)


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _make_state(section: IRNode) -> ReplayState:
    return ReplayState(ir=_body(section))


def _make_op(
    op_type: OpType,
    target_label: int,
    *,
    source_suffix: str = "seq",
) -> AmendmentOp:
    return AmendmentOp(
        op_id=f"{op_type.lower()}_{target_label}_{source_suffix}",
        op_type=op_type,
        target_section="1",
        target_unit_kind="section",
        target_paragraph=target_label,
        source_statute="2020/1",
    )


def _section_path(state: ReplayState) -> tuple[tuple[str, str], ...]:
    path = state.find_section_path("1")
    assert path is not None
    return path


def _section_signature(section: IRNode) -> tuple:
    return (
        section.kind.value,
        section.label,
        section.text,
        tuple(sorted(section.attrs.items())),
        tuple(
            (
                child.kind.value,
                child.label,
                child.text,
                tuple(sorted(child.attrs.items())),
                tuple(grand.kind.value for grand in child.children),
            )
            for child in section.children
        ),
    )


def _expected_section_signature(model: list[tuple[str, str]]) -> tuple:
    return (
        IRNodeKind.SECTION.value,
        "1",
        "",
        (),
        (
            (
                IRNodeKind.CONTENT.value,
                None,
                "1 §",
                (),
                (),
            ),
            *(
                (
                    IRNodeKind.SUBSECTION.value,
                    label,
                    text,
                    (),
                    (),
                )
                for label, text in model
            ),
        ),
    )


def _model_insert(model: list[tuple[str, str]], target_label: int, text: str) -> list[tuple[str, str]]:
    """Mirror _insert_subsection_with_renumber_ir for a tiny numeric slot list."""
    rebuilt = list(model)
    insert_idx = next((i for i, (label, _txt) in enumerate(rebuilt) if int(label) >= target_label), len(rebuilt))
    rebuilt.insert(insert_idx, (str(target_label), text))

    next_num = target_label + 1
    for idx in range(insert_idx + 1, len(rebuilt)):
        label, slot_text = rebuilt[idx]
        current_num = int(label)
        if current_num < next_num:
            rebuilt[idx] = (str(next_num), slot_text)
            current_num = next_num
        next_num = current_num + 1
    return rebuilt


def _model_replace(model: list[tuple[str, str]], target_label: int, text: str) -> list[tuple[str, str]]:
    rebuilt = list(model)
    for idx, (label, _slot_text) in enumerate(rebuilt):
        if int(label) == target_label:
            rebuilt[idx] = (label, text)
            return rebuilt
    raise AssertionError(f"target label {target_label} not present in model")


def _model_repeal(model: list[tuple[str, str]], target_label: int) -> list[tuple[str, str]]:
    rebuilt = [(label, text) for label, text in model if int(label) != target_label]
    if len(rebuilt) == len(model):
        raise AssertionError(f"target label {target_label} not present in model")
    return rebuilt


@st.composite
def initial_section_case(draw) -> tuple[IRNode, list[tuple[str, str]]]:
    n_subsections = draw(st.integers(min_value=2, max_value=5))
    children = tuple(_sub(str(i), f"base {i}: {draw(SHORT_TEXT)}") for i in range(1, n_subsections + 1))
    section = _sec("1", _content("1 §"), *children)
    model = [(child.label or "", child.text or "") for child in children]
    return section, model


@dataclass
class _StateModel:
    state: ReplayState
    model: list[tuple[str, str]]


class ReplaySubsectionSequenceMachine(RuleBasedStateMachine):
    """State machine for short subsection-slot replay sequences."""

    def __init__(self) -> None:
        super().__init__()
        self._sm: _StateModel | None = None

    @initialize(case=initial_section_case())
    def init_state(self, case: tuple[IRNode, list[tuple[str, str]]]) -> None:
        section, model = case
        self._sm = _StateModel(state=_make_state(section), model=list(model))

    def _require_state(self) -> _StateModel:
        assert self._sm is not None
        return self._sm

    def _current_section(self) -> IRNode:
        sm = self._require_state()
        section = sm.state.find_section("1")
        assert section is not None
        return section

    def _check_against_model(self) -> None:
        sm = self._require_state()
        section = self._current_section()
        subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
        got = [(sub.label or "", sub.text or "") for sub in subsections]
        assert got == sm.model
        assert _section_signature(section) == _expected_section_signature(sm.model)
        assert check_invariants(sm.state.ir) == []

    def _apply_and_sync(
        self,
        *,
        op_type: OpType,
        target_label: int,
        amend_text: str | None,
    ) -> None:
        sm = self._require_state()
        section = self._current_section()
        sec_path = list(_section_path(sm.state))
        subsecs = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
        ctx_label = f"1 § {op_type.lower()} {target_label}"
        op = _make_op(op_type, target_label)
        amend_sub = _sub(str(target_label), amend_text or "") if amend_text is not None else None

        if op_type == "REPLACE":
            result = _apply_subsection_replace(
                sm.state,
                op,
                sec_path,
                section,
                subsecs,
                amend_sub,
                None,
                _PROFILE,
                ctx_label,
            )
        elif op_type == "INSERT":
            result = _apply_subsection_insert(
                sm.state,
                op,
                sec_path,
                section,
                subsecs,
                amend_sub,
                ctx_label,
            )
        elif op_type == "REPEAL":
            result = _apply_subsection_repeal(
                sm.state,
                op,
                sec_path,
                section,
                subsecs,
                _PROFILE,
                ctx_label,
            )
        else:
            raise AssertionError(f"Unsupported op_type {op_type!r}")

        assert result is not None and result is not sm.state
        sm.state = result

    @rule(data=st.data(), text=SHORT_TEXT)
    def replace_slot(self, data: st.DataObject, text: str) -> None:
        sm = self._require_state()
        if not sm.model:
            return
        target_label = data.draw(st.sampled_from([int(label) for label, _ in sm.model]))
        self._apply_and_sync(op_type="REPLACE", target_label=target_label, amend_text=text)
        sm.model = _model_replace(sm.model, target_label, text)

    @rule(data=st.data(), text=SHORT_TEXT)
    def insert_slot(self, data: st.DataObject, text: str) -> None:
        sm = self._require_state()
        if not sm.model:
            return
        target_label = data.draw(st.integers(min_value=1, max_value=len(sm.model) + 1))
        self._apply_and_sync(op_type="INSERT", target_label=target_label, amend_text=text)
        sm.model = _model_insert(sm.model, target_label, text)

    @rule(data=st.data())
    def repeal_slot(self, data: st.DataObject) -> None:
        sm = self._require_state()
        if not sm.model:
            return
        target_label = data.draw(st.sampled_from([int(label) for label, _ in sm.model]))
        self._apply_and_sync(op_type="REPEAL", target_label=target_label, amend_text=None)
        sm.model = _model_repeal(sm.model, target_label)

    @rule(data=st.data(), text=SHORT_TEXT)
    def renumber_slot(self, data: st.DataObject, text: str) -> None:
        sm = self._require_state()
        # Force a shift-heavy insert by targeting an existing numeric slot when
        # possible; otherwise the rule falls back to a late append.
        if not sm.model:
            return
        target_label = data.draw(st.sampled_from([int(label) for label, _ in sm.model]))
        self._apply_and_sync(op_type="INSERT", target_label=target_label, amend_text=text)
        sm.model = _model_insert(sm.model, target_label, text)

    @invariant()
    def model_matches_section(self) -> None:
        self._check_against_model()

TestReplaySubsectionSequenceMachine = ReplaySubsectionSequenceMachine.TestCase
TestReplaySubsectionSequenceMachine.settings = settings(max_examples=50, stateful_step_count=25, deadline=None)
