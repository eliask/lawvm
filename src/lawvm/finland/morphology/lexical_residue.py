"""The SMALL stored flags --- NOT form tables.

These are the entire irreducible "vocabulary" of M1: per-lemma boolean / enum
flags that the rules cannot derive from the surface.  Everything else is rule.

* :data:`SINGLE_K` --- single-``k`` realization (k -> zero/v/j).  Lexically
  conditioned; most are ``zero``.  Consumed by :func:`gradation.weaken_stem` via
  the head table (``single_k=SINGLE_K.get(lemma)`` in :mod:`heads`).

Removed as UNWIRED a-priori dead code (audited 2026-06-19, zero consumers in
``src/`` or ``tests/``): ``AGENCY_ACRONYMS``/``is_acronym`` (acronyms live in the
canonical actor registry with lifecycle semantics), ``GRADATION_OCCURS`` (head
gradation comes from :mod:`heads`; non-head gradation defaults False), and
``EXTERNAL_LOCATIVE``/``is_external_locative`` (the ``locative_series`` field on
``MorphEntry`` is the live mechanism, set by callers, never fed from this list;
place names are never inflected as lemma heads today).  If place-name inflection
is ever needed, build a corpus-derived municipality registry (the
``build_statute_name_registry`` pattern) rather than re-introducing a hand list.
"""

from __future__ import annotations

# Single-k realization: lemma -> "zero" | "v" | "j".
SINGLE_K: dict[str, str] = {
    "laki": "zero",  # lain
    "luku": "v",  # luvun (chapter head: single-k realizes as v)
    "Turku": "zero",  # Turun
    "Helsinki": "zero",  # Helsingin handled by nk->ng rule, not single-k
    # NOTE: Helsinki's k is part of the -nk- cluster -> assimilative rule, so it
    # is intentionally NOT given a single_k flag where the rule already fires.
}
# Helsinki's -nk- is rule-handled; drop it to avoid double-application.
del SINGLE_K["Helsinki"]


__all__ = [
    "SINGLE_K",
]
