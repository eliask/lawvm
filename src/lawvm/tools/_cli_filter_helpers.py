"""Shared argparse filter helpers for LawVM CLI query commands.

Provides reusable argparse argument definitions for the common global options
per JURISDICTION_CLI_TOOLING_CONTRACT.md §4:
  -j / --jurisdiction
  --as-of DATE
  -o / --output-format
  --limit N
  --data-dir PATH

These helpers add arguments to an argparse.ArgumentParser or subparser
without creating a duplicate parent-parser dependency.
"""
from __future__ import annotations

import argparse
import re


# ---------------------------------------------------------------------------
# Typed argument validators
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_iso_date(value: str) -> str:
    """Argparse type: validate ISO date string (YYYY-MM-DD).

    Raises argparse.ArgumentTypeError on invalid format.
    """
    if not _ISO_DATE_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"expected date in YYYY-MM-DD format, got {value!r}"
        )
    return value


def positive_int(value: str) -> int:
    """Argparse type: validate positive integer."""
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")
    return n


def non_negative_int(value: str) -> int:
    """Argparse type: validate non-negative integer."""
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError(
            f"expected a non-negative integer, got {value!r}"
        )
    return n


# ---------------------------------------------------------------------------
# Common argument adders
# ---------------------------------------------------------------------------


def add_output_format_arg(
    parser: argparse.ArgumentParser,
    default: str = "table",
) -> None:
    """Add -o / --output-format argument to parser."""
    parser.add_argument(
        "-o",
        "--output-format",
        dest="output_format",
        default=default,
        choices=["table", "json", "jsonl", "csv", "parquet"],
        help="output format (default: table)",
    )


def add_limit_arg(parser: argparse.ArgumentParser) -> None:
    """Add --limit N argument to parser."""
    parser.add_argument(
        "--limit",
        type=positive_int,
        metavar="N",
        help="limit output rows",
    )


def add_data_dir_arg(
    parser: argparse.ArgumentParser,
    default: str = ".tmp/projections",
    help_suffix: str = "",
) -> None:
    """Add --data-dir argument to parser."""
    help_text = f"directory containing projection files (default: {default})"
    if help_suffix:
        help_text = f"{help_text}; {help_suffix}"
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=default,
        help=help_text,
    )


def add_as_of_arg(parser: argparse.ArgumentParser) -> None:
    """Add --as-of DATE argument to parser."""
    parser.add_argument(
        "--as-of",
        dest="as_of",
        metavar="DATE",
        type=valid_iso_date,
        help="filter to records valid at DATE (YYYY-MM-DD)",
    )
