"""Pure copy-on-write tree operations for IRNode.

All functions are Tree → Tree (no mutation). The tree is rebuilt along
the path from root to target, sharing unchanged subtrees. This is a
copy-on-write rebuild pattern without explicit zipper state. It should not
be read as permission to mutate shared-kernel IR in place. `IRNode` is now a
frozen shared-core type; if a frontend wants a mutable workspace, that
workspace must remain outside core and rebuild back into `IRNode`.

These operations are the logical core of the grafter — everything else
(XML parsing, PEG extraction, omission merge) is input preparation.

The three primitive operations:
    replace_at(tree, path, content) → tree'
    insert_sorted(tree, parent_path, content, sort_fn) → tree'
    remove_at(tree, path) → tree'

Path = sequence of (kind, label) pairs navigating from root to target.

API tier
--------
Stable kernel tree-rewrite primitive surface. Some query helpers remain for
older call sites, but the copy-on-write operation model is the shared-core
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
import re
from typing import Callable, Collection, Dict, FrozenSet, Iterator, List, Literal, Optional, Protocol, Sequence, Tuple

import icontract

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.semantic_types import IRNodeKind


# ---------------------------------------------------------------------------
# Label matching and sort keys
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^\d\w]+")
_COMPOUND_NUMERIC_SORT_LABEL_RE = re.compile(r"^(\d+)_(\d+)$")
_LETTER_SUFFIX_SORT_LABEL_RE = re.compile(r"^(\d+)([a-z]*)$")
_RANGE_LABEL_SPLIT_RE = re.compile(r"\s*[–-]\s*")
_NON_DIGIT_RE = re.compile(r"\D+")
_TEXT_LINT_TOKEN_RE = re.compile(r"\w+", re.IGNORECASE)


def _match_label(node_label: Optional[str], target: str) -> bool:
    """Match node label against target, normalizing both."""
    return _norm(node_label or "") == _norm(target)


def _kind_matches(node_kind: IRNodeKind | str, target_kind: IRNodeKind | str) -> bool:
    """Return True when two kinds name the same structural kind."""
    if type(node_kind) is type(target_kind):
        return node_kind == target_kind
    return _kind_str(node_kind) == _kind_str(target_kind)


@lru_cache(maxsize=65536)
def _norm(s: str) -> str:
    """Normalize label for matching: lowercase, strip non-alphanum."""
    if not s:
        return ""
    return _NON_ALNUM_RE.sub("", s).lower()


def normalized_label_key(label: Optional[str]) -> str:
    """Return the shared default normalized label key used by tree lookups."""
    return _norm(label or "")


def _with_children(node: IRNode, children: Sequence[IRNode]) -> IRNode:
    """Create a new IRNode with different children, sharing everything else."""
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=dict(node.attrs),
        children=tuple(children),
    )


@lru_cache(maxsize=16384)
def _default_sort_key(label: Optional[str]) -> Tuple[int, str, int]:
    """Sort key for section labels: (number, letter_suffix, sub_number).

    Handles a few common label formats:
      '5'    → (5, '', 0)
      '5a'   → (5, 'a', 0)   letter-suffix form: 5 a §
      '12b'  → (12, 'b', 0)
      '26_1' → (26, '', 1)   compound numeric slot form
      '71_1' → (71, '', 1)
    """
    if label is None:
        return (-1, "", 0)
    if "-" in label or "–" in label:
        first_part = _RANGE_LABEL_SPLIT_RE.split(label, maxsplit=1)[0].strip()
        if first_part and first_part != label:
            return _default_sort_key(first_part)
    s = _norm(label)
    # Compound numeric slot format: N_M
    m = _COMPOUND_NUMERIC_SORT_LABEL_RE.match(s)
    if m:
        return (int(m.group(1)), "", int(m.group(2)))
    # Letter-suffix format: Na or plain N
    m = _LETTER_SUFFIX_SORT_LABEL_RE.match(s)
    if m:
        return (int(m.group(1)), m.group(2), 0)
    digits = _NON_DIGIT_RE.sub("", s)
    return (int(digits), "", 0) if digits else (-1, s, 0)


def default_label_sort_key(label: Optional[str]) -> Tuple[int, str, int]:
    """Return the shared default structural-label sort key.

    This is the core default, not a jurisdiction-specific legal ordering rule.
    Jurisdiction frontends may still pass their own sort key where needed.
    """
    return _default_sort_key(label)


def _insert_child_sorted(
    parent: IRNode,
    content: IRNode,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]],
) -> IRNode:
    """Insert content among parent's children at sorted position."""
    target_key = sort_key_fn(content.label)
    new_children: List[IRNode] = []
    inserted = False
    for child in parent.children:
        if not inserted and _kind_matches(child.kind, content.kind):
            child_key = sort_key_fn(child.label)
            if child_key > target_key:
                new_children.append(content)
                inserted = True
        new_children.append(child)
    if not inserted:
        new_children.append(content)
    return _with_children(parent, new_children)


# ---------------------------------------------------------------------------
# Post-processing operations (structural fixes + normalization)
# ---------------------------------------------------------------------------


