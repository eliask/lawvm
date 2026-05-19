from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.tools import evidence, evidence_render


def _simple_uk_ir(statute_id: str, *, e_id: str = "section-1") -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Section.", attrs={"eId": e_id}),),
        ),
    )


def test_uk_evidence_bundle_preserves_compile_rejection_lanes(monkeypatch, tmp_path: Path, capsys) -> None:
    archive_path = tmp_path / "uk.farchive"
    archive_path.write_bytes(b"fake")
    monkeypatch.setattr(evidence, "_DEFAULT_UK_FARCHIVE", archive_path)

    class FakeFarchive:
        def __init__(self, _path: Path) -> None:
            self.path = _path

        def __enter__(self) -> "FakeFarchive":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str) -> bytes:
            return b"<Legislation>" + (b"x" * 128) + b"</Legislation>"

    monkeypatch.setattr("farchive.Farchive", FakeFarchive)

    def fake_parse(_data: bytes, *, statute_id: str, **_kwargs: Any) -> IRStatute:
        return _simple_uk_ir(statute_id)

    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fake_parse)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        lambda _data: {"eid_map": {"body:section-1": "section-1"}, "text_map": {}},
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [],
    )

    class FakePipeline:
        def __init__(self, _repo_root: Path) -> None:
            pass

        def compile_ops_for_statute(
            self,
            _statute_id: str,
            *,
            authority_rejections_out: list[dict[str, Any]],
            lowering_rejections_out: list[dict[str, Any]],
            effect_feed_parse_rejections_out: list[dict[str, Any]],
            effect_diagnostics_out: list[dict[str, Any]],
            **_kwargs: Any,
        ) -> list[Any]:
            authority_rejections_out.append(
                {
                    "rule_id": "uk_authority_mode_rejected",
                    "rejected_reason_counts": {"metadata_backfill": 2},
                }
            )
            lowering_rejections_out.append(
                {
                    "rule_id": "uk_effect_lowering_no_ops_rejected",
                    "blocking": True,
                }
            )
            lowering_rejections_out.append(
                {
                    "rule_id": "uk_effect_lowering_note",
                    "strict_disposition": "record",
                }
            )
            effect_feed_parse_rejections_out.append(
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                }
            )
            effect_feed_parse_rejections_out.append(
                {
                    "rule_id": "uk_effect_feed_pages_absent_recorded",
                    "strict_disposition": "record",
                }
            )
            effect_diagnostics_out.append(
                {
                    "rule_id": "uk_effect_source_pathology_classified",
                    "source_pathology": "missing_extracted_source",
                    "strict_disposition": "record",
                }
            )
            effect_diagnostics_out.append(
                {
                    "rule_id": "uk_manual_compile_frontier_classified",
                    "manual_compile_status": "manual_compile_candidate",
                    "manual_compile_rule_id": "uk_manual_compile_heading_candidate",
                    "manual_compile_reason": "heading facet requires manual compile",
                    "blocking": False,
                }
            )
            effect_diagnostics_out.append(
                {
                    "rule_id": "uk_affecting_act_xml_missing_rejected",
                    "phase": "acquisition",
                    "blocking": True,
                }
            )
            effect_diagnostics_out.append(
                {
                    "rule_id": "uk_affecting_act_xml_cached_recorded",
                    "phase": "acquisition",
                    "blocking": False,
                    "strict_disposition": "record",
                }
            )
            return []

        def apply_ops(
            self,
            base_ir: IRStatute,
            _ops: list[Any],
            *,
            adjudications_out: list[Any],
            **_kwargs: Any,
        ) -> IRStatute:
            assert isinstance(adjudications_out, list)
            adjudications_out.extend(
                [
                    CompileAdjudication(
                        kind="uk_replay_target_not_found",
                        message="target missing",
                        source_statute="ukpga/2000/1",
                    ),
                    CompileAdjudication(
                        kind="uk_replay_text_match_missing",
                        message="text preimage missing",
                        source_statute="ukpga/2000/1",
                    ),
                ]
            )
            return base_ir

    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", FakePipeline)

    bundle = evidence.build_uk_evidence_bundle(
        "ukpga/2000/1",
        allow_oracle_alignment=False,
        allow_metadata_backfill=False,
        allow_metadata_only_effects=False,
        authority_mode="source_text_only",
    )

    observations = bundle["compiler_observations"]
    assert bundle["enacted_source_status"] == "available"
    assert bundle["oracle_source_status"] == "available"
    assert bundle["enacted_source_size"] > 100
    assert bundle["oracle_source_size"] > 100
    assert bundle["enacted_source_sha256"] == hashlib.sha256(
        b"<Legislation>" + (b"x" * 128) + b"</Legislation>"
    ).hexdigest()
    assert bundle["oracle_source_sha256"] == bundle["enacted_source_sha256"]
    assert bundle["uk_replay_regime"]["metadata_only_effects_enabled"] is False
    authority = observations["uk_source_authority_summary"]
    assert authority["authority_rejection_count"] == 1
    assert authority["authority_rejection_reason_counts"] == {"metadata_backfill": 2}
    replay_adjudications = observations["uk_replay_adjudication_summary"]
    assert replay_adjudications == {
        "replay_adjudication_count": 2,
        "replay_adjudication_kind_counts": {
            "uk_replay_target_not_found": 1,
            "uk_replay_text_match_missing": 1,
        },
        "replay_adjudication_bucket_counts": {
            "replay_bug": 1,
            "text_surface": 1,
        },
    }
    assert observations["uk_residual_claim_summary"] == {
        "selected_tier": "PROVED_REPLAY_BUG",
        "selected_kind": "uk_replay_target_not_found",
        "comparison_class": "commensurable",
        "core_comparison": True,
        "only_in_replayed_count": 0,
        "only_in_oracle_count": 0,
        "adjudication_kinds": [
            "uk_replay_target_not_found",
            "uk_replay_text_match_missing",
        ],
        "section_claim_count": 1,
        "section_claim_emitted": True,
    }

    compile_rejections = observations["uk_compile_rejection_summary"]
    assert compile_rejections["effect_feed_parse_rejection_count"] == 1
    assert compile_rejections["effect_feed_parse_rejection_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert compile_rejections["blocking_effect_feed_parse_rejection_count"] == 1
    assert compile_rejections["effect_source_pathology_rejection_count"] == 0
    assert compile_rejections["source_acquisition_rejection_count"] == 1

    metadata_only_bundle = evidence.build_uk_evidence_bundle(
        "ukpga/2000/1",
        allow_oracle_alignment=False,
        allow_metadata_backfill=False,
        allow_metadata_only_effects=True,
        authority_mode="source_text_only",
    )
    assert metadata_only_bundle["uk_replay_regime"]["source_semantics_clean"] is False
    assert metadata_only_bundle["uk_replay_regime"]["source_first_candidate"] is False
    assert metadata_only_bundle["uk_replay_regime"]["source_first_candidate_reasons"] == [
        "metadata_only_effects_enabled",
    ]
    assert compile_rejections["source_acquisition_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert compile_rejections["blocking_source_acquisition_rejection_count"] == 1
    assert compile_rejections["lowering_rejection_count"] == 1
    assert compile_rejections["lowering_rejection_rule_counts"] == {
        "uk_effect_lowering_no_ops_rejected": 1,
    }
    assert compile_rejections["blocking_lowering_rejection_count"] == 1
    compile_observations = observations["uk_compile_observation_summary"]
    assert compile_observations["effect_feed_parse_observation_count"] == 2
    assert compile_observations["effect_feed_parse_observation_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert compile_observations["effect_source_pathology_observation_count"] == 1
    assert compile_observations["effect_source_pathology_observation_rule_counts"] == {
        "uk_effect_source_pathology_classified": 1,
    }
    assert compile_observations["manual_compile_frontier_observation_count"] == 1
    assert compile_observations["manual_compile_status_counts"] == {
        "manual_compile_candidate": 1,
    }
    assert compile_observations["manual_compile_rule_counts"] == {
        "uk_manual_compile_heading_candidate": 1,
    }
    manual_observation = compile_observations["manual_compile_frontier_observations"][0]
    assert manual_observation["manual_compile_reason"] == "heading facet requires manual compile"
    assert compile_observations["source_acquisition_observation_count"] == 2
    assert compile_observations["source_acquisition_observation_rule_counts"] == {
        "uk_affecting_act_xml_cached_recorded": 1,
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert compile_observations["lowering_observation_count"] == 2
    assert compile_observations["lowering_observation_rule_counts"] == {
        "uk_effect_lowering_no_ops_rejected": 1,
        "uk_effect_lowering_note": 1,
    }
    evidence_render._print_evidence_bundle(bundle)
    text = capsys.readouterr().out
    assert "UK compiler obs:" in text
    assert (
        "authority: mode=source_text_only metadata_backfill_ops=0 "
        "rejections=1 reasons=[metadata_backfill=2]"
    ) in text
    assert (
        "compile observations: source_parse=0 feed_parse=2 "
        "effect_source_pathology=1 manual_compile_frontier=1 "
        "source_acquisition=2 lowering=2"
    ) in text
    assert (
        "feed observation rules: uk_effect_feed_pages_absent_recorded=1, "
        "uk_effect_feed_xml_parse_rejected=1"
    ) in text
    assert (
        "effect source pathology observation rules: "
        "uk_effect_source_pathology_classified=1"
    ) in text
    assert "manual compile frontier statuses: manual_compile_candidate=1" in text
    assert (
        "manual compile frontier rules: "
        "uk_manual_compile_heading_candidate=1"
    ) in text
    assert (
        "source acquisition observation rules: "
        "uk_affecting_act_xml_cached_recorded=1, "
        "uk_affecting_act_xml_missing_rejected=1"
    ) in text
    assert (
        "lowering observation rules: uk_effect_lowering_no_ops_rejected=1, "
        "uk_effect_lowering_note=1"
    ) in text
    assert (
        "compile rejections: source_parse=0 blocking_source_parse=0 "
        "feed_parse=1 blocking_feed_parse=1 "
        "effect_source_pathology=0 blocking_effect_source_pathology=0 "
        "source_acquisition=1 blocking_source_acquisition=1 "
        "lowering=1 blocking_lowering=1"
    ) in text
    assert "replay adjudications: total=2" in text
    assert "replay adjudication buckets: replay_bug=1, text_surface=1" in text
    assert (
        "replay adjudication kinds: uk_replay_target_not_found=1, "
        "uk_replay_text_match_missing=1"
    ) in text
    assert (
        "residual claim: tier=PROVED_REPLAY_BUG "
        "kind=uk_replay_target_not_found comparison=commensurable "
        "core=True only_in_replayed=0 only_in_oracle=0 section_claims=1"
    ) in text
    assert "feed rules: uk_effect_feed_xml_parse_rejected=1" in text
    assert "blocking feed rules: uk_effect_feed_xml_parse_rejected=1" in text
    assert "source acquisition rules: uk_affecting_act_xml_missing_rejected=1" in text
    assert (
        "blocking source acquisition rules: uk_affecting_act_xml_missing_rejected=1"
    ) in text
    assert "lowering rules: uk_effect_lowering_no_ops_rejected=1" in text
    assert "blocking lowering rules: uk_effect_lowering_no_ops_rejected=1" in text


def test_uk_evidence_bundle_records_initial_effect_count_failure(monkeypatch, tmp_path: Path) -> None:
    archive_path = tmp_path / "uk.farchive"
    archive_path.write_bytes(b"fake")
    monkeypatch.setattr(evidence, "_DEFAULT_UK_FARCHIVE", archive_path)

    class FakeFarchive:
        def __init__(self, _path: Path) -> None:
            self.path = _path

        def __enter__(self) -> "FakeFarchive":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str) -> bytes:
            return b"<Legislation>" + (b"x" * 128) + b"</Legislation>"

    monkeypatch.setattr("farchive.Farchive", FakeFarchive)

    def fake_parse(_data: bytes, *, statute_id: str, **_kwargs: Any) -> IRStatute:
        return _simple_uk_ir(statute_id)

    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fake_parse)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        lambda _data: {"eid_map": {"body:section-1": "section-1"}, "text_map": {}},
    )
    load_calls = {"count": 0}

    def fake_load_effects(*_args: object, **_kwargs: object) -> list[Any]:
        load_calls["count"] += 1
        if load_calls["count"] == 1:
            raise ValueError("bad feed count")
        return []

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        fake_load_effects,
    )

    class FakePipeline:
        def __init__(self, _repo_root: Path) -> None:
            pass

        def compile_ops_for_statute(
            self,
            _statute_id: str,
            **_kwargs: Any,
        ) -> list[Any]:
            return []

        def apply_ops(self, base_ir: IRStatute, _ops: list[Any], **_kwargs: Any) -> IRStatute:
            return base_ir

    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", FakePipeline)

    bundle = evidence.build_uk_evidence_bundle("ukpga/2000/1")

    assert bundle["uk_oracle_comparison"]["n_effects"] == 0
    observations = bundle["compiler_observations"]
    compile_rejections = observations["uk_compile_rejection_summary"]
    assert compile_rejections["effect_feed_parse_rejection_count"] == 1
    assert compile_rejections["effect_feed_parse_rejection_rule_counts"] == {
        "uk_effect_feed_count_error": 1,
    }
    count_error = compile_rejections["effect_feed_parse_rejections"][0]
    assert count_error["rule_id"] == "uk_effect_feed_count_error"
    assert count_error["exception_type"] == "ValueError"
    assert count_error["blocking"] is True
    compile_observations = observations["uk_compile_observation_summary"]
    assert compile_observations["effect_feed_parse_observation_count"] == 1
    assert compile_observations["effect_feed_parse_observation_rule_counts"] == {
        "uk_effect_feed_count_error": 1,
    }


