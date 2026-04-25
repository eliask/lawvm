from __future__ import annotations

from pathlib import Path
import importlib.util
import sys

import pytest


def _load_reference_model():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "tests" / "fixtures" / "composite_interaction_reference_model.py"
    if not module_path.exists():
        pytest.skip("archived composite interaction reference model is not present")
    spec = importlib.util.spec_from_file_location("composite_interaction_reference_model", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("Unable to load composite interaction reference model")
    module = importlib.util.module_from_spec(spec)
    sys.modules["composite_interaction_reference_model"] = module
    spec.loader.exec_module(module)
    return module


def test_relabel_expansion_updates_identity_address_occupancy_and_lineage() -> None:
    model_mod = _load_reference_model()
    model = model_mod.CompositeInteractionModel.seed({"section:5": "P5"})
    model.begin_act("A1")

    micro_ops = model.expand_relabel(source_address="section:5", destination_address="section:7")
    model.apply_micro_ops(micro_ops)

    assert [type(op).__name__ for op in micro_ops] == [
        "VacateSlot",
        "OccupySlot",
        "SetDisplayAddress",
        "RecordLineage",
    ]
    assert model.slot("section:5").occupancy is model_mod.SlotOccupancy.TOMBSTONE
    assert model.slot("section:7") == model_mod.SlotState(
        occupancy=model_mod.SlotOccupancy.SUBSTANTIVE,
        provision_id="P5",
    )
    assert model.display_address("P5") == "section:7"
    assert model.lineage_for("P5") == (
        model_mod.LineageEvent(
            kind=model_mod.LineageKind.RELABEL,
            provision_id="P5",
            from_address="section:5",
            to_address="section:7",
            act_id="A1",
        ),
    )


def test_same_act_frames_distinguish_old_and_new_same_label() -> None:
    model_mod = _load_reference_model()
    model = model_mod.CompositeInteractionModel.seed({"section:5": "P5"})
    model.begin_act("A2")

    model.apply_micro_ops(
        model.expand_relabel(source_address="section:5", destination_address="section:7")
    )
    model.insert_new(address="section:5", provision_id="P_new")

    assert model.resolve_address("section:5", model_mod.ReferenceFrame.ACT_START) == "P5"
    assert model.resolve_address("section:5", model_mod.ReferenceFrame.WORKING) == "P_new"
    assert model.display_address("P5") == "section:7"
    assert model.display_address("P_new") == "section:5"


def test_relabel_rejects_destination_with_different_live_provision() -> None:
    model_mod = _load_reference_model()
    model = model_mod.CompositeInteractionModel.seed(
        {
            "section:5": "P5",
            "section:7": "P7",
        }
    )
    model.begin_act("A3")

    with pytest.raises(model_mod.CompositeModelError):
        model.expand_relabel(source_address="section:5", destination_address="section:7")
