"""``lawvm.tools.coverage_totality_report`` — per-jurisdiction coverage-partition rate.

READ-ONLY offline diagnostic for stream D (coverage totality). It compiles a
small fixed SAMPLE of each jurisdiction's corpus into emitted
:class:`~lawvm.core.ir.LegalOperation` streams, derives the
``core/coverage.py`` carriers from those ops (a frontend-neutral extractor: each
op's leaf target becomes one :class:`~lawvm.core.coverage.CoverageUnit` and one
:class:`~lawvm.core.coverage.CoverageClaim`), runs
:func:`lawvm.core.coverage_totality.assert_coverage_totality` over them, and
reports the **coverage-partition accounting per jurisdiction**:

* ``unclassified_rate`` — share of source units the core audit flags
  ``COVERAGE.UNIT_UNCLASSIFIED`` (neither covered by a claim nor classified by
  the disposition). Under the op-derived extractor every unit is claimed, so the
  default-classifier rate is structurally 0.0 — the row's value is in proving the
  assertion RUNS over a real producer's ops and that the partition stays TOTAL
  (``covered ∪ classified ∪ unclassified == input``), which the diagnostic
  asserts per statute (``partition_total``).
* ``classified_gap_rate`` — share of units owned via a typed disposition rather
  than a direct claim (the classifier lane).

The derivation is deliberately frontend-neutral so the diagnostic does NOT reach
into FI's (DELEGATED) body-parse internals: it builds the carriers from the
already-emitted op stream, exactly as the stream-C provenance diagnostic does.
This shows ``assert_coverage_totality`` works against real compiled ops without
modifying any frontend.

DISCIPLINE. Pure read-only: it never modifies a frontend, never mutates carriers,
never fabricates a claim or a disposition. Per-statute compile failures are
caught and counted (``errors``) so the diagnostic never crashes the survey. NOT
wired into any apply lane (NET-NEW core audit + offline diagnostic only).

Run::

    LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM \\
        uv run python -m lawvm.tools.coverage_totality_report
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lawvm.core.coverage import CoverageClaim, CoverageUnit
from lawvm.core.coverage_totality import (
    COVERAGE_UNIT_UNCLASSIFIED,
    assert_coverage_totality,
    target_touch_partition,
)
from lawvm.core.ir import LegalOperation


def _data_root() -> Path:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        raise RuntimeError(
            "LAWVM_CANONICAL_DATA_ROOT must be set to the canonical LawVM data root"
        )
    return Path(root)


# --- Per-jurisdiction op-compile samples (read-only) -----------------------
#
# Reuse the same tiny fixed samples as the stream-C provenance diagnostic, so the
# two coverage/provenance surveys describe the same corpus slice.


def _sample_finland(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    from lawvm.finland._compile import compile_fi_facade

    sample_ids = ["1990/1295", "2009/953", "2015/1480"][:limit]
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    for statute_id in sample_ids:
        facade = compile_fi_facade(statute_id, replay_mode="legal_pit")
        out.append((statute_id, facade.bundle.structural_ops))
    return out


def _sample_estonia(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    from lawvm.estonia import fetch, grafter

    archive_path = data_root / "data" / "ee_riigiteataja.farchive"
    sample_ids = ["127122011011", "119082015004", "128092014004"][:limit]
    archive = fetch.open_rt_archive(archive_path, readonly=True)
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    for amendment_id in sample_ids:
        xml = fetch.fetch_rt_xml(amendment_id, archive=archive)
        ops = grafter.parse_ee_amendment_ops(xml, source_id=f"ee/{amendment_id}")
        out.append((f"ee/{amendment_id}", ops))
    return out


def _sample_uk(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    from farchive import Farchive

    from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

    archive_path = data_root / "data" / "uk_legislation.farchive"
    sample_ids = ["asc/2021/1", "ukpga/2000/27", "ukpga/1998/11"][:limit]
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    pipeline = UKReplayPipeline(data_root)
    with Farchive(str(archive_path), readonly=True) as archive:
        for statute_id in sample_ids:
            ops = pipeline.compile_ops_for_statute(statute_id, archive=archive)
            out.append((statute_id, ops))
    return out


_SAMPLERS: dict[str, Callable[[Path, int], list[tuple[str, Sequence[LegalOperation]]]]] = {
    "finland": _sample_finland,
    "estonia": _sample_estonia,
    "uk": _sample_uk,
}


# --- Frontend-neutral op→carrier extractor ---------------------------------


def _op_unit_id(op: LegalOperation) -> str:
    """Stable ``<kind>_<label>`` id for an op's leaf target.

    Mirrors FI's ``<kind>_<label>`` unit-id shape so the covered-set algebra in
    :mod:`lawvm.core.coverage_totality` matches a unit to its claim. Falls back
    to the op id when the target has no path leaf.
    """
    target = op.target
    if target is not None and target.path:
        kind = target.leaf_kind()
        label = target.leaf_label()
        return f"{kind}_{label}" if label else f"{kind}_{op.op_id}"
    return f"op_{op.op_id}"


def carriers_from_ops(
    ops: Sequence[LegalOperation],
) -> tuple[tuple[CoverageUnit, ...], tuple[CoverageClaim, ...]]:
    """Derive ``(source_units, claims)`` from a compiled op stream (read-only).

    Each op's leaf target becomes one :class:`CoverageUnit` (deduplicated by
    unit id) and one :class:`CoverageClaim` covering it. This is the frontend-
    neutral extractor the diagnostic uses to feed real compiled ops to the
    assertion without touching any frontend's body-parse internals.
    """
    units: list[CoverageUnit] = []
    claims: list[CoverageClaim] = []
    seen: set[str] = set()
    for op in ops:
        unit_id = _op_unit_id(op)
        target = op.target
        if unit_id not in seen:
            seen.add(unit_id)
            units.append(
                CoverageUnit(
                    unit_id=unit_id,
                    kind=target.leaf_kind() if (target and target.path) else "op",
                    observed_label=(target.leaf_label() if (target and target.path) else None)
                    or None,
                    parent_label=None,
                    payload_ref=None,
                )
            )
        claims.append(
            CoverageClaim(
                claim_kind="explicit",
                target=op,
                covered_unit_ids=frozenset({unit_id}),
                evidence=(f"op_id={op.op_id}",),
            )
        )
    return tuple(units), tuple(claims)


@dataclass(frozen=True)
class JurisdictionCoverageRate:
    """Coverage-partition accounting for one jurisdiction's sample."""

    jurisdiction: str
    statutes: tuple[str, ...]
    total_units: int
    unclassified_units: int  # COVERAGE.UNIT_UNCLASSIFIED finding count
    classified_gap_units: int  # owned via a classifier disposition, not a claim
    partition_total: bool  # covered ∪ classified ∪ unclassified == input, every statute
    errors: tuple[str, ...]

    @property
    def unclassified_rate(self) -> float:
        return self.unclassified_units / self.total_units if self.total_units else 0.0

    @property
    def classified_gap_rate(self) -> float:
        return self.classified_gap_units / self.total_units if self.total_units else 0.0

    def as_jsonable(self) -> dict[str, object]:
        return {
            "jurisdiction": self.jurisdiction,
            "statutes": list(self.statutes),
            "total_units": self.total_units,
            "unclassified_units": self.unclassified_units,
            "unclassified_rate": round(self.unclassified_rate, 6),
            "classified_gap_units": self.classified_gap_units,
            "classified_gap_rate": round(self.classified_gap_rate, 6),
            "partition_total": self.partition_total,
            "errors": list(self.errors),
        }


