"""Source-correction findings consume the model's content digests.

These tests pin the consumer side of ``AmendmentSourceModel.source_digest`` /
``pre_correction_digest``: the corrigendum-correction findings carry the model's
content-hash pair (not just an ``op_id`` name), and a name-vs-content drift
cross-check fires when bytes changed under correction with no owning patch op.
"""

from __future__ import annotations

import lxml.etree as etree
import pytest

from lawvm.core.phase_result import Finding
from lawvm.finland.process_acquisition import ProcessAcquisitionContext
from lawvm.finland.process_findings import ProcessFindingRecorder
from lawvm.finland.source_model import AmendmentSourceModel, SourceMetadataSeed

_POST_XML = (
    b"<akomaNtoso><act><preamble>muutetaan 3 ss seuraavasti:</preamble>"
    b"<body><section><num>3 ss</num></section></body></act></akomaNtoso>"
)
_PRE_XML = (
    b"<akomaNtoso><act><preamble>muutetaan 2 ss seuraavasti:</preamble>"
    b"<body><section><num>3 ss</num></section></body></act></akomaNtoso>"
)


def _context(amendment_id: str = "2020/100") -> ProcessAcquisitionContext:
    return ProcessAcquisitionContext(
        amendment_id=amendment_id,
        parent_id="1990/1",
        parent_title="Test laki",
        parent_issue_date="1990-01-01",
        xml_bytes=_POST_XML,
        strict_profile=None,
        processed_amendment_titles={},
        effect_relation_signals=[],
        finding_recorder=ProcessFindingRecorder(process_findings=[]),
        record_finding=lambda **_kwargs: pytest.fail("record_finding must not run"),
        replay_print=lambda _msg: None,
        tree_title=lambda _tree: "",
        amendment_lacks_operative_structure=lambda _tree: (False, []),
    )


def _model(post: bytes, pre: bytes | None) -> AmendmentSourceModel:
    return AmendmentSourceModel.from_tree(
        etree.fromstring(post),
        source_ref="2020/100",
        source_bytes=post,
        pre_correction_bytes=pre,
    )


def _findings(ctx: ProcessAcquisitionContext) -> list[Finding]:
    return ctx.finding_recorder.process_findings


def test_patch_finding_carries_model_content_digest_pair() -> None:
    ctx = _context()
    model = _model(_POST_XML, _PRE_XML)
    assert model.source_digest is not None
    assert model.pre_correction_digest is not None

    ctx._record_source_correction_findings(model, ("op-1",))

    findings = _findings(ctx)
    assert len(findings) == 1
    detail = findings[0].detail
    assert detail["op_id"] == "op-1"
    # The asserted content change is anchored to the model's content hashes,
    # not reconstructed from the op_id name.
    assert detail["source_digest"]["digest"] == model.source_digest.digest
    assert detail["pre_correction_digest"]["digest"] == model.pre_correction_digest.digest
    assert detail["content_change_witnessed"] is True
    assert "digest_drift" not in detail


def test_patch_finding_flags_drift_when_digests_not_distinct() -> None:
    ctx = _context()
    # A patch claims a change, but the model carries no distinct pre/post pair
    # (no pre_correction_bytes): the name-based claim diverged from content.
    model = _model(_POST_XML, None)
    assert model.pre_correction_digest is None

    ctx._record_source_correction_findings(model, ("op-1",))

    detail = _findings(ctx)[0].detail
    assert detail["content_change_witnessed"] is False
    assert detail["digest_drift"] == "patch_claimed_change_without_distinct_digests"


def test_no_patch_quiet_when_content_unchanged() -> None:
    ctx = _context()
    model = _model(_POST_XML, None)

    ctx._record_source_correction_findings(model, ())

    assert _findings(ctx) == []


def test_no_patch_flags_unowned_content_drift() -> None:
    ctx = _context()
    # Content changed under correction (distinct pre/post digests) but no patch
    # op claims ownership: surface the drift instead of letting it stay silent.
    model = _model(_POST_XML, _PRE_XML)

    assert model.source_digest is not None
    assert model.pre_correction_digest is not None

    ctx._record_source_correction_findings(model, ())

    findings = _findings(ctx)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "APPLY.SOURCE_CORRECTION_DIGEST_DRIFT"
    assert finding.detail["source_digest"]["digest"] == model.source_digest.digest
    assert (
        finding.detail["pre_correction_digest"]["digest"]
        == model.pre_correction_digest.digest
    )
    assert finding.detail["digest_drift"] == "content_changed_without_owning_patch"


def test_acquisition_uses_selection_metadata_seed_when_source_is_uncorrected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = SourceMetadataSeed(
        source_issue_date=None,
        source_title="selection title",
        effective_date=None,
        effective_date_step="selection-step",
    )
    ctx = _context()
    ctx.selection_metadata = seed
    monkeypatch.setattr(
        ProcessAcquisitionContext,
        "_apply_source_corrections",
        lambda self, xml_bytes: (xml_bytes, ()),
    )

    acquired = ctx.acquire()

    assert acquired.source_model.metadata_seed is seed


def test_acquisition_drops_selection_metadata_seed_when_source_is_corrected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = SourceMetadataSeed(
        source_issue_date=None,
        source_title="stale selection title",
        effective_date=None,
        effective_date_step="selection-step",
    )
    ctx = _context()
    ctx.selection_metadata = seed
    monkeypatch.setattr(
        ProcessAcquisitionContext,
        "_apply_source_corrections",
        lambda self, xml_bytes: (xml_bytes, ("patch-op",)),
    )

    acquired = ctx.acquire()

    assert acquired.source_model.metadata_seed is None
