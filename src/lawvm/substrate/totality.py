"""The within-work totality LENS — a THIRD verdict axis (design §23, §23.x §24.1).

Today the checker reports two orthogonal axes: ``integrity`` (did the bytes /
roots / structure verify) × ``certification`` (the legal quality of a clean
pass). This module adds the third, orthogonal to both:

    **totality** — is this a COMPLETE account of the work's own declared universe?

Integrity says *nothing was tampered*; certification says *the legal assertion
is clean / qualified / blocked*; **totality says nothing was silently omitted.**
This is *täyslaskenta* (full accounting) lifted to the package boundary: the pack
must account for everything it touches, or name precisely — with a TYPE — what it
cannot.

**The relativity principle (design §23.x, load-bearing).** Totality is NEVER
"this contains all law." It is "this is a complete account **relative to the
work's own declared universe**" — the address-tree the pack itself carries in
``base/`` and the selection universe it commits to in ``state/``. A within-work
``TOTAL`` verdict makes no claim about whether the corpus contains every work;
that is the *corpus*-level totality (``lawvm.corpus_totality.v0``), a separate
object with its own ``closed_world_claim``.

The verdict is one of three (design §23):

* ``TOTAL`` — every addressable node is owned by a selection entry, every
  provision is classified into exactly one coverage class with no unclassified
  remainder, and there are no residuals (a fully clean, gap-free account);
* ``TOTAL_WITH_RESIDUALS`` — the same completeness, but typed residuals exist
  (every gap is named + typed + owned). A qualified totality, never silently
  ``TOTAL``;
* ``INCOMPLETE`` — a SILENT gap was found: an addressable node with neither a
  selection entry nor a typed non-selection reason, an unclassified coverage
  remainder, or an untyped residual. A pack that cannot prove totality MUST say
  so — the same fail-loud principle the engine enforces, now a portable,
  server-lessly-checkable property of the distributed artifact.

The lens is **computed from what the pack already carries** — no new emitted
object is required for the within-work verdict (the ``corpus_totality`` object is
the corpus-level companion). It reuses the L0.6 ``selection_universe`` idea (rows
== declared universe) and extends it from "rows match the declared universe" to
"the declared universe itself covers every addressable node in the address tree,
or names a typed non-selection reason."
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from lawvm.substrate.canonical_json import JsonValue

# --------------------------------------------------------------------------- #
# The totality verdict + its typed shortfalls.                                #
# --------------------------------------------------------------------------- #


class TotalityVerdict(enum.Enum):
    """The third lens (design §23) — orthogonal to integrity × certification.

    ``TOTAL`` / ``TOTAL_WITH_RESIDUALS`` are the two complete states (the latter
    qualified by typed, owned gaps); ``INCOMPLETE`` means a SILENT gap was found
    (an unowned addressable node, an unclassified coverage remainder, or an
    untyped residual). ``NOT_COMPUTED`` is emitted when the pack carries no
    within-work address tree / selection layer to compute totality over (e.g. a
    corpus-only shared-leaf pack), exactly as ``certification`` is
    ``NOT_COMPUTED`` when there is no legal-state detail to fold.
    """

    TOTAL = "TOTAL"
    TOTAL_WITH_RESIDUALS = "TOTAL_WITH_RESIDUALS"
    INCOMPLETE = "INCOMPLETE"
    NOT_COMPUTED = "NOT_COMPUTED"


class TotalityShortfallCode(enum.Enum):
    """The closed set of totality shortfalls (the self-evidencing ``code``).

    Each is a distinct way a pack fails to be a complete account; every one is
    a SILENT-gap finding (design §23) and forces ``INCOMPLETE``.
    """

    # An addressable node in base/ with no selection entry AND no typed reason.
    UNOWNED_ADDRESSABLE_NODE = "UNOWNED_ADDRESSABLE_NODE"
    # A provision left in no coverage class (unclassified remainder).
    UNCLASSIFIED_COVERAGE_REMAINDER = "UNCLASSIFIED_COVERAGE_REMAINDER"
    # A residual with no ``kind`` (a silent / untyped drop).
    UNTYPED_RESIDUAL = "UNTYPED_RESIDUAL"


@dataclass(frozen=True, slots=True)
class TotalityShortfall:
    """One self-evidencing totality shortfall (memory ``diagnostics_self_evidencing``).

    ``subject`` carries the offending address / coverage tag / residual id; the
    ``detail`` embeds the offending value text so the finding is readable without
    the source. Mirrors :class:`lawvm.substrate.checker.TypedViolation`.
    """

    code: TotalityShortfallCode
    criterion: str
    subject: str
    detail: str

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "code": self.code.value,
            "criterion": self.criterion,
            "subject": self.subject,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class TotalityResult:
    """The totality lens output (folded into the checker's verdict, design §23).

    Carries the verdict + the typed shortfalls + the three criterion sub-results
    (each a small audit count) so a UI can show *why* a pack is not ``TOTAL``,
    not just *that* it is. ``residual_kinds`` is the sorted, deduped set of typed
    residual kinds present (what qualifies a ``TOTAL_WITH_RESIDUALS``).
    """

    verdict: TotalityVerdict
    shortfalls: tuple[TotalityShortfall, ...] = ()
    addressable_nodes: int = 0
    owned_nodes: int = 0
    typed_non_selection_nodes: int = 0
    residual_count: int = 0
    residual_kinds: tuple[str, ...] = ()
    coverage_classes: tuple[str, ...] = ()

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "verdict": self.verdict.value,
            "shortfalls": [s.to_canonical_dict() for s in self.shortfalls],
            "addressable_nodes": self.addressable_nodes,
            "owned_nodes": self.owned_nodes,
            "typed_non_selection_nodes": self.typed_non_selection_nodes,
            "residual_count": self.residual_count,
            "residual_kinds": list(self.residual_kinds),
            "coverage_classes": list(self.coverage_classes),
        }


# --------------------------------------------------------------------------- #
# Vocabulary.                                                                  #
# --------------------------------------------------------------------------- #

# Selection statuses that OWN a node WITHOUT selecting a version — they are a
# *typed* non-selection reason (the address is accounted, just not text-bearing).
# A node carrying one of these is owned-by-type, never a silent gap.
_TYPED_NON_SELECTION_STATUSES: frozenset[str] = frozenset(
    {
        "absent",
        "out_of_scope",
        "unsupported_profile",
        "ambiguous_missing_scope",
        "blocked",
    }
)

# The four coverage classes a total pack partitions every provision into
# (design §23: "every provision falls in exactly one class — owned / benign /
# residual / violation — with no unclassified remainder").
COVERAGE_CLASSES: frozenset[str] = frozenset(
    {"owned", "benign", "residual", "violation"}
)

_SCHEMA_ADDRESS_NODE = "lawvm.address_node.v1"
_SCHEMA_SELECTION_ROW = "lawvm.selection_row.v1"
_SCHEMA_RESIDUAL = "lawvm.residual.v1"
_SCHEMA_COVERAGE = "lawvm.coverage_row.v1"


def _bodies(rows: Sequence[Mapping[str, JsonValue]], schema: str) -> list[Mapping[str, JsonValue]]:
    out: list[Mapping[str, JsonValue]] = []
    for row in rows:
        body = row.get("object")
        if isinstance(body, Mapping):
            typed = cast(Mapping[str, JsonValue], body)
            if typed.get("schema") == schema:
                out.append(typed)
    return out


# --------------------------------------------------------------------------- #
# The lens.                                                                    #
# --------------------------------------------------------------------------- #


def compute_totality(
    base_rows: Sequence[Mapping[str, JsonValue]],
    state_rows: Sequence[Mapping[str, JsonValue]],
    proof_rows: Sequence[Mapping[str, JsonValue]],
) -> TotalityResult:
    """Compute the within-work :class:`TotalityVerdict` from the pack's own rows.

    Three orthogonal criteria, each a silent-gap fire drill (design §23):

    1. **universe-completeness (level D, the core):** every ``address_node`` in
       ``base/`` must be OWNED — either covered by a ``selected`` selection_row
       at that ``address_id``, or carrying a TYPED non-selection reason
       (``absent`` / ``out_of_scope`` / ``blocked`` / …). A node with neither a
       selection entry nor a typed reason is a silent gap →
       ``UNOWNED_ADDRESSABLE_NODE`` → INCOMPLETE. This extends the L0.6
       "rows == declared universe" check to "the declared universe covers the
       address tree."
    2. **coverage-exhaustiveness:** every coverage_row's ``coverage_class`` is one
       of the four closed classes; an unknown / blank class is an unclassified
       remainder → ``UNCLASSIFIED_COVERAGE_REMAINDER`` → INCOMPLETE.
    3. **residual-typedness:** every ``residual`` carries a non-empty ``kind``; an
       untyped residual is a silent drop → ``UNTYPED_RESIDUAL`` → INCOMPLETE.
       The mere PRESENCE of (typed) residuals qualifies a complete pack to
       ``TOTAL_WITH_RESIDUALS`` — never silently ``TOTAL``.

    When the pack carries no address tree (no ``address_node`` rows) the verdict
    is ``NOT_COMPUTED`` — there is no within-work universe to be total over
    (a corpus shared-leaf pack is the case; its totality is the corpus object).
    """
    address_nodes = _bodies(base_rows, _SCHEMA_ADDRESS_NODE)
    selection_rows = _bodies(state_rows, _SCHEMA_SELECTION_ROW)
    residuals = _bodies(proof_rows, _SCHEMA_RESIDUAL)
    coverage_rows = _bodies(proof_rows, _SCHEMA_COVERAGE)

    if not address_nodes:
        # No within-work address tree → no within-work universe to be total over.
        return TotalityResult(verdict=TotalityVerdict.NOT_COMPUTED)

    shortfalls: list[TotalityShortfall] = []

    # -- (1) universe-completeness: every addressable node owned -------------- #
    owned_by_selection: set[str] = set()
    owned_by_typed_reason: set[str] = set()
    for row in selection_rows:
        addr = row.get("address_id")
        status = row.get("status")
        if not isinstance(addr, str):
            continue
        if status == "selected":
            owned_by_selection.add(addr)
        elif isinstance(status, str) and status in _TYPED_NON_SELECTION_STATUSES:
            owned_by_typed_reason.add(addr)

    addressable_ids: list[str] = []
    for node in address_nodes:
        nid = node.get("struct_node_id")
        if isinstance(nid, str):
            addressable_ids.append(nid)

    owned = 0
    typed_non_selection = 0
    for nid in addressable_ids:
        if nid in owned_by_selection:
            owned += 1
        elif nid in owned_by_typed_reason:
            typed_non_selection += 1
        else:
            # A node with NO selection entry AND no typed reason — a silent gap.
            addr_path = _address_path_of(address_nodes, nid)
            shortfalls.append(
                TotalityShortfall(
                    code=TotalityShortfallCode.UNOWNED_ADDRESSABLE_NODE,
                    criterion="universe_completeness",
                    subject=nid,
                    detail=(
                        f"addressable node {addr_path!r} ({nid}) has no selection "
                        f"entry and no typed non-selection reason — a SILENT GAP "
                        f"(the declared universe does not cover the address tree)"
                    ),
                )
            )

    # -- (2) coverage-exhaustiveness: no unclassified remainder --------------- #
    coverage_classes_seen: set[str] = set()
    for cov in coverage_rows:
        klass = cov.get("coverage_class")
        if not isinstance(klass, str) or klass not in COVERAGE_CLASSES:
            shortfalls.append(
                TotalityShortfall(
                    code=TotalityShortfallCode.UNCLASSIFIED_COVERAGE_REMAINDER,
                    criterion="coverage_exhaustiveness",
                    subject=str(klass),
                    detail=(
                        f"coverage_row declares coverage_class {klass!r} which is not "
                        f"one of the four closed classes {sorted(COVERAGE_CLASSES)!r} "
                        f"(an unclassified remainder is a silent omission)"
                    ),
                )
            )
        elif isinstance(klass, str):
            coverage_classes_seen.add(klass)

    # -- (3) residual-typedness: every residual carries a kind ---------------- #
    residual_kinds: set[str] = set()
    for res in residuals:
        kind = res.get("kind")
        if not isinstance(kind, str) or not kind:
            rid = res.get("residual_id")
            shortfalls.append(
                TotalityShortfall(
                    code=TotalityShortfallCode.UNTYPED_RESIDUAL,
                    criterion="residual_typedness",
                    subject=str(rid) if isinstance(rid, str) else "<no residual_id>",
                    detail=(
                        "residual carries no typed ``kind`` (an untyped residual is a "
                        "SILENT DROP — every gap must be typed and owned)"
                    ),
                )
            )
        else:
            residual_kinds.add(kind)

    # -- fold ----------------------------------------------------------------- #
    if shortfalls:
        verdict = TotalityVerdict.INCOMPLETE
    elif residuals:
        # Complete, but typed residuals exist → qualified, NEVER silently TOTAL.
        verdict = TotalityVerdict.TOTAL_WITH_RESIDUALS
    else:
        verdict = TotalityVerdict.TOTAL

    return TotalityResult(
        verdict=verdict,
        shortfalls=tuple(shortfalls),
        addressable_nodes=len(addressable_ids),
        owned_nodes=owned,
        typed_non_selection_nodes=typed_non_selection,
        residual_count=len(residuals),
        residual_kinds=tuple(sorted(residual_kinds)),
        coverage_classes=tuple(sorted(coverage_classes_seen)),
    )


def _address_path_of(
    address_nodes: Sequence[Mapping[str, JsonValue]], struct_node_id: str
) -> str:
    """Best-effort human address path for a struct_node_id (self-evidencing detail)."""
    for node in address_nodes:
        if node.get("struct_node_id") == struct_node_id:
            path = node.get("address_path")
            if isinstance(path, str):
                return path
    return struct_node_id