@pytest.mark.parametrize(
    ("failing_side", "expected_error", "expected_rule"),
    [
        ("enacted", "NO_ENACTED", "uk_enacted_xml_parse_rejected"),
        ("oracle", "NO_ORACLE", "uk_oracle_xml_parse_rejected"),
    ],
)
def test_uk_evidence_bundle_records_available_source_parse_failures(
    monkeypatch,
    tmp_path: Path,
    failing_side: str,
    expected_error: str,
    expected_rule: str,
) -> None:
    archive_path = tmp_path / "uk.farchive"
    archive_path.write_bytes(b"fake")
    monkeypatch.setattr(evidence, "_DEFAULT_UK_FARCHIVE", archive_path)

    class FakeFarchive:
        def __init__(self, _path: Path) -> None:
            self.path = _path

        def __enter__(self) -> "FakeFarchive":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str) -> bytes:
            return b"<Legislation>" + (b"x" * 128) + b"</Legislation>"

    monkeypatch.setattr("farchive.Farchive", FakeFarchive)

    def fake_parse(_data: bytes, *, statute_id: str, version_label: str, **_kwargs: Any) -> IRStatute:
        if version_label == failing_side:
            raise ValueError(f"bad {failing_side} source")
        return _simple_uk_ir(statute_id)

    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fake_parse)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        lambda _data: {"eid_map": {"body:section-1": "section-1"}, "text_map": {}},
    )

    bundle = evidence.build_uk_evidence_bundle("ukpga/2000/1")

    assert bundle["error"] == expected_error
    assert bundle[f"{failing_side}_source_status"] == "available"
    assert bundle[f"{failing_side}_source_parse_failed"] is True
    observations = bundle["compiler_observations"]
    compile_rejections = observations["uk_compile_rejection_summary"]
    assert compile_rejections["source_parse_rejection_count"] == 1
    assert compile_rejections["source_parse_rejection_rule_counts"] == {expected_rule: 1}
    assert compile_rejections["source_parse_rejections"][0]["blocking"] is True
    compile_observations = observations["uk_compile_observation_summary"]
    assert compile_observations["source_parse_observation_count"] == 1
    assert compile_observations["source_parse_observation_rule_counts"] == {expected_rule: 1}


