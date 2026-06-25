"""Johtolause scope mention extraction helpers.

The uncovered-body fallback uses these labels as a guard: source body sections
not mentioned in the operative preamble should not silently enter replay.
"""

from __future__ import annotations

import functools
import re
from lawvm.core.regex_safety import compile_classifier_regex
from dataclasses import dataclass

from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.references.freetext_addresses import scan_legal_addresses

_DASH_CHARS = r"[-\u2013\u2014\u2015]"  # hyphen, en-dash, em-dash, horizontal bar
# --- Section-label site ANCHOR (lexer-primitive floor; demoted from parser) ----
#
# Section scope-mentions are parsed by the shared grammar driver
# ``scan_legal_addresses`` (which expands ranges/coordinated lists and resolves
# momentti/kohta structure through the johtolause grammar). The grammar
# deliberately DECLINES the illative ``N §:ään`` insertion-target case
# (``grammar.sections.recognize_section_ref``: ``case != "ILL"``) and the lexer
# does not classify some plural/partitive ``§`` inflections (``§:ä``,
# ``§:ien``, ``§:t``) as the PYKALA marker. For SCOPE MENTIONS those
# inflected sites ARE mentioned sections that must be collected, so a bounded
# label-run ANCHOR supplements the declined sites — fail-loud-not-silent: a
# § site the grammar does not yield a section for is still captured by the
# anchor (no scope-mention is silently dropped). The anchor finds WHERE a
# section site is and the flat number-run that precedes the § (the allowed
# regex floor); range/list expansion reuses the same shared helper.
#
# Verified over the full 58,546-doc johtolause corpus: the grammar+anchor path
# is a STRICT SUPERSET of the legacy ``_SECTION_REF_RE``/``_SECTION_LIST_RE``
# parser (0 section labels lost, +284 recovered = precise ranges, alpha-suffix
# lists, glued/prose-led shapes the legacy regex dropped).
_SEC_LABEL_ATOM = r"\d{1,4}+\s{0,3}+[a-z]?"
_SEC_LABEL_RANGE = _SEC_LABEL_ATOM + r"(?:[-—–―]" + _SEC_LABEL_ATOM + r")?"
_SECTION_SITE_ANCHOR_RE = re.compile(
    r"((?:" + _SEC_LABEL_RANGE + r")"
    r"(?:\s{0,3}(?:,|ja|sekä)\s{0,3}(?:" + _SEC_LABEL_RANGE + r"))*)"
    r"\s{0,40}§",
    re.I,
)
_SECTION_LIST_SPLIT_RE = re.compile(r"\s*(?:,|ja|sekä)\s*")
_SECTION_RANGE_SEGMENT_RE = re.compile(
    r"(\d{1,4}+\s{0,3}+[a-z]?)[-\u2014\u2013\u2015](\d{1,4}+\s{0,3}+[a-z]?)",
    re.I,
)
_ALPHA_SUFFIX_LABEL_RE = re.compile(r"(\d+)([a-z])")
_NEW_CHAPTER_RE = re.compile(
    r"(?:lisätään\s+(?:lakiin\s+)?|uusi\s+)"
    r"(\d{1,4}(?:\s{0,3}[a-z](?![a-z]))?)"
    r"(?:\s*" + _DASH_CHARS + r"\s*(\d{1,4}(?:\s{0,3}[a-z](?![a-z]))?))?"
    r"\s+(?:luku(?:un)?|luvun|luvut)\b",
    re.I,
)
_MOVE_DESTINATION_CHAPTER_RE = re.compile(
    r"\bsiirretään\b[^§\n]{0,200}?(?:lakiin\s+)?(\d{1,4}+\s{0,3}+[a-z]?)\s+lukuun",
    re.I,
)
_MOVE_SECTION_TO_CHAPTER_RE = re.compile(
    r"(\d{1,4}+\s{0,3}+[a-z]?)\s*§[^§\n]{0,120}?\bsiirretään\b[^§\n]{0,200}?"
    r"(?:lakiin\s+)?(\d{1,4}+\s{0,3}+[a-z]?)\s+lukuun",
    re.I,
)
# Bounded anaphor anchor for ``uusi 6 a luku, johon ... siirretään 25, 26 ja
# 27 §``. The section list itself is parsed by ``scan_legal_addresses`` below;
# this regex only ties the list to the single declared new chapter antecedent.
_ANAPHORIC_NEW_CHAPTER_MOVE_TAIL_RE = compile_classifier_regex(r"\bjohon\b[^§\n]{0,80}?\bsiirretään\b(?P<section_tail>[^§\n]{0,240}§)", re.I, classifier_id="fi.johto_scope_mentions.anaphoric_new_chapter_move_tail_re")
_MUUTETAAN_RE = compile_classifier_regex(r"\bmuutetaan\b", re.I, classifier_id="fi.johto_scope_mentions.muutetaan_re")
_LUKU_RE = compile_classifier_regex(r"\bluku\b", re.I, classifier_id="fi.johto_scope_mentions.luku_re")
_CHAPTER_NUMBER_RE = re.compile(
    r"(\d+\s*(?:[a-z](?![a-z]))?)(?:\s*" + _DASH_CHARS + r"\s*(\d+\s*(?:[a-z](?![a-z]))?))?",
    re.I,
)
_SECTION_OR_GENITIVE_CHAPTER_RE = compile_classifier_regex(r"§|luvun", re.I, classifier_id="fi.johto_scope_mentions.section_or_genitive_chapter_re")
_NUMBERED_TABLE_TARGET_RE = compile_classifier_regex(r"(?P<section>\d{1,4}+\s{0,3}+[a-z]?)\s*§\s*:\s*n\s+"
    r"tauluk(?:ko|on)\s+(?P<table>\d{1,4}+\s{0,3}+[a-z]?)\b", re.I, classifier_id="fi.johto_scope_mentions.numbered_table_target_re")
