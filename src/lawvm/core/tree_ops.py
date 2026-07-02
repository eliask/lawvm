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
from typing import Callable, Collection, Dict, FrozenSet, Iterator, List, Literal, Mapping, Optional, Protocol, Sequence, Tuple, TYPE_CHECKING, TypeAlias

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import _kind_str, structural_subtree_hash
from lawvm.core.mutation_boundary import (
    TreePath,
    TreePaths,
    TreePathStep,
    diff_ir_paths_identity_pruned,
)
from lawvm.core.observed_write_audit import ObservedWriteAudit, build_observed_write_audit
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.stage_result import (
    EMPTY_EVIDENCE,
    NEUTRAL_AUTHORITY,
    CoverageCertificate,
    EvidenceBundle,
    Residual,
    StageResult,
)
from lawvm.core.write_receipt import WriteReceipt, receipt_address_string

if TYPE_CHECKING:
    from lawvm.core.provenance import SourceAnchor


# ---------------------------------------------------------------------------
# Label matching and sort keys
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^\d\w]+")
_COMPOUND_NUMERIC_SORT_LABEL_RE = re.compile(r"^(\d+)_(\d+)$")
_LETTER_SUFFIX_SORT_LABEL_RE = re.compile(r"^(\d+)([a-z]*)$")
_RANGE_LABEL_SPLIT_RE = re.compile(r"\s*[–-]\s*")
_NON_DIGIT_RE = re.compile(r"\D+")
_TEXT_LINT_TOKEN_RE = re.compile(r"\w+", re.IGNORECASE)
_PURE_DIGIT_LABEL_RE = re.compile(r"^\d+$")
_PURE_ALPHA_LABEL_RE = re.compile(r"^[A-Za-z]+$")
_PURE_ROMAN_LABEL_RE = re.compile(r"^[IVXLCDMivxlcdm]+$")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([.,;:])")
_FS_DIGIT_LABEL_RE = re.compile(r"^\d+[a-zA-Z]?$")
_FS_ROMAN_LABEL_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_FS_ALPHA_LABEL_RE = re.compile(r"^[a-zA-Z]+\d*$")
_FS_ORDINAL_DIGITS_RE = re.compile(r"(\d+)")


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


def with_children(node: IRNode, children: Sequence[IRNode]) -> IRNode:
    """Return a NEW ``IRNode`` with ``children`` replaced, sharing every other field.

    Public kernel helper for copy-on-write rebuild along the path from a
    mutated subtree back to the root (AGENTS.md §2.3 — frontends route their
    CoW rebuilds through this single core primitive so the IRNode identity /
    immutability invariant is owned in one place). The originally private
    ``_with_children`` name is retained as a module-level alias so existing
    call sites (``_tops._with_children`` in finland, plus
    ``tests/test_mutation_gaps.py``) continue to resolve; new call sites
    should use the public name.
    """
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=dict(node.attrs),
        children=tuple(children),
    )


# Backward-compat alias — the formerly private ``_with_children`` symbol is
# retained because many frontend modules (``_tops._with_children`` in
# ``finland/*``) and ``tests/test_mutation_gaps.py`` still reference it by
# that name. iter2 W7 L1 promotes the function to public ``with_children``;
# the alias keeps the rename's external blast radius at zero. New code should
# import ``with_children`` directly.
_with_children = with_children


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


_ROMAN_GLYPHS: FrozenSet[str] = frozenset("ivxlcdm")
_LOWER_ALPHA_LABEL_RE = re.compile(r"^[a-z]*$")


def _split_roman_suffix(normalized: str) -> Optional[Tuple[str, str]]:
    """Split a normalized label into (leading-roman-run, lowercase-letter-suffix).

    Consumes the maximal leading run of Roman glyphs, then requires the
    remainder to be lowercase letters.  Returns ``None`` when there is no leading
    Roman glyph or the remainder is not all lowercase letters.  This is a linear
    scan rather than an ``[ivxlcdm]+[a-z]*`` regex: those adjacent variable
    repeats overlap (the roman glyphs are a subset of ``[a-z]``) and are a
    catastrophic-backtracking hazard the regex perf gate rejects.
    """
    i = 0
    n = len(normalized)
    while i < n and normalized[i] in _ROMAN_GLYPHS:
        i += 1
    if i == 0:
        return None
    suffix = normalized[i:]
    if not _LOWER_ALPHA_LABEL_RE.fullmatch(suffix):
        return None
    return (normalized[:i], suffix)


def _roman_run_ordinals(labels: Sequence[Optional[str]]) -> Optional[Dict[str, Tuple[int, str]]]:
    """Classify a sibling *run* of labels as a Roman-numeral sequence.

    ``_default_sort_key`` only ever sees one label at a time, so it cannot tell a
    single glyph such as ``i``/``v``/``x`` apart from an alphabetic item label,
    NOR a multi-letter roman-matching token such as ``dc``/``dl`` apart from an
    alphabetic continuation label (``da``/``db``/.../``dm``) in a list that ran
    past ``z``.  Disambiguation is fundamentally impossible per-label and MUST
    NOT be attempted there.

    A *run*, however, is disambiguating: an alphabetic continuation list
    (``a``..``f``, ``da``..``dm``) always contains members such as ``a``/``b``/
    ``e``/``f`` (or ``da``/``db``) that are NOT valid Roman numerals, which
    immediately disqualifies the run.  A genuine Roman list is the only shape in
    which *every* member parses as a Roman numeral.  We additionally require at
    least one multi-letter Roman token (``ii``/``iii``/``iv``/...) as the
    unambiguous positive signal — a single-glyph-only run (``i`` alone, or a
    bare ``a, b, c`` which is not roman anyway) is never promoted.  This mirrors
    the multi-letter-only roman heuristic already used by ``_fs_label_family``
    and ``_label_sequence_family_and_ordinal``, lifted to run scope so the
    single-glyph members of a genuine Roman list also sort/compare numerically.

    Letter-suffixed Roman labels (amendment inserts such as ``iia``/``iiia``)
    are part of the same sequence and key by ``(roman_ordinal, suffix)`` so they
    sort right after their base (``ii`` < ``iia`` < ``iib`` < ``iii``).

    Returns a mapping ``normalized_label -> (roman_ordinal, letter_suffix)`` when
    *every* non-empty label in the run is a (optionally suffixed) valid Roman
    numeral AND at least one label is a multi-letter Roman token; returns
    ``None`` otherwise — callers then fall back to ``_default_sort_key`` rather
    than guessing.
    """
    present = [n for n in (_norm(label) for label in labels if label) if n]
    if len(present) < 2:
        return None
    has_multichar_roman = False
    mapping: Dict[str, Tuple[int, str]] = {}
    for n in present:
        split = _split_roman_suffix(n)
        if split is None:
            return None
        roman_part, suffix = split
        ordinal = _roman_ordinal(roman_part)
        if ordinal <= 0:
            return None
        if len(roman_part) > 1:
            has_multichar_roman = True
        mapping[n] = (ordinal, suffix)
    if not has_multichar_roman:
        return None
    return mapping


