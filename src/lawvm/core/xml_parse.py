"""Hardened lxml parser config for untrusted corpus XML.

Purpose:
    Every corpus-XML parse site in LawVM must route through ``parse_corpus_xml``
    so the lxml defaults that are unsafe for untrusted input are pinned in one
    auditable place.  The defaults lxml ships with are partially safe:

      * ``no_network=True``  — already safe (no external DTD fetch).
      * ``huge_tree=False``  — already safe (caps document size).
      * ``resolve_entities=True``  — UNSAFE for untrusted input.  Internal-entity
        expansion (the billion-laughs family) is only partially mitigated by
        lxml's 10 MB expansion cap; turning off entity resolution closes the
        class entirely rather than relying on the cap.  DOCTYPE / external-entity
        vectors collapse to ``load_dtd=False`` + ``dtd_validation=False``.

    The hardened config therefore sets:

      * ``resolve_entities=False``
      * ``no_network=True``
      * ``huge_tree=False``
      * ``load_dtd=False``
      * ``dtd_validation=False``
      * ``recover`` — caller-opted; default False so malformed XML fails loud
        (AGENTS.md §1.10).  ``recover=True`` is only for known-broken sources.

Reference: AGENTS.md §1.10 (no broad exception swallowing; fail loud),
§2.6 (rule of three — the same hardened-config shape landed at 30 sites).
Used by: every frontend corpus / oracle / amendment / HE / finlex / norway
source-XML parse site (see ``tests/test_corpus_xml_parser_ratchet.py`` for the
monotone ratchet that forbids new raw ``etree.fromstring`` calls without a
``parser=`` keyword in frontend modules).
"""
from __future__ import annotations

from lxml import etree

__all__ = ["parse_corpus_xml"]


# Hardened parser config — see module docstring.  Module-scope so the parser
# state is reused (lxml parser reuse is significantly cheaper than rebuilding).
_LIMITED_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    huge_tree=False,
    load_dtd=False,
    dtd_validation=False,
)
_LIMITED_RECOVERING_PARSER = etree.XMLParser(
    recover=True,
    resolve_entities=False,
    no_network=True,
    huge_tree=False,
    load_dtd=False,
    dtd_validation=False,
)


def parse_corpus_xml(xml_bytes: bytes, *, recover: bool = False) -> etree._Element:
    """Parse untrusted corpus XML with the hardened lxml config.

    Use this for every corpus / oracle / amendment / HE / finlex / norway
    source-XML parse.  ``recover=True`` is reserved for known-broken sources
    (Norway Lovdata HTML, corrigendum-fragment recovery) where strict parsing
    has historically failed — every other site should keep the default
    ``recover=False`` so malformed input fails loud with ``XMLSyntaxError``
    instead of silently producing a partial tree (AGENTS.md §1.10).

    Args:
        xml_bytes: Untrusted XML bytes (corpus source, oracle, amendment, HE,
            finlex collection page, Norway Lovdata document, ...).
        recover: If True, parse in recovery mode — lxml will tolerate
            well-formedness errors and return a best-effort tree.  Use only
            when the source is known to be broken and strict parsing is not
            an option.

    Returns:
        The parsed root ``etree._Element``.

    Raises:
        lxml.etree.XMLSyntaxError: On malformed XML in non-recover mode
            (the desired fail-loud behaviour).
    """
    return etree.fromstring(
        xml_bytes,
        parser=_LIMITED_RECOVERING_PARSER if recover else _LIMITED_PARSER,
    )
