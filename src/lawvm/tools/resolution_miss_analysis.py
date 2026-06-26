"""Diagnostic: categorize ``statute_only`` resolution MISSES of ``fi-name:`` refs.

The reference-resolution projection (``references/resolve.py``) resolves a
by-name placeholder ``fi-name:<normalized_name>`` (emitted by
``references/by_name.py``) against the full statute-name registry
(``registries/statute_name.py``, artifact ``data/finland/statute_name_registry.jsonl``).
A corpus sample shows a large fraction of resolved by-name references end as
``status=statute_only`` — the registry returned ``none`` for the normalized
name.  This module categorizes WHY, so the next fix can be ranked by EV.

It is **diagnosis only**: it imports the production recognizer, resolver and
registry untouched and reports, never patches.

Buckets (per the registry MISS taxonomy):

  (a) NORMALIZED-NAME NOT IN REGISTRY AT ALL.  The ``fi-name:`` normalized key
      (``modifier + nominative_head``, e.g. ``ympäristönsuojelulaki``) is not a
      registry index key, and no registry title — base-act or amendment — has a
      compound head matching that modifier.  The act is simply not enumerated by
      a head-bearing title the recognizer could ever have generated.  In
      practice this bucket is dominated by RECOGNIZER FALSE POSITIVES: ordinary
      Finnish words whose tail coincides with an oblique statute head
      (``sellaista`` -> ``sellaki``, ``veronalaista`` -> ``veronalaki``,
      ``tilinpäätöksen`` -> ``tilinpäätös``).  Bucket (e) isolates those.

  (e) RECOGNIZER FALSE POSITIVE.  A bucket-(a) miss whose normalized name is not
      a plausible statute name at all — the by-name recognizer fired on a common
      word that merely ENDS in an oblique head surface.  Detected heuristically
      (very short modifier, or the modifier is a known non-name word fragment).
      These are NOT a registry gap; they are an over-firing recognizer and would
      be FIXED by tightening the recognizer (out of scope here, diagnosed only).

  (b) COLLISION.  The normalized key IS a registry index key but maps to >1
      distinct statute id.  This lands ``ambiguous`` upstream, NOT
      ``statute_only`` — so by construction this bucket is EMPTY among the
      misses.  We still count collisions over the SAMPLE separately as a sanity
      cross-check (and to confirm the claim).

  (c) NORMALIZATION MISMATCH.  The base act IS in the registry under a title
      that, normalized by a FIXED/looser normalizer, equals the recognizer's
      key — but the registry's generation-first inflection did not produce that
      exact key.  A fixable normalization bug (spacing, casing, hyphen, head
      variant).

  (d) GENUINELY-UNINDEXED ACT.  The modifier names a real base act whose
      *amendment* titles ("Laki <Modifier>n muuttamisesta") ARE in the registry
      (so the act provably exists in the corpus) but whose *base/consolidated*
      head-bearing title is NOT indexed with inflected variants — a registry
      build gap (the base act's own title was not enumerated as a head-bearing
      entry).

Run:

  LAWVM_CANONICAL_DATA_ROOT=/path uv run python -m lawvm.tools.resolution_miss_analysis \\
      --sample 2000 --report notes_internal/RESOLUTION_MISS_ANALYSIS.md
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lawvm.finland.references.by_name import recognize_by_name_refs
from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    default_artifact_path,
    load_statute_name_entries,
    load_statute_name_registry,
)
from lawvm.finland.references.resolve import (
    ResolutionStatus,
    resolve_mentions,
)
from lawvm.finland.references.registries import eu_nickname

# The closed statute heads, mirrored from the registry module (so we can detect
# the nominative head a normalized fi-name key ends in).
_HEADS_LONGEST_FIRST: tuple[str, ...] = (
    "direktiivi",
    "ilmoitus",
    "sopimus",
    "määräys",
    "päätös",
    "säädös",
    "asetus",
    "laki",
    "ohje",
)

_FI_NAME_PREFIX = "fi-name:"


# ---------------------------------------------------------------------------
# Registry side-indexes (built once from the artifact entries)
# ---------------------------------------------------------------------------


@dataclass
class RegistryIndexes:
    """Auxiliary indexes over the registry entries for miss categorization.

    Attributes:
        modifier_to_amend_ids: nominative modifier-stem (lowercased, e.g.
            ``ympäristönsuojelu``) -> set of statute ids whose title is an
            *amendment* of an act with that modifier
            ("Laki <Modifier>n muuttamisesta" and friends).  Evidence that the
            base act EXISTS in the corpus.
        loose_title_keys: a looser-normalized base-title key -> set of ids, for
            normalization-mismatch detection (strips spaces/hyphens around the
            head, folds case).
    """

    modifier_to_amend_ids: dict[str, set[str]]
    loose_title_keys: dict[str, set[str]]


# Amendment-title pattern: "Laki <X>:n muuttamisesta", "... kumoamisesta", etc.
# Captures the modifier phrase <X> (the base act's name minus its head, in
# genitive).  We only need its STEM for matching, so we strip the trailing
# genitive marker loosely.
_AMEND_TITLE_RE = re.compile(
    r"^(?:laki|asetus|valtioneuvoston\s+asetus|tasavallan\s+presidentin\s+asetus)\s+"
    r"(?P<mod>.+?):n\s+"
    r"(?:muuttamisesta|kumoamisesta|muuttamisesta\s+annetun.*"
    r"|väliaikaisesta\s+muuttamisesta)",
    re.IGNORECASE,
)


def _loose_norm(s: str) -> str:
    """A looser normalization than the registry's: drop spaces+hyphens, fold case."""
    return re.sub(r"[\s\-]+", "", s.lower())


