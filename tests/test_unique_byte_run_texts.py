"""Byte-identity contract for the shared ``unique_byte_run_texts`` kernel.

``lawvm.core.provenance.unique_byte_run_texts`` is the indexed replacement for the
per-candidate two-``find`` scan the US (``amendatory._unique_byte_run_bodies``) and
UK (``uk_amendment_replay._unique_byte_run_bodies``) anchor passes used to run (the
profiled §2.7 ``bytes.find`` O(N^2) hotspot). It is a PURE SPEEDUP: it must return
exactly the list the old predicate did — every candidate whose UTF-8 encoding occurs
at exactly ONE start position (overlap-allowed) in the raw bytes, sorted LONGEST-first
with a stable tiebreak that preserves the caller's document order.

These tests pin that byte-identity against a literal transcription of the old
two-``find`` reference, including the corner cases the index must not regress:
self-overlapping needles, needles shorter than the prefix-bucket width, absent
needles, and equal-length ordering (the stable-sort tiebreak the per-op selector
relies on).
"""

from __future__ import annotations

import random

from lawvm.core.provenance import unique_byte_run_texts


def _reference(raw_bytes: bytes, candidate_texts: list[str]) -> list[str]:
    """The old per-frontend loop, verbatim: two ``find`` scans + LONGEST-first sort."""
    bodies: list[str] = []
    for text in candidate_texts:
        needle = text.encode("utf-8")
        if not needle:
            continue
        first = raw_bytes.find(needle)
        if first >= 0 and raw_bytes.find(needle, first + 1) < 0:
            bodies.append(text)
    bodies.sort(key=lambda s: -len(s))
    return bodies


def test_empty_inputs_return_empty() -> None:
    assert unique_byte_run_texts(b"", ["x"]) == []
    assert unique_byte_run_texts(b"abc", []) == []


def test_unique_and_repeated_and_absent() -> None:
    raw = b"<a>alpha</a><b>beta beta</b><c>gamma</c>"
    # "alpha" unique -> kept; "beta" repeated -> dropped; "delta" absent -> dropped.
    cands = ["alpha", "beta", "gamma", "delta"]
    got = unique_byte_run_texts(raw, cands)
    assert got == _reference(raw, cands)
    assert set(got) == {"gamma", "alpha"}


def test_self_overlapping_needle_matches_find_twice() -> None:
    # "aa" in "aaa": the two-``find`` predicate sees an OVERLAPPING second start and
    # rejects it; a naive non-overlapping count would wrongly keep it. The index must
    # agree with two-``find``.
    raw = b"aaa"
    cands = ["aa", "aaa"]
    assert unique_byte_run_texts(raw, cands) == _reference(raw, cands)
    assert "aa" not in unique_byte_run_texts(raw, cands)


def test_equal_length_order_is_document_order_stable() -> None:
    # Two distinct unique bodies of equal length must retain input (document) order,
    # since the per-op selector's LONGEST-first tiebreak depends on it.
    raw = b"<x>WORD1</x><y>WORD2</y>"
    cands = ["WORD1", "WORD2"]
    assert unique_byte_run_texts(raw, cands) == ["WORD1", "WORD2"]
    assert unique_byte_run_texts(raw, cands) == _reference(raw, cands)


def test_short_needles_take_reference_path() -> None:
    # Needles shorter than the prefix-bucket width fall back to two-``find``; verify
    # a mix of short unique / short repeated / short absent matches the reference.
    raw = b"a bb ccc a dddd"
    cands = ["bb", "ccc", "a", "dddd", "zz"]
    assert unique_byte_run_texts(raw, cands) == _reference(raw, cands)


def test_fuzz_matches_reference() -> None:
    rng = random.Random(20260701)
    alphabet = "ab "
    for _ in range(4000):
        raw_str = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        raw = raw_str.encode("utf-8")
        seen: set[str] = set()
        cands: list[str] = []
        for _ in range(rng.randint(0, 8)):
            if raw_str and rng.random() < 0.8:
                a = rng.randint(0, len(raw_str))
                b = rng.randint(a, len(raw_str))
                piece = raw_str[a:b]
            else:
                piece = "".join(rng.choice("abcz") for _ in range(rng.randint(0, 5)))
            if not piece or piece in seen:
                seen.add(piece)
                continue
            seen.add(piece)
            cands.append(piece)
        assert unique_byte_run_texts(raw, cands) == _reference(raw, cands)