def _run_aware_sort_key_fn(
    labels: Sequence[Optional[str]],
    base_sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]],
) -> Callable[[Optional[str]], Tuple[int, str, int]]:
    """Return a sort key fn specialised for a sibling run.

    When the run is a genuine Roman-numeral sequence (see ``_roman_run_ordinals``)
    every member — including ambiguous single glyphs — is keyed by its Roman
    ordinal so the run sorts numerically (``ix`` after ``viii``).  Otherwise the
    supplied ``base_sort_key_fn`` is returned unchanged.

    Run-aware Roman promotion only applies to the shared default sort key.
    Jurisdiction frontends that inject their own key (e.g. Norway's litra-vs-
    roman ``_no_sort_key`` with its own witnessed single-glyph disambiguation)
    own that decision and are left untouched.
    """
    if base_sort_key_fn is not _default_sort_key:
        return base_sort_key_fn
    roman = _roman_run_ordinals(labels)
    if roman is None:
        return base_sort_key_fn

    def _key(label: Optional[str]) -> Tuple[int, str, int]:
        if label is not None:
            entry = roman.get(_norm(label))
            if entry is not None:
                ordinal, suffix = entry
                return (ordinal, suffix, 0)
        return base_sort_key_fn(label)

    return _key


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
        cleaned = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", tree.text)
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

PathStep: TypeAlias = TreePathStep
Path: TypeAlias = TreePath  # ((kind, label), ...)
type _NormalizedPathStep = Tuple[str, str]
type _NormalizedPath = Tuple[_NormalizedPathStep, ...]
LabelIndex = Dict[PathStep, List[Path]]

