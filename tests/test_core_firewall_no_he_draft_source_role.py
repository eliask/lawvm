"""AST-scan no-leak test: the FI ``he_draft`` document-role idiom stays out of core.

``lawvm.core`` is the jurisdiction-neutral waist (AGENTS.md §2.3): a
jurisdiction-specific term must never leak into it as a machine identifier —
an enum/Literal value, a ``source_role`` / status string, a rule-id, an error
code, or a field name. The user-flagged exemplar was
``source_role="he_draft"`` (HE = Finnish *hallituksen esitys*, a government
proposal) set by FI callers and documented/branched on inside core
(``core/source_document/extraction.py`` role docstring +
``core/source_document/coverage.py::ResidualFamily``).

The fix neutralized the role to ``government_proposal_draft`` (a Finnish HE
luonnos / a draft SI / a COM proposal all map onto it) end-to-end: the core
vocab, every FI/tools/scripts caller, and the tests. The ``ResidualFamily``
member ``HE_DRAFT_OP_SET_UNEXTRACTED`` → ``GOVERNMENT_PROPOSAL_DRAFT_OP_SET_UNEXTRACTED``
(value ``government_proposal_draft.op_set_unextracted``).

This test is a permanent GREEN floor at ZERO — no allowlist, no ratchet.
Mirrors the precedent in ``tests/test_core_firewall_no_fi_definition_phrases.py``:
a structural AST exclusion is the only mechanism that catches a future re-leak
through code review alone — a re-introduced ``he_draft`` literal in the kernel
would compile fine and pass every behaviour test.

NOTE: the FI *frontend* (``lawvm.finland.source_document.he_draft`` module,
``fetch_he_draft`` / ``extract_he_draft_proposal`` / ``HeDraftProposal``) may
keep the ``he_draft`` name — those are legitimately jurisdiction-specific and
live in ``lawvm/finland/**``, outside this scan. Only ``lawvm/core/**`` is a
firewall floor at zero.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_DIR = _REPO_ROOT / "src" / "lawvm" / "core"

# The FI government-proposal (HE) document-role token. No ``core/**.py`` file
# may carry it — neither as a string-literal fragment (a ``source_role`` value,
# an enum value, a rule-id) nor as an identifier fragment (the old enum
# member ``HE_DRAFT_OP_SET_UNEXTRACTED``). The neutral role is
# ``government_proposal_draft``.
_FORBIDDEN_TOKEN = "he_draft"


def _python_files(root: Path) -> list[Path]:
    """Return every ``.py`` file under *root*, sorted for stable diffs."""
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _core_python_files() -> list[Path]:
    return _python_files(_CORE_DIR)


def _ast_constant_strings(source: str) -> list[str]:
    """Every string literal (Constant node) in *source*.

    A substring scan against these catches ``he_draft`` however it is embedded
    (``"he_draft"``, ``"he_draft.op_set_unextracted"``, a docstring mention)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
    return found


def _ast_identifiers(source: str) -> set[str]:
    """Every Name / Attribute / def / class / arg identifier in *source*."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.add(node.arg)
    return found


# ---------------------------------------------------------------------------
# Invariants (permanent GREEN floor at zero)
# ---------------------------------------------------------------------------


def test_core_carries_no_he_draft_string_literal() -> None:
    """No ``core/**.py`` string literal may contain the ``he_draft`` token.

    Catches a re-leak as a ``source_role`` value, a ``ResidualFamily`` value,
    a rule-id, or an error code — even inside a docstring (the firewall is
    structural, not rhetorical)."""
    offenders: list[str] = []
    for path in _core_python_files():
        for lit in _ast_constant_strings(path.read_text(encoding="utf-8")):
            if _FORBIDDEN_TOKEN in lit:
                offenders.append(f"{path.relative_to(_CORE_DIR)}: {lit!r}")
    assert not offenders, (
        "core/ carries a Finnish `he_draft` (government-proposal / HE) token in a "
        "string literal (AGENTS.md §2.3). Use the neutral role "
        "`government_proposal_draft`; the FI-specific name belongs in "
        "lawvm/finland/. Offenders: " + "; ".join(offenders)
    )


def test_core_carries_no_he_draft_identifier() -> None:
    """No ``core/**.py`` identifier may contain the ``he_draft`` token.

    Guards the old ``ResidualFamily.HE_DRAFT_OP_SET_UNEXTRACTED`` member name
    (and any future ``he_draft``-stemmed field/param/function) from returning
    to the kernel."""
    offenders: list[str] = []
    for path in _core_python_files():
        for ident in _ast_identifiers(path.read_text(encoding="utf-8")):
            if _FORBIDDEN_TOKEN in ident.lower():
                offenders.append(f"{path.relative_to(_CORE_DIR)}: {ident}")
    assert not offenders, (
        "core/ defines/references an identifier carrying the Finnish `he_draft` "
        "(HE / government-proposal) token (AGENTS.md §2.3). Rename to the neutral "
        "`government_proposal_draft` form. Offenders: " + "; ".join(offenders)
    )


def test_neutral_government_proposal_draft_role_is_the_owner() -> None:
    """The neutral role replaced ``he_draft`` in the core vocab (not merely deleted).

    Pins that the ``ResidualFamily`` member was RENAMED to the neutral form
    (its value is ``government_proposal_draft.op_set_unextracted``), so the
    firewall floor above is not vacuously green because the concept vanished."""
    from lawvm.core.source_document.coverage import ResidualFamily

    member = ResidualFamily.GOVERNMENT_PROPOSAL_DRAFT_OP_SET_UNEXTRACTED
    assert member.value == "government_proposal_draft.op_set_unextracted"
    assert not hasattr(ResidualFamily, "HE_DRAFT_OP_SET_UNEXTRACTED")


# ---------------------------------------------------------------------------
# Guard-liveness: the detectors actually catch a NEW leak and ignore comments.
# ---------------------------------------------------------------------------


class TestHeDraftDetectorLiveness:
    def test_string_literal_leak_is_detected(self) -> None:
        src = 'x = "he_draft"\n'
        hits = [lit for lit in _ast_constant_strings(src) if _FORBIDDEN_TOKEN in lit]
        assert hits == ["he_draft"]

    def test_dotted_enum_value_leak_is_detected(self) -> None:
        src = 'x = "he_draft.op_set_unextracted"\n'
        hits = [lit for lit in _ast_constant_strings(src) if _FORBIDDEN_TOKEN in lit]
        assert hits == ["he_draft.op_set_unextracted"]

    def test_identifier_leak_is_detected(self) -> None:
        src = "HE_DRAFT_OP_SET_UNEXTRACTED = 1\n"
        idents = {i for i in _ast_identifiers(src) if _FORBIDDEN_TOKEN in i.lower()}
        assert "HE_DRAFT_OP_SET_UNEXTRACTED" in idents

    def test_comment_mention_is_not_detected(self) -> None:
        # AST Constant/identifier walks ignore ``#`` comments entirely.
        src = "x = 1  # he_draft is the FI role, mapped to government_proposal_draft\n"
        assert [lit for lit in _ast_constant_strings(src) if _FORBIDDEN_TOKEN in lit] == []
        assert {i for i in _ast_identifiers(src) if _FORBIDDEN_TOKEN in i.lower()} == set()

    def test_neutral_role_is_not_flagged(self) -> None:
        src = 'x = "government_proposal_draft"\n'
        assert [lit for lit in _ast_constant_strings(src) if _FORBIDDEN_TOKEN in lit] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
