"""Rank-23 conservation: a missing amendment source surfaces a residual.

``_read_amendment_candidates`` must not silently shorten the replay plan when
``corpus.read_source`` returns no bytes for a child amendment. The drop is
recorded as a typed ``AmendmentSourcePathology`` (mirroring amendment_index's
``fi_amendment_index_source_vts_artifact_missing`` for the identical condition),
and the SELECTED candidate set is unchanged for the candidates that DO have a
source.
"""
from __future__ import annotations

from typing import cast

import lawvm.finland.amendment_selection as sel
from lawvm.corpus_store import CorpusStore


_PRESENT_XML = (
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b"<act><preface><docTitle>Laki esimerkista</docTitle></preface>"
    b"<body><section><num>1 \xc2\xa7</num></section></body></act>"
    b"</akomaNtoso>"
)


class _PartialCorpus:
    """Returns source bytes for one child amendment, None for the other."""

    def __init__(self, missing_id: str) -> None:
        self._missing_id = missing_id

    def read_source(self, amendment_id: str) -> bytes | None:
        if amendment_id == self._missing_id:
            return None
        return _PRESENT_XML


def test_missing_source_surfaces_residual_selected_set_unchanged(monkeypatch) -> None:
    parent_id = "2000/1"
    present_id = "2001/10"
    missing_id = "2002/20"

    monkeypatch.setattr(
        sel,
        "amendment_children_by_parent",
        lambda: {parent_id: [present_id, missing_id]},
    )

    corpus = cast(CorpusStore, _PartialCorpus(missing_id=missing_id))
    candidates, residuals = sel._read_amendment_candidates(parent_id, corpus)

    # The present-source candidate is read normally; the missing one is NOT in
    # the candidate (selected) set...
    assert [c.amendment_id for c in candidates] == [present_id]

    # ...but it is conserved as a typed source-pathology residual rather than a
    # silent ``continue``.
    assert len(residuals) == 1
    residual = residuals[0]
    assert residual.amendment_id == missing_id
    assert residual.family == "source_pathology"
    assert residual.phase == "acquisition"
    assert residual.rule_id == "fi_amendment_selection_source_artifact_missing"


def test_all_sources_present_yields_no_residual(monkeypatch) -> None:
    parent_id = "2000/2"
    monkeypatch.setattr(
        sel,
        "amendment_children_by_parent",
        lambda: {parent_id: ["2001/11"]},
    )

    class _FullCorpus:
        def read_source(self, amendment_id: str) -> bytes | None:
            return _PRESENT_XML

    candidates, residuals = sel._read_amendment_candidates(
        parent_id, cast(CorpusStore, _FullCorpus())
    )

    assert [c.amendment_id for c in candidates] == ["2001/11"]
    assert residuals == ()


