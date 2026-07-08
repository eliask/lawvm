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
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Literal, Mapping, Optional, Set, Tuple

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

# ---------------------------------------------------------------------------
# Frontier ranking:  score = B × S × EIG   (FABLE_SPEC_RECONSTRUCTION §8(7))
# ---------------------------------------------------------------------------
# The pre-frontier ledger ranked witness rules by *raw firing count* (or, in
# ``ranked_entries``, by contradicted-count), which over-weights high-frequency
# benign rules and buries rare rules with wide blast radius or a suspicious
# agree/disagree history. Fable's design replaces that with an active-learning
# frontier score, the product of three independent factors:
#
#   B  blast radius   — how much of the corpus a rule's firing touches. A rare rule
#                       that reaches many statutes should outrank a frequent one
#                       confined to a single statute. We have no per-rule *provision*
#                       fan-out in the ledger (that would need a replay-path change,
#                       out of scope), so the proxy is the count of DISTINCT STATUTES
#                       the rule fired in (``affected_statutes``), log-damped so raw
#                       repetition inside one statute cannot dominate a rule that
#                       spans the corpus:  B = 1 + ln(1 + distinct_statutes).
#
#   S  suspicion      — the Beta-Bernoulli posterior mean that the rule is DEFECTIVE,
#                       from its falsifying-vs-corroborating history. Each firing is a
#                       Bernoulli trial: a falsifying (``contradicted``) divergence is a
#                       "defect" success; a firing not implicated in any divergence
#                       (``corroborated_est``) is a "clean" failure. With a uniform
#                       Beta(1, 1) (Laplace) prior — documented, symmetric, adds one
#                       pseudo-observation of each outcome so a never-fired or
#                       all-agree rule sits at 0.5 → shrinks toward 0 with evidence —
#                       the posterior mean is
#                           S = (contradicted + α) / (contradicted + corroborated + α + β).
#                       A rule that fires 1000× and always agrees → S≈0; one that fires
#                       10× and disagrees 6× → S≈0.58.
#
#   EIG expected info  — how much adjudicating this rule's next firing would reduce our
#       gain           uncertainty about S. This is the posterior VARIANCE of the same
#                       Beta, Var = αβ / ((α+β)²(α+β+1)); it is maximal near S≈0.5 with a
#                       LOW sample count (the classic active-learning frontier) and
#                       collapses as evidence piles up on either side. Rules we are
#                       already confident about (very clean or very broken) yield little
#                       information from another look and sink.
#
# The product means a rule must be non-trivial on ALL THREE axes to top the queue:
# wide-reaching AND plausibly-defective AND still-uncertain. Deterministic (pure
# arithmetic over integer counts); raw firing count is retained as a secondary key and
# emitted in the output for side-by-side comparison with the legacy rank.
_SUSPICION_PRIOR_ALPHA = 1.0  # Beta prior: pseudo-count of "defect" outcomes
_SUSPICION_PRIOR_BETA = 1.0   # Beta prior: pseudo-count of "clean"  outcomes


def _beta_posterior(contradicted: int, corroborated: int) -> Tuple[float, float]:
    """Return the Beta(α, β) posterior parameters for a rule's defect rate.

    ``contradicted`` (falsifying divergences) are "defect" successes; ``corroborated``
    (firings not implicated in any divergence) are "clean" failures. The uniform
    Beta(1, 1) prior is added so a rule with no evidence sits at the maximally-uncertain
    p=0.5 rather than an undefined 0/0.
    """
    alpha = _SUSPICION_PRIOR_ALPHA + max(0, contradicted)
    beta = _SUSPICION_PRIOR_BETA + max(0, corroborated)
    return alpha, beta


def blast_radius(distinct_statutes: int) -> float:
    """B: log-damped count of distinct statutes a rule's firing reaches (≥ 1.0)."""
    return 1.0 + math.log1p(max(0, distinct_statutes))


def suspicion(contradicted: int, corroborated: int) -> float:
    """S: Beta-Bernoulli posterior mean that the rule is defective (in (0, 1))."""
    alpha, beta = _beta_posterior(contradicted, corroborated)
    return alpha / (alpha + beta)


