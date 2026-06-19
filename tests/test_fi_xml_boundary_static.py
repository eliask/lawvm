from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (_REPO / path).read_text(encoding="utf-8")


def test_uncovered_recovery_runner_stays_source_model_primary() -> None:
    source = _source("src/lawvm/finland/uncovered_recovery_runner.py")

    assert "import lxml.etree as etree" not in source
    assert "fi_xml_to_ir_node" not in source
    assert "_xml_part_label" not in source
    assert "find_payload_ir(" in source


def test_uncovered_candidate_iteration_does_not_dispatch_xml_nodes() -> None:
    source = _source("src/lawvm/finland/uncovered_recovery_iteration.py")

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "UncoveredSectionCandidate" in source


def test_uncovered_recovery_support_is_xml_free() -> None:
    source = _source("src/lawvm/finland/uncovered_recovery_support.py")

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "_xml_part_label" not in source


def test_finland_body_coverage_payload_refs_are_typed_not_xml_handles() -> None:
    source = _source("src/lawvm/finland/body_coverage.py")

    assert "payload_ref=el" not in source
    assert "payload_ref=child" not in source
    assert "payload_ref=muutos_tree" not in source
    assert "BodyCoveragePayloadRef(" in source


def test_uncovered_recovery_prepare_uses_source_model_not_xml_root() -> None:
    source = _source("src/lawvm/finland/uncovered_recovery_prepare.py")

    assert "import lxml.etree as etree" not in source
    assert "source_model.muutos_tree" not in source
    assert "build_uncovered_recovery_context,\n" not in source
    assert "source_model.build_uncovered_recovery_context(" in source
    assert "has_uncovered_recovery_content_ops(" in source


def test_compile_group_scope_recovery_uses_source_model_not_xml_root() -> None:
    source = _source("src/lawvm/finland/compile_group_scope_recovery.py")

    assert "source_model.muutos_tree" not in source
    assert "request.source_model.muutos_tree" not in source
    assert "muutos_tree=" not in source
    assert "request.source_model.resolve_group_surface_scope(" in source


def test_compile_group_elaboration_constraint_filter_uses_source_model_not_xml_root() -> None:
    source = _source("src/lawvm/finland/compile_group_elaboration.py")

    assert "source_model.muutos_tree" not in source
    assert "muutos_tree=" not in source
    assert "source_model=source_model" in source


def test_payload_lookup_does_not_query_source_model_xml_nodes() -> None:
    source = _source("src/lawvm/finland/amendment_payload_lookup.py")

    assert "source_model.find_xml_node" not in source
    assert "source_model:" not in source


def test_lowering_scope_recovery_source_model_path_uses_inventory_not_xml_nodes() -> None:
    source = _source("src/lawvm/finland/lowering_scope_recovery.py")

    assert "source_model.find_xml_node" not in source
    assert "source_model.body_section_lookup(" in source


def test_apply_executor_precreates_chapters_through_source_model() -> None:
    source = _source("src/lawvm/finland/apply_ops_executor.py")

    assert "_PrecreateApplyChaptersRequest" not in source
    assert "precreate_apply_chapters as _precreate_apply_chapters" not in source
    assert "source_model.precreate_apply_chapters(" in source


def test_compile_amendment_metadata_reads_use_source_model() -> None:
    source = _source("src/lawvm/finland/compile_amendment.py")

    assert "from lawvm.finland.frontend_compile import _tree_title" not in source
    assert "from lawvm.finland.metadata import _amendment_effective_date" not in source
    assert "from lawvm.finland.metadata import _statute_issue_date" not in source
    assert "from lawvm.finland.scope import find_body_section_chapter" not in source
    assert "source_model.title()" in source
    assert "source_model.issue_date()" in source
    assert "source_model.effective_date()" in source
    assert "source_model.first_body_section_chapter" in source


def test_temporal_authority_date_reads_use_source_model() -> None:
    source = _source("src/lawvm/finland/process_temporal_authority.py")

    assert "from lawvm.finland.metadata import" not in source
    assert "_amendment_effective_date_with_step" not in source
    assert "_amendment_expiry_date" not in source
    assert "_statute_issue_date" not in source
    assert "source_model.effective_date_with_step()" in source
    assert "source_model.expiry_date()" in source
    assert "source_model.issue_date()" in source


def test_precompile_selection_eid_free_body_check_uses_source_model() -> None:
    source = _source("src/lawvm/finland/process_precompile_selection.py")

    assert 'findall(".//{*}section' not in source
    assert "source_model.has_eid_free_body_sections()" in source


def test_route_rejection_expiry_override_uses_source_model() -> None:
    source = _source("src/lawvm/finland/process_route_rejection.py")

    assert "from lawvm.finland.metadata import _commencement_expiry_override" not in source
    assert "_commencement_expiry_override(" not in source
    assert "source_model.commencement_expiry_override(" in source


def test_temporal_postprocessing_expiry_override_uses_source_model() -> None:
    source = _source("src/lawvm/finland/process_temporal_postprocessing.py")

    assert "from lawvm.finland.metadata import _commencement_expiry_override" not in source
    assert "_commencement_expiry_override(" not in source
    assert "source_model.commencement_expiry_override(" in source


def test_temporal_postprocessing_commencement_overrides_use_source_model() -> None:
    source = _source("src/lawvm/finland/process_temporal_postprocessing.py")

    assert "_section_commencement_effective_override" not in source
    assert "_section_subsection_commencement_effective_override" not in source
    assert "source_model.section_commencement_effective_override(" in source
    assert "source_model.section_subsection_commencement_effective_override(" in source
