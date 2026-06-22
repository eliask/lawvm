"""Citation routing for Finland amendment replay.

Pure string functions — no corpus access, no side effects.
Extracted from grafter.py (Phase H, lines 1799--1973).
"""
from __future__ import annotations

import re

from lawvm.finland.johtolause.affected_statute import (
    instrument_from_text,
    parse_routing_surface,
)
from lawvm.finland.morphology import (
    MorphCase,
    MorphNumber,
    generate_forms,
    head_entry,
    is_known_head,
)

# Closed statute/instrument heads the M1 morphology engine can inflect, sorted
# longest-first so a title ending in ``...asetus`` splits on ``asetus`` and never
# on a shorter coincidental suffix (mirrors
# ``references/registries/statute_name.py``, the reference implementation of
# "inflect a statute title head via M1").
_INFLECTABLE_HEADS: tuple[str, ...] = (
    "direktiivi",
    "ilmoitus",
    "määräys",
    "päätös",
    "sopimus",
    "asetus",
    "säädös",
    "ohje",
    "laki",
)

# Legacy string-slice genitive fallback, used ONLY when M1 returns no
# deterministic genitive for a head (so coverage never regresses below the old
# behavior). Maps a trailing nominative head -> its genitive surface.
_LEGACY_GENITIVE_BY_HEAD: dict[str, str] = {
    "laki": "lain",
    "asetus": "asetuksen",
    "päätös": "päätöksen",
}


def _split_title_head(norm_title: str) -> tuple[str, str] | None:
    """Split a normalized (lowercased) title into ``(modifier, head_lemma)``.

    Returns the invariant modifier prefix plus the closed-class head lemma the
    title ends with, or ``None`` if the title ends in no known statute head.
    """
    for head in sorted(_INFLECTABLE_HEADS, key=len, reverse=True):
        if norm_title.endswith(head):
            return norm_title[: len(norm_title) - len(head)], head
    return None


def _head_genitive_title(norm_title: str) -> str | None:
    """Return the genitive surface of ``norm_title`` via real head inflection.

    Splits off the closed-class head, inflects it through the M1 morphology
    engine, and re-attaches the invariant modifier (the same generation-first
    strategy as the M2 statute-name registry). Falls back to the legacy
    string-slice genitive when M1 declines to inflect the head
    (``certainty="unsupported"``) so coverage never regresses. Returns ``None``
    when the title ends in no recognized head.
    """
    split = _split_title_head(norm_title)
    if split is None:
        return None
    modifier, head = split
    if is_known_head(head):
        for form in generate_forms(
            head_entry(head),
            cases=(MorphCase.GEN,),
            numbers=(MorphNumber.SG,),
        ):
            if form.certainty == "deterministic" and form.surface:
                return modifier + form.surface
    legacy = _LEGACY_GENITIVE_BY_HEAD.get(head)
    if legacy is not None:
        return modifier + legacy
    return None

# Compiled at module scope per §1.11.  Two unbounded .* with re.DOTALL would
# cause O(N^2) backtracking on long non-matching inputs.
# Bounded to {0,400}? (lazy) — legitimate meta-repeal clauses are well under
# 400 chars per segment; 400 provides generous headroom.
_FI_META_REPEAL_RE = re.compile(
    r'kumotaan\b.{0,400}?muuttamisesta\s+.{0,400}?annetun\s+lain\s*\(\s*\d',
    re.IGNORECASE | re.DOTALL,
)

_FI_BARE_LEADING_META_REPEAL_RE = re.compile(
    r"^\s*kumotaan\s*\(\s*\d{1,4}\s*/\s*\d{2,4}\s*\)\s*,\s*(?P<rest>.{0,2500})",
    re.IGNORECASE | re.DOTALL,
)

_FI_VERBOSE_LEADING_META_REPEAL_RE = re.compile(
    r"^\s*kumotaan\b.{0,800}?muuttamisesta\s+"
    r"(?:annettu\s+(?:laki|asetus)|annetun\s+(?:lain|asetuksen))"
    r"\s*\(\s*\d{1,4}\s*/\s*\d{2,4}\s*\)\s*,\s*(?P<rest>.{0,2500})",
    re.IGNORECASE | re.DOTALL,
)