def test_uk_evidence_bundle_reports_too_small_source_without_parsing(monkeypatch, tmp_path: Path) -> None:
    archive_path = tmp_path / "uk.farchive"
    archive_path.write_bytes(b"fake")
    monkeypatch.setattr(evidence, "_DEFAULT_UK_FARCHIVE", archive_path)

    class FakeFarchive:
        def __init__(self, _path: Path) -> None:
            self.path = _path

        def __enter__(self) -> "FakeFarchive":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> bytes:
            if url.endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 128) + b"</Legislation>"
            return b"<short/>"

    monkeypatch.setattr("farchive.Farchive", FakeFarchive)

    def fail_parse(*_args: object, **_kwargs: object) -> IRStatute:
        raise AssertionError("too-small oracle source must not be parsed")

    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fail_parse)

    bundle = evidence.build_uk_evidence_bundle("ukpga/2000/1")

    assert bundle["error"] == "NO_ORACLE"
    assert bundle["enacted_source_status"] == "available"
    assert bundle["oracle_source_status"] == "too_small"
    assert bundle["oracle_source_size"] == len(b"<short/>")
    assert bundle["oracle_source_sha256"] == hashlib.sha256(b"<short/>").hexdigest()
    assert bundle["enacted_source_url"].endswith("/ukpga/2000/1/enacted/data.xml")
    assert bundle["oracle_source_url"].endswith("/ukpga/2000/1/data.xml")
    assert bundle["uk_replay_regime"]["semantic_replay_lane"] == "not_run_source_unavailable"
    assert bundle["uk_replay_regime"]["source_purity_lane"] == "not_run_source_unavailable"
    assert bundle["uk_replay_regime"]["source_semantics_clean"] is False
    assert bundle["uk_replay_regime"]["authority_mode"] == "current_mixed"
    assert bundle["uk_applicability_regime"]["selection_model"] == "effective_date_plus_feed_applied"
    assert bundle["compiler_observations"]["uk_source_availability_summary"]["oracle_source_status"] == "too_small"
    assert bundle["compiler_observations"]["uk_replay_adjudication_summary"] == {
        "replay_adjudication_count": 0,
        "replay_adjudication_kind_counts": {},
        "replay_adjudication_bucket_counts": {},
    }
    assert bundle["compiler_observations"]["uk_residual_claim_summary"] == {
        "selected_tier": "UNRESOLVED",
        "selected_kind": "not_run_source_unavailable",
        "comparison_class": "source_unavailable",
        "core_comparison": False,
        "only_in_replayed_count": 0,
        "only_in_oracle_count": 0,
        "adjudication_kinds": [],
        "section_claim_count": 0,
        "section_claim_emitted": False,
    }
    assert (
        bundle["compiler_observations"]["uk_compile_rejection_summary"]["blocking_lowering_rejection_count"]
        == 0
    )
    assert bundle["uk_oracle_comparison"] == {
        "comparison_class": "no_oracle_eids",
        "core_comparison": False,
        "n_enacted_eids": None,
        "n_oracle_eids": None,
        "n_replayed_eids": None,
        "n_effects": None,
        "only_in_replayed": [],
        "only_in_oracle": [],
    }


