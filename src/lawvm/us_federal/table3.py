"""Deterministic act-section -> USC classification resolver over Table III.

Table III is the OLRC **Statutes-at-Large -> USC** classification table (parsed
by :mod:`lawvm.us_federal.import_table3`): for every act it records, per
``<act-section>``, the USC title/section that act-section was editorially
classified into. It is the *all-time superset* of the per-Congress PL§->USC§
classification tables in :mod:`lawvm.us_federal.classification_tables` — the same
``(act-key, act-section) -> USC address`` join, but spanning every Congress and
carrying the ``<united-states-code-status>`` field (Rep./Elim./Rev. T./…).

This module promotes the scout :class:`~lawvm.us_federal.import_table3.Table3Index`
to a full deterministic resolver:

- **range expansion** — an ``act-section`` like ``"2001-2004"`` is expanded so a
  lookup of any integer in the range resolves to the range's USC target (the
  literal range key is also kept);
- **sub-section roots** — ``"1101(a)"`` indexes under root ``"1101"`` and a
  ``"1101(a)"`` lookup falls back to the ``"1101"`` row;
- **status surfacing** — a repealed/eliminated classification (``Rep.``/``Elim.``)
  is still a *real* mapping; the resolver returns it and surfaces the status
  rather than dropping it;
- **uncodified holdout** — a ``usckey`` ending ``nt`` (or a ``"… nt"`` section)
  is an UNCODIFIED note target and is NEVER mapped onto a codified section.

The resolver is deterministic and fail-loud (AGENTS.md §1.7/§1.10): when several
classified rows disagree on the USC target for one ``(act-key, act-section)`` it
refuses (returns the typed ambiguous result) rather than picking by accident.

It also exposes a :meth:`Table3Resolver.resolve` adapter compatible with the
:class:`lawvm.us_federal.classification_tables.ClassificationIndex` ``resolve``
contract (``(statute_id, pl_section) -> LegalAddress | None``) so it can drop into
the existing ``classification_index`` slot as the all-time superset.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Iterable

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.import_table3 import (
    Table3Record,
    iter_table3_records,
)
from lawvm.us_federal.source_tree import usc_section_address

# Release-point pin the farchive-resident Table III bulk XML is keyed under.
DEFAULT_TABLE3_RELEASE_POINT = "119-99"
# Opt-out / override knobs for the lazily-loaded default resolver:
#   LAWVM_US_TABLE3_RELEASE_POINT — pin a different release point.
#   LAWVM_US_TABLE3_DISABLE=1     — disable the default resolver entirely.
_TABLE3_RELEASE_POINT_ENV = "LAWVM_US_TABLE3_RELEASE_POINT"
_TABLE3_DISABLE_ENV = "LAWVM_US_TABLE3_DISABLE"

# ---------------------------------------------------------------------------
# Act-key + act-section normalization
# ---------------------------------------------------------------------------

# A modern Public-Law act key: "117-2", "119-99" (congress-number).
_MODERN_ACT_KEY_RE = re.compile(r"^\s*(?P<c>\d{1,3})\s*[-–]\s*(?P<n>\d{1,5})\s*$")
# A "PL 117-2" / "P.L. 117-2" prose form of a modern act key.
_PL_PROSE_KEY_RE = re.compile(
    r"^\s*P\.?\s*L\.?\s+(?P<c>\d{1,3})\s*[-–]\s*(?P<n>\d{1,5})\s*$",
    re.IGNORECASE,
)
# A numeric range act-section: "2001-2004" (ASCII hyphen or en/em dash).
_SECTION_RANGE_RE = re.compile(r"^(?P<lo>\d+)\s*[-–—]\s*(?P<hi>\d+)$")
# A trailing parenthesised sub-segment group: "(a)", "(1)", "(a)(1)".
_PAREN_TAIL_RE = re.compile(r"\([0-9A-Za-z]+\)\s*$")

# Range expansion is bounded to guard against a pathological OLRC cell (mirrors
# ClassificationIndex._MAX_RANGE_EXPANSION).
_MAX_RANGE_EXPANSION = 256
_SECTION_ROOT_CACHE_SIZE = 65536


def normalize_act_key(act_key: str) -> str:
    """Normalize an act key to the Table III ``<num>`` form.

    Accepts the modern Public-Law forms ``"117-2"``, ``"PL 117-2"``,
    ``"P.L. 117–2"`` (en-dash) and returns the canonical ``"117-2"``; an
    older-act chapter key (a bare ``"531"``) is returned stripped. Returns ``""``
    when the input is empty (never raises — an unrecognised key simply will not
    match any Table III row).
    """
    raw = (act_key or "").strip()
    if not raw:
        return ""
    m = _PL_PROSE_KEY_RE.match(raw) or _MODERN_ACT_KEY_RE.match(raw)
    if m is not None:
        return f"{int(m.group('c'))}-{int(m.group('n'))}"
    return raw


@lru_cache(maxsize=_SECTION_ROOT_CACHE_SIZE)
def section_root(act_section: str) -> str:
    """The integer/letter root of an act-section (``"1101(a)"`` -> ``"1101"``)."""
    text = (act_section or "").strip()
    idx = 0
    n = len(text)
    while idx < n and text[idx].isdigit():
        idx += 1
    if idx == 0:
        return text
    if idx < n and (
        ("A" <= text[idx] <= "Z") or ("a" <= text[idx] <= "z")
    ):
        idx += 1
    return text[:idx] if idx else text


def _strip_one_paren_group(section: str) -> str:
    """Peel one trailing ``(X)`` group: ``"1101(a)(1)"`` -> ``"1101(a)"``."""
    out = section.rstrip()
    if not out.endswith(")"):
        return out
    open_idx = out.rfind("(")
    if open_idx <= 0:
        return out
    return out[:open_idx].rstrip()


# ---------------------------------------------------------------------------
# Typed resolution outcome
# ---------------------------------------------------------------------------


class Table3ResolveStatus(StrEnum):
    """Closed set of Table III act-section resolution outcomes."""

    CLASSIFIED = "classified"
    """Resolved to a unique codified USC address (possibly repealed/eliminated)."""

    UNCODIFIED = "uncodified"
    """The only matching row(s) are ``nt`` note targets — held out, unmapped."""

    AMBIGUOUS = "ambiguous"
    """Several classified rows disagree on the USC target — refused (§1.7)."""

    UNMAPPED = "unmapped"
    """No Table III row matches this (act-key, act-section)."""


@dataclass(frozen=True, slots=True)
class Table3Resolution:
    """Typed, frozen carrier for one Table III act-section resolution.

    ``address`` is the resolved codified USC :class:`LegalAddress` (``None``
    unless ``status`` is :attr:`Table3ResolveStatus.CLASSIFIED`). ``usc_status``
    carries the ``<united-states-code-status>`` of the resolving row (e.g.
    ``"Rep."``, ``"Elim."``) — empty for a live classification. ``usckey`` is the
    resolving Table III record's ``usckey`` (the audit witness identifying which
    record resolved it). ``candidates`` records the distinct codified targets seen
    (one when unambiguous; several when ``AMBIGUOUS``).
    """

    status: Table3ResolveStatus
    address: LegalAddress | None
    usc_status: str = ""
    usckey: str = ""
    act_key: str = ""
    act_section: str = ""
    candidates: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.address is not None

    @property
    def is_repealed(self) -> bool:
        """True when the resolving classification carries a repealed/eliminated status."""
        s = self.usc_status.strip().lower()
        return s.startswith("rep.") or s.startswith("elim.") or s == "repealed"


_UNMAPPED = Table3Resolution(status=Table3ResolveStatus.UNMAPPED, address=None)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class Table3Resolver:
    """Deterministic ``(act-key, act-section) -> USC address`` resolver.

    Built from the parsed Table III records (the farchive bulk XML, or any
    iterable of :class:`Table3Record`). Keyed by ``(normalized act_key,
    section_root)`` so a range row is expanded to each integer in the range and a
    sub-section lookup peels to its parent root. Both the modern Public-Law
    ``"117-2"`` act key and the older-act chapter ``<num>`` (with its
    ``<public-law>``) are indexed.
    """

    def __init__(self, records: Iterable[Table3Record]) -> None:
        # (act_key, section_root) -> records that classify under that root.
        self._by_key: dict[tuple[str, str], list[Table3Record]] = {}
        self.record_count = 0
        for rec in records:
            self.record_count += 1
            for act_key in self._act_keys_for(rec):
                for root in self._section_roots_for(rec.act_section):
                    self._by_key.setdefault((act_key, root), []).append(rec)

    @classmethod
    def from_bytes(cls, data: bytes) -> Table3Resolver:
        """Build a resolver from the Table III bulk-XML bytes."""
        return cls(iter_table3_records(data))

    # -- key derivation --------------------------------------------------

    @staticmethod
    def _act_keys_for(rec: Table3Record) -> tuple[str, ...]:
        """Index keys for one record.

        A modern row keys on its ``act_num`` ("117-2"). An older chapter row keys
        on both its chapter ``act_num`` ("531") AND, when a ``<public-law>`` is
        present, the synthetic ``{congress}-{public_law}`` form so a caller that
        only knows the congress+PL-number can still hit it.
        """
        if not rec.act_num:
            return ()
        if "-" in rec.act_num or not rec.public_law or not rec.act_congress:
            return (rec.act_num,)
        synthetic = f"{rec.act_congress}-{rec.public_law}"
        if synthetic == rec.act_num:
            return (rec.act_num,)
        return (rec.act_num, synthetic)

    @staticmethod
    @lru_cache(maxsize=_SECTION_ROOT_CACHE_SIZE)
    def _section_roots_for(act_section: str) -> tuple[str, ...]:
        """Roots a record's act-section is indexed under (range -> each member)."""
        cell = (act_section or "").strip()
        if not cell:
            return ()
        rm = (
            _SECTION_RANGE_RE.match(cell)
            if "-" in cell or "–" in cell or "—" in cell
            else None
        )
        if rm is not None:
            lo, hi = int(rm.group("lo")), int(rm.group("hi"))
            if lo <= hi and (hi - lo + 1) <= _MAX_RANGE_EXPANSION:
                roots = [str(n) for n in range(lo, hi + 1)]
                roots.append(cell)  # keep the literal "2001-2004" key too
                return tuple(roots)
            # Inverted / oversized range: index only the literal key (suspect edit
            # stays visible, never silently expanded).
            return (cell,)
        return (section_root(cell),)

    # -- lookup ----------------------------------------------------------

    def lookup(self, act_key: str, act_section: str) -> list[Table3Record]:
        """All Table III records for ``(act_key, act_section)`` (root-keyed).

        Tries the exact root first, then peels trailing ``(X)`` sub-segment groups
        ("1101(a)(1)" -> "1101(a)" -> "1101") so a sub-section with no row of its
        own resolves to its parent root's classification.
        """
        key = normalize_act_key(act_key)
        if not key:
            return []
        probe = (act_section or "").strip()
        # Exact (root or literal-range) match.
        root = section_root(probe)
        hit = self._by_key.get((key, root))
        if hit:
            return list(hit)
        # Literal-range key (caller passed "2001-2004" verbatim).
        hit = self._by_key.get((key, probe))
        if hit:
            return list(hit)
        # Peel parenthesised sub-segments and retry each parent level.
        peel = probe
        while _PAREN_TAIL_RE.search(peel):
            peel = _strip_one_paren_group(peel)
            if not peel:
                break
            hit = self._by_key.get((key, section_root(peel)))
            if hit:
                return list(hit)
        return []

    def resolve(self, act_key: str, act_section: str) -> Table3Resolution:
        """Resolve to a typed :class:`Table3Resolution`.

        Adjudication (§1.7): collect the distinct *codified* targets across all
        matching rows. Exactly one -> ``CLASSIFIED`` (surfacing its status, with a
        live row preferred as the resolving witness over a repealed one when both
        carry the same address). Several distinct -> ``AMBIGUOUS`` (refused). None
        codified but a matching ``nt`` row exists -> ``UNCODIFIED`` (held out). No
        match at all -> ``UNMAPPED``.
        """
        rows = self.lookup(act_key, act_section)
        if not rows:
            return _UNMAPPED
        classified = [r for r in rows if r.is_classified]
        if not classified:
            # Only note/uncodified rows matched — never mapped onto a section.
            return Table3Resolution(
                status=Table3ResolveStatus.UNCODIFIED,
                address=None,
                usckey=rows[0].usckey,
                act_key=normalize_act_key(act_key),
                act_section=(act_section or "").strip(),
            )
        # Distinct codified targets, in first-seen order.
        ordered_targets: list[tuple[str, str]] = []
        seen_targets: set[tuple[str, str]] = set()
        for r in classified:
            tgt = (str(int(r.usc_title)), r.usc_section.strip())
            if tgt not in seen_targets:
                seen_targets.add(tgt)
                ordered_targets.append(tgt)
        candidates = tuple(f"{t[0]}:{t[1]}" for t in ordered_targets)
        if len(ordered_targets) != 1:
            return Table3Resolution(
                status=Table3ResolveStatus.AMBIGUOUS,
                address=None,
                act_key=normalize_act_key(act_key),
                act_section=(act_section or "").strip(),
                candidates=candidates,
            )
        title_s, section_s = ordered_targets[0]
        # Prefer a live (no-status) row as the resolving witness; fall back to the
        # first matching row when every row carries a status (e.g. all repealed).
        rows_for_target = [
            r
            for r in classified
            if (str(int(r.usc_title)), r.usc_section.strip()) == (title_s, section_s)
        ]
        witness = next((r for r in rows_for_target if not r.status), rows_for_target[0])
        return Table3Resolution(
            status=Table3ResolveStatus.CLASSIFIED,
            address=usc_section_address(int(title_s), section_s),
            usc_status=witness.status,
            usckey=witness.usckey,
            act_key=normalize_act_key(act_key),
            act_section=(act_section or "").strip(),
            candidates=candidates,
        )

    # -- ClassificationIndex-compatible adapter --------------------------

    def resolve_address(self, act_key: str, act_section: str) -> LegalAddress | None:
        """Just the resolved :class:`LegalAddress`, or ``None`` (incl. ambiguous).

        Mirrors :meth:`ClassificationIndex.resolve`: a codified classification
        (even repealed) resolves; uncodified/ambiguous/unmapped return ``None``.
        """
        return self.resolve(act_key, act_section).address

    @staticmethod
    def _statute_id_to_act_key(statute_id: str) -> str:
        """``"PL 118-5"`` / ``"118-5"`` -> ``"118-5"`` for the table-index slot."""
        return normalize_act_key(statute_id)

    def resolve_classification(self, statute_id: str, pl_section: str) -> LegalAddress | None:
        """``ClassificationIndex.resolve``-compatible entry point.

        Lets the resolver drop into the existing ``classification_index`` slot as
        the all-time superset (``(statute_id, pl_section) -> LegalAddress | None``).
        """
        return self.resolve_address(self._statute_id_to_act_key(statute_id), pl_section)


