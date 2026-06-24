"""address_parse — Finnish legal address value type.

This module owns the canonical :class:`ParsedLegalAddress` value type emitted by
the legal-address recognizers.

The structural free-text address PARSER that used to live here
(``parse_legal_addresses``) has been demoted: it was the last parallel weaker
regex sub-ref grammar in the FI tree and is fully superseded by the shared
grammar driver :func:`lawvm.finland.references.freetext_addresses.scan_legal_addresses`,
which parses every site's structure through the shared johtolause grammar and is
a verified place-level superset. New consumers must call that recognizer; this
module is now only the value type.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedLegalAddress:
    """Structured legal address parsed from Finnish text.

    Attributes:
        section:    Section number label, e.g. "6", "24a".  Empty string
                    means this address has no section context (e.g. a
                    standalone momentti reference).
        subsection: Subsection (momentti) number, or None.
        item:       Item (kohta) label, e.g. "3", "a".  None if absent.
        subitem:    Sub-item (alakohta) label, e.g. "a".  None if absent.
                    Per Lainkirjoittajan opas: "6 §:n 2 momentin 1 kohdan
                    a alakohta".
        chapter:    Chapter number label, e.g. "3", "5a".  None means this
                    address is not a chapter reference.
        special:    "heading", "intro", or "" for whole-node addresses.
    """

    section: str = ""
    subsection: int | None = None
    item: str | None = None
    subitem: str | None = None
    chapter: str | None = None
    special: str = ""
