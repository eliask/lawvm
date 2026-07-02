"""UK adapter for the witness-attribution spec-discovery ledger.

This is the UK sibling of :mod:`lawvm.tools.spec_ledger`'s jurisdiction-neutral core,
and of the FI / EE / US / NZ adapters. It reuses that core read-only (``DivergenceRow``
-> ``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) and turns the UK
oracle-check per-EID surface into neutral ledger inputs.

It self-registers into the core's adapter registry at import time (see
:func:`lawvm.tools.spec_ledger.register_ledger_adapter`) so ``run_ledger("uk", ...)`` and
the ``-j uk`` CLI dispatch through the registry without the core importing this package.

Run:  uv run python -m lawvm.tools.spec_ledger -j uk asp/2000/1 [more sids ...]
      uv run python -m lawvm.tools.spec_ledger -j uk --corpus-bench --json ledger.json
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Dict, Iterator, List

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    LedgerAdapter,
    Mode,
    RuleRole,
    StatuteLedgerInput,
    WitnessDisposition,
    disposition_for,
    register_ledger_adapter,
)

# UK diagnosis vocabulary -> witness disposition. Three vocabularies feed it:
#  1. the §2.1 oracle-check bucket names emitted per-EID by
#     ``uk_divergence_rows_for_statute`` (deterministic_gap / manual_frontier /
#     oracle_suspect / text_diff);
#  2. the source-pathology classes (UK_EFFECT_SOURCE_PATHOLOGY_CLASSES) and
#     compare-shape classes (UK_EFFECT_COMPARE_SHAPE_CLASSES) from
#     uk_legislation.source_adjudication, which may appear as a covering
#     rejection's ``source_pathology``;
#  3. the owning phase constants (UK_PHASE_*) from phase_discipline.
#
# Diagnoses absent here fall back to "unknown" (loud) — never silently a pass.
# Discipline: a missing-source / out-of-scope pathology is "missing_source"
# (the source did not deterministically specify it); a compare-shape class is
# "oracle_suspect" (oracle exposes a different editorial shape, replay coherent);
# deterministic_gap is "lawvm_wrong" (our compiler should have produced it).
_UK_DIAGNOSIS_DISPOSITION: Dict[str, WitnessDisposition] = {
    # --- §2.1 oracle-check bucket names (the primary per-EID diagnosis) ---
    "deterministic_gap": "lawvm_wrong",
    "manual_frontier": "missing_source",
    "oracle_suspect": "oracle_suspect",
    "text_diff": "unknown",  # both sides carry the EID; needs per-text analysis
}


def _seed_uk_pathology_dispositions() -> None:
    """Extend the diagnosis map with the source-pathology / compare-shape /
    phase vocabularies so a covering rejection's finer label also resolves."""
    try:
        from lawvm.uk_legislation.source_adjudication import (
            UK_EFFECT_COMPARE_SHAPE_CLASSES,
            UK_EFFECT_SOURCE_PATHOLOGY_CLASSES,
        )
        from lawvm.uk_legislation.phase_discipline import (
            UK_PHASE_AFFECTING_SOURCE_EXTRACTION,
            UK_PHASE_CANONICAL_OP_COMPILATION,
            UK_PHASE_COMPARE_ORACLE_CLASSIFICATION,
            UK_PHASE_EFFECT_METADATA_FRONTEND,
            UK_PHASE_REPLAY_INVARIANTS,
            UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER,
            UK_PHASE_TYPED_ELABORATION,
        )
    except ImportError:
        return
    # source pathology = the source text did not deterministically specify the
    # result (unsupported shape / out of scope / missing payload): missing_source.
    for cls in UK_EFFECT_SOURCE_PATHOLOGY_CLASSES:
        _UK_DIAGNOSIS_DISPOSITION.setdefault(cls, "missing_source")
    # compare-shape = oracle exposes a different editorial structure while replay
    # stays source-faithful: oracle_suspect (a finding, not our bug).
    for cls in UK_EFFECT_COMPARE_SHAPE_CLASSES:
        _UK_DIAGNOSIS_DISPOSITION.setdefault(cls, "oracle_suspect")
    # owning-phase constants, when a row's diagnosis is reported as its phase.
    phase_dispositions: Dict[str, WitnessDisposition] = {
        UK_PHASE_EFFECT_METADATA_FRONTEND: "missing_source",
        UK_PHASE_AFFECTING_SOURCE_EXTRACTION: "missing_source",
        UK_PHASE_TYPED_ELABORATION: "lawvm_wrong",
        UK_PHASE_CANONICAL_OP_COMPILATION: "lawvm_wrong",
        UK_PHASE_REPLAY_INVARIANTS: "lawvm_wrong",
        UK_PHASE_COMPARE_ORACLE_CLASSIFICATION: "oracle_suspect",
        UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER: "missing_source",
    }
    for phase, disp in phase_dispositions.items():
        _UK_DIAGNOSIS_DISPOSITION.setdefault(phase, disp)