InvariantPathStep = Tuple[str, Optional[str]]
InvariantPath = Tuple[InvariantPathStep, ...]
TreeInvariantKind = Literal[
    "duplicate_label",
    "normalized_duplicate_label",
    "sort_order",
    "unexpected_child_kind",
    "mixed_hierarchy_child",
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


@lru_cache(maxsize=65536)
def _normalize_path(path: Path) -> _NormalizedPath:
    return tuple((_kind_str(kind), _norm(label)) for kind, label in path)


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
            child_kind = _kind_str(child.kind)
            step = (child_kind, child.label or "")
            path = prefix + (step,)
            if child.label and (indexed_kinds is None or child_kind in indexed_kinds):
                key = (child_kind, _norm(child.label))
                index.setdefault(key, []).append(path)
            _walk(child, path)

    _walk(tree, ())
    return index


def build_provision_label_index(
    tree: IRNode,
    *,
    indexed_kinds: FrozenSet[str] = frozenset({"part", "chapter", "section"}),
    terminal_kinds: FrozenSet[str] = frozenset({"section"}),
) -> LabelIndex:
    """Build a sparse provision-container label index.

    This is for hot paths that only resolve provision containers.  Sections are
    terminal for this index, so subsection/paragraph/item descendants are not
    walked merely to discover that their kinds are not indexed.
    """
    index: LabelIndex = {}

    def _walk(node: IRNode, prefix: Path) -> None:
        for child in node.children:
            child_kind = _kind_str(child.kind)
            step = (child_kind, child.label or "")
            path = prefix + (step,)
            if child.label and child_kind in indexed_kinds:
                key = (child_kind, _norm(child.label))
                index.setdefault(key, []).append(path)
            if child_kind in terminal_kinds:
                continue
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


def _resolve_from_path(
    tree: IRNode,
    path: _NormalizedPath,
    depth: int,
    path_len: int,
) -> Optional[IRNode]:
    kind, label_key = path[depth]
    leaf_depth = path_len - 1
    for child in tree.children:
        if _kind_str(child.kind) != kind or _norm(child.label or "") != label_key:
            continue
        if depth == leaf_depth:
            return child
        resolved = _resolve_from_path(child, path, depth + 1, path_len)
        if resolved is not None:
            return resolved
    return None


def resolve(tree: IRNode, path: Sequence[PathStep]) -> Optional[IRNode]:
    """Find the node at path, or None if not found.

    The input path is normalized to an immutable tuple path so callers may
    pass either a tuple path or a list path.
    """
    path = _as_path(path)
    if not path:
        return tree

    normalized_path = _normalize_path(path)
    return _resolve_from_path(tree, normalized_path, 0, len(normalized_path))


def resolve_required(tree: IRNode, path: Sequence[PathStep]) -> IRNode:
    """Resolve one path or fail explicitly when the path is absent."""
    normalized_path = _as_path(path)
    resolved = resolve(tree, normalized_path)
    if resolved is None:
        raise MissingPathError(f"Missing tree path: {normalized_path!r}")
    return resolved


def _resolve_from_path_ordinal(
    tree: IRNode,
    path: _NormalizedPath,
    ordinals: Mapping[int, int],
    depth: int,
    path_len: int,
) -> Optional[IRNode]:
    """Ordinal-aware analogue of :func:`_resolve_from_path`.

    ``ordinals`` maps a path-element index to a 1-indexed occurrence selector.
    At a depth carrying an ordinal ``n``, the ``n``-th sibling matching ``(kind,
    label)`` is selected (the disambiguator for DUPLICATE labels — a defective-
    but-enacted statute condition); if fewer than ``n`` siblings match, the path
    is absent. At a depth WITHOUT an ordinal the behavior is identical to
    :func:`_resolve_from_path`: first match, with backtracking so a deeper miss
    can be recovered from a later duplicate. See ``LegalAddress.ordinals``.
    """

    kind, label_key = path[depth]
    leaf_depth = path_len - 1
    ordinal = ordinals.get(depth)
    if ordinal is not None:
        seen = 0
        for child in tree.children:
            if _kind_str(child.kind) != kind or _norm(child.label or "") != label_key:
                continue
            seen += 1
            if seen != ordinal:
                continue
            if depth == leaf_depth:
                return child
            return _resolve_from_path_ordinal(child, path, ordinals, depth + 1, path_len)
        return None
    for child in tree.children:
        if _kind_str(child.kind) != kind or _norm(child.label or "") != label_key:
            continue
        if depth == leaf_depth:
            return child
        resolved = _resolve_from_path_ordinal(child, path, ordinals, depth + 1, path_len)
        if resolved is not None:
            return resolved
    return None


def resolve_with_ordinals(
    tree: IRNode,
    path: Sequence[PathStep],
    ordinals: Mapping[int, int],
) -> Optional[IRNode]:
    """Resolve ``path`` selecting the Nth match at any depth named in ``ordinals``.

    ``ordinals`` maps a path-element index to a 1-indexed occurrence selector.
    With an empty ``ordinals`` this is identical to :func:`resolve` (every
    element resolves to its first match, so ordinal-free addresses are
    unchanged). The ordinal disambiguator selects among DUPLICATE labels — the
    defective-but-enacted statute condition the US "the second paragraph (1)"
    redesignations name. See FABLE_UNIVERSAL_ALGEBRA §5.4.
    """

    if not ordinals:
        return resolve(tree, path)
    path = _as_path(path)
    if not path:
        return tree
    normalized_path = _normalize_path(path)
    return _resolve_from_path_ordinal(tree, normalized_path, dict(ordinals), 0, len(normalized_path))


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
    if not kind:
        raise ValueError("kind must be non-empty")
    if not label:
        raise ValueError("label must be non-empty")
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


def replace_at(tree: IRNode, path: Sequence[PathStep], content: IRNode) -> IRNode:
    """Return new tree with node at path replaced by content.

    >>> t = IRNode('body', children=[IRNode('section', '1', 'old')])
    >>> t2 = replace_at(t, [('section', '1')], IRNode('section', '1', 'new'))
    >>> t2.children[0].text
    'new'
    >>> t.children[0].text  # original unchanged
    'old'
    """
    if not isinstance(path, (list, tuple)):
        raise TypeError("path must be a list or tuple")
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
    result = _with_children(tree, new_children)
    if tree.children and result is tree:
        raise AssertionError("replace_at must return a new tree root (copy-on-write update)")
    return result


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
    if not content.kind:
        raise ValueError("inserted content must have a kind")
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


# ---------------------------------------------------------------------------
# Witnessed write primitives — receipt-by-construction
# ---------------------------------------------------------------------------
#
# These wrap the pure primitives above so that performing a write YIELDS a
# WriteReceipt computed from landed reality (the actual before/after diff +
# structural subtree hashes) alongside the mutated tree. You cannot obtain the
# mutated tree from a witnessed primitive without also obtaining the receipt —
# the conservation account is a structural product of the write, not an
# optional side-channel. The receipt records WHAT LANDED, never what was
# intended; the included ObservedWriteAudit is the independent tree-diff check
# that catches an incomplete or lying receipt (contract
# notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md §4, §5).


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """A mutated tree bundled with its conservation receipt and audit.

    The pair is inseparable by construction: the witnessed primitives return
    this, so no caller can take ``tree`` without also taking ``receipt`` and
    ``audit``. ``audit.audit_status == "violation"`` means the receipt did not cover
    the observed mutation footprint — strict mode must block on it.
    """

    tree: IRNode
    receipt: WriteReceipt
    audit: ObservedWriteAudit


_WriteAction = Literal["create", "replace", "remove", ""]


def receipt_from_diff(
    before: IRNode,
    after: IRNode,
    *,
    op_id: str,
    helper: str,
    action: str,
    bound_target_path: TreePath | None,
    landed_primary_path: TreePath | None = None,
    footprint_kind: _WriteAction = "",
    recovery_rule_ids: tuple[str, ...] = (),
    migration_rule_ids: tuple[str, ...] = (),
    fallback_rule_ids: tuple[str, ...] = (),
    source_anchor: "SourceAnchor | None" = None,
    observed_paths: TreePaths | None = None,
) -> WriteReceipt:
    """Build a WriteReceipt from the ACTUAL before/after diff (landed reality).

    The declared footprint and the pre/post structural hashes are derived from
    the real identity-pruned tree diff, NEVER from the nominal target. This is
    the by-construction guarantee: the receipt records exactly the paths that
    landed. ``footprint_kind`` only chooses which footprint bucket
    (created/replaced/removed) the observed paths are filed under; the paths
    themselves are always the observed diff, so the declared footprint equals
    the observed footprint and the independent audit is clean by construction.
    ``bound_target_path`` / ``landed_primary_path`` are the nominal addresses,
    kept for the bound→landed divergence check at lanes that have a real
    resolver binding. ``observed_paths`` lets a caller reuse the exact diff it
    already computed for this before/after pair; when omitted this helper owns
    the diff computation itself.
    """
    observed = (
        observed_paths
        if observed_paths is not None
        else diff_ir_paths_identity_pruned(before, after)
    )
    created: TreePaths = ()
    replaced: TreePaths = ()
    removed: TreePaths = ()
    if footprint_kind == "create":
        created = observed
    elif footprint_kind == "remove":
        removed = observed
    else:
        # Default and "replace": file every observed change as a replaced path.
        replaced = observed
    pre_hashes: Dict[str, str] = {}
    post_hashes: Dict[str, str] = {}
    for path in observed:
        addr = receipt_address_string(path)
        pre_hashes[addr] = structural_subtree_hash(resolve(before, path))
        post_hashes[addr] = structural_subtree_hash(resolve(after, path))
    landed = landed_primary_path
    if landed is None:
        landed = observed[0] if observed else bound_target_path
    return WriteReceipt(
        op_id=op_id,
        helper=helper,
        action=action,
        bound_target_path=bound_target_path,
        landed_primary_path=landed,
        created_paths=created,
        replaced_paths=replaced,
        removed_paths=removed,
        recovery_rule_ids=recovery_rule_ids,
        migration_rule_ids=migration_rule_ids,
        fallback_rule_ids=fallback_rule_ids,
        pre_hashes=pre_hashes,
        post_hashes=post_hashes,
        source_anchor=source_anchor,
    )


def _witnessed_outcome(
    before: IRNode,
    after: IRNode,
    *,
    op_id: str,
    helper: str,
    action: str,
    bound_target_path: TreePath | None,
    footprint_kind: _WriteAction,
) -> WriteOutcome:
    receipt = receipt_from_diff(
        before,
        after,
        op_id=op_id,
        helper=helper,
        action=action,
        bound_target_path=bound_target_path,
        footprint_kind=footprint_kind,
    )
    audit = build_observed_write_audit(before, after, receipt)
    return WriteOutcome(tree=after, receipt=receipt, audit=audit)


def replace_at_witnessed(
    tree: IRNode,
    path: Sequence[PathStep],
    content: IRNode,
    *,
    op_id: str = "",
    helper: str = "tree_ops.replace_at_witnessed",
) -> WriteOutcome:
    """``replace_at`` that yields a receipt + audit by construction."""
    target_path = _as_path(path)
    after = replace_at(tree, target_path, content)
    return _witnessed_outcome(
        tree,
        after,
        op_id=op_id,
        helper=helper,
        action="replace",
        bound_target_path=target_path or None,
        footprint_kind="replace",
    )


def remove_at_witnessed(
    tree: IRNode,
    path: Sequence[PathStep],
    *,
    op_id: str = "",
    helper: str = "tree_ops.remove_at_witnessed",
) -> WriteOutcome:
    """``remove_at`` that yields a receipt + audit by construction."""
    target_path = _as_path(path)
    after = remove_at(tree, target_path)
    return _witnessed_outcome(
        tree,
        after,
        op_id=op_id,
        helper=helper,
        action="remove",
        bound_target_path=target_path or None,
        footprint_kind="remove",
    )


def insert_sorted_witnessed(
    tree: IRNode,
    parent_path: Sequence[PathStep],
    content: IRNode,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]] = _default_sort_key,
    *,
    op_id: str = "",
    helper: str = "tree_ops.insert_sorted_witnessed",
) -> WriteOutcome:
    """``insert_sorted`` that yields a receipt + audit by construction."""
    parent = _as_path(parent_path)
    after = insert_sorted(tree, parent, content, sort_key_fn)
    created = parent + ((_kind_str(content.kind), content.label or ""),)
    return _witnessed_outcome(
        tree,
        after,
        op_id=op_id,
        helper=helper,
        action="insert",
        bound_target_path=created,
        footprint_kind="create",
    )


