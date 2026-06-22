"""Bounded AST inventories for the two control-flow audit ratchets (registry lane L2a).

Hosts the reusable scans for two monotone ratchet gates over
``src/lawvm/{core,finland}`` (non-test python), mirroring the proven
``scripts/inventory_parser_smells.py`` regex-ratchet shape (per-file un-waived
count, committed baseline JSON that may only fall):

  * EV-08 — fail-loud ratchet (``scan_fail_loud_ratchet``): bans a NEW non-test
    ``except Exception`` / bare ``except:`` whose handler SILENTLY swallows the
    error (returns/continues/passes a literal fallback) without re-raising or
    emitting a named typed diagnostic/finding. A handler that re-raises (any
    ``raise``), or appends/constructs a typed *Finding/Diagnostic/Failure/...*
    on or near the handler, is owned and NOT counted. An inline
    ``# lawvm-failloud: <reason>`` waiver (on the ``except`` line or the line
    above) clears a site.

  * OV-03 — confidence-as-control ratchet (``scan_confidence_control_ratchet``):
    bans a NEW ``if`` / ternary whose test compares a ``confidence`` /
    ``certified`` / ``selected``-named value against a numeric or string LITERAL
    threshold (``conf > 0.8``, ``certified == "high"``). A typed-enum branch
    (``x.confidence != SomeEnum.MEMBER``, ``status == Rail.SELECTED``) is the
    sanctioned form and is NOT counted — only a raw float/str confidence
    threshold deciding flow is the violation. An inline
    ``# lawvm-confidence-control: <reason>`` waiver clears a site.

Both scans are pure AST visitors (no regex over prose; patterns compiled at
module scope), bounded by the file set, and deterministic. They are imported by
``tests/test_fail_loud_ratchet.py`` and ``tests/test_confidence_control_ratchet.py``.
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

from collections.abc import Iterable


# ===========================================================================
# Shared file enumeration (mirrors inventory_parser_smells.iter_scanned_files,
# but with NO pre-clear map: these are language-level control-flow bans that
# apply to every non-test core/finland module).
# ===========================================================================

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = (
    Path("src/lawvm/core"),
    Path("src/lawvm/finland"),
)


def _rel_posix(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def iter_scanned_files(repo_root: Path | None = None) -> list[str]:
    """All ``src/lawvm/{core,finland}`` non-test python files (sorted, rel posix)."""
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    scanned: list[str] = []
    for scan_root in _SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        for pyfile in sorted(base.rglob("*.py")):
            rel = _rel_posix(pyfile, root)
            if "/tests/" in f"/{rel}" or pyfile.name.startswith("test_"):
                continue
            scanned.append(rel)
    return scanned


def _waiver_on(lines: list[str], lineno: int, marker: str) -> bool:
    """A site is waived if its own line, or the line directly above it, carries
    the ``marker`` (a ``# lawvm-...:`` token) inside the line's COMMENT portion.
    ``lineno`` is 1-based; ``marker`` itself begins with ``#`` so an incidental
    code-string substring without a real comment does not waive."""
    idx = lineno - 1
    for probe in (idx, idx - 1):
        if 0 <= probe < len(lines):
            line = lines[probe]
            hash_pos = line.find("#")
            if hash_pos != -1 and marker in line[hash_pos:]:
                return True
    return False


# ===========================================================================
# EV-08 — fail-loud ratchet
# ===========================================================================

FAIL_LOUD_WAIVER = "# lawvm-failloud:"

# Identifier sub-strings that, when constructed or called inside a handler body,
# count as "emitting a named typed diagnostic/finding" (ownership of the error).
_DIAGNOSTIC_NAME_TOKENS: tuple[str, ...] = (
    "Finding",
    "Diagnostic",
    "Failure",
    "Residual",
    "Residue",
    "Pathology",
    "Disagreement",
    "Violation",
    "Warning",
    "Witness",
    "Receipt",
    "Rejected",
)
# Lowercase method/attr tokens that emit/record a typed diagnostic when called.
_DIAGNOSTIC_CALL_TOKENS: tuple[str, ...] = (
    "diagnostic",
    "finding",
    "failure",
    "residual",
    "pathology",
    "warn",
    "record_",
    "emit_",
    "append_",
    "_append",
    "report",
)


def _name_of_func(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _handler_is_owned(handler: ast.ExceptHandler) -> bool:
    """A broad handler is OWNED (not a silent swallow) if its body either
    re-raises (any ``raise``, incl. ``raise`` bare or ``raise X``) OR constructs
    / calls a named typed diagnostic/finding/failure/residual.

    Conservative / sound-leaning toward NOT flagging an owned handler: any
    ``raise`` anywhere in the body, any constructor whose name carries a
    diagnostic token, or any call/attribute whose terminal name carries a
    diagnostic-emit token, marks the handler owned.
    """
    for node in ast.walk(handler):
        # Do not look past the handler into nested handlers' bodies for raises
        # in the *inner* except — ast.walk over THIS handler includes its own
        # body and any nested try; that is acceptable (a re-raise anywhere in the
        # handler's reachable body is genuine ownership).
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            fname = _name_of_func(node.func)
            if any(tok in fname for tok in _DIAGNOSTIC_NAME_TOKENS):
                return True
            low = fname.lower()
            if any(tok in low for tok in _DIAGNOSTIC_CALL_TOKENS):
                return True
        # Bare reference to a diagnostic-typed constructor passed around, e.g.
        # `failures.append(HEAcquisitionFailure(...))` is caught by the Call arm
        # above; a constructor used as `x = SomeFailure` (rare) is caught here.
        if isinstance(node, ast.Name) and any(
            tok in node.id for tok in _DIAGNOSTIC_NAME_TOKENS
        ):
            return True
    return False


def _handler_is_broad(handler: ast.ExceptHandler) -> bool:
    """True for ``except:`` (bare) or ``except Exception`` / ``except BaseException``.

    A narrowly-typed handler (``except ValueError``, ``except (KeyError, OSError)``)
    is NOT broad — only the catch-all shapes are banned by EV-08.
    """
    exc = handler.type
    if exc is None:
        return True  # bare except:
    names: list[str] = []
    targets = exc.elts if isinstance(exc, ast.Tuple) else [exc]
    for t in targets:
        nm = _name_of_func(t) if isinstance(t, ast.Attribute) else (
            t.id if isinstance(t, ast.Name) else ""
        )
        names.append(nm)
    return any(nm in {"Exception", "BaseException"} for nm in names)


def scan_file_fail_loud_sites(rel_path: str, text: str) -> list[dict[str, Any]]:
    """One record per broad-except handler in a file; ``waived``/``owned`` flags.

    A handler counts toward the ratchet only when it is broad, NOT owned, and NOT
    waived. Returns every broad handler (owned/waived included) so guard-liveness
    tests can assert the classification directly.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _handler_is_broad(node):
            continue
        owned = _handler_is_owned(node)
        waived = _waiver_on(lines, node.lineno, FAIL_LOUD_WAIVER)
        records.append(
            {
                "file": rel_path,
                "line": node.lineno,
                "bare": node.type is None,
                "owned": owned,
                "waived": waived,
                "counts": (not owned) and (not waived),
                "snippet": (
                    lines[node.lineno - 1].strip()
                    if 0 <= node.lineno - 1 < len(lines)
                    else ""
                ),
            }
        )
    return records


def scan_fail_loud_ratchet(repo_root: Path | None = None) -> dict[str, Any]:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    for rel in iter_scanned_files(root):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_fail_loud_sites(rel, text))

    counts: Counter[str] = Counter()
    for rec in records:
        if rec["counts"]:
            counts[rec["file"]] += 1
    return {
        "unwaived_counts": dict(sorted(counts.items())),
        "total_unwaived": sum(counts.values()),
        "records": records,
        "scanned_file_count": len(iter_scanned_files(root)),
    }


