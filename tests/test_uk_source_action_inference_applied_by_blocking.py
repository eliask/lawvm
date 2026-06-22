"""§1.11 regression: non-textual modification effect types (``applied by…``,
``applied (with modifications)``, ``modified``, ``excluded``, ``restricted``,
``disapplied``) MUST NOT acquire ``action='repeal'`` / ``action='replace'`` /
``action='insert'`` from a free-text surface predicate.

Before this fix, when the effect_type wasn't matched by ``_UK_EFFECT_TYPE_ACTIONS``
(``_uk_effect_type_action`` returned ``None``) and the source schedule contained
the word "repeal" or "omit", the substring predicate at
``source_action_inference.infer_uk_effect_action_from_source`` (line ~397)
silently inferred ``action='repeal'``. That is the forbidden §1.11 shape:
a surface predicate authorizing legal state. The same suffix-sniff let
``applied by 2010 c. 8, Sch 7A para. 36(6) (as inserted)`` effects on
``ukpga/2006/46`` + ``ukpga/1970/9`` lower as a structural REPEAL of the
affected scope even though the modifying Schedule text is the variant body of
a non-textual modification overlay.

``is_uk_non_textual_modification_effect_type`` (mirrors
``source_adjudication._is_uk_non_textual_modification_effect_type``) is the
typed prefilter; on a non-textual effect_type with no canonical action, the
inference lane emits ``uk_effect_applied_by_action_inference_blocked`` and
returns ``blocked=True`` — the row stays visible on the evidence plane (§1.8
conservation) but no structural op is emitted against the principal text.
"""
from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_actions import (
    is_uk_non_textual_modification_effect_type,
)
from lawvm.uk_legislation.source_action_inference import (
    infer_uk_effect_action_from_source,
)


_APPLIED_BY_SCHEDULE_TEXT = (
    "SCHEDULE 7A TRANSITIONAL PROVISIONS Application of amendments "
    "1 In section 75 (interpretation), in subsection (2), repeal the "
    "definition of \"constituent authority\"; insert— \"constituent "
    "authority\" means the body referred to in section 4A(2)"
)


def _applied_by_effect(effect_type: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="uk_test_applied_by_action_inference_blocked",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="",
        affected_uri="/id/ukpga/1970/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1970",
        affected_number="9",
        affected_provisions="Act",
        affecting_uri="/id/uksi/2008/373",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2008",
        affecting_number="373",
        affecting_provisions="reg. 4A(2)",
        affecting_title=(
            "The Test Regulations (Amendment) (No. 2) Regulations 2008"
        ),
        in_force_dates=[{"date": "2009-04-06", "prospective": "false"}],
    )


def _schedule(text: str) -> ET._Element:
    el = ET.Element("Schedule")
    p = ET.SubElement(el, "Text")
    p.text = text
    return el


def test_detector_matches_non_textual_verbs() -> None:
    """Mirror ``source_adjudication._is_uk_non_textual_modification_effect_type``:
    the closed verb vocabulary + the (as ...)/with-modifications paren suffix."""
    assert is_uk_non_textual_modification_effect_type("applied by 2010 c. 8, Sch 7A para. 36(6) (as inserted)")
    assert is_uk_non_textual_modification_effect_type("applied (with modifications)")
    assert is_uk_non_textual_modification_effect_type("modified")
    assert is_uk_non_textual_modification_effect_type("modified (temp.)")
    assert is_uk_non_textual_modification_effect_type("excluded (temp.)")
    assert is_uk_non_textual_modification_effect_type("restricted")
    assert is_uk_non_textual_modification_effect_type("disapplied")


def test_detector_rejects_textual_verbs() -> None:
    assert not is_uk_non_textual_modification_effect_type("repealed")
    assert not is_uk_non_textual_modification_effect_type("repealed in part")
    assert not is_uk_non_textual_modification_effect_type("words substituted")
    assert not is_uk_non_textual_modification_effect_type("entry inserted")
    assert not is_uk_non_textual_modification_effect_type("")
    assert not is_uk_non_textual_modification_effect_type("omitted")


