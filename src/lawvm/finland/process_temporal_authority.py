"""Amendment-level temporal authority derivation for ``process_muutoslaki``."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from lxml import etree

from lawvm.core.compile_result import ActivationRule
from lawvm.core.phase_result import Finding
from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
from lawvm.finland.metadata import (
    _amendment_effective_date_with_step,
    _amendment_expiry_date,
    _statute_issue_date,
)
from lawvm.finland.temporal_lowering import (
    activation_rules_from_meta_clauses,
    classify_contingent,
    default_activation_rule,
)


RecordProcessFinding = Callable[..., Finding]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AmendmentTemporalAuthority:
    effective_date: Optional[dt.date]
    expiry_date: Optional[dt.date]
    issue_date: Optional[dt.date]
    effective_step: str
    primary_activation_rule: ActivationRule
    typed_contingent: bool


@dataclass(slots=True)
class ProcessTemporalAuthorityContext:
    amendment_id: str
    johto: str
    muutos_tree: etree._Element
    record_finding: RecordProcessFinding

    def derive(self) -> AmendmentTemporalAuthority:
        effective_date, effective_step = _amendment_effective_date_with_step(self.muutos_tree)
        expiry_date = _amendment_expiry_date(self.muutos_tree)
        issue_date = _statute_issue_date(self.muutos_tree)

        meta_clauses = extract_meta_surface_clauses(self.johto)
        activation_rules = activation_rules_from_meta_clauses(meta_clauses)
        if not activation_rules:
            activation_rules = [default_activation_rule()]

        primary_rule = activation_rules[0]
        typed_contingent = classify_contingent(primary_rule)

        # Empty johtolause temporal info can still be detected by the legacy
        # body-level voimaantulo scanner. Preserve that as an explicit bridge.
        if not typed_contingent and effective_step == "contingent_text":
            typed_contingent = True

        if typed_contingent:
            self.record_finding(
                kind="TIME.CONTINGENT_EFFECTIVE_DATE",
                message=(
                    "Effective date is contingent or decree-set in voimaantulo text; "
                    "publication date is not a trustworthy legal PIT proxy."
                ),
                source_statute=self.amendment_id,
                detail={
                    "step": effective_step,
                    "activation_rule_kind": primary_rule.kind,
                },
            )
        elif effective_step in ("text_regex", "publication_date"):
            self.record_finding(
                kind="TIME.ESTIMATED_EFFECTIVE_DATE",
                message=(
                    "Effective date estimated by voimaantulo text regex (step 2)."
                    if effective_step == "text_regex"
                    else "Effective date substituted by publication date - dateEntryIntoForce absent (step 3)."
                ),
                source_statute=self.amendment_id,
                detail={"step": effective_step},
                role="obligation",
                blocking=False,
            )

        if typed_contingent and effective_step not in ("contingent_text",):
            logger.debug(
                "[%s] activation_rule=%s (contingent) but _eff_step=%s - typed model more specific",
                self.amendment_id,
                primary_rule.kind,
                effective_step,
            )
        elif not typed_contingent and effective_step == "contingent_text":
            logger.debug(
                "[%s] _eff_step=contingent_text but activation_rule=%s (not contingent) - legacy more specific",
                self.amendment_id,
                primary_rule.kind,
            )

        return AmendmentTemporalAuthority(
            effective_date=effective_date,
            expiry_date=expiry_date,
            issue_date=issue_date,
            effective_step=effective_step,
            primary_activation_rule=primary_rule,
            typed_contingent=typed_contingent,
        )
