"""Non-textual application/extent overlay effects must lower to a non-blocking
observation, not a blocking ``no_supported_action`` rejection.

A large class of UK effects-feed verbs (``applied``, ``modified``, ``excluded``,
``extended``, ``power to ... conferred``, ``transfer of functions``,
``amendment to earlier affecting provision ...`` …) describe overlay
relationships rather than mutations of the affected Act's consolidated text.
They correctly lower to no replay operation, so the terminal missing-action lane
must record them as a non-blocking observation
(``uk_effect_application_overlay_no_textual_action_observed``) instead of the
blocking ``uk_effect_lowering_no_supported_action_rejected``. Genuinely textual
verbs (``substituted`` …) must still lower to a real action.

Rule ID  : uk_effect_application_overlay_no_textual_action_observed
Family   : applicability_scope
Blocking : False
"""
from __future__ import annotations

from lxml import etree as ET

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.uk_legislation.effect_compiler import compile_effect_to_ir_ops
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_actions import (
    is_uk_benign_application_overlay_effect_type,
)
from lawvm.uk_legislation.source_action_inference import (
    append_no_supported_action_rejection,
)

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"

_OVERLAY_VERBS = (
    "applied",
    "applied (with modifications)",
    "applied (temp.)",
    "applied (ni)",
    "applied in part",
    "incorporated",
    "modified",
    "excluded",
    "restricted",
    "extended",
    "disapplied",
    "saved",
    "power to apply conferred",
    "power to modify conferred",
    "transfer of functions",
    "functions transferred",
    "amendment to earlier affecting provision si 2001/1184 reg. 9 sch. (as amended)",
    "applied (with modifications) by s.i. 2001/2599, sch. 1 (as substituted)",
)

# Verbs that genuinely rewrite the printed text and must NOT be silenced.
_TEXTUAL_VERBS = (
    "substituted",
    "inserted",
    "repealed",
    "omitted",
    "added",
    "replaced",
    "words substituted",
    "words inserted",
    "amended",
    "text amended",
    "sum substituted",
    "entry substituted",
)


def _overlay_effect(effect_type: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="overlay-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri="/id/ukpga/2000/17/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="17",
        affected_provisions="s. 1",
        affecting_uri="/id/uksi/2001/2599",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2001",
        affecting_number="2599",
        affecting_provisions="sch. 1",
        affecting_title="Test Applying Instrument 2001",
        in_force_dates=[{"date": "2002-01-01", "prospective": "false"}],
    )


class TestOverlayPredicate:
    def test_overlay_verbs_classified_benign(self) -> None:
        for verb in _OVERLAY_VERBS:
            assert is_uk_benign_application_overlay_effect_type(verb) is True, verb

    def test_textual_verbs_not_classified_benign(self) -> None:
        for verb in _TEXTUAL_VERBS:
            assert is_uk_benign_application_overlay_effect_type(verb) is False, verb

    def test_empty_type_not_classified_benign(self) -> None:
        # Empty-type rows are genuinely-unresolved and owned by another lane.
        assert is_uk_benign_application_overlay_effect_type("") is False


class TestNoSupportedActionLane:
    def test_overlay_emits_nonblocking_observation(self) -> None:
        rejections: list[dict] = []
        append_no_supported_action_rejection(
            effect=_overlay_effect("applied"),
            effect_type="applied",
            extracted_el=None,
            extracted_text=None,
            lowering_rejections_out=rejections,
        )
        assert len(rejections) == 1
        rec = rejections[0]
        assert rec["rule_id"] == "uk_effect_application_overlay_no_textual_action_observed"
        assert is_blocking_compile_record(rec) is False

    def test_modified_overlay_emits_nonblocking_observation(self) -> None:
        rejections: list[dict] = []
        append_no_supported_action_rejection(
            effect=_overlay_effect("modified"),
            effect_type="modified",
            extracted_el=None,
            extracted_text=None,
            lowering_rejections_out=rejections,
        )
        assert rejections[0]["rule_id"] == (
            "uk_effect_application_overlay_no_textual_action_observed"
        )
        assert is_blocking_compile_record(rejections[0]) is False

    def test_unresolved_textual_row_stays_blocking_rejection(self) -> None:
        # Empty-type / genuinely-unresolved rows that reach this lane must keep
        # the blocking missing-action rejection — not be silenced as overlays.
        rejections: list[dict] = []
        append_no_supported_action_rejection(
            effect=_overlay_effect(""),
            effect_type="",
            extracted_el=None,
            extracted_text=None,
            lowering_rejections_out=rejections,
        )
        assert rejections[0]["rule_id"] == "uk_effect_lowering_no_supported_action_rejected"
        assert is_blocking_compile_record(rejections[0]) is True


class TestCompileEndToEnd:
    def test_applied_overlay_compiles_to_no_ops_nonblocking(self) -> None:
        el = ET.fromstring(
            f'<P1 xmlns="{_LEG_NS}"><Text>The Act applies for the purposes of '
            f"these Regulations.</Text></P1>"
        )
        rejections: list[dict] = []
        ops = compile_effect_to_ir_ops(
            _overlay_effect("applied"),
            el,
            lowering_rejections_out=rejections,
        )
        assert ops == []
        overlay_rows = [
            r
            for r in rejections
            if r["rule_id"]
            == "uk_effect_application_overlay_no_textual_action_observed"
        ]
        assert len(overlay_rows) == 1
        assert is_blocking_compile_record(overlay_rows[0]) is False
        # No blocking no_supported_action masquerading as an unhandled_op.
        assert not any(
            r["rule_id"] == "uk_effect_lowering_no_supported_action_rejected"
            and is_blocking_compile_record(r)
            for r in rejections
        )

    def test_substituted_textual_effect_still_lowers_to_action(self) -> None:
        # A genuinely-textual word substitution must still lower to a real op and
        # must NOT be routed through the overlay observation lane.
        el = ET.fromstring(
            f'<P1 xmlns="{_LEG_NS}"><Text>In section 1, for "old" substitute '
            f'"new".</Text></P1>'
        )
        effect = _overlay_effect("words substituted")
        rejections: list[dict] = []
        ops = compile_effect_to_ir_ops(
            effect,
            el,
            lowering_rejections_out=rejections,
        )
        assert ops, "textual word substitution must lower to at least one op"
        assert not any(
            r["rule_id"]
            == "uk_effect_application_overlay_no_textual_action_observed"
            for r in rejections
        )
