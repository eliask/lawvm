"""Probe: classify enum dispatch sites across core + finland.

For every Enum/StrEnum defined in src/lawvm/{core,finland}, find dispatch
sites and classify them:

  - match_exhaustive : a `match` statement over the enum with NO wildcard
    `case _` AND followed (statically) by an assert_never on the fall-through.
  - match_wildcard   : a `match` with a `case _` catch-all (silent other).
  - if_elif_chain    : an if/elif chain comparing the value against >=2
    distinct enum members (silent fall-through if no exhaustiveness guard).
  - dict_dispatch    : a dict literal keyed by enum members used with
    `.get(x, default)` (silent default) or subscript.

This is a heuristic structural classifier, not a type-checker. It is meant
to *rank* enums by how many non-exhaustive dispatch sites they have so a
human can target the highest-EV `match`+`assert_never` conversions.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOTS = [
    Path("src/lawvm/core"),
    Path("src/lawvm/finland"),
]


@dataclass
class EnumInfo:
    name: str
    members: set[str] = field(default_factory=set)
    defined_in: str = ""


@dataclass
class Site:
    enum: str
    kind: str  # match_exhaustive|match_wildcard|if_elif_chain|dict_dispatch
    file: str
    line: int
    detail: str = ""


def collect_enums(files: list[Path]) -> dict[str, EnumInfo]:
    enums: dict[str, EnumInfo] = {}
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = set()
            for b in node.bases:
                base_names.add(ast.unparse(b))
            if not any("Enum" in bn for bn in base_names):
                continue
            info = EnumInfo(name=node.name, defined_in=str(f))
            for stmt in node.body:
                # MEMBER = "value"  (assign with simple target, value is constant/Call)
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    tgt = stmt.targets[0]
                    if isinstance(tgt, ast.Name) and tgt.id.isupper():
                        info.members.add(tgt.id)
                    elif isinstance(tgt, ast.Name) and tgt.id[:1].isupper() and not tgt.id.startswith("_"):
                        # allow CamelCase-ish member names too (rare)
                        if tgt.id.isupper() or tgt.id.replace("_", "").isalnum():
                            info.members.add(tgt.id)
            if info.members:
                enums[node.name] = info
    return enums


def member_refs_in(node: ast.AST, enum_name: str) -> set[str]:
    """Find EnumName.MEMBER attribute refs inside node."""
    found = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == enum_name:
            found.add(n.attr)
    return found


def classify_match(node: ast.Match, enums: dict[str, EnumInfo]) -> tuple[str, str, str] | None:
    """Return (enum_name, kind, detail) for a match statement, or None."""
    # Determine which enum the cases reference
    member_to_enum: dict[str, str] = {}
    for ename, info in enums.items():
        for m in info.members:
            member_to_enum.setdefault(m, ename)
    referenced_enums: dict[str, int] = defaultdict(int)
    has_wildcard = False
    for case in node.cases:
        pat = case.pattern
        # case _:
        if isinstance(pat, ast.MatchAs) and pat.pattern is None and pat.name is None:
            has_wildcard = True
            continue
        # case EnumName.MEMBER:
        for sub in ast.walk(pat):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id in enums:
                referenced_enums[sub.value.id] += 1
    if not referenced_enums:
        return None
    ename = max(referenced_enums, key=lambda k: referenced_enums[k])
    n_cases = referenced_enums[ename]
    if has_wildcard:
        return (ename, "match_wildcard", f"{n_cases} member cases + wildcard")
    return (ename, "match_no_wildcard", f"{n_cases} member cases, no wildcard")


def analyze_file(f: Path, enums: dict[str, EnumInfo]) -> list[Site]:
    sites: list[Site] = []
    src = f.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return sites
    has_assert_never = "assert_never" in src

    for node in ast.walk(tree):
        # match statements
        if isinstance(node, ast.Match):
            res = classify_match(node, enums)
            if res:
                ename, kind, detail = res
                if kind == "match_no_wildcard":
                    kind = "match_exhaustive" if has_assert_never else "match_no_wildcard_noguard"
                sites.append(Site(ename, kind, str(f), node.lineno, detail))
            continue

        # if/elif chains comparing enum value to >=2 members
        if isinstance(node, ast.If):
            # collect the whole if/elif chain
            members_compared: dict[str, set[str]] = defaultdict(set)
            cur: ast.If | None = node
            chain_len = 0
            # avoid double-counting nested: only treat top-level If (not in orelse of another If handled by walk)
            while cur is not None:
                chain_len += 1
                for sub in ast.walk(cur.test):
                    if isinstance(sub, ast.Compare):
                        for operand in [sub.left, *sub.comparators]:
                            if (
                                isinstance(operand, ast.Attribute)
                                and isinstance(operand.value, ast.Name)
                                and operand.value.id in enums
                            ):
                                members_compared[operand.value.id].add(operand.attr)
                if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                    cur = cur.orelse[0]
                else:
                    cur = None
            for ename, mset in members_compared.items():
                if len(mset) >= 2:
                    sites.append(
                        Site(ename, "if_elif_chain", str(f), node.lineno, f"{len(mset)} distinct members across {chain_len} branches")
                    )

        # dict dispatch: {EnumName.X: ...} possibly used with .get
        if isinstance(node, ast.Dict):
            key_enums: dict[str, int] = defaultdict(int)
            for k in node.keys:
                if isinstance(k, ast.Attribute) and isinstance(k.value, ast.Name) and k.value.id in enums:
                    key_enums[k.value.id] += 1
            for ename, cnt in key_enums.items():
                if cnt >= 2:
                    sites.append(Site(ename, "dict_dispatch", str(f), node.lineno, f"{cnt} enum keys"))

    return sites


def main() -> int:
    files: list[Path] = []
    for root in ROOTS:
        files.extend(p for p in root.rglob("*.py") if "test" not in p.name)
    enums = collect_enums(files)

    all_sites: list[Site] = []
    for f in files:
        all_sites.extend(analyze_file(f, enums))

    # Aggregate per enum
    by_enum: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in all_sites:
        by_enum[s.enum][s.kind] += 1

    NON_EXHAUSTIVE = {"match_wildcard", "match_no_wildcard_noguard", "if_elif_chain", "dict_dispatch"}

    rows = []
    for ename, kinds in by_enum.items():
        non_ex = sum(v for k, v in kinds.items() if k in NON_EXHAUSTIVE)
        n_members = len(enums[ename].members)
        rows.append((non_ex, ename, n_members, dict(kinds)))
    rows.sort(reverse=True)

    print("=== ENUM DISPATCH CLASSIFICATION (ranked by non-exhaustive dispatch sites) ===")
    print(f"{'non_ex':>6}  {'members':>7}  enum  (defined_in)")
    print("-" * 100)
    for non_ex, ename, n_members, kinds in rows:
        if non_ex == 0:
            continue
        defined = enums[ename].defined_in.replace("src/lawvm/", "")
        print(f"{non_ex:>6}  {n_members:>7}  {ename}  ({defined})")
        for k, v in sorted(kinds.items()):
            print(f"            {k}: {v}")

    print("\n=== ENUMS WITH ZERO DETECTED NON-EXHAUSTIVE DISPATCH ===")
    zero = [e for e in enums if sum(v for k, v in by_enum[e].items() if k in NON_EXHAUSTIVE) == 0]
    print(f"count={len(zero)} (of {len(enums)} total enums)")

    print("\n=== SITE DETAIL (non-exhaustive only), grouped by enum ===")
    sites_by_enum: dict[str, list[Site]] = defaultdict(list)
    for s in all_sites:
        if s.kind in NON_EXHAUSTIVE:
            sites_by_enum[s.enum].append(s)
    for non_ex, ename, _n, _k in rows:
        if non_ex == 0:
            continue
        print(f"\n## {ename} ({non_ex} sites)")
        for s in sorted(sites_by_enum[ename], key=lambda x: (x.file, x.line)):
            print(f"  {s.kind:28} {s.file.replace('src/lawvm/','')}:{s.line}  [{s.detail}]")

    print(f"\n=== TOTALS ===")
    print(f"enums defined: {len(enums)}")
    print(f"total dispatch sites detected: {len(all_sites)}")
    tot_kinds: dict[str, int] = defaultdict(int)
    for s in all_sites:
        tot_kinds[s.kind] += 1
    for k, v in sorted(tot_kinds.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