def hoist_trailing_into_container(
    tree: IRNode,
    container_kind: str,
    child_kind: str,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]] = _default_sort_key,
    skip_heading_prefixes: Optional[List[str]] = None,
) -> IRNode:
    """Move trailing child_kind nodes into the preceding container_kind.

    E.g. hoist_trailing_into_container(body, 'chapter', 'section') moves
    sections that appear after a chapter into that chapter, if their sort
    key continues monotonically.

    Args:
        container_kind:       Kind of the container node (e.g. 'chapter').
        child_kind:           Kind of trailing nodes to hoist (e.g. 'section').
        sort_key_fn:          Sort key for labels; default handles numeric and
                              letter-suffixed labels.
        skip_heading_prefixes: Optional list of heading text prefixes (lowercase).
                              Nodes whose first heading starts with any of these
                              prefixes are never hoisted.  Used to prevent
                              jurisdiction-specific structural sections (e.g.
                              entry-into-force sections)
                              from being erroneously placed inside a chapter.
                              Pass ``None`` (default) for generic behaviour.
    """

    def _process(node: IRNode) -> IRNode:
        new_children: List[IRNode] = []
        current_container: Optional[IRNode] = None
        container_idx: int = -1
        last_key: Optional[Tuple] = None

        for child in node.children:
            if _kind_matches(child.kind, container_kind):
                # Flush any accumulated hoists into the previous container
                if current_container is not None and container_idx >= 0:
                    new_children[container_idx] = current_container
                current_container = child
                container_idx = len(new_children)
                # Find last child_kind key in this container
                container_children = [c for c in child.children if _kind_matches(c.kind, child_kind)]
                last_key = sort_key_fn(container_children[-1].label) if container_children else None
                new_children.append(child)
            elif _kind_matches(child.kind, child_kind) and current_container is not None:
                child_key = sort_key_fn(child.label)
                # Skip nodes whose heading starts with a caller-specified prefix
                skip = False
                if skip_heading_prefixes:
                    heading_children = [c for c in child.children if c.kind == IRNodeKind.HEADING]
                    if heading_children and heading_children[0].text:
                        heading_lower = heading_children[0].text.strip().lower()
                        if any(heading_lower.startswith(pfx) for pfx in skip_heading_prefixes):
                            skip = True
                if not skip and last_key is not None and child_key > last_key:
                    # Hoist into container
                    current_container = _with_children(current_container, list(current_container.children) + [child])
                    last_key = child_key
                    continue  # Don't add to new_children at this level
                new_children.append(child)
            else:
                new_children.append(child)

        # Flush final container
        if current_container is not None and container_idx >= 0:
            new_children[container_idx] = current_container

        if new_children != list(node.children):
            return _with_children(node, new_children)
        return node

    # Apply to body and any hcontainer wrappers
    new_children = []
    for child in tree.children:
        if child.kind in (IRNodeKind.HCONTAINER, IRNodeKind.BODY):
            new_children.append(_process(child))
        else:
            new_children.append(child)
    result = _process(tree)  # also process at root level
    return result


def normalize_text(tree: IRNode) -> IRNode:
    """Fix common text artifacts: strip spaces before punctuation."""
    if tree.text:
        cleaned = re.sub(r"\s+([.,;:])", r"\1", tree.text)
        if cleaned != tree.text:
            tree = IRNode(
                kind=tree.kind,
                label=tree.label,
                text=cleaned,
                attrs=dict(tree.attrs),
                children=tuple(normalize_text(c) for c in tree.children),
            )
            return tree
    if tree.children:
        new_children = [normalize_text(c) for c in tree.children]
        if any(nc is not oc for nc, oc in zip(new_children, tree.children, strict=True)):
            return _with_children(tree, new_children)
    return tree


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

PathStep = Tuple[str, str]
Path = Tuple[PathStep, ...]  # ((kind, label), ...)
LabelIndex = Dict[PathStep, List[Path]]

InvariantPathStep = Tuple[str, Optional[str]]
InvariantPath = Tuple[InvariantPathStep, ...]
TreeInvariantKind = Literal[
    "duplicate_label",
    "normalized_duplicate_label",
    "sort_order",
    "unexpected_child_kind",
]


class TreeInvariantNode(Protocol):
    """Read-only node surface needed by the shared invariant scanner."""

    @property
    def kind(self) -> IRNodeKind | str: ...

    @property
    def label(self) -> Optional[str]: ...

    @property
    def children(self) -> Sequence["TreeInvariantNode"]: ...


def _as_path(path: Sequence[PathStep]) -> Path:
    return tuple(path)


class AmbiguousLookupError(ValueError):
    """Raised when a lookup expected to be unique but multiple paths match."""

    pass


class MissingPathError(KeyError):
    """Raised when a strict tree operation is asked to use a missing path."""

    pass


def build_label_index(
    tree: IRNode,
    indexed_kinds: Optional[FrozenSet[str]] = None,
) -> LabelIndex:
    """Build label→paths index from a tree via single O(N) DFS.

    Returns dict mapping (kind, norm_label) to list of paths in DFS order.
    Use with find(..., label_index=idx) for O(1) lookups.
    """
    index: LabelIndex = {}

    def _walk(node: IRNode, prefix: Path) -> None:
        for child in node.children:
            step = (_kind_str(child.kind), child.label or "")
            path = prefix + (step,)
            if child.label and (indexed_kinds is None or _kind_str(child.kind) in indexed_kinds):
                key = (_kind_str(child.kind), _norm(child.label))
                index.setdefault(key, []).append(path)
            _walk(child, path)

    _walk(tree, ())
    return index


def find_all(
    tree: IRNode,
    kind: str,
    label: str,
    scope_kind: Optional[str] = None,
    scope_label: Optional[str] = None,
    label_index: Optional[LabelIndex] = None,
) -> List[Path]:
    """Return all matching paths for ``(kind, label)``.

    This is the ambiguity-preserving companion to ``find()``. Callers that
    require a unique answer should prefer ``find_unique()`` or explicitly
    inspect the returned candidates instead of inheriting DFS/build-order
    fallback by accident.
    """
    if label_index is not None:
        norm_label = _norm(label)
        kind_key = _kind_str(kind)
        target_paths = list(label_index.get((kind_key, norm_label), []))
        if scope_kind and scope_label:
            scope_kind_key = _kind_str(scope_kind)
            scope_paths = list(label_index.get((scope_kind_key, _norm(scope_label)), []))
            if not scope_paths:
                return []
            return [
                path
                for path in target_paths
                if any(len(path) > len(scope) and path[: len(scope)] == scope for scope in scope_paths)
            ]
        return target_paths

    matches: List[Path] = []

    def _search(node: IRNode, prefix: Path) -> None:
        for child in node.children:
            child_path = prefix + ((_kind_str(child.kind), child.label or ""),)
            if _kind_matches(child.kind, kind) and _match_label(child.label, label):
                matches.append(child_path)
            _search(child, child_path)

    if scope_kind and scope_label:
        scope_paths = find_all(tree, scope_kind, scope_label, label_index=label_index)
        for scope_path in scope_paths:
            scope_node = resolve(tree, scope_path)
            if scope_node is None:
                continue
            scoped_matches: List[Path] = []

            def _search_scoped(
                node: IRNode,
                prefix: Path,
                *,
                matches_out: List[Path] = scoped_matches,
            ) -> None:
                for child in node.children:
                    child_path = prefix + ((_kind_str(child.kind), child.label or ""),)
                    if _kind_matches(child.kind, kind) and _match_label(child.label, label):
                        matches_out.append(child_path)
                    _search_scoped(child, child_path)

            _search_scoped(scope_node, ())
            matches.extend(scope_path + inner for inner in scoped_matches)
        return matches

    _search(tree, ())
    return matches


