"""Canonical "operative body text" filter for the Finnish legal-surface path.

``decode_body_text`` and ``build_provision_index`` both flatten a statute's
``<p>`` set into the analysis coordinate space (the flat string reference /
definition / segmentation recognisers scan). Finlex embeds NON-OPERATIVE
editorial material in that body:

  * ``<authorialNote>`` — AKN footnotes / corrigendum notes; and
  * ``<hcontainer|block name="noteAuthorial|signatures|conclusions|attachments">``
    — editorial version notes ("Aiempi sanamuoto kuuluu / tulee voimaan …") and
    document-tail boilerplate.

This is editorial metadata, never operative law. The temporal lane reads its
commencement dates straight from the XML TREE (``oracle_text.build_temporal_
spans``) and the ``<ref>`` lane reads ``entryIntoForce`` refs from the tree —
neither needs it as flat body text, and the by-id reference recogniser actively
DECLINES the ``d.m.YYYY/NNN`` date-refs it would leak. Letting it into the
coordinate space only manufactures spurious mentions.

This module is the SINGLE definition of "which element is non-operative
editorial material", shared by both body extractors so they cannot drift apart
(a drift between them is precisely what the provision-index join guard catches).
It mirrors the strip set the bench-critical replay path already applies in
``tools/section_keys._normalize_oracle_section``; a coherence test pins the two
sets equal so they cannot silently diverge.

It filters only the flat text extraction — it never touches the raw XML
(``xml_bytes``) the tree-reading lenses and the temporal lane consume, so no
structural signal (commencement, version boundaries, entryIntoForce refs) is
lost.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping

# Editorial ``hcontainer``/``block`` ``name`` values Finlex uses for
# non-operative material. The SAME set
# ``tools/section_keys._ORACLE_SECTION_STRIP_NAMES`` strips on the replay path
# (``tests/test_fi_editorial_filter.py`` pins them equal).
EDITORIAL_NOTE_NAMES = frozenset(
    {"noteAuthorial", "signatures", "conclusions", "attachments"}
)

# Provision containers whose nesting ``build_provision_index`` folds into a
# provision path (chapter ▸ section ▸ subsection ▸ paragraph). ``article`` is the
# treaty/EU analogue of ``section``; ``point``/``item`` of ``paragraph``.
PROVISION_CONTAINER_TAGS = frozenset(
    {"chapter", "section", "article", "subsection", "paragraph", "point", "item"}
)


def _localname(tag: object) -> str:
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def is_editorial_element(local: str, attrib: Mapping[str, str]) -> bool:
    """True for a non-operative editorial element (footnote / version note /
    document-tail boilerplate) whose text must NOT enter the body coordinate
    space."""
    if local == "authorialNote":
        return True
    if local in ("hcontainer", "block") and attrib.get("name") in EDITORIAL_NOTE_NAMES:
        return True
    return False


def operative_itertext(el: ET.Element) -> Iterator[str]:
    """``el.itertext()`` but skipping non-operative editorial subtrees.

    An editorial child's own text is dropped; its TAIL (operative text that
    follows the note, still inside the parent) is kept — so a corrigendum
    footnote embedded mid-sentence is removed without dropping the operative
    remainder of the sentence.
    """
    if el.text:
        yield el.text
    for child in el:
        if is_editorial_element(_localname(child.tag), child.attrib):
            if child.tail:
                yield child.tail
            continue
        yield from operative_itertext(child)
        if child.tail:
            yield child.tail


def iter_operative_paragraphs(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, tuple[ET.Element, ...]]]:
    """Yield ``(p_element, container_stack)`` for every OPERATIVE ``<p>`` in
    document order, skipping non-operative editorial subtrees.

    ``container_stack`` is the tuple of enclosing provision containers
    (chapter ▸ section ▸ subsection ▸ paragraph …) — the ancestry
    ``build_provision_index`` folds into a provision path. ``decode_body_text``
    ignores the stack. Both extractors consume this SAME enumeration, so their
    newline joins are identical by construction (the provision-index drift guard
    then becomes a redundant safety net rather than a live failure mode).
    """

    def walk(
        el: ET.Element, stack: tuple[ET.Element, ...]
    ) -> Iterator[tuple[ET.Element, tuple[ET.Element, ...]]]:
        local = _localname(el.tag)
        if is_editorial_element(local, el.attrib):
            return  # skip the whole non-operative subtree (and its nested <p>)
        if local == "p":
            yield (el, stack)
            # fall through and descend: a nested OPERATIVE <p> (rare) is kept in
            # document order; a nested editorial <p> is skipped by the check
            # above when the walk reaches its editorial ancestor.
        new_stack = stack + (el,) if local in PROVISION_CONTAINER_TAGS else stack
        for child in el:
            yield from walk(child, new_stack)

    yield from walk(root, ())