_ILLATIVE_SECTION_SUBSECTION_INSERT_RE = re.compile(
    r"(?P<labels>(?:" + _SEC_LABEL_RANGE + r")"
    r"(?:\s{0,3}(?:,|ja|sekä)\s{0,3}(?:" + _SEC_LABEL_RANGE + r"))*)"
    r"\s*§\s*:\s*(?:ään|aan)\b"
    r"(?=[^§.;]{0,180}?\bmoment(?:ti|in)\b)",
    re.I,
)
_PRECEDING_ILLATIVE_SECTION_SIBLING_RE = compile_classifier_regex(r"(?P<section>\d{1,4}+\s{0,3}+[a-z]?)\s*§\s*:\s*(?:ään|aan)\s+"
    r"(?:ja|sekä)\s*$", re.I, classifier_id="fi.johto_scope_mentions.preceding_illative_section_sibling_re")


@dataclass(frozen=True, slots=True)
class MovedSectionDestination:
    section_label: str
    destination_chapter_label: str


@dataclass(frozen=True, slots=True)
class JohtoChapterScopeMentions:
    new_chapter_labels: frozenset[str]
    replaced_chapter_labels: frozenset[str]
    moved_destination_chapter_labels: frozenset[str]
    moved_section_destinations: tuple[MovedSectionDestination, ...]


@dataclass(frozen=True, slots=True)
class NumberedTableTarget:
    """A Finnish johtolause target naming a numbered table inside a section."""

    section_label: str
    table_label: str


def _append_moved_section_destination(
    moved_section_destinations: list[MovedSectionDestination],
    seen: set[tuple[str, str]],
    *,
    section_label: str,
    destination_chapter_label: str,
) -> None:
    section = _norm_num_token(section_label)
    destination = _norm_num_token(destination_chapter_label).removesuffix("luku")
    if not section or not destination:
        return
    key = (section, destination)
    if key in seen:
        return
    seen.add(key)
    moved_section_destinations.append(
        MovedSectionDestination(
            section_label=section,
            destination_chapter_label=destination,
        )
    )


