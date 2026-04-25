"""Tests for the peg-audit scan preservation auditor.

Verifies that audit_scan_preservation correctly identifies structural
tokens that survive through the annotation pipeline vs those covered
by annotation spans vs those that are unaccounted (information loss).
"""
from __future__ import annotations

import pytest

from lawvm.tools.peg_audit import audit_scan_preservation, ScanAuditResult


class TestSimpleJohtolause:
    """Johtolause with no provenance — all structural tokens in view."""

    def test_simple_modify(self):
        """Simple 'muutetaan 3 §' — one PYKALA token passes through."""
        result = audit_scan_preservation("muutetaan 3 §", amendment_id="test/1")
        assert result.valid
        assert result.raw_structural_count == 1  # § = PYKALA
        assert result.structural_view_count >= 1
        assert result.unaccounted_count == 0

    def test_multi_section_modify(self):
        """Multiple sections — several PYKALA tokens pass through."""
        text = "muutetaan 3, 5 ja 7 §"
        result = audit_scan_preservation(text, amendment_id="test/2")
        assert result.valid
        assert result.raw_structural_count >= 1  # at least the § sign
        assert result.unaccounted_count == 0

    def test_chapter_and_section(self):
        """Modify with chapter reference — LUKU and PYKALA both pass through."""
        text = "muutetaan 2 luvun 5 §"
        result = audit_scan_preservation(text, amendment_id="test/3")
        assert result.valid
        assert result.raw_structural_count >= 2  # luku + §
        assert result.unaccounted_count == 0

    def test_subsection_and_item(self):
        """Modify with momentti and kohta — all pass through."""
        text = "muutetaan 10 §:n 2 momentin 3 kohta"
        result = audit_scan_preservation(text, amendment_id="test/4")
        assert result.valid
        assert result.raw_structural_count >= 3  # §, momentti, kohta
        assert result.unaccounted_count == 0

    def test_empty_text(self):
        """Empty text produces zero counts and is valid."""
        result = audit_scan_preservation("", amendment_id="test/empty")
        assert result.valid
        assert result.raw_structural_count == 0
        assert result.unaccounted_count == 0

    def test_no_structural_tokens(self):
        """Text with no structural tokens is trivially valid."""
        result = audit_scan_preservation(
            "Tämä laki tulee voimaan 1 päivänä tammikuuta 2024.",
            amendment_id="test/voimaantulo",
        )
        assert result.valid
        assert result.unaccounted_count == 0


class TestProvenanceJohtolause:
    """Johtolause with provenance — structural tokens inside provenance
    should be annotation-covered, not unaccounted."""

    def test_sellaisena_kuin_provenance(self):
        """'sellaisena kuin se on laissa 123/2020' — provenance covers citation."""
        text = "muutetaan 5 §, sellaisena kuin se on laissa 123/2020, seuraavasti:"
        result = audit_scan_preservation(text, amendment_id="test/prov")
        assert result.valid
        assert result.raw_structural_count >= 1  # at least the § in target
        assert result.unaccounted_count == 0

    def test_provenance_with_structural_inside(self):
        """Provenance clause containing a § reference — that § should be
        annotation-covered, not unaccounted."""
        text = (
            "muutetaan 10 §:n 1 momentti, sellaisena kuin se on laissa "
            "200/2019, ja 12 §:n 3 momentti seuraavasti:"
        )
        result = audit_scan_preservation(text, amendment_id="test/prov2")
        assert result.valid
        assert result.unaccounted_count == 0
        # Raw structural count should include § and momentti from both
        # the main targets and any that appear in provenance
        assert result.raw_structural_count >= 4  # 10§ mom, 12§ mom


class TestComplexJohtolause:
    """More complex johtolause patterns."""

    def test_insert_new_section(self):
        """'lisätään uusi 3a §' — PYKALA + UUSI survive."""
        text = "lisätään 4 luvun 3 §:n jälkeen uusi 3 a §"
        result = audit_scan_preservation(text, amendment_id="test/insert")
        assert result.valid
        assert result.unaccounted_count == 0

    def test_repeal(self):
        """'kumotaan 5 §' — PYKALA survives."""
        text = "kumotaan 5 §"
        result = audit_scan_preservation(text, amendment_id="test/repeal")
        assert result.valid
        assert result.raw_structural_count >= 1
        assert result.unaccounted_count == 0

    def test_liite(self):
        """Amendment to appendix — LIITE survives."""
        text = "muutetaan liitteen 1 kohta"
        result = audit_scan_preservation(text, amendment_id="test/liite")
        assert result.valid
        assert result.unaccounted_count == 0

    def test_osa(self):
        """Amendment to a part — OSA survives."""
        text = "muutetaan 1 osan 3 luvun 5 §"
        result = audit_scan_preservation(text, amendment_id="test/osa")
        assert result.valid
        assert result.raw_structural_count >= 3  # osa, luku, §
        assert result.unaccounted_count == 0


class TestResultProperties:
    """Test the ScanAuditResult dataclass properties."""

    def test_valid_when_zero_unaccounted(self):
        result = ScanAuditResult(
            amendment_id="test",
            raw_structural_count=10,
            annotation_covered_count=4,
            structural_view_count=6,
            unaccounted_count=0,
        )
        assert result.valid

    def test_invalid_when_nonzero_unaccounted(self):
        result = ScanAuditResult(
            amendment_id="test",
            raw_structural_count=10,
            annotation_covered_count=4,
            structural_view_count=5,
            unaccounted_count=1,
            unaccounted_tokens=["§"],
        )
        assert not result.valid


# ---------------------------------------------------------------------------
# Corpus smoke test — only runs when corpus is available
# ---------------------------------------------------------------------------

def _corpus_available() -> bool:
    try:
        from lawvm.finland.corpus import get_corpus
        cs = get_corpus()
        return cs.read_source("1999/731") is not None
    except Exception:
        return False


@pytest.mark.skipif(not _corpus_available(), reason="corpus not available")
class TestCorpusSmoke:
    """Run peg-audit on a known-good statute from the corpus."""

    def test_known_statute_2009_953(self):
        """2009/953 is a well-tested statute in the conformance corpus."""
        from lawvm.finland.corpus import get_corpus
        from lawvm.finland.grafter import (
            _amendment_children_by_parent,
            get_johtolause,
            OP_KEYWORDS,
        )
        from lawvm.finland.metadata import _normalize_johtolause_verbs

        sid = "2009/953"
        cs = get_corpus()
        children = _amendment_children_by_parent()
        amendment_ids = list(children.get(sid, []))
        assert amendment_ids, f"No amendments for {sid}"

        for amendment_id in amendment_ids[:5]:  # check first 5 amendments
            xml_bytes = cs.read_source(amendment_id)
            if xml_bytes is None:
                continue
            johto = get_johtolause(xml_bytes)
            if not johto or len(johto) < 10:
                continue
            johto = _normalize_johtolause_verbs(johto)
            if not any(kw in johto.lower() for kw in OP_KEYWORDS):
                continue

            result = audit_scan_preservation(johto, amendment_id=amendment_id)
            assert result.valid, (
                f"{amendment_id}: {result.unaccounted_count} unaccounted tokens: "
                f"{result.unaccounted_tokens}"
            )
