"""Unit tests for the refs-bench reference-resolution coverage benchmark.

Two layers, mirroring how other corpus-dependent tools are tested:

* A fast, archive-free unit test that drives the per-statute tally logic
  (``_scan_one``'s core) over a tiny synthetic in-memory AKN document and asserts
  a status distribution is produced without error.
* An archive-guarded integration test that runs the real ``run_fi`` scan with
  ``--limit`` and asserts a status distribution falls out — SKIPPED with a clear
  reason when ``data/finlex.farchive`` is not present in this checkout.
"""

from __future__ import annotations

import collections
import os
from types import SimpleNamespace

import pytest

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions
from lawvm.tools import refs_bench


_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A minimal AKN body with a plain-text cross-statute citation "(731/1999)".
_SYNTHETIC_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN_NS}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Tasta jonka mukaan perustuslaissa (731/1999) 5 § saadetaan.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _tally(xml_bytes: bytes, sid: str) -> dict[str, int]:
    """Replicate refs_bench's per-statute status tally (the worker core, no farchive)."""
    result = extract_all_reference_mentions(xml_bytes, sid)
    status_ct: collections.Counter[str] = collections.Counter()
    for m in result.mentions:
        status_ct[m.cite_confidence.value] += 1
    return dict(status_ct)


def test_status_order_covers_all_confidences() -> None:
    """The display order must name every CiteConfidence value (no silent drop)."""
    for conf in CiteConfidence:
        assert conf.value in refs_bench._STATUS_ORDER


def test_ordered_status_items_includes_unknown() -> None:
    ct: collections.Counter[str] = collections.Counter({"exact": 3, "weird_status": 1})
    ordered = refs_bench._ordered_status_items(ct)
    keys = [k for k, _ in ordered]
    # Canonical statuses appear first in declared order; unknowns appended.
    assert keys[0] == "exact"
    assert "weird_status" in keys
    assert dict(ordered)["exact"] == 3
    assert dict(ordered)["weird_status"] == 1


def test_synthetic_scan_produces_status_distribution() -> None:
    """A tiny synthetic statute yields a non-empty resolution-status tally."""
    tally = _tally(_SYNTHETIC_XML, "001/2000")
    # The plain-text "(731/1999)" citation must be extracted and classified.
    assert sum(tally.values()) >= 1
    # Every key must be a real CiteConfidence value.
    valid = {c.value for c in CiteConfidence}
    assert set(tally).issubset(valid)
    # A bare statute-level citation with no section is STATUTE_ONLY (or EXACT if a
    # section parsed) — in either case it is a recognized confidence, not a crash.
    assert any(
        s in (CiteConfidence.STATUTE_ONLY.value, CiteConfidence.EXACT.value)
        for s in tally
    )


def test_kind_distribution_recognizes_cross_statute() -> None:
    result = extract_all_reference_mentions(_SYNTHETIC_XML, "001/2000")
    kinds = {m.cite_kind.value for m in result.mentions}
    assert CiteKind.CROSS_STATUTE.value in kinds


# A synthetic body carrying BOTH a covered anchor and an uncovered one:
#   - "(731/1999)" — a plain-text statute id the recognizer emits a mention for
#     (its STATUTE_ID anchor + the surrounding STATUTE_NAME_HEAD must be COVERED).
#   - "direktiivin 12 artiklan" — an EU directive / artikla family now captured
#     by the EU-by-nickname lane as STATUTE_ONLY (directive identity pending), so
#     its ARTIKLA anchor is COVERED.
_RECALL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN_NS}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Tasta jonka mukaan perustuslaissa (731/1999) 5 § saadetaan.</p>
          <p>Lisaksi sovelletaan direktiivin 12 artiklan saannoksia.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _recall_anchor_scan(xml_bytes: bytes, sid: str):
    """Drive the recall anchor scan + coverage mask offline (no farchive).

    Mirrors the body of ``_scan_one_recall`` over an in-memory document so the
    covered/missed bookkeeping is unit-testable without the corpus archive.
    Returns (per_anchor_counts, miss_examples).
    """
    text = refs_bench._decode_body(xml_bytes)
    result = extract_all_reference_mentions(xml_bytes, sid)
    intervals = refs_bench._coverage_intervals(text, result.mentions)

    counts: dict[str, list[int]] = {t: [0, 0] for t in refs_bench._ANCHOR_ORDER}
    miss_examples: list[tuple[str, str]] = []
    for anchor_type, pat, guard in refs_bench._ANCHOR_PATTERNS:
        if guard is not None and guard not in text:
            continue
        for m in pat.finditer(text):
            counts[anchor_type][1] += 1
            if refs_bench._covered(intervals, m.start(), m.end()):
                counts[anchor_type][0] += 1
            else:
                miss_examples.append((anchor_type, m.group(0)))
    return counts, miss_examples


