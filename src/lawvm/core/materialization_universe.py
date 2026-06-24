"""Jurisdiction-neutral per-unit materialization-totality lens.

This is the jurisdiction-neutral CORE of the "no hidden universe" invariant: a
declared UNIVERSE of expected provision units for a work at a PIT (derived from
the base/source IR tree) is partitioned against the materialized PIT tree so
that every expected unit is PRESENT, owned by a typed absence (tombstone /
caller reason), covered by a typed residual, or a NAMED ``VIOLATION`` (a silent
drop). Aggregate-sum totality (a corpus-wide structural/Levenshtein average) is
strictly weaker — it does not see a single unit vanish.

Why this module exists (the anti-overfitting move)
---------------------------------------------------
The invariant was first built FI-namespaced
(:mod:`lawvm.finland.materialization_totality`) against the ``1929/234``
(rikoslaki) ``content=None`` part-replace masking witness. But nothing in the
partition is Finland-specific: it ranges over the **shared** :class:`IRNode`
tree, the **shared** provision-label index (:mod:`lawvm.core.tree_ops`), and the
**shared** ``map_root`` keystone (:mod:`lawvm.substrate.roots`). The only
jurisdiction-coupled choices are (a) which IR ``kind`` string is the unit kind
(``"section"`` for both FI statutes and Estonian RT acts) and (b) the
``map_root`` domain label (so two jurisdictions' universe roots do not collide
by accident). Both are parameters of :func:`universe_from_tree` /
:class:`UniverseSpec`. The FI module is now a thin domain-bound wrapper over this
core, and the SAME core fires on a real Estonian replay tree (see
``tests/test_crossjur_materialization_universe.py``).

Honesty boundary (mandatory)
----------------------------
The generality this module demonstrates is precisely: *the membership-totality
partition is one implementation that runs unmodified over any jurisdiction whose
provision units are ``IRNode`` nodes of a single ``unit_kind`` indexed by the
shared label index.* It does NOT claim:

1. **That the silent-drop CLASS is equally reachable in every jurisdiction.**
   The ``1929/234`` masking bug is a Finnish *replay-apply* pathology
   (``content=None`` snapshot supersede). A structured-source jurisdiction whose
   materialization is a near-verbatim official-text snapshot exercises a
   different drop surface; the lens still *checks* the same invariant, but a
   CLEAN verdict there is evidence about that jurisdiction's materialization
   path, not proof the FI bug class exists there.
2. **Unit kinds other than the single ``unit_kind``.** v0 enumerates one kind
   (sections). Container / descendant / special-target units are out of scope,
   exactly as in the FI module.
3. **Derivation of the typed-absence set, surplus units, or content drift** —
   identical carve-outs to the FI module (this lens consumes typed absences,
   checks SHORTFALL only, and ignores content drift).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lawvm.core.ir import IRNode
from lawvm.core import tree_ops as _tops
from lawvm.substrate.roots import map_root

# The repealed-unit tombstone marker (the model's existing typed "repealed"
# reason). A unit present in the materialized tree carrying this attr is
# BENIGN_ABSENT (owned by a typed reason), never a silent drop. This attribute
# is jurisdiction-neutral — it is the shared IR's repeal placeholder marker.
_REPEAL_PLACEHOLDER_ATTR = "lawvm_repeal_placeholder"

# The default unit kind enumerated by the universe (the v0 witness kind, shared
# across FI statutes and EE RT acts).
DEFAULT_UNIT_KIND = "section"

# The default leaf-hash domain for the universe MapRoot. Callers SHOULD pass a
# jurisdiction-qualified domain so two jurisdictions' universe roots cannot
# collide by accident; this neutral default exists so the core is usable without
# a domain choice in tests.
DEFAULT_UNIVERSE_DOMAIN = "lawvm.materialization_universe.section.v0"


class MaterializationTotalityError(ValueError):
    """A materialization-totality object violates a v0 invariant."""


# --------------------------------------------------------------------------- #
# The partition classes + the verdict (substrate §23 COVERAGE_CLASSES mirror). #
# --------------------------------------------------------------------------- #


class UnitDisposition(enum.Enum):
    """The class each declared universe unit is partitioned into (exactly one)."""

    PRESENT = "PRESENT"
    BENIGN_ABSENT = "BENIGN_ABSENT"
    TYPED_RESIDUAL = "TYPED_RESIDUAL"
    VIOLATION = "VIOLATION"


class MaterializationTotalityVerdict(enum.Enum):
    """The lens verdict, orthogonal to integrity x certification (substrate §23).

    ``TOTAL`` — every expected unit is PRESENT (no absences at all);
    ``TOTAL_WITH_RESIDUALS`` — every expected unit is owned (PRESENT,
    BENIGN_ABSENT, or TYPED_RESIDUAL) but at least one is absent-with-a-typed
    reason (a qualified, never-silent totality);
    ``INCOMPLETE`` — at least one unit is a SILENT DROP (a VIOLATION);
    ``NOT_COMPUTED`` — the declared universe is empty.
    """

    TOTAL = "TOTAL"
    TOTAL_WITH_RESIDUALS = "TOTAL_WITH_RESIDUALS"
    INCOMPLETE = "INCOMPLETE"
    NOT_COMPUTED = "NOT_COMPUTED"


class MaterializationTotalityCode(enum.Enum):
    """The closed set of materialization-totality shortfalls (self-evidencing ``code``)."""

    SILENTLY_DROPPED_UNIT = "SILENTLY_DROPPED_UNIT"


@dataclass(frozen=True, slots=True)
class MaterializationTotalityShortfall:
    """One self-evidencing silent-drop finding (memory ``diagnostics_self_evidencing``).

    ``address_key`` names the offending unit (e.g. ``"sec_110"``); ``detail``
    embeds the human-readable address text so the finding is readable without
    re-deriving the universe.
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
    owned, never silent.
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

    ``expected_units`` maps a stable per-unit ``address_key`` to a
    human-readable ``address_text``. The keystone is :attr:`universe_root`, a
    :func:`lawvm.substrate.roots.map_root` over ``{address_key: address_text}``
    so that adding, dropping, or renaming a member changes the root — the claim
    ranges over a checkable, committed set.

    ``domain`` is the ``map_root`` domain label; it is part of the frozen value
    so two jurisdictions' universes carry distinct, self-describing roots.
    """

    work_id: str
    pit_date: str
    expected_units: Mapping[str, str]
    domain: str = DEFAULT_UNIVERSE_DOMAIN

    def __post_init__(self) -> None:
        if not isinstance(self.expected_units, Mapping):
            raise MaterializationTotalityError(
                "UniverseSpec.expected_units must be a mapping {address_key: address_text}"
            )
        if not self.domain or not isinstance(self.domain, str):
            raise MaterializationTotalityError(
                "UniverseSpec.domain must be a non-empty string"
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
        unit changes the root, so the SET the totality claim ranges over is
        itself committed and checkable.
        """
        return map_root(self.domain, dict(self.expected_units))

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


