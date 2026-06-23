"""Generate bounded architecture-coherence smell inventories from source files.

This script hosts the AST scans for two of the Wave-1 architecture-coherence CI
ratchets (mirroring ``scripts/inventory_parser_smells.py`` for the regex ratchet):

  * **Ratchet B — untyped authority boundary**
    (``scan_authority_boundary_ratchet``). The central authority predicate
    ``lawvm.core.compile_records.is_blocking_compile_record`` decides whether a
    compile/evidence row blocks strict replay. That decision is an
    authority-boundary act, so its input should be the typed ``CompileRecord``
    carrier, not a raw ``dict``/``Mapping`` row. The scan finds every
    ``is_blocking_compile_record(<arg>)`` call site and classifies the argument
    as TYPED (a ``CompileRecord(...)`` / ``CompileRecord.from_mapping(...)``
    construction, or a name annotated/assigned as ``CompileRecord``) or UNTYPED
    (a bare row name typed ``dict``/``Mapping``/``Any``, a dict literal, a
    ``.get(...)``-style row). The committed baseline records the current UNTYPED
    count (the back-compat residue still passing raw rows via the ``Mapping``
    overload) per file; it is monotone non-increasing.

  * **Ratchet D — authority bleed (raw-string scope-source)**
    (``scan_scope_source_ratchet``). A Finland chapter-scope decision must
    compare a scope witness ``.source`` against a ``ScopeResolutionSource`` enum
    member, never a raw string literal. The scan finds ``Compare`` nodes where
    one operand is a scope-related ``.source`` attribute access and the other is
    a str literal (or a set/tuple/list of str literals), and the raw-string
    ``group_has_scope_source(..., "<literal>")`` calls. The committed baseline is
    0 in the already-migrated comparison files and N (the residue) in the
    scope-source PRODUCER files; it is monotone non-increasing.

Both baselines live next to the regex ratchet baseline under ``tests/data/`` and
are regenerated with ``--update-baseline``. See ``tests/test_authority_boundary_ratchet.py``
and ``tests/test_scope_source_ratchet.py``.

Ratchet C (filter conservation) is NOT a count scan — a count cannot express the
"conserving carrier returned + rejected lane read by a production consumer"
contract — so it is enforced directly as a structural contract test in
``tests/test_filter_conservation_ratchet.py``.
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


def _rel_posix(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_python_files(repo_root: Path, *roots: str) -> list[str]:
    """All non-test python files under the given ``src/lawvm`` sub-roots."""
    out: list[str] = []
    for sub in roots:
        base = repo_root / sub
        if not base.exists():
            continue
        for pyfile in sorted(base.rglob("*.py")):
            rel = _rel_posix(pyfile, repo_root)
            if "/tests/" in f"/{rel}" or pyfile.name.startswith("test_"):
                continue
            out.append(rel)
    return out


# ===========================================================================
# Ratchet B — untyped authority boundary
# ===========================================================================
#
# The central authority predicate. A call site is the authority boundary; the
# question is whether the *carrier* crossing it is typed.
_AUTHORITY_PREDICATE = "is_blocking_compile_record"
_TYPED_CARRIER = "CompileRecord"
_BOUNDARY_SCAN_ROOTS = ("src/lawvm",)


def _expr_is_typed_carrier(node: ast.expr, typed_names: set[str]) -> bool:
    """True if ``node`` evaluates to a ``CompileRecord`` (the typed carrier).

    Typed shapes:
      * ``CompileRecord(...)`` — direct construction;
      * ``CompileRecord.from_mapping(...)`` / any ``CompileRecord.<classmethod>(...)``;
      * a bare ``Name`` known to be CompileRecord-typed in this scope (a local
        assigned from a typed-carrier expression, or a param annotated
        ``CompileRecord``).
    """
    if isinstance(node, ast.Call):
        func = node.func
        # CompileRecord(...) construction.
        if isinstance(func, ast.Name) and func.id == _TYPED_CARRIER:
            return True
        # CompileRecord.from_mapping(...) / CompileRecord.<classmethod>(...).
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == _TYPED_CARRIER:
                return True
    if isinstance(node, ast.Name) and node.id in typed_names:
        return True
    return False


def _annotation_is_typed_carrier(annotation: ast.expr | None) -> bool:
    """True if a parameter/var annotation names ``CompileRecord`` anywhere."""
    if annotation is None:
        return False
    for child in ast.walk(annotation):
        if isinstance(child, ast.Name) and child.id == _TYPED_CARRIER:
            return True
        if isinstance(child, ast.Attribute) and child.attr == _TYPED_CARRIER:
            return True
    return False


def _nodes_owned_by_scope(scope: ast.AST) -> list[ast.AST]:
    """Nodes belonging to ``scope`` WITHOUT descending into nested function bodies.

    Each function/module is its own binding universe; a local in a nested function
    must not pollute the enclosing scope's typed-name set (and vice versa). Mirrors
    the same carve-out used by the regex ratchet's scope analysis.
    """
    owned: list[ast.AST] = []
    stack: list[ast.AST] = []

    def _push(children: Iterable[object]) -> None:
        for child in children:
            if not isinstance(child, ast.AST):
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested scope; handled by its own pass
            stack.append(child)

    body = getattr(scope, "body", None)
    if isinstance(body, list):
        _push(reversed(body))
    elif body is not None:
        _push([body])
    while stack:
        node = stack.pop()
        owned.append(node)
        _push(ast.iter_child_nodes(node))
    return owned


def _typed_carrier_names_for_scope(scope: ast.AST) -> set[str]:
    """Names bound to a ``CompileRecord`` within this function/module scope.

    Seeded from parameters annotated ``CompileRecord``, then a fixpoint over
    assignment statements whose value is a typed-carrier expression. Conservative
    (no kill on re-bind): once typed, stays typed, so a later untyped re-bind of
    the same name does NOT silently launder it back to untyped — but in practice
    the carrier locals are single-assignment.
    """
    typed: set[str] = set()
    args = getattr(scope, "args", None)
    if isinstance(args, ast.arguments):
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            for a in group:
                if _annotation_is_typed_carrier(a.annotation):
                    typed.add(a.arg)

    assigns: list[tuple[list[str], ast.expr]] = []
    for node in _nodes_owned_by_scope(scope):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if names:
                assigns.append((names, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _annotation_is_typed_carrier(node.annotation):
                typed.add(node.target.id)
            if node.value is not None:
                assigns.append(([node.target.id], node.value))

    changed = True
    while changed:
        changed = False
        for names, value in assigns:
            if all(n in typed for n in names):
                continue
            if _expr_is_typed_carrier(value, typed):
                for n in names:
                    if n not in typed:
                        typed.add(n)
                        changed = True
    return typed


def _enclosing_typed_names(tree: ast.AST) -> dict[int, set[str]]:
    """Map each function node id -> typed-carrier names visible in its body.

    Module scope is keyed by ``id(tree)``. Nested functions get the union of
    their own scope's typed names (parameter annotations + local assignments).
    """
    result: dict[int, set[str]] = {id(tree): _typed_carrier_names_for_scope(tree)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[id(node)] = _typed_carrier_names_for_scope(node)
    return result


def _nearest_scope_typed_names(
    tree: ast.AST,
    call: ast.Call,
    typed_by_scope: dict[int, set[str]],
) -> set[str]:
    """Typed-carrier names in scope at ``call`` (nearest enclosing function, else
    module). Unions every enclosing function scope plus module scope so a name
    typed in an outer scope is honoured."""
    names: set[str] = set(typed_by_scope.get(id(tree), set()))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if sub is call:
                    names |= typed_by_scope.get(id(node), set())
                    break
    return names


def scan_file_authority_calls(rel_path: str, text: str) -> list[dict[str, Any]]:
    """Classify every ``is_blocking_compile_record(<arg>)`` call site in a file.

    Each record: {file, line, typed: bool, arg_kind}. ``arg_kind`` is a coarse
    label of the first positional argument shape for diagnostics.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    typed_by_scope = _enclosing_typed_names(tree)
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != _AUTHORITY_PREDICATE:
            continue
        if not node.args:
            # Defensive: a no-arg call cannot carry a typed row; treat as untyped.
            records.append(
                {"file": rel_path, "line": node.lineno, "typed": False, "arg_kind": "no_arg"}
            )
            continue
        arg = node.args[0]
        typed_names = _nearest_scope_typed_names(tree, node, typed_by_scope)
        typed = _expr_is_typed_carrier(arg, typed_names)
        records.append(
            {
                "file": rel_path,
                "line": node.lineno,
                "typed": typed,
                "arg_kind": _arg_kind_label(arg),
            }
        )
    return records


