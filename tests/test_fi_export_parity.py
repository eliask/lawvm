"""Stage-3 export parity gate: the graph writer == the extractor oracle.

Pro r5 Phase 3 Stage 3. Phase 3b proved that the Legal Surface Graph round-trips
to fi_refs rows (``tests/test_fi_graph_parity.py``). Stage 3 makes the graph the
SOURCE OF TRUTH for the production fi_refs export: ``export_fi_refs`` now projects
each statute via :func:`export_fi_refs._project_refs_for_statute_via_graph` by
default, keeping :func:`export_fi_refs._project_refs_for_statute_via_extractor`
reachable only as the parity oracle.

This gate asserts that the two PRODUCTION projector functions — the graph writer
(default) and the extractor oracle — emit the SAME augmented fi_refs rows
(including the Slice-3 provenance columns ``_augment_row`` adds), as an
order-insensitive MULTISET, field-for-field, over:

  * several synthetic statutes (varied lanes), and
  * >=4 real Finlex statutes when the canonical corpus is available
    (``LAWVM_CANONICAL_DATA_ROOT``).

FAIL-LOUD: the comparison is the FULL augmented row (every field both projectors
emit), so any divergence — a dropped mention, a wrong field, a missing provenance
column — is caught. The comparison is NOT narrowed to force green.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Sequence, cast

import pytest

from lawvm.core.manual_claims.primitive import _ProfileTagDeprecated as ProfileTag
from lawvm.core.manual_claims.primitive import (
    ClaimStatus,
    ReviewStatus,
    ValidatorStatus,
)
from lawvm.tools import export_fi_refs as export_fi_refs_module
from lawvm.tools.export_fi_refs import (
    FinlexRepealedByCandidateProjectionRow,
    FinlexRepealedByCandidateStatus,
    FinlexRepealedByPromotionStatus,
    ReferenceSuccessorFrontierReasonCode,
    ReferenceSuccessorPromotionClaim,
    ReferenceSuccessorPromotionRejectionCode,
    RejectedRepealedByCandidatePromotion,
    export_fi_reference_successors,
    export_fi_reference_successors_from_promoted_candidates,
    export_fi_repealed_by_candidates,
    load_reference_successor_edges,
    load_reference_successor_promotion_claims,
    promote_repealed_by_candidates_to_successor_edges,
    project_reference_successor_frontier_rows,
    _project_repealed_by_candidate_for_statute,
    _project_reference_successors_for_statute_via_graph,
    _project_refs_for_statute_via_extractor,
    _project_refs_for_statute_via_graph,
)
from lawvm.finland.legal_surface.projection import (
    ReferenceSuccessorChainWitness,
    ReferenceSuccessorProjectionRow,
)
from lawvm.finland.references.resolve import (
    StatuteSuccessorEdge,
    SuccessorReferenceReasonCode,
    SuccessorReferenceResolutionBasis,
    SuccessorReferenceStatus,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_PROFILE = ProfileTag.DETERMINISTIC_ONLY
_CORPUS_SUCCESSOR_PROMOTION_CLAIMS = Path(
    "data/finland/reference_successor_promotion_claims_fi.jsonl"
)


def _assert_dict_span_slices_to_text(
    row: dict[str, Any],
    *,
    xml_bytes: bytes,
    expected_text: str,
) -> None:
    """Assert a public artifact row carries a usable byte-span witness."""
    offset = row["source_span_byte_offset"]
    length = row["source_span_len"]
    assert row["source_span_file"]
    assert isinstance(offset, int)
    assert isinstance(length, int)
    assert length > 0
    assert xml_bytes[offset : offset + length] == expected_text.encode("utf-8")


def _assert_successor_projection_span_slices_to_text(
    row: ReferenceSuccessorProjectionRow,
    *,
    xml_bytes: bytes,
    expected_text: str,
) -> None:
    """Assert the typed successor projection carries a usable byte-span witness."""
    assert row.source_span_file
    assert row.source_span_byte_offset is not None
    assert row.source_span_len is not None
    assert row.source_span_len > 0
    assert (
        xml_bytes[
            row.source_span_byte_offset : row.source_span_byte_offset
            + row.source_span_len
        ]
        == expected_text.encode("utf-8")
    )


# ── Synthetic statute fixtures (mirror the lanes in test_fi_graph_parity) ──────

# Plain-text id cite + internal § ref + a same-statute § ref.
_XML_PLAIN_AND_INTERNAL = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Tata lakia sovelletaan ymparistonsuojelulain (527/2014) 5 §:ssa tarkoitettuun toimintaan.</p>
    <p>Edella 1 momentissa tarkoitettuun toimintaan sovelletaan myos 5 §:n saannoksia.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

# AKN <ref> element cites (carry surface_text) + an internal § ref.
_XML_REF_ELEMENTS = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Sovelletaan <ref href="/akn/fi/act/statute/2014/527/sec_5">5 §:aa</ref> mukaisesti.</p>
    <p>Lisaksi noudatetaan <ref href="/akn/fi/act/statute/2011/379/sec_3">3 §:aa</ref>.</p>
    <p>Edella 1 momentissa tarkoitetaan 2 §:ssa saadettya.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

# A vague (OPEN, targetless) catch-all + a cross-statute by-name-ish cite.
_XML_VAGUE = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Jollei muussa laissa toisin saadeta, sovelletaan tata lakia.</p>
    <p>Sovelletaan myos tieliikennelain (729/2018) 12 §:aa.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

_XML_RADIATION_SUCCESSOR = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_3"><num>3 §</num><content>
    <p>Tätä lakia ei sovelleta säteilylaissa (592/1991) tarkoitettuun toimintaan.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")

_FINLEX_NS = "http://data.finlex.fi/schema/finlex"

_XML_REPEALED_BY_CANDIDATE = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}" xmlns:finlex="{_FINLEX_NS}">
  <act><meta>
    <finlex:repealedBy><finlex:statuteReference>
      <finlex:ref href="/akn/fi/act/statute-consolidated/2018/859">859/2018</finlex:ref>
      <finlex:inForce><finlex:dateEntryIntoForce date="2018-12-15"/></finlex:inForce>
    </finlex:statuteReference></finlex:repealedBy>
  </meta></act>
</akomaNtoso>
""".encode("utf-8")