def find_unique(
    tree: IRNode,
    kind: str,
    label: str,
    scope_kind: Optional[str] = None,
    scope_label: Optional[str] = None,
    label_index: Optional[LabelIndex] = None,
) -> Optional[Path]:
    """Return the unique match or fail explicitly on ambiguity."""
    matches = find_all(
        tree,
        kind,
        label,
        scope_kind=scope_kind,
        scope_label=scope_label,
        label_index=label_index,
    )
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousLookupError(
            f"Ambiguous lookup for ({kind!r}, {label!r})"
            + (f" within ({scope_kind!r}, {scope_label!r})" if scope_kind and scope_label else "")
            + f": {matches!r}"
        )
    return matches[0]


def resolve(tree: IRNode, path: Sequence[PathStep]) -> Optional[IRNode]:
    """Find the node at path, or None if not found.

    The input path is normalized to an immutable tuple path so callers may
    pass either a tuple path or a list path.
    """
    path = _as_path(path)
    if not path:
        return tree

    kind, label = path[0]
    for child in tree.children:
        if not _kind_matches(child.kind, kind) or not _match_label(child.label, label):
            continue
        if len(path) == 1:
            return child
        resolved = resolve(child, path[1:])
        if resolved is not None:
            return resolved
    return None


def resolve_required(tree: IRNode, path: Sequence[PathStep]) -> IRNode:
    """Resolve one path or fail explicitly when the path is absent."""
    normalized_path = _as_path(path)
    resolved = resolve(tree, normalized_path)
    if resolved is None:
        raise MissingPathError(f"Missing tree path: {normalized_path!r}")
    return resolved


def find_provisions_parent(tree: IRNode) -> Path:
    """Find the path to the deepest hcontainer wrapper that contains sections.

    In AKN XML, sections live inside hcontainer[statuteProvisionsWrapper],
    not directly under body. Returns the path to that wrapper, or [] if none found.
    """
    # Look for hcontainer with sections as direct or nested children
    for i, child in enumerate(tree.children):
        if child.kind == IRNodeKind.HCONTAINER:
            # Check if this container has sections or chapters
            has_provisions = any(
                c.kind in (IRNodeKind.SECTION, IRNodeKind.CHAPTER, IRNodeKind.PART) for c in child.children
            )
            if has_provisions:
                return ((_kind_str(child.kind), child.label or ""),)
    return ()


@icontract.require(lambda kind: kind, "kind must be non-empty")
@icontract.require(lambda label: label, "label must be non-empty")
def find(
    tree: IRNode,
    kind: str,
    label: str,
    scope_kind: Optional[str] = None,
    scope_label: Optional[str] = None,
    label_index: Optional[LabelIndex] = None,
) -> Optional[Path]:
    """Find path to first node matching (kind, label) at any depth.

    If scope_kind/scope_label given, only search within that container
    (e.g. scope_kind='chapter', scope_label='3' → search within chapter 3).

    If label_index is provided (from build_label_index), uses O(1) lookup
    instead of O(N) DFS.

    Ambiguity-preserving callers should prefer ``find_all()`` or
    ``find_unique()``. This function intentionally remains the first-match
    helper that returns the first match in DFS/index order.

    Returns the full path from tree root, or None if not found.
    """
    if label_index is not None:
        paths = find_all(
            tree,
            kind,
            label,
            scope_kind=scope_kind,
            scope_label=scope_label,
            label_index=label_index,
        )
        return paths[0] if paths else None

    # Fallback: O(N) DFS when no index provided
    def _search(node: IRNode, prefix: Path) -> Optional[Path]:
        for child in node.children:
            if _kind_matches(child.kind, kind) and _match_label(child.label, label):
                return prefix + ((_kind_str(child.kind), child.label or ""),)
            # Recurse into non-matching containers
            result = _search(child, prefix + ((_kind_str(child.kind), child.label or ""),))
            if result is not None:
                return result
        return None

    if scope_kind and scope_label:
        scope_path = find(tree, scope_kind, scope_label)
        if scope_path is None:
            return None
        scope_node = resolve(tree, scope_path)
        if scope_node is None:
            return None
        # Search within scope, prepend scope path
        inner = _search(scope_node, ())
        if inner is not None:
            return scope_path + inner
        return None

    return _search(tree, ())


# ---------------------------------------------------------------------------
# The three primitive operations
# ---------------------------------------------------------------------------


@icontract.require(lambda path: isinstance(path, (list, tuple)), "path must be a list or tuple")
@icontract.ensure(
    lambda tree, result: tree is not result or not tree.children,
    "replace_at must return a new tree root (copy-on-write update)",
)
def replace_at(tree: IRNode, path: Sequence[PathStep], content: IRNode) -> IRNode:
    """Return new tree with node at path replaced by content.

    >>> t = IRNode('body', children=[IRNode('section', '1', 'old')])
    >>> t2 = replace_at(t, [('section', '1')], IRNode('section', '1', 'new'))
    >>> t2.children[0].text
    'new'
    >>> t.children[0].text  # original unchanged
    'old'
    """
    path = _as_path(path)
    if not path:
        return content
    kind, label = path[0]
    new_children = []
    replaced = False
    for child in tree.children:
        if (
            not replaced
            and _kind_matches(child.kind, kind)
            and _match_label(child.label, label)
            and (len(path) == 1 or resolve(child, path[1:]) is not None)
        ):
            new_children.append(replace_at(child, path[1:], content))
            replaced = True
        else:
            new_children.append(child)
    return _with_children(tree, new_children)


