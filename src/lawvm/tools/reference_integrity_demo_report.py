"""``reference-integrity-report`` — the demo-grade FI reference-integrity Markdown.

WHAT THIS IS. A read-only ASSEMBLER that composes three already-computed,
independently-verifiable surfaces into one neutral Markdown document a Finnish
legislative-tracking partner can read and check:

1. The corpus-wide DANGLING cross-reference claim (``lawvm.fi.reference.dangling.v1``)
   rendered by :func:`render_cross_reference_integrity_report` — the headline
   PRESENT / DANGLING / EXISTENCE_UNKNOWN three-way split.
2. The DANGLING-by-temporal-CAUSE split
   (:mod:`lawvm.tools.dangling_temporal_cause`): of the DANGLING set, how many
   cite a provision that was REPEALED (evidenced by an in-place repeal note in the
   target act citing the amending act + date) vs how many are UNDETERMINED (absent
   with no repeal note; the as-of-now oracle cannot tell repealed-without-note /
   renumbered / never-materialized apart — the honest residual).
3. The EU-directive / CELEX reference category
   (:mod:`lawvm.tools.eu_reference_report`): FI statutes that reference an EU
   instrument, with declared transposition relationships (CELEX-bound where
   determinable).

HONESTY BOUNDARY (load-bearing — this feeds an external partner demo):

* Every number is a SURFACE fact with its determination method stated. A REPEALED
  classification CITES the repeal evidence (the amending act + date Finlex
  rendered in the target text). Where the temporal model cannot determine the
  cause, the row is UNDETERMINED — never guessed "never existed".
* A transposition relationship means the FI act DECLARES it transposes the named
  directive — NOT a verified conformance.
* This assembler adds NO new computation: it reads the three typed reports and
  lays them out. Its honesty is exactly the union of the three underlying claims'
  honesty boundaries, restated where a reader will see them.

The assembler is a PURE function of the three reports plus a scope label.
"""
from __future__ import annotations

import sys
from typing import Any, Optional

from lawvm.tools.cross_reference_integrity_report import (
    render_cross_reference_integrity_report,
)
from lawvm.tools.dangling_references import DanglingReferenceReport
from lawvm.tools.dangling_temporal_cause import DanglingCauseReport
from lawvm.tools.eu_reference_report import EuReferenceReport


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "n/a"
    return f"{100.0 * part / whole:.2f}%"


def _render_cause_section(cause: DanglingCauseReport, *, top: int) -> list[str]:
    lines: list[str] = []
    lines.append("## DANGLING split by temporal cause (repealed vs undetermined)")
    lines.append("")
    lines.append(
        "Each DANGLING cross-reference points at a provision that resolves to "
        "nothing in the target act's current consolidated text. This section asks "
        "*why*, using a single POSITIVE signal: an in-place repeal note in the "
        "target act's published text (Finlex renders a repealed unit as "
        "`“<spec> § on kumottu L:lla <amending act/date>”`)."
    )
    lines.append("")
    lines.append("| Cause | Count | Share of DANGLING | Determination |")
    lines.append("| --- | ---: | ---: | --- |")
    lines.append(
        f"| DANGLING_REPEALED_TARGET | {cause.repealed_target:,} | "
        f"{_pct(cause.repealed_target, cause.total_dangling)} | a repeal note in "
        "the target act covers the cited provision and **names the amending act + "
        "date** — the repeal is evidenced, not inferred. |"
    )
    lines.append(
        f"| DANGLING_CAUSE_UNDETERMINED | {cause.undetermined:,} | "
        f"{_pct(cause.undetermined, cause.total_dangling)} | the provision is "
        "absent and **no repeal note covers it**. The as-of-now consolidated oracle "
        "cannot distinguish repealed-without-an-in-place-note / renumbered / "
        "never-materialized. Reported as UNDETERMINED, **never** as never-existed. |"
    )
    lines.append("")
    lines.append(
        f"The two causes sum to the {cause.total_dangling:,} DANGLING references "
        "(totality is enforced by the cause report's constructor). The repeal-note "
        "matcher is RANGE-AWARE: Finlex collapses a repealed span into one note "
        "(e.g. `“67–84 § on kumottu L:lla 16.4.1987/411”`), so a "
        "citation to any section inside that span is attributed to that repeal."
    )
    lines.append("")
    lines.append(
        "**Honesty note.** Absence of a repeal note is NOT evidence that the "
        "provision never existed — Finlex does not always leave an in-place note "
        "for an older renumber/repeal. The split therefore over-reports UNDETERMINED "
        "rather than over-claiming either cause. Determining the residual would "
        "require the heavier as-of-citing replay path (declared out of scope here)."
    )
    lines.append("")
    if cause.repealed_rows:
        shown = min(top, len(cause.repealed_rows))
        lines.append(
            f"**Evidenced repealed-target witnesses** (showing {shown:,} of "
            f"{cause.repealed_target:,}). Each cites a provision covered by a repeal "
            "note that names the repealing act:"
        )
        lines.append("")
        lines.append(
            "| Citing act | Citing provision | Cited (repealed) target | "
            "Repealed by (amending act/date) | Repeal-note span |"
        )
        lines.append("| --- | --- | --- | --- | --- |")
        for row in cause.repealed_rows[:shown]:
            lines.append(
                f"| `{row.source_statute_id}` | `{row.source_provision_ref_str}` | "
                f"`{row.target_provision_ref_str}` | **{row.amending_act}** | "
                f"`{row.repeal_spec} {row.repeal_unit}` |"
            )
        lines.append("")
        if shown < cause.repealed_target:
            lines.append(
                f"_{cause.repealed_target - shown:,} further evidenced repealed "
                "witnesses are in the JSON report._"
            )
            lines.append("")
    lines.append(
        f"The UNDETERMINED residual spans **{len(cause.undetermined_targets):,} "
        "distinct target acts**. These are honest non-determinations, not findings."
    )
    lines.append("")
    return lines


