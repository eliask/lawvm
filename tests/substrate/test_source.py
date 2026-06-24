"""Pins for the source-plane lineage objects (SOURCE_LINEAGE_V0.md; design §3.4).

Covers: id-derivation determinism, the ``"sha256:"`` prefix, NFC-at-construction,
the metadata-republish invariant (keeper hints / availability change but NOT the
semantic id), availability / genesis / role enum faithfulness, the §5
``unclassified`` hard rule, the §8.2 snapshot-genesis ``creation_event_id`` rule,
and a couple of golden hash vectors that lock the canonical bytes.
"""

from __future__ import annotations

import pytest

from lawvm.substrate.canonical_json import semantic_hash, unwrap_and_verify, wrap_row
from lawvm.substrate.roots import leaf_hash, set_root
from lawvm.substrate.source import (
    Availability,
    ExtractionCorrectionAssertion,
    GenesisKind,
    InitialStateEvent,
    KeeperCorrectionEvent,
    KeeperVersionHints,
    LegalEffect,
    Locator,
    OfficialCorrigendumEvent,
    PriorHistoryStatus,
    RecomputeScope,
    SourceBundleVersion,
    SourceDeltaClassification,
    SourceLocatorRef,
    SourceManifestation,
    SourceRecord,
    SourceRole,
    SourceUnit,
    SourceUnitDelta,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _record(keeper: str = "keeper_a") -> SourceRecord:
    return SourceRecord(
        jurisdiction="xx",
        keeper=keeper,
        logical_kind="act_xml",
        logical_key="xx:keeper_a:act_xml:301/2004",
        work_id_hint="xx:act:301/2004",
    )


def _manifestation(
    record_id: str,
    *,
    availability: str | Availability = Availability.AVAILABLE_IN_LAWVM_CAS,
    hints: KeeperVersionHints | None = None,
) -> SourceManifestation:
    return SourceManifestation(
        source_record_id=record_id,
        raw_witness_hash="sha256:" + "ab" * 32,
        media_type="application/xml",
        fetched_at="2026-06-21T08:00:00Z",
        locator=Locator(scheme="farchive", value="cas://blob", byte_count=48213),
        availability=availability,
        keeper_version_hints=hints,
    )


def _locator() -> SourceLocatorRef:
    return SourceLocatorRef(
        jurisdiction="xx",
        artifact_kind="akn_xml",
        source_id="src-1",
        quote_hash="sha256:" + "cd" * 32,
        normalization_policy="lawvm.canon.source_normalization.v1",
        byte_span=(1024, 1280),
    )


def _unit(manifestation_id: str, record_id: str, *, text: str = "hello §1") -> SourceUnit:
    return SourceUnit(
        manifestation_id=manifestation_id,
        source_record_id=record_id,
        work_id="xx:act:301/2004",
        source_role=SourceRole.ENACTED,
        extraction_profile="lawvm.extract.akn_xml.v0",
        canonical_text=text,
        source_locator=_locator(),
        semantic_metadata={"language": "fi", "provision_status": "in_force"},
    )


# ---------------------------------------------------------------------------
# Determinism + prefix + recompute
# ---------------------------------------------------------------------------


def test_every_id_is_sha256_prefixed_and_deterministic() -> None:
    rec = _record()
    man = _manifestation(rec.source_record_id)
    unit = _unit(man.manifestation_id, rec.source_record_id)
    bundle = SourceBundleVersion(
        corpus_version="xx:corpus:2026-06-21",
        built_under_source_policy="keeper_latest_semantic",
        checkable_under_source_policy="archival_exact",
        manifestation_ids=(man.manifestation_id,),
        source_unit_ids=(unit.source_unit_id,),
        correction_event_ids=(),
    )
    for value in (
        rec.source_record_id,
        man.manifestation_id,
        unit.source_unit_id,
        bundle.source_bundle_version_id,
    ):
        assert value.startswith("sha256:")
    # Determinism: rebuilding identical inputs yields identical ids.
    assert _record().source_record_id == rec.source_record_id
    assert _manifestation(rec.source_record_id).manifestation_id == man.manifestation_id


def test_ids_recompute_from_emitted_body_minus_id() -> None:
    rec = _record()
    body = rec.to_canonical_dict()
    # The id is leaf_hash over the body WITHOUT the id member (and without the
    # non-authoritative work_id_hint, per §1.1).
    stripped = {k: v for k, v in body.items() if k not in {"source_record_id", "work_id_hint"}}
    assert leaf_hash("source_record", stripped) == rec.source_record_id


def test_wrapper_row_roundtrips_and_object_hash_matches() -> None:
    rec = _record()
    row = wrap_row(rec.to_canonical_dict())
    assert row["object_hash"] == semantic_hash(rec.to_canonical_dict())
    assert unwrap_and_verify(row) == rec.to_canonical_dict()


# ---------------------------------------------------------------------------
# §1.1 identity scoping
# ---------------------------------------------------------------------------


def test_keeper_is_part_of_record_identity_no_cross_keeper_dedup() -> None:
    # §9.1 RESOLVED: same logical source, two keepers → two distinct records.
    a = _record(keeper="keeper_a")
    b = _record(keeper="keeper_b")
    assert a.source_record_id != b.source_record_id


def test_work_id_hint_does_not_affect_record_identity() -> None:
    a = SourceRecord("xx", "k", "act_xml", "lk", work_id_hint="xx:act:1/2000")
    b = SourceRecord("xx", "k", "act_xml", "lk", work_id_hint=None)
    assert a.source_record_id == b.source_record_id


def test_metadata_republish_changes_provenance_not_semantic_id() -> None:
    # The republish invariant (§7): keeper hints + availability are observation /
    # verdict metadata, NOT identity. A metadata-only republish of the SAME bytes
    # at the same time/locator leaves manifestation_id unchanged.
    rec = _record()
    base = _manifestation(rec.source_record_id, availability=Availability.AVAILABLE_IN_LAWVM_CAS)
    republished = _manifestation(
        rec.source_record_id,
        availability=Availability.AVAILABLE_FROM_KEEPER_AT_LOCATOR,
        hints=KeeperVersionHints(etag="new-etag", consolidation_date="2026-06-22"),
    )
    assert base.manifestation_id == republished.manifestation_id
    # But the emitted rows differ (the metadata IS visible / carried).
    assert base.to_canonical_dict() != republished.to_canonical_dict()


def test_manifestation_id_changes_when_bytes_change() -> None:
    rec = _record()
    man = _manifestation(rec.source_record_id)
    other = SourceManifestation(
        source_record_id=rec.source_record_id,
        raw_witness_hash="sha256:" + "ff" * 32,  # different bytes
        media_type="application/xml",
        fetched_at="2026-06-21T08:00:00Z",
        locator=Locator(scheme="farchive", value="cas://blob", byte_count=48213),
        availability=Availability.AVAILABLE_IN_LAWVM_CAS,
    )
    assert man.manifestation_id != other.manifestation_id


# ---------------------------------------------------------------------------
# §1.3 extraction binding + NFC
# ---------------------------------------------------------------------------


def test_source_unit_id_binds_extraction_profile() -> None:
    # §9.5: two extractions of the SAME bytes under different profiles → distinct units.
    rec = _record()
    man = _manifestation(rec.source_record_id)
    u0 = _unit(man.manifestation_id, rec.source_record_id)
    u1 = SourceUnit(
        manifestation_id=man.manifestation_id,
        source_record_id=rec.source_record_id,
        work_id="xx:act:301/2004",
        source_role=SourceRole.ENACTED,
        extraction_profile="lawvm.extract.akn_xml.v1",  # improved profile
        canonical_text="hello §1",
        source_locator=_locator(),
    )
    assert u0.source_unit_id != u1.source_unit_id


def test_canonical_text_is_nfc_normalized_at_construction() -> None:
    # "e-acute" composed (U+00E9) vs decomposed (e + combining acute U+0301).
    composed_text = "caf\u00e9"
    decomposed_text = "cafe\u0301"
    assert composed_text != decomposed_text  # distinct codepoint sequences
    composed = _unit("sha256:m", "sha256:r", text=composed_text)
    decomposed = _unit("sha256:m", "sha256:r", text=decomposed_text)
    assert composed.canonical_text == composed_text  # NFC = precomposed form
    assert decomposed.canonical_text == composed_text  # decomposed -> NFC at construction
    assert composed.source_unit_id == decomposed.source_unit_id
    assert composed.to_canonical_dict() == decomposed.to_canonical_dict()


def test_section_sign_and_nbsp_preserved_in_canonical_text() -> None:
    unit = _unit("sha256:m", "sha256:r", text="1 §")  # NBSP + section sign
    assert unit.canonical_text == "1 §"


# ---------------------------------------------------------------------------
# §1.4 bundle set roots
# ---------------------------------------------------------------------------


def test_bundle_set_roots_match_set_root() -> None:
    bundle = SourceBundleVersion(
        corpus_version="xx:corpus:2026-06-21",
        built_under_source_policy="keeper_latest_semantic",
        checkable_under_source_policy="archival_exact",
        manifestation_ids=("sha256:m1", "sha256:m2"),
        source_unit_ids=("sha256:u1",),
        correction_event_ids=(),
    )
    assert bundle.manifestation_set_root == set_root(
        "source_manifestation", ("sha256:m1", "sha256:m2")
    )
    assert bundle.source_unit_set_root == set_root("source_unit", ("sha256:u1",))
    assert bundle.correction_event_root == set_root("source_correction", ())


def test_bundle_id_changes_with_membership() -> None:
    def _bundle(manifestation_ids: tuple[str, ...]) -> SourceBundleVersion:
        return SourceBundleVersion(
            corpus_version="xx:corpus:2026-06-21",
            built_under_source_policy="keeper_latest_semantic",
            checkable_under_source_policy="archival_exact",
            manifestation_ids=manifestation_ids,
            source_unit_ids=("sha256:u1",),
            correction_event_ids=(),
        )

    a = _bundle(("sha256:m1",))
    b = _bundle(("sha256:m1", "sha256:m2"))
    assert a.source_bundle_version_id != b.source_bundle_version_id


# ---------------------------------------------------------------------------
# §4 correction events
# ---------------------------------------------------------------------------


def test_three_correction_events_share_one_domain_and_carry_legal_effect() -> None:
    corr = OfficialCorrigendumEvent(
        source_record_id="sha256:r",
        old_source_unit_id="sha256:u0",
        new_source_unit_id="sha256:u1",
        instrument_ref="xx:corrigendum:1",
        correction_kind="text_fix",
        legal_effect=LegalEffect.RELATES_BACK,
    )
    keeper = KeeperCorrectionEvent(
        source_record_id="sha256:r",
        old_manifestation_id="sha256:m0",
        new_manifestation_id="sha256:m1",
        old_source_unit_id="sha256:u0",
        new_source_unit_id="sha256:u1",
        reason="ocr_fix",
        legal_effect=LegalEffect.EVIDENCE_ONLY,
    )
    extraction = ExtractionCorrectionAssertion(
        manifestation_id="sha256:m0",
        old_source_unit_id="sha256:u0",
        new_source_unit_id="sha256:u1",
        reason="extraction_fix",
        legal_effect=LegalEffect.FROM_CORRECTION_DATE,
    )
    for ev in (corr, keeper, extraction):
        cid = ev.correction_event_id
        assert cid.startswith("sha256:")
        body = ev.to_canonical_dict()
        # recompute over body-minus-id under the shared correction domain
        stripped = {k: v for k, v in body.items() if k != "correction_event_id"}
        assert leaf_hash("source_correction", stripped) == cid
        assert body["legal_effect"] in {e.value for e in LegalEffect}


# ---------------------------------------------------------------------------
# §5 delta classifier hard rule
# ---------------------------------------------------------------------------


def test_unclassified_delta_may_not_map_to_none_scope() -> None:
    with pytest.raises(ValueError, match="unclassified"):
        SourceDeltaClassification(
            source_record_id="sha256:r",
            from_manifestation_id="sha256:m0",
            to_manifestation_id="sha256:m1",
            manifestation_delta="byte_changed",
            from_source_unit_id="sha256:u0",
            to_source_unit_id="sha256:u1",
            source_unit_delta=SourceUnitDelta.UNCLASSIFIED,
            recompute_scope=RecomputeScope.NONE,
        )


def test_unclassified_delta_allows_legal_state_scope() -> None:
    delta = SourceDeltaClassification(
        source_record_id="sha256:r",
        from_manifestation_id="sha256:m0",
        to_manifestation_id="sha256:m1",
        manifestation_delta="byte_changed",
        from_source_unit_id="sha256:u0",
        to_source_unit_id="sha256:u1",
        source_unit_delta=SourceUnitDelta.UNCLASSIFIED,
        recompute_scope=RecomputeScope.LEGAL_STATE,
    )
    assert delta.delta_id.startswith("sha256:")
    assert delta.to_canonical_dict()["recompute_scope"] == "legal_state"


def test_metadata_only_delta_is_account_only() -> None:
    delta = SourceDeltaClassification(
        source_record_id="sha256:r",
        from_manifestation_id="sha256:m0",
        to_manifestation_id="sha256:m1",
        manifestation_delta="byte_changed",
        from_source_unit_id="sha256:u0",
        to_source_unit_id="sha256:u0",
        source_unit_delta=SourceUnitDelta.METADATA_ONLY_NONSEMANTIC,
        recompute_scope=RecomputeScope.ACCOUNT_ONLY,
    )
    assert delta.to_canonical_dict()["source_unit_delta"] == "metadata_only_nonsemantic"


# ---------------------------------------------------------------------------
# §8.2 typed genesis
# ---------------------------------------------------------------------------


def test_original_enactment_genesis_needs_no_creation_event() -> None:
    ev = InitialStateEvent(
        work_id="xx:act:301/2004",
        genesis_kind=GenesisKind.ORIGINAL_ENACTMENT,
        effective_date="2004-05-01",
        prior_history_status=PriorHistoryStatus.NONE,
        source_refs=("sha256:s1",),
    )
    assert ev.to_canonical_dict()["creation_event_id"] is None
    assert ev.initial_state_event_id.startswith("sha256:")


def test_snapshot_genesis_requires_manifestation_creation_event() -> None:
    # §8.2 RESOLVED: snapshot genesis creation_event_id = manifestation_id.
    with pytest.raises(ValueError, match="snapshot genesis"):
        InitialStateEvent(
            work_id="xx:act:301/2004",
            genesis_kind=GenesisKind.OBSERVED_CODIFICATION_SNAPSHOT,
            effective_date="2004-05-01",
            prior_history_status=PriorHistoryStatus.UNAVAILABLE,
            source_refs=("sha256:s1",),
            creation_event_id=None,
        )


def test_snapshot_genesis_with_manifestation_id_is_valid() -> None:
    rec = _record()
    man = _manifestation(rec.source_record_id)
    ev = InitialStateEvent(
        work_id="xx:act:301/2004",
        genesis_kind=GenesisKind.OFFICIAL_CONSOLIDATION_CHECKPOINT,
        effective_date="2004-05-01",
        prior_history_status=PriorHistoryStatus.PARTIALLY_OBSERVED,
        source_refs=(man.manifestation_id,),
        creation_event_id=man.manifestation_id,
    )
    assert ev.to_canonical_dict()["creation_event_id"] == man.manifestation_id


# ---------------------------------------------------------------------------
# enum faithfulness
# ---------------------------------------------------------------------------


def test_availability_enum_round_trips_through_row() -> None:
    rec = _record()
    for av in Availability:
        man = _manifestation(rec.source_record_id, availability=av)
        assert man.to_canonical_dict()["availability"] == av.value


def test_genesis_kind_enum_coverage() -> None:
    rec = _record()
    man = _manifestation(rec.source_record_id)
    for kind in GenesisKind:
        snapshot = kind != GenesisKind.ORIGINAL_ENACTMENT
        ev = InitialStateEvent(
            work_id="xx:act:1/2000",
            genesis_kind=kind,
            effective_date="2000-01-01",
            prior_history_status=PriorHistoryStatus.NONE,
            source_refs=(),
            creation_event_id=man.manifestation_id if snapshot else None,
        )
        assert ev.to_canonical_dict()["genesis_kind"] == kind.value


def test_string_and_enum_inputs_produce_identical_ids() -> None:
    # Constructors accept either the closed enum or its wire string.
    rec = _record()
    man = _manifestation(rec.source_record_id, availability="available_in_lawvm_cas")
    man_enum = _manifestation(
        rec.source_record_id, availability=Availability.AVAILABLE_IN_LAWVM_CAS
    )
    assert man.manifestation_id == man_enum.manifestation_id
    assert man.to_canonical_dict() == man_enum.to_canonical_dict()


# ---------------------------------------------------------------------------
# golden vectors (lock the canonical bytes)
# ---------------------------------------------------------------------------


def test_golden_source_record_id() -> None:
    rec = SourceRecord(
        jurisdiction="xx",
        keeper="keeper_a",
        logical_kind="act_xml",
        logical_key="xx:keeper_a:act_xml:301/2004",
    )
    expected = leaf_hash(
        "source_record",
        {
            "schema": "lawvm.source_record.v1",
            "jurisdiction": "xx",
            "keeper": "keeper_a",
            "logical_kind": "act_xml",
            "logical_key": "xx:keeper_a:act_xml:301/2004",
        },
    )
    assert rec.source_record_id == expected


def test_golden_initial_state_event_id() -> None:
    ev = InitialStateEvent(
        work_id="xx:act:301/2004",
        genesis_kind=GenesisKind.ORIGINAL_ENACTMENT,
        effective_date="2004-05-01",
        prior_history_status=PriorHistoryStatus.NONE,
        source_refs=("sha256:s1",),
    )
    expected = leaf_hash(
        "initial_state_event",
        {
            "schema": "lawvm.initial_state_event.v1",
            "work_id": "xx:act:301/2004",
            "genesis_kind": "original_enactment",
            "effective_date": "2004-05-01",
            "prior_history_status": "none",
            "source_refs": ["sha256:s1"],
            "creation_event_id": None,
        },
    )
    assert ev.initial_state_event_id == expected
