"""Inventory naming-hygiene surfaces and host their monotone ratchet scans.

This script hosts the reusable scans for the *naming-hygiene ratchet* gate
("Gate 46a", ``tests/test_naming_hygiene_ratchet.py``). It locks the naming gains
of the #46/#47 rename rounds (``notes/ARCHITECTURE_LEAK_LEDGER.md`` "Deferred
CI-lint ideas (Pro §14)") so they cannot silently regress. It implements the
four Pro §14 lint ideas, each as a *monotone baseline ratchet* (record current
state, fail on NEW violations) — the same zero-false-positive discipline as the
regex ratchet (Gate 2) and the apply-decline ratchet (Gate 3).

Why baseline-and-ratchet rather than hard allowlists
----------------------------------------------------
The four ideas range from "tractable lock" to "broad greenfield retrofit". A
hard allowlist for any of them would FALSE-POSITIVE on the current clean tree
(hundreds of legitimate ``certificate`` symbols survive; ~349 ``status`` fields/
keys across all jurisdictions legitimately stay internal; ~95 public schema
strings exist with no machine-checkable plane/seam declaration vocabulary). The
established, zero-FP-safe pattern is therefore: snapshot the CURRENT state into a
committed baseline, and FAIL only on a NEW item (a count/set that grows over the
baseline). A drop must be re-committed (the ratchet only ever tightens). This
locks the surface — nothing new can leak — without grading the existing
(human-judged) state.

The four scans
--------------
Lint 1 — ``certified``-family symbol ratchet (``scan_certified_symbols``).
    RN1 reserved the word "certified" for cert-root-covered artifacts; the
    rename rounds moved the rest to ``*Coverage``. This scan records every
    current identifier containing the ``certif`` stem (``certified`` /
    ``certificate`` / ``CoverageCertificate`` / ``CertificateProductionResult``
    / ...). The committed baseline is the set of CURRENT survivors. A NEW
    ``certif``-stem identifier FAILS CI — the author must either use ``*Coverage``
    (the renamed form) or, if it is genuinely cert-root-related, consciously add
    it to the baseline. This is a hard ratchet (zero-FP on the clean tree because
    the baseline IS the clean tree).

Lint 2 — bare ``status`` public-schema field/key ratchet (``scan_bare_status``).
    A4 namespaced the public-schema ``status`` keys it could (e.g.
    ``provision_status`` / ``claim_status`` / ``evidence_status``). But a robust
    machine discriminator for "public schema status" vs "internal status local"
    does NOT exist — the A4 round was human judgment, and ~349 ``status`` sites
    across all jurisdictions legitimately remain (references-lens surface facts,
    transient locals, ...). So this scan counts the bare-``status`` *surface*
    sites — a serialized dict key literal ``"status":`` and a dataclass/function
    field declared exactly ``status:`` — and BASELINES them. A NEW bare-``status``
    surface site FAILS CI; the author must namespace it (``*_status``) or, if it
    is a genuinely internal/non-public ``status``, consciously add it to the
    baseline. HEURISTIC LIMIT (loud): this is a SURFACE proxy, not a proven
    public/internal classifier; it deliberately over-includes internal sites into
    the baseline so it never FAILS on an internal ``status`` — the ratchet value
    is "no NEW bare status anywhere without a conscious decision", not "every
    baselined site is public".

Lint 3 + Lint 4 — public-schema registry ratchet (``scan_public_schemas``).
    Pro §14 asks every public schema to declare its plane + seam/waist relation
    (3) and every projection to name its source dossier/root (4). There is
    currently NO declaration vocabulary in the codebase for either (a survey
    finds ~0 ``plane=`` / ``source_root``-projection-tag declarations near the
    ~95 ``lawvm.*.vN`` schema strings). The full semantic check is therefore
    DEFERRED (see the module-level DEFERRED_SPEC below) — there is nothing to
    check declarations against yet. The TRACTABLE, zero-FP subset implemented
    here locks the *public schema surface itself*: it records the set of
    ``lawvm.<name>.vN`` versioned schema-id string literals (the consumer-visible
    serialized roots). The committed baseline is the current set. A NEW public
    schema id FAILS CI — forcing a conscious act (the future plane/seam +
    source-root declaration will hang off this same registry). The set may only
    grow by an explicit baseline update, never silently.

Each scan returns plain JSON-able state; the committed baseline
(``tests/data/naming_hygiene_ratchet_baseline.json``) is the monotone ratchet.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]

RATCHET_BASELINE_PATH = Path("tests/data/naming_hygiene_ratchet_baseline.json")

# The source roots scanned for every lint. All non-test ``src/lawvm`` python.
_SRC_SCAN_ROOT = Path("src/lawvm")


# ===========================================================================
# DEFERRED SPEC (Pro §14 lints 3 + 4, full semantic form)
# ===========================================================================
#
# The full "declare plane + seam/waist relation" (3) and "name source
# dossier/root or explicitly 'unchecked'" (4) checks are DEFERRED, not faked.
# Concrete spec for the later build:
#
#   1. Introduce a declaration vocabulary. Each ``lawvm.*.vN`` schema gets a
#      machine-readable descriptor next to its emit site, e.g. a module-level
#      ``SCHEMA_DESCRIPTORS: dict[str, SchemaDescriptor]`` mapping the schema id
#      to ``SchemaDescriptor(plane=<A|B|C|D|E>, seam=<seam-name|"waist"|None>,
#      source_root=<dossier-id|"unchecked">)``.
#   2. The registry baseline below (``public_schemas``) becomes the key set the
#      descriptor map must cover: every registered schema id MUST have a
#      descriptor (3/4 enforced), and the ratchet flips from "set may only grow"
#      to "every member declares plane+seam (3) and source_root-or-unchecked (4)".
#   3. ``"unchecked"`` is an explicit, greppable escape hatch (per the brief's
#      "or explicitly say 'unchecked'") so the retrofit can land incrementally
#      while still being honest about which projections are not yet rooted.
#
# Until that vocabulary exists there is nothing to check declarations against, so
# enforcing them now would either FALSE-POSITIVE on all ~95 schemas or fake a
# pass. The tractable lock (the schema-id registry ratchet) is implemented; the
# semantic declaration check is this spec.
DEFERRED_SPEC = (
    "Pro §14 lints 3+4 full semantic form (plane+seam+source_root declaration) "
    "is DEFERRED pending a SchemaDescriptor vocabulary; see DEFERRED_SPEC in "
    "scripts/inventory_naming_hygiene.py. The tractable subset (public-schema-id "
    "registry ratchet) is enforced now."
)


def _rel_posix(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_src_files(repo_root: Path) -> list[Path]:
    base = repo_root / _SRC_SCAN_ROOT
    files: list[Path] = []
    for pyfile in sorted(base.rglob("*.py")):
        rel = _rel_posix(pyfile, repo_root)
        if pyfile.name.startswith("test_") or "/tests/" in f"/{rel}":
            continue
        files.append(pyfile)
    return files


# ===========================================================================
# Lint 1: ``certified``-family symbol ratchet
# ===========================================================================
#
# We scan IDENTIFIERS (ast.Name / attribute / def / class / arg names), not raw
# text, so a ``certif`` substring inside a comment or docstring or a schema-id
# STRING (e.g. "lawvm.certified_tree_transition.v0") is NOT counted — only real
# code symbols. The ratchet quantity is the SET of distinct certif-stem
# identifiers; a NEW one (not in the baseline) is a leak.

_CERTIF_STEM = re.compile(r"certif", re.IGNORECASE)


def _identifier_has_certif_stem(name: str) -> bool:
    return bool(_CERTIF_STEM.search(name))


def _collect_identifiers(tree: ast.AST) -> set[str]:
    """Every code identifier in a module: names, attributes, def/class names,
    function arguments, keyword-argument names. Excludes string/number literals
    and (because we walk the AST, not text) comments and docstrings."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            names.add(node.arg)
    return names


