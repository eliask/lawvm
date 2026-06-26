"""Title-scoped effect-candidate scan over the U.S. federal farchive.

Given a window of staged Public Laws, this surface detects which laws amend a
target USC title (default Title 11, Bankruptcy), lowers each through
``amendatory.lower_plaw_amendatory``, and aggregates the candidate
``LegalOperation`` envelopes + typed findings + a witness-anchored coverage
summary.

Honest coverage (AGENTS.md §0): this emits CANDIDATES only. It never claims
replay or agreement. Coverage is reported as lowered-vs-total amendment
instructions per law and across the window; the dry-run stage proves the
candidates against the USC oracle.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from lawvm.core.ir import LegalOperation
from lawvm.us_federal.amendatory import (
    USAmendatoryFinding,
    USAmendatoryReport,
    lower_plaw_amendatory,
)
from lawvm.us_federal.sources import (
    PlawMemberIdentity,
    UsArchiveReader,
    list_plaw_identities,
    read_plaw_locator,
)

# Default scan window: the staged Congresses that include the Title 11 first target.
DEFAULT_TITLE_11_CONGRESS_WINDOW = (114, 115, 116, 117, 118)

NO_TITLE_TARGET_RULE_ID = "us_effect_scan_law_does_not_target_title"


@dataclass(frozen=True)
class USLawCandidateRow:
    """One scanned Public Law and its lowered amendatory report (if it targets the title)."""

    identity: PlawMemberIdentity
    targets_title: bool
    report: USAmendatoryReport | None = None

    @property
    def statute_id(self) -> str:
        return self.report.statute_id if self.report is not None else self.identity.public_law_label

    def operations(self) -> tuple[LegalOperation, ...]:
        return self.report.operations() if self.report is not None else ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "public_law": self.identity.public_law_label,
            "locator": self.identity.locator,
            "targets_title": self.targets_title,
            "coverage": self.report.coverage() if self.report is not None else None,
        }


@dataclass(frozen=True)
class USTitleEffectCandidateReport:
    """Window-level candidate aggregation for one USC title."""

    title: str
    rows: tuple[USLawCandidateRow, ...]

    def title_targeting_rows(self) -> tuple[USLawCandidateRow, ...]:
        return tuple(r for r in self.rows if r.targets_title)

    def operations(self) -> tuple[LegalOperation, ...]:
        ops: list[LegalOperation] = []
        for row in self.rows:
            if row.report is None:
                continue
            for instr in row.report.instructions:
                if instr.operation is None:
                    continue
                # Only emit candidates whose resolved target is on this title.
                addr = instr.target_address
                if addr is not None and addr.path and addr.path[0] == ("title", self.title):
                    ops.append(instr.operation)
        return tuple(ops)

    def findings(self) -> tuple[USAmendatoryFinding, ...]:
        out: list[USAmendatoryFinding] = []
        for row in self.rows:
            if row.report is not None:
                out.extend(row.report.findings)
        return tuple(out)

    def coverage(self) -> dict[str, Any]:
        targeting = self.title_targeting_rows()
        total_instr = sum(len(r.report.instructions) for r in targeting if r.report)
        lowered = sum(
            sum(1 for i in r.report.instructions if i.operation is not None)
            for r in targeting
            if r.report
        )
        accepted = sum(
            sum(1 for i in r.report.instructions if i.instruction_status == "accepted")
            for r in targeting
            if r.report
        )
        title_ops = len(self.operations())
        finding_rule_counts = Counter(f.rule_id for f in self.findings())
        return {
            "title": self.title,
            "laws_scanned": len(self.rows),
            "laws_targeting_title": len(targeting),
            "law_labels_targeting_title": sorted(r.statute_id for r in targeting),
            "instructions_total": total_instr,
            "instructions_lowered": lowered,
            "instructions_accepted": accepted,
            "title_candidate_operations": title_ops,
            "finding_rule_counts": dict(sorted(finding_rule_counts.items())),
            "findings_total": len(self.findings()),
            "replay_claims": False,
            "candidate_claims": True,
        }

    def to_jsonable(self, *, include_rows: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "us_federal",
            "report_kind": "title_effect_candidates",
            "truth_claim": "candidate_legal_operations_not_replayed",
            "replay_claims": False,
            "candidate_claims": True,
            "coverage": self.coverage(),
        }
        if include_rows:
            payload["laws"] = [r.to_jsonable() for r in self.rows if r.targets_title]
        return payload


def _law_targets_title(data: bytes, title: str) -> bool:
    """Witness-anchored detection that a law's amendatory text targets ``title``.

    Detects both the USLM ref href form (``/us/usc/t{N}/``) and the prose form
    ("of title {N}, United States Code"). Decode is lenient; this is only the
    cheap pre-filter, the authoritative target is the resolved LegalAddress.
    """
    text = data.decode("utf-8", "replace")
    href_marker = f'/us/usc/t{title}/'
    prose_marker = f"of title {title}, United States Code"
    prose_marker_short = f"of title {title}"
    return href_marker in text or prose_marker in text or prose_marker_short in text


def scan_title_effect_candidates(
    archive: UsArchiveReader,
    *,
    title: str = "11",
    congress_window: Iterable[int] = DEFAULT_TITLE_11_CONGRESS_WINDOW,
) -> USTitleEffectCandidateReport:
    """Scan staged Public Laws for amendments to ``title`` and emit candidates.

    For each Public Law in ``congress_window`` that targets the title, the law is
    lowered and its candidate operations + findings are aggregated. Laws that do
    not target the title are recorded (``targets_title=False``) but not lowered.
    """
    window = set(int(c) for c in congress_window)
    rows: list[USLawCandidateRow] = []
    for ident in list_plaw_identities(archive):
        if ident.congress not in window or not ident.is_public:
            continue
        data = read_plaw_locator(archive, ident.locator)
        if data is None:
            continue
        if not _law_targets_title(data, title):
            rows.append(USLawCandidateRow(identity=ident, targets_title=False))
            continue
        report = lower_plaw_amendatory(data)
        rows.append(USLawCandidateRow(identity=ident, targets_title=True, report=report))
    return USTitleEffectCandidateReport(title=title, rows=tuple(rows))
