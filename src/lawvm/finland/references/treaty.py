"""Finnish treaty-series (SopS) reference recognition.

Recognises Finnish treaty-series citations — the ``SopS NNN/YYYY`` form used to
cite treaties published in the *Suomen säädöskokoelman sopimussarja* (Finnish
Treaty Series). Both the bare ``SopS 19/2020`` and the parenthetical
``(SopS 19/2020)`` forms appear in statute bodies.

This is the ``treaty.sops`` family from FI_REFERENCE_CATALOGUE.md §2. It is a
**T1** (pure-grammar) recognizer: the SopS number is itself a determinate
treaty-series id, so a recognised citation resolves EXACT — no registry lookup
is needed to pin the target series (the in-series provision path, if any, is a
later concern and is not parsed here).

The recognizer is SELF-CONTAINED: it takes raw text and returns typed
``ReferenceMention`` rows with ``cite_kind=TREATY`` and
``cite_confidence=EXACT``. Wiring into the document-level extractor (which
supplies the citing-provision context and re-anchors the source ref) is a later
integration step; this module does not edit ``ref_mention_extractor``.

§1.11 hot-path regex discipline: the pattern is compiled at module scope with
bounded quantifiers (series number 1-6 digits, year exactly 4 digits) and the
caller performs a cheap ``"SopS"`` substring guard before invoking the matcher.
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
# Compiled pattern (module scope — §1.11)
# ---------------------------------------------------------------------------

#: Cheap substring guard the caller / helper checks before running the matcher.
_SOPS_GUARD = "SopS"

# "SopS NNN/YYYY" with an optional surrounding parenthesis pair. The series
# number is 1-6 digits; the year is exactly 4 digits. Word boundaries keep the
# match from gluing onto adjacent alphanumerics. Bounded quantifiers throughout.
_SOPS_RE = re.compile(
    r"\bSopS\s+(?P<num>\d{1,6})/(?P<year>\d{4})\b",
)


def recognize_treaty_refs(text: str) -> List[ReferenceMention]:
    """Recognise Finnish treaty-series (SopS) references in ``text``.

    Returns one :class:`ReferenceMention` per ``SopS NNN/YYYY`` citation, in
    document order. Each is typed ``cite_kind=TREATY``,
    ``cite_confidence=EXACT`` (the SopS number is a determinate treaty-series
    id), with a target ``ProvisionRef`` whose ``statute_id`` is the canonical
    treaty id ``fi:treaty:sops/YYYY/NNN``.

    The parenthetical form ``(SopS 19/2020)`` and the bare form
    ``SopS 19/2020`` both match; the surrounding parentheses (when present) are
    not part of the recognised span — only the ``SopS NNN/YYYY`` core is.

    ``source_provision_ref`` is an empty placeholder; the citing-provision
    context is supplied by the document-level integration step, which re-anchors
    using ``source_span`` / ``surface_text``.
    """
    if _SOPS_GUARD not in text:
        return []
    out: List[ReferenceMention] = []
    for m in _SOPS_RE.finditer(text):
        num = m.group("num")
        year = m.group("year")
        target = ProvisionRef(statute_id=f"fi:treaty:sops/{year}/{num}")
        out.append(
            ReferenceMention(
                source_provision_ref=ProvisionRef(statute_id=""),
                target_provision_ref=target,
                cite_kind=CiteKind.TREATY,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="treaty_sops",
                source_span=SourceSpan(
                    source_file="",
                    byte_offset=m.start(),
                    byte_len=m.end() - m.start(),
                ),
                valid_at_interval=(None, None),
                edge_subtype="CITES",
                surface_text=m.group(0),
            )
        )
    return out
