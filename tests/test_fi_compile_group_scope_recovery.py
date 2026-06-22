from lxml import etree

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, StructuralAction
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.finland.compile_amendment import compile_amendment_ops
from lawvm.finland.compile_group_scope_recovery import (
    CompileGroupScopeRecoveryRequest,
    resolve_compile_group_scope_recovery,
)
from lawvm.finland.ops import (
    AmendmentOp,
    ScopeConfidence,
    ScopeResolutionConfidence,
    ScopeResolutionSource,
)
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
            source=ScopeResolutionSource.PREAMBLE,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="6",
        ),
        scope_provenance_tags=("chapter_scope_from_preamble",),
        source_statute="1998/4",
        lo=LegalOperation(
            op_id="replace_25_heading",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "6"), ("section", "25")), special=FacetKind.HEADING),
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
            source=ScopeResolutionSource.PREAMBLE,
            confidence=ScopeResolutionConfidence.EXPLICIT,
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


def test_source_body_part_scope_is_promoted_when_chapter_already_matches() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="13a",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="147"),),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <part>
              <num>II OSA</num>
              <chapter>
                <num>13 a luku</num>
                <section><num>148 §</num><content><p>uusi 148</p></content></section>
              </chapter>
            </part>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="insert_148",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="148",
        target_chapter="13a",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="13a",
        ),
        source_statute="2016/773",
        lo=LegalOperation(
            op_id="insert_148",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "13a"), ("section", "148"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="148",
            target_chapter="13a",
            target_part=None,
            group_ops=[op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_part == "2"
    assert result.output.effective_target_chapter == "13a"
    assert result.output.group_ops[0].target_part == "2"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (
        ("part", "2"),
        ("chapter", "13a"),
        ("section", "148"),
    )


def test_source_body_scope_overrides_prior_repeal_reinstatement_address() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="14",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="148"),),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <part>
              <num>II OSA</num>
              <chapter>
                <num>13 a luku</num>
                <section><num>148 §</num><content><p>uusi 148</p></content></section>
              </chapter>
            </part>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="insert_148",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="148",
        target_part="2",
        target_chapter="14",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="14",
        ),
        witness_rule_id="fi_reinstated_section_scope_from_prior_repeal_address",
        source_statute="2016/773",
        lo=LegalOperation(
            op_id="insert_148",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("part", "2"), ("chapter", "14"), ("section", "148"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="148",
            target_chapter="14",
            target_part="2",
            group_ops=[op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_part == "2"
    assert result.output.effective_target_chapter == "13a"
    assert result.output.group_ops[0].target_part == "2"
    assert result.output.group_ops[0].target_chapter == "13a"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (
        ("part", "2"),
        ("chapter", "13a"),
        ("section", "148"),
    )


def test_source_body_chapter_with_heading_overrides_live_stem_insert_scope() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="6",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="147"),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="13a",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="147"),),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <part>
              <num>II OSA</num>
              <chapter>
                <num>13 a luku</num>
                <section><num>147 §</num><content><p>uusi 147</p></content></section>
                <section><num>147 a §</num><content><p>uusi 147 a</p></content></section>
              </chapter>
            </part>
          </body>
        </act>
        """
    )
    heading_op = AmendmentOp(
        op_id="replace_13a_heading",
        op_type="REPLACE",
        target_unit_kind="chapter",
        target_section="13a",
        target_special="otsikko",
        source_statute="2016/773",
    )
    op = AmendmentOp(
        op_id="insert_147a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="147a",
        target_chapter="6",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="6",
        ),
        source_statute="2016/773",
        lo=LegalOperation(
            op_id="insert_147a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "6"), ("section", "147a"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="147a",
            target_chapter="6",
            target_part=None,
            group_ops=[op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
            amendment_group_ops=(heading_op, op),
        )
    )

    assert result.output.effective_target_part == "2"
    assert result.output.effective_target_chapter == "13a"
    assert result.output.group_ops[0].target_part == "2"
    assert result.output.group_ops[0].target_chapter == "13a"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (
        ("part", "2"),
        ("chapter", "13a"),
        ("section", "147a"),
    )


def test_pseudo_marker_body_chapter_scopes_following_child_section_insert() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="53"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>7 luku</num>
              <section>
                <num>7 a luku</num>
                <heading>Tuontiluvat</heading>
              </section>
              <section>
                <num>53 a §</num>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_53a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="53a",
        target_chapter="7",
        source_statute="1996/473",
        lo=LegalOperation(
            op_id="insert_53a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "7"), ("section", "53a"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="53a",
            target_chapter="7",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels={"7a"},
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "7a"
    assert result.output.group_ops[0].target_chapter == "7a"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "7a"), ("section", "53a"))
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]


def test_compile_amendment_uses_pseudo_marker_chapter_as_inserted_scope() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="53"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>7 luku</num>
              <section>
                <num>7 a luku</num>
                <heading>Tuontiluvat</heading>
              </section>
              <section>
                <num>53 a §</num>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="insert_53a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="53a",
        target_chapter="7",
        source_statute="1996/473",
        lo=LegalOperation(
            op_id="insert_53a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "7"), ("section", "53a"))),
            payload=None,
        ),
    )

    result = compile_amendment_ops(
        master,
        [op],
        AmendmentSourceModel.from_tree(muutos_tree),
        "lisätään asetukseen uusi 53 a § seuraavasti:",
        "legal_pit",
    )

    assert len(result.output) == 1
    resolved = result.output[0]
    assert resolved.resolved_target_scope_view.target_chapter == "7a"
    assert resolved.resolved_target_address is not None
    assert resolved.resolved_target_address.path == (("chapter", "7a"), ("section", "53a"))


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
    assert result.output.group_ops[0].body_chapter_move_from == "5"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "6"), ("section", "37a"))
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]


def test_real_inserted_body_chapter_scopes_unscoped_section_insert_before_live_stem() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="15"),),
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
                <num>15 a §</num>
                <heading>Varustautumiskorvaus</heading>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_15a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="15a",
        target_chapter=None,
        source_statute="2014/1020",
        lo=LegalOperation(
            op_id="insert_15a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "15a"),)),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="15a",
            target_chapter=None,
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels={"6a"},
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "6a"
    assert result.output.group_ops[0].target_chapter == "6a"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "6a"), ("section", "15a"))
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]


def test_inserted_subchapter_body_overrides_live_stem_scope_guess() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="15"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="18"),),
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
                <num>15 a §</num>
                <heading>Varustautumiskorvaus</heading>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
              <section>
                <num>18 a §</num>
                <heading>Explicitly scoped elsewhere</heading>
                <subsection><content><p>Wrapper sibling.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_15a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="15a",
        target_chapter="6",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="6",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        source_statute="2014/1020",
        lo=LegalOperation(
            op_id="insert_15a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "6"), ("section", "15a"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="15a",
            target_chapter="6",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels={"6a"},
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "6a"
    assert result.output.group_ops[0].target_chapter == "6a"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "6a"), ("section", "15a"))
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]


def test_existing_letter_run_body_chapter_overrides_live_stem_scope_guess() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="70"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9b",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="70s"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>9 b luku</num>
              <section>
                <num>70 s §</num>
                <subsection><content><p>Live sibling context.</p></content></subsection>
              </section>
              <section>
                <num>70 t §</num>
                <subsection><content><p>New letter-run section.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_70t",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="70t",
        target_chapter="9",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="9",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        source_statute="2005/896",
        lo=LegalOperation(
            op_id="insert_70t",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "9"), ("section", "70t"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="70t",
            target_chapter="9",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "9b"
    assert result.output.group_ops[0].target_chapter == "9b"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "9b"), ("section", "70t"))
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]


def test_mixed_body_chapter_wrapper_does_not_override_whole_section_replace_live_scope() -> None:
    """A broad source XML chapter wrapper is not scope authority for REPLACE."""
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="23"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="47"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="10",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="67"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="12",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="80"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>4 luku</num>
              <section><num>23 §</num><content><p>Chapter 4 text.</p></content></section>
              <section><num>47 §</num><content><p>Chapter 6 text.</p></content></section>
              <section><num>67 §</num><content><p>Chapter 10 text.</p></content></section>
              <section><num>80 §</num><content><p>Chapter 12 text.</p></content></section>
            </chapter>
          </body>
        </act>
        """
    )
    replace_op = AmendmentOp(
        op_id="replace_67",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="67",
        target_chapter="4",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="4",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
        source_statute="2022/616",
        lo=LegalOperation(
            op_id="replace_67",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "4"), ("section", "67"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="67",
            target_chapter="4",
            target_part=None,
            group_ops=[replace_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "10"
    assert result.output.group_ops[0].target_chapter == "10"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "10"), ("section", "67"))
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
    ]


