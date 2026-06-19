"""Mine recurring EU-instrument nicknames from R4 defined-term bindings.

Finnish statutes introduce EU-instrument nicknames inline, e.g.

    … eläimistä saatavista sivutuotteista … asetuksessa (EY) N:o 1069/2009
    (sivutuoteasetus) …

binding ``sivutuoteasetus`` → ``(EY) N:o 1069/2009`` for the remainder of that
document. The defined-term binder (``references/defined_terms.py``) already
recognizes these binding sites and returns
:class:`~lawvm.finland.references.defined_terms.DefinedTermBinding` records whose
``target_ref`` is the EU id surface digits (``1069/2009`` / ``2016/679`` —
the form marker EY/EU/… is dropped by the binder).

A nickname that binds to the SAME CELEX/act across MANY statutes is a stable
term-of-art worth seeding into the ``eu_nickname → CELEX`` registry
(``references/registries/eu_nickname.py``). This script HARVESTS those recurring
bindings across a bounded corpus sample and produces a CANDIDATE report for human
review — it NEVER auto-edits the seed map.

CELEX conversion (fail-loud, §0.3)
----------------------------------
The binder's ``target_ref`` carries only the act digits (``NUMBER/YEAR`` or the
GDPR-style ``YEAR/NUMBER``); the CELEX *type* char (``R`` regulation / ``L``
directive) is NOT in those digits. It is recovered DETERMINISTICALLY from the
nickname head: a nickname ending in ``-asetus`` (regulation) → ``R``; ending in
``-direktiivi`` (directive) → ``L``. A binding whose term carries neither head, or
whose ``target_ref`` does not parse into a 4-digit year + sequence number, is
reported as UNCONVERTIBLE — never guessed. The CELEX number is zero-padded to 4
digits: ``3<YEAR><TYPE><NNNN>`` (e.g. ``1069/2009`` + asetus → ``32009R1069``).

Discipline: this is a harvest + verification-support tool, not a registry
auto-committer. An unverified single mapping is worse than a miss, so every
candidate is reported with its corpus support count (how many distinct statutes
bound it to that CELEX) for the reviewer to judge.

Run::

    LAWVM_CANONICAL_DATA_ROOT=/path uv run python -m lawvm.tools.mine_eu_nicknames \\
        --sample 3000 --min-support 3 --json notes_internal/EU_NICKNAME_CANDIDATES.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import traceback
from dataclasses import dataclass
from typing import Optional

from lawvm.finland.references.defined_terms import (
    DefinedTermBinding,
    recognize_defined_term_bindings,
)
from lawvm.finland.references.registries import eu_nickname

# ---------------------------------------------------------------------------
# CELEX conversion
# ---------------------------------------------------------------------------
#
# The nickname head determines the CELEX type char deterministically. These are
# the same closed statute-head class the registry recognises as EU-instrument
# heads (``direktiivi`` / ``asetus``); the head also carries the regulation vs
# directive distinction the bare ``target_ref`` digits lack.
_HEAD_TO_CELEX_TYPE: tuple[tuple[str, str], ...] = (
    ("direktiivi", "L"),
    ("asetus", "R"),
)


def celex_type_for_term(term: str) -> Optional[str]:
    """CELEX type char (``R`` / ``L``) implied by the nickname head, or ``None``.

    Deterministic from the head morpheme: a nickname whose lowercased term ends
    in ``-asetus`` is a regulation (``R``); ``-direktiivi`` a directive (``L``).
    Returns ``None`` when neither head terminates the term — the caller then
    reports the binding as unconvertible rather than guessing the type.
    """
    low = term.strip().lower()
    for head, type_char in _HEAD_TO_CELEX_TYPE:
        if low.endswith(head):
            return type_char
    return None


@dataclass(frozen=True, slots=True)
class CelexConversion:
    """Outcome of converting a binding's EU id surface to a CELEX id.

    Exactly one of ``celex`` / ``reason`` is populated. ``celex`` is the
    well-formed ``3<YEAR><TYPE><NNNN>`` id; ``reason`` explains why the surface
    could not be converted (fail-loud — the offending surface is embedded so the
    diagnostic is self-evidencing).
    """

    celex: Optional[str]
    reason: Optional[str]


def _is_plausible_eu_year(s: str) -> bool:
    """True iff ``s`` is a 4-digit string in the EU-instrument year range.

    EU legislation CELEX years run from 1951 (ECSC era) onward; we bound the
    window to 1950..2099 so a 4-digit SEQUENCE number that merely looks
    year-shaped (e.g. ``1069`` in ``1069/2009``) is not mistaken for the year.
    """
    if len(s) != 4 or not s.isdigit():
        return False
    return 1950 <= int(s) <= 2099


def _parse_year_number(target_ref: str) -> Optional[tuple[str, str]]:
    """Split an EU id surface ``A/B`` into ``(year, number)``.

    The binder emits either number-first (``1069/2009`` — old ``N:o`` form,
    NUMBER/YEAR) or year-first (``2016/679`` — GDPR-style, YEAR/NUMBER). The
    discriminator is which component is a PLAUSIBLE EU year (1950..2099):

      * exactly one component a plausible year → that one is the year;
      * both plausible years (e.g. ``2018/1999`` — a sequence number that is
        itself year-shaped) → modern EU convention is year-first, so take the
        FIRST as the year (genuinely ambiguous; the seed reviewer sees the CELEX);
      * neither a plausible year → return ``None`` (unconvertible, fail-loud).
    """
    parts = target_ref.split("/")
    if len(parts) != 2:
        return None
    a, b = parts[0].strip(), parts[1].strip()
    if not (a.isdigit() and b.isdigit()):
        return None
    a_year = _is_plausible_eu_year(a)
    b_year = _is_plausible_eu_year(b)
    if a_year and not b_year:
        return a, b  # year-first: (year, number)
    if b_year and not a_year:
        return b, a  # number-first: (number, year) -> (year, number)
    if a_year and b_year:
        # Both plausible years: modern convention is year-first.
        return a, b
    return None


def target_ref_to_celex(target_ref: str, term: str) -> CelexConversion:
    """Convert a binding's EU id surface + nickname term into a CELEX id.

    Fail-loud: returns a populated ``reason`` (never a guessed CELEX) when
      * the term carries no recognised EU-instrument head (no R/L type), or
      * the ``target_ref`` does not parse into year + sequence number.
    """
    type_char = celex_type_for_term(term)
    if type_char is None:
        return CelexConversion(
            celex=None,
            reason=(
                f"term {term!r} has no EU-instrument head "
                "(-asetus/-direktiivi); cannot determine CELEX type"
            ),
        )
    parsed = _parse_year_number(target_ref)
    if parsed is None:
        return CelexConversion(
            celex=None,
            reason=f"target_ref {target_ref!r} is not a well-formed EU id (year/number)",
        )
    year, number = parsed
    if len(number) > 4:
        return CelexConversion(
            celex=None,
            reason=f"target_ref {target_ref!r} sequence number {number!r} exceeds 4 digits",
        )
    celex = f"3{year}{type_char}{int(number):04d}"
    return CelexConversion(celex=celex, reason=None)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class _NicknameStat:
    """Mining accumulator for one nickname lemma (lowercased term surface).

    ``celex_support`` maps each distinct CELEX this nickname bound to → the set of
    statute ids that bound it there (so support count is distinct-statute, not
    raw-occurrence). ``unconvertible`` collects (statute, target_ref, reason) for
    bindings that could not be converted to CELEX (fail-loud, not dropped).
    """

    lemma: str
    celex_support: dict[str, set[str]]
    unconvertible: list[tuple[str, str, str]]


@dataclass
class _MiningResult:
    """Aggregated mining outcome over the scanned corpus sample."""

    statutes_scanned: int
    statutes_with_eu_bindings: int
    total_eu_bindings: int
    # lemma -> _NicknameStat
    stats: dict[str, _NicknameStat]
    # statute ids whose body read / binding pass raised (fail-loud, never skipped)
    errored: list[tuple[str, str]]


def _eu_bindings(bindings: list[DefinedTermBinding]) -> list[DefinedTermBinding]:
    """Filter bindings to those targeting an EU act (digits-only ``target_ref``).

    A defined-term binding's ``target_ref`` is the same ``NUMBER/YEAR`` shape for
    both Finnish acts (``527/2014``) and EU acts (``1069/2009``); the binder drops
    the EU form marker. We treat a binding as EU-targeted iff its term carries a
    recognised EU-instrument head (``-asetus`` / ``-direktiivi``) — that is the
    deterministic signal that the cited act is an EU instrument, and it is the
    same head the CELEX type derivation needs. (A Finnish ``laki`` alias has no
    such head and is excluded.)
    """
    out: list[DefinedTermBinding] = []
    for b in bindings:
        if b.target_ref is None:
            continue
        if celex_type_for_term(b.term) is None:
            continue
        out.append(b)
    return out


def mine_bindings(
    per_statute: list[tuple[str, list[DefinedTermBinding]]],
) -> _MiningResult:
    """Aggregate EU-targeted nickname bindings across statutes.

    ``per_statute`` is a list of ``(statute_id, bindings)`` — the binder output
    for each scanned statute. Pure function over already-collected bindings (no
    corpus access) so the aggregation/CELEX logic is unit-testable with synthetic
    bindings.
    """
    stats: dict[str, _NicknameStat] = {}
    statutes_with = 0
    total = 0
    for sid, bindings in per_statute:
        eu = _eu_bindings(bindings)
        if eu:
            statutes_with += 1
        for b in eu:
            total += 1
            lemma = b.term.strip().lower()
            stat = stats.get(lemma)
            if stat is None:
                stat = _NicknameStat(lemma=lemma, celex_support={}, unconvertible=[])
                stats[lemma] = stat
            assert b.target_ref is not None  # guaranteed by _eu_bindings
            conv = target_ref_to_celex(b.target_ref, b.term)
            if conv.celex is None:
                assert conv.reason is not None
                stat.unconvertible.append((sid, b.target_ref, conv.reason))
                continue
            stat.celex_support.setdefault(conv.celex, set()).add(sid)
    return _MiningResult(
        statutes_scanned=0,  # filled by the corpus driver
        statutes_with_eu_bindings=statutes_with,
        total_eu_bindings=total,
        stats=stats,
        errored=[],
    )


# ---------------------------------------------------------------------------
# Seed cross-check + candidate classification
# ---------------------------------------------------------------------------

CLASS_ALREADY_SEEDED = "already_seeded"
CLASS_NEW_SINGLE = "new_single"
CLASS_NEW_AMBIGUOUS = "new_ambiguous"
CLASS_BELOW_SUPPORT = "below_support"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A classified mined nickname candidate (analysis artifact row).

    ``classification`` is one of the ``CLASS_*`` constants. ``mined_celex`` maps
    each CELEX the nickname bound to → distinct-statute support count.
    ``seed_celex`` is the registry's curated candidate tuple for the lemma (empty
    if not seeded). ``unconvertible_count`` is how many bindings of this lemma
    could not be converted (reported, never dropped).
    """

    lemma: str
    classification: str
    mined_celex: tuple[tuple[str, int], ...]
    seed_celex: tuple[str, ...]
    statute_support: int
    unconvertible_count: int


