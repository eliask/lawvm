"""Per-unit materialization-totality lens — the "no hidden universe" invariant.

This is the FINLAND-layer replay/materialization analogue of the substrate
within-work totality lens (:mod:`lawvm.substrate.totality`). The substrate lens
operates over a *distributable pack*'s already-emitted ``base/`` / ``state/`` /
``proof/`` rows; this lens operates one stage earlier, over the **replay
materialization** itself: a declared UNIVERSE of expected provision units for a
work at a PIT (derived from the base/source tree) checked against the
materialized PIT tree.

As of the cross-jurisdiction generality work, the partition logic lives in the
jurisdiction-neutral core :mod:`lawvm.core.materialization_universe`; this module
is the thin FINLAND-bound facade over it (FI universe domain + the section unit
kind). The SAME core runs unmodified over a real Estonian RT replay tree — see
``tests/test_crossjur_materialization_universe.py`` and
``notes/CROSS_JURISDICTION_GENERALITY.md`` — which is the evidence the invariant
is not FI-overfitting. Existing FI imports of the names below are unchanged.

Why this is a distinct, load-bearing check (the witness)
--------------------------------------------------------
Statute ``1929/234`` (rikoslaki) silently lost sections 110-113 in a part-level
REPLACE orphan-retirement bug (``cae79014``, fixed in ``apply_runtime_support``
``48e20106``): a ``content=None`` chapter snapshot masked four live sections via
the timeline content-None supersede branch. The decisive observation is that the
**aggregate bench score did not move** while four sections vanished — aggregate-sum
totality (a corpus-wide structural/Levenshtein average) is strictly weaker than
PER-UNIT totality. A per-unit "no hidden universe" check over the section
universe would have fired a typed violation naming sections 110-113; the
aggregate did not. (Audit registry §0 generative principle; the registry's
LS-04 ``same_source_descendant_snapshot_shadow`` does NOT catch this class — it
requires a non-``None`` ancestor payload carrying the descendant path with
*different text*, so a ``content=None`` masking snapshot is excluded outright.)

The partition (audit registry §0 / substrate §23 COVERAGE_CLASSES)
------------------------------------------------------------------
Every expected unit in the declared universe is partitioned into EXACTLY one of:

* ``PRESENT`` — a live (non-tombstone) node exists at the unit's address in the
  materialized tree (accepted/owned);
* ``BENIGN_ABSENT`` — the unit is owned by a TYPED absence reason: either a
  ``lawvm_repeal_placeholder`` tombstone present in the materialized tree (the
  model's existing typed "repealed" marker), or a caller-supplied typed
  absence reason (a declared repeal / migration / out-of-scope record);
* ``TYPED_RESIDUAL`` — a caller-supplied typed residual covers the unit (the
  absence is named + typed + owned, never silent);
* ``VIOLATION`` — the unit is in the declared universe, has NO live node, NO
  tombstone, and NO typed reason: a SILENT DROP. This is the 1929/234 class.

A ``VIOLATION`` emits a :class:`MaterializationTotalityShortfall` with code
``SILENTLY_DROPPED_UNIT`` that NAMES the offending address (self-evidencing,
memory ``diagnostics_self_evidencing``).

Honesty boundary (the constructive-invariant pattern — mandatory)
-----------------------------------------------------------------
This lens NEWLY enables the query: *"no section unit in work W at PIT T is
silently dropped — every expected section is live, an owned tombstone, a
caller-declared typed absence, or a typed residual; a section that is in the
declared universe yet vanishes from materialization with no typed reason is a
named VIOLATION, not an invisible gap."* The universe is root-committed
(:attr:`UniverseSpec.universe_root`), so the set of expected units the claim
ranges over is itself checkable.

It does NOT yet compute, and MUST NOT be read as asserting:

1. **Unit kinds other than ``section``.** The v0 universe enumerates SECTION
   units only (the 1929/234 witness kind). Chapter/part container units,
   subsection/paragraph/item descendant units, and special targets
   (headings/intro) are OUT OF SCOPE — their silent-drop checks are unbuilt
   here.
2. **Derivation of the typed-absence set.** This lens CONSUMES a caller-supplied
   set of typed absence reasons (repeals/migrations); it does NOT itself prove
   that a given absence is a *legitimate* repeal vs a bug. The only
   absence-reason it derives autonomously is the in-tree
   ``lawvm_repeal_placeholder`` tombstone. A section legitimately repealed
   WITHOUT a surviving tombstone and WITHOUT a caller-supplied reason will be
   reported as a ``VIOLATION`` — that is the intended fail-loud posture (an
   undeclared absence is a finding to be triaged, not silently accepted), but it
   means a CLEAN verdict from this lens is relative to the completeness of the
   supplied typed-absence set, not an absolute "nothing was wrongly repealed."
3. **Surplus units** (a materialized section absent from the declared universe).
   The substrate ``SelectionUniverse`` checks BOTH shortfall and surplus; this
   v0 lens checks SHORTFALL only (the silent-drop direction the witness
   exercises). Surplus detection is unbuilt here.
4. **Content drift.** A unit that is PRESENT but whose materialized content
   differs from what the universe expected is NOT examined — that is the
   province of LS-17 replay-timeline consistency (``content_mismatch``), not
   this membership-totality lens.
"""