# ---------------------------------------------------------------------------
# Staged structural ops — the WriteReceipt footprint account, RETURNED.
# ---------------------------------------------------------------------------
#
# The ``_witnessed`` variants already build a WriteReceipt by construction, but
# that account is only reachable by a caller that opts into a receipt list. The
# ``_staged`` wrappers below SURFACE the same account as the canonical
# ``StageResult[IRNode]`` (Pro §2 stage contract) so the structural-mutation
# surface returns its footprint coverage + mutation-boundary residual type-
# carried, not as a side channel.
#
# Authority firewall (Pro §8): the core tree-op surface carries value + coverage
# + evidence ONLY. It never defaults ``replay_authorized`` on — the apply/
# authority waist attaches an ``ExecutionAuthorization`` later. ``authority``
# here is always the neutral surface.

#: The structural-mutation stage result alias (the carrier these ops return).
type StructuralStageResult = StageResult[IRNode]


def structural_stage_result(tree: IRNode, receipt: WriteReceipt) -> StructuralStageResult:
    """Project a landed ``WriteReceipt`` into the canonical ``StageResult[IRNode]``.

    The single mapping the staged tree ops and any receipt-building apply
    consumer share, so the footprint coverage + the mutation-boundary residual
    are derived identically wherever a structural write lands:

      * ``value``    = ``tree`` (the mutated IRNode).
      * ``coverage`` = every declared footprint path is ``owned`` (the write
        claimed it); ``unit="paths"``, ``residual``/``violation`` 0. The point
        is making the footprint a RETURNED account.
      * ``residuals`` = if ``receipt.divergence_explained is False`` exactly one
        blocking ``unowned_violation`` residual (the §4-contract "unexplained
        divergence becomes a blocking residual", now type-carried instead of
        strict-mode-only); otherwise empty.
      * ``evidence`` = a ``SourceWitness`` projecting the receipt's
        ``source_anchor`` quote-hash into a ``DigestWitness`` when present, else
        empty footing.
      * ``findings``  = ``()`` (the FI apply layer owns source-pathology
        findings; the core op surface emits none).
      * ``authority`` = neutral (the firewall; authorization is attached later).
    """
    footprint = receipt.declared_footprint
    declared = len(footprint)
    coverage = CoverageCertificate(
        unit="paths",
        total=declared,
        owned=declared,
        residual=0,
        violation=0,
    )

    residuals: tuple[Residual, ...] = ()
    if not receipt.divergence_explained:
        residuals = (
            Residual(
                kind="unowned_violation",
                reason="unexplained_mutation_boundary_divergence",
                scope=(
                    receipt_address_string(receipt.bound_target_path)
                    if receipt.bound_target_path is not None
                    else ""
                ),
                text="",
                blocking=True,
            ),
        )

    evidence = EMPTY_EVIDENCE
    anchor = receipt.source_anchor
    if anchor is not None:
        algorithm, _, digest = anchor.quote_hash.partition(":")
        evidence = EvidenceBundle(
            (
                SourceWitness(
                    source_role="amendment_source_clause",
                    artifact_id=anchor.source_artifact_id,
                    digest=DigestWitness(digest_algorithm=algorithm, digest=digest),
                ),
            )
        )

    return StageResult(
        value=tree,
        evidence=evidence,
        residuals=residuals,
        findings=(),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )


def replace_at_staged(
    tree: IRNode,
    path: Sequence[PathStep],
    content: IRNode,
    *,
    op_id: str = "",
    helper: str = "tree_ops.replace_at_staged",
) -> StructuralStageResult:
    """``replace_at`` returning the footprint account as ``StageResult[IRNode]``."""
    outcome = replace_at_witnessed(tree, path, content, op_id=op_id, helper=helper)
    return structural_stage_result(outcome.tree, outcome.receipt)


def remove_at_staged(
    tree: IRNode,
    path: Sequence[PathStep],
    *,
    op_id: str = "",
    helper: str = "tree_ops.remove_at_staged",
) -> StructuralStageResult:
    """``remove_at`` returning the footprint account as ``StageResult[IRNode]``."""
    outcome = remove_at_witnessed(tree, path, op_id=op_id, helper=helper)
    return structural_stage_result(outcome.tree, outcome.receipt)


def insert_sorted_staged(
    tree: IRNode,
    parent_path: Sequence[PathStep],
    content: IRNode,
    sort_key_fn: Callable[[Optional[str]], Tuple[int, str, int]] = _default_sort_key,
    *,
    op_id: str = "",
    helper: str = "tree_ops.insert_sorted_staged",
) -> StructuralStageResult:
    """``insert_sorted`` returning the footprint account as ``StageResult[IRNode]``."""
    outcome = insert_sorted_witnessed(
        tree, parent_path, content, sort_key_fn, op_id=op_id, helper=helper
    )
    return structural_stage_result(outcome.tree, outcome.receipt)


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
        "subdivision",
        "schedule",
        "appendix",
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
    }
)