# ===========================================================================
# OV-03 — confidence-as-control ratchet
# ===========================================================================

CONFIDENCE_CONTROL_WAIVER = "# lawvm-confidence-control:"

# A value is "confidence-shaped" when a name/attribute in the comparison carries
# one of these tokens. Typed-enum comparisons are excluded by _is_enum_operand.
_CONFIDENCE_NAME_TOKENS: tuple[str, ...] = (
    "confidence",
    "certified",
    "certainty",
)
# `selected` is included but is by far the noisiest token (selected_version,
# selected_target, selected_lane ...). It only counts when compared to a STRING
# or NUMERIC literal threshold, never against an enum/None/bool — see the
# literal-threshold gate below.
_SELECTED_NAME_TOKENS: tuple[str, ...] = ("selected",)

# Ordinal threshold operators — a confidence-shaped value compared with one of
# these against a NUMERIC literal is the raw "conf > 0.8 decides apply" shape.
_ORDINAL_OPS = (ast.Gt, ast.GtE, ast.Lt, ast.LtE)
# Equality operators only count as a confidence threshold when the literal is a
# GRADED confidence string ("high"/"medium"/"low"/...), not an arbitrary
# categorical label (a lane name, a determinism category) which is the
# sanctioned typed-branch equivalent of an enum member.
_EQUALITY_OPS = (ast.Eq, ast.NotEq)
_GRADED_CONFIDENCE_STRINGS: frozenset[str] = frozenset(
    {
        "high",
        "medium",
        "med",
        "low",
        "very_high",
        "very_low",
        "strong",
        "weak",
        "certain",
        "uncertain",
        "probable",
        "likely",
        "unlikely",
        "confident",
    }
)


