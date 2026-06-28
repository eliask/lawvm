"""Shared XML helpers for the UK legislation frontend."""
from __future__ import annotations

import copy
from lxml import etree as ET
from functools import lru_cache
from typing import Any, Sequence

from lawvm.core.ir import IRNode
from lawvm.uk_legislation.uk_grafter import _LEG_NS


# ---------------------------------------------------------------------------
# §source_root_lifecycle (AGENTS.md §2.7): element-keyed dict cache with a
# root-id reverse index so eviction is O(keys-for-this-root) instead of
# O(cache_size).
#
# lxml _Element objects do not support weak references, so plain dicts are
# used as the storage and explicit eviction via evict_root(root) is the
# memory-safety contract.  The reverse index maps id(root_element) → set of
# cache keys whose root is that element, maintained on __setitem__/pop/__del__;
# evict_root(root) walks only that bucket rather than scanning the whole
# cache × .getroottree().getroot() per entry.
#
# Keys MUST be lxml _Element objects (the root or any of its descendants);
# the cache walks key.getparent() up to the root on insert (O(depth), no
# ElementTree wrapper allocation) and stays in sync on mutation.
# ---------------------------------------------------------------------------


class _RootScopedCache(dict):
    """A dict keyed on lxml ``_Element`` objects that maintains a
    ``dict[id(root), set[key]]`` reverse index so eviction of an entire
    source-root tree is O(keys-for-this-root) rather than O(cache_size).

    All public dict operations (``__setitem__``, ``__getitem__``,
    ``__delitem__``, ``__contains__``, ``.get``, ``.pop``, ``__iter__``)
    are inherited unchanged and stay in sync with the reverse index via the
    overridden mutators.  The single new entry point is
    :meth:`evict_root`, called from the compile-loop boundary when the
    last effect for a source root has been processed.
    """

    # NOTE: no ``__slots__`` — dict subclasses already carry a ``__dict__``
    # for instance state, and we need one for ``_reverse``.
    _reverse: dict[int, set[Any]]

    def __init__(self) -> None:
        super().__init__()
        # Avoid the per-instance ``_reverse`` attribute being annotated
        # away from the class: keep it as a plain dict on each instance.
        object.__setattr__(self, "_reverse", {})

    @staticmethod
    def _root_id(key: ET._Element) -> int:
        # Walk key.getparent() up to the root element; avoid allocating an
        # ``_ElementTree`` wrapper per insertion (lxml allocates one each
        # time ``getroottree()`` is called).  O(depth); trees are shallow.
        parent: ET._Element = key
        while True:
            next_parent = parent.getparent()
            if next_parent is None:
                return id(parent)
            parent = next_parent

    def __setitem__(self, key: ET._Element, value: Any) -> None:
        super().__setitem__(key, value)
        rid = self._root_id(key)
        bucket = self._reverse.get(rid)
        if bucket is None:
            self._reverse[rid] = {key}
        else:
            bucket.add(key)

    def __delitem__(self, key: ET._Element) -> None:
        super().__delitem__(key)
        self._drop_key(key)

    def _drop_key(self, key: ET._Element) -> None:
        # ``getparent()`` is O(depth) and the key was alive (still in the
        # cache) moments ago, so its root is alive too.
        rid = self._root_id(key)
        bucket = self._reverse.get(rid)
        if bucket is None:
            return
        bucket.discard(key)
        if not bucket:
            self._reverse.pop(rid, None)

    def pop(self, key: ET._Element, default: Any = None) -> Any:
        if key in self:
            self._drop_key(key)
        return super().pop(key, default)

    def setdefault(self, key: ET._Element, default: Any = None) -> Any:
        if key in self:
            return self[key]
        self[key] = default
        return default

    def update(self, *args: Any, **kwargs: Any) -> None:
        # Route through __setitem__ so the reverse index stays in sync.  The
        # ``**kwargs`` branch is intentionally absent: cache keys are always
        # ``_Element`` (never ``str``), so a keyword-style update cannot
        # correspond to a valid cache key.  Accepting ``**kwargs`` only for
        # signature parity keeps the dict substitution shape working while
        # ignoring the (always-empty) keyword set.
        if args:
            other = args[0]
            if hasattr(other, "items"):
                for k, v in other.items():
                    self[k] = v
            else:
                for k, v in other:
                    self[k] = v

    def clear(self) -> None:
        self._reverse.clear()
        super().clear()

    def evict_root(self, root: ET._Element) -> None:
        """Remove every cache entry whose key belongs to ``root``.

        O(keys-for-this-root) — walks only the bucket indexed by
        ``id(root)`` rather than scanning the whole cache.  No-op when root
        has no entries (e.g. a fresh root, or one already evicted).
        """
        bucket = self._reverse.pop(id(root), None)
        if bucket is None:
            return
        # Snapshot to guard against concurrent mutation during iteration; we
        # use the dict's own pop to avoid the (O(depth)) re-walk in __del__.
        for key in tuple(bucket):
            super().pop(key, None)


