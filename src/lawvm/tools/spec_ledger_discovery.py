"""Self-evidencing emit-site location for spec-ledger rule-id catalog gates.

Each jurisdiction has a *believed_spec catalog* completeness test (``tests/
test_spec_ledger_*_catalog.py``).  Those tests discover the jurisdiction's rule-id
surface (constants, AST literals, witness contexts — the discovery shape differs per
jurisdiction) and fail when any discovered id lacks a ``believed_spec`` entry.

The historical failure message listed only the bare ids, forcing a grep to find where
each id is emitted.  This module supplies the *location* half: given the set of ids a
jurisdiction already discovered, find every source line where each id appears as a
string literal, and format an uncataloged-id list that names its emit site(s).

Design note — why a literal-occurrence locator works for every jurisdiction even
though the discovery *shapes* differ: a rule id always reaches the catalog because it
exists as a string literal *somewhere* in the package source — the RHS of a
``*_RULE_ID = "..."`` constant, a ``ParseRule(rule_id="...")`` registration, a
``_make_witness("...")`` call arg, or a ``"uk_manual_frontier_..."`` literal.  So the
location pass is independent of how the *set* was discovered: each jurisdiction keeps
its own discovery (the discovered population is unchanged), and this helper only adds
file:line provenance for the ids it is handed.  The literal regex is parameterizable so
a caller can scope the scan (e.g. only quoted occurrences) if needed.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

# Matches a rule id appearing as a quoted string literal: ``"id"`` or ``'id'``.
# ``re.escape`` is applied to the concrete id at call time, so this template only
# needs the surrounding-quote anchors.
_QUOTE = r"""['"]"""


def locate_rule_ids(
    package_dir: Path,
    rule_ids: Iterable[str],
    *,
    recursive: bool = False,
    repo_root: Path | None = None,
) -> dict[str, list[tuple[str, int]]]:
    """Map each ``rule_id`` to the source ``(relative_file, lineno)`` sites it appears at.

    Scans every ``*.py`` under ``package_dir`` (``rglob`` when ``recursive``) and records
    each 1-based line on which a given id occurs as a quoted string literal.  Paths are
    made relative to ``repo_root`` when given (else to ``package_dir``) for compact,
    paste-ready provenance.  Ids with no literal occurrence map to an empty list (the
    id was discovered via a context the locator cannot see as a literal — still reported,
    just without a site, never silently dropped).
    """
    ids = sorted(set(rule_ids))
    if not ids:
        return {}
    # One compiled alternation over all ids, captured so we know which id matched.
    pattern = re.compile(
        _QUOTE + "(" + "|".join(re.escape(rid) for rid in ids) + ")" + _QUOTE
    )
    base = repo_root if repo_root is not None else package_dir
    locations: dict[str, list[tuple[str, int]]] = {rid: [] for rid in ids}
    paths = package_dir.rglob("*.py") if recursive else package_dir.glob("*.py")
    for path in sorted(paths):
        rel = _relative(path, base)
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in pattern.finditer(line):
                locations[match.group(1)].append((rel, lineno))
    return locations


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def format_uncataloged(
    uncataloged: Iterable[str],
    locations: dict[str, list[tuple[str, int]]],
) -> str:
    """Render an uncataloged-id list with each id's emit site(s), one per line.

    ``us_amend_strike_insert_tail  <- us_federal/amendatory.py:55, us_federal/amendatory.py:2086``

    Ids with no located literal are annotated ``<no literal emit site found>`` so the
    omission is still loud (e.g. a context-resolved witness id) rather than silent.
    """
    lines: list[str] = []
    for rid in sorted(set(uncataloged)):
        sites = locations.get(rid) or []
        if sites:
            rendered = ", ".join(f"{rel}:{lineno}" for rel, lineno in sites)
        else:
            rendered = "<no literal emit site found>"
        lines.append(f"{rid}  <- {rendered}")
    return "\n".join(lines)


def believed_spec_skeleton(
    uncataloged: Iterable[str],
    locations: dict[str, list[tuple[str, int]]],
) -> str:
    """Paste-ready ``believed_spec`` skeleton lines, one per uncataloged id.

    Each line is a dict entry stub with the emit site as a trailing comment, so a
    maintainer closing the gate can paste the block and fill in the hypothesis prose::

        "us_amend_strike_insert_tail": "",  # us_federal/amendatory.py:55
    """
    lines: list[str] = []
    for rid in sorted(set(uncataloged)):
        sites = locations.get(rid) or []
        site = sites[0] if sites else None
        comment = f"  # {site[0]}:{site[1]}" if site else "  # <no literal emit site>"
        lines.append(f'    "{rid}": "",{comment}')
    return "\n".join(lines)
