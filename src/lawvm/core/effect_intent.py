"""EffectIntent — typed representations of non-structural legal effects.

These types capture amendment clauses that change the *temporal or conditional
status* of law rather than its structural content.  They are the counterpart to
CanonicalIntent (which covers structural tree operations) for the class of
clauses that ClauseAST represents as MetaClause nodes.

Five effect kinds are defined, modelling common commencement, expiry,
applicability, suspension, and revival clause patterns:

  Commencement   — this instrument or provision comes into force on a date
  Expiry         — this instrument or provision remains in force until a date
  Suspension     — a provision is temporarily suspended
  Applicability  — applicability scope restriction or extension
  Revival        — re-entry into force of a previously expired provision

Usage
-----
    from lawvm.core.effect_intent import EffectIntent, Commencement, Expiry
    from some_frontend.effect_lowering import lower_meta_clause

    intent = lower_meta_clause(meta_clause)
    if isinstance(intent, Commencement):
        # use intent.effective_date
        ...

Relation to CanonicalIntent
---------------------------
CanonicalIntent covers structural tree ops (Replace, Insert, Repeal, ...).
EffectIntent covers temporal/conditional meta-effects. The two are disjoint
on purpose: EffectIntent is parse-layer meaning, while executable temporal
behavior lives on `TemporalEvent`. The shared execution rail is:
`EffectIntent` -> `TemporalEvent` -> timeline/PIT execution.

API tier
--------
Parse-layer contract for temporal and conditional effects.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Union


# ---------------------------------------------------------------------------
# EffectKind discriminant
# ---------------------------------------------------------------------------


class EffectKind(StrEnum):
    """Discriminant tag for the parse-layer EffectIntent sum type."""
    COMMENCEMENT = "commencement"
    EXPIRY = "expiry"
    SUSPENSION = "suspension"
    APPLICABILITY = "applicability"
    REVIVAL = "revival"


# ---------------------------------------------------------------------------
# EffectIntent variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Commencement:
    """The amendment (or provision) enters into force on ``effective_date``.

    Corresponds to entry-into-force clauses and their XML equivalents
    (dateEntryIntoForce metadata).

    effective_date
        The date on which the amendment enters into force.  None when the
        commencement is contingent (decree-set) and cannot be determined
        from the text alone.

    is_contingent
        True when the text indicates a decree-set or conditional commencement
        date, such as a date to be appointed by later instrument.

    raw_text
        The source clause text for traceability.

    Parse-layer status
        Parse-layer effect carrier only. Operational temporal execution lives
        on `TemporalEvent`.
    """
    kind: EffectKind = EffectKind.COMMENCEMENT
    effective_date: Optional[dt.date] = None
    is_contingent: bool = False
    raw_text: str = ""


@dataclass(frozen=True)
class Expiry:
    """The amendment (or provision) expires on ``expiry_date``.

    Corresponds to fixed-expiry clauses that set an explicit end-of-force date.

    expiry_date
        The date on which the amendment ceases to be in force.  None when the
        expiry date cannot be determined from the clause text.

    raw_text
        The source clause text for traceability.

    Parse-layer status
        Parse-layer effect carrier only. Operational temporal execution lives
        on `TemporalEvent`.
    """
    kind: EffectKind = EffectKind.EXPIRY
    expiry_date: Optional[dt.date] = None
    raw_text: str = ""


@dataclass(frozen=True)
class Suspension:
    """A provision is temporarily suspended (not in force) for a period.

    This models clauses that explicitly suspend the application of a provision
    rather than repealing it.  Suspensions are relatively uncommon in law but
    occur in exceptional circumstances (wartime, emergency, etc.).

    suspended_until
        The end date of the suspension period.  None when not determinable.

    raw_text
        The source clause text for traceability.

    Parse-layer status
        Parse-layer effect carrier only. Operational temporal execution lives
        on `TemporalEvent`.
    """
    kind: EffectKind = EffectKind.SUSPENSION
    suspended_until: Optional[dt.date] = None
    raw_text: str = ""


@dataclass(frozen=True)
class Applicability:
    """An applicability scope restriction or extension.

    Covers transition and applicability clauses that constrain when or where
    the new law applies.

    raw_text
        The source clause text for traceability (full text of the clause,
        since applicability clauses are diverse and do not reduce to a
        single structured field).

    Parse-layer status
        Parse-layer effect carrier only. Operational temporal execution lives
        on `TemporalEvent`.
    """
    kind: EffectKind = EffectKind.APPLICABILITY
    raw_text: str = ""


@dataclass(frozen=True)
class Revival:
    """A previously expired provision re-enters into force.

    Some legal systems occasionally revive (bring back into force) provisions
    that had lapsed.  This is distinct from re-enactment (which would be a
    structural INSERT or REPLACE in CanonicalIntent).

    revived_from
        The date from which the provision is in force again.  None when not
        determinable.

    raw_text
        The source clause text for traceability.

    Parse-layer status
        Parse-layer effect carrier only. Operational temporal execution lives
        on `TemporalEvent`.
    """
    kind: EffectKind = EffectKind.REVIVAL
    revived_from: Optional[dt.date] = None
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Top-level union
# ---------------------------------------------------------------------------

# Parse-layer sum type. Keep for extraction/lowering; executable temporal
# authority lives on `TemporalEvent`.
EffectIntent = Union[Commencement, Expiry, Suspension, Applicability, Revival]
