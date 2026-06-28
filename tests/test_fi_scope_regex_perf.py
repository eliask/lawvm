"""Performance regression tests for ``lawvm.finland.scope`` chapter-chunk recognizers.

The ``_chapter_chunk_mentions_section_label`` / ``_chapter_chunk_mentions_lo``
recognizers run in the hot per-amendment-op path: scope resolution scans the
johtolause chunk once per candidate target. Pre-iter-2 each per-call
``re.search(rf"\\b{label}\\s*§", chunk, flags=re.I)`` re-compiled the bounded
label + suffix fragment on every invocation across 11 sites in scope.py. The
H6 fix (iter-2 W4) routes these sites through
``_scope_section_pattern(label_pat, suffix, flags)`` — a bounded
``@lru_cache(maxsize=512)`` factory mirroring
``finland.constraints._label_clause_pattern`` (which itself mirrors
``us_federal._word_boundary_pattern``).

This file pins three invariants, mirroring ``test_fi_se_tools_regex_perf``:

  1. Positive: a known-matching chunk returns the expected truthy result.
  2. Negative: a chunk with NO ``§`` markers completes under the ceiling.
  3. Adversarial: a ~5 KB chunk with many ``§``-lead-ins completes under the
     per-call ceiling across each of the three subsec/item branches (plain
     fall-through, subsec+item, subsec-only).
"""

from __future__ import annotations

import time

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import StructuralAction

from lawvm.finland.scope import (
    _chapter_chunk_mentions_lo,
    _chapter_chunk_mentions_section_label,
)


_CEILING_MS = 100  # generous per-call ceiling (mirrors test_fi_se_tools_regex_perf)


def _make_lo(
    *,
    section: str = "5",
    subsection: str | None = None,
    item: str | None = None,
) -> LegalOperation:
    """Build a minimal ``LegalOperation`` for the scope recognizer.

    ``special=None`` so the recognizer takes the fall-through branches
    (the heading/intro fast paths compare against string literals and do
    not fire on a ``FacetKind`` enum — exercising them is out of scope for
    this H6 perf ceiling).
    """
    path: list[tuple[str, str]] = [("chapter", "1"), ("section", section)]
    if subsection is not None:
        path.append(("subsection", subsection))
    if item is not None:
        path.append(("item", item))
    return LegalOperation(
        op_id="perf-t",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=tuple(path), special=None),
    )


# ---------------------------------------------------------------------------
# Positive: a known-matching input returns True
# ---------------------------------------------------------------------------


def test_chapter_chunk_mentions_section_label_positive_match() -> None:
    """A chunk that names section 5 with a § lead-in returns True."""
    chunk = "muutetaan 5 § seuraavasti:"
    assert _chapter_chunk_mentions_section_label(chunk, "5") is True


def test_chapter_chunk_mentions_lo_positive_match_plain_section() -> None:
    """A plain ``5 §`` mention matches a section-only op via fall-through."""
    chunk = "muutetaan 5 § seuraavasti:"
    lo = _make_lo(section="5")
    assert _chapter_chunk_mentions_lo(chunk, lo) is True


# ---------------------------------------------------------------------------
# Negative: no § markers → completes under ceiling
# ---------------------------------------------------------------------------


def test_chapter_chunk_mentions_section_label_no_section_sign_is_fast() -> None:
    """A chunk with no § markers must return False under ceiling.

    Pre-iter-2 each per-call ``re.search(rf"\\b{label}\\s*§", chunk, flags=re.I)``
    re-compiled the label fragment per call; the H6 ``_scope_section_pattern``
    LRU factory must keep this bounded.
    """
    chunk = "x" * 5000 + " 5 " + "y" * 5000
    assert "§" not in chunk
    t0 = time.perf_counter()
    result = _chapter_chunk_mentions_section_label(chunk, "5")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"no-§ chunk took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "H6 LRU factory regression suspected"
    )


# ---------------------------------------------------------------------------
# Adversarial: ~5 KB chunks with many §-lead-ins, across the family of
# recognizer branches that route through ``_scope_section_pattern``.
# ---------------------------------------------------------------------------


def test_chapter_chunk_mentions_section_label_adversarial_many_signs_is_fast() -> None:
    """~5 KB chunk with many ``§`` markers, label "1234" never matches.

    Exercises the ``_chapter_chunk_mentions_section_label`` direct + genitive
    + range + ``ja`` branches (sites at scope.py:870, 887-892, plus the
    range/ja findall sites that scan the chunk).
    """
    block = "Lain 5 §:n 1 momentti sekä 7 § ja 9 §:n 2 momentti. "
    chunk = block * 100  # ~5 KB
    assert len(chunk) > 5000
    t0 = time.perf_counter()
    result = _chapter_chunk_mentions_section_label(chunk, "1234")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial many-§ chunk took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); H6 LRU factory regression suspected"
    )


def test_chapter_chunk_mentions_lo_plain_adversarial_many_signs_is_fast() -> None:
    """~5 KB chunk, op-target has no subsec/item — exercises the
    fall-through from ``_chapter_chunk_mentions_lo`` to
    ``_chapter_chunk_mentions_section_label``."""
    block = "Lain 5 §:n 1 momentti sekä 7 § ja 9 §:n 2 momentti. "
    chunk = block * 100  # ~5 KB
    lo = _make_lo(section="1234")
    t0 = time.perf_counter()
    result = _chapter_chunk_mentions_lo(chunk, lo)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial plain branch took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); H6 LRU factory regression suspected"
    )


def test_chapter_chunk_mentions_lo_subsec_item_adversarial_is_fast() -> None:
    """~5 KB chunk, op-target has subsec+item — exercises the
    ``subsec is not None and item is not None`` branch (sites at
    scope.py:978, 980 + the ``_item_in_chunk`` kohta search at 954)."""
    block = "Lain 5 §:n 1 momentin 2 kohta sekä 7 §:n 3 momentti. "
    chunk = block * 100  # ~5 KB
    lo = _make_lo(section="5", subsection="1", item="2")
    t0 = time.perf_counter()
    result = _chapter_chunk_mentions_lo(chunk, lo)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Result may be True or False depending on internal exact-match; assert fast.
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial subsec+item branch took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); H6 LRU factory regression suspected"
    )


def test_chapter_chunk_mentions_lo_subsec_only_adversarial_is_fast() -> None:
    """~5 KB chunk, op-target has subsec only — exercises the
    ``subsec is not None`` branch (sites at scope.py:986, 988-990 +
    the ``_moment_in_chunk`` search at 936)."""
    block = "Lain 5 §:n 1 momentti sekä 7 §:n 2 momentti. "
    chunk = block * 100  # ~5 KB
    lo = _make_lo(section="5", subsection="99")  # high target unlikely to match
    t0 = time.perf_counter()
    result = _chapter_chunk_mentions_lo(chunk, lo)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial subsec-only branch took {elapsed_ms:.1f} ms "
        f"(ceiling {_CEILING_MS} ms); H6 LRU factory regression suspected"
    )
