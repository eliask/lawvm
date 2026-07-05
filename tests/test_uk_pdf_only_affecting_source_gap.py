"""PDF-only affecting-source structural effects TYPE their gap, not silent no-op.

When a UK effect names an affecting Act that exists upstream only as PDF, that
Act's ``/data.xml`` (and ``/enacted/data.xml``) is a ``NumberOfProvisions="0"``
metadata stub with no ``<Body>``/``<Schedule>``.  The size-only source gate reports
it "available", so the affecting-source extraction parses an empty tree, finds no
node, and a structural effect against it produces ZERO ops.

Before #222 that zero-op outcome was buried under the generic
``uk_effect_missing_structural_payload_rejected`` (reason ``missing_extracted_
payload``) — indistinguishable from a target-geometry miss against a REAL body.
The fix threads a ``source_metadata_only`` signal (computed by
``uk_root_is_metadata_only_stub`` on the already-parsed root at source-context
build) into the lowering gate, so the PDF-only case emits the distinct typed pathology
``uk_effect_pdf_only_affecting_source_missing_payload_rejected`` naming the missing
input, while every real-body case is byte-identical (the flag is False).
"""
from __future__ import annotations

from typing import Any, Optional

from lawvm.uk_legislation.effect_compiler import compile_effect_to_ir_ops
from lawvm.uk_legislation.effect_source_selection import select_source_for_effect
from lawvm.uk_legislation.effects import UKEffectRecord

_PDF_ONLY_STUB = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation" '
    b'NumberOfProvisions="0">'
    b'<ukm:Metadata xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">'
    b'<ukm:PrimaryMetadata><ukm:DocumentClassification/></ukm:PrimaryMetadata>'
    b'</ukm:Metadata>'
    b'</Legislation>'
)


class _Archive:
    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._m = mapping

    def get(self, url: str) -> Optional[bytes]:
        return self._m.get(url)


def _structural_effect_against(affecting_act: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="repro-effect-1",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/1990/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1990",
        affected_number="1",
        affected_provisions="s. 5",
        affecting_uri=f"http://www.legislation.gov.uk/id/{affecting_act}",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="1985",
        affecting_number="99",
        affecting_provisions="Sch. 2 para. 3",
        affecting_title="PDF-Only Amending Act 1985",
    )


def _select(effect: UKEffectRecord, archive: _Archive):
    return select_source_for_effect(
        effect=effect,
        archive=archive,
        applicability_mode="effective_date_plus_feed_applied",
        extraction_cache={},
        enacted_extraction_cache={},
        effect_diagnostics_out=[],
    )


def test_pdf_only_affecting_source_context_flags_metadata_only() -> None:
    effect = _structural_effect_against("ukpga/1985/99")
    archive = _Archive(
        {
            "https://www.legislation.gov.uk/ukpga/1985/99/data.xml": _PDF_ONLY_STUB,
        }
    )
    selection = _select(effect, archive)
    # The size gate calls the stub "available", but the structure-aware build
    # flags it as a PDF-only metadata stub and extracts nothing.
    assert selection.source_context.source_status == "available"
    assert selection.source_context.source_metadata_only is True
    assert selection.extracted_el is None


def test_pdf_only_affecting_source_emits_typed_gap_not_generic_missing_payload() -> None:
    effect = _structural_effect_against("ukpga/1985/99")
    archive = _Archive(
        {
            "https://www.legislation.gov.uk/ukpga/1985/99/data.xml": _PDF_ONLY_STUB,
        }
    )
    selection = _select(effect, archive)

    rejections: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        effect,
        selection.extracted_el,
        sequence=0,
        lowering_rejections_out=rejections,
        source_root=selection.source_context.root,
        source_authority_layer=selection.source_context.authority_layer,
        source_metadata_only=selection.source_context.source_metadata_only,
    )
    assert ops == []
    rule_ids = [r.get("rule_id") for r in rejections]
    assert "uk_effect_pdf_only_affecting_source_missing_payload_rejected" in rule_ids
    assert "uk_effect_missing_structural_payload_rejected" not in rule_ids
    typed = next(
        r
        for r in rejections
        if r.get("rule_id")
        == "uk_effect_pdf_only_affecting_source_missing_payload_rejected"
    )
    assert typed["reason_code"] == "pdf_only_affecting_source_no_extractable_payload"
    assert typed["affecting_source_lane"] == "pdf_only_metadata_stub"
    assert typed["affecting_act_id"] == "ukpga/1985/99"
    assert typed["blocking"] is True


def test_real_body_affecting_source_unchanged_generic_gate_when_target_missing() -> None:
    # A real-body affecting act whose provision_ref does NOT resolve to a node
    # must still hit the GENERIC missing-payload gate (source_metadata_only False),
    # proving the XML lane behaviour is untouched.
    real_body = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation" '
        b'NumberOfProvisions="1">'
        b'<Primary><Body><P1group><P1><Pnumber>1</Pnumber>'
        b'<P1para><Text>Unrelated body text.</Text></P1para>'
        b'</P1></P1group></Body></Primary>'
        b'</Legislation>'
    )
    effect = _structural_effect_against("ukpga/1985/99")
    archive = _Archive(
        {
            "https://www.legislation.gov.uk/ukpga/1985/99/data.xml": real_body,
        }
    )
    selection = _select(effect, archive)
    assert selection.source_context.source_metadata_only is False
    assert selection.extracted_el is None  # Sch. 2 para. 3 not in this body

    rejections: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        effect,
        selection.extracted_el,
        sequence=0,
        lowering_rejections_out=rejections,
        source_root=selection.source_context.root,
        source_authority_layer=selection.source_context.authority_layer,
        source_metadata_only=selection.source_context.source_metadata_only,
    )
    assert ops == []
    rule_ids = [r.get("rule_id") for r in rejections]
    assert "uk_effect_missing_structural_payload_rejected" in rule_ids
    assert (
        "uk_effect_pdf_only_affecting_source_missing_payload_rejected" not in rule_ids
    )
