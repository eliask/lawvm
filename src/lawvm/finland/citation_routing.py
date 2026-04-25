"""Citation routing for Finland amendment replay.

Pure string functions — no corpus access, no side effects.
Extracted from grafter.py (Phase H, lines 1799--1973).
"""
from __future__ import annotations

import re

OP_KEYWORDS = {
    'muutetaan', 'muutettu', 'muuttaa', 'muuttanut',
    'kumotaan', 'kumottu', 'kumoaa', 'kumonnut',
    'lisätään', 'lisätty', 'lisää', 'lisännyt',
    'siirretään', 'siirretty', 'siirtää', 'siirtänyt',
}


def _normalize_source_citation_id(raw: str, source_year: int) -> str | None:
    """Normalize textual source citations like ``631/2022`` or ``631/22``."""
    raw = re.sub(r"\s+", "", (raw or ""))
    m = re.fullmatch(r"(\d{1,4})/(\d{2,4})", raw)
    if not m:
        return None
    left, right = m.groups()
    num = int(left)
    if len(right) == 4:
        return f"{right}/{num}"
    year_two = int(right)
    source_century = (source_year // 100) * 100
    full_year = source_century + year_two
    if full_year > source_year:
        full_year -= 100
    return f"{full_year}/{num}"


def _parent_title_reference_variants(parent_title: str) -> set[str]:
    """Return conservative title variants for parent-title matching."""
    norm = re.sub(r"\s+", " ", (parent_title or "").strip().lower())
    if not norm:
        return set()

    variants = {norm}

    if norm.endswith("laki"):
        variants.add(f"{norm[:-4]}lain")
    if norm.endswith("asetus"):
        variants.add(f"{norm[:-6]}asetuksen")

    if norm.startswith("laki "):
        body = norm[5:].strip()
        if body:
            variants.add(f"{body} annetun lain")
    if norm.startswith("asetus "):
        body = norm[7:].strip()
        if body:
            variants.add(f"{body} annetun asetuksen")

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
    johto_compact = re.sub(r"\s+", " ", johto or "")
    cut = re.search(r"\bsellais(?:ena|ina)\s+kuin\b|\bsiihen\s+myöhemmin\b", johto_compact, re.I)
    target_zone = johto_compact[:cut.start()] if cut else johto_compact
    for ref_num, ref_year in re.findall(r"\(\s*(\d+)\s*/\s*(\d{2,4})\s*\)", target_zone):
        target_id = _normalize_source_citation_id(f"{ref_num}/{ref_year}", source_year)
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
    johto_compact = re.sub(r'\s+', ' ', johto)
    # Truncate at "sellaisena/sellaisina kuin" / "siihen myöhemmin" — everything
    # after is prior-amendment references, not the target statute citation.
    cut = re.search(r'\bsellais(?:ena|ina)\s+kuin\b|\bsiihen\s+myöhemmin\b', johto_compact, re.I)
    target_zone = johto_compact[:cut.start()] if cut else johto_compact
    refs = re.findall(r'\(\s*(\d+)\s*/\s*(\d{2,4})\s*\)', target_zone)
    if not refs:
        return True
    try:
        year_str, num_str = parent_id.split("/")
        num = int(num_str)
    except (ValueError, AttributeError):
        return True
    year_short = year_str[-2:]  # "1991" → "91"
    for ref_num, ref_year in refs:
        try:
            if int(ref_num) == num and ref_year in (year_str, year_short):
                return True
        except ValueError:
            continue
    return False


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
    if re.search(r'(?:^|\s)laki\b|laki$', parent_norm):
        parent_kind = 'laki'
    elif re.search(r'(?:^|\s)asetus\b|asetus$', parent_norm):
        parent_kind = 'asetus'

    return bool(parent_kind) and source_kind != parent_kind


def route_amendment(
    citation_guard_johto: str,
    citation_guard_sec1: str,
    johto: str,
    parent_id: str,
    amendment_id: str,
    source_title: str = "",
    parent_title: str = "",
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
    """
    # Guard condition: only run routing check when both IDs are present and
    # the amendment year is a digit string (replicates the inline condition).
    if not (parent_id and amendment_id and amendment_id.split("/")[0].isdigit()):
        return True, "no_guard_needed"

    try:
        amendment_num = amendment_id.split("/")[1]
        parent_num = parent_id.split("/")[1]
    except IndexError:
        amendment_num = parent_num = ""

    # Primary citation check: does the preamble reference the parent?
    _refs_match = (
        _johtolause_references_parent(citation_guard_johto, parent_id)
        if citation_guard_johto
        else True
    )

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
        if _title_targets_pending_amendment_of_parent(source_title, parent_title):
            return False, "pending_amendment_of_parent_skip"
        if amendment_num and amendment_num == parent_num:
            # Tier 1: NUM collision — high confidence misroute (same number,
            # different year → amendment_parents.csv false-mapped by NUM).
            return False, "num_collision_skip"
        else:
            # Tier 2: johtolause explicitly cites a different statute.
            # Sub-case: meta-repeal targets a prior amendment act, not the parent.
            if re.search(
                r'kumotaan\b.*muuttamisesta\s+.*annetun\s+lain\s*\(\s*\d',
                johto,
                re.IGNORECASE | re.DOTALL,
            ):
                return False, "citation_mismatch_skip"
            return False, "citation_mismatch_skip"

    # Even when the citation check passes, a title-based check can still
    # override: if the amendment title explicitly names a different statute.
    if _title_explicitly_targets_other_statute(source_title, parent_title):
        return False, "citation_mismatch_skip"

    return True, "references_parent"
