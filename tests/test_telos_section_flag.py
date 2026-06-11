"""Tests for the telos-section flag (feature #5).

Implements all 7 required test categories per AGENTS.md §15 and
TELOS_SECTION_FLAG.md §Verification regime.

Design discipline:
  - Every test is self-contained. No corpus access. No network.
  - Tests pin the classification invariant, not implementation detail.
  - CRITICAL: zero false positives. Every non-telos section must produce
    is_purpose_section=False. Any false positive is a hard failure.

Test categories (per AGENTS.md §15):
  1. Synthetic unit tests — conformance corpus cases.
  2. Findings/observation tests — BorderlineTelosCandidate emission.
  3. Negative tests — non-telos §1 headings stay unflagged.
  4. Strict-mode test — strict == non-strict (tight rule is the same).
  5. No-leak test — synthetic internal markers must not appear in output.
  6. Schema-stability test — result has expected fields; no extra required keys.
  7. False-positive guard — for each non-telos fixture, column MUST be false.

Conformance corpus (from brief §Verification regime):
  C1. §1 "Lain tarkoitus" → flagged.
  C2. §1 "Tarkoitus ja soveltamisala" → flagged.
  C3. §1 "Soveltamisala" + body starts with telos-phrasing → flagged.
  C4. §1 "Soveltamisala" + body does NOT start with telos-phrasing →
        UNFLAGGED + BorderlineTelosCandidate.
  C5. Statute without §1 (or §2+ only) → unflagged.
  C6. §1 "Määritelmät" / "Yleiset säännökset" → unflagged.
  C7. §1 "Tarkoitus" (canonical) / §2 "Tarkoitus" (split-telos) → §2 unflagged.

Additional cases:
  C8. §1 "Tavoite" → flagged.
  C9. §1 "Lain tavoite" → flagged.
  C10. §1 "Tarkoitus" with empty body → NOT flagged (body condition).
  C11. §1 "Soveltamisala ja tarkoitus" → flagged.
  C12. §1 label "1 §" (with §-suffix) → normalizes to §1 → flagged.
  C13. §3 "Lain tarkoitus" → NOT §1 → unflagged.
"""
from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.telos_section_flag import (
    BorderlineTelosCandidate,
    classify_telos_section,
)


# ---------------------------------------------------------------------------
# IR node construction helpers (mirrors test_conformance.py)
# ---------------------------------------------------------------------------


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _heading(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.HEADING, text=text)


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _num(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.NUM, text=text)


def _section_with_heading_and_body(
    label: str,
    heading_text: str,
    body_text: str,
) -> IRNode:
    """Build a minimal section with heading + one subsection containing body text."""
    heading_node = _heading(heading_text)
    subsection = _sub("1", _content(body_text))
    return _sec(label, heading_node, subsection)


def _section_with_heading_only(label: str, heading_text: str) -> IRNode:
    """Build a section with heading but no body content (empty body condition)."""
    return _sec(label, _heading(heading_text))


# ---------------------------------------------------------------------------
# Category 1: Synthetic unit tests — conformance corpus cases
# ---------------------------------------------------------------------------