def has_dedup_label_duplicates(tree: IRNode) -> bool:
    """Return True when ``dedup_children_by_label`` would have duplicate work.

    This is a cheap predicate for hot replay paths that only need to decide
    whether the owned same-kind+label dedup backstop is relevant.  It uses the
    exact same container and target-kind policy as ``dedup_children_by_label``.
    """
    stack: list[IRNode] = [tree]
    while stack:
        node = stack.pop()
        children = node.children
        if not children:
            continue
        node_kind = node.kind
        is_dedup_container = (
            node_kind is IRNodeKind.BODY
            or node_kind is IRNodeKind.CHAPTER
            or node_kind is IRNodeKind.PART
            or node_kind is IRNodeKind.HCONTAINER
            or node_kind is IRNodeKind.SECTION
            or (type(node_kind) is str and node_kind in _SECTION_DEDUP_CONTAINER_KINDS)
        )
        if is_dedup_container:
            seen: set[tuple[str, str]] = set()
            for child in children:
                if not child.label:
                    continue
                # Use the same total kind->string + target-membership policy as
                # ``dedup_children_by_label`` so the cheap predicate cannot drift
                # from the function it predicts.  Mapping enum members through a
                # hand-rolled if/elif (the prior form) silently excluded any
                # dedup-target kind whose enum branch was forgotten -- a new
                # member of _DEDUP_TARGET_KINDS would then be dropped here while
                # still deduped by dedup_children_by_label, making this predicate
                # wrongly report "no duplicates".
                child_kind = _kind_str(child.kind)
                if child_kind not in _DEDUP_TARGET_KINDS:
                    # Defensive coherence guard: an IRNodeKind enum member whose
                    # string value IS a dedup target but that ``_kind_str`` failed
                    # to surface would be a silent loss.  ``_kind_str`` is total,
                    # so this only fires on a genuinely corrupt/unmapped kind.
                    if isinstance(child.kind, IRNodeKind) and child.kind.value in _DEDUP_TARGET_KINDS:
                        raise ValueError(
                            "has_dedup_label_duplicates: IRNodeKind "
                            f"{child.kind!r} is a dedup target by value but did "
                            "not resolve to its target string via _kind_str; "
                            "refusing to silently drop it from dedup detection"
                        )
                    continue
                key = (child_kind, child.label)
                if key in seen:
                    return True
                seen.add(key)
        for child in children:
            if child.children:
                stack.append(child)
    return False


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
            labels = [str(n.label or "") for n in nodes]
            if _preserve_source_order_for_mixed_labels(ck, labels):
                continue
            run_key_fn = _run_aware_sort_key_fn(labels, sort_key_fn)
            sorted_nodes = sorted(nodes, key=lambda n, _k=run_key_fn: _k(n.label))
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
        "p1group",
    },
    "hcontainer": {"part", "chapter", "section", "subsection", "hcontainer", "crossHeading", "heading", "num", "content", "omission"},
    "part": {
        "chapter",
        "section",
        "heading",
        "num",
        "crossHeading",
        "crossheading",
        "p1group",
        "pblock",
    },
    "chapter": {
        "section",
        "subsection",
        "division",
        "omission",
        "heading",
        "num",
        "crossHeading",
        "crossheading",
        "p1group",
    },
    "section": {"subsection", "paragraph", "subparagraph", "schedule_entry", "table", "pgroup", "omission", "heading", "num", "content", "crossHeading", "crossheading"},
    "subsection": {"intro", "content", "paragraph", "subparagraph", "item", "schedule_entry", "table", "pgroup", "num", "hcontainer", "wrapUp", "omission", "crossHeading", "crossheading"},
    "division": {
        "division",
        "subdivision",
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
    "subdivision": {
        "subdivision",
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
        "schedule_entry",
        "table",
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
    "subparagraph": {"subparagraph", "item", "schedule_entry", "sentence", "heading", "num", "content", "pgroup", "intro", "wrapUp", "omission", "hcontainer"},
    "item": {"item", "subparagraph", "sentence", "content", "intro", "wrapUp", "omission", "hcontainer"},
    "sentence": {"content"},
    "p1group": {"paragraph", "section", "article", "rule", "regulation", "heading", "num"},
    "pblock": {"p1group", "crossHeading", "crossheading", "section", "heading", "num"},
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
        "subdivision",
        "schedule",
        "appendix",
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
    }
)
_SOURCE_ORDER_MIXED_LABEL_KINDS = frozenset({"paragraph", "subparagraph", "item", "sentence"})


def _label_order_family(label: str) -> str:
    normalized = _norm(label)
    if _COMPOUND_NUMERIC_SORT_LABEL_RE.match(normalized) or _LETTER_SUFFIX_SORT_LABEL_RE.match(normalized):
        return "numbered"
    if _PURE_ALPHA_LABEL_RE.match(normalized):
        return "alpha"
    return "other"


def _preserve_source_order_for_mixed_labels(kind: str, labels: Sequence[str]) -> bool:
    if kind not in _SOURCE_ORDER_MIXED_LABEL_KINDS:
        return False
    families = {_label_order_family(label) for label in labels if label}
    return "numbered" in families and "alpha" in families


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
    container_kind: Optional[str] = None
    container_label: Optional[str] = None

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
        if self.kind == "mixed_hierarchy_child":
            container = self.container_kind or "container"
            if self.container_label:
                container = f"{container}:{self.container_label}"
            child = self.child_kind or "child"
            if self.label:
                child = f"{child}:{self.label}"
            return f"{self.path_text}: direct {child} alongside {container}"
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
            "container_kind": self.container_kind,
            "container_label": self.container_label,
        }


