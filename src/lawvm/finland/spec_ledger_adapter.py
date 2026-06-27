"""Finland adapter for the witness-attribution spec-discovery ledger.

This is the Finland sibling of :mod:`lawvm.tools.spec_ledger`'s jurisdiction-neutral
core, and of the UK / EE / US / NZ adapters. It reuses that core read-only
(``DivergenceRow`` -> ``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) and
turns Finland's oracle-check classification surface into neutral ledger inputs.

It self-registers into the core's adapter registry at import time (see
:func:`lawvm.tools.spec_ledger.register_ledger_adapter`) so ``run_ledger("fi", ...)`` and
the ``-j fi`` CLI dispatch through the registry without the core importing this package.

Run:  uv run python -m lawvm.tools.spec_ledger 1958/370 [more sids ...]
      uv run python -m lawvm.tools.spec_ledger -j fi --corpus-bench --json ledger.json
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterator, List

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    LedgerAdapter,
    Mode,
    StatuteLedgerInput,
    WitnessDisposition,
    disposition_for,
    register_ledger_adapter,
)

# Finland oracle-check ``diagnosis`` vocabulary -> witness disposition.  Diagnoses absent
# here fall back to "unknown" (loud, not silently bucketed as a pass).
_FI_DIAGNOSIS_DISPOSITION: Dict[str, WitnessDisposition] = {
    "UNKNOWN": "lawvm_wrong",
    "REPLAY_EXTRA": "lawvm_wrong",
    "REPLAY_MISSING": "lawvm_wrong",
    # An oracle whole-section repeal stub whose repealing statute is reachable and
    # in-window, yet replay kept the section — a genuine missed-repeal bug.
    "REPLAY_UNREPEALED": "lawvm_wrong",
    "EMPTY_OPERATIVE_BODY": "lawvm_wrong",
    "ORACLE_STALE": "oracle_suspect",
    "CORRIGENDUM_APPLIED": "oracle_suspect",
    "EDITORIAL_CONVENTION": "oracle_suspect",
    "REPEAL_NOTICE": "oracle_suspect",
    "SOURCE_INCOMPLETE": "missing_source",
    "SOURCE_PATHOLOGY": "missing_source",
    "MISSING": "structural",
    "EXTRA": "structural",
    "LIITE_DIFF": "structural",
}
_FI_NON_DIVERGENCE = {"NO_ORACLE", "OK", "MATCH", ""}

# Finland rule catalog seed (believed_spec prose per witness_rule_id).  Co-owned with
# the grafter seams over time; uncataloged rules show as "·" and are loud.
_FI_RULE_SPECS: Dict[str, str] = {
    "fi.section_ref": "A johtolause '<n> §' citation targets the live section <n>.",
    "fi.chapter_ref": "A johtolause '<n> luku' citation targets the live chapter <n>.",
    "fi.insertion_section": "lisätään ... uusi <n> § inserts a new section at <n>.",
    "fi.insertion_chapter": "lisätään ... uusi <n> luku inserts a new chapter at <n>.",
    "fi.insertion_sub_target": "An insertion's sub-target (momentti/kohta) lands inside its parent section.",
    "fi.jolloin_renumber": "A 'jolloin ... siirtyy' clause renumbers the displaced sections.",
    "fi_body_chapter_scope_from_source_body": "A body-scoped section inherits chapter scope from the amendment body container.",
    "fi_chapter_seed_inserted_from_amendment_body": "Chapter seeding inserts a missing base chapter from the earliest amendment body before replay.",
    "fi.recovery.uncovered_body": "Uncovered-body recovery synthesizes a section INSERT/REPLACE from unclaimed amendment body XML.",
    "fi.recovery.uncovered_body.part_insert_subtree_johto_bypass": "When an amendment INSERTs a new part, uncovered-body recovery may materialize sections carried in that part payload even if the johtolause only names other targets; same-wave repeal-placeholder slots under the inserted part may be reinstated.",
    "fi.recovery.uncovered_chapter_scaffold": "Uncovered-body recovery materializes a missing chapter scaffold needed to host recovered or parsed section operations.",
    "fi.recovery.uncovered_kumotaan": "Uncovered kumotaan recovery applies a repeal named in operative text but not emitted as a parsed structural op.",
    "fi.restructure.renumber_timeline": "A restructure-plan migration event emits an explicit RENUMBER operation so timeline compilation tombstones the old address.",
    "fi.restructure.relabel_section_snapshot": "After a successful section relabel, replay emits a payload snapshot at the live post-relabel IR path so PIT materialization owns the relocated section body.",
    "fi.restructure.chapter_part_move_timeline": "A chapter moved under a newly created part emits an old-address tombstone plus new-address insert so PIT materialization preserves the move.",
    "fi.restructure.chapter_part_move_timeline.label_reuse_guard": "Suppress inferred chapter part-move timeline LOs when the same chapter label still lives under its pre-amendment part (label reuse during part INSERT, not a cross-part move).",
    "fi.restructure.relabel_migration_ledger_lookup": "A later restructure-plan relabel may resolve amendment-frame addresses through prior same-statute migration lineage (as_of_date, not content-lineage not_before) instead of failing with target_not_found.",
    "fi.restructure.relabel_structural_label_alias_lookup": "A restructure-plan relabel may resolve live tree nodes whose display label differs from the amendment-frame token when Finland structural-label normalization proves equivalence (for example part IIa vs part 2a).",
    "fi.process.post_apply_label_dedup": "After a restructure-heavy amendment apply loop, transient same-kind+label siblings are removed with the global replay-fold dedup backstop before the next amendment consumes the state.",
    "fi.replay.fold_timeline_backfill": "Before PIT materialization, replay products graft fold-owned section snapshots onto timeline addresses that only received payload-less renumber/move authority during restructure waves.",
    "fi.elaboration.named_row_province_table_merge": "Named regional table replaces merge only the claimed province blocks from a sparse amendment table into the live province layout instead of replacing the whole section.",
}

# Fold the FI catalog supplement (firing parse-witness rules + the fallback-extraction
# lane id, authored in a sibling module) into a SEPARATE merged catalog used by the
# ledger, leaving the seed ``_FI_RULE_SPECS`` literal pure (the anti-drift test in
# tests/test_fi_spec_ledger_catalog.py checks base and supplement separately). Mirrors
# the UK split.
def _load_fi_rule_specs() -> Dict[str, str]:
    specs = dict(_FI_RULE_SPECS)
    try:
        from lawvm.tools.spec_ledger_fi_catalog_supplement import (
            _FI_RULE_SPECS_SUPPLEMENT,
        )
        specs.update(_FI_RULE_SPECS_SUPPLEMENT)
    except ImportError:
        pass
    return specs


_FI_RULE_SPECS_FULL: Dict[str, str] = _load_fi_rule_specs()


def fi_ledger_inputs(sids: List[str], mode: Mode) -> Iterator[StatuteLedgerInput]:
    """Turn Finland's ClassifyResult surface into neutral ledger inputs.

    firings come from ``compiled_ops[].witness_rule_id``; divergences come from
    ``section_results`` (per-section ``diagnosis``), attributed to the witness rule of
    the blame op resolved the same way as oracle-check classify
    (``_blame_map_for_classify_result`` / ``_lookup_blame_op_for_classify_result``),
    including replay ``lo_ops`` lineage witnesses and migration-event predecessor lookup.
    """
    from lawvm.tools.oracle_check import (
        _classify_statute_sync,
        _lookup_blame_op_for_classify_result,
    )

    for sid in sids:
        cr = _classify_statute_sync(sid, mode)
        if cr is None or cr.error:
            continue  # caller counts errors separately via the sentinel below
        firings: Dict[str, int] = defaultdict(int)
        for op in cr.compiled_ops:
            if isinstance(op, dict):
                rid = op.get("witness_rule_id") or ""
                if rid:
                    firings[rid] += 1
        divergences: List[DivergenceRow] = []
        for sec in cr.section_results:
            diagnosis = str(sec.get("diagnosis") or "")
            if diagnosis in _FI_NON_DIVERGENCE:
                continue
            section_key = str(sec.get("section") or "")
            blame_op = _lookup_blame_op_for_classify_result(cr, section_key)
            rid = blame_op.get("witness_rule_id") if isinstance(blame_op, dict) else None
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=section_key,
                    diagnosis=diagnosis,
                    disposition=disposition_for(diagnosis, _FI_DIAGNOSIS_DISPOSITION),
                    rule_id=rid or None,
                    blame_source=str(sec.get("blame_source") or ""),
                )
            )
        yield StatuteLedgerInput(sid=sid, rule_firings=dict(firings), divergences=divergences)


def _load_bench_core_ids() -> List[str]:
    """Finland statute ids from data/finland/bench_core.csv (``count,sid`` rows)."""
    from pathlib import Path

    base = Path(__file__).resolve().parents[3] / "data" / "finland"
    path = base / "bench_core.csv"
    if not path.exists():
        path = base / "bench_corpus.csv"
    sids: List[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) >= 2 and "/" in parts[1]:
                sids.append(parts[1])
    return sids


register_ledger_adapter(
    LedgerAdapter(
        jurisdiction="fi",
        ledger_inputs=fi_ledger_inputs,
        catalog=_FI_RULE_SPECS_FULL,
        corpus_loaders={"bench": _load_bench_core_ids},
    )
)
