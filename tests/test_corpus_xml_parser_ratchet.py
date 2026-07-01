"""Monotone ratchet: forbid NEW raw ``etree.fromstring`` calls in frontends.

The hardened parser config lives in ``lawvm.core.xml_parse.parse_corpus_xml``,
which is the one entry point every corpus-XML parse must route through so the
``resolve_entities=False`` / ``no_network=True`` / ``load_dtd=False`` defaults
stay pinned in one auditable place (AGENTS.md §1.10, §2.6).

This gate AST-scans every frontend module under
``src/lawvm/{finland,norway,sweden,estonia,uk_legislation,new_zealand,eu,
us_federal}/`` for raw ``etree.fromstring(...)`` calls that do NOT pass a
``parser=`` keyword argument (the "raw" form — the hardened config is sanitizer-
side, not caller-side, so passing ``parser=`` is the legacy escape hatch).

A site is allowed iff either:

  (1) the call, or the line directly above it, carries an inline waiver
      ``# lawvm-xml: <category>`` with a known category (``own_output_check``
      for validating LawVM's own produced XML bytes, ``schema_internal`` for
      XML schema validation, etc.); OR

  (2) the site's per-file un-waived count is bounded by the committed
      baseline (``tests/data/corpus_xml_parser_baseline.json``) — the
      technical-debt ledger of pre-existing unmigrated sites the
      ``parse_corpus_xml`` migration wave (Security H1) deliberately left in
      place outside its 16-file scope.

Behaviour:
  * current_count  > baseline_count  →  FAIL  (new violation; migrate to
                                            ``parse_corpus_xml`` or add an
                                            inline ``# lawvm-xml:`` waiver)
  * current_count  < baseline_count  →  FAIL  (count dropped — regenerate the
                                            baseline to lock the gain in)
  * new file with raw ``etree.fromstring`` and baseline absent → FAIL

The ratchet is one-way: per-file un-waived counts may only FALL.

Reference: AGENTS.md §1.10 (fail loud; no broad ``except``); §2.6 (rule of
three — 30 sites shared the same hardened-config shape before
``parse_corpus_xml``).
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


# All LawVM source frontends scanned by this ratchet. Same set of roots as the
# regex ratchet (scripts/inventory_parser_smells.py:_RATCHET_SCAN_ROOTS), so
# "no NEW raw etree.fromstring" is enforced across every frontend and cannot
# silently re-leak on a non-fi/no frontend.
_RATCHET_SCAN_ROOTS: tuple[Path, ...] = (
    Path("src/lawvm/finland"),
    Path("src/lawvm/estonia"),
    Path("src/lawvm/norway"),
    Path("src/lawvm/sweden"),
    Path("src/lawvm/new_zealand"),
    Path("src/lawvm/eu"),
    Path("src/lawvm/us_federal"),
    Path("src/lawvm/uk_legislation"),
)


_BASELINE_PATH = _REPO_ROOT / "tests" / "data" / "corpus_xml_parser_baseline.json"

# Current-tree raw-parse debt that is already present but not represented by the
# stale per-file baseline. This is deliberately separate from the committed
# baseline so the debt is visible while unrelated frontend reds block a clean
# baseline regeneration. Keep this list small and delete entries as the sites
# move to ``parse_corpus_xml`` or the baseline can be lowered cleanly.
_CURRENT_DEBT_BASELINE_OVERLAY: dict[str, int] = {
    # ``grafter_parse.py`` was split out of ``grafter.py``; the total Estonia
    # count did not grow, but the per-file baseline predates the split.
    "src/lawvm/estonia/grafter.py": 2,
    "src/lawvm/estonia/grafter_parse.py": 1,
    # Legacy raw parses surfaced by the expanded scanner scope.
    "src/lawvm/eu/fmx4_amendment_grammar.py": 1,
    "src/lawvm/uk_legislation/uk_amendment_replay.py": 1,
    "src/lawvm/us_federal/amendatory.py": 2,
}


def _comparison_baseline_counts(baseline_counts: dict[str, int]) -> dict[str, int]:
    """Baseline counts plus explicit current-debt overlay entries."""
    merged = dict(baseline_counts)
    merged.update(_CURRENT_DEBT_BASELINE_OVERLAY)
    return merged


# ---------------------------------------------------------------------------
# Inline waiver vocabulary
# ---------------------------------------------------------------------------
#
# An inline ``# lawvm-xml: <category> [<rationale>]`` on the call's own line OR
# the line directly above it waives the site. Categories:
#
#   own_output_check  — the call validates XML bytes LawVM itself just produced
#                       (e.g. corrigendum-patch validation). The bytes are still
#                       routed through the hardened config when they enter the
#                       system; this is the second-stage well-formedness check
#                       only and is handed back to ``etree.fromstring`` because
#                       the bytes returned to the caller are LawVM-authored,
#                       not raw corpus input. ``resolve_entities=False`` is
#                       still preferable as the audit-default — prefer
#                       migrating these sites to ``parse_corpus_xml`` over
#                       decades of ``own_output_check`` waivers.
#   schema_internal   — validating against an XML schema / DTD shipped with
#                       the package (LawVM-local resource, not corpus input).
#   test_fixture      — test-only embedded-XML parsing (not in scope here —
#                       ``tests/`` is excluded by the scan roots — but kept in
#                       the vocabulary in case a future scan extension includes
#                       test modules).
#   legacy_lxml_path  — temporary: a known-migrated-front-end trailing case
#                       awaiting ``parse_corpus_xml`` rollout. Use sparingly;
#                       the baseline is the preferred accounting for the
#                       technical-debt ledger because it is monotone.
WAIVER_CATEGORIES: frozenset[str] = frozenset(
    {
        "own_output_check",
        "schema_internal",
        "test_fixture",
        "legacy_lxml_path",
    }
)
_RE_WAIVER_COMMENT = re.compile(
    r"#\s*lawvm-xml:\s*(?P<category>[a-z_]+)\b(?P<rationale>.*)$"
)


# ---------------------------------------------------------------------------
# AST scan
# ---------------------------------------------------------------------------


def _rel_posix(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_frontend_files(repo_root: Path | None = None) -> list[str]:
    """Python files under the 8 frontend scan roots (excluding tests/__pycache__)."""
    root = (repo_root or _REPO_ROOT).resolve()
    scanned: list[str] = []
    for scan_root in _RATCHET_SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        for pyfile in sorted(base.rglob("*.py")):
            rel = _rel_posix(pyfile, root)
            if "/tests/" in f"/{rel}" or pyfile.name.startswith("test_"):
                continue
            scanned.append(rel)
    return scanned


def _is_fromstring_call(call: ast.Call) -> bool:
    """True if ``call`` is a ``<alias>.fromstring(...)`` call.

    The receiver alias is NOT filtered — any ``<X>.fromstring(...)`` is a
    candidate (``etree.fromstring``, ``_etree.fromstring``,
    ``lxml.etree.fromstring``, ...).  ``parser=`` keyword filtering is done at
    the call site, not here.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr == "fromstring"


