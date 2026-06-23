"""Engine-boundary normalization for Finnish säädös ids.

A Finnish statute carries two id orderings that look identical in shape:

- the canonical, user-/cross-reference-facing säädös id ``num/year`` (e.g.
  ``"301/2004"``); and
- the engine-internal ``year/num`` id (e.g. ``"2004/301"``) that keys the
  corpus (``finlex://sd/{year}/{num}/...``) and the amendment index.

These two orderings are a structural hazard: the corpus and amendment index
are keyed *only* in ``year/num`` form, so handing the engine the ``num/year``
form reads no base and (worse) resolves to an *empty* amendment set without
error — a silent degradation to base-only materialization.

This module is the single well-defined boundary that resolves both orderings
to the engine ``year/num`` form, and the place that raises a clear, named
diagnostic when an id is malformed. Resolution against a real corpus (and the
loud failure when an id reads no base) lives at the replay entry point that has
a corpus in hand.
"""
from __future__ import annotations

from typing import Optional, Tuple

# Plausible Finnish statute year window. Finland's statute corpus starts in the
# 1734 lawbook era; the upper bound is a generous guard against typos.
_MIN_PLAUSIBLE_YEAR = 1734
_MAX_PLAUSIBLE_YEAR = 2200


class StatuteIdError(ValueError):
    """A säädös id could not be parsed into a (year, num) pair.

    Distinct, named diagnostic so a malformed/unorderable id can never be
    confused with a generic parse failure or silently degrade downstream.
    """


def _is_plausible_year(token: str) -> bool:
    return (
        len(token) == 4
        and token.isdigit()
        and _MIN_PLAUSIBLE_YEAR <= int(token) <= _MAX_PLAUSIBLE_YEAR
    )


def split_year_num(statute_id: str) -> Optional[Tuple[str, str]]:
    """Return ``(year, num)`` for a ``year/num`` or ``num/year`` id, else ``None``.

    Disambiguates by which component is a plausible 4-digit year
    (``1734..2200``). When both look like years (rare, pathological), prefers
    the engine ordering where the year is first. Returns ``None`` for anything
    that is not a clean two-component id (caller decides whether that is fatal).
    """
    parts = statute_id.strip().split("/")
    if len(parts) != 2:
        return None
    a, b = parts[0].strip(), parts[1].strip()
    if not a or not b:
        return None
    a_year = _is_plausible_year(a)
    b_year = _is_plausible_year(b)
    if a_year and not b_year:
        return a, b  # already 'year/num'
    if b_year and not a_year:
        return b, a  # 'num/year' -> swap to engine '(year, num)'
    if a_year and b_year:
        return a, b  # ambiguous double-year: assume engine 'year/num'
    return None


def engine_statute_id(statute_id: str) -> str:
    """Normalize a säädös id to engine ``year/num`` form.

    Accepts both orderings (``"301/2004"`` and ``"2004/301"`` both return
    ``"2004/301"``). Ids that are not a recognizable two-component statute id
    (sub-numbered olders like ``"1889/39-001"``, or anything without a
    plausible year) are passed through unchanged — this normalizer only fixes
    the year/num vs num/year ordering hazard and never invents structure it
    cannot prove.
    """
    yn = split_year_num(statute_id)
    if yn is None:
        return statute_id.strip()
    year, num = yn
    return f"{year}/{num}"


def canonical_statute_id(statute_id: str) -> str:
    """Normalize a säädös id to canonical ``num/year`` form (``"301/2004"``).

    Inverse facing form of :func:`engine_statute_id`; used for cross-reference
    and user-facing display. Passes through unrecognized ids unchanged.
    """
    yn = split_year_num(statute_id)
    if yn is None:
        return statute_id.strip()
    year, num = yn
    return f"{num}/{year}"


def require_year_major(statute_id: str) -> str:
    """Strict year-major gate for user-facing entry points (CLI/exporter boundary).

    Accepts ONLY the engine ``year/num`` ordering where the FIRST ``/``-component
    is a plausible 4-digit year (``1734..2200``): e.g. ``"2004/301"``, ``"1889/39"``.
    A sub-numbered tail is permitted as long as the year is still first:
    ``"1889/39-001"`` is accepted. The id is returned stripped and unchanged.

    Raises :class:`StatuteIdError` (carrying a ``FI_STATUTE_ID_NOT_YEAR_MAJOR``
    marker in its message) for anything whose first component is not a plausible
    year — the Finnish ``num/year`` citation form (``"301/2004"``), a bare number,
    or junk. This boundary **never silently swaps** ``num/year`` to ``year/num``;
    the auto-swap in :func:`split_year_num` is reserved for engine-internal
    resolution where the ordering hazard is already understood.
    """
    raw = statute_id.strip()
    parts = raw.split("/")
    first = parts[0].strip() if parts else ""
    if len(parts) >= 2 and _is_plausible_year(first):
        return raw
    raise StatuteIdError(
        f"FI_STATUTE_ID_NOT_YEAR_MAJOR: statute id {statute_id!r} is not year-major. "
        "Use the engine year/num form where the FIRST component is a 4-digit year "
        f"({_MIN_PLAUSIBLE_YEAR}..{_MAX_PLAUSIBLE_YEAR}), e.g. '2004/301' or '1889/39' "
        "(sub-numbered '1889/39-001' is also allowed). The Finnish num/year citation "
        "form (e.g. '301/2004') and bare numbers are rejected here so the id is never "
        "silently swapped."
    )


def looks_like_statute_id(statute_id: str) -> bool:
    """True iff the id is a recognizable two-component ``year``/``num`` säädös id.

    Sub-numbered older ids (``"1889/39-001"``) are not handled by the
    year/num swap and return False; callers that accept them must not assume
    this normalizer reordered them.
    """
    return split_year_num(statute_id) is not None
