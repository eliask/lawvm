#!/usr/bin/env python3
"""Offline synthetic-label + positional-id leak sweep over FI stored surfaces.

Implements registry rows **LS-13** (``APPLY.SYNTHETIC_LABEL_LEAK``) and, as a
free companion off the shared scan scaffolding, **LS-12**
(``APPLY.POSITIONAL_ID_LEAK``) — the offline audit half of AGENTS.md §2.8 / §2.9
test-6.

Without ``--statute``/``--corpus`` the script runs a self-test over a clean
synthetic dossier and exits 0. With a statute id (or corpus file) it replays
each statute, materializes its PIT IR tree, and sweeps the *stored surfaces*
that survive into the materialization — the IR body tree (labels + attrs),
the compiled :class:`ProvisionTimeline` keys, and the projection rows — for the
synthetic-marker and positional-id vocabulary. The single sanctioned home for a
synthesized rule id is ``attrs.source_rule_id``; a marker anywhere else is a
leak.

Usage::

    uv run python scripts/audit_synthetic_label_leak.py            # self-test
    uv run python scripts/audit_synthetic_label_leak.py --statute 2002/738
    uv run python scripts/audit_synthetic_label_leak.py --corpus path/to/ids.txt
    uv run python scripts/audit_synthetic_label_leak.py --statute 2002/738 --positional-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

from lawvm.core.identity_intrinsic_audit import (  # noqa: E402
    IdentityAuditReport,
    IdentityLeakFinding,
    sweep_positional_id_leaks,
    sweep_synthetic_label_leaks,
)


def _combined_sweep(surfaces: object, *, root_name: str, positional_only: bool, synthetic_only: bool) -> IdentityAuditReport:
    findings: list[IdentityLeakFinding] = []
    if not synthetic_only:
        findings.extend(sweep_positional_id_leaks(surfaces, root_name=root_name).findings)
    if not positional_only:
        findings.extend(sweep_synthetic_label_leaks(surfaces, root_name=root_name).findings)
    return IdentityAuditReport(findings=tuple(findings))


def _self_test_dossier() -> object:
    """A clean synthetic dossier mirroring the materialized stored surfaces."""
    from lawvm.core.ir import (
        IRNode,
        IRStatute,
        LegalAddress,
        ProvisionTimeline,
        ProvisionVersion,
    )
    from lawvm.core.semantic_types import IRNodeKind

    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                text="Tätä lakia sovelletaan.",
                attrs={"source_rule_id": "fi_section_intro_normalized", "eId": "sec_1"},
            ),
        ),
    )
    statute = IRStatute(statute_id="999/2025", title="Testilaki", body=body)
    timeline = ProvisionTimeline(
        address=LegalAddress(path=(("section", "1"),)),
        versions=[ProvisionVersion(effective="2025-01-01")],
    )
    return {"statute": statute, "timelines": [timeline]}


def _real_surfaces(statute_id: str) -> tuple[object, str]:
    """Replay one statute and return its stored surfaces + a label.

    Returns the materialized IR tree, the compiled timelines, and the projection
    rows bundled into one structure the walker can sweep. Any replay failure is
    surfaced as a typed error tuple so the sweep is never silently skipped.
    """
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest

    result = replay_xml(
        request=ReplayXmlRequest(parent_id=statute_id, mode="legal_pit", quiet=True),
    )
    timelines = result.timelines
    surfaces: dict[str, object] = {
        "materialized_ir": result.ir,
        # Timeline keys are LegalAddresses; sweep both keys and the timeline bodies.
        "timeline_keys": list(getattr(timelines, "addresses", lambda: [])())
        if timelines is not None and hasattr(timelines, "addresses")
        else [],
        "timelines": timelines,
        "projection_rows": list(result.projection_rows()),
    }
    return surfaces, statute_id


def _print_report(label: str, report: IdentityAuditReport) -> None:
    if report.clean:
        print(f"[clean] {label}: no positional-id / synthetic-label leak in stored surfaces")
        return
    print(f"[LEAK]  {label}: {len(report.findings)} finding(s)")
    for f in report.findings:
        print(f"        {f.finding_kind}[{f.vocab}] at {f.location}: {f.value!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--statute", help="Single FI statute id (e.g. 2002/738) to sweep.")
    parser.add_argument("--corpus", type=Path, help="Plain-text file of statute ids, one per line.")
    parser.add_argument("--positional-only", action="store_true", help="Run only the LS-12 positional-id sweep.")
    parser.add_argument("--synthetic-only", action="store_true", help="Run only the LS-13 synthetic-label sweep.")
    parser.add_argument("--limit", type=int, default=0, help="Cap the number of corpus statutes swept (0 = all).")
    args = parser.parse_args(argv)

    if args.positional_only and args.synthetic_only:
        parser.error("--positional-only and --synthetic-only are mutually exclusive")

    statute_ids: list[str] = []
    if args.statute:
        statute_ids.append(args.statute)
    if args.corpus:
        for raw in args.corpus.read_text(encoding="utf-8").splitlines():
            sid = raw.strip()
            if sid and not sid.startswith("#"):
                statute_ids.append(sid)
    if args.limit and len(statute_ids) > args.limit:
        statute_ids = statute_ids[: args.limit]

    if not statute_ids:
        # Self-test: clean synthetic dossier must sweep green.
        report = _combined_sweep(
            _self_test_dossier(),
            root_name="self_test",
            positional_only=args.positional_only,
            synthetic_only=args.synthetic_only,
        )
        _print_report("self_test (clean synthetic dossier)", report)
        return 0 if report.clean else 1

    any_leak = False
    for sid in statute_ids:
        try:
            surfaces, label = _real_surfaces(sid)
        except Exception as exc:  # replay failure is a sweep-skip, reported loudly
            print(f"[error] {sid}: replay failed, surface sweep skipped: {type(exc).__name__}: {exc}")
            continue
        report = _combined_sweep(
            surfaces,
            root_name=label,
            positional_only=args.positional_only,
            synthetic_only=args.synthetic_only,
        )
        _print_report(label, report)
        any_leak = any_leak or not report.clean

    return 1 if any_leak else 0


if __name__ == "__main__":
    raise SystemExit(main())
