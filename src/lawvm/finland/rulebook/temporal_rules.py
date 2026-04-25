"""Temporal-family Finland rulebook constants."""

from __future__ import annotations

from lawvm.finland.rulebook.atoms import TemporalAtom
from lawvm.finland.rulebook.common import (
    AuthorityTier,
    CitationRef,
    EmitRef,
    GuardRef,
    RuleExample,
    RuleHeader,
    RulePhase,
    RuleStrength,
)
from lawvm.finland.rulebook.families import TemporalRule
from lawvm.finland.rulebook.registries import EmitId, GuardId


TEMPORAL_VALIAIKAISESTI_IMMEDIATE_CLUSTER = TemporalRule(
    header=RuleHeader(
        rule_id="fi.temporal.valiaikaisesti_immediate_target_cluster",
        phase=RulePhase.TEMPORAL,
        priority=180,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Temporary marker applies to the immediately governed insert cluster",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="temporal rule"),),
        examples=(
            RuleExample(
                label="21b only temporary",
                input_text="lisätään lakiin väliaikaisesti uusi 21 b § sekä uusi 21 c ja 22 b §",
                expects=(
                    "temporary target section:21b",
                    "permanent target section:21c",
                    "permanent target section:22b",
                ),
            ),
        ),
    ),
    when=(TemporalAtom.TEMPORAL_WORD, TemporalAtom.INSERT_TARGET_CLUSTER),
    guards=(GuardRef(GuardId.MARKER_ATTACHES_TO_IMMEDIATE_CLUSTER),),
    emits=(EmitRef(EmitId.MARK_TARGETS_TEMPORARY, (("targets", "cluster"),)),),
)

TEMPORAL_COMMENCEMENT_EXTRACT = TemporalRule(
    header=RuleHeader(
        rule_id="fi.temporal.commencement_extract",
        phase=RulePhase.TEMPORAL,
        priority=175,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Extract commencement targets from explicit voimaantulo-style text",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="temporal rule"),),
        examples=(
            RuleExample(
                label="commencement date",
                input_text="Tämä laki tulee voimaan 1.1.2027.",
                expects=("commencement:1.1.2027",),
            ),
        ),
    ),
    when=(TemporalAtom.COMMENCEMENT_WORD,),
    emits=(EmitRef(EmitId.MARK_COMMENCEMENT_TARGETS, (("kind", "commencement"),)),),
)

TEMPORAL_EXPIRY_EXTRACT = TemporalRule(
    header=RuleHeader(
        rule_id="fi.temporal.expiry_extract",
        phase=RulePhase.TEMPORAL,
        priority=170,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Extract expiry targets from explicit määräaikainen / asti-style text",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="temporal rule"),),
        examples=(
            RuleExample(
                label="expiry date",
                input_text="Tämä laki on voimassa 31.12.2027 asti.",
                expects=("expiry:31.12.2027",),
            ),
        ),
    ),
    when=(TemporalAtom.EXPIRY_WORD,),
    emits=(EmitRef(EmitId.MARK_EXPIRY_TARGETS, (("kind", "expiry"),)),),
)

TEMPORAL_DEFERRED_COMMENCEMENT = TemporalRule(
    header=RuleHeader(
        rule_id="fi.temporal.deferred_commencement",
        phase=RulePhase.TEMPORAL,
        priority=172,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Keep explicit deferred commencement markers attached to the deferred activation window",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="temporal family"),),
        examples=(
            RuleExample(
                label="deferred commencement",
                input_text="Lain 1 § tulee voimaan myöhemmin erikseen säädettävänä ajankohtana.",
                expects=("commencement:deferred",),
            ),
        ),
    ),
    when=(TemporalAtom.DEFERRED_COMMENCEMENT_WORD,),
    emits=(EmitRef(EmitId.MARK_DEFERRED_COMMENCEMENT, (("kind", "deferred"),)),),
)

TEMPORAL_PHASED_ACTIVATION = TemporalRule(
    header=RuleHeader(
        rule_id="fi.temporal.phased_activation",
        phase=RulePhase.TEMPORAL,
        priority=168,
        authority=AuthorityTier.LAWVM_POLICY,
        strength=RuleStrength.POLICY,
        purpose="Represent phased activation as a structured activation policy rather than free text",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="temporal family"),),
        examples=(
            RuleExample(
                label="phased activation",
                input_text="Pykälät 1-3 tulevat voimaan vaiheittain.",
                expects=("activation:phased",),
            ),
        ),
    ),
    when=(TemporalAtom.PHASED_ACTIVATION_WORD,),
    emits=(EmitRef(EmitId.MARK_PHASED_ACTIVATION, (("kind", "phased"),)),),
)