def _operand_name_tokens(node: ast.AST) -> str:
    """Lowercased concatenation of every Name.id / Attribute.attr in an operand
    expression (so ``op.scope_confidence`` -> '... scope_confidence ...')."""
    parts: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            parts.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            parts.append(sub.attr)
    return " ".join(parts).lower()


def _is_confidence_operand(node: ast.AST) -> tuple[bool, bool]:
    """(is_confidence_shaped, is_selected_only).

    ``is_selected_only`` flags the noisier ``selected`` family so the caller can
    require a literal threshold for it (an enum/bool ``selected`` branch is fine).
    """
    blob = _operand_name_tokens(node)
    conf = any(tok in blob for tok in _CONFIDENCE_NAME_TOKENS)
    sel = any(tok in blob for tok in _SELECTED_NAME_TOKENS)
    return (conf or sel, sel and not conf)


def _numeric_literal(node: ast.AST) -> bool:
    """True if ``node`` is a numeric (int/float, NOT bool) literal threshold."""
    return isinstance(node, ast.Constant) and isinstance(
        node.value, (int, float)
    ) and not isinstance(node.value, bool)


def _graded_confidence_string(node: ast.AST) -> bool:
    """True if ``node`` is a STRING literal naming a graded confidence level
    (``"high"``/``"low"``/...), the string-confidence-threshold shape — as opposed
    to an arbitrary categorical label (a lane name, ``"deterministic"``), which is
    the sanctioned discrete typed-branch equivalent of an enum member."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    return node.value.strip().lower() in _GRADED_CONFIDENCE_STRINGS


def _compare_is_confidence_threshold(cmp: ast.Compare) -> bool:
    """A ``Compare`` is a confidence-as-control violation when a confidence-shaped
    operand is either

      * ordinally compared (``>``, ``>=``, ``<``, ``<=``) against a NUMERIC
        literal (``conf > 0.8`` deciding flow), OR
      * equality-compared (``==``, ``!=``) against a GRADED confidence STRING
        (``certified == "high"``).

    A categorical equality against an arbitrary discrete label (a lane name, a
    determinism category) and any enum / None / bool / variable RHS is the
    sanctioned typed branch and is NOT a violation.
    """
    operands = [cmp.left, *cmp.comparators]
    # (is_confidence_shaped, is_selected_only) per operand. A selected-only
    # operand (selected_count/selected_address_count/selected_lane) is a count /
    # index / categorical label, NOT a graded confidence — it can only be a
    # violation under the graded-confidence-STRING equality arm, never an ordinal
    # numeric threshold (`selected_count < 0` is a count guard, not a threshold).
    flags = [_is_confidence_operand(o) for o in operands]
    if not any(conf for conf, _sel in flags):
        return False
    # cmp.ops[k] relates operands[k] and operands[k+1].
    for k, op in enumerate(cmp.ops):
        for conf_i, lit_i in ((k, k + 1), (k + 1, k)):
            conf, sel_only = flags[conf_i]
            if not conf:
                continue
            cand = operands[lit_i]
            if (
                isinstance(op, _ORDINAL_OPS)
                and not sel_only
                and _numeric_literal(cand)
            ):
                return True
            if isinstance(op, _EQUALITY_OPS) and _graded_confidence_string(cand):
                return True
    return False


class _ConfidenceControlVisitor(ast.NodeVisitor):
    """Collect (lineno) of every if/while-test and ternary that is a confidence
    threshold branch. Bounded single AST walk."""

    def __init__(self) -> None:
        self.hits: list[int] = []

    def _check_test(self, test: ast.AST) -> None:
        # A test may be a bare Compare or a BoolOp combining several.
        for node in ast.walk(test):
            if isinstance(node, ast.Compare) and _compare_is_confidence_threshold(
                node
            ):
                self.hits.append(node.lineno)

    def visit_If(self, node: ast.If) -> None:
        self._check_test(node.test)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._check_test(node.test)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:  # ternary
        self._check_test(node.test)
        self.generic_visit(node)


def scan_file_confidence_control_sites(
    rel_path: str, text: str
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    visitor = _ConfidenceControlVisitor()
    visitor.visit(tree)
    records: list[dict[str, Any]] = []
    for lineno in sorted(set(visitor.hits)):
        waived = _waiver_on(lines, lineno, CONFIDENCE_CONTROL_WAIVER)
        records.append(
            {
                "file": rel_path,
                "line": lineno,
                "waived": waived,
                "counts": not waived,
                "snippet": (
                    lines[lineno - 1].strip()
                    if 0 <= lineno - 1 < len(lines)
                    else ""
                ),
            }
        )
    return records


def scan_confidence_control_ratchet(
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    for rel in iter_scanned_files(root):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_confidence_control_sites(rel, text))

    counts: Counter[str] = Counter()
    for rec in records:
        if rec["counts"]:
            counts[rec["file"]] += 1
    return {
        "unwaived_counts": dict(sorted(counts.items())),
        "total_unwaived": sum(counts.values()),
        "records": records,
        "scanned_file_count": len(iter_scanned_files(root)),
    }


# ===========================================================================
# Baselines
# ===========================================================================

FAIL_LOUD_BASELINE_PATH = Path("tests/data/fail_loud_ratchet_baseline.json")
CONFIDENCE_CONTROL_BASELINE_PATH = Path(
    "tests/data/confidence_control_ratchet_baseline.json"
)

_FAIL_LOUD_DOC = (
    "Monotone fail-loud ratchet baseline (registry EV-08). Counts UN-waived "
    "broad `except Exception` / bare `except:` handlers in non-test "
    "src/lawvm/{core,finland} python whose body SILENTLY swallows the error "
    "(no re-raise, no named typed diagnostic/finding emit). Per-file 'unwaived' "
    "counts may only FALL, never rise; a fall must be committed (regenerate with "
    "`uv run python scripts/inventory_control_flow_smells.py --ratchet failloud "
    "--update-baseline`). Waive an intentional swallow with "
    "`# lawvm-failloud: <reason>`. See tests/test_fail_loud_ratchet.py."
)

_CONFIDENCE_CONTROL_DOC = (
    "Monotone confidence-as-control ratchet baseline (registry OV-03). Counts "
    "UN-waived if/while/ternary tests that compare a confidence/certified/"
    "certainty/selected-named value against a raw numeric or string LITERAL "
    "threshold (a typed-enum / None / bool branch is sanctioned and NOT counted). "
    "Per-file 'unwaived' counts may only FALL; a fall must be committed "
    "(regenerate with `uv run python scripts/inventory_control_flow_smells.py "
    "--ratchet confidence --update-baseline`). Waive with "
    "`# lawvm-confidence-control: <reason>`. See "
    "tests/test_confidence_control_ratchet.py."
)


def fail_loud_baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_fail_loud_ratchet(repo_root)
    return {
        "_doc": _FAIL_LOUD_DOC,
        "total_unwaived": state["total_unwaived"],
        "unwaived_counts": state["unwaived_counts"],
    }


def confidence_control_baseline_snapshot(
    repo_root: Path | None = None,
) -> dict[str, Any]:
    state = scan_confidence_control_ratchet(repo_root)
    return {
        "_doc": _CONFIDENCE_CONTROL_DOC,
        "total_unwaived": state["total_unwaived"],
        "unwaived_counts": state["unwaived_counts"],
    }


def _write_baseline(path: Path, snapshot: dict[str, Any], repo_root: Path) -> Path:
    out_path = repo_root / path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_fail_loud_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    return _write_baseline(
        FAIL_LOUD_BASELINE_PATH, fail_loud_baseline_snapshot(root), root
    )


def write_confidence_control_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    return _write_baseline(
        CONFIDENCE_CONTROL_BASELINE_PATH,
        confidence_control_baseline_snapshot(root),
        root,
    )


def _top_offenders(counts: dict[str, int], n: int = 10) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control-flow audit ratchet inventories (EV-08, OV-03)."
    )
    parser.add_argument(
        "--ratchet",
        choices=("failloud", "confidence"),
        required=True,
        help="Which ratchet to operate on.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate the committed baseline JSON for the chosen ratchet.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.ratchet == "failloud":
        state = scan_fail_loud_ratchet()
        if args.update_baseline:
            out = write_fail_loud_baseline()
            print(f"wrote {out} (total_unwaived={state['total_unwaived']})")
            return 0
    else:
        state = scan_confidence_control_ratchet()
        if args.update_baseline:
            out = write_confidence_control_baseline()
            print(f"wrote {out} (total_unwaived={state['total_unwaived']})")
            return 0
    print(
        json.dumps(
            {
                "ratchet": args.ratchet,
                "total_unwaived": state["total_unwaived"],
                "top_offenders": _top_offenders(state["unwaived_counts"]),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
