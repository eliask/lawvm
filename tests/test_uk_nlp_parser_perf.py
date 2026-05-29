"""Adversarial perf regression tests for `parse_fragment_substitution`.

These pin the two protections that landed after a tight-bench worker spent
30+ minutes inside `_parse_fragment_substitution_cached`:

1. A substring fast-guard at the top of the function: inputs that contain
   none of the operative verbs short-circuit before any regex is touched.
2. A bounded form of the hot multi-occurrence substitution pattern: the
   `.*?` captures between quote pairs are bounded by character class, and
   the `(...)*` list repeat is bounded by a small count.

Either protection alone catches the production hang; both are kept because
the costs are negligible and they cover different adversarial shapes.
"""

from __future__ import annotations

import time

from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution


_PERF_BUDGET_S = 0.1  # 100 ms is the agreed adversarial budget for classifiers.


def _adversarial_no_verb(num_quotes: int = 50, noise_words: int = 200) -> str:
    """Long quote-heavy input with no operative verb anywhere."""
    quotes = ", ".join(f'"item{i}"' for i in range(num_quotes))
    noise = "noise " * noise_words
    return f"for {quotes} in each place where they occur, {noise}"


def _adversarial_verb_no_terminal_anchor(num_quotes: int = 30) -> str:
    """Has the verb in the prefix, has many quote pairs, but never reaches
    the bounded-regex's terminal `[quote] replacement [quote]` form.
    """
    quotes = ", ".join(f'"item{i}"' for i in range(num_quotes))
    return f"for {quotes}, in each place where they occur, substitute and then"


def test_parse_fragment_substitution_no_verb_short_circuits_under_budget() -> None:
    text = _adversarial_no_verb()
    t0 = time.perf_counter()
    result = parse_fragment_substitution(text)
    elapsed = time.perf_counter() - t0
    assert result == []
    assert elapsed < _PERF_BUDGET_S, (
        f"no-verb adversarial took {elapsed * 1000:.1f}ms, "
        f"must be under {_PERF_BUDGET_S * 1000:.0f}ms"
    )


def test_parse_fragment_substitution_unmatched_anchor_under_budget() -> None:
    text = _adversarial_verb_no_terminal_anchor()
    t0 = time.perf_counter()
    result = parse_fragment_substitution(text)
    elapsed = time.perf_counter() - t0
    assert result == []
    assert elapsed < _PERF_BUDGET_S, (
        f"unmatched-anchor adversarial took {elapsed * 1000:.1f}ms, "
        f"must be under {_PERF_BUDGET_S * 1000:.0f}ms"
    )


def test_parse_fragment_substitution_multi_occurrence_positive_unchanged() -> None:
    """Behaviour preservation: real multi-occurrence input still matches."""
    text = (
        'for "alpha", "beta" and "gamma" in each place where they occur, '
        'substitute "delta"'
    )
    result = parse_fragment_substitution(text)
    originals = {entry["original"] for entry in result}
    assert {"alpha", "beta", "gamma"} <= originals
    assert all(entry["replacement"] == "delta" for entry in result if entry["original"] in {"alpha", "beta", "gamma"})


def test_parse_fragment_substitution_repeated_calls_use_cache() -> None:
    """Behaviour preservation: cache hit returns the same content."""
    text = 'for "X" substitute "Y"'
    first = parse_fragment_substitution(text)
    second = parse_fragment_substitution(text)
    assert first == second