@functools.lru_cache(maxsize=8192)
def expand_johto_section_label_range(start: str, end: str) -> tuple[str, ...]:
    """Expand a johto-mentioned section range into normalized labels.

    Supports purely numeric ranges (``17-21 §``) and same-base alpha suffix
    ranges (``21 a-21 d §``). Unknown shapes fall back to the normalized
    endpoints rather than guessing intermediate labels.
    """
    start_norm = _norm_num_token(start)
    end_norm = _norm_num_token(end)
    if not start_norm or not end_norm:
        return tuple(label for label in (start_norm, end_norm) if label)

    if start_norm.isdigit() and end_norm.isdigit():
        s_int, e_int = int(start_norm), int(end_norm)
        if 0 < e_int - s_int < 500:
            return tuple(str(i) for i in range(s_int, e_int + 1))
        return (start_norm, end_norm)

    start_match = _ALPHA_SUFFIX_LABEL_RE.fullmatch(start_norm)
    end_match = _ALPHA_SUFFIX_LABEL_RE.fullmatch(end_norm)
    if start_match and end_match and start_match.group(1) == end_match.group(1):
        start_ord = ord(start_match.group(2))
        end_ord = ord(end_match.group(2))
        if 0 <= end_ord - start_ord < 26:
            base = start_match.group(1)
            return tuple(f"{base}{chr(code)}" for code in range(start_ord, end_ord + 1))

    return (start_norm, end_norm)


def collect_johto_mentioned_section_labels(johto_text: str) -> set[str]:
    return set(collect_johto_mentioned_section_labels_frozenset(johto_text))


def collect_johto_moment_targets(johto_text: str) -> dict[str, frozenset[int]]:
    """Map johto-mentioned section labels to explicit momentti ordinals.

    Uncovered-body omission merges need these targets when the preamble names
    ``N §:n M momentti`` but compile emits no paragraph-scoped AmendmentOps.
    """
    targets: dict[str, set[int]] = {}
    for addr in scan_legal_addresses(johto_text):
        if (
            not addr.section
            or addr.subsection is None
            or addr.item is not None
            or addr.special
        ):
            continue
        section = _norm_num_token(addr.section)
        if section:
            targets.setdefault(section, set()).add(addr.subsection)
    return {section: frozenset(moments) for section, moments in targets.items()}


def collect_johto_whole_section_targets(johto_text: str) -> frozenset[str]:
    """Return section labels parsed as whole-section targets in the preamble.

    Unlike ``collect_johto_mentioned_section_labels``, this intentionally does
    not use the supplemental section-site anchor. The anchor captures declined
    item-like insertion sites such as ``13 §:ään ... uusi merkkiä 141 a koskeva
    kohta`` for broad body-coverage guarding; those sites must not authorize a
    section-level omission merge.
    """
    targets: set[str] = set()
    for addr in scan_legal_addresses(johto_text):
        if (
            not addr.section
            or addr.subsection is not None
            or addr.item is not None
            or addr.subitem is not None
            or addr.special
        ):
            continue
        section = _norm_num_token(addr.section)
        if section:
            targets.add(section)
    return frozenset(targets)