def scan_certified_symbols(repo_root: Path | None = None) -> dict[str, Any]:
    """The set of distinct ``certif``-stem code identifiers across src/lawvm.

    Returns ``{"certified_symbols": sorted[str], "certified_symbol_count": int,
    "symbol_sites": {symbol: [file:line, ...]}}``. The monotone ratchet quantity
    is ``certified_symbols`` (a SET, so it may only shrink; a NEW member fails).
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    symbols: set[str] = set()
    sites: dict[str, list[str]] = {}
    for pyfile in _iter_src_files(root):
        try:
            text = pyfile.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        rel = _rel_posix(pyfile, root)
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                name = node.name
            elif isinstance(node, ast.arg):
                name = node.arg
            elif isinstance(node, ast.keyword) and node.arg is not None:
                name = node.arg
            if name and _identifier_has_certif_stem(name):
                symbols.add(name)
                lineno = getattr(node, "lineno", 0)
                sites.setdefault(name, []).append(f"{rel}:{lineno}")
    return {
        "certified_symbols": sorted(symbols),
        "certified_symbol_count": len(symbols),
        "symbol_sites": {k: sorted(set(v)) for k, v in sorted(sites.items())},
    }


# ===========================================================================
# Lint 2: bare ``status`` public-schema field/key ratchet
# ===========================================================================
#
# Two surface shapes, both AST-based (so comments/docstrings never count):
#   (a) a serialized dict key literal ``"status": <value>`` in a dict display,
#   (b) a field/parameter declared exactly ``status`` with an annotation
#       (``status: <type>`` — an ``ast.AnnAssign`` field or an annotated
#       function parameter).
# These are the consumer-/cross-phase-visible surfaces a bare ``status`` leaks
# through. We count SITES (file:line) and baseline the per-file count.
#
# HEURISTIC LIMIT (documented loudly in the gate + baseline _doc): this is a
# SURFACE proxy, not a proven public-vs-internal classifier. It deliberately
# over-includes (an internal annotated ``status:`` local counts too) so the
# baseline absorbs all current internal sites and the ratchet never FAILS on a
# pre-existing internal status. The lock is "no NEW bare status surface without a
# conscious baseline bump", not "every baselined site is public".


def _bare_status_sites_in_module(
    tree: ast.AST, rel_path: str
) -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        # (a) dict key literal "status": ...
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "status"
                ):
                    sites.append(
                        {
                            "file": rel_path,
                            "line": getattr(key, "lineno", 0),
                            "shape": "dict_key",
                        }
                    )
        # (b) annotated field/param declared exactly ``status``
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "status":
                sites.append(
                    {
                        "file": rel_path,
                        "line": getattr(node, "lineno", 0),
                        "shape": "ann_field",
                    }
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
            ):
                if arg.arg == "status" and arg.annotation is not None:
                    sites.append(
                        {
                            "file": rel_path,
                            "line": getattr(arg, "lineno", 0),
                            "shape": "ann_param",
                        }
                    )
    return sites


def scan_bare_status(repo_root: Path | None = None) -> dict[str, Any]:
    """Bare-``status`` public-schema surface sites per file across src/lawvm.

    Returns ``{"bare_status_counts": {rel: count}, "total_bare_status": int,
    "sites": [...]}``. The monotone ratchet quantity is the per-file count.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    sites: list[dict[str, Any]] = []
    for pyfile in _iter_src_files(root):
        try:
            text = pyfile.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        rel = _rel_posix(pyfile, root)
        sites.extend(_bare_status_sites_in_module(tree, rel))

    counts: dict[str, int] = {}
    for site in sites:
        counts[site["file"]] = counts.get(site["file"], 0) + 1
    return {
        "bare_status_counts": dict(sorted(counts.items())),
        "total_bare_status": len(sites),
        "sites": sites,
    }