def _iter_duplicate_order_tree_invariant_violations(
    tree: TreeInvariantNode,
    *,
    sort_key: Callable[[Optional[str]], Tuple[int, str, int]],
    root_path: InvariantPath,
) -> Iterator[TreeInvariantViolation]:
    """Specialized traversal for callers that only consume duplicate/order records."""

    def _check(node: TreeInvariantNode, path: InvariantPath) -> Iterator[TreeInvariantViolation]:
        child_entries: list[tuple[TreeInvariantNode, str, Optional[str]]] = []
        seen: Dict[Tuple[str, str], int] = {}
        by_kind: Dict[str, List[str]] = {}
        for child in node.children:
            child_kind = _kind_str(child.kind)
            child_label = child.label
            child_entries.append((child, child_kind, child_label))
            if child_label:
                seen_key = (child_kind, child_label)
                seen[seen_key] = seen.get(seen_key, 0) + 1
                by_kind.setdefault(child_kind, []).append(child_label)
        for (kind, label), count in seen.items():
            if count > 1:
                yield TreeInvariantViolation(
                    kind="duplicate_label",
                    path=path,
                    child_kind=kind,
                    label=label,
                    count=count,
                )
        for kind, labels in by_kind.items():
            if kind in _ORDERED_INVARIANT_KINDS:
                if _preserve_source_order_for_mixed_labels(kind, labels):
                    continue
                run_key = _run_aware_sort_key_fn(labels, sort_key)
                keys = [run_key(label) for label in labels]
                for i, (left_key, right_key) in enumerate(pairwise(keys)):
                    if left_key > right_key:
                        yield TreeInvariantViolation(
                            kind="sort_order",
                            path=path,
                            child_kind=kind,
                            previous_label=labels[i],
                            next_label=labels[i + 1],
                        )
        for child, child_kind, child_label in child_entries:
            if child.children:
                yield from _check(child, path + ((child_kind, child_label),))

    yield from _check(tree, root_path)


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
    root = root_path or ((_kind_str(tree.kind), None),)
    if selected == {"duplicate_label", "sort_order"}:
        yield from _iter_duplicate_order_tree_invariant_violations(
            tree,
            sort_key=_sort_key,
            root_path=root,
        )
        return

    # Hoist the per-family membership predicates out of the per-node closure.
    # The cProfile of a complete UK statute replay (`ukpga/1988/1`,
    # 2026-06-24) showed 102M ``enum.__hash__`` / 21s dominated by the
    # ~5 ``_wants(...)`` calls per node visit at ~17M visits. ``selected`` is
    # constant per scan; resolving family membership once per scan (not per
    # child) preserves the yielded violations exactly.
    wants_duplicate = selected is None or "duplicate_label" in selected
    wants_normalized_duplicate = (
        selected is None or "normalized_duplicate_label" in selected
    )
    wants_sort_order = selected is None or "sort_order" in selected
    wants_unexpected_kind = selected is None or "unexpected_child_kind" in selected
    wants_mixed_hierarchy = selected is not None and "mixed_hierarchy_child" in selected

    def _check(node: TreeInvariantNode, path: InvariantPath) -> Iterator[TreeInvariantViolation]:
        if not node.children:
            return
        # Resolve kind_str once per child rather than 4-5 times per visit; the
        # ``kind_str`` helper's ``isinstance`` + ``.value`` lookup was the
        # single largest idle cost on the UK replay hot path.
        child_entries: list[tuple[TreeInvariantNode, str, Optional[str]]] = [
            (child, _kind_str(child.kind), child.label) for child in node.children
        ]

        if wants_duplicate:
            seen: Dict[Tuple[str, str], int] = {}
            for _child, child_kind, child_label in child_entries:
                if child_label:
                    key = (child_kind, child_label)
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

        if wants_normalized_duplicate:
            norm_seen: Dict[Tuple[str, str], str] = {}
            for _child, child_kind, child_label in child_entries:
                if child_label is not None:
                    normalized_label = _norm(child_label)
                    norm_key = (child_kind, normalized_label)
                    if norm_key in norm_seen:
                        if norm_seen[norm_key] != child_label:
                            yield TreeInvariantViolation(
                                kind="normalized_duplicate_label",
                                path=path,
                                child_kind=child_kind,
                                normalized_label=normalized_label,
                            )
                    else:
                        norm_seen[norm_key] = child_label

        if wants_sort_order:
            by_kind: Dict[str, List[str]] = {}
            for _child, child_kind, child_label in child_entries:
                if child_label:
                    by_kind.setdefault(child_kind, []).append(child_label)
            for kind, labels in by_kind.items():
                if kind in _ORDERED_INVARIANT_KINDS:
                    if _preserve_source_order_for_mixed_labels(kind, labels):
                        continue
                    run_key = _run_aware_sort_key_fn(labels, _sort_key)
                    keys = [run_key(label) for label in labels]
                    for i, (left_key, right_key) in enumerate(pairwise(keys)):
                        if left_key > right_key:
                            yield TreeInvariantViolation(
                                kind="sort_order",
                                path=path,
                                child_kind=kind,
                                previous_label=labels[i],
                                next_label=labels[i + 1],
                            )

        if wants_unexpected_kind:
            parent_kind = _kind_str(node.kind)
            allowed = _NESTING_ORDER.get(parent_kind)
            if allowed is not None:
                for _child, child_kind, _child_label in child_entries:
                    if child_kind not in allowed:
                        yield TreeInvariantViolation(
                            kind="unexpected_child_kind",
                            path=path,
                            parent_kind=parent_kind,
                            child_kind=child_kind,
                        )

        if wants_mixed_hierarchy:
            ranked_children = [
                (index, kind_value, label_value)
                for index, (_child, kind_value, label_value) in enumerate(child_entries)
                if label_value
            ]
            container_rank = {"part": 0, "chapter": 1, "section": 2}
            ranked_children = [
                (index, kind_value, label_value, container_rank[kind_value])
                for index, kind_value, label_value in ranked_children
                if kind_value in container_rank
            ]
            for child_index, child_kind, child_label, child_rank in ranked_children:
                previous_containers = [
                    (container_index, container_kind, container_label)
                    for container_index, container_kind, container_label, container_rank_value in ranked_children
                    if container_index < child_index and container_rank_value < child_rank
                ]
                following_containers = [
                    (container_index, container_kind, container_label)
                    for container_index, container_kind, container_label, container_rank_value in ranked_children
                    if container_index > child_index and container_rank_value < child_rank
                ]
                container = (
                    previous_containers[-1]
                    if previous_containers
                    else (following_containers[0] if following_containers else None)
                )
                if container is None:
                    continue
                _container_index, container_kind, container_label = container
                yield TreeInvariantViolation(
                    kind="mixed_hierarchy_child",
                    path=path,
                    parent_kind=_kind_str(node.kind),
                    child_kind=child_kind,
                    label=child_label,
                    container_kind=container_kind,
                    container_label=container_label,
                )

        for child, child_kind, child_label in child_entries:
            if child.children:
                yield from _check(
                    child, path + ((child_kind, child_label),)
                )

    yield from _check(tree, root)


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
#
# §2.3 firewall note: this kernel lint previously branched on the Finnish
# ``tarkoitetaan`` idiom via hardcoded phrase constants. That fragment has been
# moved to ``lawvm.finland.definition_introducer`` (the FI frontend). The kernel
# still applies the jurisdiction-neutral suffix-colon (``:``) drafting signal,
# but the language-specific signal is supplied as a frontend-provided predicate
# via ``find_flattened_sublist_warnings(definition_introducer_predicate=...)``.
# Core treats the predicate's verdict as an opaque bool (AGENTS.md §2.3 — core
# may host a hook used by frontends; it must not interpret frontend-local
# values).


