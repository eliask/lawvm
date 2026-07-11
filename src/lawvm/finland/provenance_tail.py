"""Canonical Finnish provenance-tail marker (``sellaisena/sellaisina kuin``).

A Finnish amendment clause names its target statute and then, in many cases,
appends an amendment-history / prior-version tail introduced by ``sellaisena
kuin se on ...`` / ``sellaisina kuin ne ovat ...`` ("as it stands / as amended
by acts ..."). Everything after that marker cites the PRIOR AMENDING acts of the
touched provisions, not the governing target. Readers and parsers across the
Finland frontend need to separate the target reference from this provenance tail.

This module is the single, discoverable home for that primitive so callers stop
re-deriving it. Two shapes are exported:

* :data:`HISTORY_MARKER_RE` — the ``sellais(ena|ina) kuin`` boundary marker used
  to REJECT any statute citation lying past the tail (the HE enacting-clause
  reader and the HE-branch johtolause parser share it verbatim).
* :func:`strip_source_provenance_tail` — DROP a trailing ``, sellaisena/sellaisina
  kuin ...`` qualifier before extracting repeal targets (the kumotaan extractor).

DELIBERATELY NOT UNIFIED: several Finland modules carry INTENTIONALLY different
variants of the same marker idiom, and they must NOT be collapsed into a single
regex — the differences are load-bearing:

* ``finland.johtolause.affected_statute._TARGET_ZONE_CUT_RE`` tolerates an OCR
  typo (``sell?ais``), a ``,\\s+kuin`` spelling, and the sibling marker ``siihen
  myöhemmin`` (the richest routing-surface cut; ``citation_routing`` delegates to
  it);
* ``finland.scope`` / ``finland.amendment_index`` add ``siitä on`` / ``siihen
  myöhemmin`` alternatives;
* ``finland.references.cited_version`` uses a looser stem (``sellais[ei][a-zäöå]*``)
  and is a CLASSIFIER-registered cue (captures the cited version id), not a plain
  delimiter.

Those keep their own patterns; this module owns only the two byte-identical
shapes that were genuinely duplicated. See the audit note "Don't reinvent
canonical capabilities".
"""

from __future__ import annotations

import re

#: The ``sellaisena/sellaisina kuin`` amendment-history sub-clause marker. A
#: genuine amendment directive lists the provisions it touches between its statute
#: citation and ``seuraavasti:``; the parenthesised ids that follow this marker are
#: PRIOR amending acts, not the governing target, and must be excluded from
#: head-cite / bill-scope resolution. Consumed verbatim by the HE enacting-clause
#: reader (``tools.fi_he_ir_compare``) and the HE-branch johtolause parser
#: (``finland.he_branch_parser``).
HISTORY_MARKER_RE = re.compile(r"sellais(?:ena|ina)\s+kuin", re.IGNORECASE)


def strip_source_provenance_tail(kumotaan_text: str) -> str:
    """Drop a trailing ``, sellaisena/sellaisina kuin ...`` provenance tail.

    Kumotaan clauses often append source-history qualifiers like ``sellaisina
    kuin ne ovat ... asetuksessa 1282/2000``. Those extra statute references do
    not change the repeal targets; they only identify the amendment source of the
    current wording. Strip that tail (leading-comma anchored, permissive stem)
    before applying the multi-statute guard and extracting targets.
    """
    return re.split(r",\s*sellais[a-zäöå\s]*kuin\b", kumotaan_text, maxsplit=1, flags=re.I)[0]
