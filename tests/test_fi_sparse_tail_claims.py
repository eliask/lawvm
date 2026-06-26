from __future__ import annotations

from lxml import etree

from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.compile_amendment import compile_amendment_ops
from lawvm.finland.compile_group_surface import BuildGroupSurfaceRequest, build_group_surface
from lawvm.finland.corpus import get_corpus_store
from lawvm.finland.frontend_compile import normalize_and_compile_ops
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.ops import OpType, AmendmentOp
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.sparse_tail_claims import (
    SPARSE_OMISSION_TAIL_CLAIM_RULE,
    SPARSE_OMISSION_TAIL_PRUNE_RULE,
    build_sparse_omission_tail_claims,
    prune_sparse_tail_claims_from_carrier,
)


def test_sparse_omission_tail_claim_synthesizes_missing_descendant_payload() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <chapter>
                <num>2 luku</num>
                <section>
                  <num>29 \xc2\xa7</num>
                  <subsection><num>1 mom.</num><content>First.</content></subsection>
                  <subsection><num>2 mom.</num><content>Second.</content></subsection>
                  <hcontainer name="omission"/>
                  <subsection><num>3 mom.</num><content>Claimed tail.</content></subsection>
                </section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2099/1")
    carrier = AmendmentOp(
        op_id="replace_29",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="29",
        target_chapter="4",
        source_statute="2099/1",
    )
    descendant = AmendmentOp(
        op_id="replace_31_3",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="31",
        target_chapter="4",
        target_paragraph=3,
        source_statute="2099/1",
    )

    claims = build_sparse_omission_tail_claims([carrier, descendant], model)

    assert len(claims) == 1
    claim = claims[0]
    assert claim.carrier_section == "29"
    assert claim.carrier_source_chapter == "2"
    assert claim.target_section == "31"
    assert irnode_to_text(claim.payload_section_ir()) == "31 § 3 mom. Claimed tail."

    surface_result = build_group_surface(
        BuildGroupSurfaceRequest(
            group_ops=[descendant],
            target_unit_kind="section",
            target_norm="31",
            target_chapter="4",
            target_part=None,
            source_model=model,
            sparse_omission_tail_claims=claims,
        )
    )

    assert surface_result.output.body_ir is not None
    assert surface_result.output.body_ir.kind is IRNodeKind.SECTION
    assert surface_result.output.body_ir.label == "31"
    assert "Claimed tail." in irnode_to_text(surface_result.output.body_ir)
    assert any(finding.kind == SPARSE_OMISSION_TAIL_CLAIM_RULE for finding in surface_result.findings())

    carrier_payload = model.lookup_payload_ir("section", "29", "2", None).payload_ir
    pruned_payload, pruned_claims = prune_sparse_tail_claims_from_carrier(
        carrier_payload,
        claims,
        target_norm="29",
        target_chapter="2",
        target_part=None,
    )

    assert len(pruned_claims) == 1
    assert pruned_payload is not None
    assert "Claimed tail." not in irnode_to_text(pruned_payload)


def test_1995_1084_routes_sparse_tail_from_29_to_31_third_moment() -> None:
    before = replay_xml(
        request=ReplayXmlRequest(
            parent_id="1985/336",
            mode="official_consolidation",
            stop_before="1995/1084",
            quiet=True,
            build_full_products=False,
        )
    )
    xml = get_corpus_store().read_source("1995/1084")
    assert xml is not None
    tree = etree.fromstring(xml)
    johto = get_johtolause(xml)
    source_model = AmendmentSourceModel.from_tree(tree, source_ref="1995/1084")
    phase = normalize_and_compile_ops(
        johto,
        tree,
        before.state,
        "1995/1084",
        "Asetus harjoittelukouluasetuksen muuttamisesta",
        False,
        parent_id="1985/336",
        source_model=source_model,
    )
    ops = [op for op in phase.output if str(op.target_cols.target_section) in {"29", "31"}]

    result = compile_amendment_ops(
        before.state,
        ops,
        source_model,
        johto,
        "official_consolidation",
        source_ref="1995/1084",
        target_statute="1985/336",
    )

    assert {rop.op.target_cols.target_section for rop in result.output} == {"29", "31"}
    section_29 = next(rop for rop in result.output if rop.op.target_cols.target_section == "29")
    section_31 = next(rop for rop in result.output if rop.op.target_cols.target_section == "31")
    assert section_29.muutos_ir is not None
    assert section_31.muutos_ir is not None

    section_29_text = irnode_to_text(section_29.muutos_ir)
    section_31_text = irnode_to_text(section_31.muutos_ir)
    assert "opettajankoulutusyksikkö voi harjoittelukoululain 11 §:n 2 momentissa" not in section_29_text
    assert "opettajankoulutusyksikkö voi harjoittelukoululain 11 §:n 2 momentissa" in section_31_text
    assert section_31.op.target_cols.target_paragraph == 3

    finding_kinds = {finding.kind for finding in result.findings()}
    assert SPARSE_OMISSION_TAIL_CLAIM_RULE in finding_kinds
    assert SPARSE_OMISSION_TAIL_PRUNE_RULE in finding_kinds
    assert not any(
        finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
        and finding.detail.get("reason_code") == "ELAB.REJECTED_NO_SOURCE_PAYLOAD"
        and "31" in str(finding.detail.get("description", ""))
        for finding in result.findings()
    )
