"""Pins the enacted-base loader — the chokepoint that PDF-substitutes empty stubs.

``enacted_base_loader.load_enacted_base`` is the single place every UK replay
driver loads its enacted base.  For an Act whose enacted CLML carries a real
``<Body>`` it must be byte-identical to ``parse_uk_statute_ir_bytes`` (the XML
lane is unchanged).  For a metadata-only PDF-only stub (``NumberOfProvisions="0"``,
empty body) it must substitute the PDF replay base built from the Act's inline
PDF blob — the wiring that lets ~7.8k PDF-only Acts enter the effects/in-force
replay path.  Failure classes (no PDF url, blob absent, image-only scan) must be
attributed via ``pdf_status``, never silently proceeding on an empty base.
"""

from __future__ import annotations

from typing import Optional

from lawvm.uk_legislation.enacted_base_loader import (
    load_enacted_base,
    xml_base_is_empty,
)
from lawvm.uk_legislation.pdf_layout_uk import BodyLine, MarginalNote, UKPdfLayout


# A real Act's enacted CLML: a genuine <Body> with two sections.
_REAL_XML = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
<Metadata><dc:title>Real Act 1885</dc:title></Metadata>
<Primary><Body>
<P1group><Title>Definition of thing</Title>
  <P1><Pnumber>1</Pnumber><P1para><Text>A thing is defined here.</Text></P1para></P1>
</P1group>
</Body></Primary></Legislation>"""

# A PDF-only metadata stub: NumberOfProvisions="0", no Body, names its PDF inline.
_PDF_ONLY_STUB = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
<ukm:Metadata>
  <dc:title>PDF-only Act 1900</dc:title>
  <ukm:SecondaryMetadata><ukm:UnappliedEffects/></ukm:SecondaryMetadata>
  <ukm:EnactmentDate Date="1900-01-01"/>
  <ukm:Statistics><ukm:TotalParagraphs Value="0"/></ukm:Statistics>
  <ukm:Alternatives>
    <ukm:Alternative Date="1900-01-01" Size="1234"
      URI="http://www.legislation.gov.uk/ukpga/1900/5/pdfs/ukpga_19000005_en.pdf"/>
  </ukm:Alternatives>
</ukm:Metadata>
<Primary NumberOfProvisions="0"/>
</Legislation>"""


class _FakeArchive:
    """Minimal archive: locator -> bytes (mirrors Farchive.get/has)."""

    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = dict(blobs)

    def get(self, locator: str) -> Optional[bytes]:
        return self._blobs.get(locator)

    def has(self, locator: str) -> bool:
        return locator in self._blobs


def _pdf_layout() -> UKPdfLayout:
    """A tiny two-section PDF layout standing in for a real segmented scan."""
    return UKPdfLayout(
        body_lines=("1. First provision text.", "2. Second provision text."),
        positioned_body_lines=(
            BodyLine("1. First provision text.", 0, 100.0),
            BodyLine("2. Second provision text.", 0, 200.0),
        ),
        marginal_notes=(
            MarginalNote("First", 0, 100.0),
            MarginalNote("Second", 0, 200.0),
        ),
        detected=True,
    )


def test_real_xml_body_is_returned_unchanged_xml_lane_byte_identical() -> None:
    """An Act with a real <Body> takes the XML lane, identical to a direct parse."""
    from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes

    archive = _FakeArchive({})
    # The loader threads version_label="enacted"/source_path="<archive>" through to
    # parse_uk_statute_ir_bytes; the comparison must pass the same args so the only
    # thing under test is that the XML body is used verbatim.
    direct = parse_uk_statute_ir_bytes(
        _REAL_XML, statute_id="ukpga/1885/1", version_label="enacted"
    )
    result = load_enacted_base("ukpga/1885/1", _REAL_XML, archive)

    assert result.source_lane == "xml"
    assert result.pdf_status is None
    # Byte-identical: same IRStatute the historical path produced.
    assert result.base_ir == direct
    assert not xml_base_is_empty(result.base_ir)


def test_pdf_only_stub_substitutes_pdf_replay_base(monkeypatch) -> None:
    """An empty PDF-only stub with an in-lane PDF blob yields a PDF replay base."""
    # The stub's inline PDF maps to this lane locator.
    pdf_loc = "leg://pdf/ukpga/1900/5/pdfs/ukpga_19000005_en.pdf"
    archive = _FakeArchive({pdf_loc: b"%PDF-1.4 fake"})

    # The XML stub alone parses to an empty base — the trigger for the fallback.
    from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes

    stub_ir = parse_uk_statute_ir_bytes(_PDF_ONLY_STUB, statute_id="ukpga/1900/5")
    assert xml_base_is_empty(stub_ir)

    # Stand in for pdfplumber segmentation with a deterministic layout.
    monkeypatch.setattr(
        "lawvm.uk_legislation.pdf_layout_uk.segment_uk_pdf_layout",
        lambda pdf_bytes, **kw: _pdf_layout(),
    )

    result = load_enacted_base("ukpga/1900/5", _PDF_ONLY_STUB, archive)
    assert result.source_lane == "pdf"
    assert result.pdf_status == "ok"
    assert result.base_ir.metadata.get("source_lane") == "pdf"
    assert result.base_ir.metadata.get("pdf_lane_locator") == pdf_loc
    # The PDF base is non-empty: real provisions entered the replay base.
    assert not xml_base_is_empty(result.base_ir)


def test_pdf_only_stub_with_missing_blob_attributes_the_miss() -> None:
    """No PDF blob in the lane => empty base returned, miss attributed (not silent)."""
    archive = _FakeArchive({})  # PDF never acquired
    result = load_enacted_base("ukpga/1900/5", _PDF_ONLY_STUB, archive)
    assert result.source_lane == "pdf"
    assert result.pdf_status == "pdf_blob_absent"
    # Base stays the empty stub — but the caller can SEE why (pdf_status).
    assert xml_base_is_empty(result.base_ir)


def test_pdf_only_stub_image_only_scan_is_attributed(monkeypatch) -> None:
    """pdfplumber returning None (image-only scan) is an attributed miss."""
    pdf_loc = "leg://pdf/ukpga/1900/5/pdfs/ukpga_19000005_en.pdf"
    archive = _FakeArchive({pdf_loc: b"%PDF-1.4 image only"})
    monkeypatch.setattr(
        "lawvm.uk_legislation.pdf_layout_uk.segment_uk_pdf_layout",
        lambda pdf_bytes, **kw: None,
    )
    result = load_enacted_base("ukpga/1900/5", _PDF_ONLY_STUB, archive)
    assert result.source_lane == "pdf"
    assert result.pdf_status == "pdf_layout_unextractable"
    assert xml_base_is_empty(result.base_ir)