def _node_intro_text(node: IRNode) -> str:
    """Return visible intro/content text from one IR node."""
    parts: List[str] = []
    if node.text and node.text.strip():
        parts.append(node.text.strip())
    for child in node.children:
        if _kind_str(child.kind) in {"intro", "content"} and child.text and child.text.strip():
            parts.append(child.text.strip())
    return " ".join(parts).strip()


def _has_definition_list_introducer(
    parent: IRNode,
    *,
    definition_introducer_predicate: Optional[Callable[[IRNode], bool]] = None,
) -> bool:
    """Return True when a subsection-style parent opens a definitions list.

    The suffix-colon (``:``) check is a jurisdiction-neutral drafting convention
    and stays in the kernel (``Tämä luvussa tarkoitetaan:`` style intros are
    universal across drafting traditions). The language-specific fragment signal
    — e.g. the Finnish ``tarkoitetaan`` idiom — is delegated to a
    frontend-supplied predicate (AGENTS.md §2.3: core hosts the hook; it does
    not interpret frontend-local values).

    When ``definition_introducer_predicate`` is ``None`` only the suffix-colon
    check applies, which is the safe default for any caller that has not wired
    in a frontend predicate (UK's ``replay_records`` and the generic CLI
    ``invariant-bisect`` path take this default; Finland supplies its predicate
    at the FI replay-projection call sites).
    """
    for child in parent.children:
        if _kind_str(child.kind) != "intro":
            continue
        text = _node_intro_text(child)
        if not text:
            continue
        if text.rstrip().endswith(":"):
            return True
    if definition_introducer_predicate is not None and definition_introducer_predicate(parent):
        return True
    return False


def _fs_label_family(label: str) -> str:
    """Classify a label into: 'digit', 'alpha', 'roman', or 'mixed'."""
    s = label.strip().rstrip(".")
    if not s:
        return "mixed"
    if _FS_DIGIT_LABEL_RE.fullmatch(s):
        return "digit"
    if _FS_ROMAN_LABEL_RE.fullmatch(s):
        # Multi-letter roman tokens only.  Single glyphs such as i/v/x/c are
        # almost always alphabetic subitems in legal corpora, not roman numerals.
        if len(s) > 1:
            return "roman"
        return "alpha"
    if _FS_ALPHA_LABEL_RE.fullmatch(s):
        return "alpha"
    return "mixed"


def _fs_family_runs(families: Sequence[str]) -> list[tuple[str, int]]:
    """Collapse consecutive identical label families into (family, run_length) pairs."""
    runs: list[tuple[str, int]] = []
    for family in families:
        if family == "mixed":
            continue
        if runs and runs[-1][0] == family:
            runs[-1] = (family, runs[-1][1] + 1)
        else:
            runs.append((family, 1))
    return runs


