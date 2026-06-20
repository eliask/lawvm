"""Unit test for the self-evidencing rule-id locator (spec_ledger_discovery).

Locks the improvement that the catalog-completeness gates name *where* each uncataloged
rule id is emitted (file:line), so a future refactor cannot regress the diagnostic back
to a bare id list.  Uses a synthetic package directory with a known rule-id literal so
the test does not depend on any jurisdiction's real source.
"""
from __future__ import annotations

from pathlib import Path

from lawvm.tools.spec_ledger_discovery import (
    believed_spec_skeleton,
    format_uncataloged,
    locate_rule_ids,
)


def _make_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "jx_legislation"
    pkg.mkdir()
    (pkg / "amendatory.py").write_text(
        "SOME_RULE_ID = \"jx_amend_strike_insert_tail\"\n"
        "OTHER = 1\n"
        "def emit():\n"
        "    return witness(\"jx_amend_strike_insert_tail\")\n",
        encoding="utf-8",
    )
    (pkg / "cataloged.py").write_text(
        "X = \"jx_already_cataloged\"\n",
        encoding="utf-8",
    )
    return pkg


def test_locate_finds_each_literal_occurrence_with_line(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    locations = locate_rule_ids(
        pkg, ["jx_amend_strike_insert_tail"], repo_root=tmp_path
    )
    sites = locations["jx_amend_strike_insert_tail"]
    # Both the constant RHS (line 1) and the witness call arg (line 4) are located.
    assert sites == [
        ("jx_legislation/amendatory.py", 1),
        ("jx_legislation/amendatory.py", 4),
    ]


def test_locate_relative_to_package_when_no_repo_root(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    locations = locate_rule_ids(pkg, ["jx_already_cataloged"])
    assert locations["jx_already_cataloged"] == [("cataloged.py", 1)]


def test_recursive_descends_into_subpackages(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    sub = pkg / "nested"
    sub.mkdir()
    (sub / "more.py").write_text('Y = "jx_nested_rule"\n', encoding="utf-8")
    flat = locate_rule_ids(pkg, ["jx_nested_rule"], repo_root=tmp_path)
    assert flat["jx_nested_rule"] == []  # non-recursive misses the subpackage
    deep = locate_rule_ids(pkg, ["jx_nested_rule"], recursive=True, repo_root=tmp_path)
    assert deep["jx_nested_rule"] == [("jx_legislation/nested/more.py", 1)]


def test_format_uncataloged_names_the_emit_site(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    uncataloged = ["jx_amend_strike_insert_tail"]
    locations = locate_rule_ids(pkg, uncataloged, repo_root=tmp_path)
    message = format_uncataloged(uncataloged, locations)
    # The self-evidencing contract: the message carries the id AND its file:line site(s),
    # so no grep is needed to find where it is emitted.
    assert "jx_amend_strike_insert_tail" in message
    assert "jx_legislation/amendatory.py:1" in message
    assert "jx_legislation/amendatory.py:4" in message
    assert "<-" in message


def test_format_marks_ids_without_a_literal_site_loudly() -> None:
    # An id discovered via a context the locator cannot see as a literal still appears,
    # annotated rather than silently dropped.
    message = format_uncataloged(["jx_context_only"], {"jx_context_only": []})
    assert "jx_context_only" in message
    assert "<no literal emit site found>" in message


def test_believed_spec_skeleton_is_paste_ready(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    uncataloged = ["jx_amend_strike_insert_tail"]
    locations = locate_rule_ids(pkg, uncataloged, repo_root=tmp_path)
    skeleton = believed_spec_skeleton(uncataloged, locations)
    assert '"jx_amend_strike_insert_tail": "",' in skeleton
    assert "# jx_legislation/amendatory.py:1" in skeleton


def test_empty_input_returns_empty(tmp_path: Path) -> None:
    assert locate_rule_ids(_make_pkg(tmp_path), []) == {}
    assert format_uncataloged([], {}) == ""