# ---------------------------------------------------------------------------
# Agreement adjudication (§1.7) — Table III vs an existing witness
# ---------------------------------------------------------------------------


class AgreementVerdict(StrEnum):
    """Outcome of comparing a Table III resolution with an existing witness."""

    AGREE = "agree"
    """Both resolve and to the same USC address."""

    DISAGREE = "disagree"
    """Both resolve but to different addresses — a typed divergence witness."""

    TABLE3_ONLY = "table3_only"
    """Only Table III resolved (the existing channel was empty)."""

    EXISTING_ONLY = "existing_only"
    """Only the existing witness resolved (Table III did not)."""

    NEITHER = "neither"
    """Neither channel resolved."""


@dataclass(frozen=True, slots=True)
class AgreementAdjudication:
    """Typed result of Table-III-vs-existing-witness adjudication.

    Per §1.7 the resolver does NOT silently pick one on disagreement: it records
    *both* addresses and (matching the standing nonpositive policy) prefers the
    existing witness as the chosen address, flagging the divergence as evidence.
    """

    verdict: AgreementVerdict
    chosen: LegalAddress | None
    table3_address: LegalAddress | None
    existing_address: LegalAddress | None


def adjudicate(
    table3_address: LegalAddress | None,
    existing_address: LegalAddress | None,
) -> AgreementAdjudication:
    """Compare a Table III address with an existing GPO-href/parenthetical address.

    - both present and equal      -> ``AGREE`` (chosen = the shared address)
    - both present, differ        -> ``DISAGREE`` (chosen = existing; divergence
      recorded as evidence — never silently overwrite the existing witness)
    - only Table III              -> ``TABLE3_ONLY`` (chosen = Table III)
    - only the existing witness   -> ``EXISTING_ONLY`` (chosen = existing)
    - neither                     -> ``NEITHER``
    """
    if table3_address is not None and existing_address is not None:
        if table3_address.path == existing_address.path:
            return AgreementAdjudication(
                verdict=AgreementVerdict.AGREE,
                chosen=existing_address,
                table3_address=table3_address,
                existing_address=existing_address,
            )
        return AgreementAdjudication(
            verdict=AgreementVerdict.DISAGREE,
            chosen=existing_address,
            table3_address=table3_address,
            existing_address=existing_address,
        )
    if table3_address is not None:
        return AgreementAdjudication(
            verdict=AgreementVerdict.TABLE3_ONLY,
            chosen=table3_address,
            table3_address=table3_address,
            existing_address=None,
        )
    if existing_address is not None:
        return AgreementAdjudication(
            verdict=AgreementVerdict.EXISTING_ONLY,
            chosen=existing_address,
            table3_address=None,
            existing_address=existing_address,
        )
    return AgreementAdjudication(
        verdict=AgreementVerdict.NEITHER,
        chosen=None,
        table3_address=None,
        existing_address=None,
    )