def _fs_ordinal(label: str, family: str) -> int:
    """Return a rough ordinal for a label within its family (0 if unknown)."""
    s = label.strip().rstrip(".")
    if family == "digit":
        m = _FS_ORDINAL_DIGITS_RE.match(s)
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
    definition_introducer_predicate: Optional[Callable[[IRNode], bool]] = None,
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

    3. **Mixed alpha+digit families**: same-kind siblings contain runs of both
       lettered and digit labels with length ≥ 2 each.  Example: ``a b c 1 2 3``
       where lettered subitems were not nested under their introducer digit
       parent.  Single-letter introducers such as ``jos`` between digit runs do
       not satisfy the ≥ 2 alpha-run threshold.

    These are lint-style warnings, not hard invariants.  They are useful for
    detecting replay/apply bugs where sections from separate subsections have been
    collapsed to the same structural level.

    ``definition_introducer_predicate`` (optional) lets a frontend supply a
    language-specific "is this parent a definition-list introducer?" signal —
    the kernel applies the jurisdiction-neutral suffix-colon (``:``) drafting
    check universally, and ORs in this predicate's verdict. Finland wires its
    ``fi_definition_list_introducer_predicate`` from
    ``lawvm.finland.definition_introducer``; other callers omit the parameter
    and get the suffix-colon-only default (AGENTS.md §2.3 — core does not
    interpret frontend-local values).
    """
    warnings: List[Dict[str, object]] = []

    def _walk(node: IRNode, path: str) -> None:
        skip_mixed_family_lint = _has_definition_list_introducer(
            node,
            definition_introducer_predicate=definition_introducer_predicate,
        )

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
                continue  # don't double-report with pattern 2/3

            # --- Pattern 3: mixed alpha+digit sibling families ---
            ordinal_runs = [
                (family, run_length)
                for family, run_length in _fs_family_runs(families)
                if family in {"alpha", "digit", "roman"}
            ]
            alpha_run = max(
                (
                    run_length
                    for family, run_length in ordinal_runs
                    if family in {"alpha", "roman"}
                ),
                default=0,
            )
            digit_run = max(
                (run_length for family, run_length in ordinal_runs if family == "digit"),
                default=0,
            )
            if alpha_run >= 2 and digit_run >= 2:
                if skip_mixed_family_lint and kind in {"paragraph", "subparagraph", "item"}:
                    continue
                mixed_families = sorted({family for family, _run_length in ordinal_runs})
                warnings.append({
                    "kind": "flattened_sublist_mixed_family",
                    "path": path,
                    "node_kind": kind,
                    "families": mixed_families,
                    "alpha_run": alpha_run,
                    "digit_run": digit_run,
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


# ---------------------------------------------------------------------------
# Label-sequence-gap detection (lint-level heuristic)
# ---------------------------------------------------------------------------

_LOCAL_SEQUENCE_START_KINDS = frozenset(
    {
        "subsection",
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
    }
)


def _is_tombstone_or_scaffold_node(node: IRNode) -> bool:
    attrs = node.attrs
    return bool(
        attrs.get("lawvm_repeal_placeholder")
        or attrs.get("lawvm_tombstone")
        or attrs.get("lawvm_scaffold")
        or attrs.get("content_state") in {"tombstone", "scaffold"}
    )


def _alpha_ordinal(label: str) -> int:
    value = 0
    for char in label.lower():
        if not ("a" <= char <= "z"):
            return 0
        value = value * 26 + (ord(char) - ord("a") + 1)
    return value


def _roman_ordinal(label: str) -> int:
    roman_map = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    result, previous = 0, 0
    for char in reversed(label.lower()):
        value = roman_map.get(char, 0)
        if value == 0:
            return 0
        result += value if value >= previous else -value
        previous = value
    return result


def _label_sequence_family_and_ordinal(label: str) -> tuple[str, int] | None:
    normalized = normalized_label_key(label)
    if not normalized:
        return None
    if _PURE_DIGIT_LABEL_RE.fullmatch(normalized):
        return ("digit", int(normalized))
    if _PURE_ALPHA_LABEL_RE.fullmatch(normalized):
        # Single-letter legal labels are almost always alphabetic subitems.  Treat
        # multi-letter roman-looking labels such as "iv" as roman only when they
        # are not an alphabetic continuation token such as "aa" or "ab".
        if len(normalized) > 1 and _PURE_ROMAN_LABEL_RE.fullmatch(normalized) and normalized not in {"aa", "ab"}:
            ordinal = _roman_ordinal(normalized)
            if ordinal > 0:
                return ("roman", ordinal)
        return ("alpha", _alpha_ordinal(normalized))
    return None


_ROMAN_RENDER_TABLE: tuple[tuple[int, str], ...] = (
    (1000, "m"),
    (900, "cm"),
    (500, "d"),
    (400, "cd"),
    (100, "c"),
    (90, "xc"),
    (50, "l"),
    (40, "xl"),
    (10, "x"),
    (9, "ix"),
    (5, "v"),
    (4, "iv"),
    (1, "i"),
)


def _render_roman(ordinal: int) -> str:
    """Render a positive ordinal as a lowercase Roman numeral (inverse of ``_roman_ordinal``)."""
    if ordinal <= 0:
        return str(ordinal)
    chars: list[str] = []
    remaining = ordinal
    for value, glyphs in _ROMAN_RENDER_TABLE:
        while remaining >= value:
            chars.append(glyphs)
            remaining -= value
    return "".join(chars)


def _render_sequence_label(family: str, ordinal: int) -> str:
    if family == "digit":
        return str(ordinal)
    if family == "alpha":
        chars: list[str] = []
        n = ordinal
        while n > 0:
            n -= 1
            chars.append(chr(ord("a") + (n % 26)))
            n //= 26
        return "".join(reversed(chars))
    if family == "roman":
        return _render_roman(ordinal)
    return str(ordinal)


def find_label_sequence_gap_warnings(
    tree: IRNode,
    *,
    max_missing_labels: int = 12,
) -> List[Dict[str, object]]:
    """Return warnings for suspicious gaps in same-kind sibling label sequences.

    This is a lint, not a hard invariant.  It catches shapes like a local list
    that starts at ``g`` with no ``a``-``f`` siblings, or a sibling run
    ``1, 3, 4`` with no visible ``2``.  Existing tombstone/scaffold children
    count as occupied labels, so a repealed slot represented in the tree does
    not trigger a gap warning.
    """
    warnings: List[Dict[str, object]] = []

    def _missing_labels(family: str, start: int, end: int) -> list[str]:
        return [_render_sequence_label(family, ordinal) for ordinal in range(start, min(end, start + max_missing_labels))]

    def _walk(node: IRNode, path: str) -> None:
        by_kind: Dict[str, List[tuple[IRNode, str, str, int]]] = {}
        tombstone_labels_by_kind: Dict[str, List[str]] = {}
        for child in node.children:
            if not child.label:
                continue
            child_kind = _kind_str(child.kind)
            parsed = _label_sequence_family_and_ordinal(child.label)
            if parsed is None:
                continue
            family, ordinal = parsed
            by_kind.setdefault(child_kind, []).append((child, family, child.label, ordinal))
            if _is_tombstone_or_scaffold_node(child):
                tombstone_labels_by_kind.setdefault(child_kind, []).append(child.label)

        for child_kind, entries in by_kind.items():
            if len({family for _child, family, _label, _ordinal in entries}) != 1:
                continue
            family = entries[0][1]
            labels = [label for _child, _family, label, _ordinal in entries]
            ordinals = [ordinal for _child, _family, _label, ordinal in entries]
            present_ordinals = set(ordinals)
            tombstone_labels = tombstone_labels_by_kind.get(child_kind, [])
            for tombstone_label in tombstone_labels:
                parsed_tombstone = _label_sequence_family_and_ordinal(tombstone_label)
                if parsed_tombstone is not None and parsed_tombstone[0] == family:
                    present_ordinals.add(parsed_tombstone[1])

            if (
                child_kind == "section"
                and path in {"body", "hcontainer"}
                and any(
                    _kind_str(sibling.kind) in {"part", "chapter", "hcontainer"}
                    for sibling in node.children
                    if sibling.label is not None or _kind_str(sibling.kind) == "hcontainer"
                )
            ):
                continue

            first = ordinals[0]
            if child_kind in _LOCAL_SEQUENCE_START_KINDS and first > 1:
                missing_ordinals = [ordinal for ordinal in range(1, first) if ordinal not in present_ordinals]
                if missing_ordinals:
                    warnings.append(
                        {
                            "kind": "label_sequence_starts_late",
                            "path": path,
                            "node_kind": child_kind,
                            "family": family,
                            "previous_label": None,
                            "next_label": labels[0],
                            "missing_labels": _missing_labels(family, missing_ordinals[0], first),
                            "missing_label_count": len(missing_ordinals),
                            "label_sample": labels[:14],
                            "tombstone_labels_present": tombstone_labels,
                        }
                    )

            for index, (left, right) in enumerate(pairwise(ordinals)):
                if right <= left + 1:
                    continue
                missing_ordinals = [ordinal for ordinal in range(left + 1, right) if ordinal not in present_ordinals]
                if not missing_ordinals:
                    continue
                if child_kind == "section" and len(missing_ordinals) == 1:
                    continue
                warnings.append(
                    {
                        "kind": "label_sequence_internal_gap",
                        "path": path,
                        "node_kind": child_kind,
                        "family": family,
                        "previous_label": labels[index],
                        "next_label": labels[index + 1],
                        "missing_labels": _missing_labels(family, missing_ordinals[0], right),
                        "missing_label_count": len(missing_ordinals),
                        "label_sample": labels[:14],
                        "tombstone_labels_present": tombstone_labels,
                    }
                )

        for child in node.children:
            _walk(child, f"{path}/{_kind_str(child.kind)}:{child.label or '?'}")

    _walk(tree, _kind_str(tree.kind))
    return warnings
