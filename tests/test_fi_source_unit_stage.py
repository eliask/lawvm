"""Token/source-unit waist (StageResult endgame row #2) conversion tests.

Covers ``build_surface_bundle_staged`` returning a ``StageResult[SourceSurfaceBundle]``
whose ``coverage`` is the SegmentationGraph's exact char partition, whose residuals
are benign-whitespace, and whose evidence is a typed source witness — plus the
production-consumer fire-drill proving ``graph_build`` actually READS the coverage
account (a severed/never-read account is a FAIL).
"""
from __future__ import annotations

import pytest

from lawvm.core.legal_surface_lens import SourceSurfaceBundle
from lawvm.core.source_witness import SourceWitness
from lawvm.core.stage_result import (
    CoverageCertificate,
    StageResult,
)
from lawvm.finland.legal_surface import graph_build
from lawvm.finland.legal_surface.bundle import (
    build_surface_bundle,
    build_surface_bundle_staged,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Tata lakia sovelletaan 5 §:ssa tarkoitettuun toimintaan.</p>
          <p>Lisaksi 5 §:ssa saadetaan poikkeuksesta.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")

_STATUTE_ID = "123/2020"


def test_staged_returns_stage_result_of_bundle() -> None:
    stage = build_surface_bundle_staged(_XML, _STATUTE_ID, surface_time="2020-06-01")
    assert isinstance(stage, StageResult)
    assert isinstance(stage.value, SourceSurfaceBundle)
    # value path is byte-identical to the legacy wrapper (0-delta).
    wrapped = build_surface_bundle(_XML, _STATUTE_ID, surface_time="2020-06-01")
    assert stage.value == wrapped


def test_coverage_is_exact_char_partition() -> None:
    stage = build_surface_bundle_staged(_XML, _STATUTE_ID)
    coverage = stage.coverage
    assert isinstance(coverage, CoverageCertificate)
    assert coverage.unit == "chars"
    unit = stage.value.units[0]
    assert coverage.total == len(unit.raw_text)
    # the four classes partition the whole body exactly (the totality check).
    assert coverage.is_partition()
    # whitespace is the only residue today; nothing benign-bucketed, no violation.
    assert coverage.benign == 0
    assert coverage.violation == 0
    assert coverage.is_clean
    assert coverage.owned + coverage.residual == coverage.total


def test_residuals_are_benign_whitespace_non_blocking() -> None:
    stage = build_surface_bundle_staged(_XML, _STATUTE_ID)
    unit = stage.value.units[0]
    residual_chars = 0
    for residual in stage.residuals:
        assert residual.kind == "benign_uninterpreted"
        assert residual.reason == "segmentation_benign_whitespace"
        assert residual.blocking is False
        assert residual.source_unit_id == unit.source_unit_id
        assert residual.char_start is not None and residual.char_end is not None
        # self-evidencing: the verbatim span is carried and is whitespace.
        assert residual.text == unit.raw_text[residual.char_start : residual.char_end]
        assert residual.text.strip() == ""
        residual_chars += residual.char_end - residual.char_start
    # the residual chars reconcile with the coverage residual count.
    assert residual_chars == stage.coverage.residual
    # benign whitespace must NOT forbid a clean claim.
    assert not stage.has_blocking_residual


def test_evidence_carries_typed_source_witness_over_body_hash() -> None:
    stage = build_surface_bundle_staged(_XML, _STATUTE_ID)
    assert not stage.evidence.is_empty
    (witness,) = stage.evidence.witnesses
    assert isinstance(witness, SourceWitness)
    assert witness.source_role == "statute_body_source"
    assert witness.artifact_id == _STATUTE_ID
    assert witness.digest is not None
    assert witness.digest.digest_algorithm == "sha256"
    # the digest is the body source hash, content-addressed (not derived from id).
    assert witness.digest.digest == stage.value.units[0].source_hash


def test_authority_is_neutral_firewall() -> None:
    stage = build_surface_bundle_staged(_XML, _STATUTE_ID)
    assert stage.authority.is_neutral
    assert stage.authority.replay_authorized is False
    assert stage.findings == ()


def test_consumer_reads_coverage_clean_build() -> None:
    # Fire-drill: the production consumer (build_legal_surface_graph) drives the
    # staged builder and reads the coverage account; a clean real build succeeds.
    g = graph_build.build_legal_surface_graph(_XML, _STATUTE_ID, surface_time="2020-06-01")
    assert g is not None
    assert g.subject.work_id == _STATUTE_ID


def test_consumer_fails_loud_on_non_partition_coverage(monkeypatch) -> None:
    # Prove the consumer ACTUALLY READS .coverage (not a field only tests touch):
    # inject a non-partition coverage and assert graph_build fails loud.
    real = build_surface_bundle_staged

    def _broken(*args, **kwargs):
        stage = real(*args, **kwargs)
        bad = CoverageCertificate(
            unit="chars",
            total=stage.coverage.total + 1,  # owned+residual no longer == total
            owned=stage.coverage.owned,
            residual=stage.coverage.residual,
        )
        return StageResult(
            value=stage.value,
            evidence=stage.evidence,
            residuals=stage.residuals,
            coverage=bad,
            authority=stage.authority,
        )

    monkeypatch.setattr(graph_build, "build_surface_bundle_staged", _broken)
    with pytest.raises(ValueError, match="not a total partition"):
        graph_build.build_legal_surface_graph(_XML, _STATUTE_ID)


def test_consumer_fails_loud_on_violation_coverage(monkeypatch) -> None:
    real = build_surface_bundle_staged

    def _violating(*args, **kwargs):
        stage = real(*args, **kwargs)
        # keep the partition total but reclassify one owned char as a violation.
        assert stage.coverage.owned >= 1
        bad = CoverageCertificate(
            unit="chars",
            total=stage.coverage.total,
            owned=stage.coverage.owned - 1,
            residual=stage.coverage.residual,
            violation=1,
        )
        return StageResult(
            value=stage.value,
            evidence=stage.evidence,
            residuals=stage.residuals,
            coverage=bad,
            authority=stage.authority,
        )

    monkeypatch.setattr(graph_build, "build_surface_bundle_staged", _violating)
    with pytest.raises(ValueError, match="unowned violation"):
        graph_build.build_legal_surface_graph(_XML, _STATUTE_ID)