def _looks_like_fi_meta_repeal(text: str) -> bool:
    """Return True when *text* is a meta-repeal of a prior amendment act.

    Fast substring guards eliminate the regex path for the vast majority of
    inputs that obviously cannot match.  Lowercase once for guard comparisons
    since the regex uses re.IGNORECASE and input case may vary.
    """
    lo = text.lower()
    if 'muuttamisesta' not in lo:
        return False
    if 'annetun' not in lo:
        return False
    # lawvm-regex: owning_parser meta-repeal clause recognizer over this module's own johto input string (substring-guarded); not a cross-plane raw_text read
    return bool(_FI_META_REPEAL_RE.search(text))


def _title_looks_like_fi_meta_repeal(source_title: str) -> bool:
    """Return True for titles that repeal a prior amendment instrument.

    Some Finnish acts carry only a bare enacting formula in the preamble while
    the title itself says ``<parent> N §:n muuttamisesta annetun lain
    kumoamisesta``. That is lifecycle evidence about the amending instrument,
    not authorization to execute a repeal of ``N §`` against the parent statute.
    """
    lo = source_title.casefold()
    if "muuttamisesta" not in lo:
        return False
    return any(
        marker in lo
        for marker in (
            "annetun lain kumoamisesta",
            "annetun asetuksen kumoamisesta",
            "annetun valtioneuvoston asetuksen kumoamisesta",
        )
    )


OP_KEYWORDS = {
    'muutetaan', 'muutettu', 'muuttaa', 'muuttanut', 'muutettava',
    'kumotaan', 'kumottu', 'kumoaa', 'kumonnut',
    'lisätään', 'lisätty', 'lisää', 'lisännyt',
    'siirretään', 'siirretty', 'siirtää', 'siirtänyt',
}

def _target_head_matches_parent_metadata(
    *,
    johto: str,
    parent_title: str,
    parent_issue_date: str,
    source_title: str = "",
) -> bool:
    """Return True when non-citation metadata identifies the parent statute.

    This is the conservative typo gate for corrupt ``(NUM/YY)`` tokens in an
    otherwise clear affected-statute head.  It deliberately ignores edit
    distance and requires matching instrument plus either issue date or title.
    """
    head = parse_routing_surface(johto).affected_head
    if head is None:
        return False
    parent_instrument = instrument_from_text(parent_title)
    if not parent_instrument or head.instrument != parent_instrument:
        return False
    if "muuttamisesta annetun" in head.title_phrase.lower():
        return False

    variants = set(_parent_title_reference_variants(parent_title))
    variants.update(_source_title_target_reference_variants(source_title))
    target_norm = re.sub(r"\s+", " ", head.title_phrase.lower())
    title_matches = bool(variants) and any(variant in target_norm for variant in variants)
    if not title_matches:
        return False
    if parent_issue_date and head.issue_date is not None:
        return head.issue_date.isoformat() == parent_issue_date
    return True


def _looks_like_nojalla_authority_clause(johto: str) -> bool:
    return parse_routing_surface(johto).delegated_authority is not None


def _single_target_amending_act_title(source_title: str) -> bool:
    """Return True for a title naming one statute's amendment act."""
    source_norm = re.sub(r"\s+", " ", (source_title or "").strip().lower())
    if not source_norm:
        return False
    if any(token in source_norm for token in ("eräiden", "väliaikais", "voimaan", "kumoamisesta")):
        return False
    return bool(
        # lawvm-regex: owning_parser single-target amendment-title shape recognizer over this module's own normalized source_title; not a cross-plane raw_text read
        re.match(
            r"^(?:valtioneuvoston\s+)?(?:laki|asetus)\s+"
            r".+?\s+annetun\s+(?:lain|asetuksen)\s+muuttamisesta$",
            source_norm,
        )
    )


