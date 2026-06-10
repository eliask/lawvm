"""Extract statute-level fixed-term validity bounds from Finnish timelines.

Ownership of *recognising* the whole-law expiry clause stays in the typed
meta-clause lane: ``extract_meta_surface_clauses`` classifies the
voimaantulosäännös prose as a ``MetaClauseKind.EXPIRY`` clause. This module
consumes that classification and reuses the proven date regex
(``whole_law_expiry_date_from_text``) as a helper to lift it into a
``StatuteValidityBound`` per version of the entry-into-force provision.

Extension acts text-replace the entry-into-force provision, so each version of
that provision carries the then-current bound; one bound fact is produced per
version (Pro design D′, §10 step 4).

Diagnostics (spec §4) are returned as structured records; the caller decides
their finding role (the governing-bound unparseable/ambiguous cases are
blocking obligations, scoped/weak cases are observations).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Optional

from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import MetaClauseKind
from lawvm.core.statute_validity import (
    FIXED_TERM_WHOLE_STATUTE_RULE_ID,
    StatuteValidityBound,
    expires_on_from_valid_until,
)
from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
from lawvm.finland.metadata import (
    CHAPTER_SCOPED_EXPIRY_RE,
    SECTION_SCOPED_EXPIRY_RE,
    _normalize_fi_parse_text,
    whole_law_expiry_date_from_text,
)

# Diagnostic codes (registered in observation_registry under role per spec §4).
FIXED_TERM_EXPIRY_UNPARSEABLE = "TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE"
FIXED_TERM_EXPIRY_AMBIGUOUS = "TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS"
SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED = "TEMPORAL.SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED"
POSSIBLE_EXPIRY_TEXT_UNSUPPORTED = "TEMPORAL.POSSIBLE_EXPIRY_TEXT_UNSUPPORTED"
FIXED_TERM_LATE_EXTENSION_GAP = "TEMPORAL.FIXED_TERM_LATE_EXTENSION_GAP"


@dataclass(frozen=True)
class FixedTermDiagnostic:
    """One extraction-time diagnostic about fixed-term validity."""

    code: str
    statute_id: str
    address: str
    effective: str
    detail: str


@dataclass(frozen=True)
class FixedTermExtraction:
    """Result of scanning one statute's timelines for fixed-term bounds."""

    statute_id: str
    bounds: tuple[StatuteValidityBound, ...]
    diagnostics: tuple[FixedTermDiagnostic, ...]
    # True when a whole-law expiry CLAUSE was recognised on at least one version
    # (whether or not its date parsed). Cheap corpus-report candidate signal.
    has_candidate: bool


def _has_whole_law_expiry_clause(normalized_text: str) -> bool:
    """True when the typed meta-clause lane classifies ``normalized_text`` as a
    whole-law expiry clause ("Tämä laki ... on voimassa ...")."""
    for clause in extract_meta_surface_clauses(normalized_text):
        if clause.kind is not MetaClauseKind.EXPIRY:
            continue
        # Restrict to the WHOLE-law form: the clause subject is the act itself
        # ("Tämä laki/asetus/päätös"), not a named section/chapter.
        if _whole_law_subject_re_search(clause.text):
            return True
    return False


_WHOLE_LAW_SUBJECT = "tämä "


def _whole_law_subject_re_search(text: str) -> bool:
    lowered = text.lower()
    idx = lowered.find(_WHOLE_LAW_SUBJECT)
    if idx < 0:
        return False
    tail = lowered[idx : idx + 40]
    return any(word in tail for word in ("laki", "asetus", "päätös"))


def _content_hash(version: ProvisionVersion) -> str:
    if version.content_hash:
        return version.content_hash
    text = irnode_to_text(version.content) if version.content is not None else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _version_source_id(version: ProvisionVersion, statute_id: str) -> str:
    if version.source is not None and version.source.statute_id:
        return version.source.statute_id
    return statute_id


