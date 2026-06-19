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