_SYNTHETIC_CASES = [
    ("123/2020", _XML_PLAIN_AND_INTERNAL),
    ("200/2019", _XML_REF_ELEMENTS),
    ("300/2021", _XML_VAGUE),
]


# ── In-memory store stub (the projectors only call store.read_oracle) ─────────


class _DictStore:
    """Minimal store: the projectors only call ``read_oracle(statute_id)``."""

    def __init__(self, mapping: Dict[str, bytes]) -> None:
        self._mapping = mapping

    def read_oracle(self, statute_id: str) -> bytes:
        return self._mapping[statute_id]


# ── Parity key (the FULL augmented row both projectors emit) ──────────────────


def _row_key(row: Dict[str, Any]) -> tuple[tuple[str, object], ...]:
    """Order-insensitive, field-for-field key over the WHOLE augmented row.

    Sorting the items makes the key independent of dict insertion order while
    still covering every field (the deterministic extractor and the graph
    projector both run rows through ``_augment_row``, so the provenance columns
    are in scope too). Nothing is dropped from the comparison.
    """
    return tuple(sorted(row.items(), key=lambda kv: kv[0]))


def _multiset(rows: List[Dict[str, Any]]) -> Counter:
    return Counter(_row_key(r) for r in rows)


def _assert_parity(statute_id: str, store: Any) -> int:
    """Assert graph-projector rows == extractor-projector rows. Returns row count."""
    extractor_rows, _ = _project_refs_for_statute_via_extractor(
        statute_id, store, _PROFILE
    )
    graph_rows, _ = _project_refs_for_statute_via_graph(statute_id, store, _PROFILE)

    # Cardinality identity first (a dropped/extra mention is the loudest failure).
    assert len(graph_rows) == len(extractor_rows), (
        f"{statute_id}: row cardinality diverged "
        f"(graph {len(graph_rows)} vs extractor {len(extractor_rows)})"
    )

    expected = _multiset(extractor_rows)
    actual = _multiset(graph_rows)
    assert actual == expected, (
        f"{statute_id}: graph writer vs extractor oracle FULL-row parity diverged.\n"
        f"  only in extractor: {sorted(map(str, (expected - actual).elements()))[:5]}\n"
        f"  only in graph:     {sorted(map(str, (actual - expected).elements()))[:5]}"
    )
    return len(graph_rows)


# ── Synthetic parity ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("statute_id,xml_bytes", _SYNTHETIC_CASES)
def test_export_parity_synthetic(statute_id: str, xml_bytes: bytes) -> None:
    """Default graph writer reproduces the extractor oracle on synthetic lanes."""
    store = _DictStore({statute_id: xml_bytes})
    n = _assert_parity(statute_id, store)
    assert n > 0, f"{statute_id}: expected mentions in this fixture"


def test_default_writer_uses_graph() -> None:
    """The default ``_project_refs_for_statute`` IS the graph projector.

    Stage 3 contract: the production writer reads the graph. We assert that the
    default entry point produces exactly what the graph projector produces (not
    merely something parity-equal — the default dispatches to the graph path).
    """
    from lawvm.tools.export_fi_refs import _project_refs_for_statute

    statute_id, xml_bytes = _SYNTHETIC_CASES[1]
    store = _DictStore({statute_id: xml_bytes})

    default_rows, _ = _project_refs_for_statute(statute_id, store, _PROFILE)
    graph_rows, _ = _project_refs_for_statute_via_graph(statute_id, store, _PROFILE)
    assert _multiset(default_rows) == _multiset(graph_rows)
    assert default_rows  # non-empty fixture


def test_successor_projection_is_export_sibling_not_fi_refs_rewrite() -> None:
    """Exporter exposes B5 successor rows without changing literal fi_refs rows."""
    statute_id = "527/2014"
    store = _DictStore({statute_id: _XML_RADIATION_SUCCESSOR})
    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )

    successor_rows = _project_reference_successors_for_statute_via_graph(
        statute_id,
        store,
        successor_edges=(edge,),
        successor_as_of=date(2026, 1, 1),
    )
    assert len(successor_rows) == 1
    successor_row = successor_rows[0]
    assert successor_row.source_work_id == "527/2014"
    assert successor_row.source_provision_ref_str == "527/2014"
    _assert_successor_projection_span_slices_to_text(
        successor_row,
        xml_bytes=_XML_RADIATION_SUCCESSOR,
        expected_text="säteilylaissa (592/1991)",
    )
    assert successor_row.surface_text == "säteilylaissa (592/1991)"
    assert successor_row.literal_work_id == "1991/592"
    assert successor_row.operative_work_id == "859/2018"
    assert successor_row.successor_as_of == "2026-01-01"
    assert successor_row.successor_status is SuccessorReferenceStatus.RESOLVED
    assert (
        successor_row.successor_resolution_basis
        is SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN
    )
    assert successor_row.successor_candidates == ("859/2018",)
    assert successor_row.successor_rejected_candidates == ()
    assert (
        successor_row.successor_reason_code
        is SuccessorReferenceReasonCode.UNIQUE_WITNESSED_SUCCESSOR_CHAIN
    )
    assert successor_row.successor_chain == (
        ReferenceSuccessorChainWitness(
            predecessor_work_id="1991/592",
            successor_work_id="859/2018",
            effective_from=date(2018, 12, 15),
            witness_id="finlex:1991/592:repealed-by:859/2018",
            witness_text="Tämä laki on kumottu lailla 859/2018.",
            rule_id="fi.reference_successor.witnessed_edge",
        ),
    )

    fi_refs_rows, _ = _project_refs_for_statute_via_graph(statute_id, store, _PROFILE)
    assert len(fi_refs_rows) == 1
    assert fi_refs_rows[0]["target_statute_id"] == "1991/592"
    assert fi_refs_rows[0]["target_provision_ref_str"] == "1991/592"


