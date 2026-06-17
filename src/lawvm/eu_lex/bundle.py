"""Map an EUR-Lex-shaped XML fragment into the FI ``SourceSurfaceBundle`` shape.

SCAFFOLD, not ingestion. This proves that an EU act's structured XML can be
decoded into the SAME substrate the FI pipeline feeds its lenses
(``finland/legal_surface/bundle.py``::``build_surface_bundle``): a
``SourceSurfaceBundle`` of ``SourceSurfaceUnit``s over a ``SurfaceGraphSubject``,
with ``raw_text`` as the coordinate space and ``metadata["xml_bytes"]`` carrying
the source tree (the Stage-1 bridge the FI bundle also uses).

The synthetic fragment :data:`SYNTHETIC_EU_ACT_XML` is a deliberately small,
hand-authored shape — NOT real EU text. It mixes the structural vocabulary of
EUR-Lex's two real serialisations so the parser stub exercises the union it
will need to handle:

  * **Formex** (the operative EUR-Lex publication format): ``<ARTICLE>`` with an
    ``IDENTIFIER``/``NO.SEQ``-style attribute, ``<PARAG>``, ``<ALINEA>``, ``<P>``.
  * **Akoma Ntoso** (the EU's AKN4EU profile / what CELLAR increasingly serves):
    ``<article>``/``<paragraph>``/``<content>``/``<p>`` with ``eId`` attributes.

The stub's ``decode_eu_body_text`` collects ``<p>``/``<P>`` text exactly as the
FI ``decode_body_text`` collects ``<p>`` text, so the resulting ``raw_text``
coordinate space is shape-compatible. Where a real ingestion needs richer
per-article unit boundaries (one ``SourceSurfaceUnit`` per ARTICLE instead of
one whole-body unit), the boundary is marked with ``# TODO(eu-ingestion)``.

Boundaries that real ingestion must replace are marked ``# TODO(eu-ingestion)``.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from lawvm.core.legal_surface_graph import SourceSpanRef, SurfaceGraphSubject
from lawvm.core.legal_surface_lens import SourceSurfaceBundle, SourceSurfaceUnit
from lawvm.eu_lex.celex import celex_to_canonical_id

# A SYNTHETIC fragment. NOT real EU text. Authored to exercise both the Formex
# and AKN structural vocabularies in one document (a real document is one or the
# other; the stub handles the union by local-name so either serialisation maps
# in). Sector-3 CELEX so it looks like a regulation; the digits are invented.
SYNTHETIC_EU_ACT_XML: bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
<ACT celex="32099R0001">
  <TITLE><P>Synthetic Regulation (EU) 2099/1 (TEST FIXTURE, not real law)</P></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="001">
      <TI.ART><P>Article 1</P></TI.ART>
      <PARAG>
        <ALINEA><P>This Regulation lays down synthetic rules for scaffold testing.</P></ALINEA>
      </PARAG>
    </ARTICLE>
    <article eId="art_2">
      <num>Article 2</num>
      <paragraph eId="art_2__para_1">
        <content><p>References to Directive 31999L9999 are illustrative only.</p></content>
      </paragraph>
    </article>
  </ENACTING.TERMS>
</ACT>
"""


@dataclass(frozen=True, slots=True)
class EuActDocument:
    """A parsed EU act, pre-bundle.

    The minimal typed handle a real Formex/AKN parser would produce: the CELEX
    (the identity), the raw XML bytes (carried into the bundle as the Stage-1
    tree), and the decoded body text (the coordinate space). A real parser would
    additionally expose article/paragraph structure; that is the
    ``# TODO(eu-ingestion)`` extension point.
    """

    celex: str
    xml_bytes: bytes
    body_text: str
    language: str = "en"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def decode_eu_body_text(xml_bytes: bytes) -> str:
    """Decode an EU act body into one coordinate space (mirrors FI decode).

    Collects every paragraph element's text — Formex ``<P>`` and AKN ``<p>``
    (matched case-insensitively by local name) — joined by newlines, exactly the
    way ``finland/legal_surface/bundle.py``::``decode_body_text`` collects FI
    ``<p>`` text. Returns "" on parse error (fail-soft on malformed XML, same as
    the FI decoder; ingestion validity is a separate, louder concern).

    # TODO(eu-ingestion): real Formex/AKN has more text-bearing elements
    # (HT highlighting, NOTE, list items). A real decoder must enumerate the
    # actual element set per serialisation, not just paragraph tags.
    """
    if not xml_bytes:
        return ""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    parts: list[str] = []
    for el in root.iter():
        if _local_name(el.tag).lower() == "p":
            parts.append("".join(el.itertext()))
    return "\n".join(parts)


