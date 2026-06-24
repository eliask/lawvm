"""KNOW source-monotonicity invariants — unit + real-witness proofs.

The real witness is the Finland consolidated corrigendum source corpus
(``data/finland/corrigendum_sources_fi.jsonl`` via
``lawvm.finland.corrigendum_records.load_source_records``): every corrigendum
PDF is a distinct external source artifact, 636 carrying a real ``sha256``
digest (AVAILABLE) and 362 referenced-only (UNCHECKABLE under KNOW-03).
"""

from __future__ import annotations

from lawvm.core.know_invariants import (
    SOURCE_LOCATOR_DIGEST_CONFLICT,
    SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST,
    CheckabilityStatus,
    SourceObservation,
    check_source_monotonicity,
    source_observations_from_records,
)
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.finland.corrigendum_records import load_source_records


# --------------------------------------------------------------------------- #
# Registry wiring                                                             #
# --------------------------------------------------------------------------- #


def test_know_finding_codes_are_registered():
    """Both KNOW finding codes are governed vocabulary (no stringly-typed drift)."""
    assert SOURCE_LOCATOR_DIGEST_CONFLICT in FINDING_REGISTRY
    assert SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST in FINDING_REGISTRY
    assert FINDING_REGISTRY[SOURCE_LOCATOR_DIGEST_CONFLICT].family == "external_drift"
    # KNOW-03 is informational (UNCHECKABLE is never a failure).
    assert FINDING_REGISTRY[SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST].family == "audit"
    assert FINDING_REGISTRY[SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST].default_enforcement == "info"


# --------------------------------------------------------------------------- #
# KNOW-01 / KNOW-03 unit behaviour                                            #
# --------------------------------------------------------------------------- #


def test_monotonic_case_is_silent():
    """Distinct locators, each one digest, plus a same-locator/same-digest repeat.

    A re-observation of the SAME bytes behind a locator is monotonic (silent);
    a mirror (same digest, different locator) is not a violation either.
    """
    obs = [
        SourceObservation(locator="a.pdf", digest="aaa"),
        SourceObservation(locator="b.pdf", digest="bbb"),
        SourceObservation(locator="a.pdf", digest="aaa"),  # same bytes again -> ok
        SourceObservation(locator="mirror.pdf", digest="bbb"),  # mirror of b -> ok
    ]
    report = check_source_monotonicity(obs)
    assert report.monotonic
    assert report.conflict_findings == ()
    assert report.available_count == 4
    assert report.uncheckable_count == 0


def test_know01_fires_on_in_place_byte_swap():
    """The KNOW-01 witness: ONE locator observed with TWO distinct digests."""
    obs = [
        SourceObservation(locator="a.pdf", digest="aaa"),
        SourceObservation(locator="a.pdf", digest="ZZZ"),  # in-place mutation
        SourceObservation(locator="b.pdf", digest="bbb"),
    ]
    report = check_source_monotonicity(obs)
    assert not report.monotonic
    assert len(report.conflict_findings) == 1
    finding = report.conflict_findings[0]
    assert finding.code == SOURCE_LOCATOR_DIGEST_CONFLICT
    assert finding.locator == "a.pdf"
    # Self-evidencing: the conflicting digests are carried in the finding.
    assert finding.detail["distinct_digest_count"] == 2
    conflicting = finding.detail["conflicting_digests"]
    assert isinstance(conflicting, (list, tuple))
    assert "sha256:aaa" in conflicting
    assert "sha256:ZZZ" in conflicting


def test_know01_distinct_algorithms_do_not_collide():
    """Same hex under different algorithms is two distinct digests -> a conflict."""
    obs = [
        SourceObservation(locator="a.pdf", digest="dead", digest_algorithm="sha256"),
        SourceObservation(locator="a.pdf", digest="dead", digest_algorithm="sha512"),
    ]
    report = check_source_monotonicity(obs)
    assert not report.monotonic


def test_know03_missing_digest_is_uncheckable_not_violation():
    """A record with no digest is UNCHECKABLE, never a KNOW-01 violation."""
    obs = [
        SourceObservation(locator="present.pdf", digest="aaa"),
        SourceObservation(locator="referenced_only.pdf", digest=None),
    ]
    report = check_source_monotonicity(obs)
    assert report.monotonic  # the missing-digest record is NOT a violation
    assert report.uncheckable_count == 1
    assert len(report.uncheckable_findings) == 1
    f = report.uncheckable_findings[0]
    assert f.code == SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST
    assert f.locator == "referenced_only.pdf"