def test_export_reference_successors_writes_separate_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The corpus writer emits successor rows without writing legacy fi_refs."""
    statute_id = "527/2014"
    store = _DictStore({statute_id: _XML_RADIATION_SUCCESSOR})
    monkeypatch.setattr(export_fi_refs_module, "_load_corpus_store", lambda: store)
    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )

    count = export_fi_reference_successors(
        [(1, statute_id)],
        successor_edges=(edge,),
        successor_as_of="2026-01-01",
        data_dir=str(tmp_path),
        use_parquet=False,
    )

    assert count == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "fi_reference_successors.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["source_work_id"] == "527/2014"
    assert row["source_provision_ref_str"] == "527/2014"
    _assert_dict_span_slices_to_text(
        row,
        xml_bytes=_XML_RADIATION_SUCCESSOR,
        expected_text="säteilylaissa (592/1991)",
    )
    assert row["surface_text"] == "säteilylaissa (592/1991)"
    assert row["literal_work_id"] == "1991/592"
    assert row["operative_work_id"] == "859/2018"
    assert row["successor_as_of"] == "2026-01-01"
    assert row["successor_status"] == SuccessorReferenceStatus.RESOLVED.value
    assert (
        row["successor_resolution_basis"]
        == SuccessorReferenceResolutionBasis.SUCCESSOR_CHAIN.value
    )
    assert row["successor_candidates"] == ["859/2018"]
    assert row["successor_rejected_candidates"] == []
    assert (
        row["successor_reason_code"]
        == SuccessorReferenceReasonCode.UNIQUE_WITNESSED_SUCCESSOR_CHAIN.value
    )
    assert row["successor_chain"] == [
        {
            "predecessor_work_id": "1991/592",
            "successor_work_id": "859/2018",
            "effective_from": "2018-12-15",
            "witness_id": "finlex:1991/592:repealed-by:859/2018",
            "witness_text": "Tämä laki on kumottu lailla 859/2018.",
            "rule_id": "fi.reference_successor.witnessed_edge",
        }
    ]
    assert not (tmp_path / "fi_refs.jsonl").exists()
    assert not (tmp_path / "fi_refs__deterministic_only.jsonl").exists()


def test_repealed_by_candidates_are_evidence_not_successor_edges() -> None:
    """Finlex repealedBy metadata projects as a non-authorizing candidate row."""
    statute_id = "1991/592"
    store = _DictStore({statute_id: _XML_REPEALED_BY_CANDIDATE})

    row = _project_repealed_by_candidate_for_statute(statute_id, store)

    assert row is not None
    assert row.predecessor_work_id == "1991/592"
    assert row.repealing_work_id == "2018/859"
    assert row.effective_from == "2018-12-15"
    assert row.witness_href == "/akn/fi/act/statute-consolidated/2018/859"
    assert row.witness_text == "859/2018"
    assert row.rule_id == "fi.finlex.repealed_by_candidate"
    assert row.candidate_status is FinlexRepealedByCandidateStatus.CANDIDATE
    assert row.promotion_status is FinlexRepealedByPromotionStatus.NOT_PROMOTED
    assert row.replay_authorized is False


def test_repealed_by_candidate_statuses_are_typed() -> None:
    """Candidate ledger statuses are not stringly semantic fields."""
    with pytest.raises(TypeError, match="candidate_status"):
        FinlexRepealedByCandidateProjectionRow(
            predecessor_work_id="1991/592",
            repealing_work_id="2018/859",
            effective_from="2018-12-15",
            witness_href="/akn/fi/act/statute-consolidated/2018/859",
            witness_text="859/2018",
            rule_id="fi.finlex.repealed_by_candidate",
            candidate_status=cast(Any, "candidate"),
        )

    with pytest.raises(TypeError, match="promotion_status"):
        FinlexRepealedByCandidateProjectionRow(
            predecessor_work_id="1991/592",
            repealing_work_id="2018/859",
            effective_from="2018-12-15",
            witness_href="/akn/fi/act/statute-consolidated/2018/859",
            witness_text="859/2018",
            rule_id="fi.finlex.repealed_by_candidate",
            promotion_status=cast(Any, "not_promoted"),
        )


def test_export_repealed_by_candidates_writes_separate_candidate_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corpus writer persists candidate witnesses without successor authority."""
    statute_id = "1991/592"
    store = _DictStore({statute_id: _XML_REPEALED_BY_CANDIDATE})
    monkeypatch.setattr(export_fi_refs_module, "_load_corpus_store", lambda: store)

    count = export_fi_repealed_by_candidates(
        [(0, statute_id)],
        data_dir=str(tmp_path),
        use_parquet=False,
    )

    assert count == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "fi_repealed_by_candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows == [
        {
            "predecessor_work_id": "1991/592",
            "repealing_work_id": "2018/859",
            "effective_from": "2018-12-15",
            "witness_href": "/akn/fi/act/statute-consolidated/2018/859",
            "witness_text": "859/2018",
            "rule_id": "fi.finlex.repealed_by_candidate",
            "candidate_status": "candidate",
            "promotion_status": "not_promoted",
            "replay_authorized": False,
        }
    ]
    assert not (tmp_path / "fi_reference_successors.jsonl").exists()
    assert not (tmp_path / "fi_refs.jsonl").exists()


