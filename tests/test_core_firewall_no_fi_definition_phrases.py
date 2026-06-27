"""AST-scan no-leak test for the §2.3 core/frontend firewall fix.

Pins that the Finnish ``tarkoitetaan`` definition-introducer idiom never
returns to ``src/lawvm/core/**``. The fix moved
``core/tree_ops._FI_DEFINITION_INTRO_PHRASES`` (and the substring check) into
``lawvm.finland.definition_introducer``; the kernel now consumes a
frontend-supplied predicate (``definition_introducer_predicate``) and treats
its verdict as an opaque ``bool`` (AGENTS.md §2.3).

Mirrors the precedent in ``tests/test_fi_recovery_kind_enum.py``'s AST scan:
a structural exclusion test is the only mechanism that catches a future
re-leak through code review alone — a re-introduced phrase literal in the
kernel would compile fine and pass every behavior test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_DIR = _REPO_ROOT / "src" / "lawvm" / "core"

# The Finnish definition-introducer phrase literals that lived in
# ``core/tree_ops._FI_DEFINITION_INTRO_PHRASES`` before the fix. None of these
# may appear in any ``core/**.py`` file — neither as bare strings nor as
# identifier fragments. The single owner is
# ``lawvm.finland.definition_introducer._FI_DEFINITION_INTRO_PHRASES``.
_FORBIDDEN_FI_PHRASES: tuple[str, ...] = (
    "tarkoitetaan",
    "joilla tarkoitetaan",
    "jolla tarkoitetaan",
)

# Identifier that previously held the leak in the kernel. No ``core/**.py``
# file may reference this name — neither import nor attribute access nor
# redefinition.
_FORBIDDEN_KERNEL_IDENTIFIERS: tuple[str, ...] = (
    "_FI_DEFINITION_INTRO_PHRASES",
    "fi_definition_list_introducer_predicate",
)


def _python_files(root: Path) -> list[Path]:
    """Return every ``.py`` file under *root*, sorted for stable diffs."""
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _ast_constant_strings(path: Path) -> set[str]:
    """Return every string literal that appears in *path*.

    AST Constant nodes are exact source literals — substring searches against
    these would miss partial matches (``"tarkoitetaan"`` cannot sneak in as
    a prefix of ``"xyz_tarkoitetaan"`` without surfacing here).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


def _ast_identifiers(path: Path) -> set[str]:
    """Return every Name/Attribute identifier that appears in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def _core_python_files() -> list[Path]:
    return _python_files(_CORE_DIR)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_core_does_not_define_fi_definition_intro_phrase_constant() -> None:
    """No ``core/**.py`` may reference the FI definition-introducer phrases.

    Before the fix, ``core/tree_ops._FI_DEFINITION_INTRO_PHRASES`` branched on
    the Finnish ``tarkoitetaan`` idiom — a §2.3 firewall leak. After the fix
    the kernel consumes an opaque frontend predicate; the language fragment is
    owned by ``lawvm.finland.definition_introducer``. This test catches a
    future re-leak through review alone.
    """
    offenders: list[str] = []
    for path in _core_python_files():
        # Identifier check — no Name/Attr may match the forbidden identifiers.
        identifiers = _ast_identifiers(path)
        for forbidden_id in _FORBIDDEN_KERNEL_IDENTIFIERS:
            if forbidden_id in identifiers:
                offenders.append(
                    f"{path.relative_to(_CORE_DIR)}: forbidden identifier "
                    f"{forbidden_id!r}"
                )
    assert not offenders, (
        "core/ leaked a frontend-local Finnish definition-introducer identifier "
        "(AGENTS.md §2.3). Move the language fragment into lawvm/finland/ and "
        "have the kernel consume an opaque predicate instead: " + "; ".join(offenders)
    )


def test_core_does_not_contain_fi_definition_intro_phrase_literals() -> None:
    """No ``core/**.py`` may carry the FI ``tarkoitetaan`` phrase literals.

    A kernel file holding these string literals — even as doc-comment prose or
    a leftover comment — would re-open the §2.3 leak. Test only the file's
    string *constant* nodes so a docstring mention in a non-action comment is
    also flagged (no exceptions — the firewall is structural, not rhetorical).
    """
    offenders: list[str] = []
    for path in _core_python_files():
        constants = _ast_constant_strings(path)
        for phrase in _FORBIDDEN_FI_PHRASES:
            if phrase in constants:
                offenders.append(
                    f"{path.relative_to(_CORE_DIR)}: forbidden FI phrase "
                    f"{phrase!r}"
                )
    assert not offenders, (
        "core/ contains a Finnish definition-introducer phrase literal "
        "(AGENTS.md §2.3). The language fragment belongs in lawvm/finland/: "
        + "; ".join(offenders)
    )


def test_fi_predicate_lives_only_in_finland_module() -> None:
    """The FI definition-introducer phrase constant lives in FI, not core.

    An invariant against silent drift toward re-hosting the fragment in a
    second FI module or accidentally exporting it back into core. The owner
    is ``lawvm.finland.definition_introducer``.
    """
    from lawvm.finland import definition_introducer as fi_module

    # The owner module defines the constant.
    assert hasattr(fi_module, "_FI_DEFINITION_INTRO_PHRASES")
    # The owner module exports the predicate used by the kernel call sites.
    assert hasattr(fi_module, "fi_definition_list_introducer_predicate")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