def test_uk_evidence_bundle_classifies_missing_enacted_source_without_parsing(
    monkeypatch, tmp_path: Path
) -> None:
    archive_path = tmp_path / "uk.farchive"
    archive_path.write_bytes(b"fake")
    monkeypatch.setattr(evidence, "_DEFAULT_UK_FARCHIVE", archive_path)

    class FakeFarchive:
        def __init__(self, _path: Path) -> None:
            self.path = _path

        def __enter__(self) -> "FakeFarchive":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> bytes | None:
            if url.endswith("/enacted/data.xml"):
                return None
            return b"<Legislation>" + (b"x" * 128) + b"</Legislation>"

    monkeypatch.setattr("farchive.Farchive", FakeFarchive)

    def fail_parse(*_args: object, **_kwargs: object) -> IRStatute:
        raise AssertionError("missing enacted source must not be parsed")

    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fail_parse)

    bundle = evidence.build_uk_evidence_bundle("ukpga/2000/1")

    assert bundle["error"] == "NO_ENACTED"
    assert bundle["enacted_source_status"] == "absent"
    assert bundle["enacted_source_sha256"] == ""
    assert bundle["oracle_source_status"] == "available"
    assert bundle["oracle_source_sha256"] == hashlib.sha256(
        b"<Legislation>" + (b"x" * 128) + b"</Legislation>"
    ).hexdigest()
    assert bundle["uk_replay_regime"]["semantic_replay_lane"] == "not_run_source_unavailable"
    assert bundle["uk_replay_regime"]["source_purity_lane"] == "not_run_source_unavailable"
    assert bundle["uk_replay_regime"]["source_semantics_clean"] is False
    assert bundle["uk_replay_regime"]["source_first_candidate_reasons"] == ["source_unavailable"]
    assert bundle["uk_applicability_regime"]["selection_model"] == "effective_date_plus_feed_applied"
    assert bundle["compiler_observations"]["uk_source_availability_summary"]["enacted_source_status"] == "absent"
    assert bundle["compiler_observations"]["uk_residual_claim_summary"]["selected_kind"] == (
        "not_run_source_unavailable"
    )
    assert (
        bundle["compiler_observations"]["uk_compile_observation_summary"][
            "effect_feed_parse_observation_count"
        ]
        == 0
    )
    assert bundle["uk_oracle_comparison"] == {
        "comparison_class": "no_enacted_eids",
        "core_comparison": False,
        "n_enacted_eids": None,
        "n_oracle_eids": None,
        "n_replayed_eids": None,
        "n_effects": None,
        "only_in_replayed": [],
        "only_in_oracle": [],
    }