def test_repealed_by_candidate_needs_explicit_promotion_claim() -> None:
    """Candidate metadata alone never becomes a successor edge."""
    candidate = FinlexRepealedByCandidateProjectionRow(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        witness_href="/akn/fi/act/statute-consolidated/2018/859",
        witness_text="859/2018",
        rule_id="fi.finlex.repealed_by_candidate",
    )

    result = promote_repealed_by_candidates_to_successor_edges([candidate], [])

    assert result.accepted_edges == ()
    assert len(result.rejected_candidates) == 1
    assert result.rejected_claims == ()
    rejection = result.rejected_candidates[0]
    assert rejection.candidate == candidate
    assert (
        rejection.reason_code
        is ReferenceSuccessorPromotionRejectionCode.MISSING_PROMOTION_CLAIM
    )
    assert rejection.blocking is True


def test_explicit_promotion_claim_mints_successor_edge() -> None:
    """An exact reviewed claim promotes one candidate into an explicit edge."""
    candidate = FinlexRepealedByCandidateProjectionRow(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        witness_href="/akn/fi/act/statute-consolidated/2018/859",
        witness_text="859/2018",
        rule_id="fi.finlex.repealed_by_candidate",
    )
    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:successor:1991/592:2018/859",
        promotion_witness_text=(
            "Manual review: 859/2018 replaces the repealed Säteilylaki 592/1991."
        ),
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )

    result = promote_repealed_by_candidates_to_successor_edges([candidate], [claim])

    assert result.rejected_candidates == ()
    assert result.rejected_claims == ()
    assert result.accepted_edges == (
        StatuteSuccessorEdge(
            predecessor_work_id="1991/592",
            successor_work_id="2018/859",
            effective_from=date(2018, 12, 15),
            witness_id="manual-review:successor:1991/592:2018/859",
            witness_text=(
                "Manual review: 859/2018 replaces the repealed Säteilylaki 592/1991."
            ),
            rule_id="fi.reference_successor.promoted_repealed_by_candidate",
        ),
    )


def test_reference_successor_frontier_rows_account_for_unpromoted_candidate() -> None:
    """Literal old-id references with only candidate evidence get a frontier row."""
    candidate = FinlexRepealedByCandidateProjectionRow(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        witness_href="/akn/fi/act/statute-consolidated/2018/859",
        witness_text="859/2018",
        rule_id="fi.finlex.repealed_by_candidate",
    )
    rejection = RejectedRepealedByCandidatePromotion(
        candidate=candidate,
        reason_code=ReferenceSuccessorPromotionRejectionCode.MISSING_PROMOTION_CLAIM,
        reason="No explicit promotion claim matched this candidate.",
    )

    rows = project_reference_successor_frontier_rows(
        [
            {
                "source_statute_id": "527/2014",
                "source_provision_ref_str": "section:3",
                "target_statute_id": "1991/592",
                "phrase_lemma": "säteilylaissa (592/1991)",
            }
        ],
        candidates=(candidate,),
        accepted_edges=(),
        candidate_rejections=(rejection,),
        successor_as_of=date(2026, 1, 1),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.source_work_id == "527/2014"
    assert row.literal_work_id == "1991/592"
    assert row.source_span_file == ""
    assert row.source_span_byte_offset is None
    assert row.source_span_len is None
    assert row.candidate_repealing_work_ids == ("2018/859",)
    assert row.candidate_promotion_rejection_codes == (
        ReferenceSuccessorPromotionRejectionCode.MISSING_PROMOTION_CLAIM,
    )
    assert row.reason_code is (
        ReferenceSuccessorFrontierReasonCode.MISSING_PROMOTED_SUCCESSOR_CLAIM
    )
    assert row.replay_authorized is False

    accepted = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="2018/859",
        effective_from=date(2018, 12, 15),
        witness_id="manual-review:successor:1991/592:2018/859",
        witness_text="Reviewed successor relation.",
        rule_id="fi.reference_successor.promoted_repealed_by_candidate",
    )
    assert (
        project_reference_successor_frontier_rows(
            [
                {
                    "source_statute_id": "527/2014",
                    "source_provision_ref_str": "section:3",
                    "target_statute_id": "1991/592",
                    "phrase_lemma": "säteilylaissa (592/1991)",
                }
            ],
            candidates=(candidate,),
            accepted_edges=(accepted,),
            candidate_rejections=(rejection,),
            successor_as_of=date(2026, 1, 1),
        )
        == ()
    )


def test_duplicate_promotion_claims_reject_candidate() -> None:
    """Same candidate plus multiple claims is ambiguity, not last-wins."""
    candidate = FinlexRepealedByCandidateProjectionRow(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        witness_href=None,
        witness_text="859/2018",
        rule_id="fi.finlex.repealed_by_candidate",
    )
    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:1",
        promotion_witness_text="reviewed",
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )

    result = promote_repealed_by_candidates_to_successor_edges(
        [candidate], [claim, claim]
    )

    assert result.accepted_edges == ()
    assert len(result.rejected_candidates) == 1
    assert result.rejected_claims == ()
    assert (
        result.rejected_candidates[0].reason_code
        is ReferenceSuccessorPromotionRejectionCode.AMBIGUOUS_PROMOTION_CLAIM
    )


def test_unverified_promotion_claim_rejects_candidate() -> None:
    """A matching claim still needs explicit verified_manual review status."""
    candidate = FinlexRepealedByCandidateProjectionRow(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        witness_href=None,
        witness_text="859/2018",
        rule_id="fi.finlex.repealed_by_candidate",
    )
    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:proposed",
        promotion_witness_text="not yet reviewed",
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )

    result = promote_repealed_by_candidates_to_successor_edges([candidate], [claim])

    assert result.accepted_edges == ()
    assert result.rejected_claims == ()
    assert len(result.rejected_candidates) == 1
    expected_reason = (
        ReferenceSuccessorPromotionRejectionCode.PROMOTION_CLAIM_NOT_VERIFIED_MANUAL
    )
    assert (
        result.rejected_candidates[0].reason_code
        is expected_reason
    )


