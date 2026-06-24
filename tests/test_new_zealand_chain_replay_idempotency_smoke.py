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


def test_resolve_target_nodes_rejects_part_at_xml_id_leading_segment() -> None:
    """Synthetic regression proving the `amendment_skipped_target_absent` gap
    documented in `notes/IMPLEMENTATION_DIVERGENCE_LEDGER.md`'s
    "amendment_skipped_target_absent — classified 2026-06-22" section.

    The source-tree parser emits a `part@DLM_xml_id`-shaped path segment when
    a `<part>` element lacks a parseable `<label>` — observed in
    `act_public_1981_23_en_2007-09-03` (199 nodes with @-segments) and absent
    in `act_public_1981_23_en_2026-02-21` (0 such nodes). The chain-replay starts
    at the earliest archived version; ops resolve against the clean
    `prov:N`/`subprov:N` form from the operation surface; `_resolve_target_nodes`
    accepts the leading-1-extra-part tolerance only when
    `path[0].split(':',1)[0] == "part"` — and `'part@DLM44815'.split(':',1)[0]`
    returns the full `'part@DLM44815'` (no colon in the segment), so the check
    fails and the op is correctly target_absent-skipped per the BUG (not honestly).

    This test PINS the buggy current behavior as a synthetic witness so the
    subsequent strict-superset widening of `_resolve_target_nodes` flips this
    test green as a paired change. (AGENTS §2.5 — a parser-invariant widening
    is a paired spec + test change; this test asserts the gap first.)
    """
    from lawvm.new_zealand.source_tree import parse_nz_source_document

    # XML where <part> lacks <label>: parser emits path[0] = `part@DLM888`-shaped.
    # A <prov> nested inside an unlabeled <part>: its path will become
    # `('part@DLM888', 'prov:21')`.
    xml = b"""\
<act>
  <body>
    <part id="DLM888"><heading>Unlabeled part -- parser falls back to xml_id</heading>
      <prov id="DLM821" deletion-status=""><label>21</label><heading>Section 21</heading>
        <prov.body><para><text>21 Section body 21 original.</text></para></prov.body></prov>
    </part>
  </body>
</act>
"""
    doc = parse_nz_source_document(xml, xml_locator="before", version_id="before")
    # Confirm parser produced the part@xml_id shape.
    prov21_nodes = [n for n in doc.nodes if n.kind == "prov" and n.label == "21"]
    assert prov21_nodes, "expected prov:21 to parse"
    prov21 = prov21_nodes[0]
    assert prov21.path[0].startswith("part@"), (
        f"expected the source-tree parser to fall back to `part@xml_id` shape for "
        f"unlabeled <part>, got {prov21.path[0]!r} -- parser behavior changed! "
        f"Either the parser now produces a stable `part:N` shape for unlabeled <part> "
        f"(GREAT -- this test should be updated to pin the new behavior) OR the synthetic "
        f"XML here no longer triggers the fallback (update the XML fixture)."
    )

    # Piece 2 of the part@xml_id widening lane (paired spec + test change, AGENTS
    # §2.5): _resolve_target_nodes('part@DLM888', 'prov:21') with the op-resolved
    # path ('prov:21',) now resolves via the widened _is_leading_part_segment
    # predicate (accepts both the labeled `part:N` form and the unlabeled
    # `part@xml_id` fallback shape). The synthetic fixture pins the parser-side
    # invariant; this assertion pins the resolver-side widening.
    matches = _resolve_target_nodes(doc, ("prov:21",))
    assert len(matches) == 1, (
        f"_resolve_target_nodes did not resolve the `part@xml_id` leading segment "
        f"for path ('prov:21',). The strict-superset widening (helper "
        f"_is_leading_part_segment in dry_run.py) must have regressed back to the "
        f"prior narrow predicate (`path[0].split(':',1)[0] == 'part'`) that rejected "
        f"the unlabeled-`<part>` fallback shape. matches={matches}"
    )
    assert matches[0].label == "21"
    assert matches[0].path == ("part@DLM888", "prov:21")


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.skipif(not _SMOKE_CORPUS.exists(), reason="NZ smoke corpus CSV not present")
@pytest.mark.slow
def test_chain_replay_target_absent_no_in_oracle_prov_level_after_part_at_xml_id_widening() -> None:
    """After the strict-superset widening of `_resolve_target_nodes` (helper
    `_is_leading_part_segment`) that accepts BOTH the labeled `part:N` form
    AND the unlabeled `part@DLM_xml_id` fallback shape, the witness work
    `act_public_1981_23` (199 node paths carrying `@`-segments in its
    earliest archived snapshot — see the ledger's
    `amendment_skipped_target_absent -- classified 2026-06-22` section)
    MUST have ZERO chain-replay `amendment_skipped_target_absent` skips on
    prov-level section paths whose source_path STILL resolves to a node in
    the work's FINAL archived oracle.

    Before the widening the resolved-path check `path[0].split(':', 1)[0] == 'part'`
    silently rejected `part@DLM44667`-shaped segments (no colon → the split
    returns the full string), framing a deterministic gap as honest
    `target_absent` residue. After the widening those sections resolve
    against the carried tree and proceed (closing ~50-60 of the 62
    post-cutoff in-oracle-substantive target_absent skips this lane
    estimated).

    Witnesses the ledger's piecemeal step 3 ("cross-corpus @slow regression
    pinning that structural invariant") in narrow form: scrubbed
    corpus-wide to confirm the part@xml_id lane is **not** the cause of the
    remaining 10 offenders (those cluster on the omnibus-reparent lane:
    `act_public_1992_122`, `act_public_2022_77`, `act_public_2009_13`).
    The witness-work assertion is the strict-equality regression surface:
    any re-narrowing of `_is_leading_part_segment` (or any change that
    re-introduces an in-oracle prov-level target_absent skip on
    `act_public_1981_23`) fails this test precisely because the closing
    condition stopped holding.

    AGENTS §0 + §1.1: target hijacking via ambiguous search widening is
    forbidden; this test verifies the conservative bounded widening (a
    same-kind fallback shape emitted by the same parser path) didn't
    regress into a coarse parent-coercion fallback — the assertion is that
    the carried-tree prov-level target resolvers STILL operate per the
    strict single-match rule, only now under both `part` shapes.
    AGENTS §2.9: a structural invariant (zero, not a fragile count).
    """
    work_id = "act_public_1981_23"
    archive = open_farchive(_REAL_DB)
    try:
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
        assert versions, f"no archived versions for {work_id}"
        oracle_doc = _parse_archived_version(archive, versions[0], {})
        assert oracle_doc is not None, f"could not parse latest archived version of {work_id}"

        in_oracle_section: list[tuple[str, tuple[str, ...]]] = []
        saw_prov_level_absent = False
        for skip in report.get("skips", ()):
            if skip.get("bucket") != "amendment_skipped_target_absent":
                continue
            src_path = tuple(skip.get("source_path", ()))
            if not src_path:
                continue
            head = src_path[0]
            # Prov-level section path: topmost is `prov:N`, OR a single
            # leading `part:N` / `part@xml_id` wrapper with `prov:N` next.
            if head.split(":", 1)[0] == "prov" and not any(
                p.split(":", 1)[0] in ("subprov", "para", "def-para", "item")
                for p in src_path[1:]
            ):
                is_prov_level = True
            elif (
                (head.split(":", 1)[0] == "part" or head.startswith("part@"))
                and len(src_path) >= 2
                and src_path[1].split(":", 1)[0] == "prov"
                and not any(
                    p.split(":", 1)[0] in ("subprov", "para", "def-para", "item")
                    for p in src_path[2:]
                )
            ):
                is_prov_level = True
            else:
                is_prov_level = False
            if not is_prov_level:
                continue
            saw_prov_level_absent = True
            if _resolve_target_nodes(oracle_doc, src_path):
                in_oracle_section.append(
                    (skip.get("amendment_date_iso", ""), src_path)
                )
    finally:
        archive.close()

    # The widening paid in: there are prov-level target_absent skips
    # (legacy pre-cutoff / genuine missing-target cases — saw_prov_level_absent
    # may be True), but NONE of them point at a substantive node the FINAL
    # oracle still carries live.
    assert not in_oracle_section, (
        f"act_public_1981_23 (199 part@xml_id paths in earliest snapshot): "
        f"{len(in_oracle_section)} post-widening `amendment_skipped_target_absent` "
        f"skip(s) on prov-level section paths whose source_path STILL resolves in "
        f"the work's FINAL archived oracle. The `_is_leading_part_segment` widening "
        f"(dry_run.py) has either regressed to the narrow `path[0].split(':',1)[0]=='part'` "
        f"predicate, OR a new structural path shape is no longer covered by the strict-"
        f"superset widening (audit `_resolve_target_nodes` and "
        f"`_is_leading_part_segment`). amendment_date, source_path for the offenders: "
        f"{in_oracle_section[:8]}"
    )