def test_uk_evidence_main_error_prints_source_context(monkeypatch, capsys) -> None:
    def fake_build_uk_evidence_bundle(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "statute_id": "ukpga/2000/1",
            "mode": "legal_pit",
            "jurisdiction": "uk",
            "error": "NO_ORACLE",
            "enacted_source_status": "available",
            "oracle_source_status": "too_small",
            "enacted_source_size": 123,
            "oracle_source_size": 7,
            "enacted_source_sha256": "enacted-sha",
            "oracle_source_sha256": "oracle-sha",
            "enacted_source_url": "https://example.test/ukpga/2000/1/enacted/data.xml",
            "oracle_source_url": "https://example.test/ukpga/2000/1/data.xml",
            "uk_oracle_comparison": {
                "comparison_class": "no_oracle_eids",
                "core_comparison": False,
            },
        }

    monkeypatch.setattr(evidence, "build_uk_evidence_bundle", fake_build_uk_evidence_bundle)

    with pytest.raises(SystemExit) as excinfo:
        evidence.main(
            Namespace(
                command="evidence",
                jurisdiction="uk",
                statute_id=["ukpga/2000/1"],
                mode="legal_pit",
                json=False,
                json_output=False,
                markdown=False,
                output="",
                with_bisect=False,
                uk_allow_metadata_backfill=None,
                uk_allow_oracle_alignment=None,
                uk_respect_feed_applied=None,
                uk_applicability_mode=None,
                uk_source_first_candidate=False,
                uk_authority_mode=None,
            )
        )

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR: NO_ORACLE" in err
    assert (
        "UK source: enacted=available(123b)@https://example.test/ukpga/2000/1/enacted/data.xml "
        "oracle=too_small(7b)@https://example.test/ukpga/2000/1/data.xml "
        "hashes=enacted:enacted-sha oracle:oracle-sha"
    ) in err
    assert "UK compare: class=no_oracle_eids core=no" in err