class TestConformanceCorpus:
    """Category 1: Synthetic unit tests covering the full conformance corpus."""

    def test_c1_lain_tarkoitus_flagged(self) -> None:
        """C1: §1 'Lain tarkoitus' → is_purpose_section=True."""
        section = _section_with_heading_and_body(
            "1", "Lain tarkoitus", "Tämän lain tarkoituksena on edistää hyvinvointia."
        )
        result = classify_telos_section(section, "1", "2024/100")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None
        assert "hyvinvointia" in result.purpose_text_snippet
        assert result.borderline_candidate is None

    def test_c2_tarkoitus_ja_soveltamisala_flagged(self) -> None:
        """C2: §1 'Tarkoitus ja soveltamisala' → is_purpose_section=True."""
        section = _section_with_heading_and_body(
            "1",
            "Tarkoitus ja soveltamisala",
            "Tämän lain tarkoituksena on säännellä toimintaa.",
        )
        result = classify_telos_section(section, "1", "2024/200")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None
        assert result.borderline_candidate is None

    def test_c3_soveltamisala_with_telos_body_flagged(self) -> None:
        """C3: §1 'Soveltamisala' + body starts with telos-phrasing → flagged."""
        section = _section_with_heading_and_body(
            "1",
            "Soveltamisala",
            "Tämän lain tarkoituksena on säädellä yritystoimintaa.",
        )
        result = classify_telos_section(section, "1", "2024/300")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None
        assert result.borderline_candidate is None

    def test_c4_soveltamisala_without_telos_body_unflagged_with_observation(
        self,
    ) -> None:
        """C4: §1 'Soveltamisala' + body does NOT start with telos-phrasing →
        UNFLAGGED + BorderlineTelosCandidate emitted.
        """
        section = _section_with_heading_and_body(
            "1",
            "Soveltamisala",
            "Tätä lakia sovelletaan kaikkiin yrityksiin.",
        )
        result = classify_telos_section(section, "1", "2024/400")
        # Must NOT be flagged — this is the false-positive guard.
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None
        # Must emit BorderlineTelosCandidate.
        assert result.borderline_candidate is not None
        assert isinstance(result.borderline_candidate, BorderlineTelosCandidate)
        assert (
            result.borderline_candidate.rule_id
            == "TELOS.BORDERLINE_SOVELTAMISALA_NO_TELOS_BODY"
        )
        assert result.borderline_candidate.statute_id == "2024/400"
        assert result.borderline_candidate.blocking is False

    def test_c5_no_section_1_unflagged(self) -> None:
        """C5: Statute where §1 is absent (§2+ only) → unflagged."""
        section_2 = _section_with_heading_and_body(
            "2", "Tarkoitus", "Lain tarkoituksena on."
        )
        result = classify_telos_section(section_2, "2", "2024/500")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None
        assert result.borderline_candidate is None

    def test_c6_definitions_heading_unflagged(self) -> None:
        """C6: §1 'Määritelmät' → unflagged."""
        section = _section_with_heading_and_body(
            "1", "Määritelmät", "Tässä laissa tarkoitetaan seuraavaa."
        )
        result = classify_telos_section(section, "1", "2024/600")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None
        assert result.borderline_candidate is None

    def test_c6_yleiset_saannokset_unflagged(self) -> None:
        """C6: §1 'Yleiset säännökset' → unflagged."""
        section = _section_with_heading_and_body(
            "1", "Yleiset säännökset", "Tätä lakia sovelletaan yleisesti."
        )
        result = classify_telos_section(section, "1", "2024/601")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None
        assert result.borderline_candidate is None

    def test_c7_split_telos_sec2_unflagged(self) -> None:
        """C7: §2 'Tarkoitus' in a split-telos statute → §2 is unflagged.

        The tight rule only flags §1. §2 with a telos heading stays unflagged
        regardless of content.
        """
        section_2 = _section_with_heading_and_body(
            "2",
            "Tarkoitus",
            "Tämän lain tarkoituksena on edistää kestävää kehitystä.",
        )
        result = classify_telos_section(section_2, "2", "2024/700")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None
        assert result.borderline_candidate is None

    def test_c8_tavoite_flagged(self) -> None:
        """C8: §1 'Tavoite' → flagged."""
        section = _section_with_heading_and_body(
            "1", "Tavoite", "Lain tavoitteena on parantaa yhteiskunnan toimintaa."
        )
        result = classify_telos_section(section, "1", "2024/800")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None
        assert result.borderline_candidate is None

    def test_c9_lain_tavoite_flagged(self) -> None:
        """C9: §1 'Lain tavoite' → flagged."""
        section = _section_with_heading_and_body(
            "1",
            "Lain tavoite",
            "Tämän lain tavoitteena on turvata kansalaisten oikeudet.",
        )
        result = classify_telos_section(section, "1", "2024/900")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None
        assert result.borderline_candidate is None

    def test_c11_soveltamisala_ja_tarkoitus_flagged(self) -> None:
        """C11: §1 'Soveltamisala ja tarkoitus' → flagged."""
        section = _section_with_heading_and_body(
            "1",
            "Soveltamisala ja tarkoitus",
            "Tätä lakia sovelletaan kaikkiin elinkeinonharjoittajiin.",
        )
        result = classify_telos_section(section, "1", "2024/1100")
        assert result.is_purpose_section is True
        assert result.borderline_candidate is None

    def test_c12_label_with_section_symbol_flagged(self) -> None:
        """C12: label '1 §' normalizes to §1 → canonical heading → flagged."""
        section = _section_with_heading_and_body(
            "1 §", "Lain tarkoitus", "Lain tarkoituksena on."
        )
        result = classify_telos_section(section, "1 §", "2024/1200")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None

    def test_c13_section_3_lain_tarkoitus_unflagged(self) -> None:
        """C13: §3 heading 'Lain tarkoitus' → NOT §1 → unflagged."""
        section = _section_with_heading_and_body(
            "3", "Lain tarkoitus", "Tämän lain tarkoituksena on."
        )
        result = classify_telos_section(section, "3", "2024/1300")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None
        assert result.borderline_candidate is None

    def test_tarkoitus_simple_flagged(self) -> None:
        """§1 'Tarkoitus' → flagged."""
        section = _section_with_heading_and_body(
            "1", "Tarkoitus", "Tämän lain tarkoituksena on."
        )
        result = classify_telos_section(section, "1", "2024/10")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None


