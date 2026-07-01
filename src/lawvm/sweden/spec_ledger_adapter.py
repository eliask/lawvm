"""Sweden (SE) adapter for the witness-attribution spec-discovery ledger.

This is the SE sibling of :mod:`lawvm.tools.spec_ledger`'s jurisdiction-neutral core,
and of the FI / UK / EE / NO / US / NZ adapters. It reuses that core read-only
(``DivergenceRow`` -> ``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) and
turns Sweden's replay-vs-consolidation surface (``check_se_official_replay``) into
neutral ledger inputs.

It self-registers into the core's adapter registry at import time (see
:func:`lawvm.tools.spec_ledger.register_ledger_adapter`) so ``run_ledger("se", ...)`` and
the ``-j se`` CLI dispatch through the registry without the core importing this package.

SE replays each amending SFS act against the authoritative SFS consolidated-text oracle
(``notes/SWEDEN_LAWVM_STATUS.md``). That oracle is a single-version (latest) consolidation,
so the dominant residual family is ``temporal_mismatch`` (the oracle folds strictly-later
amendments) — a first-class ``oracle_suspect`` finding, NOT a replay bug. The classify
surface's per-row ``classification`` string is the diagnosis vocabulary; it is mapped
through the same closed family table (:mod:`lawvm.sweden.se_agreement_residuals`) that the
typed residual ledger uses, so the ledger's disposition is a faithful projection of the
frontend's own residual taxonomy.

Firings (each a named, cataloged SE ``rule_id`` hypothesis):

* the projector's uniform witness ``se_replay_classification_to_agreement_residual``
  fires once per compared row (the row-classification hypothesis);
* per-op replay-skip **adjudications** on the result (``se_replay_*`` reason codes),
  when present, each fire their reason-code hypothesis.

Divergences come from the *unresolved* rows (residual / frontier status): a matching or
editorially-folded row is corroboration, not a divergence.

Run:  uv run python -m lawvm.tools.spec_ledger -j se 1999:280
      uv run python -m lawvm.tools.spec_ledger -j se --corpus-bench --json ledger.json
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterator, List, Mapping

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    LedgerAdapter,
    Mode,
    StatuteLedgerInput,
    WitnessDisposition,
    disposition_for,
    register_ledger_adapter,
)

# The uniform witness rule the SE agreement-residual projector stamps on every row
# (cataloged in ``spec_ledger_se_catalog``). Each compared row is one firing of this
# row-classification hypothesis.
_SE_CLASSIFICATION_RULE_ID = "se_replay_classification_to_agreement_residual"

# SE residual *family* -> witness disposition. The per-row ``classification`` resolves
# to a family via the closed table in ``se_agreement_residuals`` (the same table the
# typed residual ledger types over); we translate that family into the neutral
# disposition. Discipline mirrors EE: ``temporal_mismatch`` / editorial pathology are
# ``oracle_suspect`` (the single-version oracle carries a later PIT / editorial shape,
# replay coherent — a finding, not our bug); ``replay_bug`` is ``lawvm_wrong`` (the §0
# 3-way frontier the doctrine splits, defaulting to "suspect our own rule first");
# ``unknown`` stays loud. Families absent here fall to "unknown".
_SE_FAMILY_DISPOSITION: Dict[str, WitnessDisposition] = {
    "agreement": "unknown",  # never reaches a divergence row (agrees status filtered)
    "oracle_editorial_pathology": "oracle_suspect",
    "temporal_mismatch": "oracle_suspect",
    "replay_bug": "lawvm_wrong",
    "unknown": "unknown",
}


def _load_se_rule_specs() -> Dict[str, str]:
    """Believed-spec catalog authored by a sibling agent; {} if absent."""
    try:
        from lawvm.tools.spec_ledger_se_catalog import _SE_RULE_SPECS
    except ImportError:
        return {}
    return dict(_SE_RULE_SPECS)


_SE_RULE_SPECS: Dict[str, str] = _load_se_rule_specs()


def _se_classification_family_table() -> Mapping[str, tuple]:
    """The closed classification->(family, status, ...) table, or {} if unavailable."""
    try:
        from lawvm.sweden.se_agreement_residuals import _SE_CLASSIFICATION_FAMILY_TABLE
    except ImportError:
        return {}
    return _SE_CLASSIFICATION_FAMILY_TABLE


def _row_is_divergence(classification: str, matched: bool, table: Mapping[str, tuple]) -> bool:
    """A row is a divergence when its residual status is unresolved (frontier/residual).

    Falls back to the raw ``match`` flag when the classification is not in the closed
    table (a new class the residual module has not yet mapped — loud, never a silent
    pass): an unmatched row is a divergence.
    """
    entry = table.get(classification)
    if entry is None:
        return not matched
    status = entry[1]  # (family, status, safe_default, missing_proofs)
    return status in {"frontier", "residual"}


def se_ledger_inputs(sids: List[str], mode: Mode) -> Iterator[StatuteLedgerInput]:
    """Turn Sweden's ``check_se_official_replay`` surface into neutral ledger inputs.

    ``sids`` are amending SFS ids (``YYYY:N``). ``mode`` is accepted for signature
    parity; SE compares against the latest single-version consolidation oracle.
    Statutes whose oracle / ops artifacts are not archived raise inside the replay and
    are skipped (counted as errors by ``run_ledger``), mirroring EE/UK.
    """
    from lawvm.sweden.fetch import check_se_official_replay, open_se_archive

    archive = open_se_archive(_se_archive_path())
    table = _se_classification_family_table()
    for sid in sids:
        try:
            result = check_se_official_replay(archive, sid)
        except (FileNotFoundError, ValueError, KeyError, AssertionError):
            continue  # oracle/ops not archived, or unfeasible: caller counts errors

        rows = result.get("rows") or []
        base_sfs_id = str(result.get("base_sfs_id") or "")

        firings: Dict[str, int] = defaultdict(int)
        divergences: List[DivergenceRow] = []
        for row in rows:
            classification = str(row.get("classification") or "")
            matched = bool(row.get("match"))
            section_key = str(row.get("section") or "")
            # Each compared row is one firing of the row-classification hypothesis.
            firings[_SE_CLASSIFICATION_RULE_ID] += 1
            if not _row_is_divergence(classification, matched, table):
                continue
            entry = table.get(classification)
            family = str(entry[0]) if entry is not None else ""
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=section_key,
                    diagnosis=classification or "unclassified",
                    disposition=disposition_for(family, _SE_FAMILY_DISPOSITION),
                    rule_id=_SE_CLASSIFICATION_RULE_ID,
                    blame_source=base_sfs_id,
                )
            )

        # Per-op replay-skip adjudications (``se_replay_*`` reason codes) are named
        # hypotheses too; tally each as a firing so its corroborated/contradicted
        # arithmetic is well-formed. Adjudications may be dict- or object-shaped.
        for adjudication in result.get("adjudications") or []:
            kind = _se_adjudication_kind(adjudication)
            if kind:
                firings[kind] += 1

        yield StatuteLedgerInput(
            sid=sid, rule_firings=dict(firings), divergences=divergences
        )


def _se_adjudication_kind(adjudication: Any) -> str:
    if isinstance(adjudication, Mapping):
        return str(adjudication.get("reason_code") or adjudication.get("kind") or "")
    return str(getattr(adjudication, "reason_code", "") or getattr(adjudication, "kind", "") or "")


def _se_archive_path():
    """Resolve the populated SE farchive (honors ``LAWVM_CANONICAL_DATA_ROOT``)."""
    from lawvm.corpus_store import resolve_farchive_path

    path, _rule = resolve_farchive_path("sweden.farchive", explicit_env="LAWVM_SWEDEN_DB")
    return path


def _load_se_bench_ids() -> List[str]:
    """Amending SFS ids with compiled ops (the enumerable SE replay corpus).

    SE has no committed bench CSV; the corpus is every amending act carrying compiled
    ops in the archive, bounded to a smoke slice so ``--corpus-bench`` is fast.
    """
    from lawvm.sweden.fetch import (
        open_se_archive,
        se_amending_sfs_ids_with_compiled_ops,
    )

    archive = open_se_archive(_se_archive_path())
    ids = se_amending_sfs_ids_with_compiled_ops(archive)
    return list(ids[:_SE_BENCH_LIMIT])


_SE_BENCH_LIMIT = 200


register_ledger_adapter(
    LedgerAdapter(
        jurisdiction="se",
        ledger_inputs=se_ledger_inputs,
        catalog=_SE_RULE_SPECS,
        corpus_loaders={"bench": _load_se_bench_ids},
    )
)
