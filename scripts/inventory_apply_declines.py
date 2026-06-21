"""Inventory apply-path op-handler declines and their typed witness status.

This script hosts the reusable scan for the *apply-decline ratchet* gate
("Gate 3", ``tests/test_apply_decline_ratchet.py``). It closes the silent
legal-state / apply divergence class that three successive re-audits kept finding
fresh tiers of (``notes_internal/EXIT_REAUDIT.md``, ``_2.md``, ``_3.md``):

    an apply-path op-handler that DECLINES an authored op (returns the unmodified
    ``state`` ReplayState — a no-op) WITHOUT first appending a typed pathology /
    finding to a production-visible ledger.

A decline of this shape leaves the authored op unaccounted-for on every
production surface: the op is silently dropped, the legal state diverges from
the source, and nothing on the certificate / replay-findings ledger records it.
The recurring fix (Gates witnessed at apply_item_ops.py ~793/1732/1744/1765 and
apply_structure_ops.py container-otsikko / scoped-insert / unhandled-op sites)
is to append a typed ``SourcePathology`` (or ``Finding`` / ``CompileFailure``)
to ``source_pathologies_out`` / ``findings_out`` / ``failed_ops_out`` BEFORE the
``return state`` so the decline is witnessed.

What the detector flags (Part 1)
--------------------------------
Every ``return state`` statement in a scanned apply file, where ``state`` is the
bare ReplayState parameter of the enclosing function (an op-handler) — i.e. the
handler returns the input tree UNCHANGED. Each such decline is classified:

  - ``witnessed``   — a typed witness sink ``*_out.append(...)`` (one of
    ``source_pathologies_out`` / ``findings_out`` / ``failed_ops_out``) appears
    as a statement that dominates the ``return state`` on its AST path (a
    preceding sibling at the return's own block level or any enclosing block).
  - ``unwitnessed`` — no such emit dominates the return: a SILENT decline (the
    failure class this gate forbids growing).

Why ``return state`` specifically (scope rationale)
---------------------------------------------------
``return state`` (the literal unmodified ReplayState parameter) is the
unambiguous decline shape, and it is exactly the tier the re-audits closed.
It is distinguished from the two look-alikes the brief calls out:

  - dispatch-protocol ``return None`` ("not me, try the next handler") — NOT a
    drop; an Optional-returning handler signals non-applicability with ``None``,
    never by returning ``state``. We do not treat ``return None`` as a decline.
  - a real IR change ``return state.with_ir(...)`` /
    ``return _with_preserved_provision_index(state, ...)`` /
    ``return ReplayState(...)`` — the op WAS applied; the returned value is not
    the bare ``state`` name, so it is not a decline.

Loop ``continue`` op-drops are deliberately OUT of scope: in these files
``continue`` is overwhelmingly benign child-iteration filtering, and AST-level
discrimination of "drops an authored op" vs "skips a non-matching child" cannot
be done without injecting false positives — which would defeat the gate's
zero-false-positive contract. The ``return state`` shape is the witnessed tier;
``continue`` op-drops, if ever found, are a separate future gate.

The committed baseline (``tests/data/apply_decline_ratchet_baseline.json``) is a
monotone ratchet: the number of UN-witnessed declines may only fall, never rise.
A NEW un-witnessed ``return state`` fails CI.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]

# Apply-path modules whose op-handlers fold authored ops into the live tree are
# discovered by globbing ``src/lawvm/finland/apply_*.py`` (see
# ``discover_apply_files``) so a NEW apply file is auto-scanned — coverage cannot
# silently shrink by adding a file the scan forgot. The glob directory:
_APPLY_FILES_DIR = Path("src/lawvm/finland")
_APPLY_FILES_GLOB = "apply_*.py"

# Deliberate, documented exclusions from the glob. Each entry must name an
# ``apply_*.py`` that genuinely hosts no op-handler ``return state`` decline (it
# is an orchestrator / pure-helper module). Removing an entry only ever WIDENS
# coverage; adding one is a conscious act recorded here. (Currently empty — every
# discovered ``apply_*.py`` is scanned.)
EXCLUDED_APPLY_FILES: frozenset[str] = frozenset()

# Typed witness sinks: appending to one of these reaches a production-visible
# ledger (source_pathologies -> APPLY.SOURCE_PATHOLOGY_DETECTED -> certificate;
# findings -> PhaseResult finding ledger; failed_ops -> APPLY.FAILED_OPERATION).
WITNESS_SINK_NAMES: frozenset[str] = frozenset(
    {
        "source_pathologies_out",
        "findings_out",
        "failed_ops_out",
        "replay_findings",
    }
)

# Mutation-event emit helpers: a call to one of these records the op's
# disposition (a ``skipped`` / ``failed`` mutation event carrying a
# ``reason_code`` / ``failure_reason``) on the production mutation-accounting
# ledger (``check_apply_mutation_accounting`` -> replay findings). A decline that
# stamps a mutation event immediately before ``return state`` is NOT a silent
# drop — the op is accounted-for. These are recognized as typed witnesses
# alongside the ``*_out.append`` sinks above. (Without this, the relabel/move
# handlers' resolved-but-not-found ``skipped`` declines would read as false
# positives — they already account for the op on the mutation ledger.)
WITNESS_EMIT_CALL_NAMES: frozenset[str] = frozenset(
    {
        "_emit_apply_mutation_event_for_rop",
        "_emit_apply_mutation_event_from_receipt",
        "_emit_apply_mutation_event",
        "_emit_relabel_skip",
        "_emit_legacy_dispatch_fallback_event",
        # ``_fail`` (apply_typed_dispatch._apply_canonical_intent closure) and
        # ``_record_unhandled_typed_target_failed_op`` both append a typed
        # ``FailedOp`` (the latter to ``failed_ops_out``, the former additionally
        # stamps a mutation event) — a production-visible witness for the
        # unhandled-target / unknown-intent ``case`` arms (e.g.
        # apply_typed_dispatch.py:2195/2207).
        "_fail",
        "_record_unhandled_typed_target_failed_op",
    }
)

# Inline waiver: a ``return state`` carrying this comment (on the line or the
# line directly above) is an acknowledged, recorded NON-decline (e.g. a genuine
# satisfied-intent no-op that provably needs no witness). Use sparingly; every
# waiver is recorded debt.
WAIVER_MARKER = "lawvm-apply-decline:"

RATCHET_BASELINE_PATH = Path("tests/data/apply_decline_ratchet_baseline.json")


def _rel_posix(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_bare_state_return(node: ast.Return) -> bool:
    """True iff the statement is ``return state`` (the bare ReplayState name).

    ``return state.with_ir(...)`` (a real IR change) is an ``ast.Call`` /
    ``ast.Attribute`` value, not an ``ast.Name``, so it is excluded. ``return
    None`` (dispatch) has a ``Constant`` / no value. Only the literal name
    ``state`` counts as the decline shape.
    """
    return isinstance(node.value, ast.Name) and node.value.id == "state"


def _function_has_state_param(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff ``state`` is a parameter of the function (it is an op-handler).

    Restricting to functions that take ``state`` as a parameter ensures a
    ``return state`` returns the *input* tree (a decline), not a freshly built
    local also named ``state`` (which would be a real result). In practice every
    apply op-handler takes ``state: ReplayState`` as its first parameter.
    """
    args = fn.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg is not None:
        names.append(args.vararg.arg)
    return "state" in names