@functools.lru_cache(maxsize=8192)
def collect_johto_named_subprovision_section_targets(johto_text: str) -> frozenset[str]:
    """Return sections whose johtolause target is a named sub-provision.

    Drafting such as ``16 §:n merkkiä 317 koskeva kohta`` names a row-like
    sub-provision by description, not the host section as a whole. These labels
    are negative evidence for uncovered-body omission merge: until LawVM has a
    typed address for the named row, recovery must not widen the target to a
    whole-section replacement.
    """
    if "§" not in johto_text or "koht" not in johto_text:
        return frozenset()

    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
    from lawvm.finland.johtolause.surface_model import (
        SurfaceScopeBlock,
        SurfaceTargetRef,
        TargetKind,
    )
    from lawvm.finland.parser_facade import parse_tokens_production

    raw_tokens = tokenize(johto_text)
    tokens, jolloin_pairs = apply_annotations_with_jolloin_pairs(raw_tokens)
    parsed = parse_tokens_production(
        tokens,
        jolloin_renumber_pairs=jolloin_pairs if jolloin_pairs else None,
    )

    labels: set[str] = set()

    def visit_node(node: object) -> None:
        if isinstance(node, SurfaceScopeBlock):
            for child in node.targets:
                visit_node(child)
            return
        if not isinstance(node, SurfaceTargetRef):
            return
        if node.kind is not TargetKind.SECTION:
            return
        if not any(
            sub.special and sub.facet is None and not sub.momentti and not sub.item
            for sub in node.sub_refs
        ):
            return
        label = _norm_num_token(node.label)
        if label:
            labels.add(label)

    for group in parsed.clause.verb_groups:
        for node in group.nodes:
            visit_node(node)
    return frozenset(labels)


def collect_johto_insert_subsection_section_targets(johto_text: str) -> frozenset[str]:
    """Return sections targeted by ``N §:ään uusi M momentti`` insertions.

    This is narrower than a section mention: it authorizes sparse omission
    merge only for subsection insertions into an existing section. Item/special
    insertion phrases such as ``uusi merkkiä ... koskeva kohta`` deliberately do
    not match.
    """
    if "§:" not in johto_text or "moment" not in johto_text:
        return frozenset()
    targets: set[str] = set()
    # lawvm-regex: owning_parser bounded illative subsection-insert anchor over owned johto, supplementing the scan_legal_addresses grammar driver; not a cross-plane raw_text read
    for match in _ILLATIVE_SECTION_SUBSECTION_INSERT_RE.finditer(johto_text):
        targets.update(_expand_section_label_run(match.group("labels")))
        prefix = johto_text[max(0, match.start() - 80) : match.start()]
        # lawvm-regex: owning_parser bounded preceding-section sibling anchor over owned johto window; not a cross-plane raw_text read
        sibling = _PRECEDING_ILLATIVE_SECTION_SIBLING_RE.search(prefix)
        if sibling:
            section = _norm_num_token(sibling.group("section"))
            if section:
                targets.add(section)
    return frozenset(targets)


def collect_johto_insert_section_targets(johto_text: str) -> frozenset[str]:
    """Return sections targeted by ``lisätään ... uusi N §`` insertions."""
    if "lisätään" not in johto_text and "lisataan" not in johto_text:
        return frozenset()

    from lawvm.finland.johtolause.lexer import tokenize

    tokens = tokenize(johto_text)
    targets: set[str] = set()
    in_insert_group = False
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.lemma == "lisätä":
            in_insert_group = True
            idx += 1
            continue
        if not in_insert_group:
            idx += 1
            continue
        if token.cat != "UUSI":
            idx += 1
            continue

        labels: list[str] = []
        scan_idx = idx + 1
        while scan_idx < len(tokens):
            scan = tokens[scan_idx]
            if scan.cat == "NUM":
                labels.append(scan.text)
                scan_idx += 1
                continue
            if scan.cat == "CONJ" or scan.text == ",":
                scan_idx += 1
                continue
            if scan.cat == "PYKALA" and scan.case in {"", "NOM"}:
                targets.update(
                    label
                    for label in (_norm_num_token(raw) for raw in labels)
                    if label
                )
                idx = scan_idx
                break
            break
        idx += 1
    return frozenset(targets)


@functools.lru_cache(maxsize=8192)
def collect_johto_numbered_table_targets(johto_text: str) -> tuple[NumberedTableTarget, ...]:
    """Return explicit ``N §:n taulukko M`` table targets from a Finnish johtolause."""
    targets: list[NumberedTableTarget] = []
    seen: set[tuple[str, str]] = set()
    # lawvm-regex: owning_parser bounded table-target lexer for explicit johtolause scope mentions
    for match in _NUMBERED_TABLE_TARGET_RE.finditer(johto_text or ""):
        section = _norm_num_token(match.group("section"))
        table = _norm_num_token(match.group("table"))
        key = (section, table)
        if not section or not table or key in seen:
            continue
        seen.add(key)
        targets.append(NumberedTableTarget(section_label=section, table_label=table))
    return tuple(targets)


