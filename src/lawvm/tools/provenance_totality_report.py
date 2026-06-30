"""``lawvm.tools.provenance_totality_report`` — per-jurisdiction provenance-orphan rate.

READ-ONLY offline diagnostic for stream C (provenance totality). It compiles a
small fixed SAMPLE of each jurisdiction's corpus into emitted
:class:`~lawvm.core.ir.LegalOperation` streams, runs
:func:`lawvm.core.provenance_totality_audit.assert_op_provenance_totality` over
them, and reports the **provenance-orphan rate per jurisdiction**.

It surfaces two complementary rates:

* ``orphan_rate`` — the share of ops the core audit flags
  ``PROVENANCE.SOURCE_ANCHOR_MISSING`` (no typed anchor AND no textual footing
  at all: ``source_anchor`` / ``op.raw_text`` / ``source.raw_text`` /
  ``source.statute_id`` all empty). The hard "traces back to nothing" gap.
* ``no_typed_anchor_rate`` — the share of ops lacking the STRONGEST footing, the
  typed byte-span :class:`~lawvm.core.provenance.SourceAnchor`
  (``source.source_anchor is None``). This is the real gap the task names: the
  ``source_anchor`` carrier is "owned by the frontend compile" and is OPTIONAL
  today, so most frontends never populate it even when they carry a source
  statute_id. Reporting both separates "named its source instrument" from
  "carries a re-derivable byte anchor".

DISCIPLINE. Pure read-only: it never modifies a frontend, never mutates ops,
never fabricates an anchor. Per-statute compile failures are caught and counted
(``errors``) so the diagnostic never crashes the survey — it reports what it
could compile. NOT wired into any apply lane (NET-NEW core audit + offline
diagnostic only).

Run::

    LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM \\
        uv run python -m lawvm.tools.provenance_totality_report
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from lawvm.core.ir import LegalOperation
from lawvm.core.provenance_totality_audit import (
    PROVENANCE_SOURCE_ANCHOR_MISSING,
    assert_op_provenance_totality,
)


def _data_root() -> Path:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        raise RuntimeError(
            "LAWVM_CANONICAL_DATA_ROOT must be set to the canonical LawVM data root"
        )
    return Path(root)


# --- Per-jurisdiction op-compile samples (read-only) -----------------------
#
# Each sampler yields ``(statute_id, ops)`` pairs for a small fixed sample. The
# samples are deliberately tiny: this is a gap-surfacing diagnostic, not a bench.
# Each sampler reads the canonical farchives read-only and never builds a DB.


def _sample_finland(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    from lawvm.finland._compile import compile_fi_facade

    # Parent statutes with rich amendment histories (varied op shapes).
    sample_ids = ["1990/1295", "2009/953", "2015/1480"][:limit]
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    for statute_id in sample_ids:
        facade = compile_fi_facade(statute_id, replay_mode="legal_pit")
        out.append((statute_id, facade.bundle.structural_ops))
    return out


def _sample_estonia(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    from lawvm.estonia import fetch, grafter

    archive_path = data_root / "data" / "ee_riigiteataja.farchive"
    # Oracle (consolidated amendment) ids from the replayable-corpus CSV header rows.
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
    # Affected acts (compile_ops_for_statute takes the AFFECTED act id and
    # returns the ops that amend it). These carry non-trivial op streams.
    sample_ids = ["asc/2021/1", "ukpga/2000/27", "ukpga/1998/11"][:limit]
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    pipeline = UKReplayPipeline(data_root)
    with Farchive(str(archive_path), readonly=True) as archive:
        for statute_id in sample_ids:
            ops = pipeline.compile_ops_for_statute(statute_id, archive=archive)
            out.append((statute_id, ops))
    return out


def _sample_norway(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    from lawvm.norway.grafter import parse_no_amendment_ops
    from lawvm.norway.sources import load_no_amendment_bytes, resolve_no_source_path

    source_path = resolve_no_source_path(data_root / "data" / "norway.farchive")
    # Lovtid amendment acts with non-trivial change-group op streams (read-only;
    # bytes pulled straight from the Norway farchive, no index/DB build).
    sample_ids = ["no/lovtid/2001-01-19-6", "no/lovtid/2001-03-02-7", "no/lovtid/2001-04-06-12"][:limit]
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    for source_id in sample_ids:
        html_bytes = load_no_amendment_bytes(source_id, source_path)
        if html_bytes is None:
            continue
        ops = parse_no_amendment_ops(html_bytes, source_id)
        out.append((source_id, ops))
    return out


def _sample_sweden(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    import json

    from lawvm.sweden.fetch import load_se_official_act_from_archive, open_se_archive
    from lawvm.sweden.grafter import parse_se_amendment_ops

    archive_path = data_root / "data" / "sweden.farchive"
    # Official-act amending SFS records with varied op shapes (replace/insert/...).
    sample_ids = ["1999:1001", "1999:1003", "1999:1004"][:limit]
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    archive = open_se_archive(archive_path, readonly=True)
    try:
        for sfs_id in sample_ids:
            act = load_se_official_act_from_archive(archive, sfs_id)
            if act is None:
                continue
            ops = parse_se_amendment_ops(json.dumps(act).encode(), f"se/{sfs_id}")
            out.append((f"se/{sfs_id}", ops))
    finally:
        archive.close()
    return out


def _sample_us_federal(data_root: Path, limit: int) -> list[tuple[str, Sequence[LegalOperation]]]:
    from lawvm.us_federal.amendatory import lower_plaw_amendatory
    from lawvm.us_federal.sources import (
        open_us_federal_farchive,
        parse_plaw_locator,
        plaw_locator,
        read_plaw_locator,
    )

    archive_path = data_root / "data" / "us_federal.farchive"
    # Public Laws whose USLM amendatory text lowers to candidate op streams
    # (on the default Title-11 proof surface). Read-only USLM bytes only.
    sample_locators = [
        plaw_locator(108, 126),
        plaw_locator(108, 121),
        plaw_locator(108, 128),
    ][:limit]
    out: list[tuple[str, Sequence[LegalOperation]]] = []
    archive = open_us_federal_farchive(archive_path, readonly=True)
    try:
        for locator in sample_locators:
            ident = parse_plaw_locator(locator)
            data = read_plaw_locator(archive, locator)
            if data is None or ident is None:
                continue
            statute_id = f"PL {ident.congress}-{ident.number}"
            report = lower_plaw_amendatory(data, statute_id=statute_id)
            out.append((statute_id, report.operations()))
    finally:
        archive.close()
    return out


_SAMPLERS: dict[str, Callable[[Path, int], list[tuple[str, Sequence[LegalOperation]]]]] = {
    "finland": _sample_finland,
    "estonia": _sample_estonia,
    "uk": _sample_uk,
    "norway": _sample_norway,
    "sweden": _sample_sweden,
    "us_federal": _sample_us_federal,
}


@dataclass(frozen=True)
class JurisdictionProvenanceRate:
    """Provenance-orphan accounting for one jurisdiction's sample."""

    jurisdiction: str
    statutes: tuple[str, ...]
    total_ops: int
    orphan_ops: int  # no footing at all (the core audit's finding count)
    no_typed_anchor_ops: int  # source.source_anchor is None (the strong gap)
    errors: tuple[str, ...]

    @property
    def orphan_rate(self) -> float:
        return self.orphan_ops / self.total_ops if self.total_ops else 0.0

    @property
    def no_typed_anchor_rate(self) -> float:
        return self.no_typed_anchor_ops / self.total_ops if self.total_ops else 0.0

    def as_jsonable(self) -> dict[str, object]:
        return {
            "jurisdiction": self.jurisdiction,
            "statutes": list(self.statutes),
            "total_ops": self.total_ops,
            "orphan_ops": self.orphan_ops,
            "orphan_rate": round(self.orphan_rate, 6),
            "no_typed_anchor_ops": self.no_typed_anchor_ops,
            "no_typed_anchor_rate": round(self.no_typed_anchor_rate, 6),
            "errors": list(self.errors),
        }