# ---------------------------------------------------------------------------
# Lazily-loaded default resolver (farchive-resident Table III bulk XML)
# ---------------------------------------------------------------------------

_DEFAULT_RESOLVER: Table3Resolver | None = None
_DEFAULT_RESOLVER_LOADED = False


def load_default_table3_resolver() -> Table3Resolver | None:
    """Lazily build the default resolver from the farchive Table III bulk XML.

    Resolves ``us://classification/table3/{release_point}.xml`` through the
    canonical U.S. farchive (the same precedence every consumer uses) and caches
    the built resolver process-wide. Returns ``None`` — never raises — when the
    table is absent or disabled (``LAWVM_US_TABLE3_DISABLE=1``), so a build host
    without the Table III archive degrades to the existing inherit-or-refuse
    behaviour rather than failing.

    The cache is module-level so the 125 MB parse happens at most once per
    process; tests that need a clean slate call :func:`reset_default_table3_resolver`.
    """
    global _DEFAULT_RESOLVER, _DEFAULT_RESOLVER_LOADED
    if _DEFAULT_RESOLVER_LOADED:
        return _DEFAULT_RESOLVER
    _DEFAULT_RESOLVER_LOADED = True
    if os.environ.get(_TABLE3_DISABLE_ENV) == "1":
        return None
    try:
        from lawvm.us_federal.sources import (
            open_us_federal_farchive,
            usc_classification_table_locator,
        )

        release_point = os.environ.get(
            _TABLE3_RELEASE_POINT_ENV, DEFAULT_TABLE3_RELEASE_POINT
        )
        archive = open_us_federal_farchive(readonly=True)
        try:
            data = archive.get(
                usc_classification_table_locator("table3", release_point, ext="xml")
            )
        finally:
            archive.close()
    except Exception:
        # Any acquisition failure (no archive, missing table, IO) degrades to the
        # inherit-or-refuse baseline; the resolver is a capability add, not a
        # hard dependency.
        return None
    if not data:
        return None
    _DEFAULT_RESOLVER = Table3Resolver.from_bytes(data)
    return _DEFAULT_RESOLVER


def reset_default_table3_resolver() -> None:
    """Clear the cached default resolver (test isolation)."""
    global _DEFAULT_RESOLVER, _DEFAULT_RESOLVER_LOADED
    _DEFAULT_RESOLVER = None
    _DEFAULT_RESOLVER_LOADED = False
