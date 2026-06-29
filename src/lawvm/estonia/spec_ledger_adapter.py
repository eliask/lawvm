"""Estonia (EE) adapter for the witness-attribution spec-discovery ledger.

This is the EE sibling of :mod:`lawvm.tools.spec_ledger`'s jurisdiction-neutral core,
and of the FI / UK / US / NZ adapters. It reuses that core read-only (``DivergenceRow``
-> ``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) and turns Estonia's
replay/consistency surface into neutral ledger inputs.

It self-registers into the core's adapter registry at import time (see
:func:`lawvm.tools.spec_ledger.register_ledger_adapter`) so ``run_ledger("ee", ...)`` and
the ``-j ee`` CLI dispatch through the registry without the core importing this package.

Run:  uv run python -m lawvm.tools.spec_ledger -j ee --corpus-bench --json ledger.json
      uv run python -m lawvm.tools.spec_ledger -j ee --corpus-full
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterator, List, Mapping, Optional

from lawvm.core.named_swallow import swallow_call
from lawvm.core.phase_result import Finding
from lawvm.tools.spec_ledger import (
    DivergenceRow,
    LedgerAdapter,
    Mode,
    StatuteLedgerInput,
    WitnessDisposition,
    disposition_for,
    register_ledger_adapter,
)

# EE is NOT an oracle-*check* like FI/UK: it replays as a *consistency verification*
# against the official consolidated text (Riigi Teataja terviktekst).  That text is
# law-in-force — but legal force is NOT consolidation-correctness: consolidating the
# amendment acts into a running text is an editorial act that can mis-render them, and
# a wrong terviktekst stays in force until corrected.  So even here LawVM replaying the
# primary amendment acts can be RIGHT while the in-force consolidation is WRONG.
# ``oracle_suspect`` is therefore a first-class outcome and a high-value finding (the
# adoption wedge, AGENTS.md §2.1/§3) — NOT a rare escape hatch, and the authoritative
# oracle is never presumed correct.
#
# The raw-divergence default below is ``lawvm_wrong`` only as the conservative, *humble*
# discovery stance — "suspect our own rule first" — never as deference to the
# consolidation's correctness: over-attributing a divergence to ourselves is the safe
# direction; presuming the in-force text correct is the dangerous one.  An adjudicated
# residual bucket flips it to ``oracle_suspect`` (consolidation drift / correction
# notice) or ``missing_source`` (the amendment source itself is incomplete).
#
# Two layers map onto the neutral WitnessDisposition:
#   1. residual bucket (preferred, when the address is adjudicated), and
#   2. raw consistency divergence_type (provisional default, no residual record).
# Anything unmapped in either layer falls to "unknown" (loud, never a silent pass).
_EE_DIAGNOSIS_DISPOSITION: Dict[str, WitnessDisposition] = {
    # -- raw consistency divergence_type (no adjudicated residual record) --
    "MISMATCH": "lawvm_wrong",
    "OPS_MISSING": "lawvm_wrong",          # in oracle but not replay
    "CONSOLIDATED_MISSING": "lawvm_wrong",  # in replay but not oracle
    # -- adjudicated residual buckets --
    "replay_bug": "lawvm_wrong",
    "source_oracle_drift": "oracle_suspect",
    "oracle_correction_notice": "oracle_suspect",
    "source_pathology": "missing_source",
    "source_ambiguity": "missing_source",
    "appendix_display_pathology": "structural",
    "descendant_residual_mix": "unknown",
    "presentation_punctuation_whitespace": "unknown",
}


# Believed-spec catalog authored by a sibling agent in
# ``lawvm.tools.spec_ledger_ee_catalog``; import if present, else fall back to {}.
def _load_ee_rule_specs() -> Dict[str, str]:
    try:
        from lawvm.tools.spec_ledger_ee_catalog import _EE_RULE_SPECS
    except ImportError:
        return {}
    return dict(_EE_RULE_SPECS)


_EE_RULE_SPECS: Dict[str, str] = _load_ee_rule_specs()


def _ee_address_key(address: object) -> str:
    """Render a StructuredAddress ``.path`` as the residual-inventory key form.

    Mirrors ``ConsistencyDivergence.__str__`` and the residual-inventory keys
    (e.g. ``"section:5/subsection:2"``).
    """
    path = getattr(address, "path", None)
    if not path:
        return ""
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _ee_resolve_as_of(
    oracle_id: str,
    archive: object,
    *,
    findings_out: Optional[List[Finding]] = None,
) -> str:
    """Resolve an oracle terviktekst's own effective date (its PIT as_of).

    ``fetch_rt_xml`` may raise across network/parse/storage errors. Previously
    ``except Exception: return ""`` silently swallowed to an empty string
    (AGENTS.md §1.10 silent-fallback). Now routed through
    ``lawvm.core.named_swallow.swallow_call`` so a typed Finding is constructed
    carrying the offending ``oracle_id`` as ``source_artifact`` and
    ``clause_text`` (the EE URL path, truncated 400 chars). When the caller
    plumbs ``findings_out`` the Finding is appended there (per-statute
    audit-trail sink, threaded from the caller); otherwise ``log_emitter``
    keeps stderr WARNING visibility — never silent. On swallow returns "" so
    the spec-ledger adapter continues to mark this oracle's as-of unresolved
    rather than aborting a full corpus scan.
    """
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml

    from lawvm.core.named_swallow import log_emitter

    # Sink dispatch mirrors corpus.py:122 named_swallow precedent: when the
    # caller plumbed ``findings_out``, the Finding lands in that audit-trail
    # list; when not, ``log_emitter`` keeps stderr WARNING visibility so the
    # swallow is still observed at this acquisition boundary.
    emit = None if findings_out is not None else log_emitter()
    xml_bytes = swallow_call(
        lambda: fetch_rt_xml(oracle_id, archive),
        rule_id="ee_spec_ledger_fetch_rt_xml",
        default=b"",
        jurisdiction="ee",
        source_artifact=oracle_id,
        clause_text=f"oracle_id={oracle_id[:400]}",
        emit=emit,
        findings_out=findings_out,
    )
    if xml_bytes == b"":
        return ""
    return extract_effective_date(xml_bytes)


def ee_ledger_inputs(sids: List[str], mode: Mode) -> Iterator[StatuteLedgerInput]:
    """Turn Estonia's replay/consistency surface into neutral ledger inputs.

    Each ``sid`` is the ``"<base_id>/<oracle_id>"`` pair form.  firings come from
    ``op.witness_rule_id`` over the compiled ops; divergences come from the
    consistency report (``replay_ee_to_pit(...).divergences``), each refined by the
    adjudicated residual bucket where one exists.  A divergence is attributed to the
    *earliest* op whose target address contains (is a suffix-or-equal of) the
    divergence address; no such op => an unattributed blind spot.  ``blame_source`` is
    left empty because the EE oracle is authoritative, not a blamed amendment surface.
    """
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit
    from lawvm.estonia.residual_reporting import build_ee_residual_summary

    archive = open_rt_archive()
    for sid in sids:
        base_id, _, oracle_id = sid.partition("/")
        if not base_id or not oracle_id:
            continue
        as_of = _ee_resolve_as_of(oracle_id, archive)
        if not as_of:
            continue
        result = replay_ee_to_pit(base_id, as_of, archive=archive, oracle_id=oracle_id)
        if result.error:
            continue

        firings: Dict[str, int] = defaultdict(int)
        for op in result.compiled_ops:
            rid = getattr(op, "witness_rule_id", "") or ""
            if rid:
                firings[rid] += 1

        # Pre-index ops by their target address key, keeping the earliest sequence.
        op_owner: Dict[str, object] = {}
        for op in sorted(result.compiled_ops, key=lambda o: getattr(o, "sequence", 0)):
            key = _ee_address_key(getattr(op, "target", None))
            if key and key not in op_owner:
                op_owner[key] = op

        divergence_addresses = [_ee_address_key(d.address) for d in result.divergences]
        summary = build_ee_residual_summary(
            base_id, oracle_id, divergence_addresses=divergence_addresses
        )
        record_by_address = dict(summary.record_by_address) if summary is not None else {}

        divergences: List[DivergenceRow] = []
        for div in result.divergences:
            addr_key = _ee_address_key(div.address)
            record = record_by_address.get(addr_key)
            if record is not None:
                diagnosis = record.bucket
            else:
                diagnosis = div.divergence_type
            rid = _ee_attribute_divergence(addr_key, op_owner)
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=addr_key,
                    diagnosis=diagnosis,
                    disposition=disposition_for(diagnosis, _EE_DIAGNOSIS_DISPOSITION),
                    rule_id=rid,
                    blame_source="",
                )
            )
        yield StatuteLedgerInput(sid=sid, rule_firings=dict(firings), divergences=divergences)


def _ee_attribute_divergence(addr_key: str, op_owner: Mapping[str, object]) -> Optional[str]:
    """Find the witness rule of the op that owns ``addr_key`` (suffix-or-equal match).

    Prefers the exact-address owner; otherwise the longest owning prefix-chain match
    (an op whose target is an ancestor of, or equal to, the divergence address).
    ``op_owner`` is pre-sorted to the earliest op per address.  Returns None when no op
    owns the address — a blind spot.
    """
    if not addr_key:
        return None
    if addr_key in op_owner:
        op = op_owner[addr_key]
        return (getattr(op, "witness_rule_id", "") or None)
    # Ancestor match: the longest op-target key that ``addr_key`` extends.
    best_key = ""
    for key in op_owner:
        if addr_key == key or addr_key.startswith(f"{key}/"):
            if len(key) > len(best_key):
                best_key = key
    if best_key:
        op = op_owner[best_key]
        return (getattr(op, "witness_rule_id", "") or None)
    return None


def _load_ee_corpus_pairs(fuller: bool = False) -> List[str]:
    """Estonia ``<base_id>/<oracle_id>`` pairs from data/estonia/.

    Default smoke uses ``bench_corpus.csv`` (the EE analog of FI's bench_core); the
    fuller replayable list is ``current_replayable_corpus.csv``.  Both are CSVs with
    ``base_id`` and ``oracle_id`` columns.
    """
    import csv
    from pathlib import Path

    base = Path(__file__).resolve().parents[3] / "data" / "estonia"
    path = base / ("current_replayable_corpus.csv" if fuller else "bench_corpus.csv")
    pairs: List[str] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            base_id = (row.get("base_id") or "").strip()
            oracle_id = (row.get("oracle_id") or "").strip()
            if base_id and oracle_id:
                pairs.append(f"{base_id}/{oracle_id}")
    return pairs


register_ledger_adapter(
    LedgerAdapter(
        jurisdiction="ee",
        ledger_inputs=ee_ledger_inputs,
        catalog=_EE_RULE_SPECS,
        corpus_loaders={
            "bench": lambda: _load_ee_corpus_pairs(fuller=False),
            "full": lambda: _load_ee_corpus_pairs(fuller=True),
        },
    )
)