def _source_title_target_reference_variants(source_title: str) -> set[str]:
    """Return target-title surfaces from a single-target amendment title."""
    source_norm = re.sub(r"\s+", " ", (source_title or "").strip().lower())
    if not source_norm:
        return set()
    if any(token in source_norm for token in ("eräiden", "väliaikais", "voimaan", "kumoamisesta")):
        return set()
    # lawvm-regex: owning_parser amendment-title target-extraction recognizer over this module's own normalized source_title; not a cross-plane raw_text read
    match = re.match(
        r"^(?:valtioneuvoston\s+)?(?:laki|asetus)\s+(.+?)\s+muuttamisesta$",
        source_norm,
    )
    if match is None:
        return set()
    target = match.group(1).strip()
    if " annetun " not in target:
        return set()
    return {target} if target else set()


def _leading_meta_repeal_rest(johto: str) -> str | None:
    """Return base-operation text after a leading prior-amendment repeal."""
    if "kumotaan" not in johto.lower():
        return None
    for pattern in (_FI_BARE_LEADING_META_REPEAL_RE, _FI_VERBOSE_LEADING_META_REPEAL_RE):
        match = pattern.match(johto)
        if match is not None:
            rest = match.group("rest").strip()
            return rest or None
    return None


def _leading_meta_repeal_then_parent_ops(
    *,
    johto: str,
    parent_id: str,
    source_title: str,
) -> bool:
    """Return True when a prior-amendment repeal precedes base-statute ops.

    Finnish preambles can begin by repealing an earlier amending act, e.g.
    ``kumotaan (579/1994), muutetaan lain nimike ...``.  The citation belongs
    to the repealed amending act, not the base statute target.  Accept only
    when the source title is a single-target amendment title and the remaining
    operative text has no foreign target citation.
    """
    if not _single_target_amending_act_title(source_title):
        return False
    rest = _leading_meta_repeal_rest(johto)
    if rest is None:
        return False
    rest_lower = rest.lower()
    if not any(keyword in rest_lower for keyword in OP_KEYWORDS):
        return False
    return _johtolause_references_parent(rest, parent_id)


def _parent_title_reference_variants(parent_title: str) -> set[str]:
    """Return conservative title variants for parent-title matching."""
    norm = re.sub(r"\s+", " ", (parent_title or "").strip().lower())
    if not norm:
        return set()

    variants = {norm}

    # Genitive form via real M1 head inflection (covers laki/asetus/päätös and
    # the wider closed-head set), with a legacy string-slice fallback when M1
    # declines so coverage never regresses below the old laki/asetus slicing.
    genitive = _head_genitive_title(norm)
    if genitive is not None:
        variants.add(genitive)

    if norm.startswith("laki "):
        body = norm[5:].strip()
        if body:
            variants.add(f"{body} annetun lain")
    if norm.startswith("asetus "):
        body = norm[7:].strip()
        if body:
            variants.add(f"{body} annetun asetuksen")
    if norm.startswith("valtioneuvoston päätös "):
        body = norm.removeprefix("valtioneuvoston päätös ").strip(" .")
        if body:
            variants.add(f"{body} annetun valtioneuvoston päätöksen")

    return {v.strip() for v in variants if v.strip()}


def _title_targets_pending_amendment_of_parent(source_title: str, parent_title: str) -> bool:
    """Return True when the title targets a pending amending act of this parent.

    Examples:
    - ``Laki valmiuslain muuttamisesta annetun lain 88 ja 126 §:n muuttamisesta``
    - ``Laki valmiuslain 109 §:n muuttamisesta annetun lain muuttamisesta``
    """
    source_norm = re.sub(r"\s+", " ", (source_title or "").strip().lower())
    if not source_norm:
        return False
    if "muuttamisesta annetun lain" not in source_norm:
        return False
    if any(token in source_norm for token in ("eräiden", "kumoamisesta", "voimaantulosta")):
        return False

    parent_variants = _parent_title_reference_variants(parent_title)
    if not parent_variants:
        return False
    return any(variant in source_norm for variant in parent_variants)