def _has_parser_keyword(call: ast.Call) -> bool:
    """True if the call passes a ``parser=`` keyword argument.

    A call that explicitly passes ``parser=<XMLParser>`` is the legacy
    escape-hatch form — the caller has chosen the parser consciously (e.g.
    ``etree.HTMLParser(recover=True)`` for HTML fallback).  The hardened
    config is the *default*-parser hardening, so a call that pins its own
    parser is by-design NOT flagged.
    """
    return any(kw.arg == "parser" for kw in call.keywords)


def _line_is_waived(lines: list[str], idx: int) -> tuple[bool, str]:
    """A use-site is waived if its own line, or the line directly above it,
    carries a ``# lawvm-xml: <category> [...]`` comment with a known category.
    Returns ``(waived, waiver_category)``.
    """
    for probe in (idx, idx - 1):
        if probe < 0:
            continue
        match = _RE_WAIVER_COMMENT.search(lines[probe])
        if not match:
            continue
        category = match.group("category")
        if category in WAIVER_CATEGORIES:
            return True, category
    return False, ""


def scan_file_corpus_xml_sites(
    rel_path: str,
    text: str,
) -> list[dict[str, Any]]:
    """Find every raw ``etree.fromstring(...)`` (no ``parser=``) in one file."""
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError:
        # Conservative: if a file is malformed enough to fail AST parsing,
        # skip it rather than block the whole gate.  Production source files
        # are valid Python; this only guards test scaffolding.
        return []

    lines = text.splitlines()
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_fromstring_call(node):
            continue
        if _has_parser_keyword(node):
            continue  # caller pinned its own parser — legacy escape hatch
        lineno = getattr(node, "lineno", 0)
        idx = lineno - 1
        waived, waiver_category = _line_is_waived(lines, idx)
        snippet = lines[idx].strip() if 0 <= idx < len(lines) else ""
        records.append(
            {
                "file": rel_path,
                "line": lineno,
                "waived": waived,
                "waiver_category": waiver_category,
                "snippet": snippet,
            }
        )
    return records


