"""Authority-grounding bridge for Estonia (EE) compiler rules.

This is the *source-only* side of the correct-by-construction compiler
(``AGENTS.md`` §2.1): it attaches drafting/legal-technique authority to an EE
``ee_*`` rule_id, distinct from the oracle-derived ``believed_spec`` hypothesis
carried by the spec-discovery ledger.  It reads a machine-readable mapping
(``data/ee/spec_authority_grounding.json``) keyed by rule_id and exposes it as
frozen ``EEAuthorityGrounding`` rows.

It is a standalone library, deliberately mirroring
``src/lawvm/tools/spec_authority.py`` (the UK grounding loader) without importing
or editing it: a ledger report can call ``render_ee_grounding_column`` to add an
authority column, but this module does **not** touch any replay/compile path and
has no side effects.

Honesty over coverage (``AGENTS.md`` §0):

EE grounding is thinner than the UK's.  The UK frontend grounds its rules in
acquired official OPC drafting guidance (``notes/UK_OFFICIAL_DRAFTING_SOURCE_LEDGER.md``);
EE has no acquired external good-legislative-drafting source.  Neither the EE
living spec (``notes/ESTONIA_FRONTEND_LIVING_SPEC.md``) nor the EE code cites
HÕNTE (*Hea õigusloome ja normitehnika eeskiri*) or Riigi Teataja / Riigikogu
drafting conventions for any rule.  Every EE rule is therefore grounded only in
the living spec's own rule statement (``authority_kind="internal_spec"``).  An
``internal_spec`` row must not claim an external ``source_ref``; inventing a HÕNTE
citation would be fabricating legal authority.  The ``external`` kind exists in
the schema only so a future acquisition of a real EE drafting source can be
recorded faithfully.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal

# Status vocabulary mirrors the EE living spec's framing: HAVE (implemented /
# verified per spec), GAP (not handled / partial), SPEC (diagnostic /
# evidence-lane / known-limitation, no settled body replay).
Status = Literal["HAVE", "GAP", "SPEC"]
VALID_STATUSES: frozenset[str] = frozenset({"HAVE", "GAP", "SPEC"})

# An EE rule is grounded either in a real documented external drafting authority
# (HÕNTE / RT / Riigikogu convention) that the living spec or code references, or
# only in the living spec's own rule statement.  Today every EE rule is the
# latter; see the module docstring.
AuthorityKind = Literal["external", "internal_spec"]
VALID_AUTHORITY_KINDS: frozenset[str] = frozenset({"external", "internal_spec"})


@dataclass(frozen=True, slots=True)
class EEAuthorityGrounding:
    """One EE rule_id grounded in drafting/legal-technique authority.

    ``rule_id`` is an ``ee_*`` _RULE constant string from
    ``src/lawvm/estonia/peg.py`` or ``grafter.py``.  ``source_ref`` cites the
    grounding authority; for ``internal_spec`` rows it is the EE living-spec
    section that states the rule.
    """

    rule_id: str
    authority_kind: AuthorityKind
    source_ref: str
    authority_grounding_status: Status
    ledger_section: str = ""
    note: str = ""


def _grounding_path() -> Path:
    # src/lawvm/estonia/ee_authority_grounding.py -> repo_root/data/ee/...
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "ee"
        / "spec_authority_grounding.json"
    )


def load_ee_authority_grounding(
    path: Path | None = None,
) -> Dict[str, EEAuthorityGrounding]:
    """Load the EE authority-grounding map keyed by ``rule_id``.

    Pure: no caching, no mutation of inputs, deterministic for a given file.
    Raises on a malformed file (bad status / authority_kind, duplicate rule_id,
    or an ``internal_spec`` row carrying an empty source_ref) rather than
    silently dropping or weakening rows, so a grounding entry can never vanish or
    be quietly misclassified (``AGENTS.md`` §1.8).
    """
    src = path or _grounding_path()
    raw = json.loads(src.read_text(encoding="utf-8"))
    groundings: Dict[str, EEAuthorityGrounding] = {}
    for row in raw["groundings"]:
        rule_id = row["rule_id"]
        if rule_id in groundings:
            raise ValueError(f"duplicate rule_id in EE grounding file: {rule_id!r}")
        status = row["status"]
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r} for rule {rule_id!r}")
        authority_kind = row["authority_kind"]
        if authority_kind not in VALID_AUTHORITY_KINDS:
            raise ValueError(
                f"invalid authority_kind {authority_kind!r} for rule {rule_id!r}"
            )
        source_ref = row["source_ref"]
        if authority_kind == "internal_spec" and not source_ref:
            raise ValueError(
                f"internal_spec rule {rule_id!r} must cite a living-spec source_ref"
            )
        groundings[rule_id] = EEAuthorityGrounding(
            rule_id=rule_id,
            authority_kind=authority_kind,
            source_ref=source_ref,
            authority_grounding_status=status,
            ledger_section=row.get("ledger_section", ""),
            note=row.get("note", ""),
        )
    return groundings


def render_ee_grounding_column(
    rule_id: str,
    grounding: Dict[str, EEAuthorityGrounding] | None = None,
) -> str:
    """Render a one-line authority-grounding column for an EE rule_id.

    Pure helper for a ledger report.  Returns a compact
    ``status·kind·source_ref`` string, or ``"-"`` when the rule has no recorded
    grounding (itself an honest signal: the rule is oracle-grounded only).
    """
    table = grounding if grounding is not None else load_ee_authority_grounding()
    entry = table.get(rule_id)
    if entry is None:
        return "-"
    return f"{entry.authority_grounding_status} · {entry.authority_kind} · {entry.source_ref}"