def title_targets_pending_amendment_title(source_title: str, pending_title: str) -> bool:
    """Return True when ``source_title`` targets a cited pending amendment act.

    This is the title-side half of pending amendment composition for cases where
    the base statute has been renamed. The replay context may still carry the
    original parent title, while a later amendment-of-amendment names the already
    processed pending amending act by its own title:

    ``Laki ydinvastuulain muuttamisesta annetun lain muuttamisesta``
    targets pending act title ``Laki ydinvastuulain muuttamisesta``.

    The cited instrument id still has to come from routing/citation evidence;
    this helper only answers whether the two titles form that exact source-title
    family.
    """
    source_norm = re.sub(r"\s+", " ", (source_title or "").strip().lower())
    if not source_norm:
        return False
    if "muuttamisesta annetun lain" not in source_norm:
        return False
    if any(token in source_norm for token in ("eräiden", "kumoamisesta", "voimaantulosta")):
        return False

    pending_variants = _parent_title_reference_variants(pending_title)
    if not pending_variants:
        return False
    return any(variant in source_norm for variant in pending_variants)


def extract_pending_amendment_target_id(
    johto: str,
    amendment_id: str,
    source_title: str,
    parent_title: str,
) -> str | None:
    """Return the cited pending amendment id for amendment-of-amendment titles.

    This is intentionally conservative and only activates for the recognized
    ``pending_amendment_of_parent_skip`` title family.
    """
    if not _title_targets_pending_amendment_of_parent(source_title, parent_title):
        return None
    try:
        source_year = int(str(amendment_id).split("/", 1)[0])
    except (TypeError, ValueError, IndexError):
        return None
    surface = parse_routing_surface(johto, source_year=source_year)
    for citation in surface.target_citations:
        target_id = citation.normalized_id
        if target_id and target_id != amendment_id:
            return target_id
    return None


def _johtolause_references_parent(johto: str, parent_id: str) -> bool:
    """Return True if the johtolause is consistent with targeting parent_id.

    Scans for explicit statute references of the form (NUM/YY) or (NUM/YYYY).
    Only considers citations that appear BEFORE "sellaisena kuin" or "siihen
    myöhemmin tehtyine muutoksineen" clauses — those cite prior amendments,
    not the target statute.

    If no target-position citations found → True (can't tell, allow).
    If some found and at least one matches parent_id → True.
    If some found but NONE match parent_id → False (wrong statute).
    """
    return parse_routing_surface(johto).references_statute(parent_id)


def johtolause_cited_target_ids(johto: str, source_year: int) -> list[str]:
    """Normalized ``YEAR/NUM`` statute ids cited in a johtolause target zone.

    Mirrors the scan in :func:`_johtolause_references_parent`: only citations
    before a ``sellaisena kuin`` / ``siihen myöhemmin`` clause are considered
    target citations (the rest are prior-amendment references). Returns ids in
    first-seen order. Used to self-evidence ``citation_mismatch_skip`` /
    ``num_collision_skip`` diagnostics — so the message can name what the
    johtolause actually cites rather than just saying "a different statute".
    """
    return list(parse_routing_surface(johto, source_year=source_year).normalized_target_ids())