from __future__ import annotations

from collections.abc import Sequence

from lawvm.core.ir import IRNode
from lawvm.core.materialization_universe import (
    MaterializationTotalityCode,
    MaterializationTotalityError,
    MaterializationTotalityResult,
    MaterializationTotalityShortfall,
    MaterializationTotalityVerdict,
    TypedAbsenceReason,
    UniverseSpec,
    UnitDisposition,
)
from lawvm.core.materialization_universe import (
    check_materialization_totality as _check_materialization_totality,
)
from lawvm.core.materialization_universe import (
    universe_from_tree as _universe_from_tree,
)

__all__ = [
    "MaterializationTotalityCode",
    "MaterializationTotalityError",
    "MaterializationTotalityResult",
    "MaterializationTotalityShortfall",
    "MaterializationTotalityVerdict",
    "TypedAbsenceReason",
    "UniverseSpec",
    "UnitDisposition",
    "check_materialization_totality",
    "universe_from_tree",
]

# The FINLAND section-universe MapRoot domain (one per object kind). Bound here
# so FI universe roots are jurisdiction-self-describing and never collide with
# another jurisdiction's section universe.
_FI_UNIVERSE_DOMAIN = "fi.materialization_universe.section.v0"


def universe_from_tree(
    base_tree: IRNode,
    *,
    work_id: str,
    pit_date: str,
) -> UniverseSpec:
    """Derive a FINLAND section :class:`UniverseSpec` from a base/source IR tree.

    Thin facade over :func:`lawvm.core.materialization_universe.universe_from_tree`
    bound to the FI universe domain + the ``section`` unit kind. Behaviour is
    unchanged from the original FI-local implementation.
    """
    return _universe_from_tree(
        base_tree,
        work_id=work_id,
        pit_date=pit_date,
        unit_kind="section",
        domain=_FI_UNIVERSE_DOMAIN,
    )


def check_materialization_totality(
    universe: UniverseSpec,
    materialized_tree: IRNode,
    *,
    typed_absences: Sequence[TypedAbsenceReason] = (),
    typed_residual_keys: Sequence[str] = (),
) -> MaterializationTotalityResult:
    """Partition every declared FI section universe unit against the materialized tree.

    Thin facade over
    :func:`lawvm.core.materialization_universe.check_materialization_totality`
    bound to the ``section`` unit kind. Behaviour is unchanged.
    """
    return _check_materialization_totality(
        universe,
        materialized_tree,
        typed_absences=typed_absences,
        typed_residual_keys=typed_residual_keys,
        unit_kind="section",
    )
