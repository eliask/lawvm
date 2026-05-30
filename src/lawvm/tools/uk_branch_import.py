"""Import an explicit UK proposed-law branch graph payload."""

from __future__ import annotations

import json
import sys
from typing import Any

from lawvm.uk_legislation.proposed_law_branch import (
    build_uk_proposed_law_branch_payload_from_dict,
)


def build_uk_branch_import_payload(path: str) -> dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("UK branch import payload must be a JSON object")
    return build_uk_proposed_law_branch_payload_from_dict(data).to_dict()


def main(args: Any) -> None:
    indent = 2 if args.pretty else None
    json.dump(
        build_uk_branch_import_payload(str(args.input)),
        sys.stdout,
        ensure_ascii=False,
        indent=indent,
    )
    sys.stdout.write("\n")
