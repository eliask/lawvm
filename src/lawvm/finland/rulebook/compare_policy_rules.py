"""Compare-policy Finland rulebook family."""

from __future__ import annotations

from lawvm.finland.rulebook.atoms import CompareAtom
from lawvm.finland.rulebook.common import (
    AuthorityTier,
    CitationRef,
    EmitRef,
    RuleExample,
    RuleHeader,
    RulePhase,
    RuleStrength,
)
from lawvm.finland.rulebook.compare_policy_types import CompareRule
from lawvm.finland.rulebook.registries import EmitId


COMPARE_REPEAL_NOTICE_EDITORIAL = CompareRule(
    header=RuleHeader(
        rule_id="fi.compare.repeal_notice_editorial",
        phase=RulePhase.COMPARE,
        priority=160,
        authority=AuthorityTier.LAWVM_POLICY,
        strength=RuleStrength.POLICY,
        purpose="Classify oracle repeal notice text against a replay repeal placeholder as editorial convention, not replay-missing",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="comparison policy spec"),),
        examples=(
            RuleExample(
                label="oracle repeal notice vs replay placeholder",
                expects=("compare_equivalent:repeal_notice_editorial",),
                rejects=("false_positive_xml_topology_drift",),
            ),
        ),
    ),
    when=(CompareAtom.ORACLE_REPEAL_NOTICE_TEXT, CompareAtom.REPLAY_REPEAL_PLACEHOLDER),
    emits=(EmitRef(EmitId.MARK_COMPARE_EQUIVALENT, (("reason", "repeal_notice_editorial"),)),),
)

COMPARE_ORACLE_HTML_XML_TOPOLOGY_DRIFT = CompareRule(
    header=RuleHeader(
        rule_id="fi.compare.oracle_html_xml_topology_drift",
        phase=RulePhase.COMPARE,
        priority=155,
        authority=AuthorityTier.LAWVM_POLICY,
        strength=RuleStrength.POLICY,
        purpose="Treat oracle HTML/XML topology drift as display-only when the substantive markers still align",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="comparison policy spec"),),
        examples=(
            RuleExample(
                label="html/xml layout drift",
                expects=("compare_equivalent:topology_drift_display_only",),
                rejects=("substantive_marker_mismatch",),
            ),
        ),
    ),
    when=(CompareAtom.ORACLE_HTML_XML_TOPOLOGY_DRIFT,),
    emits=(EmitRef(EmitId.MARK_COMPARE_EQUIVALENT, (("reason", "topology_drift_display_only"),)),),
)

COMPARE_ORACLE_OMISSION_BLANK = CompareRule(
    header=RuleHeader(
        rule_id="fi.compare.oracle_omission_blank",
        phase=RulePhase.COMPARE,
        priority=150,
        authority=AuthorityTier.ORACLE_EDITORIAL,
        strength=RuleStrength.CONVENTIONAL,
        purpose="Treat oracle omission blanks as a display convention rather than a semantic mismatch",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="comparison policy spec"),),
        examples=(
            RuleExample(
                label="omission blank",
                expects=("compare_equivalent:oracle_omission_blank",),
                rejects=("semantic_payload_mismatch",),
            ),
        ),
    ),
    when=(CompareAtom.ORACLE_OMISSION_BLANK,),
    emits=(EmitRef(EmitId.MARK_COMPARE_OMISSION_BLANK, (("reason", "oracle_omission_blank"),)),),
)

COMPARE_ORACLE_STALE_SOURCE = CompareRule(
    header=RuleHeader(
        rule_id="fi.compare.oracle_stale_source",
        phase=RulePhase.COMPARE,
        priority=148,
        authority=AuthorityTier.LAWVM_POLICY,
        strength=RuleStrength.POLICY,
        purpose="Treat stale oracle source material as a compare-policy concern instead of a replay mismatch",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="comparison policy spec"),),
        examples=(
            RuleExample(
                label="stale oracle source",
                expects=("compare_equivalent:oracle_stale_source",),
                rejects=("replay_materialization_mismatch",),
            ),
        ),
    ),
    when=(CompareAtom.ORACLE_STALE_SOURCE,),
    emits=(EmitRef(EmitId.MARK_COMPARE_STALE_SOURCE, (("reason", "oracle_stale_source"),)),),
)