def _call_is_witness(call: ast.Call) -> bool:
    """True iff ``call`` is a typed witness: a ``<witness_sink>.append(...)`` or a
    mutation-event emit helper (records the op's disposition on a production
    ledger)."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "append":
        receiver = func.value
        return isinstance(receiver, ast.Name) and receiver.id in WITNESS_SINK_NAMES
    if isinstance(func, ast.Name) and func.id in WITNESS_EMIT_CALL_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in WITNESS_EMIT_CALL_NAMES:
        return True
    return False


def _stmt_is_witness_emit(stmt: ast.stmt) -> bool:
    """True iff ``stmt`` is a typed-witness call statement.

    Covers both a bare witness call expression and an assignment whose value is a
    witness call (e.g. ``finding = findings_out.append(...)`` style — rare, but
    handled).
    """
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _call_is_witness(stmt.value)
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
        return _call_is_witness(stmt.value)
    return False


def _child_stmt_blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """All nested statement-lists of ``stmt``.

    Covers ``if``/``for``/``while``/``with`` ``body``/``orelse``/``finalbody``,
    exception ``handler`` bodies, AND ``match`` ``case`` bodies — the last is the
    G1 blind spot the original walkers missed (``ast.Match.cases[*].body``).
    """
    blocks: list[list[ast.stmt]] = []
    for attr in ("body", "orelse", "finalbody"):
        inner = getattr(stmt, attr, None)
        if isinstance(inner, list):
            blocks.append(inner)
    for handler in getattr(stmt, "handlers", []) or []:
        blocks.append(handler.body)
    if isinstance(stmt, ast.Match):
        for case in stmt.cases:
            blocks.append(case.body)
    return blocks


def _block_always_exits(body: list[ast.stmt]) -> bool:
    """True iff control NEVER falls out the bottom of ``body``.

    A preceding ``if``/``match`` arm whose body always exits (returns / raises /
    continues / breaks on every path) is control-flow DISJOINT from a later
    return — a witness inside it must NOT count as dominating that return (G2).
    Conversely a guard ``if ... is not None: <witness>`` that simply falls
    through DOES reach the return and is a real dominator.

    Conservative: only the unconditional terminal-statement and an
    if/else-both-exit shape are treated as "always exits"; anything else is
    assumed to fall through (so we never wrongly DROP a real witness — we only
    refuse to credit a provably-disjoint branch).
    """
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.If) and last.orelse:
        return _block_always_exits(last.body) and _block_always_exits(last.orelse)
    return False


def _block_contains_dominating_witness(body: list[ast.stmt]) -> bool:
    """True iff a witness emit on ``body``'s own straight-line path dominates the
    bottom of ``body`` (i.e. would dominate a ``return state`` placed after it).

    Used for a *preceding sibling* statement that the return falls through from.
    A direct witness statement dominates. A nested guard block (``if ... is not
    None: <witness>``) dominates only when that block FALLS THROUGH back to this
    level (``_block_always_exits`` is False) — a guard that itself returns /
    continues is a disjoint alternative path and is NOT credited (the G2 fix).
    ``match`` cases are always disjoint alternatives, so a witness inside a
    *sibling* ``case`` is never credited here.
    """
    for stmt in body:
        if _stmt_is_witness_emit(stmt):
            return True
        # A ``match`` is N disjoint alternative arms; a witness in any single arm
        # does NOT dominate the fall-through, so never descend a sibling Match.
        if isinstance(stmt, ast.Match):
            continue
        for inner in _child_stmt_blocks(stmt):
            # Only credit a witness in a nested block if that block falls through
            # to here (an early-exit branch is a disjoint path, not a dominator).
            if _block_always_exits(inner):
                continue
            if _block_contains_dominating_witness(inner):
                return True
    return False


def _block_contains_witness_emit(body: list[ast.stmt]) -> bool:
    """Back-compat alias retained for the producer-side scan / self-tests:
    whether ``body`` contains a *path-dominating* witness emit (G2-correct)."""
    return _block_contains_dominating_witness(body)


def _return_is_witnessed(
    return_node: ast.Return,
    block_stack: list[list[ast.stmt]],
) -> bool:
    """True iff a typed witness emit DOMINATES ``return_node`` on its AST path.

    ``block_stack`` is the chain of statement-lists from the function body down
    to the block directly containing the return (each ``match``/``case`` body on
    the path is one such block, via the G1-aware ``_walk``). Real dominance
    requires the witness on EVERY path to the return, so for each block on the
    path we only consider the statements STRICTLY PRECEDING the return's ancestor
    in that block, and credit a witness only when it is on the straight-line
    fall-through path (a direct witness statement, or a guard ``if`` that falls
    through) — NOT inside a disjoint sibling branch / ``case`` arm that the
    return's path never executes (the G2 fix). This matches the production
    witnessing convention: an ``*_out.append(...)`` (often guarded by ``if ... is
    not None:``) placed immediately before the ``return state``.
    """
    for block in block_stack:
        # Statements strictly before the return (or before the ancestor of the
        # return that lives in this block) are the dominators on this path.
        for stmt in block:
            if _stmt_contains_node(stmt, return_node):
                break
            if _block_contains_dominating_witness([stmt]):
                return True
    return False


def _stmt_contains_node(stmt: ast.stmt, target: ast.AST) -> bool:
    if stmt is target:
        return True
    for child in ast.walk(stmt):
        if child is target:
            return True
    return False


def _line_waived(lines: list[str], line_no: int) -> bool:
    for probe in (line_no - 1, line_no - 2):
        if 0 <= probe < len(lines) and WAIVER_MARKER in lines[probe]:
            return True
    return False


def _scan_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    rel_path: str,
    lines: list[str],
    records: list[dict[str, Any]],
) -> None:
    if not _function_has_state_param(fn):
        return

    def _walk(block: list[ast.stmt], stack: list[list[ast.stmt]]) -> None:
        next_stack = stack + [block]
        for stmt in block:
            if isinstance(stmt, ast.Return) and _is_bare_state_return(stmt):
                witnessed = _return_is_witnessed(stmt, next_stack)
                waived = _line_waived(lines, stmt.lineno)
                records.append(
                    {
                        "file": rel_path,
                        "line": stmt.lineno,
                        "function": fn.name,
                        "witnessed": witnessed,
                        "waived": waived,
                        "status": (
                            "waived"
                            if waived
                            else "witnessed"
                            if witnessed
                            else "unwitnessed"
                        ),
                        "snippet": (
                            lines[stmt.lineno - 1].strip()
                            if 0 <= stmt.lineno - 1 < len(lines)
                            else "return state"
                        ),
                    }
                )
            # Recurse into nested blocks (but NOT into nested function defs; a
            # nested closure with its own ``state`` is handled when visited as a
            # FunctionDef below). ``_child_stmt_blocks`` includes ``match`` /
            # ``case`` bodies so a ``return state`` inside a ``case`` arm is no
            # longer invisible (the G1 fix).
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in _child_stmt_blocks(stmt):
                _walk(inner, next_stack)

    _walk(fn.body, [])

    # Nested function defs (closures) inside this handler are also op-handlers if
    # they take / close over ``state``; visit them so closure declines count.
    for node in ast.walk(fn):
        if node is fn:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_function(node, rel_path, lines, records)


def discover_apply_files(repo_root: Path | None = None) -> list[str]:
    """Glob every ``src/lawvm/finland/apply_*.py`` (minus documented exclusions).

    Replaces the former hardcoded allowlist (G3): a NEW apply file is scanned
    automatically, so coverage cannot silently shrink by a forgotten file. Any
    deliberate omission must be an explicit, documented entry in
    ``EXCLUDED_APPLY_FILES``.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    base = root / _APPLY_FILES_DIR
    rels = sorted(
        _rel_posix(p, root)
        for p in base.glob(_APPLY_FILES_GLOB)
        if p.is_file() and not p.name.startswith("test_")
    )
    return [rel for rel in rels if rel not in EXCLUDED_APPLY_FILES]


