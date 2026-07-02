"""HTML-manifestation source parsing for New Zealand legislation.

The NZ acquisition layer stores an HTML rendition of a version whenever no XML
manifestation is available (``_fetch_html_fallback`` in ``acquisition.py``, under
``series_key nzleg://version/{version_id}/format/html`` with the ``www`` URL as
its locator). Those HTML blobs — ~12k scan-only public/local/private Acts whose
XML 404s — were unreplayable because the consume side (``source_tree.py``) was
XML-only.

This module lifts a stored ``legislation.govt.nz`` HTML manifestation into the
**same** format-agnostic ``NZSourceDocument`` IR that ``parse_nz_source_document``
produces from XML, so every downstream replay / self-consistency / oracle
consumer works unchanged on HTML-sourced Acts.

Design: rather than reimplement the audited XML node/text/label logic, the HTML
is transformed into an ``lxml.etree`` element tree whose element *localnames*
mirror the PCO XML vocabulary (``prov``, ``prov.body``, ``subprov``,
``label-para``, ``def-para``, ``para``, ``text``, ``label``, ``heading``,
``schedule``, ``part``, ``body`` …). The resulting tree is then handed to the
existing ``_parse_nz_source_document_uncached`` walker, so the extracted IR is
produced by byte-identical structural/text logic — HTML and XML converge on one
walker. This is the correctness crux: the HTML lowering is a *vocabulary
translation*, not a second parser.

The legislation.govt.nz HTML template encodes the XML structure as ``class``
tokens on ``<div>``/``<h*>``/``<p>`` elements:

* ``<div class="prov" id="LMSxxx">`` → ``<prov id="LMSxxx">``
* ``<h5 class="prov"><span class="label">3</span> Heading text.</h5>`` → the
  ``<label>`` (bare) plus ``<heading>`` (the tail text) of the enclosing prov.
* ``<div class="prov-body">`` → ``<prov.body>``
* ``<div class="subprov"><p class="subprov"><span class="label">(1)</span></p>…``
  → ``<subprov><label>1</label>…`` — the rendered parentheses on a sub-item
  label are a display artifact and are stripped so the label matches the XML's
  bare ``1``.
* ``<div class="def-para">`` has **no** ``def-term`` element in HTML; the defined
  term is the leading curly-quoted (``“…”``) span of the first ``p.text``. It is
  synthesized into a ``<def-term>`` so the def-para addressing matches XML.
* ``<div class="para">`` / ``<p class="text">`` → ``<para>`` / ``<text>``.
* ``<div class="schedule">``, ``<div class="part">``, ``<div class="body">``,
  ``<div class="schedule-group">`` → the same structural localnames.

Residual lossiness (documented, never faked):

* Provision-level amendment ``history-note`` witnesses are absent from these
  scan-only HTML Acts (the PCO amendment-annotation format predates them), so
  ``NZSourceNode.history`` is empty. This is a genuine property of the source,
  not a dropped extraction.
* ``<amend.in>`` typed amend instructions are not present in scan-only HTML, so
  HTML-sourced Acts are replay *targets*, not amending sources.
"""

from __future__ import annotations

import re
from typing import cast

from lxml import etree
from lxml import html as lxml_html
from lxml.html import HtmlElement

from lawvm.new_zealand.source_tree import (
    NZSourceDocument,
    _parse_nz_source_document_uncached,
)


def _html_elements(nodes: object) -> list[HtmlElement]:
    """Cast an ``xpath`` result to the ``HtmlElement`` list it always is here.

    Every ``xpath`` in this module selects element nodes (``//div``, ``//span``,
    ``//meta`` …), never attribute/text/smart-string results, so the broad union
    ``xpath`` is statically typed to return is, at these sites, a list of
    ``HtmlElement``. The cast documents that invariant in one place.
    """
    return cast("list[HtmlElement]", nodes)


# HTML ``class`` tokens that name a structural PCO kind, mapped to the XML
# localname the walker recognizes. ``prov.body``/``quote.in`` carry a dot in XML
# but a dash in the HTML class token, so the mapping is explicit rather than a
# bare identity. Only tokens in this map become structural container elements;
# every other class is treated as inline flow markup (its text folds into the
# enclosing node's legal text exactly as a non-structural XML element does).
_HTML_CLASS_TO_XML_LOCALNAME: dict[str, str] = {
    "act": "act",
    "body": "body",
    "front": "front",
    "part": "part",
    "subpart": "subpart",
    "prov": "prov",
    "prov-body": "prov.body",
    "subprov": "subprov",
    "label-para": "label-para",
    "def-para": "def-para",
    "para": "para",
    "text": "text",
    "label": "label",
    "heading": "heading",
    "schedule": "schedule",
    "schedule-group": "schedule.group",
    "schedule-misc": "schedule-misc",
    "amend": "amend",
    "quote-in": "quote.in",
    "def-term": "def-term",
}