def _title_explicitly_targets_other_statute(source_title: str, parent_title: str) -> bool:
    """Return True when an amendment title clearly names another single target statute.

    This is a conservative backstop for cases where amendment_parents.csv pulls a
    statute into the wrong parent chain and the johtolause lacks explicit statute
    number citations. Only explicit single-target "... muuttamisesta" titles are
    considered; generic "eräiden ..." or other broad titles are ignored.
    """
    source_norm = re.sub(r'\s+', ' ', (source_title or '').strip().lower())
    parent_norm = re.sub(r'\s+', ' ', (parent_title or '').strip().lower())
    if not source_norm or not parent_norm:
        return False
    if parent_norm in source_norm:
        return False
    if 'muuttamisesta' not in source_norm:
        return False
    if 'annetun' not in source_norm:
        return False
    if any(token in source_norm for token in ('eräiden', 'väliaikais', 'voimaan', 'kumoamisesta')):
        return False
    # lawvm-regex: owning_parser amendment-title target-head recognizer over this module's own normalized source_title; not a cross-plane raw_text read
    m = re.match(
        r'^(?:valtioneuvoston\s+)?(?:laki|asetus)\s+(.+?\s+annetun\s+(?:lain|asetuksen))\s+muuttamisesta$',
        source_norm,
    )
    if not m:
        return False
    target_norm = m.group(1).strip()
    if not target_norm or parent_norm in target_norm:
        return False

    source_kind = 'laki' if source_norm.startswith('laki ') else 'asetus'
    parent_kind = ''
    # lawvm-regex: owning_parser instrument-kind classifier over this module's own normalized parent_title; not a cross-plane raw_text read
    if re.search(r'(?:^|\s)laki\b|laki$', parent_norm):
        parent_kind = 'laki'
    # lawvm-regex: owning_parser instrument-kind classifier over this module's own normalized parent_title; not a cross-plane raw_text read
    elif re.search(r'(?:^|\s)asetus\b|asetus$', parent_norm):
        parent_kind = 'asetus'

    return bool(parent_kind) and source_kind != parent_kind


def _single_target_title_names_other_statute(source_title: str, parent_title: str) -> bool:
    """Return True when a title names one different target statute.

    This broader title check is not sufficient by itself to reject routing:
    many legitimate amendments have sparse or inflected titles. It is only used
    at the processing boundary when a concrete VTS side-lane repeal for the
    current parent has also been extracted from source XML.
    """
    source_norm = re.sub(r"\s+", " ", (source_title or "").strip().lower())
    parent_norm = re.sub(r"\s+", " ", (parent_title or "").strip().lower())
    if not source_norm or not parent_norm:
        return False
    if "muuttamisesta" not in source_norm:
        return False
    if any(token in source_norm for token in ("eräiden", "väliaikais", "voimaan", "kumoamisesta")):
        return False
    # lawvm-regex: owning_parser amendment-title target-extraction recognizer over this module's own normalized source_title; not a cross-plane raw_text read
    match = re.match(
        r"^(?:valtioneuvoston\s+)?(?:laki|asetus)\s+(.+?)\s+muuttamisesta$",
        source_norm,
    )
    if not match:
        return False
    target_norm = match.group(1).strip()
    if not target_norm:
        return False
    parent_variants = _parent_title_reference_variants(parent_norm)
    if any(
        target_norm == variant or target_norm.startswith(f"{variant} ")
        for variant in parent_variants
    ):
        return False
    if any(separator in target_norm for separator in (",", " ja ", " sekä ")):
        return False
    return bool(parent_variants)