def _render_eu_section(eu: EuReferenceReport, *, top: int) -> list[str]:
    lines: list[str] = []
    lines.append("## EU-directive / CELEX reference category")
    lines.append("")
    lines.append(
        "Finnish statutes that REFERENCE an EU instrument in their body text, "
        "surfaced via LawVM's deterministic EU extractors. A transposition "
        "relationship means the FI act **declares** it transposes the named "
        "directive — it is **not** a verified conformance assessment."
    )
    lines.append("")
    lines.append(f"**Scanned:** {eu.statutes_scanned:,} FI statutes.")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("| --- | ---: |")
    lines.append(
        f"| FI acts declaring a directive transposition | {eu.transposition_acts:,} |"
    )
    lines.append(f"| Transposition declarations (total) | {eu.transposition_claims:,} |")
    lines.append(
        f"| — directive bound to a single CELEX | {eu.transposition_bound:,} |"
    )
    lines.append(
        f"| — directive named but unbound (tag, don't guess) | "
        f"{eu.transposition_unbound:,} |"
    )
    lines.append(f"| FI acts citing an EU instrument (general) | {eu.eu_citation_acts:,} |")
    lines.append(f"| Primary EU-act citation spans | {eu.eu_citation_spans:,} |")
    lines.append(f"| Bare-CELEX citation spans | {eu.celex_spans:,} |")
    lines.append(
        f"| Embedded-repeal provenance spans (excluded from operative count) | "
        f"{eu.eu_citation_embedded_repeal_spans:,} |"
    )
    lines.append("")
    if eu.transposition_witnesses:
        shown = min(top, len(eu.transposition_witnesses))
        lines.append(
            f"**Declared transposition witnesses** (showing {shown:,} of "
            f"{len(eu.transposition_witnesses):,} captured):"
        )
        lines.append("")
        lines.append(
            "| FI act | Directive (CELEX or surface) | Binding | Transposition deadline |"
        )
        lines.append("| --- | --- | --- | --- |")
        for w in eu.transposition_witnesses[:shown]:
            directive = w.directive_celex or f"_(unbound: {w.directive_surface})_"
            deadline = w.transposition_deadline or "—"
            lines.append(
                f"| `{w.citing_statute_id}` | {directive} | {w.binding_status} | "
                f"{deadline} |"
            )
        lines.append("")
    if eu.eu_citation_witnesses:
        shown = min(top, len(eu.eu_citation_witnesses))
        lines.append(
            f"**General EU-citation witnesses** (showing {shown:,} of "
            f"{len(eu.eu_citation_witnesses):,} captured):"
        )
        lines.append("")
        lines.append("| FI act | Cited EU instrument |")
        lines.append("| --- | --- |")
        for w in eu.eu_citation_witnesses[:shown]:
            ident = w.celex or f"({w.eu_form} N:o {w.eu_number}/{w.eu_year})"
            lines.append(f"| `{w.citing_statute_id}` | `{ident}` |")
        lines.append("")
    lines.append(
        "**Honesty note.** This is a recognition SURFACE, not a complete "
        "EU-relationship ledger: a FI act may reference an EU instrument in a form "
        "the deterministic recognizers do not cover; such a reference is simply "
        "absent here, not denied. The transposition extractor is conservative "
        "(it requires an explicit “täytäntöönpanemiseksi”-class "
        "declaration), so the transposition counts are a floor."
    )
    lines.append("")
    return lines