def unit_address_key(label: str) -> str:
    """Stable, non-positional address key for a unit (``"sec_110"``).

    The ``sec_`` prefix is a stable, jurisdiction-neutral token for the v0
    single-kind universe; it identifies the address space, not the jurisdiction.
    """
    return f"sec_{_tops.normalized_label_key(label)}"


def universe_from_tree(
    base_tree: IRNode,
    *,
    work_id: str,
    pit_date: str,
    unit_kind: str = DEFAULT_UNIT_KIND,
    domain: str = DEFAULT_UNIVERSE_DOMAIN,
) -> UniverseSpec:
    """Derive a single-kind :class:`UniverseSpec` from a base/source IR tree.

    Enumerates every ``unit_kind`` label in the tree (via the shared provision
    label index) and declares it an expected unit. Live (non-tombstone) base
    units only: an already-tombstoned base unit is not an EXPECTED-present unit.

    Jurisdiction-neutral: the only jurisdiction-coupled inputs are ``unit_kind``
    (which shared IR kind is the unit) and ``domain`` (the root label).
    """
    index = _tops.build_provision_label_index(base_tree)
    expected: dict[str, str] = {}
    for (kind, _norm_label), paths in index.items():
        if kind != unit_kind:
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
            key = unit_address_key(label)
            expected.setdefault(key, f"{label} §")
    return UniverseSpec(
        work_id=work_id, pit_date=pit_date, expected_units=expected, domain=domain
    )


def _live_and_tombstone_unit_keys(
    materialized_tree: IRNode,
    unit_kind: str,
) -> tuple[set[str], set[str]]:
    """Partition materialized ``unit_kind`` addresses into (live keys, tombstone keys)."""
    index = _tops.build_provision_label_index(materialized_tree)
    live: set[str] = set()
    tombstone: set[str] = set()
    for (kind, _norm_label), paths in index.items():
        if kind != unit_kind:
            continue
        for path in paths:
            node = _tops.resolve(materialized_tree, path)
            if node is None or not node.label:
                continue
            key = unit_address_key(node.label)
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
    unit_kind: str = DEFAULT_UNIT_KIND,
) -> MaterializationTotalityResult:
    """Partition every declared universe unit against the materialized tree.

    Each expected unit (see :class:`UniverseSpec`) is placed in exactly one
    :class:`UnitDisposition`:

    * **PRESENT** — a live (non-tombstone) ``unit_kind`` node exists at its address;
    * **BENIGN_ABSENT** — not live, but owned by a typed reason: an in-tree
      ``lawvm_repeal_placeholder`` tombstone, or a caller-supplied
      :class:`TypedAbsenceReason`;
    * **TYPED_RESIDUAL** — not live / no absence reason, but covered by a
      caller-supplied ``typed_residual_key``;
    * **VIOLATION** — none of the above: a SILENT DROP. Emits a
      ``SILENTLY_DROPPED_UNIT`` shortfall NAMING the address.

    Precedence: PRESENT > BENIGN_ABSENT > TYPED_RESIDUAL > VIOLATION.
    """
    live_keys, tombstone_keys = _live_and_tombstone_unit_keys(materialized_tree, unit_kind)
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
