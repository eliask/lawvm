"""D1 (#211/#219): block untyped foreign-payload whole-provision REPLACE clobbers.

A UK effect feed carries untyped (``Type=""``) rows whose action can only be
INFERRED from the affecting-source drafting verbs. When such a row is an inferred
whole-section/subsection REPLACE and the structural-payload extraction found NO
source node matching the target, the downstream
``infer_source_payload_from_target`` fallback reuses the ENTIRE affecting
schedule text as the section body — e.g. CRoW 2000 Sch. 9 para. 1 substituting
s. 28 of the Wildlife & Countryside Act 1981 (a DIFFERENT act) lowered as a
~41 kB flat payload identically onto NPACA 1949 ss. 16/103/106/107, deleting
every real subsection eId.

The guard blocks ONLY that foreign-payload clobber signal:
  * ``action == "replace"``;
  * ``effect_type == ""`` (untyped → inferred action);
  * a bare section/subsection whole-provision target (no facet);
  * the structural extraction found no source-matching node
    (``source_structural_payload_matches_target`` False).

Genuine untyped substitutions that DO carry a target-matching source node (e.g.
NPACA s. 20(2)/(3): real P2 payload) never reach the guard because a source node
was found.

AGENTS.md obligations covered:
  §0    repairs that change legal structure must be owned and observable
  §1.5  no payload smuggling
  §15   synthetic unit test + negative tests
"""
from __future__ import annotations

from typing import Any

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.uk_legislation.effect_payload_rejections import (
    reject_untyped_foreign_payload_whole_provision_replace,
)
from lawvm.uk_legislation.effects import UKEffectRecord


def _effect(effect_type: str = "") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-foreign-payload-clobber",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2005-01-01",
        affected_uri="/id/ukpga/1949/97/section/16",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1949",
        affected_number="97",
        affected_provisions="s. 16",
        affecting_uri="/id/ukpga/2000/37",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2000",
        affecting_number="37",
        affecting_provisions="Sch. 9 para. 1",
        affecting_title="Countryside and Rights of Way Act 2000",
    )


def _call(
    target: LegalAddress,
    *,
    effect_type: str = "",
    action: str = "replace",
    structural_extraction_found_source_node: bool = False,
    source_structural_payload_matches_target: bool = False,
    source_routes_to_text_patch: bool = False,
) -> tuple[bool, list[dict[str, Any]]]:
    rejections: list[dict[str, Any]] = []
    rejected = reject_untyped_foreign_payload_whole_provision_replace(
        effect=_effect(effect_type),
        effect_type=effect_type,
        action=action,
        t_str="s. 16",
        target=target,
        structural_extraction_found_source_node=structural_extraction_found_source_node,
        source_structural_payload_matches_target=source_structural_payload_matches_target,
        source_routes_to_text_patch=source_routes_to_text_patch,
        extracted_el=None,
        extracted_text=None,
        lowering_rejections_out=rejections,
    )
    return rejected, rejections


def test_blocks_untyped_whole_section_foreign_payload_replace() -> None:
    target = LegalAddress(path=(("section", "16"),))
    rejected, rejections = _call(target)
    assert rejected is True
    assert len(rejections) == 1
    detail = rejections[0]
    assert (
        detail["rule_id"]
        == "uk_effect_untyped_foreign_payload_whole_provision_replace_rejected"
    )
    assert detail["family"] == "applicability_scope"
    assert (
        detail["reason_code"]
        == "untyped_foreign_payload_whole_provision_replace_clobber"
    )
    assert detail["target_ref"] == "s. 16"
    assert detail["target_leaf_kind"] == "section"
    assert detail["strict_disposition"] == "block"


def test_blocks_untyped_whole_subsection_foreign_payload_replace() -> None:
    target = LegalAddress(path=(("section", "20"), ("subsection", "2")))
    rejected, rejections = _call(target)
    assert rejected is True
    assert rejections[0]["target_leaf_kind"] == "subsection"


def test_allows_genuine_untyped_substitution_with_matching_source_node() -> None:
    """NPACA s. 20(2)/(3) shape: a real source P2 was found → matches True."""
    target = LegalAddress(path=(("section", "20"), ("subsection", "2")))
    rejected, rejections = _call(
        target,
        structural_extraction_found_source_node=True,
        source_structural_payload_matches_target=True,
    )
    assert rejected is False
    assert not rejections


def test_allows_when_source_node_found_even_if_match_flag_false() -> None:
    """A source structural node WAS located (content_ir/actual_el present); this is
    not the foreign-payload clobber signal, so the guard must abstain."""
    target = LegalAddress(path=(("section", "16"),))
    rejected, _ = _call(
        target,
        structural_extraction_found_source_node=True,
        source_structural_payload_matches_target=False,
    )
    assert rejected is False


def test_ignores_typed_rows() -> None:
    target = LegalAddress(path=(("section", "16"),))
    rejected, rejections = _call(target, effect_type="substituted")
    assert rejected is False
    assert not rejections


def test_ignores_non_replace_actions() -> None:
    target = LegalAddress(path=(("section", "16"),))
    rejected, _ = _call(target, action="insert")
    assert rejected is False


def test_ignores_facet_targets() -> None:
    """A heading-facet target is not a whole-provision body replace."""
    target = LegalAddress(path=(("section", "16"),), special=FacetKind.HEADING)
    rejected, _ = _call(target)
    assert rejected is False


def test_ignores_deeper_leaf_targets() -> None:
    """A paragraph/item leaf carries its own narrower lowering lanes."""
    target = LegalAddress(
        path=(("section", "16"), ("subsection", "5"), ("paragraph", "c"))
    )
    rejected, _ = _call(target)
    assert rejected is False


def test_ignores_schedule_container_targets() -> None:
    target = LegalAddress(path=(("schedule", "1"), ("paragraph", "3")))
    rejected, _ = _call(target)
    assert rejected is False


def test_ignores_text_patch_routed_rows() -> None:
    """False-positive guard: an empty-type row whose extracted source parses into
    text-patch fragments (``after "X" there is inserted "Y"`` compound word-insert
    on a bare subsection, e.g. the ukpga/1962/46 s.86(4) case that lowers to two
    TEXT_PATCH ops) is NOT a whole-body clobber and must NOT be blocked, even
    though its inferred placeholder action is ``replace`` with no source-matching
    structural node."""
    target = LegalAddress(path=(("section", "86"), ("subsection", "4")))
    rejected, rejections = _call(target, source_routes_to_text_patch=True)
    assert rejected is False
    assert not rejections
