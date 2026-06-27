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

Each adapter lives in its OWN jurisdiction package
(``lawvm.finland.spec_ledger_adapter``, ``lawvm.uk_legislation.spec_ledger_adapter``,
``lawvm.estonia.spec_ledger_adapter``, and the US/NZ adapters) and *self-registers* into
the :class:`LedgerAdapterRegistry` at import time via :func:`register_ledger_adapter` —
mirroring how ``core/bench_comparator_registry`` registers per-jurisdiction comparators.
The core never imports a jurisdiction package itself; ``run_ledger`` loads the adapter
module on demand (which triggers its self-registration) and dispatches through the
registry.

"Oracle" is a *witness surface, not ground truth*: a divergence carries a disposition
(``lawvm_wrong`` vs ``oracle_suspect`` vs ``missing_source``) so we never refine a rule
to fit an oracle bug.

It is read-only and additive — no replay-path or grafter*.py changes.

Run:  uv run python -m lawvm.tools.spec_ledger 1958/370 [more sids ...]
      uv run python -m lawvm.tools.spec_ledger -j fi --corpus-bench --json ledger.json
      uv run python -m lawvm.tools.spec_ledger -j uk asp/2000/1 [more sids ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Literal, Mapping, Optional

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


def disposition_for(
    raw: str, mapping: Mapping[str, WitnessDisposition]
) -> WitnessDisposition:
    """Map an adapter's raw diagnosis ``raw`` onto a neutral witness disposition.

    The one shared rule every jurisdiction adapter uses: a known diagnosis resolves
    through its own ``mapping``; anything unmapped falls to ``"unknown"`` — *loud*, never
    silently bucketed as a pass.  This is the single jurisdiction-neutral home of the
    ``map.get(raw, "unknown")`` one-liner the FI/UK/EE/US/NZ adapters all duplicated.
    """
    return mapping.get(raw, "unknown")


@dataclass(frozen=True)
class DivergenceRow:
    """One per-section divergence, already classified and (maybe) attributed."""

    sid: str
    section_key: str
    diagnosis: str               # the adapter's raw diagnosis label (kept for provenance)
    disposition: WitnessDisposition
    rule_id: Optional[str]       # witness rule that produced the section, if attributable
    blame_source: str = ""       # amendment blamed by the frontend, if any
    # Optional per-frontend attribution facets. Default "" keeps the FI adapter
    # (which does not set them) byte-for-byte unaffected. The UK adapter uses
    # phase_owner to bucket blind-spots by the owning compiler phase and
    # authority_layer by source purity.
    phase_owner: str = ""
    authority_layer: str = ""

    def exemplar(self) -> Dict[str, str]:
        ex = {
            "statute": self.sid,
            "section": self.section_key,
            "diagnosis": self.diagnosis,
            "disposition": self.disposition,
            "blame_source": self.blame_source,
        }
        if self.phase_owner:
            ex["phase_owner"] = self.phase_owner
        if self.authority_layer:
            ex["authority_layer"] = self.authority_layer
        return ex


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
    # per-statute count of falsifying (real-bug-suspect) divergences = efficient
    # mining targets: statutes where real bugs concentrate, vs the diffuse per-rule view
    statute_real_bugs: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

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
            "top_statutes": sorted(
                self.statute_real_bugs.items(), key=lambda kv: kv[1], reverse=True
            )[:30],
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
            if div.disposition in _FALSIFYING:
                ledger.statute_real_bugs[div.sid] += 1
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
# Per-jurisdiction adapter registry
# ---------------------------------------------------------------------------
#
# The adapter is the ONLY jurisdiction-specific part: a ``ledger_inputs`` callable
# (frontend classification surface -> neutral ``StatuteLedgerInput``s), the rule
# ``catalog`` (believed_spec per witness rule id), and optional corpus loaders for the
# ``--corpus-bench`` / ``--corpus-full`` CLI flags.  Each adapter lives in its own
# jurisdiction package and self-registers here at import time (see
# ``lawvm.finland.spec_ledger_adapter`` etc.), mirroring
# ``core/bench_comparator_registry``.  The core never imports a jurisdiction package;
# ``run_ledger`` loads the adapter module on demand, which triggers registration.

LedgerInputsFn = Callable[[List[str], "Mode"], Iterable[StatuteLedgerInput]]


@dataclass(frozen=True)
class LedgerAdapter:
    """One jurisdiction's spec-ledger adapter: how to turn its frontend surface into
    neutral ledger inputs, plus its rule catalog and corpus loaders."""

    jurisdiction: str
    ledger_inputs: LedgerInputsFn
    catalog: Mapping[str, str]
    # CLI corpus loaders keyed by flag name ("bench" -> --corpus-bench,
    # "full" -> --corpus-full); a jurisdiction wires only the flags it supports.
    corpus_loaders: Mapping[str, Callable[[], List[str]]] = field(default_factory=dict)


_LEDGER_ADAPTERS: Dict[str, LedgerAdapter] = {}

# Jurisdiction -> the module that, when imported, self-registers that adapter.  The core
# loads these lazily (never at import time) so it carries no compile-time dependency on a
# jurisdiction package.  US/NZ are standalone (their own CLIs) and are not dispatched
# through ``run_ledger``; they are intentionally absent.
_ADAPTER_MODULES: Dict[str, str] = {
    "fi": "lawvm.finland.spec_ledger_adapter",
    "uk": "lawvm.uk_legislation.spec_ledger_adapter",
    "ee": "lawvm.estonia.spec_ledger_adapter",
}


def register_ledger_adapter(adapter: LedgerAdapter) -> None:
    """Register *adapter* under its ``jurisdiction`` key.

    Registering twice for the same key overwrites — a jurisdiction owns its key.
    """
    if not adapter.jurisdiction:
        raise ValueError("ledger adapter jurisdiction key must be non-empty")
    _LEDGER_ADAPTERS[adapter.jurisdiction] = adapter


def get_ledger_adapter(jurisdiction: str) -> LedgerAdapter:
    """Return the registered adapter for *jurisdiction*, importing its module on demand.

    Raises :class:`NotImplementedError` (fail loud) when the jurisdiction has no adapter,
    rather than silently producing an empty ledger.
    """
    if jurisdiction not in _LEDGER_ADAPTERS:
        module = _ADAPTER_MODULES.get(jurisdiction)
        if module is not None:
            import importlib

            importlib.import_module(module)  # triggers self-registration
    try:
        return _LEDGER_ADAPTERS[jurisdiction]
    except KeyError:
        raise NotImplementedError(
            f"spec-ledger adapter for -j {jurisdiction} not implemented; "
            f"{sorted(_ADAPTER_MODULES)} are the dispatchable adapters (provide a "
            "classify surface + diagnosis map + catalog and self-register via "
            "register_ledger_adapter)"
        ) from None


# ---------------------------------------------------------------------------
# Dispatch + CLI
# ---------------------------------------------------------------------------

def run_ledger(jurisdiction: str, sids: List[str], mode: Mode) -> SpecLedger:
    adapter = get_ledger_adapter(jurisdiction)
    inputs = list(adapter.ledger_inputs(sids, mode))
    ledger = build_ledger(
        inputs, jurisdiction=jurisdiction, mode=mode, catalog=adapter.catalog
    )
    # The adapter drops un-classifiable statutes from the stream; reflect them honestly.
    ledger.statute_errors = len(sids) - ledger.statutes
    return ledger


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Witness-attribution spec-discovery ledger")
    ap.add_argument("sids", nargs="*", help="statute ids, e.g. 1958/370")
    ap.add_argument("-j", "--jurisdiction", default="fi", help="frontend adapter (fi/uk/ee)")
    ap.add_argument("--corpus-bench", action="store_true",
                    help="[-j fi] data/finland/bench_core.csv  "
                         "[-j uk] data/uk/bench_corpus_smoke.csv  "
                         "[-j ee] data/estonia/bench_corpus.csv")
    ap.add_argument("--corpus-full", action="store_true",
                    help="[-j ee] data/estonia/current_replayable_corpus.csv")
    ap.add_argument("--mode", default="official_consolidation",
                    choices=["official_consolidation", "legal_pit"])
    ap.add_argument("--json", default="", help="write full ledger JSON to this path")
    args = ap.parse_args(argv)

    sids = list(args.sids)
    if args.corpus_bench or args.corpus_full:
        adapter = get_ledger_adapter(args.jurisdiction)
        if args.corpus_bench:
            loader = adapter.corpus_loaders.get("bench")
            if loader is None:
                ap.error(
                    "--corpus-bench is only wired for -j "
                    f"{sorted(_ADAPTER_MODULES)}"
                )
            else:
                sids = loader()
        if args.corpus_full:
            loader = adapter.corpus_loaders.get("full")
            if loader is None:
                ap.error("--corpus-full is only wired for -j ee")
            else:
                sids = loader()
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
