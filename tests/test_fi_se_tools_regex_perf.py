"""Performance regression tests for FI/SE/tools regex landmines.

Actuator batch 2 (2026-05-29): bounded-regex + fast-guard fixes for
Sensor H findings #12–15.

Covered sites:
  #12  citation_routing._looks_like_fi_meta_repeal  (also grafter.py:6441)
  #13  clause_patterns._MIXED_ROW_PATTERNS / _SINGLE_ROW_{REPLACE,REPEAL}_RE
  #14  sweden/grafter._SE_{REPLACE,REPEAL,RENUMBER}_CLAUSE_RE, _SE_WORD_SUBSTITUTION_RE
  #15  divergence_heuristics._REPEAL_PRIOR_WORDING_BANNER_RE / _FUTURE_REPEAL_OVERLAY_RE

Template: f2ee4479 (Actuator 8 — UK referent-qualified substitution classifier).

Each fixture tests:
  1. Positive: a known-matching input returns the expected truthy result.
  2. Negative: short obviously-non-matching input returns empty/False quickly.
  3. Adversarial: a long string (~10 KB) that would have caused catastrophic
     backtracking on the old unbounded pattern returns False/empty AND
     completes in < 100 ms.
"""
from __future__ import annotations

import time

_CEILING_MS = 100  # generous per-call ceiling (old code: >1 s on adversarial)


# ---------------------------------------------------------------------------
# Site #12 — FI citation_routing._looks_like_fi_meta_repeal
# ---------------------------------------------------------------------------

from lawvm.finland.citation_routing import _looks_like_fi_meta_repeal


def test_fi_meta_repeal_positive_matches() -> None:
    text = (
        "Tällä lailla kumotaan eräiden lakien muuttamisesta "
        "annetun lain ( 123/2010 ) 3 §"
    )
    assert _looks_like_fi_meta_repeal(text) is True


def test_fi_meta_repeal_no_muuttamisesta_returns_false() -> None:
    assert _looks_like_fi_meta_repeal("kumotaan jotain annetun lain (123/2010)") is False


def test_fi_meta_repeal_no_annetun_returns_false() -> None:
    assert _looks_like_fi_meta_repeal("kumotaan muuttamisesta 123") is False


def test_fi_meta_repeal_empty_returns_false() -> None:
    assert _looks_like_fi_meta_repeal("") is False


def test_fi_meta_repeal_adversarial_long_no_annetun_is_fast() -> None:
    """Long text with 'muuttamisesta' and 'kumotaan' but no 'annetun'.

    Old pattern: two unbounded .* with DOTALL → O(N^2) backtracking.
    New: 'annetun' guard fires before regex; must complete in < 100 ms.
    """
    text = (
        "kumotaan " + "x" * 5000 + " muuttamisesta " + "y" * 5000
        + " lain ( 99/2010 ) 1 §"
    )
    assert "annetun" not in text.lower()
    t0 = time.perf_counter()
    result = _looks_like_fi_meta_repeal(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-annetun took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "guard regression suspected"
    )


def test_fi_meta_repeal_adversarial_all_guards_but_no_digit_is_fast() -> None:
    """Text passes both guards and 'kumotaan' is present, but no digit after '('.

    The bounded regex must fail fast instead of backtracking across 10 KB.
    """
    text = (
        "kumotaan " + "a" * 800 + " muuttamisesta " + "b" * 400
        + " annetun lain ( X"  # no digit after '('
    )
    t0 = time.perf_counter()
    result = _looks_like_fi_meta_repeal(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial all-guards no-digit took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); bounded regex regression suspected"
    )


# ---------------------------------------------------------------------------
# Site #13 — FI clause_patterns
# ---------------------------------------------------------------------------

from lawvm.finland.johtolause.clause_patterns import (
    parse_named_table_row_mixed_clauses,
    parse_named_table_row_single_clauses,
)


def test_clause_patterns_mixed_kohdat_positive() -> None:
    johto = (
        "kumotaan käräjäoikeuksien kanslioiden ja istuntopaikkojen sijainnista annetun "
        "päätöksen 1 §:n Iitin ja Juvan käräjäoikeuksia koskevat kohdat sekä muutetaan "
        "Kouvolan ja Mikkelin käräjäoikeuksia koskevat kohdat seuraavasti:"
    )
    result = parse_named_table_row_mixed_clauses(johto)
    assert len(result) >= 1


def test_clause_patterns_mixed_no_käräjäoikeu_returns_empty() -> None:
    assert parse_named_table_row_mixed_clauses("muutetaan lain 1 §:n seuraavasti:") == []


def test_clause_patterns_mixed_adversarial_long_no_terminal_is_fast() -> None:
    """Long text with 'käräjäoikeu' and 'muut' but no terminal anchor.

    Old: unbounded .+? → O(N^2) on non-matching input.
    New: bounded {1,200}? caps scan depth.
    """
    # Passes the module-level guard ("käräjäoikeu" and "muut" both present)
    # but has no matching section number before the names, so patterns fail.
    text = (
        "käräjäoikeu " + "muut " + "x" * 5000
        + " käräjäoikeuksia koskevat kohdat"
        + " y" * 5000
    )
    t0 = time.perf_counter()
    result = parse_named_table_row_mixed_clauses(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result == []
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial mixed-clauses took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex regression suspected"
    )