# ---------------------------------------------------------------------------
# Category 2: Findings / observation tests
# ---------------------------------------------------------------------------


class TestBorderlineTelosCandidateEmission:
    """Category 2: BorderlineTelosCandidate emission tests."""

    def test_soveltamisala_no_telos_body_emits_candidate(self) -> None:
        """'Soveltamisala' without telos body emits BorderlineTelosCandidate."""
        section = _section_with_heading_and_body(
            "1", "Soveltamisala", "Tätä lakia sovelletaan kaikkiin."
        )
        result = classify_telos_section(section, "1", "2024/obs1")
        assert result.borderline_candidate is not None
        candidate = result.borderline_candidate
        assert candidate.rule_id == "TELOS.BORDERLINE_SOVELTAMISALA_NO_TELOS_BODY"
        assert candidate.statute_id == "2024/obs1"
        assert candidate.section_label == "1"
        assert "Soveltamisala" in candidate.heading_text
        assert candidate.blocking is False

    def test_canonical_heading_no_candidate_emitted(self) -> None:
        """Canonical heading 'Lain tarkoitus' must NOT emit a BorderlineTelosCandidate."""
        section = _section_with_heading_and_body(
            "1", "Lain tarkoitus", "Tämän lain tarkoituksena on."
        )
        result = classify_telos_section(section, "1", "2024/obs2")
        assert result.borderline_candidate is None

    def test_definitions_heading_no_candidate_emitted(self) -> None:
        """Non-borderline heading 'Määritelmät' must NOT emit a candidate."""
        section = _section_with_heading_and_body(
            "1", "Määritelmät", "Tässä laissa tarkoitetaan."
        )
        result = classify_telos_section(section, "1", "2024/obs3")
        assert result.borderline_candidate is None

    def test_borderline_candidate_has_body_snippet(self) -> None:
        """BorderlineTelosCandidate.body_snippet contains leading body text."""
        body = "Tätä lakia sovelletaan kaikkiin julkisiin laitoksiin."
        section = _section_with_heading_and_body("1", "Soveltamisala", body)
        result = classify_telos_section(section, "1", "2024/obs4")
        assert result.borderline_candidate is not None
        assert body[:20] in result.borderline_candidate.body_snippet

    def test_borderline_candidate_reason_non_empty(self) -> None:
        """BorderlineTelosCandidate.reason must be non-empty."""
        section = _section_with_heading_and_body(
            "1", "Soveltamisala", "Tätä lakia sovelletaan."
        )
        result = classify_telos_section(section, "1", "2024/obs5")
        assert result.borderline_candidate is not None
        assert result.borderline_candidate.reason.strip() != ""


# ---------------------------------------------------------------------------
# Category 3: Negative tests
# ---------------------------------------------------------------------------