def _arg_kind_label(arg: ast.expr) -> str:
    if isinstance(arg, ast.Call):
        func = arg.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return f"{func.value.id}.{func.attr}(...)"
        if isinstance(func, ast.Name):
            return f"{func.id}(...)"
        return "call(...)"
    if isinstance(arg, ast.Name):
        return f"name:{arg.id}"
    if isinstance(arg, ast.Dict):
        return "dict_literal"
    if isinstance(arg, ast.Subscript):
        return "subscript"
    return type(arg).__name__


def scan_authority_boundary_ratchet(repo_root: Path | None = None) -> dict[str, Any]:
    """Compute the untyped-authority-boundary ratchet state.

    Returns per-file UNTYPED counts (the monotone quantity), the total, the typed
    count for context, and every call-site record.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    for rel in _iter_python_files(root, *_BOUNDARY_SCAN_ROOTS):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_authority_calls(rel, text))

    untyped_counts: Counter[str] = Counter()
    typed_counts: Counter[str] = Counter()
    for rec in records:
        if rec["typed"]:
            typed_counts[rec["file"]] += 1
        else:
            untyped_counts[rec["file"]] += 1

    return {
        "untyped_counts": dict(sorted(untyped_counts.items())),
        "total_untyped": sum(untyped_counts.values()),
        "typed_counts": dict(sorted(typed_counts.items())),
        "total_typed": sum(typed_counts.values()),
        "records": records,
    }


AUTHORITY_BOUNDARY_BASELINE_PATH = Path("tests/data/authority_boundary_ratchet_baseline.json")


def authority_boundary_baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_authority_boundary_ratchet(repo_root)
    return {
        "_doc": (
            "Monotone untyped-authority-boundary ratchet baseline. Counts raw-dict/"
            "Mapping (UNTYPED) arguments crossing is_blocking_compile_record instead "
            "of the typed CompileRecord carrier. Per-file 'untyped' counts may only "
            "fall, never rise; a fall must be committed (regenerate with "
            "`uv run python scripts/inventory_architecture_smells.py "
            "--ratchet authority --update-baseline`). See "
            "tests/test_authority_boundary_ratchet.py."
        ),
        "total_untyped": state["total_untyped"],
        "untyped_counts": state["untyped_counts"],
    }


# ===========================================================================
# Ratchet D — authority bleed (raw-string scope-source)
# ===========================================================================
#
# Scope decisions must compare a witness ``.source`` against a
# ``ScopeResolutionSource`` enum member, never a raw string. We catch two shapes:
#   (1) a Compare node where one operand is a scope-related ``.source`` attribute
#       and the other is a str literal (or a collection of str literals);
#   (2) a ``group_has_scope_source(..., "<literal>")`` call (the helper takes a
#       raw ``source: str`` and compares it internally).
_SCOPE_SOURCE_ATTR = "source"
# Receiver names that mark a ``.source`` as scope-related (reasonably inclusive,
# but excludes the many unrelated ``op.source`` / ``finding.source`` accesses).
_SCOPE_RECEIVER_HINTS = frozenset(
    {
        "scope_confidence",
        "scope_witness",
        "witness",
        "scope",
        "confidence",
    }
)
_GROUP_SCOPE_SOURCE_HELPER = "group_has_scope_source"
_SCOPE_SOURCE_SCAN_ROOTS = ("src/lawvm/finland",)

# The D ledger's already-migrated comparison files (must stay at 0) and the
# scope-source producer files (residue fenced here). Recorded for documentation /
# the contract test; the scan itself covers all of src/lawvm/finland.
SCOPE_SOURCE_COMPARISON_FILES: tuple[str, ...] = (
    "src/lawvm/finland/apply_structure_ops.py",
    "src/lawvm/finland/frontend_compile.py",
    "src/lawvm/finland/scope.py",
    "src/lawvm/finland/standalone_targets.py",
)


def _is_str_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_str_literal_collection(node: ast.expr) -> bool:
    """True for a set/tuple/list/frozenset({...}) all of whose elements are str
    literals (and non-empty)."""
    elts: list[ast.expr] | None = None
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        elts = list(node.elts)
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"frozenset", "set", "tuple", "list"}
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.Set, ast.Tuple, ast.List))
    ):
        elts = list(node.args[0].elts)
    if not elts:
        return False
    return all(_is_str_literal(e) for e in elts)


def _is_scope_source_attr(node: ast.expr) -> bool:
    """True if ``node`` is ``<scope-related>.source``."""
    if not isinstance(node, ast.Attribute) or node.attr != _SCOPE_SOURCE_ATTR:
        return False
    receiver = node.value
    if isinstance(receiver, ast.Name):
        return receiver.id in _SCOPE_RECEIVER_HINTS
    # `something.scope_confidence.source` etc. — the immediate receiver is an
    # attribute whose attr is a scope hint.
    if isinstance(receiver, ast.Attribute):
        return receiver.attr in _SCOPE_RECEIVER_HINTS
    return False


def scan_file_scope_source_compares(rel_path: str, text: str) -> list[dict[str, Any]]:
    """Find every raw-string scope-source compare / helper call in one file."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        # (1) Compare: <scope>.source == "lit" / in {"a","b"} / != "lit"
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            has_scope_source = any(_is_scope_source_attr(op) for op in operands)
            has_raw_string = any(
                _is_str_literal(op) or _is_str_literal_collection(op)
                for op in operands
            )
            if has_scope_source and has_raw_string:
                records.append(
                    {
                        "file": rel_path,
                        "line": node.lineno,
                        "kind": "compare",
                    }
                )
        # (2) group_has_scope_source(..., "<literal>")
        if isinstance(node, ast.Call):
            func = node.func
            fname = ""
            if isinstance(func, ast.Name):
                fname = func.id
            elif isinstance(func, ast.Attribute):
                fname = func.attr
            if fname == _GROUP_SCOPE_SOURCE_HELPER:
                if any(_is_str_literal(a) for a in node.args):
                    records.append(
                        {
                            "file": rel_path,
                            "line": node.lineno,
                            "kind": "group_has_scope_source_literal",
                        }
                    )
    return records