def test_clause_patterns_single_replace_positive() -> None:
    johto = "muutetaan päätöksen 1 §:n Iisalmen käräjäoikeutta koskevat kohdat seuraavasti:"
    result = parse_named_table_row_single_clauses(johto)
    assert len(result) >= 1


def test_clause_patterns_single_no_käräjäoikeu_returns_empty() -> None:
    assert parse_named_table_row_single_clauses("muutetaan lain 5 §") == []


def test_clause_patterns_single_adversarial_long_is_fast() -> None:
    """Long text passing the 'käräjäoikeu' guard but with no terminal kohd* anchor."""
    text = (
        "muutetaan käräjäoikeuksia koskeva 1 §:n "
        + "Jyväskylän käräjäoikeutta " * 200
        + " muu teksti ilman terminaattoria"
    )
    t0 = time.perf_counter()
    result = parse_named_table_row_single_clauses(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # May or may not match, but must be fast
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial single-clauses took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex regression suspected"
    )


# ---------------------------------------------------------------------------
# Site #14 — SE grafter clause extractors
# ---------------------------------------------------------------------------

from lawvm.sweden.grafter import (
    _extract_replace_section_labels_from_clause,
    _extract_repealed_section_labels_from_clause,
    _extract_section_renumber_pairs_from_clause,
    _section_renumber_arity_mismatch_diagnostics,
    _extract_se_official_word_substitution_pair,
)


def test_se_replace_labels_positive() -> None:
    clause = "dels att 2 § ska ha följande lydelse"
    result = _extract_replace_section_labels_from_clause(clause)
    assert result == ("2",)


def test_se_replace_labels_no_terminal_returns_empty() -> None:
    assert _extract_replace_section_labels_from_clause("dels att 2 § ska upphöra") == ()


def test_se_replace_labels_adversarial_long_no_terminal_is_fast() -> None:
    """Long text without 'följande lydelse' — guard fires immediately."""
    text = "dels att " + "x" * 9000 + " ska ha" + " y" * 500
    assert "följande lydelse" not in text.lower()
    t0 = time.perf_counter()
    result = _extract_replace_section_labels_from_clause(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result == ()
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial replace-labels took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "guard regression suspected"
    )


def test_se_replace_labels_adversarial_has_terminal_but_no_section_is_fast() -> None:
    """Text with 'följande lydelse' but ~500-char gap exceeds bound."""
    text = "dels att " + "x" * 500 + " ska ha följande lydelse"
    t0 = time.perf_counter()
    result = _extract_replace_section_labels_from_clause(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Result may be empty (no §) but must be fast
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial replace-with-terminal took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); bounded regex regression suspected"
    )


def test_se_repeal_labels_positive() -> None:
    clause = "dels att 16 och 22 §§ ska upphöra att gälla"
    result = _extract_repealed_section_labels_from_clause(clause)
    assert "16" in result
    assert "22" in result


def test_se_repeal_labels_no_terminal_returns_empty() -> None:
    assert _extract_repealed_section_labels_from_clause("dels att 3 § ska ha följande lydelse") == ()


def test_se_repeal_labels_adversarial_long_no_terminal_is_fast() -> None:
    text = "dels att " + "x" * 9000 + " ska ha"
    assert "upphöra att gälla" not in text.lower()
    t0 = time.perf_counter()
    result = _extract_repealed_section_labels_from_clause(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result == ()
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial repeal-labels took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "guard regression suspected"
    )


def test_se_renumber_pairs_positive() -> None:
    clause = "nuvarande 17 a och 17 b §§ ska betecknas 16 och 17 §§"
    result = _extract_section_renumber_pairs_from_clause(clause)
    assert len(result) >= 1


def test_se_renumber_pairs_no_betecknas_returns_empty() -> None:
    assert _extract_section_renumber_pairs_from_clause("dels att 2 § ska ha följande lydelse") == ()


def test_se_renumber_pairs_adversarial_long_no_betecknas_is_fast() -> None:
    text = "nuvarande " + "x" * 9000 + " §§"
    assert "betecknas" not in text.lower()
    t0 = time.perf_counter()
    result = _extract_section_renumber_pairs_from_clause(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result == ()
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial renumber-pairs took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "guard regression suspected"
    )


def test_se_renumber_arity_mismatch_positive() -> None:
    # Three sources, two destinations → arity mismatch
    clause = "nuvarande 1, 2 och 3 §§ ska betecknas 4 och 5 §§"
    result = _section_renumber_arity_mismatch_diagnostics(clause, "sfs:2024:123")
    assert len(result) >= 1


def test_se_renumber_arity_mismatch_no_betecknas_returns_empty() -> None:
    assert _section_renumber_arity_mismatch_diagnostics("inga paragrafer", "sfs:2024:1") == ()


