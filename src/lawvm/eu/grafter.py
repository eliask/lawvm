"""EU Regulation → LawVM IR adapter.

Parses official EU legal manifestaciones (primarily FMX4 XML) into
canonical LawVM IRNode trees.

Supports Article-level granularity, recitals, and annexes.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import List, Optional, cast

from lawvm.core.archive_safety import safe_zip_read
from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind

# ---------------------------------------------------------------------------
# EU Parsing Helpers
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalize whitespace and strip noise."""
    if not text:
        return ""
    return " ".join(text.split())


def _element_text(el: ET.Element[str] | None) -> str:
    """Collect all inner text recursively."""
    if el is None:
        return ""
    return _normalize_text("".join(str(_t) for _t in el.itertext()))


def _get_kind(tag: str) -> str:
    """Map EU FMX4 tags to LawVM IR kinds."""
    t = tag.upper()
    if t == "ARTICLE":
        return "section"
    if t == "PARAG":
        return "paragraph"
    if t == "SUBPARAG":
        return "subparagraph"
    if t == "DIVISION":
        return "division"
    if t == "CHAPTER":
        return "chapter"
    if t == "ANNEX":
        return "annex"
    if t == "CONSID":
        return "recital"
    if t == "P":
        return "p"
    if t == "LIST":
        return "list"
    if t == "ITEM":
        return "item"
    return t.lower()


def _point_items(el: ET.Element[str]) -> list[tuple[str, str]]:
    """The ``(label, text)`` of every list-point directly under ``el``'s LISTs.

    A real FMX4 point is ``<LIST><ITEM><NP><NO.P>(a)</NO.P><TXT>…</TXT></NP>``.
    The point label is the ``NO.P`` marker head ("(a)" → "a", "1." → "1"); the
    point text is the whole NP rendered GRAFTER-COMMENSURABLY (marker excluded,
    itertext joined by single spaces — mirroring the amendment payload
    extractor). Only the TOPMOST list level is lowered to coordinates here (a
    nested sub-list stays inside the point's own text), matching the article-only
    resolution surface. ``el`` may be a ``<LIST>`` itself or a block (``<P>`` /
    ``<ALINEA>``) that CONTAINS a direct-child ``<LIST>``.
    """
    lists: list[ET.Element[str]]
    if el.tag == "LIST":
        lists = [el]
    else:
        lists = [c for c in el if c.tag == "LIST"]
    out: list[tuple[str, str]] = []
    for lst in lists:
        for item in lst.findall("ITEM"):
            np = item.find("NP")
            if np is None:
                continue
            no = np.find("NO.P")
            marker = _normalize_text("".join(no.itertext())) if no is not None else ""
            m = re.match(r"^\(?([0-9]{1,3}[a-z]{0,2}|[a-z]{1,3}|[ivxlcdm]{1,6})\)?[).]?", marker, re.IGNORECASE)  # lawvm-regex: witness_only reads the list point's own NO.P marker for the point coordinate, not a semantic recognizer over statute text
            label = m.group(1) if m else ""
            # Render the point text: the marker STAYS in the flattened text (the
            # grafter renders it inline, itertext-order), so include NO.P.
            text = _element_text(np)
            if not label:
                continue
            out.append((label, text))
    return out


def _intro_text(el: ET.Element[str]) -> str:
    """Text of ``el`` BEFORE its first ``<LIST>`` (a point-list's lead-in prose)."""
    parts: list[str] = []
    if el.text and el.text.strip():
        parts.append(el.text.strip())
    for child in el:
        if child.tag == "LIST":
            break
        parts.append(_element_text(child))
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return _normalize_text(" ".join(p for p in parts if p))


def _trailing_text(el: ET.Element[str]) -> str:
    """Text of ``el`` AFTER its last ``<LIST>`` (post-list wrap-up prose)."""
    children = list(el)
    last_list = -1
    for i, child in enumerate(children):
        if child.tag == "LIST":
            last_list = i
    if last_list < 0:
        return ""
    parts: list[str] = []
    tail = children[last_list].tail
    if tail and tail.strip():
        parts.append(tail.strip())
    for child in children[last_list + 1:]:
        parts.append(_element_text(child))
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return _normalize_text(" ".join(p for p in parts if p))


# ---------------------------------------------------------------------------
# Core Parser
# ---------------------------------------------------------------------------


