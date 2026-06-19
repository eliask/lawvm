from __future__ import annotations

import pytest

from lawvm.tools import explain


def test_find_divergence_snippet_uses_linear_path_for_large_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForbiddenSequenceMatcher:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("SequenceMatcher should not run for oversized snippets")

    monkeypatch.setattr(explain.difflib, "SequenceMatcher", ForbiddenSequenceMatcher)

    replay = "a" * 12_000 + " replay-only " + "z" * 12_000
    oracle = "a" * 12_000 + " oracle-only " + "z" * 12_000

    snippet = explain._find_divergence_snippet(replay, oracle)

    assert "replay-only" in snippet
    assert "oracle-only" in snippet


def test_find_divergence_snippet_keeps_sequence_matcher_for_small_text() -> None:
    snippet = explain._find_divergence_snippet(
        "abc replay tail",
        "abc oracle tail",
    )

    assert 'replay: "abc replay tail"' in snippet
    assert 'oracle: "abc oracle tail"' in snippet