def test_invalid_candidate_effective_from_is_typed_rejection() -> None:
    """Invalid candidate dates remain owned receipts, not parse fallbacks."""
    candidate = FinlexRepealedByCandidateProjectionRow(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="15.12.2018",
        witness_href=None,
        witness_text="859/2018",
        rule_id="fi.finlex.repealed_by_candidate",
    )
    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="15.12.2018",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:date",
        promotion_witness_text="reviewed",
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )

    result = promote_repealed_by_candidates_to_successor_edges([candidate], [claim])

    assert result.accepted_edges == ()
    assert result.rejected_claims == ()
    assert len(result.rejected_candidates) == 1
    expected_reason = (
        ReferenceSuccessorPromotionRejectionCode.INVALID_CANDIDATE_EFFECTIVE_FROM
    )
    assert (
        result.rejected_candidates[0].reason_code
        is expected_reason
    )


def test_unmatched_promotion_claim_is_rejected_receipt() -> None:
    """Promotion claims outside the candidate set are owned, not dropped."""
    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:orphan",
        promotion_witness_text="reviewed",
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )

    result = promote_repealed_by_candidates_to_successor_edges([], [claim])

    assert result.accepted_edges == ()
    assert result.rejected_candidates == ()
    assert len(result.rejected_claims) == 1
    assert result.rejected_claims[0].claim == claim
    assert (
        result.rejected_claims[0].reason_code
        is ReferenceSuccessorPromotionRejectionCode.PROMOTION_CLAIM_WITHOUT_CANDIDATE
    )
    assert result.rejected_claims[0].blocking is True