def collect_johto_numbered_table_targets_by_section(
    johto_text: str,
) -> dict[str, frozenset[str]]:
    """Map section labels to explicitly targeted numbered table labels."""
    out: dict[str, set[str]] = {}
    for target in collect_johto_numbered_table_targets(johto_text):
        out.setdefault(target.section_label, set()).add(target.table_label)
    return {section: frozenset(tables) for section, tables in out.items()}


def _expand_section_label_run(run: str) -> set[str]:
    """Expand a flat section-label run (anchor capture) into normalized labels.

    ``"2, 4 ja 5"`` -> ``{"2","4","5"}``; ``"8-10"`` -> ``{"8","9","10"}``.
    Reuses :func:`expand_johto_section_label_range` for range segments, the same
    helper the grammar driver uses, so range semantics stay identical.
    """
    labels: set[str] = set()
    for segment in _SECTION_LIST_SPLIT_RE.split(run):
        segment = segment.strip()
        if not segment:
            continue
        range_match = _SECTION_RANGE_SEGMENT_RE.fullmatch(segment)
        if range_match:
            labels.update(
                expand_johto_section_label_range(
                    range_match.group(1),
                    range_match.group(2),
                )
            )
            continue
        norm = _norm_num_token(segment)
        if norm:
            labels.add(norm)
    return labels


@functools.lru_cache(maxsize=8192)
def collect_johto_mentioned_section_labels_frozenset(johto_text: str) -> frozenset[str]:
    """Collect every section label mentioned in a Finnish johtolause.

    The structural parse routes through the shared grammar driver
    :func:`scan_legal_addresses` (range/coordinated-list expansion and
    momentti/kohta resolution). A bounded label-run ANCHOR
    (``_SECTION_SITE_ANCHOR_RE``) supplements the § sites the grammar
    deliberately declines (illative ``N §:ään`` insertion targets) or cannot
    lex (plural/partitive ``§:ä`` / ``§:ien`` / ``§:t``) so no scope-mention is
    silently dropped. Verified strict superset of the legacy regex parser over
    the full johtolause corpus (0 lost, +284 recovered).
    """
    labels: set[str] = set()

    # Grammar-parsed sites: ranges, coordinated lists, momentti/kohta structure.
    for addr in scan_legal_addresses(johto_text):
        if not addr.section:
            continue
        norm = _norm_num_token(addr.section)
        if norm:
            labels.add(norm)

    # Anchor supplement: § sites the grammar declined/could-not-lex. Capturing
    # the flat number-run before the § keeps these mentioned sections in scope.
    # lawvm-regex: owning_parser bounded §-site anchor floor over owned johto for sites the grammar declined/could-not-lex (proven strict superset of legacy); not a cross-plane raw_text read
    for match in _SECTION_SITE_ANCHOR_RE.finditer(johto_text):
        labels.update(_expand_section_label_run(match.group(1)))

    return frozenset(labels)


