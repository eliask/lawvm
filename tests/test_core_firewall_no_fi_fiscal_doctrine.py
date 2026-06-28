"""AST-scan no-leak test for the §2.3 core/frontend firewall fix (iter2 W5 H1).

Pins that Finnish fiscal doctrine never returns to ``src/lawvm/core/**``. The
fix moved ``lawvm.core.pool_mention`` (which previously hosted the concrete
Finnish ``PoolMention``, ``QuantityKind``, ``PoolResolutionConfidence``,
``AmbiguousPoolMention``, ``BudgetLineRenumberingObservation``,
``RejectedPoolCandidate``, the ``pool_canonical_id`` factory, and the
``pool_mention_to_row`` serializer) into
``lawvm.finland.pool_mention_primitive``; core now hosts only the abstract
``ProvisionMention`` marker protocol (mirrors the ``ScopeConfidence``
precedent in ``lawvm.core.scope_confidence``).

Mirrors the precedent in ``tests/test_core_firewall_no_fi_definition_phrases.py``
and ``tests/test_fi_recovery_kind_enum.py``'s AST scan: a structural exclusion
test is the only mechanism that catches a future re-leak through code review
alone -- a re-introduced phrase literal or concrete-class import in the kernel
would compile fine and pass every behavior test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_DIR = _REPO_ROOT / "src" / "lawvm" / "core"

# The Finnish fiscal-doctrine phrase literals that lived in
# ``core/pool_mention.py`` before the fix. None of these may appear in any
# ``core/**.py`` file as a string literal -- neither as bare strings nor as
# identifier fragments surfaced through Constant nodes. The single owner is
# ``lawvm.finland.pool_mention_primitive`` (and ``lawvm.finland.pool_mention_extractor``
# for the ``momentti_code`` candidate field).
#
# ``momentti`` alone is NOT on this list -- it has legitimate non-budget uses
# elsewhere in core as a "subsection" gloss (``selector.py``,
# ``reference_mention.py``, ``unit_registry.py``). The forbidden forms are the
# compounds that are unambiguously budget-line doctrine: ``talousarvion
# momentti`` (Finnish "the budget's momentti") and the budget-line resolver's
# canonical-id-probing identifier ``momentti_code``.
_FORBIDDEN_FI_PHRASES: tuple[str, ...] = (
    "talousarviolaki",
    "paaluokka",
    "yleiskate",
    "talousarvion momentti",
    "momentti_code",
)

# Substring forbidden in any string-literal Constant in ``core/**.py``. The
# canonical-id prefix ``fi.budget`` is what makes a ``pool_canonical_id`` value
# identifiably Finnish (``fi.budget.28.91.50``); a literal containing this
# substring in core would re-open the §2.3 leak. Mirrors the precedent's
# exact-phrase check, broadened to a substring because the suffix is variable
# (``fi.budget.N.N.N``).
_FORBIDDEN_FI_SUBSTRINGS: tuple[str, ...] = (
    "fi.budget",
)

# Identifier that previously held the leak in the kernel. No ``core/**.py``
# file may reference these names as a Name/Attribute AST node -- neither
# import nor attribute access nor redefinition. (Docstring text mentions are
# exempt; the AST scan walks Name/Attribute nodes only, not Constant strings.)
_FORBIDDEN_KERNEL_IDENTIFIERS: tuple[str, ...] = (
    "PoolMention",
    "QuantityKind",
    "PoolResolutionConfidence",
    "AmbiguousPoolMention",
    "BudgetLineRenumberingObservation",
    "RejectedPoolCandidate",
    "pool_canonical_id",
    "pool_mention_to_row",
)


def _python_files(root: Path) -> list[Path]:
    """Return every ``.py`` file under *root*, sorted for stable diffs."""
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _ast_constant_strings(path: Path) -> set[str]:
    """Return every string literal that appears in *path*.

    AST Constant nodes are exact source literals -- substring searches against
    these would miss partial matches (``"talousarviolaki"`` cannot sneak in as
    a prefix of ``"xyz_talousarviolaki"`` without surfacing here).
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