def _amend_modifier_stem(title: str) -> Optional[str]:
    """If ``title`` is an amendment title, return the base modifier stem (loose).

    "Laki ympäristönsuojelulain muuttamisesta" -> the base name is
    "ympäristönsuojelulaki"; its modifier (minus head) is "ympäristönsuojelu".
    We return the loose-normalized FULL base name surface key (head reattached
    in nominative if we can detect the genitive head), to compare against a
    fi-name key.  Returns ``None`` when not an amendment title.
    """
    m = _AMEND_TITLE_RE.match(title.strip())
    if not m:
        return None
    base = m.group("mod").strip()
    # ``base`` is the base act name in GENITIVE without its trailing ``:n``
    # already stripped by the regex (we matched ``<mod>:n``).  ``base`` is e.g.
    # "ympäristönsuojelulai" (genitive head ``lain`` minus ``n`` -> ``lai``) OR
    # the regex captured up to ``:n`` so ``base`` excludes the genitive ``n``.
    # In practice Finlex amendment titles read "Laki ympäristönsuojelulain
    # muuttamisesta" so <mod> = "ympäristönsuojelulai" and ":n" consumed "n".
    # Reattach a nominative-ish head by mapping the genitive head stem back.
    return _loose_norm(base)


def build_registry_indexes(entries: list[StatuteNameEntry]) -> RegistryIndexes:
    """Build the auxiliary indexes used to categorize a miss."""
    modifier_to_amend_ids: dict[str, set[str]] = collections.defaultdict(set)
    loose_title_keys: dict[str, set[str]] = collections.defaultdict(set)
    for e in entries:
        title = e.canonical_title
        # Loose key over the WHOLE title (head-bearing base titles only — an
        # amendment title's loose key is its own long phrase, harmless here).
        loose_title_keys[_loose_norm(title)].add(e.statute_id)
        stem = _amend_modifier_stem(title)
        if stem is not None:
            modifier_to_amend_ids[stem].add(e.statute_id)
    return RegistryIndexes(
        modifier_to_amend_ids=dict(modifier_to_amend_ids),
        loose_title_keys=dict(loose_title_keys),
    )


