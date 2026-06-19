"""Body-XML selection for reference extraction (archive-only, no replay).

The reference surface normally lives in the consolidated oracle, so it is
preferred. But Finlex serves an *empty* consolidated body for repealed/expired
statutes — a ``<body><hcontainer name="contentAbsent"/></body>`` stub (or a
PDF-wrapper ``componentRef`` stub). That stub is non-empty bytes, so a naive
``read_oracle() or read_source()`` accepts it and the statute's entire reference
surface vanishes (~1 node instead of all of them).

This module centralizes the body-selection policy so every reference-extraction
read site falls back to the enacted source when the oracle is a content-absent
stub. It is used ONLY by the reference-extraction (legal-surface) read path; the
replay / consolidation / PIT pipeline selects its bytes elsewhere and is
deliberately untouched.
"""

from __future__ import annotations

from typing import Protocol

from lxml import etree

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


class BodyStore(Protocol):
    def read_oracle(self, sid: str) -> bytes | None: ...
    def read_source(self, sid: str) -> bytes | None: ...
    def read_amendment(self, sid: str) -> bytes | None: ...


def _localname(el: etree._Element) -> str:
    tag = el.tag
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def is_content_absent_body(xml_bytes: bytes) -> bool:
    """True when ``xml_bytes`` is an empty/stub consolidated body.

    Finlex serves these for repealed/expired (and undigitized PDF-wrapper)
    statutes: the act parses but its ``<body>`` carries no substantive
    provision content, only a ``contentAbsent`` marker hcontainer or a
    ``componentRef`` PDF stub. Such a body has no reference surface to scan.

    Detection mirrors ``he_acquisition.classify_structural_tier`` (the marker
    Finlex itself emits): a missing/empty mainBody, a single
    ``hcontainer[@name='contentAbsent']``, or any ``componentRef`` in the body.
    Unparseable bytes are treated as NOT content-absent (let the caller's normal
    parse path raise/handle them), so this never silently discards real content.
    """
    if not xml_bytes:
        return True
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return False

    body = root.find(f".//{{{_AKN_NS}}}mainBody")
    if body is None:
        body = root.find(f".//{{{_AKN_NS}}}body")
    if body is None:
        return True

    children = list(body)
    if not children:
        return True

    for el in body.iter():
        if _localname(el) == "componentRef":
            return True

    if len(children) == 1:
        child = children[0]
        if (
            _localname(child) == "hcontainer"
            and child.attrib.get("name") == "contentAbsent"
        ):
            return True

    return False


def read_reference_body(store: BodyStore, sid: str) -> bytes | None:
    """Best available body XML for reference extraction (oracle preferred).

    Prefers the consolidated oracle, but falls back to the enacted source (then
    the amendment act) when the oracle is absent OR a ``contentAbsent`` stub, so
    an expired/repealed statute's references are recovered instead of vanishing.
    Active statutes (oracle has real content) are unaffected — no extra read.

    Archive-only: this never triggers replay. Read errors fall back to ``None``
    via the next source (oracle absence is normal).
    """
    try:
        xb = store.read_oracle(sid)
    except Exception:  # noqa: BLE001 — oracle absence is normal, fall back
        xb = None
    if xb and not is_content_absent_body(xb):
        return xb
    try:
        src = store.read_source(sid)
    except Exception:  # noqa: BLE001 — unreadable source → try amendment/None
        src = None
    if src:
        return src
    try:
        return store.read_amendment(sid)
    except Exception:  # noqa: BLE001 — unreadable target → no body
        return None