def replace_at_required(tree: IRNode, path: Sequence[PathStep], content: IRNode) -> IRNode:
    """Replace one path or fail explicitly when the target is absent."""
    normalized_path = _as_path(path)
    resolve_required(tree, normalized_path)
    return replace_at(tree, normalized_path, content)


def remove_at(tree: IRNode, path: Sequence[PathStep]) -> IRNode:
    """Return new tree with node at path removed."""
    path = _as_path(path)
    if len(path) == 1:
        kind, label = path[0]
        removed = False
        new_children = []
        for child in tree.children:
            if not removed and _kind_matches(child.kind, kind) and _match_label(child.label, label):
                removed = True
                continue
            new_children.append(child)
        return _with_children(tree, new_children)
    kind, label = path[0]
    new_children = []
    removed = False
    for child in tree.children:
        if (
            not removed
            and _kind_matches(child.kind, kind)
            and _match_label(child.label, label)
            and resolve(child, path[1:]) is not None
        ):
            new_children.append(remove_at(child, path[1:]))
            removed = True
        else:
            new_children.append(child)
    return _with_children(tree, new_children)


def remove_at_required(tree: IRNode, path: Sequence[PathStep]) -> IRNode:
    """Remove one path or fail explicitly when the target is absent."""
    normalized_path = _as_path(path)
    resolve_required(tree, normalized_path)
    return remove_at(tree, normalized_path)


@icontract.require(lambda content: content.kind, "inserted content must have a kind")
# NOTE: Removed complex @icontract.ensure (resolve() in lambda triggers
# icontract AST parser failure on some call stacks — broke 2017/320).
# The invariant (parent gains exactly one child) is tested by hypothesis
# stateful tests and exhaustive enumeration instead.
def insert_sorted(
    tree: IRNode,
    parent_path: Sequence[PathStep],
    content: IRNode,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]] = _default_sort_key,
) -> IRNode:
    """Return new tree with content inserted at sorted position among parent's children.

    Only compares against children of the same kind as content. Insert position
    is determined by sort_key_fn(label).
    """
    parent_path = _as_path(parent_path)
    if not parent_path:
        return _insert_child_sorted(tree, content, sort_key_fn)
    kind, label = parent_path[0]
    new_children = []
    inserted = False
    for child in tree.children:
        if (
            not inserted
            and _kind_matches(child.kind, kind)
            and _match_label(child.label, label)
            and (len(parent_path) == 1 or resolve(child, parent_path[1:]) is not None)
        ):
            new_children.append(insert_sorted(child, parent_path[1:], content, sort_key_fn))
            inserted = True
        else:
            new_children.append(child)
    return _with_children(tree, new_children)


def insert_sorted_required(
    tree: IRNode,
    parent_path: Sequence[PathStep],
    content: IRNode,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]] = _default_sort_key,
) -> IRNode:
    """Insert under one parent path or fail explicitly when the parent is absent."""
    normalized_parent_path = _as_path(parent_path)
    if normalized_parent_path:
        resolve_required(tree, normalized_parent_path)
    return insert_sorted(tree, normalized_parent_path, content, sort_key_fn)


def insert_after(
    tree: IRNode,
    parent_path: Sequence[PathStep],
    after_label: str,
    content: IRNode,
) -> IRNode:
    """Return new tree with content inserted after the child with after_label."""
    parent_path = _as_path(parent_path)
    if not parent_path:
        new_children = []
        for child in tree.children:
            new_children.append(child)
            if _kind_matches(child.kind, content.kind) and _match_label(child.label, after_label):
                new_children.append(content)
        return _with_children(tree, new_children)
    kind, label = parent_path[0]
    new_children = []
    for child in tree.children:
        if _kind_matches(child.kind, kind) and _match_label(child.label, label):
            new_children.append(insert_after(child, parent_path[1:], after_label, content))
        else:
            new_children.append(child)
    return _with_children(tree, new_children)


# ---------------------------------------------------------------------------
# Index-based child operations (for positionally-addressed children)
# ---------------------------------------------------------------------------


def replace_nth(node: IRNode, kind: str, n: int, content: IRNode) -> IRNode:
    """Return new node with the nth child of `kind` replaced by content."""
    if n < 0:
        raise ValueError("replace_nth requires n >= 0")
    count = 0
    new_children = []
    for child in node.children:
        if _kind_matches(child.kind, kind):
            new_children.append(content if count == n else child)
            count += 1
        else:
            new_children.append(child)
    return _with_children(node, new_children)


def remove_nth(node: IRNode, kind: str, n: int) -> IRNode:
    """Return new node with the nth child of `kind` removed."""
    if n < 0:
        raise ValueError("remove_nth requires n >= 0")
    count = 0
    new_children = []
    for child in node.children:
        if _kind_matches(child.kind, kind):
            if count != n:
                new_children.append(child)
            count += 1
        else:
            new_children.append(child)
    return _with_children(node, new_children)


def insert_after_nth(node: IRNode, kind: str, n: int, content: IRNode) -> IRNode:
    """Return new node with content inserted after the nth child of `kind`."""
    if n < 0:
        raise ValueError("insert_after_nth requires n >= 0")
    count = 0
    new_children = []
    for child in node.children:
        new_children.append(child)
        if _kind_matches(child.kind, kind):
            if count == n:
                new_children.append(content)
            count += 1
    return _with_children(node, new_children)


# ---------------------------------------------------------------------------
# Tree-wide filtering
# ---------------------------------------------------------------------------


def strip_nodes(tree: IRNode, predicate: Callable[[IRNode], bool]) -> IRNode:
    """Remove all nodes (at any depth) matching predicate."""
    new_children = []
    changed = False
    for child in tree.children:
        if predicate(child):
            changed = True
            continue
        stripped = strip_nodes(child, predicate)
        if stripped is not child:
            changed = True
        new_children.append(stripped)
    if changed:
        return _with_children(tree, new_children)
    return tree


