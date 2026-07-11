"""Text-layer REPAIR — validated glyph-substitution token repair (§8).

The REPAIR sibling of :mod:`lawvm.ingest.suspect_region`. That module DETECTS a
text-layer-quality defect (``lexical_implausibility`` /
``cross_reader_disagrees`` / ``more_plausible`` / ``_bigram_plausibility``);
this one REPAIRS a *specific, known* glyph confusion — deterministically and only
when an INDEPENDENT constraint confirms the repair is plausible. The pair is the
detect/repair seam for text-layer fidelity; keep them findable together.

The problem it generalizes
==========================
An embedded PDF font can render one glyph AS ANOTHER, so a token arrives in the
text layer with a wrong-but-legible shape: a Finnish statute citation ``/`` that
renders as ``1`` (``(1505/1992)`` → ``(150511992)``), an ``l`` read as ``1``, an
``O`` read as ``0``, an ``rn`` read as ``m``. The mis-read is not garbled sludge
(``suspect_region``'s lexical detector will not fire — the token still looks like
a plausible number/word); it silently defeats a downstream recognizer whose
anchor expects the *intended* shape.

Blindly substituting the intended glyph everywhere would corrupt genuine tokens
(a real ``1`` is far more common than a mis-read ``/``). The discipline that
makes a substitution SAFE is the same one ``suspect_region`` uses for a re-read:
**adopt the repair only when an independent validator confirms it**. Here the
validator is a *constraint on the restored token* — a year sitting in a plausible
statute-year band, a checksum, a known enumerated shape, or (the phase-5
direction) agreement with a second, independently-produced reader. The mechanism
is jurisdiction- and language-agnostic; only the corrupt SHAPE, the intended
glyph, and the plausibility CONSTRAINT are caller-specific surface.

The general contract
====================
:func:`repair_glyph_substitution` is the thin, well-named home for this. A caller
supplies:

* ``corrupt_re`` — a compiled pattern matching the *corrupted* token shape, with
  capture groups for the parts to carry into the restored token. The pattern
  encodes the caller's confusion (which glyph, in which surrounding shape).
* ``restore`` — a :meth:`re.Match.expand` template that rebuilds the *intended*
  token from those groups (e.g. ``r"(\1/\2)"`` re-inserts the ``/``).
* ``is_plausible`` — the INDEPENDENT validator, ``Match -> bool``. The repair is
  adopted for a match ONLY when this returns ``True``; otherwise the original
  substring is left byte-identical. Defaults to *always plausible* for a
  confusion whose shape is already unambiguous, but the value of the seam is that
  a caller can gate on a constraint the corrupted shape alone cannot guarantee.

This is deliberately NOT a framework: it is one ``re.sub`` with a validated
replacer. Its worth is the DISCOVERABLE SEAM plus a single place to accumulate
known glyph confusions as registered callers, rather than a scatter of one-off
``_repair_*`` helpers each re-deriving the "restore-then-validate" discipline.

Known / anticipated glyph confusions (grow this catalog as callers appear):

* ``/`` ↔ ``1`` — a parenthesised statute citation slash mis-read as a digit;
  validated by a plausible year band. First caller:
  ``lawvm.tools.fi_he_ir_compare._repair_slash_as_one_cites`` (FI/EU cite shape +
  1600–2099 band — the only FI-specific surface; the mechanic here is general).
* ``l`` ↔ ``1`` / ``O`` ↔ ``0`` / ``rn`` ↔ ``m`` — classic OCR/font confusions,
  each validated by a shape or checksum constraint on the restored token.
"""
from __future__ import annotations

import re
from typing import Callable

__all__ = ["repair_glyph_substitution"]


def repair_glyph_substitution(
    text: str,
    *,
    corrupt_re: "re.Pattern[str]",
    restore: str,
    is_plausible: Callable[["re.Match[str]"], bool] = lambda _m: True,
) -> str:
    """Restore a known glyph substitution in ``text``, gated by an independent validator.

    For every non-overlapping match of ``corrupt_re`` (the corrupted token shape),
    rebuild the intended token via ``restore`` (a :meth:`re.Match.expand` template
    over the match's groups) — but ADOPT the rebuilt token ONLY when
    ``is_plausible(match)`` is ``True``. A match the validator rejects is left
    BYTE-IDENTICAL (its original substring is returned), so a genuine token that
    merely resembles the corrupted shape is never mangled into a phantom.

    Pure and deterministic: no model, no I/O. The independence that makes a
    substitution safe lives entirely in ``is_plausible`` — a constraint on the
    restored token (a value band, a checksum, a known shape) or, in the phase-5
    direction, agreement with a second independently-produced reader. See the
    module docstring for the general contract and the known-confusion catalog, and
    :mod:`lawvm.ingest.suspect_region` for the DETECTION sibling.
    """

    def _replace(match: "re.Match[str]") -> str:
        if is_plausible(match):
            return match.expand(restore)
        return match.group(0)

    return corrupt_re.sub(_replace, text)
