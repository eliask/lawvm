"""Conformance test for Finland's θ TotalizationTable (#186, §2.3).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3.

WHAT THIS GUARDS. ``finland/totalization_table.py`` models FI's off-domain
(precondition-failure) dispositions. Unlike NO/SE/EE/UK — which partition every
off-domain op into a REJECTED lane keyed by a ``*_replay_*`` code, so a
conformance test can drive an op through ``apply_*_ops_conserved`` and read the
rejected reason_code — FI is OBSERVATION-BASED for its occupancy lane (an
off-domain occupancy is a NON-BLOCKING observation; the op still applies) and
accounts via a three-outcome mutation-event ledger (applied/skipped/failed) with
no ``apply_fi_ops_conserved(statute, [op])`` analogue. This test binds the table
to FI's ACTUAL runtime codes with TWO regimes (see the per-cell test docstring):

  * ROUTED cells (#206 tail): the three RENUMBER/REPEAL precondition-failure cells
    are now LOAD-BEARING — ``restructure_plan.py`` and ``apply_typed_dispatch.py``
    read ``FI_TOTALIZATION_TABLE.lookup(action, failure).code`` and emit it as the
    ``reason_code=``, so the table is the PRODUCTION IMPORTER. The binding is the
    ``.lookup(...)`` CALL: drop the routing and the call vanishes → this fails.
  * DECLARED-only cell (the occupancy observation): FI's off-``allowed_from`` lane
    is a non-blocking observation emitted for any action outside its allowed
    occupancy (not keyed on one ``(action, failure)`` cell), so it is not
    byte-safely routable through a single table cell. It stays DECLARED, bound to
    the ``kind="..."`` Finding literal apply_policy.py emits.

  Either way, if FI renames a code / drops a routing / moves the observation lane,
  the bound token vanishes from the source and THIS test fails — keeping the table
  a faithful spec (for the declared cell) and the single source (for routed ones).

It also pins the FI-specific disposition SEMANTICS the table encodes: the
occupancy cells are ``NoopIdempotent`` (apply-and-observe, non-blocking), the
idempotent-skip cells are ``NoopIdempotent``, and the one genuine
destination-collision cell is a ``Reject`` (FI does NOT recover it). And it
re-pins the core-type invariants (empty-code rejection; ``lookup`` default
fallback) for the FI table.

ROUTING STATUS. The three RENUMBER/REPEAL cells are ROUTED (θ is their single
source) and byte-identical on the FI corpus (SHA-verified). The occupancy cell
stays DECLARED (routing N-A: not a single-cell partition). See the table module
docstring for the per-site routing rationale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.semantic_types import StructuralAction
from lawvm.core.totalization import (
    FailureClass,
    NoopIdempotent,
    Reject,
    TotalizationTable,
)
from lawvm.finland.totalization_table import (
    FI_OCCUPANCY_OBSERVATION_KIND,
    FI_TOTALIZATION_TABLE,
    build_fi_totalization_table,
)

_FI_SRC = Path(__file__).resolve().parents[1] / "src" / "lawvm" / "finland"


def _fi_source_blob() -> str:
    """Concatenate every FI ``.py`` source (cached per-process by the fixture)."""
    parts: list[str] = []
    for path in sorted(_FI_SRC.glob("*.py")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def fi_source() -> str:
    return _fi_source_blob()


# ---------------------------------------------------------------------------
# Table shape / core-type invariants (do not touch the FI corpus).
# ---------------------------------------------------------------------------


def test_table_is_the_singleton_and_rebuildable() -> None:
    """The module singleton equals a fresh build (pure, deterministic)."""
    assert FI_TOTALIZATION_TABLE.jurisdiction == "fi"
    rebuilt = build_fi_totalization_table()
    assert rebuilt.jurisdiction == "fi"
    assert rebuilt.rows == FI_TOTALIZATION_TABLE.rows
    assert rebuilt.default == FI_TOTALIZATION_TABLE.default


def test_default_is_the_strict_floor_not_a_real_cell() -> None:
    """§2.3 strict-default floor. FI's real off-domain cells never hit it (each
    site names its own reason_code / the occupancy observation); it exists only so
    the table is total over the grid."""
    assert isinstance(FI_TOTALIZATION_TABLE.default, Reject)
    assert FI_TOTALIZATION_TABLE.default.code == "fi_replay_skipped_unspecified"
    # An unlisted cell falls back to the strict default rather than raising.
    fallback = FI_TOTALIZATION_TABLE.lookup(
        StructuralAction.META, FailureClass.PAYLOAD_MISSING
    )
    assert fallback is FI_TOTALIZATION_TABLE.default


def test_empty_code_disposition_is_unrepresentable() -> None:
    """Core-type invariant re-pinned for FI: a disposition with an empty code
    fails loud at construction (a codeless off-domain disposition is the boundary
    leak the algebra closes)."""
    with pytest.raises(ValueError):
        NoopIdempotent("")
    with pytest.raises(ValueError):
        Reject("")
    with pytest.raises(ValueError):
        TotalizationTable(jurisdiction="", rows={})


# ---------------------------------------------------------------------------
# Declared disposition SEMANTICS — FI's observation model.
# ---------------------------------------------------------------------------


def test_occupancy_cells_are_apply_and_observe_noops() -> None:
    """§2.3 FI column: INSERT target_occupied / REPLACE target_absent are
    apply-and-observe — modeled as NoopIdempotent citing the observation kind
    (non-blocking; the op applies)."""
    for cell in (
        (StructuralAction.INSERT, FailureClass.TARGET_OCCUPIED),
        (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT),
    ):
        disposition = FI_TOTALIZATION_TABLE.lookup(*cell)
        assert isinstance(disposition, NoopIdempotent), cell
        assert disposition.code == FI_OCCUPANCY_OBSERVATION_KIND, cell


def test_idempotent_skip_cells_are_noops() -> None:
    """REPEAL of a subsection whose parent is already absent, and a RENUMBER whose
    source == destination, are idempotent no-ops (outcome=skipped)."""
    repeal = FI_TOTALIZATION_TABLE.lookup(
        StructuralAction.REPEAL, FailureClass.TARGET_ABSENT
    )
    assert isinstance(repeal, NoopIdempotent)
    assert repeal.code == "idempotent_repeal_parent_section_absent"

    self_relabel = FI_TOTALIZATION_TABLE.lookup(
        StructuralAction.RENUMBER, FailureClass.SELF_RELABEL
    )
    assert isinstance(self_relabel, NoopIdempotent)
    assert self_relabel.code == "self_relabel_noop"


def test_destination_collision_is_the_one_genuine_reject() -> None:
    """A grouped RENUMBER whose destination label is held by a DIFFERENT occupant
    FAILS — FI does not recover it (no scaffold-relabel-over-occupant)."""
    disposition = FI_TOTALIZATION_TABLE.lookup(
        StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED
    )
    assert isinstance(disposition, Reject)
    assert disposition.code == "destination_occupied"


def test_self_relabel_and_dest_occupied_are_distinct_classes() -> None:
    """The additive SELF_RELABEL class (source==destination) is genuinely distinct
    from DEST_OCCUPIED (a different node holds the label) — different codes,
    different dispositions (noop vs reject)."""
    self_relabel = FI_TOTALIZATION_TABLE.lookup(
        StructuralAction.RENUMBER, FailureClass.SELF_RELABEL
    )
    dest_occupied = FI_TOTALIZATION_TABLE.lookup(
        StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED
    )
    assert self_relabel != dest_occupied
    assert isinstance(self_relabel, NoopIdempotent)
    assert isinstance(dest_occupied, Reject)


# ---------------------------------------------------------------------------
# SOURCE / ROUTING binding — every declared code is FI's ACTUAL runtime code.
# ---------------------------------------------------------------------------

#: The three disposition cells FI now ROUTES through the table at production
#: apply sites (#206 tail): the table is the SINGLE SOURCE of each code —
#: ``restructure_plan.py`` (RENUMBER self-relabel / dest-occupied) and
#: ``apply_typed_dispatch.py`` (REPEAL of a subsection whose parent is absent)
#: read ``FI_TOTALIZATION_TABLE.lookup(action, failure_class).code`` and emit it
#: as the ``reason_code=``. The inline ``reason_code="<literal>"`` therefore no
#: longer appears at the site (that is the POINT — the table imported it), so
#: these cells bind to the LOOKUP CALL, not to a source literal.
_FI_ROUTED_CELLS: frozenset[tuple[StructuralAction, FailureClass]] = frozenset(
    {
        (StructuralAction.RENUMBER, FailureClass.SELF_RELABEL),
        (StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED),
        (StructuralAction.REPEAL, FailureClass.TARGET_ABSENT),
    }
)


def _lookup_call_literals(cell: tuple[StructuralAction, FailureClass]) -> tuple[str, str]:
    """The two whitespace-normalized forms a ``FI_TOTALIZATION_TABLE.lookup``
    call for ``cell`` can take in FI source (one-line and two-line arg layout)."""
    action, klass = cell
    a = f"StructuralAction.{action.name}"
    k = f"FailureClass.{klass.name}"
    one_line = f"FI_TOTALIZATION_TABLE.lookup({a}, {k})"
    two_line = f"FI_TOTALIZATION_TABLE.lookup( {a}, {k} )"
    return one_line, two_line


@pytest.mark.parametrize(
    "cell",
    sorted(FI_TOTALIZATION_TABLE.rows, key=lambda c: (c[0].value, c[1].value)),
)
def test_every_declared_code_is_emitted_by_fi_source(
    cell: tuple[StructuralAction, FailureClass], fi_source: str
) -> None:
    """Bind each declared cell to FI's ACTUAL runtime disposition.

    Two binding regimes, one per cell class:

    * ROUTED cells (``_FI_ROUTED_CELLS``): the table is the PRODUCTION IMPORTER —
      the FI apply site calls ``FI_TOTALIZATION_TABLE.lookup(action, failure)`` and
      emits the returned ``.code``. The binding is the LOOKUP CALL itself (the
      inline ``reason_code="<literal>"`` is gone precisely because the table now
      supplies it). If FI drops the routing, the lookup call vanishes and this
      fails — the LOAD-BEARING guard (θ is the single source of the disposition).

    * DECLARED-only cell (the occupancy observation): FI's off-``allowed_from``
      lane is a NON-BLOCKING observation emitted for any action outside its
      allowed occupancy (not keyed on one ``(action, failure)`` cell), so it is
      not byte-safely routable through a single table cell. It stays DECLARED,
      bound to the ``kind="..."`` Finding literal apply_policy.py emits."""
    disposition = FI_TOTALIZATION_TABLE.lookup(*cell)
    # FI's table holds only Reject / NoopIdempotent cells (no Recover — FI does
    # not rewrite off-domain ops); both carry a ``.code``.
    assert isinstance(disposition, (Reject, NoopIdempotent)), cell
    code = disposition.code
    if cell in _FI_ROUTED_CELLS:
        # A whitespace-tolerant scan for the lookup call: strip runs of
        # whitespace so a multi-line call (arg-per-line) still matches.
        normalized = " ".join(fi_source.split())
        one_line, two_line = _lookup_call_literals(cell)
        assert one_line in normalized or two_line in normalized, (
            f"routed cell {cell} is not sourced from a "
            f"FI_TOTALIZATION_TABLE.lookup({cell[0].name}, {cell[1].name}) call in "
            "FI source — θ is no longer the production importer for this code."
        )
    elif code == FI_OCCUPANCY_OBSERVATION_KIND:
        # The observation lane cites the Finding.kind (apply_policy.py), which is
        # emitted as a kind="..." literal rather than a reason_code= assignment.
        assert f'kind="{code}"' in fi_source, (
            f"declared occupancy observation kind {code!r} for cell {cell} is not "
            "emitted by any FI source Finding — the table has drifted from apply."
        )
    else:
        assert f'reason_code="{code}"' in fi_source, (
            f"declared code {code!r} for cell {cell} is not emitted at any FI "
            "reason_code= source site — the declared table has drifted from apply."
        )


def test_strict_default_code_is_not_a_live_reason_code(fi_source: str) -> None:
    """The strict-default floor is a synthetic type-completeness code, NOT a real
    FI emit site — asserting it is absent from the source proves the default is a
    floor and not silently masking a real (un-declared) skip site."""
    assert 'reason_code="fi_replay_skipped_unspecified"' not in fi_source