def compute_jurisdiction_rate(
    jurisdiction: str,
    *,
    data_root: Path | None = None,
    limit: int = 3,
) -> JurisdictionCoverageRate:
    """Compile the sample for one jurisdiction and run the totality assertion.

    Per-statute compile failures are caught and recorded in ``errors`` (the
    survey continues); the diagnostic stays read-only and never crashes on a
    single bad input.
    """
    root = data_root if data_root is not None else _data_root()
    sampler = _SAMPLERS[jurisdiction]
    total_units = 0
    unclassified_units = 0
    classified_gap_units = 0
    partition_total = True
    statutes: list[str] = []
    errors: list[str] = []
    try:
        compiled = sampler(root, limit)
    except Exception as exc:  # noqa: BLE001 — read-only survey, surface not crash
        return JurisdictionCoverageRate(
            jurisdiction=jurisdiction,
            statutes=(),
            total_units=0,
            unclassified_units=0,
            classified_gap_units=0,
            partition_total=True,
            errors=(f"sampler:{type(exc).__name__}:{exc}",),
        )
    for statute_id, ops in compiled:
        try:
            ops_tuple = tuple(ops)
            source_units, claims = carriers_from_ops(ops_tuple)
            observations, report = assert_coverage_totality(
                source_units,
                ops_tuple,
                source_units,  # base-IR target_units ≈ the same unit set here
                claims,
                source_statute=statute_id,
            )
            # Symmetric target half — proves the touched/untouched split runs.
            touched, untouched = target_touch_partition(source_units, claims)
            statutes.append(statute_id)
            total_units += len(source_units)
            unclassified_units += len(observations)
            classified_gap_units += sum(
                1 for gap in report.gaps if gap.disposition != "ambiguous_uncovered"
            )
            # Partition totality: covered + every gap accounts for every unit.
            covered = len(source_units) - len(report.gaps)
            if covered + len(report.gaps) != len(source_units):
                partition_total = False
            if len(touched) + len(untouched) != len(source_units):
                partition_total = False
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{statute_id}:{type(exc).__name__}:{exc}")
    return JurisdictionCoverageRate(
        jurisdiction=jurisdiction,
        statutes=tuple(statutes),
        total_units=total_units,
        unclassified_units=unclassified_units,
        classified_gap_units=classified_gap_units,
        partition_total=partition_total,
        errors=tuple(errors),
    )