# Container kinds at which structural same-kind+label deduplication is applied.
_SECTION_DEDUP_CONTAINER_KINDS: FrozenSet[str] = frozenset(
    {"body", "chapter", "part", "hcontainer", "section"}
)
# Node kinds for which label-based deduplication is meaningful (structural
# provisions that carry a canonical label).
_DEDUP_TARGET_KINDS: FrozenSet[str] = frozenset(
    {"section", "chapter", "part", "subsection"}
)
# Node kinds whose labeled siblings should be kept in sort order.
# Matches the set checked by check_invariants() so resort_children fixes
# exactly the violations that check would report.
_SORT_TARGET_KINDS: FrozenSet[str] = frozenset(
    {
        "section",
        "chapter",
        "part",
        "division",
        "schedule",
        "appendix",
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
    }
)


def dedup_children_by_label(tree: IRNode) -> IRNode:
    """Recursively remove duplicate same-kind+label children, keeping last occurrence.

    When omission-merges expand master sections AND amendments also provide
    explicit replacements for those same labels, the merged child list at body
    or chapter level can contain duplicate section labels.  The last occurrence
    is preferred because it is the amendment-provided (authoritative) version.

    Only nodes whose kind is in ``_DEDUP_TARGET_KINDS`` and that carry a label
    are subject to deduplication.  Other children are always kept.  The
    deduplication is applied recursively at every ``_SECTION_DEDUP_CONTAINER_KINDS``
    level of the tree.
    """
    # Recurse into children first (bottom-up so we don't double-process).
    new_children_list: List[IRNode] = []
    changed = False
    for child in tree.children:
        deduped = dedup_children_by_label(child)
        if deduped is not child:
            changed = True
        new_children_list.append(deduped)

    # Apply dedup at this level only for container kinds that host sections.
    if _kind_str(tree.kind) in _SECTION_DEDUP_CONTAINER_KINDS:
        # Find which (kind, label) pairs appear more than once among target kinds.
        pair_counts: Dict[Tuple[str, str], int] = {}
        for child in new_children_list:
            ck = _kind_str(child.kind)
            if ck in _DEDUP_TARGET_KINDS and child.label:
                key = (ck, child.label)
                pair_counts[key] = pair_counts.get(key, 0) + 1
        dup_pairs = {p for p, cnt in pair_counts.items() if cnt > 1}
        if dup_pairs:
            # Find last position for each dup pair.
            last_pos: Dict[Tuple[str, str], int] = {}
            for i, child in enumerate(new_children_list):
                ck = _kind_str(child.kind)
                if ck in _DEDUP_TARGET_KINDS and child.label:
                    key = (ck, child.label)
                    if key in dup_pairs:
                        last_pos[key] = i
            result: List[IRNode] = []
            for i, child in enumerate(new_children_list):
                ck = _kind_str(child.kind)
                if ck in _DEDUP_TARGET_KINDS and child.label:
                    key = (ck, child.label)
                    if key in dup_pairs:
                        if i == last_pos[key]:
                            result.append(child)
                        # else: skip earlier duplicate
                        changed = True
                        continue
                result.append(child)
            new_children_list = result

    if changed:
        return _with_children(tree, new_children_list)
    return tree


def resort_children(
    tree: IRNode,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]] = _default_sort_key,
) -> IRNode:
    """Recursively sort labeled children of sortable kinds into correct order.

    After replay, amendment operations can leave same-kind labeled siblings out
    of order (e.g. sections 5, 3, 7 instead of 3, 5, 7).  This pass restores
    monotonic sort order for all kinds in ``_SORT_TARGET_KINDS`` while leaving
    non-labeled children (heading, num, content, intro, wrapUp, etc.) and
    non-sortable kinds in their original relative positions.

    The sort is applied per-kind independently: only children of the same kind
    that carry a label are reordered relative to each other.  Children of other
    kinds are untouched.  The function is purely copy-on-write — it returns the
    original node unchanged when no reordering is needed.
    """

    def _resort_node(node: IRNode) -> IRNode:
        # Recurse first (bottom-up so inner violations are fixed before outer).
        processed: List[IRNode] = []
        any_changed = False
        for child in node.children:
            new_child = _resort_node(child)
            if new_child is not child:
                any_changed = True
            processed.append(new_child)

        # For each sortable kind, collect the positions and nodes of labeled
        # children, sort them by label key, then re-inject in those positions.
        # Positions of all other children stay unchanged.
        by_kind: Dict[str, List[Tuple[int, IRNode]]] = {}
        for i, child in enumerate(processed):
            ck = _kind_str(child.kind)
            if ck in _SORT_TARGET_KINDS and child.label:
                by_kind.setdefault(ck, []).append((i, child))

        # Build the replacement map: original_index -> replacement node.
        replacement: Dict[int, IRNode] = {}
        for ck, entries in by_kind.items():
            indices = [idx for idx, _ in entries]
            nodes = [n for _, n in entries]
            sorted_nodes = sorted(nodes, key=lambda n: sort_key_fn(n.label))
            if any(orig is not repl for orig, repl in zip(nodes, sorted_nodes, strict=True)):
                any_changed = True
                for idx, repl_node in zip(indices, sorted_nodes, strict=True):
                    replacement[idx] = repl_node

        if not any_changed:
            return node

        new_children: List[IRNode] = [replacement.get(i, c) for i, c in enumerate(processed)]
        return _with_children(node, new_children)

    return _resort_node(tree)


# ---------------------------------------------------------------------------
# Family anchor lookup
# ---------------------------------------------------------------------------


def find_family(
    tree: IRNode,
    kind: str,
    label: str,
    scope_kind: Optional[str] = None,
    scope_label: Optional[str] = None,
    label_index: Optional[LabelIndex] = None,
) -> Optional[Path]:
    """Find the 'family base' for a suffixed label.

    E.g., for label '5a', finds the node with label '5'.
    Returns None if label has no letter suffix or base not found.
    """
    norm = _norm(label)
    m = re.match(r"^(\d+)[a-z]", norm)
    if not m:
        return None
    return find(tree, kind, m.group(1), scope_kind, scope_label, label_index=label_index)


# ---------------------------------------------------------------------------
# Compound operations (built from primitives)
# ---------------------------------------------------------------------------


