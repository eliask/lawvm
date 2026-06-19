from lxml import etree

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.compile_group_scope_recovery import (
    CompileGroupScopeRecoveryRequest,
    resolve_compile_group_scope_recovery,
)
from lawvm.finland.ops import AmendmentOp, ScopeConfidence
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState


def test_inserted_body_chapter_scopes_following_child_section_insert() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="25"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>6 a luku</num>
              <section>
                <num>25 §</num>
                <heading>Tarkemmat säännökset, määräykset ja ohjeet</heading>
                <hcontainer name="omission"/>
                <subsection><content><p>uusi 2 momentti</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    heading_op = AmendmentOp(
        op_id="replace_25_heading",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="25",
        target_chapter="6",
        target_special="otsikko",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_preamble",
            source="preamble",
            confidence="explicit",
            resolved_chapter="6",
        ),
        scope_provenance_tags=("chapter_scope_from_preamble",),
        source_statute="1998/4",
        lo=LegalOperation(
            op_id="replace_25_heading",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "6"), ("section", "25")), special="heading"),
            payload=None,
        ),
    )
    insert_op = AmendmentOp(
        op_id="insert_25_subsec_2",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="25",
        target_chapter="6",
        target_paragraph=2,
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_preamble",
            source="preamble",
            confidence="explicit",
            resolved_chapter="6",
        ),
        scope_provenance_tags=("chapter_scope_from_preamble",),
        source_statute="1998/4",
        lo=LegalOperation(
            op_id="insert_25_subsec_2",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(
                path=(("chapter", "6"), ("section", "25"), ("subsection", "2"))
            ),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="25",
            target_chapter="6",
            target_part=None,
            group_ops=[heading_op, insert_op],
            inserted_chapter_labels={"6a"},
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "6a"
    assert [op.target_chapter for op in result.output.group_ops] == ["6a", "6a"]
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "6a"), ("section", "25"))
    assert result.output.group_ops[1].lo is not None
    assert result.output.group_ops[1].lo.target.path == (
        ("chapter", "6a"),
        ("section", "25"),
        ("subsection", "2"),
    )
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]


def test_real_inserted_body_chapter_overrides_nonexplicit_family_target() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>6 luku</num>
              <section>
                <num>37 a §</num>
                <heading>Tietojen kirjaaminen tietokantaan</heading>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_37a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="37a",
        target_chapter="5",
        source_statute="2025/500",
        lo=LegalOperation(
            op_id="insert_37a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "37a"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="37a",
            target_chapter="5",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels={"6"},
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "6"
    assert result.output.group_ops[0].target_chapter == "6"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "6"), ("section", "37a"))
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]


def test_real_inserted_body_chapter_does_not_override_explicit_source_chapter() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>6 luku</num>
              <section>
                <num>37 a §</num>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_37a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="37a",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_from_explicit_chunk",),
        source_statute="test/1",
        lo=LegalOperation(
            op_id="insert_37a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "37a"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="37a",
            target_chapter="5",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels={"6"},
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "5"
    assert result.output.group_ops[0].target_chapter == "5"
    assert result.findings() == ()


def test_unscoped_insert_inferred_to_live_family_keeps_existing_source_body_chapter() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="38"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>6 luku</num>
              <section>
                <num>37 a §</num>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_37a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="37a",
        target_chapter="5",
        source_statute="test/1",
        lo=LegalOperation(
            op_id="insert_37a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "37a"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="37a",
            target_chapter="5",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "6"
    assert result.output.group_ops[0].target_chapter == "6"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "6"), ("section", "37a"))