class TestNegativeCases:
    """Category 3: Negative tests — nearby valid shapes that must NOT fire."""

    def test_no_heading_section_1_unflagged(self) -> None:
        """§1 with no heading child → unflagged (no heading match possible)."""
        section = _sec("1", _sub("1", _content("Lain tarkoituksena on toimia.")))
        result = classify_telos_section(section, "1", "2024/neg1")
        assert result.is_purpose_section is False
        assert result.borderline_candidate is None

    def test_siirtymassaantos_unflagged(self) -> None:
        """§1 'Siirtymäsäännökset' → unflagged."""
        section = _section_with_heading_and_body(
            "1", "Siirtymäsäännökset", "Ennen lain voimaantuloa."
        )
        result = classify_telos_section(section, "1", "2024/neg2")
        assert result.is_purpose_section is False
        assert result.borderline_candidate is None

    def test_voimaantulo_unflagged(self) -> None:
        """§1 'Voimaantulo' → unflagged."""
        section = _section_with_heading_and_body(
            "1", "Voimaantulo", "Tämä laki tulee voimaan 1.1.2025."
        )
        result = classify_telos_section(section, "1", "2024/neg3")
        assert result.is_purpose_section is False
        assert result.borderline_candidate is None

    def test_soveltamisala_with_unrelated_body_unflagged(self) -> None:
        """'Soveltamisala' + body mentioning 'tarkoitus' mid-sentence → unflagged.

        The body-phrasing check requires the text to START with the canonical
        prefix. A mid-sentence mention must not trigger the flag.
        """
        section = _section_with_heading_and_body(
            "1",
            "Soveltamisala",
            "Tätä lakia sovelletaan, jollei muun lain tarkoituksena ole muuta.",
        )
        result = classify_telos_section(section, "1", "2024/neg4")
        assert result.is_purpose_section is False
        # This is a borderline candidate, not a flagged section.
        assert result.borderline_candidate is not None

    def test_section_label_none_unflagged(self) -> None:
        """label=None → cannot be §1 → unflagged."""
        section = _section_with_heading_and_body(
            "", "Lain tarkoitus", "Tarkoituksena on toimia."
        )
        result = classify_telos_section(section, None, "2024/neg5")
        assert result.is_purpose_section is False
        assert result.borderline_candidate is None

    def test_empty_label_unflagged(self) -> None:
        """label='' → cannot be §1 → unflagged."""
        section = _section_with_heading_and_body(
            "", "Tarkoitus", "Tarkoituksena on toimia."
        )
        result = classify_telos_section(section, "", "2024/neg6")
        assert result.is_purpose_section is False


# ---------------------------------------------------------------------------
# Category 4: Strict-mode tests
# ---------------------------------------------------------------------------


class TestStrictMode:
    """Category 4: Strict-mode behavior is identical to non-strict.

    The tight rule does not change under strict mode. Both modes use exactly
    the same classification logic.
    """

    def test_strict_mode_identical_to_non_strict_flagged(self) -> None:
        """Canonical telos section: strict and non-strict produce same result."""
        section = _section_with_heading_and_body(
            "1", "Lain tarkoitus", "Tämän lain tarkoituksena on."
        )
        result_normal = classify_telos_section(section, "1", "2024/sm1")
        # The telos classifier has no strict/quirks distinction.
        # Calling it twice must produce the same result.
        result_again = classify_telos_section(section, "1", "2024/sm1")
        assert result_normal.is_purpose_section == result_again.is_purpose_section
        assert result_normal.purpose_text_snippet == result_again.purpose_text_snippet
        assert (
            result_normal.borderline_candidate is None
        ) == (result_again.borderline_candidate is None)

    def test_strict_mode_identical_to_non_strict_borderline(self) -> None:
        """Borderline case: both calls agree and emit BorderlineTelosCandidate."""
        section = _section_with_heading_and_body(
            "1", "Soveltamisala", "Tätä lakia sovelletaan."
        )
        r1 = classify_telos_section(section, "1", "2024/sm2")
        r2 = classify_telos_section(section, "1", "2024/sm2")
        assert r1.is_purpose_section is False
        assert r2.is_purpose_section is False
        assert r1.borderline_candidate is not None
        assert r2.borderline_candidate is not None


# ---------------------------------------------------------------------------
# Category 5: No-leak tests
# ---------------------------------------------------------------------------


class TestNoLeak:
    """Category 5: Internal synthetic labels must not leak into output.

    The telos extractor must not produce rule IDs, snippets, or labels that
    contain internal sentinel prefixes (e.g. '__ord_').
    """

    def test_no_synthetic_label_prefix_in_rule_id(self) -> None:
        """BorderlineTelosCandidate.rule_id must not contain '__ord_' prefix."""
        section = _section_with_heading_and_body(
            "1", "Soveltamisala", "Tätä lakia sovelletaan."
        )
        result = classify_telos_section(section, "1", "2024/leak1")
        if result.borderline_candidate is not None:
            assert "__ord_" not in result.borderline_candidate.rule_id

    def test_no_synthetic_label_prefix_in_snippet(self) -> None:
        """purpose_text_snippet must not contain '__ord_' prefix."""
        section = _section_with_heading_and_body(
            "1", "Lain tarkoitus", "Lain tarkoituksena on toimia oikeudenmukaisesti."
        )
        result = classify_telos_section(section, "1", "2024/leak2")
        if result.purpose_text_snippet is not None:
            assert "__ord_" not in result.purpose_text_snippet

    def test_result_fields_are_correct_types(self) -> None:
        """TelosExtractionResult fields have correct types (no leaking dicts/sentinels)."""
        section = _section_with_heading_and_body(
            "1", "Lain tarkoitus", "Tarkoituksena on."
        )
        result = classify_telos_section(section, "1", "2024/leak3")
        assert isinstance(result.is_purpose_section, bool)
        if result.purpose_text_snippet is not None:
            assert isinstance(result.purpose_text_snippet, str)
        if result.borderline_candidate is not None:
            assert isinstance(result.borderline_candidate, BorderlineTelosCandidate)