def scan_apply_declines(repo_root: Path | None = None) -> dict[str, Any]:
    """Compute the full apply-decline ratchet state across scanned apply files."""
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    scanned_files: list[str] = []
    for rel in discover_apply_files(root):
        path = root / rel
        if not path.exists():
            continue
        scanned_files.append(rel)
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        tree = ast.parse(text)
        # Scan module-level + class-level function defs; _scan_function recurses
        # into nested closures, and we de-dup by (file, line) below in case a
        # closure is reached more than once.
        scanned_records: list[dict[str, Any]] = []
        for node in tree.body:
            _scan_module_member(node, rel, lines, scanned_records)
        # De-dup by (file, line) in case a closure is reached twice.
        deduped: dict[tuple[str, int], dict[str, Any]] = {}
        for rec in scanned_records:
            deduped[(rec["file"], rec["line"])] = rec
        records.extend(sorted(deduped.values(), key=lambda r: r["line"]))

    unwitnessed = [r for r in records if r["status"] == "unwitnessed"]
    witnessed = [r for r in records if r["status"] == "witnessed"]
    waived = [r for r in records if r["status"] == "waived"]

    unwitnessed_counts: dict[str, int] = {}
    for rec in unwitnessed:
        unwitnessed_counts[rec["file"]] = unwitnessed_counts.get(rec["file"], 0) + 1

    return {
        "scanned_files": scanned_files,
        "records": records,
        "decline_count": len(records),
        "witnessed_count": len(witnessed),
        "waived_count": len(waived),
        "unwitnessed": unwitnessed,
        "unwitnessed_count": len(unwitnessed),
        "unwitnessed_counts": dict(sorted(unwitnessed_counts.items())),
    }


