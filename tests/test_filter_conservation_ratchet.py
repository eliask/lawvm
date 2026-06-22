"""Filter-conservation contract ratchet (Audit C).

A *conserving* filter must not silently drop its rejected/residual material: it
returns a conserving carrier (a ``PartitionResult`` / ``FilterResult`` subclass
with accepted + rejected + residual lanes), AND a production consumer reads the
rejected/residual lane. A count-scan cannot express "conservation" — reverting a
carrier to a bare ``list`` is not a count change — so Audit C is enforced as a
STRUCTURAL CONTRACT test, mirroring the regex-ratchet ergonomics in spirit.

This pins the five Audit-C conversions. For each one it asserts:
  (1) the producer's declared return type is the conserving carrier (a
      ``PartitionResult`` subclass, or a result type that exposes the rejected /
      residual lane) — so reverting to a bare ``list``/``tuple`` return fails
      typecheck AND this assertion;
  (2) the carrier exposes the rejected/residual accessor the consumer drains
      (``.rejected`` / ``.residuals`` / ``.skipped_targets`` / domain accessor),
      and (where cheap) a runtime construction confirms the lane is real;
  (3) the named production consumer's source actually READS that lane (so merely
      building the carrier and discarding the rejected lane regresses too).

The five (notes_internal/STAGERESULT_ENDGAME.md, Audit C):
  1. vts.extract_voimaantulo_repeals_partition -> VtsRepealPartition
  2. body_coverage.collect_coverage_claims_partition -> CoverageClaimPartition
  3. amendment_selection._filter_candidates -> PartitionResult (surfaced on
     ApplicableAmendmentSelection.out_of_scope)
  4. interlink_targets.project_fi_interlinks_partition -> InterlinkProjection
  5. process_structural_prepare._drop_seeded_chapter_ops -> PartitionResult
"""
from __future__ import annotations

import inspect
import typing
from pathlib import Path

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.stage_result import PartitionResult, Residual

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _return_annotation(func: object) -> object:
    """Resolved return annotation of a function (handles string annotations)."""
    hints = typing.get_type_hints(func)
    return hints.get("return")


def _origin_or_self(annotation: object) -> object:
    """The class behind a (possibly subscripted) annotation, e.g.
    ``PartitionResult[T]`` -> ``PartitionResult``."""
    origin = typing.get_origin(annotation)
    return origin if origin is not None else annotation


# ---------------------------------------------------------------------------
# Carrier shape primitives
# ---------------------------------------------------------------------------


def _is_partition_carrier(cls: object) -> bool:
    return isinstance(cls, type) and issubclass(cls, PartitionResult)


# ===========================================================================
# 1. vts.extract_voimaantulo_repeals_partition -> VtsRepealPartition
# ===========================================================================


class TestVtsRepealPartitionConservation:
    def test_return_type_is_conserving_carrier(self) -> None:
        from lawvm.finland import vts

        ret = _origin_or_self(_return_annotation(vts.extract_voimaantulo_repeals_partition))
        assert ret is vts.VtsRepealPartition, (
            "extract_voimaantulo_repeals_partition must return VtsRepealPartition "
            f"(got {ret!r}); a bare list/tuple return is a conservation regression."
        )
        assert _is_partition_carrier(vts.VtsRepealPartition)

    def test_carrier_has_rejected_lanes(self) -> None:
        from lawvm.finland import vts

        carrier = vts.VtsRepealPartition(FilterResult())
        # accepted lane (PartitionResult contract) + the two typed reject/residual
        # channels the production replay ledger drains.
        assert hasattr(carrier, "accepted")
        assert hasattr(carrier, "rejected")
        assert hasattr(carrier, "residuals")
        assert carrier.skipped_targets == ()
        assert carrier.source_diagnostics == ()

    def test_production_consumer_reads_rejected_lane(self) -> None:
        from lawvm.finland.source_model import AmendmentSourceModel

        # The in-set production consumer (ledger: source_model adapter) builds the
        # partition and drains its skipped_targets rejected lane.
        src = inspect.getsource(
            AmendmentSourceModel.extract_vts_cross_statute_repeals
        )
        assert "extract_voimaantulo_repeals_partition" in src
        assert "partition.skipped_targets" in src, (
            "the source_model adapter must drain partition.skipped_targets into the "
            "production out-param, else the rejected lane is silently dropped."
        )


# ===========================================================================
# 2. body_coverage.collect_coverage_claims_partition -> CoverageClaimPartition
# ===========================================================================


class TestCoverageClaimPartitionConservation:
    def test_return_type_is_conserving_carrier(self) -> None:
        from lawvm.finland import body_coverage

        ret = _origin_or_self(
            _return_annotation(body_coverage.collect_coverage_claims_partition)
        )
        assert ret is body_coverage.CoverageClaimPartition, (
            "collect_coverage_claims_partition must return CoverageClaimPartition "
            f"(got {ret!r}); a bare list return is a conservation regression."
        )
        assert _is_partition_carrier(body_coverage.CoverageClaimPartition)

    def test_carrier_has_rejected_lane(self) -> None:
        from lawvm.finland import body_coverage

        carrier = body_coverage.CoverageClaimPartition(FilterResult())
        assert hasattr(carrier, "accepted")
        assert hasattr(carrier, "rejected")
        assert hasattr(carrier, "residuals")
        assert carrier.rejected_claims == ()

    def test_production_consumer_reads_rejected_lane(self) -> None:
        from lawvm.finland import uncovered_recovery_prepare

        src = inspect.getsource(uncovered_recovery_prepare)
        # The production prepare path collects the rejected claims and iterates them.
        assert "rejected_claims" in src
        assert "rejected_claims_out" in src, (
            "uncovered_recovery_prepare must drain the rejected coverage-claim "
            "lane (rejected_claims_out), else the drop is silent."
        )


