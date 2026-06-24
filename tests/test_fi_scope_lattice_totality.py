"""Tests for the SCOPE-01/02 scope-lattice totality sweep.

The sweep (``scope_lattice_totality.sweep_scope_lattice``) is a READ-ONLY audit
over the finished replay output. For each address it groups versions by the
production precedence-rail rank key ``(variant_kind, effective, enacted,
source_statute)``; a rank-key group holding two or more rows with DISTINCT legal
content is a co-effective collision the precedence rail does NOT resolve
(SCOPE-01) — and if no disjoint scope predicate admits the overlap (SCOPE-02),
the collision is ``SCOPE.OVERLAP_WITHOUT_DISJOINT_SCOPE``. It mutates nothing.

Carrier note: over the FI corpus no version carries a populated scope predicate,
so SCOPE-01's full disjointness lattice is PART with a named missing carrier; the
precedence-rail residual checked here is the implementable arm.
"""

from __future__ import annotations

from typing import Literal

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    ProvisionTimeline,
    ProvisionVersion,
    ScopePredicate,
)
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.legal_surface.scope_lattice_totality import (
    SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE,
    ScopeOverlapFinding,
    sweep_scope_lattice,
)
from lawvm.finland.replay_products import ReplayProducts
from lawvm.finland.statute import ReplayState


_ADDR = LegalAddress(path=(("section", "9"),))
_SOURCE = OperationSource(statute_id="0001/2024", effective="2024-01-01")


def _state() -> ReplayState:
    return ReplayState(ir=IRNode(kind=IRNodeKind.BODY))


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label="9", text=text)


def _version(
    *,
    text: str,
    effective: str = "2024-01-01",
    enacted: str = "2024-01-01",
    variant_kind: Literal["permanent", "temporary"] = "permanent",
    source: OperationSource | None = _SOURCE,
    applicability: list[ScopePredicate] | None = None,
) -> ProvisionVersion:
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        variant_kind=variant_kind,
        content=_content(text),
        source=source,
        applicability=applicability or [],
    )


def _products(
    *,
    timelines: dict[LegalAddress, ProvisionTimeline] | None,
) -> ReplayProducts:
    state = _state()
    return ReplayProducts(
        replay_fold_state=state,
        materialized_state=state,
        timelines=timelines,
    )


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_scope_overlap_code_registered_as_nonblocking_observation() -> None:
    spec = FINDING_REGISTRY[SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE]
    assert spec.role == "observation"
    assert spec.default_enforcement == "warn"


# ---------------------------------------------------------------------------
# SCOPE-01: co-effective equal-rank collision with distinct content fires
# ---------------------------------------------------------------------------


def test_equal_rank_distinct_content_collision_fires() -> None:
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[_version(text="variant A"), _version(text="variant B distinct")],
    )
    findings = sweep_scope_lattice(_products(timelines={_ADDR: timeline}))
    assert len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, ScopeOverlapFinding)
    assert finding.code == SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE
    assert finding.address == str(_ADDR)
    assert finding.effective == "2024-01-01"
    assert finding.candidate_count == 2
    assert finding.left_content_hash != finding.right_content_hash
    assert not finding.scope_disjoint
    # self-evidencing detail names the address + the two distinct hashes.
    assert str(_ADDR) in finding.detail
    assert finding.left_content_hash[:12] in finding.detail
    assert finding.right_content_hash[:12] in finding.detail


def test_same_rank_same_content_is_silent() -> None:
    # Duplicate content under the same rank key -> dedupe collapses it; no
    # co-effective ambiguity to surface.
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[_version(text="identical"), _version(text="identical")],
    )
    assert sweep_scope_lattice(_products(timelines={_ADDR: timeline})) == ()


def test_distinct_rank_keys_are_resolved_by_precedence_rail() -> None:
    # Different effective dates -> lex posterior separates them -> silent.
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[
            _version(text="variant A", effective="2024-01-01"),
            _version(text="variant B distinct", effective="2025-01-01"),
        ],
    )
    assert sweep_scope_lattice(_products(timelines={_ADDR: timeline})) == ()