# ---------------------------------------------------------------------------
# Baseline / scan-state
# ---------------------------------------------------------------------------


def scan_corpus_xml_ratchet(repo_root: Path | None = None) -> dict[str, Any]:
    """Compute the full ratchet state across every frontend module.

    Returns:
      - ``unwaived_counts``: ``{rel_path: count}`` per-file un-waived count
        (the monotone ratchet quantity);
      - ``total_unwaived``: sum across files;
      - ``waived_counts`` / ``total_waived``: telemetry on waivers;
      - ``records``: every raw ``etree.fromstring`` site record;
      - ``scanned_file_count``: number of frontend files scanned.
    """
    root = (repo_root or _REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    files = _iter_frontend_files(root)
    for rel in files:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_corpus_xml_sites(rel, text))

    unwaived_counts: dict[str, int] = {}
    waived_counts: dict[str, int] = {}
    for rec in records:
        if rec["waived"]:
            waived_counts[rec["file"]] = waived_counts.get(rec["file"], 0) + 1
        else:
            unwaived_counts[rec["file"]] = unwaived_counts.get(rec["file"], 0) + 1

    return {
        "unwaived_counts": dict(sorted(unwaived_counts.items())),
        "total_unwaived": sum(unwaived_counts.values()),
        "waived_counts": dict(sorted(waived_counts.items())),
        "total_waived": sum(waived_counts.values()),
        "records": records,
        "scanned_file_count": len(files),
    }


def baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    """The committed-baseline shape: per-file un-waived counts + total."""
    state = scan_corpus_xml_ratchet(repo_root)
    return {
        "_doc": (
            "Monotone ratchet baseline for raw etree.fromstring calls in "
            "frontend modules. Generated by tests/test_corpus_xml_parser_ratchet.py "
            "(baseline_snapshot). Per-file 'unwaived' counts may only fall, "
            "never rise; a fall must be committed (regenerate by deleting the "
            "file and running the test). See AGENTS.md §1.10 / §2.6 and "
            "src/lawvm/core/xml_parse.py."
        ),
        "total_unwaived": state["total_unwaived"],
        "unwaived_counts": state["unwaived_counts"],
    }


