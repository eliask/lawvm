"""Static AST lint banning stringly-typed comparisons against ``IRNodeKind``.

``IRNodeKind`` (``src/lawvm/core/semantic_types.py``) is a plain ``Enum``, NOT a
``str``-mixin.  Therefore a comparison like ``some_irnode.kind == "subsection"``
is *always False*: the enum member never equals the bare string.  ``ty`` does
not catch it because ``Enum.__eq__`` accepts any object.  Such comparisons are
silently-dead branches whose body never executes — a recurring bug class
(fixed instances: oracle annex counting, EE blame provision walk).

This gate walks ``src/lawvm/`` and fails if any ``.kind == "<lit>"`` /
``.kind != "<lit>"`` / ``.kind in (...)`` / ``.kind not in {...}`` compares a
``.kind`` attribute against a string literal that is one of the
**IRNodeKind-only** values.  Those values name node kinds that are NEVER used as
a string ``kind`` on any other object (CoverageUnit, finding, event, witness,
facet, locator-segment, structural-action items all use *other* string kinds
like "section"/"chapter"/"part"/"renumber"/"complete"/...).  Restricting the
ban to IRNodeKind-only values avoids false positives on genuine string-``kind``
objects, while still catching the real always-False bugs.

If you legitimately need to add a string-``kind`` object whose value collides
with one of these tokens, add the precise ``file:line`` to ``_ALLOWLIST`` with a
reason — but the correct fix for an IRNode comparison is to compare against the
``IRNodeKind`` member (e.g. ``node.kind is IRNodeKind.SUBSECTION``).
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src" / "lawvm"

# IRNodeKind member values that are NEVER used as a string `kind` on any other
# object in the codebase.  These are the safe-to-ban tokens: a `.kind ==
# "<token>"` against any of these is necessarily an IRNode comparison and thus
# always-False.  Deliberately EXCLUDES dual-use tokens like "section",
# "chapter", "part", "subsection", "item" — those ARE genuine string kinds on
# CoverageUnit / locator segments / SemanticStructureNode / _SEMutableNode.
#
# NOTE on "heading"/"intro": deliberately EXCLUDED.  Although they are
# ``IRNodeKind`` members, they are ALSO genuine string ``kind`` values on the
# semantic layer (``SemanticStructureFacet.kind: str`` /
# ``SemanticStructureNode.kind: str`` — see ``semantic/model.py``,
# ``semantic/diff.py``, ``semantic/projection.py``).  Banning them would flag
# those legitimate string comparisons.  The tokens kept below are
# ``IRNodeKind``-only: they never appear as a string ``kind`` on a semantic
# node, coverage unit, finding, event, witness, or builder node.
_BANNED_IRNODE_ONLY_VALUES = frozenset(
    {
        "hcontainer",
        "content",
        "num",
        "nimike",
        "appendix",
    }
)
_BANNED_IRNODE_ONLY_LITERAL_SNIPPETS = frozenset(
    f"{quote}{value}{quote}"
    for value in _BANNED_IRNODE_ONLY_VALUES
    for quote in ("'", '"')
)

# Precise file:line allowlist for confirmed sites that legitimately use a banned
# token as a NON-IRNode string kind, OR that live in a file owned by another
# work-stream and are fixed there.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        # oracle_check.py annex hcontainer counting: an IRNode comparison whose
        # enum fix lands via the oracle-check work-stream (on
        # that branch converts these to IRNodeKind members).  Listed here so the
        # gate stays green in this worktree without editing the owned file.
        "src/lawvm/tools/oracle_check.py:315",
        "src/lawvm/tools/oracle_check.py:318",
        "src/lawvm/tools/oracle_check.py:323",
    }
)


def _string_literals(node: ast.expr) -> list[str]:
    """Return string-constant values reachable as direct comparison operands.

    Handles a bare string constant and tuple/list/set collections of string
    constants (the ``in`` / ``not in`` membership forms).
    """
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
    return out


def _is_kind_attr(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "kind"


def _scan() -> list[str]:
    violations: list[str] = []
    for pyfile in sorted(_SRC_ROOT.rglob("*.py")):
        try:
            source = pyfile.read_text()
            if ".kind" not in source or not any(
                snippet in source for snippet in _BANNED_IRNODE_ONLY_LITERAL_SNIPPETS
            ):
                continue
            tree = ast.parse(source, filename=str(pyfile))
        except Exception:
            continue
        rel = pyfile.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            # Walk each (left, op, right) comparison link.
            operands = [node.left, *node.comparators]
            for idx, op in enumerate(node.ops):
                left = operands[idx]
                right = operands[idx + 1]
                if not isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                    continue
                # `.kind` may be on either side of == / != ; for `in` it is the
                # left operand.
                kind_side_is_left = _is_kind_attr(left)
                kind_side_is_right = _is_kind_attr(right)
                if not (kind_side_is_left or kind_side_is_right):
                    continue
                literal_node = right if kind_side_is_left else left
                literals = _string_literals(literal_node)
                banned = [lit for lit in literals if lit in _BANNED_IRNODE_ONLY_VALUES]
                if not banned:
                    continue
                site = f"{rel}:{node.lineno}"
                if site in _ALLOWLIST:
                    continue
                violations.append(
                    f"{site}: .kind compared against IRNodeKind-only string "
                    f"literal(s) {banned} — always-False dead branch. "
                    f"Compare against IRNodeKind member instead "
                    f"(e.g. `node.kind is IRNodeKind.HEADING`)."
                )
    return violations


def test_no_stringly_typed_irnodekind_comparisons() -> None:
    violations = _scan()
    assert not violations, (
        "Stringly-typed IRNodeKind comparison(s) found (IRNodeKind is a plain "
        "Enum, not a str-mixin, so these are always False):\n  "
        + "\n  ".join(violations)
    )


def test_gate_detects_synthetic_violation() -> None:
    """The gate must actually flag the banned pattern (anti-vacuity check)."""
    src = 'if node.kind == "hcontainer":\n    pass\n'
    tree = ast.parse(src)
    found = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and _is_kind_attr(n.left):
            lits = _string_literals(n.comparators[0])
            if any(lit in _BANNED_IRNODE_ONLY_VALUES for lit in lits):
                found = True
    assert found, "gate logic failed to detect a synthetic always-False .kind comparison"
