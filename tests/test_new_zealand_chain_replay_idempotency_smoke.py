"""Smoke corpus pin on the NZ chain-replay insert-idempotency contract.

Pins the corpus-classification finding verified in this session:
  Every `amendment_skipped_insert_target_already_present` skip in
  `nz-corpus replay-chain --work-id <X> --json` output across the curated
  smoke corpus MUST point at a source_path that resolves to a real node in
  the work's FINAL archived oracle.

That invariant distinguishes HONEST idempotency (the carried tree already
inserted the node in an earlier transition; later duplicated witness rows on
the same source path are correctly skipped; the final oracle carries the
node, so no divergence is framed) from a DETERMINISTIC GAP (chain-replay
silently skips an insert whose source_path has NO final-oracle counterpart —
which would frame a divergence as a "skipped" op and hide a real replay bug).

A future change that introduces a path-not-in-final-oracle skip will fail
this test precisely because the skip no longer reflects honest idempotency.

Witness (verified 2026-06-22): 775/775 of the smoke corpus's
`amendment_skipped_insert_target_already_present` skips are in the final
archived oracle of their respective works. The contract is HONEST.

@slow because the corpus sweep runs the chain-replay end-to-end across the
33-work smoke set (~5-7 min on the dev clone). Pure measurement; never
mutates the archive.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
from pathlib import Path

import pytest

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dry_run import _parse_archived_version, _resolve_target_nodes
from lawvm.new_zealand.version_diff import archived_xml_versions_for_work

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATA_ROOT = Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or _REPO_ROOT)
_REAL_DB = _DATA_ROOT / "data" / "nz_legislation.farchive"
_SMOKE_CORPUS = _DATA_ROOT / "data" / "nz" / "bench_corpus_smoke.csv"


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.skipif(not _SMOKE_CORPUS.exists(), reason="NZ smoke corpus CSV not present")
@pytest.mark.slow
def test_chain_replay_insert_already_present_skips_are_in_final_oracle() -> None:
    """Every `amendment_skipped_insert_target_already_present` skip's source_path
    MUST resolve to a node in the work's final archived oracle.

    Witnesses the chain-replay idempotency contract: a skip means the carried
    tree already has the node from an earlier transition — and the final
    archived oracle (end-state of the work's archived snapshot chain) carries
    the same node, so the idempotency is HONEST rather than a deterministic
    gap (chain-replay silently skipping an insert whose source_path has NO
    final-oracle counterpart would frame a divergence invisibly).

    AGENTS §1.8: conservation evidence. AGENTS §0: every skip is auditable.
    AGENTS §2.9: a regression that introduces a path-not-in-final-oracle skip
    fails this test precisely because the skip stops reflecting honest
    idempotency.

    Tested @slow because the smoke corpus sweep drives the per-work chain
    replay end-to-end across 33 works.
    """
    works = [row["work_id"] for row in csv.DictReader(_SMOKE_CORPUS.open())]
    assert works, "smoke corpus CSV present but had no work rows"

    archive = open_farchive(_REAL_DB)
    not_in_oracle: list[tuple[str, str, tuple[str, ...], str]] = []
    total_already_present_skips = 0
    try:
        for work_id in works:
            # Per-work chain replay: invoke the CLI as a downstream operator would;
            # it surfaces the per-op skip rows as JSON we read in-process to assert
            # the contract. The subprocess approach mirrors the same path a human
            # operator would invoke, so a regression in the CLI path also surfaces.
            result = subprocess.run(
                ["uv", "run", "lawvm", "nz-corpus", "replay-chain",
                 "--work-id", work_id, "--json"],
                capture_output=True, text=True, cwd=str(_REPO_ROOT),
            )
            if result.returncode != 0:
                pytest.fail(
                    f"chain-replay failed for {work_id}: stderr={result.stderr[:400]}"
                )
            report = json.loads(result.stdout)
            versions = archived_xml_versions_for_work(archive, work_id)
            if not versions:
                continue
            oracle_doc = _parse_archived_version(archive, versions[0], {})
            if oracle_doc is None:
                continue
            for skip in report.get("skips", ()):
                if skip.get("bucket") != "amendment_skipped_insert_target_already_present":
                    continue
                total_already_present_skips += 1
                src_path = tuple(skip.get("source_path", ()))
                if not src_path:
                    continue
                if not _resolve_target_nodes(oracle_doc, src_path):
                    not_in_oracle.append(
                        (work_id, skip.get("row_id", ""), src_path, skip.get("amendment_date_iso", ""))
                    )
    finally:
        archive.close()

    # Invariant: the smoke corpus has at least one already-present skip (act_public_1956_47
    # alone has 271); a future change that breaks even one of these into a
    # not-in-oracle skip is the regression signal we want to flag.
    assert total_already_present_skips > 0, (
        "smoke corpus pin: 0 insert_target_already_present skips emitted. "
        "Either the corpus lost its amendment-bearing works OR the chain-replay "
        "stopped emitting the idempotency skip bucket entirely — both are a "
        "regression in the corpus-classification fixture."
    )
    assert not not_in_oracle, (
        f"smoke corpus pin: {len(not_in_oracle)} chain-replay insert_target_already_present "
        f"skip(s) had source_path NOT in the work's final archived oracle (deterministic "
        f"gap regression — the skip no longer reflects honest idempotency). "
        f"work_id, row_id, source_path, amendment_date for the offenders: "
        f"{not_in_oracle[:8]}"
    )


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.skipif(not _SMOKE_CORPUS.exists(), reason="NZ smoke corpus CSV not present")
@pytest.mark.slow
def test_chain_replay_already_tombstoned_skips_no_section_repeal_mismatch() -> None:
    """For every `amendment_skipped_already_tombstoned` skip whose leaf segment is
    `prov:`, the source_path MUST NOT resolve to a substantive node in the work's
    final archived oracle. A prov-level section repeal is structural (the section
    body is gone); if the final oracle carries the section substantive, that
    combination is a real replay bug (the chain-marked-tombstoned a section the
    oracle kept live).

    Verified (2026-06-22) classification of all 135 smoke-corpus already-tombstoned
    skips: the 53 "in-oracle-and-substantive" suspects are ALL either schedule-
    level rows (history-note "repealed" misclassified a repeal-and-substitute
    structural replace) or part-level rows (heading-facet repeal, not body). The
    invariant pinned here is that NONE of these substantive-in-oracle mismatches
    are at the section level — a section-level mismatch would be a deterministic
    gap, not honest frontier residue.

    See `notes/IMPLEMENTATION_DIVERGENCE_LEDGER.md`'s
    "amendment_skipped_already_tombstoned — classified 2026-06-22" section for the
    full breakdown.

    AGENTS §2.9: structural invariant (no fragile exact count). Raising the
    corpus-wide already-tombstoned count is fine; introducing even one section-
    level substantive-in-oracle mismatch fails this test precisely because that
    would be a real §1.0 mutation-boundary regression.
    """
    works = [row["work_id"] for row in csv.DictReader(_SMOKE_CORPUS.open())]
    archive = open_farchive(_REAL_DB)
    section_mismatches: list[tuple[str, str, tuple[str, ...], str]] = []
    try:
        for work_id in works:
            result = subprocess.run(
                ["uv", "run", "lawvm", "nz-corpus", "replay-chain",
                 "--work-id", work_id, "--json"],
                capture_output=True, text=True, cwd=str(_REPO_ROOT),
            )
            if result.returncode != 0:
                pytest.fail(
                    f"chain-replay failed for {work_id}: stderr={result.stderr[:400]}"
                )
            report = json.loads(result.stdout)
            versions = archived_xml_versions_for_work(archive, work_id)
            if not versions:
                continue
            oracle_doc = _parse_archived_version(archive, versions[0], {})
            if oracle_doc is None:
                continue
            for skip in report.get("skips", ()):
                if skip.get("bucket") != "amendment_skipped_already_tombstoned":
                    continue
                src_path = tuple(skip.get("source_path", ()))
                if not src_path:
                    continue
                # Invariant applies only to section-level (leaf `prov:`) repeals.
                # schedule/part/subprov "repeal-and-substitute" misclassifications
                # are honest frontier; this test EXCLUSIVELY guards the prov-level
                # invariant because a section-tombstone with a live oracle is a
                # structural bug, not a frontier lane.
                if not src_path[-1].startswith("prov:"):
                    continue
                matches = _resolve_target_nodes(oracle_doc, src_path)
                if not matches:
                    continue  # final oracle removed the section entirely — honest
                occupancy = None
                # _occupancy returns "tombstone" / "substantive" / etc.
                from lawvm.new_zealand.dry_run import _occupancy
                occupancy = _occupancy(matches[0])
                if occupancy == "substantive":
                    section_mismatches.append(
                        (work_id, skip.get("row_id", ""), src_path, skip.get("amendment_date_iso", ""))
                    )
    finally:
        archive.close()

    assert not section_mismatches, (
        f"chain-replay idempotency pin: {len(section_mismatches)} section-level "
        f"(`prov:`) amendment_skipped_already_tombstoned skip(s) had source_path "
        f"matching a SUBSTANTIVE node in the work's final archived oracle (§1.0 "
        f"mutation-boundary regression — the chain tombstoned a section the oracle "
        f"kept live; investigation required). work_id, row_id, source_path, "
        f"amendment_date for the offenders: {section_mismatches[:8]}"
    )
