"""refs-bench — corpus-wide, parse-only COVERAGE benchmark for the reference/interlink layer.

This is the reference-layer counterpart to ``parse-bench``.  Where ``parse-bench``
measures whether the amendment grammar CONSUMED every operative target, ``refs-bench``
measures how well the reference recognizer RESOLVES the cross-references it extracts
from each statute's body text.  It is the measurement loop the whole reference
program plugs into.

It is parse-only and replay-free — like ``parse-bench`` it just reads each statute's
cached body XML from the farchive and runs the existing recognizer
(``extract_all_reference_mentions``) READ-ONLY.  No oracle replay, no apply, no
materialize, no diff.  That makes it cheap enough to sweep the whole FI corpus.

What it tallies, over the scanned statutes:

* resolution-status distribution: count of each ``CiteConfidence``
  (EXACT / STATUTE_ONLY / AMBIGUOUS / OPEN / BROKEN / UNRESOLVED / APPROXIMATE).
* breakdown per ``CiteKind`` (internal / cross_statute / eu / treaty /
  non_statutory_instrument).
* a RANKED inventory of the residue shapes — the lowest-resolution mention
  outcomes plus the rejected candidates (by ``rule_id``) — the reference worklist,
  mirroring parse-bench's drop-shape ranking.

Headline metric: corpus-wide fraction of extracted mentions that are EXACT
(fully resolved) vs the residue tail (everything below EXACT).

Like parse-bench this dispatches off ``-j/--jurisdiction``; only ``fi`` has a
free-text reference recognizer today.  Other jurisdictions print a pointer.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

# Stable display order for the status table (declared once, used by both the
# worker tally and the summary so the residue tail ordering is deterministic).
_STATUS_ORDER = (
    "exact",
    "statute_only",
    "approximate",
    "ambiguous",
    "open",
    "broken",
    "unresolved",
)

# Statuses that count as the resolved residue head (EXACT) vs. everything else.
_RESOLVED_STATUS = "exact"


# ---------------------------------------------------------------------------
# Scorecard (per-family "how to judge success" buckets)
# ---------------------------------------------------------------------------
#
# The precision pass tallies the raw CiteConfidence distribution. The SCORECARD
# is the Pro "how to judge success" view: per cite-FAMILY (CiteKind), collapse
# the confidence statuses into the small, human-legible success buckets and show
# coverage + the bucket fractions, e.g.
#
#   references (cross_statute): 98% resolved / 1% ambiguous / 1% open
#   eu:                         90% resolved / 8% statute_only / 2% ambiguous
#
# This turns the bench from a single global headline into a per-lens scorecard.
# It is purely additive — it re-buckets the same status x kind shapes already
# carried on each _RefsResult, so it adds no scan cost.
#
# Bucket map (every CiteConfidence value lands in exactly one bucket — see the
# _scorecard_status_order coverage test so a new confidence can never silently
# vanish from the scorecard):
#   resolved     <- exact, approximate   (fully/defensibly resolved)
#   statute_only <- statute_only         (act fixed, provision deferred)
#   ambiguous    <- ambiguous            (multiple plausible targets)
#   open         <- open                 (vague catch-all by construction)
#   unsupported  <- unresolved           (no target; typo/future/no match)
#   broken       <- broken               (target repealed/renumbered)
_SCORECARD_BUCKET: dict[str, str] = {
    "exact": "resolved",
    "approximate": "resolved",
    "statute_only": "statute_only",
    "ambiguous": "ambiguous",
    "open": "open",
    "unresolved": "unsupported",
    "broken": "broken",
}

# Stable display order for the scorecard buckets (resolved head first, then the
# residue buckets). Any status that does not map to a known bucket is collapsed
# into "other" so the scorecard fractions always sum to 100%.
_SCORECARD_BUCKET_ORDER: tuple[str, ...] = (
    "resolved",
    "statute_only",
    "approximate",  # placeholder kept out; see note below
    "ambiguous",
    "open",
    "unsupported",
    "broken",
    "other",
)
# "approximate" is folded into "resolved"; it is NOT a standalone bucket. Strip
# it from the display order (kept above only to document the fold explicitly).
_SCORECARD_BUCKET_ORDER = tuple(
    b for b in _SCORECARD_BUCKET_ORDER if b != "approximate"
)


def _bucket_for_status(ref_status: str) -> str:
    """Map a CiteConfidence value to its scorecard success bucket ("other" if new)."""
    return _SCORECARD_BUCKET.get(ref_status, "other")


def _scorecard_rows(
    shape_ct: collections.Counter[tuple[str, str]],
    status_ct_by_kind: dict[str, collections.Counter[str]],
) -> list[tuple[str, int, list[tuple[str, int, float]]]]:
    """Build per-family scorecard rows from the status x kind tallies.

    Returns a list of (kind, total, buckets) sorted by descending total, where
    ``buckets`` is the non-empty bucket breakdown in display order as
    (bucket, count, pct) triples. ``shape_ct`` is unused for the math (kept in
    the signature so callers pass the same residue shapes they already hold) —
    the authoritative counts come from ``status_ct_by_kind``.
    """
    rows: list[tuple[str, int, list[tuple[str, int, float]]]] = []
    for kind, status_ct in status_ct_by_kind.items():
        total = sum(status_ct.values())
        if not total:
            continue
        bucket_ct: collections.Counter[str] = collections.Counter()
        for status, n in status_ct.items():
            bucket_ct[_bucket_for_status(status)] += n
        buckets = [
            (b, bucket_ct[b], bucket_ct[b] / total * 100.0)
            for b in _SCORECARD_BUCKET_ORDER
            if bucket_ct.get(b)
        ]
        rows.append((kind, total, buckets))
    rows.sort(key=lambda row: -row[1])
    return rows


def _print_scorecard(
    scorecard: list[tuple[str, int, list[tuple[str, int, float]]]],
) -> None:
    """Render the per-family scorecard (Pro 'how to judge success' view).

    One line per cite-family, e.g.::

        references (cross_statute): 98% resolved / 1% ambiguous / 1% open
        eu:                         90% resolved / 8% statute_only / 2% ambiguous
    """
    print("\n  per-family SCORECARD (coverage = resolved fraction; fractions sum to 100%):")
    if not scorecard:
        print("    (no families with mentions)")
        return
    # Left-align the family label so the bucket fractions line up.
    label_w = max(len(_family_label(kind)) for kind, _, _ in scorecard) + 1
    for kind, total, buckets in scorecard:
        frac = " / ".join(f"{pct:.0f}% {b}" for b, _, pct in buckets)
        label = _family_label(kind) + ":"
        print(f"    {label:<{label_w + 1}} {frac}  (n={total})")


def _family_label(kind: str) -> str:
    """Human label for a cite-family. cross_statute is the canonical 'references' lens."""
    if kind == "cross_statute":
        return "references (cross_statute)"
    return kind


# ---------------------------------------------------------------------------
# Recall (anchor-driven coverage proxy)
# ---------------------------------------------------------------------------
#
# refs-bench's default (precision) mode measures the resolution-status of the
# mentions the recognizers EMIT. That tells us nothing about RECALL — what the
# recognizers MISS. The recall pass closes that gap with a cheap, bounded,
# HEURISTIC anchor scan:
#
#   1. Scan the decoded body text for reference-BEARING anchor spans with a
#      handful of module-scope regexes (each tagged with its anchor type +
#      char offset + a short surface snippet).
#   2. Build a coverage mask over the SAME decoded text from every emitted
#      mention's surface footprint (emitted ReferenceMention records carry
#      source_span=None today — see ref_mention_extractor — so byte-offset
#      reconciliation is impossible; we fall back to surface-text containment,
#      see the docstring on _coverage_intervals).
#   3. An anchor occurrence is COVERED iff it overlaps a covered interval, else
#      it is a MISS.
#   4. Report per-anchor-type recall + a ranked MISS WORKLIST with self-
#      evidencing surface snippets.
#
# CAVEAT (baked into the output header): anchors are HEURISTIC. Some are false
# positives (a `§` in a non-citation enumeration, a `(2014/65)`-shaped pair that
# is not a statute id, a generic `tässä laissa`). So the recall PERCENTAGE is a
# PROXY, not a hard figure — the real deliverable is the ranked miss-shape
# worklist, which surfaces the uncaptured families (named-statute-only, EU
# directive, defined-term) ranked by corpus frequency.

# Anchor types. Each pattern is compiled once at module scope with bounded
# quantifiers and (where useful) a cheap substring guard applied before the
# scan. Order matters only for display; overlap is resolved per-occurrence.
_ANCHOR_PATTERNS: tuple[tuple[str, "re.Pattern[str]", str | None], ...] = (
    # SECTION — the § mark itself (substring guard: "§").
    ("SECTION", re.compile(r"\xa7"), "\xa7"),
    # STATUTE_ID — "(NUMBER/YEAR)" and "(YEAR/NUMBER)" parentheticals.
    (
        "STATUTE_ID",
        re.compile(r"\(\s*(?:\d{1,4}\s*/\s*\d{4}|\d{4}\s*/\s*\d{1,4})\s*\)"),
        "(",
    ),
    # ARTIKLA — EU-style article references.
    ("ARTIKLA", re.compile(r"\bartikla(?:n|ssa|sta|an)?\b", re.IGNORECASE), "artikla"),
    # CELEX — EU document numbers, e.g. 32014L0065.
    ("CELEX", re.compile(r"\b3\d{4}[A-Z]\d{4}\b"), None),
    # STATUTE_NAME_HEAD — inflected law/asetus/direktiivi heads.
    (
        "STATUTE_NAME_HEAD",
        re.compile(
            r"\w+?(?:lain|laissa|lakia|laista|laki|asetuksen|asetuksessa|"
            r"asetusta|direktiivin|direktiiviss\xe4|direktiivi\xe4)\b",
            re.IGNORECASE,
        ),
        None,
    ),
    # EU_FORM — "(EU)", "(EY)", "(EEY)", "(ETY)" qualifiers.
    ("EU_FORM", re.compile(r"\((?:EU|EY|EEY|ETY)\)"), "("),
    # COURT_PREP — court / preparatory-work / oversight abbreviations.
    ("COURT_PREP", re.compile(r"\b(?:KKO|KHO|HE|EOAK?|OKV|VTV|SopS)\b"), None),
    # DEFINED_TERM_CUE — definition-introduction cues.
    (
        "DEFINED_TERM_CUE",
        re.compile(r"\b(?:j\xe4ljemp\xe4n\xe4|tarkoitetaan)\b", re.IGNORECASE),
        None,
    ),
)

# Stable display order for anchor types.
_ANCHOR_ORDER: tuple[str, ...] = tuple(t for t, _, _ in _ANCHOR_PATTERNS)

# Matched-text window used as the surface snippet for a miss (chars).
_MISS_SNIPPET_PAD = 32

# Bounded paren-id pattern reused to locate a mention's statute-id footprint in
# the body when the mention has no surface_text (plain-text / EU lanes).
_PAREN_ID_RE = re.compile(r"\(\s*(?:\d{1,4}\s*/\s*\d{4}|\d{4}\s*/\s*\d{1,4})\s*\)")


@dataclass(frozen=True)
class _RecallResult:
    sid: str
    # anchor_type -> (covered, total) over this statute's body
    anchor_counts: tuple[tuple[str, int, int], ...]
    # ranked miss snippets: (anchor_type, snippet)
    miss_examples: tuple[tuple[str, str], ...]
    n_anchors: int
    n_misses: int


def _decode_body(xb: bytes) -> str:
    """Decode body XML bytes to text for anchor scanning.

    We scan the raw XML *text* (tags included) rather than the stripped prose so
    that (a) it is cheap (no parse) and (b) the offsets line up with the same
    string we build the coverage mask over. Anchor regexes target legal-surface
    tokens (``\xa7``, ``(711/2022)``, ``artiklan``, …) that do not occur inside
    AKN tag names, so tag noise does not inflate the anchor counts materially.
    """
    try:
        return xb.decode("utf-8", errors="replace")
    except Exception:
        return xb.decode("latin-1", errors="replace")


def _coverage_intervals(text: str, mentions) -> list[tuple[int, int]]:
    """Build a coverage mask (sorted, merged char intervals) from emitted mentions.

    OFFSET-RECONCILIATION NOTE: emitted ReferenceMention records carry
    ``source_span=None`` (the CrossRefEdge → ReferenceMention lift, the EU lane,
    and the plain-text lane all set it to None). There are therefore NO byte
    offsets to reconcile against the anchor scan. We fall back to SURFACE-TEXT
    CONTAINMENT, documented in the brief as the explicit fallback:

      * If the mention carries a non-empty ``surface_text`` (the AKN <ref>
        lane), locate every occurrence of that literal surface in ``text`` and
        mark those char ranges covered.
      * Otherwise (plain-text / EU lanes have empty surface_text but a resolved
        target statute id), locate the target's ``(NUMBER/YEAR)`` /
        ``(YEAR/NUMBER)`` id parenthetical in ``text`` and mark that range.

    This is a containment proxy, not exact span overlap — it is intentionally
    generous (it can mark a real but non-cited occurrence of the same surface as
    covered). That bias makes the recall number an UPPER bound and keeps the
    miss worklist conservative (a flagged miss is genuinely uncaptured surface).
    """
    raw: list[tuple[int, int]] = []

    # Pre-extract candidate id substrings ("711/2022" and "2022/711") for each
    # mention with no surface text, so we can match either ordering in the body.
    for m in mentions:
        surface = (getattr(m, "surface_text", "") or "").strip()
        if surface and len(surface) >= 2:
            start = 0
            # finditer-free literal scan (surface may contain regex metachars).
            while True:
                idx = text.find(surface, start)
                if idx < 0:
                    break
                raw.append((idx, idx + len(surface)))
                start = idx + len(surface)
            continue

        tgt = getattr(m, "target_provision_ref", None)
        sid = getattr(tgt, "statute_id", "") if tgt is not None else ""
        if not sid or "/" not in sid:
            continue
        num, _, year = sid.partition("/")
        for needle in (f"{num}/{year}", f"{year}/{num}"):
            start = 0
            while True:
                idx = text.find(needle, start)
                if idx < 0:
                    break
                raw.append((idx, idx + len(needle)))
                start = idx + len(needle)

    if not raw:
        return []
    raw.sort()
    merged: list[tuple[int, int]] = [raw[0]]
    for lo, hi in raw[1:]:
        plo, phi = merged[-1]
        if lo <= phi:
            merged[-1] = (plo, max(phi, hi))
        else:
            merged.append((lo, hi))
    return merged


def _covered(intervals: list[tuple[int, int]], lo: int, hi: int) -> bool:
    """True iff [lo, hi) overlaps any merged coverage interval (binary search)."""
    if not intervals:
        return False
    import bisect

    # Find the last interval whose start <= hi; check it and a couple of
    # neighbours for overlap. Intervals are merged + sorted by start.
    i = bisect.bisect_right(intervals, (hi, float("inf"))) - 1
    for j in (i, i - 1):
        if 0 <= j < len(intervals):
            clo, chi = intervals[j]
            if lo < chi and clo < hi:
                return True
    return False


def _scan_one_recall(sid: str) -> _RecallResult | None:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xb = _read_body(store, sid)
    if not xb:
        return None

    text = _decode_body(xb)
    if not text:
        return None

    try:
        result = extract_all_reference_mentions(xb, sid)
    except Exception:
        return None

    intervals = _coverage_intervals(text, result.mentions)

    counts: dict[str, list[int]] = {t: [0, 0] for t in _ANCHOR_ORDER}  # type: [covered, total]
    miss_examples: list[tuple[str, str]] = []
    n_anchors = 0
    n_misses = 0

    for anchor_type, pat, guard in _ANCHOR_PATTERNS:
        if guard is not None and guard not in text:
            continue
        for m in pat.finditer(text):
            lo, hi = m.start(), m.end()
            counts[anchor_type][1] += 1
            n_anchors += 1
            if _covered(intervals, lo, hi):
                counts[anchor_type][0] += 1
            else:
                n_misses += 1
                if len(miss_examples) < 8:
                    snip_lo = max(0, lo - _MISS_SNIPPET_PAD)
                    snip_hi = min(len(text), hi + _MISS_SNIPPET_PAD)
                    snippet = " ".join(text[snip_lo:snip_hi].split())
                    miss_examples.append((anchor_type, snippet[:160]))

    if n_anchors == 0:
        return None

    return _RecallResult(
        sid=sid,
        anchor_counts=tuple(
            (t, counts[t][0], counts[t][1]) for t in _ANCHOR_ORDER
        ),
        miss_examples=tuple(miss_examples),
        n_anchors=n_anchors,
        n_misses=n_misses,
    )


@dataclass(frozen=True)
class _RefsResult:
    sid: str
    n_mentions: int
    # status -> count over this statute's mentions
    status_counts: tuple[tuple[str, int], ...]
    # cite_kind -> count over this statute's mentions
    kind_counts: tuple[tuple[str, int], ...]
    # (status, kind) -> count over ALL this statute's mentions (drives the
    # per-family scorecard; unlike residue_shapes this includes EXACT mentions).
    status_kind_counts: tuple[tuple[str, str, int], ...]
    # residue shapes: (status, kind) pairs for every sub-EXACT mention
    residue_shapes: tuple[tuple[str, str], ...]
    # rejected-candidate rule ids (a second residue channel)
    rejected_rule_counts: tuple[tuple[str, int], ...]
    # self-evidencing examples of residue: (status, kind, surface_text)
    examples: tuple[tuple[str, str, str], ...]


def _archive_path() -> str:
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


def _read_body(store, sid: str) -> bytes | None:
    """Best available body text for reference scanning.

    References live in the consolidated body, so prefer the oracle (the same
    text the fi_refs projection scans); fall back to the enacted source or the
    amendment act XML so non-consolidated statutes still contribute mentions.
    All three are archive-only reads — no replay.
    """
    try:
        xb = store.read_oracle(sid)
    except Exception:
        xb = None
    if xb:
        return xb
    return store.read_source(sid) or store.read_amendment(sid)


def _scan_one(sid: str) -> _RefsResult | None:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xb = _read_body(store, sid)
    if not xb:
        return None

    try:
        result = extract_all_reference_mentions(xb, sid)
    except Exception:
        return None

    if not result.mentions and not result.rejected:
        return None

    status_ct: collections.Counter[str] = collections.Counter()
    kind_ct: collections.Counter[str] = collections.Counter()
    status_kind_ct: collections.Counter[tuple[str, str]] = collections.Counter()
    residue: list[tuple[str, str]] = []
    examples: list[tuple[str, str, str]] = []
    for m in result.mentions:
        status = m.cite_confidence.value
        kind = m.cite_kind.value
        status_ct[status] += 1
        kind_ct[kind] += 1
        status_kind_ct[(status, kind)] += 1
        if status != _RESOLVED_STATUS:
            residue.append((status, kind))
            if len(examples) < 3:
                examples.append((status, kind, (m.surface_text or "")[:160]))

    rej_ct: collections.Counter[str] = collections.Counter()
    for rej in result.rejected:
        rej_ct[rej.rule_id or "__none__"] += 1
        if len(examples) < 3:
            examples.append(
                ("rejected", rej.rule_id or "__none__", (rej.matched_text or "")[:160])
            )

    return _RefsResult(
        sid=sid,
        n_mentions=len(result.mentions),
        status_counts=tuple(sorted(status_ct.items())),
        kind_counts=tuple(sorted(kind_ct.items())),
        status_kind_counts=tuple(
            (st, kd, n) for (st, kd), n in sorted(status_kind_ct.items())
        ),
        residue_shapes=tuple(residue),
        rejected_rule_counts=tuple(sorted(rej_ct.items())),
        examples=tuple(examples),
    )


def _statute_ids(limit: int) -> list[str]:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    return ids[:limit] if limit else ids


def _ordered_status_items(status_ct: collections.Counter[str]) -> list[tuple[str, int]]:
    """Status counts in the canonical display order, with any unknown status appended."""
    out = [(s, status_ct.get(s, 0)) for s in _STATUS_ORDER]
    extra = sorted(k for k in status_ct if k not in _STATUS_ORDER)
    out.extend((k, status_ct[k]) for k in extra)
    return out


def run_fi(args) -> None:
    """FI reference-layer coverage: resolution-status distribution over extracted mentions."""
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 0
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20
    # Scorecard: explicit --scorecard, OR folded into --mode both (so the per-lens
    # view rides along with the widest precision+recall request) per the brief.
    show_scorecard = bool(getattr(args, "scorecard", False)) or (
        (getattr(args, "mode", "precision") or "precision") == "both"
    )

    if not workers:
        import os

        workers = min(8, max(1, (os.cpu_count() or 2) - 2))

    ids = _statute_ids(limit)
    print(
        f"refs-bench: scanning {len(ids)} statutes (parse-only, no replay) with {workers} workers...",
        file=sys.stderr,
    )

    results: list[_RefsResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_scan_one, ids, chunksize=25)):
            if r is not None:
                results.append(r)
            if i and i % 5000 == 0:
                print(f"  {i}/{len(ids)}", file=sys.stderr)

    status_ct: collections.Counter[str] = collections.Counter()
    kind_ct: collections.Counter[str] = collections.Counter()
    shape_ct: collections.Counter[tuple[str, str]] = collections.Counter()
    rej_ct: collections.Counter[str] = collections.Counter()
    status_ct_by_kind: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for r in results:
        for s, n in r.status_counts:
            status_ct[s] += n
        for k, n in r.kind_counts:
            kind_ct[k] += n
        for st, kd, n in r.status_kind_counts:
            status_ct_by_kind[kd][st] += n
        shape_ct.update(r.residue_shapes)
        for rule, n in r.rejected_rule_counts:
            rej_ct[rule] += n

    scorecard = _scorecard_rows(shape_ct, dict(status_ct_by_kind))

    total_mentions = sum(status_ct.values())
    exact = status_ct.get(_RESOLVED_STATUS, 0)
    residue = total_mentions - exact
    exact_pct = (exact / total_mentions * 100.0) if total_mentions else 0.0

    if as_json:
        payload: dict = {
                "jurisdiction": "fi",
                "metric": "reference_resolution_coverage",
                "unit": "reference_mention",
                "statutes_with_refs": len(results),
                "mentions_total": total_mentions,
                "mentions_exact": exact,
                "mentions_residue": residue,
                "rejected_candidates": sum(rej_ct.values()),
                "exact_coverage_pct": round(exact_pct, 3),
                "status_counts": dict(_ordered_status_items(status_ct)),
                "kind_counts": dict(sorted(kind_ct.items())),
                "top_residue_shapes": [
                    {"ref_status": st, "kind": kd, "count": n}
                    for (st, kd), n in shape_ct.most_common(top)
                ],
                "top_rejected_rules": [
                    {"rule_id": rule, "count": n} for rule, n in rej_ct.most_common(top)
                ],
                "worst_statutes": [
                    {"sid": r.sid, "mentions": r.n_mentions, "residue": len(r.residue_shapes)}
                    for r in sorted(results, key=lambda r: -len(r.residue_shapes))[:top]
                ],
        }
        if show_scorecard:
            payload["scorecard"] = [
                {
                    "family": kind,
                    "total": total,
                    "coverage_pct": round(
                        next((p for b, _, p in buckets if b == "resolved"), 0.0), 3
                    ),
                    "buckets": {b: c for b, c, _ in buckets},
                    "bucket_pct": {b: round(p, 3) for b, _, p in buckets},
                }
                for kind, total, buckets in scorecard
            ]
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
        return

    print("\n=== refs-bench (reference resolution coverage, fi) ===")
    print(f"  statutes with references  : {len(results)}")
    print(f"  reference mentions        : {total_mentions}")
    print(f"  EXACT (resolved)          : {exact}")
    print(f"  residue (sub-EXACT)       : {residue}")
    print(f"  rejected candidates       : {sum(rej_ct.values())}")
    print(f"  EXACT COVERAGE            : {exact_pct:.3f}%")
    print("\n  resolution-status distribution:")
    for st, n in _ordered_status_items(status_ct):
        pct = (n / total_mentions * 100.0) if total_mentions else 0.0
        print(f"    {n:8}  {pct:6.2f}%  {st}")
    print("\n  per cite-kind distribution:")
    for kd, n in sorted(kind_ct.items(), key=lambda kv: -kv[1]):
        pct = (n / total_mentions * 100.0) if total_mentions else 0.0
        print(f"    {n:8}  {pct:6.2f}%  {kd}")
    if show_scorecard:
        _print_scorecard(scorecard)
    print(f"\n  top {top} residue shapes (status x kind — the reference worklist):")
    for (st, kd), n in shape_ct.most_common(top):
        print(f"    {n:8}  {st} / {kd}")
    if rej_ct:
        print(f"\n  top {top} rejected-candidate rules:")
        for rule, n in rej_ct.most_common(top):
            print(f"    {n:8}  {rule}")
    print(f"\n  top {top} statutes by residue count:")
    for r in sorted(results, key=lambda r: -len(r.residue_shapes))[:top]:
        print(f"    {r.sid}: {len(r.residue_shapes)} residue ({r.n_mentions} mentions)")
    print("\n  sample residue (self-evidencing):")
    shown = 0
    for r in sorted(results, key=lambda r: -len(r.residue_shapes)):
        for st, kd, surface in r.examples:
            print(f"    [{st} / {kd}] {r.sid}: {surface}")
            shown += 1
            if shown >= top:
                break
        if shown >= top:
            break


def run_fi_recall(args) -> None:
    """FI reference-layer RECALL: anchor-driven coverage proxy + ranked miss worklist."""
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 0
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20

    if not workers:
        import os

        workers = min(8, max(1, (os.cpu_count() or 2) - 2))

    ids = _statute_ids(limit)
    print(
        f"refs-bench (recall): scanning {len(ids)} statutes (parse-only, no replay) "
        f"with {workers} workers...",
        file=sys.stderr,
    )

    results: list[_RecallResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_scan_one_recall, ids, chunksize=25)):
            if r is not None:
                results.append(r)
            if i and i % 5000 == 0:
                print(f"  {i}/{len(ids)}", file=sys.stderr)

    # anchor_type -> [covered, total]
    agg: dict[str, list[int]] = {t: [0, 0] for t in _ANCHOR_ORDER}
    miss_snips: dict[str, collections.Counter[str]] = {
        t: collections.Counter() for t in _ANCHOR_ORDER
    }
    for r in results:
        for t, cov, tot in r.anchor_counts:
            agg[t][0] += cov
            agg[t][1] += tot
        for t, snip in r.miss_examples:
            miss_snips[t][snip] += 1

    total_anchors = sum(v[1] for v in agg.values())
    total_covered = sum(v[0] for v in agg.values())
    total_miss = total_anchors - total_covered
    overall_recall = (total_covered / total_anchors * 100.0) if total_anchors else 0.0

    header = (
        "PROXY recall: anchors are HEURISTIC (false positives exist — a non-cited "
        "`\xa7`, a year-shaped pair, generic `t\xe4ss\xe4 laissa`); coverage is "
        "SURFACE-CONTAINMENT (emitted mentions carry no byte span). The number is a "
        "proxy; the deliverable is the ranked MISS WORKLIST below."
    )

    if as_json:
        json.dump(
            {
                "jurisdiction": "fi",
                "metric": "reference_anchor_recall_proxy",
                "unit": "anchor_occurrence",
                "caveat": header,
                "statutes_scanned": len(results),
                "anchors_total": total_anchors,
                "anchors_covered": total_covered,
                "anchors_missed": total_miss,
                "overall_recall_pct": round(overall_recall, 3),
                "per_anchor": [
                    {
                        "anchor": t,
                        "total": agg[t][1],
                        "covered": agg[t][0],
                        "missed": agg[t][1] - agg[t][0],
                        "recall_pct": round(
                            (agg[t][0] / agg[t][1] * 100.0) if agg[t][1] else 0.0, 3
                        ),
                    }
                    for t in _ANCHOR_ORDER
                ],
                "miss_worklist": {
                    t: [
                        {"snippet": s, "count": n}
                        for s, n in miss_snips[t].most_common(top)
                    ]
                    for t in _ANCHOR_ORDER
                    if miss_snips[t]
                },
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    print("\n=== refs-bench (anchor-driven RECALL proxy, fi) ===")
    print(f"  ! {header}")
    print(f"\n  statutes scanned          : {len(results)}")
    print(f"  anchor occurrences        : {total_anchors}")
    print(f"  covered (overlaps mention): {total_covered}")
    print(f"  MISSED                    : {total_miss}")
    print(f"  OVERALL RECALL (proxy)    : {overall_recall:.3f}%")
    print("\n  per-anchor-type recall (sorted by miss count):")
    print(f"    {'anchor':<18}{'total':>10}{'covered':>10}{'missed':>10}{'recall':>9}")
    for t in sorted(_ANCHOR_ORDER, key=lambda a: -(agg[a][1] - agg[a][0])):
        tot = agg[t][1]
        cov = agg[t][0]
        miss = tot - cov
        rec = (cov / tot * 100.0) if tot else 0.0
        print(f"    {t:<18}{tot:>10}{cov:>10}{miss:>10}{rec:>8.2f}%")
    print(f"\n  ranked MISS WORKLIST (top {top} snippets per anchor type):")
    for t in sorted(_ANCHOR_ORDER, key=lambda a: -(agg[a][1] - agg[a][0])):
        if not miss_snips[t]:
            continue
        miss = agg[t][1] - agg[t][0]
        print(f"\n    [{t}] {miss} misses:")
        for s, n in miss_snips[t].most_common(top):
            print(f"      {n:6}  {s}")
    print(f"\n  top {top} statutes by miss count:")
    for r in sorted(results, key=lambda r: -r.n_misses)[:top]:
        print(f"    {r.sid}: {r.n_misses} misses ({r.n_anchors} anchors)")


_POINTERS = {
    "ee": "ee: no free-text reference recognizer yet",
    "us": "us: no free-text reference recognizer yet",
    "nz": "nz: no free-text reference recognizer yet",
    "uk": "uk: no free-text reference recognizer yet",
    "no": "no: no free-text reference recognizer yet",
    "se": "se: no free-text reference recognizer yet",
}


def main(args) -> None:
    """Dispatch refs-bench on the global -j/--jurisdiction flag."""
    jur = (getattr(args, "jurisdiction", None) or "fi").lower()
    mode = getattr(args, "mode", "precision") or "precision"
    if getattr(args, "recall", False):
        # --recall is shorthand for --mode recall (unless --mode already widened
        # to "both", in which case keep the wider request).
        if mode != "both":
            mode = "recall"
    if jur == "fi":
        if mode in ("precision", "both"):
            run_fi(args)
        if mode in ("recall", "both"):
            run_fi_recall(args)
        return
    if jur in _POINTERS:
        print(
            "refs-bench: reference-resolution coverage is defined for fi (the only "
            "jurisdiction with a free-text reference recognizer). "
            f"{_POINTERS[jur]}."
        )
        return
    print(
        f"refs-bench: unknown jurisdiction {jur!r}; reference-resolution coverage "
        "is defined for fi."
    )
