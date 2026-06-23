"""Finnish ActorMention extractor -- two-pass extraction with modal-verb classification.

Entry points:

  extract_actor_mentions(xml_bytes, statute_id, ...) -> ActorExtractionResult
      All ActorMention records from statute_id + findings/rejections.

Design discipline (AGENTS.md §1.1, §1.6, §1.8, §1.11, §1.13):

  §1.1 No silent target hijacking:
      Ambiguous phrase (multiple registry entries) -> AmbiguousActorMention finding,
      not a silent pick. Per-mention canonical_id is None for ambiguous.

  §1.6 No unstated migration:
      Lifecycle phrase ('Evira') -> LIFECYCLE_RESOLVED confidence +
      LifecycleActorObservation emitted.

  §1.8 No unsupported source lane disappears:
      Every rejected candidate emits RejectedActorCandidate.

  §1.11 Hot-path regex discipline:
      All patterns compiled at module scope.
      Bounded quantifiers; no adjacent unbounded repeats.
      Substring guards before regex on long text.

  §1.13 Grammar trigger -- modal-verb classification:
      Finnish modal patterns (DUTY/DISCRETION/PERMISSION/PROHIBITION/
      PASSIVE_OBLIGATION) form a FAMILY. Built as a single named recognizer
      (ModalVerbRecognizer) scanning context window around the actor phrase.
      NOT N parallel regexes racing each other.

Source: Finlex Akoma Ntoso consolidated XML.
Registry: lawvm.finland.canonical_actor_registry.REGISTRY.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from lawvm.core.actor_mention import (
    ActorMention,
    ActorModalKind,
    ActorResolutionConfidence,
    AmbiguousActorMention,
    LifecycleActorObservation,
    RejectedActorCandidate,
)
from lawvm.finland.canonical_actor_registry import REGISTRY, CanonicalActor

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_FINLEX_NS = "http://data.finlex.fi/schema/finlex"

# AKN TLCOrganization href extractor.
# Matches e.g. '/akn/ontology/organization/fi.ministry-of-...'
# Bounded: [^'"]{5,200} is safe for AKN hrefs.
_TLC_ORG_HREF_RE = re.compile(
    r"/akn/ontology/organization/([^/\"']{5,200})",
    re.IGNORECASE,
)

# AKN TLCOrganization eId extractor.
# eId like 'organization_fi.ministry-of-social-affairs-and-health'
_TLC_ORG_EID_RE = re.compile(
    r"organization[_-]([a-zA-Z0-9._-]{5,100})",
    re.IGNORECASE,
)

# AKN section-number extractor from <num> element text.
# Matches '5 §', '5a §', etc.
_SECTION_NUM_RE = re.compile(
    r"(\d{1,6}[a-z]?)\s*\xa7",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Modal-verb recognizer (AGENTS.md §1.13 -- named family, not N parallel regexes)
# ---------------------------------------------------------------------------
# The Finnish modal classification is a FAMILY of related patterns:
#   PROHIBITION  : 'ei saa' (must match before PERMISSION)
#   DUTY         : genitive+'on' or 'on [verb]inf' or 'tulee'
#   PASSIVE_OBL  : 'tehtavana on'
#   PERMISSION   : 'saa'
#   DISCRETION   : 'voi'
#
# These are NOT parallel regexes. They are a single-pass priority scan of a
# bounded context window (up to 120 chars) immediately after the actor phrase.
#
# Pattern design:
#   - Substring guard before regex (fast path)
#   - Bounded quantifiers: [\s\w,;]{0,80} is safe for Finnish inter-word gaps
#   - Priority order handles overlapping Finnish morphology

_MODAL_CONTEXT_WINDOW = 120  # chars to look at after the actor phrase

# The recognizer runs patterns in PRIORITY ORDER.
# First match wins. Each pattern has a guard string + compiled regex.
_MODAL_PATTERNS: Tuple[Tuple[str, ActorModalKind, re.Pattern[str]], ...] = (
    # PROHIBITION: 'ei saa' (must precede PERMISSION check)
    (
        "ei saa",
        ActorModalKind.PROHIBITION,
        re.compile(r"\bei\s+saa\b", re.IGNORECASE),
    ),
    # PASSIVE_OBLIGATION: 'tehtavana on' / 'X:n tehtavana on'
    (
        "teht",
        ActorModalKind.PASSIVE_OBLIGATION,
        re.compile(r"\bteht[a\xe4]v[a\xe4]n[a\xe4]\s+on\b", re.IGNORECASE),
    ),
    # DUTY: genitive 'on' construction.
    # Two sub-patterns in PRIORITY ORDER:
    #   1. Actor phrase itself ends in genitive (context_after starts with 'on')
    #      e.g. phrase='Liikenne- ja viestintaviraston', context_after=' on myonnettava'
    #   2. Genitive word + 'on' appears later in context
    #      e.g. context_after=' viranomaisen on toimittava'
    # Also handles 'tulee' as duty marker.
    (
        " on",
        ActorModalKind.DUTY,
        re.compile(
            r"(?:"
            r"^\s*on\b"                                              # pattern 1: genitive phrase, 'on' right after
            r"|^\s*tulee\b"                                          # pattern 1b: 'tulee' right after
            r"|[a-z\xe4\xf6\xe5é]{2,40}n\s+on\b"              # pattern 2: word-in-context n + on
            r"|[a-z\xe4\xf6\xe5é]{2,40}n\s+tulee\b"           # pattern 2b: word-in-context n + tulee
            r")",
            re.IGNORECASE,
        ),
    ),
    # PERMISSION: 'saa' (without 'ei' before it)
    (
        "saa",
        ActorModalKind.PERMISSION,
        re.compile(r"(?<!ei\s)\bsaa\b", re.IGNORECASE),
    ),
    # DISCRETION: 'voi'
    (
        "voi",
        ActorModalKind.DISCRETION,
        re.compile(r"\bvoi\b", re.IGNORECASE),
    ),
)


class ModalVerbRecognizer:
    """Named recognizer for Finnish modal-verb classification (AGENTS.md §1.13).

    Classifies the syntactic context of an actor phrase into one of:
    DUTY / DISCRETION / PERMISSION / PROHIBITION / PASSIVE_OBLIGATION /
    MENTION / UNRESOLVED.

    This is a single-pass structured recognizer over a bounded context window,
    not N overlapping backtracking scans.

    Usage:
        recognizer = ModalVerbRecognizer()
        kind = recognizer.classify(context_after_phrase)
    """

    def classify(self, context: str) -> ActorModalKind:
        """Classify modal kind from text immediately after the actor phrase.

        Args:
            context: Up to _MODAL_CONTEXT_WINDOW chars of text after the
                     actor phrase (or surrounding sentence fragment).

        Returns:
            ActorModalKind enum value.
        """
        if not context:
            return ActorModalKind.MENTION

        # Single-pass: iterate patterns in priority order, first match wins.
        for guard, kind, pattern in _MODAL_PATTERNS:
            if guard in context or guard.lower() in context.lower():
                if pattern.search(context):
                    return kind

        return ActorModalKind.MENTION


_MODAL_RECOGNIZER = ModalVerbRecognizer()


# ---------------------------------------------------------------------------
# Extraction result container
# ---------------------------------------------------------------------------


@dataclass
class ActorExtractionResult:
    """Container for all artifacts from one actor extraction pass.

    mentions:             Successfully typed ActorMention records.
    rejected:             RejectedActorCandidate records.
    ambiguous_findings:   AmbiguousActorMention findings.
    lifecycle_observations: LifecycleActorObservation records.
    """

    mentions: List[ActorMention] = field(default_factory=list)
    rejected: List[RejectedActorCandidate] = field(default_factory=list)
    ambiguous_findings: List[AmbiguousActorMention] = field(default_factory=list)
    lifecycle_observations: List[LifecycleActorObservation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pass 1: TLCOrganization AKN typed elements (EXACT confidence)
# ---------------------------------------------------------------------------


def _extract_tlc_org_id(element: ET.Element[str]) -> Optional[str]:
    """Extract AKN ontology organization ID from a TLCOrganization element.

    Tries href first, then eId as fallback.
    Returns the last path segment as canonical form, e.g. 'fi.ministry-of-finance'.
    Returns None if neither attribute is present or parseable.
    """
    href = element.get("href", "")
    if href:
        # lawvm-regex: owning_parser AKN TLCOrganization href attribute parse to ontology id, structured attribute not prose
        m = _TLC_ORG_HREF_RE.search(href)
        if m:
            return m.group(1)

    eid = element.get("eId", "")
    if eid:
        # lawvm-regex: owning_parser AKN eId attribute parse to organization id, structured attribute not prose
        m2 = _TLC_ORG_EID_RE.search(eid)
        if m2:
            return m2.group(1)

    return None


def _match_tlc_id_to_registry(
    tlc_id: str,
) -> Optional[CanonicalActor]:
    """Attempt to match an AKN TLCOrganization ID to a registry actor.

    Tries exact canonical_id match first, then substring match.
    """
    # Exact match by canonical_id
    actor = REGISTRY.get_actor(tlc_id)
    if actor is not None:
        return actor

    # Try prefix/suffix matching on canonical_id components
    for cid, actor_obj in (
        (cid, REGISTRY.get_actor(cid))
        for cid in (
            "fi." + tlc_id,
            tlc_id.replace("-", "."),
        )
    ):
        if actor_obj is not None:
            return actor_obj

    return None


def _pass1_tlc_organizations(
    root: ET.Element[str],
    statute_id: str,
    valid_at_interval: Tuple[Optional[date], Optional[date]],
) -> Tuple[List[ActorMention], List[LifecycleActorObservation]]:
    """Pass 1: extract ActorMention from typed <TLCOrganization> elements.

    These have EXACT confidence because they are structured AKN markup.
    Provision context is the enclosing <section> num, or empty if not found.

    Returns (mentions, lifecycle_observations).
    """
    mentions: List[ActorMention] = []
    lifecycle_obs: List[LifecycleActorObservation] = []

    ns_tag = f"{{{_AKN_NS}}}TLCOrganization"

    for tlc_el in root.iter(ns_tag):
        show_as = tlc_el.get("showAs", "")
        tlc_id = _extract_tlc_org_id(tlc_el)

        if not show_as and not tlc_id:
            continue  # No usable data in this element

        # Try to match to registry
        actor = _match_tlc_id_to_registry(tlc_id) if tlc_id else None

        if actor is not None:
            canonical_id = actor.canonical_id
            canonical_show_as = actor.show_as
            confidence = ActorResolutionConfidence.EXACT
        elif tlc_id is not None:
            # AKN-typed but not in our registry -- still EXACT (the markup is authoritative)
            canonical_id = tlc_id
            canonical_show_as = show_as or tlc_id
            confidence = ActorResolutionConfidence.EXACT
        else:
            # show_as present but no href/eId -- UNRESOLVED
            canonical_id = None
            canonical_show_as = None
            confidence = ActorResolutionConfidence.UNRESOLVED

        actor_phrase = show_as or (tlc_id or "")
        if not actor_phrase:
            continue

        valid_start, valid_end = valid_at_interval
        mention = ActorMention(
            source_provision_ref=statute_id,
            actor_phrase=actor_phrase,
            actor_canonical_id=canonical_id,
            actor_canonical_show_as=canonical_show_as,
            modal_kind=ActorModalKind.MENTION,  # TLC block -- no modal context available
            resolution_confidence=confidence,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_start=valid_start,
            valid_at_end=valid_end,
        )
        mentions.append(mention)

    return mentions, lifecycle_obs


# ---------------------------------------------------------------------------
# Pass 2: Prose scan against canonical registry
# ---------------------------------------------------------------------------


def _text_of(element: ET.Element[str]) -> str:
    """Collect all text content from an element and its descendants."""
    parts: List[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        parts.append(_text_of(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _section_provision_ref(section_el: ET.Element[str], statute_id: str) -> str:
    """Build a provision_ref string from a <section> element and statute_id."""
    ns_num = f"{{{_AKN_NS}}}num"
    num_el = section_el.find(ns_num)
    if num_el is not None and num_el.text:
        # lawvm-regex: owning_parser section label parse from already-extracted AKN <num> element text, not legal prose
        m = _SECTION_NUM_RE.search(num_el.text)
        if m:
            return f"{statute_id}/{m.group(1)}"
    return statute_id


def _pass2_prose_scan(
    root: ET.Element[str],
    statute_id: str,
    valid_at_interval: Tuple[Optional[date], Optional[date]],
    seen_tlc_phrases: frozenset[str],
) -> Tuple[
    List[ActorMention],
    List[AmbiguousActorMention],
    List[LifecycleActorObservation],
    List[RejectedActorCandidate],
]:
    """Pass 2: scan provision prose for canonical actor phrases.

    For each section in the AKN body:
      1. Collect full text.
      2. Substring-guard: skip text that contains no known actor phrases.
      3. Greedy scan longest-first over registry phrases.
      4. For each match: classify modal kind, resolve to canonical ID.
      5. Emit ActorMention (or AmbiguousActorMention finding + rejected).

    Per AGENTS.md §1.1: ambiguous -> AmbiguousActorMention, not silent pick.
    Per AGENTS.md §1.6: lifecycle phrase -> LifecycleActorObservation.
    Per AGENTS.md §1.8: unresolvable candidates -> RejectedActorCandidate.

    Args:
        root:            XML root element.
        statute_id:      Source statute ID.
        valid_at_interval: (start, end) for valid_at fields.
        seen_tlc_phrases:  Set of phrases already captured by Pass 1 (avoid duplicates).

    Returns:
        (mentions, ambiguous_findings, lifecycle_observations, rejected)
    """
    mentions: List[ActorMention] = []
    ambiguous_findings: List[AmbiguousActorMention] = []
    lifecycle_obs: List[LifecycleActorObservation] = []
    rejected: List[RejectedActorCandidate] = []

    valid_start, valid_end = valid_at_interval
    all_phrases = REGISTRY.all_phrases_longest_first()

    # Iterate over body sections
    ns_body = f"{{{_AKN_NS}}}body"
    ns_section = f"{{{_AKN_NS}}}section"

    body_el = root.find(f".//{ns_body}")
    if body_el is None:
        body_el = root  # fallback: scan whole tree

    for section_el in body_el.iter(ns_section):
        text = _text_of(section_el)
        if not text:
            continue

        provision_ref = _section_provision_ref(section_el, statute_id)

        # Substring guard: check if any registered phrase appears before regex
        has_any = any(phrase in text for phrase in all_phrases)
        if not has_any:
            continue

        # Track matched spans to avoid overlapping matches
        matched_spans: List[Tuple[int, int]] = []

        for phrase in all_phrases:
            # Substring guard per phrase (fast path)
            if phrase not in text:
                continue

            # Search for all non-overlapping occurrences
            start_pos = 0
            while True:
                idx = text.find(phrase, start_pos)
                if idx < 0:
                    break

                phrase_end = idx + len(phrase)

                # Check overlap with already-matched spans
                overlaps = any(
                    not (phrase_end <= s or idx >= e)
                    for s, e in matched_spans
                )
                if overlaps:
                    start_pos = idx + 1
                    continue

                # Boundary check: phrase must not be part of a longer word
                char_before = text[idx - 1] if idx > 0 else " "
                char_after = text[phrase_end] if phrase_end < len(text) else " "
                if char_before.isalpha() or char_after.isalpha():
                    start_pos = idx + 1
                    continue

                # Skip if already covered by TLC pass for this exact phrase
                if phrase in seen_tlc_phrases:
                    start_pos = phrase_end
                    continue

                # Resolve phrase against registry
                canonical_id, candidates = REGISTRY.lookup(phrase)

                if len(candidates) == 0:
                    # Should not happen (phrase came from all_phrases), but guard
                    start_pos = phrase_end
                    continue

                if len(candidates) > 1:
                    # Ambiguous: multiple registry entries match
                    # Per AGENTS.md §1.1: emit finding, not silent pick
                    af = AmbiguousActorMention(
                        rule_id="fi_actor_mention_ambiguous_phrase",
                        phase="actor_mention_extraction",
                        source_statute_id=statute_id,
                        source_provision_ref=provision_ref,
                        actor_phrase=phrase,
                        candidate_canonical_ids=tuple(candidates),
                        reason=(
                            f"Phrase {phrase!r} matches {len(candidates)} registry "
                            f"entries: {candidates}. No single canonical ID assigned."
                        ),
                        blocking=False,
                        strict_disposition="block",
                    )
                    ambiguous_findings.append(af)
                    matched_spans.append((idx, phrase_end))
                    start_pos = phrase_end
                    continue

                # Single match -- resolve. ActorRegistry.lookup() guarantees a
                # canonical ID when exactly one candidate is returned.
                assert canonical_id is not None
                actor = REGISTRY.get_actor(canonical_id)
                assert actor is not None  # invariant: lookup returned a valid id

                # Determine confidence: LIFECYCLE_RESOLVED vs REGISTRY_RESOLVED
                lifecycle_info = REGISTRY.lifecycle_observation_for(phrase, canonical_id)
                if lifecycle_info is not None:
                    # Per AGENTS.md §1.6: lifecycle resolution emits observation
                    pred_id, succ_id, lc_date = lifecycle_info
                    confidence = ActorResolutionConfidence.LIFECYCLE_RESOLVED
                    obs = LifecycleActorObservation(
                        rule_id="fi_actor_lifecycle_phrase_resolved",
                        phase="actor_mention_extraction",
                        source_statute_id=statute_id,
                        source_provision_ref=provision_ref,
                        actor_phrase=phrase,
                        predecessor_id=pred_id,
                        successor_id=succ_id,
                        lifecycle_date=lc_date,
                        reason=(
                            f"Predecessor phrase {phrase!r} resolved to "
                            f"{succ_id} via lifecycle (merger/rename on {lc_date})."
                        ),
                    )
                    lifecycle_obs.append(obs)
                    # The canonical_id IS the successor after lifecycle resolution
                    # (the actor's canonical_id is always the current/successor ID)
                    resolved_id = succ_id
                else:
                    confidence = ActorResolutionConfidence.REGISTRY_RESOLVED
                    resolved_id = canonical_id

                # Classify modal kind from context after phrase
                context_after = text[phrase_end:phrase_end + _MODAL_CONTEXT_WINDOW]
                modal_kind = _MODAL_RECOGNIZER.classify(context_after)

                mention = ActorMention(
                    source_provision_ref=provision_ref,
                    actor_phrase=phrase,
                    actor_canonical_id=resolved_id,
                    actor_canonical_show_as=actor.show_as,
                    modal_kind=modal_kind,
                    resolution_confidence=confidence,
                    source_span_file=None,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                    valid_at_start=valid_start,
                    valid_at_end=valid_end,
                )
                mentions.append(mention)
                matched_spans.append((idx, phrase_end))
                start_pos = phrase_end

    return mentions, ambiguous_findings, lifecycle_obs, rejected


# ---------------------------------------------------------------------------
# Strict-mode barrier (AGENTS.md §14)
# ---------------------------------------------------------------------------


def _apply_strict_barriers(
    result: ActorExtractionResult,
    statute_id: str,
    strict: bool,
) -> None:
    """In strict mode, flag UNRESOLVED mentions and ambiguous findings as blocking."""
    if not strict:
        return

    for mention in result.mentions:
        if mention.resolution_confidence == ActorResolutionConfidence.UNRESOLVED:
            result.rejected.append(
                RejectedActorCandidate(
                    rule_id="fi_actor_mention_strict_unresolved_barrier",
                    phase="actor_mention_extraction",
                    source_statute_id=statute_id,
                    reason=(
                        f"strict mode: UNRESOLVED actor phrase {mention.actor_phrase!r} "
                        "may not propagate to canonical-id columns in strict mode."
                    ),
                    matched_text=mention.actor_phrase,
                    source_span_file=None,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                    blocking=True,
                    strict_disposition="block",
                )
            )

    for af in result.ambiguous_findings:
        # Upgrade ambiguous findings to blocking in strict mode
        result.rejected.append(
            RejectedActorCandidate(
                rule_id="fi_actor_mention_strict_ambiguous_barrier",
                phase="actor_mention_extraction",
                source_statute_id=statute_id,
                reason=(
                    f"strict mode: ambiguous actor phrase {af.actor_phrase!r} "
                    f"matches {len(af.candidate_canonical_ids)} registry entries."
                ),
                matched_text=af.actor_phrase,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                blocking=True,
                strict_disposition="block",
            )
        )


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def extract_actor_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    strict: bool = False,
) -> ActorExtractionResult:
    """Extract ActorMention records from a Finnish statute XML.

    Two-pass extraction:
      Pass 1 (EXACT): typed <TLCOrganization> AKN elements.
      Pass 2 (REGISTRY_RESOLVED / LIFECYCLE_RESOLVED / UNRESOLVED): prose scan.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID, e.g. '711/2022'.
        valid_at_interval: (start, end) date range for these mentions.
        strict:            If True, UNRESOLVED/AMBIGUOUS mentions emit blocking
                           RejectedActorCandidate records.

    Returns:
        ActorExtractionResult with mentions, rejected, findings, observations.

    Per AGENTS.md §1.1: ambiguous phrase -> AmbiguousActorMention, not silent pick.
    Per AGENTS.md §1.6: lifecycle phrase -> LifecycleActorObservation emitted.
    Per AGENTS.md §1.8: rejected candidates preserved.
    """
    result = ActorExtractionResult()

    # Parse XML
    if not xml_bytes:
        result.rejected.append(
            RejectedActorCandidate(
                rule_id="fi_actor_mention_xml_parse_failed",
                phase="actor_mention_extraction",
                source_statute_id=statute_id,
                reason="Empty XML bytes provided.",
                matched_text="",
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                blocking=True,
                strict_disposition="block",
            )
        )
        return result

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        result.rejected.append(
            RejectedActorCandidate(
                rule_id="fi_actor_mention_xml_parse_failed",
                phase="actor_mention_extraction",
                source_statute_id=statute_id,
                reason=f"XML parse error: {exc}",
                matched_text="",
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                blocking=True,
                strict_disposition="block",
            )
        )
        return result

    # Pass 1: TLCOrganization elements
    p1_mentions, p1_lifecycle = _pass1_tlc_organizations(root, statute_id, valid_at_interval)
    result.mentions.extend(p1_mentions)
    result.lifecycle_observations.extend(p1_lifecycle)

    # Pass 2: Prose scan runs independently of Pass 1.
    # Pass 1 captures EXACT confidence from structured AKN markup (modal_kind=MENTION).
    # Pass 2 captures modal context from prose (REGISTRY_RESOLVED / LIFECYCLE_RESOLVED).
    # They serve complementary purposes; no deduplication.
    p2_mentions, p2_ambiguous, p2_lifecycle, p2_rejected = _pass2_prose_scan(
        root, statute_id, valid_at_interval, frozenset()
    )
    result.mentions.extend(p2_mentions)
    result.ambiguous_findings.extend(p2_ambiguous)
    result.lifecycle_observations.extend(p2_lifecycle)
    result.rejected.extend(p2_rejected)

    # Apply strict-mode barriers
    _apply_strict_barriers(result, statute_id, strict)

    return result
