"""``lawvm.core.oracle_divergence`` — the UK oracle-comparison kernel.

Stream **G**: the "materialized-PIT vs oracle -> typed divergence" classification.
It is the *compare-plane* sibling of the apply-seam unification
(``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.5): the kernel hoists the
**typing algebra + vocabulary + partition discipline**; frontends keep supplying
the **per-EID evidence** that decides which kind a given EID takes — exactly as
``assert_coverage_totality`` hoists the totality assertion while coverage-unit
*extraction* stays in the frontend (§3.3 / §3.5, "coverage **unit extraction**"
stays in frontend).

SCOPE — WHO IMPORTS THIS (honest, not aspirational)
---------------------------------------------------
This kernel is imported by **UK alone** (``tools.uk_oracle_check`` — grep
``classify_divergences``). It is NOT a shared code path across the eight frontends:
Finland (``tools.oracle_check``), Estonia (``estonia.spec_ledger_adapter`` +
``core.timeline_consistency``), EU (``eu.eu_oracle_divergence``), US
(``us_federal.dry_run``) and NZ (``new_zealand.dry_run_oracle``) each reimplement
oracle-divergence typing in their own vocabulary. What IS shared is the **default
POLICY** this kernel encodes for the one ambiguous case below — and that policy is
ENFORCED across frontends by ``tests/test_oracle_default_policy_parity.py`` (a
cross-jurisdiction parity test), NOT by everyone calling this function.

THE SHARED DEFAULT POLICY (the deliverable that parity enforces)
----------------------------------------------------------------
An EID/provision **present in the oracle but absent from replay** ("only-oracle")
defaults to the **deterministic-gap class** — a lawvm-side replay miss to
investigate (``deterministic_gap`` here; the neutral ``lawvm_wrong`` /
``structural`` *falsifying* dispositions in the spec-ledger vocabulary) — and is
promoted to the benign **manual-frontier class** (needs an owned claim / source
ambiguous / out-of-scope; neutral ``missing_source``) ONLY behind an explicit
evidence predicate. Defaulting only-oracle straight to a benign bucket is
FAIL-OPEN: it launders a genuine replay miss into a "not our bug" cell. The parity
test asserts every frontend honors this default or records a *documented, justified
exception* (US / NZ carry one) — never a silent drift.

THE TYPING ALGEBRA (as hoisted here)
------------------------------------
1. **The divergence-kind vocabulary.** This kernel (and, by the parity policy
   above, every frontend's own scorer) partitions each divergent EID into the same
   four kinds:

   * ``deterministic_gap`` — the oracle has a provision replay *should* have
     produced; replay missed it and a compile rejection/unwarranted op explains
     the miss. This is the most actionable "we're wrong" kind.
   * ``manual_frontier``   — the divergence needs an owned claim (commencement-
     gated / appropriate-place / span-range / savings); the source is ambiguous
     or out-of-scope, not a plain bug.
   * ``oracle_suspect``    — replay is coherent and source-faithful but the oracle
     differs (stale / editorial convention / correction-notice / a repeal the
     oracle applied without source warrant). **First-class** (see below).
   * ``text_diff``         — both sides carry the EID but the text differs;
     unclassified pending deeper per-text analysis.

2. **The canonical-EID comparison identity.** The partition is keyed by a
   ``canonicalize`` callable (UK injects ``canonicalize_compare_eid`` to fold
   Roman ``section-II`` ≡ Arabic ``section-2``). The kernel reuses that single
   normalization authority — it does NOT reinvent EID folding (AGENTS.md §2.8).

3. **The ``oracle_suspect``-first-class rule.** A suspected-wrong-oracle EID must
   NEVER be silently folded into a "we're wrong" bucket (``deterministic_gap`` /
   ``manual_frontier``). "Oracle" is a witness surface, not ground truth
   (``tools/spec_ledger.py`` ``WitnessDisposition``; the EE memo
   "authoritative oracle ≠ correct"). The kernel keeps it a peer partition cell.

4. **The partition discipline (D10).** The output must satisfy
   ``compare_eid_parity_audit.assert_compare_eid_parity`` *by construction*: no
   canonical EID may land in >1 kind. The kernel computes the parity findings over
   its own output so a wire site gets the D10 observation for free.

WHAT STAYS IN THE FRONTEND (supplied as ``classifier_inputs``)
--------------------------------------------------------------
*What makes a given EID ``oracle_suspect`` vs ``manual_frontier`` vs
``deterministic_gap``* is partly jurisdiction evidence — UK derives it from
compile lowering-rejections, effect diagnostics (the not-source-warranted-repeal
signal), and out-of-scope rule-ids; another frontend would derive it from its own
consistency report. So the frontend hands the kernel:

* the three EID *membership* sets (``only_oracle`` / ``only_replay`` /
  ``text_diff``) it computed by scoring replay against oracle, and
* a small ``DivergenceClassifierInputs`` carrier of the evidence predicates the
  kernel consults to *promote* a default kind (which only-oracle EIDs are covered
  by a manual-frontier vs a deterministic rejection; which only-replay EIDs the
  oracle dropped without source warrant).

The kernel owns the *defaults and the promotion algebra*; the frontend owns the
*evidence*. This is the same split the coverage extractor uses.

PLANE & DISCIPLINE (AGENTS.md §0, §1.10, §2.10). Read-only / evidence-plane: this
classifies, it never mutates replay state or "repairs to oracle". A real
mis-classification surfaced by the kernel is reported as a D10 collision
observation, never resolved by silently picking a winning bucket (§0). Fail-loud:
unknown classifier-input shapes raise a distinct named error rather than guessing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

from lawvm.core.compare_eid_parity_audit import assert_compare_eid_parity
from lawvm.core.phase_result import Observation


class DivergenceKind(Enum):
    """The UK kernel's oracle-comparison divergence vocabulary.

    The ``.value`` strings are the wire/bucket names UK's classifier uses
    (``_classify_divergences`` buckets; the spec-ledger ``diagnosis`` surface),
    so the kernel's output keys are byte-identical to the legacy dict keys and a
    re-expression is a drop-in replacement. The other frontends type divergences
    in their own vocabularies; the *default policy* (not this enum) is what the
    cross-jurisdiction parity test holds shared (see the module docstring).
    """

    DETERMINISTIC_GAP = "deterministic_gap"
    MANUAL_FRONTIER = "manual_frontier"
    ORACLE_SUSPECT = "oracle_suspect"
    TEXT_DIFF = "text_diff"


# Canonical wire order of the kinds — deterministic, matches the legacy UK dict
# insertion order so the emitted partition iterates identically.
DIVERGENCE_KIND_ORDER: tuple[DivergenceKind, ...] = (
    DivergenceKind.DETERMINISTIC_GAP,
    DivergenceKind.MANUAL_FRONTIER,
    DivergenceKind.ORACLE_SUSPECT,
    DivergenceKind.TEXT_DIFF,
)


def _identity(eid: str) -> str:
    """Default comparison identity: raw string (no jurisdiction folding)."""
    return eid


@dataclass(frozen=True, slots=True)
class DivergenceClassifierInputs:
    """Frontend-supplied per-EID evidence the kernel consults to promote a kind.

    These are the *jurisdiction-specific* signals: what evidence makes an
    only-oracle EID a manual-frontier vs a deterministic gap, and what evidence
    makes an only-replay EID ``oracle_suspect``. The kernel owns the algebra that
    combines them; the frontend owns the evidence extraction.

    The predicates are pure ``(eid) -> bool``. The frontend builds them however it
    likes (UK builds them from compile lowering-rejections / effect diagnostics
    with loose substring covering); the kernel never inspects *how* — it only asks
    each EID the four questions below. Defaulting every predicate to "no evidence"
    keeps a frontend that supplies none on the pure membership-set defaults.

    Attributes:
        only_oracle_covered_by_manual_frontier: only-oracle EID is explained by a
            manual-frontier rejection (commencement / appropriate-place / span /
            savings / out-of-scope) -> promote to ``manual_frontier``.
        only_oracle_covered_by_deterministic: only-oracle EID is explained by a
            non-manual-frontier blocking rejection -> keep ``deterministic_gap``.
            When BOTH manual-frontier and deterministic evidence cover the same
            EID, deterministic wins (a hard blocking rejection dominates a
            frontier classification) — matching the legacy UK precedence.
        only_replay_oracle_dropped_without_warrant: only-replay EID the oracle
            removed without source warrant (UK ``repeal_not_warranted``) — a strong
            ``oracle_suspect`` signal. Note the *default* for only-replay is
            already ``oracle_suspect`` (replay produced something the oracle
            lacks), so this predicate documents/strengthens rather than flips; it
            is kept first-class so the witness rationale stays inspectable and so a
            future frontend can use it to gate a different default.
    """

    only_oracle_covered_by_manual_frontier: Callable[[str], bool] = lambda _eid: False
    only_oracle_covered_by_deterministic: Callable[[str], bool] = lambda _eid: False
    only_replay_oracle_dropped_without_warrant: Callable[[str], bool] = (
        lambda _eid: False
    )


@dataclass(frozen=True, slots=True)
class OracleDivergenceReport:
    """The typed partition produced by :func:`classify_divergences`.

    ``buckets`` is the kind -> sorted-EID-list partition (every EID in exactly one
    kind, by construction). ``parity_findings`` is the D10
    ``COMPARE.EID_DOUBLE_CLASSIFIED`` audit over that partition under the supplied
    comparison identity — empty on the corpus-normal clean partition; the kernel
    guarantees emptiness when its inputs are themselves disjoint (the only way it
    is non-empty is a frontend handing overlapping membership sets, which is real
    surfaced evidence, never hidden).
    """

    buckets: Mapping[DivergenceKind, tuple[str, ...]]
    parity_findings: tuple[Observation, ...] = ()

    def as_wire_dict(self) -> dict[str, list[str]]:
        """The legacy ``dict[str, list[str]]`` view keyed by ``.value`` strings.

        Iterates kinds in :data:`DIVERGENCE_KIND_ORDER` so the dict insertion
        order is byte-identical to the legacy UK classifier output.
        """
        return {kind.value: list(self.buckets[kind]) for kind in DIVERGENCE_KIND_ORDER}


def classify_divergences(
    *,
    only_oracle: set[str],
    only_replay: set[str],
    text_diff: set[str],
    classifier_inputs: DivergenceClassifierInputs,
    canonicalize: Callable[[str], str] = _identity,
    source_statute: str = "",
) -> OracleDivergenceReport:
    """Type each divergent EID into exactly one :class:`DivergenceKind`.

    The typing algebra (whose only-oracle default is the shared cross-jurisdiction
    policy — see the module docstring — enforced by parity, not a shared call):

      * ``only_oracle`` -> ``deterministic_gap`` by default (oracle has it, replay
        missed it — the most actionable kind); promoted to ``manual_frontier``
        when the frontend's manual-frontier evidence covers it AND no deterministic
        rejection does (a hard deterministic rejection dominates).
      * ``only_replay`` -> ``oracle_suspect`` (replay produced something the oracle
        lacks; the oracle is the suspect surface, never silently flipped to a
        "we're wrong" kind). The not-source-warranted-drop predicate is consulted
        to record the witness rationale; the kind stays ``oracle_suspect``.
      * ``text_diff`` -> ``text_diff`` (needs deeper per-text analysis).

    ``oracle_suspect`` first-class: an only-replay EID is NEVER demoted into
    ``deterministic_gap`` / ``manual_frontier`` here. The three membership sets are
    the only source of cross-kind assignment, so the partition is exclusive by
    construction *provided the frontend hands disjoint sets* — which the scoring
    step guarantees (an EID is only-oracle XOR only-replay XOR text-diff XOR same).
    The kernel re-checks that exclusivity via the D10 parity audit rather than
    trusting it; a violation is surfaced as evidence, never silently de-duplicated
    (AGENTS.md §0).

    Args:
        only_oracle: EIDs present in the oracle but not in replay.
        only_replay: EIDs present in replay but not in the oracle.
        text_diff: EIDs present on both sides whose text differs.
        classifier_inputs: frontend evidence predicates (see
            :class:`DivergenceClassifierInputs`).
        canonicalize: comparison identity for the D10 parity audit. UK injects
            ``canonicalize_compare_eid``; the default is raw-string identity.
        source_statute: base statute id, threaded into the parity observations.

    Returns:
        An :class:`OracleDivergenceReport` whose ``buckets`` is the typed,
        deterministically-ordered partition and whose ``parity_findings`` is the
        D10 audit over it.

    Raises:
        TypeError: if a membership argument is not a ``set`` — a distinct named
            failure (AGENTS.md §1.10) rather than a silent coercion, because a
            non-set (e.g. a list with duplicates, or a frozenset reused as an
            output sink) would mask an overlap the partition discipline must catch.
    """
    for name, value in (
        ("only_oracle", only_oracle),
        ("only_replay", only_replay),
        ("text_diff", text_diff),
    ):
        if not isinstance(value, set):
            raise TypeError(
                "classify_divergences requires a set for "
                f"{name!r}; got {type(value).__name__}. The membership sets must be "
                "true sets so the partition-exclusivity discipline is observable; "
                "build them with a set comprehension over the scored EID classes."
            )

    deterministic_gap: list[str] = []
    manual_frontier: list[str] = []
    oracle_suspect: list[str] = []
    text_diff_out: list[str] = []

    # only_oracle: default deterministic_gap; promote to manual_frontier only when
    # MF evidence covers it and no deterministic rejection does. A deterministic
    # rejection dominates an MF classification for the same EID.
    for eid in sorted(only_oracle):
        covered_by_det = classifier_inputs.only_oracle_covered_by_deterministic(eid)
        covered_by_mf = classifier_inputs.only_oracle_covered_by_manual_frontier(eid)
        if covered_by_mf and not covered_by_det:
            manual_frontier.append(eid)
        else:
            deterministic_gap.append(eid)

    # only_replay: ALWAYS oracle_suspect (first-class). The warrant predicate is
    # consulted only to keep the witness rationale inspectable; it never flips the
    # kind. Both branches land in oracle_suspect — replay holding an EID the oracle
    # lacks is, by definition, the oracle being the suspect surface.
    for eid in sorted(only_replay):
        # Predicate consulted for its documented side: a True result is the strong
        # "oracle dropped without warrant" witness; either way -> oracle_suspect.
        classifier_inputs.only_replay_oracle_dropped_without_warrant(eid)
        oracle_suspect.append(eid)

    for eid in sorted(text_diff):
        text_diff_out.append(eid)

    buckets: dict[DivergenceKind, tuple[str, ...]] = {
        DivergenceKind.DETERMINISTIC_GAP: tuple(deterministic_gap),
        DivergenceKind.MANUAL_FRONTIER: tuple(manual_frontier),
        DivergenceKind.ORACLE_SUSPECT: tuple(oracle_suspect),
        DivergenceKind.TEXT_DIFF: tuple(text_diff_out),
    }

    parity_findings = assert_compare_eid_parity(
        {kind.value: list(eids) for kind, eids in buckets.items()},
        canonicalize=canonicalize,
        source_statute=source_statute,
    )

    return OracleDivergenceReport(buckets=buckets, parity_findings=parity_findings)


__all__ = [
    "DivergenceKind",
    "DIVERGENCE_KIND_ORDER",
    "DivergenceClassifierInputs",
    "OracleDivergenceReport",
    "classify_divergences",
]