# ---------------------------------------------------------------------------
# Category 6: Schema-stability tests
# ---------------------------------------------------------------------------


class TestSchemaStability:
    """Category 6: Schema-stability — output shape is consistent across inputs."""

    def test_flagged_result_has_expected_fields(self) -> None:
        """Flagged result has is_purpose_section, purpose_text_snippet, borderline_candidate."""
        section = _section_with_heading_and_body(
            "1", "Tarkoitus", "Tämän lain tarkoituksena on."
        )
        result = classify_telos_section(section, "1", "2024/schema1")
        # These field names must be stable (per brief's Parquet schema).
        assert hasattr(result, "is_purpose_section")
        assert hasattr(result, "purpose_text_snippet")
        assert hasattr(result, "borderline_candidate")

    def test_unflagged_result_has_expected_fields(self) -> None:
        """Unflagged result also has the same three fields."""
        section = _section_with_heading_and_body(
            "2", "Tarkoitus", "Tämän lain tarkoituksena on."
        )
        result = classify_telos_section(section, "2", "2024/schema2")
        assert hasattr(result, "is_purpose_section")
        assert hasattr(result, "purpose_text_snippet")
        assert hasattr(result, "borderline_candidate")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None

    def test_borderline_candidate_has_expected_fields(self) -> None:
        """BorderlineTelosCandidate has the documented fields."""
        section = _section_with_heading_and_body(
            "1", "Soveltamisala", "Tätä lakia sovelletaan."
        )
        result = classify_telos_section(section, "1", "2024/schema3")
        assert result.borderline_candidate is not None
        candidate = result.borderline_candidate
        # Field existence check (schema stability).
        assert hasattr(candidate, "rule_id")
        assert hasattr(candidate, "statute_id")
        assert hasattr(candidate, "section_label")
        assert hasattr(candidate, "heading_text")
        assert hasattr(candidate, "body_snippet")
        assert hasattr(candidate, "reason")
        assert hasattr(candidate, "blocking")

    def test_snippet_max_300_chars(self) -> None:
        """purpose_text_snippet is at most 300 characters when flagged."""
        long_body = "Tämän lain tarkoituksena on. " * 30  # >300 chars
        section = _section_with_heading_and_body("1", "Lain tarkoitus", long_body)
        result = classify_telos_section(section, "1", "2024/schema4")
        assert result.is_purpose_section is True
        assert result.purpose_text_snippet is not None
        assert len(result.purpose_text_snippet) <= 300

    def test_snippet_is_none_when_not_flagged(self) -> None:
        """purpose_text_snippet is None when is_purpose_section is False."""
        section = _section_with_heading_and_body(
            "1", "Määritelmät", "Tässä laissa tarkoitetaan."
        )
        result = classify_telos_section(section, "1", "2024/schema5")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None

    def test_borderline_candidate_is_frozen(self) -> None:
        """BorderlineTelosCandidate is frozen (immutable)."""
        section = _section_with_heading_and_body(
            "1", "Soveltamisala", "Tätä lakia sovelletaan."
        )
        result = classify_telos_section(section, "1", "2024/schema6")
        assert result.borderline_candidate is not None
        with pytest.raises((AttributeError, TypeError)):
            cast(Any, result.borderline_candidate).rule_id = "mutated"

    def test_result_is_frozen(self) -> None:
        """TelosExtractionResult is frozen (immutable)."""
        section = _section_with_heading_and_body(
            "1", "Tarkoitus", "Tämän lain tarkoituksena on."
        )
        result = classify_telos_section(section, "1", "2024/schema7")
        with pytest.raises((AttributeError, TypeError)):
            cast(Any, result).is_purpose_section = False