def render_reference_integrity_demo_report(
    dangling: DanglingReferenceReport,
    cause: DanglingCauseReport,
    eu: EuReferenceReport,
    *,
    scope_label: str,
    dangling_top: int = 60,
    cause_top: int = 60,
    eu_top: int = 30,
) -> str:
    """Compose the three reports into one demo-grade Markdown document.

    Pure function: reads only the three typed reports + display parameters. The
    DANGLING three-way body is rendered by the existing cross-ref renderer (no new
    classification); the cause split and EU category are appended as sections.
    """
    head: list[str] = []
    head.append("# Reference integrity of the Finnish statute corpus")
    head.append("")
    head.append(
        "_A neutral, independently-verifiable reference-integrity report generated "
        "by LawVM over the published Finnish statute corpus. Every figure is a "
        "surface fact about the published text with its determination method "
        "stated — **not** a legal conclusion. Three surfaces follow: (1) the "
        "DANGLING cross-reference split, (2) the DANGLING-by-cause split "
        "(references to already-repealed provisions vs undetermined), and (3) the "
        "EU-directive / CELEX reference category._"
    )
    head.append("")
    head.append(f"**Scope of this run:** {scope_label}")
    head.append("")
    head.append("---")
    head.append("")

    body = render_cross_reference_integrity_report(
        dangling, scope_label=scope_label, top=dangling_top
    ).splitlines()

    sections: list[str] = []
    sections.append("")
    sections.append("---")
    sections.append("")
    sections.extend(_render_cause_section(cause, top=cause_top))
    sections.append("---")
    sections.append("")
    sections.extend(_render_eu_section(eu, top=eu_top))

    return "\n".join(head + body + sections) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    import os

    fi_refs_path: Optional[str] = getattr(args, "fi_refs", None)
    out_path: Optional[str] = getattr(args, "out", None)
    scope_label: Optional[str] = getattr(args, "scope_label", None)
    eu_limit: Optional[int] = getattr(args, "eu_limit", None)

    from lawvm.tools.dangling_references import (
        CurrentStateExistenceOracle,
        _default_fi_refs_path,
        _resolve_store,
        build_dangling_report,
    )
    from lawvm.tools.dangling_temporal_cause import (
        RepealNoteCauseOracle,
        classify_dangling_causes,
    )
    from lawvm.tools.eu_reference_report import build_eu_reference_report

    refs_path = fi_refs_path or _default_fi_refs_path()
    if not os.path.exists(refs_path):
        raise SystemExit(
            f"ERROR: fi_refs projection not found at {refs_path!r}. Generate it "
            "(lawvm export-fi-refs) or pass --fi-refs PATH."
        )

    store = _resolve_store()
    print(
        f"reference-integrity-report: classifying dangling references over {refs_path} ...",
        file=sys.stderr,
    )
    dangling = build_dangling_report(refs_path, CurrentStateExistenceOracle(store))
    print(
        "reference-integrity-report: splitting DANGLING by temporal cause ...",
        file=sys.stderr,
    )
    cause = classify_dangling_causes(dangling, RepealNoteCauseOracle(store))
    print(
        "reference-integrity-report: scanning corpus for EU-instrument references ...",
        file=sys.stderr,
    )
    ids = store.list_statute_ids()
    if eu_limit is not None:
        ids = ids[: int(eu_limit)]
    eu = build_eu_reference_report(store, ids)

    default_scope = (
        f"full Finlex consolidated corpus; fi_refs projection {refs_path}; "
        f"{len(ids):,} statutes scanned for EU references"
    )
    markdown = render_reference_integrity_demo_report(
        dangling, cause, eu, scope_label=scope_label or default_scope
    )

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        print(f"reference-integrity-report: wrote Markdown -> {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(markdown)


__all__ = [
    "main",
    "render_reference_integrity_demo_report",
]
