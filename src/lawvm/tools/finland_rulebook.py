"""CLI helpers for the frozen Finland rulebook scaffold."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

from lawvm.finland.rulebook import (
    FINLAND_RULEBOOK,
    render_rulebook_markdown,
    validate_rulebook_vocabulary,
)
from lawvm.finland.rulebook.export import write_generated_rulebook_assets


def main(args: Namespace) -> None:
    if getattr(args, "validate", False):
        validate_rulebook_vocabulary(FINLAND_RULEBOOK)
        print("OK: Finland rulebook vocabulary is valid")
        return

    write_dir = getattr(args, "write_dir", None)
    if write_dir:
        markdown_path, index_path = write_generated_rulebook_assets(
            FINLAND_RULEBOOK, Path(write_dir)
        )
        print(f"wrote {markdown_path}")
        print(f"wrote {index_path}")
        return

    sys.stdout.write(render_rulebook_markdown(FINLAND_RULEBOOK))