def test_recall_anchor_coverage_and_miss_worklist_mechanism() -> None:
    """The recall scan covers captured anchors and lists the rest as typed misses.

    Asserts the bench MECHANISM, not a particular recogniser's reach (which rises
    as lanes are wired in). Two anchors are stably covered: an explicit statute id
    (plain-text lane) and a directive ``artikla`` (the EU-by-nickname lane now
    types ``direktiivin 12 artiklan`` STATUTE_ONLY — anaphoric directive, identity
    pending). The miss worklist must still surface the remaining anchors as
    correctly-typed (anchor_type, snippet) tuples.
    """
    counts, miss_examples = _recall_anchor_scan(_RECALL_XML, "001/2000")

    # The "(731/1999)" parenthetical is emitted as a plain-text mention, so its
    # STATUTE_ID anchor must be covered (covered == total for that type here).
    sid_cov, sid_tot = counts["STATUTE_ID"]
    assert sid_tot >= 1
    assert sid_cov == sid_tot, "explicit statute id should be a covered anchor"

    # The directive "artiklan" reference is now captured (EU-by-nickname lane,
    # wired into extract_all_reference_mentions) → covered, not a miss.
    artikla_cov, artikla_tot = counts["ARTIKLA"]
    assert artikla_tot >= 1
    assert artikla_cov == artikla_tot, "directive artikla should now be covered"

    # The miss worklist machinery yields correctly-typed entries for whatever
    # remains uncovered (e.g. the structural section-num §). We assert its SHAPE,
    # not which family is missed (that shrinks as recognizers land).
    assert miss_examples, "some anchors remain uncovered in this fixture"
    assert all(t in refs_bench._ANCHOR_ORDER and snip for t, snip in miss_examples)


def test_coverage_intervals_merge_and_overlap() -> None:
    """Coverage intervals merge adjacent ranges and overlap test is correct."""
    merged = refs_bench._coverage_intervals(
        "aaa (731/1999) bbb (731/1999) ccc",
        # two synthetic mentions, one with surface text, one id-only
        [
            SimpleNamespace(surface_text="(731/1999)", target_provision_ref=None),
            SimpleNamespace(
                surface_text="",
                target_provision_ref=SimpleNamespace(statute_id="731/1999"),
            ),
        ],
    )
    assert merged, "should locate the id surface in the body"
    # An anchor span inside a covered interval overlaps; one far outside does not.
    lo, hi = merged[0]
    assert refs_bench._covered(merged, lo, hi)
    assert not refs_bench._covered(merged, 0, 3)  # the "aaa" prefix is uncovered


def test_scorecard_buckets_cover_all_confidences() -> None:
    """Every CiteConfidence value maps to a scorecard bucket (no silent drop)."""
    for conf in CiteConfidence:
        assert conf.value in refs_bench._SCORECARD_BUCKET
        bucket = refs_bench._SCORECARD_BUCKET[conf.value]
        assert bucket in refs_bench._SCORECARD_BUCKET_ORDER