def replace_or_insert(
    tree: IRNode,
    path: Sequence[PathStep],
    content: IRNode,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]] = _default_sort_key,
) -> IRNode:
    """Replace node at path if it exists, otherwise insert at sorted position."""
    path = _as_path(path)
    if resolve(tree, path) is not None:
        return replace_at(tree, path, content)
    parent_path = path[:-1] if len(path) > 1 else ()
    return insert_sorted(tree, parent_path, content, sort_key_fn)


# ---------------------------------------------------------------------------
# Tree invariant checking (debug/validation)
# ---------------------------------------------------------------------------

_NESTING_ORDER = {
    "body": {
        "part",
        "chapter",
        "section",
        "subsection",
        "hcontainer",
        "crossHeading",
        "crossheading",
        "division",
        "schedule",
        "appendix",
        "preamble",
        "recital",
        "final",
    },
    "hcontainer": {"part", "chapter", "section", "subsection", "hcontainer", "crossHeading", "heading", "num", "content", "omission"},
    "part": {"chapter", "section", "heading", "num"},
    "chapter": {"section", "subsection", "omission", "heading", "num", "crossHeading"},
    "section": {"subsection", "pgroup", "omission", "heading", "num", "content", "crossHeading"},
    "subsection": {"intro", "content", "paragraph", "pgroup", "num", "hcontainer", "wrapUp", "omission", "crossHeading"},
    "division": {
        "division",
        "part",
        "chapter",
        "section",
        "schedule",
        "appendix",
        "heading",
        "num",
        "crossHeading",
        "crossheading",
    },
    "schedule": {
        "part",
        "paragraph",
        "subparagraph",
        "item",
        "heading",
        "num",
        "content",
        "p1group",
        "pgroup",
        "crossHeading",
        "crossheading",
    },
    "appendix": {
        "part",
        "chapter",
        "section",
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
        "heading",
        "num",
        "content",
        "hcontainer",
        "crossHeading",
        "crossheading",
    },
    "paragraph": {
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
        "heading",
        "num",
        "content",
        "pgroup",
        "intro",
        "wrapUp",
        "omission",
        "hcontainer",
    },
    "subparagraph": {"subparagraph", "item", "sentence", "heading", "num", "content", "pgroup", "intro", "wrapUp", "omission", "hcontainer"},
    "item": {"sentence", "content", "intro", "wrapUp", "omission", "hcontainer"},
    "sentence": {"content"},
    "p1group": {"paragraph"},
    "pgroup": {"subsection", "paragraph", "subparagraph", "item"},
    "preamble": {"paragraph", "subparagraph", "item", "sentence", "content", "heading", "num", "hcontainer"},
    "recital": {"paragraph", "subparagraph", "item", "sentence", "content", "heading", "num", "hcontainer"},
    "final": {"paragraph", "subparagraph", "item", "sentence", "content", "heading", "num", "hcontainer"},
}

_ORDERED_INVARIANT_KINDS = frozenset(
    {
        "section",
        "chapter",
        "part",
        "division",
        "schedule",
        "appendix",
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
    }
)


def format_invariant_path(path: InvariantPath) -> str:
    """Format an invariant path with the legacy `check_invariants` spelling."""
    if not path:
        return ""
    head_kind, head_label = path[0]
    parts = [head_kind if head_label is None else f"{head_kind}:{head_label or '?'}"]
    for kind, label in path[1:]:
        parts.append(f"{kind}:{label or '?'}")
    return "/".join(parts)


@dataclass(frozen=True, slots=True)
class TreeInvariantViolation:
    """Typed structural invariant violation with a legacy message projection."""

    kind: TreeInvariantKind
    path: InvariantPath
    parent_kind: Optional[str] = None
    child_kind: Optional[str] = None
    label: Optional[str] = None
    normalized_label: Optional[str] = None
    count: Optional[int] = None
    previous_label: Optional[str] = None
    next_label: Optional[str] = None

    @property
    def path_text(self) -> str:
        return format_invariant_path(self.path)

    @property
    def message(self) -> str:
        if self.kind == "duplicate_label":
            return f"{self.path_text}: duplicate {self.child_kind}:{self.label} ({self.count} times)"
        if self.kind == "normalized_duplicate_label":
            return f"{self.path_text}: normalized-duplicate {self.child_kind}:{self.normalized_label}"
        if self.kind == "sort_order":
            return f"{self.path_text}: {self.child_kind} out of order: {self.previous_label} > {self.next_label}"
        return f"{self.path_text}: unexpected {self.child_kind} inside {self.parent_kind}"

    def to_dict(self) -> dict[str, object]:
        """Return a stable machine-readable projection for audit metadata."""
        return {
            "kind": self.kind,
            "path": self.path_text,
            "message": self.message,
            "parent_kind": self.parent_kind,
            "child_kind": self.child_kind,
            "label": self.label,
            "normalized_label": self.normalized_label,
            "count": self.count,
            "previous_label": self.previous_label,
            "next_label": self.next_label,
        }