# ---------------------------------------------------------------------------
# Miss categorization
# ---------------------------------------------------------------------------

BUCKET_A = "a_not_in_registry_at_all"
BUCKET_B = "b_collision"
BUCKET_C = "c_normalization_mismatch"
BUCKET_D = "d_genuinely_unindexed_base_act"
BUCKET_E = "e_recognizer_false_positive"

# Common Finnish word tails that the by-name recognizer mis-fires on: an
# ordinary inflected word ending in an oblique statute-head surface. The
# normalized key these collapse to (modifier + nominative head) is not a statute
# name. Detected by the resulting nominative key being a known non-name word or
# the modifier being implausibly short.  This is a deny-list of normalized keys
# seen to be pure false positives (over-firing recognizer), NOT a registry gap.
_FALSE_POSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "sellaki",  # sellainen / sellaista ("such")
        "tällaki",  # tällainen / tällaista ("this kind")
        "millaki",  # millainen
        "tuollaki",  # tuollainen
        "veronalaki",  # veron-alainen ("subject to tax")
        "alaki",  # -alainen tail ("subject to")
        "oikeudenalaki",
        "ulosoton-alaki",
        "valvonnanalaki",
        "elatusvelvollisuudenalaki",
    }
)


def _looks_like_false_positive(normalized: str) -> bool:
    """Heuristic: is this normalized fi-name key a recognizer false positive?

    Triggers when (1) the key is on the explicit deny-list, or (2) the modifier
    (key minus nominative head) ends in ``alai`` (the ``-alainen`` adjective
    family: ``veronalainen``, ``ulosotonalainen`` …, which inflect to
    ``-alaista``/``-alaisen`` and collapse to a spurious ``…alaki``), or (3) the
    modifier is empty or a single short fragment that is not a plausible
    compound statute name.
    """
    low = normalized.lower()
    if low in _FALSE_POSITIVE_KEYS:
        return True
    # The "-alainen" adjective family: modifier ends in "ala" before "laki"
    # (veron|alai|sta -> veron + alai? actually collapses to "...alaki").
    if low.endswith("alaki") and not low.endswith("vakuutusalaki"):
        # "-alainen"/"-alaista" adjectives, not a real "X-alalaki".
        return True
    return False


def _fi_name_to_modifier_stem(normalized: str) -> str:
    """From a fi-name normalized key (``modifier+nominativehead``) get the modifier.

    ``ympäristönsuojelulaki`` -> ``ympäristönsuojelu`` (loose-normalized).  If
    the key ends in no known head (shouldn't happen for by_name output), return
    the whole loose key.
    """
    low = normalized.lower()
    for head in _HEADS_LONGEST_FIRST:
        if low.endswith(head):
            return _loose_norm(low[: len(low) - len(head)])
    return _loose_norm(low)