def test_load_reference_successor_promotion_claims_is_fail_loud(
    tmp_path: Path,
) -> None:
    """Promotion-claim input is typed JSONL, not derived from candidates."""
    path = tmp_path / "promotion_claims.jsonl"
    path.write_text(
        json.dumps(
            {
                "predecessor_work_id": "1991/592",
                "repealing_work_id": "2018/859",
                "effective_from": "2018-12-15",
                "candidate_rule_id": "fi.finlex.repealed_by_candidate",
                "promotion_witness_id": "manual-review:successor:1991/592:2018/859",
                "promotion_witness_text": "Reviewed successor relation.",
                "claim_status": "accepted",
                "review_status": "verified_manual",
                "validator_status": "migration_revalidated",
                "promotion_rule_id": (
                    "fi.reference_successor.promoted_repealed_by_candidate"
                ),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_reference_successor_promotion_claims(path) == [
        ReferenceSuccessorPromotionClaim(
            predecessor_work_id="1991/592",
            repealing_work_id="2018/859",
            effective_from="2018-12-15",
            candidate_rule_id="fi.finlex.repealed_by_candidate",
            promotion_witness_id="manual-review:successor:1991/592:2018/859",
            promotion_witness_text="Reviewed successor relation.",
            claim_status=ClaimStatus.ACCEPTED,
            review_status=ReviewStatus.VERIFIED_MANUAL,
            validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
            promotion_rule_id="fi.reference_successor.promoted_repealed_by_candidate",
        )
    ]

    bad = tmp_path / "bad_promotion_claims.jsonl"
    bad.write_text(
        json.dumps(
            {
                "predecessor_work_id": "1991/592",
                "repealing_work_id": "2018/859",
                "effective_from": "not-a-date",
                "candidate_rule_id": "fi.finlex.repealed_by_candidate",
                "promotion_witness_id": "w",
                "promotion_witness_text": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 1 field 'effective_from'"):
        load_reference_successor_promotion_claims(bad)

    bad_status = tmp_path / "bad_promotion_claim_status.jsonl"
    bad_status.write_text(
        json.dumps(
            {
                "predecessor_work_id": "1991/592",
                "repealing_work_id": "2018/859",
                "effective_from": "2018-12-15",
                "candidate_rule_id": "fi.finlex.repealed_by_candidate",
                "promotion_witness_id": "w",
                "promotion_witness_text": "x",
                "claim_status": "handwave",
                "review_status": "verified_manual",
                "validator_status": "migration_revalidated",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="field 'claim_status'"):
        load_reference_successor_promotion_claims(bad_status)


def test_corpus_radiation_successor_promotion_claim_promotes_reference_rows(
    tmp_path: Path,
) -> None:
    """The checked-in FI successor claim promotes a real corpus candidate.

    Corpus witness:
    * `1991/592` Finlex `repealedBy` metadata yields the candidate.
    * `2018/859` §200 states that references elsewhere to the 1991 Radiation Act
      are to be understood as references to the new act.
    * `2014/527` current text still literally cites `säteilylaissa (592/1991)`.
    """
    if not _CORPUS_SUCCESSOR_PROMOTION_CLAIMS.exists():
        pytest.skip("corpus successor promotion claim fixture is unavailable")
    claims = load_reference_successor_promotion_claims(
        _CORPUS_SUCCESSOR_PROMOTION_CLAIMS
    )
    matching_claims = [
        claim
        for claim in claims
        if (
            claim.predecessor_work_id,
            claim.repealing_work_id,
            claim.effective_from,
        )
        == ("1991/592", "2018/859", "2018-12-15")
    ]
    assert len(matching_claims) == 1
    claim = matching_claims[0]
    assert claim.claim_status is ClaimStatus.ACCEPTED
    assert claim.review_status is ReviewStatus.VERIFIED_MANUAL
    assert claim.validator_status is ValidatorStatus.MIGRATION_REVALIDATED
    assert "viittauksen on katsottava tarkoittavan tätä lakia" in (
        claim.promotion_witness_text
    )

    store = export_fi_refs_module._load_corpus_store()
    candidate = _project_repealed_by_candidate_for_statute("1991/592", store)
    if candidate is None:
        pytest.skip("canonical corpus lacks 1991/592 repealedBy candidate")
    assert candidate.predecessor_work_id == "1991/592"
    assert candidate.repealing_work_id == "2018/859"
    assert candidate.effective_from == "2018-12-15"

    promotion = promote_repealed_by_candidates_to_successor_edges(
        [candidate], matching_claims
    )
    assert promotion.rejected_candidates == ()
    assert promotion.rejected_claims == ()
    assert promotion.accepted_edges == (
        StatuteSuccessorEdge(
            predecessor_work_id="1991/592",
            successor_work_id="2018/859",
            effective_from=date(2018, 12, 15),
            witness_id="finlex:2018/859:section:200:reference-successor:1991/592",
            witness_text=claim.promotion_witness_text,
            rule_id="fi.reference_successor.promoted_repealed_by_candidate",
        ),
    )

    count = export_fi_reference_successors_from_promoted_candidates(
        [(0, "1991/592"), (1, "2014/527")],
        promotion_claims=matching_claims,
        successor_as_of="2026-01-01",
        data_dir=str(tmp_path),
        use_parquet=False,
    )
    assert count >= 1
    successor_rows = [
        json.loads(line)
        for line in (tmp_path / "fi_reference_successors.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        row["source_work_id"] == "2014/527"
        and row["surface_text"] == "(592/1991)"
        and row["literal_work_id"] == "1991/592"
        and row["operative_work_id"] == "2018/859"
        and row["successor_reason_code"]
        == SuccessorReferenceReasonCode.UNIQUE_WITNESSED_SUCCESSOR_CHAIN.value
        for row in successor_rows
    )


def test_export_reference_successors_from_promoted_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production path promotes only reviewed candidates, then resolves old cites."""
    store = _DictStore(
        {
            "1991/592": _XML_REPEALED_BY_CANDIDATE,
            "527/2014": _XML_RADIATION_SUCCESSOR,
        }
    )
    monkeypatch.setattr(export_fi_refs_module, "_load_corpus_store", lambda: store)
    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:successor:1991/592:2018/859",
        promotion_witness_text="Reviewed successor relation.",
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )

    count = export_fi_reference_successors_from_promoted_candidates(
        [(0, "1991/592"), (1, "527/2014")],
        promotion_claims=(claim,),
        successor_as_of="2026-01-01",
        data_dir=str(tmp_path),
        use_parquet=False,
    )

    assert count == 1
    successor_rows = [
        json.loads(line)
        for line in (tmp_path / "fi_reference_successors.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert successor_rows[0]["literal_work_id"] == "1991/592"
    assert successor_rows[0]["operative_work_id"] == "2018/859"
    assert successor_rows[0]["successor_chain"] == [
        {
            "predecessor_work_id": "1991/592",
            "successor_work_id": "2018/859",
            "effective_from": "2018-12-15",
            "witness_id": "manual-review:successor:1991/592:2018/859",
            "witness_text": "Reviewed successor relation.",
            "rule_id": "fi.reference_successor.promoted_repealed_by_candidate",
        }
    ]
    accepted_edges = [
        json.loads(line)
        for line in (tmp_path / "fi_reference_successor_edges_from_promotions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert accepted_edges == [
        {
            "predecessor_work_id": "1991/592",
            "successor_work_id": "2018/859",
            "effective_from": "2018-12-15",
            "witness_id": "manual-review:successor:1991/592:2018/859",
            "witness_text": "Reviewed successor relation.",
            "rule_id": "fi.reference_successor.promoted_repealed_by_candidate",
        }
    ]
    assert (
        tmp_path / "fi_repealed_by_candidate_promotion_rejections.jsonl"
    ).read_text(encoding="utf-8") == ""
    assert (
        tmp_path / "fi_reference_successor_promotion_claim_rejections.jsonl"
    ).read_text(encoding="utf-8") == ""
    assert (
        tmp_path / "fi_reference_successor_frontier.jsonl"
    ).read_text(encoding="utf-8") == ""


def test_export_promoted_candidates_writes_missing_promotion_frontier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejected candidate evidence owns literal old-id references as frontier rows."""
    store = _DictStore(
        {
            "1991/592": _XML_REPEALED_BY_CANDIDATE,
            "527/2014": _XML_RADIATION_SUCCESSOR,
        }
    )
    monkeypatch.setattr(export_fi_refs_module, "_load_corpus_store", lambda: store)

    count = export_fi_reference_successors_from_promoted_candidates(
        [(0, "1991/592"), (1, "527/2014")],
        promotion_claims=(),
        successor_as_of="2026-01-01",
        data_dir=str(tmp_path),
        use_parquet=False,
    )

    assert count == 0
    assert (tmp_path / "fi_reference_successors.jsonl").read_text(
        encoding="utf-8"
    ) == ""
    frontier_rows = [
        json.loads(line)
        for line in (tmp_path / "fi_reference_successor_frontier.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(frontier_rows) == 1
    row = frontier_rows[0]
    assert "operative_work_id" not in row
    assert row["source_work_id"] == "527/2014"
    assert row["source_provision_ref_str"] == "527/2014"
    _assert_dict_span_slices_to_text(
        row,
        xml_bytes=_XML_RADIATION_SUCCESSOR,
        expected_text="säteilylaissa (592/1991)",
    )
    assert row["surface_text"] == "citation_construction"
    assert row["literal_work_id"] == "1991/592"
    assert row["successor_as_of"] == "2026-01-01"
    assert row["candidate_repealing_work_ids"] == ["2018/859"]
    assert row["candidate_effective_from"] == ["2018-12-15"]
    assert row["candidate_rule_ids"] == ["fi.finlex.repealed_by_candidate"]
    assert row["candidate_promotion_rejection_codes"] == [
        ReferenceSuccessorPromotionRejectionCode.MISSING_PROMOTION_CLAIM.value
    ]
    assert (
        row["reason_code"]
        == ReferenceSuccessorFrontierReasonCode.MISSING_PROMOTED_SUCCESSOR_CLAIM.value
    )
    assert row["replay_authorized"] is False


def test_export_promoted_candidates_writes_unmatched_claim_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Promotion claims with no candidate remain visible as rejected receipts."""
    store = _DictStore({"527/2014": _XML_RADIATION_SUCCESSOR})
    monkeypatch.setattr(export_fi_refs_module, "_load_corpus_store", lambda: store)
    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:orphan",
        promotion_witness_text="reviewed",
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )

    count = export_fi_reference_successors_from_promoted_candidates(
        [(1, "527/2014")],
        promotion_claims=(claim,),
        successor_as_of="2026-01-01",
        data_dir=str(tmp_path),
        use_parquet=False,
    )

    assert count == 0
    assert (
        tmp_path / "fi_reference_successor_edges_from_promotions.jsonl"
    ).read_text(encoding="utf-8") == ""
    claim_rejections = [
        json.loads(line)
        for line in (
            tmp_path / "fi_reference_successor_promotion_claim_rejections.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert claim_rejections == [
        {
            "claim": {
                "predecessor_work_id": "1991/592",
                "repealing_work_id": "2018/859",
                "effective_from": "2018-12-15",
                "candidate_rule_id": "fi.finlex.repealed_by_candidate",
                "promotion_witness_id": "manual-review:orphan",
                "promotion_witness_text": "reviewed",
                "claim_status": "accepted",
                "review_status": "verified_manual",
                "validator_status": "migration_revalidated",
                "promotion_rule_id": (
                    "fi.reference_successor.promoted_repealed_by_candidate"
                ),
            },
            "reason_code": "promotion_claim_without_candidate",
            "reason": (
                "Promotion claim did not match any Finlex repealedBy "
                "candidate in the corpus slice."
            ),
            "blocking": True,
        }
    ]
    assert (
        tmp_path / "fi_repealed_by_candidate_promotion_rejections.jsonl"
    ).read_text(encoding="utf-8") == ""


def test_load_reference_successor_edges_is_explicit_and_fail_loud(
    tmp_path: Path,
) -> None:
    """Successor edge input is typed JSONL, not inferred from lifecycle text."""
    path = tmp_path / "successor_edges.jsonl"
    path.write_text(
        json.dumps(
            {
                "predecessor_work_id": "1991/592",
                "successor_work_id": "859/2018",
                "effective_from": "2018-12-15",
                "witness_id": "finlex:1991/592:repealed-by:859/2018",
                "witness_text": "Tämä laki on kumottu lailla 859/2018.",
                "rule_id": "fi.reference_successor.witnessed_edge",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_reference_successor_edges(path) == [
        StatuteSuccessorEdge(
            predecessor_work_id="1991/592",
            successor_work_id="859/2018",
            effective_from=date(2018, 12, 15),
            witness_id="finlex:1991/592:repealed-by:859/2018",
            witness_text="Tämä laki on kumottu lailla 859/2018.",
            rule_id="fi.reference_successor.witnessed_edge",
        )
    ]

    bad = tmp_path / "bad_successor_edges.jsonl"
    bad.write_text(
        json.dumps(
            {
                "predecessor_work_id": "1991/592",
                "effective_from": "not-a-date",
                "witness_id": "w",
                "witness_text": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 1 field 'effective_from'"):
        load_reference_successor_edges(bad)


def test_export_projections_dispatches_reference_successor_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production export dispatcher wires explicit edge input to B5 export."""
    from lawvm.tools import export_parquet

    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )
    calls: list[tuple[tuple[StatuteSuccessorEdge, ...], str]] = []

    def _project_worker_stub(item: tuple[int, str, str]):
        _count, sid, _mode = item
        return (
            sid,
            {"statute": [], "sections": [], "findings": [], "ops": []},
            0.0,
        )

    def _load_edges_stub(path: str | Path) -> list[StatuteSuccessorEdge]:
        assert Path(path).name == "edges.jsonl"
        return [edge]

    def _export_successors_stub(
        corpus: list[tuple[int, str]],
        *,
        successor_edges: Sequence[StatuteSuccessorEdge],
        successor_as_of: date | str,
        data_dir: str = ".tmp/projections",
        use_parquet: bool = True,
        limit: int | None = None,
        statute_registry: object | None = None,
        compile_metadata: object | None = None,
    ) -> int:
        assert corpus == [(0, "527/2014")]
        assert data_dir == str(tmp_path)
        assert use_parquet is False
        assert limit is None
        assert statute_registry is None
        assert compile_metadata is None
        calls.append((tuple(successor_edges), str(successor_as_of)))
        return 1

    monkeypatch.setattr(export_parquet, "_load_corpus", lambda _path: [(0, "527/2014")])
    monkeypatch.setattr(export_parquet, "_project_worker", _project_worker_stub)
    monkeypatch.setattr(
        export_fi_refs_module, "load_reference_successor_edges", _load_edges_stub
    )
    monkeypatch.setattr(
        export_fi_refs_module,
        "export_fi_reference_successors",
        _export_successors_stub,
    )

    counts = export_parquet.export_projections(
        corpus_path="synthetic.csv",
        data_dir=str(tmp_path),
        workers=1,
        use_parquet=False,
        include_reference_successors=True,
        reference_successor_edges_path=str(tmp_path / "edges.jsonl"),
        reference_successor_as_of="2026-01-01",
    )

    assert calls == [((edge,), "2026-01-01")]
    assert counts["fi_reference_successors"] == 1


def test_export_projections_dispatches_promoted_candidate_successors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher wires promotion-claim input as an alternative to edge JSONL."""
    from lawvm.tools import export_parquet

    claim = ReferenceSuccessorPromotionClaim(
        predecessor_work_id="1991/592",
        repealing_work_id="2018/859",
        effective_from="2018-12-15",
        candidate_rule_id="fi.finlex.repealed_by_candidate",
        promotion_witness_id="manual-review:successor:1991/592:2018/859",
        promotion_witness_text="Reviewed successor relation.",
        claim_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.MIGRATION_REVALIDATED,
    )
    calls: list[tuple[tuple[ReferenceSuccessorPromotionClaim, ...], str]] = []

    def _project_worker_stub(item: tuple[int, str, str]):
        _count, sid, _mode = item
        return (
            sid,
            {"statute": [], "sections": [], "findings": [], "ops": []},
            0.0,
        )

    def _load_claims_stub(path: str | Path) -> list[ReferenceSuccessorPromotionClaim]:
        assert Path(path).name == "promotion_claims.jsonl"
        return [claim]

    def _export_promoted_stub(
        corpus: list[tuple[int, str]],
        *,
        promotion_claims: Sequence[ReferenceSuccessorPromotionClaim],
        successor_as_of: date | str,
        data_dir: str = ".tmp/projections",
        use_parquet: bool = True,
        limit: int | None = None,
        statute_registry: object | None = None,
        compile_metadata: object | None = None,
    ) -> int:
        assert corpus == [(0, "527/2014")]
        assert data_dir == str(tmp_path)
        assert use_parquet is False
        assert limit is None
        assert statute_registry is None
        assert compile_metadata is None
        calls.append((tuple(promotion_claims), str(successor_as_of)))
        return 1

    monkeypatch.setattr(export_parquet, "_load_corpus", lambda _path: [(0, "527/2014")])
    monkeypatch.setattr(export_parquet, "_project_worker", _project_worker_stub)
    monkeypatch.setattr(
        export_fi_refs_module,
        "load_reference_successor_promotion_claims",
        _load_claims_stub,
    )
    monkeypatch.setattr(
        export_fi_refs_module,
        "export_fi_reference_successors_from_promoted_candidates",
        _export_promoted_stub,
    )

    counts = export_parquet.export_projections(
        corpus_path="synthetic.csv",
        data_dir=str(tmp_path),
        workers=1,
        use_parquet=False,
        include_reference_successors=True,
        reference_successor_promotion_claims_path=str(
            tmp_path / "promotion_claims.jsonl"
        ),
        reference_successor_as_of="2026-01-01",
    )

    assert calls == [((claim,), "2026-01-01")]
    assert counts["fi_reference_successors"] == 1


def test_export_projections_dispatches_repealed_by_candidate_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production export dispatcher wires the non-authorizing candidate export."""
    from lawvm.tools import export_parquet

    calls: list[list[tuple[int, str]]] = []

    def _project_worker_stub(item: tuple[int, str, str]):
        _count, sid, _mode = item
        return (
            sid,
            {"statute": [], "sections": [], "findings": [], "ops": []},
            0.0,
        )

    def _export_candidates_stub(
        corpus: list[tuple[int, str]],
        *,
        data_dir: str = ".tmp/projections",
        use_parquet: bool = True,
        limit: int | None = None,
        compile_metadata: object | None = None,
    ) -> int:
        assert data_dir == str(tmp_path)
        assert use_parquet is False
        assert limit is None
        assert compile_metadata is None
        calls.append(corpus)
        return 1

    monkeypatch.setattr(export_parquet, "_load_corpus", lambda _path: [(0, "1991/592")])
    monkeypatch.setattr(export_parquet, "_project_worker", _project_worker_stub)
    monkeypatch.setattr(
        export_fi_refs_module,
        "export_fi_repealed_by_candidates",
        _export_candidates_stub,
    )

    counts = export_parquet.export_projections(
        corpus_path="synthetic.csv",
        data_dir=str(tmp_path),
        workers=1,
        use_parquet=False,
        include_repealed_by_candidates=True,
    )

    assert calls == [[(0, "1991/592")]]
    assert counts["fi_repealed_by_candidates"] == 1


# ── Real-corpus parity (opt-in via the canonical data root) ───────────────────


def _corpus_available() -> bool:
    return bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus export parity skipped",
)
@pytest.mark.slow
def test_export_parity_real_corpus() -> None:
    """Default graph writer reproduces the extractor oracle on real Finlex statutes.

    Checks >=4 statutes that each yield >=3 mentions; asserts the production graph
    projector emits the same augmented fi_refs rows the extractor oracle does
    (full-row multiset + cardinality), field-for-field.
    """
    from lawvm.finland.corpus import get_corpus_store

    store = get_corpus_store()
    all_ids = store.list_statute_ids()
    checked = 0
    checked_ids: List[str] = []
    for statute_id in all_ids:
        if checked >= 4:
            break
        try:
            xml_bytes = store.read_oracle(statute_id)
        except Exception:
            continue
        if not xml_bytes:
            continue
        extractor_rows, _ = _project_refs_for_statute_via_extractor(
            statute_id, store, _PROFILE
        )
        if len(extractor_rows) < 3:
            continue

        _assert_parity(statute_id, store)
        checked += 1
        checked_ids.append(statute_id)

    assert checked >= 4, (
        f"expected >=4 real-corpus statutes checked, got {checked} "
        f"(ids={checked_ids})"
    )