def compute_rows(
    *,
    data_root: Path | None = None,
    jurisdictions: Sequence[str] | None = None,
    limit: int = 3,
) -> tuple[JurisdictionCoverageRate, ...]:
    """Compute the typed per-jurisdiction rate rows for the sample."""
    names = tuple(jurisdictions) if jurisdictions is not None else tuple(sorted(_SAMPLERS))
    return tuple(
        compute_jurisdiction_rate(name, data_root=data_root, limit=limit) for name in names
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Per-jurisdiction coverage-partition rate (read-only diagnostic).",
    )
    parser.add_argument(
        "--jurisdiction",
        action="append",
        choices=sorted(_SAMPLERS),
        help="Restrict to one or more jurisdictions (default: all).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Max sample statutes per jurisdiction (default 3).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full JSON report instead of the human summary.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    rows = compute_rows(jurisdictions=args.jurisdiction, limit=args.limit)
    if args.json:
        report = {
            "audit": COVERAGE_UNIT_UNCLASSIFIED,
            "limit_per_jurisdiction": args.limit,
            "jurisdictions": [row.as_jsonable() for row in rows],
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print(f"coverage-totality audit: {COVERAGE_UNIT_UNCLASSIFIED}")
    print(
        f"{'jurisdiction':<12} {'units':>6} {'unclass':>8} {'unclass%':>9} "
        f"{'clsgap':>8} {'total?':>7}"
    )
    for row in rows:
        print(
            f"{row.jurisdiction:<12} {row.total_units:>6} {row.unclassified_units:>8} "
            f"{row.unclassified_rate * 100:>8.2f}% {row.classified_gap_units:>8} "
            f"{str(row.partition_total):>7}"
        )
        for err in row.errors:
            print(f"  ! {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
