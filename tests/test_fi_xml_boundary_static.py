from __future__ import annotations

import ast
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (_REPO / path).read_text(encoding="utf-8")


def test_uncovered_recovery_runner_stays_source_model_primary() -> None:
    source = _source("src/lawvm/finland/uncovered_recovery_runner.py")

    assert "import lxml.etree as etree" not in source
    assert "fi_xml_to_ir_node" not in source
    assert "_xml_part_label" not in source
    assert "lookup_payload_ir_for_coverage_ref(" in source
    assert "lookup_payload_ir(" not in source
    assert "find_payload_ir(" not in source


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


def test_uncovered_recovery_context_is_preamble_text_primary() -> None:
    source = _source("src/lawvm/finland/uncovered_recovery_context.py")
    source_model = _source("src/lawvm/finland/source_model.py")

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "muutos_tree" not in source
    assert "preamble_text:" in source
    assert "preamble_text=self.preamble_text()" in source_model


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


def test_compile_group_surface_uses_typed_source_payload_lookup() -> None:
    source = _source("src/lawvm/finland/compile_group_surface.py")

    assert "lookup_payload_ir(" in source
    assert "find_payload_ir(" not in source
    assert ".payload_ir" in source
    assert ".cross_heading_ir" in source


def test_source_model_exposes_typed_payload_lookup_result() -> None:
    source = _source("src/lawvm/finland/source_model.py")

    assert "class SourcePayloadLookupResult" in source
    assert "class SourcePayloadTextLookupResult" in source
    assert "class SourceBodyInventoryIndex" in source
    assert "def lookup_payload_ir(" in source
    assert "def lookup_payload_ir_for_coverage_ref(" in source
    assert "def lookup_section_payload_text(" in source
    assert "_body_inventory_index_cache" in source
    assert "body_lookup_status:" in source
    assert "body_candidates:" in source
    assert "payload_basis:" in source
    assert "legacy_xml_fallback" not in source
    assert "def find_xml_node(" not in source
    assert "_node_cache" not in source
    assert "_find_muutos_node_uncached" not in source
    assert "_coverage_node_cache" not in source
    assert "_coverage_payload_nodes_by_unit_id" not in source
    assert "_coverage_payload_ir_cache" not in source
    assert "_observed_payload_ir_cache" not in source
    assert "class SourcePayloadIrIndex" in source
    assert "_source_payload_ir_index_cache" in source


def test_lowering_scope_recovery_source_model_path_uses_inventory_not_xml_nodes() -> None:
    source = _source("src/lawvm/finland/lowering_scope_recovery.py")

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "_find_muutos_node" not in source
    assert "muutos_tree" not in source
    assert "source_model.find_xml_node" not in source
    assert "source_model.body_section_scope(" in source
    assert "source_model.body_has_section(" in source


def test_source_model_has_source_node_uses_inventory_not_xml_nodes() -> None:
    source = _source("src/lawvm/finland/source_model.py")
    method_body = source.split("    def has_source_node(", 1)[1].split(
        "    def lookup_payload_ir(",
        1,
    )[0]

    assert "lookup_body_unit(" in method_body
    assert "has_single_unlabeled_section_payload()" in method_body
    assert "find_xml_node(" not in method_body
    assert "_find_muutos_node" not in method_body


def test_source_model_scope_retarget_adapters_use_inventory_not_xml_root() -> None:
    source = _source("src/lawvm/finland/source_model.py")
    method_body = source.split(
        "    def retarget_duplicate_body_section_scope_from_close_live_siblings(",
        1,
    )[1].split("    def resolve_group_surface_scope(", 1)[0]

    assert "self.muutos_tree" not in method_body
    assert "observed_body_inventory()" in method_body