def test_existing_letter_run_body_chapter_needs_live_same_stem_sibling() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="70"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9b",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="80s"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>9 b luku</num>
              <section>
                <num>70 t §</num>
                <subsection><content><p>New section without sibling witness.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_70t",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="70t",
        target_chapter="9",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="9",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        source_statute="2005/896",
        lo=LegalOperation(
            op_id="insert_70t",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "9"), ("section", "70t"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="70t",
            target_chapter="9",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "9"
    assert result.output.group_ops[0].target_chapter == "9"
    assert result.findings() == ()


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
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
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


def test_live_stem_insert_keeps_existing_source_body_chapter_with_sibling_heading() -> None:
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
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        source_statute="test/1",
        lo=LegalOperation(
            op_id="insert_37a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "37a"))),
            payload=None,
        ),
    )
    heading_op = AmendmentOp(
        op_id="replace_chapter_6_heading",
        op_type="REPLACE",
        target_unit_kind="chapter",
        target_section="6",
        target_special="otsikko",
        source_statute="test/1",
        lo=LegalOperation(
            op_id="replace_chapter_6_heading",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "6"),), special=FacetKind.HEADING),
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
            amendment_group_ops=(heading_op, insert_op),
        )
    )

    assert result.output.effective_target_chapter == "6"
    assert result.output.group_ops[0].target_chapter == "6"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "6"), ("section", "37a"))