def iter_tree_invariant_violations(
    tree: TreeInvariantNode,
    *,
    sort_key: Optional[Callable[[Optional[str]], Tuple[int, str, int]]] = None,
    families: Optional[Collection[TreeInvariantKind]] = None,
    root_path: Optional[InvariantPath] = None,
) -> Iterator[TreeInvariantViolation]:
    """Yield typed tree invariant violations.

    `check_invariants` remains the compatibility string projection. New callers
    should consume these records instead of parsing violation messages.
    """
    _sort_key = sort_key if sort_key is not None else _default_sort_key
    selected = frozenset(families) if families is not None else None

    def _wants(kind: TreeInvariantKind) -> bool:
        return selected is None or kind in selected

    def _check(node: TreeInvariantNode, path: InvariantPath) -> Iterator[TreeInvariantViolation]:
        if _wants("duplicate_label"):
            seen: Dict[Tuple[str, str], int] = {}
            for child in node.children:
                if child.label:
                    key = (_kind_str(child.kind), child.label)
                    seen[key] = seen.get(key, 0) + 1
            for (kind, label), count in seen.items():
                if count > 1:
                    yield TreeInvariantViolation(
                        kind="duplicate_label",
                        path=path,
                        child_kind=kind,
                        label=label,
                        count=count,
                    )

        if _wants("normalized_duplicate_label"):
            norm_seen: Dict[Tuple[str, str], str] = {}
            for child in node.children:
                if child.label is not None:
                    child_kind = _kind_str(child.kind)
                    normalized_label = _norm(child.label)
                    norm_key = (child_kind, normalized_label)
                    if norm_key in norm_seen:
                        if norm_seen[norm_key] != child.label:
                            yield TreeInvariantViolation(
                                kind="normalized_duplicate_label",
                                path=path,
                                child_kind=child_kind,
                                normalized_label=normalized_label,
                            )
                    else:
                        norm_seen[norm_key] = child.label

        if _wants("sort_order"):
            by_kind: Dict[str, List[str]] = {}
            for child in node.children:
                if child.label:
                    by_kind.setdefault(_kind_str(child.kind), []).append(child.label)
            for kind, labels in by_kind.items():
                if kind in _ORDERED_INVARIANT_KINDS:
                    keys = [_sort_key(label) for label in labels]
                    for i, (left_key, right_key) in enumerate(pairwise(keys)):
                        if left_key > right_key:
                            yield TreeInvariantViolation(
                                kind="sort_order",
                                path=path,
                                child_kind=kind,
                                previous_label=labels[i],
                                next_label=labels[i + 1],
                            )

        if _wants("unexpected_child_kind"):
            parent_kind = _kind_str(node.kind)
            allowed = _NESTING_ORDER.get(parent_kind)
            if allowed is not None:
                for child in node.children:
                    child_kind = _kind_str(child.kind)
                    if child_kind not in allowed:
                        yield TreeInvariantViolation(
                            kind="unexpected_child_kind",
                            path=path,
                            parent_kind=parent_kind,
                            child_kind=child_kind,
                        )

        for child in node.children:
            child_path = path + ((_kind_str(child.kind), child.label),)
            yield from _check(child, child_path)

    yield from _check(tree, root_path or ((_kind_str(tree.kind), None),))


def check_invariants(
    tree: IRNode,
    *,
    sort_key: Optional[Callable[[Optional[str]], Tuple[int, str, int]]] = None,
) -> List[str]:
    """Check tree invariants, returning list of violation descriptions.

    Invariants:
    1. Label uniqueness: no two same-kind siblings share a label
    2. Sort ordering: same-kind labeled siblings are in sort order
    3. Nesting validity: children kinds match expected nesting

    Args:
        sort_key: Optional sort key function for ordering checks.  Defaults to
                  ``_default_sort_key``.  Jurisdiction adapters can pass their
                  own function to apply jurisdiction-specific ordering rules.
    """
    return [violation.message for violation in iter_tree_invariant_violations(tree, sort_key=sort_key)]


def find_text_duplication_warnings(
    tree: IRNode,
    *,
    min_token_run: int = 12,
    min_char_run: int = 80,
    excerpt_chars: int = 160,
) -> List[Dict[str, object]]:
    """Return heuristic warnings for large duplicated text tracts.

    These are lint-style warnings, not hard structural invariants. The goal is
    to catch suspicious exact duplicates or large shared tails/heads across
    sibling labeled provisions, which often signals a replay/apply bug.
    """
    warnings: List[Dict[str, object]] = []
    skip_hcontainer_names = {"attachments", "signatures", "conclusions", "omission"}

    def _substantive_text(node: IRNode) -> str:
        if node.kind in {IRNodeKind.NUM, IRNodeKind.HEADING}:
            return ""
        if node.kind == IRNodeKind.HCONTAINER and str(node.attrs.get("name") or "") in skip_hcontainer_names:
            return ""
        parts: List[str] = []
        if node.text:
            parts.append(node.text)
        for child in node.children:
            child_text = _substantive_text(child)
            if child_text:
                parts.append(child_text)
        return " ".join(part.strip() for part in parts if part and part.strip()).strip()

    def _tokens(text: str) -> List[str]:
        return [tok.lower() for tok in _TEXT_LINT_TOKEN_RE.findall(text)]

    def _shared_prefix_len(lhs: List[str], rhs: List[str]) -> int:
        n = 0
        for left, right in zip(lhs, rhs, strict=False):
            if left != right:
                break
            n += 1
        return n

    def _shared_suffix_len(lhs: List[str], rhs: List[str]) -> int:
        n = 0
        for left, right in zip(reversed(lhs), reversed(rhs), strict=False):
            if left != right:
                break
            n += 1
        return n

    def _excerpt(tokens: List[str]) -> str:
        return " ".join(tokens)[:excerpt_chars]

    def _walk(node: IRNode, path: str) -> None:
        labeled_children = [child for child in node.children if child.label]
        enriched: List[Tuple[IRNode, str, List[str]]] = []
        for child in labeled_children:
            text = _substantive_text(child)
            if len(text) < min_char_run:
                continue
            toks = _tokens(text)
            if len(toks) < min_token_run:
                continue
            enriched.append((child, text, toks))

        for i, (left_node, left_text, left_tokens) in enumerate(enriched):
            for right_node, right_text, right_tokens in enriched[i + 1 :]:
                if left_node.kind != right_node.kind:
                    continue
                if left_text == right_text:
                    warnings.append(
                        {
                            "kind": "duplicate_full_text",
                            "path": path,
                            "left": f"{left_node.kind}:{left_node.label}",
                            "right": f"{right_node.kind}:{right_node.label}",
                            "shared_token_count": len(left_tokens),
                            "excerpt": _excerpt(left_tokens),
                        }
                    )
                    continue
                shared_suffix = _shared_suffix_len(left_tokens, right_tokens)
                if shared_suffix >= min_token_run:
                    suffix_tokens = left_tokens[-shared_suffix:]
                    if len(" ".join(suffix_tokens)) >= min_char_run:
                        warnings.append(
                            {
                                "kind": "duplicate_suffix_text",
                                "path": path,
                                "left": f"{left_node.kind}:{left_node.label}",
                                "right": f"{right_node.kind}:{right_node.label}",
                                "shared_token_count": shared_suffix,
                                "excerpt": _excerpt(suffix_tokens),
                            }
                        )
                        continue
                shared_prefix = _shared_prefix_len(left_tokens, right_tokens)
                if shared_prefix >= min_token_run:
                    prefix_tokens = left_tokens[:shared_prefix]
                    if len(" ".join(prefix_tokens)) >= min_char_run:
                        warnings.append(
                            {
                                "kind": "duplicate_prefix_text",
                                "path": path,
                                "left": f"{left_node.kind}:{left_node.label}",
                                "right": f"{right_node.kind}:{right_node.label}",
                                "shared_token_count": shared_prefix,
                                "excerpt": _excerpt(prefix_tokens),
                            }
                        )

        for child in node.children:
            _walk(child, f"{path}/{_kind_str(child.kind)}:{child.label or '?'}")

    _walk(tree, _kind_str(tree.kind))
    return warnings