def scan_scope_source_ratchet(repo_root: Path | None = None) -> dict[str, Any]:
    """Compute the raw-string scope-source ratchet state.

    Returns per-file raw-string compare counts (the monotone quantity), the
    total, and every record.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    for rel in _iter_python_files(root, *_SCOPE_SOURCE_SCAN_ROOTS):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_scope_source_compares(rel, text))

    raw_string_counts: Counter[str] = Counter()
    for rec in records:
        raw_string_counts[rec["file"]] += 1

    return {
        "raw_string_counts": dict(sorted(raw_string_counts.items())),
        "total_raw_string": sum(raw_string_counts.values()),
        "records": records,
    }


SCOPE_SOURCE_BASELINE_PATH = Path("tests/data/scope_source_ratchet_baseline.json")


def scope_source_baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_scope_source_ratchet(repo_root)
    return {
        "_doc": (
            "Monotone raw-string scope-source ratchet baseline (Audit D). Counts "
            "scope `.source` comparisons against a str literal / collection of str "
            "literals (and group_has_scope_source raw-string calls) instead of a "
            "ScopeResolutionSource enum member. The 4 migrated comparison files MUST "
            "be 0; the producer files carry the fenced residue. Per-file counts may "
            "only fall, never rise; a fall must be committed (regenerate with "
            "`uv run python scripts/inventory_architecture_smells.py --ratchet scope "
            "--update-baseline`). See tests/test_scope_source_ratchet.py."
        ),
        "total_raw_string": state["total_raw_string"],
        "raw_string_counts": state["raw_string_counts"],
        "comparison_files": list(SCOPE_SOURCE_COMPARISON_FILES),
    }


# ===========================================================================
# Ratchet — author-set replay authority at projection (audit row PROJ-02)
# ===========================================================================
#
# PROJECTION plane invariant (LAWVM_AUDIT_INVARIANT_REGISTRY.md §7 / §3.E,
# core/stage_result.py AuthoritySurface): a projection row is NON-AUTHORITATIVE by
# construction. ``replay_authorized`` (the legal-state replay-authority field) may
# be granted ONLY by an explicit, granting ``ExecutionAuthorization`` carrier — it
# may NEVER be author-set to True on any other row / dataclass / dict at projection
# time. The fi_refs deterministic export was the canonical violation (it stamped
# ``replay_authorized: True`` on every deterministic extraction row via
# ``_DETERMINISTIC_ROW_EXTRAS``; fixed in 2f7f30e6 to ``False`` + a positive
# ``deterministic_extraction`` surface fact). This scan GENERALIZES that fix
# tree-wide: it finds every site that author-sets ``replay_authorized`` to a TRUTHY
# literal and classifies it as
#   * ALLOWED   — the assignment is a keyword inside an ``ExecutionAuthorization(...)``
#                 construction (the sole legitimate authority-grant carrier), or
#   * VIOLATION — a truthy ``replay_authorized`` set ANYWHERE else (a dataclass
#                 default, a dict literal, a kwarg to a non-ExecutionAuthorization
#                 constructor) — an author-set authority on a projection row.
# Comparisons (``replay_authorized == True``), reads, and ``False``/falsy literals
# are NOT flagged; only a truthy AUTHOR-SET is. The committed baseline is 0
# violations (the firewall is type-enforced by the FrontierWorkItem validator + the
# legal_surface_graph assembler today); the scan fences any NEW author-set truthy
# ``replay_authorized`` crossing under ``src/lawvm`` so the firewall cannot regress.
_REPLAY_AUTHORITY_FIELD = "replay_authorized"
_AUTHORITY_GRANT_CARRIER = "ExecutionAuthorization"
_PROJECTION_AUTHORITY_SCAN_ROOTS = ("src/lawvm",)


def _is_truthy_literal(node: ast.expr) -> bool:
    """True for an author-set TRUTHY constant (``True`` / ``1``).

    A ``False`` / ``0`` / ``None`` literal is the surface-truthful default and is
    NOT an author-set authority claim, so it is not flagged. A non-literal value
    (a derived expression / name) is also not flagged here — the firewall is about
    a hard-coded author-set authority, not a derived one (a derived True must come
    through an ``ExecutionAuthorization`` carrier, which this scan allows).
    """
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if value is True:
        return True
    return isinstance(value, int) and not isinstance(value, bool) and value != 0


def _call_is_authority_grant_carrier(node: ast.Call) -> bool:
    """True if ``node`` constructs an ``ExecutionAuthorization`` (the grant carrier)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == _AUTHORITY_GRANT_CARRIER
    if isinstance(func, ast.Attribute):
        return func.attr == _AUTHORITY_GRANT_CARRIER
    return False


