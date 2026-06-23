"""lawvm — a COUNTERFACTUAL "what does this amendment actually do" report.

This is a READ-ONLY projection that answers, for one Finnish amending statute,
"which provisions of the law does this bill actually move?" — split into THREE
structurally distinct tiers that are kept SEPARATE and NEVER conflated.  The
honesty boundary (tier 3) is PART of the result: what this module declines to
compute is declared, not silently omitted.

The three tiers (one builder each — never merged):

* **TIER 1 — directly changed.**  The provisions the amendment's own operations
  mutate.  Source: the johtolause parsed to ``ParsedOp`` (the resolved-op target
  set) via :func:`lawvm.finland.johtolause.api.parse_clause`.  Each item is a
  provision target (§ / luku / momentti / kohta) in the AMENDED act, tagged with
  its verb (insert / amend / repeal / renumber).  Cheaply derivable; this is the
  same op list ``analyze-bill`` v0 lowers — here it is the FIRST tier, never
  folded into a "surface delta".

* **TIER 2 — changed via references (and, deferred, definitions).**  Provisions
  whose meaning / applicability shifts because they CITE a tier-1-changed
  provision.  Source: the interlink graph of the AMENDED act
  (:func:`lawvm.finland.interlink_targets.project_fi_interlinks_partition`):
  every resolved internal citation in the amended act whose target locator names
  a tier-1-changed section is a tier-2 effect — a back-reference whose
  applicability now depends on changed text.  ONLY citations whose resolution
  status is ``resolved`` / ``unchanged`` (a real ``target_work_id`` pointing back
  into the amended act) are traced; ``statute_only`` / ``ambiguous`` / ``open`` /
  ``broken`` citations CANNOT be traced to a provision and therefore flow to
  tier 3 as a declared limit, never guessed.

  The *definition-user* sub-tier (provisions whose meaning shifts because a
  tier-1 op changed a DEFINITION they use) is DEFERRED in v1 — see the tier-3
  boundary.  The reason is structural and declared: the definition graph
  (:func:`lawvm.finland.references.definition_graph.build_definition_graph`)
  anchors bindings by BYTE OFFSET into an assembled body string, not by section
  label, so "is this binding inside a tier-1-changed section?" needs an
  offset→section crosswalk this module does not yet build.  Landing it requires
  that crosswalk and is a near-term extension, NOT a silent gap.

* **TIER 3 — uncomputed second-order.**  A DECLARED boundary: a statement plus an
  effect-class list, NOT a computation.  It names the effect classes this report
  does NOT compute (semantic / teleological impact, multi-hop cascades — only the
  1-hop back-reference is computed, temporal / contingency cascades, institutional
  / capacity effects, transnational transposition cascades) and the resolution-
  status limit (untraceable citations) and the deferred definition-user sub-tier.

DISCIPLINE.  Like ``analyze-bill`` v0, this report carries NO score, NO
magnitude, NO severity — it reports WHAT moves, never how much it matters.  Every
tier-1 / tier-2 item carries PROVENANCE (the source machine + the node id) so the
finding is auditable from the row alone.  It is a pure projection: it reads the
amendment SOURCE XML, the amended act's interlink projection, and the johtolause
parse — it NEVER touches the replay / apply path, the substrate source, or
``finland/metadata.py`` mutation.

Rendering and assembly live in pure ``build_*`` functions that take already-built
inputs (a list of ``ParsedOp`` and a list of ``LawvmInterlinkRow``) and return
frozen dataclasses, so every builder is testable on synthetic fixtures with NO
corpus dependency.  The CLI / corpus handler is a thin wrapper; fail-loud: a
missing statute or a johtolause parse error raises ``SystemExit`` with the
offending id, never a silent empty report.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lawvm.finland.johtolause.types import ParsedOp
    from lawvm.tools.transition_graph_interlinks import LawvmInterlinkRow


# ---------------------------------------------------------------------------
# Provenance / vocabulary constants (read-only; mirror the producers' labels)
# ---------------------------------------------------------------------------

#: provenance source machines (which producer a tier-1/2 item came from)
SOURCE_JOHTOLAUSE_PARSE = "johtolause_parse"
SOURCE_INTERLINK_GRAPH = "interlink_graph"
SOURCE_DEFINITION_GRAPH = "definition_graph"

#: the ONLY interlink resolution states a tier-2 back-reference may be traced
#: from (a real target_work_id pointing back into the amended act).  Every other
#: state is untraceable and flows to the tier-3 declared limit, never guessed.
_TRACEABLE_STATUSES = frozenset({"resolved", "unchanged"})

_VERB_LABELS = {
    "M": "AMEND",
    "K": "REPEAL",
    "L": "INSERT",
    "S": "RENUMBER",
}

_SECTION_LOCATOR_RE = re.compile(r"(?:^|/)section:([^/]+)")


# ---------------------------------------------------------------------------
# Typed tier rows (frozen, provenance-carrying)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DirectEffect:
    """One TIER-1 directly-changed provision (a johtolause op target).

    Attributes:
        verb:        Operation code M/K/L/S.
        verb_label:  Human label (AMEND / REPEAL / INSERT / RENUMBER).
        section:     The § (or chapter, for chapter ops) number the op targets.
        chapter:     Chapter context, "" when none.
        momentti:    Subsection ordinal (0 = whole section).
        item:        Item (kohta) label, "" when none.
        op_code:     Canonical op-code string (``ParsedOp.code()``).
        source:      Provenance machine — always ``johtolause_parse``.
        node_id:     Provenance id — the op-code (the op's stable identity here).
    """

    verb: str
    verb_label: str
    section: str
    chapter: str
    momentti: int
    item: str
    op_code: str
    source: str
    node_id: str


@dataclass(frozen=True, slots=True)
class CitingProvisionEffect:
    """One TIER-2 provision that CITES a tier-1-changed provision.

    A resolved internal back-reference in the AMENDED act: provision
    ``citing_section`` cites ``cited_section`` (which a tier-1 op changed), so its
    applicability now depends on changed text.

    Attributes:
        citing_section:   The provision (in the amended act) that makes the cite.
        cited_section:    The tier-1-changed § it points at.
        role:             The interlink role (e.g. ``cites``).
        resolution_status: The (traceable) interlink resolution status.
        source:           Provenance machine — always ``interlink_graph``.
        node_id:          Provenance id — the interlink row's stable id.
    """

    citing_section: str
    cited_section: str
    role: str
    resolution_status: str
    source: str
    node_id: str


@dataclass(frozen=True, slots=True)
class UncomputedBoundary:
    """The TIER-3 DECLARED boundary — a statement + effect-class list, NOT a
    computation.

    Attributes:
        statement:        The honesty statement (what this report does / does not
                          compute).
        effect_classes:   The named second-order effect classes this report does
                          NOT compute.
        resolution_limits: The untraceable-citation classes that flow here instead
                          of being guessed (count + status names).
        deferred:         Declared near-term extensions (e.g. the definition-user
                          sub-tier of tier 2) that are out of v1, not silent gaps.
    """

    statement: str
    effect_classes: tuple[str, ...]
    resolution_limits: dict[str, Any]
    deferred: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CounterfactualEffectsReport:
    """The full three-tier counterfactual effects report for one amendment.

    The three tiers are SEPARATE fields and are never merged.  Every tier-1 / 2
    item carries provenance.  No score, no magnitude.

    Attributes:
        amendment_id:     The amending statute id (the bill).
        amended_act_id:   The act the amendment modifies (tier-1/2 are about THIS
                          act's provisions), or "" when it could not be resolved.
        tier_1_direct:    Provisions the amendment's ops directly mutate.
        tier_2_via_defs_and_refs: The tier-2 account; ``citing_provisions`` are the
                          resolved back-references; ``definition_users`` is the
                          DEFERRED sub-tier (always empty in v1, declared in the
                          boundary).
        tier_3_uncomputed_second_order: the declared boundary.
    """

    amendment_id: str
    amended_act_id: str
    tier_1_direct: tuple[DirectEffect, ...]
    tier_2_via_defs_and_refs: dict[str, Any]
    tier_3_uncomputed_second_order: UncomputedBoundary


# ---------------------------------------------------------------------------
# TIER 1 — directly-changed provisions (pure)
# ---------------------------------------------------------------------------


def build_tier_1_direct(parsed_ops: list["ParsedOp"]) -> tuple[DirectEffect, ...]:
    """Project the johtolause ops into the tier-1 directly-changed provisions.

    Pure: takes the lowered ``ParsedOp`` list (the resolved-op target set) and
    returns one :class:`DirectEffect` per op, each tagged with provenance
    ``johtolause_parse`` and the op-code as its node id.  No corpus access.
    """
    out: list[DirectEffect] = []
    for op in parsed_ops:
        code = op.code()
        out.append(
            DirectEffect(
                verb=op.verb,
                verb_label=_VERB_LABELS.get(op.verb, op.verb),
                section=op.number,
                chapter=op.chapter,
                momentti=op.momentti,
                item=op.item,
                op_code=code,
                source=SOURCE_JOHTOLAUSE_PARSE,
                node_id=code,
            )
        )
    return tuple(out)


def _changed_sections(direct: tuple[DirectEffect, ...]) -> frozenset[str]:
    """The distinct § labels tier-1 directly changes (for tier-2 matching).

    Section ops (``ParsedOp.kind == 'P'``) carry the § in ``number``; chapter ops
    carry a chapter number.  Tier-2 back-reference matching is by SECTION locator,
    so we collect the section numbers of the ops that name one.  A label is
    normalized only by stripping surrounding whitespace — the comparison is exact
    against the interlink target's ``section:<label>`` locator.
    """
    return frozenset(d.section.strip() for d in direct if d.section.strip())


# ---------------------------------------------------------------------------
# TIER 2 — changed via references (pure)
# ---------------------------------------------------------------------------


def _section_of_locator(locator: str | None) -> str:
    """The ``section:<label>`` value of an interlink locator, or "" when none."""
    match = _SECTION_LOCATOR_RE.search(locator or "")
    return match.group(1).strip() if match else ""


def build_tier_2_citing_provisions(
    direct: tuple[DirectEffect, ...],
    amended_act_id: str,
    amended_act_interlinks: list["LawvmInterlinkRow"],
) -> tuple[CitingProvisionEffect, ...]:
    """Tier-2 back-references: provisions in the amended act that CITE a changed §.

    Pure (no corpus access): takes the tier-1 result, the amended act id, and the
    AMENDED act's already-projected interlink rows.  An interlink row is a tier-2
    effect iff ALL of:

      * its resolution status is traceable (``resolved`` / ``unchanged``) — every
        other status is untraceable and is NOT guessed (it flows to tier 3);
      * it resolves to the amended act itself (a real ``target_work_id`` matching
        ``amended_act_id`` — an INTERNAL back-reference, the only kind a single-act
        scan can see; cross-act citers are deferred to tier 3);
      * its target locator names a section that tier 1 changed.

    Each hit is a :class:`CitingProvisionEffect` tagged with provenance
    ``interlink_graph`` and the interlink row's stable id.  Results are sorted and
    de-duplicated on (citing_section, cited_section, node_id) for a stable report.
    """
    changed = _changed_sections(direct)
    if not changed:
        return ()
    amended = amended_act_id.strip()
    seen: set[tuple[str, str, str]] = set()
    out: list[CitingProvisionEffect] = []
    for row in amended_act_interlinks:
        if row.resolution_status not in _TRACEABLE_STATUSES:
            continue
        if not _interlink_targets_act(row, amended):
            continue
        cited = _section_of_locator(row.target_locator)
        if not cited or cited not in changed:
            continue
        citing = _section_of_locator(row.source_locator)
        node_id = str(getattr(row, "interlink_id", "") or "")
        key = (citing, cited, node_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            CitingProvisionEffect(
                citing_section=citing,
                cited_section=cited,
                role=str(getattr(row, "role", "") or ""),
                resolution_status=row.resolution_status,
                source=SOURCE_INTERLINK_GRAPH,
                node_id=node_id,
            )
        )
    out.sort(key=lambda e: (e.cited_section, e.citing_section, e.node_id))
    return tuple(out)


def _interlink_targets_act(row: "LawvmInterlinkRow", amended_act_id: str) -> bool:
    """True when the interlink's resolved target work IS the amended act.

    The neutral target work id is namespaced (``fi:normative_act:<id>``); the
    amended act id is the engine ``YEAR/NUMBER``.  We accept a match when the
    engine id appears as the target's local-id tail.  When ``amended_act_id`` is
    unknown ("") we cannot scope the back-reference and report nothing (the
    cross-act / unknown-parent case is a declared tier-3 limit, never guessed).
    """
    if not amended_act_id:
        return False
    target = str(getattr(row, "target_work_id", "") or "")
    if not target:
        return False
    return target == amended_act_id or target.endswith(f":{amended_act_id}")


def _untraceable_status_counts(
    amended_act_interlinks: list["LawvmInterlinkRow"],
) -> dict[str, int]:
    """Count, per status, the AMENDED-act-internal citations that name a section
    but could NOT be traced because their resolution status is untraceable.

    These are the citations that WOULD have been candidate tier-2 effects had they
    resolved — they are surfaced in the tier-3 resolution limits so the boundary is
    self-evidencing (it names HOW MANY effects the resolver could not reach), never
    silently dropped.
    """
    counts: dict[str, int] = {}
    for row in amended_act_interlinks:
        if row.resolution_status in _TRACEABLE_STATUSES:
            continue
        # Only count rows that look like an internal section cite (a section
        # locator present) — those are the ones a resolved status could have
        # promoted into tier 2.  We deliberately do NOT require target-act match
        # here (an untraceable row has no reliable target work id by definition).
        if not _section_of_locator(row.source_locator) and not _section_of_locator(
            row.target_locator
        ):
            continue
        counts[row.resolution_status] = counts.get(row.resolution_status, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# TIER 3 — the declared boundary (pure)
# ---------------------------------------------------------------------------

#: The closed list of second-order effect classes this report does NOT compute.
#: This IS the honesty boundary — declared, never silently omitted.
_UNCOMPUTED_EFFECT_CLASSES = (
    "semantic/teleological impact — whether and how the changed provisions alter "
    "the law's purpose or meaning (this report reports WHAT moves, never what it "
    "means or how much it matters)",
    "multi-hop cascades — only the 1-hop back-reference (a provision that directly "
    "cites a changed §) is computed; a provision that cites a provision that cites "
    "a changed § is NOT followed",
    "temporal / contingency cascades — effects that depend on commencement dates, "
    "transitional provisions, or conditional triggers are not modelled",
    "institutional / capacity effects — downstream load on agencies, courts, or "
    "administrative capacity is not modelled",
    "transnational transposition cascades — EU-directive / treaty transposition "
    "ripple effects (e.g. a changed § that transposes an EU obligation) are not "
    "followed across the FI↔EU boundary",
    "section-granularity precision — tier-2 back-references are matched by bare § "
    "label against the interlink target's section locator.  When the amended act "
    "numbers sections PER CHAPTER (the same § number recurs in several luku), a "
    "citation to one chapter's §N and a tier-1 change to another chapter's §N "
    "share a label, so a tier-2 hit may OVER-ATTRIBUTE across chapters.  The "
    "back-reference is reported as a candidate connection, not an asserted "
    "chapter-exact dependency; chapter-scoped matching is a near-term extension.",
)

#: Declared near-term extensions that are OUT of v1 — not silent gaps.
_DEFERRED = (
    "tier-2 definition-users — provisions whose meaning shifts because a tier-1 op "
    "changed a DEFINITION they use.  Deferred because the definition graph anchors "
    "bindings by byte offset into an assembled body string, not by section label, "
    "so an offset→section crosswalk is required to know which bindings sit inside "
    "a tier-1-changed section.  Near-term extension, not a silent omission.",
    "cross-act back-references — provisions in OTHER acts that cite a changed § are "
    "not scanned; tier-2 is scoped to internal (within-amended-act) back-references "
    "only.  A corpus-wide reverse-citation scan is deferred (heavy).",
)


def build_tier_3_boundary(
    amended_act_interlinks: list["LawvmInterlinkRow"],
) -> UncomputedBoundary:
    """Assemble the tier-3 declared boundary.

    Pure: the effect-class list and deferred list are closed constants; the
    resolution-limits sub-account is computed from the interlink rows so the
    boundary is self-evidencing about HOW MANY would-be tier-2 effects the
    resolver could not trace (by untraceable status).
    """
    untraceable = _untraceable_status_counts(amended_act_interlinks)
    return UncomputedBoundary(
        statement=(
            "DECLARED BOUNDARY — this report computes tier 1 (provisions the "
            "amendment's ops directly change) and tier 2 (1-hop internal "
            "back-references: provisions in the amended act that cite a changed §, "
            "traced ONLY through resolved/unchanged citations).  Everything below "
            "is NOT computed and is declared here rather than silently omitted.  "
            "No score, no magnitude: this report reports WHAT moves, never how much "
            "it matters."
        ),
        effect_classes=_UNCOMPUTED_EFFECT_CLASSES,
        resolution_limits={
            "note": (
                "citations in the amended act that name a section but could NOT be "
                "traced into tier 2 because their resolution status is not "
                "resolved/unchanged.  These would-be effects are declared here, "
                "never guessed."
            ),
            "untraceable_by_status": untraceable,
            "total_untraceable": sum(untraceable.values()),
        },
        deferred=_DEFERRED,
    )


# ---------------------------------------------------------------------------
# Report assembly (pure)
# ---------------------------------------------------------------------------


def build_counterfactual_report(
    amendment_id: str,
    amended_act_id: str,
    parsed_ops: list["ParsedOp"],
    amended_act_interlinks: list["LawvmInterlinkRow"],
) -> CounterfactualEffectsReport:
    """Assemble the full three-tier report from already-built inputs (pure).

    Takes the amendment id, the resolved amended-act id, the lowered johtolause
    ops, and the AMENDED act's interlink rows.  No corpus access — every tier is a
    pure projection of these inputs, so the whole report is testable on synthetic
    fixtures.
    """
    tier_1 = build_tier_1_direct(parsed_ops)
    citing = build_tier_2_citing_provisions(
        tier_1, amended_act_id, amended_act_interlinks
    )
    tier_3 = build_tier_3_boundary(amended_act_interlinks)
    return CounterfactualEffectsReport(
        amendment_id=amendment_id,
        amended_act_id=amended_act_id,
        tier_1_direct=tier_1,
        tier_2_via_defs_and_refs={
            "citing_provisions": citing,
            # DEFERRED sub-tier — always empty in v1, declared in tier 3.
            "definition_users": (),
        },
        tier_3_uncomputed_second_order=tier_3,
    )


# ---------------------------------------------------------------------------
# Serialization (pure)
# ---------------------------------------------------------------------------


def report_to_dict(report: CounterfactualEffectsReport) -> dict[str, Any]:
    """Renderer-neutral dict projection of the report (stable key order)."""
    return {
        "amendment_id": report.amendment_id,
        "amended_act_id": report.amended_act_id,
        "tier_1_direct": [
            {
                "verb": d.verb,
                "verb_label": d.verb_label,
                "section": d.section,
                "chapter": d.chapter,
                "momentti": d.momentti,
                "item": d.item,
                "op_code": d.op_code,
                "provenance": {"source": d.source, "node_id": d.node_id},
            }
            for d in report.tier_1_direct
        ],
        "tier_2_via_defs_and_refs": {
            "citing_provisions": [
                {
                    "citing_section": c.citing_section,
                    "cited_section": c.cited_section,
                    "role": c.role,
                    "resolution_status": c.resolution_status,
                    "provenance": {"source": c.source, "node_id": c.node_id},
                }
                for c in report.tier_2_via_defs_and_refs["citing_provisions"]
            ],
            "definition_users": list(
                report.tier_2_via_defs_and_refs["definition_users"]
            ),
        },
        "tier_3_uncomputed_second_order": {
            "statement": report.tier_3_uncomputed_second_order.statement,
            "effect_classes": list(
                report.tier_3_uncomputed_second_order.effect_classes
            ),
            "resolution_limits": report.tier_3_uncomputed_second_order.resolution_limits,
            "deferred": list(report.tier_3_uncomputed_second_order.deferred),
        },
    }


def render_report(report: CounterfactualEffectsReport) -> str:
    """Plain-text render — the three tiers kept visually separate."""
    lines: list[str] = []
    lines.append(f"COUNTERFACTUAL EFFECTS — amendment {report.amendment_id}")
    lines.append(f"amended act: {report.amended_act_id or '(unresolved)'}")
    lines.append("=" * 64)

    lines.append("")
    lines.append(f"TIER 1 — DIRECTLY CHANGED ({len(report.tier_1_direct)})")
    for d in report.tier_1_direct:
        ctx = f" luku {d.chapter}" if d.chapter else ""
        sub = f" mom {d.momentti}" if d.momentti else ""
        item = f" kohta {d.item}" if d.item else ""
        lines.append(
            f"    • {d.verb_label:8} §{d.section}{ctx}{sub}{item}  "
            f"[{d.source}:{d.node_id}]"
        )

    citing = report.tier_2_via_defs_and_refs["citing_provisions"]
    deffers = report.tier_2_via_defs_and_refs["definition_users"]
    lines.append("")
    lines.append(
        f"TIER 2 — CHANGED VIA REFERENCES ({len(citing)} citing provision(s); "
        f"{len(deffers)} definition-user(s) [deferred — see tier 3])"
    )
    for c in citing:
        lines.append(
            f"    └─ §{c.citing_section or '?'} cites changed §{c.cited_section} "
            f"({c.role}, {c.resolution_status})  [{c.source}:{c.node_id}]"
        )

    b = report.tier_3_uncomputed_second_order
    lines.append("")
    lines.append("TIER 3 — UNCOMPUTED SECOND-ORDER (declared boundary)")
    lines.append(f"  {b.statement}")
    lines.append("  NOT computed:")
    for cls in b.effect_classes:
        lines.append(f"    × {cls}")
    rl = b.resolution_limits
    lines.append(
        f"  resolution limits: {rl.get('total_untraceable', 0)} untraceable "
        f"section-citation(s) by status {rl.get('untraceable_by_status', {})}"
    )
    lines.append("  deferred near-term extensions:")
    for d in b.deferred:
        lines.append(f"    ~ {d}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Corpus access (lazy — only the CLI handler needs it; the builders are pure)
# ---------------------------------------------------------------------------


def _amendment_parents_path() -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finland", "amendment_parents.csv")


def _resolve_amended_act_id(amendment_id: str) -> str:
    """Resolve the act an amendment modifies via the amendment→parent map.

    Read-only consult of ``data/finland/amendment_parents.csv`` (a static data
    table, NOT ``finland/metadata.py`` and NOT the apply path).  Returns "" when
    the amendment has no recorded parent — the tier-2 scan then reports nothing and
    the unknown-parent case is declared in tier 3, never guessed.
    """
    import csv
    import os

    path = _amendment_parents_path()
    if not os.path.exists(path):
        return ""
    keys = (amendment_id, f"{amendment_id}-000")
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("amendment_id") in keys:
                parent = str(row.get("parent_id") or "")
                # parents are recorded as e.g. ``1734/4-000``; strip the -NNN tail
                return parent.split("-", 1)[0].strip()
    return ""


def _parse_amendment_ops(statute_id: str, store: Any) -> list["ParsedOp"]:
    """Read the amendment SOURCE XML, parse its johtolause to ops (fail-loud).

    Reads the as-enacted amendment text (``read_source`` / ``read_amendment``),
    NOT the consolidated oracle — the oracle of a base act carries no amendment
    ops.  A missing source or a parse error raises ``SystemExit`` with the
    offending id, never a silent empty op list.
    """
    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.metadata import get_johtolause

    xml_bytes = store.read_source(statute_id) or store.read_amendment(statute_id)
    if xml_bytes is None:
        raise SystemExit(
            f"ERROR: no archived source/amendment XML for statute {statute_id!r}"
        )
    johto = get_johtolause(xml_bytes)
    if not johto:
        raise SystemExit(
            f"ERROR: no johtolause (enacting clause) in statute {statute_id!r} — "
            "not an amending statute, or unsupported source shape"
        )
    result = parse_clause(johto, statute_id=statute_id)
    if result.parse_error:
        raise SystemExit(
            f"ERROR: johtolause parse failed for {statute_id!r}: {result.parse_error}"
        )
    return list(result.parsed_ops)


def _amended_act_interlinks(
    amended_act_id: str, store: Any
) -> list["LawvmInterlinkRow"]:
    """Project the AMENDED act's interlink rows (read-only).

    Empty when the amended act is unknown ("") — the tier-2 scan then reports
    nothing and the unknown-parent case is declared in tier 3.
    """
    if not amended_act_id:
        return []
    from lawvm.finland.interlink_targets import project_fi_interlinks_partition

    projection = project_fi_interlinks_partition(amended_act_id, store)
    return list(projection.rows)


def _build_report_from_corpus(statute_id: str) -> CounterfactualEffectsReport:
    """Load the amendment + amended-act interlinks from the corpus and assemble."""
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    parsed_ops = _parse_amendment_ops(statute_id, store)
    amended_act_id = _resolve_amended_act_id(statute_id)
    interlinks = _amended_act_interlinks(amended_act_id, store)
    return build_counterfactual_report(
        statute_id, amended_act_id, parsed_ops, interlinks
    )


# ---------------------------------------------------------------------------
# CLI handler (thin wrapper)
# ---------------------------------------------------------------------------


def main(args: argparse.Namespace) -> None:
    statute_id: str = getattr(args, "statute_id", "")
    if not statute_id:
        raise SystemExit(
            "ERROR: bill-counterfactual requires a statute id, e.g. 2018/301"
        )
    report = _build_report_from_corpus(statute_id)
    if bool(getattr(args, "json", False)):
        print(
            json.dumps(
                report_to_dict(report), indent=2, default=str, ensure_ascii=False
            )
        )
    else:
        print(render_report(report))


__all__ = [
    "SOURCE_DEFINITION_GRAPH",
    "SOURCE_INTERLINK_GRAPH",
    "SOURCE_JOHTOLAUSE_PARSE",
    "CitingProvisionEffect",
    "CounterfactualEffectsReport",
    "DirectEffect",
    "UncomputedBoundary",
    "build_counterfactual_report",
    "build_tier_1_direct",
    "build_tier_2_citing_provisions",
    "build_tier_3_boundary",
    "main",
    "render_report",
    "report_to_dict",
]