def route_amendment(
    citation_guard_johto: str,
    citation_guard_sec1: str,
    johto: str,
    parent_id: str,
    amendment_id: str,
    source_title: str = "",
    parent_title: str = "",
    parent_issue_date: str = "",
) -> tuple[bool, str]:
    """Decide whether an amendment should be applied to this parent statute.

    This is the citation routing layer: a pure function that reads only text
    strings and returns a routing decision. No side effects, no corpus access.

    Parameters
    ----------
    citation_guard_johto:
        Normalized johtolause text extracted from the preamble element
        (NOT the sec1 fallback). Used as the primary citation check.
        Pass empty string when no preamble exists.
    citation_guard_sec1:
        Normalized text of section 1 of the amendment act. Used as a
        secondary citation check when the preamble is terse/empty.
        Pass empty string when not available.
    johto:
        The working johtolause that may have been replaced by a sec1
        fallback (i.e. what PEG will parse). Used only for the
        meta-repeal pattern check — not for the primary citation check.
    parent_id:
        Finlex ID of the parent statute being replayed (e.g. "2009/953").
    amendment_id:
        Finlex ID of the amendment being routed (e.g. "2012/715").
    source_title:
        Title of the amendment statute (optional). Used for the
        title-based fallback mismatch check.
    parent_title:
        Title of the parent statute (optional). Used for the
        title-based fallback mismatch check.

    Returns
    -------
    (should_apply, reason) where reason is one of:
      "references_parent"      — johtolause cites the parent; apply
      "pending_amendment_of_parent_skip"
                               — title targets a pending amending act of this
                                 parent; recognized family but not yet applied
      "no_guard_needed"        — guard conditions not met (missing IDs or
                                 non-numeric amendment year); apply by default
      "num_collision_skip"     — amendment NUM == parent NUM, different year;
                                 johtolause targets a different statute
      "citation_mismatch_skip" — johtolause cites a different statute
                                 (meta-repeal or explicit foreign citation)
      "delegated_authority_nojalla_skip"
                               — johtolause cites an enabling statute in a
                                 ``säädetään ... nojalla`` authority clause
      "citation_typo_rewrite_parent_validated"
                               — citation token disagrees, but parent metadata
                                 validates the affected-statute head
      "leading_meta_repeal_then_parent_ops"
                               — a leading repealed-amendment citation was not
                                 treated as the base-statute target citation
    """
    # Guard condition: only run routing check when both IDs are present and
    # the amendment year is a digit string (replicates the inline condition).
    if not (parent_id and amendment_id and amendment_id.split("/")[0].isdigit()):
        return True, "no_guard_needed"

    # Both IDs must be well-formed ``YEAR/NUM`` so the downstream citation match
    # runs against real identifiers. A tuple missing its NUM part (no ``/``)
    # cannot be parsed — decline the guard rather than continuing the match with
    # empty identifiers, which would scan the johtolause against a malformed
    # parent id and could yield a spurious match or a wrong skip reason.
    amendment_parts = amendment_id.split("/")
    parent_parts = parent_id.split("/")
    if len(amendment_parts) < 2 or len(parent_parts) < 2:
        return True, "no_guard_needed"
    amendment_num = amendment_parts[1]
    parent_num = parent_parts[1]

    # Primary citation check: does the preamble reference the parent?
    _refs_match = (
        _johtolause_references_parent(citation_guard_johto, parent_id)
        if citation_guard_johto
        else True
    )
    apply_reason = "references_parent"

    # Secondary fallback: if preamble has no op keywords but sec1 cites the
    # parent, treat as a match (omnibus repeal acts with terse preamble).
    if (
        not _refs_match
        and citation_guard_sec1
        and not any(kw in citation_guard_johto.lower() for kw in OP_KEYWORDS)
        and _johtolause_references_parent(citation_guard_sec1, parent_id)
    ):
        _refs_match = True

    if not _refs_match:
        if _leading_meta_repeal_then_parent_ops(
            johto=citation_guard_johto or johto,
            parent_id=parent_id,
            source_title=source_title,
        ):
            _refs_match = True
            apply_reason = "leading_meta_repeal_then_parent_ops"
        else:
            if _title_targets_pending_amendment_of_parent(source_title, parent_title):
                return False, "pending_amendment_of_parent_skip"
            if _looks_like_nojalla_authority_clause(citation_guard_johto or johto):
                return False, "delegated_authority_nojalla_skip"
            if _target_head_matches_parent_metadata(
                johto=citation_guard_johto or johto,
                parent_title=parent_title,
                parent_issue_date=parent_issue_date,
                source_title=source_title,
            ):
                return True, "citation_typo_rewrite_parent_validated"
            if amendment_num and amendment_num == parent_num:
                # Tier 1: NUM collision — high confidence misroute (same number,
                # different year → amendment_parents.csv false-mapped by NUM).
                return False, "num_collision_skip"
            else:
                # Tier 2: johtolause explicitly cites a different statute.
                # Sub-case: meta-repeal targets a prior amendment act, not the parent.
                if _looks_like_fi_meta_repeal(johto):
                    return False, "citation_mismatch_skip"
                return False, "citation_mismatch_skip"

    # Even when the citation check passes, a title-based check can still
    # override: if the amendment title explicitly names a different statute.
    if _title_explicitly_targets_other_statute(source_title, parent_title):
        return False, "citation_mismatch_skip"

    return True, apply_reason
