"""Performance regression tests for FI/SE/tools regex landmines.

Bounded-regex + fast-guard fixes (2026-05-29) for
backtracking-risk findings #12–15.

Covered sites:
  #12  citation_routing._looks_like_fi_meta_repeal  (also grafter.py:6441)
  #13  clause_patterns._MIXED_ROW_PATTERNS / _SINGLE_ROW_{REPLACE,REPEAL}_RE
  #14  sweden/grafter._SE_{REPLACE,REPEAL,RENUMBER}_CLAUSE_RE, _SE_WORD_SUBSTITUTION_RE
  #15  divergence_heuristics._REPEAL_PRIOR_WORDING_BANNER_RE / _FUTURE_REPEAL_OVERLAY_RE

Template: f2ee4479 (UK referent-qualified substitution classifier).

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


# ---------------------------------------------------------------------------
# Site #16 — FI editorial_hygiene._KUMOTTU_STUBS_RE (rebuild-indexes stall)
#
# `lawvm rebuild-indexes` stalled for >30 min on a single tail statute
# (2008/834): a ~1 MB table/attachment-heavy consolidated body with thousands
# of "digit + huge indentation run" sequences but ZERO "kumottu" occurrences.
# The unbounded \s* / \s+ in the optional-prefix vs. required structural-group
# alternation partitioned each whitespace run exponentially → catastrophic
# backtracking. Fix: bounded whitespace quantifiers ({0,4}/{1,4}) + literal
# "kumottu" fast-guard in normalize_kumottu_stubs.
# ---------------------------------------------------------------------------

from lawvm.finland.oracle_comparison import (
    _KUMOTTU_STUBS_RE,
    normalize_kumottu_stubs,
)


def test_kumottu_stub_positive_still_stripped() -> None:
    """Canonical kumottu stub sentences must still be removed (output preserved)."""
    cases = {
        "2 kohta on kumottu A:lla 25.11.2021/1030.": "",
        "2 momentti on kumottu A:lla 25.11.2021/1030.": "",
        "5 § on kumottu L:lla 1.4.2022/261, joka tuli voimaan 1.5.2022.": "",
        "3 luku on kumottu L:lla 123/2020.": "",
        # range form leaves the leading "N–"/"N-" residual prefix (pre-existing
        # behaviour — pinned here so the bounded rewrite cannot drift it):
        "1–2 kohta on kumottu A:lla 25.11.2021/1030.": "1–",
        "1-2 kohta on kumottu A:lla 25.11.2021/1030.": "1-",
        # NBSP-separated form (etree/Finlex residue):
        "2 kohta on kumottu A:lla 25.11.2021/1030.": "",
    }
    for src, expected in cases.items():
        assert normalize_kumottu_stubs(src) == expected, (
            f"kumottu stub stripping changed for {src!r}: "
            f"got {normalize_kumottu_stubs(src)!r}, expected {expected!r}"
        )


def test_kumottu_stub_no_kumottu_guard_returns_text_unchanged() -> None:
    """Text without the literal 'kumottu' is returned untouched (fast-guard)."""
    text = "1 § Tämä on tavallinen pykälä ilman kumoamista. 2 § Toinen pykälä."
    assert "kumottu" not in text
    assert normalize_kumottu_stubs(text) == text


def test_kumottu_stub_adversarial_large_whitespace_no_kumottu_is_fast() -> None:
    """Reproduces the 2008/834 stall feature: many digit tokens separated by
    huge indentation runs, ZERO 'kumottu'.

    Old unbounded \\s*/\\s+ alternation: >30 min on the real ~1 MB statute via
    rebuild-indexes. New bounded rule + literal guard: must complete fast.
    """
    # ~1 MB of "<num>\n<big whitespace>" — the exact digit/whitespace shape that
    # fed the optional-prefix/required-group backtracking explosion.
    block = "892\n" + (" " * 400 + "\n") * 6
    text = block * 2000
    assert "kumottu" not in text
    assert len(text) > 1_000_000

    # Direct rule (no guard) must still be bounded thanks to the bounded quantifiers.
    t0 = time.perf_counter()
    out_rule = _KUMOTTU_STUBS_RE.sub("", text)
    elapsed_rule_ms = (time.perf_counter() - t0) * 1000
    assert out_rule == text  # nothing to strip
    assert elapsed_rule_ms < 5000, (
        f"bounded _KUMOTTU_STUBS_RE took {elapsed_rule_ms:.1f} ms on 1 MB "
        f"no-kumottu text; catastrophic backtracking regression suspected"
    )

    # The guarded public path must be effectively instant.
    t0 = time.perf_counter()
    out_guarded = normalize_kumottu_stubs(text)
    elapsed_guard_ms = (time.perf_counter() - t0) * 1000
    assert out_guarded == text
    assert elapsed_guard_ms < _CEILING_MS, (
        f"guarded normalize_kumottu_stubs took {elapsed_guard_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); 'kumottu' fast-guard regression suspected"
    )


# ---------------------------------------------------------------------------
# Site #17 — FI metadata._CROSS_LAW_DESC_PAT / _SCOPED_COMMENCEMENT_RE
#
# _CROSS_LAW_DESC_PAT: lazy ``.{0,400}?`` before a literal citation ``(`` —
#   rewritten to a tempered-possessive fill that cannot backtrack across the
#   bounded window.  Match semantics identical (load-bearing 400-char cap).
# _SCOPED_COMMENCEMENT_RE: unbounded lazy gap ``(.+?)`` before a distant
#   ``tule…kuitenkin voimaan`` anchor expanded to end-of-text from every
#   subject word on non-matching input (~23 s on 112 KB).  Fix: bound the gap
#   to 2000 chars (corpus max is 633) + literal ``kuitenkin``/``voimaan``
#   pre-guard at the call sites.
# ---------------------------------------------------------------------------

import re as _re

from lawvm.core.regex_safety import PrefilteredPattern
from lawvm.finland.metadata import (
    _CROSS_LAW_DESC_PAT,
    _SCOPED_COMMENCEMENT_RE,
    _scoped_commencement_guard,
)

# Reference: the pre-hardening patterns, kept here so the hardened forms are
# proven equivalent rather than merely asserted.
_OLD_CROSS_LAW_DESC_PAT = _re.compile(
    r'(?:§:[nä]|§:ss[aä]).{0,400}?\(\s*(\d{3,4}/\d{4})\s*\)',
    _re.DOTALL,
)
_OLD_SCOPED_COMMENCEMENT_RE = _re.compile(
    r"(?:Tämän\s+lain|Lain|Asetuksen|Päätöksen|Sen)\s+(.+?)\s+"
    r"tule(?:vat|e)\s+kuitenkin\s+voimaan(?:\s+(?:jo|vasta))?\s+"
    r"(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})",
    _re.IGNORECASE,
)


def _assert_same_match(
    old: "_re.Pattern[str] | PrefilteredPattern",
    new: "_re.Pattern[str] | PrefilteredPattern",
    text: str,
) -> None:
    mo = old.search(text)
    mn = new.search(text)
    assert (mo is None) == (mn is None), f"presence diverged on {text!r}"
    if mo is not None and mn is not None:
        assert mo.span() == mn.span() and mo.groups() == mn.groups(), (
            f"span/groups diverged on {text!r}: {mo.span()},{mo.groups()} "
            f"vs {mn.span()},{mn.groups()}"
        )


def test_cross_law_desc_pat_matches_reference_on_canonical_inputs() -> None:
    for text in (
        "muutetaan valmiuslain 106 §:n 1 momentissa ja 107 §:ssä säädettyjen "
        "toimivaltuuksien käyttöönotosta annetun valtioneuvoston asetuksen "
        "(186/2021) 2 ja 3 § seuraavasti:",
        "§:n alusel (123/2020)",
        "§:ssä säädetty (456/2019)",
        "§:ssä (2021/999)",
        "§ 3 ei ole",  # no colon-form → no match
        "§:n " + "(" * 50 + " (999/2020)",  # many '(' before the citation
        "§:n " + "x" * 500 + " (999/2020)",  # citation beyond the 400 window
    ):
        _assert_same_match(_OLD_CROSS_LAW_DESC_PAT, _CROSS_LAW_DESC_PAT, text)


def test_cross_law_desc_pat_adversarial_paren_run_is_fast() -> None:
    """Many '§:n' prefixes each with a 400-char window full of '(' but no
    trailing citation.  The tempered-possessive fill must stay linear."""
    text = ("§:n " + "(" * 400) * 4000
    assert _CROSS_LAW_DESC_PAT.search(text) is None
    t0 = time.perf_counter()
    _CROSS_LAW_DESC_PAT.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < _CEILING_MS, (
        f"cross-law-desc adversarial took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); anti-backtracking regression suspected"
    )


def test_scoped_commencement_matches_reference_on_canonical_inputs() -> None:
    for text in (
        "Tämän lain 21 a ja 21 b § ja 21 c §:n 1–3 momentti tulevat kuitenkin "
        "voimaan jo 1 päivänä tammikuuta 2022.",
        "Sen 4 ja 5§ tulevat kuitenkin voimaan 1 päivänä tammikuuta 1988.",
        "Lain 2 § tulee kuitenkin voimaan vasta 1 päivänä heinäkuuta 2020.",
        "Tämä laki tulee voimaan heti.",  # no 'kuitenkin' → no match
        # gap near (but under) the 2000-char bound stays identical:
        "Lain " + "a " * 800 + "§ tulee kuitenkin voimaan 1 päivänä kesäkuuta 2021.",
    ):
        _assert_same_match(_OLD_SCOPED_COMMENCEMENT_RE, _SCOPED_COMMENCEMENT_RE, text)


def test_scoped_commencement_guard_rejects_text_without_anchor_literals() -> None:
    assert _scoped_commencement_guard("Lain 2 § tulee voimaan 1.1.2020.") is False
    assert _scoped_commencement_guard("kuitenkin mutta ei astu käyttöön") is False
    assert (
        _scoped_commencement_guard("... tulee kuitenkin voimaan 1 päivänä ...") is True
    )


def test_scoped_commencement_corpus_gap_under_bound() -> None:
    """The bound in _SCOPED_COMMENCEMENT_RE must stay above the real-corpus gap.

    The largest observed gap (2025/1440) is 633 chars; the pattern bounds it to
    2000.  If a future corpus needs a larger gap, this invariant flags the
    silent-truncation risk before it changes replay output.
    """
    # Extract the {1,N} bound from the compiled pattern source.
    m = _re.search(r"\.\{1,(\d+)\}\?", _SCOPED_COMMENCEMENT_RE.pattern)
    assert m is not None, "expected a bounded lazy gap in _SCOPED_COMMENCEMENT_RE"
    bound = int(m.group(1))
    assert bound >= 1000, (
        f"_SCOPED_COMMENCEMENT_RE gap bound {bound} is below the safety margin "
        "for the observed 633-char real-corpus gap"
    )


def test_scoped_commencement_adversarial_no_anchor_is_fast() -> None:
    """Many subject words ('Lain'), no anchor: the guard rejects in O(n)."""
    text = ("Lain " * 64000) + ("a " * 64000)
    assert _scoped_commencement_guard(text) is False
    t0 = time.perf_counter()
    if _scoped_commencement_guard(text):
        _SCOPED_COMMENCEMENT_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < _CEILING_MS, (
        f"scoped-commencement no-anchor adversarial took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); literal guard regression suspected"
    )
