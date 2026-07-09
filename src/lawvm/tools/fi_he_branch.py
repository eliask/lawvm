"""``lawvm fi-he-branch`` — run the draft-HE source_document pipeline from farchive.

The entry point that wires the producer-neutral source_document subsystem to the
real corpus: it reads a government-proposal (HE) PDF out of
``fi_government_proposal.farchive`` (imported by ``acquire-fi-proposals
--include-pdfs``), lowers it to a ``ProposalPackage`` of non-authoritative
``ConditionalBranch``es ("if enacted, then …"), and — with ``--materialize`` —
compiles each targeted enacted statute (full amendment replay) to show the
counterfactual "if this HE passes" provision + diff.

Everything here is READ-ONLY over enacted law: a materialized provision is a
branch view, never replay-authorized. A PDF absent from the farchive, an
unresolved target statute, or a section not in the corpus is a TYPED finding,
never a crash and never a fabricated op (AGENTS.md §1.8).
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from lawvm.core.source_document import (
    ProposalAuthorityStatus,
    ProposalPackage,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import AssuranceTier
from lawvm.finland.he_acquisition import he_locator
from lawvm.finland.source_document import (
    he_pdf_to_proposal,
    load_enacted_provision,
    load_manifestation_from_farchive,
)

_DEFAULT_FARCHIVE = "data/fi_government_proposal.farchive"


@dataclass(frozen=True, slots=True)
class HeBranchResult:
    """The lowered proposal + (optional) materialized counterfactual provisions."""

    proposal_id: str
    package: ProposalPackage
    materialized: Tuple[dict, ...]  # one per resolved provision: statute/section/diff/findings
    findings: Tuple[str, ...]


def _section_label(provision_ref: str) -> str:
    # lawvm-regex: owning_parser — bounded locator pulling the section label out of a structured provision_ref (section:N/...); not prose interpretation
    m = re.search(r"section:(\w+)", provision_ref)
    return m.group(1) if m else ""


def load_he_manifestation(
    year: int, number: int, *, lang: str = "fin", farchive_path: str = _DEFAULT_FARCHIVE
) -> Optional[SourceManifestation]:
    """Load an HE ``main.pdf`` from the gov-proposal farchive, or ``None`` if absent."""
    locator = he_locator(year, number, lang, "main.pdf")
    try:
        return load_manifestation_from_farchive(
            locator, farchive_path=farchive_path, source_role="he_draft"
        )
    except Exception:
        return None


def run_he_branch(
    year: int,
    number: int,
    *,
    lang: str = "fin",
    farchive_path: str = _DEFAULT_FARCHIVE,
    adjudicate: bool = False,
    materialize: bool = False,
) -> HeBranchResult:
    """Lower one farchive HE PDF to conditional branches (+ optional materialization)."""
    proposal_id = f"fi:he:{year}/{number}"
    findings: List[str] = []

    manifestation = load_he_manifestation(year, number, lang=lang, farchive_path=farchive_path)
    if manifestation is None:
        findings.append(
            f"HE {year}/{number} main.pdf ({lang}) not in {farchive_path} — "
            "run `lawvm acquire-fi-proposals --include-pdfs` to import gov-proposal PDFs"
        )
        empty_root = SourceDocumentNode(
            kind=SourceDocumentNodeKind.WORK_ROOT,
            assurance_tier=AssuranceTier.UNADJUDICATED_PROPOSAL,
            anchor=SourceAnchor(artifact_digest="0" * 64, locator=he_locator(year, number, lang, "main.pdf")),
        )
        empty = ProposalPackage(
            proposal_id=proposal_id,
            source_manifestation_digests=(),
            branches=(),
            reasoning_root=empty_root,
            authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
            findings=(),  # run-level finding lives on the result, not the package
        )
        return HeBranchResult(proposal_id, empty, (), tuple(findings))

    adjudicator = None
    if adjudicate:
        from lawvm.finland.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator

        adj = LlmWorkflowAdjudicator(verify_pass=False, max_tokens=700)
        if adj.is_available():
            adjudicator = adj
        else:
            findings.append("adjudicator requested but no llama.cpp server reachable — single-witness")

    package = he_pdf_to_proposal(manifestation, proposal_id, adjudicator=adjudicator)

    materialized: List[dict] = []
    if materialize:
        import asyncio

        from lawvm.finland.source_document import materialize_conditional_provision

        for branch in package.branches:
            targets = {
                (op.target_statute_id, _section_label(op.target_provision_ref))
                for op in branch.candidate_ops
            }
            for sid, sec in sorted(targets):
                if not sid or not sec:
                    continue
                try:
                    enacted = asyncio.run(load_enacted_provision(sid, sec))
                except Exception as exc:  # replay error is a typed skip, not a crash
                    materialized.append(
                        {"statute": sid, "section": sec, "materialize_status": f"replay_error:{type(exc).__name__}"}
                    )
                    continue
                if enacted is None:
                    materialized.append(
                        {"statute": sid, "section": sec, "materialize_status": "section_not_in_corpus"}
                    )
                    continue
                mat = materialize_conditional_provision(
                    enacted, branch, statute_id=sid, provision_ref=f"section:{sec}"
                )
                materialized.append(
                    {
                        "statute": sid,
                        "section": sec,
                        "materialize_status": "materialized",
                        "enacted_children": len(mat.enacted_ir.children),
                        "conditional_children": len(mat.conditional_ir.children),
                        "diff": list(mat.diff_lines),
                        "findings": list(mat.findings),
                    }
                )

    return HeBranchResult(proposal_id, package, tuple(materialized), tuple(findings))


def _render_text(result: HeBranchResult) -> str:
    pkg = result.package
    out: List[str] = []
    out.append(f"HE {result.proposal_id}  (replay_authorized={pkg.replay_authorized})")
    out.append(f"  branches: {len(pkg.branches)}   candidate ops: {sum(len(b.candidate_ops) for b in pkg.branches)}")
    for br in pkg.branches:
        out.append(f"  branch {br.branch_id}  ({br.condition})")
        for op in br.candidate_ops:
            out.append(
                f"    {op.action:16} {op.target_statute_id:9} {op.target_provision_ref:34} "
                f"tier={op.assurance_tier.name}"
            )
    for m in result.materialized:
        if m.get("materialize_status") == "materialized":
            out.append(
                f"  MATERIALIZE {m['statute']} §{m['section']}: "
                f"{m['enacted_children']} -> {m['conditional_children']} children"
            )
            for d in m.get("diff", [])[:3]:
                out.append(f"      {d}")
        else:
            out.append(f"  MATERIALIZE {m['statute']} §{m['section']}: {m['materialize_status']} (typed skip)")
    for f in list(pkg.findings) + list(result.findings):
        out.append(f"  finding: {f}")
    return "\n".join(out)


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-he-branch``."""
    year = int(args.year)
    number = int(args.number)
    lang = getattr(args, "lang", "fin") or "fin"
    dest = getattr(args, "dest", None) or _DEFAULT_FARCHIVE
    result = run_he_branch(
        year,
        number,
        lang=lang,
        farchive_path=dest,
        adjudicate=bool(getattr(args, "adjudicate", False)),
        materialize=bool(getattr(args, "materialize", False)),
    )
    if getattr(args, "json", False):
        pkg = result.package
        payload = {
            "proposal_id": result.proposal_id,
            "replay_authorized": pkg.replay_authorized,
            "branches": [
                {
                    "branch_id": br.branch_id,
                    "condition": br.condition,
                    "ops": [
                        {
                            "action": op.action,
                            "target_statute_id": op.target_statute_id,
                            "target_provision_ref": op.target_provision_ref,
                            "assurance_tier": op.assurance_tier.name,
                        }
                        for op in br.candidate_ops
                    ],
                }
                for br in pkg.branches
            ],
            "materialized": list(result.materialized),
            "findings": list(pkg.findings) + list(result.findings),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_text(result))
