"""Act-section → USC-address resolution for NON-positive-law U.S. Code titles.

The 27 *positive-law* USC titles (1, 11, 18, 28, 35, 38, 49, ...) are themselves
enacted text: an amending Public Law cites the USC directly ("Section 362 of
title 11, United States Code"), so the target address is read off the prose/href
with no mapping (handled by :mod:`lawvm.us_federal.amendatory`).

The 24 *non-positive* titles (15 Commerce, 26 Internal Revenue, 42 Public
Health, ...) are an OLRC *editorial* arrangement of free-standing Acts. An
amendment targets the originating Act ("Section 5 of the Securities Act of
1933"), and the OLRC editorially *classifies* that act-section into a USC
section. To replay such an amendment we need the **act-section → USC-address**
mapping. The official OLRC classification tables live on geo-blocked
``uscode.house.gov``.

This module answers the open question the jurisdiction profile poses: **can the
mapping be derived from govinfo-reachable data alone?** Two reachable channels
inside the *already-acquired* govinfo PLAW USLM XML carry the editorial
classification pre-applied by GPO's USLM converter:

1. the inline ``(N U.S.C. M)`` **parenthetical** in the amendment target phrase
   ("Section 5 of the Securities Act of 1933 (15 U.S.C. 77e) is amended"); and
2. the USLM ``<ref href="/us/usc/t15/s77e/...">`` **structural href** the
   converter attaches to the target citation.

A *structural* href is a USC address; a ``... note`` href is an editorial
cross-reference to an UNCODIFIED provision (a Statutes-at-Large note), not a
codified section, and is NOT a structural target.

Resolution policy (Prime Directive: no guessed mappings):

- both channels present and agree            -> ``paren_href_agree``
- both present, disagree                      -> ``href`` (USLM ref is canonical
  for the editorial landing), disagreement flagged in the typed witness
- only the structural href                    -> ``href``
- only the parenthetical                      -> ``paren``
- only a ``note`` href / neither              -> ``unmapped`` + a typed
  ``us_nonpositive_target_unmapped`` finding. The amendment is NEVER pointed at
  a guessed address; the classification table (geo-blocked) is the residual gap.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Iterable

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.amendatory import (
    _amending_actions,
    _first_usc_ref,
    _iter_instruction_units,
    _localname,
    _text_of,
)
from lawvm.us_federal.sources import (
    UsArchiveReader,
    list_plaw_identities,
    read_plaw_locator,
)

_USLM_NS = {"u": "http://schemas.gpo.gov/xml/uslm"}


class NonPositiveResolveStatus(StrEnum):
    """Closed set of act-section -> USC-address resolution outcomes.

    A ``StrEnum`` so existing string consumers (f-strings, JSON dict keys,
    ``Counter`` keys, test ``== "..."`` comparisons) keep working byte-for-byte
    while the value set is closed and dispatch can be made exhaustive.
    """

    PAREN_HREF_AGREE = "paren_href_agree"
    """Both the parenthetical and the structural href resolved and agree."""

    HREF = "href"
    """Resolved via the USLM structural href (sole channel, or canonical on disagreement)."""

    PAREN = "paren"
    """Resolved via the inline ``(N U.S.C. M)`` parenthetical only."""

    NOTE_ONLY = "note_only"
    """Only a ``note`` editorial cross-ref to an UNCODIFIED provision — unmapped."""

    TABLE3 = "table3"
    """Resolved from the OLRC Table III classification superset (act-section -> USC)."""

    ACT_NAME_TABLE3 = "act_name_table3"
    """Resolved by mapping a NAMED act citation -> originating act key -> Table III."""

    UNMAPPED = "unmapped"
    """No reachable channel yielded a codified section (residual OLRC-table gap)."""

# Witness / finding rule ids (stable).
RULE_PAREN = "us_nonpositive_target_via_paren"
RULE_HREF = "us_nonpositive_target_via_href"
RULE_PAREN_HREF_AGREE = "us_nonpositive_target_paren_href_agree"
RULE_PAREN_HREF_DISAGREE = "us_nonpositive_target_paren_href_disagree"
RULE_TABLE3 = "us_nonpositive_target_via_table3"
RULE_TABLE3_HREF_AGREE = "us_nonpositive_target_table3_href_agree"
RULE_TABLE3_HREF_DISAGREE = "us_nonpositive_target_table3_href_disagree"
RULE_ACT_NAME_TABLE3 = "us_nonpositive_target_via_act_name_table3"
ACT_NAME_AMBIGUOUS_FINDING_RULE_ID = "us_nonpositive_target_act_name_ambiguous"
UNMAPPED_FINDING_RULE_ID = "us_nonpositive_target_unmapped"
NOTE_ONLY_FINDING_RULE_ID = "us_nonpositive_target_note_only"

# The 27 positive-law USC titles (enacted as positive law by Congress). An
# amendment whose resolved title is one of these is NOT a non-positive target and
# is handled by the direct USC-address path, not this module.
POSITIVE_LAW_TITLES: frozenset[int] = frozenset(
    {1, 3, 4, 5, 9, 10, 11, 13, 14, 17, 18, 23, 28, 31, 32, 35, 36, 37, 38, 39,
     40, 41, 44, 46, 49, 51, 54}
)

# USC nesting ladder (deepest-last), shared with the amendatory address adapter.
_USC_LEVELS = ("subsection", "paragraph", "subparagraph", "clause", "subclause", "item")

# Inline "(15 U.S.C. 77e)" / "(26 U.S.C. 461(l)(1))" / "(42 U.S.C. App. 1234)".
# Captures title, section, and the parenthesized sub-segment tail.
_PAREN_CITE_RE = re.compile(
    r"\(\s*(?P<title>\d+)\s+U\.?\s*S\.?\s*C\.?\s+(?:App\.\s+)?"
    r"(?P<section>\d+[A-Za-z]*(?:[-‐‑‒–]\d+)?)"
    # The section number must end at a non-alphanumeric boundary (so greedy
    # backtracking cannot split "9999 note" into section "999" + " note").
    r"(?![0-9A-Za-z])"
    r"(?P<segments>(?:\s*\([0-9A-Za-z]+\))*)"
    # A cite followed by "note" / "et seq." is an editorial cross-ref to an
    # UNCODIFIED provision (a Statutes-at-Large note / range), not a codified
    # section target. A negative lookahead drops it so it is never mapped.
    r"(?!\s+(?:note\b|et\s+seq))"
)
_SEGMENT_RE = re.compile(r"\(([0-9A-Za-z]+)\)")

# USLM structural href: /us/usc/t15/s77e/a/1  (NOT /us/pl/..., /us/stat/...).
_HREF_RE = re.compile(
    r"^/us/usc/t(?P<title>\d+)/s(?P<section>\d+[A-Za-z]*(?:[-‐‑‒–]\d+)?)"
    r"(?P<rest>(?:/[^/]+)*)$"
)
# Non-structural href tails that carry a citation facet, not addressable
# sub-structure: a ``note`` ref is an editorial cross-ref to an UNCODIFIED
# provision (Statutes-at-Large note), never a codified section target.
_NON_STRUCTURAL_TAIL = frozenset({"note", "etseq", "et_seq"})


def is_positive_law_title(title: int) -> bool:
    """Return True for the 27 positive-law USC titles (direct-address path)."""
    return int(title) in POSITIVE_LAW_TITLES


def _label_level(label: str, index: int) -> str:
    """Infer the USC segment kind for a positional label (shared convention).

    Mirrors :func:`lawvm.us_federal.amendatory._label_level`: USC labels are
    positional ((a) subsection, (1) paragraph, (A) subparagraph, (i) clause,
    (I) subclause). The label *form* disambiguates; nesting depth is the floor.
    """
    stripped = label.strip()
    expected = _USC_LEVELS[min(index, len(_USC_LEVELS) - 1)]
    if stripped[:1].isdigit():
        kind = "paragraph"
    elif re.fullmatch(r"[ivxl]+", stripped):
        # A single lowercase letter that is ALSO a roman numeral (i, v, x, l, c)
        # is ambiguous: it is a roman-numeral clause only when nesting position
        # expects a clause/subclause; at a shallower expected level it is a
        # subsection letter (e.g. IRC "(l)" is a subsection, not clause "l").
        if len(stripped) == 1 and expected in ("subsection", "paragraph", "subparagraph"):
            kind = "subsection"
        else:
            kind = "clause"
    elif re.fullmatch(r"[IVXL]+", stripped):
        if len(stripped) == 1 and expected in ("subsection", "paragraph", "subparagraph"):
            kind = "subparagraph"
        else:
            kind = "subclause"
    elif stripped.islower():
        kind = "subsection"
    elif stripped.isupper():
        kind = "subparagraph"
    else:
        kind = "subsection"
    if _USC_LEVELS.index(kind) < _USC_LEVELS.index(expected):
        return expected
    return kind


@dataclass(frozen=True)
class NonPositiveTargetWitness:
    """Typed witness for a resolved (or unresolved) non-positive-title target.

    ``address`` is the resolved USC :class:`LegalAddress` (``None`` when
    unmapped). ``resolve_status`` is a :class:`NonPositiveResolveStatus`.
    ``rule_id`` is the stable witness/finding id. ``paren_title``/``href_title``
    record what each channel saw (for the agreement audit). ``finding`` is
    implied by an unmapped/note_only status.
    """

    resolve_status: NonPositiveResolveStatus
    rule_id: str
    address: LegalAddress | None
    paren_cite: str = ""
    href: str = ""
    paren_title: int | None = None
    href_title: int | None = None
    note_href: str = ""
    target_phrase: str = ""
    table3_usckey: str = ""
    table3_status: str = ""
    act_name: str = ""
    act_name_key: str = ""

    @property
    def resolved(self) -> bool:
        return self.address is not None

    @property
    def title(self) -> int | None:
        if self.address is not None and self.address.path:
            return int(self.address.path[0][1])
        return self.paren_title or self.href_title

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "resolve_status": self.resolve_status,
            "rule_id": self.rule_id,
            "address": str(self.address) if self.address is not None else "",
            "resolved": self.resolved,
            "paren_cite": self.paren_cite,
            "href": self.href,
            "paren_title": self.paren_title,
            "href_title": self.href_title,
            "note_href": self.note_href,
            "target_phrase": self.target_phrase,
            "table3_usckey": self.table3_usckey,
            "table3_status": self.table3_status,
            "act_name": self.act_name,
            "act_name_key": self.act_name_key,
        }


def parse_usc_paren_cite(text: str) -> LegalAddress | None:
    """Parse an inline ``(N U.S.C. M(...))`` parenthetical into a USC address.

    Returns ``None`` when ``text`` carries no ``(N U.S.C. M)`` parenthetical.
    The address is the pinned convention ``("title", N) -> ("section", M) ->
    typed sub-segments``.
    """
    match = _PAREN_CITE_RE.search(text)
    if match is None:
        return None
    path: list[tuple[str, str]] = [
        ("title", str(int(match.group("title")))),
        ("section", match.group("section")),
    ]
    for i, seg in enumerate(_SEGMENT_RE.findall(match.group("segments") or "")):
        path.append((_label_level(seg, i), seg))
    return LegalAddress(path=tuple(path))


def _is_structural_href(href: str) -> bool:
    """True when ``href`` is a structural USC ref (not a ``note`` cross-ref)."""
    match = _HREF_RE.match(href.strip())
    if match is None:
        return False
    rest = match.group("rest") or ""
    tail = [s for s in rest.split("/") if s]
    # A ref whose ONLY tail segment is a non-structural facet (``note``) is an
    # editorial cross-reference, not a codified-section target.
    if tail and all(seg in _NON_STRUCTURAL_TAIL for seg in tail):
        return False
    return True


def parse_usc_structural_href(href: str) -> LegalAddress | None:
    """Parse a structural ``/us/usc/tN/sM/...`` href into a USC address.

    Returns ``None`` for a non-USC href or a ``note``-only editorial cross-ref.
    Non-structural facet tails (``note``/``etseq``) are dropped from a mixed ref.
    """
    match = _HREF_RE.match(href.strip())
    if match is None:
        return None
    rest = match.group("rest") or ""
    structural = [s for s in rest.split("/") if s and s not in _NON_STRUCTURAL_TAIL]
    raw_tail = [s for s in rest.split("/") if s]
    # Pure ``note``/``etseq`` cross-ref: not a codified section target.
    if raw_tail and not structural:
        return None
    path: list[tuple[str, str]] = [
        ("title", str(int(match.group("title")))),
        ("section", match.group("section")),
    ]
    for idx, seg in enumerate(structural):
        path.append((_label_level(seg, idx), seg))
    return LegalAddress(path=tuple(path))


def _href_title(href: str) -> int | None:
    match = _HREF_RE.match(href.strip())
    if match is None:
        return None
    return int(match.group("title"))


# A NAMED-act citation in the amendment target prose: "Section 5 of the
# Securities Act of 1933", "section 1902(a) of the Social Security Act". Captures
# the cited act-section and the act's popular name (up to the closing "Act"/"Code",
# optionally with a trailing "of YYYY"). The act name is what the
# :class:`PopularNameRegistry` keys on; the section is the act-section Table III
# expects (NOT the amending PL's section — that is the structural miss this lane
# closes). A trailing "(N U.S.C. M)" / "[...]" parenthetical is excluded from the
# name by stopping the name at the first "Act"/"Code" head plus optional year.
_NAMED_ACT_CITE_RE = re.compile(
    r"\b[Ss]ection\s+(?P<section>\d+[A-Za-z]*(?:\([0-9A-Za-z]+\))*)\s+of\s+the\s+"
    r"(?P<name>[A-Z][A-Za-z0-9 ,.&'’-]*?(?:Act|Code)(?:\s+of\s+\d{4})?)"
    r"(?=[\s,.;:)\[]|$)"
)


@dataclass(frozen=True, slots=True)
class NamedActCitation:
    """A parsed ``Section <sec> of the <Act Name>`` citation."""

    act_name: str
    act_section: str


def parse_named_act_citation(text: str) -> NamedActCitation | None:
    """Parse the first ``Section <sec> of the <Act Name>`` citation in ``text``.

    Returns ``None`` when the text carries no such named-act citation. The act
    name is the popular name a :class:`PopularNameRegistry` keys on; the section is
    the ACT's own section (the join key Table III expects), distinct from the
    amending Public Law's section.
    """
    m = _NAMED_ACT_CITE_RE.search(text or "")
    if m is None:
        return None
    return NamedActCitation(
        act_name=m.group("name").strip(),
        act_section=m.group("section").strip(),
    )


@dataclass(frozen=True, slots=True)
class _ActNameLaneResult:
    """Internal carrier for the named-act lane (address + audit + refusal reason)."""

    address: LegalAddress | None
    act_name: str = ""
    act_key: str = ""
    t3_usckey: str = ""
    t3_status: str = ""
    name_ambiguous: bool = False


def _resolve_via_act_name(
    *,
    registry: Any,
    table3: Any,
    target_phrase: str,
    raw_text: str,
) -> _ActNameLaneResult:
    """Resolve a NAMED-act citation to a USC address via registry -> Table III.

    ``address`` is ``None`` unless the act name resolves to exactly one originating
    act key AND Table III classifies that ``(act-key, act-section)`` to a codified
    address. An AMBIGUOUS/UNMAPPED act name, or an ambiguous/unmapped Table III
    section, all refuse (``address is None``) — never a guessed mapping (§1.7).
    ``name_ambiguous`` distinguishes an act-name §1.7 refusal so the holdout is
    surfaced as a distinct finding rather than a bare ``unmapped``.
    """
    cite = parse_named_act_citation(target_phrase) or parse_named_act_citation(raw_text)
    if cite is None:
        return _ActNameLaneResult(address=None)
    if registry is None:
        from lawvm.us_federal.act_name_registry import load_default_act_name_registry

        registry = load_default_act_name_registry()
        if registry is None:
            return _ActNameLaneResult(address=None, act_name=cite.act_name)
    name_res = registry.resolve(cite.act_name)
    if not name_res.resolved:
        # AMBIGUOUS / UNMAPPED act name — refused, never guessed onto one act.
        from lawvm.us_federal.act_name_registry import ActNameStatus

        return _ActNameLaneResult(
            address=None,
            act_name=cite.act_name,
            name_ambiguous=name_res.status is ActNameStatus.AMBIGUOUS,
        )
    t3 = _resolve_table3(table3, name_res.act_key, cite.act_section)
    if t3 is None:
        return _ActNameLaneResult(
            address=None, act_name=cite.act_name, act_key=name_res.act_key
        )
    return _ActNameLaneResult(
        address=t3.address,
        act_name=cite.act_name,
        act_key=name_res.act_key,
        t3_usckey=t3.usckey,
        t3_status=t3.usc_status,
    )


def _resolve_table3(table3: Any, act_key: str, act_section: str) -> Any:
    """Run the Table III resolver, returning a CLASSIFIED resolution or ``None``.

    Returns the :class:`~lawvm.us_federal.table3.Table3Resolution` only when it
    yields a *codified* USC address (``status == CLASSIFIED``). An UNCODIFIED
    ``nt`` row, an AMBIGUOUS set (§1.7 refusal), or no match all return ``None``
    so the holdout/refusal is preserved — Table III never guesses a section.
    """
    if not act_key or not act_section:
        return None
    if table3 is None:
        # No explicit resolver supplied: consult the lazily-loaded default built
        # from the farchive-resident Table III bulk XML (None when the table is
        # absent/disabled, so the baseline inherit-or-refuse behaviour stands).
        from lawvm.us_federal.table3 import load_default_table3_resolver

        table3 = load_default_table3_resolver()
        if table3 is None:
            return None
    resolution = table3.resolve(act_key, act_section)
    if resolution.address is not None:
        return resolution
    return None


def _annotate_with_table3(
    witness: NonPositiveTargetWitness,
    *,
    t3_addr: LegalAddress | None,
    t3_usckey: str,
    t3_status: str,
) -> NonPositiveTargetWitness:
    """Adjudicate an existing-channel witness against a Table III resolution (§1.7).

    The existing govinfo witness (``href``/``paren``) is the chosen address — it is
    NEVER overwritten by Table III. When Table III also resolved, the two are
    compared and the witness is re-stamped with a table3 rule id recording the
    verdict (agree/disagree) plus the Table III audit fields; when Table III is
    absent the original witness is returned byte-for-byte (backward compatible).
    """
    if t3_addr is None or witness.address is None:
        return witness
    agree = witness.address.path == t3_addr.path
    return replace(
        witness,
        rule_id=RULE_TABLE3_HREF_AGREE if agree else RULE_TABLE3_HREF_DISAGREE,
        table3_usckey=t3_usckey,
        table3_status=t3_status,
    )


def resolve_nonpositive_target(
    *,
    target_phrase: str = "",
    target_href: str = "",
    raw_text: str = "",
    table3: Any = None,
    act_key: str = "",
    act_section: str = "",
    act_name_registry: Any = None,
    act_name_source: str = "",
) -> NonPositiveTargetWitness:
    """Resolve an act-section amendment target to a USC address.

    Inputs are the same the amendatory lowering already extracts: the target
    ``target_phrase`` ("Section 5 of the Securities Act of 1933 (15 U.S.C. 77e)"),
    the USLM ``target_href`` of the target ref, and the unit ``raw_text`` (used
    only as a fallback parenthetical source).

    Resolution order (Prime Directive: no guessed mappings):

    1. GPO pre-applied structural href / inline ``(N U.S.C. M)`` parenthetical
       (the existing inherit-or-refuse channels);
    2. **Table III** (NEW): when a ``table3`` :class:`~lawvm.us_federal.table3.Table3Resolver`
       and an ``act_key`` + ``act_section`` are supplied, the deterministic OLRC
       Statutes-at-Large -> USC classification. A repealed/eliminated row is still
       a real mapping (its status is surfaced); a ``nt`` uncodified row is held
       OUT, never guessed onto a section;
    3. **Act-name -> Table III** (NEW): when every channel above missed and the
       target prose (``target_phrase`` / the dedicated ``act_name_source``) CITES a
       named act ("section 1902(a) of the Social Security Act"), the
       :class:`~lawvm.us_federal.act_name_registry.PopularNameRegistry` maps the
       popular name -> originating act key (grounded in OLRC short-title
       statements) and Table III runs on ``(originating act key, the ACT's own
       section)`` — the join key the structural boundary never knew. This is the
       half that makes the in-corpus Table III join fire (``RULE_ACT_NAME_TABLE3``).
       An AMBIGUOUS name is refused distinctly
       (``us_nonpositive_target_act_name_ambiguous``); an UNMAPPED name or an
       ambiguous/uncodified Table III section falls through to the refusal below.
    4. ``us_nonpositive_target_unmapped`` refusal only when ALL fail.

    ``act_name_source`` is a SEPARATE source for the named-act citation only (the
    instruction's raw body) — it is NOT consulted as a parenthetical/href fallback,
    so a stray ``(N U.S.C. M)`` cross-citation in the body can never hijack the
    target; the named-act parser only matches a ``Section <sec> of the <Act Name>``
    shape.

    **Agreement adjudication (§1.7):** when Table III AND an existing
    href/parenthetical both resolve, the two are compared. Agreement -> covered
    (rule id ``RULE_TABLE3_HREF_AGREE``); disagreement -> the existing witness is
    kept as the chosen address and the divergence is flagged in the witness
    (``RULE_TABLE3_HREF_DISAGREE``); neither is silently overwritten.

    Returns a :class:`NonPositiveTargetWitness` whose ``address`` is the resolved
    USC address, or ``None`` with a typed finding rule id when no reachable channel
    yields a codified section.
    """
    paren_addr = parse_usc_paren_cite(target_phrase) or parse_usc_paren_cite(raw_text)
    paren_cite = ""
    pm = _PAREN_CITE_RE.search(target_phrase) or _PAREN_CITE_RE.search(raw_text)
    if pm is not None:
        paren_cite = pm.group(0)

    href_addr = parse_usc_structural_href(target_href) if target_href else None
    href_t = _href_title(target_href) if target_href else None
    note_only = bool(
        target_href and href_addr is None and href_t is not None
        and not _is_structural_href(target_href)
    )

    paren_t = int(paren_addr.path[0][1]) if paren_addr is not None and paren_addr.path else None

    # Step 2 (NEW): the deterministic Table III classification. Computed once;
    # ``None`` unless a resolver + act key + act-section were supplied AND the
    # lookup yields a *codified* address (uncodified ``nt`` rows stay held out).
    t3 = _resolve_table3(table3, act_key, act_section)
    t3_addr = t3.address if t3 is not None else None
    t3_usckey = t3.usckey if t3 is not None else ""
    t3_status = t3.usc_status if t3 is not None else ""

    # Both existing channels resolved structurally.
    if paren_addr is not None and href_addr is not None:
        base_status, base_rule, base_addr = (
            (NonPositiveResolveStatus.PAREN_HREF_AGREE, RULE_PAREN_HREF_AGREE, href_addr)
            if paren_addr.path == href_addr.path
            # Disagree: the USLM ref is the converter's editorial landing and is
            # canonical for *where* the amendment lands; the parenthetical is the
            # drafter's cite (can be coarser, e.g. section-only). Take the href but
            # flag the disagreement in the witness so review sees it.
            else (NonPositiveResolveStatus.HREF, RULE_PAREN_HREF_DISAGREE, href_addr)
        )
        return _annotate_with_table3(
            NonPositiveTargetWitness(
                resolve_status=base_status,
                rule_id=base_rule,
                address=base_addr,
                paren_cite=paren_cite,
                href=target_href,
                paren_title=paren_t,
                href_title=href_t,
                target_phrase=target_phrase,
            ),
            t3_addr=t3_addr,
            t3_usckey=t3_usckey,
            t3_status=t3_status,
        )

    if href_addr is not None:
        return _annotate_with_table3(
            NonPositiveTargetWitness(
                resolve_status=NonPositiveResolveStatus.HREF,
                rule_id=RULE_HREF,
                address=href_addr,
                href=target_href,
                href_title=href_t,
                target_phrase=target_phrase,
            ),
            t3_addr=t3_addr,
            t3_usckey=t3_usckey,
            t3_status=t3_status,
        )

    if paren_addr is not None:
        return _annotate_with_table3(
            NonPositiveTargetWitness(
                resolve_status=NonPositiveResolveStatus.PAREN,
                rule_id=RULE_PAREN,
                address=paren_addr,
                paren_cite=paren_cite,
                paren_title=paren_t,
                target_phrase=target_phrase,
            ),
            t3_addr=t3_addr,
            t3_usckey=t3_usckey,
            t3_status=t3_status,
        )

    # No existing govinfo channel resolved. Table III is the deterministic
    # classification source — the residual ``us_nonpositive_target_unmapped`` gap
    # this capability dissolves. A repealed/eliminated classification is still a
    # real mapping (status surfaced); a ``nt`` row was already held out above.
    if t3_addr is not None:
        return NonPositiveTargetWitness(
            resolve_status=NonPositiveResolveStatus.TABLE3,
            rule_id=RULE_TABLE3,
            address=t3_addr,
            href_title=int(t3_addr.path[0][1]) if t3_addr.path else None,
            target_phrase=target_phrase,
            table3_usckey=t3_usckey,
            table3_status=t3_status,
        )

    # NAMED-ACT lane (NEW): the existing href/parenthetical channels and the
    # direct (amending-PL-keyed) Table III channel all missed. When the target
    # prose CITES a named act ("section 1902(a) of the Social Security Act"), map
    # the popular name -> originating act key (the :class:`PopularNameRegistry`,
    # grounded in OLRC short-title statements) and run Table III on
    # ``(originating act key, the ACT's own section)`` — the join key Table III
    # expects, which the structural boundary never knew. This is the half that
    # makes the in-corpus Table III join fire. The registry refuses an
    # AMBIGUOUS/UNMAPPED name (never guesses one act); Table III refuses an
    # ambiguous/uncodified section — so this lane only ever yields a codified
    # address, never a guess (§1.7). It is purely ADDITIONAL: it fires only after
    # every existing channel and refusal above has been exhausted.
    an = _resolve_via_act_name(
        registry=act_name_registry,
        table3=table3,
        target_phrase=target_phrase,
        raw_text=act_name_source or raw_text,
    )
    if an.address is not None:
        return NonPositiveTargetWitness(
            resolve_status=NonPositiveResolveStatus.ACT_NAME_TABLE3,
            rule_id=RULE_ACT_NAME_TABLE3,
            address=an.address,
            href_title=int(an.address.path[0][1]) if an.address.path else None,
            target_phrase=target_phrase,
            table3_usckey=an.t3_usckey,
            table3_status=an.t3_status,
            act_name=an.act_name,
            act_name_key=an.act_key,
        )
    # A named-act citation whose popular name grounds to SEVERAL distinct acts is a
    # §1.7 refusal — surfaced distinctly from the bare unmapped case (the name is
    # auditable), but still NOT pointed at any guessed address.
    if an.name_ambiguous:
        return NonPositiveTargetWitness(
            resolve_status=NonPositiveResolveStatus.UNMAPPED,
            rule_id=ACT_NAME_AMBIGUOUS_FINDING_RULE_ID,
            address=None,
            target_phrase=target_phrase,
            act_name=an.act_name,
        )

    # Nothing structural. A ``note``-only href is the common shape of an
    # amendment to an UNCODIFIED provision (a Statutes-at-Large note); record it
    # distinctly from the bare no-signal case, but both stay unmapped.
    if note_only:
        return NonPositiveTargetWitness(
            resolve_status=NonPositiveResolveStatus.NOTE_ONLY,
            rule_id=NOTE_ONLY_FINDING_RULE_ID,
            address=None,
            href=target_href,
            note_href=target_href,
            href_title=href_t,
            target_phrase=target_phrase,
        )
    return NonPositiveTargetWitness(
        resolve_status=NonPositiveResolveStatus.UNMAPPED,
        rule_id=UNMAPPED_FINDING_RULE_ID,
        address=None,
        target_phrase=target_phrase,
    )


# ---------------------------------------------------------------------------
# Title-window feasibility measurement (resolve-rate over real PLAW data)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NonPositiveResolveReport:
    """Resolve-rate report for one non-positive title over a Congress window.

    The honest feasibility number: of all amendment instruction-units whose
    target lands on ``title``, how many resolve to a USC address via reachable
    govinfo channels (``resolved``) vs need the geo-blocked OLRC classification
    table (``unmapped`` + ``note_only``).
    """

    title: int
    congress_window: tuple[int, ...]
    units: int = 0
    resolved: int = 0
    unmapped: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    rule_counts: dict[str, int] = field(default_factory=dict)
    unmapped_samples: list[dict[str, str]] = field(default_factory=list)

    @property
    def resolve_rate(self) -> float:
        return self.resolved / self.units if self.units else 0.0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "us_federal",
            "report_kind": "nonpositive_resolve_rate",
            "title": self.title,
            "positive_law_title": is_positive_law_title(self.title),
            "congress_window": list(self.congress_window),
            "units": self.units,
            "resolved": self.resolved,
            "unmapped": self.unmapped,
            "resolve_rate": round(self.resolve_rate, 4),
            "status_counts": dict(sorted(self.status_counts.items())),
            "rule_counts": dict(sorted(self.rule_counts.items())),
            "unmapped_samples": self.unmapped_samples,
        }


def _first_any_usc_href(unit: ET.Element) -> str:
    """First USC ``<ref>`` href in the unit, INCLUDING ``note`` cross-refs.

    The amendatory ``_first_usc_ref`` deliberately skips ``... note`` refs (they
    are not structural amendment targets). Here we want the note href too, so a
    note-only target is surfaced as ``note_only`` (carrying its title) rather
    than silently dropping out of the resolve-rate denominator.
    """
    for ref in unit.iter():
        if _localname(ref.tag) != "ref":
            continue
        href = ref.get("href", "")
        if "/usc/" in href and _HREF_RE.match(href):
            return href
    return ""


def _structural_target_href(unit: ET.Element, section_href: str) -> str:
    """First structural (non-note) USC href in the unit, else any USC href.

    Prefers a structural href (a codified target). Falls back to the section's
    structural href, then to any USC href in the unit (a ``note`` cross-ref) so a
    note-only target is still surfaced (and resolved to ``note_only``).
    """
    _phrase, href = _first_usc_ref(unit)
    if href and _is_structural_href(href):
        return href
    if section_href and _is_structural_href(section_href):
        return section_href
    return href or _first_any_usc_href(unit) or section_href


def iter_nonpositive_targets(
    data: bytes, *, title: int
) -> Iterable[NonPositiveTargetWitness]:
    """Yield a resolution witness per amendment unit whose target lands on ``title``.

    Reuses the amendatory unit iterator over one Public Law's USLM XML, resolves
    each unit's target via :func:`resolve_nonpositive_target`, and keeps only the
    witnesses whose resolved-or-cited title equals ``title``.
    """
    root = ET.fromstring(data)
    main = root.find(".//u:main", _USLM_NS)
    if main is None:
        return
    for section in main.iter():
        if _localname(section.tag) != "section":
            continue
        if not any(_localname(a.tag) == "amendingAction" for a in section.iter()):
            continue
        section_content = section.find("u:content", _USLM_NS)
        sec_phrase, sec_href = ("", "")
        if section_content is not None:
            sec_phrase, sec_href = _first_usc_ref(section_content)
        for (
            _uid,
            unit,
            _inherited,
            _effective,
            _expires,
            _via_class,
            _scope_text,
        ) in _iter_instruction_units(section):
            if not _amending_actions(unit):
                continue
            unit_phrase, _unit_href = _first_usc_ref(unit)
            target_phrase = unit_phrase or sec_phrase
            target_href = _structural_target_href(unit, sec_href)
            raw_text = _text_of(unit)
            witness = resolve_nonpositive_target(
                target_phrase=target_phrase,
                target_href=target_href,
                raw_text=raw_text,
            )
            if witness.title == title:
                yield witness


def measure_nonpositive_resolve_rate(
    archive: UsArchiveReader,
    *,
    title: int,
    congress_window: Iterable[int],
    max_unmapped_samples: int = 12,
) -> NonPositiveResolveReport:
    """Measure the govinfo-only act→USC resolve-rate for one non-positive title.

    Scans every Public Law in ``congress_window``, resolves each amendment unit
    targeting ``title``, and aggregates resolved-vs-unmapped counts. This is the
    feasibility number for ``us/spec/US_NONPOSITIVE_TITLES.md``.
    """
    window = tuple(sorted({int(c) for c in congress_window}))
    status_counts: Counter[str] = Counter()
    rule_counts: Counter[str] = Counter()
    units = resolved = unmapped = 0
    samples: list[dict[str, str]] = []

    for ident in list_plaw_identities(archive):
        if ident.congress not in window or not ident.is_public:
            continue
        data = read_plaw_locator(archive, ident.locator)
        if data is None:
            continue
        # Cheap pre-filter: skip laws that never mention this title.
        needle_href = f"/us/usc/t{title}/".encode()
        needle_cite = f"{title} U.S.C.".encode()
        needle_cite2 = f"{title} USC".encode()
        if needle_href not in data and needle_cite not in data and needle_cite2 not in data:
            continue
        for witness in iter_nonpositive_targets(data, title=title):
            units += 1
            status_counts[witness.resolve_status] += 1
            rule_counts[witness.rule_id] += 1
            if witness.resolved:
                resolved += 1
            else:
                unmapped += 1
                if len(samples) < max_unmapped_samples:
                    samples.append(
                        {
                            "public_law": ident.public_law_label,
                            "resolve_status": witness.resolve_status,
                            "target_phrase": witness.target_phrase[:160],
                            "note_href": witness.note_href,
                        }
                    )

    return NonPositiveResolveReport(
        title=title,
        congress_window=window,
        units=units,
        resolved=resolved,
        unmapped=unmapped,
        status_counts=dict(status_counts),
        rule_counts=dict(rule_counts),
        unmapped_samples=samples,
    )