class EUIRGrafter:
    """Stateful parser for EU Regulations."""

    def __init__(self, celex: Optional[str] = None):
        self.celex = celex

    def parse_fmx4(self, xml_path: Path) -> IRStatute:
        """Parse an FMX4 XML or ZIP file into an IRStatute."""
        if zipfile.is_zipfile(xml_path):
            with zipfile.ZipFile(xml_path) as zf:
                # Find the main XML (e.g., 01000101.xml or similar)
                names = zf.namelist()
                xml_names = [n for n in names if n.endswith(".xml") and ".doc." not in n and ".toc." not in n]

                # Pattern-based first
                act_name = next((name for name in xml_names if "01000101" in name or "000101" in name), "")
                if not act_name and xml_names:
                    # Fallback: largest XML file
                    act_name = max(xml_names, key=lambda n: zf.getinfo(n).file_size)

                if not act_name:
                    raise ValueError(f"No main FMX4 XML found in ZIP {xml_path}")
                # Wave 3 decompression-bomb cap (Security M1): safe_zip_read
                # checks the declared uncompressed size against
                # $LAWVM_MAX_ARCHIVE_MEMBER_BYTES BEFORE materialising. A
                # malicious FMX4 zip declaring a huge main.xml would OOM the
                # process. There is no skip accumulator in this one-shot parse
                # (the grafter returns a single IRStatute), so a too-large
                # member is fail-loud: ArchiveMemberTooLarge carries the typed
                # (archive_path, member_name, declared_size, cap_bytes)
                # receipt (AGENTS.md §1.8/§1.10) and surfaces to the caller's
                # CompileAdjudication / acquisition-failure path. Mirrors
                # src/lawvm/eu/cellar.py:611 extract_fmx4_structure.
                data = safe_zip_read(zf, act_name, archive_path=str(xml_path))
                root = ET.fromstring(data)
        else:
            tree = ET.parse(xml_path)
            root = tree.getroot()

        if root.tag != "ACT":
            # Some manifestations wrap ACT in a envelope
            act = root.find(".//ACT")
            if act is not None:
                root = act
            else:
                # Consolidated (sector-0) manifestations are rooted at
                # <CONS.ACT> with a <CONS.DOC> body that carries the SAME
                # ENACTING.TERMS / ARTICLE / TITLE / ANNEX structure as an ACT
                # (verified live against 02016R0044-20160401: CONS.ACT > CONS.DOC
                # with 26 ENACTING.TERMS ARTICLEs). The consolidated byte lane was
                # 5xx during Increment 1 so this shape was never reachable; with
                # REST recovered, treating CONS.DOC as the ACT-equivalent root
                # unblocks the live replay-vs-consolidation oracle diff. This is
                # additive (a NEW root branch; the existing ACT path is unchanged).
                cons_doc = root.find(".//CONS.DOC")
                if cons_doc is not None:
                    root = cons_doc
                else:
                    raise ValueError(
                        f"Expected ACT/CONS.DOC root or descendant, got {root.tag}"
                    )

        title_el = root.find("TITLE")
        title = _element_text(title_el)

        # 1. Preamble (Recitals)
        body_nodes: List[IRNode] = []
        preamble = root.find("PREAMBLE")
        if preamble is not None:
            recitals = self._parse_recitals(preamble)
            if recitals:
                body_nodes.append(recitals)

        # 2. Enacting Terms (Articles/Chapters)
        enacting = root.find("ENACTING.TERMS")
        if enacting is not None:
            for child in enacting:
                node = self._parse_structural_node(child)
                if node:
                    body_nodes.append(node)

        # 3. Final (Signature/Closing)
        final = root.find("FINAL")
        if final is not None:
            # Often contains entry into force clauses
            final_node = IRNode(kind=IRNodeKind.FINAL, text=_element_text(final))
            body_nodes.append(final_node)

        # 4. Annexes
        supplements: List[IRNode] = []
        for annex in root.findall("ANNEX"):
            node = self._parse_structural_node(annex)
            if node:
                supplements.append(node)

        body_root = IRNode(kind=IRNodeKind.BODY, children=tuple(body_nodes))

        metadata = {
            "celex": self.celex,
            "source": "fmx4",
            "path": str(xml_path),
        }

        return IRStatute(
            statute_id=self.celex or xml_path.stem,
            title=title,
            body=body_root,
            supplements=supplements,
            metadata=metadata,
        )

    def _parse_recitals(self, el: ET.Element[str]) -> Optional[IRNode]:
        """Parse the preamble into a container of recitals."""
        children = []
        for consid in el.findall(".//CONSID"):
            text = _element_text(consid)
            # Try to extract the number from (1), (2) etc.
            num_match = re.match(r"^\((\d+)\)", text)
            label = num_match.group(1) if num_match else None
            children.append(IRNode(kind=IRNodeKind.RECITAL, label=label, text=text))

        if not children:
            return None
        return IRNode(kind=IRNodeKind.PREAMBLE, children=tuple(children))

    @staticmethod
    def _annex_label(el: ET.Element[str]) -> Optional[str]:
        """Extract the annex coordinate (e.g. ``II``, ``III``, ``1``) from an
        FMX4 ``<ANNEX>`` element so ``annex:N`` ops resolve against it.

        Preference order (real CELLAR annex shapes):
          1. the ``IDENTIFIER`` attribute (often the bare coordinate ``II``);
          2. the ``NNC`` / ``NUMERO`` attribute;
          3. the title text after "ANNEX" (``<TI>ANNEX II</TI>`` /
             ``<TI.DOC>ANNEX II</TI.DOC>``).

        Returns ``None`` for a sole-annex with no coordinate (the ``annex:``
        empty-label form, which the apply seam skips as a typed target-not-found
        — resolving it to the base's single annex is deferred grammar work).
        """
        for attr in ("IDENTIFIER", "NNC", "NUMERO"):
            raw = el.attrib.get(attr)
            if raw:
                coord = _normalize_text(raw).strip()
                # Strip a leading "ANNEX" if the identifier carries the word.
                m = re.match(r"^(?:ANNEX\s+)?([A-Za-z0-9]+)$", coord, re.IGNORECASE)
                if m and m.group(1).upper() != "ANNEX":
                    return m.group(1)
        # Fall back to the title text: "ANNEX II" → "II". Real FMX4 wraps the
        # title as ``<TITLE><TI>ANNEX II</TI></TITLE>`` (or a bare ``<TI.DOC>``),
        # so read the annex's TITLE / direct TI / TI.DOC — NOT a deep descendant
        # walk (which could pick up an "ANNEX" mention in the annex BODY text).
        for ti_el in (el.find("TITLE"), el.find("TI"), el.find("TI.DOC")):
            if ti_el is None:
                continue
            title = _normalize_text(_element_text(ti_el))
            m = re.search(r"\bANNEX(?:E|ES)?\s+([IVXLCDM]+|\d+|[A-Z])\b", title, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _parag_label(el: ET.Element[str], eid: Optional[str]) -> Optional[str]:
        """Resolve a ``<PARAG>``'s coordinate (the ``paragraph:M`` label).

        Preference order (real CELLAR paragraph shapes):
          1. the ``<NO.PARAG>`` marker head — "1.", "2.", "5a." → "1"/"2"/"5a";
          2. the legacy inline "N." shape at the start of the first ``<P>``;
          3. the trailing dotted segment of ``IDENTIFIER`` ("001.005" → "5").
        """
        no = el.find("NO.PARAG")
        if no is not None:
            txt = _normalize_text("".join(no.itertext()))
            m = re.match(r"^\(?(\d{1,3}[a-z]{0,2})[).]?", txt)  # lawvm-regex: witness_only reads the PARAG's own NO.PARAG marker for the paragraph coordinate, not a semantic recognizer over statute text
            if m:
                return m.group(1)
        first_p = el.find("P")
        if first_p is not None and first_p.text:
            m = re.match(r"^(\d+)\.", _normalize_text(first_p.text))  # lawvm-regex: witness_only reads the legacy inline "N." paragraph marker for the coordinate, not a semantic recognizer over statute text
            if m:
                return m.group(1)
        if eid and "." in eid:
            tail = eid.rsplit(".", 1)[-1].lstrip("0") or eid.rsplit(".", 1)[-1]
            if re.match(r"^\d{1,3}[a-z]{0,2}$", tail):  # lawvm-regex: witness_only validates the IDENTIFIER's trailing coordinate segment shape, not a semantic recognizer over statute text
                return tail
        return None

    def _parse_paragraph_body(
        self, el: ET.Element[str], label: Optional[str], eid: Optional[str]
    ) -> IRNode:
        """Lower a ``<PARAG>`` into a paragraph node with resolvable coordinates.

        The paragraph body is a sequence of block units: intro ``<P>`` prose,
        list-point items (``<LIST><ITEM><NP><NO.P>(a)</NO.P><TXT>…</TXT></NP>``)
        and — when a PARAG carries more than one prose block — successive
        SUBPARAGRAPHs (each ``<ALINEA>``/``<P>`` a 1-based ordinal). The pre-fix
        grafter collapsed the whole body to one flat ``_element_text`` string, so
        ``point`` / ``subparagraph`` amendment coordinates (``article:N/
        paragraph:M/point:b``, ``…/subparagraph:2``) had no node to resolve
        against. This lowers those coordinates into child ``item`` (point) /
        ``subparagraph`` nodes carrying their own text, so ``tree_ops.find``
        (which matches the target's LAST path step scoped to the article) locates
        them and the sub-article REPLACE/REPEAL applies.

        Text stays GRAFTER-COMMENSURABLE with the amendment payload extractor
        (``fmx4_amendment_grammar._quoted_struct_payload_text``): block units are
        rendered by joining stripped ``itertext`` parts with single spaces, and
        the same rendering runs on BOTH the replay base and the oracle
        consolidation (both grafted here), so any spacing normalisation is
        symmetric across the compare surface.

        DOCUMENT ORDER is load-bearing: the flattened article text
        (``eu_oracle_divergence._node_text`` = own ``text`` THEN each descendant's
        ``text``, depth-first in child order) is compared per-article. To keep the
        flatten byte-commensurable with the pre-fix flat rendering, the paragraph
        carries NO own ``text`` — EVERY body unit becomes a child in SOURCE order:
        intro/wrap-up prose → a plain ``p`` child, each list point → an ``item``
        (point) child, and — when the PARAG has more than one plain prose block —
        each such block → a 1-based ``subparagraph`` child. That way a point /
        subparagraph amendment coordinate resolves against a real node while the
        depth-first flatten still yields the units in document order.
        """
        children: List[IRNode] = []
        # Count plain prose blocks (ALINEA/P with no point-list) up front: a PARAG
        # whose body is a single prose block is NOT sub-divided (the common case);
        # only a multi-prose-block paragraph mints ``subparagraph`` coordinates.
        prose_blocks = [
            c for c in el if c.tag in ("ALINEA", "P") and not _point_items(c)
        ]
        multi_block = len(prose_blocks) > 1
        subpara_ord = 0

        def _emit_prose(text: str) -> None:
            if text:
                children.append(IRNode(kind=cast(IRNodeKind, "p"), text=text))

        def _emit_points(block: ET.Element[str]) -> None:
            for pt_label, pt_text in _point_items(block):
                children.append(
                    IRNode(kind=cast(IRNodeKind, "item"), label=pt_label, text=pt_text)
                )

        for child in el:
            if child.tag == "NO.PARAG":
                continue
            if child.tag in ("ALINEA", "P"):
                if _point_items(child):
                    # Lead-in prose, then the points, then any wrap-up prose — all
                    # as children in document order.
                    _emit_prose(_intro_text(child))
                    _emit_points(child)
                    _emit_prose(_trailing_text(child))
                    continue
                block_text = _element_text(child)
                if not block_text:
                    continue
                if multi_block:
                    subpara_ord += 1
                    children.append(
                        IRNode(
                            kind=cast(IRNodeKind, "subparagraph"),
                            label=str(subpara_ord),
                            text=block_text,
                        )
                    )
                else:
                    _emit_prose(block_text)
            elif child.tag == "LIST":
                if _point_items(child):
                    _emit_points(child)
                else:
                    _emit_prose(_element_text(child))

        attrs = {}
        if eid:
            attrs["eId"] = eid
        return IRNode(
            kind=cast(IRNodeKind, "paragraph"),
            label=label,
            text="",
            children=tuple(children),
            attrs=attrs,
        )

    def _parse_structural_node(self, el: ET.Element[str], parent_eid: str = "") -> Optional[IRNode]:
        """Recursively parse articles, chapters, divisions, annexes."""
        tag = el.tag
        kind = _get_kind(tag)

        # EU EIDs are often explicitly in the IDENTIFIER attribute
        eid = el.attrib.get("IDENTIFIER")
        # If not, we'll try to synthesize one
        label = None

        # Extract Label/Title
        if kind == "section":
            label_el = el.find("TI.ART")
            if label_el is not None:
                label = _normalize_text(label_el.text or "").replace("Article", "").strip()
        elif kind == "paragraph":
            # Real FMX4 numbers a <PARAG> with a <NO.PARAG> marker ("1.", "2.",
            # "5a." …) — the paragraph number lives in that marker, NOT inline in
            # the first <P>'s text. The pre-fix grafter only read the inline
            # "N." shape, so every real <PARAG> (NO.PARAG form) got ``label=None``
            # and an amendment op targeting ``article:N/paragraph:M`` resolved to
            # a labelless node — ``tree_ops.find(kind='paragraph', label='M')``
            # missed, and the whole sub-article edit typed-skipped
            # (``eu_replay_target_not_found``). Read the NO.PARAG marker first
            # (the coordinate is its numeric/letter-suffix head), then fall back
            # to the inline "N." shape, then to the IDENTIFIER's trailing segment
            # ("001.005" → "5"). This is the NO.PARAG sub-article coordinate that
            # 32010R1093's closure amenders (32013R1022 …) address.
            label = self._parag_label(el, eid)
            return self._parse_paragraph_body(el, label, eid)
        elif kind == "annex":
            # An ``annex:N`` op (``fmx4_amendment_grammar``) targets the base
            # annex by its coordinate — the Roman/arabic numeral after "ANNEX"
            # in the title (``<TI>ANNEX II</TI>``) or the ``IDENTIFIER`` attr.
            # Without a label the annex node cannot be resolved by
            # ``tree_ops.find(kind='annex', label='II')`` even once it is found
            # in ``supplements`` (the annex-in-supplements resolution seam), so
            # the coordinate must be lifted onto ``label`` here.
            label = self._annex_label(el)

        # Children
        children = []
        text_parts = []

        # Generic child walk
        for child in el:
            ckind = _get_kind(child.tag)
            if ckind in ("section", "paragraph", "subparagraph", "division", "chapter", "item"):
                cnode = self._parse_structural_node(child, eid or parent_eid)
                if cnode:
                    children.append(cnode)
            elif child.tag in ("P", "LIST", "ALINEA"):
                # Mixed content container. ALINEA is the real FMX4 paragraph-body
                # wrapper: a <PARAG> holds <NO.PARAG> + <ALINEA>, and the ALINEA
                # carries the operative text either DIRECTLY or via nested <P>/<LIST>
                # (verified against real CELLAR bytes, 32016R0044 Articles 2/3).
                # Without ALINEA here the grafter DROPPED all PARAG>ALINEA article
                # text — the Increment-1 "text preserved on the nested child"
                # residual held only for the PARAG>P fixture shape, NOT real bytes.
                # _element_text recurses itertext, so both ALINEA shapes are covered.
                text_parts.append(_element_text(child))

        text = " ".join(text_parts).strip()

        attrs = {}
        if eid:
            attrs["eId"] = eid

        return IRNode(kind=cast(IRNodeKind, kind), label=label, text=text, children=tuple(children), attrs=attrs)

    def parse_xhtml(self, xhtml_path: Path) -> IRStatute:
        """Basic XHTML parser for EU manifestations."""
        # This is a heuristic parser for EU OJ XHTML
        # EU Articles in XHTML often look like: <p class="oj-article">Article 1</p>
        # or have specific ID patterns like 'art1'.

        # For now, we'll use a simplified version that looks for 'Article' text
        from lxml import html
        from lxml.html import HtmlElement

        content = xhtml_path.read_bytes()
        tree = html.fromstring(content)

        title_nodes = cast(list[str], tree.xpath("//title/text()"))
        title = _normalize_text(title_nodes[0] if title_nodes else xhtml_path.stem)

        body_nodes = []
        # Find all Article-like structures
        # A common pattern is <p class="article">... or <div id="art_1">
        # Let's look for any element containing "Article X" at the start
        for el in cast(list[HtmlElement], tree.xpath("//p[contains(@class, 'oj-ti-art')]")):
            text = _normalize_text(el.text_content())
            m = re.match(r"^Article\s+(\d+)", text)
            if m:
                label = m.group(1)
                # Find following content until next article
                article_content = []
                # getnext() stubs return Optional[_Element]; the lxml HTML parser
                # actually returns HtmlElement subclasses, but we only need tag +
                # text_content() which are available on all lxml elements.
                sibling = cast(Optional[HtmlElement], el.getnext())
                while sibling is not None and not (
                    sibling.tag == "p" and _normalize_text(sibling.text_content()).startswith("Article ")
                ):
                    article_content.append(_normalize_text(sibling.text_content()))
                    sibling = cast(Optional[HtmlElement], sibling.getnext())

                body_nodes.append(IRNode(kind=IRNodeKind.SECTION, label=label, text=" ".join(article_content), children=()))

        body_root = IRNode(kind=IRNodeKind.BODY, children=tuple(body_nodes))

        return IRStatute(
            statute_id=self.celex or xhtml_path.stem,
            title=title,
            body=body_root,
            supplements=[],
            metadata={"source": "xhtml", "celex": self.celex},
        )


def parse_eu_regulation_ir(path: Path, celex: Optional[str] = None) -> IRStatute:
    """Convenience wrapper for EUIRGrafter. Supports XML and XHTML."""
    grafter = EUIRGrafter(celex=celex)
    if path.suffix.lower() in (".xhtml", ".html"):
        return grafter.parse_xhtml(path)
    return grafter.parse_fmx4(path)
