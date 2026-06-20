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
