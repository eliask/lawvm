"""Authority-grounding bridge for UK spec-discovery rules (Stream C).

This is the *source-only* side of the correct-by-construction compiler
(``AGENTS.md`` §2.1): it attaches official UK legislative-drafting authority to a
LawVM rule, distinct from the oracle-derived ``believed_spec`` hypothesis carried
by ``spec_ledger``.  It reads a machine-readable mapping mined from
``notes/UK_OFFICIAL_DRAFTING_SOURCE_LEDGER.md`` and exposes it as frozen
``AuthorityGrounding`` rows.

It is a standalone library: a ledger report can call
``render_grounding_column`` to add an authority column, but this module does
**not** import or edit ``spec_ledger`` and has no replay-path side effects.

The grounding is honest, not aspirational: a rule the note marks ``GAP`` or
``SPEC`` is recorded as such here, even where adjacent rules are ``HAVE``.  The
point is faithful grounding in real drafting guidance, not inflating coverage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal

# Status vocabulary mirrors the note's legend (§ "Mined rules -> LawVM
# destinations"):  HAVE (implemented, verify), GAP (not handled / partial),
# SPEC (diagnostic / spec-yield, no replay).
Status = Literal["HAVE", "GAP", "SPEC"]
VALID_STATUSES: frozenset[str] = frozenset({"HAVE", "GAP", "SPEC"})

# A rule_id either names a real uk_legislation witness_rule_id constant or is a
# stable family key for a guidance section the note describes without a single
# named LawVM constant.
KeyKind = Literal["witness_rule_id", "guidance_family"]
VALID_KEY_KINDS: frozenset[str] = frozenset({"witness_rule_id", "guidance_family"})


@dataclass(frozen=True, slots=True)
class AuthorityGrounding:
    """One rule_id grounded in official UK drafting authority.

    ``authority_tier`` is ``int | str`` because the note uses split tiers such as
    ``"1/2"`` for sources that sit between Tier 1 guidance and Tier 2 manuals.
    """

    rule_id: str
    authority_tier: int | str
    source_ref: str
    authority_status: Status
    key_kind: KeyKind = "witness_rule_id"
    ledger_section: str = ""
    note: str = ""


def _grounding_path() -> Path:
    # src/lawvm/tools/spec_authority.py -> repo_root/data/uk/...
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "uk"
        / "spec_authority_grounding.json"
    )


def load_uk_authority_grounding(
    path: Path | None = None,
) -> Dict[str, AuthorityGrounding]:
    """Load the UK authority-grounding map keyed by ``rule_id``.

    Pure: no caching, no mutation of inputs, deterministic for a given file.
    Raises on a malformed file rather than silently dropping rows, so a
    grounding entry can never vanish unnoticed (``AGENTS.md`` §1.8).
    """
    src = path or _grounding_path()
    raw = json.loads(src.read_text(encoding="utf-8"))
    groundings: Dict[str, AuthorityGrounding] = {}
    for row in raw["groundings"]:
        rule_id = row["rule_id"]
        if rule_id in groundings:
            raise ValueError(f"duplicate rule_id in grounding file: {rule_id!r}")
        groundings[rule_id] = AuthorityGrounding(
            rule_id=rule_id,
            authority_tier=row["authority_tier"],
            source_ref=row["source_ref"],
            authority_status=row["status"],
            key_kind=row.get("key_kind", "witness_rule_id"),
            ledger_section=row.get("ledger_section", ""),
            note=row.get("note", ""),
        )
    return groundings


def render_grounding_column(
    rule_id: str,
    grounding: Dict[str, AuthorityGrounding] | None = None,
) -> str:
    """Render a one-line authority-grounding column for a rule_id.

    Pure helper for a ledger report.  Returns a compact ``status·tier·source_ref``
    string, or ``"-"`` when the rule has no official authority grounding (which is
    itself an honest signal: the rule is oracle-grounded only).
    """
    table = grounding if grounding is not None else load_uk_authority_grounding()
    entry = table.get(rule_id)
    if entry is None:
        return "-"
    return f"{entry.authority_status} · T{entry.authority_tier} · {entry.source_ref}"