# Heading tags that, in the HTML template, carry a structural kind's label +
# heading (e.g. ``<h5 class="prov">``). Their ``span.label`` becomes the
# enclosing structural element's ``<label>`` and the trailing text its
# ``<heading>``; the ``h*`` wrapper itself contributes no separate node.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# The document body of the legislation.govt.nz page.
_LEGISLATION_CONTAINER_ID = "legislation"

# Leading curly-quoted defined term in a def-para's first text run:
# ``“Board” means …`` → ``Board``. Straight quotes are accepted as a fallback.
_DEF_TERM_LEADING_QUOTE = re.compile(r'^\s*[“"]([^”"]+)[”"]')

# A rendered sub-item label wrapped in parentheses (``(1)`` / ``(a)`` /
# ``(iv)``): the parens are a display artifact of the HTML rendition; the XML
# label is bare. Only a fully-parenthesized token is unwrapped so an intentional
# label like ``1(a)`` is left untouched.
_PARENTHESIZED_LABEL = re.compile(r"^\(([^()]+)\)$")

# A schedule label rendered with the leading ``Schedule`` word
# (``Schedule 2`` → ``2``; a bare ``Schedule`` → empty). Older Acts render
# word-labels (``FIRST SCHEDULE``) which are left intact (that IS the XML label).
_SCHEDULE_WORD_LABEL = re.compile(r"^Schedule\s+([0-9]+[A-Za-z]*)$", re.IGNORECASE)
_SCHEDULE_BARE_WORD = re.compile(r"^Schedule$", re.IGNORECASE)

# legislation.govt.nz serves UTF-8 (the ``“…”`` def-term quotes, em dashes, and
# macronated Māori text depend on it). The stored blobs declare
# ``<meta charset="UTF-8">``, but a blob without the declaration would be decoded
# as the lxml default (Latin-1-ish), corrupting the curly quotes the def-term
# extraction keys on. Pinning the parser encoding to UTF-8 makes the lowering
# encoding-independent.
_HTML_PARSER = lxml_html.HTMLParser(encoding="utf-8")

_LABEL_CLASS_XPATH = (
    './/span[contains(concat(" ", normalize-space(@class), " "), " label ")]'
)
_TEXT_CLASS_XPATH = (
    './/p[contains(concat(" ", normalize-space(@class), " "), " text ")]'
)


class NZHtmlSourceError(ValueError):
    """Raised when an HTML manifestation exposes no parseable legislation body."""


def parse_nz_html_source_document(
    html_bytes: bytes,
    *,
    html_locator: str = "",
    version_id: str = "",
) -> NZSourceDocument:
    """Parse a stored NZ HTML manifestation into the XML-equivalent source IR.

    Pure: ``bytes -> frozen NZSourceDocument``. The returned document is built by
    the same walker the XML path uses, so it is replay-equivalent to an
    XML-sourced document (modulo the documented HTML residuals). ``html_locator``
    is recorded as the document's ``xml_locator`` (the source-locator identity)
    so downstream witness attribution and the corpus cache key are stable.
    """
    xml_root = _html_to_xml_localname_tree(html_bytes)
    xml_bytes = etree.tostring(xml_root, encoding="utf-8")
    return _parse_nz_source_document_uncached(
        xml_bytes,
        xml_locator=html_locator,
        version_id=version_id,
    )


def _html_to_xml_localname_tree(html_bytes: bytes) -> etree._Element:
    """Transform a legislation.govt.nz HTML page into an XML-localname tree.

    The returned ``<act>`` root carries the page's title/iid metadata and one
    lowered structural subtree per ``div#legislation`` child, so the XML walker
    sees the same vocabulary it reads from a PCO XML act.
    """
    doc = lxml_html.fromstring(html_bytes, parser=_HTML_PARSER)
    container = _find_legislation_container(doc)
    if container is None:
        raise NZHtmlSourceError("HTML manifestation exposes no div#legislation body")

    act = etree.Element("act")
    _apply_document_metadata(doc, act)
    title = _page_title(doc)
    if title:
        title_el = etree.SubElement(act, "title")
        title_el.text = title

    for child in container:
        if isinstance(child.tag, str):
            _lower_element(child, act)
    return act


def _find_legislation_container(doc: HtmlElement) -> HtmlElement | None:
    matches = _html_elements(doc.xpath(f'//*[@id="{_LEGISLATION_CONTAINER_ID}"]'))
    if matches:
        return matches[0]
    # The ``act`` div is the fallback anchor when the page id is absent.
    act_matches = _html_elements(doc.xpath('//div[@class="act"]'))
    return act_matches[0] if act_matches else None