# ===========================================================================
# 3. amendment_selection._filter_candidates -> PartitionResult
#    (surfaced on ApplicableAmendmentSelection.out_of_scope)
# ===========================================================================


class TestAmendmentSelectionConservation:
    def test_filter_returns_partition_result(self) -> None:
        from lawvm.finland import amendment_selection

        ret = _return_annotation(amendment_selection._filter_candidates)
        # Return is a tuple; its first element must be a PartitionResult[...].
        args = typing.get_args(ret)
        assert args, f"_filter_candidates return must be a tuple type (got {ret!r})"
        first = _origin_or_self(args[0])
        assert first is PartitionResult, (
            "_filter_candidates must return a PartitionResult as its first tuple "
            f"element (got {first!r}); a bare candidate list is a regression."
        )

    def test_out_of_scope_lane_is_typed_rejected(self) -> None:
        from lawvm.finland import amendment_selection

        hints = typing.get_type_hints(amendment_selection.ApplicableAmendmentSelection)
        oos = hints.get("out_of_scope")
        # tuple[RejectedItem[...], ...]
        args = typing.get_args(oos)
        assert args, f"out_of_scope must be a typed tuple (got {oos!r})"
        elem = _origin_or_self(args[0])
        assert elem is RejectedItem, (
            "ApplicableAmendmentSelection.out_of_scope must carry RejectedItem "
            f"records (got {elem!r})."
        )

    def test_production_consumer_surfaces_rejected_lane(self) -> None:
        from lawvm.finland import amendment_selection

        # The selection result surfaces the filter partition's rejected lane.
        src = inspect.getsource(amendment_selection)
        assert "out_of_scope=partition.rejected" in src, (
            "the selection result must surface the filter partition's rejected "
            "lane onto out_of_scope, else excluded candidates are dropped silently."
        )


# ===========================================================================
# 4. interlink_targets.project_fi_interlinks_partition -> InterlinkProjection
# ===========================================================================


class TestInterlinkProjectionConservation:
    def test_return_type_is_conserving_carrier(self) -> None:
        from lawvm.finland import interlink_targets

        ret = _origin_or_self(
            _return_annotation(interlink_targets.project_fi_interlinks_partition)
        )
        assert ret is interlink_targets.InterlinkProjection, (
            "project_fi_interlinks_partition must return InterlinkProjection "
            f"(got {ret!r}); a bare row list is a conservation regression."
        )
        assert _is_partition_carrier(interlink_targets.InterlinkProjection)

    def test_carrier_has_residual_lane(self) -> None:
        from lawvm.finland import interlink_targets

        carrier = interlink_targets.InterlinkProjection(FilterResult())
        assert hasattr(carrier, "accepted")
        assert hasattr(carrier, "rows")
        assert hasattr(carrier, "residuals")
        assert carrier.residuals == ()
        # A real residual must round-trip through the carrier.
        live = interlink_targets.InterlinkProjection(
            FilterResult(),
            residuals=(Residual(kind="typed_residual", reason="r", scope="s"),),
        )
        assert len(live.residuals) == 1

    def test_production_consumer_reads_residual_lane(self) -> None:
        from lawvm.finland import interlink_targets

        src = inspect.getsource(
            interlink_targets.project_fi_interlinks_for_transition_graph
        )
        assert "project_fi_interlinks_partition" in src
        assert "projection.residuals" in src, (
            "the transition-graph consumer must read projection.residuals (and "
            "surface blocking residue), else the previously-discarded diagnostics "
            "are silently dropped again."
        )


# ===========================================================================
# 5. process_structural_prepare._drop_seeded_chapter_ops -> PartitionResult
# ===========================================================================


class TestChapterSeedDropConservation:
    def test_return_type_is_partition_result(self) -> None:
        from lawvm.finland import process_structural_prepare as psp

        ret = _origin_or_self(
            _return_annotation(psp.ProcessStructuralPrepareContext._drop_seeded_chapter_ops)
        )
        assert ret is PartitionResult, (
            "_drop_seeded_chapter_ops must return a PartitionResult "
            f"(got {ret!r}); a bare kept-ops list is a conservation regression."
        )

    def test_carrier_lanes_exist(self) -> None:
        carrier = PartitionResult(
            FilterResult(
                accepted_items=(),
                rejected_items=(
                    RejectedItem(item=object(), reason="r", reason_code="c"),
                ),
            ),
            residuals=(Residual(kind="out_of_scope", reason="r", scope="s"),),
        )
        assert len(carrier.rejected) == 1
        assert len(carrier.residuals) == 1

    def test_production_consumer_reads_rejected_lane(self) -> None:
        from lawvm.finland import process_structural_prepare as psp

        # prepare() consumes the partition and records the rejected lane.
        prepare_src = inspect.getsource(psp.ProcessStructuralPrepareContext.prepare)
        assert "_drop_seeded_chapter_ops" in prepare_src
        assert "_record_chapter_seed_skip" in prepare_src
        record_src = inspect.getsource(psp.ProcessStructuralPrepareContext._record_chapter_seed_skip)
        assert "partition.rejected" in record_src and "partition.residuals" in record_src, (
            "_record_chapter_seed_skip must drain partition.rejected/.residuals "
            "onto the elaboration-observation ledger, else the drop is silent."
        )