def expected_information_gain(contradicted: int, corroborated: int) -> float:
    """EIG: posterior variance of the defect-rate Beta — the active-learning frontier.

    Maximal near S≈0.5 with a low sample count; → 0 as evidence accumulates either way.
    """
    alpha, beta = _beta_posterior(contradicted, corroborated)
    total = alpha + beta
    return (alpha * beta) / (total * total * (total + 1.0))


def frontier_score(
    distinct_statutes: int, contradicted: int, corroborated: int
) -> float:
    """The B × S × EIG frontier score used to rank witness rules (deterministic)."""
    return (
        blast_radius(distinct_statutes)
        * suspicion(contradicted, corroborated)
        * expected_information_gain(contradicted, corroborated)
    )

# §3.5 of notes_internal/FABLE_SPEC_RECONSTRUCTION.md: every catalogued rule is one of
# two sorts, and conflating them is the main way a published "reconstructed spec" smuggles
# in implementation detail.
#   S = a hypothesis ABOUT THE LAW (the drafting language's semantics) — belongs in the
#       final published spec (the S-rule subset ∪ the negative spec);
#   P = a policy of THIS COMPILER for surviving its own coverage gaps (recovery /
#       tolerance / fallback heuristics). Not a statement about FI/UK/EU law at all.
# P-rule firing DENSITY is the heatmap of undiscovered spec (SPEC_DISCOVERY_DESIGN Gap A):
# where P-rules fire a lot, the real S-spec is still unknown, and the trajectory metric
# that matters is P-rule firing share -> 0 as S-rules absorb their territory.
RuleRole = Literal["S", "P"]

# When a catalogued rule carries no explicit role, it is treated as ``"S"`` (a
# law-hypothesis): the conservative default keeps an un-annotated legacy catalog reading
# as spec, and the coverage guard (test) is what forces new rules to declare a role
# rather than silently defaulting.
_DEFAULT_RULE_ROLE: RuleRole = "S"

