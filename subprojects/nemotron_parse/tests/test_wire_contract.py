"""Frozen wire-contract test — HERMETIC: runs without torch/transformers.

Pins the service's emission half of the process boundary against the shared
golden (``tests/data/wire_contract_golden.txt``). The main repo pins the
PARSING half of the same golden in ``tests/test_fi_nemotron_client.py``
(via ``vision_producer._parse_blocks``), so a drift on either side of the
boundary fails a committed test somewhere.

Run: uv run --project subprojects/nemotron_parse pytest subprojects/nemotron_parse/tests -p no:cacheprovider
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nemotron_parse import wire  # noqa: E402

_GOLDEN = Path(__file__).parent / "data" / "wire_contract_golden.txt"

#: The (nemotron_class, text) regions whose emission IS the golden file.
#: Includes dropped classes (page furniture) and an empty region to pin the
#: drop semantics, not just the happy path.
_GOLDEN_REGIONS: tuple[tuple[str, str], ...] = (
    ("Page-header", "HE 45/2026 vp"),  # dropped: unmapped class
    ("Title", "4 §"),
    (
        "Text",
        "Sen lisäksi, mitä 1 momentissa säädetään, hakijalle palautetaan\n"
        "valmisteveroa 4 senttiä litralta.",
    ),
    ("List-item", "1) ensimmäinen kohta"),
    ("Table", "Vero | Määrä"),
    ("Text", "   "),  # dropped: whitespace-only
    ("Footnote", "1) Sovelletaan verovuodesta 2025."),
    ("Page-footer", "12"),  # dropped: unmapped class
)


def test_emission_matches_frozen_golden() -> None:
    assert wire.emit_kind_blocks(_GOLDEN_REGIONS) == _GOLDEN.read_text(encoding="utf-8")


def test_governed_vocabulary_is_frozen() -> None:
    # The wire vocabulary mirrors vision_producer._VISION_KINDS on the main
    # side. Changing either alone breaks the process boundary — this freeze
    # forces a conscious two-sided change.
    assert wire.GOVERNED_WIRE_KINDS == ("HEADING", "PARA", "ITEM", "TABLE", "FOOTNOTE")
    assert set(wire.NEMOTRON_CLASS_TO_WIRE.values()) <= set(wire.GOVERNED_WIRE_KINDS)


def test_unmapped_class_is_dropped_never_relabeled() -> None:
    assert wire.emit_kind_blocks((("Picture", "a chart"), ("Formula", "E=mc^2"))) == ""


def test_empty_regions_emit_empty_string() -> None:
    assert wire.emit_kind_blocks(()) == ""


def test_wire_clean_guard_rejects_ungoverned_lead() -> None:
    with pytest.raises(ValueError):
        wire.assert_wire_clean("BOGUS: not a governed head\nPARA: fine")
    wire.assert_wire_clean(_GOLDEN.read_text(encoding="utf-8"))  # golden passes


def test_wire_clean_allows_continuation_line_that_looks_like_a_head() -> None:
    # A wrapped continuation line INSIDE a governed block may contain a colon
    # (legal text like ``4 §:ään``, or even a word that resembles a head). The
    # guard only forbids an ungoverned head BEFORE any block opens; once a block
    # is open, following lines are that block's content. This is the documented
    # accepted hazard of the frozen compact format (wire.emit_kind_blocks note).
    wire.assert_wire_clean("PARA: Sen 4 §:ään\nHEADING is not re-framed here\n")


def test_wire_clean_rejects_ungoverned_head_with_no_preceding_block() -> None:
    # Blank leading lines are ignored, but the FIRST non-empty content line must
    # be a governed head — otherwise stdout is not the wire format.
    with pytest.raises(ValueError):
        wire.assert_wire_clean("\n\nnot a block at all\nPARA: too late")


def test_labels_used_reports_governed_heads() -> None:
    labels = wire.wire_labels_used(_GOLDEN.read_text(encoding="utf-8"))
    assert labels == ("HEADING", "PARA", "ITEM", "TABLE", "FOOTNOTE")


def test_labels_used_dedups_and_ignores_continuation_and_ungoverned() -> None:
    # Two PARA blocks report PARA once; a wrapped continuation line and an
    # ungoverned ``Note:`` line never count as heads.
    blocks = "PARA: one\nwrapped continuation\nNote: not governed\nPARA: two\n"
    assert wire.wire_labels_used(blocks) == ("PARA",)
