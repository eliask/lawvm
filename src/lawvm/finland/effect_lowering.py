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
from lawvm.finland.fi_dates import (
    FiDateForm,
    match_fi_date,
    parse_fi_day_month_year,
)

UNSUPPORTED_META_CLAUSE_RULE_ID = "PARSE.META_CLAUSE_UNSUPPORTED"

UnsupportedMetaClauseReason = Literal[
    "delegation_clause_not_executable_effect",
    "unsupported_meta_clause_kind",
    "commencement_shape_no_effect_intent",
    "expiry_shape_no_effect_intent",
]

COMMENCEMENT_SHAPE_NO_EFFECT_RULE_ID = "PARSE.COMMENCEMENT_SHAPE_NO_EFFECT"


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

def _parse_fi_date(day: str, month_name: str, year: str) -> Optional[dt.date]:
    return parse_fi_day_month_year(day, month_name, year)


def _extract_fi_date(text: str) -> Optional[dt.date]:
    # Essive (commencement) form takes priority over allative (expiry) wherever
    # either occurs, mirroring the legacy two-pass search order; the shared
    # recognizer owns the date-token lexing for both.
    essive = match_fi_date(text, forms={FiDateForm.ESSIVE})
    if essive is not None:
        return essive.value
    allative = match_fi_date(text, forms={FiDateForm.ALLATIVE})
    if allative is not None:
        return allative.value
    return None


_CONTINGENT_PATTERNS = re.compile(
    r"asetuksella\s+säädettävänä\s+ajankohtana"
    r"|valtioneuvoston\s+(?:asetuksella|päätöksellä)"
    r"|erikseen\s+säädettävän[aä]",
    re.IGNORECASE,
)


def _lower_voimaantulo(raw: str) -> Optional[EffectIntent]:
    # lawvm-regex: owning_parser expiry-tail recognizer inside a COMMENCEMENT-classified MetaClause; produces a typed Expiry, no silent drop
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

    # lawvm-regex: owning_parser commencement-vs-other discriminator over the already-classified MetaClause text
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
    """Build a typed residual for a meta clause that lowered to no effect intent.

    A clause whose kind was *recognized* (COMMENCEMENT / EXPIRY) but produced no
    executable EffectIntent is a representation-regression risk: it would
    otherwise vanish silently. It is recorded with a distinct reason code and the
    commencement-shape rule id so the unrecognized-but-recognized-shape case is
    triageable without re-running extraction.
    """
    rule_id = UNSUPPORTED_META_CLAUSE_RULE_ID
    if clause.kind == MetaClauseKind.DELEGATION:
        reason_code: UnsupportedMetaClauseReason = (
            "delegation_clause_not_executable_effect"
        )
    elif clause.kind == MetaClauseKind.COMMENCEMENT:
        reason_code = "commencement_shape_no_effect_intent"
        rule_id = COMMENCEMENT_SHAPE_NO_EFFECT_RULE_ID
    elif clause.kind == MetaClauseKind.EXPIRY:
        reason_code = "expiry_shape_no_effect_intent"
        rule_id = COMMENCEMENT_SHAPE_NO_EFFECT_RULE_ID
    else:
        reason_code = "unsupported_meta_clause_kind"
    return UnsupportedMetaClause(
        rule_id=rule_id,
        reason_code=reason_code,
        clause_kind=clause.kind.value,
        raw_text=clause.raw_text,
    )


_META_SENTENCE_PATTERNS: List[tuple[MetaClauseKind, re.Pattern[str]]] = [
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
    unsupported_out: List[UnsupportedMetaClause],
) -> List[EffectIntent]:
    """Lower johto meta clauses to effect intents.

    Every recognized meta clause that lowers to no executable EffectIntent is
    appended to ``unsupported_out`` as a typed residual — the sink is mandatory
    so an unrecognized commencement/expiry shape can never drop silently.
    """
    intents: List[EffectIntent] = []
    for clause in extract_meta_clauses(johto):
        intent = lower_meta_clause(clause)
        if intent is not None:
            intents.append(intent)
        else:
            unsupported_out.append(_unsupported_meta_clause_record(clause))
    return intents
