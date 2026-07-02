"""Tests for the oracle-defect external-confirmation rail (#191).

Validates:
* the frozen ``OracleDefectExternalConfirmation`` dataclass round-trips through
  the on-disk store (write -> load) byte-stably,
* deterministic ordering of the store regardless of insertion order,
* the coverage metric (externally-validated oracle-defect count) on a fixture,
* rejection of malformed records (bad response, bad date, empty residual ids,
  string-not-sequence residuals, unknown fields, corrected/date mismatch),
* the shipped store file parses and is read-only telemetry (no scoring).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lawvm.tools.oracle_defect_confirmation import (
    CONFIRMING_RESPONSES,
    SCHEMA,
    ConfirmationCoverage,
    OracleDefectExternalConfirmation,
    add_confirmation,
    annotate_residuals,
    compute_coverage,
    load_confirmations,
    write_confirmations,
    _store_path,
)


def _rec(**overrides: Any) -> OracleDefectExternalConfirmation:
    base: dict[str, Any] = dict(
        confirmation_id="fi-2026-0001",
        source="finlex",
        ticket="FINLEX-1234",
        submitted_date="2026-07-01",
        keeper_response="acknowledged",
        affected_residual_ids=("fi:aaaa", "fi:bbbb"),
        note="reported batch of 3 editorial defects",
    )
    base.update(overrides)
    return OracleDefectExternalConfirmation(
        confirmation_id=base["confirmation_id"],
        source=base["source"],
        ticket=base["ticket"],
        submitted_date=base["submitted_date"],
        keeper_response=base["keeper_response"],
        affected_residual_ids=base["affected_residual_ids"],
        correction_date=base.get("correction_date", ""),
        note=base.get("note", ""),
    )


def test_dataclass_round_trip_through_store(tmp_path: Path):
    store = tmp_path / "confirmations.json"
    rec = _rec()
    write_confirmations([rec], store)
    loaded = load_confirmations(store)
    assert len(loaded) == 1
    assert loaded[0] == rec
    # to_dict/from_dict identity
    assert OracleDefectExternalConfirmation.from_dict(rec.to_dict()) == rec


def test_deterministic_ordering(tmp_path: Path):
    store = tmp_path / "confirmations.json"
    a = _rec(confirmation_id="uk-1", source="legislation.gov.uk", submitted_date="2026-06-01")
    b = _rec(confirmation_id="fi-2", source="finlex", submitted_date="2026-06-02")
    c = _rec(confirmation_id="fi-1", source="finlex", submitted_date="2026-06-01")
    write_confirmations([a, b, c], store)
    first = store.read_text(encoding="utf-8")
    # reversed insertion order -> identical bytes on disk
    write_confirmations([c, b, a], store)
    second = store.read_text(encoding="utf-8")
    assert first == second
    ordered = [rec.confirmation_id for rec in load_confirmations(store)]
    # sorted by (source, submitted_date, confirmation_id)
    assert ordered == ["fi-1", "fi-2", "uk-1"]


def test_add_confirmation_rejects_duplicate_id(tmp_path: Path):
    store = tmp_path / "confirmations.json"
    add_confirmation(_rec(confirmation_id="dup"), store)
    with pytest.raises(ValueError, match="already exists"):
        add_confirmation(_rec(confirmation_id="dup"), store)


def test_coverage_metric_on_fixture():
    # inventory = the current oracle_suspect residual ids
    inventory = ["fi:aaaa", "fi:bbbb", "fi:cccc", "fi:dddd"]
    confirmations = [
        # confirming: acknowledged, covers aaaa + bbbb
        _rec(confirmation_id="c1", keeper_response="acknowledged", affected_residual_ids=("fi:aaaa", "fi:bbbb")),
        # confirming: corrected, covers cccc (+ a dangling id not in inventory)
        _rec(
            confirmation_id="c2",
            keeper_response="corrected",
            correction_date="2026-07-05",
            affected_residual_ids=("fi:cccc", "fi:zzzz"),
        ),
        # non-confirming: rejected, references dddd -> stays pending
        _rec(confirmation_id="c3", keeper_response="rejected", affected_residual_ids=("fi:dddd",)),
    ]
    cov = compute_coverage(inventory, confirmations)
    assert isinstance(cov, ConfirmationCoverage)
    assert cov.inventory_residual_count == 4
    assert cov.externally_validated_count == 3
    assert cov.confirmed_residual_ids == ("fi:aaaa", "fi:bbbb", "fi:cccc")
    assert cov.pending_residual_ids == ("fi:dddd",)
    assert cov.dangling_residual_ids == ("fi:zzzz",)
    assert cov.confirmations_total == 3
    assert cov.confirmations_confirming == 2


def test_annotate_residuals_read_only_telemetry():
    inventory = ["fi:aaaa", "fi:cccc", "fi:unref"]
    confirmations = [
        _rec(confirmation_id="c1", keeper_response="acknowledged", affected_residual_ids=("fi:aaaa",)),
        _rec(confirmation_id="c2", source="uk", ticket="T-9", keeper_response="rejected", affected_residual_ids=("fi:cccc",)),
    ]
    annotated = annotate_residuals(inventory, confirmations)
    assert set(annotated) == {"fi:aaaa", "fi:cccc", "fi:unref"}
    assert annotated["fi:aaaa"]["externally_confirmed"] is True
    assert annotated["fi:aaaa"]["sources"] == ["finlex"]
    # rejected does not count as confirmed
    assert annotated["fi:cccc"]["externally_confirmed"] is False
    assert annotated["fi:cccc"]["keeper_responses"] == ["rejected"]
    # unreferenced inventory id -> no confirmation
    assert annotated["fi:unref"]["externally_confirmed"] is False
    assert annotated["fi:unref"]["keeper_responses"] == []


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"keeper_response": "maybe"}, "keeper_response must be one of"),
        ({"submitted_date": "2026/07/01"}, "must be YYYY-MM-DD"),
        ({"submitted_date": "2026-13-01"}, "not a valid date"),
        ({"submitted_date": ""}, "submitted_date is required"),
        ({"affected_residual_ids": ()}, "affected_residual_ids is required"),
        ({"affected_residual_ids": "fi:aaaa"}, "not a bare string"),
        ({"source": "  "}, "source is required"),
        ({"ticket": ""}, "ticket is required"),
        ({"confirmation_id": ""}, "confirmation_id is required"),
        (
            {"keeper_response": "corrected"},  # missing correction_date
            "requires a correction_date",
        ),
        (
            {"correction_date": "2026-07-05"},  # ack, but has correction_date
            "only valid when keeper_response is 'corrected'",
        ),
    ],
)
def test_rejects_malformed_records(overrides, match):
    with pytest.raises(ValueError, match=match):
        _rec(**overrides)


def test_from_dict_rejects_unknown_field():
    row = _rec().to_dict()
    row["bogus"] = "x"
    with pytest.raises(ValueError, match="unknown field"):
        OracleDefectExternalConfirmation.from_dict(row)


def test_load_rejects_duplicate_id_in_store(tmp_path: Path):
    store = tmp_path / "confirmations.json"
    payload = {
        "_meta": {},
        "confirmations": [
            _rec(confirmation_id="dup").to_dict(),
            _rec(confirmation_id="dup", source="uk", ticket="T-2").to_dict(),
        ],
    }
    store.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate confirmation_id"):
        load_confirmations(store)


def test_load_missing_store_returns_empty(tmp_path: Path):
    assert load_confirmations(tmp_path / "does_not_exist.json") == ()


def test_shipped_store_parses_and_is_confirming_vocab_stable():
    # The shipped store must parse and carry the schema tag.
    raw = json.loads(_store_path().read_text(encoding="utf-8"))
    assert raw["_meta"]["schema"] == SCHEMA
    assert sorted(CONFIRMING_RESPONSES) == raw["_meta"]["confirming_responses"]
    # load path must not raise on the shipped file
    load_confirmations()