def _apply_document_metadata(doc: HtmlElement, act: etree._Element) -> None:
    """Copy the identifying HTML ``<meta>`` fields onto the synthetic act root.

    Only the fields the XML metadata surfaces (the internal id) are lifted; the
    HTML page title becomes the ``<title>`` element below (matching the XML
    ``_document_metadata`` title extraction), not a root attribute.
    """
    iid = _meta_content(doc, "iid")
    if iid:
        act.set("id", iid)


def _meta_content(doc: HtmlElement, name: str) -> str:
    for meta in _html_elements(doc.xpath(f'//meta[@name="{name}"]')):
        content = meta.get("content")
        if content and content.strip():
            return content.strip()
    return ""


def _page_title(doc: HtmlElement) -> str:
    """The Act's title from the rendered ``<h1 class="title">`` cover heading.

    Falls back to the ``<title>`` page tag with the site suffix trimmed. The XML
    metadata title is the ``<title>`` element text; the cover ``h1`` is the same
    normative string in the HTML rendition.
    """
    for h1 in _html_elements(
        doc.xpath('//h1[contains(concat(" ", normalize-space(@class), " "), " title ")]')
    ):
        text = _text_of(h1)
        if text:
            return text
    for title in _html_elements(doc.xpath("//title")):
        raw = _text_of(title)
        if raw:
            return raw.split("|")[0].strip()
    return ""


def _lower_element(html_el: HtmlElement, xml_parent: etree._Element) -> None:
    """Lower one HTML element into the XML-localname tree under ``xml_parent``.

    Structural class tokens become their XML localname element; heading tags
    contribute the enclosing structural element's ``<label>``/``<heading>``;
    everything else is a generic inline element preserving text/tail so the
    walker folds it into legal text exactly like a non-structural XML element.
    """
    tag = html_el.tag
    if not isinstance(tag, str):
        return
    tag = tag.lower()
    token = _structural_class_token(html_el)

    if token is not None and _is_label_heading_carrier(html_el, tag):
        # A label/heading carrier (``<h5 class="prov">``'s label+heading, or the
        # ``<p class="subprov">`` that holds only a sub-item's label span): its
        # label/heading belong to the CURRENT structural parent, not a fresh
        # node. Recognized by directly wrapping a ``span.label`` (the template's
        # carrier shape) so a real ``<p class="text">`` provision line is never
        # mistaken for a carrier.
        _emit_label_and_heading(html_el, xml_parent, token)
        return

    if token is not None:
        localname = _HTML_CLASS_TO_XML_LOCALNAME[token]
        xml_el = etree.SubElement(xml_parent, localname)
        _copy_id(html_el, xml_el)
        if localname == "def-para":
            _emit_def_term(html_el, xml_el)
        _lower_children(html_el, xml_el)
        return

    # Non-structural wrapper: emit a generic element that carries this node's own
    # text and its children in document order, so flow text is preserved exactly
    # as the XML walker collects it from an inline element.
    generic = etree.SubElement(xml_parent, "span")
    if html_el.text:
        generic.text = html_el.text
    _lower_children(html_el, generic)
    if html_el.tail:
        generic.tail = html_el.tail


def _lower_children(html_el: HtmlElement, xml_el: etree._Element) -> None:
    # Preserve the element's own leading text (flow text before the first child)
    # when it has not already been set (structural containers rarely carry it,
    # but a ``<p class="text">`` does).
    if html_el.text and xml_el.text is None:
        xml_el.text = html_el.text
    for child in html_el:
        if not isinstance(child.tag, str):
            continue
        _lower_element(child, xml_el)


def _emit_label_and_heading(
    heading_el: HtmlElement,
    xml_parent: etree._Element,
    token: str,
) -> None:
    """Emit ``<label>``/``<heading>`` for a structural heading tag.

    The ``span.label`` text (normalized: parens/schedule-word stripped) becomes
    ``<label>``; the trailing prose of the heading becomes ``<heading>``. Both
    are appended to the enclosing structural element so ``_direct_child_text``
    reads them exactly as it does from XML.
    """
    label_spans = _html_elements(heading_el.xpath(_LABEL_CLASS_XPATH))
    raw_label = _text_of(label_spans[0]) if label_spans else ""
    label = _normalize_label(raw_label, token)
    if label:
        label_el = etree.SubElement(xml_parent, "label")
        label_el.text = label

    heading_text = _heading_prose(heading_el, raw_label)
    if heading_text:
        heading_el_out = etree.SubElement(xml_parent, "heading")
        heading_el_out.text = heading_text


def _heading_prose(
    heading_el: HtmlElement,
    raw_label: str,
) -> str:
    """The heading prose = the heading's full text minus the leading label span."""
    full = _text_of(heading_el)
    if not full:
        return ""
    if raw_label:
        normalized_label = " ".join(raw_label.split())
        if normalized_label and full.startswith(normalized_label):
            return full[len(normalized_label):].strip()
    return full