def test_live_stem_insert_without_sibling_heading_keeps_live_stem_scope() -> None:
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
                <subsection><content><p>Carried context.</p></content></subsection>
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
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
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
            amendment_group_ops=(insert_op,),
        )
    )

    assert result.output.effective_target_chapter == "5"
    assert result.output.group_ops[0].target_chapter == "5"
    assert result.findings() == ()


def test_live_stem_insert_multi_section_body_chapter_keeps_live_stem_scope() -> None:
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
                <subsection><content><p>First carried context.</p></content></subsection>
              </section>
              <section>
                <num>38 a §</num>
                <subsection><content><p>Second carried context.</p></content></subsection>
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
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        source_statute="test/1",
        lo=LegalOperation(
            op_id="insert_37a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "37a"))),
            payload=None,
        ),
    )
    heading_op = AmendmentOp(
        op_id="replace_chapter_6_heading",
        op_type="REPLACE",
        target_unit_kind="chapter",
        target_section="6",
        target_special="otsikko",
        source_statute="test/1",
        lo=LegalOperation(
            op_id="replace_chapter_6_heading",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "6"),), special=FacetKind.HEADING),
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
            amendment_group_ops=(heading_op, insert_op),
        )
    )

    assert result.output.effective_target_chapter == "5"
    assert result.output.group_ops[0].target_chapter == "5"
    assert result.findings() == ()


def test_source_owned_existing_chapter_insert_is_not_retargeted_to_duplicate_live_section() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="4"),
                        IRNode(kind=IRNodeKind.SECTION, label="5"),
                        IRNode(kind=IRNodeKind.SECTION, label="6"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="2b"),
                        IRNode(kind=IRNodeKind.SECTION, label="3"),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>2 luku</num>
              <section>
                <num>5 §</num>
                <heading>Tutkinta-arestiin liittyvät ilmoitukset</heading>
                <subsection><content><p>Uusi pykälä.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_2_5",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="5",
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="2",
        ),
        scope_provenance_tags=("chapter_scope_from_explicit_chunk",),
        source_statute="2018/1134",
        lo=LegalOperation(
            op_id="insert_2_5",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "2"), ("section", "5"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="5",
            target_chapter="2",
            target_part=None,
            group_ops=[insert_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.effective_target_chapter == "2"
    assert result.output.group_ops[0].target_chapter == "2"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (("chapter", "2"), ("section", "5"))
    assert result.findings() == ()


def test_item_targets_rewrite_to_subsections_for_flat_definition_entries() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.HEADING, text="Määritelmiä"),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="12"),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section>
              <num>2 §</num>
              <subsection><content><p>11 a. Uusi määritelmä.</p></content></subsection>
              <subsection><content><p>12. Korvattu määritelmä.</p></content></subsection>
            </section>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_11a",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="2",
        target_chapter="1",
        target_paragraph=1,
        target_item="11a",
        source_statute="2006/168",
        lo=LegalOperation(
            op_id="insert_11a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(
                path=(
                    ("chapter", "1"),
                    ("section", "2"),
                    ("subsection", "1"),
                    ("item", "11a"),
                )
            ),
            payload=None,
        ),
    )
    replace_op = AmendmentOp(
        op_id="replace_12",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="2",
        target_chapter="1",
        target_paragraph=1,
        target_item="12",
        source_statute="2006/168",
        lo=LegalOperation(
            op_id="replace_12",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(
                path=(
                    ("chapter", "1"),
                    ("section", "2"),
                    ("subsection", "1"),
                    ("item", "12"),
                )
            ),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="2",
            target_chapter="1",
            target_part=None,
            group_ops=[insert_op, replace_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert [op.target_item for op in result.output.group_ops] == [None, None]
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (
        ("chapter", "1"),
        ("section", "2"),
        ("subsection", "11a"),
    )
    assert result.output.group_ops[1].lo is not None
    assert result.output.group_ops[1].lo.target.path == (
        ("chapter", "1"),
        ("section", "2"),
        ("subsection", "12"),
    )
    assert [finding.kind for finding in result.findings()] == [
        "LOWER.ITEM_AS_SUBSECTION_TARGET_REWRITE"
    ]


def test_item_targets_do_not_rewrite_when_live_host_has_paragraph_items() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.HEADING, text="Määritelmiä"),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section>
              <num>2 §</num>
              <subsection><content><p>2. Not a subsection rewrite.</p></content></subsection>
            </section>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="replace_item_2",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="2",
        target_paragraph=1,
        target_item="2",
        source_statute="2020/1",
        lo=LegalOperation(
            op_id="replace_item_2",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(
                path=(("section", "2"), ("subsection", "1"), ("item", "2"))
            ),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="2",
            target_chapter=None,
            target_part=None,
            group_ops=[op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    assert result.output.group_ops[0].target_item == "2"
    assert result.output.group_ops[0].lo is not None
    assert result.output.group_ops[0].lo.target.path == (
        ("section", "2"),
        ("subsection", "1"),
        ("item", "2"),
    )
    assert result.findings() == ()
