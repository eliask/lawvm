"""Witness-attribution ledger — the spec-discovery loop, closed.

Reframing (see notes_internal/SPEC_DISCOVERY_DESIGN.md): LawVM compiles a hostile
source with a missing spec — amendment laws transform statutes by rules nobody wrote
down.  The bench % is a *proxy*; the deliverable is the *discovered spec*.  Every
transformation the compiler makes is a named, falsifiable hypothesis about that spec,
carried as a per-op witness rule id.

This module closes the loop the bench leaves open: it pushes oracle divergence back
down to the *rule* responsible, so an undifferentiated section-score becomes a ranked
table of *specific hypotheses about the law that the witness (the oracle) contradicts*.

Architecture: a **jurisdiction-neutral core** (``DivergenceRow`` ->
``StatuteLedgerInput`` -> ``build_ledger`` -> ``SpecLedger``) plus per-jurisdiction
**adapters** that turn a frontend's classification surface into neutral inputs.  Only
the adapter is jurisdiction-specific (classify entrypoint, diagnosis vocabulary, corpus
list, rule catalog); the witness-attribution and aggregation are shared.  This mirrors
``self_consistency`` ``-j fi/uk/ee`` dispatch.

"Oracle" is a *witness surface, not ground truth*: a divergence carries a disposition
(``lawvm_wrong`` vs ``oracle_suspect`` vs ``missing_source``) so we never refine a rule
to fit an oracle bug.

It is read-only and additive — no replay-path or grafter*.py changes.

Run:  uv run python -m lawvm.tools.spec_ledger 1958/370 [more sids ...]
      uv run python -m lawvm.tools.spec_ledger -j fi --corpus-bench --json ledger.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Literal, Mapping, Optional

# ---------------------------------------------------------------------------
# Jurisdiction-neutral core
# ---------------------------------------------------------------------------

Mode = Literal["official_consolidation", "legal_pit"]

# A divergence is not automatically "our bug" — the oracle is a witness that can itself
# be wrong/stale.  Adapters map their own diagnosis vocabulary onto these.
WitnessDisposition = Literal[
    "lawvm_wrong",     # replay produced the wrong thing; a real false hypothesis
    "oracle_suspect",  # the oracle is stale / applies an editorial convention we don't
    "missing_source",  # the source amendment text was incomplete/pathological
    "structural",      # section present/absent mismatch, owner not yet pinned
    "unknown",
]

# Dispositions that count as falsifying evidence against a rule.
_FALSIFYING = ("lawvm_wrong", "structural")


@dataclass(frozen=True)
class DivergenceRow:
    """One per-section divergence, already classified and (maybe) attributed."""

    sid: str
    section_key: str
    diagnosis: str               # the adapter's raw diagnosis label (kept for provenance)
    disposition: WitnessDisposition
    rule_id: Optional[str]       # witness rule that produced the section, if attributable
    blame_source: str = ""       # amendment blamed by the frontend, if any

    def exemplar(self) -> Dict[str, str]:
        return {
            "statute": self.sid,
            "section": self.section_key,
            "diagnosis": self.diagnosis,
            "disposition": self.disposition,
            "blame_source": self.blame_source,
        }


@dataclass(frozen=True)
class StatuteLedgerInput:
    """Neutral per-statute input: how often each rule fired + its divergences."""

    sid: str
    rule_firings: Mapping[str, int]      # witness_rule_id -> firing count
    divergences: List[DivergenceRow]


@dataclass
class RuleLedgerEntry:
    """Per-rule accumulation across the corpus run."""

    rule_id: str
    firings: int = 0
    by_disposition: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    exemplars: List[Dict[str, str]] = field(default_factory=list)
    believed_spec: str = ""

    @property
    def divergences(self) -> int:
        return sum(self.by_disposition.values())

    @property
    def contradicted(self) -> int:
        """Divergences that look like our bug — the falsifying evidence."""
        return sum(self.by_disposition.get(d, 0) for d in _FALSIFYING)

    @property
    def corroborated_est(self) -> int:
        """Firings not implicated in any divergence (derived estimate, not a count)."""
        return max(0, self.firings - self.divergences)

    def to_dict(self) -> Dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "believed_spec": self.believed_spec,
            "cataloged": bool(self.believed_spec),
            "firings": self.firings,
            "corroborated_est": self.corroborated_est,
            "contradicted": self.contradicted,
            "divergences": self.divergences,
            "by_disposition": dict(self.by_disposition),
            "exemplars": self.exemplars[:8],
        }


@dataclass
class SpecLedger:
    jurisdiction: str
    mode: str
    statutes: int = 0
    statute_errors: int = 0
    rules: Dict[str, RuleLedgerEntry] = field(default_factory=dict)
    # divergences with a real diagnosis but no attributable witness rule = blind spots
    unattributed: List[Dict[str, str]] = field(default_factory=list)

    def _rule(self, rule_id: str, catalog: Mapping[str, str]) -> RuleLedgerEntry:
        if rule_id not in self.rules:
            self.rules[rule_id] = RuleLedgerEntry(
                rule_id=rule_id, believed_spec=catalog.get(rule_id, "")
            )
        return self.rules[rule_id]

    def ranked_entries(self) -> List[RuleLedgerEntry]:
        # Rank by contradicted (falsifying evidence), then total divergences.
        return sorted(
            self.rules.values(),
            key=lambda e: (e.contradicted, e.divergences),
            reverse=True,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "jurisdiction": self.jurisdiction,
            "mode": self.mode,
            "statutes": self.statutes,
            "statute_errors": self.statute_errors,
            "n_rules": len(self.rules),
            "n_unattributed": len(self.unattributed),
            "rules": [e.to_dict() for e in self.ranked_entries()],
            "unattributed": self.unattributed[:40],
        }


def build_ledger(
    inputs: Iterable[StatuteLedgerInput],
    *,
    jurisdiction: str,
    mode: str,
    catalog: Mapping[str, str],
) -> SpecLedger:
    """Aggregate neutral per-statute inputs into a ranked witness-attribution ledger."""
    ledger = SpecLedger(jurisdiction=jurisdiction, mode=mode)
    for inp in inputs:
        ledger.statutes += 1
        for rule_id, count in inp.rule_firings.items():
            ledger._rule(rule_id, catalog).firings += count
        for div in inp.divergences:
            if div.rule_id:
                entry = ledger._rule(div.rule_id, catalog)
                entry.by_disposition[div.disposition] += 1
                if len(entry.exemplars) < 8 and div.disposition in _FALSIFYING:
                    entry.exemplars.append(div.exemplar())
            elif div.disposition in _FALSIFYING:
                # divergence with a real diagnosis but no named owner = Gap A blind spot
                ledger.unattributed.append(div.exemplar())
    return ledger


def render_markdown(ledger: SpecLedger) -> str:
    lines = [
        f"# Spec-discovery ledger (-j {ledger.jurisdiction}, {ledger.mode})",
        f"statutes={ledger.statutes} errors={ledger.statute_errors} "
        f"rules={len(ledger.rules)} unattributed_divergences={len(ledger.unattributed)}",
        "",
        "| rule_id | cat | firings | corrob~ | contradicted | dispositions |",
        "|---------|-----|---------|---------|--------------|--------------|",
    ]
    for e in ledger.ranked_entries():
        cat = "Y" if e.believed_spec else "·"
        disp = " ".join(f"{k}:{v}" for k, v in sorted(e.by_disposition.items()))
        lines.append(
            f"| {e.rule_id} | {cat} | {e.firings} | {e.corroborated_est} "
            f"| {e.contradicted} | {disp} |"
        )
    if ledger.unattributed:
        lines += ["", f"## Unattributed divergences (blind spots): {len(ledger.unattributed)}"]
        for u in ledger.unattributed[:20]:
            lines.append(
                f"- {u['statute']} {u['section']} [{u['diagnosis']}] blame={u['blame_source']}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Finland adapter
# ---------------------------------------------------------------------------

# Finland oracle-check ``diagnosis`` vocabulary -> witness disposition.  Diagnoses absent
# here fall back to "unknown" (loud, not silently bucketed as a pass).
_FI_DIAGNOSIS_DISPOSITION: Dict[str, WitnessDisposition] = {
    "UNKNOWN": "lawvm_wrong",
    "REPLAY_EXTRA": "lawvm_wrong",
    "REPLAY_MISSING": "lawvm_wrong",
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
    "fi.recovery.uncovered_kumotaan": "Uncovered kumotaan recovery applies a repeal named in operative text but not emitted as a parsed structural op.",
}


def fi_ledger_inputs(sids: List[str], mode: Mode) -> Iterator[StatuteLedgerInput]:
    """Turn Finland's ClassifyResult surface into neutral ledger inputs.

    firings come from ``compiled_ops[].witness_rule_id``; divergences come from
    ``section_results`` (per-section ``diagnosis``), attributed to the witness rule of
    the blame compiled-op (``_build_blame_map`` / ``_lookup_blame_op``).
    """
    from lawvm.tools.oracle_check import (
        _build_blame_map,
        _classify_statute_sync,
        _lookup_blame_op,
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
        blame_map = _build_blame_map(cr.compiled_ops)
        divergences: List[DivergenceRow] = []
        for sec in cr.section_results:
            diagnosis = str(sec.get("diagnosis") or "")
            if diagnosis in _FI_NON_DIVERGENCE:
                continue
            section_key = str(sec.get("section") or "")
            blame_op = _lookup_blame_op(blame_map, section_key)
            rid = blame_op.get("witness_rule_id") if isinstance(blame_op, dict) else None
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=section_key,
                    diagnosis=diagnosis,
                    disposition=_FI_DIAGNOSIS_DISPOSITION.get(diagnosis, "unknown"),
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


# ---------------------------------------------------------------------------
# Dispatch + CLI
# ---------------------------------------------------------------------------

def run_ledger(jurisdiction: str, sids: List[str], mode: Mode) -> SpecLedger:
    if jurisdiction != "fi":
        raise NotImplementedError(
            f"spec-ledger adapter for -j {jurisdiction} not implemented; "
            "fi is the first adapter (provide classify surface + diagnosis map + catalog)"
        )
    inputs = list(fi_ledger_inputs(sids, mode))
    ledger = build_ledger(
        inputs, jurisdiction="fi", mode=mode, catalog=_FI_RULE_SPECS
    )
    # The adapter drops un-classifiable statutes from the stream; reflect them honestly.
    ledger.statute_errors = len(sids) - ledger.statutes
    return ledger


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Witness-attribution spec-discovery ledger")
    ap.add_argument("sids", nargs="*", help="statute ids, e.g. 1958/370")
    ap.add_argument("-j", "--jurisdiction", default="fi", help="frontend adapter (fi)")
    ap.add_argument("--corpus-bench", action="store_true",
                    help="[-j fi] use data/finland/bench_core.csv")
    ap.add_argument("--mode", default="official_consolidation",
                    choices=["official_consolidation", "legal_pit"])
    ap.add_argument("--json", default="", help="write full ledger JSON to this path")
    args = ap.parse_args(argv)

    sids = list(args.sids)
    if args.corpus_bench:
        if args.jurisdiction != "fi":
            ap.error("--corpus-bench is only wired for -j fi")
        sids = _load_bench_core_ids()
    if not sids:
        ap.error("provide statute ids or --corpus-bench")

    ledger = run_ledger(args.jurisdiction, sids, args.mode)  # type: ignore[arg-type]
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(ledger.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.json}", file=sys.stderr)
    print(render_markdown(ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