# ===========================================================================
# VOCAB-02: bare ``confidence`` schema-field surface ratchet
# ===========================================================================
#
# §11.8 says ``confidence`` is diagnostic metadata only — never a control signal —
# and a public/cross-phase schema must namespace its status fields. The bare-
# ``status`` surface ratchet above (Lint 2) already locks the bare-``status`` arm
# of VOCAB-02. VOCAB-02 ADDS the bare-``confidence`` schema-field arm: a
# serialized ``"confidence":`` dict key or an annotated ``confidence`` field/param
# is a bare confidence surface that §11.8 treats as diagnostic-only — it must not
# become a cross-phase control field. This scan mirrors ``_bare_status_sites`` and
# baselines the current per-file count (HEURISTIC: a surface proxy, over-includes
# internal sites, so the lock is "no NEW bare confidence surface").


def _bare_confidence_sites_in_module(
    tree: ast.AST, rel_path: str
) -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "confidence":
                    sites.append(
                        {
                            "file": rel_path,
                            "line": getattr(key, "lineno", 0),
                            "shape": "dict_key",
                        }
                    )
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "confidence":
                sites.append(
                    {
                        "file": rel_path,
                        "line": getattr(node, "lineno", 0),
                        "shape": "ann_field",
                    }
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                if arg.arg == "confidence" and arg.annotation is not None:
                    sites.append(
                        {
                            "file": rel_path,
                            "line": getattr(arg, "lineno", 0),
                            "shape": "ann_param",
                        }
                    )
    return sites


def scan_bare_confidence(repo_root: Path | None = None) -> dict[str, Any]:
    """Bare-``confidence`` schema-field surface sites per file across src/lawvm.

    Returns ``{"bare_confidence_counts": {rel: count}, "total_bare_confidence":
    int, "sites": [...]}``. The monotone ratchet quantity is the per-file count.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    sites: list[dict[str, Any]] = []
    for pyfile in _iter_src_files(root):
        try:
            text = pyfile.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        rel = _rel_posix(pyfile, root)
        sites.extend(_bare_confidence_sites_in_module(tree, rel))

    counts: dict[str, int] = {}
    for site in sites:
        counts[site["file"]] = counts.get(site["file"], 0) + 1
    return {
        "bare_confidence_counts": dict(sorted(counts.items())),
        "total_bare_confidence": len(sites),
        "sites": sites,
    }


# ===========================================================================
# Lint 3 + 4: public-schema-id registry ratchet (tractable subset)
# ===========================================================================
#
# A "public schema" is a ``lawvm.<name>.vN`` versioned schema-id string literal —
# the consumer-visible serialized root. We collect the SET of distinct such ids
# from string literals across src/lawvm. The ratchet quantity is the set (it may
# only grow by an explicit baseline update; a NEW id that is not in the baseline
# fails, forcing the conscious registration that the deferred plane/seam +
# source-root declaration will hang off).

_PUBLIC_SCHEMA_ID = re.compile(r"^lawvm\.[a-z0-9_]+\.v[0-9]+$")


def scan_public_schemas(repo_root: Path | None = None) -> dict[str, Any]:
    """The set of ``lawvm.<name>.vN`` public schema-id strings across src/lawvm.

    Returns ``{"public_schemas": sorted[str], "public_schema_count": int,
    "schema_sites": {id: [file:line, ...]}}``. The monotone ratchet quantity is
    ``public_schemas`` (a SET; a NEW id fails).
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    schemas: set[str] = set()
    sites: dict[str, list[str]] = {}
    for pyfile in _iter_src_files(root):
        try:
            text = pyfile.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        rel = _rel_posix(pyfile, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _PUBLIC_SCHEMA_ID.match(node.value):
                    schemas.add(node.value)
                    lineno = getattr(node, "lineno", 0)
                    sites.setdefault(node.value, []).append(f"{rel}:{lineno}")
    return {
        "public_schemas": sorted(schemas),
        "public_schema_count": len(schemas),
        "schema_sites": {k: sorted(set(v)) for k, v in sorted(sites.items())},
    }


# ===========================================================================
# Baseline snapshot + writer
# ===========================================================================


def ratchet_baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    """The committed-baseline shape for all three scans.

    Only the monotone quantities are persisted (the certified-symbol SET, the
    per-file bare-status COUNTS, the public-schema-id SET). Volatile detail (line
    numbers, sites) is NOT persisted so the baseline is stable across cosmetic
    edits and only changes when a real quantity changes.
    """
    certified = scan_certified_symbols(repo_root)
    status = scan_bare_status(repo_root)
    schemas = scan_public_schemas(repo_root)
    return {
        "_doc": (
            "Monotone naming-hygiene ratchet baseline (Gate 46a, Pro §14). Locks "
            "the #46/#47 rename gains. THREE ratchets, all one-way: "
            "(1) certified_symbols — the SET of `certif`-stem code identifiers; a "
            "NEW member fails CI (use `*Coverage` or consciously extend the set). "
            "(2) bare_status_counts — per-file count of bare-`status` SURFACE "
            "sites (a `\"status\":` dict key or an annotated `status` field/param). "
            "HEURISTIC: a surface proxy, NOT a public/internal classifier; it "
            "over-includes internal sites so it never fails on a pre-existing "
            "internal status. The lock is `no NEW bare status surface`. "
            "(3) public_schemas — the SET of `lawvm.<name>.vN` schema-id strings; "
            "a NEW id fails (conscious registration). The full plane/seam + "
            "source-root declaration check (Pro §14 lints 3+4) is DEFERRED; see "
            "DEFERRED_SPEC in scripts/inventory_naming_hygiene.py. Regenerate "
            "with `uv run python scripts/inventory_naming_hygiene.py "
            "--update-baseline`. See tests/test_naming_hygiene_ratchet.py and "
            "notes/ARCHITECTURE_LEAK_LEDGER.md."
        ),
        "certified_symbols": certified["certified_symbols"],
        "certified_symbol_count": certified["certified_symbol_count"],
        "bare_status_counts": status["bare_status_counts"],
        "total_bare_status": status["total_bare_status"],
        "public_schemas": schemas["public_schemas"],
        "public_schema_count": schemas["public_schema_count"],
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
            "Inventory naming-hygiene surfaces (certified-family symbols, bare "
            "`status` public-schema fields, public `lawvm.*.vN` schema ids) and "
            "host their monotone ratchet scans (Gate 46a, Pro §14)."
        )
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Regenerate tests/data/naming_hygiene_ratchet_baseline.json from the "
            "current tree. Only ever commit a baseline whose certified-symbol set "
            "and public-schema set and per-file bare-status counts are <= the "
            "committed one (the ratchet only tightens)."
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
            f"(certified_symbols={snapshot['certified_symbol_count']}, "
            f"bare_status={snapshot['total_bare_status']}, "
            f"public_schemas={snapshot['public_schema_count']})"
        )
        return 0

    certified = scan_certified_symbols()
    status = scan_bare_status()
    schemas = scan_public_schemas()
    if args.format == "summary":
        text_lines = [
            f"certified-family symbols: {certified['certified_symbol_count']}",
            f"bare-status surface sites: {status['total_bare_status']}",
            f"public schema ids: {schemas['public_schema_count']}",
            "",
            DEFERRED_SPEC,
            "",
        ]
        text = "\n".join(text_lines) + "\n"
    else:
        text = json.dumps(
            {
                "certified": certified,
                "bare_status": status,
                "public_schemas": schemas,
                "deferred_spec": DEFERRED_SPEC,
            },
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
