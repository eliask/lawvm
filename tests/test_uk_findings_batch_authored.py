"""Guard the authored UK findings-only manual-claim batch from silent rot.

This loads the on-disk per-statute store files for the findings-only families
authored by ``stream-uk-author-findings-batch`` and asserts, for each authored
claim, that:

  - it deserializes through the production store loader and routes to the right
    ``compile_ops_for_statute`` bucket;
  - it VALIDATES against its real bound effect + the extracted affecting source
    (the same surface the manual-frontier classifier binds); and
  - a validated claim EMITS its typed finding through the gate (the frontier
    residual becomes owned), never mutating base text.

The four findings-only families covered here:
  - ``savings_scoped_omission`` (savings-qualified text omission);
  - ``source_feed_target_reconciliation`` (N5 ambiguous / parent-authoritative);
  - ``deixis_in_application`` (N4 application-by-reference deixis); and
  - ``application_overlay`` (M5 non-textual application/modification overlay).

Integration tests: they require ``data/uk_legislation.farchive`` (the real
affecting source is read to bind each claim). When the archive is absent the
file-level schema/round-trip assertions still run; the validate+emit assertions
are skipped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from farchive import Farchive
from lawvm.uk_legislation.effects import (
    get_affecting_act_enacted_xml_from_archive,
    get_affecting_act_xml_from_archive,
    load_effects_for_statute_from_archive,
)
from lawvm.uk_legislation.effect_source_selection import (
    extracted_tag_and_text,
    select_source_for_effect,
)
from lawvm.uk_legislation.provision_extractor import (
    extract_provision_element_from_bytes,
)
from lawvm.uk_legislation.manual_claim_store import (
    default_store_dir,
    load_manual_claims_for_statute,
)

from lawvm.uk_legislation.application_overlay_claim import (
    APPLICATION_OVERLAY_CLAIM_KIND,
    APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID,
    gate_application_overlay_claim,
    validate_application_overlay_claim,
)
from lawvm.uk_legislation.deixis_application_claim import (
    DEIXIS_IN_APPLICATION_CLAIM_KIND,
    DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID,
    gate_deixis_in_application_claim,
    validate_deixis_in_application_claim,
)
from lawvm.uk_legislation.savings_omission_claim import (
    SAVINGS_SCOPED_OMISSION_CLAIM_KIND,
    SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID,
    gate_savings_scoped_omission_claim,
    validate_savings_scoped_omission_claim,
)
from lawvm.uk_legislation.source_feed_reconciliation_claim import (
    SOURCE_FEED_RECONCILIATION_CLAIM_KIND,
    SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID,
    gate_source_feed_reconciliation_claim,
    validate_source_feed_reconciliation_claim,
)

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"
_APPLICABILITY_MODE = "structural_only"

# claim_kind -> (validate, gate, expected emitted-finding rule_id). The validate/
# gate callables are heterogeneous per family (each takes its own claim type), so
# the table is typed ``Any`` and the per-family claim object is recovered from its
# typed bucket below before dispatch.
_FINDINGS_FAMILIES: dict[str, tuple[Any, Any, str]] = {
    SAVINGS_SCOPED_OMISSION_CLAIM_KIND: (
        validate_savings_scoped_omission_claim,
        gate_savings_scoped_omission_claim,
        SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID,
    ),
    SOURCE_FEED_RECONCILIATION_CLAIM_KIND: (
        validate_source_feed_reconciliation_claim,
        gate_source_feed_reconciliation_claim,
        SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID,
    ),
    DEIXIS_IN_APPLICATION_CLAIM_KIND: (
        validate_deixis_in_application_claim,
        gate_deixis_in_application_claim,
        DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID,
    ),
    APPLICATION_OVERLAY_CLAIM_KIND: (
        validate_application_overlay_claim,
        gate_application_overlay_claim,
        APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID,
    ),
}

# The statutes this batch authored findings-only claims into.
_BATCH_STATUTES = (
    "ukpga/1995/26",
    "ukpga/2005/5",
    "ukpga/2006/46",
    "ukpga/2007/15",
    "ukpga/2018/12",
)

# The provenance tag this batch stamps on every claim it authored.
_BATCH_CLAIMANT_PREFIX = "stream-uk-author-findings-batch"


def _authored_findings_rows() -> list[tuple[str, dict]]:
    """(statute_id, claim_row) pairs for every findings-only claim this batch owns."""
    rows: list[tuple[str, dict]] = []
    store_dir = default_store_dir()
    for statute_id in _BATCH_STATUTES:
        path = store_dir / f"{statute_id.replace('/', '__')}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        for claim in payload.get("claims", []):
            if claim.get("claim_kind") not in _FINDINGS_FAMILIES:
                continue
            if not str(claim.get("claimant", "")).startswith(_BATCH_CLAIMANT_PREFIX):
                continue
            rows.append((statute_id, claim))
    return rows


_AUTHORED_ROWS = _authored_findings_rows()


def test_batch_authored_at_least_ten_findings_claims():
    """The batch authored a meaningful number of findings-only claims (~10-20)."""
    assert len(_AUTHORED_ROWS) >= 10, (
        f"expected >=10 authored findings-only claims, found {len(_AUTHORED_ROWS)}"
    )


def test_loader_routes_authored_claims_to_findings_buckets():
    """Every authored findings-only claim loads + routes to a findings-only bucket."""
    for statute_id in _BATCH_STATUTES:
        loaded = load_manual_claims_for_statute(statute_id, enabled=True)
        assert not loaded.unknown_kind_rows, (
            f"{statute_id}: unexpected unknown-kind rows {loaded.unknown_kind_rows}"
        )
    # Spread: at least three distinct statutes carry findings-only claims.
    statutes_with_findings = {sid for sid, _ in _AUTHORED_ROWS}
    assert len(statutes_with_findings) >= 3, statutes_with_findings


def _extracted_source(archive, statute_id, effect_id, effects_cache):
    if statute_id not in effects_cache:
        effects_cache[statute_id] = {
            e.effect_id: e
            for e in load_effects_for_statute_from_archive(statute_id, archive)
        }
    effect = effects_cache[statute_id].get(effect_id)
    if effect is None:
        return None, None
    selection = select_source_for_effect(
        effect=effect,
        archive=archive,
        applicability_mode=_APPLICABILITY_MODE,
        extraction_cache={},
        enacted_extraction_cache={},
        effect_diagnostics_out=None,
        current_xml_loader=get_affecting_act_xml_from_archive,
        enacted_xml_loader=get_affecting_act_enacted_xml_from_archive,
        provision_extractor=extract_provision_element_from_bytes,
    )
    text = extracted_tag_and_text(selection.extracted_el).text
    return effect, (text or None)


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live validate+emit test",
)
def test_each_authored_claim_validates_and_emits_its_finding():
    """Each authored findings-only claim validates against the real effect + source
    and emits its typed finding (non-replayable; base text never mutated)."""
    assert _AUTHORED_ROWS, "no authored findings-only claims discovered on disk"
    archive = Farchive(str(_DB_PATH))
    effects_cache: dict[str, dict] = {}
    try:
        for statute_id, row in _AUTHORED_ROWS:
            loaded = load_manual_claims_for_statute(statute_id, enabled=True)
            # Recover the typed claim object matching this row's claim_id.
            claim = None
            for bucket_claims in (
                loaded.savings_omission_claims,
                loaded.source_feed_reconciliation_claims,
                loaded.deixis_application_claims,
                loaded.application_overlay_claims,
            ):
                for candidate in bucket_claims:
                    if candidate.claim_id == row["claim_id"]:
                        claim = candidate
            assert claim is not None, f"{row['claim_id']} not loaded"
            validate, gate, emitted_rule = _FINDINGS_FAMILIES[row["claim_kind"]]
            effect, src = _extracted_source(
                archive, statute_id, row["effect_id"], effects_cache
            )
            assert effect is not None, (
                f"{row['claim_id']}: bound effect {row['effect_id']} not in feed"
            )
            validation = validate(claim, effect=effect, extracted_source_text=src)
            assert validation.validated, (
                f"{row['claim_id']} failed to validate: {validation.reason}"
            )
            gate_result = gate(claim, validated=True)
            assert gate_result.emitted, f"{row['claim_id']} did not emit a finding"
            assert gate_result.finding is not None
            finding = gate_result.finding.to_dict()
            assert finding["rule_id"] == emitted_rule
            # Findings-only: never replayable as a base-text op.
            assert finding.get("replayable") is False, (
                f"{row['claim_id']}: findings-only claim must not be replayable"
            )
    finally:
        archive.close()