def scan_file_projection_authority(rel_path: str, text: str) -> list[dict[str, Any]]:
    """Find every author-set ``replay_authorized=<truthy>`` site in one file.

    Two author-set shapes are caught:
      (1) a keyword ``replay_authorized=True`` in a CALL (constructor / factory);
      (2) a dict entry ``"replay_authorized": True`` in a dict literal.
    A class-body / module default ``replay_authorized: bool = True`` (AnnAssign /
    Assign to a truthy literal) is also caught (it author-sets the default).

    Each keyword (1) site is classified ALLOWED iff its enclosing call constructs
    an ``ExecutionAuthorization`` (the legitimate grant carrier). Dict-literal and
    default sites are ALWAYS violations when truthy — a projection row's dict /
    default authority is never the grant carrier.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    records: list[dict[str, Any]] = []

    # (1) keyword in a call: classify by the enclosing call's callee.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        allowed = _call_is_authority_grant_carrier(node)
        for kw in node.keywords:
            if (
                kw.arg == _REPLAY_AUTHORITY_FIELD
                and _is_truthy_literal(kw.value)
                and not allowed
            ):
                records.append(
                    {
                        "file": rel_path,
                        "line": getattr(kw.value, "lineno", node.lineno),
                        "kind": "keyword_non_grant_carrier",
                    }
                )

    # (2) dict literal entry "replay_authorized": True.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and key.value == _REPLAY_AUTHORITY_FIELD
                and _is_truthy_literal(value)
            ):
                records.append(
                    {
                        "file": rel_path,
                        "line": getattr(value, "lineno", node.lineno),
                        "kind": "dict_literal",
                    }
                )

    # (3) class-body / module default: replay_authorized[: bool] = True.
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Name)
                and target.id == _REPLAY_AUTHORITY_FIELD
                and node.value is not None
                and _is_truthy_literal(node.value)
            ):
                records.append(
                    {
                        "file": rel_path,
                        "line": node.lineno,
                        "kind": "default_assignment",
                    }
                )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == _REPLAY_AUTHORITY_FIELD
                    and _is_truthy_literal(node.value)
                ):
                    records.append(
                        {
                            "file": rel_path,
                            "line": node.lineno,
                            "kind": "default_assignment",
                        }
                    )
    return records


def scan_projection_authority_ratchet(repo_root: Path | None = None) -> dict[str, Any]:
    """Compute the author-set-replay-authority-at-projection ratchet state.

    Returns per-file VIOLATION counts (the monotone quantity, baseline 0), the
    total, and every record. A violation is a truthy ``replay_authorized`` set
    outside an ``ExecutionAuthorization`` grant carrier.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    for rel in _iter_python_files(root, *_PROJECTION_AUTHORITY_SCAN_ROOTS):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_projection_authority(rel, text))

    violation_counts: Counter[str] = Counter()
    for rec in records:
        violation_counts[rec["file"]] += 1

    return {
        "violation_counts": dict(sorted(violation_counts.items())),
        "total_violations": sum(violation_counts.values()),
        "records": records,
    }