def categorize_miss(
    normalized_name: str,
    indexes: RegistryIndexes,
) -> tuple[str, str]:
    """Categorize one ``fi-name:`` registry miss into a bucket.

    ``normalized_name`` is the key AFTER the ``fi-name:`` prefix is stripped
    (the recognizer's ``modifier+nominativehead``, already lower-cased).

    Returns ``(bucket, reason)``.
    """
    # The registry already returned "none" for the exact key (that's why this is
    # a miss). Distinguish the four buckets.
    loose_key = _loose_norm(normalized_name)

    # (c) NORMALIZATION MISMATCH: a registry base title exists whose LOOSE key
    # equals the fi-name loose key — the only reason it missed is the registry's
    # generation-first inflection produced a different exact key (spacing /
    # hyphen / head-variant).  Highest-precision fixable signal.
    if loose_key in indexes.loose_title_keys:
        ids = indexes.loose_title_keys[loose_key]
        return (
            BUCKET_C,
            f"loose key {loose_key!r} matches registry title id(s) "
            f"{sorted(ids)[:3]} but exact generated key missed",
        )

    # (d) GENUINELY-UNINDEXED BASE ACT: the act's AMENDMENT titles are in the
    # registry (so the base act exists in corpus) but its base head-bearing
    # title is not indexed with the matching inflected key.
    stem = _fi_name_to_modifier_stem(normalized_name)
    # Amendment stems are stored as loose base-name keys; match on the head-less
    # modifier stem by checking any amendment stem that STARTS with our modifier
    # stem (amendment captured "ympäristönsuojelulai", our stem is
    # "ympäristönsuojelu").
    if stem and stem in indexes.modifier_to_amend_ids:
        ids = indexes.modifier_to_amend_ids[stem]
        return (
            BUCKET_D,
            f"modifier stem {stem!r} has amendment titles "
            f"{sorted(ids)[:3]} but no indexed base head-title",
        )
    # Substring fallback: amendment title captured the base name WITH its head
    # stem (e.g. "ympäristönsuojelulai"); match where an amendment stem begins
    # with our modifier stem.
    for amend_stem, ids in indexes.modifier_to_amend_ids.items():
        if stem and amend_stem.startswith(stem) and len(stem) >= 6:
            return (
                BUCKET_D,
                f"modifier stem {stem!r} ⊑ amendment stem {amend_stem!r} "
                f"(ids {sorted(ids)[:3]}); base head-title unindexed",
            )

    # (e) RECOGNIZER FALSE POSITIVE: the normalized key is not a plausible
    # statute name — the recognizer fired on a common word ending in an oblique
    # head surface.  Check AFTER (c)/(d) so a real act is never mislabeled.
    if _looks_like_false_positive(normalized_name):
        return (
            BUCKET_E,
            f"normalized {normalized_name!r} is a common-word tail, not a "
            f"statute name (recognizer over-fired)",
        )

    # (a) NOT IN REGISTRY AT ALL: no exact key, no loose match, no amendment
    # evidence the act exists.  Either a non-corpus act, a generic/garbage head
    # token, or a name the corpus never carries as a head-bearing title.
    return (
        BUCKET_A,
        f"no exact/loose/amendment evidence for {normalized_name!r}",
    )


# ---------------------------------------------------------------------------
# Sample driver
# ---------------------------------------------------------------------------


@dataclass
class MissRecord:
    bucket: str
    normalized_name: str
    surface: str
    reason: str
    source_statute_id: str


@dataclass
class AnalysisResult:
    sample_size: int
    statutes_with_text: int
    total_fi_name_mentions: int
    resolved: int
    ambiguous: int
    statute_only: int
    misses: list[MissRecord]
    collisions_in_registry: int


