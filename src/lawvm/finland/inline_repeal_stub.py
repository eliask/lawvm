"""Finnish paragraph-level inline repeal stub detector.

Finlex consolidated oracle XML embeds editorial notices as italic paragraphs
when a kohta has been repealed inline:

    <paragraph eId="chp_1__sec_3__subsec_1__para_2v20211030">
      <content><p><i>2 kohta on kumottu A:lla 25.11.2021/1030.</i></p></content>
    </paragraph>

These are editorial metadata, not law.  This module detects them and registers
the detector with the shared projection layer via the jurisdiction dispatch hook
so that ``semantic_structure_from_oracle`` can strip them without importing
Finnish-specific regex strings directly.

Detector registration happens at import time.  The Finland frontend's startup
path must ensure this module is imported before oracle projection runs.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from lxml import etree


# ---------------------------------------------------------------------------
# Finnish-specific regex constants
# ---------------------------------------------------------------------------

# Paragraph-level (kohta/momentti/§) inline repeal stub detection.
#
# Matches: "N kohta on kumottu A:lla DD.MM.YYYY/NNNN"
# or range: "N–M kohta on kumottu A:lla DD.MM.YYYY/NNNN"
# Non-breaking space (\u00a0) is accepted alongside normal space.
_PARA_KUMOTTU_RE = re.compile(
    r'^\s*\d+(?:\s*[-\u2013\u2014]\s*\d+)?\s+'
    r'(?:kohta|kohdat|momentti|momentit|mom\.?|§)\s+'
    r'on\s+kumottu\s+[LAP]:ll[äa]\s+'
    r'.*\d+\.\d+\.\d{4}/\d+',
    re.IGNORECASE | re.DOTALL,
)

# eId suffix that marks a synthetic versioned paragraph in oracle consolidated XML.
_PARA_VERSIONED_EID_RE = re.compile(r'.*para_\d+v\d{8}$')


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

def _is_paragraph_level_kumottu_stub(para: etree._Element) -> bool:
    """Return True when a ``<paragraph>`` element is a Finlex inline repeal stub.

    Detection criteria (all must hold):

    1. ``eId`` matches ``.*para_\\d+v\\d{8}$`` — synthetic versioned suffix.
    2. No ``<num>`` child — stubs carry no item number.
    3. Text content (stripped, normalised whitespace) matches the Finnish
       repeal phrasing ``N kohta on kumottu A:lla DD.MM.YYYY/NNNN``, optionally
       with a range ``N–M``.  Non-breaking spaces are tolerated.
    4. Contains ``<i>`` markup — the Finlex editorial visual convention.

    These stubs are **not** law; they are Finlex editorial metadata rendered
    inline into the consolidated text for human readers.  They must be stripped
    from the oracle semantic tree before comparison so they do not produce
    spurious ``unit_added_right`` diff events.
    """
    # 1. eId suffix check
    eid = para.get("eId", "")
    if not _PARA_VERSIONED_EID_RE.match(eid):
        return False

    # 2. No <num> child
    if para.find("{*}num") is not None or para.find("num") is not None:
        return False

    # 3. Text content matches repeal phrasing
    raw_text = etree.tostring(para, method="text", encoding="unicode")
    # Normalise non-breaking spaces and collapse whitespace for matching
    normalised = re.sub(r'\s+', ' ', raw_text.replace('\u00a0', ' ')).strip()
    if not _PARA_KUMOTTU_RE.match(normalised):
        return False

    # 4. Contains <i> markup
    if para.find(".//{*}i") is None and para.find(".//i") is None:
        return False

    return True


def extract_paragraph_stub_amendment_id(para: etree._Element) -> Optional[str]:
    """Extract the amendment id (e.g. '2021/1030') from an inline repeal stub.

    Looks for a ``<ref>`` child whose text matches ``DD.MM.YYYY/NNNN``.  If not
    found, falls back to a regex search on the plain text content.  Returns None
    when the id cannot be determined.
    """
    # Try <ref> text first — most reliable.
    for ref in list(para.iter("{*}ref")) + list(para.iter("ref")):
        text = etree.tostring(ref, method="text", encoding="unicode").strip()
        m = re.search(r'\d+\.\d+\.(\d{4})/(\d+)', text)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    # Fallback: search plain text of whole element
    raw = etree.tostring(para, method="text", encoding="unicode")
    m = re.search(r'\d+\.\d+\.(\d{4})/(\d+)', raw)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def extract_paragraph_stub_target_range(para: etree._Element) -> list[int]:
    """Extract the target kohta ordinals from an inline repeal stub.

    Handles single ("2 kohta") and range ("1–2 kohta") forms.  Returns a list
    of integer ordinals, e.g. [2] or [1, 2].
    """
    raw = etree.tostring(para, method="text", encoding="unicode")
    normalised = re.sub(r'\s+', ' ', raw.replace('\u00a0', ' ')).strip()
    m = re.match(
        r'^\s*(\d+)(?:\s*[-\u2013\u2014]\s*(\d+))?\s+'
        r'(?:kohta|kohdat|momentti|momentit|mom\.?|§)',
        normalised,
        re.IGNORECASE,
    )
    if not m:
        return []
    first = int(m.group(1))
    if m.group(2) is not None:
        return list(range(first, int(m.group(2)) + 1))
    return [first]


# ---------------------------------------------------------------------------
# Projection hook: detect and package stub observation for projection layer
# ---------------------------------------------------------------------------

def _detect_fi_inline_repeal_stub(node: Any) -> dict[str, Any] | None:
    """Jurisdiction hook for the shared projection layer.

    Called by ``_detect_inline_repeal_stub("fi", node)`` in
    ``lawvm.semantic.projection``.  Returns an observation dict if *node* is a
    Finlex inline repeal stub paragraph, otherwise returns None.

    The returned dict has keys:
    - ``kind``: ``"FINLEX_INLINE_REPEAL_STUB"``
    - ``eId``: the paragraph's eId attribute
    - ``target_range``: list[int] of affected kohta ordinals
    - ``amendment_id``: str like ``"2021/1030"`` or None
    """
    if not isinstance(node, etree._Element):
        return None
    # node.tag is a string for normal elements but a function for comments /
    # processing instructions / etc.  Guard against the non-element case.
    if not isinstance(node.tag, str):
        return None
    tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
    if tag != "paragraph":
        return None
    if not _is_paragraph_level_kumottu_stub(node):
        return None
    return {
        "kind": "FINLEX_INLINE_REPEAL_STUB",
        "eId": node.get("eId", ""),
        "target_range": extract_paragraph_stub_target_range(node),
        "amendment_id": extract_paragraph_stub_amendment_id(node),
    }


# ---------------------------------------------------------------------------
# Self-registration — runs at import time
# ---------------------------------------------------------------------------

from lawvm.semantic.projection import register_inline_repeal_stub_detector  # noqa: E402

register_inline_repeal_stub_detector("fi", _detect_fi_inline_repeal_stub)
