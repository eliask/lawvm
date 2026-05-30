"""Emit a UK-shaped proposed-law branch graph demo."""

from __future__ import annotations

import json
import sys
from typing import Any

from lawvm.uk_legislation.proposed_law_branch import build_uk_proposed_law_demo_payload


def build_uk_branch_demo_payload() -> dict[str, object]:
    return build_uk_proposed_law_demo_payload().to_dict()


def main(args: Any) -> None:
    indent = 2 if args.pretty else None
    json.dump(build_uk_branch_demo_payload(), sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")
