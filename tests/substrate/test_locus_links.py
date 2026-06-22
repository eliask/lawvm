"""Unit tests for LOCUS implied-links extraction (citations; the harmonization
measurement is duckdb-backed and exercised by the e2e demo, not here).

These drive the citation extractor + per-work resolver with SYNTHETIC rows (no
duckdb / no parquet), asserting:

* typed reference extraction (internal section/container vs external USC/CFR);
* internal refs resolve to claimed addresses in the same work → real
  ``reference_resolution`` overlays with content-addressed ``resolution_id``;
* an internal ref to an address that does NOT exist stays a TYPED unresolved
  target (counted, never a phantom edge) — the honesty backstop.
"""

from __future__ import annotations

from lawvm.substrate.locus import LocusRow
from lawvm.substrate.locus_links import (
    extract_references,
    resolve_work_citations,
)


def _scores() -> dict[str, float | None]:
    return {k: None for k in ("enforcement_discretion", "opacity", "paternalism", "problem_salience")}


def test_extract_typed_references() -> None:
    text = (
        "Penalties are as provided in Section 5.30.090 and Chapter 6. "
        "This is subject to 26 U.S.C. § 501 and 40 C.F.R. § 60.1."
    )
    refs = extract_references(text, 0)
    kinds = {r.target_kind for r in refs}
    assert "internal_section" in kinds
    assert "internal_container" in kinds
    assert "external_usc" in kinds
    assert "external_cfr" in kinds
    # the internal section ref normalizes to its dotted comparison token.
    sect = [r for r in refs if r.target_kind == "internal_section"]
    assert any(r.target_token == "5.30.090" for r in sect)


def test_dash_section_normalizes_to_dotted() -> None:
    refs = extract_references("See Section 38-1014 for details.", 0)
    sect = [r for r in refs if r.target_kind == "internal_section"]
    assert sect and sect[0].target_token == "38.1014"


def _work_rows() -> list[LocusRow]:
    return [
        LocusRow(0, "### 5.30.090 Penalty.", "Violations are punishable as provided herein.",
                 True, "Process", None, _scores()),
        LocusRow(1, "### 5.30.100 Cross-ref.", "See Section 5.30.090 and Chapter 6 for penalties.",
                 True, "Process", None, _scores()),
        LocusRow(2, "### 6.10.010 Chapter 6 head.", "Refers to Section 9.99.999 which does not exist.",
                 True, "Process", None, _scores()),
    ]


def test_internal_refs_resolve_to_addresses() -> None:
    result = resolve_work_citations("us-local:cities:zz/testville", _work_rows(), "us-local:corpus:test")
    # Section 5.30.090 exists (row 0) → at least one resolved internal ref.
    assert result.internal_resolved >= 1
    assert result.internal_resolve_rate > 0.0
    # The resolution is a real reference_resolution overlay, content-addressed.
    assert result.resolutions
    body = result.resolutions[0]
    assert body["overlay_kind"] == "reference_resolution"
    assert body["resolution_id"].startswith("sha256:")
    assert body["target_selector"]["target_address"] == "title:5/chapter:30/section:090"


def test_unresolved_internal_stays_typed() -> None:
    """A ref to a non-existent address is a TYPED unresolved target, not a phantom."""
    result = resolve_work_citations("us-local:cities:zz/testville", _work_rows(), "us-local:corpus:test")
    # Section 9.99.999 (row 2) does not exist in the work.
    assert any("9.99.999" in t for t in result.unresolved_internal_tokens)
    # It is counted as an internal ref but NOT resolved.
    assert result.internal_refs > result.internal_resolved


def test_external_refs_counted_not_resolved() -> None:
    rows = [
        LocusRow(0, "### 1.05.010 Fed.", "Governed by 42 U.S.C. § 1983 and 29 C.F.R. § 1910.",
                 True, "Process", None, _scores()),
    ]
    result = resolve_work_citations("us-local:cities:zz/x", rows, "us-local:corpus:test")
    assert result.external_refs >= 2
    # No internal resolution for external refs.
    assert result.internal_resolved == 0
    assert "external_usc" in result.refs_by_kind
    assert "external_cfr" in result.refs_by_kind
