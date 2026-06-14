"""Temporary-provision sunset detection for the U.S. federal dry-run (F2).

The dry-run kernel proves candidate amendatory ops against the USC annual-edition
oracle. Some edition-to-edition section changes are NOT produced by any public
law's textual amendment in the window: they are the **expiry of a temporary
provision** that reverts the section text to its prior permanent form.

The motivating case (Title 11, 2023->2024 window):

- §109(e): the SBRA chapter-13 debt-limit increase (raised by a temporary
  amendment, last extended by PL 117-151) **sunset** "2 years after June 21,
  2022" = June 21, 2024. The 2024 edition reverts §109 to its pre-increase
  permanent text (which equals the 2018 edition exactly).
- §1182(1): the subchapter-V "debtor" definition, expanded by the same temporary
  SBRA-era amendment, reverts on the same sunset date to "means a small business
  debtor".

At the amendment layer the dry-run correctly emits ``missing_source`` (no public
law in the window amends these sections), but the real mechanism is temporal.
This module distinguishes the two so the dry-run can reclassify a genuine sunset
reversion (``sunset_reversion``) from a still-un-lowered amendment
(``missing_source``).

Honest scope (Prime Directive)
------------------------------
This is **detection + classification using the editions and their notes as
witnesses**, never a repair-to-oracle and never a replay claim. A reversion is
asserted ONLY with evidence:

  (a) the after-text matches an EARLIER permanent edition's text (the prior
      permanent form the section reverts to), AND/OR
  (b) a sunset note (effective/termination/reversion language) whose computed
      sunset date falls inside the window.

When neither channel fires, this module returns ``None`` (no claim) — the
dry-run keeps ``missing_source``. When the section changed but the evidence is
only partial/ambiguous (e.g. a sunset note exists but its date cannot be
resolved into the window, or reversion language without any prior-edition or
quoted-text anchor), a typed ``SunsetFinding`` is emitted rather than guessing a
reversion. Full temporal-replay materialization of the reversion (rebuilding the
permanent version from a temporary overlay's expiry) is the documented next
step; here the prior permanent edition IS the materialized witness.

The temporary provision is modelled with the shared core temporal types
(:class:`lawvm.core.ir.ProvisionVersion` with ``variant_kind="temporary"`` and an
``expires`` date, reverting to a ``permanent`` version), not a bespoke model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from lawvm.core.comparison_normalization import normalize_inline_comparison_text
from lawvm.core.ir import ProvisionVersion
from lawvm.us_federal.source_tree import UscSection, UscSectionNote

# --- Stable rule-id vocabulary. ------------------------------------------------

# A changed section reclassified from missing_source to a temporal sunset
# reversion, with a temporal witness.
US_SUNSET_REVERSION_RULE_ID = "us_sunset_temporary_provision_reverted_to_prior_permanent"
# A changed section that carries sunset/termination language but the evidence is
# insufficient to assert a reversion (date not resolvable into window, or no
# prior-permanent / quoted-text anchor). Emitted as a finding, never a claim.
US_SUNSET_AMBIGUOUS_RULE_ID = "us_sunset_temporal_note_present_but_reversion_unproven"

# The new disposition the dry-run carries for a proven sunset reversion.
DISPOSITION_SUNSET_REVERSION = "sunset_reversion"

# Note-head labels that carry temporal mechanics. Matched case-insensitively as a
# prefix of the normalized head.
_TEMPORAL_NOTE_HEAD_PREFIXES = (
    "effective date",
    "effective and termination date",
    "termination date",
    "applicability",
)

# Reversion language in a note body: the temporary amendment expires and the text
# returns to the prior permanent form.
_REVERSION_PHRASES = (
    "to read as it read",
    "as it read on the day before",
    "prior to reversion",
    "after reversion",
    "ceased to be effective",
    "ceased to have effect",
    "shall cease to be effective",
    "reverted",
    "reversion",
)

# "Prior to amendment, text read as follows: \"...\"" — the quoted prior text the
# section reverts to. The prior-text block is delimited by DOUBLE quotes
# (straight ``"`` or curly ``“ ”``); inner single quotes (``'debtor'``) are
# content, so the close is the next double quote (non-greedy), not the next single
# quote — this captures the whole prior-text block without overshooting into later
# quoted matter in the same note body.
_PRIOR_TEXT_QUOTE_RE = re.compile(
    "prior to amendment[^\"“]*?[\"“](?P<quote>[^\"”]+)[\"”]",
    re.IGNORECASE,
)
# A quoted prior-text witness must be substantial (not a single stray word like
# an inner ``'debtor'``) before it counts as reversion evidence.
_MIN_QUOTE_WORDS = 4
# Quote characters stripped from BOTH the note quote and the after-text before the
# substring witness test, so straight/curly/single/double mismatches around an
# inner term do not defeat an otherwise-exact match.
_QUOTE_CHARS = "\"'“”‘’"

# "... effective on the date that is N years after MONTH DD, YYYY" — the SBRA
# re-extension sunset form (PL 117-151 §2(i)(1)).
_N_YEARS_AFTER_RE = re.compile(
    r"effective\s+on\s+the\s+date\s+that\s+is\s+(?P<n>\d+)\s+years?\s+after\s+"
    r"(?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})",
    re.IGNORECASE,
)
# "effective MONTH DD, YYYY" / "effective ... MONTH DD, YYYY" — a plain explicit
# sunset/effective date.
_EXPLICIT_DATE_RE = re.compile(
    r"effective\s+(?:[A-Za-z0-9 ,]*?\b)?(?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _norm(text: str) -> str:
    return normalize_inline_comparison_text(text)


def _quote_blind(text: str) -> str:
    """Normalized text with all quote characters removed, for the quote witness."""
    return _norm(text.translate({ord(ch): None for ch in _QUOTE_CHARS}))


def _iso(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_sunset_dates(text: str) -> tuple[str, ...]:
    """All resolvable sunset/effective dates in a note body, as ISO strings.

    Handles the SBRA "N years after MONTH DD, YYYY" re-extension form and the
    plain "effective MONTH DD, YYYY" form. Never guesses: only month names in the
    known table and well-formed dates are returned.
    """
    dates: list[str] = []
    for m in _N_YEARS_AFTER_RE.finditer(text):
        month = _MONTHS.get(m.group("month").lower())
        if month is None:
            continue
        try:
            base_year = int(m.group("year"))
            day = int(m.group("day"))
            n = int(m.group("n"))
        except ValueError:
            continue
        dates.append(_iso(base_year + n, month, day))
    for m in _EXPLICIT_DATE_RE.finditer(text):
        month = _MONTHS.get(m.group("month").lower())
        if month is None:
            continue
        try:
            dates.append(_iso(int(m.group("year")), month, int(m.group("day"))))
        except ValueError:
            continue
    # Stable de-dup preserving order.
    return tuple(dict.fromkeys(dates))


def _has_reversion_language(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in _REVERSION_PHRASES)


def _temporal_notes(section: UscSection) -> tuple[UscSectionNote, ...]:
    """Note blocks whose head carries temporal mechanics, plus the Amendments
    block (which carries the "to read as it read"/"Prior to amendment" reversion
    language)."""
    out: list[UscSectionNote] = []
    for note in section.notes:
        head = note.head.lower()
        if head.startswith("amendments") or head.startswith("amendment"):
            out.append(note)
            continue
        if any(head.startswith(p) for p in _TEMPORAL_NOTE_HEAD_PREFIXES):
            out.append(note)
    return tuple(out)


# ---------------------------------------------------------------------------
# Typed results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SunsetWitness:
    """The temporal evidence backing a sunset-reversion classification.

    ``temporary_version`` is the expiring temporary provision (variant_kind
    ``temporary``, with ``expires`` = the sunset date); ``permanent_version`` is
    the prior permanent form it reverts to (variant_kind ``permanent``), seeded
    from the matching earlier edition's text when channel (a) fires. ``note_text``
    is the verbatim sunset note; ``sunset_date`` is the ISO sunset date when
    resolvable; ``reverts_to_edition_year`` is the earlier edition whose text the
    after-text matches (channel a), or empty.
    """

    sunset_date: str
    note_head: str
    note_text: str
    reverts_to_edition_year: str
    quoted_prior_text_matches: bool
    temporary_version: Optional[ProvisionVersion]
    permanent_version: Optional[ProvisionVersion]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "sunset_date": self.sunset_date,
            "note_head": self.note_head,
            "note_text": self.note_text,
            "reverts_to_edition_year": self.reverts_to_edition_year,
            "quoted_prior_text_matches": self.quoted_prior_text_matches,
            "temporary_version_kind": (
                self.temporary_version.variant_kind if self.temporary_version else ""
            ),
            "temporary_version_expires": (
                self.temporary_version.expires if self.temporary_version else ""
            ),
            "permanent_version_kind": (
                self.permanent_version.variant_kind if self.permanent_version else ""
            ),
        }


@dataclass(frozen=True)
class SunsetClassification:
    """A proven sunset reversion for one section across an edition window."""

    title: int
    section: str
    before_year: str
    after_year: str
    rule_id: str
    disposition: str
    witness: SunsetWitness

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "section": self.section,
            "before_year": self.before_year,
            "after_year": self.after_year,
            "rule_id": self.rule_id,
            "disposition": self.disposition,
            "witness": self.witness.to_jsonable(),
        }


@dataclass(frozen=True)
class SunsetFinding:
    """An ambiguous temporal residual: sunset language without enough evidence.

    Self-evidencing: carries the offending note text so the gap is inspectable.
    Never a reversion claim — the dry-run keeps ``missing_source``.
    """

    title: int
    section: str
    rule_id: str
    reason: str
    note_head: str = ""
    note_text: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "section": self.section,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "note_head": self.note_head,
            "note_text": self.note_text,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class SunsetResult:
    """Outcome of consulting the detector for one section/window.

    Exactly one of ``classification`` / ``finding`` is set when the section
    carries temporal signal; both are ``None`` when there is no temporal signal
    at all (the dry-run then keeps its amendment-layer disposition unchanged).
    """

    classification: Optional[SunsetClassification] = None
    finding: Optional[SunsetFinding] = None

    @property
    def is_reversion(self) -> bool:
        return self.classification is not None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _build_versions(
    *,
    after_year: str,
    sunset_date: str,
    reverts_to_year: str,
    permanent_text: str,
) -> tuple[Optional[ProvisionVersion], Optional[ProvisionVersion]]:
    """Model the expiring temporary provision + the prior permanent it reverts to.

    Reuses the core :class:`ProvisionVersion`. The temporary version carries
    ``variant_kind='temporary'`` and ``expires`` = the sunset date; the permanent
    version (seeded from the matching earlier edition when known) carries
    ``variant_kind='permanent'``. Effective dates are the editions' Jan-1 anchors
    (the annual edition is the coarsest reliable date carrier here) — full
    day-precision commencement is the documented temporal-replay next step.
    """
    temporary: Optional[ProvisionVersion] = None
    permanent: Optional[ProvisionVersion] = None
    if reverts_to_year:
        permanent = ProvisionVersion(
            effective=f"{reverts_to_year}-01-01",
            variant_kind="permanent",
            content_hash=_norm(permanent_text)[:64],
        )
    # The temporary overlay was in force up to the sunset date; after expiry the
    # permanent form (the after-edition text) is in force. Effective is unknown at
    # day-precision from the editions alone; anchor to the prior-edition year when
    # known, else the sunset year.
    temp_effective = (
        f"{reverts_to_year}-01-01"
        if reverts_to_year
        else f"{int(after_year) - 1:04d}-01-01"
    )
    if sunset_date and temp_effective <= sunset_date:
        temporary = ProvisionVersion(
            effective=temp_effective,
            expires=sunset_date,
            variant_kind="temporary",
        )
    return temporary, permanent


def classify_sunset_reversion(
    *,
    title: int,
    section: str,
    before_year: str,
    after_year: str,
    before_text: str,
    after_text: str,
    after_section: UscSection,
    prior_edition_texts: Mapping[str, str],
) -> SunsetResult:
    """Decide whether ``before_year``->``after_year`` change to ``section`` is a sunset.

    Parameters
    ----------
    before_text, after_text
        The section's normalized-or-raw statutory text in the two editions.
    after_section
        The parsed after-edition section (carries the temporal notes).
    prior_edition_texts
        ``{year: statutory_text}`` for editions at or before ``before_year`` that
        can serve as the prior-permanent reversion target (channel a). The before
        edition itself is excluded by the caller (it is the temporary form).

    Returns
    -------
    SunsetResult
        ``classification`` set when a reversion is proven (channel a and/or b),
        ``finding`` set when temporal signal exists but is insufficient, both
        ``None`` when there is no temporal signal.
    """
    # No actual change -> nothing for the sunset layer.
    if _norm(before_text) == _norm(after_text):
        return SunsetResult()

    temporal_notes = _temporal_notes(after_section)

    # --- Channel (a): the after-text matches an earlier permanent edition. ------
    reverts_to_year = ""
    permanent_text = ""
    after_norm = _norm(after_text)
    after_quote_blind = _quote_blind(after_text)
    for year in sorted(prior_edition_texts):
        if _norm(prior_edition_texts[year]) == after_norm and after_norm:
            reverts_to_year = year
            permanent_text = prior_edition_texts[year]
            break

    # --- Channel (b): a sunset note with a date inside the window + reversion. ---
    window_lo = f"{before_year}-01-01"
    window_hi = f"{int(after_year):04d}-12-31"
    sunset_date = ""
    sunset_note: Optional[UscSectionNote] = None
    reversion_language = False
    quoted_matches = False

    for note in temporal_notes:
        body = note.text
        if _has_reversion_language(body):
            reversion_language = True
        # "Prior to amendment, text read as follows: \"X\"" where X matches after.
        for qm in _PRIOR_TEXT_QUOTE_RE.finditer(body):
            quote = qm.group("quote")
            qb = _quote_blind(quote)
            # Require a substantial quoted block (quote-blind), present in the
            # quote-blind after-text. A single inner word is not evidence.
            if qb and len(qb.split()) >= _MIN_QUOTE_WORDS and qb in after_quote_blind:
                quoted_matches = True
        for date in _parse_sunset_dates(body):
            if window_lo <= date <= window_hi:
                # Prefer the first in-window date paired with a temporal note.
                if not sunset_date:
                    sunset_date = date
                    sunset_note = note

    channel_a = bool(reverts_to_year)
    # Channel (b) requires an in-window sunset date AND a reversion anchor
    # (either reversion language or a quoted prior-text match) — a bare effective
    # date without reversion semantics is an ordinary amendment, not a sunset.
    channel_b = bool(sunset_date) and (reversion_language or quoted_matches)

    if channel_a or channel_b:
        note_head = sunset_note.head if sunset_note is not None else ""
        note_text = sunset_note.text if sunset_note is not None else ""
        if not note_text and reversion_language:
            # Reversion language lives in the Amendments block; surface it.
            for note in temporal_notes:
                if _has_reversion_language(note.text):
                    note_head, note_text = note.head, note.text
                    break
        temporary_version, permanent_version = _build_versions(
            after_year=after_year,
            sunset_date=sunset_date,
            reverts_to_year=reverts_to_year,
            permanent_text=permanent_text,
        )
        witness = SunsetWitness(
            sunset_date=sunset_date,
            note_head=note_head,
            note_text=note_text,
            reverts_to_edition_year=reverts_to_year,
            quoted_prior_text_matches=quoted_matches,
            temporary_version=temporary_version,
            permanent_version=permanent_version,
        )
        return SunsetResult(
            classification=SunsetClassification(
                title=title,
                section=section,
                before_year=before_year,
                after_year=after_year,
                rule_id=US_SUNSET_REVERSION_RULE_ID,
                disposition=DISPOSITION_SUNSET_REVERSION,
                witness=witness,
            )
        )

    # --- Temporal signal present but insufficient -> typed finding, no claim. ----
    if temporal_notes and (reversion_language or any(_parse_sunset_dates(n.text) for n in temporal_notes)):
        # Pick the most informative note to evidence the finding.
        ev_note = next(
            (n for n in temporal_notes if _has_reversion_language(n.text)),
            temporal_notes[0],
        )
        if sunset_date and not (reversion_language or quoted_matches):
            reason = (
                "an in-window sunset/effective date is present but no reversion "
                "language or matching prior-permanent text anchors a reversion; "
                "treated as an ordinary amendment, not a sunset"
            )
        elif reversion_language and not sunset_date:
            reason = (
                "reversion language is present but no sunset date resolves inside "
                "the edition window and no earlier edition matches the after-text"
            )
        else:
            reason = (
                "temporal note present but neither an in-window sunset date nor a "
                "prior-permanent text match proves a reversion"
            )
        return SunsetResult(
            finding=SunsetFinding(
                title=title,
                section=section,
                rule_id=US_SUNSET_AMBIGUOUS_RULE_ID,
                reason=reason,
                note_head=ev_note.head,
                note_text=ev_note.text,
                detail={
                    "in_window_sunset_date": sunset_date,
                    "reversion_language": reversion_language,
                    "quoted_prior_text_matches": quoted_matches,
                },
            )
        )

    return SunsetResult()


__all__ = [
    "US_SUNSET_REVERSION_RULE_ID",
    "US_SUNSET_AMBIGUOUS_RULE_ID",
    "DISPOSITION_SUNSET_REVERSION",
    "SunsetWitness",
    "SunsetClassification",
    "SunsetFinding",
    "SunsetResult",
    "classify_sunset_reversion",
]