def run_analysis(
    *,
    sample: int,
    artifact_path: Optional[Path] = None,
    seed: int = 1,
) -> AnalysisResult:
    """Sample the corpus, resolve by-name refs, categorize the statute_only misses.

    The sample is a RANDOM draw (fixed ``seed`` for reproducibility) over the
    full id list — the ids are chronologically ordered, so a prefix slice would
    over-sample archaic 1700s/1800s statutes (heavy recognizer-false-positive
    territory) and misrepresent the modern corpus.
    """
    import os
    import random

    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    if artifact_path is None:
        artifact_path = default_artifact_path()

    print(f"[load] registry artifact {artifact_path} ...", file=sys.stderr)
    entries = load_statute_name_entries(artifact_path)
    registry = load_statute_name_registry(artifact_path)
    indexes = build_registry_indexes(entries)
    # Count registry collisions (keys mapping to >1 distinct id) as a sanity
    # cross-check for bucket (b).
    collisions = sum(
        1
        for bucket in registry._index.values()
        if len({e.statute_id for e in bucket}) > 1
    )
    print(
        f"[load] {len(entries)} entries, {len(registry._index)} surface keys, "
        f"{collisions} colliding keys",
        file=sys.stderr,
    )

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    arch = os.path.join(root, "data", "finlex.farchive")
    store = TransparentCorpusStore(Farchive(arch))
    all_ids = store.list_statute_ids()
    rng = random.Random(seed)
    if sample < len(all_ids):
        sampled_ids = rng.sample(all_ids, sample)
    else:
        sampled_ids = list(all_ids)

    statutes_with_text = 0
    total_mentions = 0
    resolved = ambiguous = statute_only = 0
    misses: list[MissRecord] = []

    seen = 0
    for sid in sampled_ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        seen += 1
        statutes_with_text += 1
        try:
            text = xb.decode("utf-8", errors="ignore")
        except Exception:
            continue
        # Recognize by-name refs from raw body text.  We feed the whole decoded
        # XML text to the recognizer (it scans plain prose; tags are inert as
        # they carry no inflected-head tokens that the closed trigger set fires
        # on except inside <p>, which is exactly the body we want).
        mentions = recognize_by_name_refs(text)
        if not mentions:
            continue
        resolutions = resolve_mentions(mentions, statute_registry=registry, eu_registry=eu_nickname)
        for res in resolutions:
            tgt = res.mention.target_provision_ref
            if tgt is None or not tgt.statute_id.startswith(_FI_NAME_PREFIX):
                # Resolved ones get rewritten to the real id; recover the
                # original key from candidates is not needed for counting.
                pass
            total_mentions += 1
            if res.resolution_status is ResolutionStatus.RESOLVED:
                resolved += 1
            elif res.resolution_status is ResolutionStatus.AMBIGUOUS:
                ambiguous += 1
            elif res.resolution_status is ResolutionStatus.STATUTE_ONLY:
                statute_only += 1
                # The target id still carries the fi-name placeholder on a miss.
                placeholder = res.mention.target_provision_ref
                if placeholder is None:
                    continue
                normalized = placeholder.statute_id[len(_FI_NAME_PREFIX):]
                bucket, reason = categorize_miss(normalized, indexes)
                misses.append(
                    MissRecord(
                        bucket=bucket,
                        normalized_name=normalized,
                        surface=res.mention.surface_text or "",
                        reason=reason,
                        source_statute_id=sid,
                    )
                )

    return AnalysisResult(
        sample_size=seen,
        statutes_with_text=statutes_with_text,
        total_fi_name_mentions=total_mentions,
        resolved=resolved,
        ambiguous=ambiguous,
        statute_only=statute_only,
        misses=misses,
        collisions_in_registry=collisions,
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

_BUCKET_TITLES = {
    BUCKET_A: "(a) NOT IN REGISTRY AT ALL (real act, no corpus evidence)",
    BUCKET_B: "(b) COLLISION (lands ambiguous, not statute_only)",
    BUCKET_C: "(c) NORMALIZATION MISMATCH (fixable)",
    BUCKET_D: "(d) GENUINELY-UNINDEXED BASE ACT (registry build gap)",
    BUCKET_E: "(e) RECOGNIZER FALSE POSITIVE (over-firing recognizer)",
}

_BUCKET_ORDER = (BUCKET_E, BUCKET_D, BUCKET_C, BUCKET_A, BUCKET_B)


def render_report(result: AnalysisResult) -> str:
    """Render the categorized findings as a Markdown report."""
    lines: list[str] = []
    w = lines.append
    w("# Resolution-miss analysis — `fi-name:` `statute_only` misses\n")
    w("Diagnostic output of `lawvm.tools.resolution_miss_analysis` "
      "(by-name recognizer + full-registry resolver, diagnosis only).\n")
    w("## Sample\n")
    w(f"- statutes sampled (with text): **{result.statutes_with_text}**")
    w(f"- total `fi-name:` by-name mentions resolved: **{result.total_fi_name_mentions}**")
    den = max(1, result.total_fi_name_mentions)
    w(f"- RESOLVED: {result.resolved} ({100*result.resolved/den:.1f}%)")
    w(f"- AMBIGUOUS: {result.ambiguous} ({100*result.ambiguous/den:.1f}%)")
    w(f"- STATUTE_ONLY (miss): **{result.statute_only} "
      f"({100*result.statute_only/den:.1f}%)**")
    w(f"- registry colliding surface keys (bucket-b population): "
      f"{result.collisions_in_registry}\n")

    by_bucket: dict[str, list[MissRecord]] = collections.defaultdict(list)
    for m in result.misses:
        by_bucket[m.bucket].append(m)

    miss_den = max(1, result.statute_only)
    w("## Bucket breakdown\n")
    w("| bucket | count | % of statute_only |")
    w("|---|---:|---:|")
    for b in _BUCKET_ORDER:
        n = len(by_bucket.get(b, []))
        w(f"| {_BUCKET_TITLES[b]} | {n} | {100*n/miss_den:.1f}% |")
    w("")

    for b in _BUCKET_ORDER:
        recs = by_bucket.get(b, [])
        w(f"### {_BUCKET_TITLES[b]} — {len(recs)} misses\n")
        if not recs:
            w("_none._\n")
            continue
        # Most-frequent normalized names in this bucket.
        freq = collections.Counter(r.normalized_name for r in recs)
        w("Top names in this bucket (by frequency):\n")
        for name, c in freq.most_common(8):
            w(f"- `{name}` × {c}")
        w("\nConcrete examples (surface → why it missed):\n")
        shown: set[str] = set()
        n_shown = 0
        for r in recs:
            if r.normalized_name in shown:
                continue
            shown.add(r.normalized_name)
            surf = (r.surface[:70] + "…") if len(r.surface) > 70 else r.surface
            w(f"- `{surf}` → {r.reason} _(in {r.source_statute_id})_")
            n_shown += 1
            if n_shown >= 5:
                break
        w("")

        # Bucket (a) sub-breakdown by head: the non-laki/asetus heads (sopimus,
        # päätös, ilmoitus, määräys, ohje) are highly productive COMMON NOUNS, so
        # a large share of bucket (a) is suspected recognizer over-firing on
        # ordinary compound nouns ("vuokrasopimuksen" the noun, "lupapäätöksen"
        # the noun) rather than real unindexed acts.  Reporting the split keeps
        # the (a)-vs-(e) boundary honest.
        if b == BUCKET_A:
            head_counts: collections.Counter[str] = collections.Counter()
            for r in recs:
                low = r.normalized_name.lower()
                matched = "(other)"
                for h in _HEADS_LONGEST_FIRST:
                    if low.endswith(h):
                        matched = h
                        break
                head_counts[matched] += 1
            weak = {"sopimus", "päätös", "ilmoitus", "määräys", "ohje", "säädös"}
            weak_n = sum(c for h, c in head_counts.items() if h in weak)
            w("Bucket (a) by head (weak heads = productive common nouns, "
              "suspected over-firing):\n")
            for h, c in head_counts.most_common():
                tag = " ⚠ weak/common-noun head" if h in weak else ""
                w(f"- `…{h}` × {c}{tag}")
            w(f"\n**Suspected common-noun over-firing inside (a): "
              f"~{weak_n} / {len(recs)} "
              f"({100*weak_n/max(1, len(recs)):.0f}%)** "
              f"(weak heads sopimus/päätös/ilmoitus/määräys/ohje/säädös).\n")

    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--report", type=Path, default=None)
    ns = ap.parse_args(argv)

    result = run_analysis(sample=ns.sample, seed=ns.seed)
    report = render_report(result)
    if ns.report is not None:
        ns.report.parent.mkdir(parents=True, exist_ok=True)
        ns.report.write_text(report, encoding="utf-8")
        print(f"[report] wrote {ns.report}", file=sys.stderr)
    else:
        print(report)
    # Also echo the headline to stderr for CI visibility.
    print(
        f"[result] sampled={result.statutes_with_text} "
        f"fi_name_mentions={result.total_fi_name_mentions} "
        f"statute_only={result.statute_only}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