def write_baseline(repo_root: Path | None = None) -> Path:
    out_path = _BASELINE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = baseline_snapshot(repo_root)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def _load_baseline() -> dict[str, Any]:
    assert _BASELINE_PATH.exists(), (
        f"Missing corpus-XML parser ratchet baseline at {_BASELINE_PATH}. "
        "Regenerate it with "
        "`uv run python -c 'from tests.test_corpus_xml_parser_ratchet import "
        "write_baseline; write_baseline()'`."
    )
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestCorpusXmlParserRatchet:
    def test_no_new_unwaived_fromstring_sites(self) -> None:
        baseline = _load_baseline()
        state = scan_corpus_xml_ratchet(_REPO_ROOT)
        baseline_counts = _comparison_baseline_counts(baseline["unwaived_counts"])
        current_counts: dict[str, int] = state["unwaived_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                offenders = "\n".join(
                    f"      {r['file']}:{r['line']}  {r['snippet']}"
                    for r in state["records"]
                    if r["file"] == rel
                )
                increases.append(
                    f"  {rel}: {count} raw `etree.fromstring()` call(s) "
                    f"(baseline {allowed}, +{count - allowed})\n{offenders}"
                )

        if increases:
            pytest.fail(
                "\n[CORPUS-XML RATCHET] NEW raw `etree.fromstring()` call(s) "
                "added without the hardened parser config:\n"
                + "\n".join(increases)
                + "\n\nEvery corpus-XML parse must route through "
                "`lawvm.core.xml_parse.parse_corpus_xml` so the hardened lxml "
                "defaults (resolve_entities=False / no_network=True / "
                "load_dtd=False) stay pinned in one auditable place. Either:\n"
                "  (1) route the call through `parse_corpus_xml(bytes)` "
                "(or `parse_corpus_xml(bytes, recover=True)` for known-broken "
                "sources), or\n"
                "  (2) if the call genuinely validates LawVM's own XML output "
                "or parses a schema-internal resource (NOT untrusted corpus "
                "input), add an inline `# lawvm-xml: <category>` waiver on "
                "or above the call, or\n"
                "  (3) if the new call is a temporary baseline item to be "
                "migrated later, regenerate the baseline to acknowledge it:\n"
                "      uv run python -c 'from "
                "tests.test_corpus_xml_parser_ratchet import write_baseline; "
                "write_baseline()'\n"
                "(the baseline is a one-way ratchet; per-file counts may only "
                "ever fall)."
            )

    def test_ratchet_only_tightens(self) -> None:
        """If the un-waived count dropped, the baseline MUST be re-committed."""
        baseline = _load_baseline()
        state = scan_corpus_xml_ratchet(_REPO_ROOT)
        baseline_counts = _comparison_baseline_counts(baseline["unwaived_counts"])
        current_counts: dict[str, int] = state["unwaived_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} raw `etree.fromstring()` call(s) "
                    f"(baseline {allowed}, -{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[CORPUS-XML RATCHET] The un-waived count DROPPED — good "
                "work, but the baseline must be lowered to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python -c 'from "
                "tests.test_corpus_xml_parser_ratchet import write_baseline; "
                "write_baseline()'\n"
                "(the baseline is a one-way ratchet; per-file counts may only "
                "ever fall)."
            )

    def test_total_unwaived_matches_baseline_invariant(self) -> None:
        baseline = _load_baseline()
        assert baseline["total_unwaived"] == sum(
            baseline["unwaived_counts"].values()
        ), "Baseline total_unwaived is inconsistent with its per-file counts."

        state = scan_corpus_xml_ratchet(_REPO_ROOT)
        comparison_total = sum(_comparison_baseline_counts(baseline["unwaived_counts"]).values())
        assert state["total_unwaived"] <= comparison_total, (
            f"Total un-waived etree.fromstring sites {state['total_unwaived']} "
            f"exceeds baseline+overlay {comparison_total}."
        )

    def test_baseline_covers_only_frontend_paths(self) -> None:
        """Baseline keys must all live under one of the 8 scan roots.

        Catches a stale baseline entry for a file that has since been moved
        outside the scan roots, or a paste that accidentally names a core/
        path that the ratchet does not scan."""
        baseline = _load_baseline()
        leaked: list[str] = []
        for rel in baseline["unwaived_counts"]:
            in_scope = any(
                rel.startswith(f"{scan_root}/")
                for scan_root in (p.as_posix() for p in _RATCHET_SCAN_ROOTS)
            )
            if not in_scope:
                leaked.append(rel)
        assert not leaked, (
            "baseline keys are outside the scan roots (stale or wrong): "
            f"{leaked!r}"
        )

    def test_scanner_covers_every_frontend(self) -> None:
        """Defence in depth: every scan root must contribute at least one file
        so a typo in ``_RATCHET_SCAN_ROOTS`` cannot silently drop a frontend
        from the gate."""
        state = scan_corpus_xml_ratchet(_REPO_ROOT)
        files_by_root: dict[str, int] = {}
        for rel in (r["file"] for r in state["records"]):
            for scan_root in (p.as_posix() for p in _RATCHET_SCAN_ROOTS):
                if rel.startswith(f"{scan_root}/"):
                    files_by_root[scan_root] = files_by_root.get(scan_root, 0) + 1
                    break
        # We don't assert every root has a violation — only that the SCANNER
        # visits files in each root. ``scanned_file_count`` is the upper
        # bound; ``records`` may be empty for clean frontends.
        scanned = _iter_frontend_files(_REPO_ROOT)
        scanned_by_root: dict[str, int] = {}
        for rel in scanned:
            for scan_root in (p.as_posix() for p in _RATCHET_SCAN_ROOTS):
                if rel.startswith(f"{scan_root}/"):
                    scanned_by_root[scan_root] = scanned_by_root.get(scan_root, 0) + 1
                    break
        missing = sorted(
            root for root in (p.as_posix() for p in _RATCHET_SCAN_ROOTS)
            if scanned_by_root.get(root, 0) == 0
        )
        assert not missing, (
            f"scanner did not visit any file under these scan roots: {missing}"
        )