def extract_fixed_term_bounds(
    *,
    statute_id: str,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
) -> FixedTermExtraction:
    """Scan ``timelines`` for whole-law fixed-term validity bounds.

    Each version of the entry-into-force provision that carries a whole-law
    expiry clause yields one bound. Parent/child duplicates of the same clause
    (e.g. section + its subsection) are de-duplicated, keeping the shallowest
    carrying address. Detected-but-unparseable clauses and scoped forms emit
    diagnostics rather than silently degrading.
    """

    bounds: list[StatuteValidityBound] = []
    diagnostics: list[FixedTermDiagnostic] = []
    has_candidate = False

    # (effective, source_id) -> shallowest carrying address depth seen so far.
    claimed: dict[tuple[str, str], int] = {}
    sequence = 0

    for address in sorted(timelines, key=lambda a: (len(a.path), str(a))):
        timeline = timelines[address]
        for version in timeline.versions:
            if version.content is None:
                continue
            normalized = _normalize_fi_parse_text(irnode_to_text(version.content))

            scoped_only = (
                SECTION_SCOPED_EXPIRY_RE.search(normalized) is not None
                or CHAPTER_SCOPED_EXPIRY_RE.search(normalized) is not None
            )
            whole_law_clause = _has_whole_law_expiry_clause(normalized)

            if not whole_law_clause and not scoped_only:
                continue

            source_id = _version_source_id(version, statute_id)
            key = (version.effective, source_id)

            if scoped_only and not whole_law_clause:
                # A scoped (chapter/section) fixed-term form. v1 does not lift it
                # into a statute-level bound; surface it for review.
                diagnostics.append(
                    FixedTermDiagnostic(
                        code=SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED,
                        statute_id=statute_id,
                        address=str(address),
                        effective=version.effective,
                        detail="scoped (chapter/section) fixed-term expiry detected; not lifted in v1",
                    )
                )
                continue

            # Whole-law clause recognised — this is a fixed-term candidate.
            has_candidate = True
            depth = len(address.path)
            prior_depth = claimed.get(key)
            if prior_depth is not None and depth >= prior_depth:
                # The same clause already owned by a shallower (or equal) address.
                continue

            valid_until = whole_law_expiry_date_from_text(normalized)
            if valid_until is None:
                diagnostics.append(
                    FixedTermDiagnostic(
                        code=FIXED_TERM_EXPIRY_UNPARSEABLE,
                        statute_id=statute_id,
                        address=str(address),
                        effective=version.effective,
                        detail="whole-law expiry clause recognised but validity date unparseable",
                    )
                )
                # Do not record a bound; mark the key so deeper duplicates of the
                # same unparseable clause do not re-diagnose.
                claimed[key] = depth
                continue

            expires_on = expires_on_from_valid_until(valid_until)
            bound = StatuteValidityBound(
                statute_id=statute_id,
                scope="whole_statute",
                effective=version.effective,
                enacted=version.enacted or None,
                valid_until=valid_until.isoformat(),
                expires_on=expires_on.isoformat(),
                source_provision=address,
                source_version_id=source_id,
                source_hash=_content_hash(version),
                source_span=None,
                rule_id=FIXED_TERM_WHOLE_STATUTE_RULE_ID,
                source_text=normalized[:500],
                source_sequence=sequence,
            )
            sequence += 1
            # Remove any earlier deeper-address bound for the same key.
            if prior_depth is not None:
                bounds[:] = [
                    b
                    for b in bounds
                    if not (b.effective == version.effective and b.source_version_id == source_id)
                ]
            claimed[key] = depth
            bounds.append(bound)

    # Ambiguity: two distinct whole-law bounds with the SAME effective date but
    # different validity ends cannot be deterministically ranked.
    by_effective: dict[str, set[str]] = {}
    for bound in bounds:
        by_effective.setdefault(bound.effective, set()).add(bound.valid_until)
    for effective, valids in by_effective.items():
        if len(valids) > 1:
            diagnostics.append(
                FixedTermDiagnostic(
                    code=FIXED_TERM_EXPIRY_AMBIGUOUS,
                    statute_id=statute_id,
                    address="<whole_statute>",
                    effective=effective,
                    detail=f"conflicting whole-law bounds at {effective}: {sorted(valids)}",
                )
            )

    return FixedTermExtraction(
        statute_id=statute_id,
        bounds=tuple(bounds),
        diagnostics=tuple(diagnostics),
        has_candidate=has_candidate,
    )