def _no_typed_anchor(op: LegalOperation) -> bool:
    return op.source is None or op.source.source_anchor is None


def compute_jurisdiction_rate(
    jurisdiction: str,
    *,
    data_root: Path | None = None,
    limit: int = 3,
) -> JurisdictionProvenanceRate:
    """Compile the sample for one jurisdiction and compute its orphan rates.

    Per-statute compile failures are caught and recorded in ``errors`` (the
    survey continues over the remaining statutes); the diagnostic stays
    read-only and never crashes on a single bad input.
    """
    root = data_root if data_root is not None else _data_root()
    sampler = _SAMPLERS[jurisdiction]
    total_ops = 0
    orphan_ops = 0
    no_typed_anchor_ops = 0
    statutes: list[str] = []
    errors: list[str] = []
    try:
        compiled = sampler(root, limit)
    except Exception as exc:  # noqa: BLE001 — read-only survey, surface not crash
        return JurisdictionProvenanceRate(
            jurisdiction=jurisdiction,
            statutes=(),
            total_ops=0,
            orphan_ops=0,
            no_typed_anchor_ops=0,
            errors=(f"sampler:{type(exc).__name__}:{exc}",),
        )
    for statute_id, ops in compiled:
        try:
            ops_tuple = tuple(ops)
            statutes.append(statute_id)
            total_ops += len(ops_tuple)
            orphan_ops += len(
                assert_op_provenance_totality(ops_tuple, source_statute=statute_id)
            )
            no_typed_anchor_ops += sum(1 for op in ops_tuple if _no_typed_anchor(op))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{statute_id}:{type(exc).__name__}:{exc}")
    return JurisdictionProvenanceRate(
        jurisdiction=jurisdiction,
        statutes=tuple(statutes),
        total_ops=total_ops,
        orphan_ops=orphan_ops,
        no_typed_anchor_ops=no_typed_anchor_ops,
        errors=tuple(errors),
    )