def _scan_module_member(
    node: ast.stmt,
    rel_path: str,
    lines: list[str],
    records: list[dict[str, Any]],
) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _scan_function(node, rel_path, lines, records)
    elif isinstance(node, ast.ClassDef):
        for member in node.body:
            _scan_module_member(member, rel_path, lines, records)


# ---------------------------------------------------------------------------
# Part 2: registered-code -> production-emit-site check
# ---------------------------------------------------------------------------
#
# Every code in the Finland source-pathology registry must have >= 1 NON-TEST
# production emit site: a ``SourcePathology(code=...)`` / ``.from_scope(code=...)``
# construction (the producer). A registered code with no producer is a dead code
# whose witness reaches no production consumer — the same silent-divergence class
# as Part 1, one level up (the code exists, is registered with blocking
# enforcement, but can never fire from production). The registry file itself and
# pure consumer-side declarations (``allowed_pathology_codes=(...)``) are NOT
# emit sites.

_REGISTRY_REL_PATH = "src/lawvm/finland/source_pathology_proof_registry.py"
_SRC_SCAN_ROOT = Path("src/lawvm")


def _pathology_emit_sites(repo_root: Path) -> dict[str, list[str]]:
    """Map each ``SourcePathology(code=<const>)`` / ``from_scope(code=<const>)``
    construction in non-test production code to its file:line emit sites."""
    sites: dict[str, list[str]] = {}
    base = repo_root / _SRC_SCAN_ROOT
    for pyfile in sorted(base.rglob("*.py")):
        if pyfile.name.startswith("test_") or "/tests/" in f"/{_rel_posix(pyfile, repo_root)}":
            continue
        try:
            tree = ast.parse(pyfile.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = _rel_posix(pyfile, repo_root)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name not in {"SourcePathology", "from_scope"}:
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "code"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    sites.setdefault(kw.value.value, []).append(f"{rel}:{node.lineno}")
    return sites


def scan_pathology_code_producers(repo_root: Path | None = None) -> dict[str, Any]:
    """Find registered pathology codes that have NO production emit site."""
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    from lawvm.finland.source_pathology_proof_registry import (
        registered_source_pathology_proof_rule_codes,
    )

    codes = list(registered_source_pathology_proof_rule_codes())
    emit_sites = _pathology_emit_sites(root)
    producerless = sorted(c for c in codes if not emit_sites.get(c))
    return {
        "registered_code_count": len(codes),
        "producerless_codes": producerless,
        "producerless_count": len(producerless),
        "emit_site_counts": {c: len(emit_sites.get(c, [])) for c in sorted(codes)},
    }


def ratchet_baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_apply_declines(repo_root)
    producers = scan_pathology_code_producers(repo_root)
    return {
        "_doc": (
            "Monotone apply-decline ratchet baseline. Counts UN-witnessed "
            "`return state` op-handler declines per apply file (a decline that "
            "drops an authored op without appending a typed pathology/finding to "
            "a production ledger). The per-file count may only FALL, never rise; "
            "a fall must be committed (regenerate with `uv run python "
            "scripts/inventory_apply_declines.py --update-baseline`). See "
            "tests/test_apply_decline_ratchet.py and "
            "notes_internal/EXIT_REAUDIT.md. `producerless_codes` lists "
            "registered source-pathology codes with NO production emit site "
            "(recorded debt); the list may only SHRINK."
        ),
        "total_unwitnessed": state["unwitnessed_count"],
        "unwitnessed_counts": state["unwitnessed_counts"],
        "producerless_codes": producers["producerless_codes"],
    }


def write_ratchet_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / RATCHET_BASELINE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = ratchet_baseline_snapshot(root)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory apply-path op-handler `return state` declines and whether "
            "each is witnessed by a typed pathology/finding emit (Gate 3)."
        )
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Regenerate tests/data/apply_decline_ratchet_baseline.json from the "
            "current tree. Only ever commit a baseline whose un-witnessed counts "
            "are <= the committed one."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "summary"),
        default="json",
        help="Output format for the inventory (default json).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path; if omitted, prints to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.update_baseline:
        out_path = write_ratchet_baseline()
        snapshot = json.loads(out_path.read_text(encoding="utf-8"))
        print(
            f"wrote {out_path} "
            f"(total_unwitnessed={snapshot['total_unwitnessed']})"
        )
        return 0

    state = scan_apply_declines()
    producers = scan_pathology_code_producers()
    if args.format == "summary":
        text_lines = [
            f"scanned_files: {len(state['scanned_files'])}",
            f"declines: {state['decline_count']}",
            f"  witnessed:   {state['witnessed_count']}",
            f"  waived:      {state['waived_count']}",
            f"  unwitnessed: {state['unwitnessed_count']}",
            "",
        ]
        if state["unwitnessed"]:
            text_lines.append("UN-WITNESSED declines (silent drops):")
            for rec in state["unwitnessed"]:
                text_lines.append(
                    f"  {rec['file']}:{rec['line']}  {rec['function']}  {rec['snippet']}"
                )
            text_lines.append("")
        text_lines.append(
            f"registered pathology codes: {producers['registered_code_count']}"
        )
        text_lines.append(
            f"  producerless (no production emit site): {producers['producerless_count']}"
        )
        for code in producers["producerless_codes"]:
            text_lines.append(f"    {code}")
        text = "\n".join(text_lines) + "\n"
    else:
        text = json.dumps(
            {"declines": state, "producers": producers},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