def test_se_renumber_arity_mismatch_adversarial_long_no_betecknas_is_fast() -> None:
    text = "nuvarande " + "x" * 9000 + " §§ ska ha"
    assert "betecknas" not in text.lower()
    t0 = time.perf_counter()
    result = _section_renumber_arity_mismatch_diagnostics(text, "sfs:2024:1")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result == ()
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial renumber-arity took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "guard regression suspected"
    )


def test_se_word_substitution_positive() -> None:
    clause = 'ordet "transport" bytas ut mot "fordon"'
    result = _extract_se_official_word_substitution_pair(clause)
    assert result == ("transport", "fordon")


def test_se_word_substitution_no_keyword_returns_none() -> None:
    assert _extract_se_official_word_substitution_pair("inga ändringar") is None


def test_se_word_substitution_adversarial_long_no_terminal_is_fast() -> None:
    """Long text with 'ordet' but no 'bytas ut mot'/'ersättas med'.

    Old: .*? + .* with DOTALL → O(N^2).  New: bounded {0,400}?.
    """
    text = "ordet " + "x" * 9000 + " ska ändras"
    t0 = time.perf_counter()
    result = _extract_se_official_word_substitution_pair(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial word-substitution took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex regression suspected"
    )


# ---------------------------------------------------------------------------
# Site #15 — tools/divergence_heuristics
# ---------------------------------------------------------------------------

from lawvm.tools.divergence_heuristics import (
    oracle_has_repeal_banner_with_prior_wording,
    oracle_has_future_repeal_overlay,
)


def test_repeal_banner_positive() -> None:
    oracle = "5 § on kumottu lailla 123/2020. Aiempi sanamuoto kuuluu:"
    assert oracle_has_repeal_banner_with_prior_wording(oracle) is True


def test_repeal_banner_no_aiempi_sanamuoto_returns_false() -> None:
    assert oracle_has_repeal_banner_with_prior_wording("on kumottu lailla 123/2020") is False


def test_repeal_banner_empty_returns_false() -> None:
    assert oracle_has_repeal_banner_with_prior_wording("") is False


def test_repeal_banner_adversarial_long_no_aiempi_is_fast() -> None:
    """Long text with 'on kumottu' but no 'aiempi sanamuoto'.

    Old: .*? with DOTALL → O(N^2).  New: guard fires before regex.
    """
    text = "on kumottu lailla " + "x" * 9000 + " ei mitään"
    assert "aiempi sanamuoto" not in text.lower()
    t0 = time.perf_counter()
    result = oracle_has_repeal_banner_with_prior_wording(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial repeal-banner took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "guard regression suspected"
    )


def test_repeal_banner_adversarial_has_guard_but_gap_exceeds_bound_is_fast() -> None:
    """Text passes guard but 600-char gap between anchors exceeds .{0,500}? bound."""
    text = "on kumottu " + "x" * 600 + " aiempi sanamuoto kuuluu:"
    t0 = time.perf_counter()
    result = oracle_has_repeal_banner_with_prior_wording(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial repeal-banner gap-exceeds-bound took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); bounded regex regression suspected"
    )


def test_future_repeal_overlay_positive() -> None:
    oracle = (
        "11 § on kumottu lailla 456/2021, joka tulee voimaan 1.1.2022. "
        "Aiempi sanamuoto kuuluu:"
    )
    assert oracle_has_future_repeal_overlay(oracle) is True


def test_future_repeal_overlay_no_tulee_voimaan_returns_false() -> None:
    oracle = "11 § on kumottu lailla 456/2021. Aiempi sanamuoto kuuluu:"
    assert oracle_has_future_repeal_overlay(oracle) is False


def test_future_repeal_overlay_no_aiempi_returns_false() -> None:
    oracle = "on kumottu joka tulee voimaan 1.1.2022"
    assert oracle_has_future_repeal_overlay(oracle) is False


def test_future_repeal_overlay_adversarial_long_no_aiempi_is_fast() -> None:
    """Long text passing 'tulee voimaan' guard but no 'aiempi sanamuoto'."""
    text = "on kumottu " + "x" * 5000 + " joka tulee voimaan 1.1.2025 " + "y" * 5000
    assert "aiempi sanamuoto" not in text.lower()
    t0 = time.perf_counter()
    result = oracle_has_future_repeal_overlay(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial future-repeal-overlay took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); guard regression suspected"
    )


def test_future_repeal_overlay_adversarial_gap_exceeds_bound_is_fast() -> None:
    """Text passes all guards but 600-char gap between first two anchors exceeds .{0,500}?."""
    text = (
        "on kumottu " + "x" * 600
        + " joka tulee voimaan 1.1.2025. aiempi sanamuoto kuuluu:"
    )
    t0 = time.perf_counter()
    result = oracle_has_future_repeal_overlay(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial future-repeal gap-exceeds-bound took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); bounded regex regression suspected"
    )
