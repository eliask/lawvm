"""eu_oracle_divergence.py — replay-vs-consolidation divergence (NEVER repair).

The LawVM-native replay (design §3.2a) reconstructs a point-in-time body by
applying amending acts' ops to the base. The EUR-Lex SECTOR-0 consolidation
(§3.2b) is the Office's editorial rendering of the same point-in-time. They CHECK
each other: agreement corroborates both; divergence is a FIRST-CLASS FINDING.

Consolidation has "no legal value … no guarantee [of] the latest state" (EUR-Lex)
— the same ``authoritative oracle ≠ correct`` situation already first-class in
LawVM (EE oracle_suspect). So this comparator NEVER auto-repairs the replayed
body to match the consolidation: it CLASSIFIES each per-article divergence and
returns it as evidence. The caller decides; the kernel does not fit-to-oracle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind

if TYPE_CHECKING:
    from lawvm.core.bench_contract import BenchUnitResult

DivergenceKind = Literal[
    "agreement",
    "text_divergence",
    "present_in_replay_absent_in_oracle",
    "present_in_oracle_absent_in_replay",
]


@dataclass(frozen=True, slots=True)
class ArticleDivergence:
    """One per-article divergence classification (or agreement)."""

    article_label: str
    kind: DivergenceKind
    replay_text: str = ""
    oracle_text: str = ""

    @property
    def agrees(self) -> bool:
        return self.kind == "agreement"


@dataclass
class OracleComparison:
    """The full replay-vs-consolidation comparison at one PIT. Never repaired."""

    as_of: str
    base_celex: str
    divergences: list[ArticleDivergence] = field(default_factory=list)

    @property
    def article_count(self) -> int:
        return len(self.divergences)

    @property
    def agreement_count(self) -> int:
        return sum(1 for d in self.divergences if d.agrees)

    @property
    def divergence_count(self) -> int:
        return sum(1 for d in self.divergences if not d.agrees)

    @property
    def agreement_fraction(self) -> float:
        if not self.divergences:
            return 0.0
        return self.agreement_count / self.article_count

    def divergences_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.divergences:
            out[d.kind] = out.get(d.kind, 0) + 1
        return out


def _articles(statute: IRStatute) -> dict[str, str]:
    """Map article label → normalized text for every SECTION in the body tree.

    Compares the node kind by its STRING value, not enum identity: the native
    replay path mints nodes with the ``IRNodeKind.SECTION`` enum, while the
    grafter (parsing a real consolidated FMX4) sets ``kind`` to the bare string
    ``"section"`` (``cast(IRNodeKind, "section")``). Both must be recognised, or
    a grafter-parsed consolidation looks article-less and every replayed article
    is mis-classified as ``present_in_replay_absent_in_oracle``.
    """
    section_value = IRNodeKind.SECTION.value  # "section"
    out: dict[str, str] = {}

    def _walk(node: IRNode) -> None:
        kind_value = node.kind.value if hasattr(node.kind, "value") else str(node.kind)
        if kind_value == section_value and node.label:
            out[str(node.label)] = _norm(_node_text(node))
        for child in node.children:
            _walk(child)

    _walk(statute.body)
    return out


def _node_text(node: IRNode) -> str:
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node.children:
        parts.append(_node_text(child))
    return " ".join(p for p in parts if p)


def _norm(text: str) -> str:
    return " ".join(text.split())


def compare_replay_to_consolidation(
    replayed: IRStatute,
    consolidated: IRStatute,
    *,
    as_of: str,
    base_celex: str,
) -> OracleComparison:
    """Classify per-article divergence between a replayed body and a consolidation.

    NEVER mutates either input toward the other. Returns an :class:`OracleComparison`
    whose ``divergences`` is the per-article evidence ledger.
    """
    comparison = OracleComparison(as_of=as_of, base_celex=base_celex)
    replay_arts = _articles(replayed)
    oracle_arts = _articles(consolidated)
    labels = sorted(set(replay_arts) | set(oracle_arts), key=_label_sort_key)

    for label in labels:
        in_replay = label in replay_arts
        in_oracle = label in oracle_arts
        if in_replay and in_oracle:
            r, o = replay_arts[label], oracle_arts[label]
            kind: DivergenceKind = "agreement" if r == o else "text_divergence"
            comparison.divergences.append(
                ArticleDivergence(
                    article_label=label, kind=kind, replay_text=r, oracle_text=o
                )
            )
        elif in_replay:
            comparison.divergences.append(
                ArticleDivergence(
                    article_label=label,
                    kind="present_in_replay_absent_in_oracle",
                    replay_text=replay_arts[label],
                )
            )
        else:
            comparison.divergences.append(
                ArticleDivergence(
                    article_label=label,
                    kind="present_in_oracle_absent_in_replay",
                    oracle_text=oracle_arts[label],
                )
            )
    return comparison


def _label_sort_key(label: str) -> tuple[int, str]:
    """Sort article labels numerically when possible (1, 2, 5a, 10), then lexically."""
    head = label
    suffix = ""
    while head and not head[-1].isdigit():
        suffix = head[-1] + suffix
        head = head[:-1]
    try:
        return (int(head), suffix)
    except ValueError:
        return (1_000_000, label)


# ---------------------------------------------------------------------------
# Increment 3 (Goal 3) — corpus-scale divergence account
# ---------------------------------------------------------------------------
#
# A single ``OracleComparison`` is one act at one PIT. The corpus-scale account
# aggregates many of them and maps each per-article ``DivergenceKind`` to the
# cross-jurisdiction divergence-class vocabulary already first-class in LawVM (the
# ``authoritative oracle ≠ correct`` regime; cf. EE ``oracle_suspect``):
#
#   * ``agreement``                          → corroboration (replay == oracle)
#   * ``text_divergence``                    → ``text_diff``       (both present,
#                                              text differs — a divergence to TYPE,
#                                              never auto-repaired)
#   * ``present_in_replay_absent_in_oracle`` → ``deterministic_gap`` (replay knows
#                                              an article the editorial
#                                              consolidation does not render —
#                                              a deterministic-replay surplus)
#   * ``present_in_oracle_absent_in_replay`` → ``deterministic_gap`` by DEFAULT (an
#                                              article the native replay has not
#                                              reconstructed is, absent evidence, a
#                                              deterministic replay MISS), PROMOTED
#                                              to ``manual_frontier`` ONLY when the
#                                              caller supplies explicit
#                                              manual-compilation evidence for that
#                                              label (see ``add``)
#
# The only-oracle default mirrors the kernel policy in
# :func:`lawvm.core.oracle_divergence.classify_divergences` (only_oracle defaults to
# ``deterministic_gap``; it is promoted to ``manual_frontier`` only behind an
# explicit MF-evidence predicate). A BLANKET ``manual_frontier`` default is
# fail-open: it launders a genuine deterministic replay miss into a benign
# editorial-frontier bucket, so it is NOT centralised in ``_KIND_TO_CLASS``.
#
# ``oracle_suspect`` is reserved for an article the corpus marks as a known
# editorial artifact of the consolidation; the comparator does not synthesise it
# (it never repairs), so its count is 0 unless a caller supplies suspect labels.

#: Map from the per-article ``DivergenceKind`` to the DEFAULT corpus divergence
#: class. Only-oracle articles default to ``deterministic_gap``; the
#: ``manual_frontier`` promotion is an explicit, evidence-gated override applied in
#: :meth:`CorpusDivergenceAccount.add`, never a fail-open default here.
_KIND_TO_CLASS: dict[str, str] = {
    "agreement": "agreement",
    "text_divergence": "text_diff",
    "present_in_replay_absent_in_oracle": "deterministic_gap",
    "present_in_oracle_absent_in_replay": "deterministic_gap",
}

#: The corpus divergence classes, in a stable reporting order (denominator-first).
CORPUS_DIVERGENCE_CLASSES: tuple[str, ...] = (
    "agreement",
    "text_diff",
    "deterministic_gap",
    "manual_frontier",
    "oracle_suspect",
)


@dataclass
class CorpusDivergenceAccount:
    """Typed corpus-scale divergence account over many replay-vs-oracle PITs.

    Total-accounting discipline: ``article_total`` (the denominator) equals the
    sum of every per-class count — every compared article is owned by exactly one
    class, never silently dropped. ``oracle_suspect`` carries labels a caller has
    flagged as known editorial artifacts (the comparator never synthesises them).
    """

    comparisons: list[OracleComparison] = field(default_factory=list)
    class_counts: dict[str, int] = field(
        default_factory=lambda: {c: 0 for c in CORPUS_DIVERGENCE_CLASSES}
    )
    suspect_labels: dict[str, set[str]] = field(default_factory=dict)

    @property
    def act_count(self) -> int:
        """Distinct (base_celex, as_of) PITs compared."""
        return len({(c.base_celex, c.as_of) for c in self.comparisons})

    @property
    def article_total(self) -> int:
        """The denominator: every per-article comparison across the corpus."""
        return sum(self.class_counts[c] for c in CORPUS_DIVERGENCE_CLASSES)

    def add(
        self,
        comparison: OracleComparison,
        *,
        oracle_suspect_labels: frozenset[str] = frozenset(),
        manual_frontier_labels: frozenset[str] = frozenset(),
    ) -> None:
        """Fold one act's comparison into the corpus account.

        ``oracle_suspect_labels`` are article labels the caller KNOWS are editorial
        artifacts of THIS consolidation (so their divergence is the
        ``authoritative oracle ≠ correct`` case, not a replay defect). Such an
        article is counted as ``oracle_suspect`` regardless of its raw kind — the
        only place a label leaves its mechanical class, and only on explicit
        caller assertion (the comparator itself never repairs/relabels).

        ``manual_frontier_labels`` are only-oracle article labels the caller has
        POSITIVE manual-compilation evidence for (the editorial consolidation
        genuinely carries something the deterministic replay structurally cannot
        reconstruct — e.g. a manual corrigendum). An only-oracle article
        (``present_in_oracle_absent_in_replay``) DEFAULTS to ``deterministic_gap``
        (a deterministic replay miss) and is PROMOTED to ``manual_frontier`` ONLY
        when its label is in this set — mirroring the kernel only-oracle policy
        (:func:`lawvm.core.oracle_divergence.classify_divergences`). Absent
        evidence there is no blanket ``manual_frontier`` default that would launder
        a genuine replay miss into a benign editorial-frontier bucket.
        """
        self.comparisons.append(comparison)
        suspect_here: set[str] = set()
        for d in comparison.divergences:
            if d.article_label in oracle_suspect_labels:
                self.class_counts["oracle_suspect"] += 1
                suspect_here.add(d.article_label)
                continue
            try:
                cls = _KIND_TO_CLASS[d.kind]
            except KeyError:
                # §1.10 fail-loud: a DivergenceKind with no class mapping must
                # NOT be silently bucketed (a mislabeled gap is the exact
                # pathology the total-accounting discipline forbids). A new kind
                # added to the Literal must be mapped here deliberately.
                raise ValueError(
                    f"unmapped DivergenceKind {d.kind!r} in corpus divergence "
                    "accounting; add it to _KIND_TO_CLASS rather than defaulting "
                    "it to a benign class"
                ) from None
            # Only-oracle promotion: the map DEFAULTS an only-oracle article to
            # ``deterministic_gap``; promote it to ``manual_frontier`` ONLY behind
            # explicit manual-compilation evidence (never a fail-open default).
            if (
                d.kind == "present_in_oracle_absent_in_replay"
                and d.article_label in manual_frontier_labels
            ):
                cls = "manual_frontier"
            self.class_counts[cls] += 1
        if suspect_here:
            self.suspect_labels[f"{comparison.base_celex}@{comparison.as_of}"] = (
                suspect_here
            )

    @property
    def divergence_total(self) -> int:
        """All non-agreement, non-suspect classes (the typed divergence frontier)."""
        return (
            self.class_counts["text_diff"]
            + self.class_counts["deterministic_gap"]
            + self.class_counts["manual_frontier"]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "act_count": self.act_count,
            "article_total": self.article_total,
            "class_counts": dict(self.class_counts),
            "divergence_total": self.divergence_total,
            "conserved": self.article_total
            == sum(self.class_counts.values()),
        }


# ---------------------------------------------------------------------------
# Unified cross-jurisdiction bench contract adapter (EU joins the gated set)
# ---------------------------------------------------------------------------
#
# The unified bench contract (``lawvm.core.bench_contract.BenchUnitResult``) fixes
# the SHAPE of every jurisdiction's per-unit bench result — two error axes
# (``structural_err`` / ``text_err``, both ``[0, 1]``, 0 = perfect, worst-of is the
# headline), a typed ``residue_buckets`` account that must reconcile with the
# structural axis, and a ``BenchStatus``. FI / UK / EE / NZ / US / SE each register
# a comparator that maps their native oracle-comparison struct onto this contract;
# EU was the last frontend not registered. This adapter closes that gap.
#
# EU's native oracle-comparison struct is the per-article :class:`OracleComparison`
# (replay vs the sector-0 consolidation). The two axes fall straight out of the
# per-article ``DivergenceKind`` ontology, and — crucially — respect EU's honesty
# regime (the consolidation is editorial, "no legal value"; the comparator NEVER
# repairs the replay toward it):
#
#   * STRUCTURAL axis — an article present on exactly ONE side is a structural
#     divergence: ``present_in_replay_absent_in_oracle`` (a replay surplus /
#     ``deterministic_gap``) and ``present_in_oracle_absent_in_replay`` (the
#     editorial-consolidation surplus the native replay has not reconstructed /
#     ``manual_frontier``). ``structural_err`` = (those two) / (all compared
#     articles). Its residue buckets are those same two families, so a positive
#     structural error ALWAYS has a matching typed residue (and vice-versa) —
#     the contract's residue-reconciliation invariant holds by construction.
#   * TEXT axis — over the CO-PRESENT articles (present on both sides), the
#     fraction whose text diverges (``text_divergence``). ``text_err`` = text_diffs
#     / (agreements + text_diffs). It is ``None`` (not attempted, NOT a false 0)
#     when there are no co-present articles.
#
# ``oracle_suspect`` (a caller-asserted known editorial artifact) is NOT synthesised
# by the comparator, so an :class:`OracleComparison` never carries it; the
# ``ArticleDivergence`` ontology has no such kind, and it therefore does not enter
# either denominator here. A comparison with zero compared articles is ``NO_TRUTH``
# (nothing to score against — not a failure, mirroring the EE empty-oracle rule).


def eu_bench_unit_result(comparison: "OracleComparison") -> "BenchUnitResult":
    """Map an EU :class:`OracleComparison` onto a contract ``BenchUnitResult``.

    Deterministic and network-free: it consumes an already-built per-article
    comparison (the native replay vs the sector-0 consolidation), exactly as the
    sibling adapters consume their own native result structs. See the module note
    above for the axis derivation and the honesty regime it respects.
    """
    from lawvm.core.bench_contract import BenchStatus, BenchUnitResult

    unit_id = comparison.base_celex or "eu"
    kinds = comparison.divergences_by_kind()
    compared = comparison.article_count
    if compared == 0:
        # No comparable articles (e.g. an empty oracle body): unscorable, but not
        # a failure — a NO_TRUTH non-scored exclusion (mirrors EE empty-oracle).
        return BenchUnitResult(unit_id=unit_id, bench_unit_status=BenchStatus.NO_TRUTH)

    replay_only = kinds.get("present_in_replay_absent_in_oracle", 0)
    oracle_only = kinds.get("present_in_oracle_absent_in_replay", 0)
    agreements = kinds.get("agreement", 0)
    text_diffs = kinds.get("text_divergence", 0)

    # STRUCTURAL axis: one-sided articles over all compared articles.
    structural_err = (replay_only + oracle_only) / compared
    residue: dict[str, int] = {}
    if replay_only:
        residue["deterministic_gap"] = replay_only  # replay surplus (lawvm side)
    if oracle_only:
        residue["manual_frontier"] = oracle_only  # editorial-consolidation surplus

    # TEXT axis: over co-present articles only; None when there are none.
    co_present = agreements + text_diffs
    text_err: float | None = (text_diffs / co_present) if co_present > 0 else None

    return BenchUnitResult(
        unit_id=unit_id,
        bench_unit_status=BenchStatus.SCORED,
        structural_err=structural_err,
        text_err=text_err,
        residue_buckets=residue,
    )


def _register_eu_bench_comparator() -> None:
    from lawvm.core.bench_comparator_registry import register_bench_comparator

    register_bench_comparator("eu", eu_bench_unit_result)


_register_eu_bench_comparator()