# ---------------------------------------------------------------------------
# Guard-liveness: drive synthetic inputs through the production scan functions
# so the gate provably catches a NEW raw etree.fromstring call and ignores
# parser=-passing / commented-out / def-only sites.
# ---------------------------------------------------------------------------


class TestCorpusXmlRatchetGuardLiveness:
    def test_bare_fromstring_is_detected(self) -> None:
        src = (
            "import lxml.etree as etree\n"
            "def f(b: bytes):\n"
            "    tree = etree.fromstring(b)\n"
            "    return tree\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert len(records) == 1
        assert records[0]["waived"] is False
        assert records[0]["line"] == 3

    def test_fromstring_with_parser_keyword_is_not_detected(self) -> None:
        # The legacy escape hatch: caller pins its own parser.  The hardened
        # config applies only to the DEFAULT-parser parse, so this call is by
        # design NOT flagged.
        src = (
            "import lxml.etree as etree\n"
            "def f(b: bytes):\n"
            "    p = etree.HTMLParser(recover=True)\n"
            "    tree = etree.fromstring(b, parser=p)\n"
            "    return tree\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert records == []

    def test_aliased_etree_is_detected(self) -> None:
        # Some files use ``_etree`` as the alias.  The scanner must catch any
        # ``<X>.fromstring()`` call regardless of alias.
        src = (
            "from lxml import etree as _etree\n"
            "def f(b: bytes):\n"
            "    tree = _etree.fromstring(b)\n"
            "    return tree\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert len(records) == 1
        assert records[0]["line"] == 3

    def test_statement_level_fromstring_is_detected(self) -> None:
        # The corrigendum.py candidate_xml validation pattern:
        # ``etree.fromstring(candidate_xml)`` as a bare statement (the call is
        # only used for its side effect of raising on malformed input).
        src = (
            "import lxml.etree as etree\n"
            "def f(candidate_xml: bytes):\n"
            "    etree.fromstring(candidate_xml)\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert len(records) == 1
        assert records[0]["line"] == 3

    def test_inline_waiver_on_same_line_suppresses(self) -> None:
        src = (
            "import lxml.etree as etree\n"
            "def f(candidate_xml: bytes):\n"
            "    etree.fromstring(candidate_xml)  # lawvm-xml: own_output_check\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert len(records) == 1
        assert records[0]["waived"] is True
        assert records[0]["waiver_category"] == "own_output_check"

    def test_inline_waiver_on_line_above_suppresses(self) -> None:
        src = (
            "import lxml.etree as etree\n"
            "def f(candidate_xml: bytes):\n"
            "    # lawvm-xml: own_output_check validate bytes LawVM itself produced\n"
            "    etree.fromstring(candidate_xml)\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert len(records) == 1
        assert records[0]["waived"] is True
        assert records[0]["waiver_category"] == "own_output_check"

    def test_unknown_waiver_category_does_not_suppress(self) -> None:
        # A misspelled category must not silently suppress the violation.
        src = (
            "import lxml.etree as etree\n"
            "def f(b: bytes):\n"
            "    etree.fromstring(b)  # lawvm-xml:@testable\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert len(records) == 1
        assert records[0]["waived"] is False

    def test_commented_out_fromstring_is_not_detected(self) -> None:
        # AST scan: a commented-out call is not in the AST tree at all, so
        # won't be detected.  This is the by-design behaviour — the ratchet
        # counts LIVE code paths only.
        src = (
            "import lxml.etree as etree\n"
            "def f(b: bytes):\n"
            "    # tree = etree.fromstring(b)\n"
            "    return None\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert records == []

    def test_def_line_only_is_not_detected(self) -> None:
        # ``etree.fromstring`` mentioned in a docstring / identifier name must
        # not count.  The AST scanner only walks Call nodes, so this is a
        # no-op by construction; the test pins the invariant against future
        # AST broadening.
        src = (
            "import lxml.etree as etree\n"
            "def etree_fromstring_helper(b: bytes) -> None:\n"
            "    return None\n"
        )
        records = scan_file_corpus_xml_sites("<test>", src)
        assert records == []