def _emit_def_term(def_para_el: HtmlElement, xml_el: etree._Element) -> None:
    """Synthesize a ``<def-term>`` from a def-para's leading curly-quoted term.

    The HTML def-para carries no explicit ``def-term`` element (unlike XML); the
    NZ definition convention wraps the defined term in ``“…”`` at the very start
    of the first text run (``“Board” means …``). Extracting that term is a
    faithful reconstruction of the XML ``def-term``, not a guess. When no leading
    quoted term is present the def-para falls back to the walker's xml-id/ordinal
    addressing (no synthetic term emitted).
    """
    first_text = _html_elements(def_para_el.xpath(_TEXT_CLASS_XPATH))
    if not first_text:
        return
    run = _text_of(first_text[0])
    match = _DEF_TERM_LEADING_QUOTE.match(run)
    if not match:
        return
    term = match.group(1).strip()
    if not term or "/" in term or ":" in term:
        return
    # The def-term must be the first descendant of the def-para's leading text so
    # ``_first_def_term`` (first def-term in document order) reads it. Wrap it in
    # a leading ``<para><text>`` so the walker's def-para text collection still
    # sees the full definition prose after the term.
    para = etree.SubElement(xml_el, "para")
    text_el = etree.SubElement(para, "text")
    term_el = etree.SubElement(text_el, "def-term")
    term_el.text = term
    # Carry the remainder of the definition prose as the term's tail so the
    # def-para's collected legal text includes ``means …`` (matching XML).
    remainder = run[match.end():]
    if remainder:
        term_el.tail = remainder
    # Remove the now-duplicated leading text run from the HTML subtree so the
    # generic lowering below does not emit the definition prose twice.
    parent = first_text[0].getparent()
    if parent is not None:
        parent.remove(first_text[0])


def _is_label_heading_carrier(html_el: HtmlElement, tag: str) -> bool:
    """Whether a structural-token element is a label/heading carrier, not a node.

    The template renders a structural element's label (and, for a section, its
    heading) in a leading ``<h5 class="prov">`` / ``<p class="subprov">`` sibling
    that DIRECTLY wraps a ``<span class="label">``. Such a carrier contributes
    the enclosing structural element's ``<label>``/``<heading>`` — it is not a
    fresh structural node. A structural ``<div>`` (the real node) and a
    ``<p class="text">`` provision line never directly wrap a ``span.label``, so
    they are not carriers.
    """
    if tag == "div":
        # A structural ``<div>`` is the node itself, never a carrier.
        return False
    if tag in _HEADING_TAGS:
        # ``<h5 class="prov">`` etc. always render the label + heading; they hold
        # no nested provision node in this template.
        return True
    # A ``<p class="subprov">`` carrier directly wraps the label span; a real
    # ``<p class="text">`` provision line does not.
    for child in html_el:
        if isinstance(child.tag, str) and _structural_class_token(child) == "label":
            return True
    return False


def _structural_class_token(html_el: HtmlElement) -> str | None:
    """The single structural class token on an element, or ``None``.

    Returns the first class token that names a PCO structural kind. An element
    carries at most one structural token in the template; presentational tokens
    (``fontsize12``, ``align-left`` …) are ignored.
    """
    class_attr = html_el.get("class")
    if not class_attr:
        return None
    for token in class_attr.split():
        if token in _HTML_CLASS_TO_XML_LOCALNAME:
            return token
    return None


def _copy_id(html_el: HtmlElement, xml_el: etree._Element) -> None:
    node_id = html_el.get("id")
    if node_id:
        xml_el.set("id", node_id)


def _normalize_label(raw_label: str, token: str) -> str:
    """Normalize a rendered HTML label to its bare XML form.

    * sub-item labels (``subprov``/``label-para``): strip enclosing parentheses
      (``(1)`` → ``1``);
    * schedule labels: strip a leading ``Schedule`` word (``Schedule 2`` → ``2``;
      bare ``Schedule`` → empty), leaving word-style labels (``FIRST SCHEDULE``)
      intact;
    * every other kind: the label is used as-rendered (already bare in the XML).
    """
    label = " ".join(raw_label.split())
    if not label:
        return ""
    if token in {"subprov", "label-para"}:
        match = _PARENTHESIZED_LABEL.match(label)
        if match:
            return match.group(1).strip()
        return label
    if token == "schedule":
        word_match = _SCHEDULE_WORD_LABEL.match(label)
        if word_match:
            return word_match.group(1)
        if _SCHEDULE_BARE_WORD.match(label):
            return ""
        return label
    return label


def _text_of(el: HtmlElement) -> str:
    return " ".join(el.text_content().split())