def _extract_celex(root: ET.Element) -> str | None:
    """Best-effort CELEX from the document.

    Synthetic fragment carries it on the root ``celex=`` attribute. Real Formex
    keeps it in metadata/work-level RDF (CELLAR notice), AKN in
    ``<FRBRWork>/<FRBRthis>``. # TODO(eu-ingestion): read the real metadata path
    instead of a convenience attribute; fail loud if absent (the CELEX is the
    identity — a document with no CELEX cannot align to a frontier node).
    """
    celex = root.get("celex")
    return celex or None


def parse_eu_act_fragment(
    xml_bytes: bytes,
    *,
    celex: str | None = None,
    language: str = "en",
) -> EuActDocument:
    """Parse an EUR-Lex-shaped fragment into a typed :class:`EuActDocument`.

    ``celex`` may be passed explicitly (a real ingestion knows the CELEX from
    the fetch request) or recovered from the document. Fail loud if neither is
    available — the CELEX is the boundary identity.
    """
    root = ET.fromstring(xml_bytes)
    resolved_celex = celex or _extract_celex(root)
    if not resolved_celex:
        raise ValueError(
            "EU act fragment carries no CELEX and none was supplied; "
            "cannot mint an aligning entity id without the boundary identity"
        )
    body_text = decode_eu_body_text(xml_bytes)
    return EuActDocument(
        celex=resolved_celex,
        xml_bytes=xml_bytes,
        body_text=body_text,
        language=language,
    )


def build_eu_surface_bundle(
    doc: EuActDocument,
    *,
    surface_time: str | None = None,
) -> SourceSurfaceBundle:
    """Build a whole-body ``SourceSurfaceBundle`` for one EU act (scaffold v0).

    Mirrors ``finland/legal_surface/bundle.py``::``build_surface_bundle`` exactly
    in shape, with two deliberate differences that ARE the boundary:

      * ``jurisdiction="eu"`` (not ``"fi"``).
      * ``work_id`` is the CELEX canonical id ``celex:<CELEX>`` — the SAME
        canonical id the FI resolver carries for an EU target — so the work
        entity a future EU ReferenceLens mints (``entity:celex:<CELEX>``) lands
        on the FI-side frontier node. This is the join.

    # TODO(eu-ingestion): v0 emits ONE whole-body unit (like FI v0). A real
    # ingestion should emit one ``SourceSurfaceUnit`` per ARTICLE with an
    # ``address`` so provision-level (``entity:celex:<CELEX>#art_2``) targets
    # exist as distinct nodes — matching the FI ``legal_address_entity`` story
    # in ``corpus_graph.py``.
    # TODO(eu-ingestion): token_tape / morph_overlay are FI-specific (Finnish
    # morphology). EU acts need a language-appropriate tokenizer or none in v0;
    # left None here, which is valid (lenses that need them set required_views).
    """
    canonical_id = celex_to_canonical_id(doc.celex)
    raw_text = doc.body_text
    source_hash = _sha256_bytes(doc.xml_bytes)
    text_hash = _sha256_text(raw_text)
    source_unit_id = f"{canonical_id}#body"
    unit = SourceSurfaceUnit(
        source_unit_id=source_unit_id,
        work_id=canonical_id,
        address=None,
        raw_text=raw_text,
        source_hash=source_hash,
        source_ref=SourceSpanRef(
            source_unit_id=source_unit_id,
            source_hash=source_hash,
            work_id=canonical_id,
            address=None,
            char_start=0,
            char_end=len(raw_text),
            text_hash=text_hash,
        ),
        # Stage-1 bridge, same as FI: carry the tree so future EU adapter lenses
        # can run structural recognizers over the real XML.
        metadata={"xml_bytes": doc.xml_bytes},
    )
    subject = SurfaceGraphSubject(
        jurisdiction="eu",
        work_id=canonical_id,
        scope={"kind": "whole_work"},
        surface_time=surface_time,
        source_bundle_hash=source_hash,
        language=doc.language,
    )
    return SourceSurfaceBundle(jurisdiction="eu", subject=subject, units=(unit,))