PROJECTION_AUTHORITY_BASELINE_PATH = Path(
    "tests/data/projection_authority_ratchet_baseline.json"
)


def projection_authority_baseline_snapshot(
    repo_root: Path | None = None,
) -> dict[str, Any]:
    state = scan_projection_authority_ratchet(repo_root)
    return {
        "_doc": (
            "Monotone author-set-replay-authority-at-projection ratchet baseline "
            "(audit row PROJ-02). Counts truthy `replay_authorized` author-set "
            "OUTSIDE an ExecutionAuthorization grant carrier (a dict literal, a "
            "non-grant constructor kwarg, or a class/module default) — a projection "
            "row claiming legal-state replay authority it cannot hold. The firewall "
            "is type-enforced today (FrontierWorkItem validator + legal_surface_graph "
            "assembler both raise), so the baseline is 0 and may only stay 0; a new "
            "crossing fails CI. Regenerate with `uv run python "
            "scripts/inventory_architecture_smells.py --ratchet projection "
            "--update-baseline`. See tests/test_projection_author_set_authority.py."
        ),
        "total_violations": state["total_violations"],
        "violation_counts": state["violation_counts"],
    }


# ===========================================================================
# Ratchet — source-witness liveness (StageResult WAIST #1)
# ===========================================================================
#
# The content-addressed read witnesses on the corpus store
# (``read_source_witness`` / ``read_amendment_witness`` / ``read_oracle_witness``)
# build a sha256 ``DigestWitness`` over the actual bytes. They were once SEVERED
# (only tests called them), the recurring "witness built-then-severed" failure
# class. This ratchet asserts each named witness method has >= 1 NON-TEST caller
# (a method-name call ``<recv>.<method>(...)`` in a non-test src file) and is
# MONOTONE: the per-method non-test caller count may not fall below the committed
# baseline (severance cannot regress). Defining methods are not counted as
# callers.
_WITNESS_LIVENESS_METHODS: tuple[str, ...] = (
    "read_source_witness",
    "read_amendment_witness",
    "read_oracle_witness",
)
_WITNESS_LIVENESS_SCAN_ROOTS = ("src/lawvm",)