def test_distinct_source_statute_is_resolved_by_precedence_rail() -> None:
    # Same effective/enacted but distinct source statutes -> distinct rank keys
    # (lex posterior by source) -> precedence rail resolves -> silent.
    other_source = OperationSource(statute_id="0002/2024", effective="2024-01-01")
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[
            _version(text="variant A"),
            _version(text="variant B distinct", source=other_source),
        ],
    )
    assert sweep_scope_lattice(_products(timelines={_ADDR: timeline})) == ()


# ---------------------------------------------------------------------------
# SCOPE-02: a disjoint scope predicate admits the co-effective overlap
# ---------------------------------------------------------------------------


def test_disjoint_territory_predicates_admit_overlap_silent() -> None:
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[
            _version(
                text="variant A",
                applicability=[
                    ScopePredicate(dimension="territory", includes=frozenset({"mainland"}))
                ],
            ),
            _version(
                text="variant B distinct",
                applicability=[
                    ScopePredicate(dimension="territory", includes=frozenset({"aland"}))
                ],
            ),
        ],
    )
    assert sweep_scope_lattice(_products(timelines={_ADDR: timeline})) == ()


def test_overlapping_territory_predicates_still_fire() -> None:
    # Shared dimension with INTERSECTING includes -> not disjoint -> the overlap
    # is not admitted by scope -> still a collision.
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[
            _version(
                text="variant A",
                applicability=[
                    ScopePredicate(
                        dimension="territory", includes=frozenset({"mainland", "aland"})
                    )
                ],
            ),
            _version(
                text="variant B distinct",
                applicability=[
                    ScopePredicate(dimension="territory", includes=frozenset({"aland"}))
                ],
            ),
        ],
    )
    findings = sweep_scope_lattice(_products(timelines={_ADDR: timeline}))
    assert len(findings) == 1
    assert findings[0].code == SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE


def test_one_sided_predicate_does_not_admit_overlap() -> None:
    # One row applies everywhere (no predicate), the other is scoped -> no scope
    # distinction admits the overlap -> still a collision.
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[
            _version(text="variant A"),
            _version(
                text="variant B distinct",
                applicability=[
                    ScopePredicate(dimension="territory", includes=frozenset({"aland"}))
                ],
            ),
        ],
    )
    findings = sweep_scope_lattice(_products(timelines={_ADDR: timeline}))
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Degenerate / structural cases
# ---------------------------------------------------------------------------


def test_single_version_timeline_is_silent() -> None:
    timeline = ProvisionTimeline(address=_ADDR, versions=[_version(text="only")])
    assert sweep_scope_lattice(_products(timelines={_ADDR: timeline})) == ()


def test_no_timelines_is_silent() -> None:
    assert sweep_scope_lattice(_products(timelines={})) == ()
    assert sweep_scope_lattice(_products(timelines=None)) == ()


def test_temporary_and_permanent_at_same_date_are_distinct_rails() -> None:
    # variant_kind is part of the rank key: an overlay (temporary) and a
    # background (permanent) at the same date are different rails -> the two-rail
    # selection doctrine resolves them -> silent.
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[
            _version(text="overlay", variant_kind="temporary"),
            _version(text="background distinct", variant_kind="permanent"),
        ],
    )
    assert sweep_scope_lattice(_products(timelines={_ADDR: timeline})) == ()


def test_sweep_is_deterministic_sorted() -> None:
    addr_a = LegalAddress(path=(("section", "1"),))
    addr_b = LegalAddress(path=(("section", "2"),))
    tl_a = ProvisionTimeline(
        address=addr_a,
        versions=[_version(text="A1"), _version(text="A2 distinct")],
    )
    tl_b = ProvisionTimeline(
        address=addr_b,
        versions=[_version(text="B1"), _version(text="B2 distinct")],
    )
    findings = sweep_scope_lattice(
        _products(timelines={addr_b: tl_b, addr_a: tl_a})
    )
    assert [f.address for f in findings] == [str(addr_a), str(addr_b)]
