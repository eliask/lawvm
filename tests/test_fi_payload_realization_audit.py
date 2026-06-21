from __future__ import annotations

from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.payload_realization_audit import payload_realization_findings
from lawvm.finland.source_model import AmendmentSourceModel


_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _source(inner: str) -> AmendmentSourceModel:
    root = etree.fromstring(
        f'<act xmlns="{_NS}"><body>{inner}</body></act>'.encode()
    )
    return AmendmentSourceModel.from_tree(root, source_ref="2000/1")


def _after(text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
            ),
        ),
    )


def test_payload_realization_audit_is_clean_when_payload_text_lands() -> None:
    source_model = _source(
        """
        <section>
          <num>1 §</num>
          <subsection><content><p>Substantive amendment payload appears here.</p></content></subsection>
        </section>
        """
    )

    findings = payload_realization_findings(
        source_model=source_model,
        after_ir=_after("The folded statute says substantive amendment payload appears here."),
        amendment_id="2000/1",
    )

    assert findings == ()


def test_payload_realization_audit_reports_missing_payload_text() -> None:
    source_model = _source(
        """
        <section>
          <num>1 §</num>
          <subsection><content><p>Substantive amendment payload appears here.</p></content></subsection>
        </section>
        """
    )

    findings = payload_realization_findings(
        source_model=source_model,
        after_ir=_after("The folded statute still contains unrelated old text."),
        amendment_id="2000/1",
    )

    assert [finding.kind for finding in findings] == ["COVERAGE.PAYLOAD_REALIZATION_GAP"]
    assert findings[0].stage == "post_apply_payload_realization"
    assert findings[0].source_statute == "2000/1"
    assert findings[0].detail["unit_id"] == "section_1"
    assert findings[0].detail["disposition"] == "source_payload_text_not_realized_in_post_fold_state"


def test_payload_realization_audit_ignores_nonoperative_units() -> None:
    source_model = _source(
        """
        <section>
          <num>1 §</num>
          <heading>Voimaantulo</heading>
          <subsection><content><p>This commencement payload should not be required in the parent fold.</p></content></subsection>
        </section>
        """
    )

    findings = payload_realization_findings(
        source_model=source_model,
        after_ir=_after("The parent statute body is unchanged."),
        amendment_id="2000/1",
    )

    assert findings == ()