def _seed_lemma_celex(lemma: str) -> tuple[str, ...]:
    """Return the seed's curated CELEX tuple for ``lemma`` via the public lookup.

    Uses the registry's own (inflection-tolerant) lookup so a mined nominative
    surface that the seed stores under an equivalent lemma is recognised as
    already-seeded. Returns ``()`` when the registry reports no candidate.
    """
    result = eu_nickname.lookup(lemma)
    return result.candidates


def classify_candidates(result: _MiningResult, *, min_support: int) -> list[Candidate]:
    """Classify each mined nickname against the current registry seed.

    For each lemma:
      * the set of distinct CELEX it bound to (with per-CELEX support counts);
      * the seed's curated tuple (via the public inflection-tolerant lookup).

    Classification:
      * ``new_ambiguous`` — mined to >1 distinct CELEX and NOT seeded: report all,
        never collapse (the seed discipline). Emitted regardless of support so an
        ambiguity is always visible.
      * ``already_seeded`` — the registry already has a candidate for this lemma.
      * ``new_single`` — mined to exactly ONE CELEX, not seeded, with
        distinct-statute support ≥ ``min_support``.
      * ``below_support`` — mined to one CELEX, not seeded, support < min_support.

    Support counted is the MAX distinct-statute support across the CELEX the
    nickname bound to (for an unambiguous nickname that is just its single CELEX's
    support).
    """
    candidates: list[Candidate] = []
    for lemma, stat in result.stats.items():
        mined = tuple(
            sorted(
                ((celex, len(sids)) for celex, sids in stat.celex_support.items()),
                key=lambda cs: (-cs[1], cs[0]),
            )
        )
        seed = _seed_lemma_celex(lemma)
        support = max((c for _, c in mined), default=0)
        distinct_celex = len(mined)

        if seed:
            classification = CLASS_ALREADY_SEEDED
        elif distinct_celex > 1:
            classification = CLASS_NEW_AMBIGUOUS
        elif distinct_celex == 1 and support >= min_support:
            classification = CLASS_NEW_SINGLE
        else:
            classification = CLASS_BELOW_SUPPORT

        candidates.append(
            Candidate(
                lemma=lemma,
                classification=classification,
                mined_celex=mined,
                seed_celex=seed,
                statute_support=support,
                unconvertible_count=len(stat.unconvertible),
            )
        )
    # Stable, reviewer-friendly order: new singles first (by support desc),
    # then ambiguous, then already-seeded, then below-support; ties by lemma.
    order = {
        CLASS_NEW_SINGLE: 0,
        CLASS_NEW_AMBIGUOUS: 1,
        CLASS_ALREADY_SEEDED: 2,
        CLASS_BELOW_SUPPORT: 3,
    }
    candidates.sort(
        key=lambda c: (order.get(c.classification, 9), -c.statute_support, c.lemma)
    )
    return candidates