def test_apply_executor_precreates_chapters_through_source_model() -> None:
    source = _source("src/lawvm/finland/apply_ops_executor.py")
    boundary_source = _source("src/lawvm/finland/apply_ops_boundary.py")
    source_model = _source("src/lawvm/finland/source_model.py")
    adapter_body = source_model.split("    def precreate_apply_chapters(", 1)[1].split(
        "    def preamble_text(",
        1,
    )[0]

    assert "_PrecreateApplyChaptersRequest" not in source
    assert "precreate_apply_chapters as _precreate_apply_chapters" not in source
    assert "AmendmentSourceModel.from_tree" not in source
    assert "request.muutos_tree" not in source
    assert "source_model.precreate_apply_chapters(" in source
    assert "self.muutos_tree" not in adapter_body
    assert "source_chapters=self.source_chapters()" in adapter_body
    assert "source_pseudo_chapters=self.source_pseudo_chapters()" in adapter_body
    assert "import lxml.etree as etree" not in boundary_source
    assert "etree._Element" not in boundary_source
    assert "muutos_tree" not in boundary_source
    assert "source_model: AmendmentSourceModel" in boundary_source


def test_precreate_apply_chapters_request_is_typed_source_fact_only() -> None:
    source = _source("src/lawvm/finland/amendment_chapter_precreate.py")
    request_body = source.split("class PrecreateApplyChaptersRequest:", 1)[1].split(
        "@dataclass(frozen=True, slots=True)\nclass PrecreateApplyChaptersResult:",
        1,
    )[0]
    apply_body = source.split("def precreate_apply_chapters(", 1)[1].split(
        "    chapterization_labels = _chapterization_required_labels(",
        1,
    )[0]

    assert "muutos_tree" not in request_body
    assert "etree._Element" not in request_body
    assert "request.muutos_tree" not in apply_body
    assert "source_chapters_from_tree(" not in apply_body
    assert "source_pseudo_chapters_from_tree(" not in apply_body
    assert "source_chapters: tuple[SourceChapter, ...]" in request_body
    assert "source_pseudo_chapters: tuple[SourcePseudoChapter, ...]" in request_body


def test_compile_amendment_metadata_reads_use_source_model() -> None:
    source = _source("src/lawvm/finland/compile_amendment.py")

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "muutos_tree" not in source
    assert "AmendmentSourceModel.from_tree" not in source
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

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "muutos_tree" not in source
    assert "from lawvm.finland.metadata import" not in source
    assert "_amendment_effective_date_with_step" not in source
    assert "_amendment_expiry_date" not in source
    assert "_statute_issue_date" not in source
    assert "source_model.effective_date_with_step()" in source
    assert "source_model.expiry_date()" in source
    assert "source_model.issue_date()" in source


def test_precompile_selection_eid_free_body_check_uses_source_model() -> None:
    source = _source("src/lawvm/finland/process_precompile_selection.py")

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "muutos_tree" not in source
    assert "\n    xml_bytes: bytes" not in source
    assert "self.xml_bytes" not in source
    assert 'findall(".//{*}section' not in source
    assert "source_model.has_eid_free_body_sections()" in source
    assert "source_model.extract_vts_repeals(" in source
    assert "source_model.enrich_ops_from_amendment_tree(" in source


def test_route_rejection_expiry_override_uses_source_model() -> None:
    source = _source("src/lawvm/finland/process_route_rejection.py")

    assert "import lxml.etree as etree" not in source
    assert "from lawvm.finland.metadata import _commencement_expiry_override" not in source
    assert "from lawvm.finland.frontend_compile import _enrich_ops_from_amendment_tree" not in source
    assert "from lawvm.finland.vts import VtsSkippedTarget\n" in source
    assert "from lawvm.finland.vts import VtsSkippedTarget, extract_vts_cross_statute_repeals" not in source
    assert "etree._Element" not in source
    assert "muutos_tree" not in source
    assert "xml_bytes: bytes" not in source
    assert "self.xml_bytes" not in source
    assert "_commencement_expiry_override(" not in source
    assert "source_model.commencement_expiry_override(" in source
    assert "source_model.extract_vts_cross_statute_repeals(" in source
    assert "source_model.enrich_amendment_ops(" in source


