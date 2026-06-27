"""New Zealand adapter for the witness-attribution spec-discovery ledger.

This is the NZ sibling of :mod:`lawvm.tools.spec_ledger`'s Finland adapter. It
reuses that module's **jurisdiction-neutral core** read-only (``DivergenceRow`` ->
``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) and turns NZ's
dry-run loop into a per-rule discovered-spec ledger.

Frame (see ``notes_internal/SPEC_DISCOVERY_DESIGN.md``): NZ's dry-run surface
reverse-engineers the *unwritten rules of NZ amendment law* from
``(amendment, before, after)`` PIT-XML oracle triples. Each apply kernel + oracle
semantic is a **named, falsifiable hypothesis** carried as a per-op
``oracle_match_rule_id``. An ``agrees`` outcome corroborates the rule; a residual
contradicts it (or, for known editorial/rendering classes, suspects the oracle).

This adapter accumulates those per-op outcomes into the neutral ledger so an
undifferentiated agreement-rate becomes a ranked table of *specific hypotheses
about NZ amendment law, with how often the oracle corroborates each one*.

It is read-only and additive: it never edits ``tools/spec_ledger.py`` (no dispatch
registration there — the NZ ledger is standalone, exposed via the ``nz-corpus
spec-ledger`` subcommand), never enables actual replay, and never mutates the
archive. It only consumes the dry-run corpus surface; it does not change its
semantics.

Run:  lawvm nz-corpus spec-ledger --corpus data/nz/bench_corpus_smoke.csv
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple, cast

from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_INSERT_AGREES_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID,
    NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZDryRunReport,
)
from lawvm.new_zealand.dry_run_corpus import build_nz_dry_run_repeal_corpus_report
from lawvm.tools.spec_ledger import (
    DivergenceRow,
    SpecLedger,
    StatuteLedgerInput,
    WitnessDisposition,
    build_ledger,
    disposition_for,
)

# ---------------------------------------------------------------------------
# The NZ dry-run rule catalog: each oracle-semantic rule as a named hypothesis.
# ---------------------------------------------------------------------------
#
# The catalog is the keepable asset (SPEC_DISCOVERY_DESIGN #2). Each entry is the
# *believed spec* of one NZ amendment-law rule the dry-run surface encodes:
# (apply kernel -> expected oracle outcome). The rule fires whenever the surface
# applies that kernel and partitions the on-or-after oracle; an ``agrees`` firing
# corroborates the believed spec, a residual firing contradicts it.
#
# ``confidence`` follows the SPEC_DISCOVERY_DESIGN tiers (certain | heuristic |
# fallback | legacy_unknown). "certain" = the rule is a direct, exactness-gated
# structural fact (e.g. a tombstone is a tombstone); "heuristic" = the rule
# encodes a believed editorial convention that the oracle could legitimately
# render differently.


class NZRuleCatalogEntry:
    """One NZ dry-run rule: a named, falsifiable hypothesis about NZ amendment law."""

    __slots__ = ("rule_id", "believed_spec", "confidence")

    def __init__(self, rule_id: str, believed_spec: str, confidence: str) -> None:
        self.rule_id = rule_id
        self.believed_spec = believed_spec
        self.confidence = confidence


_CERTAIN = "certain"
_HEURISTIC = "heuristic"

# Only the AGREEING rule ids are believed-spec hypotheses about NZ amendment law:
# each says "applying kernel K, the consolidated text should look like O". The
# residual rule ids are the *named contradictions* of those same hypotheses; they
# are cataloged too (so a fired residual rule is never an uncataloged blind spot)
# but they carry the contradiction prose, not an independent spec.
_NZ_RULE_CATALOG: Tuple[NZRuleCatalogEntry, ...] = (
    # --- Repeal family ----------------------------------------------------
    NZRuleCatalogEntry(
        NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
        "Repealing an ordinary provision converts its node to a repealed-but-"
        "addressable tombstone in the consolidated text (never delete-and-forget).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID,
        "Repealing a definition (def-para) REMOVES the whole def-para from the "
        "consolidated text rather than leaving a tombstone.",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
        "Contradiction of the tombstone rule: the oracle node exists but is still "
        "substantive (the repeal is not reflected, or the target drifted).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
        "Contradiction of the tombstone rule: the target node is absent from the "
        "oracle (NZ preserves repealed-but-addressable tombstones, so absence is a "
        "structural mismatch, not an agreement).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID,
        "Contradiction of the definition-removal rule: the repealed def-para is "
        "still present in the oracle.",
        _CERTAIN,
    ),
    # --- Text-substitution family ----------------------------------------
    NZRuleCatalogEntry(
        NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID,
        "A single-occurrence old->new text substitution on the exact target node "
        "is reflected in the consolidated text (oracle contains new_text and its "
        "residual old_text count matches the applied result).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
        "Contradiction of the text-substitution rule: the oracle still carries an "
        "old_text occurrence the substitution removed (not reflected / wrong target).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID,
        "Contradiction of the text-substitution rule: the new_text is absent from "
        "the oracle (the substitution was not reflected, or another window change "
        "overwrote the target).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_TEXT_RESIDUAL_TARGET_MISSING_RULE_ID,
        "Contradiction of the text-substitution rule: the exact target node is "
        "absent from the oracle.",
        _CERTAIN,
    ),
    # --- Structural whole-provision REPLACE family -----------------------
    NZRuleCatalogEntry(
        NZ_DRY_RUN_REPLACE_AGREES_RULE_ID,
        "Replacing/substituting a whole provision swaps its subtree for the new "
        "provision body extracted from the amending act; the oracle subtree matches "
        "the candidate replacement (normalized text/structure).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID,
        "Contradiction of the structural-replace rule: the oracle target subtree "
        "exists but differs from the candidate replacement (wrong content / other "
        "window change).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID,
        "Contradiction of the structural-replace rule: the exact target node is "
        "absent from the oracle.",
        _CERTAIN,
    ),
    # --- Structural whole-provision INSERT family ------------------------
    NZRuleCatalogEntry(
        NZ_DRY_RUN_INSERT_AGREES_RULE_ID,
        "Inserting/adding a whole provision places the new node next to a derived "
        "anchor sibling (e.g. 18A after 18); the oracle carries the new node at the "
        "expected position with matching content.",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID,
        "Contradiction of the structural-insert rule: the inserted node is absent "
        "from the oracle (insertion not reflected).",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID,
        "Contradiction of the structural-insert rule: the inserted node is present "
        "in the oracle but its content differs from the candidate payload.",
        _CERTAIN,
    ),
    NZRuleCatalogEntry(
        NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID,
        "Contradiction of the structural-insert rule: the inserted node is present "
        "with matching content but at a different position than the derived anchor "
        "(the oracle's adjacent same-kind sibling differs from the derived predecessor).",
        _CERTAIN,
    ),
)

# rule_id -> believed_spec prose (the catalog the neutral core consumes).
NZ_RULE_SPECS: Dict[str, str] = {e.rule_id: e.believed_spec for e in _NZ_RULE_CATALOG}
# rule_id -> confidence tier (carried into the artifact alongside the spec).
NZ_RULE_CONFIDENCE: Dict[str, str] = {e.rule_id: e.confidence for e in _NZ_RULE_CATALOG}

# A loud sentinel for a fired oracle rule_id with no catalog entry. Mirrors the
# spec_ledger discipline: absence of a believed-spec is a visible state, never a
# silent pass.
NZ_LEGACY_UNKNOWN = "legacy_unknown"

# ---------------------------------------------------------------------------
# oracle_match outcome -> witness disposition.
# ---------------------------------------------------------------------------
#
# HONESTY (task constraint): a residual is NOT dispositioned ``oracle_suspect`` to
# flatter a rule. Only KNOWN oracle-rendering / editorial classes map to
# ``oracle_suspect``; every genuine content/position/state mismatch stays
# ``lawvm_wrong`` (falsifying) or ``structural`` (node present/absent mismatch,
# owner pinned to the rule). Today NO NZ dry-run residual is a known editorial
# class, so none map to ``oracle_suspect`` — the surface's exactness gates make
# every residual a genuine falsification or a structural mismatch. The map is
# kept explicit so promoting a residual to ``oracle_suspect`` is a deliberate,
# reviewable edit, not an accident.
_NZ_ORACLE_MATCH_DISPOSITION: Dict[str, WitnessDisposition] = {
    # Repeal: oracle node present but not tombstoned / def still present = our
    # repeal hypothesis is falsified.
    "target_not_tombstone": "lawvm_wrong",
    "target_not_removed": "lawvm_wrong",
    # Repeal: tombstone/target absent from the oracle = section present/absent
    # mismatch, owner pinned to the rule = structural.
    "target_missing": "structural",
    # Text substitution: not reflected = falsified hypothesis.
    "residual_old_text_remains": "lawvm_wrong",
    "residual_new_text_absent": "lawvm_wrong",
    # Structural replace: subtree differs = falsified.
    "residual_replacement_mismatch": "lawvm_wrong",
    # Structural insert: absent / content differs / landed at the wrong position.
    "residual_insert_not_present": "lawvm_wrong",
    "residual_insert_content_mismatch": "lawvm_wrong",
    "residual_insert_position_mismatch": "lawvm_wrong",
}

# The four dry-run families this adapter sweeps. Each is a partial-scope dry-run
# that relaxes only the whole-work readiness gate (never per-op exactness), so the
# adapter sees every eligible operation across the corpus.
_NZ_LEDGER_SCOPES: Tuple[str, ...] = (
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
)


def _disposition_for(oracle_match: str) -> WitnessDisposition:
    """Map a residual oracle_match to a witness disposition (loud on unknowns)."""
    # ``agrees`` is never a divergence and must not reach here.
    return disposition_for(oracle_match, _NZ_ORACLE_MATCH_DISPOSITION)


def nz_ledger_inputs_from_reports(
    reports: List[NZDryRunReport],
) -> List[StatuteLedgerInput]:
    """Turn per-work dry-run reports into neutral per-work ledger inputs.

    For each per-op mutation-boundary proof:

    - ``oracle_match == "agrees"``: the proof's ``oracle_match_rule_id`` (an
      AGREES rule) fired and was corroborated -> a rule firing, no divergence.
    - otherwise: a residual -> a :class:`DivergenceRow` whose ``rule_id`` is the
      proof's ``oracle_match_rule_id`` (a residual/contradiction rule) and whose
      disposition is mapped from the residual ``oracle_match`` family. The residual
      rule's firing is also tallied so its corroborated/contradicted arithmetic is
      well-formed.

    Refusals never mutate and carry no oracle outcome, so they are not ledger
    firings (they are the dry-run surface's own coverage frontier, reported there).
    """

    # One work can appear once per family scope; keep per (work, family) inputs so
    # the neutral core counts each family's statute coverage faithfully.
    inputs: List[StatuteLedgerInput] = []
    for report in reports:
        firings: Dict[str, int] = defaultdict(int)
        divergences: List[DivergenceRow] = []
        sid = report.work_id
        for proof in report.proofs:
            rule_id = proof.oracle_match_rule_id
            firings[rule_id] += 1
            if proof.oracle_match == "agrees":
                continue
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=proof.target_address,
                    diagnosis=proof.oracle_match,
                    disposition=_disposition_for(proof.oracle_match),
                    rule_id=rule_id,
                    blame_source=report.operation_family,
                )
            )
        if not firings and not divergences:
            continue
        inputs.append(
            StatuteLedgerInput(
                sid=sid,
                rule_firings=dict(firings),
                divergences=divergences,
            )
        )
    return inputs


def build_nz_spec_ledger(
    db_path: Path,
    *,
    work_ids: Tuple[str, ...] = (),
    corpus_path: Path | None = None,
    max_works: int | None = None,
) -> SpecLedger:
    """Build the NZ discovered-spec ledger across all supported dry-run families.

    Runs the dry-run corpus surface once per family scope (repeal, text_replace,
    replace, insert), maps every per-op oracle outcome to the neutral core, and
    aggregates via :func:`lawvm.tools.spec_ledger.build_ledger` with the NZ rule
    catalog. The result is the per-rule corroborated/contradicted ledger — the
    keepable discovered-spec artifact.
    """

    resolved_work_ids = work_ids
    if not resolved_work_ids and corpus_path is not None:
        from lawvm.new_zealand.bench_corpus import read_corpus_work_ids

        resolved_work_ids = read_corpus_work_ids(corpus_path)

    all_inputs: List[StatuteLedgerInput] = []
    seen_works: set[str] = set()
    for scope in _NZ_LEDGER_SCOPES:
        report = build_nz_dry_run_repeal_corpus_report(
            db_path,
            work_ids=resolved_work_ids,
            max_works=max_works,
            scope=scope,
        )
        for r in report.work_reports:
            seen_works.add(r.work_id)
        all_inputs.extend(nz_ledger_inputs_from_reports(list(report.work_reports)))

    ledger = build_ledger(
        all_inputs,
        jurisdiction="nz",
        mode="dry_run_after_tree_vs_archived_on_or_after_xml",
        catalog=NZ_RULE_SPECS,
    )
    # statutes counts (work, family) inputs; report the distinct works swept too.
    ledger.statute_errors = 0
    return ledger


def ledger_to_dict(ledger: SpecLedger) -> Dict[str, Any]:
    """Project the ledger to a JSON artifact, enriched with NZ confidence tiers.

    Re-uses the neutral core's ``to_dict`` and folds in the per-rule confidence
    tier and a ``cataloged`` flag (``False`` = a fired rule with no believed-spec
    = a ``legacy_unknown`` blind spot).
    """

    base = ledger.to_dict()
    rules = cast(List[Dict[str, Any]], base["rules"])
    for rule in rules:
        rid = rule["rule_id"]
        rule["confidence"] = NZ_RULE_CONFIDENCE.get(
            rid, NZ_LEGACY_UNKNOWN if not rule["cataloged"] else _CERTAIN
        )
    base["legacy_unknown_rules"] = sorted(
        rule["rule_id"] for rule in rules if not rule["cataloged"]
    )
    return base


def render_text(ledger: SpecLedger) -> str:
    """Human-readable NZ discovered-spec ledger: rules ranked by contradiction."""

    art = ledger_to_dict(ledger)
    lines: List[str] = [
        "NZ discovered-spec ledger (witness = archived on-or-after PIT-XML oracle)",
        f"work_family_inputs={art['statutes']} rules={art['n_rules']} "
        f"unattributed_divergences={art['n_unattributed']}",
        "",
        f"{'rule_id':<62} {'conf':<10} {'fire':>5} {'corrob~':>7} "
        f"{'contra':>6}  dispositions",
        "-" * 120,
    ]
    for rule in art["rules"]:
        disp = " ".join(f"{k}:{v}" for k, v in sorted(rule["by_disposition"].items()))
        cataloged = "" if rule["cataloged"] else " [UNCATALOGED!]"
        lines.append(
            f"{rule['rule_id']:<62} {rule['confidence']:<10} "
            f"{rule['firings']:>5} {rule['corroborated_est']:>7} "
            f"{rule['contradicted']:>6}  {disp}{cataloged}"
        )
    lines.append("")
    lines.append("believed spec per rule:")
    for rule in art["rules"]:
        spec = rule["believed_spec"] or "(no believed_spec — uncataloged blind spot)"
        lines.append(f"  - {rule['rule_id']}:")
        lines.append(f"      {spec}")
        if rule["contradicted"]:
            top = _top_contradicting_works(rule["exemplars"])
            if top:
                lines.append(f"      top contradicting works: {', '.join(top)}")
    if art["legacy_unknown_rules"]:
        lines.append("")
        lines.append("LEGACY_UNKNOWN (fired oracle rule_ids with no catalog entry):")
        for rid in art["legacy_unknown_rules"]:
            lines.append(f"  - {rid}")
    if art["unattributed"]:
        lines.append("")
        lines.append(f"unattributed divergences (blind spots): {art['n_unattributed']}")
    return "\n".join(lines)


def _top_contradicting_works(exemplars: List[Mapping[str, str]]) -> List[str]:
    seen: List[str] = []
    for ex in exemplars:
        statute = str(ex.get("statute") or "")
        if statute and statute not in seen:
            seen.append(statute)
        if len(seen) >= 5:
            break
    return seen


def main(args: Any) -> None:
    work_ids = tuple(getattr(args, "work_id", None) or ())
    corpus_path = getattr(args, "corpus", None)
    db_path = Path(args.db)

    ledger = build_nz_spec_ledger(
        db_path,
        work_ids=work_ids,
        corpus_path=Path(corpus_path) if corpus_path else None,
        max_works=getattr(args, "max_works", None),
    )

    if getattr(args, "json", False):
        print(json.dumps(ledger_to_dict(ledger), ensure_ascii=False, indent=2))
        return
    print(render_text(ledger))
    if getattr(args, "json_out", ""):
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(ledger_to_dict(ledger), fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.json_out}", file=sys.stderr)
