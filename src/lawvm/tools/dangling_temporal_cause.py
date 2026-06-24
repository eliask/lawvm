"""``dangling-refs --temporal-cause`` — split the DANGLING class by CAUSE.

WHAT THIS IS. The corpus-wide DANGLING-reference projection
(:mod:`lawvm.tools.dangling_references`) classifies every RESOLVED cross-reference
into PRESENT / DANGLING / EXISTENCE_UNKNOWN against the target act's CURRENT
consolidated text-state. A ``DANGLING`` verdict means "the target act IS
materialized but the cited provision resolves to nothing". That single verdict
hides a temporal-integrity nuance the demo needs surfaced: WHY does the cited
provision resolve to nothing?

This module adds a SECOND classification dimension on top of an already-computed
DANGLING set. For each DANGLING row it consults the target act's current
consolidated XML for a POSITIVE repeal signal and lands the row in exactly one of
two causes (tag-don't-guess — never a guessed third):

* ``DANGLING_REPEALED_TARGET`` — a repeal NOTE is present in the target act's
  consolidated text covering the cited provision (Finlex renders a repealed unit
  in place as an italic note ``"<spec> § on kumottu L:lla <ref>DATE/ACT</ref>"``).
  The note carries the AMENDING ACT + DATE, so the repeal is EVIDENCED, not
  inferred. This is the live reference-integrity concern the partner asked for: a
  law citing a provision that no longer has force, with the repeal that removed it
  named. The matcher is RANGE-AWARE: Finlex frequently collapses a repealed span
  into one note (``"67–84 § on kumottu …"``, ``"3 a–4 § on kumottu …"``); a cited
  section that falls inside such a span is matched and attributed to that repeal.

* ``DANGLING_CAUSE_UNDETERMINED`` — the cited provision is absent from the current
  consolidated text and NO repeal note covers it. From the AS-OF-NOW consolidated
  oracle ALONE we CANNOT distinguish "repealed-without-an-in-place note" from
  "renumbered away" from "never materialized at this address". We REFUSE to guess
  a cause: this is the honest residual, NOT a claim that the provision never
  existed. (Determining it would require the heavier as-of-citing replay path —
  declared out of scope here, exactly as the parent DANGLING claim declares its
  own as-of-now boundary.)

WHY THIS IS HONEST AND BOUNDED. The repeal note is a POSITIVE, independently
checkable surface fact in the published Finlex text — a reader can open the act
and see ``"67–84 § on kumottu L:lla 16.4.1987/411"`` for themselves. Absence of a
note is NOT evidence of never-existence (Finlex does not always leave an in-place
note for an old renumber/repeal), so an unmatched DANGLING row is reported as
UNDETERMINED, never as ``NEVER_MATERIALIZED``. The split therefore over-reports
UNKNOWN rather than over-claiming either cause — the cardinal no-false-positive
discipline the parent tool already enforces, carried one level deeper.

This module adds NO new authority over the parent DANGLING claim: it is a
read-only enrichment of an existing DANGLING set, computed against the same
consolidated oracle XML the parent reads.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.tools.dangling_references import (
    STATUS_DANGLING,
    DanglingReferenceReport,
    DanglingReferenceRow,
)

_SCHEMA_CAUSE_ROW = "lawvm.dangling_cause_row.v1"
_SCHEMA_CAUSE_REPORT = "lawvm.dangling_cause_report.v1"

# ---------------------------------------------------------------------------
# The closed TWO-way cause set (tag-don't-guess; an out-of-set value is a typed
# finding, never a silently widened set). There is deliberately NO positive
# "never materialized" cause: the as-of-now consolidated oracle cannot evidence
# never-existence, so a non-repealed DANGLING is UNDETERMINED, never guessed.
# ---------------------------------------------------------------------------
CAUSE_REPEALED_TARGET = "DANGLING_REPEALED_TARGET"
CAUSE_UNDETERMINED = "DANGLING_CAUSE_UNDETERMINED"

DANGLING_CAUSES: frozenset[str] = frozenset(
    {CAUSE_REPEALED_TARGET, CAUSE_UNDETERMINED}
)


class DanglingCauseError(ValueError):
    """A dangling-cause object violates a v1 invariant (out-of-set cause)."""


# ---------------------------------------------------------------------------
# Repeal-note recognition over the target act's consolidated XML.
# ---------------------------------------------------------------------------

# A Finlex in-place repeal note. The unit spec may be a single number ("84",
# "10 a") OR a range ("67–84", "3 a–4 §"); the dash may be en/em/hyphen. The
# amending act + date is captured from the first id-shaped token after the cue
# (it sits inside the following ``<ref>…</ref>`` in the rendered text, which the
# ``(?:<[^>]*>)*`` skips past). The cue verb form "on kumottu" (and the rarer
# "kumotaan") is matched; the authority is "L:lla" (lailla) / "A:lla"/"asetuksella".
_REPEAL_NOTE = re.compile(
    r"(?P<spec>[0-9][0-9 a-zà-ÿ]*(?:\s*[–—-]\s*[0-9][0-9 a-zà-ÿ]*)?)\s*"
    r"(?P<unit>§|momentti(?:a)?|luku(?:a)?|kohta|kohdat)\s+"
    r"on\s+kumottu\s+(?:L:lla|A:lla|asetuksella|lailla)\s*"
    r"(?:<[^>]*>)*\s*(?P<evid>\d[0-9.]*/\d+)",
    re.IGNORECASE,
)

#: Closed unit vocabulary the note recognizer emits (for legibility in evidence).
_UNIT_SECTION = "§"
_UNIT_MOMENTTI = "momentti"
_UNIT_LUKU = "luku"
_UNIT_KOHTA = "kohta"


@dataclass(frozen=True, slots=True)
class RepealNoteEvidence:
    """One parsed in-place repeal note from a target act's consolidated text.

    Attributes:
        spec: The unit spec verbatim as it appeared (e.g. ``"84"``, ``"67–84"``,
            ``"3 a–4"``) — the independently-checkable surface.
        unit: The normalized unit class (``§`` / ``momentti`` / ``luku`` / ``kohta``).
        amending_act: The amending act id + date as Finlex rendered it
            (e.g. ``"16.4.1987/411"``) — the repeal evidence.
    """

    spec: str
    unit: str
    amending_act: str


def _split_alnum(token: str) -> Optional[tuple[int, str]]:
    """Parse a section token (``"10a"`` / ``"10 a"`` / ``"84"``) into ``(num, letter)``.

    Returns ``None`` for a token that is not a leading-number-plus-optional-letter
    form (so the matcher fails CLOSED on anything it cannot order).
    """
    t = token.replace(" ", "").lower()
    m = re.match(r"(\d+)([a-zà-ÿ]*)$", t)
    if not m:
        return None
    return (int(m.group(1)), m.group(2))


def _spec_covers_section(spec: str, section_label: str) -> bool:
    """True iff ``section_label`` falls within the repeal-note ``spec``.

    A single-token spec matches only the identical section. A ``LOW–HIGH`` range
    spec matches any section ordered within ``[LOW, HIGH]`` (numeric first, letter
    suffix as tiebreak, so ``3 a`` < ``4`` and ``67`` <= ``84``). Fails CLOSED:
    an unparseable spec or section yields ``False`` (no guessed coverage).
    """
    sec = _split_alnum(section_label)
    if sec is None:
        return False
    parts = re.split(r"\s*[–—-]\s*", spec.strip())
    if len(parts) == 1:
        end = _split_alnum(parts[0])
        return end == sec
    if len(parts) != 2:
        return False
    lo = _split_alnum(parts[0])
    hi = _split_alnum(parts[1])
    if lo is None or hi is None:
        return False

    def _key(x: tuple[int, str]) -> tuple[int, str]:
        return (x[0], x[1] or "")

    return _key(lo) <= _key(sec) <= _key(hi)


def _normalize_unit(raw: str) -> str:
    r = raw.lower()
    if r.startswith("§"):
        return _UNIT_SECTION
    if r.startswith("moment"):
        return _UNIT_MOMENTTI
    if r.startswith("luku") or r.startswith("lukua"):
        return _UNIT_LUKU
    if r.startswith("kohta") or r.startswith("kohdat"):
        return _UNIT_KOHTA
    return raw


def parse_repeal_notes(xml_text: str) -> tuple[RepealNoteEvidence, ...]:
    """Extract every in-place repeal note from a target act's consolidated XML text.

    Pure function of the act's serialized XML; the result is the set of evidenced
    repeals Finlex left in place. Order follows appearance in the text.
    """
    out: list[RepealNoteEvidence] = []
    for m in _REPEAL_NOTE.finditer(xml_text):
        out.append(
            RepealNoteEvidence(
                spec=m.group("spec").strip(),
                unit=_normalize_unit(m.group("unit")),
                amending_act=m.group("evid"),
            )
        )
    return tuple(out)


def _cited_section_label(target_statute_id: str, target_provision_ref_str: str) -> Optional[str]:
    """The section-granularity label of the cited provision, or ``None``.

    Mirrors the parent tool's locator stripping (the statute id may itself carry a
    slash) and drops chapter / momentti / kohta / alakohta tail — the repeal-note
    match is at SECTION granularity, the same granularity the DANGLING verdict was
    issued at. Returns the last section token (so a chapter-qualified ``ch3/20a``
    yields ``20a``).
    """
    rest = target_provision_ref_str
    prefix = target_statute_id + "/"
    if rest.startswith(prefix):
        rest = rest[len(prefix) :]
    elif rest == target_statute_id:
        return None
    section: Optional[str] = None
    for tok in (t for t in rest.split("/") if t):
        if tok.startswith("ch"):
            continue
        if tok.startswith("k") or tok.startswith("s"):
            # kohta (k) / alakohta (s) tail — below section granularity.
            continue
        if tok.isdigit() and section is not None:
            # bare momentti (subsection) after a section — below granularity.
            continue
        section = tok
    return section


# ---------------------------------------------------------------------------
# The cause oracle — repeal-note evidence over the consolidated oracle XML.
# ---------------------------------------------------------------------------


class _StoreLike(Protocol):
    def read_oracle(self, sid: str, /) -> Optional[bytes]: ...


class RepealNoteCauseOracle:
    """Attribute a DANGLING row to a repeal (with evidence) or UNDETERMINED.

    Reads the target act's current consolidated XML (the Finlex oracle, free — no
    replay), extracts its in-place repeal notes once per act (cached), and tests
    whether any note covers the cited section. A match is
    ``DANGLING_REPEALED_TARGET`` with the matched :class:`RepealNoteEvidence`; no
    match is ``DANGLING_CAUSE_UNDETERMINED`` (never a guessed never-existed cause).
    """

    def __init__(self, store: _StoreLike) -> None:
        self._store = store
        self._notes_cache: dict[str, tuple[RepealNoteEvidence, ...]] = {}

    def _notes(self, statute_id: str) -> tuple[RepealNoteEvidence, ...]:
        cached = self._notes_cache.get(statute_id)
        if cached is not None:
            return cached
        try:
            xml = self._store.read_oracle(statute_id)
        except Exception:
            xml = None
        if xml is None:
            result: tuple[RepealNoteEvidence, ...] = ()
        else:
            result = parse_repeal_notes(xml.decode("utf-8", "replace"))
        self._notes_cache[statute_id] = result
        return result

    def classify(
        self, target_statute_id: str, target_provision_ref_str: str
    ) -> tuple[str, Optional[RepealNoteEvidence]]:
        """Return ``(cause, evidence_or_None)`` for one DANGLING target.

        Tag-don't-guess: a covered repeal note is REPEALED with evidence; anything
        else (no section label, no note, no covering note) is UNDETERMINED with no
        fabricated cause.
        """
        section = _cited_section_label(target_statute_id, target_provision_ref_str)
        if section is None:
            return (CAUSE_UNDETERMINED, None)
        for note in self._notes(target_statute_id):
            if _spec_covers_section(note.spec, section):
                return (CAUSE_REPEALED_TARGET, note)
        return (CAUSE_UNDETERMINED, None)


# ---------------------------------------------------------------------------
# The cause-split report (additive over a computed DanglingReferenceReport).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DanglingCauseRow:
    """``lawvm.dangling_cause_row.v1`` — one DANGLING row attributed to a cause."""

    source_statute_id: str
    source_provision_ref_str: str
    target_statute_id: str
    target_provision_ref_str: str
    cause: str
    repeal_spec: Optional[str] = None
    repeal_unit: Optional[str] = None
    amending_act: Optional[str] = None

    def __post_init__(self) -> None:
        if self.cause not in DANGLING_CAUSES:
            raise DanglingCauseError(
                f"DanglingCauseRow.cause must be one of {sorted(DANGLING_CAUSES)!r}, "
                f"got {self.cause!r} — an out-of-set cause is a typed finding, never "
                f"a silently widened set (target {self.target_provision_ref_str})"
            )
        if self.cause == CAUSE_REPEALED_TARGET and self.amending_act is None:
            raise DanglingCauseError(
                "DanglingCauseRow with cause DANGLING_REPEALED_TARGET must carry the "
                "amending_act evidence — a repealed verdict without the citing repeal "
                f"is a guess (target {self.target_provision_ref_str})"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_CAUSE_ROW,
            "source_statute_id": nfc(self.source_statute_id),
            "source_provision_ref_str": nfc(self.source_provision_ref_str),
            "target_statute_id": nfc(self.target_statute_id),
            "target_provision_ref_str": nfc(self.target_provision_ref_str),
            "cause": self.cause,
            "repeal_spec": nfc(self.repeal_spec) if self.repeal_spec is not None else None,
            "repeal_unit": self.repeal_unit,
            "amending_act": nfc(self.amending_act) if self.amending_act is not None else None,
        }


@dataclass(frozen=True, slots=True)
class DanglingCauseReport:
    """``lawvm.dangling_cause_report.v1`` — the DANGLING set split by temporal cause.

    Fields:

    * ``total_dangling`` — every DANGLING row classified (== the parent report's
      ``dangling`` count; totality is enforced).
    * ``repealed_target`` / ``undetermined`` — the two-way counts (sum to total).
    * ``repealed_rows`` — the EVIDENCED repealed witnesses (each carries the
      amending act + date), sorted deterministically.
    * ``undetermined_targets`` — distinct target acts with at least one
      undetermined dangling row, for legibility (no per-row witness needed: an
      undetermined row carries no new evidence beyond the parent DANGLING row).
    """

    total_dangling: int
    repealed_target: int
    undetermined: int
    repealed_rows: tuple[DanglingCauseRow, ...] = field(default_factory=tuple)
    undetermined_targets: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.repealed_target + self.undetermined != self.total_dangling:
            raise DanglingCauseError(
                "DanglingCauseReport totality violated: repealed_target+undetermined="
                f"{self.repealed_target + self.undetermined} != total_dangling="
                f"{self.total_dangling}; a DANGLING row escaped the cause split"
            )
        if len(self.repealed_rows) != self.repealed_target:
            raise DanglingCauseError(
                f"DanglingCauseReport repealed_rows count {len(self.repealed_rows)} != "
                f"repealed_target tally {self.repealed_target}"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_CAUSE_REPORT,
            "total_dangling": self.total_dangling,
            "repealed_target": self.repealed_target,
            "undetermined": self.undetermined,
            "repealed_rows": [r.to_canonical_dict() for r in self.repealed_rows],
            "undetermined_targets": list(self.undetermined_targets),
        }


def classify_dangling_causes(
    report: DanglingReferenceReport,
    oracle: RepealNoteCauseOracle,
) -> DanglingCauseReport:
    """Split a computed DANGLING set by temporal cause via the repeal-note oracle.

    Reads the parent report's ``dangling_rows`` (every DANGLING witness, no cap)
    and lands each in REPEALED (with evidence) or UNDETERMINED. Pure over the
    report + the oracle; totality (repealed + undetermined == dangling) is exact.
    """
    repealed_rows: list[DanglingCauseRow] = []
    undetermined_targets: Counter[str] = Counter()
    repealed = 0
    undetermined = 0

    for row in report.dangling_rows:
        if row.existence_status != STATUS_DANGLING:  # pragma: no cover — guard
            raise DanglingCauseError(
                f"non-DANGLING row {row.existence_status!r} present in dangling_rows "
                f"(target {row.target_provision_ref_str})"
            )
        cause, evidence = oracle.classify(
            row.target_statute_id, row.target_provision_ref_str
        )
        if cause == CAUSE_REPEALED_TARGET:
            assert evidence is not None  # oracle invariant
            repealed += 1
            repealed_rows.append(
                DanglingCauseRow(
                    source_statute_id=row.source_statute_id,
                    source_provision_ref_str=row.source_provision_ref_str,
                    target_statute_id=row.target_statute_id,
                    target_provision_ref_str=row.target_provision_ref_str,
                    cause=cause,
                    repeal_spec=evidence.spec,
                    repeal_unit=evidence.unit,
                    amending_act=evidence.amending_act,
                )
            )
        else:
            undetermined += 1
            undetermined_targets[row.target_statute_id] += 1

    repealed_rows.sort(
        key=lambda r: (
            r.target_statute_id,
            r.source_statute_id,
            r.source_provision_ref_str,
            r.target_provision_ref_str,
        )
    )
    return DanglingCauseReport(
        total_dangling=report.dangling,
        repealed_target=repealed,
        undetermined=undetermined,
        repealed_rows=tuple(repealed_rows),
        undetermined_targets=tuple(sorted(undetermined_targets)),
    )


__all__ = [
    "CAUSE_REPEALED_TARGET",
    "CAUSE_UNDETERMINED",
    "DANGLING_CAUSES",
    "DanglingCauseError",
    "DanglingCauseReport",
    "DanglingCauseRow",
    "RepealNoteCauseOracle",
    "RepealNoteEvidence",
    "classify_dangling_causes",
    "parse_repeal_notes",
]
