"""Per-unit materialization-totality lens — the "no hidden universe" invariant.

This is the FINLAND-layer replay/materialization analogue of the substrate
within-work totality lens (:mod:`lawvm.substrate.totality`). The substrate lens
operates over a *distributable pack*'s already-emitted ``base/`` / ``state/`` /
``proof/`` rows; this lens operates one stage earlier, over the **replay
materialization** itself: a declared UNIVERSE of expected provision units for a
work at a PIT (derived from the base/source tree) checked against the
materialized PIT tree.

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

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lawvm.core.ir import IRNode
from lawvm.core import tree_ops as _tops
from lawvm.substrate.roots import map_root

# The repealed-section tombstone marker (the model's existing typed "repealed"
# reason). A section present in the materialized tree carrying this attr is
# BENIGN_ABSENT (owned by a typed reason), never a silent drop.
_REPEAL_PLACEHOLDER_ATTR = "lawvm_repeal_placeholder"

# The leaf-hash domain for the universe MapRoot (one per object kind).
_UNIVERSE_DOMAIN = "fi.materialization_universe.section.v0"


class MaterializationTotalityError(ValueError):
    """A materialization-totality object violates a v0 invariant."""


# --------------------------------------------------------------------------- #
# The partition classes + the verdict (substrate §23 COVERAGE_CLASSES mirror). #
# --------------------------------------------------------------------------- #


class UnitDisposition(enum.Enum):
    """The class each declared universe unit is partitioned into (exactly one).

    Mirrors the substrate four coverage classes (owned / benign / residual /
    violation) specialized to the materialization-membership question.
    """

    PRESENT = "PRESENT"
    BENIGN_ABSENT = "BENIGN_ABSENT"
    TYPED_RESIDUAL = "TYPED_RESIDUAL"
    VIOLATION = "VIOLATION"


class MaterializationTotalityVerdict(enum.Enum):
    """The lens verdict, orthogonal to integrity x certification (substrate §23).

    ``TOTAL`` — every expected unit is PRESENT (no absences at all);
    ``TOTAL_WITH_RESIDUALS`` — every expected unit is owned (PRESENT, BENIGN_ABSENT,
    or TYPED_RESIDUAL) but at least one is absent-with-a-typed-reason (a qualified,
    never-silent totality);
    ``INCOMPLETE`` — at least one unit is a SILENT DROP (a VIOLATION);
    ``NOT_COMPUTED`` — the declared universe is empty (no section units to range
    over).
    """

    TOTAL = "TOTAL"
    TOTAL_WITH_RESIDUALS = "TOTAL_WITH_RESIDUALS"
    INCOMPLETE = "INCOMPLETE"
    NOT_COMPUTED = "NOT_COMPUTED"


class MaterializationTotalityCode(enum.Enum):
    """The closed set of materialization-totality shortfalls (self-evidencing ``code``).

    v0 carries the single silent-drop code the 1929/234 witness exercises; the
    enum is the extension point for future kinds (surplus, content-drift) but
    those are out of scope (see module honesty boundary).
    """

    # A unit in the declared universe with no live node, no tombstone, and no
    # caller-supplied typed reason — the 1929/234 masked-section class.
    SILENTLY_DROPPED_UNIT = "SILENTLY_DROPPED_UNIT"


@dataclass(frozen=True, slots=True)
class MaterializationTotalityShortfall:
    """One self-evidencing silent-drop finding (memory ``diagnostics_self_evidencing``).

    ``address_key`` names the offending unit (e.g. ``"sec_110"``); ``detail``
    embeds the human-readable address text so the finding is readable without
    re-deriving the universe. Mirrors :class:`lawvm.substrate.totality.TotalityShortfall`.
    """

    code: MaterializationTotalityCode
    address_key: str
    detail: str


@dataclass(frozen=True, slots=True)
class TypedAbsenceReason:
    """A caller-declared typed reason a universe unit is legitimately absent.

    ``kind`` is a free typed token (e.g. ``"repealed"``, ``"migrated"``,
    ``"out_of_scope"``) and ``detail`` carries the owning evidence text. The
    presence of a reason for an absent unit makes that unit ``BENIGN_ABSENT`` —
    owned, never silent. (This is the seam the broader replay layer wires its
    repeal/migration events into; this lens consumes, it does not derive.)
    """

    address_key: str
    kind: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.address_key:
            raise MaterializationTotalityError(
                "TypedAbsenceReason.address_key must be non-empty"
            )
        if not self.kind:
            raise MaterializationTotalityError(
                f"TypedAbsenceReason for {self.address_key!r} must carry a non-empty "
                f"kind so the absence is OWNED, never a silent omission"
            )


# --------------------------------------------------------------------------- #
# UniverseSpec — the declared universe of expected units (root-committed).     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UniverseSpec:
    """The declared UNIVERSE of expected provision units for a work at a PIT.

    ``expected_units`` maps a stable per-unit ``address_key`` (e.g. ``"sec_110"``)
    to a human-readable ``address_text`` (e.g. ``"110 §"``). The keystone is
    :attr:`universe_root`, a :func:`lawvm.substrate.roots.map_root` over
    ``{address_key: address_text}`` so that adding, dropping, or renaming a member
    changes the root — the claim ranges over a checkable, committed set, exactly
    as the substrate ``SelectionUniverse.selection_key_root`` makes omission
    detectable.

    The v0 universe enumerates SECTION units only (see module honesty boundary).
    """

    work_id: str
    pit_date: str
    expected_units: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.expected_units, Mapping):
            raise MaterializationTotalityError(
                "UniverseSpec.expected_units must be a mapping {address_key: address_text}"
            )
        for key, text in self.expected_units.items():
            if not key or not isinstance(key, str):
                raise MaterializationTotalityError(
                    f"UniverseSpec.expected_units has a non-string/empty key {key!r}"
                )
            if not isinstance(text, str):
                raise MaterializationTotalityError(
                    f"UniverseSpec.expected_units[{key!r}] address_text must be a string, "
                    f"got {text!r}"
                )
        # Freeze to a plain dict so the root is deterministic regardless of
        # insertion order.
        object.__setattr__(self, "expected_units", dict(self.expected_units))

    @property
    def universe_root(self) -> str:
        """``MapRoot`` over ``{address_key: address_text}`` — the keystone.

        Empty universe is a valid deterministic root. Adding/dropping/renaming a
        unit changes the root, so the SET the totality claim ranges over is itself
        committed and checkable.
        """
        return map_root(_UNIVERSE_DOMAIN, dict(self.expected_units))

    def __len__(self) -> int:
        return len(self.expected_units)


@dataclass(frozen=True, slots=True)
class MaterializationTotalityResult:
    """The materialization-totality lens output.

    Carries the verdict + the typed shortfalls + the per-disposition counts +
    the keystone ``universe_root`` so a consumer can show WHY a materialization
    is not ``TOTAL`` (which units, by which class), not merely that it is not.
    """

    verdict: MaterializationTotalityVerdict
    universe_root: str
    shortfalls: tuple[MaterializationTotalityShortfall, ...] = ()
    dispositions: Mapping[str, str] = field(default_factory=dict)

    @property
    def present_count(self) -> int:
        return sum(1 for d in self.dispositions.values() if d == UnitDisposition.PRESENT.value)

    @property
    def benign_absent_count(self) -> int:
        return sum(
            1 for d in self.dispositions.values() if d == UnitDisposition.BENIGN_ABSENT.value
        )

    @property
    def typed_residual_count(self) -> int:
        return sum(
            1 for d in self.dispositions.values() if d == UnitDisposition.TYPED_RESIDUAL.value
        )

    @property
    def violation_count(self) -> int:
        return sum(1 for d in self.dispositions.values() if d == UnitDisposition.VIOLATION.value)


# --------------------------------------------------------------------------- #
# Universe derivation + the check.                                             #
# --------------------------------------------------------------------------- #


def _section_address_key(label: str) -> str:
    """Stable, non-positional address key for a section unit (``"sec_110"``)."""
    return f"sec_{_tops.normalized_label_key(label)}"


def universe_from_tree(
    base_tree: IRNode,
    *,
    work_id: str,
    pit_date: str,
) -> UniverseSpec:
    """Derive a section :class:`UniverseSpec` from a base/source IR tree.

    Enumerates every SECTION label in the tree (via the shared provision label
    index) and declares it an expected unit. This is the "universe of expected
    provision units derived from the source/base tree" the §0 per-unit-totality
    principle ranges over. Live (non-tombstone) base sections only: an
    already-tombstoned base section is not an EXPECTED-present unit.
    """
    index = _tops.build_provision_label_index(base_tree)
    expected: dict[str, str] = {}
    for (kind, _norm_label), paths in index.items():
        if kind != "section":
            continue
        for path in paths:
            node = _tops.resolve(base_tree, path)
            if node is None:
                continue
            if node.attrs.get(_REPEAL_PLACEHOLDER_ATTR) == "1":
                continue
            label = node.label or ""
            if not label:
                continue
            key = _section_address_key(label)
            expected.setdefault(key, f"{label} §")
    return UniverseSpec(work_id=work_id, pit_date=pit_date, expected_units=expected)


def _live_and_tombstone_section_keys(
    materialized_tree: IRNode,
) -> tuple[set[str], set[str]]:
    """Partition materialized section addresses into (live keys, tombstone keys)."""
    index = _tops.build_provision_label_index(materialized_tree)
    live: set[str] = set()
    tombstone: set[str] = set()
    for (kind, _norm_label), paths in index.items():
        if kind != "section":
            continue
        for path in paths:
            node = _tops.resolve(materialized_tree, path)
            if node is None or not node.label:
                continue
            key = _section_address_key(node.label)
            if node.attrs.get(_REPEAL_PLACEHOLDER_ATTR) == "1":
                tombstone.add(key)
            else:
                live.add(key)
    return live, tombstone


def check_materialization_totality(
    universe: UniverseSpec,
    materialized_tree: IRNode,
    *,
    typed_absences: Sequence[TypedAbsenceReason] = (),
    typed_residual_keys: Sequence[str] = (),
) -> MaterializationTotalityResult:
    """Partition every declared universe unit against the materialized tree.

    Each expected unit (see :class:`UniverseSpec`) is placed in exactly one
    :class:`UnitDisposition`:

    * **PRESENT** — a live (non-tombstone) section node exists at its address;
    * **BENIGN_ABSENT** — not live, but owned by a typed reason: an in-tree
      ``lawvm_repeal_placeholder`` tombstone, or a caller-supplied
      :class:`TypedAbsenceReason`;
    * **TYPED_RESIDUAL** — not live / no absence reason, but covered by a
      caller-supplied ``typed_residual_key`` (the gap is named + typed + owned);
    * **VIOLATION** — none of the above: a SILENT DROP. Emits a
      ``SILENTLY_DROPPED_UNIT`` shortfall NAMING the address (the 1929/234 class).

    Precedence (a unit can match more than one owning condition; we record the
    strongest *positive* ownership): PRESENT > BENIGN_ABSENT > TYPED_RESIDUAL >
    VIOLATION. A unit absent from materialization but carrying both a tombstone
    and a residual is BENIGN_ABSENT (the tombstone is the stronger, in-tree
    ownership).
    """
    live_keys, tombstone_keys = _live_and_tombstone_section_keys(materialized_tree)
    absence_keys = {reason.address_key for reason in typed_absences}
    residual_keys = set(typed_residual_keys)

    dispositions: dict[str, str] = {}
    shortfalls: list[MaterializationTotalityShortfall] = []

    for key, address_text in universe.expected_units.items():
        if key in live_keys:
            dispositions[key] = UnitDisposition.PRESENT.value
        elif key in tombstone_keys or key in absence_keys:
            dispositions[key] = UnitDisposition.BENIGN_ABSENT.value
        elif key in residual_keys:
            dispositions[key] = UnitDisposition.TYPED_RESIDUAL.value
        else:
            dispositions[key] = UnitDisposition.VIOLATION.value
            shortfalls.append(
                MaterializationTotalityShortfall(
                    code=MaterializationTotalityCode.SILENTLY_DROPPED_UNIT,
                    address_key=key,
                    detail=(
                        f"expected provision unit {address_text!r} ({key}) is in the "
                        f"declared universe of work {universe.work_id!r} at PIT "
                        f"{universe.pit_date!r} but is ABSENT from the materialized tree "
                        f"with no live node, no repeal tombstone, and no typed absence "
                        f"reason — a SILENT DROP (per-unit materialization totality "
                        f"violation; aggregate-sum totality would not see it)"
                    ),
                )
            )

    if not universe.expected_units:
        verdict = MaterializationTotalityVerdict.NOT_COMPUTED
    elif shortfalls:
        verdict = MaterializationTotalityVerdict.INCOMPLETE
    elif any(d != UnitDisposition.PRESENT.value for d in dispositions.values()):
        verdict = MaterializationTotalityVerdict.TOTAL_WITH_RESIDUALS
    else:
        verdict = MaterializationTotalityVerdict.TOTAL

    return MaterializationTotalityResult(
        verdict=verdict,
        universe_root=universe.universe_root,
        shortfalls=tuple(shortfalls),
        dispositions=dispositions,
    )