def has_ambiguity(extraction: FixedTermExtraction) -> bool:
    return any(d.code == FIXED_TERM_EXPIRY_AMBIGUOUS for d in extraction.diagnostics)


@dataclass(frozen=True)
class FixedTermCorpusReport:
    """Aggregate counts for a fixed-term extraction soak (Pro §10 step 5)."""

    statutes_scanned: int
    fixed_term_candidates: int
    whole_law_supported: int
    scoped_unsupported: int
    unparseable: int
    ambiguous: int
    affected_statutes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "lawvm_fixed_term_corpus_report",
            "statutes_scanned": self.statutes_scanned,
            "fixed_term_candidates": self.fixed_term_candidates,
            "whole_law_supported": self.whole_law_supported,
            "scoped_unsupported": self.scoped_unsupported,
            "unparseable": self.unparseable,
            "ambiguous": self.ambiguous,
            "affected_statutes": list(self.affected_statutes),
        }


def build_corpus_report(
    extractions: "list[FixedTermExtraction] | tuple[FixedTermExtraction, ...]",
) -> FixedTermCorpusReport:
    """Aggregate per-statute extractions into corpus counts (no semantic change).

    Cheap mode: callers pass extractions obtained from ``extract_fixed_term_bounds``
    over already-materialised timelines; this performs no replay itself.
    """
    candidates = 0
    whole_law_supported = 0
    scoped_unsupported = 0
    unparseable = 0
    ambiguous = 0
    affected: list[str] = []
    for extraction in extractions:
        if extraction.has_candidate:
            candidates += 1
        if extraction.bounds:
            whole_law_supported += 1
            affected.append(extraction.statute_id)
        for diagnostic in extraction.diagnostics:
            if diagnostic.code == SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED:
                scoped_unsupported += 1
            elif diagnostic.code == FIXED_TERM_EXPIRY_UNPARSEABLE:
                unparseable += 1
            elif diagnostic.code == FIXED_TERM_EXPIRY_AMBIGUOUS:
                ambiguous += 1
    return FixedTermCorpusReport(
        statutes_scanned=len(list(extractions)),
        fixed_term_candidates=candidates,
        whole_law_supported=whole_law_supported,
        scoped_unsupported=scoped_unsupported,
        unparseable=unparseable,
        ambiguous=ambiguous,
        affected_statutes=tuple(sorted(set(affected))),
    )


def governing_unparseable(
    extraction: FixedTermExtraction,
    *,
    as_of: str,
    query_type: str,
) -> Optional[FixedTermDiagnostic]:
    """Return an UNPARSEABLE diagnostic when the bound that WOULD govern at
    ``as_of`` is a recognised-but-unparseable whole-law expiry clause.

    A bound that fails to parse has no ``effective`` recorded in ``bounds``; we
    reconstruct eligibility from the diagnostic's effective date. This is what
    makes "detected but unparseable, and it governs" block rather than silently
    returning a live answer.
    """
    candidates = [
        d
        for d in extraction.diagnostics
        if d.code == FIXED_TERM_EXPIRY_UNPARSEABLE and d.effective <= as_of
    ]
    if not candidates:
        return None
    latest_unparseable = max(candidates, key=lambda d: d.effective)
    # If a parseable bound takes effect at or after the unparseable one and is
    # itself eligible at as_of, the parseable bound governs and we do not block.
    for bound in extraction.bounds:
        if bound.effective < latest_unparseable.effective:
            continue
        if bound.effective > as_of:
            continue
        if query_type == "in_force" and bound.enacted and bound.enacted > as_of:
            continue
        return None
    return latest_unparseable