def compute_rows(
    *,
    data_root: Path | None = None,
    jurisdictions: Sequence[str] | None = None,
    limit: int = 3,
) -> tuple[JurisdictionProvenanceRate, ...]:
    """Compute the typed per-jurisdiction rate rows for the sample."""
    names = tuple(jurisdictions) if jurisdictions is not None else tuple(sorted(_SAMPLERS))
    return tuple(
        compute_jurisdiction_rate(name, data_root=data_root, limit=limit) for name in names
    )


def build_report(
    *,
    data_root: Path | None = None,
    jurisdictions: Sequence[str] | None = None,
    limit: int = 3,
) -> dict[str, object]:
    """Run the audit over each jurisdiction's sample and return a JSON-safe report."""
    rows = compute_rows(data_root=data_root, jurisdictions=jurisdictions, limit=limit)
    return {
        "audit": "PROVENANCE.SOURCE_ANCHOR_MISSING",
        "limit_per_jurisdiction": limit,
        "jurisdictions": [row.as_jsonable() for row in rows],
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Per-jurisdiction provenance-orphan rate (read-only diagnostic).",
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
            "audit": PROVENANCE_SOURCE_ANCHOR_MISSING,
            "limit_per_jurisdiction": args.limit,
            "jurisdictions": [row.as_jsonable() for row in rows],
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print(f"provenance-totality audit: {PROVENANCE_SOURCE_ANCHOR_MISSING}")
    print(
        f"{'jurisdiction':<12} {'ops':>6} {'orphans':>8} {'orphan%':>8} "
        f"{'no_anchor':>10} {'no_anchor%':>11}"
    )
    for row in rows:
        print(
            f"{row.jurisdiction:<12} {row.total_ops:>6} {row.orphan_ops:>8} "
            f"{row.orphan_rate * 100:>7.2f}% {row.no_typed_anchor_ops:>10} "
            f"{row.no_typed_anchor_rate * 100:>10.2f}%"
        )
        for err in row.errors:
            print(f"  ! {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