def collect_johto_chapter_scope_mentions(johto_text: str) -> JohtoChapterScopeMentions:
    """Extract chapter-level ownership clues from a Finnish johtolause."""
    new_chapter_labels: set[str] = set()
    replaced_chapter_labels: set[str] = set()
    moved_destination_chapter_labels: set[str] = set()
    moved_section_destinations: list[MovedSectionDestination] = []
    seen_moved_destinations: set[tuple[str, str]] = set()

    # lawvm-regex: owning_parser bounded new-chapter ownership-clue anchor over owned johto; not a cross-plane raw_text read
    for match in _NEW_CHAPTER_RE.finditer(johto_text):
        start_label = _norm_num_token(match.group(1)).removesuffix("luku")
        end_label = _norm_num_token(match.group(2)).removesuffix("luku") if match.group(2) else None
        if start_label and end_label and start_label.isdigit() and end_label.isdigit():
            s_int, e_int = int(start_label), int(end_label)
            if 0 < e_int - s_int < 100:
                new_chapter_labels.update(str(i) for i in range(s_int, e_int + 1))
        elif start_label:
            new_chapter_labels.add(start_label)

    # lawvm-regex: owning_parser bounded move-destination chapter anchor over owned johto; not a cross-plane raw_text read
    for match in _MOVE_DESTINATION_CHAPTER_RE.finditer(johto_text):
        dest_chapter = _norm_num_token(match.group(1)).removesuffix("luku")
        if dest_chapter:
            moved_destination_chapter_labels.add(dest_chapter)

    # lawvm-regex: owning_parser bounded section->chapter move-pairing anchor over owned johto; not a cross-plane raw_text read
    for match in _MOVE_SECTION_TO_CHAPTER_RE.finditer(johto_text):
        source_label = _norm_num_token(match.group(1))
        dest_chapter = _norm_num_token(match.group(2)).removesuffix("luku")
        if source_label and dest_chapter:
            _append_moved_section_destination(
                moved_section_destinations,
                seen_moved_destinations,
                section_label=source_label,
                destination_chapter_label=dest_chapter,
            )

    if len(new_chapter_labels) == 1:
        (new_chapter_label,) = tuple(new_chapter_labels)
        # lawvm-regex: owning_parser bounded anaphor anchor over owned johto; the section list itself is parsed by scan_legal_addresses (grammar), regex only ties it to the declared new-chapter antecedent; not a cross-plane raw_text read
        for match in _ANAPHORIC_NEW_CHAPTER_MOVE_TAIL_RE.finditer(johto_text):
            for addr in scan_legal_addresses(match.group("section_tail")):
                if (
                    not addr.section
                    or addr.subsection is not None
                    or addr.item is not None
                    or addr.subitem is not None
                    or addr.special
                ):
                    continue
                _append_moved_section_destination(
                    moved_section_destinations,
                    seen_moved_destinations,
                    section_label=addr.section,
                    destination_chapter_label=new_chapter_label,
                )

    # lawvm-regex: owning_parser muutetaan keyword presence guard over owned johto; not a cross-plane raw_text read
    if _MUUTETAAN_RE.search(johto_text):
        # lawvm-regex: owning_parser bounded `luku` anchor windowing over owned johto; not a cross-plane raw_text read
        for luku_match in _LUKU_RE.finditer(johto_text):
            start = max(0, luku_match.start() - 200)
            prefix = johto_text[start : luku_match.start()]
            # lawvm-regex: owning_parser chapter-number anchor lexer over the bounded prefix window of owned johto; not a cross-plane raw_text read
            for range_match in _CHAPTER_NUMBER_RE.finditer(prefix):
                between = prefix[range_match.end() :]
                # lawvm-regex: owning_parser §/luvun disambiguation guard within the owned johto window; not a cross-plane raw_text read
                if _SECTION_OR_GENITIVE_CHAPTER_RE.search(between):
                    continue
                start_chapter = _norm_num_token(range_match.group(1)).removesuffix("luku")
                end_chapter = (
                    _norm_num_token(range_match.group(2)).removesuffix("luku")
                    if range_match.group(2)
                    else None
                )
                if start_chapter and end_chapter and start_chapter.isdigit() and end_chapter.isdigit():
                    s_int, e_int = int(start_chapter), int(end_chapter)
                    if 0 < e_int - s_int < 100:
                        replaced_chapter_labels.update(str(i) for i in range(s_int, e_int + 1))
                elif start_chapter:
                    replaced_chapter_labels.add(start_chapter)

    return JohtoChapterScopeMentions(
        new_chapter_labels=frozenset(new_chapter_labels),
        replaced_chapter_labels=frozenset(replaced_chapter_labels),
        moved_destination_chapter_labels=frozenset(moved_destination_chapter_labels),
        moved_section_destinations=tuple(moved_section_destinations),
    )