def test_applied_by_with_repeal_text_blocks_action_inference() -> None:
    """§1.11 liveness production-lane test: an ``applied by … (as inserted)``
    effect_type with source text containing the word "repeal" must NOT lower to
    action='repeal'. The substring predicate at line ~397 used to do exactly
    that; the audit now blocks before reaching the predicate."""
    rejections: list[dict[str, object]] = []
    inference = infer_uk_effect_action_from_source(
        effect=_applied_by_effect("applied by 2010 c. 8, Sch 7A para. 36(6) (as inserted)"),
        effect_type="applied by 2010 c. 8, sch 7a para. 36(6) (as inserted)",
        initial_action=None,
        extracted_el=_schedule(_APPLIED_BY_SCHEDULE_TEXT),
        extracted_text=_APPLIED_BY_SCHEDULE_TEXT,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    assert inference.blocked is True
    assert inference.action is None
    rule_ids = {str(r.get("rule_id")) for r in rejections}
    assert "uk_effect_applied_by_action_inference_blocked" in rule_ids
    rejection = next(
        r
        for r in rejections
        if r.get("rule_id") == "uk_effect_applied_by_action_inference_blocked"
    )
    assert rejection.get("family") == "applicability_scope"
    assert rejection.get("blocking") is True
    assert rejection.get("reason_code") == "non_textual_modification_overlay"
    assert rejection.get("effect_type_normalized") == (
        "applied by 2010 c. 8, sch 7a para. 36(6) (as inserted)"
    )


def test_modified_with_omit_text_blocks_action_inference() -> None:
    """Same shape for a bare ``modified`` effect_type that the legacy predicate
    would have inferred as action='repeal' from the word 'omit' in schedule text."""
    rejections: list[dict[str, object]] = []
    inference = infer_uk_effect_action_from_source(
        effect=_applied_by_effect("modified"),
        effect_type="modified",
        initial_action=None,
        extracted_el=_schedule(_APPLIED_BY_SCHEDULE_TEXT),
        extracted_text=_APPLIED_BY_SCHEDULE_TEXT,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    assert inference.blocked is True
    assert inference.action is None
    rule_ids = {str(r.get("rule_id")) for r in rejections}
    assert "uk_effect_applied_by_action_inference_blocked" in rule_ids


def test_empty_effect_type_with_repeal_text_still_infers_repeal() -> None:
    """Negative control — the §1.11 fix MUST NOT widen to plain empty
    effect_type. An empty effect_type with real operative 'repeal' source text
    is the canonical missing-action inference path: action='repeal' is the
    expected outcome."""
    rejections: list[dict[str, object]] = []
    plain_text = (
        "1 In section 4, repeal subsection (3); the words repealed are no longer "
        "in force."
    )
    inference = infer_uk_effect_action_from_source(
        effect=_applied_by_effect(""),
        effect_type="",
        initial_action=None,
        extracted_el=_schedule(plain_text),
        extracted_text=plain_text,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    # Empty effect_type is the canonical missing-action inference path; the
    # substring predicate here is the typed prefilter that owns this surface.
    assert inference.action == "repeal"
    assert inference.blocked is False
    assert "uk_effect_applied_by_action_inference_blocked" not in {
        str(r.get("rule_id")) for r in rejections
    }


def test_canonical_repeal_effect_type_with_omit_text_uses_dict() -> None:
    """The closed vocabulary ``_UK_EFFECT_TYPE_ACTIONS`` entry for ``repealed``
    / ``omitted`` still maps to action='repeal'. The §1.11 prefilter only kicks
    in for non-textual-modification effect types whose verb is OUTSIDE the
    closed vocabulary (applied/modified/excluded/restricted/disapplied)."""
    rejections: list[dict[str, object]] = []
    plain_text = "1 In section 4, omit subsection (3)."
    inference = infer_uk_effect_action_from_source(
        effect=_applied_by_effect("repealed"),
        effect_type="repealed",
        initial_action=None,
        extracted_el=_schedule(plain_text),
        extracted_text=plain_text,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    assert inference.action == "repeal"
    assert inference.blocked is False
    assert "uk_effect_applied_by_action_inference_blocked" not in {
        str(r.get("rule_id")) for r in rejections
    }
