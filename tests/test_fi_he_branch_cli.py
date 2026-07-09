"""``lawvm fi-he-branch`` — farchive HE PDF → conditional branches (the wiring entry point).

Hermetic tests exercise the pure helpers + the typed PDF-absent path (no farchive,
no PDF lib, no replay, no network). The live farchive → materialize path is proven
by running ``lawvm fi-he-branch <year> <number> --dest <farchive>`` against a real
gov-proposal farchive imported with ``acquire-fi-proposals --include-pdfs``.
"""
from __future__ import annotations

from lawvm.tools.fi_he_branch import (
    HeBranchResult,
    _render_text,
    _section_label,
    run_he_branch,
)


def test_section_label_extracts_from_provision_ref() -> None:
    assert _section_label("chapter:3/section:22/subsection:3") == "22"
    assert _section_label("section:137/subsection:1") == "137"
    assert _section_label("section:2b") == "2b"
    assert _section_label("no-section-here") == ""


def test_pdf_absent_is_a_typed_finding_not_a_crash() -> None:
    # No farchive at this path → manifestation load fails → typed finding, empty package.
    result = run_he_branch(1999, 404, farchive_path="/nonexistent/no.farchive")
    assert isinstance(result, HeBranchResult)
    assert result.proposal_id == "fi:he:1999/404"
    assert result.package.branches == ()
    assert result.package.replay_authorized is False
    assert any("not in" in f and "acquire-fi-proposals" in f for f in result.findings)
    # The run-level finding is not duplicated onto the package.
    assert result.package.findings == ()


def test_render_text_reports_the_acquire_hint() -> None:
    result = run_he_branch(1999, 404, farchive_path="/nonexistent/no.farchive")
    text = _render_text(result)
    assert "HE fi:he:1999/404" in text
    assert "branches: 0" in text
    assert "acquire-fi-proposals --include-pdfs" in text