# Shared read-only empty sidecars: an adapter that supplies neither a role map nor a
# falsifier map gets byte-identical behaviour to the pre-enrichment ledger.
_EMPTY_ROLES: Mapping[str, RuleRole] = {}
_EMPTY_FALSIFIERS: Mapping[str, str] = {}


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
    # Distinct statutes this rule fired in — the blast-radius proxy for the frontier
    # score (§8(7)). Read-only, populated by ``build_ledger``; a rule that fires once in
    # each of 50 statutes has blast radius 50 even though its firing count is 50, whereas
    # a rule firing 50× inside one statute has blast radius 1.
    affected_statutes: Set[str] = field(default_factory=set)
    believed_spec: str = ""
    # §3.5 S/P one-bit annotation and §3.2(4) named falsifier. Both are read-only
    # catalog metadata (populated by ``build_ledger`` from the adapter's role/falsifier
    # sidecars); they never touch replay. ``rule_role`` defaults to ``"S"`` when the
    # catalog does not annotate the rule (see ``_DEFAULT_RULE_ROLE``).
    rule_role: RuleRole = _DEFAULT_RULE_ROLE
    falsifier: str = ""

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

    @property
    def is_p_rule(self) -> bool:
        """A compiler-survival policy (P), not a hypothesis about the law (S)."""
        return self.rule_role == "P"

    @property
    def blast_radius(self) -> int:
        """B (raw): distinct statutes this rule's firing reached."""
        return len(self.affected_statutes)

    @property
    def suspicion(self) -> float:
        """S: Beta-Bernoulli posterior mean that the rule is defective."""
        return suspicion(self.contradicted, self.corroborated_est)

    @property
    def expected_information_gain(self) -> float:
        """EIG: posterior variance — how much the next adjudication would inform S."""
        return expected_information_gain(self.contradicted, self.corroborated_est)

    @property
    def frontier_score(self) -> float:
        """The B × S × EIG frontier score used as the primary ranking key (§8(7))."""
        return frontier_score(
            self.blast_radius, self.contradicted, self.corroborated_est
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "believed_spec": self.believed_spec,
            "cataloged": bool(self.believed_spec),
            "rule_role": self.rule_role,
            "falsifier": self.falsifier,
            "firings": self.firings,
            "corroborated_est": self.corroborated_est,
            "contradicted": self.contradicted,
            "divergences": self.divergences,
            # Frontier score (§8(7)) and its three factors, alongside firings so the
            # legacy firing-count rank stays visible for side-by-side comparison.
            "blast_radius": self.blast_radius,
            "suspicion": round(self.suspicion, 6),
            "expected_information_gain": round(self.expected_information_gain, 6),
            "frontier_score": round(self.frontier_score, 9),
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

    def _rule(
        self,
        rule_id: str,
        catalog: Mapping[str, str],
        roles: Mapping[str, RuleRole] = _EMPTY_ROLES,
        falsifiers: Mapping[str, str] = _EMPTY_FALSIFIERS,
    ) -> RuleLedgerEntry:
        if rule_id not in self.rules:
            self.rules[rule_id] = RuleLedgerEntry(
                rule_id=rule_id,
                believed_spec=catalog.get(rule_id, ""),
                rule_role=roles.get(rule_id, _DEFAULT_RULE_ROLE),
                falsifier=falsifiers.get(rule_id, ""),
            )
        return self.rules[rule_id]

    def ranked_entries(self) -> List[RuleLedgerEntry]:
        """Rank witness rules by the B × S × EIG frontier score (§8(7)).

        Primary key is the frontier score (blast-radius × suspicion × expected
        information gain — see ``frontier_score``), so a rare rule with wide blast radius
        or a suspicious agree/disagree history rises and a frequent-benign rule falls.
        Ties (e.g. every clean rule scores ~identically once EIG dominates) fall back to
        the legacy signals — contradicted count, then total divergences, then raw firing
        count — with rule_id as a final deterministic tie-break.
        """
        # Two-stage stable sort: first by rule_id ascending (the final tie-break), then
        # by the descending numeric keys. Python's sort is stable, so equal-numeric rows
        # retain the ascending-id order — fully deterministic without negating a string.
        by_id = sorted(self.rules.values(), key=lambda e: e.rule_id)
        return sorted(
            by_id,
            key=lambda e: (
                e.frontier_score,
                e.contradicted,
                e.divergences,
                e.firings,
            ),
            reverse=True,
        )

    def p_rule_density(self) -> List[RuleLedgerEntry]:
        """P-rules ranked by firing count — the undiscovered-spec heatmap (§3.5).

        Where compiler-survival policies fire a lot, the real (S) spec of the drafting
        language is still unknown there. Ranked firings-desc, then rule_id for a stable
        tie-break, so the top rows are the hottest gaps in spec coverage.
        """
        return sorted(
            (e for e in self.rules.values() if e.is_p_rule),
            key=lambda e: (-e.firings, e.rule_id),
        )

    def role_counts(self) -> Dict[str, int]:
        """S vs P catalogued-rule counts (only rules carrying a believed_spec).

        Uncatalogued ("·") rules have no stated hypothesis, so they are neither S nor P
        yet — they are pre-falsifiable and counted separately as ``uncataloged``.
        """
        counts = {"S": 0, "P": 0, "uncataloged": 0}
        for e in self.rules.values():
            if not e.believed_spec:
                counts["uncataloged"] += 1
            else:
                counts[e.rule_role] += 1
        return counts

    def to_dict(self) -> Dict[str, object]:
        return {
            "jurisdiction": self.jurisdiction,
            "mode": self.mode,
            "statutes": self.statutes,
            "statute_errors": self.statute_errors,
            "n_rules": len(self.rules),
            "n_unattributed": len(self.unattributed),
            "role_counts": self.role_counts(),
            "p_rule_density": [
                {
                    "rule_id": e.rule_id,
                    "firings": e.firings,
                    "contradicted": e.contradicted,
                }
                for e in self.p_rule_density()
            ],
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
    roles: Mapping[str, RuleRole] = _EMPTY_ROLES,
    falsifiers: Mapping[str, str] = _EMPTY_FALSIFIERS,
) -> SpecLedger:
    """Aggregate neutral per-statute inputs into a ranked witness-attribution ledger.

    ``catalog`` maps rule_id -> believed_spec (unchanged). ``roles`` and ``falsifiers``
    are OPTIONAL read-only sidecars (rule_id -> ``"S"|"P"`` and rule_id -> falsifier
    sentence): supplying neither reproduces the pre-enrichment ledger byte-for-byte.
    They annotate the S/P sort and the Popper-falsifier per §3.5 / §3.2(4) of
    ``notes_internal/FABLE_SPEC_RECONSTRUCTION.md`` without any replay/apply change.
    """
    ledger = SpecLedger(jurisdiction=jurisdiction, mode=mode)
    for inp in inputs:
        ledger.statutes += 1
        for rule_id, count in inp.rule_firings.items():
            entry = ledger._rule(rule_id, catalog, roles, falsifiers)
            entry.firings += count
            # Blast-radius proxy: a rule fires *in* this statute (count>0) → it reaches
            # one more distinct statute. Zero-count entries do not widen blast radius.
            if count:
                entry.affected_statutes.add(inp.sid)
        for div in inp.divergences:
            if div.disposition in _FALSIFYING:
                ledger.statute_real_bugs[div.sid] += 1
            if div.rule_id:
                entry = ledger._rule(div.rule_id, catalog, roles, falsifiers)
                entry.by_disposition[div.disposition] += 1
                if len(entry.exemplars) < 8 and div.disposition in _FALSIFYING:
                    entry.exemplars.append(div.exemplar())
            elif div.disposition in _FALSIFYING:
                # divergence with a real diagnosis but no named owner = Gap A blind spot
                ledger.unattributed.append(div.exemplar())
    return ledger


def render_markdown(ledger: SpecLedger) -> str:
    rc = ledger.role_counts()
    lines = [
        f"# Spec-discovery ledger (-j {ledger.jurisdiction}, {ledger.mode})",
        f"statutes={ledger.statutes} errors={ledger.statute_errors} "
        f"rules={len(ledger.rules)} unattributed_divergences={len(ledger.unattributed)}",
        f"catalogued S-rules (law-hypotheses)={rc['S']} "
        f"P-rules (compiler-survival policy)={rc['P']} "
        f"uncataloged={rc['uncataloged']}",
        "",
        "Rank key: B × S × EIG frontier score (§8(7)); firings kept for comparison.",
        "B=blast radius (distinct statutes), S=suspicion (Beta posterior mean), "
        "EIG=expected info gain (posterior variance).",
        "",
        "| rule_id | cat | S/P | score | B | S | EIG | firings | corrob~ | "
        "contradicted | dispositions |",
        "|---------|-----|-----|-------|---|---|-----|---------|---------|"
        "--------------|--------------|",
    ]
    for e in ledger.ranked_entries():
        cat = "Y" if e.believed_spec else "·"
        # An uncatalogued row has no stated hypothesis, so its S/P sort is undefined ("·").
        sort = e.rule_role if e.believed_spec else "·"
        disp = " ".join(f"{k}:{v}" for k, v in sorted(e.by_disposition.items()))
        lines.append(
            f"| {e.rule_id} | {cat} | {sort} | {e.frontier_score:.4g} "
            f"| {e.blast_radius} | {e.suspicion:.3f} | {e.expected_information_gain:.4g} "
            f"| {e.firings} | {e.corroborated_est} | {e.contradicted} | {disp} |"
        )
    density = ledger.p_rule_density()
    if density:
        lines += [
            "",
            "## P-rule firing density — the undiscovered-spec heatmap",
            "P-rules are compiler-survival policies, not law-hypotheses; where they fire "
            "a lot, the real (S) spec of the drafting language is still unknown.",
            "",
            "| rule_id | firings | contradicted | believed_spec |",
            "|---------|---------|--------------|---------------|",
        ]
        for e in density:
            spec = e.believed_spec.replace("|", "\\|")
            lines.append(
                f"| {e.rule_id} | {e.firings} | {e.contradicted} | {spec} |"
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
    # Optional read-only spec-metadata sidecars (§3.5 S/P sort, §3.2(4) falsifier).
    # Default-empty so an adapter that wires neither behaves exactly as before.
    roles: Mapping[str, "RuleRole"] = field(default_factory=dict)
    falsifiers: Mapping[str, str] = field(default_factory=dict)


_LEDGER_ADAPTERS: Dict[str, LedgerAdapter] = {}

# Jurisdiction -> the module that, when imported, self-registers that adapter.  The core
# loads these lazily (never at import time) so it carries no compile-time dependency on a
# jurisdiction package.  Standalone jurisdiction CLIs may still exist; this table is the
# shared cross-jurisdiction spine used by ``run_ledger`` / ``lawvm spec-ledger``.
_ADAPTER_MODULES: Dict[str, str] = {
    "fi": "lawvm.finland.spec_ledger_adapter",
    "uk": "lawvm.uk_legislation.spec_ledger_adapter",
    "ee": "lawvm.estonia.spec_ledger_adapter",
    "no": "lawvm.norway.spec_ledger_adapter",
    "se": "lawvm.sweden.spec_ledger_adapter",
    "eu": "lawvm.eu.spec_ledger_adapter",
    "us": "lawvm.us_federal.spec_ledger_adapter",
    "nz": "lawvm.new_zealand.spec_ledger_adapter",
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
        inputs,
        jurisdiction=jurisdiction,
        mode=mode,
        catalog=adapter.catalog,
        roles=adapter.roles,
        falsifiers=adapter.falsifiers,
    )
    # The adapter drops un-classifiable statutes from the stream; reflect them honestly.
    ledger.statute_errors = len(sids) - ledger.statutes
    return ledger


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Witness-attribution spec-discovery ledger")
    ap.add_argument("sids", nargs="*", help="statute ids, e.g. 1958/370")
    ap.add_argument("-j", "--jurisdiction", default="fi",
                    help="frontend adapter (fi/uk/ee/no/se/eu/us/nz)")
    ap.add_argument("--corpus-bench", action="store_true",
                    help="[-j fi] data/finland/bench_core.csv  "
                         "[-j uk] data/uk/bench_corpus_smoke.csv  "
                         "[-j ee] data/estonia/bench_corpus.csv  "
                         "[-j no] most-amended replayable base acts (inventory scan)  "
                         "[-j se] amending SFS ids with compiled ops (archive)  "
                         "[-j us] included US bench windows  "
                         "[-j nz] NZ smoke-corpus work ids")
    ap.add_argument("--corpus-full", action="store_true",
                    help="[-j ee] data/estonia/current_replayable_corpus.csv")
    ap.add_argument("--mode", default="official_consolidation",
                    choices=["official_consolidation", "legal_pit"])
    ap.add_argument("--json", default="", help="write full ledger JSON to this path")
    ap.add_argument(
        "--out-dir",
        default="",
        help=(
            "write deterministic spec_ledger.json + spec_ledger.md report artifacts "
            "to this directory"
        ),
    )
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
    if args.out_dir:
        from lawvm.tools.spec_ledger_report import persist_ledger

        json_path = persist_ledger(ledger, Path(args.out_dir))
        print(f"wrote {json_path}", file=sys.stderr)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(ledger.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.json}", file=sys.stderr)
    print(render_markdown(ledger))
    return 0


if __name__ == "__main__":
    # ``python -m lawvm.tools.spec_ledger`` imports this file twice — once as
    # ``__main__`` (here) and once as ``lawvm.tools.spec_ledger`` when an adapter does
    # ``from lawvm.tools.spec_ledger import register_ledger_adapter``. Each import has
    # its OWN ``_LEDGER_ADAPTERS`` dict, so adapters self-register into the canonical
    # module's registry while ``__main__``'s stays empty — every ``-j`` dispatch then
    # fails with a spurious NotImplementedError. Delegate to the canonical module's
    # ``main`` so registration and dispatch share one registry.
    import importlib

    _canonical = importlib.import_module("lawvm.tools.spec_ledger")
    raise SystemExit(_canonical.main())