# ---------------------------------------------------------------------------
# Corpus driver (real archive; guarded so unit tests need no corpus)
# ---------------------------------------------------------------------------


def _archive_path() -> str:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


def _read_body(store, sid: str) -> bytes | None:
    """Best available body XML (oracle preferred), mirroring surface-lints.

    Delegates to :func:`read_reference_body` so a ``contentAbsent`` oracle
    (repealed/expired statute) falls back to the enacted source.
    """
    from lawvm.finland.legal_surface.body_source import read_reference_body

    return read_reference_body(store, sid)


def scan_corpus(sample: int) -> _MiningResult:
    """Scan a bounded corpus sample and mine EU-nickname bindings.

    Reads each statute's cached body XML from the farchive (archive-only, no
    replay), decodes it to body text, runs the production defined-term binder, and
    aggregates the EU-targeted bindings. A statute whose read / decode / binding
    raises is recorded in ``errored`` (fail-loud, never silently skipped).
    """
    from farchive import Farchive
    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.transparent_store import TransparentCorpusStore

    store = TransparentCorpusStore(Farchive(_archive_path()))
    ids = store.list_statute_ids()
    if sample:
        ids = ids[:sample]

    per_statute: list[tuple[str, list[DefinedTermBinding]]] = []
    errored: list[tuple[str, str]] = []
    for sid in ids:
        try:
            xb = _read_body(store, sid)
            if not xb:
                per_statute.append((sid, []))
                continue
            body_text = decode_body_text(xb)
            bindings = recognize_defined_term_bindings(body_text, source_file=sid)
        except Exception:
            errored.append((sid, traceback.format_exc(limit=3).strip()))
            continue
        per_statute.append((sid, bindings))

    result = mine_bindings(per_statute)
    result.statutes_scanned = len(ids)
    result.errored = errored
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _format_celex_support(mined: tuple[tuple[str, int], ...]) -> str:
    return ", ".join(f"{celex}×{n}" for celex, n in mined) or "(none convertible)"