def scan_file_witness_callers(rel_path: str, text: str) -> list[dict[str, Any]]:
    """Find every non-defining call to a tracked witness method in one file.

    A caller is a ``Call`` whose func is an attribute access naming one of the
    tracked methods (``corpus.read_source_witness(...)`` etc.). ``FunctionDef``
    nodes that DEFINE the method are not callers.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _WITNESS_LIVENESS_METHODS:
            records.append(
                {"file": rel_path, "line": node.lineno, "method": func.attr}
            )
    return records


def scan_witness_liveness_ratchet(repo_root: Path | None = None) -> dict[str, Any]:
    """Compute per-method NON-TEST caller counts for the witness methods.

    ``_iter_python_files`` already excludes test files, so every record here is a
    production (non-test) caller — the load-bearing quantity.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    for rel in _iter_python_files(root, *_WITNESS_LIVENESS_SCAN_ROOTS):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_witness_callers(rel, text))

    caller_counts: Counter[str] = Counter()
    for rec in records:
        caller_counts[rec["method"]] += 1
    # Ensure every tracked method appears (0 if severed) so the ratchet sees it.
    nontest_caller_counts = {
        method: caller_counts.get(method, 0) for method in _WITNESS_LIVENESS_METHODS
    }

    return {
        "nontest_caller_counts": dict(sorted(nontest_caller_counts.items())),
        "total_nontest_callers": sum(nontest_caller_counts.values()),
        "records": records,
    }


