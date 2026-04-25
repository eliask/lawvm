"""meta_parse — Extract meta/effect/temporal surface clauses from johtolause text.

Phase 7 integration: meta/effect clause extraction that flows through the same
pipeline as structural amendment parsing, producing SurfaceMetaClause nodes.

This module provides:
    extract_meta_surface_clauses(text) -> list[SurfaceMetaClause]

The results are wired into parse_clause() (compat.py) so that the full clause
parse result carries both structural and meta surface information.

Design notes:
- This is ADDITIVE alongside the existing effect_lowering.py path, which
  continues to operate on the lower MetaClause / EffectIntent layer.
- SurfaceMetaClause is the surface representation; it does not lower to
  EffectIntent here.  That lowering continues via extract_meta_clauses() +
  lower_meta_clause() in effect_lowering.py.
- Patterns are sentence-level, same heuristic as effect_lowering._META_SENTENCE_PATTERNS,
  but the output is a SurfaceMetaClause with meta_kind classification.

meta_kind values:
    "commencement"  — "Tämä laki tulee voimaan..."
    "expiry"        — "Tämä laki on voimassa [until date]..."
    "transition"    — siirtymäsäännös / soveltamissäännös
    "delegation"    — valtuutus (antaa säännöksiä/määräyksiä)

TODO (future work):
    - "effect" — jolloin... consequence tails (currently handled by surface_resolve)
    - Span-level witness tracking (char offsets into source text)
"""

from __future__ import annotations

import re
from typing import List

from lawvm.core.semantic_types import MetaClauseKind
from lawvm.finland.johtolause.surface_model import SurfaceMetaClause, SurfaceWitness

# ---------------------------------------------------------------------------
# Sentence splitter — same logic as effect_lowering._SENTENCE_SPLIT
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÅ])")

# ---------------------------------------------------------------------------
# Meta-clause sentence patterns
# (parallel to effect_lowering._META_SENTENCE_PATTERNS, but producing
# SurfaceMetaClause meta_kind values)
# ---------------------------------------------------------------------------

_META_PATTERNS: List[tuple[MetaClauseKind, re.Pattern[str]]] = [
    # Transition/applicability clauses — check before voimaantulo to avoid
    # "ennen lain voimaantuloa" matching as a commencement/expiry pattern.
    (
        MetaClauseKind.TRANSITION,
        re.compile(
            r"soveltamiss[aä][äa]nn[öo]s"
            r"|siirtymäs[aä][äa]nn[öo]s"
            r"|tätä\s+lakia\s+sovelletaan"
            r"|ennen\s+(?:tämän\s+lain|lain)\s+voimaantuloa\s+(?:vireille|käsitelty|myönnetty)",
            re.IGNORECASE,
        ),
    ),
    # Expiry — "on voimassa [until date]" (must precede commencement to avoid
    # false positive on "tulee voimaan" when "on voimassa" is also present).
    (
        MetaClauseKind.EXPIRY,
        re.compile(
            r"\bon\s+voimassa\b"
            r"|voimassaoloaika",
            re.IGNORECASE,
        ),
    ),
    # Commencement — "tulee/tuli voimaan"
    (
        MetaClauseKind.COMMENCEMENT,
        re.compile(
            r"(?:tulee|tuli)\s+voimaan",
            re.IGNORECASE,
        ),
    ),
    # Delegation — "antaa tarkempia säännöksiä / määräyksiä"
    (
        MetaClauseKind.DELEGATION,
        re.compile(
            r"(?:antaa|voidaan\s+antaa)\s+(?:tarkempia?\s+)?(?:säännöksiä|määräyksiä)",
            re.IGNORECASE,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_meta_surface_clauses(text: str) -> list[SurfaceMetaClause]:
    """Extract meta/effect clauses from johtolause text as SurfaceMetaClause nodes.

    Handles:
    - Commencement: "Tämä laki tulee voimaan..."
    - Expiry: "Tämä laki on voimassa [until date]..."
    - Transition/applicability: siirtymäsäännös provisions
    - Delegation: valtuutus provisions

    Returns an empty list for purely structural johtolause text or empty input.

    The SurfaceWitness rule_id carries "meta_parse:<meta_kind>" for traceability.
    """
    if not text:
        return []

    sentences = _SENTENCE_SPLIT.split(text)
    result: list[SurfaceMetaClause] = []

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        for meta_kind, pattern in _META_PATTERNS:
            if pattern.search(sentence):
                # meta_kind is a MetaClauseKind enum from _META_PATTERNS
                witness = SurfaceWitness(rule_id=f"meta_parse:{meta_kind.value}")
                result.append(
                    SurfaceMetaClause(
                        kind=meta_kind,
                        text=sentence,
                        witness=witness,
                    )
                )
                break  # one classification per sentence

    return result
