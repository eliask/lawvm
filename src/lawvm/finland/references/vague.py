"""Closed-list vague catch-all reference recognition (the T3 OPEN boundary).

This module owns the ``vague.open_catchall`` family from
FI_REFERENCE_CATALOGUE.md §2 and implements the **T3** tier of the resolution
ladder (catalogue §0.2): a finite, audited list of by-construction vague
reference phrases. A statute body sometimes refers to "the law" without naming
*which* law or *which* provision — ``muussa laissa säädetään`` ("as provided in
another act"), ``asianomaisessa asetuksessa`` ("in the relevant decree"). These
phrases carry NO determinate target by design; the source text declines to name
one.

Per **tag-don't-guess** (catalogue §0.3): such a phrase is typed
``cite_confidence=OPEN`` with ``target_provision_ref=None`` and routed to the
bounded residue overlay — never resolved to a concrete target and never silently
dropped. ``OPEN`` is assigned ONLY by this closed-list recognizer firing, never
by a numeric confidence threshold. That is the whole point of this lane: it makes
the deterministic / residue boundary itself deterministic and auditable.

The list is kept STRICT. A phrase belongs here only if it is *genuinely
targetless* — it must NOT carry a determinate act or provision. Constructs that
DO name a target are other lanes and must not be matched here:

  - ``tämän lain 5 §:ssä``      → internal self-ref lane (determinate provision)
  - ``luonnonsuojelulaissa``    → cross_statute.by_name lane (determinate act)
  - ``(424/2003) 6 §:ssä``      → cross_statute.by_id lane (determinate id)

Matching any of those here would produce a FALSE OPEN — declaring a perfectly
resolvable reference unresolvable. The list below is therefore conservative: a
phrase is included only when no surface continuation could pin a target.

§1.11 hot-path regex discipline: one alternation pattern compiled at module
scope over a closed, fixed phrase set with bounded quantifiers; the caller may
do a cheap substring pre-guard before invoking the matcher.
"""
from __future__ import annotations

import re
from typing import List

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)

# ---------------------------------------------------------------------------
# The closed vague-marker list (NORMATIVE — every entry must be targetless)
# ---------------------------------------------------------------------------
#
# Each entry is a fixed Finnish catch-all phrase that, by construction, names no
# determinate act and no determinate provision. The justification for each (why
# it is genuinely targetless) is recorded in the module-report and re-derivable
# from the gloss below.
#
#   muussa laissa säädetään        "as provided in ANOTHER (some other) act"
#   muualla laissa säädetään       "as provided ELSEWHERE in law"
#   muualla laissa                 bare "elsewhere in law" (no act named)
#   asianomaisessa asetuksessa     "in the RELEVANT decree" (which one — open)
#   asianomaisessa laissa          "in the RELEVANT act" (which one — open)
#   sovellettavassa lainsäädännössä "in the APPLICABLE legislation" (open scope)
#   erikseen säädetään             "shall be provided SEPARATELY" (target TBD)
#   erikseen säädettävällä lailla  "by an act to be enacted SEPARATELY" (no act)
#   siten kuin erikseen säädetään  "as SEPARATELY provided" (no determinate ref)
#   siten kuin siitä erikseen säädetään  same, with anaphoric "siitä"
#   sen mukaan kuin erikseen säädetään   "according as separately provided"
#
# NB on the ``siten kuin … säädetään`` family: only the variants whose gap is
# the closed adverbial ``erikseen`` / ``siitä erikseen`` are listed. A
# ``siten kuin`` clause that DOES carry a determinate target (e.g.
# ``siten kuin tämän lain 5 §:ssä säädetään``) is NOT in this list — it belongs
# to the self-ref / by-name lanes and must keep its determinate status.
_VAGUE_MARKERS: tuple[str, ...] = (
    "muussa laissa säädetään",
    "muualla laissa säädetään",
    "muualla laissa",
    "asianomaisessa asetuksessa",
    "asianomaisessa laissa",
    "sovellettavassa lainsäädännössä",
    "erikseen säädettävällä lailla",
    "siten kuin siitä erikseen säädetään",
    "sen mukaan kuin erikseen säädetään",
    "siten kuin erikseen säädetään",
    "erikseen säädetään",
)

#: Cheap substring pre-guards; if none appears, no marker can match.
_VAGUE_GUARDS: tuple[str, ...] = ("laissa", "asetuksessa", "lainsäädännössä", "erikseen")

# ---------------------------------------------------------------------------
# Compiled pattern (module scope — §1.11)
# ---------------------------------------------------------------------------
#
# Longest-first alternation so that e.g. "siten kuin siitä erikseen säädetään"
# is preferred over the shorter "erikseen säädetään" nested inside it, and
# "muualla laissa säädetään" over the bare "muualla laissa". Each phrase is
# matched literally (escaped); inter-word whitespace is allowed to vary.
_phrases_longest_first = sorted(_VAGUE_MARKERS, key=len, reverse=True)
_alternation = "|".join(
    r"\s+".join(re.escape(word) for word in phrase.split(" "))
    for phrase in _phrases_longest_first
)
_VAGUE_RE = re.compile(_alternation)


def recognize_vague_refs(text: str) -> List[ReferenceMention]:
    """Recognise closed-list vague catch-all references in ``text``.

    Returns one :class:`ReferenceMention` per matched vague marker, in document
    order, each typed ``cite_kind=CROSS_STATUTE`` (the catch-all points at "the
    law" — domestic statutory law — without naming which act) and
    ``cite_confidence=OPEN`` with ``target_provision_ref=None``.

    ``OPEN`` is assigned EXCLUSIVELY because a named marker from the closed list
    fired; this function never assigns OPEN by a threshold and never guesses a
    target. Phrases that carry a determinate target (``tämän lain 5 §:ssä``,
    ``luonnonsuojelulaissa``, ``(424/2003) 6 §:ssä``) are deliberately absent
    from the list and therefore do NOT match here — they are other lanes.

    ``source_provision_ref`` is an empty placeholder; the document-level
    integration step supplies citing-provision context via ``source_span`` /
    ``surface_text``.
    """
    if not any(guard in text for guard in _VAGUE_GUARDS):
        return []
    out: List[ReferenceMention] = []
    for m in _VAGUE_RE.finditer(text):
        out.append(
            ReferenceMention(
                source_provision_ref=ProvisionRef(statute_id=""),
                target_provision_ref=None,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=CiteConfidence.OPEN,
                phrase_lemma="vague_open_catchall",
                source_span=SourceSpan(
                    source_file="",
                    byte_offset=m.start(),
                    byte_len=m.end() - m.start(),
                ),
                valid_at_interval=(None, None),
                edge_subtype=None,
                surface_text=m.group(0),
            )
        )
    return out