# lxml _Element objects do not support weak references; use _RootScopedCache
# so eviction of a source root is O(keys-for-this-root) rather than
# O(cache_size).  See AGENTS.md §2.7 source-root cache lifecycle.
_TEXT_CONTENT_CACHE: _RootScopedCache = _RootScopedCache()
_DIRECT_STRUCTURAL_NUM_CACHE: _RootScopedCache = _RootScopedCache()


@lru_cache(maxsize=4096)
def _local_tag_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _tag(el: ET._Element) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return _local_tag_name(tag)


def _text_content(el: ET._Element) -> str:
    """Recursively collect normalised text."""
    cached = _TEXT_CONTENT_CACHE.get(el)
    if cached is not None:
        return cached
    parts: list[str] = []
    def collect(node: ET._Element, *, include_tail: bool) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            collect(child, include_tail=True)
        if include_tail and node.tail:
            parts.append(node.tail)

    collect(el, include_tail=False)
    text = " ".join(" ".join(parts).split())
    _TEXT_CONTENT_CACHE[el] = text
    return text


def _direct_structural_num(el: ET._Element) -> str:
    """Return the node's own structural number, not a descendant's number."""
    cached = _DIRECT_STRUCTURAL_NUM_CACHE.get(el)
    if cached is not None:
        return cached
    num_el = el.find(f"./{{{_LEG_NS}}}Pnumber")
    if num_el is None:
        num_el = el.find(f"./{{{_LEG_NS}}}Number")
    if num_el is None and _tag(el) == "Schedule":
        num_el = el.find(f".//{{{_LEG_NS}}}Number")
    if num_el is None:
        _DIRECT_STRUCTURAL_NUM_CACHE[el] = ""
        return ""
    num = _text_content(num_el)
    _DIRECT_STRUCTURAL_NUM_CACHE[el] = num
    return num


def evict_xml_helper_caches(root: ET._Element) -> None:
    """Evict source-root scoped text/number caches for an archived XML root."""
    _TEXT_CONTENT_CACHE.evict_root(root)
    _DIRECT_STRUCTURAL_NUM_CACHE.evict_root(root)


def _structural_children(el: ET._Element) -> tuple[ET._Element, ...]:
    structural_tags = {
        "Part",
        "Chapter",
        "EUChapter",
        "Pblock",
        "P1group",
        "Section",
        "P1",
        "Article",
        "Rule",
        "Subsection",
        "P2",
        "P3",
        "P4",
        "Schedule",
    }
    return tuple(child for child in list(el) if _tag(child) in structural_tags)


def _clone_element(el: ET._Element) -> ET._Element:
    # perf (iter2 W5 M6): ``copy.deepcopy`` is the canonical lxml-endorsed
    # element clone — it preserves tag/text/tail/attrib/children/nsmap exactly
    # without a serialize→parse round-trip. The prior
    # ``ET.fromstring(ET.tostring(el, encoding="unicode"))`` form was O(S) over
    # the subtree AND ran a full XML re-tokenization; the 3 callers
    # (``source_payload_elaboration.py:138/404/411``) sit on the per-amendment-
    # block hot path. Behavior is byte-identical for well-formed subtrees
    # (pinned by tests/test_uk_xml_helpers.py::test_clone_element_deepcopy_*).
    return copy.deepcopy(el)


def get_all_eids(nodes: Sequence[IRNode]) -> list[str]:
    """Recursively gather all eIds from an IR tree fragment."""
    eids = []
    for n in nodes:
        eid = n.attrs.get("id") or n.attrs.get("eId")
        if eid:
            eids.append(eid)
        if n.children:
            eids.extend(get_all_eids(n.children))
    return eids
