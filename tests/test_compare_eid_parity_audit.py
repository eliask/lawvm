"""Tests for ``core.compare_eid_parity_audit`` (D10 ``COMPARE.EID_DOUBLE_CLASSIFIED``).

Per :file:`notes/LAWVM_AUDIT_REGISTRY_ROADMAP.md` D10 — the oracle-comparison
plane partitions each divergent eId into exactly one bucket. The audit asserts
that partition is a true partition under *canonical comparison identity*. The
synthetic regression covers:

* clean partition (each eId in exactly one bucket) → zero findings;
* the SAME canonical eId in two buckets (``section-II`` in deterministic_gap and
  ``section-2`` in manual_frontier) → exactly one finding, proving Roman/Arabic
  canonical identity catches the aliasing the raw-string check would miss;
* deterministic ordering across multiple collisions;
* empty input → empty output.

Audit-plane-only contract: the function emits observations and never raises (the
canonicalize callable here is the real UK one). ``Observation.kind`` is the
registered FindingSpec code, so the registry anti-drift checks in
``tests/test_finding_registry.py`` cover the wire-to-registry binding.
"""

from __future__ import annotations

from lawvm.core.compare_eid_parity_audit import (
    COMPARE_EID_DOUBLE_CLASSIFIED,
    assert_compare_eid_parity,
)
from lawvm.uk_legislation.canonicalize import canonicalize_compare_eid


def test_clean_partition_emits_no_finding() -> None:
    buckets = {
        "deterministic_gap": ["section-1", "section-3"],
        "manual_frontier": ["section-5"],
        "oracle_suspect": ["section-7"],
        "text_diff": [],
    }
    findings = assert_compare_eid_parity(buckets, canonicalize=canonicalize_compare_eid)
    assert findings == ()


def test_roman_arabic_alias_across_buckets_fires_once() -> None:
    # ``section-II`` (deterministic_gap) and ``section-2`` (manual_frontier) are
    # the SAME provision under canonical comparison identity; raw string equality
    # would miss it, canonical identity catches it.
    buckets = {
        "deterministic_gap": ["section-II"],
        "manual_frontier": ["section-2"],
        "oracle_suspect": [],
        "text_diff": [],
    }
    findings = assert_compare_eid_parity(buckets, canonicalize=canonicalize_compare_eid)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == COMPARE_EID_DOUBLE_CLASSIFIED
    assert finding.detail["canonical_eid"] == "section-2"
    assert finding.detail["colliding_buckets"] == ("deterministic_gap", "manual_frontier")
    assert finding.detail["raw_eids"] == ("section-2", "section-II")


def test_acceptance_manual_frontier_and_oracle_suspect_collision() -> None:
    # Roadmap acceptance case: one eId classified into both manual_frontier and
    # oracle_suspect → the audit fires.
    buckets = {
        "deterministic_gap": [],
        "manual_frontier": ["section-9"],
        "oracle_suspect": ["section-9"],
        "text_diff": [],
    }
    findings = assert_compare_eid_parity(buckets, canonicalize=canonicalize_compare_eid)
    assert len(findings) == 1
    assert findings[0].detail["canonical_eid"] == "section-9"
    assert findings[0].detail["colliding_buckets"] == ("manual_frontier", "oracle_suspect")


def test_intra_bucket_duplicate_is_not_a_cross_bucket_collision() -> None:
    # The same eId twice WITHIN one bucket is not a partition-exclusivity break.
    buckets = {
        "deterministic_gap": ["section-4", "section-4"],
        "manual_frontier": [],
    }
    findings = assert_compare_eid_parity(buckets, canonicalize=canonicalize_compare_eid)
    assert findings == ()


def test_deterministic_ordering_over_multiple_collisions() -> None:
    buckets = {
        "deterministic_gap": ["section-IV", "section-2"],
        "manual_frontier": ["section-4", "section-II"],
        "oracle_suspect": [],
        "text_diff": [],
    }
    findings = assert_compare_eid_parity(buckets, canonicalize=canonicalize_compare_eid)
    # Two canonical collisions: section-2 and section-4, in ascending canonical order.
    canonical_order = [f.detail["canonical_eid"] for f in findings]
    assert canonical_order == ["section-2", "section-4"]
    # Re-running yields byte-identical ordering and detail.
    again = assert_compare_eid_parity(buckets, canonicalize=canonicalize_compare_eid)
    assert [f.detail for f in findings] == [f.detail for f in again]


def test_empty_input_yields_empty_output() -> None:
    assert assert_compare_eid_parity({}, canonicalize=canonicalize_compare_eid) == ()


def test_default_identity_is_raw_string() -> None:
    # Without an injected canonicalizer, Roman/Arabic aliases do NOT collide
    # (raw identity), but exact duplicates across buckets do.
    aliased = {
        "deterministic_gap": ["section-II"],
        "manual_frontier": ["section-2"],
    }
    assert assert_compare_eid_parity(aliased) == ()

    exact = {
        "deterministic_gap": ["section-2"],
        "manual_frontier": ["section-2"],
    }
    findings = assert_compare_eid_parity(exact)
    assert len(findings) == 1
    assert findings[0].detail["canonical_eid"] == "section-2"
