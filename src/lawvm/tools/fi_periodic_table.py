"""CLI for the Finland periodic table of abstractions."""

from __future__ import annotations

import json
import sys
from argparse import Namespace

from lawvm.finland.periodic_table import (
    periodic_table_summary,
    render_finland_periodic_table_markdown,
)


def main(args: Namespace) -> None:
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(periodic_table_summary(), indent=2, sort_keys=True))
        sys.stdout.write("\n")
        return

    sys.stdout.write(render_finland_periodic_table_markdown())
