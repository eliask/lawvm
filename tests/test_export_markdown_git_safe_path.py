"""Security/contract tests for `_safe_path_component` and its production caller.

Background (Security L1 finding): `_SAFE_PATH_RE` previously allowed `/` in
its character class, so `_safe_path_component("../../etc/passwd")` returned
the input unchanged. The function name overpromised — it was a "safe path
component" sanitizer that preserved directory separators. The fix drops `/`
from the allowed class so the sanitizer cannot smuggle a path separator (or
a `..` traversal that needs `/` to function) past a caller that forgot to
split.

These tests pin:
  (1) the positive preserved-chars contract (`A-Za-z0-9._-`);
  (2) the negative `/`-stripping contract;
  (3) the explicit directory-separator contract (caller must split on `/`
      first — this function does NOT preserve separators); and
  (4) a production-lane fire-drill (AGENTS.md §2.9) that drives the actual
      caller `_statute_markdown_path` end-to-end through the production path
      to prove the `split("/")` + `__`-join pattern still yields a flat
      filename with no `/` after the regex tightening.
"""

from __future__ import annotations

import pytest

from lawvm.tools.export_markdown_git import (
    _SAFE_PATH_RE,
    _safe_path_component,
    _statute_markdown_path,
)


class TestSafePathComponentAllowedChars:
    """Positive tests — allowed chars pass through unchanged."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("section_5", "section_5"),
            ("a.b.c-1", "a.b.c-1"),
            ("1a", "1a"),
            ("2024", "2024"),
            ("finland", "finland"),
            ("fi", "fi"),
            ("1-001", "1-001"),
        ],
    )
    def test_preserves_allowed_chars(self, value: str, expected: str) -> None:
        """Chars in [A-Za-z0-9._-] pass through untouched."""
        assert _safe_path_component(value) == expected

    def test_regex_pattern_drops_slash(self) -> None:
        """Pin the regex shape: `/` is NOT in the allowed character class.

        This is the load-bearing security property — the regex tightening
        is what makes a forgotten `split("/")` in a caller non-catastrophic.
        """
        # `/` must not appear in the negated-class's complement, i.e., it
        # must be in the disallowed set.
        assert "/" not in _SAFE_PATH_RE.pattern
        # Spot-check the actual pattern — exact-string match would be brittle
        # to surrounding whitespace, so we assert on the meaningful substring.
        assert "[^A-Za-z0-9._-]+" in _SAFE_PATH_RE.pattern


class TestSafePathComponentDisallowedChars:
    """Negative tests — disallowed chars are stripped to `-`."""

    def test_strips_directory_separators_from_traversal_vector(self) -> None:
        """`_safe_path_component("../../etc/passwd")` strips every `/` and `.`.

        With the old permissive regex (`[^A-Za-z0-9._/-]+`), `/` was allowed
        and this input was returned unchanged. With `/` dropped from the
        allowed class, every `/` collapses to `-`, leaving a flat token
        with no path separators.

        Iter2 W6 LOW/M-batch Fix 3 tightened the trailing-character strip
        from ``.strip("-")`` to ``.strip("-.")`` so that leading/trailing
        dots are also removed. Interior `.` in legitimate identifiers like
        ``a.b.c-1`` is preserved (positive test above), but a bare ``..``
        traversal fragment collapses to the ``unknown`` placeholder because
        the result is empty after the strip — so ``..`` no longer survives
        in the output even as a flat-string filename.
        """
        result = _safe_path_component("../../etc/passwd")
        assert "/" not in result, (
            f"_safe_path_component must strip `/` from path-traversal input, "
            f"got {result!r}"
        )
        # Defensive: backslashes (windows separators) were never allowed and
        # still aren't.
        assert "\\" not in result
        # The sanitizer must fire — output must differ from input.
        assert result != "../../etc/passwd", (
            "sanitizer must change input containing `/` to demonstrate the fix"
        )

    def test_strips_leading_trailing_slashes(self) -> None:
        """A leading `/` (absolute-path attempt) becomes `-` then is stripped;
        the result is a relative-path component."""
        assert _safe_path_component("/etc/passwd") == "etc-passwd"
        assert _safe_path_component("a/") == "a"
        assert _safe_path_component("/a") == "a"

    def test_all_slashes_collapse_to_unknown(self) -> None:
        """An all-`/` input has nothing left after stripping — yields the
        `unknown` placeholder, not an empty string."""
        assert _safe_path_component("///") == "unknown"
        assert _safe_path_component("/") == "unknown"

    def test_runs_of_disallowed_collapse_to_single_dash(self) -> None:
        """A run of disallowed chars collapses to one `-` (regex `+`)."""
        # Multiple `/` adjacent would collapse — but here we use spaces to
        # demonstrate the `+` quantifier on a different disallowed char.
        assert _safe_path_component("hello world") == "hello-world"
        assert _safe_path_component("hello   world") == "hello-world"
        # Mixed run of `/` and space collapses to one `-`.
        assert _safe_path_component("hello/ world") == "hello-world"

    def test_strips_leading_trailing_dashes_after_substitution(self) -> None:
        """Leading/trailing `-` produced by substitution are stripped."""
        assert _safe_path_component("--hello--") == "hello"
        # Leading `/` becomes `-` then is stripped — equivalent to `-hello`.
        assert _safe_path_component("-hello-") == "hello"

    def test_empty_or_whitespace_only_returns_unknown(self) -> None:
        """Empty / whitespace-only inputs yield the `unknown` placeholder."""
        assert _safe_path_component("") == "unknown"
        assert _safe_path_component("   ") == "unknown"
        assert _safe_path_component("\t\n") == "unknown"

    # Iter2 W6 LOW/M-batch Fix 3: leading/trailing dots are also stripped
    # (previously the `.` char was preserved verbatim by the regex, so a
    # bare `..` segment would survive as a literal `..` filename even though
    # it had no traversal value once `/` was gone — it just alarmed security
    # reviewers reading the projection output). With `.strip("-.")` the
    # leading/trailing-dot class is removed; the interior-dot contract for
    # legitimate identifiers like `a.b.c-1` is preserved.
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("..", "unknown"),
            (".", "unknown"),
            ("...", "unknown"),
            (".-.", "unknown"),
            ("-.foo.-", "foo"),
            (".foo.", "foo"),
            ("..foo..", "foo"),
            # Interior dots in legitimate identifiers are preserved.
            ("a.b.c-1", "a.b.c-1"),
            ("1.0", "1.0"),
        ],
    )
    def test_strips_leading_trailing_dots_after_substitution(
        self, value: str, expected: str
    ) -> None:
        """`..`/`.`/`...` collapse to ``unknown``; interior dots survive."""
        assert _safe_path_component(value) == expected, (
            f"_safe_path_component({value!r}) should yield {expected!r}"
        )


class TestSafePathComponentDirectorySeparatorContract:
    """Explicit contract: `_safe_path_component` does NOT preserve directory
    separators. Callers that want directory handling MUST split on `/` first
    (the production caller `_statute_markdown_path` does this)."""

    @pytest.mark.parametrize(
        "malicious_input",
        [
            "../../etc/passwd",
            "/etc/passwd",
            "a/b/c",
            "///",
            "/",
            "a/",
            "/a",
            "../",
            "..",
            "windows\\system32",  # backslash also disallowed
        ],
    )
    def test_no_directory_separator_survives(self, malicious_input: str) -> None:
        """For any input that contains a directory separator (POSIX or
        Windows), the sanitizer output must not contain that separator."""
        result = _safe_path_component(malicious_input)
        assert "/" not in result, (
            f"_safe_path_component({malicious_input!r}) = {result!r} "
            "must not preserve `/`"
        )
        assert "\\" not in result, (
            f"_safe_path_component({malicious_input!r}) = {result!r} "
            "must not preserve backslash"
        )


class TestStatuteMarkdownPathProductionLane:
    """Production-lane fire-drill (AGENTS.md §2.9): drive `_statute_markdown_path`
    — the actual production caller of `_safe_path_component` — end-to-end to
    prove the caller's `split("/")` + `__`-join pattern still yields a flat
    filename with NO `/` from the input statute_id, after the regex tightening.

    This is the guard-liveness test: it would be useless to tighten the
    sanitizer and then find the production caller had a forgotten `split("/")`
    path that relied on the old permissive regex. We exercise the actual
    production function, not a unit test of the sanitizer in isolation."""

    def test_numeric_statute_id_uses_template_slashes_only(self) -> None:
        """Numeric statute IDs (`<num>/<4-digit-year>`) are detected by the
        caller and routed through the `acts/{year}/{num}.md` template. The
        `/` chars in the OUTPUT come from the template literal, not from
        `_safe_path_component` — the substituted `year` and `num` parts
        are sanitized and must not contain `/`."""
        path = _statute_markdown_path("123/2024", jurisdiction="fi")
        assert path == "acts/2024/123.md"

        # Defensive: a malicious numeric-looking statute_id where the `num`
        # part itself contains `/` is impossible by construction (partition
        # stops at the first `/`), but the substituted segments are still
        # sanitized — verify the contract holds for the `num` segment too.
        # `1000/2020` routes through the numeric branch.
        path2 = _statute_markdown_path("1000/2020", jurisdiction="fi")
        assert path2 == "acts/2020/1000.md"

    def test_non_numeric_statute_id_joins_parts_with_double_underscore(self) -> None:
        """A non-numeric statute_id containing `/` is split first, then the
        parts are joined with `__` (NOT `/`) by the caller. The filename
        portion of the resulting path must contain `__` and must NOT contain
        any `/` carried over from the input statute_id."""
        path = _statute_markdown_path("koko/2024/v", jurisdiction="fi")
        assert path == "acts/fi/koko__2024__v.md"
        # The filename (everything after `acts/<jurisdiction>/`) uses the
        # caller's `__` separator, not `/`.
        filename = path.removeprefix("acts/fi/")
        assert "__" in filename
        assert "/" not in filename, (
            f"caller must produce a flat filename via `__` join, got {filename!r}"
        )

    def test_path_traversal_statute_id_is_neutralized_by_caller(self) -> None:
        """Even a maliciously crafted statute_id like `../../etc/passwd` is
        neutralized: the caller's `split("/")` separates it into parts
        (`["..", "..", "etc", "passwd"]`), each part is sanitized, and the
        parts are joined with `__` — producing a flat filename with no `/`
        and no directory-traversal semantics. Per iter2 W6 LOW/M-batch
        Fix 3, the ``..``-parts collapse to the ``unknown`` placeholder
        (interior ``.`` is preserved but leading/trailing dots are stripped),
        so the resulting filename is e.g.
        ``unknown__unknown__etc__passwd.md`` — flat, no `/`, no remaining
        ``..`` substring to alarm a security scanner."""
        path = _statute_markdown_path("../../etc/passwd", jurisdiction="fi")
        assert path.startswith("acts/fi/")
        filename = path.removeprefix("acts/fi/")
        # The filename portion has NO `/` — that is the caller's contract
        # and the load-bearing safety property.
        assert "/" not in filename, (
            f"caller must produce a flat filename via `__` join, got {filename!r}"
        )
        assert "__" in filename, (
            f"caller must join parts with `__`, got {filename!r}"
        )
        # Iter2 W6 LOW/M-batch Fix 3: the `..` substrings no longer survive
        # as flat-string filename fragments — the leading/trailing-dot strip
        # in `_safe_path_component` collapses them to `unknown`.
        assert ".." not in filename, (
            f"`..` traversal fragment must not survive in the sanitized "
            f"filename (got {filename!r})"
        )
        # The filename must end with the `.md` extension — proving the path
        # stays inside the `acts/<jurisdiction>/` directory as a markdown file.
        assert filename.endswith(".md")

    def test_production_filename_uses_double_underscore_not_slash(self) -> None:
        """The defining property of the caller's `split("/")` + `__`-join
        pattern: for ANY statute_id containing `/`, the filename portion of
        the resulting path uses `__` as its internal separator, never `/`."""
        for statute_id in [
            "koko/2024/v",
            "a/b",
            "a/b/c/d/e",
            "../../etc/passwd",
            "foo//bar",  # double slash — `if part.strip()` skips empty parts
        ]:
            path = _statute_markdown_path(statute_id, jurisdiction="fi")
            assert path.startswith("acts/fi/"), (
                f"path {path!r} for statute_id {statute_id!r} must start with "
                f"`acts/fi/`"
            )
            filename = path.removeprefix("acts/fi/")
            assert "/" not in filename, (
                f"filename {filename!r} (from statute_id {statute_id!r}) "
                f"must not contain `/`"
            )