def test_process_pipeline_routes_vts_through_source_model() -> None:
    source = _source("src/lawvm/finland/process_pipeline.py")

    assert "from lawvm.finland.vts import extract_vts_cross_statute_repeals" not in source
    assert "source_model.extract_vts_cross_statute_repeals(" in source
    assert "xml_bytes = acquired.xml_bytes" not in source
    assert "xml_bytes=xml_bytes,\n                    source_model=source_model" not in source


def test_temporal_postprocessing_expiry_override_uses_source_model() -> None:
    source = _source("src/lawvm/finland/process_temporal_postprocessing.py")

    assert "import lxml.etree as etree" not in source
    assert "etree._Element" not in source
    assert "muutos_tree" not in source
    assert "xml_bytes" not in source
    assert "from lawvm.finland.metadata import _commencement_expiry_override" not in source
    assert "_commencement_expiry_override(" not in source
    # Leak-ledger rank 15 / AGENTS §1.11–§1.12: the commencement/expiry override
    # decision is owned solely by the typed surface; the former
    # source_text_contains("voimaantulos") substring prefilter (which could not
    # change the typed result) is removed so no raw-text predicate gates legal
    # state here.
    assert 'source_text_contains("voimaantulos")' not in source
    assert "source_model.commencement_expiry_override(" in source


def test_temporal_postprocessing_commencement_overrides_use_source_model() -> None:
    source = _source("src/lawvm/finland/process_temporal_postprocessing.py")

    assert "_section_commencement_effective_override" not in source
    assert "_section_subsection_commencement_effective_override" not in source
    assert "source_model.section_commencement_effective_override(" in source
    assert "source_model.section_subsection_commencement_effective_override(" in source


def test_temporal_postprocessing_body_repeal_candidate_uses_source_model() -> None:
    source = _source("src/lawvm/finland/process_temporal_postprocessing.py")

    assert "get_operative_body_repeal_candidate" not in source
    assert "source_model.operative_body_repeal_candidate()" in source


def test_frontend_normalization_runs_through_source_model() -> None:
    source = _source("src/lawvm/finland/process_frontend_normalization.py")
    pipeline_source = _source("src/lawvm/finland/process_pipeline.py")
    source_model = _source("src/lawvm/finland/source_model.py")

    assert "import lxml.etree as etree" not in source
    assert "muutos_node_lookup_cache_scope" not in source
    assert "muutos_tree" not in source
    assert "source_model.normalize_and_compile_ops(" in source
    assert "source_model=source_model" in pipeline_source
    assert "source_model=self" in source_model


def test_temporary_payload_expiry_lookup_prefers_source_model_text() -> None:
    source = _source("src/lawvm/finland/frontend_compile.py")
    tree = ast.parse(source)

    assert "lookup_section_payload_text(" in source
    assert "_body_text_for_temporary_op(\n                    op,\n                    muutos_tree=muutos_tree,\n                    source_model=source_model," in source
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_tag_temporary_ops"
        and {keyword.arg for keyword in node.keywords if keyword.arg is not None}
        >= {"amendment_id", "muutos_tree", "source_model"}
        for node in ast.walk(tree)
    )


def test_process_pipeline_metadata_reads_use_source_model() -> None:
    source = _source("src/lawvm/finland/process_pipeline.py")

    assert "_amendment_tree_metadata" not in source
    assert "source_model.amendment_tree_metadata(" in source


def test_process_pipeline_receives_source_model_from_acquisition() -> None:
    source = _source("src/lawvm/finland/process_pipeline.py")
    acquisition_source = _source("src/lawvm/finland/process_acquisition.py")

    assert "muutos_tree" not in source
    assert "AmendmentSourceModel.from_tree" not in source
    assert "source_model = acquired.source_model" in source
    assert "source_model: AmendmentSourceModel" in acquisition_source
    assert "AmendmentSourceModel.from_tree(" in acquisition_source
