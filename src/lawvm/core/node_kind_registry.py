"""Governed :class:`~lawvm.core.ir.IRNode` kind specifications (D1).

For each :class:`~lawvm.core.semantic_types.IRNodeKind` we declare:

* ``required_attrs`` — attrs the kind MUST carry (validator rejects absence);
* ``optional_attrs`` — additional attrs the kind MAY carry (validator tolerates);
* ``text_allowed`` — whether ``IRNode.text`` may carry operative text;
* ``children_allowed`` — whether the kind may have child nodes;
* ``plain_text_projection`` — how this kind projects to a faithful plaintext
  rendering (``"block"``, ``"inline"``, ``"omit"``, ``"label_prefix"``);
* ``address_role`` — the role this kind plays in a :class:`LegalAddress`
  (``"container"``, ``"leaf"``, ``"neither"``).

Kinds missing from the registry fall back to a permissive default
spec — the registry is **descriptive of the governed subset**, not a closed
gate on admissible IRNode shapes (an ad-hoc frontend attachment shape that
has not yet been characterised still loads and projects; the validator emits
an ``unknown_kind`` notice rather than crashing, so a missing spec is a
documentation smell, not a runtime break). When a kind gains a spec it
becomes enforced.

Validation produces :class:`NodeKindViolation` records (typed carriers
rather than raised exceptions) so the conserved-lane discipline (§1.8)
applies: every node ends up *owned* — either it conforms, or it carries one
or more typed violation records that explain the divergence.

Used by:
  - attachment IR validator (run after PDF→IR parse)
  - structural invariants test suite (SDOC D1 family)
  - future: cross-jurisdiction well-formedness assertions

Operating contract: AGENTS.md §1.9 (typed carriers) + §2.10 (planes
type-distinct — ``required_attrs`` values are evidence/projection attrs,
NOT semantic authority — the kind itself owns structure, the attrs are
footing/witness).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------

AddressRole = str  # "container" | "leaf" | "neither"
ProjectionRule = str  # "block" | "inline" | "omit" | "label_prefix"


@dataclass(frozen=True, slots=True)
class NodeKindSpec:
    """Governed specification for an :class:`IRNodeKind`."""

    kind: IRNodeKind
    required_attrs: frozenset[str] = field(default_factory=frozenset)
    optional_attrs: frozenset[str] = field(default_factory=frozenset)
    text_allowed: bool = True
    children_allowed: bool = True
    plain_text_projection: ProjectionRule = "block"
    address_role: AddressRole = "neither"

    @property
    def known_attrs(self) -> frozenset[str]:
        return self.required_attrs | self.optional_attrs


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# Source attrs that may appear on ANY kind as evidence — page_index, bbox,
# extraction_status, source_span, surface_text. These are §2.10 evidence
# attrs and never enter semantic identity (SDOC-06). Listed as a baseline
# so the kind-specific registries don't repeat them.

_EVIDENCE_ATTRS: frozenset[str] = frozenset(
    (
        "page_index",
        "bbox",
        "extraction_status",
        "source_span",
        "source_text",
        "source_ref",
        "pdf_sha256",
        "extraction_method",
    )
)

# Address attrs that any addressable kind may carry. ``eId`` is the canonical
# (judgment-neutral) structural identity slot; ``row_key`` / ``column_id`` are
# SDOC-07 semantic table coordinates (entered into content_leaf_hash).
_ADDRESS_ATTRS: frozenset[str] = frozenset(("eId", "row_key", "column_id"))


def _spec(
    kind: IRNodeKind,
    *,
    required_attrs: Sequence[str] = (),
    optional_attrs: Sequence[str] = (),
    text_allowed: bool = True,
    children_allowed: bool = True,
    plain_text_projection: ProjectionRule = "block",
    address_role: AddressRole = "neither",
) -> NodeKindSpec:
    """Build a spec; baseline evidence + address attrs are auto-allowed."""
    return NodeKindSpec(
        kind=kind,
        required_attrs=frozenset(required_attrs),
        optional_attrs=frozenset(optional_attrs) | _EVIDENCE_ATTRS | _ADDRESS_ATTRS,
        text_allowed=text_allowed,
        children_allowed=children_allowed,
        plain_text_projection=plain_text_projection,
        address_role=address_role,
    )


# Governed subset. Kinds absent here are tolerated with an ``unknown_kind``
# notice — see ``validate_node`` — so frontend attachment shapes that have
# not yet been characterised still load (§1.10 fail loud but don't crash).
NODE_KIND_SPECS: dict[IRNodeKind, NodeKindSpec] = {
    IRNodeKind.BODY: _spec(
        IRNodeKind.BODY,
        children_allowed=True,
        text_allowed=False,
        plain_text_projection="block",
        address_role="container",
    ),
    IRNodeKind.CHAPTER: _spec(
        IRNodeKind.CHAPTER,
        optional_attrs=("heading",),
        children_allowed=True,
        text_allowed=False,
        plain_text_projection="block",
        address_role="container",
    ),
    IRNodeKind.PART: _spec(
        IRNodeKind.PART,
        optional_attrs=("heading",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.SECTION: _spec(
        IRNodeKind.SECTION,
        optional_attrs=("heading",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.SUBSECTION: _spec(
        IRNodeKind.SUBSECTION,
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.PARAGRAPH: _spec(
        IRNodeKind.PARAGRAPH,
        children_allowed=True,
        text_allowed=True,
        plain_text_projection="label_prefix",
        address_role="leaf",
    ),
    IRNodeKind.SUBPARAGRAPH: _spec(
        IRNodeKind.SUBPARAGRAPH,
        children_allowed=True,
        text_allowed=True,
        plain_text_projection="label_prefix",
        address_role="leaf",
    ),
    IRNodeKind.ITEM: _spec(
        IRNodeKind.ITEM,
        children_allowed=True,
        text_allowed=True,
        plain_text_projection="label_prefix",
        address_role="leaf",
    ),
    IRNodeKind.BLOCK: _spec(
        IRNodeKind.BLOCK,
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.HCONTAINER: _spec(
        IRNodeKind.HCONTAINER,
        optional_attrs=("name",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.CONTENT: _spec(
        IRNodeKind.CONTENT,
        children_allowed=False,
        text_allowed=True,
        plain_text_projection="block",
        address_role="leaf",
    ),
    IRNodeKind.INTRO: _spec(
        IRNodeKind.INTRO,
        children_allowed=False,
        text_allowed=True,
        plain_text_projection="block",
        address_role="leaf",
    ),
    IRNodeKind.HEADING: _spec(
        IRNodeKind.HEADING,
        children_allowed=False,
        text_allowed=True,
        plain_text_projection="block",
        address_role="leaf",
    ),
    IRNodeKind.NUM: _spec(
        IRNodeKind.NUM,
        children_allowed=False,
        text_allowed=True,
        plain_text_projection="inline",
        address_role="leaf",
    ),
    IRNodeKind.P: _spec(
        IRNodeKind.P,
        children_allowed=True,
        text_allowed=True,
        plain_text_projection="block",
        address_role="leaf",
    ),
    IRNodeKind.I: _spec(
        IRNodeKind.I,
        children_allowed=True,
        text_allowed=True,
        plain_text_projection="inline",
        address_role="leaf",
    ),
    IRNodeKind.OMISSION: _spec(
        IRNodeKind.OMISSION,
        children_allowed=False,
        text_allowed=False,
        plain_text_projection="omit",
        address_role="leaf",
    ),
    IRNodeKind.CROSS_HEADING: _spec(
        IRNodeKind.CROSS_HEADING,
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.WRAP_UP: _spec(
        IRNodeKind.WRAP_UP,
        children_allowed=True,
        text_allowed=True,
        address_role="container",
    ),
    IRNodeKind.DIVISION: _spec(
        IRNodeKind.DIVISION,
        optional_attrs=("heading",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.SUBDIVISION: _spec(
        IRNodeKind.SUBDIVISION,
        optional_attrs=("heading",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.SENTENCE: _spec(
        IRNodeKind.SENTENCE,
        children_allowed=False,
        text_allowed=True,
        plain_text_projection="inline",
        address_role="leaf",
    ),
    IRNodeKind.CROSSHEADING: _spec(
        IRNodeKind.CROSSHEADING,
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.APPENDIX: _spec(
        IRNodeKind.APPENDIX,
        optional_attrs=("heading",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.SCHEDULE: _spec(
        IRNodeKind.SCHEDULE,
        optional_attrs=("heading",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.SCHEDULE_ENTRY: _spec(
        IRNodeKind.SCHEDULE_ENTRY,
        optional_attrs=("scope",),
        children_allowed=False,
        text_allowed=True,
        plain_text_projection="label_prefix",
        address_role="leaf",
    ),
    IRNodeKind.RECITAL: _spec(
        IRNodeKind.RECITAL,
        children_allowed=True,
        text_allowed=True,
        address_role="container",
    ),
    IRNodeKind.PREAMBLE: _spec(
        IRNodeKind.PREAMBLE,
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.P1GROUP: _spec(
        IRNodeKind.P1GROUP,
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.PGROUP: _spec(
        IRNodeKind.PGROUP,
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.FINAL: _spec(
        IRNodeKind.FINAL,
        children_allowed=True,
        text_allowed=True,
        address_role="container",
    ),
    IRNodeKind.TABLE: _spec(
        IRNodeKind.TABLE,
        optional_attrs=("row_count", "column_count"),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.ROW: _spec(
        IRNodeKind.ROW,
        optional_attrs=("row_index",),
        children_allowed=True,
        text_allowed=False,
        address_role="container",
    ),
    IRNodeKind.CELL: _spec(
        IRNodeKind.CELL,
        optional_attrs=("column_id", "row_key"),
        children_allowed=True,
        text_allowed=True,
        plain_text_projection="inline",
        address_role="leaf",
    ),
    IRNodeKind.HEADER_CELL: _spec(
        IRNodeKind.HEADER_CELL,
        optional_attrs=("column_id",),
        children_allowed=True,
        text_allowed=True,
        plain_text_projection="inline",
        address_role="leaf",
    ),
}


def spec_for(kind: IRNodeKind) -> NodeKindSpec | None:
    """Return the governed spec for ``kind`` or ``None`` if ungated."""
    return NODE_KIND_SPECS.get(kind)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeKindViolation:
    """One typed validation violation on an :class:`IRNode` tree.

    Replaces raised exceptions — the conserved-lane discipline (§1.8) demands
    every node end up *owned* (conforms, or carries typed violation records).
    """

    kind: str  # "missing_required_attr" | "unknown_attr" | "text_not_allowed" | "children_not_allowed" | "unknown_kind"
    node_kind: str
    node_label: str | None
    detail: str
    path: tuple[str, ...]
    offending_attr: str = ""


def _walk(
    node: IRNode, path: tuple[str, ...]
) -> "Iterator[tuple[IRNode, tuple[str, ...]]]":
    yield node, path
    for idx, child in enumerate(node.children):
        yield from _walk(
            child, path + (f"{child.kind.value}[{idx}]",)
        )


def validate_node(root: IRNode) -> list[NodeKindViolation]:
    """Walk ``root`` and emit typed violations for governed-kind mismatches.

    A node whose kind is ungated (no entry in :data:`NODE_KIND_SPECS`)
    emits one ``unknown_kind`` notice at that node — then continues walking
    children (the spec for that kind may be added once the shape is
    characterised). A node whose kind is governed emits one violation per
    actual divergence (missing required attrs, unknown attrs, disallowed
    text, disallowed children).
    """
    out: list[NodeKindViolation] = []
    for node, path in _walk(root, (f"{root.kind.value}[root]",)):
        spec = NODE_KIND_SPECS.get(node.kind)
        if spec is None:
            out.append(
                NodeKindViolation(
                    kind="unknown_kind",
                    node_kind=str(node.kind),
                    node_label=node.label,
                    detail=(
                        f"IRNodeKind {node.kind!r} has no governed spec; "
                        "add it to NODE_KIND_SPECS once the shape is "
                        "characterised."
                    ),
                    path=path,
                )
            )
            # Children are still walked — an ungated kind is a documentation
            # smell, not a hard stop (§1.10 fail loud but don't crash load).
            continue

        attrs = set(node.attrs.keys()) if node.attrs else set()
        for required in spec.required_attrs:
            if required not in attrs:
                out.append(
                    NodeKindViolation(
                        kind="missing_required_attr",
                        node_kind=str(node.kind),
                        node_label=node.label,
                        detail=(
                            f"required attr {required!r} absent on "
                            f"{node.kind!r} (label={node.label!r})"
                        ),
                        path=path,
                        offending_attr=required,
                    )
                )

        if spec.known_attrs:
            for attr in sorted(attrs - spec.known_attrs):
                out.append(
                    NodeKindViolation(
                        kind="unknown_attr",
                        node_kind=str(node.kind),
                        node_label=node.label,
                        detail=(
                            f"attr {attr!r} not declared for "
                            f"{node.kind!r} (required={sorted(spec.required_attrs)}, "
                            f"optional={sorted(spec.optional_attrs)})"
                        ),
                        path=path,
                        offending_attr=attr,
                    )
                )

        if not spec.text_allowed and node.text:
            out.append(
                NodeKindViolation(
                    kind="text_not_allowed",
                    node_kind=str(node.kind),
                    node_label=node.label,
                    detail=(
                        f"{node.kind!r} may not carry operative text; "
                        f"found {len(node.text)} chars."
                    ),
                    path=path,
                )
            )

        if not spec.children_allowed and node.children:
            out.append(
                NodeKindViolation(
                    kind="children_not_allowed",
                    node_kind=str(node.kind),
                    node_label=node.label,
                    detail=(
                        f"{node.kind!r} may not have children; "
                        f"found {len(node.children)}."
                    ),
                    path=path,
                )
            )

    return out


__all__ = [
    "AddressRole",
    "NodeKindSpec",
    "NodeKindViolation",
    "NODE_KIND_SPECS",
    "ProjectionRule",
    "spec_for",
    "validate_node",
]