def test_uk_evidence_main_json_error_emits_typed_bundle(monkeypatch, capsys) -> None:
    def fake_build_uk_evidence_bundle(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "statute_id": "ukpga/2000/1",
            "mode": "legal_pit",
            "jurisdiction": "uk",
            "error": "NO_ORACLE",
            "enacted_source_status": "available",
            "oracle_source_status": "too_small",
            "enacted_source_size": 123,
            "oracle_source_size": 7,
            "enacted_source_url": "https://example.test/ukpga/2000/1/enacted/data.xml",
            "oracle_source_url": "https://example.test/ukpga/2000/1/data.xml",
            "uk_oracle_comparison": {
                "comparison_class": "no_oracle_eids",
                "core_comparison": False,
            },
        }

    monkeypatch.setattr(evidence, "build_uk_evidence_bundle", fake_build_uk_evidence_bundle)

    with pytest.raises(SystemExit) as excinfo:
        evidence.main(
            Namespace(
                command="evidence",
                jurisdiction="uk",
                statute_id=["ukpga/2000/1"],
                mode="legal_pit",
                json=True,
                json_output=False,
                markdown=False,
                output="",
                with_bisect=False,
                uk_allow_metadata_backfill=None,
                uk_allow_oracle_alignment=None,
                uk_respect_feed_applied=None,
                uk_applicability_mode=None,
                uk_source_first_candidate=False,
                uk_authority_mode=None,
            )
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert excinfo.value.code == 1
    assert payload["error"] == "NO_ORACLE"
    assert payload["enacted_source_status"] == "available"
    assert payload["oracle_source_status"] == "too_small"
    assert payload["uk_oracle_comparison"]["comparison_class"] == "no_oracle_eids"
    assert "ERROR: NO_ORACLE" in captured.err


def test_uk_evidence_main_uses_shared_source_first_regime(monkeypatch, capsys) -> None:
    seen: dict[str, Any] = {}

    def fake_build_uk_evidence_bundle(
        statute_id: str,
        *,
        mode: str,
        include_bisect: bool,
        allow_metadata_backfill: bool,
        allow_oracle_alignment: bool,
        applicability_mode: str,
        authority_mode: str,
        allow_metadata_only_effects: bool,
    ) -> dict[str, Any]:
        seen["kwargs"] = {
            "statute_id": statute_id,
            "mode": mode,
            "include_bisect": include_bisect,
            "allow_metadata_backfill": allow_metadata_backfill,
            "allow_oracle_alignment": allow_oracle_alignment,
            "applicability_mode": applicability_mode,
            "authority_mode": authority_mode,
            "allow_metadata_only_effects": allow_metadata_only_effects,
        }
        return {
            "statute_id": statute_id,
            "mode": mode,
            "jurisdiction": "uk",
            "uk_replay_regime": {
                "metadata_backfill_enabled": allow_metadata_backfill,
                "oracle_alignment_enabled": allow_oracle_alignment,
                "applicability_mode": applicability_mode,
                "authority_mode": authority_mode,
                "metadata_only_effects_enabled": allow_metadata_only_effects,
            },
        }

    monkeypatch.setattr(evidence, "build_uk_evidence_bundle", fake_build_uk_evidence_bundle)

    evidence.main(
        Namespace(
            command="evidence",
            jurisdiction="uk",
            statute_id=["ukpga/2000/1"],
            mode="legal_pit",
            json=True,
            json_output=False,
            markdown=False,
            output="",
            with_bisect=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=True,
            uk_authority_mode=None,
            uk_allow_metadata_only_effects=None,
        )
    )

    assert seen["kwargs"] == {
        "statute_id": "ukpga/2000/1",
        "mode": "legal_pit",
        "include_bisect": False,
        "allow_metadata_backfill": False,
        "allow_oracle_alignment": False,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "source_text_only",
        "allow_metadata_only_effects": False,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["uk_replay_regime"] == {
        "metadata_backfill_enabled": False,
        "oracle_alignment_enabled": False,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "source_text_only",
        "metadata_only_effects_enabled": False,
    }


def test_evidence_main_rejects_uk_regime_flags_for_non_uk(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        evidence.main(
            Namespace(
                command="evidence",
                jurisdiction="fi",
                statute_id=["2000/1"],
                mode="legal_pit",
                json=True,
                json_output=False,
                markdown=False,
                output="",
                with_bisect=False,
                uk_allow_metadata_backfill=None,
                uk_allow_oracle_alignment=None,
                uk_respect_feed_applied=None,
                uk_applicability_mode=None,
                uk_source_first_candidate=False,
                uk_authority_mode=None,
                uk_allow_metadata_only_effects=False,
            )
        )

    assert excinfo.value.code == 2
    assert "UK replay regime flags are only supported with -j uk" in capsys.readouterr().err


def test_uk_evidence_main_threads_metadata_only_effect_regime(monkeypatch, capsys) -> None:
    seen: dict[str, Any] = {}

    def fake_build_uk_evidence_bundle(*_args: object, **kwargs: object) -> dict[str, Any]:
        seen["kwargs"] = dict(kwargs)
        return {
            "statute_id": "ukpga/2000/1",
            "mode": "legal_pit",
            "jurisdiction": "uk",
            "uk_replay_regime": {
                "metadata_only_effects_enabled": kwargs["allow_metadata_only_effects"],
            },
        }

    monkeypatch.setattr(evidence, "build_uk_evidence_bundle", fake_build_uk_evidence_bundle)

    evidence.main(
        Namespace(
            command="evidence",
            jurisdiction="uk",
            statute_id=["ukpga/2000/1"],
            mode="legal_pit",
            json=True,
            json_output=False,
            markdown=False,
            output="",
            with_bisect=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=False,
            uk_authority_mode=None,
            uk_allow_metadata_only_effects=False,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert seen["kwargs"]["allow_metadata_only_effects"] is False
    assert payload["uk_replay_regime"]["metadata_only_effects_enabled"] is False


def test_global_jurisdiction_survives_evidence_review_subparser() -> None:
    from lawvm.tools import cli

    parser = cli._build_parser()

    global_args = parser.parse_args(
        [
            "-j",
            "uk",
            "evidence-review",
            "asc/2020/1",
            "--json",
            "--source-first-candidate",
        ]
    )
    local_args = parser.parse_args(
        [
            "evidence-review",
            "-j",
            "uk",
            "asc/2020/1",
            "--json",
            "--source-first-candidate",
        ]
    )

    assert global_args.jurisdiction == "uk"
    assert local_args.jurisdiction == "uk"


def test_evidence_review_text_summary_prints_uk_regime(capsys) -> None:
    from lawvm.tools.evidence_render import _print_review_summary

    _print_review_summary(
        {
            "statute_count": 1,
            "bundle_count": 1,
            "selected_count": 0,
            "uk_metadata_backfill_enabled": False,
            "uk_oracle_alignment_enabled": False,
            "uk_applicability_mode": "effective_date_plus_feed_applied",
            "uk_authority_mode": "source_text_only",
            "by_evidence_review_lane": {"artifact_bundle": 1},
            "by_evidence_review_materialization_lane": {"artifact_bundle": 1},
            "by_enacted_source_status": {"available": 1},
            "by_oracle_source_status": {"too_small": 1},
            "by_uk_comparison_class": {"no_oracle_eids": 1},
            "by_uk_core_comparison": {"non_core": 1},
            "by_uk_replay_adjudication_bucket": {"replay_bug": 1, "text_surface": 1},
            "by_uk_replay_adjudication_kind": {
                "uk_replay_target_not_found": 1,
                "uk_replay_text_match_missing": 1,
            },
            "by_primary_tier": {},
            "by_claim_kind": {},
            "by_section_claim_kind": {},
            "rows": [
                {
                    "statute_id": "ukpga/2000/1",
                    "evidence_review_lane": "artifact_bundle",
                    "evidence_review_materialization_lane": "artifact_bundle",
                    "display_primary_tier": "UNRESOLVED",
                    "enacted_source_status": "available",
                    "oracle_source_status": "too_small",
                    "enacted_source_size": 123,
                    "oracle_source_size": 7,
                    "enacted_source_sha256": "enacted-sha",
                    "oracle_source_sha256": "oracle-sha",
                    "enacted_source_url": "https://example.test/ukpga/2000/1/enacted/data.xml",
                    "oracle_source_url": "https://example.test/ukpga/2000/1/data.xml",
                    "uk_comparison_class": "no_oracle_eids",
                    "uk_core_comparison": False,
                    "uk_replay_adjudication_count": 2,
                    "uk_replay_adjudication_bucket_counts": {
                        "replay_bug": 1,
                        "text_surface": 1,
                    },
                    "uk_replay_adjudication_kind_counts": {
                        "uk_replay_target_not_found": 1,
                        "uk_replay_text_match_missing": 1,
                    },
                }
            ],
        }
    )

    out = capsys.readouterr().out
    assert "UK Regime     : metadata_backfill=False oracle_alignment=False" in out
    assert "applicability=effective_date_plus_feed_applied authority=source_text_only" in out
    assert "Review Lanes  : input=artifact_bundle=1 materialization=artifact_bundle=1" in out
    assert "UK Source     : enacted=available=1 oracle=too_small=1" in out
    assert "UK Compare    : class=no_oracle_eids=1 core=non_core=1" in out
    assert (
        "UK Replay Adj : buckets=replay_bug=1, text_surface=1 "
        "kinds=uk_replay_target_not_found=1, uk_replay_text_match_missing=1"
    ) in out
    assert (
        "uk_source=[enacted=available(123b)@https://example.test/ukpga/2000/1/enacted/data.xml"
        "#enacted-sha oracle=too_small(7b)@https://example.test/ukpga/2000/1/data.xml#oracle-sha]"
    ) in out
    assert "uk_compare=no_oracle_eids uk_core=no" in out
    assert "uk_replay_adj=2 uk_replay_adj_buckets=[replay_bug=1, text_surface=1]" in out
    assert (
        "uk_replay_adj_kinds=[uk_replay_target_not_found=1, "
        "uk_replay_text_match_missing=1]"
    ) in out
    assert "review_lane=artifact_bundle review_materialization=artifact_bundle" in out


def test_evidence_review_text_summary_omits_empty_uk_row_surface(capsys) -> None:
    from lawvm.tools.evidence_render import _print_review_summary

    _print_review_summary(
        {
            "statute_count": 1,
            "bundle_count": 1,
            "selected_count": 0,
            "by_primary_tier": {},
            "by_claim_kind": {},
            "by_section_claim_kind": {},
            "rows": [
                {
                    "statute_id": "2000/1",
                    "display_primary_tier": "UNRESOLVED",
                }
            ],
        }
    )

    out = capsys.readouterr().out
    assert "uk_source=" not in out
    assert "uk_compare=" not in out


def test_uk_evidence_review_counts_uk_source_and_comparison_surfaces() -> None:
    review = evidence._review_bundles(
        [
            {
                "statute_id": "ukpga/2000/1",
                "title": "OK",
                "primary_proof_tier": "UNRESOLVED",
                "proof_claims": [{"tier": "UNRESOLVED", "kind": "no_strong_claim"}],
                "enacted_source_status": "available",
                "oracle_source_status": "too_small",
                "enacted_source_size": 123,
                "oracle_source_size": 7,
                "enacted_source_sha256": "enacted-sha",
                "oracle_source_sha256": "oracle-sha",
                "enacted_source_url": "https://example.test/ukpga/2000/1/enacted/data.xml",
                "oracle_source_url": "https://example.test/ukpga/2000/1/data.xml",
                "uk_oracle_comparison": {
                    "comparison_class": "commensurable",
                    "core_comparison": True,
                },
                "compiler_observations": {
                    "uk_replay_adjudication_summary": {
                        "replay_adjudication_count": 2,
                        "replay_adjudication_bucket_counts": {
                            "replay_bug": 1,
                            "text_surface": 1,
                        },
                        "replay_adjudication_kind_counts": {
                            "uk_replay_target_not_found": 1,
                            "uk_replay_text_match_missing": 1,
                        },
                    },
                },
            },
            {
                "statute_id": "ukpga/2000/2",
                "mode": "legal_pit",
                "jurisdiction": "uk",
                "error": "NO_ORACLE",
                "enacted_source_status": "available",
                "oracle_source_status": "absent",
                "enacted_source_size": 130,
                "oracle_source_size": 0,
                "uk_oracle_comparison": {
                    "comparison_class": "no_oracle_eids",
                    "core_comparison": False,
                },
                "compiler_observations": {
                    "uk_replay_adjudication_summary": {
                        "replay_adjudication_count": 0,
                        "replay_adjudication_bucket_counts": {},
                        "replay_adjudication_kind_counts": {},
                    },
                },
            },
        ],
        limit=10,
    )

    assert review["by_enacted_source_status"] == {"available": 2}
    assert review["by_oracle_source_status"] == {"absent": 1, "too_small": 1}
    assert review["by_uk_comparison_class"] == {"commensurable": 1, "no_oracle_eids": 1}
    assert review["by_uk_core_comparison"] == {"core": 1, "non_core": 1}
    assert review["by_uk_replay_adjudication_bucket"] == {"replay_bug": 1, "text_surface": 1}
    assert review["by_uk_replay_adjudication_kind"] == {
        "uk_replay_target_not_found": 1,
        "uk_replay_text_match_missing": 1,
    }
    assert review["rows"][0]["enacted_source_status"] == "available"
    assert review["rows"][0]["oracle_source_status"] == "too_small"
    assert review["rows"][0]["oracle_source_size"] == 7
    assert review["rows"][0]["enacted_source_sha256"] == "enacted-sha"
    assert review["rows"][0]["oracle_source_sha256"] == "oracle-sha"
    assert review["rows"][0]["enacted_source_url"] == "https://example.test/ukpga/2000/1/enacted/data.xml"
    assert review["rows"][0]["oracle_source_url"] == "https://example.test/ukpga/2000/1/data.xml"
    assert review["rows"][0]["uk_comparison_class"] == "commensurable"
    assert review["rows"][0]["uk_core_comparison"] is True
    assert review["rows"][0]["uk_replay_adjudication_count"] == 2
    assert review["rows"][0]["uk_replay_adjudication_bucket_counts"] == {
        "replay_bug": 1,
        "text_surface": 1,
    }
    assert review["rows"][0]["uk_replay_adjudication_kind_counts"] == {
        "uk_replay_target_not_found": 1,
        "uk_replay_text_match_missing": 1,
    }


def test_uk_evidence_review_merge_preserves_uk_comparison_counts() -> None:
    acc = {
        "bundle_count": 0,
        "error_count": 0,
        "processable_count": 0,
        "classified_count": 0,
        "strict_clean_count": 0,
        "actionable_unresolved_count": 0,
        "mixed_replay_risk_count": 0,
        "ready_oracle_artifact_count": 0,
        "evidence_context_degraded_count": 0,
        "chain_complete_count": 0,
        "by_display_primary_tier": {},
        "by_sparse_blocker_source": {},
        "by_sparse_blocker_section": {},
        "by_evidence_review_lane": {},
        "by_evidence_review_materialization_lane": {},
        "by_uk_comparison_class": {},
        "by_uk_core_comparison": {},
        "by_uk_replay_adjudication_bucket": {},
        "by_uk_replay_adjudication_kind": {},
        "error_rows": [],
        "rows": [],
        "selected_count": 0,
    }
    chunk = {
        "bundle_count": 2,
        "by_display_primary_tier": {"SOURCE_PATHOLOGY": 1},
        "by_sparse_blocker_source": {"ukpga/2000/1": 1},
        "by_sparse_blocker_section": {"section-1": 1},
        "by_evidence_review_lane": {"live_oracle_corpus": 1},
        "by_evidence_review_materialization_lane": {"live_bundle_cache_hit": 1},
        "by_uk_comparison_class": {"commensurable": 1, "no_oracle_eids": 1},
        "by_uk_core_comparison": {"core": 1, "non_core": 1},
        "by_uk_replay_adjudication_bucket": {"replay_bug": 1},
        "by_uk_replay_adjudication_kind": {"uk_replay_target_not_found": 1},
        "error_rows": [],
        "rows": [],
        "selected_count": 0,
    }

    evidence._merge_review_summary(acc, chunk)

    assert acc["by_display_primary_tier"] == {"SOURCE_PATHOLOGY": 1}
    assert acc["by_sparse_blocker_source"] == {"ukpga/2000/1": 1}
    assert acc["by_sparse_blocker_section"] == {"section-1": 1}
    assert acc["by_evidence_review_lane"] == {"live_oracle_corpus": 1}
    assert acc["by_evidence_review_materialization_lane"] == {"live_bundle_cache_hit": 1}
    assert acc["by_uk_comparison_class"] == {"commensurable": 1, "no_oracle_eids": 1}
    assert acc["by_uk_core_comparison"] == {"core": 1, "non_core": 1}
    assert acc["by_uk_replay_adjudication_bucket"] == {"replay_bug": 1}
    assert acc["by_uk_replay_adjudication_kind"] == {"uk_replay_target_not_found": 1}


def test_evidence_review_oracle_artifact_gap_filter_is_not_shadowed() -> None:
    review = evidence._review_bundles(
        [
            {
                "statute_id": "ukpga/2000/1",
                "primary_proof_tier": "UNRESOLVED",
                "proof_claims": [{"tier": "UNRESOLVED", "kind": "no_strong_claim"}],
                "artifact_summary": {
                    "verification_gaps": {"needs_manual_check": 1},
                },
            }
        ],
        oracle_artifact_gap="needs_manual_check",
        limit=10,
    )

    assert review["by_oracle_artifact_gap"] == {"needs_manual_check": 1}
    assert review["selected_count"] == 1
    assert review["rows"][0]["statute_id"] == "ukpga/2000/1"


def test_evidence_review_count_outputs_are_merge_registered() -> None:
    review = evidence._review_bundles([], limit=0)
    count_fields = {
        key
        for key, value in review.items()
        if key.startswith("by_") and isinstance(value, dict)
    }

    assert count_fields <= set(evidence._REVIEW_COUNT_FIELDS)
