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


def _element_text(el: ET.Element | None) -> str:
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
                data = zf.read(act_name)
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
                raise ValueError(f"Expected ACT root or descendant, got {root.tag}")

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

    def _parse_recitals(self, el: ET.Element) -> Optional[IRNode]:
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

    def _parse_structural_node(self, el: ET.Element, parent_eid: str = "") -> Optional[IRNode]:
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
            first_p = el.find("P")
            if first_p is not None and first_p.text:
                m = re.match(r"^(\d+)\.", _normalize_text(first_p.text))
                if m:
                    label = m.group(1)

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
            elif child.tag in ("P", "LIST"):
                # Mixed content container
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
                sibling: Optional[HtmlElement] = el.getnext()  # type: ignore[assignment]
                while sibling is not None and not (
                    sibling.tag == "p" and _normalize_text(sibling.text_content()).startswith("Article ")
                ):
                    article_content.append(_normalize_text(sibling.text_content()))
                    sibling = sibling.getnext()  # type: ignore[assignment]

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
