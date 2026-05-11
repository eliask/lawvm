"""Finland-specific johto parsing and EffectIntent lowering.

This module owns the Finnish month/date heuristics and johtolause sentence
patterns that were previously embedded in ``lawvm.core.effect_lowering``.
Core now keeps only the generic ``EffectIntent`` -> ``TemporalEvent`` bridge.

API tier
--------
Finland-local parsing/lowering surface. Use this for johto/meta-clause
extraction and lowering; do not treat it as shared core authority.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import List, Literal, Optional

from lawvm.core.clause_ast import MetaClause
from lawvm.core.effect_intent import (
    Applicability,
    Commencement,
    EffectIntent,
    Expiry,
)
from lawvm.core.semantic_types import MetaClauseKind

UNSUPPORTED_META_CLAUSE_RULE_ID = "PARSE.META_CLAUSE_UNSUPPORTED"

UnsupportedMetaClauseReason = Literal[
    "delegation_clause_not_executable_effect",
    "unsupported_meta_clause_kind",
]


@dataclass(frozen=True)
class UnsupportedMetaClause:
    """Typed visibility record for parsed meta clauses with no executable carrier."""

    rule_id: str
    reason_code: UnsupportedMetaClauseReason
    clause_kind: str
    raw_text: str
    phase: str = "frontend_extraction"
    family: str = "unsupported_meta_clause"
    blocking: bool = False

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "reason_code": self.reason_code,
            "clause_kind": self.clause_kind,
            "raw_text": self.raw_text,
            "phase": self.phase,
            "family": self.family,
            "blocking": self.blocking,
        }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "tammikuuta": 1,
    "helmikuuta": 2,
    "maaliskuuta": 3,
    "huhtikuuta": 4,
    "toukokuuta": 5,
    "kesäkuuta": 6,
    "heinäkuuta": 7,
    "elokuuta": 8,
    "syyskuuta": 9,
    "lokakuuta": 10,
    "marraskuuta": 11,
    "joulukuuta": 12,
}


def _parse_fi_date(day: str, month_name: str, year: str) -> Optional[dt.date]:
    month = _MONTH_MAP.get(month_name.lower())
    if month is None:
        return None
    try:
        return dt.date(int(year), month, int(day))
    except ValueError:
        return None


def _extract_fi_date(text: str) -> Optional[dt.date]:
    m = re.search(
        r"(\d{1,2})\s+päivän[aä]\s+([a-zäöå]+)\s+(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return _parse_fi_date(m.group(1), m.group(2), m.group(3))
    m = re.search(
        r"(\d{1,2})\s+päivään\s+([a-zäöå]+)\s+(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return _parse_fi_date(m.group(1), m.group(2), m.group(3))
    return None


_CONTINGENT_PATTERNS = re.compile(
    r"asetuksella\s+säädettävänä\s+ajankohtana"
    r"|valtioneuvoston\s+(?:asetuksella|päätöksellä)"
    r"|erikseen\s+säädettävän[aä]",
    re.IGNORECASE,
)


def _lower_voimaantulo(raw: str) -> Optional[EffectIntent]:
    expiry_match = re.search(
        r"on\s+voimassa\s+.{0,60}?(\d{1,2})\s+päivään\s+([a-zäöå]+)\s+(\d{4})",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if expiry_match:
        expiry_date = _parse_fi_date(
            expiry_match.group(1),
            expiry_match.group(2),
            expiry_match.group(3),
        )
        return Expiry(expiry_date=expiry_date, raw_text=raw)

    is_commencement = bool(re.search(r"tulee\s+voimaan", raw, re.IGNORECASE))
    if not is_commencement:
        eff_date = _extract_fi_date(raw)
        if eff_date is not None:
            return Commencement(effective_date=eff_date, raw_text=raw)
        return None

    is_contingent = bool(_CONTINGENT_PATTERNS.search(raw))
    if is_contingent:
        return Commencement(is_contingent=True, raw_text=raw)

    eff_date = _extract_fi_date(raw)
    return Commencement(effective_date=eff_date, raw_text=raw)


def lower_meta_clause(clause: MetaClause) -> Optional[EffectIntent]:
    raw = clause.raw_text
    if clause.kind == MetaClauseKind.COMMENCEMENT:
        return _lower_voimaantulo(raw)
    if clause.kind == MetaClauseKind.EXPIRY:
        eff_date = _extract_fi_date(raw)
        if eff_date is not None:
            return Expiry(expiry_date=eff_date, raw_text=raw)
        return Expiry(raw_text=raw)
    if clause.kind == MetaClauseKind.TRANSITION:
        return Applicability(raw_text=raw)
    return None


def _unsupported_meta_clause_record(clause: MetaClause) -> UnsupportedMetaClause:
    reason_code: UnsupportedMetaClauseReason = (
        "delegation_clause_not_executable_effect"
        if clause.kind == MetaClauseKind.DELEGATION
        else "unsupported_meta_clause_kind"
    )
    return UnsupportedMetaClause(
        rule_id=UNSUPPORTED_META_CLAUSE_RULE_ID,
        reason_code=reason_code,
        clause_kind=clause.kind.value,
        raw_text=clause.raw_text,
    )


_META_SENTENCE_PATTERNS: List[tuple[MetaClauseKind, re.Pattern]] = [
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
    (
        MetaClauseKind.EXPIRY,
        re.compile(
            r"on\s+voimassa"
            r"|voimassaoloaika",
            re.IGNORECASE,
        ),
    ),
    (
        MetaClauseKind.COMMENCEMENT,
        re.compile(
            r"(?:tulee|tuli)\s+voimaan",
            re.IGNORECASE,
        ),
    ),
    (
        MetaClauseKind.DELEGATION,
        re.compile(
            r"(?:antaa|voidaan\s+antaa)\s+(?:tarkempia?\s+)?(?:säännöksiä|määräyksiä)",
            re.IGNORECASE,
        ),
    ),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÅ])")


def extract_meta_clauses(johto: str) -> List[MetaClause]:
    if not johto:
        return []
    sentences = _SENTENCE_SPLIT.split(johto)
    result: List[MetaClause] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        for kind, pattern in _META_SENTENCE_PATTERNS:
            if pattern.search(sentence):
                result.append(MetaClause(kind=kind, raw_text=sentence))
                break
    return result


def lower_johto_effects(
    johto: str,
    *,
    unsupported_out: Optional[List[UnsupportedMetaClause]] = None,
) -> List[EffectIntent]:
    intents: List[EffectIntent] = []
    for clause in extract_meta_clauses(johto):
        intent = lower_meta_clause(clause)
        if intent is not None:
            intents.append(intent)
        elif unsupported_out is not None:
            unsupported_out.append(_unsupported_meta_clause_record(clause))
    return intents