def test_core_does_not_define_fi_pool_mention_kernel_identifier() -> None:
    """No ``core/**.py`` may reference the FI pool-mention concrete identifiers.

    Before the fix, ``core/pool_mention`` defined ``PoolMention``,
    ``QuantityKind``, ``PoolResolutionConfidence``,
    ``AmbiguousPoolMention``, ``BudgetLineRenumberingObservation``,
    ``RejectedPoolCandidate``, ``pool_canonical_id``, and
    ``pool_mention_to_row`` -- a §2.3 firewall leak. After the fix the kernel
    exposes only the abstract ``ProvisionMention`` marker protocol; concrete
    Finnish primitive lives in ``lawvm.finland.pool_mention_primitive``. This
    test catches a future re-leak through review alone.

    AST Name/Attribute walk only -- docstring text mentions of these class
    names are exempt (the protocol docstring still references the concrete
    frontend dataclass by name as the canonical implementer).
    """
    offenders: list[str] = []
    for path in _core_python_files():
        identifiers = _ast_identifiers(path)
        for forbidden_id in _FORBIDDEN_KERNEL_IDENTIFIERS:
            if forbidden_id in identifiers:
                offenders.append(
                    f"{path.relative_to(_CORE_DIR)}: forbidden identifier "
                    f"{forbidden_id!r}"
                )
    assert not offenders, (
        "core/ leaked a frontend-local Finnish pool-mention concrete identifier "
        "(AGENTS.md §2.3). Move the symbol into lawvm/finland/pool_mention_primitive.py "
        "and have the kernel consume only the abstract ProvisionMention protocol: "
        + "; ".join(offenders)
    )


def test_core_does_not_contain_fi_fiscal_doctrine_phrase_literals() -> None:
    """No ``core/**.py`` may carry the FI fiscal-doctrine phrase literals.

    A kernel file holding these string literals -- even as doc-comment prose or
    a leftover comment -- would re-open the §2.3 leak. Test only the file's
    string *constant* nodes so a docstring mention in a non-action comment is
    also flagged (no exceptions -- the firewall is structural, not rhetorical).
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
        "core/ contains a Finnish fiscal-doctrine phrase literal "
        "(AGENTS.md §2.3). The doctrine belongs in lawvm/finland/pool_mention_primitive.py: "
        + "; ".join(offenders)
    )


def test_core_does_not_carry_fi_budget_canonical_id_prefix() -> None:
    """No ``core/**.py`` string literal may contain the ``fi.budget`` substring.

    The canonical-id prefix ``fi.budget`` is Finnish fiscal-address doctrine
    (the form ``fi.budget.N.N.N``). A literal containing this substring in
    core -- even as an example value, a help string, or a regression-witness
    marker -- would re-open the §2.3 leak. Substring scan (rather than
    exact-phrase) because the suffix is variable; mirrors the precedent's
    exact-phrase check broadened to a substring for the canonical-id form.
    """
    offenders: list[str] = []
    for path in _core_python_files():
        constants = _ast_constant_strings(path)
        for constant in constants:
            for forbidden_sub in _FORBIDDEN_FI_SUBSTRINGS:
                if forbidden_sub in constant:
                    offenders.append(
                        f"{path.relative_to(_CORE_DIR)}: forbidden "
                        f"canonical-id substring {forbidden_sub!r} in "
                        f"literal {constant!r}"
                    )
    assert not offenders, (
        "core/ carries a fi.budget canonical-id literal prefix "
        "(AGENTS.md §2.3): " + "; ".join(offenders)
    )


def test_fi_concrete_primitive_lives_only_in_finland_module() -> None:
    """The concrete Finnish primitive lives in finland, not core.

    An invariant against silent drift toward re-hosting the concrete types in
    a second FI module or accidentally exporting them back into core. The owner
    is ``lawvm.finland.pool_mention_primitive``.
    """
    from lawvm.finland import pool_mention_primitive as fi_module

    # The owner module defines the concrete classes and helpers.
    for expected_name in (
        "PoolMention",
        "QuantityKind",
        "PoolResolutionConfidence",
        "AmbiguousPoolMention",
        "BudgetLineRenumberingObservation",
        "RejectedPoolCandidate",
        "pool_canonical_id",
        "pool_mention_to_row",
    ):
        assert hasattr(fi_module, expected_name), (
            f"lawvm.finland.pool_mention_primitive must define {expected_name!r}"
        )

    # Core exposes only the abstract protocol.
    import lawvm.core.pool_mention as core_module

    assert hasattr(core_module, "ProvisionMention"), (
        "lawvm.core.pool_mention must define the ProvisionMention marker protocol"
    )
    for forbidden_name in _FORBIDDEN_KERNEL_IDENTIFIERS:
        assert not hasattr(core_module, forbidden_name), (
            f"lawvm.core.pool_mention leaked concrete symbol {forbidden_name!r} "
            "back into core (AGENTS.md §2.3)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