_seed_uk_pathology_dispositions()


# Believed-spec catalog: authored by a sibling agent in
# ``lawvm.tools.spec_ledger_uk_catalog``. Import it if present, else fall back to
# an empty dict so this adapter works whether or not that module exists yet.
def _load_uk_rule_specs() -> Dict[str, str]:
    try:
        from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS
    except ImportError:
        return {}
    specs = dict(_UK_RULE_SPECS)
    # Fold in the effect/diagnostic-rule supplement (the ledger-firing ids that are
    # string literals, not static *_RULE_ID constants — kept separate so the main
    # catalog's no-dead-entry constant check stays pure). Merged only here, where the
    # ledger consumes the combined catalog.
    try:
        from lawvm.tools.spec_ledger_uk_catalog_supplement import (
            _UK_RULE_SPECS_SUPPLEMENT,
        )
        specs.update(_UK_RULE_SPECS_SUPPLEMENT)
    except ImportError:
        pass
    return specs


_UK_RULE_SPECS: Dict[str, str] = _load_uk_rule_specs()


def _load_uk_rule_roles() -> Dict[str, RuleRole]:
    """S/P sort per catalogued UK rule id (§3.5), computed from the classifier; {} if
    the meta sidecar is absent."""
    try:
        from lawvm.tools.spec_ledger_uk_catalog_meta import build_uk_rule_roles

        return dict(build_uk_rule_roles(_UK_RULE_SPECS))
    except ImportError:
        return {}


def _load_uk_rule_falsifiers() -> Dict[str, str]:
    """Per-rule falsifier sentence (§3.2(4)); {} if the meta sidecar is absent."""
    try:
        from lawvm.tools.spec_ledger_uk_catalog_meta import build_uk_rule_falsifiers

        return dict(build_uk_rule_falsifiers(_UK_RULE_SPECS))
    except ImportError:
        return {}


_UK_RULE_ROLES: Dict[str, RuleRole] = _load_uk_rule_roles()
_UK_RULE_FALSIFIERS: Dict[str, str] = _load_uk_rule_falsifiers()


# A statute whose divergences are overwhelmingly UNATTRIBUTED structural
# deterministic-gaps is not N rule-falsifications — it is one whole-statute
# addressing / EID-scheme incommensurability (replay and oracle structure the
# same content under different EIDs). Counting each gap as lawvm_wrong lets a
# single mismatched statute dominate the real-bug ranking (e.g. ukpga/1907/51:
# 4595 named-part deterministic-gaps, every one an unattributed blind spot).
# Demote such a statute's unattributed deterministic-gap rows to a non-falsifying
# "unknown", tagged so the pattern stays visible — not masquerading as bugs.
_NONCOMMENSURABLE_MIN_ROWS = 50
_NONCOMMENSURABLE_FRACTION = 0.9
_NONCOMMENSURABLE_DIAGNOSIS = "noncommensurable_whole_statute_structural"


