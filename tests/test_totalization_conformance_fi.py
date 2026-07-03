"""Conformance test for Finland's θ TotalizationTable (#186, §2.3).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3.

WHAT THIS GUARDS. ``finland/totalization_table.py`` is a DECLARED spec of FI's
off-domain (precondition-failure) dispositions. Unlike NO/SE/EE/UK — which
partition every off-domain op into a REJECTED lane keyed by a ``*_replay_*`` code,
so a conformance test can drive an op through ``apply_*_ops_conserved`` and read
the rejected reason_code — FI is OBSERVATION-BASED: an off-domain occupancy is a
NON-BLOCKING observation (the op still applies), and FI accounts via a
three-outcome mutation-event ledger (applied/skipped/failed) with no
``apply_fi_ops_conserved(statute, [op])`` analogue. Routing is therefore DEFERRED
(see the table module docstring); this test binds the DECLARED table to FI's
ACTUAL runtime codes at the SOURCE level:

  For each declared cell, the code the table cites MUST appear literally at the FI
  source site the table docstring names (a ``reason_code=`` assignment, or the
  observation ``Finding.kind`` constant). If FI renames a code, moves a skip site,
  or drops the observation lane, the literal string vanishes from the source and
  THIS test fails — which is what keeps the declared table a faithful spec rather
  than dead documentation.

It also pins the FI-specific disposition SEMANTICS the table encodes: the
occupancy cells are ``NoopIdempotent`` (apply-and-observe, non-blocking), the
idempotent-skip cells are ``NoopIdempotent``, and the one genuine
destination-collision cell is a ``Reject`` (FI does NOT recover it). And it
re-pins the core-type invariants (empty-code rejection; ``lookup`` default
fallback) for the FI table.

PARALLEL-FIRST / ROUTING DEFERRED. FI's production apply path is unchanged (this
increment is byte-identical on the FI corpus). The table + this test are the
faithful DECLARED spec; the load-bearing routing (if ever wanted) is a follow-up.
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
# SOURCE binding — every declared code is FI's ACTUAL runtime code.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell",
    sorted(FI_TOTALIZATION_TABLE.rows, key=lambda c: (c[0].value, c[1].value)),
)
def test_every_declared_code_is_emitted_by_fi_source(
    cell: tuple[StructuralAction, FailureClass], fi_source: str
) -> None:
    """Bind each declared cell to FI's ACTUAL runtime disposition: the code the
    table cites must appear literally at an FI source site (a ``reason_code=``
    assignment / the observation ``Finding.kind`` constant). If FI renames or
    moves the site, the literal string vanishes and this fails — the faithful-spec
    guard, standing in for the (deferred) live routing because FI's apply path is
    rop/state-heavy and observation-based, not a reject partition."""
    disposition = FI_TOTALIZATION_TABLE.lookup(*cell)
    # FI's table holds only Reject / NoopIdempotent cells (no Recover — FI does
    # not rewrite off-domain ops); both carry a ``.code``.
    assert isinstance(disposition, (Reject, NoopIdempotent)), cell
    code = disposition.code
    if code == FI_OCCUPANCY_OBSERVATION_KIND:
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
