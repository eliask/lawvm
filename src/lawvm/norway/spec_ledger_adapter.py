"""Norway (NO) adapter for the witness-attribution spec-discovery ledger.

This is the NO sibling of :mod:`lawvm.tools.spec_ledger`'s jurisdiction-neutral core,
and of the FI / UK / EE / US / NZ adapters. It reuses that core read-only
(``DivergenceRow`` -> ``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) and
turns Norway's replay-vs-current consistency surface (``verify_no_against_current``)
into neutral ledger inputs.

It self-registers into the core's adapter registry at import time (see
:func:`lawvm.tools.spec_ledger.register_ledger_adapter`) so ``run_ledger("no", ...)`` and
the ``-j no`` CLI dispatch through the registry without the core importing this package.

NO replays as a *consistency verification* against the live Lovdata consolidated text
(``notes/NORWAY_LAWVM_STATUS.md``). Like EE, that consolidation is law-in-force but not
necessarily consolidation-correct, so ``oracle_suspect`` is a first-class outcome and a
raw structural divergence (``OPS_MISSING`` / ``CONSOLIDATED_MISSING`` / ``MISMATCH`` from
``core.timeline_consistency``) defaults to the conservative *humble* disposition rather
than deference to the consolidation.

Two firing surfaces feed the ledger (both carrying cataloged NO ``rule_id`` witnesses):

* per-op replay/parse **adjudications** (``NOReplayResult.adjudications``): each
  ``adjudication.kind`` is a named, cataloged hypothesis (``no_parse_*`` /
  ``no_replay_*``) about how NO amendment law transforms the base;
* per-op **write receipts** (``NOReplayResult.write_receipts``): each receipt's
  ``named_rule_ids`` / ``migration_rule_ids`` / ``recovery_rule_ids`` /
  ``fallback_rule_ids`` name the rule that produced the landed op (e.g.
  ``no_section_renumber_relabel``).

Divergences come from ``NOVerifyResult.divergences`` (the primary
``ConsistencyDivergence`` partition); each is attributed to the write-receipt op whose
landed / created address contains (is a prefix-or-equal of) the divergence address —
no such receipt => an unattributed blind spot (the ledger's frontier).

Run:  uv run python -m lawvm.tools.spec_ledger -j no no/lov/2008-05-15-35
      uv run python -m lawvm.tools.spec_ledger -j no --corpus-bench --json ledger.json
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    LedgerAdapter,
    Mode,
    StatuteLedgerInput,
    WitnessDisposition,
    disposition_for,
    register_ledger_adapter,
)

# Norway raw consistency ``divergence_type`` (from ``core.timeline_consistency``) ->
# witness disposition. The three raw types mirror EE's raw consistency vocabulary.
# Absent diagnoses fall to "unknown" (loud, never a silent pass). Discipline (as in
# EE): the raw structural default is ``lawvm_wrong`` as the conservative "suspect our
# own rule first" stance — over-attributing to ourselves is the safe direction; the
# ``structural`` disposition owns a pure present/absent node mismatch (no text to
# adjudicate as our bug yet).
_NO_DIAGNOSIS_DISPOSITION: Dict[str, WitnessDisposition] = {
    # both sides carry the address but the text differs — a real replay/consolidation
    # divergence to type (never auto-repaired): humbly "our bug first".
    "MISMATCH": "lawvm_wrong",
    # in the current oracle but not in replay: replay is missing content it should
    # have produced (a missed op) — falsifying.
    "CONSOLIDATED_MISSING": "lawvm_wrong",
    # in replay but not in the current oracle: replay produced content the
    # consolidation does not carry — present/absent node mismatch owned as structural
    # (could be a replay surplus OR an oracle omission; not pinned as our bug).
    "OPS_MISSING": "structural",
}

# Believed-spec catalog authored by a sibling agent in
# ``lawvm.tools.spec_ledger_no_catalog``; import if present, else fall back to {} so the
# adapter works whether or not that module exists yet.
def _load_no_rule_specs() -> Dict[str, str]:
    try:
        from lawvm.tools.spec_ledger_no_catalog import _NO_RULE_SPECS
    except ImportError:
        return {}
    return dict(_NO_RULE_SPECS)


_NO_RULE_SPECS: Dict[str, str] = _load_no_rule_specs()

# The receipt rule-id facets, in attribution-preference order (a landed named rule is a
# stronger owner than a fallback).
_NO_RECEIPT_RULE_FIELDS: Tuple[str, ...] = (
    "named_rule_ids",
    "migration_rule_ids",
    "recovery_rule_ids",
    "fallback_rule_ids",
)


def _receipt_paths(receipt: object) -> List[Tuple[Tuple[str, str], ...]]:
    """Every address a write receipt touched (landed / created / renumbered)."""
    paths: List[Tuple[Tuple[str, str], ...]] = []
    for attr in ("landed_primary_path", "bound_target_path"):
        value = getattr(receipt, attr, None)
        if value:
            paths.append(tuple(value))
    for attr in ("created_paths", "renumbered_paths", "removed_paths", "consumed_paths"):
        for value in getattr(receipt, attr, None) or ():
            if value:
                paths.append(tuple(value))
    return paths


def _receipt_rule_ids(receipt: object) -> List[str]:
    ids: List[str] = []
    for field in _NO_RECEIPT_RULE_FIELDS:
        for rid in getattr(receipt, field, None) or ():
            if rid:
                ids.append(str(rid))
    return ids


def _path_covers(owner: Tuple[Tuple[str, str], ...], target: Tuple[Tuple[str, str], ...]) -> bool:
    """True when ``owner`` is a prefix-or-equal of ``target`` (owner owns target)."""
    if not owner or len(owner) > len(target):
        return False
    return tuple(target[: len(owner)]) == tuple(owner)


def _attribute_divergence(
    div_path: Tuple[Tuple[str, str], ...],
    receipt_index: Sequence[Tuple[Tuple[Tuple[str, str], ...], str]],
) -> Optional[str]:
    """Owning rule id of the receipt whose touched path best covers ``div_path``.

    Prefers the longest (most specific) covering owner path; returns ``None`` when no
    receipt op owns the divergence address — an unattributed blind spot.
    """
    best_len = -1
    best_rid: Optional[str] = None
    for owner_path, rid in receipt_index:
        if _path_covers(owner_path, div_path) and len(owner_path) > best_len:
            best_len = len(owner_path)
            best_rid = rid
    return best_rid


def no_ledger_inputs(sids: List[str], mode: Mode) -> Iterator[StatuteLedgerInput]:
    """Turn Norway's ``verify_no_against_current`` surface into neutral ledger inputs.

    ``sids`` are Norway ``base_id``s (``no/lov/YYYY-MM-DD-N``). ``mode`` is accepted for
    signature parity; NO verifies against the single live consolidated text at the
    fixed compare as-of below (the point-in-time-less current-oracle comparison).
    """
    from lawvm.norway.verify import verify_no_against_current

    as_of = _NO_COMPARE_AS_OF
    for sid in sids:
        result = verify_no_against_current(sid, as_of=as_of)
        if result.error or result.replay is None:
            continue  # caller counts errors separately via the sentinel in run_ledger

        firings: Dict[str, int] = defaultdict(int)
        receipt_index: List[Tuple[Tuple[Tuple[str, str], ...], str]] = []
        for receipt in getattr(result.replay, "write_receipts", None) or ():
            rule_ids = _receipt_rule_ids(receipt)
            for rid in rule_ids:
                firings[rid] += 1
            if rule_ids:
                for path in _receipt_paths(receipt):
                    receipt_index.append((path, rule_ids[0]))
        for adjudication in getattr(result.replay, "adjudications", None) or ():
            kind = str(getattr(adjudication, "kind", "") or "")
            if kind:
                firings[kind] += 1

        divergences: List[DivergenceRow] = []
        for div in result.divergences or ():
            diagnosis = str(div.divergence_type)
            div_path = tuple(div.address.path)
            rid = _attribute_divergence(div_path, receipt_index)
            section_key = "/".join(f"{kind}:{label}" for kind, label in div_path)
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=section_key,
                    diagnosis=diagnosis,
                    disposition=disposition_for(diagnosis, _NO_DIAGNOSIS_DISPOSITION),
                    rule_id=rid,
                    blame_source="",  # NO oracle is authoritative, not a blamed source
                )
            )
        yield StatuteLedgerInput(
            sid=sid, rule_firings=dict(firings), divergences=divergences
        )


# The fixed compare as-of for the NO current-oracle comparison. The Lovdata current
# text is the latest consolidation; a recent date requests "replay everything to now"
# so the full amendment chain is applied before the compare (mirrors the NO verify CLI
# default). Kept as a module constant so the corpus loader and per-sid path agree.
_NO_COMPARE_AS_OF = "2024-01-01"


def _load_no_bench_ids() -> List[str]:
    """Norway ``base_id``s for the smoke corpus.

    NO has no committed bench CSV; the corpus is the set of amended, fully-replayable
    base acts the inventory scan surfaces, ordered by amendment volume (most-amended
    first) so the smoke slice hits the acts where witness rules fire most. Bounded to
    the top slice so ``--corpus-bench`` is a fast smoke, not the whole corpus.
    """
    from lawvm.norway.inventory import build_no_inventory
    from lawvm.norway.sources import resolve_no_source_path

    data_dir = resolve_no_source_path()
    inventory = build_no_inventory(data_dir)
    status_map = inventory.amended_executable_law_status_map()
    candidates = sorted(
        (
            base_id
            for base_id, status in status_map.items()
            if status == "fully_replayable"
        ),
        key=lambda base_id: (
            -len(inventory.base_to_sources.get(base_id, [])),
            base_id,
        ),
    )
    return candidates[:_NO_BENCH_LIMIT]


_NO_BENCH_LIMIT = 25


register_ledger_adapter(
    LedgerAdapter(
        jurisdiction="no",
        ledger_inputs=no_ledger_inputs,
        catalog=_NO_RULE_SPECS,
        corpus_loaders={"bench": _load_no_bench_ids},
    )
)
