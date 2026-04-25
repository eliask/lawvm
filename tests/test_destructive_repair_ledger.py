from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lawvm.tools.destructive_repair_ledger import build_ledger, main, render_markdown


def test_build_ledger_contains_seeded_tranche0_families() -> None:
    ledger = build_ledger()
    families = {entry.family: entry for entry in ledger}

    assert "constraint_filter_rejected_op" in families
    assert "apply_mutation_boundary" in families
    assert "body_coverage_ignored_or_rejected" in families
    assert (
        families["constraint_filter_rejected_op"].finding_emitted
        == "ELAB.REJECTED_OPERATION / ELAB.STRICT_REJECTED_OPERATION"
    )
    assert "partial_whole_section_replace_skip" in families
    assert families["partial_whole_section_replace_skip"].status == "safe"
    assert families["partial_whole_section_replace_skip"].file == "src/lawvm/finland/merge.py"
    assert (
        families["partial_whole_section_replace_skip"].finding_emitted
        == "ELAB.REJECTED_OPERATION / ELAB.STRICT_REJECTED_OPERATION"
    )
    assert families["base_editorial_strip"].status == "safe"
    assert families["base_editorial_strip"].finding_emitted == "BASE_EDITORIAL_STRIP"
    assert families["base_numbering_repair"].status == "safe"
    assert families["base_numbering_repair"].finding_emitted == "BASE_NUMBERING_REPAIR"
    assert families["base_duplicate_sibling_drop"].status == "safe"
    assert (
        families["base_duplicate_sibling_drop"].finding_emitted
        == "BASE_DUPLICATE_SIBLING_DROP"
    )
    assert families["base_digit_reset_split"].status == "safe"
    assert families["base_digit_reset_split"].finding_emitted == "BASE_DIGIT_RESET_SPLIT"
    assert families["base_duplicate_tail_split"].status == "safe"
    assert families["base_duplicate_tail_split"].finding_emitted == "BASE_DUPLICATE_TAIL_SPLIT"


def test_render_markdown_includes_seeded_rows() -> None:
    text = render_markdown(build_ledger())

    assert text.startswith("# Destructive Repair Ledger")
    assert "| family | function | file |" in text
    assert "base_editorial_strip" in text
    assert "src/lawvm/finland/source_normalize.py" in text


def test_main_json_emits_serializable_rows(capsys: pytest.CaptureFixture[str]) -> None:
    main(SimpleNamespace(json=True))
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert isinstance(payload, list)
    assert any(row["family"] == "base_numbering_repair" for row in payload)
    assert any(row["family"] == "base_duplicate_sibling_drop" for row in payload)
    assert any(row["family"] == "base_digit_reset_split" for row in payload)
    assert any(row["family"] == "base_duplicate_tail_split" for row in payload)