# ---------------------------------------------------------------------------
# Flattened-sublist-family detection (lint-level heuristic)
# ---------------------------------------------------------------------------

def _fs_label_family(label: str) -> str:
    """Classify a label into: 'digit', 'alpha', 'roman', or 'mixed'."""
    s = label.strip().rstrip(".")
    if not s:
        return "mixed"
    if re.fullmatch(r"\d+[a-zA-Z]?", s):
        return "digit"
    if re.fullmatch(r"[ivxlcdm]+", s, re.IGNORECASE):
        # Subset that's all roman-numeral chars.  Exclude single letters that
        # are also alpha (i, v, x, etc.) when the context looks alphabetical.
        return "roman"
    if re.fullmatch(r"[a-zA-Z]+\d*", s):
        return "alpha"
    return "mixed"


def _fs_ordinal(label: str, family: str) -> int:
    """Return a rough ordinal for a label within its family (0 if unknown)."""
    s = label.strip().rstrip(".")
    if family == "digit":
        m = re.match(r"(\d+)", s)
        return int(m.group(1)) if m else 0
    if family == "alpha":
        alpha = re.sub(r"\d+$", "", s).lower()
        if len(alpha) == 1:
            return ord(alpha) - ord("a") + 1
        if len(alpha) == 2 and len(set(alpha)) == 1:
            return 26 + ord(alpha[0]) - ord("a") + 1
        return 0
    if family == "roman":
        roman_map = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
        result, prev = 0, 0
        for ch in reversed(s.lower()):
            val = roman_map.get(ch, 0)
            result += val if val >= prev else -val
            prev = val
        return result
    return 0


def find_flattened_sublist_warnings(
    tree: IRNode,
    *,
    min_children: int = 4,
) -> List[Dict[str, object]]:
    """Return heuristic warnings for flattened sublist families.

    Detects when same-kind labeled siblings contain label sequences suggesting
    that two or more independent sublists have been merged into one flat list.

    Two patterns are detected:

    1. **Family interleaving**: the same label family (digit/alpha/roman) appears
       in two or more non-contiguous runs, separated by a run of a different
       family.  Example: ``a b c 1 2 a b`` — letter-family appears twice.

    2. **Ordinal reset within family**: within the dominant label family, the
       ordinal sequence resets (drops to ≤ start of the previous run), suggesting
       a second independent list starting over.  Example: ``1 2 3 1 2`` where the
       second ``1`` indicates a second sublist.  Only fires when the drop is to
       ordinal ≤ 2 (restart from near the beginning), to avoid false positives
       from unusual legal numbering schemes.

    These are lint-style warnings, not hard invariants.  They are useful for
    detecting replay/apply bugs where sections from separate subsections have been
    collapsed to the same structural level.
    """
    warnings: List[Dict[str, object]] = []

    def _walk(node: IRNode, path: str) -> None:
        # Group labeled children by kind (preserving order)
        by_kind: Dict[str, List[str]] = {}
        for child in node.children:
            if child.label:
                k = _kind_str(child.kind)
                by_kind.setdefault(k, []).append(child.label)

        for kind, labels in by_kind.items():
            if len(labels) < min_children:
                continue

            families = [_fs_label_family(l) for l in labels]
            non_mixed = [f for f in families if f != "mixed"]
            if not non_mixed:
                continue

            # --- Pattern 1: family interleaving ---
            # Collapse consecutive same-family labels into runs.
            runs: List[str] = []
            prev_f: str | None = None
            for f in families:
                if f != prev_f:
                    runs.append(f)
                    prev_f = f

            repeated_families = {f for f in runs if runs.count(f) > 1 and f != "mixed"}
            if repeated_families:
                warnings.append({
                    "kind": "flattened_sublist_interleaved",
                    "path": path,
                    "node_kind": kind,
                    "repeated_families": sorted(repeated_families),
                    "label_sample": labels[:14],
                })
                continue  # don't double-report with pattern 2

            # --- Pattern 2: ordinal reset within dominant family ---
            dominant = max(set(non_mixed), key=non_mixed.count)
            ords = [
                _fs_ordinal(l, dominant)
                for l, f in zip(labels, families, strict=True)
                if f == dominant
            ]
            if len(ords) < min_children:
                continue

            max_so_far = 0
            for i, ordinal in enumerate(ords):
                if ordinal > 0:
                    if ordinal <= 2 and max_so_far >= 3:
                        # Sequence restarted near ordinal 1 — strong reset signal
                        reset_label = [l for l, f in zip(labels, families, strict=True) if f == dominant][i]
                        warnings.append({
                            "kind": "flattened_sublist_reset",
                            "path": path,
                            "node_kind": kind,
                            "dominant_family": dominant,
                            "max_before_reset": max_so_far,
                            "reset_at_ordinal": ordinal,
                            "reset_label": reset_label,
                            "label_sample": labels[:14],
                        })
                        break
                    max_so_far = max(max_so_far, ordinal)

        for child in node.children:
            _walk(child, f"{path}/{_kind_str(child.kind)}:{child.label or '?'}")

    _walk(tree, _kind_str(tree.kind))
    return warnings