def _demote_whole_statute_noncommensurable(
    rows: List[DivergenceRow],
) -> List[DivergenceRow]:
    """Reclassify a whole-statute structural-incommensurability wall.

    When a statute's divergences are dominated by unattributed ``deterministic_gap``
    rows (no witness rule; structural EID present in the oracle but not replay), the
    cause is one addressing-scheme mismatch, not many rule bugs. Demote those rows to
    a non-falsifying ``unknown`` disposition under a
    ``noncommensurable_whole_statute_structural`` diagnosis so a single mismatched
    statute does not dominate the real-bug ranking. Attributed rows and non-gap
    diagnoses are never touched.
    """
    if len(rows) < _NONCOMMENSURABLE_MIN_ROWS:
        return rows
    wall = sum(
        1 for r in rows if r.rule_id is None and r.diagnosis == "deterministic_gap"
    )
    if wall / len(rows) < _NONCOMMENSURABLE_FRACTION:
        return rows
    return [
        replace(r, diagnosis=_NONCOMMENSURABLE_DIAGNOSIS, disposition="unknown")
        if (r.rule_id is None and r.diagnosis == "deterministic_gap")
        else r
        for r in rows
    ]


def uk_ledger_inputs(sids: List[str], mode: Mode) -> Iterator[StatuteLedgerInput]:
    """Turn the UK oracle-check per-EID surface into neutral ledger inputs.

    Firings come from compiled UK ops' ``witness_rule_id``; divergences come from
    ``uk_divergence_rows_for_statute`` (one row per divergent EID, carrying the
    §2.1 bucket diagnosis, plus the covering rejection's rule_id / owning phase /
    authority layer when attributable).

    ``mode`` is accepted for signature parity with the FI adapter; the UK
    oracle-check path is point-in-time-less (full current-oracle comparison).
    """
    from lawvm.tools.uk_oracle_check import (
        _compute_uk_divergence_state,
        uk_divergence_rows_for_statute,
    )

    for sid in sids:
        state = _compute_uk_divergence_state(sid)
        if state.error:
            continue  # caller counts errors separately
        # Firings: each compile rejection/diagnostic row that names a witness
        # rule_id is a fired hypothesis. UK compiled ops carry witness_rule_id;
        # the per-EID rows surface the covering rule, so we tally rule_ids from
        # the diagnostic rows (the rule-level firing proxy the surface exposes).
        firings: Dict[str, int] = defaultdict(int)
        for row in (
            state.lowering_rejections
            + state.effect_feed_parse_rejections
            + state.authority_rejections
            + state.effect_diagnostics
        ):
            rid = str(row.get("rule_id") or "")
            if rid:
                firings[rid] += 1

        divergences: List[DivergenceRow] = []
        for drow in uk_divergence_rows_for_statute(sid):
            # Prefer the finer per-EID source-pathology label over the coarse §2.1
            # bucket when it resolves to a known (more specific) disposition; the
            # coarse bucket is the fallback. "unclassified"/"" never override.
            diagnosis = drow.diagnosis
            disposition = disposition_for(diagnosis, _UK_DIAGNOSIS_DISPOSITION)
            finer = drow.source_pathology_label
            if finer and finer != "unclassified":
                finer_disp = _UK_DIAGNOSIS_DISPOSITION.get(finer)
                if finer_disp is not None:
                    diagnosis, disposition = finer, finer_disp
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=drow.eid,
                    diagnosis=diagnosis,
                    disposition=disposition,
                    rule_id=drow.rule_id or None,
                    blame_source=drow.blame_source,
                    phase_owner=drow.phase_owner,
                    authority_layer=drow.authority_layer,
                )
            )
        divergences = _demote_whole_statute_noncommensurable(divergences)
        yield StatuteLedgerInput(sid=sid, rule_firings=dict(firings), divergences=divergences)


def _load_uk_bench_ids() -> List[str]:
    """UK statute ids from data/uk/bench_corpus_smoke.csv (header + CSV rows;
    first column ``statute_id`` like ``asc/2020/1``)."""
    from pathlib import Path

    base = Path(__file__).resolve().parents[3] / "data" / "uk"
    path = base / "bench_corpus_smoke.csv"
    if not path.exists():
        path = base / "bench_corpus.csv"
    sids: List[str] = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i == 0:
                continue  # header
            first = line.strip().split(",")[0]
            if first.count("/") >= 2:
                sids.append(first)
    return sids


register_ledger_adapter(
    LedgerAdapter(
        jurisdiction="uk",
        ledger_inputs=uk_ledger_inputs,
        catalog=_UK_RULE_SPECS,
        corpus_loaders={"bench": _load_uk_bench_ids},
        roles=_UK_RULE_ROLES,
        falsifiers=_UK_RULE_FALSIFIERS,
    )
)