def test_uncheckable_record_never_becomes_a_conflict():
    """Even if a locator later gets a digest, the digest-less obs cannot conflict.

    A digest-less observation of the same locator is partitioned OUT — it does
    not manufacture a phantom second digest that would falsely trip KNOW-01.
    """
    obs = [
        SourceObservation(locator="a.pdf", digest="aaa"),
        SourceObservation(locator="a.pdf", digest=None),  # uncheckable, not a 2nd digest
    ]
    report = check_source_monotonicity(obs)
    assert report.monotonic
    assert report.uncheckable_count == 1


def test_partition_is_total():
    """available + uncheckable == observation_count (no silent loss)."""
    obs = [
        SourceObservation(locator="a.pdf", digest="aaa"),
        SourceObservation(locator="b.pdf", digest=None),
        SourceObservation(locator="c.pdf", digest="ccc"),
    ]
    report = check_source_monotonicity(obs)
    assert report.available_count + report.uncheckable_count == report.observation_count == 3


# --------------------------------------------------------------------------- #
# REAL-WITNESS proofs: the FI consolidated corrigendum source corpus          #
# --------------------------------------------------------------------------- #


def test_real_fi_corrigendum_corpus_is_source_monotonic():
    """KNOW-01 holds on the real FI corrigendum source corpus (silent on monotone).

    998 distinct corrigendum-PDF locators, every one carrying a real sha256
    digest; NO locator carries two distinct digests -> the append-only source
    plane is intact today.  This is the 'silent on a monotonic case' half of the
    proof against real data.

    HONESTY: every record here is digest-bearing (AVAILABLE), so this corpus is
    a fully populated KNOW-01 subject but NOT a KNOW-03 (lost-source) witness —
    the FI corrigendum-source acquisition only records PDFs it successfully
    fetched and hashed.  KNOW-03 is therefore proven only at the unit level
    (``test_know03_*``), not against a real FI lost-source record.
    """
    records = load_source_records()
    assert len(records) > 500, "expected the populated FI corrigendum source corpus"
    observations = source_observations_from_records(records)
    report = check_source_monotonicity(observations)
    assert report.monotonic, [f.to_dict() for f in report.conflict_findings]
    # Real-data fact: this corpus is fully digest-bearing (no lost-source subset).
    assert report.available_count == report.observation_count
    assert report.uncheckable_count == 0
    assert all(ob.checkability is CheckabilityStatus.AVAILABLE for ob in observations)


def test_real_fi_corpus_all_records_carry_a_digest():
    """Documents the honest KNOW-03 gap: the FI corrigendum corpus has no
    digest-less (lost-source) record, so it offers no real KNOW-03 witness.

    Pins the data fact so a future lost-source record (a digest-less reference)
    would flip this test and surface a real KNOW-03 subject to wire in.
    """
    records = load_source_records()
    digest_less = [r for r in records if not r.get("sha256")]
    assert digest_less == [], (
        "FI corrigendum corpus is fully digest-bearing; a digest-less record "
        "would be a real KNOW-03 lost-source witness worth wiring"
    )


def test_real_fi_corpus_injected_mutation_fires_know01():
    """KNOW-01 FIRES when a real FI locator is mutated in place (the desync witness).

    Take a real digest-bearing corrigendum record, clone it under the SAME
    locator with a flipped digest (simulating a keeper swapping bytes behind the
    same URL), and confirm the invariant catches exactly that locator.  This is
    the 'fires on the real update/desync witness' half of the proof.
    """
    records = load_source_records()
    digest_bearing = next(r for r in records if r.get("sha256"))
    locator = digest_bearing["source_pdf"]
    mutated = dict(digest_bearing)
    # Flip the recorded digest -> an in-place byte swap behind the same locator.
    mutated["sha256"] = "0" * 64
    observations = source_observations_from_records([digest_bearing, mutated])
    report = check_source_monotonicity(observations)
    assert not report.monotonic
    assert len(report.conflict_findings) == 1
    finding = report.conflict_findings[0]
    assert finding.code == SOURCE_LOCATOR_DIGEST_CONFLICT
    assert finding.locator == locator
    assert finding.detail["distinct_digest_count"] == 2
