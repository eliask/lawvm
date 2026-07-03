"""Fuzz + identity tests for NZ upward whitespace-normalization composition.

``_collect_legal_text`` / ``_legal_text`` compose each node's normalized flow
text from its children's already-normalized forms instead of re-normalizing the
full raw subtree concatenation at every structural ancestor. This is only sound
if, for EVERY node, the composed normalized text is byte-identical to
``_normalize_text`` of the full raw ordered concatenation the historical
extractor built. These tests prove that identity on randomly generated nested
XML trees (varied text/tail whitespace including leading/trailing/none/Unicode-ws
/empty, deep nesting, excluded subtrees, def-para, and adversarial mid-token
boundaries where a piece has an empty tail so neighbouring tokens would merge if
the join lost its separator space).
"""

from __future__ import annotations

import random

from lxml import etree

import lawvm.new_zealand.source_tree as st


# --- Reference implementation: the historical full-raw-concat + one normalize. --

_EXCLUDE = st._TEXT_EXCLUDE_TAGS


def _ref_collect_raw(node: etree._Element, *, is_root: bool) -> str:
    """Historical raw ordered concatenation (pre-normalize), verbatim semantics."""

    if not isinstance(node.tag, str):
        return ""
    if st._localname_of_tag(node.tag) in _EXCLUDE:
        return ""
    if len(node) == 0:
        return "" if is_root else (node.text or "")
    texts: list[str] = []
    if not is_root and node.text:
        texts.append(node.text)
    for child in node:
        if not isinstance(child.tag, str):
            continue
        if st._localname_of_tag(child.tag) in _EXCLUDE:
            continue
        child_text = (
            child.text or ""
            if len(child) == 0
            else _ref_collect_raw(child, is_root=False)
        )
        if child_text:
            texts.append(child_text)
        if child.tail:
            texts.append(child.tail)
    return " ".join(texts)


def _ref_legal_text(node: etree._Element) -> str:
    if isinstance(node.tag, str) and st._localname_of_tag(node.tag) == "def-para":
        texts: list[str] = []
        for child in st._defpara_owned_children(node):
            if not isinstance(child.tag, str):
                continue
            if st._localname_of_tag(child.tag) in _EXCLUDE:
                continue
            text = _ref_collect_raw(child, is_root=False)
            if text:
                texts.append(text)
            if child.tail:
                texts.append(child.tail)
        return st._normalize_text(" ".join(texts))
    return st._normalize_text(_ref_collect_raw(node, is_root=True))


# --- Composition identity (the abstract rule, provider-agnostic). --------------


def _norm(s: str) -> str:
    return " ".join(s.split())


def test_space_join_composition_identity() -> None:
    """``norm(" ".join(ps)) == " ".join(filter(norm))`` — no boundary merge."""

    ws = [" ", "\t", "\n", "\r", "\f", "\v", "\xa0", " ", "　", ""]
    toks = ["a", "bb", "c", "", "x y", "  ", "z\tw"]
    rng = random.Random(20260703)
    for _ in range(200_000):
        pieces = []
        for _ in range(rng.randint(0, 6)):
            n = rng.randint(0, 4)
            body = rng.choice(ws).join(rng.choice(toks) for _ in range(n))
            pieces.append(rng.choice(ws) + body + rng.choice(ws))
        raw = " ".join(pieces)
        lhs = _norm(raw)
        rhs = " ".join(x for x in (_norm(p) for p in pieces) if x)
        assert lhs == rhs, (pieces, lhs, rhs)


# --- Random-tree fuzz: every node byte-identical to the reference. -------------

_TEXTS = [
    "",
    "foo",
    "foo ",
    " foo",
    "  foo  bar  ",
    "\tfoo\nbar\t",
    "foo\xa0bar",
    " leading",
    "trailing　",
    "a b c",
    "x",
]
_TAGS = ["prov", "para", "subprov", "text", "label", "extref", "citation", "def-para"]
_EXCLUDE_TAGS = sorted(_EXCLUDE)


def _rand_tree(rng: random.Random, depth: int) -> etree._Element:
    tag = rng.choice(_TAGS)
    el = etree.Element(tag)
    el.text = rng.choice(_TEXTS)
    if depth > 0:
        for _ in range(rng.randint(0, 4)):
            if rng.random() < 0.18:
                # Excluded subtree (notes/history/etc.) with its own text+tail.
                child = etree.SubElement(el, rng.choice(_EXCLUDE_TAGS))
                child.text = rng.choice(_TEXTS)
                etree.SubElement(child, "para").text = rng.choice(_TEXTS)
            elif rng.random() < 0.10:
                # Comment node (non-string tag) — must contribute nothing.
                el.append(etree.Comment("x"))
                continue
            else:
                child = _rand_tree(rng, depth - 1)
                el.append(child)
            child.tail = rng.choice(_TEXTS)
    return el


def _all_nodes(el: etree._Element):
    yield el
    for child in el:
        if isinstance(child.tag, str):
            yield from _all_nodes(child)


def test_fuzz_every_node_byte_identical() -> None:
    rng = random.Random(4041)
    for _ in range(4000):
        tree = _rand_tree(rng, depth=rng.randint(1, 5))
        # Shared cache across the whole tree, mirroring production usage, so the
        # cache fast-paths (root/non-root, def-para child recursion) are exercised.
        cache: dict = {}
        for node in _all_nodes(tree):
            got = st._legal_text(node, cache=cache)
            want = _ref_legal_text(node)
            assert got == want, (etree.tostring(node), repr(got), repr(want))
            # collect (is_root=False and True) must also match the reference raw
            # after normalization.
            for is_root in (False, True):
                g = st._collect_legal_text(node, is_root=is_root, cache=cache)
                w = st._normalize_text(_ref_collect_raw(node, is_root=is_root))
                assert g == w, (is_root, etree.tostring(node), repr(g), repr(w))


def test_adversarial_mid_token_boundary() -> None:
    """Empty tails / no inter-piece whitespace must NOT merge boundary tokens.

    The historical ``" ".join(texts)`` always inserted a separator space, so
    ``foo``|``bar`` (child text ``foo`` with empty tail, next child ``bar``) stays
    ``foo bar`` — never ``foobar``. Verify the composition preserves that.
    """

    root = etree.fromstring("<prov><para>foo</para><para>bar</para></prov>")
    root[0].tail = ""  # empty tail between the two adjacent leaf-bearing paras
    got = st._legal_text(root)
    want = _ref_legal_text(root)
    assert got == want
    assert got == "foo bar", got
    assert "foobar" not in got
