"""CTSF STATE_INDEX commensurability layer — task #197 (CTSF Phase 2).

The "commensurability FIRST" hard ordering (``pro_on_fable_notes.txt`` §4;
``FABLE_CORRECTNESS_METRIC.md`` §2 / §2.1): *before* any CTSF content comparison,
classify whether replay and oracle are even rendering the same legal state. A
pair that is state-index-incommensurable is NOT a text divergence — it
short-circuits to a typed ``STATE_INDEX.*`` residual before ``ctsf_equal`` runs.

Why this is a distinct axis (FABLE §2): oracle-newer content, future-effective
embedding, expiry-past-cutoff, extent-branch mismatch and alternate unit-version
selection are *precondition judgments about the pair*, not textual diffs. The
current system already detects them — but launders the signal through "structural
/ textual diff": ``get_consolidated_oracle_suspect`` (doc-level eff/expiry vs
``dateConsolidated``), ``oracle_amb_alternate_match`` (unit-level version
selection), and the ``extent_branch_mismatch`` / ``temporal_mismatch``
``AgreementResidual`` families. This module gives that signal a first-class typed
vocabulary and, crucially, a *hard ordering* enforced in code.

Slotting (reuse, do not duplicate):
* the ``AgreementResidual`` ``temporal_mismatch`` / ``extent_branch_mismatch``
  families (``core/agreement_residual.py``) — each STATE_INDEX kind maps to one;
* the #183 touch-relation's existing ``temporal_mismatch_commensurability``
  verdict (``tools/fi_anchor_manifest.py``) — this module is the per-unit,
  first-class generalization the touch engine's doc-level oracle-suspect gate
  anticipates.

ADDITIVE / NON-GATING (Phase 2 discipline): importing or running this layer
leaves default bench output byte-identical. It is a telemetry + future-gate
surface, wired into no bench headline.

DISCLAIMER (carried per the CTSF rename ruling): state-index commensurability is
a precondition for LawVM's *replay text-state equality* claim; an incommensurable
pair means the two documents index different legal states, not that either is
wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from lawvm.core.agreement_residual import AgreementResidual, AgreementResidualFamily

STATE_INDEX_VERSION = "v0"


# ---------------------------------------------------------------------------
# The typed STATE_INDEX residual taxonomy (pro_on_fable_notes §4 "Phase 4").
# ---------------------------------------------------------------------------

StateIndexKind = Literal[
    "STATE_INDEX.ORACLE_NEWER_UNIT",
    "STATE_INDEX.FUTURE_EFFECTIVE_EMBEDDED",
    "STATE_INDEX.EXTENT_BRANCH_MISMATCH",
    "STATE_INDEX.EXPIRY_CUTOFF_MISMATCH",
    "STATE_INDEX.UNIT_VERSION_AMBIGUOUS",
]

_STATE_INDEX_KINDS: frozenset[str] = frozenset(StateIndexKind.__args__)

# Each STATE_INDEX kind maps to exactly one existing AgreementResidual family, so
# the residual sink stays unified (FABLE §5.2: one taxonomy). All five are
# non-content, non-billable-to-replay causes.
_KIND_TO_FAMILY: dict[str, AgreementResidualFamily] = {
    "STATE_INDEX.ORACLE_NEWER_UNIT": "temporal_mismatch",
    "STATE_INDEX.FUTURE_EFFECTIVE_EMBEDDED": "temporal_mismatch",
    "STATE_INDEX.EXTENT_BRANCH_MISMATCH": "extent_branch_mismatch",
    "STATE_INDEX.EXPIRY_CUTOFF_MISMATCH": "temporal_mismatch",
    "STATE_INDEX.UNIT_VERSION_AMBIGUOUS": "temporal_mismatch",
}


@dataclass(frozen=True, slots=True)
class StateIndexResidual:
    """A typed 'the pair indexes different legal states' marker.

    ``kind`` is a ``STATE_INDEX.*`` token; ``evidence`` is a human-auditable
    witness (dates, version tags) so a reviewer can confirm the incommensurability
    call — mirroring the ``get_consolidated_oracle_suspect`` witness style.
    """

    kind: StateIndexKind
    address: str
    evidence: str

    def __post_init__(self) -> None:
        if self.kind not in _STATE_INDEX_KINDS:
            raise ValueError(
                f"StateIndexResidual.kind must be one of {sorted(_STATE_INDEX_KINDS)}"
            )

    @property
    def family(self) -> AgreementResidualFamily:
        return _KIND_TO_FAMILY[self.kind]

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "address": self.address, "evidence": self.evidence}

    def to_agreement_residual(
        self, *, jurisdiction: str = "finland", agreement_surface: str = "ctsf_state_index"
    ) -> AgreementResidual:
        """Project into the shared ``AgreementResidual`` taxonomy (reuse the sink)."""
        return AgreementResidual(
            residual_id=f"{jurisdiction}:state-index:{self.kind}:{self.address}",
            jurisdiction=jurisdiction,
            agreement_surface=agreement_surface,
            family=self.family,
            agreement_residual_status="blocked",
            owner_phase="ctsf.state_index.commensurability_first",
            rule_id=self.kind,
            source_artifact_id=self.address,
            safe_default="short_circuit_to_typed_residual_without_scoring_content",
            forbidden_shortcuts=(
                "state_index_residual_as_text_divergence",
                "incommensurable_pair_as_replay_bug",
            ),
            detail={"address": self.address, "evidence": self.evidence},
        )


# ---------------------------------------------------------------------------
# The per-side state index — the coordinates a rendering is indexed by.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StateIndex:
    """The legal-state coordinates one rendering (replay or oracle) is indexed by.

    Coordinates (FABLE §2): the time cutoff (``as_of`` / ``cutoff_date``), the
    version-pin amendment and its effective/expiry dates, the extent branch, and
    the version-selection witness. ``None`` fields mean "unknown / not asserted"
    — an unknown coordinate never manufactures an incommensurability (fail open to
    "commensurable", so nothing is silently laundered into a residual).

    Built one-sided per rendering. Replay's index is its PIT query; the oracle's
    index is read from the consolidated artifact metadata (``dateConsolidated``,
    the version-pin amendment's effective/expiry, the ``amb`` alternate match).
    """

    as_of: Optional[str] = None            # the rendering's claimed cutoff (ISO date)
    version_amendment_id: Optional[str] = None  # the version-pin amendment id
    effective_date: Optional[str] = None   # version-pin amendment's effective date
    expiry_date: Optional[str] = None      # version-pin amendment's expiry date
    extent_branch: Optional[str] = None    # force/extent branch identity
    version_selection: Optional[str] = None  # amb / alternate-version witness

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, value in (
            ("as_of", self.as_of),
            ("version_amendment_id", self.version_amendment_id),
            ("effective_date", self.effective_date),
            ("expiry_date", self.expiry_date),
            ("extent_branch", self.extent_branch),
            ("version_selection", self.version_selection),
        ):
            if value is not None:
                out[name] = value
        return out


def _lt(a: Optional[str], b: Optional[str]) -> bool:
    """ISO-date string comparison that fails open (returns False) when unknown."""
    if a is None or b is None:
        return False
    return a < b


def classify_commensurability(
    replay: StateIndex,
    oracle: StateIndex,
    *,
    address: str = "",
) -> tuple[StateIndexResidual, ...]:
    """Classify whether ``replay`` and ``oracle`` index the same legal state.

    Returns the typed ``STATE_INDEX.*`` residuals for every commensurability
    failure found (empty tuple ⇒ commensurable). Detection mirrors the existing
    signals but names them first-class:

    * ``ORACLE_NEWER_UNIT`` / ``FUTURE_EFFECTIVE_EMBEDDED``: the oracle's
      version-pin amendment enters force AFTER the replay cutoff — the oracle
      embedded a future/newer version than the PIT asks for (the
      ``get_consolidated_oracle_suspect`` eff>cutoff signal, generalized). When
      the oracle's own ``as_of`` is known and the effective date also exceeds it,
      the embedding is future-relative-to-the-oracle-cutoff too, so the stronger
      ``FUTURE_EFFECTIVE_EMBEDDED`` is emitted; otherwise ``ORACLE_NEWER_UNIT``.
    * ``EXPIRY_CUTOFF_MISMATCH``: the oracle's version-pin amendment EXPIRED
      before the oracle's own cutoff — a stale/expired version rendered (the
      ``expiry<cutoff`` signal).
    * ``EXTENT_BRANCH_MISMATCH``: replay and oracle render different extent
      branches (a genuine ``extent_branch_mismatch`` family case).
    * ``UNIT_VERSION_AMBIGUOUS``: the oracle rendered a genuine-but-not-PIT-chosen
      version of the unit (the ``amb`` alternate-match signal).
    """
    residuals: list[StateIndexResidual] = []

    # -- oracle-newer / future-effective embedding --
    eff = oracle.effective_date
    replay_cutoff = replay.as_of
    oracle_cutoff = oracle.as_of
    if _lt(replay_cutoff, eff):
        if _lt(oracle_cutoff, eff):
            residuals.append(
                StateIndexResidual(
                    kind="STATE_INDEX.FUTURE_EFFECTIVE_EMBEDDED",
                    address=address,
                    evidence=(
                        f"oracle version {oracle.version_amendment_id} eff {eff} "
                        f"> oracle cutoff {oracle_cutoff} and > replay as-of {replay_cutoff}"
                    ),
                )
            )
        else:
            residuals.append(
                StateIndexResidual(
                    kind="STATE_INDEX.ORACLE_NEWER_UNIT",
                    address=address,
                    evidence=(
                        f"oracle version {oracle.version_amendment_id} eff {eff} "
                        f"> replay as-of {replay_cutoff}"
                    ),
                )
            )

    # -- expired-before-cutoff --
    if _lt(oracle.expiry_date, oracle_cutoff):
        residuals.append(
            StateIndexResidual(
                kind="STATE_INDEX.EXPIRY_CUTOFF_MISMATCH",
                address=address,
                evidence=(
                    f"oracle version {oracle.version_amendment_id} expired "
                    f"{oracle.expiry_date} < oracle cutoff {oracle_cutoff}"
                ),
            )
        )

    # -- extent-branch mismatch --
    if (
        replay.extent_branch is not None
        and oracle.extent_branch is not None
        and replay.extent_branch != oracle.extent_branch
    ):
        residuals.append(
            StateIndexResidual(
                kind="STATE_INDEX.EXTENT_BRANCH_MISMATCH",
                address=address,
                evidence=(
                    f"replay extent branch {replay.extent_branch!r} != "
                    f"oracle extent branch {oracle.extent_branch!r}"
                ),
            )
        )

    # -- alternate unit-version selection (amb) --
    if oracle.version_selection:
        residuals.append(
            StateIndexResidual(
                kind="STATE_INDEX.UNIT_VERSION_AMBIGUOUS",
                address=address,
                evidence=(
                    f"oracle rendered a genuine-but-not-PIT-chosen version: "
                    f"{oracle.version_selection}"
                ),
            )
        )

    return tuple(residuals)


def is_commensurable(replay: StateIndex, oracle: StateIndex, *, address: str = "") -> bool:
    """True iff the pair indexes the same legal state (no STATE_INDEX residual)."""
    return not classify_commensurability(replay, oracle, address=address)


# ---------------------------------------------------------------------------
# The commensurability-FIRST short-circuit (the hard ordering).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommensurabilityOutcome:
    """The result of running commensurability BEFORE content comparison.

    ``commensurable`` is True iff ``state_index_residuals`` is empty. When it is
    False, ``content_compared`` is ``False`` — the content comparator was never
    reached — and the caller must attribute the divergence to the typed
    STATE_INDEX residual(s), never to a text diff. This is the load-bearing
    ordering: an incommensurable pair CANNOT be scored as content error.
    """

    commensurable: bool
    state_index_residuals: tuple[StateIndexResidual, ...]
    content_compared: bool
    content_equal: Optional[bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_index_version": STATE_INDEX_VERSION,
            "commensurable": self.commensurable,
            "content_compared": self.content_compared,
            "content_equal": self.content_equal,
            "state_index_residuals": [r.to_dict() for r in self.state_index_residuals],
        }


def commensurability_first(
    replay_index: StateIndex,
    oracle_index: StateIndex,
    content_equal_fn: Callable[[], bool],
    *,
    address: str = "",
) -> CommensurabilityOutcome:
    """Run STATE_INDEX commensurability BEFORE content comparison (hard ordering).

    ``content_equal_fn`` is a zero-arg callable that performs the (expensive)
    CTSF content comparison and returns a bool. It is invoked *only if* the pair
    is state-index-commensurable — so an incommensurable pair short-circuits to
    the typed residual and NEVER reaches the content comparator. This is the
    "commensurability first" pipeline of ``pro_on_fable_notes.txt`` §4, enforced
    structurally rather than by convention.
    """
    residuals = classify_commensurability(replay_index, oracle_index, address=address)
    if residuals:
        return CommensurabilityOutcome(
            commensurable=False,
            state_index_residuals=residuals,
            content_compared=False,
            content_equal=None,
        )
    equal = bool(content_equal_fn())
    return CommensurabilityOutcome(
        commensurable=True,
        state_index_residuals=(),
        content_compared=True,
        content_equal=equal,
    )
