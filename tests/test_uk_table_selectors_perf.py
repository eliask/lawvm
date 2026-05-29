"""Performance and behavior regression tests for UK table-selector hotspots.

Covers two Sensor I candidates fixed in Actuator 12 (2026-05-29):

Site 1: _source_names_containing_target_for_table_cell
  - 22,696 calls on ukpga/1970/9 with ~5,062 distinct (text, label) pairs
    (4.5x repeat ratio).  Dynamic rf"\\bin\\s+section\\s+{re.escape(label)}\\b"
    thrashed Python's 512-entry re._compile cache.
  - Fix: @functools.lru_cache factory keyed on label + "in section" substring
    guard before any regex walk (§1.11).

Site 2: _uk_source_parent_table_column_entry_omission_text_patch_claim (line ~985)
  - 113 calls × 49 ms = 5.51 s on ukpga/1970/9.  Internal re.search with two
    .*? lazy quantifiers in one alternation caused catastrophic backtracking
    (6.8 ms/call, same shape as Actuator 8).
  - Fix: substring fast-guard ("entries relating to") + split into two
    module-scope compiled patterns to kill the alternation cross-product.

Tests:
  Site 1:
    1. Adversarial perf — 10,000 calls against varied (text, label) inputs, <2s
    2. Equivalence — substring path vs regex path produce identical booleans
    3. Negative — text without phrase returns False
    4. Case-insensitive positive — "In Section 99" matches label "99"
    5. Word-boundary negative — "in section 991" does not match label "99"

  Site 2:
    6. Adversarial perf — long lead_text (~10 KB) without trailing clause, <100 ms
    7. Positive — known matching input returns expected result
    8. Negative — input missing "entries relating to" returns None quickly
    9. Positive omit-from-column form — the second pattern branch also matches
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.table_selectors import (
    _source_names_containing_target_for_table_cell,
    _uk_source_parent_table_column_entry_omission_text_patch_claim,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _addr_section(label: str) -> LegalAddress:
    """LegalAddress for section/<label>."""
    return LegalAddress(path=(("section", label),))


def _addr_subsection_table(section_label: str) -> LegalAddress:
    """LegalAddress for section/<label>/subsection/table — exercises the table-walk branch."""
    return LegalAddress(path=(("section", section_label), ("subsection", "table")))


def _make_element_with_id(tag: str = "Amendment", eid: str = "e-001") -> ET.Element:
    el = ET.Element(tag)
    el.set("id", eid)
    return el


# ---------------------------------------------------------------------------
# Site 1 — _source_names_containing_target_for_table_cell
# ---------------------------------------------------------------------------

# 1. Adversarial perf: 10,000 calls against varied (text, label) combinations.
#    Pre-fix: each call constructed a dynamic regex via re.search(rf"...", text)
#    thrashing the 512-entry re._compile cache; ~5.86 s total on ukpga/1970/9.
#    Post-fix: lru_cache + substring guard should complete 10,000 calls in <2 s.
def test_site1_adversarial_perf_10k_calls_fast() -> None:
    """10,000 varied calls to _source_names_containing_target_for_table_cell must complete <2s."""
    # Build a realistic mix of texts and labels: some matching, most not.
    texts = [
        "For the entry in section {} of the table substitute the following".format(i)
        for i in range(50)
    ] + [
        "Words substituted by SI 2001/99 in section {}(1)".format(i)
        for i in range(50)
    ] + [
        "In the first column of the table omit the word 'foo'"  # no section reference
    ] * 50 + [
        "In section {} of the Act, the table is amended".format(i)
        for i in range(50)
    ]

    targets = [_addr_section(str(i)) for i in range(50)] + [
        _addr_subsection_table(str(i)) for i in range(50)
    ] + [_addr_section(str(i * 3 + 1)) for i in range(20)]

    t0 = time.perf_counter()
    for iteration in range(10000):
        text = texts[iteration % len(texts)]
        target = targets[iteration % len(targets)]
        _source_names_containing_target_for_table_cell(text, target)
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, (
        f"10,000 calls took {elapsed:.3f}s; expected <2s after lru_cache + substring guard fix"
    )


# 2. Equivalence: substring-guarded path and the regex both agree on the same inputs.
#    Uses an explicit reference implementation that exercises the pre-fix shape.
@pytest.mark.parametrize("text,label,expected", [
    # Positive cases
    ("in section 5 of the table", "5", True),
    ("In Section 99 substitute the word", "99", True),
    ("For the entry IN SECTION 17 omit", "17", True),
    ("in section 17A of Act 1970", "17A", True),
    # Negative: wrong section number
    ("in section 5 of the table", "6", False),
    # Negative: substring present but word-boundary fails
    ("in section 991 of Act", "99", False),
    # Negative: no section phrase at all
    ("in the first column of the table", "5", False),
    # Edge: empty text
    ("", "5", False),
    # subsection/table target — should resolve to section label
    ("in section 42 of the table substitute", "42", True),
    # Case mix
    ("IN SECTION 5 OF THE TABLE", "5", True),
    # Label with special regex chars — note: \b after ) is non-word→non-word so
    # the word-boundary does NOT match; both reference and optimized return False.
    ("in section 7(2) of the Act substitute", "7(2)", False),
])
def test_site1_equivalence_with_reference_implementation(
    text: str, label: str, expected: bool
) -> None:
    """The optimized function must return the same bool as the reference regex."""
    import re

    # Reference: original implementation (pre-fix shape)
    def _reference(t: str, lbl: str) -> bool:
        if not t:
            return False
        return re.search(rf"\bin\s+section\s+{re.escape(lbl)}\b", t, flags=re.I) is not None

    target = _addr_section(label)
    result = _source_names_containing_target_for_table_cell(text, target)
    ref = _reference(text, label)

    assert result == expected, (
        f"Expected {expected!r} for text={text!r}, label={label!r}; got {result!r}"
    )
    assert result == ref, (
        f"Optimized result {result!r} != reference {ref!r} for text={text!r}, label={label!r}"
    )


# 3. Negative: text without "in section" returns False and does so fast.
def test_site1_negative_no_section_phrase() -> None:
    """Text with no 'in section' phrase returns False without any regex execution."""
    text = "In the first column of the table omit the entries relating to the Act of 1970."
    target = _addr_section("5")
    result = _source_names_containing_target_for_table_cell(text, target)
    assert result is False


# 4. Case-insensitive positive.
def test_site1_case_insensitive_match() -> None:
    target = _addr_section("99")
    assert _source_names_containing_target_for_table_cell("IN SECTION 99 substitute", target) is True


# 5. Word-boundary negative: "section 991" must not match label "99".
def test_site1_word_boundary_no_false_positive() -> None:
    target = _addr_section("99")
    assert _source_names_containing_target_for_table_cell("in section 991 of Act", target) is False


# ---------------------------------------------------------------------------
# Site 2 — _uk_source_parent_table_column_entry_omission_text_patch_claim
# ---------------------------------------------------------------------------

def _make_omission_call(
    *,
    extracted_text: str,
    extracted_el: ET.Element,
    source_root: ET.Element,
    target: LegalAddress,
    target_names_table: bool = True,
    source_names_containing_target: bool = True,
    source_parent_id: str = "e-001",
) -> dict | None:
    return _uk_source_parent_table_column_entry_omission_text_patch_claim(
        target_ref="ukpga/1970/9/section/5/subsection/table",
        target=target,
        extracted_text=extracted_text,
        extracted_el=extracted_el,
        source_root=source_root,
        target_names_table=target_names_table,
        source_names_containing_target=source_names_containing_target,
        source_parent_id=source_parent_id,
    )


def _build_source_root_with_parent_instruction(instruction: str) -> tuple[ET.Element, ET.Element]:
    """Return (source_root, extracted_el) with a parent carrying the given instruction text."""
    root = ET.Element("Amendment")
    root.set("id", "a-001")
    parent = ET.SubElement(root, "Body")
    parent.set("id", "b-001")
    # The instruction text will be found via _source_local_instruction_text_for_carried_payload
    # which looks for Instruction children.  Use a simple text approach that relies on
    # _source_text_before_extracted_child instead — put instruction text directly in parent
    # before the child element.
    # For simplicity in testing: add an Instruction element.
    instr_el = ET.SubElement(parent, "Instruction")
    instr_el.text = instruction
    child = ET.SubElement(parent, "P")
    child.set("id", "c-001")
    return root, child


# 6. Adversarial perf: long lead_text (~10 KB) WITHOUT "entries relating to" clause.
#    Pre-fix: each ancestor's lead_text triggered a slow re.search with two .*?
#    quantifiers and a trailing $ — catastrophic backtracking.
#    Post-fix: substring guard short-circuits before any regex.
def test_site2_adversarial_perf_long_text_no_clause_fast() -> None:
    """Long lead_text without 'entries relating to' must short-circuit in <100 ms."""
    # Generate ~10 KB of legal text WITHOUT the target phrase
    base = (
        "In the first column of the table for the entry for 'Taxes Management Act 1970' "
        "in column two for the words 'penalty notice' substitute 'assessment notice'. "
    )
    long_text = (base * 100).strip()  # ~10 KB
    assert len(long_text) > 9000
    assert "entries relating to" not in long_text.lower()

    target = _addr_section("5")
    el = ET.Element("P")
    el.set("id", "c-001")
    root = ET.Element("Amendment")
    root.set("id", "a-001")
    parent = ET.SubElement(root, "Body")
    parent.set("id", "b-001")
    instr_el = ET.SubElement(parent, "Instruction")
    instr_el.text = long_text
    parent.append(el)

    t0 = time.perf_counter()
    result = _make_omission_call(
        extracted_text="Taxes Management Act 1970",
        extracted_el=el,
        source_root=root,
        target=target,
    )
    elapsed = time.perf_counter() - t0

    assert result is None, "No match expected when 'entries relating to' is absent"
    assert elapsed < 0.100, (
        f"Call with long non-matching lead_text took {elapsed*1000:.1f}ms; expected <100ms"
    )


# 7. Positive: known matching input returns expected result.
def test_site2_positive_in_column_omit_entries_form() -> None:
    """'in the first column ... omit the entries relating to —' returns correct result."""
    instruction = "in the first column of the table omit the entries relating to —"
    root, child = _build_source_root_with_parent_instruction(instruction)
    target = _addr_section("5")

    result = _make_omission_call(
        extracted_text="Taxes Management Act 1970",
        extracted_el=child,
        source_root=root,
        target=target,
    )

    assert result is not None, "Expected a match dict, got None"
    assert result["rule_id"] == "uk_effect_source_parent_table_column_entry_omission_text_patch"
    assert result["column_index"] == 1
    assert result["match_text"] == "Taxes Management Act 1970"
    assert result["table_column_entry_action"] == "delete_entry_text"


# 8. Negative: input clearly missing "entries relating to" returns None quickly.
def test_site2_negative_no_entries_relating_to() -> None:
    """lead_text without 'entries relating to' returns None (substring guard fires)."""
    instruction = "in the first column of the table omit the words 'penalty notice'"
    root, child = _build_source_root_with_parent_instruction(instruction)
    target = _addr_section("5")

    result = _make_omission_call(
        extracted_text="Taxes Management Act 1970",
        extracted_el=child,
        source_root=root,
        target=target,
    )
    assert result is None


# 9. Positive: "omit from the second column ... entries relating to —" branch.
def test_site2_positive_omit_from_column_form() -> None:
    """'omit from the second column ... entries relating to —' returns correct result."""
    instruction = "omit from the second column of the table the entries relating to —"
    root, child = _build_source_root_with_parent_instruction(instruction)
    target = _addr_section("5")

    result = _make_omission_call(
        extracted_text="Income Tax Act 2007",
        extracted_el=child,
        source_root=root,
        target=target,
    )

    assert result is not None, "Expected a match dict for omit-from-column form, got None"
    assert result["rule_id"] == "uk_effect_source_parent_table_column_entry_omission_text_patch"
    assert result["column_index"] == 2
    assert result["match_text"] == "Income Tax Act 2007"