def test_scorecard_rows_bucket_math() -> None:
    """Per-family rows collapse statuses into buckets; fractions sum to 100%."""
    status_ct_by_kind = {
        "cross_statute": collections.Counter(
            {"exact": 98, "ambiguous": 1, "open": 1}
        ),
        "eu": collections.Counter(
            {"exact": 45, "approximate": 45, "statute_only": 8, "ambiguous": 2}
        ),
    }
    rows = refs_bench._scorecard_rows(collections.Counter(), status_ct_by_kind)
    by_kind = {kind: (total, buckets) for kind, total, buckets in rows}

    # cross_statute: 98% resolved / 1% ambiguous / 1% open.
    cs_total, cs_buckets = by_kind["cross_statute"]
    assert cs_total == 100
    cs = {b: (c, p) for b, c, p in cs_buckets}
    assert cs["resolved"] == (98, pytest.approx(98.0))
    assert cs["ambiguous"][0] == 1
    assert cs["open"][0] == 1
    assert sum(p for _, p in cs.values()) == pytest.approx(100.0)

    # eu: approximate folds into resolved (45+45=90).
    eu_total, eu_buckets = by_kind["eu"]
    eu = {b: c for b, c, _ in eu_buckets}
    assert eu_total == 100
    assert eu["resolved"] == 90
    assert eu["statute_only"] == 8
    assert eu["ambiguous"] == 2
    assert "approximate" not in eu  # folded, not a standalone bucket

    # Rows are sorted by descending total (tie here → both 100; stable enough).
    assert all(rows[i][1] >= rows[i + 1][1] for i in range(len(rows) - 1))


def test_scorecard_unknown_status_lands_in_other() -> None:
    """A status with no bucket mapping is collapsed into 'other', never dropped."""
    rows = refs_bench._scorecard_rows(
        collections.Counter(),
        {"eu": collections.Counter({"exact": 1, "weird_new_status": 1})},
    )
    _, total, buckets = rows[0]
    assert total == 2
    names = {b for b, _, _ in buckets}
    assert "other" in names
    assert refs_bench._bucket_for_status("weird_new_status") == "other"


_ARCHIVE = os.path.join(
    os.environ.get("LAWVM_CANONICAL_DATA_ROOT", "."), "data", "finlex.farchive"
)


@pytest.mark.skipif(
    not os.path.exists(_ARCHIVE),
    reason=f"Requires {_ARCHIVE} (corpus archive not present in this checkout)",
)
@pytest.mark.slow
def test_run_fi_limit_smoke(capsys) -> None:
    """run_fi over a tiny --limit sample prints a status distribution without error."""
    args = SimpleNamespace(jurisdiction="fi", limit=5, workers=2, top=5, json=False)
    refs_bench.run_fi(args)
    out = capsys.readouterr().out
    assert "reference resolution coverage" in out
    assert "resolution-status distribution" in out
    assert "EXACT COVERAGE" in out
    # Scorecard NOT requested → not printed (additive flag, no behavior change).
    assert "per-family SCORECARD" not in out


@pytest.mark.skipif(
    not os.path.exists(_ARCHIVE),
    reason=f"Requires {_ARCHIVE} (corpus archive not present in this checkout)",
)
@pytest.mark.slow
def test_run_fi_scorecard_smoke(capsys) -> None:
    """run_fi with --scorecard prints the per-family scorecard table additively."""
    args = SimpleNamespace(
        jurisdiction="fi", limit=20, workers=2, top=5, json=False, scorecard=True
    )
    refs_bench.run_fi(args)
    out = capsys.readouterr().out
    # Original output still present.
    assert "EXACT COVERAGE" in out
    # Scorecard table present.
    assert "per-family SCORECARD" in out


@pytest.mark.skipif(
    not os.path.exists(_ARCHIVE),
    reason=f"Requires {_ARCHIVE} (corpus archive not present in this checkout)",
)
@pytest.mark.slow
def test_run_fi_recall_limit_smoke(capsys) -> None:
    """run_fi_recall over a tiny --limit sample prints the recall worklist header."""
    args = SimpleNamespace(jurisdiction="fi", limit=5, workers=2, top=5, json=False)
    refs_bench.run_fi_recall(args)
    out = capsys.readouterr().out
    assert "anchor-driven RECALL proxy" in out
    assert "OVERALL RECALL (proxy)" in out
    assert "MISS WORKLIST" in out