def test_tuple_adapter_carries_residuals_through_residuals_out(monkeypatch) -> None:
    """The live replay path uses the backward tuple adapter; it must NOT drop the
    selection residuals. With a ``residuals_out`` sink supplied (as the replay
    pipeline does) a missing amendment source surfaces THROUGH the adapter while
    the selected record set is unchanged."""
    parent_id = "2000/3"
    present_id = "2001/12"
    missing_id = "2002/22"

    monkeypatch.setattr(
        sel,
        "amendment_children_by_parent",
        lambda: {parent_id: [present_id, missing_id]},
    )
    # Pin the oracle context so selection is corpus-source driven only.
    monkeypatch.setattr(sel, "get_consolidated_meta", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(
        sel,
        "get_consolidated_oracle_reflected_source_vts_children",
        lambda *_a, **_k: (),
    )

    corpus = cast(CorpusStore, _PartialCorpus(missing_id=missing_id))
    residuals_out: list[sel.AmendmentSourcePathology] = []
    records, _cutoff, _oracle_version = sel.resolve_applicable_amendment_records(
        parent_id,
        "legal_pit",
        corpus=corpus,
        residuals_out=residuals_out,
    )

    # Selected set: only the candidate with a present source.
    assert [str(rec["statute_id"]) for rec in records] == [present_id]
    # Residual reaches the caller THROUGH the adapter (not dropped).
    assert [r.amendment_id for r in residuals_out] == [missing_id]
    assert residuals_out[0].rule_id == "fi_amendment_selection_source_artifact_missing"

    # And without a sink the tuple shape/selected set are exactly as before.
    records_no_sink, _c2, _o2 = sel.resolve_applicable_amendment_records(
        parent_id,
        "legal_pit",
        corpus=corpus,
    )
    assert [str(rec["statute_id"]) for rec in records_no_sink] == [present_id]


def test_build_amendment_selection_source_pathologies_projects_to_sink() -> None:
    """The residuals are convertible to the SourcePathology carrier the replay
    pipeline already surfaces (warnings/meta/strict findings)."""
    from lawvm.core.compile_result import SourcePathology
    from lawvm.finland.replay_pipeline import (
        build_amendment_selection_source_pathologies,
    )

    residual = sel.AmendmentSourcePathology(
        rule_id="fi_amendment_selection_source_artifact_missing",
        family="source_pathology",
        phase="acquisition",
        reason="missing source bytes",
        amendment_id="2002/22",
    )
    pathologies = build_amendment_selection_source_pathologies(
        (residual,), parent_id="2000/3"
    )
    assert len(pathologies) == 1
    pathology = pathologies[0]
    assert isinstance(pathology, SourcePathology)
    assert pathology.code == "fi_amendment_selection_source_artifact_missing"
    assert pathology.source_statute == "2000/3"
    assert pathology.detail["amendment_id"] == "2002/22"
    assert pathology.detail["family"] == "source_pathology"


def test_prepare_replay_plan_carries_amendment_selection_residuals() -> None:
    """End-to-end through the replay pipeline boundary: a resolver that reports a
    missing-source residual via ``residuals_out`` lands it on the ReplayPlan, so
    the entrypoint can project it onto the production source-pathology ledger.
    The selected amendment records are unchanged."""
    from types import SimpleNamespace

    from lawvm.finland.replay_pipeline import prepare_replay_plan

    base_xml = _PRESENT_XML

    class _BaseCorpus:
        def read_source(self, statute_id: str) -> bytes | None:
            return base_xml

        def load_spine_base_ir(self, sid: str, base_ir: object, base_xml_bytes: bytes) -> None:
            return None

    residual = sel.AmendmentSourcePathology(
        rule_id="fi_amendment_selection_source_artifact_missing",
        family="source_pathology",
        phase="acquisition",
        reason="missing source bytes",
        amendment_id="2002/22",
    )

    def resolver(_sid, _mode, corpus=None, residuals_out=None):
        if residuals_out is not None:
            residuals_out.append(residual)
        return ([{"sequence": 1, "statute_id": "2001/12", "included": True}], None, "")

    plan = prepare_replay_plan(
        "2000/3",
        mode="legal_pit",
        strict_profile=None,
        corpus=cast(CorpusStore, _BaseCorpus()),
        stop_before="",
        label_postprocessor=lambda _sid, label: label,
        get_replay_profile=lambda _mode: SimpleNamespace(normalize_replay_text=False),
        resolve_applicable_amendment_records=resolver,
        get_consolidated_oracle_suspect=lambda _sid, corpus=None: None,
        extract_inline_corrections=lambda xml_bytes, _sid: ([], xml_bytes),
    )

    # Residual reached the plan (the production-visible carrier the entrypoint
    # projects into signals.source_pathologies).
    assert [r.amendment_id for r in plan.amendment_selection_residuals] == ["2002/22"]
    # Selected records unchanged.
    assert [str(rec["statute_id"]) for rec in plan.amendment_records] == ["2001/12"]
