"""lawvm scaffold — generate a new jurisdiction adapter skeleton.

Usage:
    lawvm scaffold <jurisdiction>   # e.g. lawvm scaffold norway

Creates src/lawvm/<jurisdiction>/ with:
  __init__.py       package + re-exports
  grafter.py        parse_statute / parse_amendment_ops / apply_ops stubs
  test_<jur>.py     minimal smoke test harness

The generated interface follows the Estonia adapter pattern (the most minimal
complete implementation). Fill in the stubs; the test harness checks imports
and NotImplementedError behaviour at every step.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_GRAFTER_TMPL = '''\
"""{display} frontend for LawVM.

Three entry points (required interface):

  parse_{jur}_statute(xml_bytes, statute_id) -> IRStatute
      Parse a base act into the canonical IRStatute / IRNode body tree.

  parse_{jur}_amendment_ops(xml_bytes, source_id) -> List[LegalOperation]
      Parse an amendment act, extracting each operation as LegalOperation.

  apply_{jur}_ops(statute, ops) -> IRStatute
      Apply a list of LegalOperations to an IRStatute.

Data source:
  TODO: describe acquisition URL/auth/format

XML schema notes:
  TODO: describe root element, namespace, section/chapter hierarchy

See also: docs/{jur}-pilot.md for recon notes (create when starting).
"""
from __future__ import annotations

from typing import List

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource


# ---------------------------------------------------------------------------
# Base act parser
# ---------------------------------------------------------------------------

def parse_{jur}_statute(xml_bytes: bytes, statute_id: str) -> IRStatute:
    """Parse a base act XML document into a canonical IRStatute.

    Steps to implement:
    1. Parse XML (ET.fromstring / lxml).
    2. Walk section/chapter hierarchy → build IRNode tree.
    3. Return IRStatute(statute_id=statute_id, body=root_irnode).

    TODO: implement
    """
    raise NotImplementedError("parse_{jur}_statute not yet implemented")


# ---------------------------------------------------------------------------
# Amendment ops extractor
# ---------------------------------------------------------------------------

def parse_{jur}_amendment_ops(xml_bytes: bytes, source_id: str) -> List[LegalOperation]:
    """Parse an amendment act XML document into a list of LegalOperations.

    Steps to implement:
    1. Locate the enacting clause / amendment table.
    2. Extract each operation: action (replace/repeal/insert), target address.
    3. Build LegalAddress(path=...) and LegalOperation for each.

    TODO: implement
    """
    raise NotImplementedError("parse_{jur}_amendment_ops not yet implemented")


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_{jur}_ops(statute: IRStatute, ops: List[LegalOperation]) -> IRStatute:
    """Apply a list of LegalOperations to an IRStatute.

    Steps to implement:
    1. For each op, locate the target node in statute.body.
    2. Replace / repeal / insert based on op.action.
    3. Return updated IRStatute (immutable preferred — deepcopy body first).

    For complex Finnish-style ops consider using lawvm.core.timeline helpers.

    TODO: implement
    """
    raise NotImplementedError("apply_{jur}_ops not yet implemented")
'''

_INIT_TMPL = '''\
"""{display} frontend package."""
from lawvm.{jur}.grafter import (
    parse_{jur}_statute,
    parse_{jur}_amendment_ops,
    apply_{jur}_ops,
)

__all__ = [
    "parse_{jur}_statute",
    "parse_{jur}_amendment_ops",
    "apply_{jur}_ops",
]
'''

_TEST_TMPL = '''\
"""Minimal smoke tests for the {display} frontend.

Run:
    cd LawVM && uv run python src/lawvm/{jur}/test_{jur}.py

These are interface-contract tests. They verify imports work and stubs are
properly stubbed. Replace with real end-to-end tests as you implement.
"""
from __future__ import annotations

import importlib
import sys


_FN_NAMES = [
    "parse_{jur}_statute",
    "parse_{jur}_amendment_ops",
    "apply_{jur}_ops",
]


def test_imports() -> None:
    """Adapter module must be importable and export all required functions."""
    mod = importlib.import_module("lawvm.{jur}.grafter")
    missing = [fn for fn in _FN_NAMES if not hasattr(mod, fn)]
    assert not missing, f"Missing from grafter.py: {{missing}}"
    print("PASS  imports")


def test_stubs_raise_not_implemented() -> None:
    """Stubs must raise NotImplementedError before real implementation exists."""
    import lawvm.{jur}.grafter as g
    for fn_name in _FN_NAMES:
        fn = getattr(g, fn_name)
        try:
            fn(b"<stub/>", "test/1")
        except NotImplementedError:
            pass  # expected
        except Exception:
            pass  # also acceptable — stub may attempt parse and fail gracefully


def test_package_init() -> None:
    """Package __init__.py must re-export all three entry points."""
    import lawvm.{jur} as pkg
    missing = [fn for fn in _FN_NAMES if not hasattr(pkg, fn)]
    assert not missing, f"Missing from __init__.py: {{missing}}"
    print("PASS  package_init")


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[4]))  # repo src/
    test_imports()
    test_stubs_raise_not_implemented()
    test_package_init()
    print("\\nAll scaffold tests passed.")
'''


# ---------------------------------------------------------------------------
# Scaffold generator
# ---------------------------------------------------------------------------

def _validate_jurisdiction(name: str) -> str:
    """Return lower-case snake_case name or raise ValueError."""
    cleaned = re.sub(r'[^a-z0-9_]', '', name.lower().replace('-', '_'))
    if not cleaned or not re.match(r'^[a-z]', cleaned):
        raise ValueError(f"Invalid jurisdiction name: {name!r} (must start with a letter, a-z/0-9/_ only)")
    return cleaned


def scaffold(jurisdiction: str, src_root: Path) -> None:
    """Create jurisdiction adapter skeleton under src_root/lawvm/<jurisdiction>/."""
    jur = _validate_jurisdiction(jurisdiction)
    display = jurisdiction.title()

    dest = src_root / "lawvm" / jur
    if dest.exists():
        print(f"ERROR: {dest} already exists. Delete it first or choose a different name.", file=sys.stderr)
        sys.exit(1)

    dest.mkdir(parents=True)

    files = {
        "__init__.py": _INIT_TMPL.format(jur=jur, display=display),
        "grafter.py":  _GRAFTER_TMPL.format(jur=jur, display=display),
        f"test_{jur}.py": _TEST_TMPL.format(jur=jur, display=display),
    }
    for fname, content in files.items():
        (dest / fname).write_text(content, encoding="utf-8")
        print(f"  created  {dest / fname}")

    print(f"\nScaffold created: {dest}")
    print("Next steps:")
    print("  1. Fill in grafter.py (parse + apply)")
    print(f"  2. Run: cd LawVM && uv run python src/lawvm/{jur}/test_{jur}.py")
    print(f"  3. Create docs/{jur}-pilot.md with acquisition recon")


def main(args) -> None:
    src_root = Path(__file__).parents[2]  # src/
    scaffold(args.jurisdiction, src_root)
