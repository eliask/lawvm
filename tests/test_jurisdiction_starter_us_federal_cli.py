"""Arg-parse smoke tests for the U.S. federal CLI subcommands.

These assert that the ``us-*`` subcommands are registered on the shared parser
and parse their flags into the attributes the dispatch block reads. They do not
touch the farchive (no archive dependency, no network); the dispatch handlers
are thin shims over ``lawvm.us_federal.*`` whose logic is covered by the
jurisdiction module tests.
"""

from __future__ import annotations

from lawvm.tools import cli


def _parse(argv: list[str]):
    return cli._build_parser().parse_args(argv)


def test_us_subcommands_registered() -> None:
    parser = cli._build_parser()
    registered: set[str] = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices:
            registered.update(choices)
    assert {
        "us-import-plaw",
        "us-import-usc",
        "us-inventory",
        "us-bench",
        "us-dry-run",
        "us-source",
    } <= registered


def test_us_inventory_parses() -> None:
    args = _parse(["us-inventory", "--congress", "118", "--json"])
    assert args.command == "us-inventory"
    assert args.congress == 118
    assert args.json is True
    assert args.dest is None


def test_us_bench_parses() -> None:
    args = _parse(["us-bench", "--corpus", "x.csv", "--json"])
    assert args.command == "us-bench"
    assert args.corpus == "x.csv"
    assert args.json is True


def test_us_dry_run_parses() -> None:
    args = _parse(
        ["us-dry-run", "--title", "11", "--before", "2023", "--after", "2024"]
    )
    assert args.command == "us-dry-run"
    assert args.title == 11
    assert args.before_year == 2023
    assert args.after_year == 2024
    assert args.json is False


def test_us_source_parses() -> None:
    args = _parse(
        ["us-source", "--title", "11", "--year", "2023", "--section", "362"]
    )
    assert args.command == "us-source"
    assert args.title == 11
    assert args.year == 2023
    assert args.section == "362"


def test_us_source_section_optional() -> None:
    args = _parse(["us-source", "--title", "11", "--year", "2023"])
    assert args.section is None


def test_us_import_plaw_parses_sources() -> None:
    args = _parse(
        ["us-import-plaw", "a.zip", "b.zip", "--skip-existing", "--dry-run"]
    )
    assert args.command == "us-import-plaw"
    assert args.sources == ["a.zip", "b.zip"]
    assert args.skip_existing is True
    assert args.dry_run is True


def test_us_import_usc_parses_sources() -> None:
    args = _parse(["us-import-usc", "t11.htm", "--dest", "/tmp/x.farchive"])
    assert args.command == "us-import-usc"
    assert args.sources == ["t11.htm"]
    assert args.dest == "/tmp/x.farchive"
