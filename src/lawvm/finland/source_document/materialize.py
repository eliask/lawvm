"""Materialize a conditional branch against enacted law — "if enacted, then …".

The payoff of the draft-HE pipeline: take the CURRENTLY ENACTED provision, apply a
proposal's candidate operation on a NON-authoritative branch, and emit the
counterfactual provision IR + a human diff — "if HE X is enacted, §4 gains a new
5th momentti reading '…4 senttiä litralta'". This is what turns a parsed op into
*law you can read*.

``load_enacted_provision`` compiles a real Finnish statute (full amendment replay,
``lawvm.finland.graph.build_statute_graph_fi``) and returns a section's live IR.
``materialize_conditional_provision`` applies a ``ConditionalBranch``'s candidate
ops onto that IR structurally — INSERT appends the new unit, REPLACE swaps the
matching child, REPEAL removes it — producing the conditional IR. The result is
NEVER replay-authorized: it is a branch view, not an edit of the enacted timeline.

Discipline (AGENTS.md §1.9, §1.10): the apply + diff are pure and testable on a
provided IR (no corpus needed); an op the structural apply cannot place is a
typed finding, never a silent no-op.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_document.proposal import CandidateOperation, ConditionalBranch


@dataclass(frozen=True, slots=True)
class MaterializedProvision:
    """The counterfactual provision + how it differs from the enacted one."""

    statute_id: str
    provision_ref: str
    enacted_ir: IRNode
    conditional_ir: IRNode
    diff_lines: Tuple[str, ...]
    findings: Tuple[str, ...]
    replay_authorized: bool = False

    def __post_init__(self) -> None:
        if self.replay_authorized:
            raise ValueError("MaterializedProvision is a branch view — never replay-authorized")


def _subsection_label(provision_ref: str) -> str:
    """Pull the new-unit label from a ``section:4/subsection:5`` provision ref."""
    for part in provision_ref.split("/"):
        kind, _, label = part.partition(":")
        if kind in ("subsection", "momentti", "item") and label:
            return label
    return ""


def _new_unit_node(op: CandidateOperation) -> IRNode:
    """Build the IR node the proposal would add (a new momentti / provision unit)."""
    label = _subsection_label(op.target_provision_ref)
    return IRNode(
        kind=IRNodeKind.P,
        label=label or None,
        text=op.payload_text.strip(),
        attrs={
            "conditional": "1",
            "conditional_action": op.action,
            "source_johtolause": op.raw_johtolause[:80],
        },
    )


def apply_candidate_op(provision_ir: IRNode, op: CandidateOperation) -> Tuple[IRNode, Optional[str]]:
    """Apply one candidate op onto a provision IR. Returns (new_ir, finding-or-None).

    Structural, pure: INSERT appends the new unit; REPLACE swaps a same-label
    child; REPEAL drops it. An op that cannot be placed returns the IR unchanged
    plus a typed finding — never a silent no-op.
    """
    action = op.action.lower()
    label = _subsection_label(op.target_provision_ref)
    if action == "insert":
        new_children = provision_ir.children + (_new_unit_node(op),)
        return _with_children(provision_ir, new_children), None
    if action == "replace":
        replaced = False
        out: List[IRNode] = []
        for ch in provision_ir.children:
            if label and ch.label == label:
                out.append(_new_unit_node(op))
                replaced = True
            else:
                out.append(ch)
        if not replaced:
            return provision_ir, f"replace target unit '{label}' not found in provision"
        return _with_children(provision_ir, tuple(out)), None
    if action in ("repeal", "delete"):
        kept = tuple(ch for ch in provision_ir.children if not (label and ch.label == label))
        if len(kept) == len(provision_ir.children):
            return provision_ir, f"repeal target unit '{label}' not found in provision"
        return _with_children(provision_ir, kept), None
    return provision_ir, f"unsupported structural action '{op.action}' — not materialized"


def _with_children(node: IRNode, children: Tuple[IRNode, ...]) -> IRNode:
    return IRNode(kind=node.kind, label=node.label, text=node.text, attrs=node.attrs, children=children)


def _diff(enacted: IRNode, conditional: IRNode, op: CandidateOperation) -> str:
    label = _subsection_label(op.target_provision_ref) or "?"
    verb = {"insert": "+ gains", "replace": "~ rewrites", "repeal": "- loses"}.get(op.action.lower(), op.action)
    snippet = (op.payload_text.strip().replace("\n", " ")[:100]) or "(no text)"
    delta = len(conditional.children) - len(enacted.children)
    return f"{verb} unit {label} ({op.action}, children {len(enacted.children)}→{len(conditional.children)}, Δ{delta:+d}): '{snippet}…'"


def materialize_conditional_provision(
    enacted_ir: IRNode,
    branch: ConditionalBranch,
    *,
    statute_id: str = "",
    provision_ref: str = "",
) -> MaterializedProvision:
    """Apply ``branch``'s candidate ops onto ``enacted_ir`` → the conditional provision.

    Only ops whose ``target_statute_id`` matches ``statute_id`` (when given) and
    whose target provision matches ``provision_ref`` (by section) are applied here
    — a branch may edit several provisions; this materializes one.
    """
    conditional = enacted_ir
    diffs: List[str] = []
    findings: List[str] = []
    for op in branch.candidate_ops:
        if statute_id and op.target_statute_id and op.target_statute_id != statute_id:
            continue
        before = conditional
        conditional, finding = apply_candidate_op(conditional, op)
        if finding:
            findings.append(finding)
        else:
            diffs.append(_diff(before, conditional, op))
    return MaterializedProvision(
        statute_id=statute_id,
        provision_ref=provision_ref,
        enacted_ir=enacted_ir,
        conditional_ir=conditional,
        diff_lines=tuple(diffs),
        findings=tuple(findings),
    )


async def load_enacted_provision(statute_id: str, section_label: str) -> Optional[IRNode]:
    """Compile a real FI statute (full amendment replay) → a section's live IR.

    Returns the current-version ``content`` IRNode of the section whose address
    ends in ``('section', section_label)``, or ``None`` if not found. Heavy (full
    replay); use in an e2e / script, not a unit test.
    """
    from lawvm.finland.graph import build_statute_graph_fi

    graph = await build_statute_graph_fi(statute_id)
    for address, timeline in graph.timelines.items():
        path = getattr(address, "path", ())
        if path and path[-1] == ("section", section_label) and timeline.versions:
            return timeline.versions[-1].content
    return None
