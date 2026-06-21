"""Item-scoped cited-version clause recognizer (references-owned grammar).

A Finnish amendment johtolause that re-states an item-scoped provision in the
form it held under a named earlier act carries a *cited-version* clause:

    muutetaan 5 §:n 2 kohta, sellaisena kuin se on laissa 123/2019

The replay layer reads this clause to decide whether an item-scoped op emitted
a stale ancestor snapshot covered by the cited act's same-effective snapshot
(``replay_products._drop_cited_version_item_ancestor_snapshots`` →
``CitedVersionSnapshotDrop``). That decision used to parse the amendment source
``raw_text`` inline in the replay module — a cross-plane reach-back into the
amendment-language plane. This module owns the clause grammar so the replay
layer passes the text in and consumes a typed result instead of parsing source
language itself.

The clause is ONE family over a single source span:

  * the target window         ``{label} §:n``                (target template)
  * the item-word cue         ``koht*``  (kohta/kohdan/…)    (item word)
  * the cited-version cue      ``sellaisena/sellaisina kuin`` (provenance cue)
  * the cited statute id       ``laissa/asetuksessa N/YYYY``  (statute id)

The cited-statute-id sub-parse is NOT a bespoke ``(\\d+)/(\\d+)`` scan here: the
NUMBER/YEAR → canonical ``YEAR/NUMBER`` statute id is built by the references
statute-id constructor (``cross_refs._make_statute_id``), the single source of
statute-id truth for this lane.

Unparsed input is never silently dropped: when the recognizer cannot confirm an
item-cited-version clause it returns ``matched=False`` and the caller keeps the
op; when the cited-version CUE is present but no statute id can be parsed from it
the result carries a typed :class:`CitedVersionParseResidual` so the unparsed
cue is accounted for rather than swallowed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.finland.references.cross_refs import _make_statute_id

# Cited statute id following the cited-version cue: ``sellaisena kuin … laissa
# 123/2019`` / ``… asetuksessa 5/2020``. The cue + head are recognized here; the
# NUMBER/YEAR id itself is handed to ``_make_statute_id`` (statute-id truth).
_FI_CITED_VERSION_ID_RE = compile_classifier_regex(
    r"\bsellais[ei][a-zäöå]*\s+kuin\b.{0,120}?\b(?:laissa|asetuksessa)\s+(\d{1,5})/(\d{4})",
    flags=re.I | re.S,
    classifier_id="fi.item_cited_version.statute_id",
)
# Target window anchor: ``{label} §:n`` (e.g. ``5 §:n``). Label is interpolated.
_FI_LOCAL_ITEM_TARGET_RE_TEMPLATE = r"(?<!\d){label}\s*§\s*:\s*n"
# Item-word cue inside the target's comma window (``kohta``/``kohdan``/…).
_FI_LOCAL_ITEM_WORD_RE = compile_classifier_regex(
    r"\bkoht[a-zäöå]*\b",
    flags=re.I | re.S,
    classifier_id="fi.item_cited_version.item_word",
)
# Cited-version provenance cue (``sellaisena/sellaisina kuin``).
_FI_CITED_VERSION_SELLAISENA_RE = compile_classifier_regex(
    r"\bsellais[ei][a-zäöå]*\s+kuin\b",
    flags=re.I | re.S,
    classifier_id="fi.item_cited_version.cited_version_cue",
)

CITED_VERSION_PARSE_RESIDUAL_RULE_ID = "fi.references.item_cited_version_parse_residual"


@dataclass(frozen=True, slots=True)
class CitedVersionParseResidual:
    """A recognized item-cited-version cue whose cited statute id did not parse.

    Emitted when the item-word + ``sellaisena kuin`` cited-version cue is present
    for the target window but no ``laissa/asetuksessa N/YYYY`` statute id could be
    extracted. The unparsed cue is surfaced rather than silently skipped.
    """

    rule_id: str
    target_label: str
    clause_text: str


@dataclass(frozen=True, slots=True)
class ItemCitedVersionClause:
    """Typed result of the item-scoped cited-version clause recognizer.

    Attributes:
        matched:       True when the target window carries an item-word +
                       ``sellaisena kuin`` cited-version cue.
        cited_statute_ids: canonical ``YEAR/NUMBER`` ids of the cited acts named
                       by the cue (empty when ``matched`` is False, or when the
                       cue parsed no id — see ``residual``).
        residual:      a :class:`CitedVersionParseResidual` when the cue matched
                       but no cited statute id parsed; otherwise None.
    """

    matched: bool
    cited_statute_ids: frozenset[str] = field(default_factory=frozenset)
    residual: CitedVersionParseResidual | None = None


@lru_cache(maxsize=512)
def _local_item_target_re(target_label: str):
    return compile_classifier_regex(
        _FI_LOCAL_ITEM_TARGET_RE_TEMPLATE.format(label=re.escape(target_label)),
        flags=re.I | re.S,
        classifier_id="fi.item_cited_version.target",
    )


def _text_has_local_item_cited_version(text: str, target_label: str) -> bool:
    """True when ``{label} §:n`` is followed by an item-word + cited-version cue."""
    for match in _local_item_target_re(target_label).finditer(text):
        tail = text[match.end() : match.end() + 220]
        comma_index = tail.find(",")
        semicolon_index = tail.find(";")
        if comma_index < 0:
            continue
        if 0 <= semicolon_index < comma_index:
            continue
        item_window = tail[:comma_index]
        if len(item_window) > 160:
            continue
        if _FI_LOCAL_ITEM_WORD_RE.search(item_window) is None:
            continue
        if _FI_CITED_VERSION_SELLAISENA_RE.search(tail[comma_index : comma_index + 100]) is not None:
            return True
    return False


def _cited_statute_ids(text: str) -> frozenset[str]:
    """Canonical ``YEAR/NUMBER`` ids named by the cited-version cue.

    The NUMBER/YEAR → ``YEAR/NUMBER`` statute-id build is routed to the shared
    references statute-id constructor, not minted inline.
    """
    return frozenset(
        _make_statute_id(match.group(2), match.group(1))
        for match in _FI_CITED_VERSION_ID_RE.finditer(text)
    )


@lru_cache(maxsize=8192)
def recognize_item_cited_version_clause(
    text: str, target_label: str
) -> ItemCitedVersionClause:
    """Recognize the item-scoped cited-version clause for ``target_label``.

    Single-pass over one source span: the target window (``{label} §:n``), the
    item-word cue (``koht*``), the cited-version cue (``sellaisena kuin``), and
    the cited statute id(s) (``laissa/asetuksessa N/YYYY``, id routed to the
    references statute-id constructor).

    Returns ``matched=False`` when the clause is not an item-cited-version clause
    for this target. When the cue matches but no cited statute id parses, the
    result is ``matched=True`` with a typed ``residual`` and no ids — never a
    silent skip.
    """
    if not _text_has_local_item_cited_version(text, target_label):
        return ItemCitedVersionClause(matched=False)
    cited_statute_ids = _cited_statute_ids(text)
    if not cited_statute_ids:
        return ItemCitedVersionClause(
            matched=True,
            residual=CitedVersionParseResidual(
                rule_id=CITED_VERSION_PARSE_RESIDUAL_RULE_ID,
                target_label=target_label,
                clause_text=text,
            ),
        )
    return ItemCitedVersionClause(
        matched=True,
        cited_statute_ids=cited_statute_ids,
    )