WITNESS_LIVENESS_BASELINE_PATH = Path("tests/data/source_witness_liveness_baseline.json")


def witness_liveness_baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_witness_liveness_ratchet(repo_root)
    return {
        "_doc": (
            "Monotone source-witness liveness ratchet (StageResult WAIST #1). "
            "Counts NON-TEST callers of read_source_witness / read_amendment_witness "
            "/ read_oracle_witness on the corpus store. The two witnesses un-severed "
            "by WAIST #1 (read_source_witness, read_amendment_witness) MUST stay "
            ">= 1 (a production consumer reads them; severance cannot regress). "
            "read_oracle_witness is still severed (its consumer is an oracle-read "
            "path outside the source-identity locus, a deferred follow-up) so its "
            "floor is its committed count. ALL per-method counts are monotone: they "
            "may only rise or hold relative to the committed baseline; regenerate "
            "with `uv run python scripts/inventory_architecture_smells.py --ratchet "
            "witness --update-baseline`. See "
            "tests/test_source_witness_liveness_ratchet.py."
        ),
        "total_nontest_callers": state["total_nontest_callers"],
        "nontest_caller_counts": state["nontest_caller_counts"],
    }


# ===========================================================================
# CLI
# ===========================================================================


def write_witness_liveness_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / WITNESS_LIVENESS_BASELINE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = witness_liveness_baseline_snapshot(root)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_authority_boundary_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / AUTHORITY_BOUNDARY_BASELINE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = authority_boundary_baseline_snapshot(root)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_scope_source_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / SCOPE_SOURCE_BASELINE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = scope_source_baseline_snapshot(root)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_projection_authority_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / PROJECTION_AUTHORITY_BASELINE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = projection_authority_baseline_snapshot(root)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate architecture-coherence smell inventories / ratchets."
    )
    parser.add_argument(
        "--ratchet",
        choices=("authority", "scope", "witness", "projection", "all"),
        default="all",
        help="Which ratchet to scan / update.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Regenerate the selected ratchet baseline(s) under tests/data/ from the "
            "current tree. Only ever commit a baseline whose counts are <= the "
            "committed one."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json",),
        default="json",
        help="Output format (json).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.update_baseline:
        if args.ratchet in {"authority", "all"}:
            out = write_authority_boundary_baseline()
            snap = json.loads(out.read_text(encoding="utf-8"))
            print(f"wrote {out} (total_untyped={snap['total_untyped']})")
        if args.ratchet in {"scope", "all"}:
            out = write_scope_source_baseline()
            snap = json.loads(out.read_text(encoding="utf-8"))
            print(f"wrote {out} (total_raw_string={snap['total_raw_string']})")
        if args.ratchet in {"witness", "all"}:
            out = write_witness_liveness_baseline()
            snap = json.loads(out.read_text(encoding="utf-8"))
            print(f"wrote {out} (total_nontest_callers={snap['total_nontest_callers']})")
        if args.ratchet in {"projection", "all"}:
            out = write_projection_authority_baseline()
            snap = json.loads(out.read_text(encoding="utf-8"))
            print(f"wrote {out} (total_violations={snap['total_violations']})")
        return 0

    payload: dict[str, Any] = {}
    if args.ratchet in {"authority", "all"}:
        payload["authority_boundary"] = scan_authority_boundary_ratchet()
    if args.ratchet in {"scope", "all"}:
        payload["scope_source"] = scan_scope_source_ratchet()
    if args.ratchet in {"witness", "all"}:
        payload["witness_liveness"] = scan_witness_liveness_ratchet()
    if args.ratchet in {"projection", "all"}:
        payload["projection_authority"] = scan_projection_authority_ratchet()
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