def render_report(result: _MiningResult, candidates: list[Candidate]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("EU-nickname mining candidate report")
    lines.append("=" * 72)
    lines.append(f"statutes scanned          : {result.statutes_scanned}")
    lines.append(f"  with EU-nickname bindings: {result.statutes_with_eu_bindings}")
    lines.append(f"total EU-nickname bindings : {result.total_eu_bindings}")
    lines.append(f"distinct nickname lemmas   : {len(result.stats)}")
    if result.errored:
        lines.append(f"errored statutes (fail-loud): {len(result.errored)}")
    lines.append("")

    by_class: dict[str, list[Candidate]] = collections.defaultdict(list)
    for c in candidates:
        by_class[c.classification].append(c)

    def _section(title: str, key: str) -> None:
        rows = by_class.get(key, [])
        lines.append(f"--- {title} ({len(rows)}) ---")
        if not rows:
            lines.append("  (none)")
            lines.append("")
            return
        for c in rows:
            seed = ", ".join(c.seed_celex) if c.seed_celex else "-"
            unconv = (
                f"  [unconvertible: {c.unconvertible_count}]"
                if c.unconvertible_count
                else ""
            )
            lines.append(
                f"  {c.lemma!r:48s} support={c.statute_support:<4d} "
                f"mined=[{_format_celex_support(c.mined_celex)}] seed=[{seed}]{unconv}"
            )
        lines.append("")

    _section("NEW single candidates (review to seed)", CLASS_NEW_SINGLE)
    _section("NEW ambiguous (>1 CELEX — never collapse)", CLASS_NEW_AMBIGUOUS)
    _section("Already seeded (cross-check)", CLASS_ALREADY_SEEDED)
    _section("Below support threshold (low-confidence)", CLASS_BELOW_SUPPORT)
    return "\n".join(lines)


def _candidates_to_json(result: _MiningResult, candidates: list[Candidate]) -> dict:
    return {
        "statutes_scanned": result.statutes_scanned,
        "statutes_with_eu_bindings": result.statutes_with_eu_bindings,
        "total_eu_bindings": result.total_eu_bindings,
        "distinct_lemmas": len(result.stats),
        "errored": [{"sid": sid, "error": err} for sid, err in result.errored],
        "candidates": [
            {
                "lemma": c.lemma,
                "classification": c.classification,
                "statute_support": c.statute_support,
                "mined_celex": [
                    {"celex": celex, "support": n} for celex, n in c.mined_celex
                ],
                "seed_celex": list(c.seed_celex),
                "unconvertible_count": c.unconvertible_count,
            }
            for c in candidates
        ],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mine recurring EU-instrument nicknames from R4 defined-term "
            "bindings and propose cross-statute-stable registry candidates."
        )
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=3000,
        help="number of statutes to scan (0 = whole corpus)",
    )
    parser.add_argument(
        "--min-support",
        type=int,
        default=3,
        help="min distinct-statute support for a NEW single candidate (K)",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="optional path to write the candidate set as JSON",
    )
    args = parser.parse_args(argv)

    result = scan_corpus(args.sample)
    candidates = classify_candidates(result, min_support=args.min_support)

    print(render_report(result, candidates))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(_candidates_to_json(result, candidates), fh, ensure_ascii=False, indent=2)
        print(f"\nwrote JSON candidate set → {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