# ---------------------------------------------------------------------------
# Category 7: False-positive guard
# ---------------------------------------------------------------------------


class TestFalsePositiveGuard:
    """Category 7: Zero false positives.

    For every non-telos conformance corpus case, is_purpose_section MUST be
    False. Any true value here is a hard failure.

    This is the critical gate: a false positive means the extractor is
    silently labeling a non-purpose section as a purpose section.
    """

    _NON_TELOS_CASES: list[tuple[str, str, str]] = [
        # (label, heading, body)
        ("1", "Määritelmät", "Tässä laissa tarkoitetaan seuraavia termejä."),
        ("1", "Yleiset säännökset", "Nämä säännökset koskevat kaikkia toimijoita."),
        ("1", "Siirtymäsäännökset", "Ennen lain voimaantuloa tehdyt päätökset."),
        ("1", "Voimaantulo", "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."),
        ("1", "Soveltamisala", "Tätä lakia sovelletaan kaikkiin yrityksiin Suomessa."),
        ("2", "Lain tarkoitus", "Tämän lain tarkoituksena on."),  # §2 not §1
        ("2", "Tarkoitus", "Tarkoituksena on edistää hyvinvointia."),  # §2 not §1
        ("3", "Tavoite", "Tavoitteena on parantaa toimintaa."),  # §3 not §1
        ("1", "Muut säännökset", "Muista asioista säädetään erikseen."),
        ("1", "Soveltamisalan rajoitukset", "Tätä lakia ei sovelleta."),
    ]

    @pytest.mark.parametrize(
        "label,heading,body,idx",
        [
            (label, heading, body, idx)
            for idx, (label, heading, body) in enumerate(_NON_TELOS_CASES)
        ],
    )
    def test_non_telos_section_not_flagged(
        self, label: str, heading: str, body: str, idx: int
    ) -> None:
        """CRITICAL: is_purpose_section MUST be False for all non-telos cases."""
        section = _section_with_heading_and_body(label, heading, body)
        result = classify_telos_section(section, label, f"2024/fp{idx}")
        assert result.is_purpose_section is False, (
            f"FALSE POSITIVE: section §{label} with heading '{heading}' was incorrectly "
            f"flagged as a purpose section. This is a hard failure."
        )
        assert result.purpose_text_snippet is None, (
            f"FALSE POSITIVE: purpose_text_snippet should be None for non-telos section "
            f"§{label} with heading '{heading}'."
        )

    def test_empty_section_not_flagged(self) -> None:
        """Empty section (no heading, no body) → unflagged."""
        section = _sec("1")
        result = classify_telos_section(section, "1", "2024/fp_empty")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None

    def test_section_with_canonical_heading_but_empty_body_not_flagged(
        self,
    ) -> None:
        """CRITICAL: §1 'Lain tarkoitus' with empty body → NOT flagged.

        Body condition (condition 3) must be enforced even for canonical headings.
        """
        section = _section_with_heading_only("1", "Lain tarkoitus")
        result = classify_telos_section(section, "1", "2024/fp_empty_body")
        assert result.is_purpose_section is False
        assert result.purpose_text_snippet is None

    def test_canonical_heading_wrong_section_number_not_flagged(self) -> None:
        """'Tarkoitus' at §5 → NOT flagged (not §1)."""
        section = _section_with_heading_and_body(
            "5", "Tarkoitus", "Tämän lain tarkoituksena on."
        )
        result = classify_telos_section(section, "5", "2024/fp_wrong_num")
        assert result.is_purpose_section is False

    def test_all_canonical_headings_require_section_1(self) -> None:
        """All canonical headings only flag when on §1, not §2-§9."""
        canonical_headings = [
            "Lain tarkoitus",
            "Tarkoitus",
            "Tarkoitus ja soveltamisala",
            "Soveltamisala ja tarkoitus",
            "Lain tavoite",
            "Tavoite",
        ]
        body = "Tämän lain tarkoituksena on edistää hyvinvointia."
        for heading in canonical_headings:
            for bad_label in ["2", "3", "10"]:
                section = _section_with_heading_and_body(bad_label, heading, body)
                result = classify_telos_section(
                    section, bad_label, f"2024/fp_canonical_{bad_label}"
                )
                assert result.is_purpose_section is False, (
                    f"FALSE POSITIVE: canonical heading '{heading}' at §{bad_label} "
                    f"was incorrectly flagged."
                )
