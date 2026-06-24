"""``cross-ref-report`` — a human-readable Markdown PRESENTATION of the dangling claim.

WHAT THIS IS. A read-only *renderer*. It consumes a
:class:`~lawvm.tools.dangling_references.DanglingReferenceReport` — the typed
artifact of the corpus-wide DANGLING-reference projection (claim
``lawvm.fi.reference.dangling.v1``) — and emits a structured, neutral,
independently-verifiable Markdown document: the kind a Finnish legal scholar, a
Finlex maintainer, or a journalist could read and check. It produces the
externally-legible form of an already-computed finding: which published RESOLVED
cross-references in Finnish law point at a target provision that is *absent* in
that target act's current consolidated text-state.

HONESTY BOUNDARY (constructive-invariant pattern — this module adds NO new
authority and NO new computation):

* This report is a PRESENTATION of the existing ``lawvm.fi.reference.dangling.v1``
  claim. It reads the report's counts and DANGLING witnesses verbatim and renders
  them. It does NOT re-classify, re-resolve, or widen the claim — there is no
  existence oracle in this module, no second opinion, no new verdict.
* Every DANGLING row shown is an **as-of-NOW** fact: the cited provision is absent
  in the target act's CURRENT consolidated text-state. It is NOT an as-of-citing
  defect (the citation may have been correct when enacted; the heavier
  ``broken-refs --provenance`` replay path answers that question, not this one).
* ``EXISTENCE_UNKNOWN`` rows are EXCLUDED from the findings. They are honest
  non-determinations (target act absent from the corpus, body not materialized,
  or no statute identity) — NOT broken references. Reporting one as DANGLING would
  be the cardinal false-positive sin the underlying claim refuses; this renderer
  inherits that discipline and never surfaces an unknown as a finding.
* Every row is INDEPENDENTLY CHECKABLE. The "How to verify" section gives a
  reader concrete, reproducible steps to confirm any single dangling row against
  the public Finlex consolidated text — no trust in LawVM required.
* The report makes NO legal conclusion. A dangling cross-reference is a textual /
  maintenance fact about the published statute corpus, NOT a ruling, NOT a defect
  finding about the substance of the law.

The renderer is a PURE function of the report (plus a declared scope label and a
display cap): same report in, same Markdown out — deterministic ordering, no
silent truncation (a capped findings list always states "showing top N of M").
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from lawvm.tools.dangling_references import (
    REASON_DANGLING_ABSENT,
    REASON_PRESENT,
    REASON_UNKNOWN_ACT_ABSENT,
    REASON_UNKNOWN_CONTENT_ABSENT,
    REASON_UNKNOWN_NO_STATUTE_ID,
    REASON_UNKNOWN_UNPARSEABLE_XML,
    DanglingReferenceReport,
    DanglingReferenceRow,
)

#: The claim this report presents (no new claim is introduced).
PRESENTED_CLAIM_ID = "lawvm.fi.reference.dangling.v1"

#: Default number of dangling witnesses rendered inline (full count always stated).
DEFAULT_TOP = 200

#: Human-readable one-liners for the closed reason vocabulary (rendered verbatim
#: from the underlying claim — this dict only TRANSLATES the codes, it does not
#: re-judge them).
_REASON_GLOSS: dict[str, str] = {
    REASON_PRESENT: "the cited provision resolves in the target act's current text",
    REASON_DANGLING_ABSENT: (
        "the target act is materialized but the cited provision resolves to no "
        "element in its current consolidated text-state"
    ),
    REASON_UNKNOWN_ACT_ABSENT: "the target act is not present in the local corpus",
    REASON_UNKNOWN_CONTENT_ABSENT: (
        "the target act exists but its body is an unmaterialized placeholder"
    ),
    REASON_UNKNOWN_NO_STATUTE_ID: "the target reference carries no statute identity",
    REASON_UNKNOWN_UNPARSEABLE_XML: "the target act's stored XML could not be parsed",
}


def _gloss(reason: str) -> str:
    return _REASON_GLOSS.get(reason, reason)


def _finlex_act_url(statute_id: str) -> str:
    """Best-effort public Finlex consolidated-text URL for a ``YYYY/NNNN`` act id.

    Finnish statute ids are ``year/number`` (the number may carry a suffix such
    as ``39-001``; the leading numeric token is the Finlex säädöskokoelma number).
    The URL is provided as a CONVENIENCE pointer for the reader's own check — the
    report makes no claim about the URL beyond "this is where to look". A
    non-standard id is rendered as-is with no fabricated link.
    """
    parts = statute_id.split("/")
    if len(parts) < 2:
        return statute_id
    year = parts[0]
    number = parts[1].split("-")[0]
    if not (year.isdigit() and number.isdigit()):
        return statute_id
    return f"https://www.finlex.fi/fi/laki/ajantasa/{year}/{year}{number}"


def _md_escape(text: str) -> str:
    """Escape the Markdown pipe so a provision-ref never breaks a table cell."""
    return text.replace("|", "\\|")


def _percent(part: int, whole: int) -> str:
    if whole == 0:
        return "n/a"
    return f"{100.0 * part / whole:.2f}%"


def _group_dangling_by_target(
    rows: tuple[DanglingReferenceRow, ...],
) -> list[tuple[str, list[DanglingReferenceRow]]]:
    """Group dangling rows by target act, deterministically ordered.

    Groups are ordered by (descending row count, ascending target_statute_id) so
    the most-cited dead targets surface first with a stable tiebreak; rows inside
    a group keep the report's own deterministic sort.
    """
    by_target: dict[str, list[DanglingReferenceRow]] = {}
    for row in rows:
        by_target.setdefault(row.target_statute_id, []).append(row)
    return sorted(by_target.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def render_cross_reference_integrity_report(
    report: DanglingReferenceReport,
    *,
    scope_label: str,
    top: int = DEFAULT_TOP,
) -> str:
    """Render ``report`` as a structured, verifiable Markdown document.

    ``scope_label`` is a free-text declaration of WHAT corpus slice the report was
    computed over (e.g. "full Finlex consolidated corpus, fi_refs export of
    2026-06-24" or "a 17,912-reference slice") — it is printed prominently so the
    reader never mistakes a slice for the whole. ``top`` caps the inline findings
    list; the full count is always stated (no silent truncation).

    This is a PURE function: it reads only ``report`` (and the two display
    parameters). It performs no classification and consults no oracle.
    """
    if top < 0:
        raise ValueError(f"top must be non-negative, got {top}")

    checked = report.resolved_checked
    lines: list[str] = []

    # --- Title -------------------------------------------------------------- #
    lines.append("# Cross-reference integrity of the Finnish statute corpus")
    lines.append("")
    lines.append(
        "_A neutral, independently-verifiable presentation of dangling "
        "cross-references — generated by LawVM from claim "
        f"`{PRESENTED_CLAIM_ID}`. This is a textual/maintenance fact about the "
        "published corpus, **not** a legal conclusion._"
    )
    lines.append("")
    lines.append(f"**Scope of this run:** {scope_label}")
    lines.append("")

    # --- Summary ------------------------------------------------------------ #
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"Of **{checked:,}** RESOLVED cross-references checked (every reference "
        "asserting a specific target provision), each was classified into exactly "
        "one of three states against the target act's current consolidated "
        "text-state:"
    )
    lines.append("")
    lines.append("| State | Count | Share | Meaning (one sentence) |")
    lines.append("| --- | ---: | ---: | --- |")
    lines.append(
        f"| PRESENT | {report.present:,} | {_percent(report.present, checked)} | "
        "the cited target provision is found in the target act's current text. |"
    )
    lines.append(
        f"| DANGLING | {report.dangling:,} | {_percent(report.dangling, checked)} | "
        "the target act is present and materialized, yet the cited provision "
        "resolves to nothing — a broken cross-reference, as of now. |"
    )
    lines.append(
        f"| EXISTENCE_UNKNOWN | {report.existence_unknown:,} | "
        f"{_percent(report.existence_unknown, checked)} | existence could not be "
        "determined (target act absent from the corpus, body not materialized, or "
        "no statute identity) — **not** counted as broken. |"
    )
    lines.append("")
    lines.append(
        f"The three counts sum to the {checked:,} checked references (totality is "
        "enforced by the underlying report's constructor). A further "
        f"{sum(report.excluded_non_resolved.values()):,} non-resolved references "
        "(see *Methodology & limits*) were out of scope and not existence-checked; "
        f"the export contained {report.total_rows:,} reference rows in total."
    )
    lines.append("")

    # --- Methodology & limits (prominent, near the top) --------------------- #
    lines.append("## Methodology & limits")
    lines.append("")
    lines.append(
        "This section is the credibility of the report. Read it before the "
        "findings."
    )
    lines.append("")
    lines.append(
        f"- **One claim, no new authority.** This document presents the existing "
        f"LawVM claim `{PRESENTED_CLAIM_ID}` and adds no new computation. Every "
        "count and every row below is read verbatim from that claim's typed "
        "report; this renderer classifies nothing."
    )
    lines.append(
        "- **As-of-now existence oracle.** A reference is judged against the "
        "target act's *current* consolidated text-state (read for free from the "
        "Finlex consolidated oracle — no point-in-time replay). A DANGLING verdict "
        "means **“absent in the current text”**, nothing more."
    )
    lines.append(
        "- **As-of-citing residual (declared non-guarantee "
        "`dangling_existence_oracle_as_of_now_not_as_of_citing`).** A reference "
        "whose target existed when the citation was written but has since been "
        "repealed or renumbered reads DANGLING here, yet may have been correct "
        "when enacted. This report does NOT perform the as-of-citing replay; that "
        "is the separate `broken-refs --provenance` path."
    )
    lines.append(
        "- **Resolved-only scope (declared non-guarantee "
        "`dangling_resolved_only_scope_section_granularity`).** Only references "
        "that confidently name a single target provision (cite confidence "
        "`exact` / `approximate`) are checked. Honest non-resolutions "
        "(`statute_only`, `ambiguous`, `open`, ...) named no single target, so "
        "there is nothing to existence-check; they are counted separately and are "
        "NOT findings."
    )
    lines.append(
        "- **Tag, don't guess (the no-false-positive discipline).** Existence is "
        "resolved into three states, never two. An `EXISTENCE_UNKNOWN` is an "
        "honest non-determination and is **excluded** from the findings — it is "
        "never reported as broken. The corpus may be incomplete (declared "
        "non-guarantee "
        "`dangling_existence_oracle_current_state_incomplete_corpus`); where it is, "
        "the answer is UNKNOWN, not DANGLING."
    )
    lines.append(
        "- **Element-granularity boundary.** Existence is resolved at section "
        "(and embedded chapter) granularity. A few Finlex acts render a "
        "renumbered range as a single merged section element, so a citation to a "
        "sub-member of that range can read DANGLING even though the text is "
        "present inside the merged span. This is a resolution-granularity "
        "boundary, not a claim that the law is broken."
    )
    lines.append("")

    # Reason breakdowns (verbatim from the report).
    if report.dangling_by_reason:
        lines.append("**DANGLING by reason:**")
        lines.append("")
        for reason, n in sorted(report.dangling_by_reason.items()):
            lines.append(f"- `{reason}` ({n:,}) — {_gloss(reason)}")
        lines.append("")
    if report.unknown_by_reason:
        lines.append("**EXISTENCE_UNKNOWN by reason (excluded from findings):**")
        lines.append("")
        for reason, n in sorted(report.unknown_by_reason.items()):
            lines.append(f"- `{reason}` ({n:,}) — {_gloss(reason)}")
        lines.append("")
    if report.excluded_non_resolved:
        lines.append("**Out-of-scope (non-resolved) references by cite confidence:**")
        lines.append("")
        for conf, n in sorted(report.excluded_non_resolved.items()):
            lines.append(f"- `{conf}` ({n:,})")
        lines.append("")

    # --- Findings ----------------------------------------------------------- #
    lines.append("## Findings: dangling cross-references")
    lines.append("")
    total_dangling = report.dangling
    if total_dangling == 0:
        lines.append(
            "No dangling cross-references were found in this run. Every checked "
            "RESOLVED reference is either PRESENT or EXISTENCE_UNKNOWN."
        )
        lines.append("")
    else:
        shown = min(top, total_dangling)
        lines.append(
            f"**Showing top {shown:,} of {total_dangling:,}** dangling references, "
            "grouped by the target (cited) act. The most-cited absent targets "
            "appear first. Within each group: source provision → cited target "
            "provision. Ordering is deterministic."
        )
        lines.append("")
        rendered = 0
        for target_id, group in _group_dangling_by_target(report.dangling_rows):
            if rendered >= shown:
                break
            url = _finlex_act_url(target_id)
            if url != target_id:
                heading_target = f"[`{target_id}`]({url})"
            else:
                heading_target = f"`{target_id}`"
            lines.append(
                f"### Target act {heading_target} — {len(group):,} dangling "
                "reference(s) into it"
            )
            lines.append("")
            lines.append("| Citing act | Citing provision | Cited target provision | Reason |")
            lines.append("| --- | --- | --- | --- |")
            for row in group:
                if rendered >= shown:
                    lines.append(
                        "| ... | ... | ... | _(remaining rows in this group "
                        "truncated by the display cap; see the full JSON report) "
                        "_ |"
                    )
                    break
                lines.append(
                    f"| `{_md_escape(row.source_statute_id)}` "
                    f"| `{_md_escape(row.source_provision_ref_str)}` "
                    f"| `{_md_escape(row.target_provision_ref_str)}` "
                    f"| {_gloss(row.reason)} |"
                )
                rendered += 1
            lines.append("")
        if shown < total_dangling:
            lines.append(
                f"_{total_dangling - shown:,} further dangling reference(s) are not "
                "shown inline. Run `lawvm dangling-refs --out report.json` for the "
                "complete, capped-free witness list._"
            )
            lines.append("")

    # --- How to verify ------------------------------------------------------ #
    lines.append("## How to verify a finding independently")
    lines.append("")
    lines.append(
        "Each dangling row is checkable against the public Finlex consolidated "
        "text, with no trust in LawVM required. For a given row "
        "(*citing act* / *citing provision* → *cited target provision*):"
    )
    lines.append("")
    lines.append(
        "1. Read the **cited target provision**, e.g. `1994/750/46`: the leading "
        "token is the target act id (`1994/750`) and the trailing token is the "
        "cited section (`§ 46`)."
    )
    lines.append(
        "2. Open that act's current consolidated text on Finlex "
        "(`https://www.finlex.fi/fi/laki/ajantasa/<year>/<year><number>`, e.g. "
        "<https://www.finlex.fi/fi/laki/ajantasa/1994/19940750>). The act will be "
        "present — a DANGLING verdict is only issued when the target act IS in "
        "the corpus and materialized."
    )
    lines.append(
        "3. Confirm the cited section is **absent**: the section numbering skips "
        "it (e.g. the act runs § 44a then § 52, with no § 46), or the "
        "section was repealed/renumbered. If the section is present, the row would "
        "have read PRESENT — report a discrepancy."
    )
    lines.append(
        "4. For an exact, machine-checkable reproduction over the same data, run "
        "`lawvm dangling-refs --fi-refs <fi_refs.jsonl>` (the projection this "
        "report was generated from). The DANGLING witnesses it prints are the rows "
        "above; the three-way counts match the Summary."
    )
    lines.append("")
    lines.append(
        "Note the **as-of-citing residual**: a section absent today may have "
        "existed when the citing text was enacted. Absence-now is a maintenance "
        "signal (the live cross-reference no longer resolves), not by itself proof "
        "the original drafting was wrong."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI — run the dangling claim (or read a saved JSON report) and render Markdown.
# ---------------------------------------------------------------------------


def _load_report_from_json(path: str) -> DanglingReferenceReport:
    """Reconstruct a ``DanglingReferenceReport`` from a saved ``dangling-refs --out`` JSON.

    Re-runs the typed constructor (re-asserting totality + closed-status
    invariants on read) so a hand-edited or corrupt JSON cannot be rendered into
    a report that violates the claim's own guards.
    """
    import json

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = tuple(
        DanglingReferenceRow(
            source_statute_id=r["source_statute_id"],
            source_provision_ref_str=r["source_provision_ref_str"],
            target_statute_id=r["target_statute_id"],
            target_provision_ref_str=r["target_provision_ref_str"],
            cite_confidence=r["cite_confidence"],
            cite_kind=r["cite_kind"],
            existence_status=r["existence_status"],
            reason=r["reason"],
            valid_at_start=r.get("valid_at_start"),
            valid_at_end=r.get("valid_at_end"),
        )
        for r in payload.get("dangling_rows", [])
    )
    return DanglingReferenceReport(
        total_rows=int(payload["total_rows"]),
        resolved_checked=int(payload["resolved_checked"]),
        excluded_non_resolved=dict(payload.get("excluded_non_resolved", {})),
        present=int(payload["present"]),
        dangling=int(payload["dangling"]),
        existence_unknown=int(payload["existence_unknown"]),
        unknown_by_reason=dict(payload.get("unknown_by_reason", {})),
        dangling_by_reason=dict(payload.get("dangling_by_reason", {})),
        dangling_rows=rows,
    )


def main(args: argparse.Namespace) -> None:
    import os

    json_in: Optional[str] = getattr(args, "report_json", None)
    fi_refs_path: Optional[str] = getattr(args, "fi_refs", None)
    out_path: Optional[str] = getattr(args, "out", None)
    top: int = int(getattr(args, "top", DEFAULT_TOP) or DEFAULT_TOP)
    scope_label: Optional[str] = getattr(args, "scope_label", None)

    if json_in:
        report = _load_report_from_json(json_in)
        default_scope = f"saved dangling-refs report: {json_in}"
    else:
        # Run the claim fresh over the fi_refs projection.
        from lawvm.tools.dangling_references import (
            CurrentStateExistenceOracle,
            _default_fi_refs_path,
            _resolve_store,
            build_dangling_report,
        )

        refs_path = fi_refs_path or _default_fi_refs_path()
        if not os.path.exists(refs_path):
            raise SystemExit(
                f"ERROR: fi_refs projection not found at {refs_path!r}. Generate it "
                "(lawvm export-fi-refs) or pass --fi-refs PATH, or render a saved "
                "report with --report-json PATH."
            )
        print(
            f"cross-ref-report: running the dangling claim over {refs_path} ...",
            file=sys.stderr,
        )
        oracle = CurrentStateExistenceOracle(_resolve_store())
        report = build_dangling_report(refs_path, oracle)
        default_scope = f"fi_refs projection: {refs_path}"

    markdown = render_cross_reference_integrity_report(
        report, scope_label=scope_label or default_scope, top=top
    )

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        print(f"cross-ref-report: wrote Markdown -> {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(markdown)


__all__ = [
    "DEFAULT_TOP",
    "PRESENTED_CLAIM_ID",
    "main",
    "render_cross_reference_integrity_report",
]
