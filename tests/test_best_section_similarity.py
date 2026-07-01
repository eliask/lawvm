"""Byte-identity guard for the pruned all-pairs similarity max.

``best_section_similarity`` is a performance rewrite of the NZ chain-replay
O(N^2) cross product::

    max(section_similarity(a, b) for a in replay_texts for b in oracle_texts)

The prune (length upper bound + ``Levenshtein.ratio`` ``score_cutoff``) must never
change which value wins — only skip pairs that provably cannot beat the running
best. These tests assert the pruned result is float-identical to the naive max
across hand-picked edge cases and a deterministic fuzz corpus.
"""
from __future__ import annotations

import random

from lawvm.core.evidence_support import best_section_similarity, section_similarity


def _naive_best(replay_texts: list[str], oracle_texts: list[str]) -> float:
    return max(
        section_similarity(a, b) for a in replay_texts for b in oracle_texts
    )


EDGE_CASES = [
    # identical text -> exact 1.0 short-circuit
    (["the quick brown fox"], ["the quick brown fox"]),
    # empty vs empty (cleaned) -> 1.0 special case
    (["   ...  "], [""]),
    (["!!!"], ["@@@"]),
    # one empty one not -> 0.0 special case
    (["hello world"], [""]),
    ([""], ["hello world"]),
    # a bucket where the best is NOT the first pair
    (["zzz", "the quick brown fox jumps"], ["nope", "the quick brown fox jumped"]),
    # length-bound skip: very different lengths cannot beat a near-1.0 pair
    (["abcdefghij", "x"], ["abcdefghik", "xxxxxxxxxxxxxxxxxxxxx"]),
    # multiple candidates all low
    (["aaaa", "bbbb"], ["cccc", "dddd", "eeee"]),
    # unicode word chars
    (["naïve café"], ["naive cafe", "naïve café"]),
]


def test_best_section_similarity_edge_cases_identical():
    for replay_texts, oracle_texts in EDGE_CASES:
        expected = _naive_best(replay_texts, oracle_texts)
        got = best_section_similarity(replay_texts, oracle_texts)
        assert got == expected, (replay_texts, oracle_texts, got, expected)


def test_best_section_similarity_fuzz_identical():
    rng = random.Random(20260701)
    alphabet = "abcde fghij .,;!?-_"
    for _ in range(2000):
        def rand_text() -> str:
            n = rng.randint(0, 30)
            return "".join(rng.choice(alphabet) for _ in range(n))

        replay_texts = [rand_text() for _ in range(rng.randint(1, 4))]
        oracle_texts = [rand_text() for _ in range(rng.randint(1, 4))]
        expected = _naive_best(replay_texts, oracle_texts)
        got = best_section_similarity(replay_texts, oracle_texts)
        # Byte-identical float equality: the prune must not perturb the max.
        assert got == expected, (replay_texts, oracle_texts, got, expected)


def test_best_section_similarity_ordering_invariance():
    # The naive max is order-independent; the pruned version must match for both
    # orderings (the running best that seeds the cutoff depends on order, so this
    # exercises that the seed choice cannot change the final value).
    rng = random.Random(99)
    alphabet = "abc xyz .!"
    for _ in range(500):
        def rand_text() -> str:
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 20)))

        replay_texts = [rand_text() for _ in range(rng.randint(1, 5))]
        oracle_texts = [rand_text() for _ in range(rng.randint(1, 5))]
        forward = best_section_similarity(replay_texts, oracle_texts)
        reverse = best_section_similarity(
            list(reversed(replay_texts)), list(reversed(oracle_texts))
        )
        assert forward == reverse
        assert forward == _naive_best(replay_texts, oracle_texts)
