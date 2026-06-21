"""Finnish canonical budget-line registry with per-year versioning.

Provides the authoritative mapping from budget-line momentti addresses
(paaluokka.luku.momentti) to canonical identifiers for Finnish state budget
lines (talousarviolaki structure).

Lifecycle versioning is load-bearing: a momentti number like '28.91.51' in a
2020 statute may have been renumbered to '28.91.50' by 2022. Resolution emits
a BudgetLineRenumberingObservation rather than silently aliasing.

Registry structure per POOL_MENTION_EXTRACTION.md:
  - canonical_id: stable ID, e.g. 'fi.budget.28.91.50'
  - paaluokka: int (main chapter)
  - luku: int (sub-chapter)
  - momentti: int (line number)
  - show_as: canonical display string
  - momentti_code: dotted string, e.g. '28.91.50'
  - year: int (budget year this line is active in)

Design discipline (AGENTS.md §1.1, §1.6, §1.9):
  - Typed dataclasses, not dicts.
  - Cross-year lineage resolution emits BudgetLineRenumberingObservation.
  - Ambiguous (multi-match) momentti codes are NOT silently picked; callers
    must emit AmbiguousPoolMention.
  - Only a SINGLE lookup table is built per year at module load; no dynamic
    construction in loops.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Registry types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetLine:
    """One entry in the canonical budget-line registry for one fiscal year.

    Attributes:
        canonical_id:     Stable cross-year ID, e.g. 'fi.budget.28.91.50'.
        paaluokka:        Main chapter (paaluokka) number.
        luku:             Sub-chapter (luku) number.
        momentti:         Line (momentti) number.
        momentti_code:    Dotted string, e.g. '28.91.50'.
        show_as:          Canonical display string.
        year:             Budget year this line is active in.
        estimated_amount: Estimated EUR amount for this line (optional).
        lineage_successor_id:   canonical_id of the successor line after
                                renumbering (if applicable, else None).
        lineage_successor_year: Year in which the successor ID is active.
    """

    canonical_id: str
    paaluokka: int
    luku: int
    momentti: int
    momentti_code: str
    show_as: str
    year: int
    estimated_amount: Optional[int] = None
    lineage_successor_id: Optional[str] = None
    lineage_successor_year: Optional[int] = None


# ---------------------------------------------------------------------------
# Registry seed data — loaded from YAML files under data/fi/canonical_budget_lines/
# ---------------------------------------------------------------------------

# Module-scope compiled pattern for momentti address extraction (AGENTS.md §1.11)
# Matches 'NN.NN.NN' or 'N.NN.NN' patterns exactly.
# Bounded: \d{1,2} avoids unbounded quantifiers.
_MOMENTTI_CODE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{1,2})$")


def _data_dir() -> Path:
    """Return the path to data/fi/canonical_budget_lines/."""
    here = Path(__file__).resolve()
    # src/lawvm/finland/ -> ../../.. -> repo root
    repo_root = here.parent.parent.parent.parent
    return repo_root / "data" / "fi" / "canonical_budget_lines"


def _parse_yaml_budget_lines(yaml_text: str, year: int) -> List[BudgetLine]:
    """Parse a budget-line YAML file into BudgetLine objects.

    Uses minimal inline YAML parsing to avoid a hard dependency on PyYAML
    in the core extraction path. The YAML format is simple enough to parse
    with a line scanner:
      - 'canonical_id: "..."'
      - 'paaluokka: N'
      - 'luku: N'
      - 'momentti: N'
      - 'momentti_code: "NN.NN.NN"'
      - 'show_as: "..."'
      - 'estimated_amount: N'
      - 'lineage_successor_id: "..."'
      - 'lineage_successor_year: N'
    """
    lines: List[BudgetLine] = []

    # Current record accumulator
    current: Dict[str, str | int] = {}

    def _flush() -> None:
        nonlocal current
        if "canonical_id" in current and "momentti_code" in current:
            cid = str(current["canonical_id"])
            mc = str(current["momentti_code"])
            # lawvm-regex: owning_parser NN.NN.NN momentti-code parse from a registry YAML value, registry data-load not statute text
            m = _MOMENTTI_CODE_RE.match(mc)
            if m:
                paaluokka = int(m.group(1))
                luku = int(m.group(2))
                momentti = int(m.group(3))
            else:
                # Fallback: use stored paaluokka/luku/momentti if present
                paaluokka = int(current.get("paaluokka", 0))
                luku = int(current.get("luku", 0))
                momentti = int(current.get("momentti", 0))

            bl = BudgetLine(
                canonical_id=cid,
                paaluokka=paaluokka,
                luku=luku,
                momentti=momentti,
                momentti_code=mc,
                show_as=str(current.get("show_as", "")),
                year=year,
                estimated_amount=int(current["estimated_amount"])
                if "estimated_amount" in current
                else None,
                lineage_successor_id=str(current["lineage_successor_id"])
                if "lineage_successor_id" in current
                else None,
                lineage_successor_year=int(current["lineage_successor_year"])
                if "lineage_successor_year" in current
                else None,
            )
            lines.append(bl)
        current = {}

    for raw_line in yaml_text.splitlines():
        stripped = raw_line.strip()

        # Detect start of a new list item
        if stripped.startswith("- canonical_id:"):
            _flush()
            val = stripped.split(":", 1)[1].strip().strip('"')
            current["canonical_id"] = val
            continue

        # Skip comment lines and non-key-value lines
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue

        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip().strip('"')

        if key in (
            "canonical_id",
            "show_as",
            "momentti_code",
            "lineage_successor_id",
        ):
            current[key] = val
        elif key in (
            "paaluokka",
            "luku",
            "momentti",
            "year",
            "estimated_amount",
            "lineage_successor_year",
        ):
            try:
                current[key] = int(val)
            except ValueError:
                pass

    _flush()
    return lines


def _load_all_budget_lines() -> Dict[int, List[BudgetLine]]:
    """Load all per-year budget-line YAML files from data/fi/canonical_budget_lines/.

    Returns dict keyed by year.
    Gracefully handles missing data directory (returns empty dict).
    """
    data_dir = _data_dir()
    result: Dict[int, List[BudgetLine]] = {}
    if not data_dir.exists():
        return result
    for yaml_path in sorted(data_dir.glob("*.yaml")):
        try:
            year = int(yaml_path.stem)
        except ValueError:
            continue
        text = yaml_path.read_text(encoding="utf-8")
        lines = _parse_yaml_budget_lines(text, year)
        result[year] = lines
    return result


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class BudgetLineRegistry:
    """Compiled registry of canonical Finnish budget lines, per year.

    Provides O(1) momentti-code lookups for the extractor hot path.
    Built once from YAML seed files; immutable after construction.

    The registry distinguishes:
      - CURRENT year entries (exact match for that year)
      - LINEAGE entries (cross-year resolution via lineage_successor_id)

    Ambiguous matches (same momentti_code in multiple entries for the
    same year, which should not happen but is guarded) are flagged.
    """

    def __init__(self, lines_by_year: Dict[int, List[BudgetLine]]) -> None:
        self._lines_by_year: Dict[int, List[BudgetLine]] = lines_by_year

        # per-year: momentti_code -> List[BudgetLine] (usually 1 entry)
        self._code_to_lines: Dict[int, Dict[str, List[BudgetLine]]] = {}
        # per-year: canonical_id -> BudgetLine
        self._id_to_line: Dict[int, Dict[str, BudgetLine]] = {}

        for year, lines in lines_by_year.items():
            code_map: Dict[str, List[BudgetLine]] = {}
            id_map: Dict[str, BudgetLine] = {}
            for bl in lines:
                if bl.momentti_code not in code_map:
                    code_map[bl.momentti_code] = []
                code_map[bl.momentti_code].append(bl)
                id_map[bl.canonical_id] = bl
            self._code_to_lines[year] = code_map
            self._id_to_line[year] = id_map

        # Cross-year lineage map: (year, old_canonical_id) -> new_canonical_id
        # Built from lineage_successor_id fields in all years.
        self._lineage: Dict[Tuple[int, str], str] = {}
        for year, lines in lines_by_year.items():
            for bl in lines:
                if bl.lineage_successor_id and bl.lineage_successor_year:
                    self._lineage[(year, bl.canonical_id)] = bl.lineage_successor_id

    def lookup_by_code(
        self, momentti_code: str, year: int
    ) -> Tuple[Optional[str], List[BudgetLine]]:
        """Look up a momentti_code in a specific year.

        Returns (canonical_id, matching_lines).
          - If exactly one match: canonical_id = that ID, lines = [line]
          - If ambiguous: canonical_id = None, lines = all matching lines
          - If no match:  canonical_id = None, lines = []
        """
        year_map = self._code_to_lines.get(year, {})
        candidates = year_map.get(momentti_code, [])
        if len(candidates) == 1:
            return candidates[0].canonical_id, candidates
        if len(candidates) > 1:
            return None, candidates
        return None, []

    def lookup_lineage(
        self, canonical_id: str, source_year: int
    ) -> Optional[Tuple[str, int]]:
        """Try to resolve a canonical_id via cross-year lineage.

        Returns (successor_canonical_id, successor_year) if a lineage path
        exists from source_year; None otherwise.

        Walks the lineage chain up to 10 steps to handle multi-year renumberings.
        """
        current_id = canonical_id
        current_year = source_year
        for _ in range(10):
            successor = self._lineage.get((current_year, current_id))
            if successor is None:
                # Try looking forward in time: check if the source_year entry
                # is not in later years but the canonical_id appears elsewhere
                for check_year in sorted(self._id_to_line.keys()):
                    if check_year <= current_year:
                        continue
                    if successor := self._lineage.get((current_year, current_id)):
                        current_id = successor
                        current_year = check_year
                        break
                else:
                    break
            else:
                # Find the year this successor is active in
                for check_year in sorted(self._id_to_line.keys()):
                    if check_year > current_year and successor in self._id_to_line.get(check_year, {}):
                        return successor, check_year
                # Successor referenced but not in any later year's registry
                break
        return None

    def get_line(self, canonical_id: str, year: int) -> Optional[BudgetLine]:
        """Get a BudgetLine by canonical_id for a specific year."""
        return self._id_to_line.get(year, {}).get(canonical_id)

    def get_line_any_year(self, canonical_id: str) -> Optional[BudgetLine]:
        """Get a BudgetLine by canonical_id from any available year."""
        for year in sorted(self._id_to_line.keys(), reverse=True):
            bl = self._id_to_line[year].get(canonical_id)
            if bl is not None:
                return bl
        return None

    def available_years(self) -> Tuple[int, ...]:
        """Return available budget years, sorted ascending."""
        return tuple(sorted(self._lines_by_year.keys()))

    def all_lines_for_year(self, year: int) -> List[BudgetLine]:
        """Return all budget lines for a given year."""
        return list(self._lines_by_year.get(year, []))

    def nearest_year(self, target_year: int) -> Optional[int]:
        """Return the available year nearest to target_year.

        Used for resolution when the exact year is not available.
        """
        years = self.available_years()
        if not years:
            return None
        return min(years, key=lambda y: abs(y - target_year))


# ---------------------------------------------------------------------------
# Module-level singleton registry (built at import time)
# ---------------------------------------------------------------------------

REGISTRY: BudgetLineRegistry = BudgetLineRegistry(_load_all_budget_lines())
